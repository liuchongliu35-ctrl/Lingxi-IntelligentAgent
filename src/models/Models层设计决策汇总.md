# Models 层设计决策汇总

本文档汇总 Models 层 V1 正式化的设计决策，依据：

- `src/models/Models设计问题回答(1).txt`
- `src/models/Models设计问题回答(2).txt`
- `src/models/模型层支持添加自定义模型的想法.md`
- 当前已完成的 Analyzer V1、Planner V1、ReActExecutor V1 第二阶段主链路约束

本文只描述 Models 层设计，不拆开发步骤。后续 `Models层开发步骤与进度.md` 应基于本文档拆分可验收 Step。

---

## 1. 总体定位

### 1.1 Models 层职责

Models 层是 Agent 的统一模型服务层，负责：

```text
调用请求封装
provider / route 选择
模型 API 调用
结构化结果返回
JSON 通用解析与 repair
模型调用错误封装
timeout / retry / fallback / 熔断基础
usage / latency / cost / trace 记录
health_check / verify
embedding 接口
上下文压缩能力
MockModel 测试能力
```

Models 层不负责：

```text
不重新判断用户意图
不生成业务计划
不校验 ActionPacket 业务规则
不决定工具是否执行
不直接调用 Tool 层
不执行 shell
不生成 Observation
不做完整安全与权限策略
不做完整 Runtime / API / Session 管理
```

### 1.2 与主链路的关系

当前主编排链路保持不变：

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

Models 层只提供模型能力，是项目级基础模型服务层。Analyzer、Planner、ReActExecutor、legacy Executor 以及后续 Memory / RAG 都可以调用 Models 层，并且都必须消费 Models 层的结构化结果。

关键原则：

```text
本项目目标是模型作为大脑的灵活任务型 Agent，不是规则硬编码结果的固定流程助手。
```

因此，凡是涉及理解、判断、生成、决策、总结、修复的场景，都应优先保留或调用 Models 层；规则只负责协议校验、安全边界、错误分类、timeout/retry/fallback 策略和测试兜底。后续开发不得用固定规则替代复杂计划生成、ActionPacket 决策、模型总结或最终自然语言回答。

关键边界：

```text
模型原始输出
  -> Models 层转换成 ModelCallResult / StructuredModelResult
  -> 调用方检查 success/code/content/data
  -> ReActExecutor 校验 ActionPacket
  -> ReActExecutor 通过 Tool / Model / User / Control 执行
  -> ReActExecutor 根据真实结果生成 Observation
```

Models 层可以帮助把模型输出解析成 JSON，但不能把 JSON 解释成“可执行命令”。ActionPacket 的业务校验继续属于 ReActExecutor。

---

## 2. V1 核心原则

### 2.1 结构化结果是唯一正式接口

V1 废弃裸字符串作为正式模型服务接口。

正式接口：

```python
generate(...) -> ModelCallResult
stream_generate(...) -> Iterator[ModelStreamChunk]
generate_json(...) -> StructuredModelResult
health_check(...) -> ModelHealthStatus
verify_provider_config(...) -> ModelHealthStatus
embed_text(...) -> EmbeddingResult
embed_texts(...) -> EmbeddingBatchResult
compress_context(...) -> ContextCompressionResult
```

模型文本放在 `content` 字段中，调用状态、错误、provider、model、route、usage、耗时、trace 等信息放在独立字段。

调用方必须：

```text
先判断 result.success
再读取 result.content 或 result.data
失败时读取 result.code / result.error / result.retriable / result.fallback_used
```

禁止：

```text
把错误字符串塞进 content 当作正常模型回答
让上层把模型调用失败误判为模型正常回答
```

### 2.2 不静默降级到 mock

MockModel 只在显式配置为 mock 或测试 fixture 明确注入时使用。

如果调用方请求真实模型，但出现：

```text
配置文件不存在
provider 未启用
provider_conf_id 不存在
model 缺失
base_url 缺失
API key 缺失
credential_ref 不可用
```

Models 层返回：

```python
ModelCallResult(
    success=False,
    content="",
    code="missing_model_config" 或 "missing_api_key",
    error="...",
)
```

Agent 启动不应因为真实 provider 缺 key 崩溃；真实调用时再返回结构化失败，让 Runtime/API/UI 提示用户配置模型。

### 2.3 OpenAI-compatible 是 V1 主协议

V1 正式实现：

```text
openai-compatible
```

V1 只预留，不完整实现：

```text
anthropic-compatible
gemini-compatible
custom-mapping
```

含义：

```text
Claude / Gemini 如果通过 OpenAI-compatible 网关接入，可以在 V1 使用。
Claude 官方 Anthropic API、Gemini 官方原生 API 不作为 V1 主路径。
非标准 Request Body / Response JSON Path 映射只预留字段，不实现完整模板引擎。
```

Qianwen / 自托管模型 / 第三方兼容端点优先走 OpenAI-compatible endpoint。DashScope 原生接口可作为后续 provider variant。

### 2.4 route 默认不自动换模型

用户选定模型作为默认模型。

route 默认表示“调用类型和调用参数策略”，不是天然表示“换模型”：

