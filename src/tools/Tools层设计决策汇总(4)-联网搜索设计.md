# Tools 层设计决策汇总（4）- 联网搜索设计

> 文档状态：Tools 层 V1 正式化设计稿  
> 适用范围：web_search、search_api provider、Tavily adapter、model_builtin provider、WebSearchData、搜索证据和测试  

## 1. 设计目标

Tools V1 正式联网搜索工具命名为：

```text
web_search
```

旧 `search_tool` 只作为 legacy alias，内部转发到 `web_search`，不再绑定 Bing。

`web_search` 的目标：

```text
根据查询获取联网搜索结果。
返回结构化、可消费、可审计的 WebSearchData。
支持 search_api provider 和 model_builtin provider。
不负责最终总结。
不负责网页正文抓取。
```

## 2. 设计边界

`web_search` 负责：

```text
接收结构化查询参数。
按配置选择 provider。
调用 Tavily 等 search_api。
或通过 Models 层调用支持联网搜索的模型。
校验 provider 返回结果。
归一化为 WebSearchData。
标注证据等级。
返回 ToolResult。
写 logs/tools.log。
```

`web_search` 不负责：

```text
最终自然语言回答。
多网页深度阅读。
完整网页正文抓取。
浏览器自动化。
RAG 入库。
长文总结。
规划如何使用搜索结果。
```

搜索结果后续怎么用，由 ReActExecutor 决定：

```text
web_search
  -> ToolResult(WebSearchData)
  -> Observation
  -> Checker / ReActExecutor
  -> 必要时 call_model 总结、提取方案、生成计划或 final answer
```

## 3. provider 架构

V1 支持：

```text
search_api:
  第三方搜索 API，V1 首选 Tavily。

model_builtin:
  通过 Models 层调用支持联网搜索能力的模型，例如 GPT / Kimi / DeepSeek / 其他兼容模型。

fake:
  单元测试 provider，不访问真实网络。

disabled:
  明确禁用，返回 search_not_configured 或 network_not_allowed。
```

配置值：

```text
tools.web_search.provider = "auto" | "search_api" | "model_builtin" | "fake" | "disabled"
```

`auto` 路由建议可配置，不硬编码唯一顺序：

```json
{
  "web_search": {
    "provider": "auto",
    "auto_order": ["search_api", "model_builtin"]
  }
}
```

如果用户希望优先使用模型联网搜索：

```json
{
  "web_search": {
    "provider": "auto",
    "auto_order": ["model_builtin", "search_api"]
  }
}
```

`auto` fallback 条件必须固定，不能因为“能试下一个 provider”就无限重试或隐藏失败：

```text
显式 provider:
  不自动切换，失败直接返回该 provider 的结构化错误。

provider_not_configured / search_not_configured:
  auto 可以尝试 auto_order 中的下一个 provider。

provider_timeout / provider_rate_limited:
  是否尝试下一个 provider 由配置控制，默认允许切换一次。

provider_auth_failed:
  默认不自动切换，避免凭证问题被另一个付费 provider 悄悄掩盖。

provider_response_invalid / schema_invalid:
  不盲目重复请求；返回失败，由 Checker/ReActExecutor 决定修复或换策略。

network_not_allowed / blocked_by_policy:
  绝不切换，直接失败。
```

每次 auto 尝试必须记录：

```text
metadata.attempted_providers
metadata.fallback_used
metadata.fallback_reason
metadata.final_provider
```

V1 实现要求：

```text
search_api provider 接口完整。
model_builtin provider 接口完整。
fake provider 覆盖测试。
真实 provider 测试默认 skip。
配置好 Tavily key 或模型 key 后可以直接打开真实测试。
```

## 4. ToolSpec

`web_search` ToolSpec：

```json
{
  "name": "web_search",
  "description": "Search the web through configured providers and return structured evidence.",
  "category": "search",
  "namespace": "builtin",
  "risk_level": "medium",
  "workspace_scope": "network",
  "requires_confirmation": false,
  "supports_dry_run": true,
  "timeout_seconds": 30,
  "default_observation_mode": "standard",
  "aliases": ["search_tool"]
}
```

