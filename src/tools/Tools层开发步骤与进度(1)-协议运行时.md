# Tools 层开发步骤与进度（1）- 协议运行时

> 覆盖步骤：Step 0-9  
> 当前状态：Step 0-9 已完成，协议运行时分卷完成
> 上位设计：`Tools层设计决策汇总(1)-总纲与跨层边界.md`、`Tools层设计决策汇总(2)-协议与运行时.md`

本分卷先建立所有工具共同依赖的协议和运行时。没有完成本分卷前，不应把新文件工具、联网搜索或 MCP 接入 ReActExecutor 正式路径。

---

## Step 0：设计基线、现状快照与迁移保护

**状态：已完成**

### 目标

固定实现前的真实代码状态、兼容入口和不可破坏行为，建立 Tools V1 的测试基线。此 Step 不增加新工具能力。

### 前置条件

```text
Tools 两轮问答已经完成。
Tools 设计决策汇总 0-6 已经完成。
当前工作区现有变更已被识别，不覆盖用户未提交修改。
```

### 涉及文件

```text
现有:
  src/tools/base.py
  src/tools/registry.py
  src/tools/tool_manager.py
  src/tools/command_tool.py
  src/agent/react_executor.py
  tests/test_tool_registry_v1.py
  tests/test_command_tool_v1.py
  tests/test_react_executor_tool_action.py
  tests/test_react_executor_command_action.py

可新增:
  tests/test_tools_current_baseline.py
```

### 具体实施

1. 记录当前 `ToolResult` 字段、`ToolManager.run_tool` 签名、`ToolSpec` 字段和默认注册工具。
2. 记录 ReActExecutor 当前工具调用位置、参数合并方式和 `ToolResult` 兼容包装逻辑。
3. 固定以下迁移约束：

```text
ActionPacket.action_target -> ToolCallRequest.tool_name
ActionPacket.action_args -> 经过 ReActExecutor 清理后的 ToolCallRequest.args
Runtime / plan / session -> ToolCallContext
执行器确认状态和会话权限 -> ToolCallOptions
```

4. 记录当前 `COMMAND_TOOL_NAMES={"command_tool","shell_tool"}` 与正式名 `shell_command_tool` 的差异。后续采用显式 alias 或分阶段迁移，不能直接删除 `shell_tool` 导致现有测试或提示词断链。
5. 记录当前 `confirmed` 可能从 `action_args` 传播的实现风险。后续正式协议必须只信任 ReActExecutor/Runtime 恢复流程注入的确认状态，不能信任模型原始字段。
6. 运行现有相关测试并保存通过数或失败清单，区分“原有失败”和“本轮新增回归”。

### 明确不做

```text
不修改 Analyzer / Planner 的输出协议。
不删除旧顺序 Executor。
不把旧顺序 Executor纳入 Tools V1 验收。
不在本 Step 扩展 ToolResult。
不新增 config/tools。
不实现任何新工具。
```

### 测试与验收

至少运行：

```powershell
python -m pytest tests/test_tool_registry_v1.py tests/test_command_tool_v1.py tests/test_react_executor_tool_action.py tests/test_react_executor_command_action.py -q
```

完成标准：

```text
现状与迁移风险已记录。
现有测试基线可重复。
后续 Step 能区分兼容要求和待替换早期行为。
```

### 完成后回写

在本 Step 下补充实际测试结果、当前失败清单和确认采用的 `shell_tool` 迁移方式。

### Step 0 完成记录（2026-08-14）

#### 修改文件

```text
新增:
  tests/test_tools_current_baseline.py

更新:
  src/tools/Tools层开发步骤与进度(1)-协议运行时.md
```

#### 当前代码快照

`ToolResult` 当前字段固定为：

```text
success
data
message
error
code
```

当前工厂与序列化行为：

```text
ToolResult.ok(data=123) -> success=True, message="123"
ToolResult.fail("boom", code="tool_failed") -> success=False, error="boom", message="boom"
ToolResult.to_dict() 只输出当前 5 个字段
ToolResult.to_text() 成功时优先 message，失败时优先 error
```

`ToolManager.run_tool` 当前兼容入口签名为：

```python
run_tool(self, tool_name: str, **kwargs) -> ToolResult
```

当前默认 `ToolManager.tools` 与 `list_tools()` 顺序一致：

```text
document_parser
text_processor
math_calculator
translator
time_query
search_tool
code_executor
file_writer
command_tool
```

当前 `run_tool` 行为：

```text
按 tool_name 从 self.tools 取实例。
工具不存在 -> ToolResult(success=False, error="Tool not found: ...")
工具没有 run -> ToolResult(success=False, error="Tool has no run method: ...")
工具返回 ToolResult -> 原样返回。
工具返回裸数据 -> 包装为 ToolResult(success=True, data=data, message=str(data))
工具抛异常 -> ToolResult(success=False, error="Tool <name> failed: <exc>")
```

`ToolSpec` 当前字段固定为：

```text
name
description
parameters_schema
required_params
returns_schema
risk_level
requires_confirmation
workspace_scope
timeout
fallback_tools
category
required_any_of
metadata
```

当前 `ToolSpec.to_model_spec()` 输出 `timeout`，尚未迁移到 `timeout_seconds`。

当前默认 Registry：

```text
build_default_tool_registry() 包含 search_tool，不包含 web_search，不包含 command_tool。
build_default_tool_registry(include_command_tool=True) 才包含 command_tool。
当前 search_tool -> web_search 只记录为后续迁移目标，本 Step 不添加 alias。
```

#### ReActExecutor 调用边界快照

当前工具调用仍由 ReActExecutor 分派到旧兼容入口：

```text
ActionPacket.action_target -> tool_name
PlanStep.args 与 ActionPacket.action_args 合并，ActionPacket.action_args 覆盖 PlanStep.args
过滤 input_from / output_key / fallback_reason 等执行器控制字段
按 ToolRegistry.validate_tool_args() 做基础参数校验
input_from 由 ObservationStore 解析后注入目标工具参数
self.tool_manager.run_tool(tool_name, **input_args)
_coerce_tool_result() 把非 ToolResult 裸结果包装为 ToolResult.ok(...)
ObservationPacket 由 ReActExecutor 基于真实 ToolResult 生成
```

固定后续迁移约束：

```text
ActionPacket.action_target -> ToolCallRequest.tool_name
清理后的 ActionPacket.action_args / PlanStep.args -> ToolCallRequest.args
Runtime / plan / session / step / packet -> ToolCallContext
执行器确认状态与会话权限 -> ToolCallOptions
```

当前命令工具名差异：

```text
COMMAND_TOOL_NAMES={"command_tool","shell_tool"}
正式目标名为 shell_command_tool。
后续采用显式 alias 或分阶段迁移；不能直接删除 shell_tool，避免现有测试、提示词和历史 ActionPacket 断链。
```

当前确认风险：

```text
正常确认恢复路径由 ReActExecutor 注入 confirmed=True。
retry / fallback 路径中仍存在 bool(packet.action_args.get("confirmed", False)) 传播风险。
后续正式协议必须只信任 ReActExecutor / ToolRuntime 恢复流程注入的 confirmation_id、preview_hash 和 confirmed 状态，不能信任模型原始 action_args.confirmed。
```

