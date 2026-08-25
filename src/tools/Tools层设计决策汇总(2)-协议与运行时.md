# Tools 层设计决策汇总（2）- 协议与运行时

> 文档状态：Tools 层 V1 正式化设计稿  
> 适用范围：ToolResult、ToolCallRequest、ToolSpec、ToolRegistry、ToolManager、ToolPolicy、Observation 分级、日志、错误码  

## 1. 总体执行流

Tools V1 正式执行流：

```text
ReActExecutor
  -> ToolCallRequest
  -> ToolRegistry 查找 ToolSpec
  -> ToolPolicy 校验权限和风险
  -> ToolManager / ToolRuntime 调用工具实例
  -> ToolResult
  -> ToolLogger 写 logs/tools.log
  -> ReActExecutor 生成 Observation / ExecutionEvent
```

任何工具调用都不允许绕过：

```text
ToolSpec
ToolPolicy
ToolResult
tools.log
```

## 2. ToolResult V1

所有正式工具必须统一返回 `ToolResult` 外壳，不返回裸字符串作为正式协议。

建议结构：

```python
ToolResult(
    success: bool,
    tool_name: str,
    tool_category: str,
    tool_namespace: str,
    data: Any,
    message: str,
    error: str | None,
    code: str,
    error_type: str | None,
    retryable: bool,
    provider: str | None,
    started_at: str | None,
    ended_at: str | None,
    duration_ms: int | None,
    trace_id: str | None,
    execution_id: str | None,
    step_id: str | None,
    call_id: str,
    raw_output: Any | None,
    raw_output_truncated: bool,
    metadata: dict,
)
```

字段说明：

```text
success:
  真实成功状态。失败不能包装成 success=True。

tool_name:
  正式工具名，例如 read_file / web_search / mcp.github.search_repositories。

tool_category:
  工具类别，例如 read / edit / command / search / mcp / document / utility。

tool_namespace:
  工具命名空间，例如 builtin / mcp.github / provider.tavily。

data:
  工具结构化结果。不同工具 data schema 可以不同。

message:
  简短人类可读摘要，可用于用户可见事件，但不能承载完整输出。

error:
  人类可读错误摘要。

code:
  稳定错误码或成功码。

error_type:
  错误类别，例如 validation / permission / timeout / provider / internal。

retryable:
  当前错误是否适合工具层或执行器重试。

provider:
  工具底层 provider，例如 tavily / model_builtin / local / mcp。

duration_ms:
  工具执行耗时。

trace_id / execution_id / step_id:
  贯穿 Analyzer -> Planner -> ReActExecutor -> Tools 的追踪字段。

call_id:
  单次工具调用唯一 id。

raw_output:
  受控原始输出。是否保留由工具和阈值策略决定。

raw_output_truncated:
  raw_output 是否已截断。

metadata:
  事件建议、artifact、preview、安全策略、hash、provider 原始摘要等扩展字段。
```

`data` 示例映射：

```text
read_file.data        -> FileReadData
write_file.data       -> FileWriteData
patch_file.data       -> FilePatchData
delete_file.data      -> FileDeleteData
command_tool.data     -> CommandExecutionData
web_search.data       -> WebSearchData
document_parser.data  -> DocumentParseData
mcp.*.data            -> MCPToolData
```

## 3. ToolResult message / data / raw_output 边界

`message`：

```text
给用户或上层展示的短句。
可以进入 ExecutionEvent 摘要。
不能放完整 stdout、完整文件内容、完整网页正文或敏感数据。
```

`data`：

```text
给程序消费。
必须结构化。
测试应主要断言 data 字段。
```

`raw_output`：

```text
仅在阈值内保留完整输出。
超过阈值时截断或转为 artifact_ref。
不默认进入 Observation。
不直接写入 logs/tools.log 全量。
```

命令输出策略：

```text
小于 max_raw_output_chars:
  ToolResult.raw_output 可保留完整 stdout/stderr。

超过 max_raw_output_chars:
  ToolResult.raw_output 截断。
  data 中标记 stdout_truncated / stderr_truncated。
  metadata 中记录 hash、bytes、artifact_ref 可选。
```

日志只记录：

```text
长度
hash
preview
truncated 标记
```

## 4. ToolCallRequest

`ToolCallRequest` 是 ToolManager 的正式输入协议。

建议结构：

