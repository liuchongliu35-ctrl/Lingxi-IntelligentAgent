# Tools 层开发步骤与进度（3）- 联网搜索

> 覆盖步骤：Step 24-29  
> 当前状态：Step 29 已完成，联网搜索 V1 分卷已收口  
> 前置步骤：Step 0-9  
> 上位设计：`Tools层设计决策汇总(4)-联网搜索设计.md`

本分卷实现统一 `web_search`。无论底层使用第三方搜索 API，还是调用具有联网能力的模型，对 Tools 层外部都返回相同 `ToolResult[WebSearchData]`。不同 provider 的原始差异只能保留在受控 metadata 中，不能泄漏为两套正式协议。

---

## Step 24：WebSearchData、Provider 接口与证据枚举

**状态：已完成**

### 目标

先固定搜索工具对外协议和 provider 插件接口，使 Tavily、model_builtin 和 fake 可以独立实现并归一化。

### 涉及文件 / 建议新增

```text
src/tools/web_search/__init__.py
src/tools/web_search/protocol.py
src/tools/web_search/providers/base.py
tests/test_web_search_protocol.py
```

### 正式数据结构

`WebSearchData`：

```text
query
provider
provider_type
mode
provider_request_id
retrieved_at
schema_version
search_depth
topic
answer
summary
results
result_count
evidence_level
source_quality
response_time_ms
usage
raw_content_included
truncated
warnings
metadata
```

`WebSearchResult`：

```text
title
url
snippet
content
score
rank
source
published_at
favicon
images
raw_content
evidence_level
```

### 证据等级

固定为：

```text
url_verified
provider_reported
model_reported
no_url_summary
```

来源质量：

```text
verified_sources
partial_sources
summary_only
empty
```

### Provider 接口

接口至少支持：

```text
provider_id
is_configured()
supports(request)
dry_run(request, context)
search(request, context)
```

provider 返回内部 `ProviderSearchResult` 或直接返回规范化数据均可，但最终必须由同一 normalization 函数生成 `WebSearchData`。

### 统一规则

```text
有 URL 不等于工具已经独立访问并验证网页正文。
url_verified 表示搜索 provider 返回可审计 URL 结果，不表示页面内容真实性已被浏览器复核。
无 URL 的模型联网总结可以 success=true，但必须是 no_url_summary/summary_only。
空结果不是 provider 异常时可 success=true/result_count=0/source_quality=empty。
schema 无效是失败，不能用自然语言文本强行包装成功。
```

### 明确不做

```text
不选择具体模型厂商。
不在协议层发起网络请求。
不抓取结果 URL 正文。
不生成最终用户回答。
```

### 测试与验收

```text
数据对象序列化。
结果数量与 results 一致。
证据枚举组合校验。
无 URL 总结正确降级。
provider 原始字段不会破坏正式 schema。
```

```powershell
python -m pytest tests/test_web_search_protocol.py -q
```

---

## Step 25：web_search 工具、provider 路由、alias 与 fake provider

**状态：已完成**

### 目标

建立可在无网络、无 API key 环境完成单元测试的正式 `web_search` 工具和 provider 路由。

### 建议新增/修改

```text
新增:
  src/tools/web_search/tool.py
  src/tools/web_search/router.py
  src/tools/web_search/providers/fake.py
  tests/test_web_search_tool.py
  tests/test_web_search_routing.py

迁移:
  src/tools/search_tool.py
  src/tools/registry.py
  config/tools/providers.json
```

### ToolSpec

正式名：

```text
web_search
```

alias：

```text
search_tool -> web_search
```

`search_tool.py` 不再拥有独立 Bing 业务逻辑；可以成为薄兼容导入/包装，或在迁移完成后只保留 deprecation 说明。

### 参数校验

```text
query:
  必填，trim 后非空，最大长度受配置控制。

max_results:
  默认 5，范围 1-20。

search_depth:
  basic|advanced。

include_raw_content:
  默认 false。

provider:
  只能从配置允许 provider 中选择，不能注入任意类或 URL。
```

### 路由

支持：

```text
auto
search_api
model_builtin
fake
disabled
```

