# Tools 层开发步骤与进度（4）- MCP 扩展工具

> 覆盖步骤：Step 30-37  
> 当前状态：Step 37 已完成，MCP 扩展工具分卷已完成  
> 前置步骤：Step 0-9  
> 上位设计：`Tools层设计决策汇总(5)-MCP扩展工具设计.md`、`Tools层MCP工具设计方案.md`

MCP 是 Tools 层的动态工具来源，不是新的执行器。V1 真实实现只覆盖本地 STDIO；每个远程工具对模型暴露为独立的 `mcp.<server_id>.<tool_name>`，底层调用统一经过 `MCPToolGateway`。

---

## Step 30：MCP 配置 schema、加载、保存与脱敏

**状态：已完成**

### 目标

建立可保存但不必立即启用的 MCP Server 配置，严格区分用户配置、运行时解析值和模型可见工具信息。

### 涉及文件 / 建议新增

```text
src/tools/mcp/__init__.py
src/tools/mcp/config.py
src/tools/mcp/protocol.py
tests/test_mcp_config_v1.py
```

使用：

```text
config/tools/mcp_servers.json
```

### 配置字段

```text
server_id（mcpServers 的 key）
display_name
enabled
transport
command
args[]
env
cwd
passEnv
allowed_tools[]
tool_policies
default_risk_level
timeout_seconds
endpoint_url（预留）
headers（预留）
credential_ref（预留）
```

### 校验

```text
server_id 唯一，只含字母、数字、下划线、短横线。
stdio 下 command 必填。
args 必须是数组，禁止把 "-y package" 当字符串再按空格拆。
env 必须是键值对象。
cwd 必须可在允许范围内解析。
transport=streamable_http 可保存但不能启用真实执行。
```

### 环境变量

支持：

```text
${env:GITHUB_TOKEN}
```

解析值只存在运行时内存；序列化、日志和错误消息均显示脱敏引用，不显示真实 token。

### 明确不做

```text
不自动下载或安装 MCP Server。
不验证未知 npm/pip 包可信度。
不实现 HTTP transport 调用。
不把 command/args/env 暴露给模型。
```

### 测试与验收

```text
合法 STDIO 配置。
server_id/args/env/cwd 错误。
保存但禁用。
HTTP 配置预留。
env 引用解析与脱敏。
```

```powershell
python -m pytest tests/test_mcp_config_v1.py -q
```

### Step 30 完成记录（2026-08-17）

```text
修改文件:
  config/tools/mcp_servers.json
  src/tools/__init__.py
  src/tools/config.py
  src/tools/mcp/__init__.py
  src/tools/mcp/config.py
  src/tools/mcp/protocol.py
  tests/test_mcp_config_v1.py
  tests/test_tool_config_v1.py

实现内容:
  1. 新增 MCP 配置包，不启动进程、不发现工具、不执行 tools/call。
  2. 正式支持 config/tools/mcp_servers.json 的 mcpServers 根对象：
     mcpServers.<server_id> 作为唯一 server_id 来源。
  3. MCPServerConfig / MCPServersConfig 固定 Step 30 配置字段：
     display_name、enabled、transport、command、args、env、cwd、passEnv、
     allowed_tools、tool_policies、default_risk_level、timeout_seconds、
     endpoint_url、headers、credential_ref。
  4. 校验 server_id 只允许字母、数字、下划线和短横线；stdio 下 command 必填；
     args 必须是数组；env/header 必须是键值对象；cwd 必须解析在 workspace 内。
  5. 支持 ${env:NAME} 运行时引用解析；真实 env 值只存在 MCPResolvedServerConfig，
     不写回配置、不进入 ToolsConfig.to_dict。
  6. 敏感 env/header key 只允许使用 ${env:NAME} 引用，拒绝明文 secret；
     序列化、保存和错误消息不泄露真实 token。
  7. streamable_http 只作为预留 transport 可保存；Step 30 不实现真实 HTTP 调用。
  8. 新增 save_mcp_servers_config_file / load_mcp_servers_config_file，
     保存时统一输出正式 mcpServers 格式。
  9. load_tools_config 已接入结构化 MCPServersConfig，并兼容迁移期旧
     {"servers": []} 空配置。

测试命令:
  python -B -m unittest tests.test_mcp_config_v1
  python -B -m unittest tests.test_mcp_config_v1 tests.test_tool_config_v1 tests.test_tools_current_baseline tests.test_tool_logging_v1 tests.test_tool_registry_dynamic tests.test_tool_registry_v1
  python -B -m compileall -q src\tools tests\test_mcp_config_v1.py
  python -B -m unittest discover -s tests -p 'test_*.py'

测试结果:
  Step 30 专项测试: 7 tests OK。
  配置/日志/Registry 聚焦回归: 41 tests OK。
  compileall 通过。
  全量 unittest: 665 tests OK, skipped=3。

边界:
  1. 本步骤只处理配置 schema、加载、保存和脱敏，不启动 MCP Server。
  2. 不自动下载或安装 npx/uvx/uv/npm/pip 包。
  3. 不向模型暴露 command、args、env、headers、credential_ref 等本地启动配置。
  4. streamable_http 只保存配置，真实调用需在后续版本返回
     mcp_transport_not_supported。
  5. 明文敏感 env/header 值直接拒绝；普通非敏感 env 字面量仍可保存。

遗留:
  Step 31 定义 MCP 协议对象、状态与错误码。
  Step 32 开始实现本地 STDIO 进程和 JSON-RPC 通信底座。
```