```python
ToolCallRequest(
    tool_name: str,
    args: dict,
    context: ToolCallContext,
    options: ToolCallOptions,
)
```

要求：

```text
tool_name 必须是 ToolRegistry 中的正式工具名或 alias。
args 必须是 object。
context 负责 trace 和运行上下文。
options 负责本次调用策略。
```

不允许：

```text
直接传自然语言工具指令。
模型传入未注册工具名。
模型传入 MCP server command/url/credential。
模型绕过 ToolRegistry 指定底层 provider 执行本地能力。
```

## 5. ToolCallContext

建议字段：

```python
ToolCallContext(
    trace_id: str | None,
    execution_id: str | None,
    plan_id: str | None,
    task_id: str | None,
    step_id: str | None,
    packet_id: str | None,
    session_id: str | None,
    user_id: str | None,
    workspace_root: str,
    source: str,
    initiated_by: str,
)
```

`source` 建议枚举：

```text
react_executor
test
runtime_api
mcp_manager
internal
historical_executor
```

说明：

```text
historical_executor 仅表示历史诊断/迁移入口，不是正式主链路。
任何 source 都不能绕过安全和确认策略。
```

## 6. ToolCallOptions

建议字段：

```python
ToolCallOptions(
    timeout_seconds: int | None,
    dry_run: bool,
    require_confirmation: bool | None,
    confirmed: bool,
    approval_scope: str | None,
    confirmation_id: str | None,
    preview_hash: str | None,
    approved_at: str | None,
    approval_source: str | None,
    allow_read_workspace: bool | None,
    allow_write_workspace: bool | None,
    allow_network: bool | None,
    allow_command: bool | None,
    allow_shell_command: bool | None,
    allow_mcp: bool | None,
    max_output_chars: int | None,
    max_raw_output_chars: int | None,
    max_observation_chars: int | None,
    observation_mode: str | None,
)
```

`dry_run`：

```text
只做参数、安全、权限、路径、影响范围检查。
不产生真实副作用。
返回 preview 数据。
```

`confirmed`：

```text
表示本次具体风险操作已经通过 Runtime/ReActExecutor 的恢复流程获得有效用户确认。
该字段不能由模型直接写入 action_args，也不能由 Tool 层自行推导为 true。
```

`approval_scope`：

```text
本次确认范围，例如 one_call / current_step / session。
V1 正式实现 one_call；session 只表示会话级能力授权，不等于自动批准所有副作用调用。
current_step 可以作为后续扩展，不在 V1 中隐式放宽确认。
```

确认票据字段：

```text
confirmation_id:
  Runtime 为一次用户确认生成的不可预测标识。

preview_hash:
  dry_run/preview 阶段对规范化目标、参数和影响范围计算的摘要。
  实际执行时必须重新计算并比对，防止“确认了 A，执行 B”。

approved_at:
  用户确认发生的时间，由 Runtime 记录。

approval_source:
  例如 user_ui / runtime_api / test；模型不能作为 approval_source。
```

## 7. 会话级权限开关

V1 支持会话级权限开关，但不做完整权限 UI。

建议默认：

```text
allow_read_workspace: true
allow_write_workspace: false
allow_network: false
allow_command: false
allow_shell_command: false
allow_mcp: false
```

默认行为：

```text
读取普通 workspace 文件:
  可自动执行，敏感文件除外。

新建普通 workspace 文件:
  allow_write_workspace=true 时可执行。

覆盖 / append / patch:
  allow_write_workspace=true 且非敏感路径时可执行。
  否则需要确认。

命令执行:
  默认确认。
  allow_command=true 后，低风险命令可自动执行。

复杂 shell:
  默认确认。
  即使 allow_command=true 也必须更严格。

联网搜索:
  受 allow_network 控制。

MCP:
  受 allow_mcp 和具体 MCP 工具风险控制。
```

会话级能力授权与单次调用确认必须分开：

```text
session capability:
  allow_write_workspace / allow_network / allow_command / allow_mcp
  表示本会话是否允许使用某类能力。

call approval:
  confirmed + confirmation_id + preview_hash
  表示用户是否批准当前这一笔具体副作用调用。
```

因此：

```text
allow_write_workspace=false:
  任何写操作直接 permission_denied，不进入确认流程。

allow_write_workspace=true 且工具要求确认:
  未取得有效 confirmation ticket 时返回 confirmation_required。

allow_write_workspace=true 且存在匹配的 confirmation ticket:
  才允许执行本次写操作。

仅有 confirmed=true 但缺少匹配 confirmation_id/preview_hash:
  视为无效确认，不得放行。
```