`auto_order` 完全来自配置，例如：

```text
["search_api", "model_builtin"]
```

本 Step 只建立路由和 fake，真实 Tavily/model_builtin 在后续 Step 接入。

### fallback 边界

必须先确定并测试：

```text
router 的“尝试下一个已配置 provider”只处理 provider 选择阶段或明确允许的无副作用搜索失败。
每次尝试都必须留审计信息。
Tools 不把搜索失败自动改成普通 call_model。
业务上的 fallback_to_tool/fallback_to_model 仍由 Checker/ReActExecutor 决定。
```

建议 V1 默认：

```text
auto 可以按 auto_order 尝试下一个搜索 provider；
显式 provider 失败不自动换 provider；
provider_not_configured/search_not_configured 可以切换；
provider_timeout/provider_rate_limited 是否切换由配置决定，默认最多切换一次；
provider_auth_failed 默认不切换；
provider_response_invalid/schema_invalid 不盲目重复请求；
network_not_allowed/blocked_by_policy 绝不切换；
ToolResult.metadata 记录 attempted_providers、fallback_used、fallback_reason、final_provider。
```

### fake provider 场景

```text
成功、有 URL 结果
空结果
timeout
schema invalid
无 URL summary
未配置
```

### network 与 dry_run

```text
allow_network=false:
  真实搜索返回 network_not_allowed。

dry_run=true:
  不访问 fake 之外的 provider 执行逻辑。
  返回 query、路由、参数和预计 timeout。
```

### 明确不做

```text
不保留 Bing 作为隐式默认。
不在 search_tool 和 web_search 中维护两套结果。
不让模型通过 provider 参数访问未配置服务。
```

### 测试与验收

```text
fake success/empty/timeout/schema invalid/no URL。
network_not_allowed。
disabled/search_not_configured。
auto_order。
显式 provider 不自动切换。
search_tool alias。
dry_run 不访问网络。
```

```powershell
python -m pytest tests/test_web_search_tool.py tests/test_web_search_routing.py -q
```

### Step 25 完成记录（2026-08-16）

```text
修改文件:
  src/tools/errors.py
  src/tools/output_control.py
  src/tools/policy.py
  src/tools/registry.py
  src/tools/runtime.py
  src/tools/search_tool.py
  src/tools/tool_manager.py
  src/tools/web_search/__init__.py
  src/tools/web_search/providers/__init__.py
  src/tools/web_search/providers/fake.py
  src/tools/web_search/router.py
  src/tools/web_search/tool.py
  config/tools/providers.json
  tests/test_tools_current_baseline.py
  tests/test_tool_registry_v1.py
  tests/test_web_search_protocol.py
  tests/test_web_search_routing.py
  tests/test_web_search_tool.py

测试命令:
  python -B -m unittest tests.test_web_search_protocol tests.test_web_search_routing tests.test_web_search_tool tests.test_tool_registry_v1 tests.test_tools_current_baseline
  python -B -m unittest discover -s tests -p 'test_*.py'
  python -B -m compileall -q src\tools tests\test_web_search_protocol.py tests\test_web_search_routing.py tests\test_web_search_tool.py

测试结果:
  聚焦测试通过。
  全量 unittest: 634 tests OK, skipped=1。
  compileall 通过。

边界:
  只接 fake provider 和正式路由，不实现 Tavily/model_builtin。
  `search_tool` 变成 `web_search` 的 alias，旧 Bing 逻辑已移除。
  dry_run 只返回预览，不访问真实 provider。
  network_not_allowed 继续由 ToolPolicy 拦截，dry_run 例外仅用于支持预览的 network 工具。

遗留:
  Step 26 接 Tavily search_api。
  Step 27 接 model_builtin / Models 扩展。
```

---

## Step 26：Tavily search_api adapter

**状态：已完成**

### 目标

实现第一个真实第三方搜索 API provider，并把 Tavily 响应严格归一化为统一搜索数据。

### 建议新增

```text
src/tools/web_search/providers/tavily.py
tests/test_tavily_search_provider.py
tests/fixtures/tavily_search_responses.json
```