#### 本 Step 发现的现状风险

```text
ToolManager 顶层导入 search_tool，而 search_tool 顶层依赖 requests。
当前测试环境没有 requests 时，直接 import ToolManager 会失败。
本 Step 未修改生产代码；新增快照测试用最小导入占位记录该现状，后续依赖治理或 web_search provider 正式化时处理。
```

#### 测试命令与结果

```powershell
python -B -m unittest tests.test_tools_current_baseline tests.test_tool_registry_v1 tests.test_command_tool_v1 tests.test_react_executor_tool_action tests.test_react_executor_command_action
```

最终结果：

```text
Ran 38 tests in 0.413s
OK
```

当前失败清单：

```text
无。
```

---

## Step 1：ToolResult V1 与工具 data schema 底座

**状态：已完成**

### 目标

把所有正式工具的统一返回协议扩展为可追踪、可分类、可审计的 `ToolResult V1`，同时允许不同工具使用不同 `data` schema。

### 为什么此时做

Registry、Policy、日志、Observation、搜索和 MCP 都依赖稳定的结果字段。先实现具体工具再改结果协议会造成重复迁移。

### 涉及文件

```text
修改:
  src/tools/base.py
  src/tools/__init__.py

新增:
  src/tools/data_types.py
  tests/test_tool_result_v1.py
```

文件归属可以根据现有代码风格微调，但必须避免 `base.py` 无限堆积所有具体工具数据类型。

### 必做字段

```text
success
tool_name
tool_category
tool_namespace
data
message
error
code
error_type
retryable
provider
started_at
ended_at
duration_ms
trace_id
execution_id
step_id
call_id
raw_output
raw_output_truncated
metadata
```

### 实施细节

1. 保留 `ToolResult.ok()`、`ToolResult.fail()`、`to_dict()`、`to_text()`，更新为 V1 字段。
2. `fail()` 必须强制 `success=False`，错误文本进入 `error`，不能用成功 `message` 掩盖失败。
3. `to_text()` 只生成短摘要，不自动串出完整 `raw_output`、完整文件内容或完整 stdout/stderr。
4. `to_dict()` 必须稳定序列化嵌套 dataclass / enum / list / dict；不能把不可 JSON 序列化对象直接泄露给日志或事件。
5. `call_id` 缺失时由运行时生成，不要求每个工具手工生成。
6. `duration_ms`、时间戳和 trace 字段由 ToolManager 统一补齐；工具可提供但不能覆盖可信上下文。
7. 定义具体数据对象或 TypedDict/dataclass 的基础组织方式，至少预留：

```text
FileReadData
FileWriteData
FilePatchData
FileDeleteData
CommandExecutionData
DocumentParseData
WebSearchData
MCPToolData
```

8. 数据对象只规定程序消费字段，不把用户自然语言最终回答塞入 `data`。
9. 保留对现有构造形式 `ToolResult(success=..., data=..., message=..., error=..., code=...)` 的迁移兼容，避免一次性打断现有测试；兼容期结束时间在 Step 44 决定。

### raw_output 边界

```text
小输出:
  可保留 raw_output，但不默认进入日志和 Observation。

大输出:
  截断 raw_output。
  raw_output_truncated=true。
  metadata 记录长度、hash、可选 artifact_ref。

敏感输出:
  即使很小也可以不保存 raw_output。
```

### 明确不做

```text
不在本 Step 决定 Observation 内容。
不调用模型生成 message 或摘要。
不定义每个具体工具的全部业务字段。
不实现 artifact 持久化服务，只预留引用字段。
```

### 测试

```text
成功和失败工厂字段正确。
失败不能伪装成成功。
旧构造形式仍可工作。
to_dict 可 JSON 序列化。
to_text 不泄露 raw_output。
metadata 使用独立默认 dict，无共享可变对象。
trace / duration 可由运行时后补。
不同工具 data schema 可以不同。
```

验收命令：

```powershell
python -m pytest tests/test_tool_result_v1.py tests/test_react_executor_tool_action.py -q
```

完成标准：

```text
现有工具返回值可被归一化为 ToolResult V1。
所有后续 Step 有稳定结果外壳可依赖。
现有 ReActExecutor 工具测试没有因字段扩展失效。
```

### Step 1 完成记录（2026-08-14）

#### 修改文件

```text
更新:
  src/tools/base.py
  src/tools/__init__.py
  tests/test_tools_current_baseline.py

新增:
  src/tools/data_types.py
  tests/test_tool_result_v1.py
```

#### ToolResult V1 实现

```text
保留旧字段及其位置参数顺序：
  success
  data
  message
  error
  code

新增字段：
  tool_name
  tool_category
  tool_namespace
  error_type
  retryable
  provider
  started_at
  ended_at
  duration_ms
  trace_id
  execution_id
  step_id
  call_id
  raw_output
  raw_output_truncated
  metadata
```

`ToolResult.ok()` 和 `ToolResult.fail()` 仍保留。`fail()` 会强制返回
`success=False`，并把错误文本写入 `error` 与短摘要 `message`，不会被调用方
传入的 `success=True` 或自定义 `message` 伪装成成功。

`call_id` 当前提供兼容性默认生成器；可信的 call identity、trace、时间戳和耗时
仍由 Step 7 的 ToolRuntime 统一注入和覆盖。`metadata` 使用独立默认字典，避免
实例之间共享可变状态。

#### data schema 底座

`src/tools/data_types.py` 建立了以下可序列化 dataclass：

```text
FileReadData
FileWriteData
FilePatchData
FileDeleteData
CommandExecutionData
DocumentParseData
WebSearchResult
WebSearchData
MCPToolData
```

`WebSearchData.results` 会把字典结果归一化为 `WebSearchResult`；所有 schema
都使用独立的 list/dict 默认值。数据对象只描述程序消费字段，不承载用户最终
自然语言回答。

#### 序列化与可见性边界

`ToolResult.to_dict()` 递归处理 dataclass、Enum、Path、bytes、异常、集合和嵌套
容器，结果可直接交给 `json.dumps()`。`to_text()` 只读取 message、error 或
data 的短摘要，不读取 `raw_output`，因此本 Step 没有把完整文件内容、stdout、
stderr 或网页正文自动放入摘要。

Step 0 中“ToolResult 恰好只有 5 个字段”的快照断言已改为旧字段顺序和旧核心
行为兼容断言；这是有意的协议扩展，不是回归。ReActExecutor 的
`_coerce_tool_result()` 和现有工具调用主链路无需修改，仍可按旧字段工作。

#### 测试命令与结果

```powershell
python -B -m unittest tests.test_tool_result_v1 tests.test_tools_current_baseline tests.test_react_executor_tool_action tests.test_react_executor_command_action
python -B -m unittest tests.test_tool_registry_v1 tests.test_command_tool_v1
```

结果：

```text
第一组：Ran 30 tests in 0.370s，OK
第二组：Ran 15 tests in 0.003s，OK
```

#### 本 Step 边界与遗留问题