---

## Step 31：MCP 协议对象、状态与错误码

**状态：已完成**

### 目标

定义 MCPManager、MCPClient、Adapter 和 Gateway 共享的数据结构，避免直接在各模块传裸 JSON。

### 对象建议

```text
MCPServerConfig
MCPServerState
MCPConnectionInfo
MCPRemoteTool
MCPCallRequest
MCPCallResult
MCPToolData
MCPProtocolError
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

错误码至少覆盖设计文档中的：

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

### MCPToolData

至少：

```text
source_type="mcp"
server_id
remote_tool_name
content[]
structured_content
resource_links[]
is_error
stderr_preview
output_truncated
metadata
```

### 明确不做

```text
不把 MCP JSON-RPC response 原样作为 ToolResult.data。
不把远程 isError=false 当作本地策略成功的唯一依据。
不定义 Resources/Prompts/Sampling 对象。
```

### 测试与验收

```text
协议对象可序列化。
状态迁移枚举稳定。
远程错误与本地错误区分。
大输出字段可截断。
```

```powershell
python -m pytest tests/test_mcp_protocol_v1.py -q
```

### Step 31 完成记录（2026-08-17）

```text
修改文件:
  src/tools/__init__.py
  src/tools/data_types.py
  src/tools/errors.py
  src/tools/mcp/__init__.py
  src/tools/mcp/protocol.py
  tests/test_mcp_protocol_v1.py

实现内容:
  1. 补齐 MCP V1 错误码词表，并纳入 ToolErrorCode /
     normalize_error_code / error_type_for_code / is_retryable_code。
  2. 新增 MCP_ERROR_CODES，覆盖设计文档要求的：
     mcp_not_configured、mcp_server_disabled、mcp_server_not_found、
     mcp_transport_not_supported、mcp_command_not_found、
     mcp_process_start_failed、mcp_connection_failed、
     mcp_initialization_failed、mcp_tool_list_failed、mcp_tool_not_found、
     mcp_tool_not_allowed、mcp_schema_invalid、mcp_invalid_args、
     mcp_timeout、mcp_transport_error、mcp_remote_error、
     mcp_result_invalid、mcp_output_too_large、mcp_confirmation_required、
     mcp_blocked。
  3. 额外预留 Step 32 需要的 mcp_stdout_invalid_json /
     mcp_process_exited，避免 STDIO 生命周期实现时再扩展基础词表。
  4. 固定 MCPServerState 与 MCP_SERVER_STATES：
     configured、disabled、starting、ready、failed、stopped、
     tool_discovery_failed。
  5. 新增 MCPProtocolError、MCPConnectionInfo、MCPRemoteTool、
     MCPCallRequest、MCPCallResult 等 Manager / Client / Adapter /
     Gateway 共享协议对象。
  6. MCPRemoteTool 校验 input_schema 必须是 object schema，
     required 必须是数组，并提供稳定 schema_hash。
  7. MCPCallRequest.to_json_rpc_params() 只输出远程 tools/call 需要的
     name / arguments，不携带本地 trace、权限或 ActionPacket。
  8. MCPCallResult 可归一化为 MCPToolData；远程 is_error / 本地协议错误
     通过不同 code 和对象区分。
  9. 为 MCPToolData 补 __post_init__，稳定 source_type/content/resource_links/
     metadata 等字段，并提供 truncate_mcp_tool_data 作为后续 Gateway 的
     输出边界底座。

测试命令:
  python -B -m unittest tests.test_mcp_protocol_v1
  python -B -m unittest tests.test_mcp_config_v1 tests.test_mcp_protocol_v1 tests.test_tool_config_v1 tests.test_tool_registry_v1 tests.test_tool_registry_dynamic tests.test_tools_current_baseline tests.test_tool_result_v1
  python -B -m compileall -q src\tools tests\test_mcp_protocol_v1.py
  python -B -m unittest discover -s tests -p 'test_*.py'

测试结果:
  Step 31 专项测试: 6 tests OK。
  MCP/配置/Registry/ToolResult 聚焦回归: 51 tests OK。
  compileall 通过。
  全量 unittest: 671 tests OK, skipped=3。

边界:
  1. 本步骤只定义协议对象、状态和错误码，不启动进程、不握手、不调用 tools/list 或 tools/call。
  2. 不把 JSON-RPC 原始 response 作为正式 ToolResult.data；后续 Gateway 仍需归一化到 MCPToolData。
  3. MCPCallRequest 的 JSON-RPC params 不携带本地 trace、权限、credential 或完整 ActionPacket。
  4. Resources、Prompts、Sampling 对象仍明确后置，不在 Step 31 定义。

遗留:
  Step 32 实现本地 STDIO 进程、JSON-RPC framing、timeout 与生命周期。
```

---

## Step 32：STDIO 进程与 MCPClient 生命周期

**状态：已完成**

### 目标

实现安全、可关闭、可超时的本地 MCP STDIO 子进程通信底座。

### 子步骤拆分

```text
Step 32.1 进程启动/关闭:
  command/args 分离、cwd/env 策略、stderr 摘要、stop/意外退出处理。