## 8. ToolSpec

`ToolSpec` 是工具声明、模型可见工具列表、参数 schema、风险等级和确认策略的统一来源。

建议字段：

```python
ToolSpec(
    name: str,
    description: str,
    category: str,
    namespace: str,
    parameters_schema: dict,
    required_params: list[str],
    required_any_of: list[list[str]],
    returns_schema: dict,
    enabled: bool,
    risk_level: str,
    requires_confirmation: bool,
    workspace_scope: str,
    timeout_seconds: int,
    max_output_chars: int,
    default_observation_mode: str,
    supports_dry_run: bool,
    fallback_tools: list[str],
    aliases: list[str],
    metadata: dict,
)
```

风险等级：

```text
low
medium
high
blocked
```

workspace_scope：

```text
none
read_workspace
write_workspace
network
command
shell_command
code_execution
mcp
```

## 9. ToolRegistry

`ToolRegistry` 负责：

```text
注册 ToolSpec。
注销 ToolSpec。
查找工具。
处理 alias。
参数 schema 校验。
输出模型可见工具列表。
只暴露 enabled 工具。
支持 MCP 动态工具注册。
```

校验顺序：

```text
1. tool_name 是否存在或是否能解析 alias
2. 工具是否 enabled
3. args 是否 object
4. required_params 是否齐全
5. required_any_of 是否满足
6. 参数类型是否符合 schema
7. unknown args 是否允许
```

ToolRegistry 不执行工具，不做真实权限判断。

## 10. ToolManager / ToolRuntime

`ToolManager` 与 `ToolRuntime` 的职责在 V1 中固定如下，不再作为两个并列运行时：

```text
ToolManager:
  对外 facade。
  保存或接收 handler 注册。
  提供 execute(request) 和迁移期 run_tool(...) 入口。
  不实现第二套旧执行语义。

ToolRuntime:
  唯一正式执行管线。
  负责 Registry 查找、参数校验、Policy 裁决、确认票据校验、
  dry_run、timeout/retry、异常捕获、ToolResult 归一化和 ToolLogger 调用。
```

正式调用关系：

```text
ReActExecutor -> ToolManager.execute(request)
ToolManager -> ToolRuntime.execute(request)
ToolRuntime -> ToolRegistry / ToolPolicy / handler
```

职责：

```text
接收 ToolCallRequest。
查询 ToolRegistry。
调用 ToolPolicy。
处理 timeout。
处理 dry_run。
执行工具实例。
捕获异常。
归一化 ToolResult。
调用 ToolLogger。
返回 ToolResult。
```

`run_tool` 方法名可以保留：

```python
run_tool(tool_name: str, **kwargs) -> ToolResult
```

但这只是迁移期入口名，内部必须转换为：

```text
ToolCallRequest
```

不允许保留旧 ToolManager 旧执行逻辑作为第二套正式运行时。

## 11. ToolPolicy

ToolPolicy 负责工具级安全和权限裁决。

输入：

```text
ToolSpec
ToolCallRequest
ToolCallContext
ToolCallOptions
Resolved paths
Session permission
```

输出：

```text
allowed
requires_confirmation
blocked
risk_level
reason
code
preview_required
```

优先级：

```text
blocked 规则
  > workspace / sensitive path
  > admin required
  > session permission
  > ToolSpec risk
  > ActionPacket request
  > Tool 默认策略
```

blocked 直接返回失败，不进入确认流程。

high 默认确认。

## 12. dry_run / preview

preview 不是独立模型可见工具，而是 `ToolCallOptions.dry_run=true` 加确认事件的执行模式。

适用：

```text
write_file 覆盖前看 diff。
patch_file 前看命中片段和影响行数。
delete_file 前看删除列表和路径风险。
command_tool 前看 command/cwd/risk。
shell_command_tool 前看复杂 shell 风险。
MCP high risk 工具前看 server/tool/参数摘要。
```

不适用：

```text
纯读取
计算
普通文本处理
低风险 metadata 查询
```

dry_run 返回 ToolResult：

```text
success=True
code="dry_run_preview"
metadata.preview = {...}
metadata.requires_confirmation = true/false
```

如果工具不支持 dry_run：