```text
未实现 ToolCallRequest、ToolCallContext、ToolCallOptions。
未实现 ToolRuntime、ToolPolicy、统一错误码、截断执行和 artifact 服务。
未把 raw_output 自动写入 Observation 或 logs/tools.log。
未迁移旧 command_tool/search_tool 的业务返回结构。
未新增 src/tools/protocol.py；输入协议留给 Step 2。
```

---

## Step 2：ToolCallRequest、ToolCallContext 与 ToolCallOptions

**状态：已完成**

### 目标

建立 ToolManager 的正式输入协议，分离工具参数、可信运行上下文和本次调用策略。

### 涉及文件

```text
修改或新增:
  src/tools/protocol.py
  src/tools/base.py
  tests/test_tool_call_protocol.py
```

### 正式结构

```python
ToolCallRequest(
    tool_name: str,
    args: dict,
    context: ToolCallContext,
    options: ToolCallOptions,
)
```

`ToolCallContext` 至少包含：

```text
trace_id
execution_id
plan_id
task_id
step_id
packet_id
session_id
user_id
workspace_root
source
initiated_by
```

`ToolCallOptions` 至少包含：

```text
timeout_seconds
dry_run
require_confirmation
confirmed
approval_scope
confirmation_id
preview_hash
approved_at
approval_source
allow_read_workspace
allow_write_workspace
allow_network
allow_command
allow_shell_command
allow_mcp
max_output_chars
max_raw_output_chars
max_observation_chars
observation_mode
```

### 信任边界

必须编码或清晰封装以下规则：

```text
args:
  来自 ActionPacket 和 PlanStep，但要由 ReActExecutor 清理控制字段。

context:
  由 ReActExecutor / Runtime / 测试构造，模型不得提供可信值。

options.confirmed:
  只来自真实用户确认后的 resume 状态，且必须和 confirmation_id / preview_hash 一起校验。

confirmation_id / preview_hash:
  由 Runtime 在确认票据恢复时注入，模型不得提供可信值。

workspace_root:
  来自项目设置或 Runtime，不能采用模型传入绝对路径作为权限根。

allow_*:
  来自会话权限、配置和执行器状态，模型请求只能作为建议，不能提权。
```

### 兼容策略

`ToolManager.run_tool(tool_name, **kwargs)` 后续仍可接收旧调用，但必须由 ToolManager 构造：

```text
source="internal" 或 "historical_executor"
最小 ToolCallContext
默认受限 ToolCallOptions
```

旧入口不能默认 `confirmed=True`，也不能默认开放写、网络、命令和 MCP 权限。

### 明确不做

```text
不在 Tools 中解析 ActionPacket 类。
不把 reason、input_from、output_key 等执行器控制字段全部透传给工具。
不实现用户确认 UI。
不让模型在 args 中设置 confirmed 后获得权限。
```

### 测试与验收

```text
args 非 object 被拒绝。
context 和 options 默认值保守。
source 枚举校验。
workspace_root 规范化。
confirmed 不从 args 自动提取。
to_dict 不泄露敏感上下文。
旧 run_tool 输入可以转换为 request。
```

```powershell
python -m pytest tests/test_tool_call_protocol.py -q
```

### Step 2 完成记录（2026-08-14）

#### 修改文件

```text
新增:
  src/tools/protocol.py
  tests/test_tool_call_protocol.py

更新:
  src/tools/__init__.py
```

#### 正式输入协议

实现了：

```text
ToolCallRequest
ToolCallContext
ToolCallOptions
ToolCallSource
```

`ToolCallRequest` 固定包含：

```text
tool_name
args
context
options
```

`args` 必须是 object/dict；request 会复制参数字典，避免调用方后续修改
原始 ActionPacket 或 PlanStep 参数。`tool_name` 会做非空校验和首尾空白规范化。

#### 信任边界实现

```text
ToolCallContext.source:
  只接受 react_executor、test、runtime_api、mcp_manager、internal、
  historical_executor。

ToolCallContext.workspace_root:
  统一 expanduser + resolve(strict=False)，保存为绝对路径。

ToolCallOptions.confirmed:
  只由 options 自身表达，不从 request.args 或模型字段自动提取。

ToolCallOptions.has_confirmation_ticket:
  只有 confirmed、confirmation_id、preview_hash 同时存在时才为 true；
  具体票据校验仍由后续 ToolPolicy / ToolRuntime 执行。

allow_*:
  默认 allow_read_workspace=true，其余写入、网络、命令、shell、MCP
  capability 均为 false。
```

`approval_scope` 当前只接受 `one_call`、`current_step`、`session`，
`observation_mode` 当前只接受 `minimal`、`standard`、`full`。这些是协议
层的结构校验，不代替后续 Policy 的风险裁决。

#### 迁移兼容

提供 `ToolCallRequest.from_legacy(tool_name, kwargs)`，将历史
`run_tool(tool_name, **kwargs)` 参数转换为：

```text
source="historical_executor"
confirmed=false
allow_write_workspace=false
allow_network=false
allow_command=false
allow_shell_command=false
allow_mcp=false
```

本步没有把 ToolManager 旧执行逻辑改造成第二套 request 执行器，也没有提前
实现 `ToolManager.execute()`、ToolRuntime、Registry/Policy 校验或真实 handler
调用；这些属于后续正式运行时步骤。

#### 安全序列化

`ToolCallRequest.to_dict()` 可交给 `json.dumps()`，并对参数中明显的
`api_key`、`token`、`authorization`、`password`、`secret`、`credential`
等字段进行 `<redacted>` 处理。该序列化用于协议快照和调试，不等同于后续
`tools.log` 的完整脱敏策略。

#### 测试命令与结果

```powershell
python -B -m unittest tests.test_tool_call_protocol
python -B -m unittest tests.test_tool_result_v1 tests.test_tools_current_baseline tests.test_tool_registry_v1 tests.test_command_tool_v1 tests.test_react_executor_tool_action tests.test_react_executor_command_action
python -B -m compileall -q src/tools tests/test_tool_call_protocol.py
```

结果：

```text
协议测试：Ran 8 tests in 0.003s，OK
回归测试：Ran 45 tests in 0.374s，OK
compileall：通过
```

#### 本 Step 边界与遗留问题

```text
未修改 Analyzer、Planner、ReActExecutor 主链路。
未把 ReActExecutor 直接改为构造并执行 ToolCallRequest。
未实现 ToolManager.execute() 或 ToolRuntime。
未实现错误码体系、ToolValidationResult、ToolPolicy 和权限裁决。
未实现真正的 confirmation_id/preview_hash 生成、过期和 hash 重算。
workspace_root 的可信来源仍由 Runtime/Session 提供，本协议只做规范化。
```

---

## Step 3：错误码、错误类型与 ToolValidationResult 正式化

**状态：已完成**

### 目标

统一验证、权限、确认、超时、provider、文件、命令和 MCP 错误的稳定机器码，避免各工具自由生成不可判断文本。

### 涉及文件

```text
修改:
  src/tools/registry.py
  src/tools/protocol.py

建议新增:
  src/tools/errors.py
  tests/test_tool_errors_v1.py
```

### 必做

1. 建立 `ToolErrorCode` 或等价常量集合，至少覆盖设计文档列出的错误码。
2. 建立错误类型：

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

3. 扩展 `ToolValidationResult`，至少能表达：

