# Models 层开发步骤与进度

本文档用于跨 Session 记录 Models 层 V1 正式化开发进度。后续切换新对话继续开发 Models 时，优先阅读：

```text
src/models/Models层开发步骤与进度.md
src/models/Models层设计决策汇总.md
src/models/Models设计问题回答(1).txt
src/models/Models设计问题回答(2).txt
src/models/模型层支持添加自定义模型的想法.md
```

本文档只记录开发步骤、状态、验收和进度，不替代设计文档。具体设计细节以 `Models层设计决策汇总.md` 为准。

---

## 当前定位

Models 层是 Agent 的统一模型服务层。

它负责：

```text
调用请求封装
provider / route 选择
模型 API 调用
结构化结果返回
JSON 通用解析与 repair
timeout / retry / fallback / 熔断基础
usage / latency / cost / trace 记录
health_check / verify
embedding 接口
上下文压缩能力
MockModel 测试能力
```

它不负责：

```text
不重新设计 Analyzer / Planner / ReActExecutor
不判断用户意图
不生成业务计划
不校验 ActionPacket 业务规则
不直接调用工具
不执行 shell
不生成 Observation
不做完整安全与权限策略
不做完整 Runtime / API / Session 管理
```

当前主编排链路保持：

```text
用户输入
  -> ReactAgent
  -> Analyzer
  -> Planner
  -> ReActExecutor
  -> 输出反馈处理器
  -> 用户反馈
```

`Tool / Model` 调用、`Observation` 和 `Checker` 属于 ReActExecutor 内部执行循环，不是独立于执行器之外的新主链路层。`ExecutionResult` / `ExecutionEvent` 是 ReActExecutor / ReactAgent 对上层返回的结构化输出协议。

输出反馈处理器负责消费 `ExecutionEvent / ExecutionResult`，把执行过程、阶段性说明、确认请求和最终回答转换为用户可见反馈。执行器内部生成的可见步骤说明应作为摘要化事件输出；原始 prompt、内部推理、工具 raw output 和开发日志不直接进入用户反馈。

Models 层是项目级基础模型服务层，Analyzer、Planner、ReActExecutor、legacy Executor 以及后续 Memory / RAG 都可以调用它。

这里的 legacy Executor 只表示历史兼容/迁移诊断入口可能仍有模型调用点，不表示 ReActExecutor 失败后会自动回退到旧顺序 Executor。

关键原则：

```text
本项目目标是模型作为大脑的灵活任务型 Agent，不是规则硬编码结果的固定流程助手。
```

后续开发和调试时，凡是涉及理解、判断、生成、决策、总结、修复的场景，都应优先保留或调用 Models 层；规则只负责协议校验、安全边界、错误分类、timeout/retry/fallback 策略和测试兜底，不能替代复杂计划生成、ActionPacket 决策、模型总结或最终自然语言回答。

Models V1 的关键变化：

```text
generate() / stream_generate() / generate_json() / health_check() / embed_text()
都返回结构化结果，不返回裸字符串或裸 bool。
```

---

## 重点必看：跨 Session 进度更新规则

后续每完成一个可验收开发步骤，都必须同步更新本文档。

```text
完成一个 Step
  -> 修改必要代码
  -> 跑测试或完成逻辑验证
  -> 确认没有明显问题
  -> 更新 Models层开发步骤与进度.md
  -> 记录已完成内容、主要文件、验证方式、当前边界、下一步建议
  -> 下一个对话继续未完成步骤
```

不需要每改一个小函数都更新；但完成一个清晰阶段后必须更新。

每个 Step 完成后至少补充：

```text
状态：
  已完成第一版 / 已完成 / 部分完成 / 阻塞

已完成内容：
  - ...

主要文件：
  - ...

已验证：
  - ...

结果：
  - ...

当前边界：
  - ...

下一步建议：
  - ...
```

如果某一步因为前置设计问题需要调整，必须同步更新：

```text
src/models/Models层设计决策汇总.md
src/models/Models层开发步骤与进度.md
```

---

## 已完成

### Step -2：Models 设计问答与设计基线

状态：已完成

已完成内容：

- 完成第一轮 Models 设计问答：

```text
src/models/Models设计问题回答(1).txt
```

- 完成第二轮 Models 补充问答：

```text
src/models/Models设计问题回答(2).txt
```

- 参考用户自定义模型设计想法：

```text
src/models/模型层支持添加自定义模型的想法.md
```

- 完成 Models 层设计汇总：

```text
src/models/Models层设计决策汇总.md
```

已确认关键决策：

- Models 层是统一模型服务层，不承担 Analyzer / Planner / ReActExecutor 职责。
- 正式模型接口统一返回结构化结果。
- `generate()` 本身返回 `ModelCallResult`，不保留裸字符串正式接口。
- 所有调用方必须迁移为先判断 `success`，再消费 `content`。
- 旧 `MODEL_NAME` 不再作为 V1 正式配置入口。
- V1 主协议是 `openai-compatible`。
- `anthropic-compatible / gemini-compatible / custom-mapping` 只预留，不完整实现。
- 支持用户自定义 OpenAI-compatible provider。
- 缺真实模型配置时返回结构化失败，不静默 mock。
- route 默认不自动换模型，只区分调用类型和参数策略。
- ActionPacket 业务校验继续属于 ReActExecutor。
- Health check 与 verify 分离。
- 上下文压缩是 Models 层能力，触发和保存由其他层负责。
- embedding 与 chat route 完全分离。

### Step 0：现有基础模型适配

状态：已完成基础版，后续将被 V1 正式化逐步替换

主要文件：

```text
src/models/base_model.py
src/models/mock_model.py
src/models/model_manager.py
src/models/openai_model.py
src/models/qianwen_model.py
src/models/doubao_model.py
```

已完成内容：

- 定义基础 `BaseModel`。
- `MockModel` 可用于无 API key 场景。
- `ModelManager` 支持 `mock/openai/qianwen/doubao`。
- 当前 `generate()` 返回字符串。
- 当前 `stream_generate()` 返回字符串 chunk。
- 当前 `health_check()` 返回 bool。
- 支持基础 `get_model_info()`。

当前边界：

- 当前实现是早期雏形，不符合 Models V1 结构化接口要求。
- 当前 provider 配置主要依赖 `.env` / `MODEL_NAME`，后续要迁移到 `config/models/`。
- 当前异常处理会把错误包装成字符串，后续要改为结构化失败。
- 当前 Analyzer / Planner / ReActExecutor / legacy Executor 仍可能直接消费字符串，后续必须迁移。

---

## 待开发

### Step 1：模型调用协议底座

状态：已完成

目标：

- 建立 Models V1 的结构化协议对象。
- 后续所有 provider、route、retry、日志、调用方适配都基于这些协议。
- 不在本 Step 接真实 provider，不改复杂路由。

已完成内容：

- 新增稳定的 Models V1 协议对象和 `to_dict()` 序列化能力。
- 固定 `ModelCallType` 调用类型枚举及其规范化逻辑。
- 固定 `ModelErrorCode` 错误码集合及未知错误归一化逻辑。
- 增加消息、调用选项、trace、usage、cost、结构化结果、流式结果、health、embedding、上下文压缩结果对象。
- 失败结果强制 `content=""`，模型文本与错误信息分离。
- `prompt` 可转换为单条 `user` 消息；显式 `messages` 优先。
- 失败工厂会忽略误传的 `content`，保持失败结果协议边界。

主要文件：

```text
src/models/protocol.py
src/models/errors.py
src/models/__init__.py
tests/test_models_protocol.py
```

协议对象清单对应文件：

```text
src/models/protocol.py
src/models/errors.py
src/models/__init__.py
tests/test_models_protocol.py
```

需要定义的核心对象：

```text
ModelMessage
ModelCallType
ModelCallOptions
ModelTraceContext
ModelUsage
ModelCost
ModelErrorInfo
ModelCallResult
ModelStreamChunk
ModelStreamResult
StructuredModelResult
ModelHealthStatus
EmbeddingResult
EmbeddingBatchResult
ContextCompressionResult
```

需要固定的调用类型枚举：

```text
chat
analyzer_intent_fallback
planner_structured_plan
react_action_decision
react_action_repair
react_call_model
checker_semantic
summary
memory_summary
context_compression
rag_answer
embedding
web_search
```

说明：

```text
web_search 是 Tools V1 model_builtin 搜索需要的最小后续扩展。
它需要在代码实现 Tools 联网搜索时同步补齐 ModelCallType、route 和 structured parse 配置。
它不改变 Models 层边界：Models 不执行 Tool，不生成 ToolResult，只返回 StructuredModelResult / ModelCallResult。
```

需要固定的错误码：

```text
missing_model_config
missing_api_key
unsupported_provider
unsupported_protocol
authentication_failed
permission_denied
model_not_found
rate_limited
quota_exceeded
context_length_exceeded
timeout
network_error
provider_server_error
invalid_request
invalid_prompt
invalid_json
schema_invalid
json_repair_failed
blocked_by_policy
user_cancelled
model_call_failed
embedding_failed
compression_failed
unknown_error
```

实现要求：

- 所有协议对象要支持稳定序列化，建议提供 `to_dict()`。
- `ModelCallResult.content` 只放模型真实生成内容。
- 错误信息放入 `code/error/error_info`。
- `ModelTraceContext` 预留：