```text
react_action_decision -> 用户选定模型 + 低 temperature + 严格 JSON
react_call_model      -> 用户选定模型 + 普通生成参数
summary               -> 用户选定模型 + summary 参数
```

只有配置中明确指定不同 route candidate 时，才允许不同任务使用不同模型。

---

## 3. 协议与数据结构

### 3.1 ModelMessage

用于统一 prompt / chat messages。

```python
ModelMessage:
  role: "system" | "user" | "assistant" | "tool"
  content: str
  name: str | None
  metadata: dict
```

设计规则：

```text
prompt: str 可自动转换为单条 user message
后续模板 Prompt 应输出 ModelMessage 列表
provider adapter 负责转换为目标协议格式
```

### 3.2 ModelCallType

模型调用类型必须枚举化，用于 route、日志、参数策略、成本、prompt 模板。

V1 初始枚举：

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

`web_search` 是 Tools V1 联网搜索的最小跨层扩展调用类型。它只表示“通过 Models 层调用支持联网能力的模型并要求结构化搜索 JSON”，不表示 Models 层直接执行 Tool，也不表示 Models 层生成 ToolResult。具体厂商如何开启联网参数由 provider adapter 根据配置和能力处理。

### 3.3 ModelCallOptions

一次模型调用的通用选项。

```python
ModelCallOptions:
  call_type: ModelCallType
  route: str | None
  provider_conf_id: str | None
  credential_slug: str | None
  model: str | None
  messages: list[ModelMessage] | None
  prompt: str | None
  temperature: float | None
  top_p: float | None
  top_k: int | None
  max_tokens: int | None
  timeout_seconds: float | None
  max_retries: int | None
  json_mode: bool | None
  response_format: str | None
  allow_fallback: bool
  allow_retry: bool
  allow_external_provider: bool | None
  sensitive_content_policy: str | None
  redact_before_send: bool | None
  trace_context: ModelTraceContext | None
  metadata: dict
```

注意：

```text
top_k 不是所有 provider 都支持。adapter 根据 ProviderSpec 能力决定是否传递。
allow_external_provider / sensitive_content_policy / redact_before_send 是安全层预留字段，V1 不实现完整安全策略。
```

### 3.3.1 ModelTraceContext

模型调用必须能挂接 Analyzer / Planner / ReActExecutor 的 trace 信息，便于从一次 Agent 执行回溯到具体模型请求。

```python
ModelTraceContext:
  source_trace_id: str | None
  conversation_id: str | None
  session_id: str | None
  plan_id: str | None
  execution_id: str | None
  task_id: str | None
  step_id: str | None
  packet_id: str | None
  parent_request_id: str | None
  caller: str | None
  metadata: dict
```

设计规则：

```text
调用方能传多少就传多少，不强制所有字段必填
Models 层必须生成自己的 model_request_id
provider 返回的请求 id 进入 provider_request_id
日志必须记录 trace_context 的关键字段
```

### 3.3.2 ModelUsage / ModelCost

usage 和 cost 单独建模，不能散落为多个松散字段。

```python
ModelUsage:
  prompt_tokens: int | None
  completion_tokens: int | None
  total_tokens: int | None
  reasoning_tokens: int | None
  cached_tokens: int | None
  source: "provider" | "estimated" | "none"
  metadata: dict
```

```python
ModelCost:
  input_cost: float | None
  output_cost: float | None
  total_cost: float | None
  currency: str | None
  pricing_source: "pricing_config" | "provider" | "none"
  metadata: dict
```

V1 不强制估算成本。provider 不返回 usage 或价格表缺失时，`usage` 可部分为空，`cost` 可为 None。

### 3.3.3 ModelErrorInfo

错误信息需要归一化，供 retry / fallback / 熔断 / 日志共用。

```python
ModelErrorInfo:
  code: str
  message: str
  category: str
  retriable: bool
  fallback_allowed: bool
  cooldown_scope: str | None
  http_status: int | None
  provider_error_code: str | None
  provider_error_message: str | None
  provider_error_hint: str | None
  raw_error_preview: str | None
  metadata: dict
```

ModelCallResult 可以直接暴露 `code/error/retriable`，内部也应保留 ModelErrorInfo，避免 provider 错误解析逻辑散落在各处。

### 3.4 ModelCallResult

`generate()` 的正式返回。

```python
ModelCallResult:
  success: bool
  content: str
  code: str | None
  error: str | None
  provider: str | None
  protocol: str | None
  provider_conf_id: str | None
  credential_slug: str | None
  model: str | None
  route: str | None
  call_type: str | None
  request_id: str
  source_trace_id: str | None
  trace_context: ModelTraceContext | None
  model_request_id: str
  provider_request_id: str | None
  latency_ms: int | None
  usage: ModelUsage | None
  cost: ModelCost | None
  attempts: int
  retriable: bool
  fallback_used: bool
  fallback_reason: str | None
  error_info: ModelErrorInfo | None
  selected_candidate: RouteCandidateSnapshot | None
  raw_response: object | None
  raw_response_preview: str | None
  raw_response_hash: str | None
  metadata: dict
```

设计规则：