```text
success
tool_name
canonical_tool_name
code
errors
missing_params
unknown_params
normalized_args
```

4. 明确 retryable 默认表：

```text
可重试候选:
  provider_timeout
  provider_rate_limited
  mcp_connection_failed
  临时文件锁

不可重试:
  invalid_args
  permission_denied
  confirmation_required
  user_rejected
  blocked_by_policy
  file_not_found
  patch_ambiguous_match
```

5. 未知内部异常统一为 `internal_error`，但 tools.log 可以记录脱敏异常类别；用户事件不展示堆栈。

### 明确不做

```text
不在工具内部决定 ReActExecutor 的 retry 次数。
不把 fallback_to_model / fallback_to_tool 作为 ToolErrorCode。
不使用异常文本作为唯一机器判断依据。
```

### 测试与验收

```text
未知错误码能归一化。
每个错误码有稳定 error_type。
retryable 映射符合边界。
ValidationResult 能区分缺失、类型错误和未知参数。
```

```powershell
python -m pytest tests/test_tool_errors_v1.py tests/test_tool_registry_v1.py -q
```

---

### Step 3 完成记录（2026-08-14）

#### 修改文件

```text
新增:
  src/tools/errors.py
  tests/test_tool_errors_v1.py

更新:
  src/tools/registry.py
  src/tools/protocol.py
  src/tools/__init__.py
  src/tools/Tools层开发步骤与进度(1)-协议运行时.md
```

#### 实现结果

```text
ToolErrorCode:
  覆盖工具不存在/禁用、参数、权限、确认、超时、provider、网络、
  文件、命令和 MCP 设计错误码，并包含 provider_rate_limited、
  temporary_file_lock 两个可重试候选码。

ToolErrorType:
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

错误归一化:
  未知错误码 -> internal_error
  error_type -> internal
  retryable -> false

ToolValidationResult:
  保留 success/tool_name/errors/missing_params 旧字段和旧文本。
  新增 canonical_tool_name/code/unknown_params/normalized_args/
  error_type/retryable。
  to_dict() 使用 JSON-safe 序列化。
```

Registry 参数校验行为：

```text
非 object 参数 -> invalid_args，保留 "tool args must be object"。
缺失 required 参数或 required_any_of -> missing_required_param。
基础 JSON 类型不匹配 -> invalid_args。
未知参数 -> 记录 unknown_params；默认兼容放行。
additionalProperties=false -> unknown 参数导致 invalid_args。
未知工具 -> tool_not_found，保留 "tool not found: <name>"。
normalized_args -> 输入字典的浅拷贝，不与调用方共享顶层可变状态。
```

#### 测试命令与结果

```powershell
python -B -m unittest tests.test_tool_errors_v1 tests.test_tool_registry_v1
# Ran 21 tests - OK

python -B -m unittest tests.test_tool_result_v1 tests.test_tools_current_baseline tests.test_command_tool_v1 tests.test_react_executor_tool_action tests.test_react_executor_command_action
# Ran 32 tests - OK

python -B -m unittest tests.test_tool_call_protocol tests.test_tool_registry_v1 tests.test_tool_errors_v1
# Ran 29 tests - OK

python -B -m unittest tests.test_react_executor_protocol tests.test_react_executor_actions tests.test_react_executor_confirmation tests.test_react_executor_safety
# Ran 35 tests - OK

python -B -m compileall -q src/tools tests/test_tool_errors_v1.py
# 通过
```

#### 兼容策略与边界

```text
未修改 Analyzer、Planner、ReActExecutor 主链路。
未实现 ToolPolicy、ToolRuntime、统一重试次数、日志落盘或异常捕获管线。
ToolErrorType/ToolErrorCode 通过 protocol 和 tools 包导出，供后续 Runtime、
Policy、Logger 复用；本 Step 不让错误枚举直接改变旧执行入口。
ToolResult 的运行时 identity、duration 和异常归一化仍由后续 ToolRuntime 统一注入。
provider、network、命令和 MCP 错误码本 Step 只建立协议映射，实际产生位置留给对应工具/Runtime。
```

---

## Step 4：ToolSpec 正式化

**状态：已完成**

### 目标

让 `ToolSpec` 成为工具声明、模型可见 schema、返回 schema、风险和默认执行策略的唯一来源。

### 正式字段

```text
name
description
category
namespace
parameters_schema
required_params
required_any_of
returns_schema
enabled
risk_level
requires_confirmation
workspace_scope
timeout_seconds
max_output_chars
default_observation_mode
supports_dry_run
fallback_tools
aliases
metadata
```

### 迁移细节

1. 当前 `timeout` 迁移为 `timeout_seconds`。
2. 为避免瞬间打断现有调用，可在反序列化或属性层保留 `timeout` 兼容读取；新代码和模型 spec 统一使用 `timeout_seconds`。
3. 扩展 `WORKSPACE_SCOPES`：

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

4. `to_model_spec()` 只输出模型选择工具所需字段，不输出 credential、底层 provider secret、MCP command/env 或内部 handler。
5. `enabled=false` 的工具不进入模型可见列表，但 Registry 管理端仍可查询其禁用原因。
6. `fallback_tools` 只是候选元数据，Tools 层不得自动执行 fallback。
7. `metadata` 明确支持：

```text
implemented
mock
source_type
provider
deprecated
replacement
```

### 明确不做

```text
不把 session permission 固化在 ToolSpec。
不把用户确认结果写回全局 ToolSpec。
不让远程 MCP 描述覆盖本地 risk policy。
```

### 测试与验收

```text
字段默认值符合保守策略。
非法 risk/workspace scope 被拒绝或明确归一化。
模型 spec 不泄露内部配置。
timeout 兼容读取有效。
returns_schema 可表达不同工具 data。
```

```powershell
python -m pytest tests/test_tool_spec_v1.py tests/test_tool_registry_v1.py -q
```

---

### Step 4 完成记录（2026-08-14）

#### 修改文件

```text
更新:
  src/tools/registry.py
  tests/test_tools_current_baseline.py
  src/tools/Tools层开发步骤与进度(1)-协议运行时.md

新增:
  tests/test_tool_spec_v1.py
```

#### 实现结果

`ToolSpec` 已正式化为以下字段：

```text
name
description
category
namespace
parameters_schema
required_params
required_any_of
returns_schema
enabled
risk_level
requires_confirmation
workspace_scope
timeout_seconds
max_output_chars
default_observation_mode
supports_dry_run
fallback_tools
aliases
metadata
```

迁移与默认策略：

```text
timeout_seconds 是正式字段，timeout 保留为兼容读写属性和旧构造参数。
新代码和 to_model_spec() 统一输出 timeout_seconds，不再输出 timeout。
workspace_scope 扩展支持 shell_command 和 mcp。
risk_level 非法值归一化为 medium；workspace_scope 非法值归一化为 none。
default_observation_mode 非法值归一化为 standard。
timeout_seconds 最小为 1；max_output_chars 允许 None，负值归一化为 0。
parameters_schema、returns_schema、metadata 及列表字段使用实例独立副本。
```

模型可见 schema：