Step 32.2 JSON-RPC framing/request id:
  stdin/stdout 消息封装、id 唯一、响应匹配、notification 忽略、非法 JSON 错误。

Step 32.3 timeout/并发策略:
  V1 默认串行请求；超时后清理等待状态，迟到响应不能污染下一次调用。
```

### 建议新增

```text
src/tools/mcp/stdio_client.py
src/tools/mcp/process.py
tests/test_mcp_stdio_client.py
tests/fixtures/fake_mcp_server.py
```

### 生命周期

```text
校验配置
  -> 查找 command
  -> 启动子进程
  -> 建立 stdin/stdout/stderr
  -> 发送请求并按 id 等待响应
  -> 捕获 stderr
  -> timeout/进程退出处理
  -> stop 时关闭管道和子进程
```

### 进程规则

```text
command 和 args 分离传给 subprocess。
默认 cwd=workspace_root 或已校验配置 cwd。
passEnv=false 时只传最小环境和显式 env。
stdout 只解析 JSON-RPC 消息。
stderr 只作为日志/错误摘要。
```

### 并发与 request id

V1 可以选择串行请求以降低复杂度，但必须：

```text
request id 唯一。
响应 id 匹配。
收到 notification 不误当 response。
非法 JSON 有稳定错误。
超时后不能把迟到响应配给下一请求。
```

### 退出

```text
正常 stop:
  关闭输入，等待有限时间，必要时终止本进程启动的子进程。

意外退出:
  状态 failed/stopped。
  正在等待的请求返回 mcp_process_exited。
```

不得递归删除或操作用户其他进程。

### 明确不做

```text
不支持远程 HTTP。
不实现自动重启无限循环。
不启动未知安装命令。
不让模型控制 passEnv/env/command。
```

### 测试与验收

使用本地 fake server：

```text
启动/停止。
command not found。
非法 stdout JSON。
stderr 捕获。
请求 id 匹配。
timeout。
进程意外退出。
环境变量脱敏。
```

```powershell
python -m pytest tests/test_mcp_stdio_client.py -q
```

### Step 32 完成记录（2026-08-17）

```text
修改文件:
  src/tools/__init__.py
  src/tools/mcp/__init__.py
  src/tools/mcp/process.py
  src/tools/mcp/stdio_client.py
  tests/fixtures/fake_mcp_server.py
  tests/test_mcp_stdio_client.py
  src/tools/Tools层开发步骤与进度(4)-MCP扩展工具.md

实现摘要:
  1. 新增 MCPStdioProcess，负责本地 STDIO server 的 command/args 分离启动、
     cwd/env 策略、stdout/stderr 管道、stderr 摘要、stop/terminate/kill 生命周期。
  2. 新增 MCPStdioClient，提供串行 JSON-RPC request/notification，固定 id 生成、
     响应 id 匹配、notification 忽略、非法 stdout JSON、remote error、timeout、
     进程退出等稳定错误映射。
  3. passEnv=false 只透传最小启动环境与显式配置 env；最小环境按大小写无关匹配，
     覆盖 Windows Python fake server 启动所需系统键，不透传父进程敏感变量。
  4. command 解析只使用配置中的 command/args，subprocess 固定 shell=False；
     不允许模型控制 command、args、env、passEnv，也不做 HTTP transport 或自动重启。
  5. 新增本地 fake_mcp_server.py，覆盖 echo、notification、mismatched id、
     invalid JSON、sleep timeout、exit、remote error、stderr/env 场景。
  6. 更新 MCP 包和 Tools 包导出，后续 Step 33/35 可复用 MCPStdioClient /
     MCPStdioProcess 作为正式 STDIO 通信底座。

测试命令:
  python -B -m unittest tests.test_mcp_stdio_client
  python -B -m unittest tests.test_mcp_config_v1 tests.test_mcp_protocol_v1 tests.test_mcp_stdio_client
  python -B -m compileall -q src\tools tests\test_mcp_stdio_client.py tests\fixtures\fake_mcp_server.py
  python -B -m unittest tests.test_tool_config_v1 tests.test_tools_current_baseline tests.test_tool_manager_v1
  python -B -m unittest discover -s tests -p 'test_*.py'

测试结果:
  tests.test_mcp_stdio_client: 11 tests OK
  MCP config/protocol/stdio: 24 tests OK
  compileall: OK
  Tools config/baseline/manager: 23 tests OK
  全量 unittest discover: Ran 682 tests, OK (skipped=3)

边界:
  1. MCP V1 当前真实执行只支持本地 STDIO；非 stdio transport 仍返回
     mcp_transport_not_supported。
  2. Step 32 只实现进程与 JSON-RPC 生命周期，不做 initialize/initialized、
     tools/list、动态 ToolSpec 注册或 tools/call 结果归一化。
  3. 超时后 V1 仍采用串行请求策略；迟到响应会因 id 不匹配被丢弃，不能污染下一次调用。
  4. stderr 只保存 bounded preview，作为审计/诊断摘要，不进入模型可伪造结果链路。

遗留:
  Step 33 实现 initialize、initialized 与 tools/list，并在发现阶段校验
  MCPRemoteTool schema 后再接入动态注册。