```text
content 只放模型真实生成内容
error/code 只放模型调用层失败
raw_response 默认不写入日志，可留在内存结果对象中
cost 可为 None，不强制估算
```

### 3.5 ModelStreamChunk / ModelStreamResult

`stream_generate()` 输出结构化 chunk，不输出裸字符串。

```python
ModelStreamChunk:
  success: bool
  content_delta: str
  index: int
  is_final: bool
  code: str | None
  error: str | None
  request_id: str
  provider: str | None
  model: str | None
  metadata: dict
```

流式结束后需要形成最终状态：

```python
ModelStreamResult:
  success: bool
  content: str
  code: str | None
  error: str | None
  request_id: str
  provider: str | None
  model: str | None
  chunks_count: int
  latency_ms: int | None
  usage: ModelUsage | None
  cost: ModelCost | None
  metadata: dict
```

V1 只保留同步 generator 形态，不做复杂异步 provider streaming 协议。

Models 层不直接把 token chunk 暴露成用户可见事件；Runtime / ReActExecutor 决定哪些 chunk 转成用户事件。

结构化 JSON 输出不做流式解析。需要 JSON 的场景使用非流式 `generate_json()`。

### 3.6 StructuredModelResult

`generate_json()` 的正式返回。

```python
StructuredModelResult:
  success: bool
  data: dict | list | None
  content: str
  code: str | None
  error: str | None
  parse_mode: "strict" | "lenient"
  schema_name: str | None
  schema_valid: bool | None
  repair_attempts: int
  model_result: ModelCallResult
  raw_json_text: str | None
  metadata: dict
```

Models 层负责：

```text
调用模型
提取 JSON object / array
解析 JSON
按通用 schema 轻量校验
失败时构造通用 repair prompt
返回 StructuredModelResult
```

Models 层不负责：

```text
判断 ActionPacket 是否可执行
判断 tool_name 是否允许
判断参数是否危险
生成 Observation
```

### 3.7 ModelHealthStatus

`health_check()` 和 `verify_provider_config()` 返回统一结构。

```python
ModelHealthStatus:
  healthy: bool
  provider_conf_id: str | None
  provider: str | None
  protocol: str | None
  model: str | None
  configured: bool
  missing_config: list[str]
  check_type: "config_check" | "live_check"
  latency_ms: int | None
  error: str | None
  code: str | None
  verified_at: str | None
  metadata: dict
```

规则：

```text
health_check 默认 config_check，不访问外部 API
verify_provider_config 显式 live_check，发送轻量真实请求
```

### 3.8 EmbeddingResult

Embedding 与 chat 完全分离配置和 route。

```python
EmbeddingResult:
  success: bool
  embedding: list[float] | None
  dimensions: int | None
  code: str | None
  error: str | None
  provider_conf_id: str | None
  model: str | None
  usage: ModelUsage | None
  metadata: dict
```

```python
EmbeddingBatchResult:
  success: bool
  embeddings: list[list[float]]
  item_results: list[EmbeddingResult]
  code: str | None
  error: str | None
  metadata: dict
```

Mock embedding 使用确定性 hash / 简单向量，只用于测试稳定性，不代表真实语义检索质量。

### 3.9 ContextCompressionResult

上下文压缩是 Models 层能力，但触发和保存由其他层负责。

```python
ContextCompressionResult:
  success: bool
  short_summary: str
  compressed_text: str
  compressed_chunks: list[CompressedChunkRef]
  source_refs: list[str]
  original_length: int | None
  compressed_length: int | None
  original_token_count: int | None
  compressed_token_count: int | None
  compression_ratio: float | None
  trigger_reason: str | None
  round_index: int | None
  loss_risk: "low" | "medium" | "high" | None
  key_points: list[str]
  preserved_entities: list[str]
  warnings: list[str]
  code: str | None
  error: str | None
  model_result: ModelCallResult | None
  metadata: dict
```

压缩策略：

```text
主路径：调用模型进行 summary / compression
工程兜底：规则化分段、长度控制、保留关键字段、失败码
规则不直接生成最终压缩语义，只负责让输入能被模型有效压缩
```

---

## 4. 配置中心设计

### 4.1 配置目录

V1 使用：

```text
config/models/
  models_config.json
  provider_specs.json
  provider_confs.json
  routes.json
  pricing.json
  structured_output.json
```

旧 `MODEL_NAME` 不再作为正式配置入口。

### 4.1.1 配置加载顺序与校验

配置加载必须有固定顺序，避免 env、默认值、用户配置互相覆盖时行为不透明。

建议加载顺序：

```text
1. 内置默认 ProviderSpec
2. config/models/provider_specs.json
3. config/models/provider_confs.json
4. config/models/routes.json
5. config/models/models_config.json
6. config/models/pricing.json
7. config/models/structured_output.json
8. 环境变量覆盖
9. Runtime/API 显式传入的单次调用 override
```

校验规则：

```text
provider_conf_id 必须唯一
credential_slug 在同一个 ProviderConf 内必须唯一
enabled=false 的 ProviderConf 不进入默认路由
protocol 不支持时返回 unsupported_protocol
provider 不存在时返回 unsupported_provider
真实 provider 缺 key 不阻止 Agent 启动，但 health_check 标记 missing_api_key
mock 只能通过显式 ProviderConf 或 fixture 启用
```