### 配置

从 `config/tools/providers.json` 读取：

```text
provider=tavily
api_key_env=TAVILY_API_KEY
endpoint
timeout_seconds
default_search_depth
include_answer
include_raw_content
```

API key 只从环境变量或后续凭证引用解析，不写入 ToolResult、metadata、日志或异常消息。

### 请求

默认：

```text
max_results=5
search_depth=basic
topic=general
include_answer=false
include_raw_content=false
```

只透传 schema 明确允许的参数。domain 和日期过滤需要做类型、数量和格式限制。

### 响应归一化

必须处理：

```text
query
answer
results
response_time
request_id
usage
title/url/content/score/favicon/images
```

缺少非关键字段可以为 null；缺少核心结果结构或类型错误返回 `provider_response_invalid`。

### 错误映射

```text
未配置 -> provider_not_configured/search_not_configured
认证失败 -> provider_auth_failed
限流 -> provider_rate_limited, retryable=true
超时 -> provider_timeout, retryable=true
其他 HTTP/provider -> provider_error
响应结构错误 -> provider_response_invalid
```

### 网络实现

使用项目已有 HTTP 客户端或标准库一致方案；在选择新依赖前检查项目依赖。timeout 必须作用于真实请求，测试用 fake transport/mock HTTP，不访问公网。

### 明确不做

```text
不默认 include_raw_content=true。
不抓取 Tavily 返回 URL。
不在 adapter 中调用 Models 总结。
不把 provider raw response 全量写日志。
```

### 测试与验收

```text
请求参数默认值和覆盖。
成功响应归一化。
缺字段/错类型。
认证/限流/timeout/error。
API key 脱敏。
raw_content 默认关闭。
```

```powershell
python -m pytest tests/test_tavily_search_provider.py -q
```

### Step 26 完成记录（2026-08-16）

```text
修改文件:
  config/tools/providers.json
  src/tools/__init__.py
  src/tools/tool_manager.py
  src/tools/web_search/__init__.py
  src/tools/web_search/providers/__init__.py
  src/tools/web_search/providers/tavily.py
  src/tools/web_search/router.py
  src/tools/web_search/tool.py
  tests/fixtures/tavily_search_responses.json
  tests/test_tavily_search_provider.py

测试命令:
  python -B -m unittest tests.test_tavily_search_provider tests.test_web_search_protocol tests.test_web_search_routing tests.test_web_search_tool tests.test_tool_registry_v1 tests.test_tools_current_baseline
  python -B -m unittest discover -s tests -p 'test_*.py'
  python -B -m compileall -q src\tools tests\test_tavily_search_provider.py

测试结果:
  聚焦测试通过。
  全量 unittest: 639 tests OK, skipped=1。
  compileall 通过。

边界:
  只实现 Tavily search_api adapter，不接 model_builtin。
  真实请求走标准库 HTTP，测试用注入 session/fake response，不访问公网。
  API key 只从环境变量读取，不写入 ToolResult、metadata 或异常消息。
  raw_content 默认关闭，仅在显式请求时透传。

遗留:
  Step 27 接 model_builtin provider 与 Models V1 扩展。
```

真实测试默认跳过，只有以下条件同时满足才运行：

```text
RUN_TOOL_INTEGRATION_TESTS=true
RUN_WEB_SEARCH_INTEGRATION_TESTS=true
TAVILY_API_KEY 存在
```

---

## Step 27：model_builtin provider 与 Models V1 接入

**状态：已完成**

### 目标

通过项目现有 Models 基础服务调用具有联网搜索能力的 GPT、Kimi、DeepSeek 或其他模型，而不是在 Tools 层硬编码具体厂商。

### 建议新增

```text
src/tools/web_search/providers/model_builtin.py
src/tools/web_search/model_search_schema.py
tests/test_model_builtin_search_provider.py
```

可能需要对 Models 层增加的最小协作点必须先核对现有接口；如果 provider adapter 尚不能表达联网参数，应提交最小、通用的 Models 扩展，不得在 Tools 内直接调用厂商 SDK。

当前设计要求的最小 Models 扩展必须写入 Models 文档并在代码实现时同步落地：

