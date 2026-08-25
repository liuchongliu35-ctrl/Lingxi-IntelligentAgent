# Tools 层设计决策汇总（5）- MCP 扩展工具设计

> 文档状态：Tools 层 V1 正式化设计稿  
> 适用范围：自定义 MCP Server、STDIO、本地 MCP 工具发现、动态 ToolSpec、MCPToolGateway、MCP 配置 UI/API、风险与测试  

## 1. 设计目标

MCP 是 Tools 层的可扩展工具来源。

Tools V1 需要支持用户添加自定义 MCP Server，并让模型通过结构化 ActionPacket 调用这些 MCP 工具。

核心原则：

```text
一个统一的 MCP 执行实现。
多个动态注册的 MCP ToolSpec。
所有 MCP 真实结果统一转换为 ToolResult。
```

V1 重点：

```text
本地 STDIO MCP Server。
用户自定义配置。
tools/list 工具发现。
tools/call 工具调用。
动态注册 mcp.<server_id>.<tool_name>。
fake MCP Server 测试。
```

V1 不做：

```text
完整插件市场。
自动安装未知 MCP Server。
Streamable HTTP 真实执行不属于 V1。
MCP Resources。
MCP Prompts。
Sampling。
多级 MCP 嵌套调用。
复杂远程工具平台。
```

Streamable HTTP 字段可以预留，但 V1 只做 STDIO，不做真实执行。

## 2. MCP 在 Agent 链路中的位置

MCP 不改变当前主链路：

```text
用户输入
  -> ReactAgent
  -> Analyzer
  -> Planner
  -> ReActExecutor
  -> 输出反馈处理器
  -> 用户反馈
```

MCP 工具调用属于 ReActExecutor 内部 Tool action：

```text
Thought
  -> ActionPacket(tool_name="mcp.github.search_repositories")
  -> ReActExecutor 校验
  -> ToolRegistry 查找 MCP ToolSpec
  -> ToolPolicy 权限和确认
  -> MCPToolGateway
  -> MCPClient
  -> MCP Server tools/call
  -> MCPToolData
  -> ToolResult
  -> ReActExecutor 生成 Observation
  -> Checker
```

Tools 层不允许：

```text
模型直接连接 MCP Server。
模型传入 MCP Server command/url/credential。
MCP Server 自己决定本地权限。
MCP 调用失败后 Tools 层自己改用命令行。
```

## 3. 为什么不只暴露 mcp_execute

不建议把一个模糊的 `mcp_execute` 作为模型可见主工具。

问题：

```text
模型看不到每个 MCP 工具的独立描述。
模型不知道每个 MCP 工具的参数 schema。
ToolRegistry 无法对具体远程工具做精确校验。
风险等级和确认策略容易藏在字符串里。
Planner 不能稳定判断工具是否可用。
```

推荐：

```text
对外:
  mcp.<server_id>.<tool_name>

对内:
  MCPToolGateway 统一执行
```

示例：

```text
mcp.github.search_repositories
mcp.github.create_issue
mcp.mysql.query
```

可以保留内部调试入口：

```text
mcp_execute
```

但它不作为模型正常生成 ActionPacket 时的主要工具名。

## 4. V1 Transport 策略

MCP 官方定义的常见 transport 包括：

```text
stdio
Streamable HTTP
```

STDIO：

```text
Agent 作为父进程启动 MCP Server 子进程。
通过 stdin / stdout 传输 JSON-RPC 消息。
stderr 可用于日志。
只能用于本机本用户会话。
适合 npx / uvx / python / node 启动的本地 MCP Server。
```

Streamable HTTP：

```text
MCP Server 是独立网络服务。
通过 HTTP POST / GET 通信。
可跨网络。
需要认证、Origin 校验和更复杂安全策略。
```

V1 决策：

```text
真实执行优先实现 STDIO。
HTTP / Streamable HTTP 只预留配置字段和接口，不作为 V1 必须可用能力。
```

原因：

```text
用户主要需求是本地自定义 MCP。
STDIO 更贴近 Codex / Claude / Cursor 等本地开发工具的配置模式。
HTTP 安全面更大，适合后续安全与权限层成熟后实现。
```

## 5. MCP 配置文件

建议文件：

```text
config/tools/mcp_servers.json
```

或拆分目录：

```text
config/tools/mcp/*.json
```

V1 推荐先用单文件，后续再拆分。

配置结构：

```json
{
  "mcpServers": {
    "github": {
      "enabled": true,
      "transport": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": {
        "GITHUB_TOKEN": "${env:GITHUB_TOKEN}"
      },
      "cwd": ".",
      "passEnv": true,
      "allowed_tools": [],
      "tool_policies": {},
      "default_risk_level": "medium",
      "timeout_seconds": 30
    }
  }
}
```

字段说明：