配置加载失败不能用 mock 悄悄顶替真实 provider，应返回结构化配置错误。

### 4.2 models_config.json

保存全局模型层开关和默认 route。

```json
{
  "default_chat_route": "chat",
  "default_structured_route": "structured",
  "default_embedding_route": "embedding",
  "default_compression_route": "context_compression",
  "default_mock_enabled": true,
  "real_provider_enabled_by_default": false,
  "max_retries": 5,
  "retry_backoff_base_seconds": 0.5,
  "retry_backoff_max_seconds": 8,
  "logs_path": "logs/models.log",
  "log_full_prompt": false,
  "log_full_response": false
}
```

默认真实 provider 可写入配置基线，但开发阶段默认不启用；整体系统测试阶段可通过配置启用。

### 4.3 ProviderSpec

ProviderSpec 是静态能力注册表，描述一类 provider / protocol 的天然能力。

```json
{
  "provider": "openai",
  "protocol": "openai-compatible",
  "display_name": "OpenAI",
  "default_base_url": "https://api.openai.com/v1",
  "default_model": "gpt-4o-mini",
  "supports_streaming": true,
  "supports_json_mode": true,
  "supports_tool_calling": false,
  "supports_embedding": true,
  "supports_vision": false,
  "supports_custom_headers": true,
  "supports_top_p": true,
  "supports_top_k": false,
  "default_timeout_seconds": 60,
  "default_max_retries": 5,
  "request_adapter": "openai_compatible",
  "response_adapter": "openai_compatible",
  "known_model_prefixes": ["gpt-", "o"],
  "tags": ["builtin", "openai-compatible"],
  "metadata": {}
}
```

V1 ProviderSpec 至少支持：

```text
mock
openai
qianwen
doubao
custom_openai_compatible
```

`anthropic-compatible`、`gemini-compatible`、`custom-mapping` 可进入枚举和字段预留，但不完整实现 adapter。

### 4.4 ProviderConf

ProviderConf 是运行时配置，描述用户实际启用的 endpoint / key / model。

```json
{
  "id": "conf_my_openai_compatible",
  "name": "My OpenAI Compatible Model",
  "provider": "custom",
  "protocol": "openai-compatible",
  "enabled": true,
  "base_url": "https://example.com/v1",
  "default_model": "my-model",
  "custom_models": ["my-model", "my-model-long"],
  "credentials": [
    {
      "slug": "default",
      "api_key_env": "MY_MODEL_API_KEY",
      "credential_ref": null,
      "enabled": true,
      "status": "active",
      "last_error_code": null,
      "last_error_at": null
    }
  ],
  "headers": {},
  "timeout_seconds": 60,
  "max_retries": 5,
  "temperature": null,
  "top_p": null,
  "max_tokens": null,
  "max_context_tokens": null,
  "supports_streaming": null,
  "supports_json_mode": null,
  "status": "active",
  "verified_at": null,
  "last_used_at": null,
  "tags": ["custom"],
  "metadata": {}
}
```

设计规则：

```text
id/provider_conf_id 是稳定主键，不用显示名参与路由
credential_slug 在单个 ProviderConf 内部稳定唯一
name/display_name/alias 只做展示
enabled=false 的 ProviderConf 不参与普通路由
status 可为 active / inactive / error / unverified
```

### 4.5 Credential 策略

开发/内置默认 provider：

```text
优先使用环境变量
ProviderConf 写 api_key_env
不把 key 写入 JSON
```

用户自定义 provider：

```text
后续 Runtime/API/UI 接入时使用本地加密 credential_ref
ProviderConf 只保存 credential_ref
不明文落盘
```

本地加密凭证存储设计要求：

```text
API key 不写入 provider_confs.json
credential_ref 指向本地凭证项
凭证项至少保存 provider_conf_id、credential_slug、encrypted_secret、created_at、updated_at
加密算法后续优先 AES-256 或系统级安全凭证存储
日志、异常、health_check、verify 结果不得输出明文 secret
```

V1 如果暂未实现本地加密凭证存储，也必须在字段上预留 `credential_ref`，并保证日志脱敏。

### 4.6 routes.json

route 按调用类型配置。

```json
{
  "react_action_decision": {
    "default_model_policy": "user_selected",
    "params": {
      "temperature": 0.1,
      "top_p": 0.9,
      "max_tokens": 1200,
      "json_mode": true,
      "timeout_seconds": 60
    },
    "candidates": []
  },
  "summary": {
    "default_model_policy": "user_selected",
    "params": {
      "temperature": 0.3,
      "max_tokens": 2000
    },
    "candidates": []
  },
  "embedding": {
    "default_model_policy": "explicit_candidates",
    "params": {},
    "candidates": [
      {
        "provider_conf_id": "conf_embedding_default",
        "credential_slug": "default",
        "model": "text-embedding-model"
      }
    ]
  }
}
```

候选结构：

```python
RouteCandidate:
  provider_conf_id: str
  credential_slug: str
  model: str
  weight: int | None
  priority: int | None
  enabled: bool
  cooldown_until: str | None
  metadata: dict
```