```

---

## Step 33：initialize、initialized 与 tools/list

**状态：已完成**

### 目标

完成 MCP 协议握手和远程工具发现，只有握手与 schema 校验成功的工具才能进入 Registry。

### 执行顺序

```text
start process
  -> initialize request
  -> 校验 server capabilities/version
  -> initialized notification
  -> tools/list
  -> 校验 remote tools
  -> state=ready
```

### 工具发现校验

每个 remote tool 至少：

```text
name 非空
description 受长度限制
inputSchema 是 object schema
required 是数组
```

无效工具：

```text
不注册。
记录 mcp_schema_invalid。
其余有效工具是否继续注册需固定策略；
建议允许部分成功，并在 server state/metadata 记录 skipped_tools。
```

`allowed_tools` 非空时只保留白名单。

### 刷新

MCPManager 提供显式 refresh：

```text
重新 tools/list
比较 schema hash
更新/移除该 server 的动态 ToolSpec
```

不在每次调用前自动 list。

### 明确不做

```text
不实现 Resources/Prompts。
不根据远程描述直接授予低风险。
不把握手原始响应全量写日志。
```

### 测试与验收

```text
正常握手。
版本/能力异常。
initialized 顺序。
tools/list 成功/失败。
无效 schema 部分跳过。
allowed_tools。
refresh schema 变化。
```

```powershell
python -m pytest tests/test_mcp_discovery.py -q
```

### Step 33 完成记录（2026-08-17）

```text
修改文件:
  src/tools/__init__.py
  src/tools/mcp/__init__.py
  src/tools/mcp/protocol.py
  src/tools/mcp/stdio_client.py
  tests/fixtures/fake_mcp_server.py
  tests/test_mcp_discovery.py
  src/tools/Tools层开发步骤与进度(4)-MCP扩展工具.md

实现摘要:
  1. 新增 MCPInitializeResult / MCPToolDiscoveryResult，以及 MCP wire
     protocol 版本、clientInfo、description 长度限制等发现阶段常量。
  2. MCPStdioClient 增加 initialize()：发送 initialize request，校验
     protocolVersion、capabilities object 和 tools capability，成功后发送
     notifications/initialized，并把连接状态推进到 ready。
  3. MCPStdioClient 增加 list_tools() / refresh_tools()：执行 tools/list，
     解析 remote tools，应用 allowed_tools 白名单，校验 name、description
     长度、inputSchema object schema、required array。
  4. 无效 remote tool 不进入 discovered tools，记录 skipped_tools；有效工具
     允许部分成功，connection_info.metadata 记录 tool_count、raw_tool_count、
     skipped_tools 和 schema_hashes。
  5. refresh_tools() 显式刷新 tools/list，比较 schema hash，记录 added_tools、
     removed_tools、changed_tools；不在每次调用前自动 list。
  6. fake_mcp_server.py 支持 initialize、notifications/initialized、
     tools/list、初始化异常、tools/list 异常、无效 schema、allowed_tools
     过滤和 refresh schema 变化测试。

测试命令:
  python -B -m unittest tests.test_mcp_discovery
  python -B -m unittest tests.test_mcp_stdio_client tests.test_mcp_discovery tests.test_mcp_protocol_v1 tests.test_mcp_config_v1
  python -B -m compileall -q src\tools tests\test_mcp_discovery.py tests\fixtures\fake_mcp_server.py
  python -B -m unittest tests.test_tool_config_v1 tests.test_tools_current_baseline tests.test_tool_manager_v1
  python -B -m unittest discover -s tests -p 'test_*.py'

测试结果:
  tests.test_mcp_discovery: 7 tests OK
  MCP config/protocol/stdio/discovery: 31 tests OK
  compileall: OK
  Tools config/baseline/manager: 23 tests OK
  全量 unittest discover: Ran 689 tests, OK (skipped=3)

边界:
  1. Step 33 只完成 initialize / initialized / tools/list 和 remote tool
     基础校验，不把 MCP 工具注册进 ToolRegistry。
  2. 不实现 MCP Resources / Prompts / Sampling，不支持 Streamable HTTP 真实连接。
  3. 不根据远程 description 决定低风险或权限；风险和确认仍留给后续本地
     ToolSpec / ToolPolicy。
  4. 不把握手原始响应、command、args、env、credential 明文暴露给模型或
     用户事件；connection_info 只保留结构化摘要和 schema hash。

遗留:
  Step 34 将 MCPRemoteTool 转换为动态 ToolSpec，处理 mcp.<server_id>.<tool_name>
  命名、metadata、风险推断和 Registry 动态注册/移除。