参数 schema：

```json
{
  "type": "object",
  "properties": {
    "query": { "type": "string" },
    "max_results": { "type": "integer" },
    "topic": { "type": "string" },
    "search_depth": { "type": "string" },
    "time_range": { "type": "string" },
    "start_date": { "type": "string" },
    "end_date": { "type": "string" },
    "include_answer": { "type": "boolean" },
    "include_raw_content": { "type": "boolean" },
    "include_domains": { "type": "array" },
    "exclude_domains": { "type": "array" },
    "provider": { "type": "string" },
    "observation_mode": { "type": "string" }
  },
  "required": ["query"]
}
```

参数策略：

```text
query:
  必填，非空字符串。
  建议限制长度，例如 400 字符以内。

max_results:
  默认 5，最大 20。

search_depth:
  basic / advanced。
  advanced 成本更高，默认 basic。

include_answer:
  是否请求 provider 生成 answer。
  Tavily 支持 include_answer。

include_raw_content:
  V1 默认 false。
  即使 provider 支持 raw content，也不默认打开。

provider:
  可选强制 provider，但必须在配置允许范围内。
```

## 5. WebSearchData

统一返回结构：

```python
WebSearchData(
    query: str,
    provider: str,
    provider_type: str,
    mode: str,
    provider_request_id: str | None,
    retrieved_at: str,
    schema_version: str,
    search_depth: str | None,
    topic: str | None,
    answer: str | None,
    summary: str | None,
    results: list[WebSearchResult],
    result_count: int,
    evidence_level: str,
    source_quality: str,
    response_time_ms: int | None,
    usage: dict,
    raw_content_included: bool,
    truncated: bool,
    warnings: list[str],
    metadata: dict,
)
```

`WebSearchResult`：

```python
WebSearchResult(
    title: str,
    url: str | None,
    snippet: str | None,
    content: str | None,
    score: float | None,
    rank: int | None,
    source: str | None,
    published_at: str | None,
    favicon: str | None,
    images: list[dict],
    raw_content: str | None,
    evidence_level: str,
)
```

证据等级：

```text
url_verified:
  有 URL，且 provider 返回结果可映射为网页来源。

provider_reported:
  provider 返回来源字段，但 URL 或元数据不完整。

model_reported:
  模型声称引用了来源，但工具无法完全验证。

no_url_summary:
  没有 URL，但模型给了联网总结。
```

source_quality：

```text
verified_sources
partial_sources
summary_only
empty
```

## 6. Tavily search_api provider

V1 search_api 首选 Tavily adapter。

选择原因：

```text
面向 Agent / RAG 场景。
返回结构化 results。
支持 max_results、search_depth、topic、时间过滤、include_answer、include_raw_content 等参数。
真实联调可通过 API key 和环境变量后置。
```

官方文档要点：

```text
POST /search。
query 必填。
max_results 范围 0-20 或常用 1-20。
search_depth 支持 basic / advanced。
topic 支持 general / news / finance。
include_answer 可返回 LLM answer。
include_raw_content 可返回清洗后的网页内容，但会增加体积和延迟。
响应包含 query、answer、results、response_time、usage、request_id。
result 包含 title、url、content、score、raw_content、favicon、images 等字段。
```

V1 默认请求：

```json
{
  "query": "...",
  "max_results": 5,
  "search_depth": "basic",
  "topic": "general",
  "include_answer": false,
  "include_raw_content": false
}
```

如果用户或模型需要更丰富证据，可请求：

```json
{
  "search_depth": "advanced",
  "include_answer": true
}
```

V1 不默认请求 `include_raw_content=true`，原因：

```text
内容体积大。
成本和延迟更高。
可能把网页正文塞进上下文。
网页正文抓取应后续由 fetch_url/read_web_page 单独设计。
```

Tavily 结果归一化：