默认原则：

```text
route 先应用参数策略
如果 candidates 为空，使用用户选定模型 / 默认模型
只有 candidates 显式配置时，route 才自动切换模型
```

### 4.7 pricing.json

V1 保留 cost 字段，但成本估算可为 None。

```json
{
  "prices": [
    {
      "provider": "openai",
      "model": "gpt-4o-mini",
      "input_price_per_1k": null,
      "output_price_per_1k": null,
      "currency": "USD"
    }
  ]
}
```

规则：

```text
provider 返回 usage -> 记录 usage
配置表有价格且 usage 可用 -> 计算 cost
否则 cost=None
不引入复杂 tokenizer 做精确估算
```

### 4.8 structured_output.json

保存 JSON 输出策略。

```json
{
  "default_parse_mode": "lenient",
  "max_repair_attempts": 1,
  "schemas": {
    "action_packet": {
      "mode": "strict",
      "business_validation_owner": "ReActExecutor"
    },
    "planner_plan": {
      "mode": "lenient"
    }
  }
}
```

业务校验归属必须写清楚。ActionPacket 的业务校验不进 Models 层。

---

## 5. Provider 与 Adapter 设计

### 5.1 Provider 抽象

Provider 管：

```text
API 地址
鉴权方式
请求格式
响应解析方式
streaming 解析
usage 解析
provider 错误解析
```

建议内部接口：

```python
BaseProvider:
  chat(messages, options, provider_conf, credential) -> ProviderResponse
  chat_stream(messages, options, provider_conf, credential) -> Iterator[ProviderStreamChunk]
  embed(texts, options, provider_conf, credential) -> ProviderEmbeddingResponse
  health_check(provider_conf, credential) -> ModelHealthStatus
```

ProviderResponse 再由 ModelManager / adapter 归一化成 ModelCallResult。

### 5.2 OpenAI-compatible adapter

OpenAI-compatible 是 V1 主 adapter。

请求基本格式：

```json
{
  "model": "model-id",
  "messages": [
    {"role": "system", "content": "..."},
    {"role": "user", "content": "..."}
  ],
  "temperature": 0.1,
  "top_p": 0.9,
  "max_tokens": 1200
}
```

响应基本解析：

```text
choices[0].message.content -> content
usage.prompt_tokens -> prompt_tokens
usage.completion_tokens -> completion_tokens
usage.total_tokens -> total_tokens
id -> provider_request_id
```

OpenAI provider 使用官方 `openai` SDK 新客户端方式，不能使用全局 `openai.api_key` 状态。

Qianwen / Doubao 如果可通过 OpenAI-compatible endpoint 接入，优先走统一 adapter；原生接口后续再做 provider variant。

### 5.3 自定义 OpenAI-compatible provider

用户自定义 provider 本质是用户新增 ProviderConf：

```text
base_url
api_key / credential_ref
model_id / default_model
protocol=openai-compatible
headers
timeout
max_context_tokens
supports_streaming
supports_json_mode
```

V1 可以不做完整 UI，但必须支持配置结构、加载、verify、route 使用。

### 5.4 Verify

`verify_provider_config(provider_conf_id)` 做 live_check。

验证内容：

```text
base_url 可访问
credential 可读取
鉴权有效
model_id 可调用
响应结构可解析成 ModelCallResult
可选验证 streaming / json mode
```

验证失败：

```text
返回 ModelHealthStatus(success=false/code/error)
ProviderConf 可保持 unverified 或 error
不应把失败配置标记为 active
```

---

## 6. 路由、fallback、retry、熔断

### 6.1 路由选择顺序

一次调用的 route 选择建议顺序：

```text
1. 调用方显式传入 provider_conf_id / model / credential_slug
2. 调用方显式传入 route
3. call_type 对应 routes.json
4. 用户选定默认模型
5. 全局默认 route
6. 显式 mock route
7. 返回 missing_model_config
```

普通自然语言不能直接绕过路由策略切换 provider。后续 Runtime/API/UI 可提供明确模型选择入口。

### 6.2 retry

默认最多 5 次，指数退避。

可重试：

```text
timeout
rate_limited
network_error
provider_server_error
temporary_unavailable
```

不可重试：

```text
missing_api_key
missing_model_config
authentication_failed
permission_denied
invalid_prompt
blocked_by_policy
user_cancelled
```

`schema_invalid / invalid_json` 不盲目 retry，应先走 JSON repair。

### 6.3 fallback

fallback 是当前 route candidate 失败后的备用模型/备用路由切换策略。

可 fallback：

```text
missing_api_key
timeout
rate_limited
provider_server_error
network_error
model_not_found
```

谨慎 fallback：

```text
schema_invalid -> 先 repair，再视情况 fallback
invalid_json -> 先 repair，再视情况 fallback
```

不可 fallback：

```text
blocked_by_policy
invalid_prompt
user_cancelled
```

### 6.4 错误归一化

统一错误码至少包含：

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

归一化优先级：

```text
HTTP status
provider error_code
provider error message / hint
本地归一化错误码
```