```text
新增 ModelCallType.WEB_SEARCH = "web_search"。
新增 web_search route / structured parse 配置。
ModelManager.generate_json(call_type="web_search", metadata={"enable_web_search": true, ...}) 可被 normalize_model_call_type 接受。
provider adapter 根据 ProviderSpec/ProviderConf 能力打开具体厂商联网参数。
```

不得用 `summary` 或 `chat` call_type 偷偷承载联网搜索，否则日志、路由、成本、权限和测试都会失去可审计语义。

### 调用链

```text
WebSearchTool
  -> ModelBuiltinSearchProvider
  -> ModelManager.generate_json(...)
  -> call_type="web_search"
  -> Models provider adapter 根据 provider_conf_id 打开联网能力
  -> ModelCallResult / StructuredModelResult
  -> Tools 校验搜索 JSON
  -> WebSearchData
```

### 模型选择方案

Tools 配置只引用：

```text
provider_conf_id
model（可选）
enable_web_search=true
timeout_seconds
```

具体选 GPT/Kimi/DeepSeek：

```text
由 config/tools/providers.json 引用 Models 中已配置的 provider_conf_id。
Tools 不写固定厂商优先级。
Models provider adapter 负责厂商联网参数。
更换搜索模型只改配置，不改 web_search 协议。
```

### 结构化提示词

提示词必须要求：

```text
只返回符合 schema 的 JSON。
summary 与 results 分离。
每条来源尽量返回 title/url/snippet/source/published_at。
没有 URL 时明确 evidence_level=no_url_summary。
不能把无 URL 总结声明为 verified_sources。
```

提示词版本写入 metadata，例如：

```text
schema_version
prompt_version
```

但不把完整内部 prompt 写入 tools.log 或用户事件。

### 结果校验

```text
Models 调用失败:
  使用 Models 的结构化错误映射为 provider 错误。

JSON parse/schema 失败:
  model_search_parse_failed/model_search_schema_invalid。

只有 summary 无 URL:
  success=true
  evidence_level=no_url_summary
  source_quality=summary_only

模型返回 URL:
  默认 model_reported 或 provider_reported；
  除非底层 provider 返回可明确识别的搜索来源结构，否则不提升为 url_verified。
```

### token 与重试

```text
不得为了格式问题在 Tools 层无限重试模型。
优先使用 Models V1 已有 JSON repair/retry。
usage/cost 从 ModelCallResult 归一化到 WebSearchData.usage/metadata。
```

### 明确不做

```text
不在 Tools 直接 import OpenAI/Kimi/DeepSeek SDK。
不让模型执行本地工具。
不把本地文件内容自动加入搜索 prompt。
不把模型总结伪装成搜索 API 结果。
```

### 测试与验收

使用 MockModel/fake ModelManager：

```text
不同 provider_conf_id 仍返回同一 WebSearchData。
严格 JSON 成功。
schema invalid/parse failed。
Models 结构化失败映射。
no_url_summary。
有来源但证据等级保守。
usage/trace 传播。
不发生第二套 provider 调用。
```

```powershell
python -m pytest tests/test_model_builtin_search_provider.py tests/test_models_protocol.py tests/test_models_structured_output.py tests/test_models_mock_model.py tests/test_models_router.py tests/test_models_v1_acceptance.py -q
```

真实测试默认跳过：

```text
RUN_MODEL_BUILTIN_SEARCH_TESTS=true
对应 Models provider 配置和 API key 存在
```

---

### Step 27 完成记录（2026-08-17）

```text
修改文件:
  src/models/protocol.py
  src/models/config.py
  src/models/mock_model.py
  src/models/providers/openai_compatible.py
  src/tools/errors.py
  src/tools/tool_manager.py
  src/tools/web_search/model_search_schema.py
  src/tools/web_search/protocol.py
  src/tools/web_search/providers/model_builtin.py
  src/tools/web_search/providers/__init__.py
  src/tools/web_search/router.py
  src/tools/web_search/tool.py
  src/tools/web_search/__init__.py
  src/tools/__init__.py
  config/models/routes.json
  config/models/structured_output.json
  config/tools/providers.json
  tests/test_model_builtin_search_provider.py
  tests/test_models_mock_model.py
```