```text
to_model_spec() 输出模型选择所需的稳定字段和 returns_schema。
不输出 timeout、metadata、fallback_tools、aliases 等兼容或内部字段。
ToolRegistry.to_model_specs() 不返回 enabled=false 的工具。
disabled ToolSpec 仍保留在 Registry 中，可由管理侧查询。
```

#### 测试命令与结果

```powershell
python -B -m unittest tests.test_tool_spec_v1 tests.test_tool_registry_v1 tests.test_tools_current_baseline tests.test_tool_errors_v1
# Ran 33 tests - OK

python -B -m unittest tests.test_tool_result_v1 tests.test_tool_call_protocol tests.test_command_tool_v1 tests.test_react_executor_tool_action tests.test_react_executor_command_action
# Ran 35 tests - OK

python -B -m compileall -q src/tools tests/test_tool_spec_v1.py tests/test_tool_registry_v1.py
# 通过
```

#### 兼容策略与边界

```text
未修改 Analyzer、Planner、ReActExecutor 主链路。
未实现 Step 5 的 alias 解析、alias 冲突检查、动态注册和 source 分组。
未实现 Step 6 的 ToolPolicy、session capability、路径裁决和确认裁决。
ToolSpec 的 fallback_tools 仍只是候选元数据，Tools 层不会自动执行 fallback。
ToolSpec 不保存 session permission 或用户确认结果。
```

---

## Step 5：ToolRegistry 的 enabled、alias 与动态注册

**状态：已完成**

### 目标

完成静态内置工具和动态 MCP 工具共享的注册中心，并让所有调用在执行前获得 canonical tool name 和参数验证结果。

### 涉及文件

```text
修改:
  src/tools/registry.py
  src/tools/tool_manager.py

新增测试:
  tests/test_tool_registry_v1.py
  tests/test_tool_registry_dynamic.py
```

### 执行细节

1. Registry 同时维护：

```text
canonical name -> ToolSpec
alias -> canonical name
dynamic source -> registered tool names
```

2. 增加或正式化：

```text
register
unregister
register_alias
resolve_name
get
has_tool
list_specs
to_model_specs
validate_tool_args
remove_dynamic_source
```

3. alias 冲突必须拒绝，不能静默覆盖正式工具。
4. `search_tool -> web_search`、`shell_tool -> shell_command_tool` 的兼容关系在对应工具注册后启用。
5. 参数校验顺序固定：

```text
名称/alias
  -> enabled
  -> args object
  -> required_params
  -> required_any_of
  -> 基础 JSON 类型
  -> additionalProperties/unknown args 策略
```

6. `to_model_specs()` 只暴露 `enabled=true` 且 `metadata.implemented` 不为 false 的正式工具。
7. MCP 动态工具按 `source=mcp:<server_id>` 分组，server 停止或禁用时可精确移除，不影响内置工具。
8. `build_default_tool_registry()` 不再依赖 ToolManager 的硬编码描述作为长期真相；注册构建应转向 ToolSpec 工厂或配置加载。

### 明确不做

```text
Registry 不执行工具。
Registry 不判断真实文件路径是否越界。
Registry 不判断 confirmed 是否可信。
Registry 不自动启动 MCP Server。
```

### 测试与验收

```text
enabled=false 不暴露给模型。
alias 返回 canonical name。
alias 冲突拒绝。
required_any_of 正确。
unknown args 按 schema 处理。
动态注册和按 source 移除正确。
search_tool/shell_tool 迁移不造成断链。
```

```powershell
python -m pytest tests/test_tool_registry_v1.py tests/test_tool_registry_dynamic.py -q
```

---

### Step 5 完成记录（2026-08-14）

#### 修改文件

```text
更新:
  src/tools/registry.py
  src/tools/tool_manager.py
  tests/test_tool_registry_v1.py
  src/tools/Tools层开发步骤与进度(1)-协议运行时.md

新增:
  tests/test_tool_registry_dynamic.py
```

#### 实现结果

Registry 现在维护三类关系：

```text
canonical tool name -> ToolSpec
alias -> canonical tool name
dynamic source -> registered canonical tool names
```

已正式支持：

```text
register
unregister
register_alias
resolve_name
get
has_tool
list_specs
list_aliases
list_dynamic_sources
to_model_specs
validate_tool_args
remove_dynamic_source
```

行为约束：

```text
canonical 名称重复、alias 与 canonical 冲突、alias 重复注册都会拒绝。
get/has_tool/validate_tool_args 支持 alias，并返回 canonical_tool_name。
disabled 工具保留在 Registry 管理面，校验返回 tool_disabled，不进入模型列表。
metadata.implemented=false 工具保留在 Registry，但不进入模型可见列表。
MCP source 可显式传入，也可由 metadata.source 或 source_type/server_id 推导为 mcp:<server_id>。
remove_dynamic_source 只移除对应 source 的工具，不影响内置工具或其他 MCP Server。
```

兼容迁移：

```text
search_tool -> web_search、shell_tool -> shell_command_tool 的 alias 机制已具备；
只有 canonical 工具实际注册后才建立 alias，不预注册不存在的 canonical 名称。
build_default_tool_registry 仍可按 ToolManager 的可用名称过滤，
但 ToolSpec 自身 description 是正式声明来源，不再被 ToolManager 描述覆盖。
ToolManager 增加 Registry facade 和动态 ToolSpec 注册/注销入口，但不执行工具。
```

#### 测试命令与结果

```powershell
python -B -m unittest tests.test_tool_registry_v1 tests.test_tool_registry_dynamic tests.test_tool_spec_v1 tests.test_tools_current_baseline tests.test_tool_errors_v1
# Ran 40 tests - OK

python -B -m unittest tests.test_tool_result_v1 tests.test_tool_call_protocol tests.test_command_tool_v1 tests.test_react_executor_tool_action tests.test_react_executor_command_action
# Ran 35 tests - OK

python -B -m unittest discover -s tests -p 'test_*.py'
# Ran 449 tests - OK (skipped=1)

python -B -m compileall -q src/tools tests/test_tool_registry_dynamic.py tests/test_tool_registry_v1.py
# 通过
```

#### 兼容策略与边界

```text
未修改 Analyzer、Planner、ReActExecutor 主链路。
Registry 不执行 handler，不判断真实路径、session capability 或 confirmed 可信度。
没有实现 MCP Server 启停、tools/list 发现和远程 transport；本 Step 只提供动态注册容器。
没有实现 ToolPolicy、ToolRuntime、统一执行入口和日志。
```

---

## Step 6：ToolPolicy 风险、权限、路径作用域与确认裁决

**状态：待开发**

### 目标

建立单一工具级策略入口。ReActExecutor 的任务级 safety 与 ToolPolicy 的工具级安全互补，但不能彼此绕过。

### 建议新增

```text
src/tools/policy.py
src/tools/path_policy.py
tests/test_tool_policy_v1.py
```

### 输入与输出

输入：

```text
ToolSpec
ToolCallRequest
解析后的目标路径和资源
配置默认权限
会话权限
```

输出建议：

```text
allowed
blocked
requires_confirmation
risk_level
reason
code
preview_required
affected_resources
```

### 裁决优先级

```text
blocked 规则
  > workspace / sensitive path
  > 不支持的管理员权限
  > session permission
  > ToolSpec risk
  > ActionPacket 请求
  > 工具默认行为
```