provider 自定义错误码和错误消息只作为辅助判断，不让 provider 特有格式污染上层协议。

### 6.5 熔断与冷却粒度

V1 做轻量内存熔断，不做复杂分布式限流。

冷却规则：

```text
401 / 403 / authentication_failed / permission_denied:
  冷却 credential_slug

404 / model_not_found:
  冷却 provider_conf_id + model

429 / rate_limited:
  retry + 视情况 fallback
  按 provider 返回信息冷却 credential_slug 或 provider_conf_id + model

quota_exceeded:
  优先冷却 credential_slug

5xx / timeout / network_error:
  冷却 route candidate，短冷却

连续失败 N 次:
  route candidate 标记 circuit_open

verify / health live_check 成功:
  恢复候选
```

### 6.6 上下文长度检查

Models 层需要根据 ProviderSpec / ProviderConf / model 的 `max_context_tokens` 或 `max_context_chars` 做最后防线检查。

规则：

```text
调用方负责构造合理上下文
Memory / Session 负责历史压缩和检索
Models 层只做最后长度检查
默认不擅自删除 prompt 中的关键结构字段
```

超限处理：

```text
如果调用方允许压缩:
  调用 context_compression route 或返回需要压缩的结构化错误

如果调用方不允许压缩:
  返回 context_length_exceeded

如果必须截断:
  只能在调用方明确 allow_truncation 时做，并记录 truncation_used / dropped_chars / dropped_tokens
```

不得默认截断 ActionPacket、计划、工具结果等结构化关键字段。

### 6.7 本地限流预留

V1 不做复杂本地限流系统，但要预留字段并识别 provider 限流。

配置预留：

```text
requests_per_minute
tokens_per_minute
concurrency_limit
cooldown_seconds
```

V1 行为：

```text
provider 返回 429 -> 归一化为 rate_limited
rate_limited 可 retry / fallback / cooldown
Runtime/API 层后续统一做并发和用户级额度控制
```

---

## 7. JSON 输出与 repair

### 7.1 strict / lenient

支持两种模式：

```text
strict:
  只接受纯 JSON 或明确 JSON schema 输出

lenient:
  允许从文本中提取第一个 JSON object / array
```

不同 call_type 可配置不同模式：

```text
react_action_decision -> strict
planner_structured_plan -> lenient 或 strict
summary -> 不要求 JSON
```

### 7.2 repair

repair 是通用格式修复，不是业务修复。

repair 输入：

```text
原始模型输出
解析错误
目标 schema 摘要
```

repair 输出仍走 `StructuredModelResult`。

最大 repair 次数由配置控制，默认 1。

### 7.3 ActionPacket 边界

Models 层最多校验：

```text
是不是 JSON
是不是 object / array
有没有基础字段
是否满足通用 JSON schema
```

ReActExecutor 继续校验：

```text
action 类型是否合法
tool_name 是否存在
args 是否符合 Tool schema
是否需要用户确认
是否允许执行
如何生成 Observation
```

---

## 8. 上下文压缩设计

### 8.1 分工

上下文压缩是 Models 层能力，但不是 Models 层独立流程。

```text
触发判断:
  Analyzer / Planner / ReActExecutor / Runtime / Session / Memory 根据长度、轮次、token 阈值判断

压缩执行:
  Models 层提供 context_compression route 和 compress_context 接口

结果保存:
  Memory / Session 层负责保存、索引、替换历史上下文
```

### 8.2 压缩输入

```python
ContextCompressionRequest:
  source_type: "session" | "planner" | "executor" | "tool_observation" | "memory"
  text: str
  chunks: list[ContextChunk]
  target_tokens: int | None
  target_chars: int | None
  preserve_keys: list[str]
  preserve_entities: list[str]
  trigger_reason: str
  metadata: dict
```

### 8.3 分段策略

如果文本过长：

```text
先按规则分段
每段调用模型压缩
再调用模型或规则整合
最终返回 ContextCompressionResult
```

规则只做：

```text
长度控制
分段
关键字段保护
必要元信息保留
兜底截断
```

规则不负责生成最终语义摘要。

### 8.4 V1 边界

V1 Models 层提供压缩接口、结果结构和 route。

完整的 Session 记忆压缩流程属于后续 Memory / Runtime / Session 层增强，不在 Models V1 内完成闭环。

---

## 9. Health、Verify、启动诊断

### 9.1 health_check

默认只做 config_check：

```text
provider_conf 是否存在
provider 是否支持
protocol 是否支持
base_url 是否存在
model 是否存在
credential 引用是否存在
必填字段是否完整
```

不访问外部 API，不消耗额度，不阻塞启动。

### 9.2 verify_provider_config

显式 live_check：

```text
发送极简请求，例如 user: hi
检查鉴权
检查模型 ID
检查响应结构
记录 latency/code/error
更新 verified_at/status
```

只有用户点击“验证模型”或 Runtime/API 显式调用时执行。

### 9.3 正式调用

正式 `generate()` 发现配置完整但 provider 真实失败时，返回结构化错误，不改写为 mock。

---

## 10. 日志与可观测性

### 10.1 日志文件

Models 层独立日志：

```text
logs/models.log
```