```text
response.query -> WebSearchData.query
response.answer -> WebSearchData.answer / summary
response.results[] -> WebSearchResult[]
response.response_time -> response_time_ms
response.request_id -> provider_request_id
response.usage -> usage
result.title -> title
result.url -> url
result.content -> snippet/content
result.score -> score
result.favicon -> favicon
```

错误码：

```text
tavily_not_configured
tavily_auth_failed
tavily_rate_limited
tavily_timeout
tavily_provider_error
tavily_response_invalid
```

统一到 ToolResult：

```text
provider_not_configured
provider_auth_failed
provider_rate_limited
provider_timeout
provider_error
provider_response_invalid
```

## 7. model_builtin provider

`model_builtin` 通过 Models 层调用支持联网搜索的模型。

重要原则：

```text
Tools 层不直接实现 GPT / Kimi / DeepSeek 等 provider 参数。
Tools 层只表达 call_type="web_search" 和 enable_web_search=true 的意图。
具体 provider 如何开启联网由 Models provider adapter 处理。
```

Models 层需要配合的最小扩展：

```text
新增 ModelCallType.WEB_SEARCH = "web_search"。
新增 web_search route / structured parse 配置。
ModelManager.generate_json(call_type="web_search", metadata={"enable_web_search": true, ...}) 必须被路由接受。
OpenAI-compatible 或其他 provider adapter 根据 ProviderSpec/ProviderConf 能力决定如何打开厂商联网参数。
```

这不是重做 Models 层，也不是把搜索 provider 写进 Tools：

```text
Tools 不直接 import OpenAI/Kimi/DeepSeek SDK。
Tools 不偷偷复用 summary 或 chat call_type 隐藏联网语义。
Models 不负责把结果变成 ToolResult。
Models 只返回 StructuredModelResult / ModelCallResult。
Tools 负责校验模型 JSON 并归一化为 WebSearchData。
```

调用链：

```text
WebSearchTool
  -> ModelManager.generate_json(...)
  -> provider_conf_id / model / call_type=web_search
  -> Models provider adapter 打开联网搜索参数
  -> 返回 ModelCallResult
  -> Tools 校验 JSON schema
  -> 归一化 WebSearchData
```

配置：

```json
{
  "web_search": {
    "model_builtin": {
      "provider_conf_id": "conf_openai_default",
      "model": "configured-web-search-model",
      "enable_web_search": true,
      "timeout_seconds": 30
    }
  }
}
```

提示词目标：

```text
要求模型返回严格 JSON。
要求把自然语言 summary 放入 summary 字段。
要求尽量返回每条来源的 title/url/snippet/source。
要求没有 URL 时显式标记 evidence_level=no_url_summary。
不允许把无 URL 的总结伪装成可审计网页证据。
```

模型输出 schema：

```json
{
  "query": "string",
  "summary": "string",
  "results": [
    {
      "title": "string",
      "url": "string|null",
      "snippet": "string|null",
      "source": "string|null",
      "published_at": "string|null"
    }
  ],
  "evidence_level": "url_verified|provider_reported|model_reported|no_url_summary",
  "source_quality": "verified_sources|partial_sources|summary_only|empty"
}
```

校验失败：

```text
model_search_schema_invalid
model_search_parse_failed
model_search_no_sources
```

如果模型只返回 summary，没有 URL：

```text
ToolResult.success = true
WebSearchData.evidence_level = "no_url_summary"
WebSearchData.source_quality = "summary_only"
message = "模型联网搜索返回了总结，但没有可审计 URL 来源。"
```

这类结果可以被 Agent 消费，但最终回答或后续规划应知道它证据较弱。

## 8. fake provider

单元测试默认使用 fake provider。

fake provider 必须支持：

```text
成功返回 WebSearchData。
未配置返回 search_not_configured。
空结果。
provider timeout。
无 URL summary。
schema invalid。
network_not_allowed。
```

fake provider 不访问网络，不消耗模型 token。

## 9. network policy

`web_search` 受：

```text
allow_network
tools.web_search.enabled
provider enabled
provider credential
timeout
```