```

---

## Step 34：MCPToolAdapter 与动态 ToolSpec

**状态：已完成**

### 目标

把发现的每个远程工具转换为独立本地 ToolSpec，模型能够看到准确名称、描述和参数 schema。

### 命名

```text
remote: query
server_id: mysql
local: mcp.mysql.query
```

名称冲突或远程名称含非法字符时必须采用稳定编码/拒绝策略，不能产生两个相同 canonical name。

### metadata

```text
source_type=mcp
server_id
remote_tool_name
transport=stdio
remote_schema_hash
```

不得包含：

```text
command
args
env
token
pid
credential_ref 明文值
```

### 风险推断

先由本地规则产生建议，再由用户配置覆盖：

```text
read/get/list/search/query -> low/medium
create/update/write/send/post/publish -> high
delete/remove/drop/pay/transfer/execute/shell -> high/blocked
```

未知默认为 medium，始终受 `allow_mcp` 控制。远程描述不能降低风险。

### 注册

```text
Registry.register(spec, source="mcp:<server_id>")
```

server 禁用/停止/刷新时按 source 精确移除。

### 明确不做

```text
不以一个模糊 mcp_execute 替代独立工具。
不向模型展示内部 mcp_execute 调试入口。
不由远程 server 决定 requires_confirmation。
```

### 测试与验收

```text
命名和 metadata。
schema 转换。
风险关键词/配置覆盖。
动态注册/移除。
模型 specs 不泄密。
冲突处理。
```

```powershell
python -m pytest tests/test_mcp_tool_adapter.py tests/test_tool_registry_dynamic.py -q
```

### Step 34 完成记录（2026-08-17）

```text
修改文件:
  src/tools/__init__.py
  src/tools/mcp/__init__.py
  src/tools/mcp/adapter.py
  tests/test_mcp_tool_adapter.py
  src/tools/Tools层开发步骤与进度(4)-MCP扩展工具.md

实现摘要:
  1. 新增 MCPToolAdapterResult 和 MCPToolAdapter 函数族，把 MCPRemoteTool
     转换为本项目统一 ToolSpec。
  2. 固定 MCP 工具本地命名：mcp.<server_id>.<tool_name_segment>。
     远程工具名包含非法字符时使用稳定归一化片段和 hash 后缀，避免
     search-repositories 与 search_repositories 这类名称冲突。
  3. ToolSpec.metadata 只保留 source_type、source、server_id、
     remote_tool_name、transport、remote_schema_hash、annotations 摘要等
     可审计信息，不包含 command、args、env、token、pid、credential 明文。
  4. 远程 inputSchema 转为 parameters_schema，required 转为 required_params；
     returns_schema 固定为 object，workspace_scope 固定为 mcp，category 固定为 mcp。
  5. 风险先由本地关键词推断：read/get/list/search/query -> low，
     create/update/write/send/post/publish/delete/remove/execute -> high，
     drop/pay/transfer/shell -> blocked；再允许用户 tool_policies 覆盖风险、
     requires_confirmation 和 timeout_seconds。
  6. 新增 register_mcp_tool_specs / remove_mcp_tool_specs，按 source=mcp:<server_id>
     精确动态注册、刷新替换和移除；注册前检查与现有 registry 的冲突，失败时
     不移除旧 source。

测试命令:
  python -B -m unittest tests.test_mcp_tool_adapter tests.test_tool_registry_dynamic
  python -B -m unittest tests.test_mcp_config_v1 tests.test_mcp_protocol_v1 tests.test_mcp_stdio_client tests.test_mcp_discovery tests.test_mcp_tool_adapter tests.test_tool_registry_dynamic
  python -B -m compileall -q src\tools tests\test_mcp_tool_adapter.py
  python -B -m unittest tests.test_tool_spec_v1 tests.test_tool_config_v1 tests.test_tools_current_baseline tests.test_tool_manager_v1
  python -B -m unittest discover -s tests -p 'test_*.py'

测试结果:
  MCP adapter + dynamic registry: 11 tests OK
  MCP config/protocol/stdio/discovery/adapter/registry: 42 tests OK
  compileall: OK
  ToolSpec/config/baseline/manager: 30 tests OK
  全量 unittest discover: Ran 696 tests, OK (skipped=3)

边界:
  1. Step 34 只完成 MCPRemoteTool -> ToolSpec 和 Registry 动态注册/移除，
     不执行 tools/call，不生成 ToolResult。
  2. 不暴露模糊 mcp_execute 给模型；每个 MCP 远程工具都是独立 ToolSpec。
  3. 远程 description 不能决定本地权限或确认策略；本地关键词与用户配置才是准绳。
  4. registry 冲突时抛出结构化可诊断错误，由后续 Manager/调用方处理，不静默覆盖
     既有工具。

遗留:
  Step 35 实现 MCPToolGateway 与 tools/call，把已注册 mcp.* ToolSpec 的真实调用
  统一归一化为 MCPToolData -> ToolResult，并接入 ToolManager 正式执行入口。
```

---

## Step 35：MCPToolGateway 与 tools/call

**状态：已完成**

### 目标

实现所有 `mcp.*` 工具共享的统一执行路径，并把远程响应归一化为 `MCPToolData -> ToolResult`。

### 建议新增

```text
src/tools/mcp/gateway.py
src/tools/mcp/manager.py
tests/test_mcp_tool_gateway.py
```

### 执行顺序

```text
ToolManager 收到 mcp.* request
  -> Registry 已完成参数校验
  -> ToolPolicy 已完成本地权限/确认
  -> Gateway 从 ToolSpec.metadata 取 server_id/remote_tool_name
  -> 检查 server ready/enabled
  -> dry_run 分支
  -> MCPClient tools/call
  -> 校验远程 result
  -> 归一化 MCPToolData
  -> ToolResult