### 权限默认值

```text
allow_read_workspace=true
allow_write_workspace=false
allow_network=false
allow_command=false
allow_shell_command=false
allow_mcp=false
```

### 确认规则

```text
blocked:
  直接失败，确认不能放行。

high:
  默认要求确认。

medium:
  根据会话权限、工具类型和目标资源决定。

low:
  在允许的作用域内可执行。
```

必须明确：

```text
模型设置 requires_confirmation=false 不能降低本地策略。
模型设置 risk_level=low 不能降低 ToolSpec 或 ToolPolicy 风险。
用户打开会话写权限后，普通 workspace 文件 overwrite/patch 可按已确认策略执行；
敏感路径和 blocked 规则仍保持更高优先级。
```

### 本 Step 与后续路径底座的关系

本 Step 建立通用接口和基础裁决；文件的具体敏感路径、命令危险矩阵在 Step 10、19-21 完善。不得在这里硬编码所有工具业务。

### 测试与验收

```text
blocked 无法通过 confirmed 放行。
high 未确认返回 confirmation_required。
confirmed 只从 options 生效。
confirmed 缺少匹配 confirmation_id/preview_hash 时仍不放行。
allow_network=false 拒绝 web_search。
allow_shell_command=false 拒绝复杂 shell。
会话权限不能越过 workspace_root。
```

```powershell
python -m pytest tests/test_tool_policy_v1.py tests/test_react_executor_safety.py tests/test_react_executor_confirmation.py -q
```

### Step 6 完成记录（2026-08-14）

#### 修改文件

```text
新增：
  src/tools/path_policy.py
  src/tools/policy.py
  tests/test_tool_policy_v1.py

更新：
  src/tools/errors.py
  src/tools/__init__.py
  src/tools/Tools层开发步骤与进度(1)-协议运行时.md
```

#### 实现结果

```text
PathPolicy：
  统一解析 workspace_root 下的相对/绝对路径。
  阻止越过 workspace_root 的资源。
  支持配置 sensitive_paths / blocked_paths。
  提供保守的通用敏感名称保护，并输出 workspace-relative affected_resources。

ToolPolicy：
  输出 allowed / blocked / requires_confirmation / risk_level / reason / code /
  preview_required / affected_resources。
  固定执行 blocked 规则、路径作用域、管理员权限、session capability、
  ToolSpec risk、调用方请求的裁决优先级。
  allow_write_workspace=false 时写操作直接 permission_denied，不进入确认流程。
  network / command / shell_command / mcp 分别受对应会话能力控制。
  high 或 requires_confirmation 操作必须同时具备 options.confirmed、
  confirmation_id 和 preview_hash。
  blocked、越界、敏感路径和未支持的管理员权限不能通过确认放行。
  模型请求的低风险或取消确认不能降低 ToolSpec / Policy 的安全级别。

错误码：
  新增 admin_permission_required，并归类为 permission 错误。
```

#### 测试命令与结果

```powershell
python -B -m unittest tests.test_tool_policy_v1
# Ran 12 tests - OK

python -B -m unittest tests.test_tool_errors_v1 tests.test_tool_call_protocol tests.test_tool_registry_v1 tests.test_tool_registry_dynamic
# Ran 36 tests - OK

python -B -m unittest tests.test_react_executor_safety tests.test_react_executor_confirmation
# Ran 16 tests - OK

python -B -m unittest tests.test_tool_result_v1 tests.test_command_tool_v1 tests.test_react_executor_tool_action tests.test_react_executor_command_action
# Ran 27 tests - OK

python -B -m unittest discover -s tests -p 'test_*.py'
# Ran 461 tests - OK (skipped=1)

python -B -m compileall -q src/tools tests/test_tool_policy_v1.py
# 通过
```

#### 本 Step 边界与遗留问题

```text
未修改 Analyzer / Planner / ReActExecutor 主链路。
未实现 ToolRuntime、ToolManager.execute、dry_run、preview hash 生成、
timeout、retry、日志和真实工具执行。
未硬编码完整文件敏感路径清单或命令危险矩阵；
具体文件与命令工具规则留给 Step 10、19-21。
Step 6 的 expected_preview_hash 只支持调用方提供当前规范化摘要进行比对；
摘要生成和确认票据生命周期由后续 Runtime / ReActExecutor 负责。
```

---

## Step 7：ToolManager / ToolRuntime 正式执行入口

**状态：待开发**

### 目标

把当前硬编码 `ToolManager` 迁移为统一运行时，任何正式工具调用都经过 Registry、Policy、timeout、结果归一化和日志钩子接口。

### 涉及文件

```text
修改:
  src/tools/tool_manager.py
  src/tools/registry.py

建议新增:
  src/tools/runtime.py
  src/tools/tool_logger.py
  tests/test_tool_manager_v1.py
```

### 正式执行顺序

```text
接收 ToolCallRequest
  -> 生成/确认 call_id 和开始时间
  -> Registry resolve alias
  -> Registry 参数校验
  -> ToolPolicy 裁决
  -> dry_run 分支或真实执行
  -> timeout 控制
  -> 捕获工具异常
  -> 归一化 ToolResult
  -> 注入可信 trace/耗时/tool identity
  -> ToolLogger 接口 / 占位实现
  -> 返回 ToolResult
```

### handler 注册

ToolSpec 和 handler 分离但同名关联：

```text
ToolRegistry:
  保存声明和 schema。

ToolManager:
  保存 canonical tool_name -> handler。
```

若有 spec 无 handler：

```text
metadata.implemented=false 时不暴露。
被内部调用时返回 tool_disabled 或 tool_not_implemented。
```

### 兼容入口

保留：

```python
run_tool(tool_name: str, **kwargs) -> ToolResult
```

但其内部必须：

```text
构造 ToolCallRequest
使用受限默认 context/options
调用正式 execute(request)
```

不得：

```text
直接从旧 tools 字典取实例并 run。
把 handler 裸异常直接抛给 ReActExecutor。
把任意裸字符串当成完整正式结果而不补齐 identity 和 code。
```

### timeout

1. 优先使用 `options.timeout_seconds`，但不得超过 ToolSpec 或全局硬上限。
2. subprocess、网络和 MCP 工具应由各自 I/O 原语真正实施 timeout。
3. 对无法安全中断的普通 Python handler，V1 不应伪称已经强杀；记录边界并优先让 handler 自己支持 timeout。

### 测试与验收

```text
正式 request 成功执行。
alias 解析后返回 canonical tool_name。
非法参数不调用 handler。
blocked/confirmation_required 不调用 handler。
dry_run 不调用不支持的真实副作用路径。
handler 抛异常 -> internal_error。
裸结果兼容包装。
trace 和 duration 由 runtime 注入。
run_tool 兼容入口走同一条执行路径。
```

```powershell
python -m pytest tests/test_tool_manager_v1.py tests/test_tool_registry_v1.py -q
```

### Step 7 完成记录（2026-08-14）

#### 修改文件

```text
新增：
  src/tools/runtime.py
  src/tools/tool_logger.py
  tests/test_tool_manager_v1.py

更新：
  src/tools/tool_manager.py
  src/tools/errors.py
  src/tools/__init__.py
  src/tools/Tools层开发步骤与进度(1)-协议运行时.md
```