```text
supports_dry_run=false
high risk 时必须保守要求确认
```

## 13. Observation 分级

`observation_mode` 表示工具结果进入 Observation 时的内容粒度。

```text
minimal:
  状态、code、message、关键字段。

standard:
  关键字段 + 受控 preview + 少量结构化内容。

full:
  全文或完整结构，只有安全且确实需要时允许。
```

决策来源：

```text
ToolSpec 默认值
ActionPacket 请求值
ReActExecutor 最终裁决
```

优先级：

```text
安全策略 > 上下文预算 > ReActExecutor 裁决 > ActionPacket 请求 > ToolSpec 默认
```

模型可以请求 full，但 ReActExecutor 可以降级。

Tools 层只提供：

```text
data_summary
preview
raw_ref / artifact_ref
raw_output_truncated
```

最终 Observation 由 ReActExecutor 生成。

## 14. input_summary

`input_summary` 是工具调用入参的安全摘要，用于日志、审计、事件摘要和调试。

它不是模型摘要，不应专门调用 Models 层生成。

生成来源：

```text
ActionPacket.user_visible_message
ActionPacket.action_args.purpose（仅当工具 schema 明确允许且由执行器清洗后）
ToolManager 规则摘要
参数长度
hash
目标路径
provider 名称
```

示例：

```text
write_file:
  file_path=src/app.py
  content_length=5320
  content_hash=...
  write_mode=overwrite

command_tool:
  program=python
  args_count=3
  cwd=.
  purpose=运行测试
```

## 15. 错误码

V1 错误码至少覆盖：

```text
tool_not_found
tool_disabled
invalid_args
missing_required_param
permission_denied
confirmation_required
user_rejected
blocked_by_policy
workspace_out_of_scope
sensitive_path_blocked
admin_permission_required
timeout
output_too_large
internal_error
provider_not_configured
provider_timeout
provider_error
network_not_allowed
search_not_configured
file_not_found
file_too_large
file_already_exists
file_conflict
path_not_found
not_a_directory
too_many_entries
binary_file_not_supported
document_parse_failed
unsupported_document_type
document_too_large
document_encrypted
dependency_not_available
patch_anchor_not_found
patch_ambiguous_match
patch_old_text_not_found
patch_line_mismatch
patch_conflict
delete_directory_not_allowed
glob_delete_not_allowed
command_blocked
command_timeout
command_nonzero_exit
command_launch_failed
command_delete_not_allowed
shell_required
mcp_not_configured
mcp_server_disabled
mcp_connection_failed
mcp_tool_not_found
mcp_invalid_args
mcp_timeout
```

错误类型：

```text
validation
permission
confirmation
timeout
not_found
conflict
provider
network
tool_runtime
internal
```

## 16. retry 边界

工具内部可以小范围 retry：

```text
网络瞬时错误
provider timeout
MCP 连接瞬断
临时文件锁
```

工具内部不 retry：

```text
权限拒绝
参数错误
blocked
用户拒绝
文件不存在
patch 定位失败
高风险副作用操作
```

工具失败后的业务重试、fallback_to_model、fallback_to_tool、ask_user、request_replan 由 ReActExecutor / Checker 决定。

## 17. logs/tools.log

Tools V1 新增：

```text
logs/tools.log
```

格式：

```text
JSONL，每行一条工具调用记录。
```

建议字段：

```text
timestamp
trace_id
execution_id
plan_id
task_id
step_id
call_id
tool_name
tool_category
tool_namespace
provider
input_summary
options_summary
risk_level
requires_confirmation
confirmed
confirmation_id
preview_hash
dry_run
success
code
error_type
retryable
duration_ms
output_summary
raw_output_hash
raw_output_truncated
affected_resources
metadata.artifacts
```

日志默认不记录：

```text
完整文件内容
完整命令输出
完整网页正文
完整 provider raw response
明文 credential
完整 authorization header
敏感路径内容
```

## 18. 用户可见事件建议

Tools 层不直接生成正式 `ExecutionEvent`，但可以在 ToolResult.metadata 中提供事件建议：

```text
event_summary
event_details
preview_summary
affected_resources
artifact_refs
```

正式事件由 ReActExecutor 生成。

典型事件：

```text
tool_started
tool_finished
confirmation_required
tool_failed
observation_created
```

用户可见内容必须简短、脱敏、可理解。