```

### tools/call

远程只收到：

```json
{
  "name": "remote_tool_name",
  "arguments": {}
}
```

不传本地 trace、权限开关、credential 配置或完整 ActionPacket，除非远程工具 schema 明确有同名普通业务参数且已通过安全审查。

### 结果处理

```text
text -> content[]
structuredContent -> structured_content
resource links -> resource_links
isError -> is_error
```

远程 `isError=true`：

```text
ToolResult.success=false
code=mcp_remote_error
data 仍保留受控远程错误内容
```

### 输出限制

对 content、structured_content 和 stderr 分别限制，超限截断或 `mcp_output_too_large`；不把完整响应写日志。

### 明确不做

```text
不让 Gateway 自己决定改用 command_tool。
不自动重试有副作用的 tools/call。
不把 resource link 自动读取。
```

### 测试与验收

```text
server/tool 不存在或禁用。
tools/call 成功。
text/structured/resource link。
remote isError。
invalid result。
timeout。
输出超限。
trace 和日志字段。
```

```powershell
python -m pytest tests/test_mcp_tool_gateway.py -q
```

### Step 35 完成记录（2026-08-17）

```text
修改文件:
  src/tools/__init__.py
  src/tools/mcp/__init__.py
  src/tools/mcp/gateway.py
  src/tools/mcp/stdio_client.py
  src/tools/tool_manager.py
  tests/fixtures/fake_mcp_server.py
  tests/test_mcp_discovery.py
  tests/test_mcp_tool_gateway.py
  src/tools/Tools层开发步骤与进度(4)-MCP扩展工具.md

实现摘要:
  1. 新增 MCPToolGateway / MCPGatewayHandler，作为所有 mcp.* ToolSpec 的
     ToolRuntime handler，不新增第二套执行管线。
  2. ToolManager.get_tool() 对 source_type=mcp 的动态 ToolSpec 返回 Gateway
     绑定 handler；Registry 参数校验、ToolPolicy allow_mcp/确认、dry_run 仍由
     ToolRuntime 先执行。
  3. MCPStdioClient 新增 call_tool()，只向远程发送 tools/call 的 name 与
     arguments，不传 trace、权限、credential 配置或完整 ActionPacket。
  4. tools/call 结果归一化为 MCPCallResult，再转为 MCPToolData -> ToolResult；
     支持 content、structuredContent、resourceLinks/resource_link、isError。
  5. remote isError=true 映射为 ToolResult.success=false、
     code=mcp_remote_error，同时保留受控 MCPToolData。
  6. Gateway 检查 server client 是否存在、enabled、已 initialize + tools/list、
     以及 remote_tool_name 是否仍在 discovery_result 中；不自动 tools/list，
     不自动 fallback 到 command_tool。
  7. 输出限制通过 MCPCallResult.to_tool_data(max_content_chars=...) 与
     OutputController 双层处理，超限时标记 output_truncated / artifact refs，
     不把完整远程响应写入用户可见事件。

测试命令:
  python -B -m unittest tests.test_mcp_tool_gateway
  python -B -m unittest tests.test_mcp_config_v1 tests.test_mcp_protocol_v1 tests.test_mcp_stdio_client tests.test_mcp_discovery tests.test_mcp_tool_adapter tests.test_tool_registry_dynamic tests.test_mcp_tool_gateway
  python -B -m compileall -q src\tools tests\test_mcp_tool_gateway.py tests\fixtures\fake_mcp_server.py
  python -B -m unittest tests.test_tool_manager_v1 tests.test_tool_config_v1 tests.test_tools_current_baseline tests.test_tool_spec_v1 tests.test_tool_policy_v1 tests.test_react_executor_tool_action
  python -B -m unittest discover -s tests -p 'test_*.py'

测试结果:
  MCP tool gateway: 7 tests OK
  MCP config/protocol/stdio/discovery/adapter/registry/gateway: 49 tests OK
  compileall: OK
  ToolManager/config/baseline/spec/policy/ReActExecutor tool action: 53 tests OK
  全量 unittest discover: Ran 703 tests, OK (skipped=3)

边界:
  1. Step 35 只实现真实 tools/call 和结果归一化；不实现 MCP Policy 的专用
     confirmation preview、日志审计细节或 fallback 策略。
  2. Gateway 不读取 resource link，不把 resource link 自动转成 read_file /
     document_parser 调用。
  3. Gateway 不自动刷新 tools/list；server refresh 仍由 Manager/后续步骤显式触发。
  4. Gateway 不自动重试有副作用的 tools/call；失败 ToolResult 交给
     ReActExecutor/Checker 后续判断。

遗留:
  Step 36 实现 MCP Policy、dry_run preview、tools.log 审计字段与 fallback 边界
  的正式化，补充高风险确认和 blocked 语义测试。
```

---

## Step 36：MCP Policy、dry_run、日志与 fallback 边界

**状态：已完成**

### 目标

完成 MCP 的本地安全裁决、确认 preview、审计和失败后职责边界。

### 权限

```text
allow_mcp=false:
  拒绝所有 MCP 工具。

high:
  dry_run preview -> confirmation -> resume。

blocked:
  直接拒绝，确认无效。