#### 实现结果

```text
ToolRuntime：
  接收 ToolCallRequest，生成可信 call_id 和时间信息。
  按 Registry resolve alias、参数校验、ToolPolicy、dry_run、handler 执行顺序处理。
  捕获 handler 异常并归一化为 internal_error。
  支持裸返回值包装为 ToolResult。
  覆盖 handler 返回结果中的 tool identity、trace、execution、step、call_id、
  时间戳和耗时字段，且不会修改 handler 原始 ToolResult 实例。
  timeout_seconds 受 ToolSpec 和 Runtime 全局上限约束；
  仅向显式声明 timeout_seconds 的 handler 注入运行时超时值。

ToolManager：
  新增 execute(request) 正式入口。
  通过 handler resolver 维护 canonical tool_name -> handler 关系。
  支持 register_tool_handler / register_handler 和带 handler 的 ToolSpec 注册。
  run_tool 保留为迁移期入口，只负责构造历史兼容 ToolCallRequest 并进入 Runtime。
  历史动态 tools 映射可注册为兼容 ToolSpec，但不再绕过正式执行管线。

日志边界：
  新增 ToolLogger Protocol 和 NullToolLogger 占位实现。
  Runtime 会调用日志钩子；日志落盘留给 Step 9。

兼容错误码：
  新增 tool_not_implemented、dry_run_preview、dry_run_not_supported。
```

#### 测试命令与结果

```powershell
python -B -m unittest tests.test_tool_manager_v1
# Ran 12 tests - OK

python -B -m unittest tests.test_tools_current_baseline tests.test_tool_policy_v1 tests.test_tool_call_protocol tests.test_tool_errors_v1 tests.test_tool_registry_v1
# Ran 49 tests - OK

python -B -m unittest tests.test_react_executor_safety tests.test_react_executor_confirmation tests.test_react_executor_tool_action tests.test_react_executor_command_action
# Ran 34 tests - OK

python -B -m unittest discover -s tests -p 'test_*.py'
# Ran 473 tests - OK (skipped=1)

python -B -m compileall -q src/tools tests/test_tool_manager_v1.py
# 通过
```

#### 本 Step 边界与遗留问题

```text
未修改 Analyzer / Planner / ReActExecutor 主链路；正式 ReActExecutor request 接入留给后续集成步骤。
未实现真正可中断的普通 Python handler 超时；subprocess/网络/MCP 仍由各自工具 I/O 原语负责。
dry_run 仅提供基础 preview/不调用 handler 的分支，完整 preview、影响范围和 hash 机制留给 Step 8。
NullToolLogger 不写 logs/tools.log，正式 JSONL 审计日志留给 Step 9。
ToolManager 仍保留旧 tools 字典作为 handler 兼容存储，但执行不再直接从字典调用。
```

---

## Step 8：dry_run、preview、输出截断与 artifact 引用策略

**状态：待开发**

### 目标

建立跨工具一致的预检查和输出控制能力，为后续 overwrite、patch、delete、command、MCP 确认流程提供真实 preview。

### 涉及文件

```text
修改:
  src/tools/runtime.py
  src/tools/protocol.py
  src/tools/policy.py

建议新增:
  src/tools/output_control.py
  tests/test_tool_preview_v1.py
  tests/test_tool_output_control.py
```

### dry_run 统一语义

```text
做:
  schema 校验
  路径解析
  权限和风险裁决
  影响范围计算
  生成 preview

不做:
  写文件
  删除文件
  运行命令
  发起网络请求
  调用远程 MCP tools/call
```

返回：

```text
success=true
code=dry_run_preview
metadata.preview
metadata.requires_confirmation
metadata.affected_resources
```

如果参数或策略本身失败，dry_run 仍返回真实失败码，不能一律成功。

### preview 一致性

高风险工具的 dry_run 和真实执行必须共享同一套参数解析与目标定位逻辑：

```text
patch preview 命中的文本必须与执行时再次校验。
delete preview 的文件列表必须与执行目标一致。
command preview 的 program/args/cwd 必须与执行值一致。
```

若 preview 后资源发生变化，真实执行应返回 conflict，而不是盲目执行。V1 可使用 hash、mtime、size 或目标存在性做最低限度检查。

### 输出控制

实现统一阈值：

```text
max_output_chars
max_raw_output_chars
max_observation_chars
```

超限处理：

```text
ToolResult.data 保留关键结构。
raw_output 截断。
metadata 记录原始长度/hash。
必要时预留 artifact_ref。
不自动调用模型摘要。
```

### Observation 边界

Tools 只提供：

```text
data_summary
preview
raw_ref/artifact_ref
raw_output_truncated
```

`minimal / standard / full` 最终选择由 ReActExecutor 在 Step 39 实现。

### 测试与验收

```text
dry_run 不产生副作用。
blocked dry_run 不伪成功。
preview 包含受影响资源。
执行前资源变化返回 conflict。
大输出正确截断并保留 hash/长度。
敏感内容不进入 preview。
```

```powershell
python -m pytest tests/test_tool_preview_v1.py tests/test_tool_output_control.py -q
```

### Step 8 完成记录（2026-08-14）

#### 修改文件

```text
新增：
  src/tools/output_control.py
  tests/test_tool_preview_v1.py
  tests/test_tool_output_control.py

更新：
  src/tools/runtime.py
  src/tools/policy.py
  src/tools/errors.py
  src/tools/__init__.py
  tests/test_tool_policy_v1.py
  src/tools/Tools层开发步骤与进度(1)-协议运行时.md
```

#### 实现结果

```text
dry_run / preview：
  参数校验、路径/权限/风险裁决仍先于 preview。
  blocked、越界、敏感路径和权限失败不会伪造成 preview 成功。
  写入、命令等副作用工具不调用真实 handler。
  高风险工具允许在未确认前生成 preview，并标记 requires_confirmation。
  preview 包含安全参数摘要、affected_resources 和资源快照。
  content/code/text/prompt 等内容只保留长度与 sha256，不进入 preview。

preview 一致性：
  preview_hash 绑定规范化参数摘要、影响资源和资源快照。
  快照包含存在性、文件大小、mtime，<=1MB 文件额外包含 sha256。
  已确认执行前重新计算 preview_hash。
  资源变化或目标变化返回 preview_conflict，不调用 handler。

输出控制：
  统一支持 max_output_chars、max_raw_output_chars、
  max_observation_chars。
  data 尽量保留结构，超限字符串/列表受控缩短。
  raw_output 超限时截断并记录长度、字节数、hash、
  raw_ref/artifact_ref。
  metadata.output_control 提供 data_summary、preview、
  observation_mode 和 raw_output_truncated 相关信息。
  不调用 Models 生成摘要，不持久化 artifact。
```

#### 测试命令与结果