```text
source_trace_id
conversation_id
session_id
plan_id
execution_id
task_id
step_id
packet_id
parent_request_id
caller
```

- `ModelUsage` 与 `ModelCost` 允许字段为 None。
- `ModelStreamChunk` 和 `ModelStreamResult` 都要结构化。

验收标准：

- 能创建成功和失败的 `ModelCallResult`。
- 失败结果必须 `success=False`、`content=""`、`code` 非空。
- `ModelMessage` 支持 system/user/assistant/tool。
- `prompt: str` 后续可转换为单条 user message。
- 所有 result 对象可转 dict。
- `ModelUsage / ModelCost` 可为空，不影响序列化。
- `ModelTraceContext` 能携带 plan/execution/task/step/packet 信息。

建议测试：

```text
python -B -m unittest tests.test_models_protocol
```

完成本 Step 后必须更新：

- 已完成的协议对象。
- 是否存在暂未实现字段。
- 测试文件和测试结果。

当前边界：

- 本 Step 只定义协议，旧 `ModelManager` 仍返回旧字符串接口，暂不迁移调用方。
- 当前未实现配置中心、真实 provider、route、retry、fallback、日志、JSON repair、MockModel V1 行为。
- Models 层不负责 ActionPacket、Planner 计划或 Analyzer 意图的业务校验。
- 本 Step 不接外部 API，不执行本地文件、代码或工具。

已验证：

- `python -B -m unittest tests.test_models_protocol`
- `python -B -m unittest tests.test_react_executor_protocol`

结果：

- Models 协议测试 6 个通过。
- ReActExecutor 协议回归测试 10 个通过。
- 全量测试 316 个通过。

### Step 2：config/models 配置中心

状态：已完成

目标：

- 新增 Models V1 配置中心。
- 废弃旧 `MODEL_NAME` 正式入口。
- 定义 ProviderSpec / ProviderConf / RouteCandidate / credential_slug。

已完成内容：

- 新增 `src/models/config.py`，定义并加载 `ModelsRuntimeConfig`、`ProviderSpec`、`ProviderConf`、`ProviderCredential`、`RouteConfig`、`RouteCandidate`、`ModelsConfig`。
- 新增 `src/models/credentials.py`，预留 `api_key_env` / `credential_ref` 解析、脱敏、`CredentialRecord` 和 `CredentialResolution`。
- 固定 protocol 枚举：`openai-compatible`、`anthropic-compatible`、`gemini-compatible`、`custom-mapping`、`mock`。
- 固定 route policy：`user_selected`、`explicit_candidates`。
- 内置默认 ProviderSpec：`mock`、`openai`、`qianwen`、`doubao`、`custom_openai_compatible`。
- 无 `config/models/` 时可加载稳定默认配置和默认 route 参数策略。
- 新增 `config/models/` 基线文件，包含全局配置、route 基线、pricing 与 structured_output 占位。
- `provider_confs.json` 支持用户自定义 OpenAI-compatible provider 的 `base_url`、`default_model` / `model_id`、`credential_slug`、`api_key_env`、`credential_ref`、`headers`、`verify` 等字段。
- `provider_conf_id` 重复、同一 ProviderConf 内 `credential_slug` 重复、不支持的 provider/protocol、route candidate 引用错误、明文 secret 配置都会返回结构化 `ModelsConfigError`。
- `enabled=false` 的 ProviderConf 不进入 `route_candidates()` 默认候选结果。
- `MODEL_NAME` 未接入 Models V1 配置中心，仍只属于旧 `ModelManager` 兼容路径，后续 Step 3 迁移。

主要文件：

```text
src/models/config.py
src/models/credentials.py
config/models/models_config.json
config/models/provider_specs.json
config/models/provider_confs.json
config/models/routes.json
config/models/pricing.json
config/models/structured_output.json
tests/test_models_config.py
```

配置加载顺序：

```text
1. 内置默认 ProviderSpec
2. config/models/provider_specs.json
3. config/models/provider_confs.json
4. config/models/routes.json
5. config/models/models_config.json
6. config/models/pricing.json
7. config/models/structured_output.json
8. 环境变量覆盖
9. Runtime/API 单次调用 override
```

需要实现的结构：

```text
ProviderSpec:
  provider
  protocol
  display_name
  default_base_url
  default_model
  supports_streaming
  supports_json_mode
  supports_tool_calling
  supports_embedding
  supports_vision
  supports_custom_headers
  supports_top_p
  supports_top_k
  default_timeout_seconds
  default_max_retries
  request_adapter
  response_adapter
  known_model_prefixes
  tags
  metadata

ProviderConf:
  id
  name
  provider
  protocol
  enabled
  base_url
  default_model
  custom_models
  credentials
  headers
  timeout_seconds
  max_retries
  temperature
  top_p
  max_tokens
  max_context_tokens
  status
  verified_at
  last_used_at
  tags
  metadata

ProviderCredential:
  slug
  api_key_env
  credential_ref
  enabled
  status
  last_error_code
  last_error_at

RouteCandidate:
  provider_conf_id
  credential_slug
  model
  weight
  priority
  enabled
  cooldown_until
  metadata
```

实现要求：

- `provider_conf_id` 必须稳定唯一。
- `credential_slug` 在同一个 ProviderConf 内必须唯一。
- `display_name/name/alias` 不参与路由主键。
- `enabled=false` 的 provider 不进入默认路由。
- 缺真实 key 不导致配置加载失败。
- 不明文写入 API key。
- 用户自定义 provider 预留 `credential_ref`。
- 开发/内置 provider 优先使用环境变量。
- 配置缺失时返回结构化配置错误，不静默 mock。
- 固定 protocol 枚举预留：

```text
openai-compatible
anthropic-compatible
gemini-compatible
custom-mapping
mock
```

- 本地加密凭证存储先预留接口和字段：

```text
credential_ref
provider_conf_id
credential_slug
encrypted_secret
created_at
updated_at
```

- 后续加密实现优先 AES-256 或系统级安全凭证存储。
- 配置文件、日志、health_check、verify 结果都不能输出明文 secret。

验收标准：

- 没有 `config/models/` 时能加载稳定默认配置。
- 缺失 provider key 不崩溃。
- `mock` 只有显式配置或 fixture 注入时使用。
- provider_conf_id 重复会被配置校验发现。
- credential_slug 重复会被配置校验发现。
- 不支持的 protocol 返回 `unsupported_protocol`。
- 不支持的 provider 返回 `unsupported_provider`。
- `MODEL_NAME` 不作为正式配置入口。
- provider_confs.json 不包含明文 API key。
- credential_ref 缺失或不可用时能返回结构化凭证错误。

建议测试：

```text
python -B -m unittest tests.test_models_config tests.test_models_protocol
```

当前边界：

- 本 Step 只完成配置加载与校验。
- 本 Step 不要求真实 provider 调用成功。
- 本地加密 credential store 可先预留接口，完整实现可后续 Runtime/API/UI 接入时补强。
- 本 Step 不迁移 `ModelManager.generate()` / `stream_generate()` 的裸字符串接口。
- 本 Step 不做 route 选择执行、retry、fallback、熔断、health_check live_check 或 verify live call。
- `default_mock_enabled` 只是配置字段，不表示真实 provider 缺配置时会静默 fallback 到 MockModel。

已验证：

- `python -B -m unittest tests.test_models_config`
- `python -B -m unittest tests.test_models_protocol tests.test_react_executor_protocol`
- `python -B -m unittest discover -s tests -p 'test_*.py'`

结果：

- Models 配置测试 7 个通过。
- Models 协议与 ReActExecutor 协议定向回归 16 个通过。
- 全量测试 323 个通过。

### Step 3：结构化 generate / stream_generate 与调用方最小迁移

状态：已完成

目标：

- 将 `generate()` / `stream_generate()` 改为结构化返回。
- 同步迁移 Analyzer / Planner / ReActExecutor / legacy Executor 的最小调用逻辑。
- 保证主链路不在中间步骤长期处于破坏状态。

建议主要文件：

```text
src/models/model_manager.py
src/models/base_model.py
src/models/mock_model.py
src/agent/complexity_analyzer.py
src/agent/planner.py
src/agent/react_executor.py
src/agent/executor.py
tests/test_models_generate_result.py
tests/test_models_callers_adaptation.py
```

实现要求：

- `ModelManager.generate(...) -> ModelCallResult`。
- `ModelManager.stream_generate(...) -> Iterator[ModelStreamChunk]`。
- `BaseModel` 或新 provider 抽象不再规定返回裸字符串。
- 失败时返回结构化失败，不返回 `"Model call failed: ..."` 字符串。
- 调用方必须改为：

```python
result = model_manager.generate(...)
if not result.success:
    # 按调用方语义处理失败
content = result.content
```

调用方适配要求：

```text
Analyzer:
  LLM fallback 失败时回到规则结果或结构化失败，不把错误文本当意图

Planner:
  LLM planner 失败时进入已有 fallback/invalid 计划，不伪装成功

ReActExecutor:
  ActionPacket 决策失败时进入模型调用失败路径，不生成伪 Observation

legacy Executor:
  model step 显式读取 result.content
```