```

### MCP dry_run

只做本地预检查并返回：

```text
server_id
remote_tool_name
arguments_summary
risk_level
requires_confirmation
transport
timeout
```

必须明确标记：

```text
remote_simulation_performed=false
```

不能声称远程副作用已模拟。

### 日志

记录：

```text
server_id
remote_tool_name
transport
command_summary（只显示可执行名，不含 secret）
args_count/argument_keys
duration
result code
stderr_preview
output_summary
```

### fallback

```text
MCP 失败
  -> ToolResult
  -> ReActExecutor 生成 Observation
  -> Checker/模型决定 fallback_to_tool
  -> 新 ActionPacket 调用 command_tool/shell_command_tool
```

Tools 层不得直接执行上述 fallback。

### 明确不做

```text
不允许用户确认放行 blocked 远程行为。
不认为 MCP Server 本身可信。
不把 MCP 错误转换成命令字符串。
```

### 测试与验收

```text
allow_mcp。
high confirmation。
blocked。
dry_run 不调用 remote。
日志脱敏。
失败后没有 Tools 内部 command 调用。
```

```powershell
python -m pytest tests/test_mcp_policy.py tests/test_mcp_tool_gateway.py tests/test_tool_logging_v1.py -q
```

### Step 36 完成记录（2026-08-17）

```text
修改文件:
  src/tools/mcp/adapter.py
  src/tools/mcp/gateway.py
  src/tools/output_control.py
  src/tools/tool_logger.py
  tests/test_mcp_policy.py
  tests/test_mcp_tool_adapter.py
  src/tools/Tools层开发步骤与进度(4)-MCP扩展工具.md

实现摘要:
  1. MCP ToolSpec 现在声明 supports_dry_run=True，并通过 metadata.preview_kind=mcp
     进入统一 OutputController preview 流程。
  2. OutputController 为 MCP dry_run 生成专用预览：server_id、remote_tool_name、
     arguments_summary、risk_level、requires_confirmation、transport、timeout，
     并明确 remote_simulation_performed=false、dry_run_scope=local_precheck_only。
  3. MCP high 风险确认沿用 ToolPolicy：未确认返回 confirmation_required；
     dry_run 可生成 preview_hash；确认票据必须绑定 preview_hash；blocked 风险
     不允许通过确认放行。
  4. MCPToolGateway 在成功和失败路径都写入 fallback_performed=false，明确 Tools
     层不会把 MCP 失败自动转换为 command_tool / shell_command_tool。
  5. Gateway metadata 增加安全审计摘要：server_id、remote_tool_name、transport、
     command_summary、argument_keys、schema_hash、stderr_preview、output_truncated。
  6. JsonlToolLogger 将 MCP 审计摘要写入 records[].metadata.mcp，只记录可执行名、
     args_count、argument_keys、stderr hash/长度等安全摘要，不记录 env key/value、
     credential、完整 stderr 或完整远程响应。

测试命令:
  python -B -m unittest tests.test_mcp_policy
  python -B -m unittest tests.test_mcp_policy tests.test_mcp_tool_gateway tests.test_tool_logging_v1
  python -B -m unittest tests.test_mcp_config_v1 tests.test_mcp_protocol_v1 tests.test_mcp_stdio_client tests.test_mcp_discovery tests.test_mcp_tool_adapter tests.test_tool_registry_dynamic tests.test_mcp_tool_gateway tests.test_mcp_policy
  python -B -m compileall -q src\tools tests\test_mcp_policy.py tests\test_mcp_tool_adapter.py
  python -B -m unittest tests.test_mcp_tool_adapter tests.test_tool_policy_v1 tests.test_tool_preview_v1 tests.test_tool_logging_v1 tests.test_tool_manager_v1 tests.test_react_executor_tool_action
  python -B -m unittest discover -s tests -p 'test_*.py'

测试结果:
  MCP policy: 6 tests OK
  MCP policy/gateway/logging: 16 tests OK
  MCP config/protocol/stdio/discovery/adapter/registry/gateway/policy: 55 tests OK
  compileall: OK
  Adapter/policy/preview/logging/manager/ReActExecutor tool action: 50 tests OK
  全量 unittest discover: Ran 709 tests, OK (skipped=3)

边界:
  1. MCP dry_run 只表示本地参数、权限、风险和影响摘要预检查，不调用远程
     tools/call，也不保证远程副作用已模拟。
  2. blocked MCP 工具不会因为 confirmed=true、confirmation_id 或 preview_hash
     被放行。
  3. Tools 层仍不做 MCP 失败后的 command fallback；失败 ToolResult 交给
     ReActExecutor Observation 和 Checker/模型后续决策。
  4. 日志只写安全摘要，用户可见 ExecutionEvent 与 logs/tools.log 继续保持分离。

遗留:
  Step 37 做 fake MCP 全链路验收和可选真实 STDIO 集成测试，覆盖加载配置、
  启动、发现、动态注册、Gateway 调用、停止/移除以及真实测试默认 skip。
```

---

## Step 37：fake MCP 全链路与可选真实 STDIO 测试

**状态：已完成**

### 目标

用仓库内 fake MCP Server 完成可重复全链路验收，并为真实本地 Server 提供显式开启的集成测试。

### fake 全链路

```text
加载配置
  -> 启动 fake STDIO server
  -> initialize
  -> tools/list
  -> 动态 ToolSpec
  -> ToolManager execute
  -> tools/call
  -> ToolResult
  -> stop/remove specs
