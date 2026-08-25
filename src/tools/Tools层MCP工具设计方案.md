# Tools 层 MCP 工具设计方案

> 文档性质：Tools 层 V1 设计阶段的 MCP 补充方案  
> 当前状态：设计参考，尚未进入正式代码实现  
> 使用方式：后续 Tools 层设计问答、设计决策汇总和开发步骤文档需要参考本文档

## 1. 设计背景

Agent 除了内置的基础工具之外，还需要支持用户自行添加 MCP Server，从而扩展数据库查询、GitHub 操作、浏览器能力、第三方 API、项目管理系统等外部能力。

MCP 工具不能被当成一条任意执行的命令，也不能让模型直接连接或控制 MCP Server。MCP 能力必须纳入本项目现有的 Tools 层协议、安全检查、权限确认、超时、错误分类、日志和 Observation 流程。

本方案的核心原则是：

```text
一个统一的 MCP 执行实现
多个动态注册的 MCP ToolSpec
所有真实结果统一转换为 ToolResult
```

## 2. MCP 在 Agent 中的定位

MCP 是 Tools 层的扩展能力，不是新的 Agent 编排层，也不是 Models 层能力。

整体关系：

```text
用户添加 MCP Server 配置
  -> MCPManager 连接 MCP Server
  -> 发现 MCP 工具及参数 schema
  -> 转换为本项目 ToolSpec
  -> 注册到 ToolRegistry
  -> Planner / ReActExecutor / Model 获取可用工具列表
  -> ReActExecutor 根据 ActionPacket 调用 MCP 工具
  -> MCPToolGateway 调用真实 MCP Server
  -> 转换为 ToolResult
  -> ReActExecutor 生成真实 Observation
  -> Checker 判断继续、重试、询问用户、结束或失败
```

MCP 工具调用仍然属于 ReActExecutor 的内部执行循环：

```text
Thought
  -> Action(ActionPacket)
  -> ToolRegistry 校验工具和参数
  -> 权限、风险、确认和超时检查
  -> MCPToolGateway
  -> MCP Server
  -> ToolResult
  -> Observation
  -> Checker
```

MCP 不改变当前外部主链路：

```text
用户输入
  -> ReactAgent
  -> Analyzer
  -> Planner
  -> ReActExecutor
  -> 输出反馈处理器
  -> 用户反馈
```

## 3. 是否需要单独的 MCP 执行工具

### 3.1 需要统一的 MCP 执行实现

所有 MCP 工具的底层调用流程具有共同特点：

```text
定位 MCP Server
  -> 校验工具名称
  -> 校验工具参数
  -> 检查 MCP Server 是否启用
  -> 建立或复用连接
  -> 发起 tools/call
  -> 接收结果或错误
  -> 归一化 ToolResult
  -> 记录日志
```

因此，Tools 层应使用统一的 MCP 执行网关，例如：

```text
MCPToolGateway
```

它负责处理所有 MCP 工具的通用执行逻辑，不为每一个 MCP 工具重复实现一套执行器。

### 3.2 不建议只向模型暴露一个模糊的 mcp_execute

不建议让模型只能调用如下工具：

```json
{
  "tool_name": "mcp_execute",
  "action_args": {
    "tool_name": "某个工具",
    "arguments": {}
  }
}
```

这种方式的问题是：

```text
模型无法直接看到每个 MCP 工具的独立描述
模型无法准确知道每个 MCP 工具需要哪些参数
ToolRegistry 难以对具体 MCP 工具做 schema 校验
风险等级和确认策略容易被隐藏在任意字符串中
Planner 和 ReActExecutor 的工具可用性判断不够明确
```

### 3.3 推荐的最终形态

统一的底层执行网关与独立的动态工具声明同时存在：

```text
模型和 ReActExecutor 看到：
  mcp.github.search_repositories
  mcp.github.create_issue
  mcp.github.get_file_contents

Tools 层内部执行：
  所有上述工具都由 MCPToolGateway 调度
```

也就是说：

```text
对外：每一个 MCP 工具都是独立 ToolSpec
对内：所有 MCP 工具共享 MCPToolGateway
```

可以保留一个内部调试入口 `mcp_execute`，但不作为模型正常生成 ActionPacket 时的主要工具名。

## 4. MCP 工具命名

推荐使用稳定的命名空间：

```text
mcp.<server_id>.<tool_name>
```

例如：