```powershell
python -B -m unittest tests.test_tool_preview_v1 tests.test_tool_output_control tests.test_tool_policy_v1
# Ran 21 tests - OK

python -B -m unittest tests.test_tool_manager_v1 tests.test_tools_current_baseline tests.test_tool_result_v1 tests.test_tool_call_protocol tests.test_tool_errors_v1 tests.test_tool_registry_v1
# Ran 56 tests - OK

python -B -m unittest tests.test_react_executor_safety tests.test_react_executor_confirmation tests.test_react_executor_tool_action tests.test_react_executor_command_action
# Ran 34 tests - OK

python -B -m unittest discover -s tests -p 'test_*.py'
# Ran 482 tests - OK (skipped=1)

python -B -m compileall -q src/tools tests/test_tool_preview_v1.py tests/test_tool_output_control.py
# 通过
```

#### 本 Step 边界与遗留问题

```text
artifact_ref 目前是受控引用标识，不提供 artifact 持久化和读取服务。
observation_mode 只生成 Tools 层安全摘要，最终 Observation 仍由 ReActExecutor 决定。
默认输出阈值暂由 OutputController 提供，统一配置加载留给 Step 9。
不同文件/命令/MCP 工具的专用 diff、命令解析和远程 preview 逻辑留给后续工具步骤。
preview_conflict 已建立通用错误边界，具体工具可在后续补充更细粒度 conflict 诊断。
```

---

## Step 9：config/tools、ToolLogger 与 logs/tools.log

**状态：待开发**

### 目标

建立 Tools V1 配置加载和独立 JSONL 审计日志，正式补齐 ToolLogger 的实现，结束硬编码默认值散落在各工具中的状态。

### 涉及文件

```text
新增:
  config/tools/defaults.json
  config/tools/policies.json
  config/tools/providers.json
  config/tools/mcp_servers.json
  src/tools/config.py
  src/tools/tool_logger.py
  tests/test_tool_config_v1.py
  tests/test_tool_logging_v1.py

运行时生成:
  logs/tools.log
```

### 配置职责

`defaults.json`：

```text
全局 enabled
默认 timeout
输出阈值
默认 observation_mode
workspace policy
```

`policies.json`：

```text
会话权限默认值
risk -> allow/confirm/block
敏感路径和忽略目录基础配置
```

`providers.json`：

```text
web_search provider 路由
Tavily env 引用
model_builtin 配置引用
```

`mcp_servers.json`：

```text
初始为空或包含禁用示例
不得写入真实明文 credential
```

### 配置加载规则

```text
缺可选配置:
  使用保守默认值。

JSON 格式错误:
  返回/记录结构化 config error，不能静默开放权限。

环境变量引用:
  运行时解析，不把值回写配置或日志。

用户/会话覆盖:
  后续 Runtime 注入，不能直接修改全局 ToolSpec。
```

### tools.log

每次调用一行 JSON，至少包含：

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

### input_summary

规则生成，不调用模型。按工具记录：

```text
路径
模式
参数数量
内容长度/hash
命令 program 和 args_count
搜索 query 长度/provider
MCP server/tool 和参数键
```

不得记录：

```text
完整文件内容
完整 stdout/stderr
完整网页正文
完整 MCP 响应
API key/token
Authorization header
敏感 env 值
内部 prompt
```

### 用户事件边界

ToolLogger 不写用户事件。ToolResult.metadata 可提供 `event_summary` 等建议，但正式 `ExecutionEvent` 由 ReActExecutor 生成。

### 测试与验收

```text
配置缺失使用保守默认值。
非法配置不开放权限。
env secret 不出现在 to_dict/log。
tools.log 每行是合法 JSON。
成功、失败、dry_run、confirmation 都有记录。
日志没有 raw_output 和敏感参数。
并发/连续写入不破坏单行 JSON。
```

```powershell
python -m pytest tests/test_tool_config_v1.py tests/test_tool_logging_v1.py tests/test_react_executor_logging.py -q
```

### Step 9 完成记录（2026-08-14）

#### 修改文件

```text
新增:
  config/tools/defaults.json
  config/tools/policies.json
  config/tools/providers.json
  config/tools/mcp_servers.json
  src/tools/config.py
  tests/test_tool_config_v1.py
  tests/test_tool_logging_v1.py

更新:
  src/tools/tool_logger.py
  src/tools/tool_manager.py
  src/tools/runtime.py
  src/tools/policy.py
  src/tools/__init__.py
  src/tools/Tools层开发步骤与进度(1)-协议运行时.md
```

#### 实现结果

```text
新增 ToolsConfig、ToolsRuntimeConfig、ToolsPolicyConfig 和结构化 ToolsConfigError。
缺失 config/tools 文件时使用保守默认值；非法 JSON、非法类型和明文 secret 会返回
结构化错误。ToolManager 会保留 config_error 并回退到保守配置，不会放宽网络、命令、
Shell 或 MCP capability。

defaults.json 管理全局 enabled、默认超时、输出阈值、Observation 模式、workspace_only
和 logs/tools.log。policies.json 管理默认 capability、risk policy、路径和目录边界。
providers.json 只保存 web_search 路由与 api_key_env 等引用名；mcp_servers.json 初始为空，
均不保存明文 credential。

JsonlToolLogger 接入 ToolRuntime 的唯一 _finish() 出口。每次调用向 logs/tools.log 追加
一行 JSON，包含调用 identity、工具身份、输入/选项摘要、风险确认、结果、耗时、输出摘要、
raw hash、受影响资源和 artifact 引用。日志只保存路径、参数键、内容长度/hash、命令
program/args_count、搜索 query 长度/hash 等安全摘要；不写完整文件、stdout/stderr、网页正文、
MCP 响应、密钥、Authorization、敏感 env 值或内部 prompt。写入使用实例级锁，失败不改变
真实 ToolResult，也不产生用户可见 ExecutionEvent。

ToolPolicy 可注入 config risk_policy，blocked 风险始终不可降低；ToolRuntime 支持全局
enabled 开关，禁用时返回 tool_disabled 并仍完成开发审计。
```

#### 测试命令与结果

```powershell
python -B -m unittest tests.test_tool_config_v1 tests.test_tool_logging_v1 tests.test_react_executor_logging
# Ran 16 tests - OK

python -B -m unittest discover -s tests -p 'test_*.py'
# Ran 491 tests - OK (skipped=1)

python -B -m compileall -q src/tools tests/test_tool_config_v1.py tests/test_tool_logging_v1.py
# 通过
```

#### 本 Step 边界与遗留问题

```text
未修改 Analyzer / Planner / ReActExecutor 主链路；ReActExecutor 的正式 ToolCallRequest
接入仍留在 Step 38。ToolLogger 只做开发 JSONL 审计，不生成 ExecutionEvent 或 Observation。

providers.json 只建立 web_search provider 配置底座；真实 search_api、必须经 Models 层的
model_builtin 和 WebSearchData 归一化留在联网搜索分卷。mcp_servers.json 只建立安全配置
底座；本地 STDIO 生命周期、发现和调用留在 MCP 分卷。artifact_ref 仍是受控引用标识，
不提供 artifact 持久化或读取服务；ignored_directories 的具体遍历应用留给文件/命令工具步骤。
```

### 分卷完成标准

Step 0-9 全部完成后：

```text
所有新工具可以只关注自身业务执行和 data schema。
所有正式调用统一经过 request、registry、policy、runtime、result、logger。
兼容 run_tool 不形成第二套逻辑。
尚未要求 ReActExecutor 完成新 request 接入；该集成在 Step 38。
```