实现内容:

```text
1. Models 新增正式 ModelCallType.WEB_SEARCH = "web_search"。
2. Models 新增 web_search route 和 strict structured-output schema 配置。
3. OpenAI-compatible adapter 仅在 ProviderConf.metadata.web_search 明确启用时
   应用 provider-specific extra_body/tools；Tools 不直接调用厂商 SDK。
4. 新增 ModelBuiltinSearchProvider，通过注入或懒加载的 ModelManager.generate_json()
   调用 Models 层，固定 call_type="web_search"。
5. 模型搜索 prompt 只包含结构化查询参数，不自动加入本地文件内容；
   prompt/schema 版本进入受控 metadata，不写入完整 tools.log。
6. 模型 JSON 由 Models V1 解析后，Tools 再做业务字段校验并归一化为
   WebSearchData / ToolResult。
7. 模型 URL 证据保持保守：model_builtin 结果最多为 model_reported；
   无 URL 但有 summary 时 success=true、no_url_summary、summary_only。
8. Models 失败、JSON parse/schema 失败和无来源结果映射为结构化 ToolErrorCode。
9. WebSearchRouter、WebSearchTool、ToolManager 支持注入 ModelManager，
   默认不会因导入 Tools 而创建真实模型客户端。
```

测试命令:

```powershell
python -B -m unittest tests.test_model_builtin_search_provider
python -B -m unittest tests.test_models_protocol tests.test_models_router tests.test_models_structured_output tests.test_models_mock_model tests.test_models_v1_acceptance
python -B -m unittest tests.test_web_search_protocol tests.test_web_search_routing tests.test_web_search_tool tests.test_tavily_search_provider tests.test_model_builtin_search_provider
python -B -m unittest discover -s tests -p 'test_*.py'
python -B -m compileall -q src\tools src\models tests\test_model_builtin_search_provider.py
```

测试结果:

```text
Step 27 专项测试: 5 tests OK。
Models/联网搜索联合聚焦测试: 61 tests OK。
全量 unittest: 644 tests OK, skipped=1。
compileall 通过。
```

边界:

```text
1. 默认 model_builtin 仍关闭；真实模型调用需要 Tools 配置、Models provider_conf、
   credential 和 allow_network 全部满足。
2. OpenAI-compatible web-search 请求参数不是跨厂商统一协议，V1 只提供配置驱动的
   ProviderConf.metadata.web_search capability mapping，不硬编码 GPT/Kimi/DeepSeek SDK。
3. model_builtin 不抓取 URL 正文，不把模型声称的 URL 提升为 url_verified。
4. fallback 仍由 WebSearchRouter / Checker / ReActExecutor 决定，provider 不偷偷改成
   普通 chat 或 summary 调用。
5. 真实联网模型测试默认不运行，需后续显式 opt-in 联调。
```

遗留:

```text
Step 28 统一证据归一化、Observation 视图、去重/截断和 cache 预留。
Step 29 完成联网搜索离线矩阵与显式 opt-in 真实联调。
```

---

## Step 28：证据归一化、Observation 视图与 cache 预留

**状态：已完成**

### 目标

统一不同 provider 的来源质量、去重、截断和提供给 ReActExecutor 的受控数据视图。

### 证据归一化

1. URL 做基础规范化，用于去重；不发起网页验证请求。
2. 相同 URL 结果合并时保留最高相关分数和非空字段，不拼接无限内容。
3. 无 URL 结果不能与有 URL 结果合并后继承 `url_verified`。
4. `result_count` 是归一化后的实际结果数。
5. provider answer/summary 与 results 证据分开保存。

### Observation 候选视图

Tools 可以在 metadata 提供：

```text
minimal_data:
  provider/query/result_count/evidence_level/code

standard_data:
  前 N 条 title/url/snippet + summary/answer + source_quality

full_data:
  完整 results，但不含 raw_content
```