```text
mcp.github.search_repositories
mcp.github.create_issue
mcp.filesystem.read_document
```

命名要求：

```text
server_id 必须来自本地已注册配置
tool_name 必须来自 MCP Server 实际发现的工具列表
模型不能通过参数伪造 server_id
模型不能调用未注册或未启用的 MCP 工具
同名工具必须通过 server_id 隔离
```

如果 MCP Server 的原始工具名包含不适合本项目协议的字符，适配器可以生成内部稳定名称，同时在 ToolSpec.metadata 中保留原始名称：

```json
{
  "name": "mcp.github.search_repositories",
  "metadata": {
    "source_type": "mcp",
    "server_id": "github",
    "remote_tool_name": "search_repositories"
  }
}
```

## 5. 核心组件职责

### 5.1 MCPServerConfig

保存一个 MCP Server 的本地配置和安全策略。

建议字段：

```text
server_id
display_name
enabled
transport
command
args
url
credential_ref
allowed_tools
default_risk_level
require_confirmation
timeout_seconds
metadata
```

说明：

```text
stdio：
  通过本地 command 和 args 启动 MCP Server

streamable_http：
  通过 url 连接远程 MCP Server。V1 只保存预留配置，不做真实连接。

credential_ref：
  只保存凭证引用，不在普通配置文件和日志中保存明文密钥

allowed_tools：
  可选的工具白名单。未列入白名单的远程工具不得注册或执行
```

### 5.2 MCPManager

负责 MCP Server 的生命周期和工具发现：

```text
添加 Server 配置
删除 Server 配置
启用或禁用 Server
连接 Server
断开 Server
刷新 Server 工具列表
查看连接状态
获取已发现工具
```

MCPManager 不直接替代 ReActExecutor 做任务决策，也不生成 Thought、ActionPacket 或 Observation。

### 5.3 MCPClient

负责与单个 MCP Server 通信：

```text
初始化连接
执行 MCP 初始化握手
获取远程工具列表
调用远程工具
处理连接关闭
处理传输层错误
```

MCPClient 不负责本项目的最终权限判断。远程工具调用前仍然必须经过本项目 Tools 层的 ToolSpec 和安全策略检查。

### 5.4 MCPToolAdapter

负责把 MCP Server 返回的工具声明转换为本项目的 ToolSpec：

```text
远程工具名称 -> 本地稳定工具名称
远程 description -> ToolSpec.description
远程 inputSchema -> ToolSpec.parameters_schema
Server 默认风险 -> 本地 risk_level
Server 来源信息 -> ToolSpec.metadata
```

适配器还需要对远程 schema 做基本清理和兼容处理，不能直接无校验地把远程数据暴露给模型或执行器。

### 5.5 MCPToolGateway

负责执行已经通过本项目检查的 MCP 工具调用：

```text
根据 ToolSpec.metadata 定位 MCP Server
检查 Server 当前是否 enabled
检查远程工具是否仍然存在
调用 MCPClient.tools_call
限制超时和输出大小
将远程结果转换为 ToolResult
记录 tools.log
```

## 6. 动态注册流程

用户添加 MCP Server 后，推荐执行以下流程：

```text
1. 读取并校验 MCPServerConfig
2. 检查 transport、command、args 或 url
3. 解析 credential_ref
4. 建立连接
5. 执行工具发现
6. 对每个远程工具做 schema 校验和名称空间转换
7. 应用 allowed_tools 白名单
8. 应用本地风险和确认策略
9. 注册为 ToolSpec
10. 更新 ToolRegistry
11. 让 Planner / ReActExecutor 后续调用获取最新工具列表
```

如果连接成功但工具发现失败：

```text
MCP Server 可以保留为已配置但不可用状态
不得把未发现 schema 的工具注册为可执行工具
应返回结构化错误和连接诊断信息
```

如果刷新后远程工具消失：

```text
旧 ToolSpec 不应继续执行
应标记为 unavailable 或从可用工具列表移除
历史日志仍保留原始调用名称和版本信息
```

## 7. ToolSpec 统一协议

MCP 工具注册后必须遵守本项目统一 ToolSpec，不应成为特殊的裸工具。

示例：