验收标准：

- 所有当前模型调用点不再假设返回 str。
- 模型失败不会被上层当作正常文本消费。
- 现有 Analyzer / Planner / ReActExecutor 主链路测试继续通过或同步更新为新协议。
- `stream_generate()` chunk 结构化。
- 需要字符串时只能显式读取 `result.content`。

建议测试：

```text
python -B -m unittest tests.test_models_generate_result tests.test_models_callers_adaptation
python -B -m unittest tests.test_analyzer_v1 tests.test_planner_v1 tests.test_react_executor_v1
```

已完成内容：

- `ModelManager.generate(...)` 现已返回 `ModelCallResult`，失败时返回结构化失败，不再返回 `"Model call failed: ..."` 字符串。
- `ModelManager.stream_generate(...)` 现已返回 `ModelStreamChunk` 序列，并对末尾 chunk 标记 `is_final`。
- 新增 `src/models/compat.py`，提供 `require_model_content()`，用于把结构化结果与旧字符串假对象兼容起来。
- `BaseModel` 类型签名已同步为结构化协议兼容形式。
- Analyzer / Planner / legacy Executor / ReActExecutor / LLMChecker / RAG 的最小调用点已迁移到结构化读取。
- 新增调用边界测试：
  - `tests/test_models_generate_result.py`
  - `tests/test_models_callers_adaptation.py`

已验证：

- `python -B -m unittest tests.test_models_generate_result tests.test_models_callers_adaptation`
- `python -B -m unittest discover -s tests -p 'test_*.py'`

结果：

- 相关新增测试通过。
- 全量测试通过，共 332 个测试。

当前边界：

- 本 Step 只完成结构化调用结果与最小调用方迁移，没有进入真实 provider 路由、retry、fallback、stream 聚合等后续步骤。
- `require_model_content()` 仅负责结构化结果解包与旧假对象兼容，不承担模型业务校验。
- 真实 provider 的完整 OpenAI-compatible 适配仍留在后续 Step。

下一步建议：

- Step 4：先补 `MockModel` 的结构化测试与 fixture，确认模型层基础假数据稳定，再继续往真实 provider 适配推进。

### Step 4：MockModel 结构化能力与基础 fixture

状态：已完成

目标：

- 让 MockModel 模拟真实模型服务的结构化返回。
- 保证无真实 API key 时本地开发和测试稳定。
- 不让 MockModel 掩盖真实 provider 配置错误。

建议主要文件：

```text
src/models/mock_model.py
tests/test_models_mock_model.py
tests/fixtures/models/
```

MockModel 需要支持：

```text
普通文本
Analyzer JSON
Planner JSON
ActionPacket JSON
summary
context_compression
embedding 占位向量
失败响应模拟
多次响应序列模拟
```

实现要求：

- MockModel 返回 `ModelCallResult`。
- MockModel 可根据 `call_type` 返回不同结构。
- MockModel 可返回 `StructuredModelResult` 所需的 JSON 文本。
- Mock embedding 使用确定性 hash / 简单向量。
- Mock compression 返回 `ContextCompressionResult` 可消费结构。
- MockModel 只在显式配置 mock 或测试 fixture 中使用。

验收标准：

- MockModel 可支持 Analyzer / Planner / ReActExecutor 的基本模型路径。
- MockModel 可模拟成功、失败、invalid_json、schema_invalid。
- 切换真实 provider 时不依赖 MockModel 专属字段。
- 真实 provider 缺配置时不会自动 fallback 到 MockModel。

建议测试：

```text
python -B -m unittest tests.test_models_mock_model tests.test_models_generate_result
```

已完成内容：

- `MockModel.generate(...)` 现已直接返回 `ModelCallResult`，不再依赖 `ModelManager` 对裸字符串二次包装。
- 根据 `call_type` 提供稳定的普通文本、Analyzer JSON、Planner JSON、ActionPacket JSON、summary、context compression 占位输出。
- 支持显式注入 `fixtures` 与 `responses` 序列，可模拟成功、`invalid_json`、`schema_invalid` 等失败响应。
- `stream_generate(...)` 返回结构化 `ModelStreamChunk`，支持测试注入分段文本。
- 新增确定性 `embed_text(...)` / `embed_texts(...)` 占位向量与 `compress_context(...)` 结构化压缩结果。
- 新增基础 fixture 文件：
  - `tests/fixtures/models/mock_model_fixtures.json`
- `MockModel` 已从 `src.models` 包入口导出。
- 同步更新 ReActExecutor prompt 测试，改为断言 `ModelCallResult` 新契约。

主要文件：

```text
src/models/mock_model.py
src/models/__init__.py
tests/test_models_mock_model.py
tests/fixtures/models/mock_model_fixtures.json
tests/test_react_executor_prompt.py
```

已验证：

- `python -B -m unittest tests.test_models_mock_model tests.test_models_generate_result tests.test_react_executor_prompt`
- `python -B -m unittest discover -s tests -p 'test_*.py'`

结果：

- MockModel 定向测试和调用结果测试通过。
- 全量测试通过，共 339 个测试。
- provider 初始化失败时仍返回结构化失败，不会自动切换为 `MockModel`。

当前边界：

- MockModel 不做复杂业务推理、真实 HTTP 调用、真实 provider 路由、retry/fallback 或完整 JSON schema 校验。
- 模型层不读取 fixture 文件；fixture 由测试层加载并显式注入。
- 复杂场景继续使用 `SequenceModel` / `FakeModel` fixture。

下一步建议：

- Step 5：实现 OpenAI-compatible 统一适配器，并将 OpenAI / Qianwen / Doubao / 用户自定义 endpoint 逐步纳入配置化 provider 创建。

### Step 5：OpenAI-compatible 统一适配器与内置 provider 配置化

状态：已完成

目标：

- 实现 V1 主协议 `openai-compatible`。
- OpenAI / Qianwen / Doubao / 用户自定义 OpenAI-compatible endpoint 走统一 adapter。
- 真实 provider 缺 key 不导致 Agent 启动崩溃。

建议主要文件：

```text
src/models/providers/base.py
src/models/providers/openai_compatible.py
src/models/openai_model.py
src/models/qianwen_model.py
src/models/doubao_model.py
src/models/model_manager.py
tests/test_models_openai_compatible.py
```

实现要求：

- OpenAI provider 使用新客户端方式，不使用全局 `openai.api_key`。
- adapter 接收 `ProviderConf + credential + ModelCallOptions`。
- 请求使用 messages 格式。
- 支持 base_url 配置。
- 支持自定义 headers。
- 支持 usage 解析。
- 支持 provider_request_id 解析。
- 支持 provider 错误解析为 `ModelErrorInfo`。
- Qianwen V1 优先走 OpenAI-compatible endpoint。
- Doubao endpoint/model 必须配置化，不硬编码。
- `anthropic-compatible / gemini-compatible / custom-mapping` 只预留，不完整实现。

验收标准：

- openai-compatible adapter 可用 fake HTTP/client 测试成功响应。
- 可解析 content、usage、provider_request_id。
- 401/403/404/429/5xx 可归一化为错误码。
- 缺 API key 返回 `missing_api_key`，不抛到业务层。
- 自定义 base_url + model_id 可形成请求。
- 内置 provider 通过配置创建。

建议测试：

```text
python -B -m unittest tests.test_models_openai_compatible tests.test_models_config
```

已完成内容：

- 新增 `src/models/providers/base.py`，定义统一 provider 协议边界。
- 新增 `src/models/providers/openai_compatible.py`：
  - 接收 `ProviderConf + CredentialResolution + ModelCallOptions`。
  - 使用 OpenAI Chat Completions-compatible `messages` 请求格式。
  - 支持 `base_url`、自定义 headers、temperature、top_p、max_tokens、json_mode、stream。
  - 解析 content、usage、provider request id、finish reason。
  - 将 400/401/403/404/408/429/5xx 等响应归一化为 Models V1 错误码。
  - 缺少 credential、base_url 或 model 时返回结构化失败。
- `OpenAIModel / QianwenModel / DoubaoModel` 已改为统一适配器兼容包装，不再使用全局 `openai.api_key` 或旧的原生 requests 调用。
- `ModelManager` 已改为通过 `config/models` 创建内置 provider：
  - `conf_openai_default`
  - `conf_qianwen_default`
  - `conf_doubao_default`
- 新增 `config/models/provider_confs.json` 内置 provider 配置，真实 provider 默认禁用，显式选择后仍不会静默降级到 MockModel。
- 用户自定义 OpenAI-compatible provider 仍可通过 `ProviderConf.base_url / default_model / credential / headers` 接入。
- 新增 `tests/test_models_openai_compatible.py`，使用 fake client 验证请求和响应，不访问外部 API。
- provider adapter 已从 `src.models` 包入口导出。

已验证：

- `python -B -m unittest tests.test_models_openai_compatible tests.test_models_config tests.test_models_generate_result tests.test_models_mock_model`
- `python -B -m unittest discover -s tests -p 'test_*.py'`

结果：

- Step 5 定向测试通过，共 24 个测试。
- 全量测试通过，共 345 个测试。
- 当前环境未安装 `openai` SDK，但缺少依赖/缺少 key 都不会导致 Agent 启动崩溃；fake client 测试覆盖协议行为。