这只是候选数据，不在 Tools 生成 ObservationPacket。ReActExecutor 在 Step 39 根据预算和安全策略裁决。

### 长结果处理

```text
结果过多:
  max_results 和 max_output_chars 双重限制。

snippet/content 过长:
  单条截断并标记。

raw_content:
  默认不包含；即使请求，也不默认进入 Observation。
```

### cache 预留

只预留：

```text
cache_key
cache_hit
cache_age_seconds
```

V1 不实现长期搜索缓存服务。

### 明确不做

```text
不通过模型再次摘要以生成 Observation。
不做网页事实核验。
不做 rerank 模型。
不做长期索引。
```

### 测试与验收

```text
URL 去重。
证据等级不被错误提升。
标准视图不含 raw_content。
单条和整体截断。
无 URL summary 保持弱证据。
```

```powershell
python -m pytest tests/test_web_search_normalization.py tests/test_react_executor_observation.py -q
```

---

### Step 28 完成记录（2026-08-17）

```text
修改文件:
  src/tools/data_types.py
  src/tools/web_search/protocol.py
  src/tools/web_search/normalization.py
  src/tools/web_search/router.py
  src/tools/web_search/tool.py
  src/tools/web_search/__init__.py
  src/tools/__init__.py
  tests/test_web_search_normalization.py
```

实现内容:

```text
1. 新增 URL 基础规范化：仅用于去重，不发起 URL 请求，不做网页事实核验。
2. 相同规范化 URL 的结果合并：
   - 保留最高 score；
   - 保留非空字段；
   - 不拼接无限正文；
   - 保持无 URL 结果独立存在。
3. result_count 始终更新为去重、截断后的实际结果数。
4. model_builtin URL 结果继续保持 model_reported / partial_sources，
   不被归一化为 url_verified / verified_sources。
5. 新增 minimal_data、standard_data、full_data 三档 Observation 候选视图：
   - standard 只提供前 N 条 title/url/snippet；
   - full 提供完整结果字段但始终排除 raw_content；
   - 候选视图写入 WebSearchData.metadata，不生成 ObservationPacket。
6. 增加单条字段截断和整体 max_output_chars 限制，并记录 truncated/warnings。
7. WebSearchContext 接入 max_output_chars 与 max_observation_chars，
   Router 在 ToolResult 完成前生成受控候选视图。
8. WebSearchData 预留 cache_key、cache_hit、cache_age_seconds 字段，
   V1 不实现缓存查找、写入或长期缓存服务。
```

测试命令:

```powershell
python -B -m unittest tests.test_web_search_normalization
python -B -m unittest tests.test_web_search_normalization tests.test_web_search_protocol tests.test_web_search_routing tests.test_web_search_tool tests.test_tavily_search_provider tests.test_model_builtin_search_provider tests.test_react_executor_observation tests.test_tool_output_control
python -B -m unittest discover -s tests -p 'test_*.py'
python -B -m compileall -q src\tools src\agent tests\test_web_search_normalization.py
```

测试结果:

```text
Step 28 聚焦测试: 49 tests OK。
全量 unittest: 650 tests OK, skipped=1。
compileall 通过。
```

边界:

```text
1. Observation 候选视图不是 ObservationPacket，最终模型可见内容仍由
   ReActExecutor 根据预算和安全策略裁决。
2. raw_content 不进入 standard/full 候选视图；原始 provider response 仍遵循
   既有受控 metadata 规则。
3. max_output_chars 是本地结构化输出边界，不替代 ToolRuntime 的统一输出控制。
4. cache 字段仅作为协议预留，不产生 cache_hit，也不根据 query 自动生成长期缓存。
5. 不做网页验证、rerank、模型二次摘要、长期索引或跨 provider 事实合并。
```

遗留:

```text
Step 29 完成联网搜索离线矩阵与显式 opt-in 真实联调。
```

---

## Step 29：联网搜索完整测试与可选真实联调

**状态：已完成**

### 目标

完成 web_search 的离线全覆盖和显式开启的真实 provider 验收，不让默认测试依赖网络、费用或外部服务稳定性。

### 离线测试矩阵