格式建议 JSONL。

### 10.2 日志字段

每次模型调用至少记录：

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

默认不记录完整 prompt，不记录完整长响应。

### 10.3 脱敏

日志和错误元数据必须脱敏：

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

V1 不默认改写业务 prompt。是否允许发送敏感内容给外部 provider，后续交给安全与权限层统一处理。

### 10.4 用户可见事件与开发日志分离

Models 层只产出结构化结果和开发日志，不直接决定用户可见事件。

```text
Models 层:
  ModelCallResult / ModelStreamChunk / logs/models.log

ReActExecutor / Runtime:
  根据模型结果决定是否生成 ExecutionEvent / message_delta / error event
```

规则：

```text
provider token chunk 不直接等于用户事件
模型错误不直接写成用户可见文本
用户可见提示由 Runtime/API/ReActExecutor 根据 code/error 生成
开发诊断信息进入 logs/models.log
```

---

## 11. Mock 与测试模型

### 11.1 MockModel 定位

MockModel 是测试和开发占位 provider，不是失败兜底 provider。

MockModel 应支持结构化响应：

```text
普通文本
Analyzer JSON
Planner JSON
ActionPacket JSON
summary
context_compression
embedding 占位向量
```

注意：

```text
MockModel 不应变成复杂业务模拟器
复杂测试仍使用 SequenceModel / FakeModel fixture
MockModel 不掩盖真实 provider 配置错误
```

### 11.2 真实 provider 集成测试

默认跳过。

执行条件：

```text
对应 API key 存在
RUN_MODEL_INTEGRATION_TESTS=true
测试显式选择真实 provider
```

整体系统测试阶段可以启用默认真实 provider。

### 11.3 测试覆盖设计

后续步骤文档和代码实现必须围绕以下测试边界设计：

```text
配置加载和 env 覆盖
provider 缺 key 不崩溃
generate / stream_generate 统一返回结构化结果
generate / stream_generate 成功 / 失败状态传播
Analyzer / Planner / ReActExecutor / legacy Executor 调用方适配
调用方必须先判断 success，再消费 content
缺配置 / 缺 key / 未启用 provider 返回明确错误码，不静默 mock
ProviderSpec / ProviderConf 加载、字段校验和 provider_conf_id 显式绑定
自定义 OpenAI-compatible provider 配置加载
多凭证 credential_slug 选择和默认凭证选择
enabled / status / verified_at 状态过滤
不同调用类型 route 的参数策略，例如 temperature、top_p、max_tokens、json mode
模型路由选择
fallback 路由
fallback 不处理 blocked_by_policy / invalid_prompt / user_cancelled
熔断冷却粒度：credential、provider_conf_id + model、route candidate
JSON strict / lenient 解析
JSON repair 耗尽
generate_json 返回 StructuredModelResult，并保留原始错误信息
ActionPacket 业务校验不进入 Models 层，只做通用 JSON/schema 校验
timeout / 5 次指数退避 retry 策略
只对可重试错误 retry，不对 missing_api_key / invalid_prompt 重试
日志脱敏
logs/models.log JSONL 字段完整性
prompt 默认不全量写日志，只记录长度、preview 或 hash
request_id / trace_id / provider_request_id 传播
health_check 结构化状态
config_check 不访问外部 API
verify_provider_config live_check 可被显式触发，失败时返回结构化状态
MockModel 结构化输出
MockModel 不伪装真实 provider，不掩盖真实配置错误
embedding 接口返回结构化 EmbeddingResult / EmbeddingBatchResult
context_compression 返回 ContextCompressionResult
真实 provider 集成测试默认 skip，只有 key + RUN_MODEL_INTEGRATION_TESTS=true 才运行
```

---

## 12. 模型管理接口预留

V1 不做完整模型管理 UI，也不做 Session 级模型切换持久化，但提供后续 Runtime/API/UI 可消费的基础接口。

建议接口：

```python
list_enabled_models() -> list[ModelDescriptor]
list_provider_configs(include_disabled=False) -> list[ProviderConfSummary]
get_provider_config(provider_conf_id) -> ProviderConfSummary
enable_provider_config(provider_conf_id) -> ProviderConfigUpdateResult
disable_provider_config(provider_conf_id) -> ProviderConfigUpdateResult
verify_provider_config(provider_conf_id) -> ModelHealthStatus
get_default_routes() -> dict
```

ModelDescriptor / ProviderConfSummary 预留字段：

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

---

## 13. 安全边界

V1 只做 Models 层能做且应该做的安全事项：

```text
不主动读本地文件
不执行代码
不调用工具
不直接生成 Observation
不明文落盘 API key
日志脱敏
错误脱敏
预留外部 provider 安全策略字段
```

V1 不做：

```text
完整数据出境审批
完整权限策略
完整用户级额度系统
自动脱敏业务 prompt
安全策略绕过判断
```

后续“安全与权限层统一”负责：

```text
是否允许发送敏感文件内容给外部模型
是否允许调用付费模型
是否需要用户确认
是否需要发送前脱敏
是否阻止某些 provider / route / tool
```

---

## 14. 与现有调用方的适配原则

### 14.1 Analyzer