当前边界：

- 真实 provider 集成测试默认跳过，本 Step 不访问外部 API。
- retry、fallback、熔断、route candidate 选择放在后续 Step。
- `health_check` / `verify_provider_config` 的结构化实现放在 Step 6。
- Anthropic / Gemini 原生 API 不在本 Step 实现。

下一步建议：

- Step 6：实现结构化 `health_check` 与自定义 provider `verify`，明确 config_check 和 live_check 边界。

### Step 6：Provider health_check 与自定义模型 verify

状态：已完成

目标：

- 实现结构化 health_check。
- 实现 `verify_provider_config(provider_conf_id)`。
- 明确 config_check 与 live_check 边界。

建议主要文件：

```text
src/models/model_manager.py
src/models/providers/base.py
src/models/providers/openai_compatible.py
tests/test_models_health_verify.py
```

实现要求：

```text
health_check:
  默认 config_check
  不访问外部 API
  检查 provider_conf、protocol、base_url、model、credential 引用

verify_provider_config:
  显式 live_check
  发送轻量请求
  检查鉴权、模型 ID、响应结构
  返回 ModelHealthStatus
```

状态处理：

```text
verify 成功:
  healthy=True
  status=active
  verified_at 更新

verify 失败:
  healthy=False
  status=error 或 unverified
  code/error 记录
```

验收标准：

- `health_check()` 返回 `ModelHealthStatus`，不是 bool。
- config_check 不联网。
- live_check 只有显式调用才执行。
- 缺 key 的真实 provider 启动不崩溃，但 health 状态可说明原因。
- verify 失败不会把 provider 标记为可用。

建议测试：

```text
python -B -m unittest tests.test_models_health_verify
```

当前边界：

- 本 Step 不做完整 UI。
- Runtime/API 后续负责把 verify 暴露给用户操作。

### Step 7：模型 route 与调用参数策略

状态：已完成

目标：

- 按 `call_type` 应用不同调用策略。
- route 默认不自动换模型，只调整参数。
- 只有配置明确指定 candidates 时才切换模型。

建议主要文件：

```text
src/models/router.py
src/models/config.py
src/models/model_manager.py
tests/test_models_router.py
```

实现要求：

- 支持 routes.json。
- 支持 `default_model_policy=user_selected`。
- 支持 route params：

```text
temperature
top_p
top_k
max_tokens
json_mode
timeout_seconds
max_retries
```

- provider 不支持的参数不盲目传递。
- `top_k` 需根据 ProviderSpec 判断。
- 调用方显式传入 provider_conf_id/model 时优先级最高。
- 普通自然语言不能直接切 provider。
- 根据 ProviderSpec / ProviderConf / model 的 `max_context_tokens` 或 `max_context_chars` 做最后长度检查。
- 默认不擅自删除 prompt 中的关键结构字段。
- 超出上下文限制时：

```text
调用方允许压缩:
  返回需要压缩的结构化状态，或在 Step 13 完成后接入 context_compression

调用方不允许压缩:
  返回 context_length_exceeded

调用方明确 allow_truncation:
  才允许截断，并记录 truncation_used / dropped_chars / dropped_tokens
```

建议默认策略：

```text
react_action_decision:
  temperature=0.1
  json_mode=true
  max_tokens=1200

planner_structured_plan:
  temperature=0.1
  json_mode=true
  max_tokens=2500

summary:
  temperature=0.3
  max_tokens=2000

chat:
  temperature=0.5
  max_tokens=2000
```

验收标准：

- 不同 call_type 能拿到不同参数策略。
- 未配置 candidates 时使用用户选定模型。
- 配置 candidates 时可选择指定 provider_conf_id / credential_slug / model。
- disabled provider 不被选中。
- embedding route 与 chat route 分离。
- prompt 超出 provider/model 上下文限制时返回 `context_length_exceeded`。
- 没有明确允许时，不自动截断 ActionPacket、Planner 计划、工具结果等结构化关键字段。

建议测试：

```text
python -B -m unittest tests.test_models_router
```

当前边界：

- 本 Step 只做选择和参数合并。
- fallback、多候选失败切换放到 Step 9。

### Step 8：timeout / retry / 错误分类

状态：已完成

目标：

- 实现统一错误分类。
- 实现 timeout 和最多 5 次指数退避 retry。
- provider 自定义错误码作为辅助判断。

建议主要文件：

```text
src/models/errors.py
src/models/retry.py
src/models/model_manager.py
tests/test_models_retry_errors.py
```

实现要求：

- 统一 `ModelErrorInfo`。
- 错误归一化优先级：

```text
HTTP status
provider error_code
provider error message / hint
本地归一化错误码
```

- 可 retry：

```text
timeout
rate_limited
network_error
provider_server_error
temporary_unavailable
```

- 不 retry：

```text
missing_api_key
missing_model_config
authentication_failed
permission_denied
invalid_prompt
blocked_by_policy
user_cancelled
```

- 默认最多 5 次指数退避。
- retry 结果写入 attempts、latency、error_info。

验收标准：

- timeout 会 retry。
- 429 会 retry。
- 5xx 会 retry。
- missing_api_key 不 retry。
- invalid_prompt 不 retry。
- retry 耗尽后返回结构化失败。
- attempts 数正确。
- provider 自定义错误码可辅助归一化。

建议测试：

```text
python -B -m unittest tests.test_models_retry_errors
```

当前边界：

- fallback 不在本 Step 完整实现。
- 本地复杂限流只预留字段。

### Step 9：多候选、fallback、多凭证和轻量熔断

状态：已完成

目标：

- 支持 route candidate 多候选。
- 支持多凭证。
- 支持按错误类型保守 fallback。
- 支持轻量内存熔断冷却。

建议主要文件：

```text
src/models/router.py
src/models/retry.py
src/models/model_manager.py
tests/test_models_fallback_circuit.py
```

实现要求：

- route candidate 使用：

```text
provider_conf_id
credential_slug
model
```

- 冷却粒度：

```text
401 / 403 / authentication_failed / permission_denied:
  冷却 credential_slug

404 / model_not_found:
  冷却 provider_conf_id + model

429 / rate_limited:
  retry + 视情况 fallback + 冷却当前候选

quota_exceeded:
  优先冷却 credential_slug

5xx / timeout / network_error:
  冷却 route candidate，短冷却

连续失败 N 次:
  route candidate 标记 circuit_open
```

- 不允许 fallback 绕过：

```text
blocked_by_policy
invalid_prompt
user_cancelled
```

- verify / health live_check 成功可恢复候选。

验收标准：

- 第一个候选 timeout 后可 fallback 到第二个候选。
- credential 鉴权失败只冷却该 credential。
- model_not_found 只冷却 provider_conf_id + model。
- blocked_by_policy 不 fallback。
- fallback_used / fallback_reason 正确写入 ModelCallResult。
- circuit_open 候选短期内不会反复被选中。

建议测试：

```text
python -B -m unittest tests.test_models_fallback_circuit tests.test_models_router tests.test_models_retry_errors
```

当前边界：

- V1 使用内存熔断，不做持久化熔断状态。
- 不做复杂轮询或加权负载均衡，字段可预留。

### Step 10：结构化 JSON 输出与 repair

状态：已完成

目标：

- 实现 `generate_json()`。
- 支持 strict / lenient JSON 解析。
- 支持通用 JSON repair。
- 保持 ActionPacket 业务校验在 ReActExecutor。

建议主要文件：

```text
src/models/structured_output.py
src/models/model_manager.py
config/models/structured_output.json
tests/test_models_structured_output.py
```

实现要求：

- `generate_json(...) -> StructuredModelResult`。
- strict 模式只接受纯 JSON 或明确 JSON schema 输出。
- lenient 模式可从文本中提取第一个 JSON object / array。
- 支持 fenced JSON。
- 支持最大 repair 次数配置，默认 1。
- repair prompt 包含原始输出、解析错误、目标 schema 摘要。
- repair 失败返回 `json_repair_failed` 或 `schema_invalid`。
- 迁移 JSON 消费型调用方逐步使用 `generate_json()`：

```text
Analyzer LLM fallback:
  用 generate_json() 获取结构化意图兜底结果

Planner LLM planner:
  用 generate_json() 获取结构化 TaskPlan / TaskUnit / PlanStep payload

ReActExecutor ActionPacket decision:
  用 generate_json() 获取 ActionPacket 候选 JSON

ReActExecutor action repair:
  用 generate_json() 获取修复后的 ActionPacket 候选 JSON
```

- 如果调用方暂时仍需业务校验，必须在本层 JSON 解析后继续由各自业务层校验。

边界：

```text
Models 层只做通用 JSON/schema 校验
ActionPacket 业务校验留在 ReActExecutor
Planner 计划业务校验留在 Planner
Analyzer 意图业务校验留在 Analyzer
```

验收标准：

- 纯 JSON 可解析。
- fenced JSON 可解析。
- 普通文本包裹 JSON 可 lenient 解析。
- 非 JSON 可触发 repair。
- repair 耗尽返回结构化失败。
- ActionPacket 非法业务字段不由 Models 层判执行。
- Analyzer / Planner / ReActExecutor 的 JSON 消费路径不再各自散落重复 JSON 提取逻辑。