```text
ToolSpec/schema/alias
network policy
dry_run
fake provider 全场景
auto 路由
Tavily 请求/响应/错误映射
model_builtin Models 接入
证据等级
输出截断
tools.log 脱敏
ReActExecutor 可消费结构化结果的兼容测试
```

### 真实集成测试

测试文件建议：

```text
tests/integration/test_web_search_tavily.py
tests/integration/test_web_search_model_builtin.py
```

默认 `skip`，启用条件严格检查环境变量。缺 key 是 skip，不是单元测试失败；显式启用且配置错误则应失败并给出结构化原因。

### 验收命令

默认：

```powershell
python -m pytest tests/test_web_search_protocol.py tests/test_web_search_tool.py tests/test_web_search_routing.py tests/test_tavily_search_provider.py tests/test_model_builtin_search_provider.py tests/test_web_search_normalization.py -q
```

可选真实 Tavily：

```powershell
$env:RUN_TOOL_INTEGRATION_TESTS='true'
$env:RUN_WEB_SEARCH_INTEGRATION_TESTS='true'
python -m pytest tests/integration/test_web_search_tavily.py -q
```

### 完成标准

```text
search_api 和 model_builtin 对外结构相同。
具体联网模型由 Models 配置决定。
没有 URL 的总结被明确降级。
search_tool 仅为 alias。
搜索工具不承担最终总结或网页正文抓取。
默认测试不访问网络。
```

### Step 29 完成记录（2026-08-17）

**修改文件**

```text
tests/test_web_search_v1_acceptance.py
tests/integration/__init__.py
tests/integration/test_web_search_tavily.py
tests/integration/test_web_search_model_builtin.py
```

**实现与验收内容**

```text
1. 新增联网搜索 V1 集中离线验收矩阵，覆盖：
   - web_search ToolSpec、参数 schema、search_tool alias；
   - ToolRuntime / ToolPolicy network_not_allowed 边界；
   - dry_run 路由预览；
   - fake provider success、empty、timeout、schema_invalid、
     no_url_summary、not_configured；
   - auto 路由 attempted_providers / fallback_used / final_provider；
   - 显式 provider 失败不自动切换；
   - model_builtin 只能通过 Models generate_json(call_type="web_search")；
   - no_url_summary / summary_only 弱证据降级；
   - Observation standard_data 不携带 raw_content 且可被 ObservationStore 消费；
   - logs/tools.log JSONL、搜索 query 和 token 内容不写入明文日志。

2. 新增 Tavily 真实联调测试：
   - 只有 RUN_TOOL_INTEGRATION_TESTS=true、
     RUN_WEB_SEARCH_INTEGRATION_TESTS=true、
     TAVILY_API_KEY 存在时执行；
   - 默认不访问公网、不消耗搜索额度。

3. 新增 model_builtin 真实联调测试：
   - 只有 RUN_TOOL_INTEGRATION_TESTS=true、
     RUN_MODEL_BUILTIN_SEARCH_TESTS=true、
     Models provider API key、openai 依赖和模型配置存在时执行；
   - 通过 ModelsConfig / ModelManager / model_builtin provider 正式链路调用；
   - 具体联网能力仍由 Models provider_conf.metadata.web_search 配置决定。
```

**测试命令与结果**

```powershell
python -B -m unittest tests.test_web_search_v1_acceptance
```

```text
Ran 8 tests
OK
```

```powershell
python -B -m unittest tests.test_web_search_v1_acceptance tests.test_web_search_protocol tests.test_web_search_routing tests.test_web_search_tool tests.test_tavily_search_provider tests.test_model_builtin_search_provider tests.test_web_search_normalization tests.test_react_executor_observation tests.test_tool_output_control
```

```text
Ran 57 tests
OK
```

```powershell
python -B -m unittest tests.integration.test_web_search_tavily tests.integration.test_web_search_model_builtin
```

```text
默认环境下 2 个真实联调测试均 skip，未访问网络。
```

```powershell
python -B -m compileall -q src\tools src\models src\agent tests\test_web_search_v1_acceptance.py tests\integration
```

```text
通过，无编译错误。
```

