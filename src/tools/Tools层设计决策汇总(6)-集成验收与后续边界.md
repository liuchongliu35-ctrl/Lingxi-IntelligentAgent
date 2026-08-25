# Tools 层设计决策汇总（6）- 集成验收与后续边界

> 文档状态：Tools 层 V1 正式化设计稿  
> 适用范围：ReActExecutor 集成、ExecutionEvent、Observation、日志、配置、测试、验收和后续边界  

## 1. 集成目标

Tools V1 最终要证明：

```text
ReActExecutor 可以通过结构化 ActionPacket 安全、稳定、可审计地调用真实工具。
```

验收链路：

```text
Analyzer
  -> Planner
  -> ReActExecutor
  -> ToolRegistry / ToolManager
  -> ToolResult
  -> Observation
  -> Checker
  -> ExecutionEvent / ExecutionResult
```

不验收：

```text
旧顺序 Executor 自动 fallback。
完整 Runtime UI。
完整安全权限系统。
完整 RAG。
完整浏览器自动化。
完整 MCP 插件市场。
```

## 2. ReActExecutor 调用 Tools 的正式路径

ReActExecutor 收到模型生成的 ActionPacket：

```json
{
  "action_type": "call_tool",
  "action_target": "read_file",
  "action_args": {
    "path": "README.md"
  },
  "user_visible_message": "读取项目说明",
  "expected_observation": "获得 README 内容"
}
```

执行流程：

```text
1. ReActExecutor 解析 ActionPacket。
2. 校验 action_type / action_target / action_args。
3. 校验当前 PlanStep 是否允许该工具。
4. 查询 ToolRegistry。
5. 构造 ToolCallRequest。
6. 调用 ToolManager。
7. ToolManager 执行 ToolPolicy。
8. ToolManager 调用真实工具。
9. ToolManager 返回 ToolResult。
10. ReActExecutor 根据 ToolResult 生成 Observation。
11. Checker 根据 Observation 决定下一步。
12. ReActExecutor 生成 ExecutionEvent / ExecutionResult。
```

任何一步失败都必须结构化返回，不允许伪成功。

## 3. ActionPacket 与 ToolCallRequest 映射

映射规则：

```text
ActionPacket.action_target
  -> ToolCallRequest.tool_name

ActionPacket.action_args
  -> ToolCallRequest.args

ActionPacket.user_visible_message
  -> input_summary 候选

ActionPacket.action_args.purpose
  -> 仅在工具 schema 明确允许且经 ReActExecutor 清洗后作为 input_summary 候选

ActionPacket.thought_summary
  -> 只供 ReActExecutor 内部受控使用，不原样进入用户事件或 tools.log

RuntimeState / PlanStep / trace
  -> ToolCallContext

ReActExecutor safety / session permission
  -> ToolCallOptions
```

ReActExecutor 应注入：

```text
trace_id
execution_id
plan_id
task_id
step_id
packet_id
session_id
workspace_root
source=react_executor
```

模型不得自行生成：

```text
confirmed=true
admin permission
credential_ref
MCP server command/url
raw filesystem absolute authority
```

这些字段由 ReActExecutor / Runtime / ToolPolicy 注入或裁决。

## 4. ToolResult 到 Observation

Observation 由 ReActExecutor 生成，不由 Tools 层生成。

ToolResult 提供：

```text
success
tool_name
tool_category
message
code
error_type
retryable
data
metadata.preview
metadata.event_summary
metadata.affected_resources
raw_output_truncated
artifact_ref
```

ReActExecutor 生成 Observation 时决定：

```text
observation_mode
data_summary
included_fields
raw_ref / artifact_ref
fallback_used
```

Observation 分级：

```text
minimal:
  状态和关键元数据。

standard:
  状态 + preview + 重要结构化字段。

full:
  完整内容或完整结构，但必须受安全和上下文预算限制。
```

重要约束：

```text
Observation 不默认放完整 raw_output。
Observation 不默认调用模型摘要。
需要模型总结时由 ReActExecutor 单独发起 call_model。
```

## 5. ExecutionEvent

Tools 层不直接生成正式 `ExecutionEvent`。

Tools 可以在 `ToolResult.metadata` 提供事件建议：

```text
event_summary
event_details
preview_summary
affected_resources
artifact_refs
```