```json
{
  "name": "mcp.github.search_repositories",
  "description": "Search repositories through the configured GitHub MCP Server.",
  "parameters_schema": {
    "type": "object",
    "properties": {
      "query": {
        "type": "string"
      }
    },
    "required": ["query"]
  },
  "required_params": ["query"],
  "returns_schema": {
    "type": "object"
  },
  "risk_level": "medium",
  "requires_confirmation": false,
  "workspace_scope": "network",
  "timeout": 30,
  "category": "mcp",
  "metadata": {
    "source_type": "mcp",
    "server_id": "github",
    "remote_tool_name": "search_repositories"
  }
}
```

需要特别注意：

```text
远程 schema 只描述参数格式
本地 ToolSpec 还必须补充风险、确认、超时、网络和审计信息
远程 Server 不能覆盖本项目的 blocked 安全策略
模型只能使用 ToolRegistry 当前暴露的 ToolSpec
```

## 8. ActionPacket 调用协议

模型正常调用 MCP 工具时，仍然使用现有 ActionPacket：

```json
{
  "action_type": "call_tool",
  "action_target": "mcp.github.search_repositories",
  "action_args": {
    "query": "python agent framework"
  },
  "user_visible_message": "查询与当前任务相关的 GitHub 项目",
  "expected_observation": "获得仓库搜索结果"
}
```

模型不得直接生成：

```text
MCP Server 的 command
MCP Server 的 url
credential_ref
内部连接句柄
底层传输控制字段
```

这些字段由 MCPManager、ToolRegistry 和 MCPToolGateway 根据本地注册配置注入。

执行前必须经过：

```text
ActionPacket 基础校验
tool_name 是否存在
MCP ToolSpec 是否存在且 enabled
action_args 是否符合远程 schema
Server 是否已连接
本地风险和权限检查
确认状态检查
timeout 限制
```

## 9. ToolResult 归一化

无论 MCP Server 返回文本、结构化对象、资源链接还是错误，都必须转换为统一 ToolResult 外壳：

```text
ToolResult
  success
  tool_name
  data
  message
  error
  error_code
  error_type
  retryable
  duration_ms
  trace_id
  metadata
```

建议的 MCP 专用 data 结构：

```json
{
  "source_type": "mcp",
  "server_id": "github",
  "remote_tool_name": "search_repositories",
  "content": [],
  "structured_content": {},
  "resource_links": [],
  "is_error": false
}
```

不同工具的 `data` 结构可以不同，但 ToolResult 外壳必须统一：

```text
web_search.data       -> WebSearchData
read_file.data        -> FileReadData
command_tool.data     -> CommandExecutionData
mcp tool.data         -> MCPToolData
```

MCP 原始响应可以保留在开发日志的脱敏摘要中，但不应未经限制地直接写入用户可见 events。

## 10. 错误分类和重试

MCP V1 建议至少区分：

```text
mcp_not_configured
mcp_server_disabled
mcp_server_not_found
mcp_connection_failed
mcp_initialization_failed
mcp_tool_not_found
mcp_tool_not_allowed
mcp_schema_invalid
mcp_invalid_args
mcp_timeout
mcp_transport_not_supported
mcp_transport_error
mcp_remote_error
mcp_result_invalid
mcp_output_too_large
mcp_confirmation_required
mcp_blocked
```

重试建议：

```text
连接瞬时失败、网络超时：
  可以小次数重试

参数校验失败、工具不存在、未授权：
  不自动重试，应返回失败并交给 Checker 或模型处理

远程业务错误：
  默认不自动重试，除非 ToolSpec 明确允许

高风险动作：
  重试前必须重新确认是否会产生重复副作用
```

MCPToolGateway 不负责替代 ReActExecutor 的业务重规划。工具失败后，真实 ToolResult 交给 ReActExecutor，由 Checker 决定停止、重试、请求用户、切换已有工具或交给模型重新判断。

## 11. 权限和确认

MCP 工具默认不能因为“来自用户配置”就完全信任。

建议按能力分类：

```text
只读查询、搜索、获取状态：
  medium risk，可按全局网络权限自动执行

创建、修改、发送数据、发布内容：
  high risk，默认需要用户确认

删除、支付、权限变更、执行系统命令：
  blocked 或必须明确确认
```

确认策略至少需要支持：

```text
require_confirmation
confirmed
dry_run
approval_scope
```

对于 MCP 工具，dry_run 只能表示本项目已完成参数、权限和影响范围预检查。除非远程 MCP Server 自己提供模拟接口，否则不能保证远程操作已经被真正模拟而没有副作用。