建议测试：

```text
python -B -m unittest tests.test_models_structured_output
python -B -m unittest tests.test_models_callers_adaptation
```

当前边界：

- 结构化输出不做流式。
- 完整 schema registry 可后续增强。

完成记录（2026-08-10）：

已完成内容：

- 新增 `src/models/structured_output.py`，实现 strict / lenient JSON 解析、fenced JSON / embedded JSON 提取、轻量 JSON schema 子集校验和 JSON repair prompt 构建。
- `ModelManager.generate_json(...)` 返回 `StructuredModelResult`，支持 `json_mode=True`、按 `call_type` 读取 parse mode、默认 repair 次数、repair 开关、可选 schema / schema_name。
- 失败结果结构化返回 `invalid_json`、`json_repair_failed`、`schema_invalid` 或底层模型调用错误，不把错误文本伪装成模型内容。
- Analyzer / Planner / ReActExecutor 的 JSON 消费路径已最小迁移：优先使用 `generate_json()`；旧测试 fixture 或旧 model manager 缺少 `generate_json()` 时仍走兼容路径。
- ReActExecutor 仍由 `parse_action_packet()` 和执行器自己的校验处理 ActionPacket 业务规则；Models 层只处理通用 JSON 解析与通用 schema 子集。

主要文件：

```text
src/models/structured_output.py
src/models/model_manager.py
src/models/__init__.py
src/agent/complexity_analyzer.py
src/agent/planner.py
src/agent/react_executor.py
tests/test_models_structured_output.py
tests/test_models_callers_adaptation.py
```

已验证：

```text
python -B -m py_compile src\models\structured_output.py src\models\model_manager.py src\models\__init__.py src\agent\complexity_analyzer.py src\agent\planner.py src\agent\react_executor.py tests\test_models_callers_adaptation.py tests\test_models_structured_output.py
python -B -m unittest tests.test_models_structured_output tests.test_models_callers_adaptation
python -B -m unittest tests.test_models_structured_output tests.test_models_callers_adaptation tests.test_models_protocol tests.test_models_generate_result tests.test_models_config tests.test_models_mock_model tests.test_models_openai_compatible tests.test_models_health_verify tests.test_models_router tests.test_models_retry_errors tests.test_models_fallback_circuit tests.test_analyzer_v1 tests.test_planner_v1_llm_planner tests.test_react_executor_action_packet_schema tests.test_react_executor_protocol
python -B -m unittest discover -s tests -p 'test_*.py'
```

结果：

- Step 10 定向测试 17 个通过。
- Models 相关与三层关键回归 122 个通过。
- 全量测试 384 个通过。

当前边界：

- `generate_json()` 暂不提供流式结构化输出。
- JSON schema 校验是轻量子集，仅覆盖 `type`、`required`、`properties`、`items`、`enum`，不实现完整 JSON Schema。
- Models 层不做 ActionPacket、Planner 计划或 Analyzer intent 的业务校验。
- repair 使用同一 `ModelManager.generate()` 路径再次调用模型，不引入独立 repair provider。

下一步建议：

- 进入 Step 11：usage / latency / cost / logs/models.log，建立 Models 独立 JSONL 日志、trace、usage/cost/latency 记录与脱敏边界。

### Step 11：usage / latency / cost / logs/models.log

状态：待开发

目标：

- 增加 Models 独立 JSONL 日志。
- 记录 usage、latency、cost、trace。
- 默认不记录完整 prompt，不记录完整长响应。
- 用户可见事件与开发日志分离。

建议主要文件：

```text
src/models/observability.py
src/models/model_manager.py
logs/models.log
tests/test_models_observability.py
```

日志字段至少包含：

```text
timestamp
level
source_trace_id
model_request_id
provider_request_id
call_type
route
provider_conf_id
provider
protocol
model
credential_slug
success
code
error_summary
latency_ms
attempts
retry_used
fallback_used
fallback_reason
prompt_length
prompt_preview
prompt_hash
messages_count
response_length
response_preview
response_hash
prompt_tokens
completion_tokens
total_tokens
cost
metadata
```

脱敏字段：

```text
api_key
token
password
secret
authorization
cookie
set-cookie
client_secret
access_key
refresh_token
```

实现要求：

- 日志写入失败不影响模型结果返回。
- `raw_response` 默认不完整写日志。
- prompt 默认只记录长度、preview 或 hash。
- provider token chunk 不直接变成用户事件。
- 用户可见提示由 Runtime/API/ReActExecutor 根据 code/error 生成。

验收标准：

- 成功调用写 JSONL。
- 失败调用写 JSONL。
- 敏感字段被脱敏。
- trace_context 字段进入日志。
- usage 可为空。
- cost 可为 None。
- 用户事件不由 Models 层直接产生。

建议测试：

```text
python -B -m unittest tests.test_models_observability
```

当前边界：

- 不做日志轮转。
- 不做完整成本预算。

完成记录（2026-08-10）：

已完成内容：

- 新增 `src/models/observability.py`，提供 `ModelCallLogger`、日志脱敏、prompt/response 长度与 preview/hash、usage/cost 字段构造。
- `ModelManager.generate()` 在最终返回前统一补充 trace_context、按 `config/models/pricing.json` 可用价格表进行轻量 cost 估算，并 best-effort 写入 `logs/models.log` JSONL。
- `ModelManager.stream_generate()` 在流式输出结束后写入一条汇总开发日志，不把 provider token chunk 转成用户可见事件。
- 日志写入失败不会影响 `ModelCallResult` / `ModelStreamChunk` 返回。
- 默认不完整记录 prompt、response 或 raw_response；记录长度、可选 preview、hash。
- 敏感字段递归脱敏，覆盖 `api_key`、`token`、`password`、`secret`、`authorization`、`cookie`、`set-cookie`、`client_secret`、`access_key`、`refresh_token`。

主要文件：

```text
src/models/observability.py
src/models/model_manager.py
src/models/__init__.py
tests/test_models_observability.py
```

已验证：

```text
python -B -m py_compile src\models\observability.py src\models\model_manager.py src\models\__init__.py tests\test_models_observability.py
python -B -m unittest tests.test_models_observability
python -B -m unittest tests.test_models_observability tests.test_models_structured_output tests.test_models_callers_adaptation tests.test_models_protocol tests.test_models_generate_result tests.test_models_config tests.test_models_mock_model tests.test_models_openai_compatible tests.test_models_health_verify tests.test_models_router tests.test_models_retry_errors tests.test_models_fallback_circuit
python -B -m unittest discover -s tests -p 'test_*.py'
```

结果：

- Step 11 定向测试 5 个通过。
- Models 相关回归 79 个通过。
- 全量测试 389 个通过。

当前边界：

- 不做日志轮转、压缩或跨进程日志聚合。
- cost 仅在 provider usage 与 pricing 配置同时可用时估算；否则保持 `cost=None`。
- 不默认完整记录 prompt、response 或 raw_response。
- Models 层仍不产生用户可见 events，用户提示继续由 Runtime/API/ReActExecutor 根据结构化 code/error 生成。

下一步建议：

- 进入 Step 12：模型管理基础查询接口，为后续 `/model`、`/manage-models`、Runtime/API/UI 预留后端能力。

### Step 12：模型管理基础查询接口

状态：已完成

目标：

- 为后续 `/model` 和 `/manage-models` 预留基础后端能力。
- 不做 UI。
- 不做 Session 级持久化切换。

建议主要文件：

```text
src/models/model_manager.py
src/models/config.py
tests/test_models_management_api.py
```

建议接口：

```python
list_enabled_models()
list_provider_configs(include_disabled=False)
get_provider_config(provider_conf_id)
enable_provider_config(provider_conf_id)
disable_provider_config(provider_conf_id)
verify_provider_config(provider_conf_id)
get_default_routes()
```

返回元数据字段：

```text
id
name
display_name
alias
description
provider
protocol
base_url
default_model
custom_models
enabled
status
verified_at
last_used_at
last_error_code
last_error_at
created_at
updated_at
created_by
supports_streaming
supports_json_mode
supports_embedding
supports_vision
supports_tool_calling
supports_custom_headers
max_context_tokens
tags
labels
group
sort_order
is_builtin
is_custom
is_default
is_verified
is_available_for_chat
is_available_for_embedding
is_available_for_structured_output
credential_count
active_credential_slug
metadata
```

验收标准：

- 能列出 enabled 模型。
- 能列出全部 provider configs。
- disabled provider 默认不进入 enabled 列表。
- 不返回明文 API key。
- verify 结果能反映到状态字段。

建议测试：

```text
python -B -m unittest tests.test_models_management_api
```

当前边界：

- 只做后端能力。
- Runtime/API/UI 后续负责交互。

完成记录（2026-08-10）：

已完成内容：

- `ModelManager` 新增模型管理后端接口：
  - `list_enabled_models()`
  - `list_provider_configs(include_disabled=False)`
  - `get_provider_config(provider_conf_id)`
  - `enable_provider_config(provider_conf_id)`
  - `disable_provider_config(provider_conf_id)`
  - `get_default_routes()`