控制。

默认：

```text
allow_network=false 时:
  返回 network_not_allowed。

provider=disabled:
  返回 search_not_configured。

provider 未配置:
  返回 search_not_configured。
```

`web_search` 属于 medium risk。通常不需要每次确认，但需要全局或会话级网络授权。

## 10. dry_run

`web_search` 的 dry_run 不访问真实网络。

返回：

```text
query
provider route
max_results
search_depth
include_answer
include_raw_content
allow_network
estimated_timeout
```

用途：

```text
用户确认联网权限。
调试 provider route。
确认不会请求 raw_content。
```

## 11. Observation 策略

默认 `observation_mode=standard`。

minimal：

```text
success
provider
query
result_count
evidence_level
code
```

standard：

```text
前 N 条结果 title / url / snippet
summary / answer 若存在
evidence_level
source_quality
```

full：

```text
包含完整 results。
不默认包含 raw_content。
仍受 max_observation_chars 限制。
```

不允许默认把完整网页正文放入 Observation。

## 12. 与 Planner / ReActExecutor 的关系

Planner 可以规划：

```text
step: search
tool_name: web_search
args.query
output_key: search_results
```

ReActExecutor 执行后：

```text
ToolResult(WebSearchData)
  -> Observation
  -> Checker
```

如果搜索结果需要总结：

```text
ReActExecutor 生成 call_model ActionPacket
```

而不是 `web_search` 内部自动总结。

如果 search_api 失败：

```text
provider error -> ToolResult success=False
Checker 可决定是否 fallback_to_tool(model_builtin) 或 ask_user。
```

如果 model_builtin 失败：

```text
ModelCallResult failure -> ToolResult success=False
Checker 决定 fallback 或失败。
```

## 13. 配置文件

建议：

```text
config/tools/providers.json
```

示例：

```json
{
  "web_search": {
    "enabled": true,
    "provider": "auto",
    "auto_order": ["search_api", "model_builtin"],
    "timeout_seconds": 30,
    "max_results": 5,
    "search_api": {
      "provider": "tavily",
      "api_key_env": "TAVILY_API_KEY",
      "endpoint": "https://api.tavily.com/search",
      "default_search_depth": "basic",
      "include_answer": false,
      "include_raw_content": false
    },
    "model_builtin": {
      "provider_conf_id": "conf_openai_default",
      "model": null,
      "enable_web_search": true,
      "timeout_seconds": 30
    }
  }
}
```

## 14. 测试

单元测试：

```text
web_search fake success
web_search disabled
network_not_allowed
search_not_configured
Tavily response normalize
Tavily auth/rate/timeout error mapping
model_builtin JSON success
model_builtin schema invalid
model_builtin no_url_summary success with weak evidence
search_tool alias -> web_search
Observation standard 不含 raw_content
```

真实测试默认 skip：

```text
RUN_TOOL_INTEGRATION_TESTS=true
RUN_WEB_SEARCH_INTEGRATION_TESTS=true
TAVILY_API_KEY 存在
```

model_builtin 真实测试：

```text
RUN_MODEL_BUILTIN_SEARCH_TESTS=true
Models provider 配置存在
模型 key 存在
```

## 15. 后续预留

V2/V3 可做：

```text
Brave Search provider。
fetch_url。
read_web_page。
browser_preview。
搜索缓存。
搜索结果去重和 rerank。
按 domain allow/deny。
RAG 入库。
```

## 16. 参考资料

- [Tavily Search API Reference](https://docs.tavily.com/documentation/api-reference/endpoint/search)
- [Tavily SDK Reference](https://docs.tavily.com/sdk/javascript/reference)
- [Tavily Search Best Practices](https://docs.tavily.com/documentation/best-practices/best-practices-search)
- [OpenAI API quickstart - tools](https://platform.openai.com/docs/quickstart/make-your-first-api-request)
- [OpenAI Responses streaming API - web_search_call](https://platform.openai.com/docs/api-reference/responses-streaming/response/refusal/delta?lang=curl)