```text
server_id:
  mcpServers 的 key，例如 github。
  只能包含字母、数字、下划线、短横线。

enabled:
  是否启用。

transport:
  V1 真实执行支持 stdio。
  streamable_http 预留。

command:
  STDIO 必填，例如 npx / uvx / python / node。

args:
  参数数组。必须是数组，不能用单个字符串按空格拆。

env:
  环境变量键值对。
  支持 ${env:NAME} 引用系统环境变量。

cwd:
  子进程工作目录。
  默认 workspace_root。

passEnv:
  是否继承父进程环境变量。

allowed_tools:
  远程工具白名单。空数组表示先发现全部，再按策略过滤。

tool_policies:
  对单个远程工具覆盖风险和确认策略。

default_risk_level:
  未识别工具的默认风险等级。

timeout_seconds:
  单次 tools/call 超时。
```

## 6. 配置 UI / API 设计

参考 Codex 风格，自定义 MCP 配置可以按四个区块设计：

```text
基础信息区
本地启动配置区
网络请求配置区
环境与上下文区
```

### 6.1 基础信息区

字段：

```text
服务名称 server_id
显示名称 display_name
启用 enabled
通信协议 transport
```

校验：

```text
server_id 必填。
server_id 唯一。
server_id 只允许英文、数字、下划线、短横线。
```

### 6.2 本地启动配置区

仅 STDIO 显示。

字段：

```text
command
args[]
cwd
passEnv
env
```

重要约束：

```text
args 必须是数组。
UI 必须提供多个输入框或列表编辑。
不能让用户用一个字符串输入 "-y package" 后再靠空格拆分。
```

原因：

```text
MCP Server 启动依赖精确 argv。
字符串拆分容易在路径、引号、空格、Windows 参数中出错。
```

### 6.3 网络请求配置区

V1 预留，不做真实执行。

字段：

```text
endpoint_url
headers
auth_ref
```

如果用户选择 HTTP：

```text
V1 可以保存配置，但启动/调用返回 mcp_transport_not_supported。
或在 UI 禁用真实启用按钮，提示 HTTP transport 后续支持。
```

### 6.4 环境与上下文区

字段：

```text
env
passEnv
cwd
credential_ref
```

安全：

```text
不把明文 token 写入普通日志。
env 中敏感 key 脱敏。
credential_ref 后续接统一凭证管理。
```

## 7. MCPManager

职责：

```text
加载配置。
保存配置。
启用/禁用 server。
启动 STDIO server。
停止 server。
刷新工具列表。
维护连接状态。
向 ToolRegistry 注册/移除 MCP ToolSpec。
```

不负责：

```text
任务规划。
ActionPacket 决策。
Observation 生成。
业务 fallback。
```

状态：

```text
configured
disabled
starting
ready
failed
stopped
tool_discovery_failed
```

## 8. MCPClient

V1 STDIO client 负责：

```text
按 command + args 启动子进程。
建立 stdin / stdout 管道。
发送 MCP initialize。
发送 initialized notification。
调用 tools/list。
调用 tools/call。
监听 stderr 日志。
处理进程退出。
处理 JSON-RPC request/response id。
处理 timeout。
```

子进程规则：

```text
stdout 只能接收 MCP JSON-RPC 消息。
stderr 作为日志捕获。
stdin 只写 MCP JSON-RPC 消息。
```

错误：

```text
mcp_command_not_found
mcp_process_start_failed
mcp_initialization_failed
mcp_stdout_invalid_json
mcp_process_exited
mcp_timeout
```

常见用户提示：

```text
未找到 npx:
  启动失败：未找到 npx 命令，请确认 Node.js 已安装并在 PATH 中。

未找到 uvx:
  启动失败：未找到 uvx 命令，请确认 uv 已安装并在 PATH 中。

MCP Server stderr 报错:
  服务启动后报错，摘要写入 ToolResult.message，完整 stderr 按日志策略截断。
```

## 9. MCPToolAdapter

工具发现流程：

```text
MCPClient.tools/list
  -> remote tools
  -> MCPToolAdapter
  -> ToolSpec
  -> ToolRegistry.register
```

转换规则：

```text
remote name:
  query

server_id:
  mysql

local tool_name:
  mcp.mysql.query
```

ToolSpec.metadata：

```json
{
  "source_type": "mcp",
  "server_id": "mysql",
  "remote_tool_name": "query",
  "transport": "stdio",
  "remote_schema_hash": "..."
}
```

schema 清理：

```text
必须是 object schema。
required 必须是数组。
不支持的 JSON Schema 特性可以保留在 metadata，但 V1 校验只做基础类型。
描述文本需要长度限制。
```

如果远程工具没有 inputSchema：

```text
不注册为可执行工具。
记录 mcp_schema_invalid。
```

## 10. MCPToolGateway

所有 MCP 工具底层统一走 MCPToolGateway。

执行流程：

```text
1. 根据 tool_name 查 ToolSpec。
2. 从 metadata 取 server_id / remote_tool_name。
3. 检查 MCP Server enabled。
4. 检查工具仍在 discovered tools 中。
5. 执行 ToolPolicy。
6. dry_run 时只返回 preview。
7. 非 dry_run 时调用 MCPClient.tools_call。
8. 校验远程结果。
9. 转为 MCPToolData。
10. 返回 ToolResult。
```

MCP tools/call 输入：

```json
{
  "name": "query",
  "arguments": {
    "sql": "select 1"
  }
}
```

模型不可见：