- 复用既有 `verify_provider_config(provider_conf_id)`；verify 成功或失败后的 `status`、`verified_at`、错误状态可通过管理查询接口读取。
- 返回 provider config 管理元数据，包括 provider/protocol/model、能力声明、route 可用性、credential 数量与 active credential slug、tags/labels/group/sort_order、状态和默认标记。
- 管理接口不返回明文 API key、credential secret 或 headers；metadata 通过 Models 日志同一套敏感字段脱敏策略处理。
- enable/disable 仅修改当前进程内 `ModelsConfig` 和 provider model cache，不写回 `config/models/`，不做 Session 级模型切换。
- 成功调用会更新当前 ProviderConf 的 `last_used_at`；失败调用记录内存中的 `last_error_code` / `last_error_at`，供后续管理查询消费。

主要文件：

```text
src/models/model_manager.py
tests/test_models_management_api.py
```

已验证：

```text
python -B -m py_compile src\models\model_manager.py tests\test_models_management_api.py
python -B -m unittest tests.test_models_management_api
python -B -m unittest tests.test_models_management_api tests.test_models_protocol tests.test_models_config tests.test_models_generate_result tests.test_models_mock_model tests.test_models_openai_compatible tests.test_models_health_verify tests.test_models_router tests.test_models_retry_errors tests.test_models_fallback_circuit tests.test_models_structured_output tests.test_models_observability tests.test_models_callers_adaptation
python -B -m unittest discover -s tests -p 'test_*.py'
```

结果：

- Step 12 定向测试 4 个通过。
- Models 相关回归 83 个通过。
- 全量测试 393 个通过。

当前边界：

- 只提供 Models 后端查询和进程内状态变更，不提供 Runtime/API/UI 交互。
- provider enable/disable、verified_at、last_used_at、last_error 状态尚未持久化回配置文件或独立数据库。
- 不做 Session / 用户级模型选择、权限、配额、审计和管理 UI。

下一步建议：

- 进入 Step 13：上下文压缩能力，提供 `compress_context(...) -> ContextCompressionResult`，触发与持久化仍由上层负责。

### Step 13：上下文压缩能力

状态：已完成

目标：

- 提供 Models 层通用上下文压缩能力。
- 返回结构化 `ContextCompressionResult`。
- 触发和保存由其他层负责。

建议主要文件：

```text
src/models/compression.py
src/models/model_manager.py
tests/test_models_context_compression.py
```

实现要求：

- 提供 `compress_context(...) -> ContextCompressionResult`。
- 支持 `call_type=context_compression`。
- 支持输入：

```text
source_type
text
chunks
target_tokens
target_chars
preserve_keys
preserve_entities
trigger_reason
metadata
```

- 支持输出：

```text
short_summary
compressed_text
compressed_chunks
source_refs
original_length
compressed_length
original_token_count
compressed_token_count
compression_ratio
trigger_reason
round_index
loss_risk
key_points
preserved_entities
warnings
code/error
```

- 文本过长时先规则分段，再模型压缩，再整合。
- 规则只负责分段、长度控制、关键字段保护和兜底截断。
- 规则不直接生成最终语义摘要。

验收标准：

- 短文本可直接压缩。
- 长文本可分段压缩。
- 模型压缩失败时返回结构化失败或规则兜底状态。
- 输出包含可供 Memory / RAG 消费的 metadata。
- 不由 Models 层保存压缩结果。

建议测试：

```text
python -B -m unittest tests.test_models_context_compression
```

当前边界：

- 完整 Session 记忆压缩闭环后续由 Memory / Runtime / Session 层实现。

完成记录（2026-08-10）：

已完成内容：

- 新增 `src/models/compression.py`，实现 Models 层通用上下文压缩服务。
- `ModelManager` 新增 `compress_context(...) -> ContextCompressionResult`。
- 支持输入：
  - `source_type`
  - `text`
  - `chunks`
  - `target_tokens`
  - `target_chars`
  - `preserve_keys`
  - `preserve_entities`
  - `trigger_reason`
  - `metadata`
- 短文本使用单次 `generate_json(call_type=context_compression)` 生成结构化压缩结果。
- 长文本先由规则按字符上限分段，再逐段模型压缩，最后由模型整合为一个压缩上下文。
- chunks 输入会保留 `source_ref`、`chunk_id`、`original_length`、`compressed_length` 和可供 Memory / RAG 消费的 metadata。
- 模型失败默认返回结构化失败，不静默降级；调用方显式 `allow_rule_fallback=True` 时才启用规则兜底截断，并在 `warnings` / `metadata` 中标记。
- 规则只负责分段、长度控制、关键字段保护和显式兜底截断，不在默认路径生成最终语义摘要。

主要文件：

```text
src/models/compression.py
src/models/model_manager.py
src/models/__init__.py
tests/test_models_context_compression.py
```

已验证：

```text
python -B -m py_compile src\models\compression.py src\models\model_manager.py src\models\__init__.py tests\test_models_context_compression.py
python -B -m unittest tests.test_models_context_compression
python -B -m unittest tests.test_models_context_compression tests.test_models_protocol tests.test_models_mock_model tests.test_models_generate_result tests.test_models_structured_output tests.test_models_observability tests.test_models_management_api tests.test_models_config tests.test_models_openai_compatible tests.test_models_health_verify tests.test_models_router tests.test_models_retry_errors tests.test_models_fallback_circuit tests.test_models_callers_adaptation
python -B -m unittest discover -s tests -p 'test_*.py'
```

结果：

- Step 13 定向测试 5 个通过。
- Models 相关回归 88 个通过。
- 全量测试 398 个通过。

当前边界：

- 不保存压缩结果，压缩结果的持久化、引用和生命周期管理由 Memory / Runtime / Session 层负责。
- 不做完整会话记忆压缩闭环。
- 不做语义质量评测或跨轮自动触发策略。
- 默认模型失败返回结构化失败；规则兜底必须由调用方显式开启。

下一步建议：

- 进入 Step 14：embedding 接口和配置分离，实现 `embed_text()` / `embed_texts()`，并确保 embedding route 与 chat route 分离。

### Step 14：embedding 接口和配置分离

状态：已完成

目标：

- 定义并实现 embedding 结构化接口。
- embedding provider 与 chat provider 完全分离配置。
- 给后续 Memory / RAG 层使用。

建议主要文件：

```text
src/models/embeddings.py
src/models/model_manager.py
tests/test_models_embeddings.py
```

接口：

```python
embed_text(text: str) -> EmbeddingResult
embed_texts(texts: list[str]) -> EmbeddingBatchResult
```

实现要求：

- `DEFAULT_EMBEDDING_MODEL` / embedding route 与 chat route 分离。
- 同一个 provider 可以同时承担 chat 和 embedding，但 route/config 分开。
- Mock embedding 使用确定性 hash / 简单向量。
- 真实 embedding provider 缺配置返回结构化失败。

验收标准：

- 单文本 embedding 返回 `EmbeddingResult`。
- 批量 embedding 返回 `EmbeddingBatchResult`。
- Mock embedding 稳定可复现。
- embedding route 不影响 chat route。
- provider 缺 key 不崩溃。

建议测试：

```text
python -B -m unittest tests.test_models_embeddings
```

当前边界：

- 不实现完整 RAG 检索。
- 不验证真实 embedding 语义质量。

完成记录（2026-08-10）：

已完成内容：

- 新增 `src/models/embeddings.py`，提供 embedding 专属默认模型名、输入规范化、失败结果构造和 batch 聚合工具。
- `ModelManager` 新增 `embed_text(...) -> EmbeddingResult` 与 `embed_texts(...) -> EmbeddingBatchResult`。
- embedding 调用固定使用 `call_type=embedding`，默认走 `runtime.default_embedding_route`，不复用 chat route。
- 默认 mock embedding 使用 `DEFAULT_EMBEDDING_MODEL`，并继续通过确定性 hash 生成稳定向量。
- 支持通过 embedding route candidate 选择独立 provider/model；chat route 与 embedding route 可以指向同一 provider，也可以完全分离。
- 真实 provider 在未配置 embedding route candidate 或未显式传入 embedding model 时返回结构化 `missing_model_config`，不静默降级 mock。
- 底层 provider 返回失败的 `EmbeddingBatchResult` 时，`ModelManager` 保留失败语义，不再误包装为成功空 batch。
- `ProviderSpec.supports_embedding=false` 的 provider 会在 `ModelManager.embed_texts()` 内被结构化拒绝，不会发起 embedding provider 调用。
- OpenAI-compatible adapter 新增 `embed_text()` / `embed_texts()`，调用 `embeddings.create(...)`，并解析 embedding data 与 usage。
- `src/models/__init__.py` 导出 embedding 工具函数和 `DEFAULT_EMBEDDING_MODEL`。

主要文件：

```text
src/models/embeddings.py
src/models/model_manager.py
src/models/providers/openai_compatible.py
src/models/__init__.py
tests/test_models_embeddings.py
```

已验证：