ReActExecutor 根据 ToolResult 生成：

```text
tool_started
tool_finished
tool_failed
confirmation_required
observation_created
```

用户可见事件原则：

```text
可读
简短
脱敏
可展开
不含完整 raw_output
不含内部 prompt
不含异常堆栈全量
不含明文凭证
```

示例：

```text
已修改 src/app.py，影响 3 行。
正在运行 python -m pytest tests/test_tools.py。
命令执行失败，exit_code=1，stderr 已截断。
调用 mcp.github.create_issue 前需要确认。
```

## 6. confirmation / preview / resume

高风险工具流程：

```text
ReActExecutor 准备执行工具
  -> ToolManager dry_run
  -> ToolResult(metadata.preview)
  -> ReActExecutor 生成 confirmation_required event
  -> 执行暂停
  -> 用户确认
  -> ReActExecutor resume
  -> ToolManager 真实执行
```

适用：

```text
overwrite
patch
delete_file
move_file
rename_file
command_tool high risk
shell_command_tool
MCP high risk
code_executor
```

blocked 动作：

```text
直接失败，不进入确认。
```

用户拒绝：

```text
ToolResult 或 Control result 标记 user_rejected。
Observation 记录拒绝。
Checker 决定 ask_user / stop / alternative。
```

## 7. 配置文件总览

Tools V1 配置建议：

```text
config/tools/defaults.json
config/tools/providers.json
config/tools/policies.json
config/tools/mcp_servers.json
```

`defaults.json`：

```json
{
  "enabled": true,
  "default_timeout_seconds": 30,
  "max_output_chars": 12000,
  "max_raw_output_chars": 50000,
  "max_observation_chars": 16000,
  "default_observation_mode": "standard",
  "workspace_root_policy": "workspace_only"
}
```

`policies.json`：

```json
{
  "session_permissions": {
    "allow_read_workspace": true,
    "allow_write_workspace": false,
    "allow_network": false,
    "allow_command": false,
    "allow_shell_command": false,
    "allow_mcp": false
  },
  "risk_policy": {
    "low": "allow",
    "medium": "allow_or_confirm",
    "high": "confirm",
    "blocked": "block"
  }
}
```

`providers.json`：

```text
web_search provider 配置。
Tavily API key env。
model_builtin search 配置。
```

`mcp_servers.json`：

```text
MCP Server 配置。
```

## 8. 日志验收

`logs/tools.log` 必须是 JSONL。

每次工具调用至少记录：

```text
timestamp
trace_id
execution_id
step_id
call_id
tool_name
tool_category
provider
input_summary
risk_level
dry_run
requires_confirmation
confirmed
success
code
error_type
retryable
duration_ms
output_summary
raw_output_truncated
```

不能记录：

```text
完整文件内容
完整 stdout/stderr
完整网页正文
完整 MCP 原始响应
明文 API key
明文 Authorization header
敏感 env 值
```

## 9. 测试矩阵

### 9.1 协议底座

```text
ToolResult 字段完整。
ToolResult.to_dict 兼容。
ToolResult.to_text 不泄露 raw_output。
失败不伪装成成功。
tool_category / namespace 正确。
trace 字段传播。
```

### 9.2 ToolRegistry

```text
ToolSpec 注册。
enabled=false 不暴露给模型。
required_params 校验。
required_any_of 校验。
类型校验。
alias 解析。
MCP 动态工具注册。
```

### 9.3 ToolManager

```text
正式 ToolCallRequest 执行。
run_tool 方法名兼容但内部走新协议。
工具不存在。
参数非法。
ToolPolicy blocked。
confirmation_required。
dry_run 不产生副作用。
异常捕获为 internal_error。
日志写入。
```

### 9.4 文件工具

```text
list/find/info/read。
read_file 大文件返回 file_too_large。
read_file_chunk/head/tail。
write_file create/overwrite/append。
patch_file replace/insert_before/insert_after/delete_block。
patch 模糊匹配拒绝。
copy/move/rename。
delete_file 明确文件列表。
目录删除拒绝。
glob 删除拒绝。
敏感路径确认/拒绝。
```

### 9.5 命令工具

```text
command_tool argv。
字符串 command 解析。
shell 元字符返回 shell_required。
shell_command_tool 最小可用。
危险命令 blocked。
删除命令拦截。
cwd 越界拒绝。
timeout。
exit_code。
stdout/stderr 截断。
```