## 12. 日志和用户事件

MCP 开发日志写入 `logs/tools.log`，建议记录：

```text
trace_id
execution_id
server_id
tool_name
remote_tool_name
transport
开始时间
耗时
参数摘要或 hash
结果状态
error_code
重试次数
输出摘要
```

以下内容默认不得写入普通日志：

```text
明文 credential
完整授权 header
完整敏感参数
未脱敏文件内容
大段远程响应
```

用户可见事件只展示经过摘要化的内容，例如：

```text
正在调用 GitHub MCP 的仓库搜索工具
已获得 5 条搜索结果
调用 GitHub MCP 的创建 issue 工具前需要你的确认
MCP 工具调用失败：远程服务暂时不可用
```

原始 MCP 响应、内部连接信息和完整开发日志不直接作为用户事件输出。

## 13. MCP 与基础工具的关系

基础工具和 MCP 扩展工具的关系如下：

```text
read_file / write_file / patch_file
command_tool
web_search
document_parser
  -> Agent 自带、协议稳定、优先保证可用

mcp.<server_id>.<tool_name>
  -> 用户动态添加、按需发现、受本地策略控制
```

如果 MCP 工具不可用：

```text
优先根据 ToolResult 交给 Checker 判断
如果已有 command_tool 能安全完成等效操作，可以由模型重新生成 command ActionPacket
如果没有等效能力，则向用户说明缺少哪个 MCP 工具或 Server
不得伪造 MCP 已经执行成功
```

## 14. MCP V1 建议范围

建议 Tools V1 支持：

```text
MCP Server 配置加载
stdio MCP Server 连接
Streamable HTTP MCP Server 配置预留
tools/list 工具发现
动态转换为 ToolSpec
ToolRegistry 注册和移除
tools/call 调用
统一 ToolResult
超时和基础重试
工具白名单
风险等级和确认策略
连接状态和工具调用日志
```

V1 传输边界：

```text
MCP V1 只真实支持 stdio。
streamable_http 可以出现在配置 schema 中，但 enabled=true 时不得真实连接。
调用 streamable_http server 必须返回 mcp_transport_not_supported。
```

建议暂不纳入 MCP V1：

```text
Streamable HTTP MCP Server 真实连接
完整 MCP 插件市场
自动下载安装未知 MCP Server
任意远程 MCP Server 的无确认执行
MCP Resources 的完整文件系统映射
MCP Prompts 的完整管理
Sampling 或多级 MCP 嵌套调用
复杂沙箱和跨进程持久化恢复
```

## 15. 推荐实现顺序

后续正式开发时建议按以下顺序推进：

```text
Step 1：定义 MCPServerConfig、MCPToolData 和错误码
Step 2：扩展 ToolSpec.metadata，支持 source_type=mcp
Step 3：实现 MCPServer 注册、启用、禁用和状态查询
Step 4：实现单个 MCP Server 的连接和 tools/list
Step 5：实现 MCPToolAdapter，将远程工具注册到 ToolRegistry
Step 6：实现 MCPToolGateway 和 tools/call
Step 7：接入 ToolManager 正式调用入口
Step 8：接入 ReActExecutor 的 ActionPacket、确认、Observation 和 Checker
Step 9：补充 tools.log、脱敏、超时、错误分类和重试
Step 10：补充 fake MCP Server、单元测试、集成测试和安全回归测试
```

## 16. 当前暂定结论

当前 MCP 设计暂定为：

```text
1. MCP 是 Tools 层的可扩展工具来源。
2. 需要统一 MCPToolGateway，但不向模型暴露模糊的 mcp_execute 作为主要调用方式。
3. 每个已发现的 MCP 工具都转换成独立的 ToolSpec。
4. 工具名使用 mcp.<server_id>.<tool_name> 命名空间。
5. 所有 MCP 工具都通过 ActionPacket 调用。
6. MCP Server 的连接信息和凭证由本地配置管理，模型不能自行指定。
7. MCP 真实结果必须归一化为 ToolResult。
8. MCP 工具必须经过本地 schema、安全、权限、确认和超时检查。
9. MCP 的开发日志与用户可见 events 分离。
10. MCP V1 先做工具发现和工具调用，不扩展为完整插件市场或复杂 MCP 平台。
```

这些结论仍需在 Tools 层后续设计问答中确认，确认后再写入正式的 `Tools层设计决策汇总.md`。