```text
server command
server args
server url
credential_ref
process id
transport internals
```

## 11. MCPToolData

建议结构：

```python
MCPToolData(
    source_type: "mcp",
    server_id: str,
    remote_tool_name: str,
    content: list[dict],
    structured_content: dict | None,
    resource_links: list[dict],
    is_error: bool,
    stderr_preview: str | None,
    output_truncated: bool,
    metadata: dict,
)
```

MCP 返回可能包含文本、结构化内容或资源引用。V1 统一归一化：

```text
text content -> content[]
structured result -> structured_content
resource link -> resource_links
remote isError -> is_error
```

如果远程返回体过大：

```text
mcp_output_too_large
或截断 content 并标记 output_truncated=true
```

## 12. MCP 风险策略

远程描述不能决定最终权限，本地策略才是准绳。

默认：

```text
未知 MCP 工具:
  medium

名称/描述包含 read/get/list/search/query:
  medium 或 low，仍受 allow_mcp 控制。

名称/描述包含 create/update/write/send/post/publish:
  high，需要确认。

名称/描述包含 delete/remove/drop/pay/transfer/execute/shell:
  high 或 blocked。
```

blocked 示例：

```text
执行 shell
支付/转账
删除数据库
删除远程仓库
修改权限
泄露密钥
```

用户可配置：

```json
{
  "tool_policies": {
    "create_issue": {
      "risk_level": "high",
      "requires_confirmation": true
    },
    "query": {
      "risk_level": "medium",
      "requires_confirmation": false
    }
  }
}
```

## 13. MCP dry_run / preview

除非远程 MCP Server 自己支持模拟接口，否则 MCP dry_run 只表示本地预检查。

dry_run 返回：

```text
server_id
remote_tool_name
arguments_summary
risk_level
requires_confirmation
transport
timeout
```

不能声称：

```text
远程操作已经模拟执行。
远程副作用一定不会发生。
```

MCP high risk 调用前必须产生 confirmation preview。

## 14. MCP 失败后的 fallback

MCP 工具失败后，Tools 层只返回 ToolResult。

允许 ReActExecutor / Checker 后续让模型改用命令行：

```text
MCP ToolResult success=False
  -> Observation
  -> Checker fallback_to_tool
  -> 模型生成 command_tool / shell_command_tool ActionPacket
  -> 命令工具安全检查
```

Tools 层不得自己把 MCP 调用失败翻译成命令执行。

## 15. MCP 错误码

V1 至少支持：

```text
mcp_not_configured
mcp_server_disabled
mcp_server_not_found
mcp_transport_not_supported
mcp_command_not_found
mcp_process_start_failed
mcp_connection_failed
mcp_initialization_failed
mcp_tool_list_failed
mcp_tool_not_found
mcp_tool_not_allowed
mcp_schema_invalid
mcp_invalid_args
mcp_timeout
mcp_transport_error
mcp_remote_error
mcp_result_invalid
mcp_output_too_large
mcp_confirmation_required
mcp_blocked
```

## 16. MCP 日志

写入：

```text
logs/tools.log
```

字段：

```text
trace_id
execution_id
server_id
tool_name
remote_tool_name
transport
command_summary
args_count
cwd
duration_ms
success
code
error_type
retryable
stderr_preview
output_summary
```

不得记录：

```text
明文 token
完整 Authorization header
完整 env secret
完整大段远程响应
```

## 17. MCP 配置校验

保存配置时：

```text
server_id 格式校验。
transport 是否支持。
stdio 下 command 必填。
args 必须是 array。
env key 格式校验。
cwd 可解析。
服务名不重复。
```

启用配置时：

```text
command 是否可找到。
cwd 是否存在。
启动是否超时。
initialize 是否成功。
tools/list 是否成功。
工具 schema 是否有效。
```

V1 可以允许“保存但未启用”的配置。

## 18. MCP 测试

单元测试：

```text
加载 mcp_servers.json。
server_id 校验。
args 必须是数组。
env 脱敏。
STDIO command not found。
fake MCP initialize。
fake MCP tools/list。
ToolSpec 动态注册。
mcp.<server_id>.<tool_name> 参数校验。
tools/call 成功归一化。
remote error 归一化。
high risk confirmation。
blocked 工具拒绝。
dry_run 不调用远程。
MCP 失败后不由 Tools 自动命令 fallback。
```

真实测试默认 skip：

```text
RUN_TOOL_INTEGRATION_TESTS=true
RUN_MCP_INTEGRATION_TESTS=true
本地具备 npx / uvx / 对应 MCP server
```

## 19. 后续预留

V2/V3 可做：

```text
Streamable HTTP 真实执行不属于 V1。
OAuth / API key 凭证管理。
MCP 模板市场。
一键安装常用 MCP。
MCP 日志查看器。
Resources / Prompts / Sampling。
远程 MCP Server 安全认证。
工具调用频率限制。
```

## 20. 参考资料

- [Model Context Protocol transports](https://modelcontextprotocol.io/specification/2025-03-26/basic/transports)
- [OpenAI API quickstart - tools and Remote MCP](https://platform.openai.com/docs/quickstart/make-your-first-api-request)