```text
python -B -m py_compile src\models\embeddings.py src\models\model_manager.py src\models\providers\openai_compatible.py src\models\__init__.py tests\test_models_embeddings.py
python -B -m unittest tests.test_models_embeddings
python -B -m unittest tests.test_models_mock_model tests.test_models_openai_compatible tests.test_models_generate_result tests.test_models_callers_adaptation tests.test_models_router
python -B -m unittest tests.test_models_protocol tests.test_models_config tests.test_models_generate_result tests.test_models_callers_adaptation tests.test_models_mock_model tests.test_models_openai_compatible tests.test_models_health_verify tests.test_models_router tests.test_models_retry_errors tests.test_models_fallback_circuit tests.test_models_structured_output tests.test_models_observability tests.test_models_management_api tests.test_models_context_compression tests.test_models_embeddings
python -B -m unittest discover -s tests -p 'test_*.py'
```

结果：

- Step 14 定向测试 6 个通过。
- Models 相关回归 94 个通过。
- 全量测试 404 个通过。

当前边界：

- 不实现完整 RAG 检索、向量库写入、向量相似度搜索或 Memory/RAG 闭环。
- 不验证真实 embedding 语义质量。
- 默认配置中 `embedding` route 仍不内置真实 provider candidate；真实 embedding provider 需要用户显式配置 route candidate 或单次显式传入 embedding model。
- 目前 embedding 不接入 generate 路径的 retry/fallback/cost 日志完整编排；真实 provider HTTP timeout 仍由 provider client 配置承担。

下一步建议：

- 进入 Step 15：真实 provider opt-in 集成测试与 Models V1 验收，建立默认跳过的真实 provider 测试入口和最终 V1 验收测试。

### Step 15：真实 provider opt-in 集成测试与 Models V1 验收

状态：已完成

目标：

- 建立真实 provider 集成测试入口。
- 默认跳过真实外部调用。
- 完成 Models V1 全链路验收。

建议主要文件：

```text
tests/test_models_provider_integration.py
tests/test_models_v1_acceptance.py
src/models/Models层开发步骤与进度.md
```

真实 provider 测试执行规则：

```text
无 API key -> skip
未设置 RUN_MODEL_INTEGRATION_TESTS=true -> skip
有 key 且 RUN_MODEL_INTEGRATION_TESTS=true -> 执行
```

默认真实 provider：

```text
开发阶段默认不启用
整体系统测试阶段可启用用户配置的 OpenAI-compatible provider
密钥通过环境变量读取
不写入仓库
```

V1 验收覆盖：

```text
协议对象
配置中心
结构化 generate / stream_generate
调用方迁移
MockModel
OpenAI-compatible adapter
health_check / verify
route 参数策略
retry / fallback / 熔断
generate_json / repair
日志与脱敏
管理查询接口
context_compression
embedding
真实 provider opt-in
```

建议最终测试：

```text
python -B -m unittest tests.test_models_protocol
python -B -m unittest tests.test_models_config
python -B -m unittest tests.test_models_generate_result
python -B -m unittest tests.test_models_callers_adaptation
python -B -m unittest tests.test_models_mock_model
python -B -m unittest tests.test_models_openai_compatible
python -B -m unittest tests.test_models_health_verify
python -B -m unittest tests.test_models_router
python -B -m unittest tests.test_models_retry_errors
python -B -m unittest tests.test_models_fallback_circuit
python -B -m unittest tests.test_models_structured_output
python -B -m unittest tests.test_models_observability
python -B -m unittest tests.test_models_management_api
python -B -m unittest tests.test_models_context_compression
python -B -m unittest tests.test_models_embeddings
python -B -m unittest tests.test_models_v1_acceptance
```

真实 provider 测试：

```text
RUN_MODEL_INTEGRATION_TESTS=true python -B -m unittest tests.test_models_provider_integration
```

验收标准：

- 所有 Models V1 单元测试通过。
- Analyzer / Planner / ReActExecutor / legacy Executor 调用方适配测试通过。
- 默认无真实 key 环境下测试不访问外部 API。
- 启用真实 provider 后可完成一次 `generate()` live call。
- `verify_provider_config()` 可验证真实 provider。
- 模型调用日志脱敏。
- 缺配置、缺 key、provider 错误都返回结构化失败。

当前边界：

- 本 Step 完成后 Models V1 正式化结束。
- 后续进入 Tools 层 V1 正式化或 Runtime/API/Session 层时，再接入 UI、Session 模型选择、完整安全策略和 Memory/RAG 闭环。

完成记录（2026-08-10）：

已完成内容：

- 新增 `tests/test_models_v1_acceptance.py`，作为 Models V1 本地最终验收入口。
- 新增 `tests/test_models_provider_integration.py`，作为真实 OpenAI-compatible provider opt-in 集成测试入口。
- 本地验收覆盖：
  - 结构化 `generate()` / `stream_generate()`。
  - `generate_json()` 与 repair。
  - `health_check()` / `verify_provider_config()` 结构化状态。
  - OpenAI-compatible adapter 的 chat、stream、embedding 伪客户端路径。
  - route 默认值与 chat / embedding route 分离。
  - MockModel、context_compression、embedding。
  - 模型管理查询接口与敏感 metadata 脱敏。
  - `logs/models.log` 开发日志脱敏。
- 真实 provider 集成测试默认跳过，不会在普通单元测试和全量测试中访问外部 API。
- 真实 provider 集成测试 opt-in 环境变量：
  - `RUN_MODEL_INTEGRATION_TESTS=true`
  - `MODEL_INTEGRATION_API_KEY_ENV`，默认 `OPENAI_API_KEY`
  - `MODEL_INTEGRATION_BASE_URL`，默认 `https://api.openai.com/v1`
  - `MODEL_INTEGRATION_MODEL`，默认 `gpt-4o-mini`
  - `MODEL_INTEGRATION_EMBEDDING_MODEL`，默认 `text-embedding-3-small`
- 真实 provider 集成测试在启用后会执行一次 config health、live verify、chat generate 和 embedding smoke test。

主要文件：

```text
tests/test_models_v1_acceptance.py
tests/test_models_provider_integration.py
src/models/Models层开发步骤与进度.md
```

已验证：

```text
python -B -m py_compile tests\test_models_v1_acceptance.py tests\test_models_provider_integration.py
python -B -m unittest tests.test_models_v1_acceptance tests.test_models_provider_integration
python -B -m unittest tests.test_models_protocol tests.test_models_config tests.test_models_generate_result tests.test_models_callers_adaptation tests.test_models_mock_model tests.test_models_openai_compatible tests.test_models_health_verify tests.test_models_router tests.test_models_retry_errors tests.test_models_fallback_circuit tests.test_models_structured_output tests.test_models_observability tests.test_models_management_api tests.test_models_context_compression tests.test_models_embeddings tests.test_models_v1_acceptance tests.test_models_provider_integration
python -B -m unittest discover -s tests -p 'test_*.py'
```

结果：

- Step 15 定向测试 3 个通过，真实 provider 集成入口 1 个按预期跳过。
- Models 相关回归 97 个通过，1 个真实 provider 集成测试按预期跳过。
- 全量测试 407 个通过，1 个真实 provider 集成测试按预期跳过。

当前边界：

- Models V1 正式化到此收口。
- 默认测试不联网；真实 provider 验证必须显式 opt-in，并由环境变量提供凭证和模型名。
- 真实 provider 集成测试只覆盖轻量 smoke，不评测模型语义质量、供应商 SLA、长上下文质量或跨 provider 兼容差异。
- provider 状态、verify 结果、fallback/circuit 状态仍为进程内状态，持久化留给后续 Runtime/API/Session 层。
- 完整 Memory/RAG 闭环、UI 模型选择、用户级权限、配额与审计不属于 Models V1 当前收口。

下一步建议：

- Models V1 可以进入整体联调或验收；后续可转向 Tools 层 V1 正式化，或进入 Runtime/API/Session 层接入模型管理、Session 模型选择和 Memory/RAG 闭环。

本次收尾补充（2026-08-10）：

- 已再次对照 `Models层设计决策汇总.md`、`Models设计问题回答(1).txt`、`Models设计问题回答(2).txt` 做最终核查，未发现与 V1 目标冲突的未实现承诺。
- 已再次核查 Analyzer / Planner / ReActExecutor / legacy Executor 的模型调用链，旧字符串消费点均已通过 `require_model_content()` 或 `generate_json()` 适配，未发现绕过 Models V1 结构化结果的直接依赖。
- 已修正 `src/models/model_manager.py` 中两个仅影响静态类型准确性的返回标注：
  - `compress_context()` -> `ContextCompressionResult`
  - `embed_text()` -> `EmbeddingResult`
- 已补跑定向与全量测试，结果保持通过；真实 provider 集成入口仍按设计默认 skip，仅在 opt-in 环境下启用。

跨层补充（2026-08-17）：

- Tools 联网搜索 Step 27 已接入 `ModelCallType.WEB_SEARCH = "web_search"`，
  Models `default_route_configs()`、`config/models/routes.json` 和
  `config/models/structured_output.json` 已提供对应 route/schema。
- `ModelManager.generate_json()` 继续作为结构化调用正式入口，Tools 只消费
  `StructuredModelResult`，不在 Tools 层直接调用任何模型厂商 SDK。
- OpenAI-compatible adapter 仅在 `ProviderConf.metadata.web_search` 明确启用时
  应用 provider-specific `extra_body/tools` 映射；具体厂商请求形状不在 Models
  通用协议中硬编码。
- 该跨层扩展由 `tests/test_model_builtin_search_provider.py` 覆盖，真实模型仍默认
  不联网，后续真实联调归入 Tools Step 29。