```powershell
python -B -m unittest discover -s tests -p 'test_*.py'
```

```text
Ran 658 tests in 11.071s
OK (skipped=3)
```

**边界与遗留**

```text
1. 默认测试完全使用 fake provider、注入 session 或 fake ModelManager，不依赖网络、费用、API key 或外部服务稳定性。
2. 真实联调测试显式 opt-in；缺少 key 或依赖时 skip，显式启用且 provider 返回错误时测试失败并保留结构化 ToolResult 原因。
3. Step 29 未新增第二套搜索运行时；测试验证的正式链路仍是
   ReActExecutor -> ToolManager/ToolRuntime -> WebSearchTool -> Router -> Provider -> ToolResult。
4. Observation 候选视图仍不是 ObservationPacket，最终 Observation 裁决继续由 ReActExecutor 负责。
5. model_builtin 真实联调不固定 GPT/Kimi/DeepSeek，具体模型、endpoint 和 web_search capability mapping 由 Models 配置决定。
6. 搜索工具仍不抓取 URL 正文、不做网页事实核验、不负责最终总结、不实现长期缓存。
```

---

## Step 24 完成记录（2026-08-16）

**状态：已完成**

### 修改文件

```text
src/tools/data_types.py
src/tools/__init__.py
src/tools/web_search/__init__.py
src/tools/web_search/protocol.py
src/tools/web_search/providers/__init__.py
src/tools/web_search/providers/base.py
tests/test_web_search_protocol.py
```

### 实现内容

```text
1. 正式化 WebSearchData / WebSearchResult 的证据枚举与来源质量枚举：
   - evidence_level: url_verified / provider_reported / model_reported / no_url_summary
   - source_quality: verified_sources / partial_sources / summary_only / empty

2. WebSearchData 继续作为 Tools 层对外统一 data schema：
   - result_count 始终由 results 实际数量生成。
   - 无 URL summary 归一为 no_url_summary / summary_only。
   - 空结果保持 result_count=0 / source_quality=empty，不等同 provider 异常。
   - model_builtin 带 URL 结果默认不提升为 search_api 的 url_verified，而是 model_reported。

3. 新增 web_search 协议包：
   - WebSearchRequest：provider 输入参数底座，不发起网络请求。
   - WebSearchContext：provider 执行上下文，承接 ToolCallRequest 的 options/context。
   - ProviderSearchResult：provider 内部中间返回。
   - normalize_web_search_data：唯一归一化函数，最终生成 WebSearchData。

4. 新增 Provider 接口：
   - provider_id
   - provider_type
   - is_configured()
   - supports(request)
   - dry_run(request, context)
   - search(request, context)

5. provider raw 字段隔离：
   - 未进入正式 WebSearchData/WebSearchResult schema 的字段不会污染顶层结构。
   - 顶层未知字段归入 metadata.provider_raw。
   - result 未知字段归入 metadata.provider_raw_results。
   - 原始 provider response 仅作为 metadata.provider_raw_response 受控保留。
```

### 测试命令与结果

```powershell
python -B -m unittest tests.test_web_search_protocol tests.test_tool_result_v1
```

结果：

```text
Ran 13 tests in 0.002s
OK
```

```powershell
python -B -m unittest discover -s tests -p 'test_*.py'
```

结果：

```text
Ran 623 tests in 10.026s
OK (skipped=1)
```

```powershell
python -B -m compileall -q src\tools tests\test_web_search_protocol.py
```

结果：

```text
通过，无编译错误。
```

### 边界与遗留

```text
1. 本步骤不注册 web_search 工具，不迁移 search_tool alias，不实现 router/fake provider。
2. 本步骤不发起真实网络请求，不接入 Tavily，不接入 Models 层 model_builtin。
3. provider fallback、network_not_allowed、search_not_configured、auto_order 等执行策略留到 Step 25。
4. Tavily adapter 留到 Step 26。
5. model_builtin provider 与 Models V1 最小扩展留到 Step 27。
6. URL evidence 的含义保持保守：url_verified 仅表示 search_api provider 返回了可审计 URL，不表示 Tools 已抓取或验证网页正文。
```