### 9.6 web_search

```text
fake provider success。
network_not_allowed。
search_not_configured。
Tavily response normalize。
model_builtin JSON normalize。
no_url_summary 标记。
provider timeout。
search_tool alias。
Observation 不含 raw_content。
```

### 9.7 MCP

```text
mcp_servers.json 加载。
server_id 校验。
args 数组校验。
fake STDIO initialize。
tools/list。
ToolSpec 动态注册。
tools/call。
remote error。
high risk confirmation。
blocked。
dry_run 不调用远程。
```

### 9.8 ReActExecutor 集成

```text
ActionPacket action_type=call_tool -> ToolCallRequest。
ReActExecutor 不直接执行 shell。
ToolResult -> Observation。
confirmation_required event。
工具失败 -> Checker retry/fallback。
MCP 失败后由 Checker/模型决定是否改用命令。
旧顺序 Executor 不作为 Tools V1 验收目标。
```

## 10. 真实外部测试策略

默认：

```text
真实外部测试 skip。
单元测试用 fake provider / fake MCP Server。
```

启用真实测试必须满足：

```text
RUN_TOOL_INTEGRATION_TESTS=true
```

联网搜索：

```text
RUN_WEB_SEARCH_INTEGRATION_TESTS=true
TAVILY_API_KEY 存在
```

模型联网：

```text
RUN_MODEL_BUILTIN_SEARCH_TESTS=true
Models provider 配置存在
模型 API key 存在
```

MCP：

```text
RUN_MCP_INTEGRATION_TESTS=true
本机具备 npx / uvx / 对应 MCP Server
```

## 11. V1 完成标准

Tools V1 完成时必须满足：

```text
1. 所有正式工具统一返回 ToolResult。
2. ToolResult 字段包含结构化 data、错误码、retryable、trace、耗时和 metadata。
3. ToolSpec 是模型可见工具声明唯一来源。
4. ToolManager 执行前经过 ToolRegistry 和 ToolPolicy。
5. ReActExecutor 不直接执行 shell。
6. Observation 由 ReActExecutor 根据真实 ToolResult 生成。
7. logs/tools.log 与用户事件分离。
8. 文件工具限制 workspace_root。
9. 高风险操作支持 dry_run / preview / confirmation。
10. blocked 动作不能通过确认放行。
11. command_tool / shell_command_tool 边界清晰。
12. read/write/patch/delete/list/find/info 等文件工具可用。
13. document_parser 支持 V1 格式。
14. web_search 支持 Tavily adapter、model_builtin adapter 和 fake provider。
15. MCP V1 支持 STDIO 配置、tools/list、动态 ToolSpec、tools/call 和 fake 测试。
16. 真实 provider 测试默认 skip。
17. 旧顺序 Executor 不进入 Tools V1 正式验收目标。
```

## 12. 后续开发方式

开发前必须先生成步骤文档：

```text
Tools层开发步骤与进度(1)-协议运行时.md
Tools层开发步骤与进度(2)-文件与命令工具.md
Tools层开发步骤与进度(3)-联网搜索.md
Tools层开发步骤与进度(4)-MCP扩展工具.md
Tools层开发步骤与进度(5)-集成验收.md
```

每个 Step 必须包含：

```text
目标
范围
涉及文件
数据结构
错误码
测试
验收命令
完成状态
遗留问题
```

开发规则：

```text
小步推进。
每个 Step 完成后运行相关测试。
每个 Step 完成后更新步骤文档。
不一次性重写整个 Tools 层。
不改 Analyzer / Planner / ReActExecutor 主链路。
不引入旧 Executor 正式 fallback。
```

## 13. V2/V3 后置能力

后置：

```text
完整权限 UI。
管理员权限执行。
跨工作区安全授权。
复杂沙箱虚拟机。
完整浏览器自动化。
fetch_url / read_web_page。
Brave / SerpAPI 等更多 search provider。
长期搜索缓存和索引。
完整 RAG 入库。
MCP Streamable HTTP 真实执行。
MCP Resources / Prompts / Sampling。
MCP 插件市场。
多 Agent 协作。
异步流式工具执行。
跨进程执行状态持久化。
```