---

## 暂停开发时更新格式

每次结束 Models 开发时，在本文档新增或更新：

```text
已完成：
- ...

当前未完成：
- ...

下一轮建议：
- 从 Step X 开始，目标是 ...
```

## Step 6 完成记录

已完成：

- `ModelManager.health_check()` 返回 `ModelHealthStatus`，默认只执行不联网的 `config_check`。
- config check 检查 provider、protocol、base_url、model 和 credential 引用，并返回结构化缺失配置与错误码。
- 新增 `verify_provider_config(provider_conf_id)`，显式执行 OpenAI-compatible 轻量 live call。
- verify 成功更新内存中的 provider 状态为 `active` 并写入 `verified_at`；失败记录结构化 code/error，状态为 `error` 或 `unverified`。
- 修复 `_resolve_credential()` 缺失 `ProviderCredential` import 的隐患。

主要文件：

```text
src/models/model_manager.py
tests/test_models_health_verify.py
```

已验证：

- `python -B -m unittest tests.test_models_health_verify`
- `python -B -m unittest tests.test_models_openai_compatible tests.test_models_config tests.test_models_generate_result`
- `python -B -m unittest discover -s tests -p 'test_*.py'`
- 结果：定向 5 个、相关回归 17 个、全量 350 个测试通过。

当前未完成：

- provider 状态和 `verified_at` 尚未持久化回配置文件。
- live verify 目前只实现 OpenAI-compatible，其他协议仍为预留。
- 尚未实现后续 JSON structured output、repair 和 schema 解析。

## 下一轮建议

从 Step 10 开始：

```text
Step 10：结构化 JSON 输出与 repair
```

优先目标：

- 建立通用 strict/lenient JSON 解析。
- 在不承担 ActionPacket 业务校验的前提下支持 repair。

## Step 7 完成记录

已完成：

- 新增 `src/models/router.py`，实现 `ModelRouter` 与 `RouteResolution`。
- 按显式 `route` 或 `call_type` 解析 route，并应用 route 参数策略。
- 保持 `user_selected` 默认策略；仅在 route 明确配置 `explicit_candidates` 时自动选择候选。
- 候选按 `priority`、`weight`、`provider_conf_id` 确定性选择，并过滤 disabled candidate/provider。
- 显式 `provider_conf_id`、`model`、`credential_slug` 优先于 route 默认值。
- 合并 route params、ProviderConf 默认参数、ProviderSpec 默认 timeout/retry 和显式调用参数。
- 按 ProviderSpec 过滤不支持的 `top_p`、`top_k`、`json_mode`，并记录 `unsupported_params`。
- 增加 ProviderSpec / ProviderConf 的 `max_context_tokens`、`max_context_chars` 限制。
- 默认超限返回结构化 `context_length_exceeded`；显式 `allow_truncation=True` 时执行受控尾部截断并记录 `truncation_used`、`dropped_chars`、`dropped_tokens`。
- `ModelManager.generate()` 与 `stream_generate()` 已接入 route resolution、动态 provider model 创建、参数合并和结果 metadata 补全。
- 导出 `ModelRouter`、`RouteResolution` 和 `ROUTE_PARAMETER_NAMES`。

主要文件：

```text
src/models/router.py
src/models/model_manager.py
src/models/config.py
src/models/protocol.py
src/models/__init__.py
tests/test_models_router.py
```

已验证：

- `python -B -m unittest tests.test_models_router`
- `python -B -m unittest tests.test_models_generate_result tests.test_models_openai_compatible tests.test_models_health_verify`
- `python -B -m unittest tests.test_models_protocol tests.test_models_config tests.test_models_mock_model tests.test_models_callers_adaptation`
- `python -B -m unittest discover -s tests -p 'test_*.py'`
- 结果：Step 7 定向 9 个、Models 相关回归 40 个、全量 359 个测试全部通过。

当前边界：

- Step 7 只负责一次调用的 route 选择、参数合并、能力过滤和上下文限制。
- Step 8 已补充 timeout/retry，Step 9 已补充 fallback、多候选失败切换和 circuit。
- `allow_truncation` 当前是通用字符级尾部截断策略；不会自动理解或保护 ActionPacket、Planner 计划、工具结果等业务结构。
- provider 状态和 `verified_at` 仍只在内存中更新，尚未持久化回配置文件。

下一步建议：

- 从 Step 10 开始，实现通用 JSON strict/lenient 解析与 repair。

## Step 8 完成记录

已完成：

- `ModelErrorCode` 增加 `temporary_unavailable`，并建立 retryable/non-retryable 错误码集合。
- 在 `src/models/errors.py` 增加统一错误分类：
  - HTTP status 优先。
  - provider error code 次之。
  - provider error message / hint 再次之。
  - 本地错误码作为最后兜底。
- `ModelErrorInfo.from_provider_error()` 统一生成 category、retriable、fallback_allowed、provider 错误字段。
- 新增 `src/models/retry.py`：
  - `RetryPolicy`
  - 最多 5 次 retry 限制。
  - 指数退避及最大退避时间。
  - retryable 错误判断。
- `ModelManager.generate()` 接入 timeout、retry、attempts、latency 和 retry metadata。
- timeout 通过标准库执行器约束单次调用；retry 保持同一个 route/provider/model，不提前引入 fallback。
- OpenAI-compatible provider 改用统一错误分类，并识别 503 为 `temporary_unavailable`。

主要文件：

```text
src/models/errors.py
src/models/retry.py
src/models/protocol.py
src/models/model_manager.py
src/models/providers/openai_compatible.py
src/models/__init__.py
tests/test_models_retry_errors.py
```

已验证：

- `python -B -m unittest tests.test_models_retry_errors`
- `python -B -m unittest tests.test_models_protocol tests.test_models_generate_result tests.test_models_openai_compatible tests.test_models_health_verify`
- `python -B -m unittest tests.test_models_config tests.test_models_mock_model tests.test_models_callers_adaptation tests.test_models_router tests.test_models_retry_errors`
- `python -B -m unittest discover -s tests -p 'test_*.py'`
- 结果：Step 8 定向 7 个、Models 相关回归 56 个、全量 366 个测试全部通过。

当前边界：

- 当前 retry 只在 `ModelManager.generate()` 主调用路径生效，`stream_generate()` 暂不做完整的流式重试编排。
- timeout 使用线程执行器限制等待时间；Python 无法强制终止已运行的 provider 线程，真实 provider 仍应配置自身 HTTP timeout。
- Step 9 已实现内存 fallback、候选失败切换和轻量 circuit，但不做持久化冷却状态。

下一步建议：

- 从 Step 10 开始，实现通用 JSON strict/lenient 解析与 repair。

## Step 9 完成记录

已完成：

- 新增 `CandidateHealthRegistry` 和 `CandidateHealthState`，提供内存 candidate health 状态。
- `ModelRouter` 支持跳过：
  - disabled candidate/provider。
  - 配置中的 `cooldown_until`。
  - 内存 credential/model/candidate cooldown。
  - 内存 `circuit_open` candidate。
- `ModelManager.generate()` 支持在 `explicit_candidates` route 中按候选顺序执行保守 fallback。
- 保持用户显式 `provider_conf_id/model/credential_slug` 优先，不自动改用户指定模型。
- fallback 错误边界：
  - `blocked_by_policy`。
  - `invalid_prompt`。
  - `user_cancelled`。
  这些错误不会绕过当前候选进行 fallback。
- 冷却粒度：
  - authentication/permission/missing key/quota：只冷却 credential。
  - `model_not_found`：只冷却 provider_conf_id + model。
  - timeout/rate limit/network/5xx：冷却当前 route candidate，并累计 candidate circuit failure。
- 成功候选会清理对应的 cooldown/circuit 状态，支持恢复。
- 成功 fallback 结果写入 `fallback_used`、`fallback_reason`、`fallback_history` 和 `fallback_attempts`。

主要文件：

```text
src/models/router.py
src/models/retry.py
src/models/model_manager.py
src/models/__init__.py
tests/test_models_fallback_circuit.py
```

已验证：

- `python -B -m unittest tests.test_models_fallback_circuit`
- `python -B -m unittest tests.test_models_fallback_circuit tests.test_models_router tests.test_models_retry_errors`
- `python -B -m unittest tests.test_models_protocol tests.test_models_config tests.test_models_generate_result tests.test_models_openai_compatible tests.test_models_health_verify tests.test_models_mock_model tests.test_models_callers_adaptation`
- `python -B -m unittest discover -s tests -p 'test_*.py'`
- 结果：Step 9 定向 6 个、Step 7/8/9 联合 22 个、Models 相关回归 40 个、全量 372 个测试全部通过。

当前边界：

- V1 只实现内存状态，不持久化 cooldown/circuit。
- 不做复杂加权轮询、全局负载均衡或跨进程熔断。
- fallback 当前接入 `ModelManager.generate()` 主路径，`stream_generate()` 暂不做完整的流式 fallback 编排。
- 当前 route candidates 仍由配置预先声明，不根据自然语言自动推断 provider。

下一步建议：

- 从 Step 10 开始，实现通用 JSON strict/lenient 解析、fenced JSON 提取与 repair。