Analyzer LLM fallback 必须从：

```python
self.model_manager.generate(prompt) -> str
```

迁移为：

```python
result = self.model_manager.generate(...)
if not result.success:
    使用规则兜底或返回结构化失败
content = result.content
```

### 14.2 Planner

Planner 结构化计划生成优先使用：

```python
generate_json(call_type="planner_structured_plan")
```

Planner 校验计划业务结构，Models 层只做 JSON 基础解析。

### 14.3 ReActExecutor

ReActExecutor 的 ActionPacket 决策使用：

```python
generate_json(call_type="react_action_decision")
```

`react_call_model` 使用：

```python
generate(call_type="react_call_model")
```

二者默认使用用户选定模型，但 route 参数策略不同。

ReActExecutor 必须继续：

```text
校验 ActionPacket
调用 Tool 层
生成 Observation
执行 Checker 转移
区分用户 events 和开发 logs
```

### 14.4 Tools web_search model_builtin

Tools V1 的 `web_search` 可以通过 `model_builtin` provider 调用支持联网搜索能力的模型。该路径必须使用 Models 层正式接口：

```python
generate_json(call_type="web_search")
```

边界：

```text
Tools 只传 provider_conf_id / model / enable_web_search / schema / trace。
Models 负责 route、provider adapter、timeout、retry、结构化输出。
Tools 负责把 StructuredModelResult 校验并归一化为 WebSearchData / ToolResult。
不能在 Tools 内直接调用 GPT/Kimi/DeepSeek SDK。
不能偷偷复用 summary 或 chat call_type 隐藏联网语义。
```

### 14.5 legacy Executor

legacy Executor 仅是历史兼容/迁移诊断开关，不是 ReActExecutor 失败后的正式 fallback。若它显式调用 Models 层，也必须消费结构化结果，不继续依赖裸字符串。

### 14.6 建议代码模块边界

后续代码可以按职责拆分，避免 `model_manager.py` 变成巨型文件。

建议模块：

```text
src/models/protocol.py
  ModelMessage、ModelCallOptions、ModelCallResult、StructuredModelResult、ModelHealthStatus、EmbeddingResult、ContextCompressionResult

src/models/errors.py
  错误码枚举、错误分类、provider 错误归一化

src/models/config.py
  config/models 加载、ProviderSpec、ProviderConf、RouteCandidate、配置校验

src/models/credentials.py
  api_key_env / credential_ref 解析、脱敏、本地加密凭证预留

src/models/providers/base.py
  BaseProvider 抽象

src/models/providers/openai_compatible.py
  OpenAI-compatible adapter

src/models/router.py
  route 选择、多候选、用户选定模型策略

src/models/retry.py
  timeout、retry、fallback、熔断冷却

src/models/structured_output.py
  JSON strict/lenient 解析、repair、schema 轻量校验

src/models/observability.py
  logs/models.log JSONL、trace、usage、cost、脱敏

src/models/compression.py
  context_compression 请求、分段、压缩结果封装

src/models/embeddings.py
  embed_text / embed_texts 协议和 provider 调用

src/models/model_manager.py
  对外门面，组合 config/router/provider/retry/logging，不直接堆业务细节
```

当前已有 `base_model.py / mock_model.py / openai_model.py / qianwen_model.py / doubao_model.py / model_manager.py` 可逐步迁移或拆分，不要求一次性重命名所有文件。

---

## 15. V1 明确不做

Models V1 暂不做：

```text
复杂多 Agent 模型调度
训练或微调
完整 prompt 管理平台
复杂成本预算和用户级额度系统
完整数据出境审批系统
异步 provider streaming 协议
复杂 tokenizer 精确计数
Anthropic / Gemini 原生协议完整 adapter
完整 custom-mapping 模板引擎
完整模型管理 UI
Session 级模型选择持久化
完整 Memory / RAG 压缩闭环
ActionPacket 业务校验
Tool 调用
shell 执行
Observation 生成
```

---

## 16. V1 设计完成标准

Models V1 设计上视为完成，应满足：

```text
1. 所有正式模型接口返回结构化结果。
2. 调用方统一消费 success/content/code/error。
3. 配置中心替代旧 MODEL_NAME。
4. ProviderSpec / ProviderConf / RouteCandidate / credential_slug 明确。
5. OpenAI-compatible provider adapter 是主路径。
6. 自定义 OpenAI-compatible provider 可配置、可验证、可路由。
7. 缺配置或缺 key 返回结构化错误，不静默 mock。
8. MockModel 支持结构化测试能力。
9. route 默认只区分策略，不自动替换用户模型。
10. retry / fallback / 熔断有统一错误分类。
11. generate_json 支持 strict / lenient / repair。
12. ActionPacket 业务校验留在 ReActExecutor。
13. health_check 与 verify 分离。
14. logs/models.log 独立且脱敏。
15. usage / latency / cost / trace 字段可观测。
16. embedding 与 chat 配置完全分离。
17. context compression 作为 Models 能力，但触发和保存由其他层负责。
18. 管理接口字段为 Runtime/API/UI 预留。
19. 真实 provider 集成测试默认 opt-in。
```