```

场景：

```text
成功工具
参数校验
远程错误
timeout
进程退出
schema 变化 refresh
high risk confirmation
blocked
dry_run
日志脱敏
```

### 真实测试

建议：

```text
tests/integration/test_mcp_stdio_real.py
```

默认跳过，启用条件：

```text
RUN_TOOL_INTEGRATION_TESTS=true
RUN_MCP_INTEGRATION_TESTS=true
测试配置显式指定允许的本地 MCP Server
```

不得在 CI 或普通 `pytest` 中自动运行 `npx -y` 安装未知包。

### 验收命令

```powershell
python -m pytest tests/test_mcp_config_v1.py tests/test_mcp_protocol_v1.py tests/test_mcp_stdio_client.py tests/test_mcp_discovery.py tests/test_mcp_tool_adapter.py tests/test_mcp_tool_gateway.py tests/test_mcp_policy.py -q
```

### 分卷完成标准

```text
MCP V1 本地 STDIO 可真实调用。
模型看到独立 mcp.<server>.<tool>。
所有调用经过 Registry/Policy/ToolResult/log。
配置和日志不泄露 secret。
HTTP、Resources、Prompts、Sampling 仍明确后置。
```

### Step 37 完成记录（2026-08-17）

```text
修改文件:
  src/tools/__init__.py
  src/tools/mcp/__init__.py
  src/tools/mcp/manager.py
  tests/fixtures/fake_mcp_server.py
  tests/test_mcp_discovery.py
  tests/test_mcp_v1_acceptance.py
  tests/integration/test_mcp_stdio_real.py
  src/tools/Tools层开发步骤与进度(4)-MCP扩展工具.md

实现摘要:
  1. 新增 MCPManager / MCPManagedServer，作为显式生命周期编排器：
     start_server -> STDIO client initialize/tools/list -> MCPToolAdapter ->
     Registry 动态注册 -> Gateway client 绑定；stop_server 精确移除 specs
     和 client；refresh_server 显式刷新 schema。
  2. MCPManager 不自动替代 ReActExecutor，不在 ToolManager 初始化时偷偷启动
     用户配置里的外部 command，也不自动安装或下载 MCP Server。
  3. fake_mcp_server.py 新增 exit_now MCP tool，覆盖 tools/call 过程中
     子进程意外退出的结构化失败场景。
  4. 新增 tests/test_mcp_v1_acceptance.py，从 mcp_servers.json 配置文件加载
     fake server，完成配置加载、启动、发现、动态 ToolSpec、模型可见 specs、
     ToolManager execute、tools/call、ToolResult、refresh、stop/remove 全链路。
  5. fake acceptance 覆盖成功工具、参数校验、远程错误、timeout、进程退出、
     schema 变化 refresh、high risk confirmation、blocked、dry_run、日志脱敏。
  6. 新增 tests/integration/test_mcp_stdio_real.py，真实 STDIO 集成测试默认 skip，
     只有显式设置 RUN_TOOL_INTEGRATION_TESTS=true、RUN_MCP_INTEGRATION_TESTS=true
     和 MCP_REAL_STDIO_COMMAND 时才运行；不自动执行 npx -y 或安装未知包。

测试命令:
  python -B -m unittest tests.test_mcp_v1_acceptance tests.integration.test_mcp_stdio_real
  python -B -m unittest tests.test_mcp_config_v1 tests.test_mcp_protocol_v1 tests.test_mcp_stdio_client tests.test_mcp_discovery tests.test_mcp_tool_adapter tests.test_tool_registry_dynamic tests.test_mcp_tool_gateway tests.test_mcp_policy tests.test_mcp_v1_acceptance tests.integration.test_mcp_stdio_real
  python -B -m compileall -q src\tools tests\test_mcp_v1_acceptance.py tests\integration\test_mcp_stdio_real.py tests\fixtures\fake_mcp_server.py
  python -B -m unittest tests.test_tool_manager_v1 tests.test_tool_config_v1 tests.test_tools_current_baseline tests.test_tool_policy_v1 tests.test_tool_logging_v1 tests.test_react_executor_tool_action
  python -B -m unittest discover -s tests -p 'test_*.py'

测试结果:
  MCP fake acceptance + real integration entry: 3 tests OK (skipped=1)
  MCP config/protocol/stdio/discovery/adapter/registry/gateway/policy/acceptance: 58 tests OK (skipped=1)
  compileall: OK
  ToolManager/config/baseline/policy/logging/ReActExecutor tool action: 49 tests OK
  全量 unittest discover: Ran 712 tests, OK (skipped=4)

边界:
  1. MCP V1 本地 STDIO 路径已覆盖真实 fake 调用；Streamable HTTP 仍只保留配置，
     不真实连接。
  2. Resources、Prompts、Sampling、OAuth/API key 凭证管理、插件市场、一键安装
     常用 MCP 均后置。
  3. 真实 STDIO 集成测试默认跳过，必须由用户显式提供本地 server command 和 args。
  4. Tools 层不做 MCP 失败后的 command fallback；仍由 ReActExecutor/Checker
     根据真实 ToolResult 决定后续 ActionPacket。

遗留:
  MCP 扩展工具分卷 Step 30-37 已完成。下一分卷进入
  Tools层开发步骤与进度(5)-集成验收。
```
