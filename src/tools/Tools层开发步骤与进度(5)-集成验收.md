# Tools 层开发步骤与进度（5）- 集成验收

> 覆盖步骤：Step 38-44  
> 当前状态：Step 38-44 已完成，分卷收官  
> 前置分卷：Step 0-37  
> 上位设计：`Tools层设计决策汇总(1)-总纲与跨层边界.md`、`Tools层设计决策汇总(6)-集成验收与后续边界.md`

本分卷不重新设计 ReActExecutor。它负责把已经完成的 Tools Runtime 接入当前正式 ReAct 主链路，并证明：

```text
用户输入
  -> ReactAgent
  -> Analyzer
  -> Planner
      生成 TaskPlan / TaskUnit / PlanStep
  -> ReActExecutor
      Reasoning -> Decision -> Tool / Model / User / Control -> Observation -> Checker
  -> 输出反馈处理器
  -> 用户反馈
```

其中 Tools 只执行真实工具；Observation、Checker、ExecutionEvent、ExecutionResult 的业务裁决仍属于 ReActExecutor/ReactAgent 侧。

---

## Step 38：ReActExecutor 将 ActionPacket 接入 ToolCallRequest

**状态：已完成**

### 目标

把当前 ReActExecutor 直接调用 `run_tool(tool_name, **kwargs)` 的正式路径迁移为：

```text
ActionPacket
  -> ReActExecutor 解析和业务校验
  -> ToolCallRequest
  -> ToolManager.execute
  -> ToolResult
```

### 涉及文件

```text
修改:
  src/agent/react_executor.py
  src/tools/tool_manager.py

测试:
  tests/test_react_executor_tool_action.py
  tests/test_react_executor_command_action.py
  tests/test_react_executor_action_packet_schema.py
  tests/test_react_executor_tool_runtime_integration.py
```

### 映射规则

```text
packet.action_target
  -> request.tool_name

packet.action_args
  -> request.args

packet.user_visible_message
  -> input_summary 候选，不作为工具业务参数，除非该工具 schema 明确声明

packet.action_args.purpose
  -> 仅在工具 schema 明确允许且经执行器清洗后作为 input_summary 候选

packet.thought_summary
  -> 不透传给 ToolManager，不原样进入用户事件或 tools.log

RuntimeState / PlanStep / trace
  -> ToolCallContext

执行器安全判断、会话权限、恢复状态
  -> ToolCallOptions
```

### ReActExecutor 必做

1. 校验 `action_type` 是允许的工具动作。
2. 校验 `action_target` 非空且可被 Registry 解析。
3. 合并 PlanStep 参数和 ActionPacket 参数时保持既有优先级，并移除执行器控制字段：

```text
input_from
output_key
fallback_reason
packet_id/observation_id/action_id 等内部引用字段
```

4. 构造完整 context：

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
initiated_by=model 或 system
```

5. 不把模型原始 `confirmed=true` 当作真实确认。只有 pending confirmation 被用户批准并 resume 后，才在 options 注入可信 `confirmed`。
6. 通过 ToolManager 正式入口执行，不能在 ReActExecutor 中直接 import subprocess 或调用工具 handler。
7. 保留当前 ReActExecutor 对错误的结构化处理、Checker retry/fallback/ask_user 语义。

### 名称迁移

```text
正式:
  command_tool
  shell_command_tool

兼容:
  shell_tool -> shell_command_tool
```

迁移期测试必须证明旧 ActionPacket 不会断链；新模型 specs 只把正式名称作为主名暴露。

### 明确不做

```text
不把完整 ActionPacket 传给 ToolManager。
不让 ToolManager 代替 Checker。
不将 ToolResult 转成虚构成功文本。
不改变 Analyzer/Planner 的计划生成职责。
不恢复旧顺序 Executor 自动 fallback。
```

### 测试与验收

```text
action_type=call_tool 的 ActionPacket 正常映射。
参数非法时 handler 不执行。
工具不存在。
工具 alias。
command_tool 不直接 shell。
shell_command_tool 走正式工具。
trace/step/packet 字段传播。
model confirmed 不能越权。
```

```powershell
python -m pytest tests/test_react_executor_tool_action.py tests/test_react_executor_command_action.py tests/test_react_executor_action_packet_schema.py tests/test_react_executor_tool_runtime_integration.py -q
```

### 完成记录（2026-08-17）

修改文件：

```text
src/agent/react_executor.py
tests/test_react_executor_tool_runtime_integration.py
tests/test_react_executor_tool_action.py
tests/test_react_executor_command_action.py
tests/test_react_executor_action_packet_schema.py
tests/test_react_executor_actions.py
tests/test_react_executor_confirmation.py
tests/test_react_executor_core.py
tests/test_react_executor_events.py
tests/test_react_executor_fallback.py
tests/test_react_executor_invariants.py
tests/test_react_executor_logging.py
tests/test_react_executor_model_action.py
tests/test_react_executor_plan_precheck.py
tests/test_react_executor_result.py
tests/test_react_executor_retry.py
tests/test_react_executor_safety.py
tests/test_react_executor_termination.py
tests/test_react_executor_v1.py
tests/test_analyzer_planner_react_executor_pipeline.py
tests/test_react_agent_with_react_executor.py
```

完成内容：

```text
ReActExecutor 的 call_tool 正式路径改为：
  ActionPacket -> ToolCallRequest -> ToolManager.execute -> ToolResult -> Observation。

新增 _build_tool_call_request()：
  packet.action_target 映射 request.tool_name。
  清洗后的 packet/step 合并参数映射 request.args。
  RuntimeState / PlanStep / trace 字段映射 ToolCallContext。
  task/session capability 和执行器安全状态映射 ToolCallOptions。

新增/固定 context 字段：
  trace_id / execution_id / plan_id / task_id / step_id / packet_id /
  session_id / user_id / workspace_root / source=react_executor /
  initiated_by=model 或 system。

参数清洗：
  input_from / output_key / fallback_reason / packet_id / observation_id /
  action_id / confirmed / confirmation_id / preview_hash 等执行器控制字段不进入工具业务参数。
  command_tool schema 中的 requires_confirmation 保留为合法业务字段。

确认边界：
  模型 ActionPacket 中的 confirmed=true 不会写入 ToolCallOptions.confirmed。
  只有执行器从 pending confirmation resume 的可信路径会把 initiated_by 标记为 system。
  本 Step 不实现 confirmation_id / preview_hash 的生成和 resume 绑定；该能力留给 Step 40。

名称与 alias：
  command_tool 仍走正式工具路径。
  shell_command_tool / shell_tool 不再被 command_tool 的 ActionPacket 结构校验误判。
  当 plan.available_tools 只含 canonical 名称时，Registry alias 也能通过 ReActExecutor 可用工具集合。

测试迁移：
  ReActExecutor 相关 fake ToolManager 统一补充 execute(request) 适配，测试仍可通过 run_calls 观察真实请求参数。
  未在生产代码中恢复 run_tool 第二套运行时。
```

测试命令与结果：

```powershell
python -m pytest tests/test_react_executor_tool_action.py tests/test_react_executor_command_action.py tests/test_react_executor_action_packet_schema.py tests/test_react_executor_tool_runtime_integration.py -q
# 43 passed

$files = Get-ChildItem -Path tests -File -Filter 'test_react_executor*.py' | ForEach-Object { $_.FullName }; python -m pytest @files -q
# 217 passed

python -m pytest tests/test_tool_call_protocol.py tests/test_tool_manager_v1.py tests/test_command_tool_argv.py tests/test_shell_command_tool.py tests/test_tool_policy_v1.py -q
# 54 passed

python -m py_compile src/agent/react_executor.py tests/test_react_executor_tool_runtime_integration.py
# passed

python -m pytest tests/test_analyzer_planner_react_executor_pipeline.py tests/test_react_agent_with_react_executor.py -q
# 12 passed

python -m pytest tests -q
# 716 passed, 4 skipped
```

边界与遗留：

```text
本 Step 不改变 Analyzer / Planner / ReActExecutor 主链路职责。
本 Step 不让 Tools 消费完整 ActionPacket，也不让 Tools 生成 Observation。
本 Step 不实现 Step 39 的 Observation minimal / standard / full。
本 Step 不实现 Step 40 的 dry_run preview、confirmation_id、preview_hash、资源变化冲突和 approve/reject resume 绑定。
当前工作树仍包含大量前序迁移期未跟踪文件，本 Step 未清理、未回退无关内容。
```

---

## Step 39：ToolResult 到 Observation 的 minimal、standard、full

**状态：已完成**

### 目标

由 ReActExecutor 根据真实 ToolResult 生成受控 Observation，解决“大结果有时需要全量、有时只需关键字段”的问题，不为每次观察默认调用模型。

### 涉及文件

```text
修改:
  src/agent/react_executor.py

建议新增:
  src/agent/observation_builder.py
  tests/test_react_executor_observation_modes.py
```

### 决策来源与优先级

```text
安全策略
  > 上下文预算和最大字符数
  > ReActExecutor 当前步骤裁决
  > ActionPacket observation_mode 请求
  > ToolSpec.default_observation_mode
```

### 三种模式

`minimal`：

```text
success
tool_name
code
message
关键状态字段
result_count/exit_code/path/affected_count 等必要元数据
```

`standard`：

```text
minimal
受控 preview
关键结构化 data
前 N 条搜索结果
命令 stdout/stderr 摘要
文件 content_preview
artifact_ref
```

`full`：

```text
在安全、敏感、大小和上下文预算允许时加入完整 data/content。
仍不得默认加入完整 raw_output、credential、内部异常堆栈。
```

### 不调用模型的默认规则

```text
Observation builder 只做规则筛选、字段提取、截断和脱敏。
input_summary 由 ActionPacket purpose/ToolManager 规则生成。
需要自然语言总结时，ReActExecutor 创建独立 call_model ActionPacket。
```

### 工具特定边界

```text
read_file:
  full 可以包含受限 content。

command:
  standard 使用 stdout/stderr preview，full 仍受上限。

web_search:
  standard 包含 title/url/snippet/summary，full 不默认 raw_content。

MCP:
  standard 只放受控 content/structured_content 摘要。
```

### 失败结果

失败 Observation 必须保留：

```text
success=false
code
error_type
retryable
message
关键 data
```

不能因为只显示 minimal 就丢掉 Checker 需要的错误码。

### 明确不做

```text
不让 Tools 生成 ObservationPacket。
不把摘要模型调用隐藏在 Observation builder。
不默认把 raw_output 全量塞入上下文。
```

### 测试与验收

```text
三种模式字段差异。
字符预算强制降级。
敏感字段脱敏。
失败错误码保留。
搜索无 raw_content。
大文件 full 仍受限制。
```

```powershell
python -m pytest tests/test_react_executor_observation.py tests/test_react_executor_observation_modes.py -q
```

### 完成记录（2026-08-18）

修改文件：

```text
src/agent/observation_builder.py
src/agent/react_executor.py
src/agent/react_executor_protocol.py
tests/test_react_executor_observation_modes.py
```

完成内容：

```text
ReActExecutor 新增 ToolResult -> Observation 的分级构建流程，正式支持 minimal / standard / full 三种观察粒度。
新增独立 observation builder，只负责规则筛选、字段提取、截断和脱敏，不调用模型、不生成 ToolResult。
minimal 侧重 success / code / message / 关键状态字段，standard 在此基础上加入受控 preview 与结构化摘要，full 在预算允许时保留完整受控 data，但仍不默认带入 raw_output、credential 或堆栈。
web_search 优先复用工具侧 already-available 的 observation_views 候选，且 raw_content 不进入 Observation。
失败结果保留 code / error_type / retryable / message / 关键 data，Checker 依赖语义不被压缩掉。
ObservationPacket 扩展了 observation_mode、data_summary、included_fields、raw_ref、artifact_ref 等可审计字段。
```

测试命令与结果：

```powershell
python -m pytest tests/test_react_executor_observation_modes.py tests/test_react_executor_observation.py tests/test_tool_output_control.py tests/test_web_search_tool.py -q
# 27 passed

$files = Get-ChildItem -Path tests -File -Filter 'test_react_executor*.py' | ForEach-Object { $_.FullName }; python -m pytest @files -q
# 223 passed

python -m pytest tests/test_tool_call_protocol.py tests/test_tool_manager_v1.py tests/test_tool_output_control.py tests/test_tool_policy_v1.py tests/test_tool_preview_v1.py tests/test_web_search_v1_acceptance.py -q
# 51 passed

python -m py_compile src/agent/observation_builder.py src/agent/react_executor.py src/agent/react_executor_protocol.py tests/test_react_executor_observation_modes.py
# passed
```

边界与遗留：

```text
本 Step 只负责 ReActExecutor 侧的 Observation 分级，不改变 Tools 侧“只返回真实 ToolResult”的边界。
本 Step 不实现 Step 40 的 confirmation preview、pending confirmation、resume 绑定和 dry_run 交互闭环。
本 Step 采用工具侧已提供的 preview / data_summary / artifact_ref 作为观察材料，不重新设计工具输出协议。
当前工作树仍包含大量前序迁移期未跟踪文件，本 Step 未清理、未回退无关内容。
```

---

## Step 40：confirmation preview、暂停与 resume

**状态：已完成**

### 目标

把高风险工具统一接入：

```text
dry_run
  -> confirmation_required event
  -> 暂停执行
  -> 用户批准/拒绝
  -> resume 或结束
```

### 适用工具

```text
write_file overwrite/append
patch_file
copy/move/rename overwrite
delete_file
command_tool high risk
shell_command_tool
MCP high risk
code_executor
```

### 执行顺序

```text
ReActExecutor 收到动作
  -> 预检查并构造 ToolCallRequest(options.dry_run=true)
  -> ToolResult(code=dry_run_preview)
  -> 生成 confirmation_required ExecutionEvent
  -> 保存 pending confirmation/context
  -> 结束当前执行轮并等待用户
  -> 用户 approve/reject
  -> resume 时校验 pending call_id/packet_id
  -> approve 才真实执行
```

### 确认安全

1. 确认必须绑定：

```text
session_id
execution_id
step_id
call_id
目标资源 hash/摘要
```

2. 用户批准后资源发生变化，重新 dry_run 或返回 conflict。
3. 用户拒绝产生 `user_rejected`，不能伪装工具失败或成功。
4. `blocked` 不产生可放行的确认请求。
5. approval scope V1 至少支持 `one_call`；`session` 需要记录并受工具/风险范围限制，不能自动覆盖 blocked。

### 用户可见内容

确认事件显示：

```text
要执行什么
目标资源
风险原因
影响范围
是否会修改/删除/联网/调用远程服务
```

不显示：

```text
完整 secret
内部 prompt
完整命令输出
完整 MCP env
```

### 完成记录

```text
修改文件:
  src/agent/react_executor.py
  src/agent/react_executor_protocol.py
  src/tools/policy.py
  tests/test_react_executor_preview_resume.py

测试命令:
  python -m pytest tests/test_react_executor_preview_resume.py tests/test_tool_policy_v1.py -q
  python -m pytest tests/test_react_executor_confirmation.py tests/test_react_executor_events.py -q

测试结果:
  19 passed
  31 passed

边界:
  preview conflict 在 resume 后直接返回失败，不再继续进入等待确认。
  confirmation_id / call_id / preview_hash 仅作为可审计票据，不写死为固定值。
  confirmation event 仅暴露摘要，不泄露完整 action_args。
  blocked、越界、管理员权限等硬拦截仍然不会通过确认放行。

遗留:
  当前工作树仍包含大量前序迁移期未跟踪文件，本 Step 未清理、未回退无关内容。
```

### 明确不做

```text
不把“用户没有开总开关”理解为所有动作永久不可用；
未授权动作可以通过确认流程获得本次授权，但 blocked/越界仍不可放行。
不在 Tools 内实现用户界面。
不让模型代替用户确认。
```

### 测试与验收

```text
overwrite preview。
patch diff preview。
delete list preview。
shell preview。
MCP preview 不调用远程。
approve resume。
reject。
资源变化 conflict。
blocked 无确认。
重复/错误 call_id resume 拒绝。
```

```powershell
python -m pytest tests/test_react_executor_confirmation.py tests/test_react_executor_preview_resume.py -q
```

---

## Step 41：ExecutionEvent、输出反馈处理器与日志分离

**状态：已完成**

### 目标

让用户看到执行器循环的安全进度说明，同时保持内部开发日志和模型上下文不泄露。

### 职责边界

```text
Tools:
  ToolResult.metadata 提供 event_summary/event_details/preview/affected_resources。

ReActExecutor:
  根据动作开始、ToolResult 和 Observation 生成正式 ExecutionEvent。

输出反馈处理器:
  消费 ExecutionEvent/ExecutionResult，转换为用户界面或 API 流式反馈。

logs/tools.log:
  记录开发审计信息，不直接展示给用户。
```

### 用户可见事件

至少覆盖：

```text
tool_started
tool_finished
tool_failed
confirmation_required
observation_created
model_step_started/model_step_finished（按现有 ReActExecutor 事件协议）
```

事件原则：

```text
短
可读
脱敏
可展开但默认摘要
不含内部思维链
不含完整 raw_output
不含明文凭证
```

示例：

```text
正在读取 src/tools/base.py。
已修改 src/app.py，影响 3 行。
命令执行失败，exit_code=1，错误输出已截断。
调用 mcp.github.create_issue 前需要确认。
```

### 与 ExecutionResult 的关系

```text
ExecutionEvent:
  执行过程中的增量反馈。

ExecutionResult:
  ReActExecutor 最终状态、结果、错误、是否需要用户输入和 final answer。
```

Tools 不创建二者，只提供真实 ToolResult 和事件建议 metadata。

### 明确不做

```text
不把 tools.log 当用户 event。
不把 Thought 原文当用户可见输出。
不让输出处理器重新执行工具或重规划。
```

### 测试与验收

```text
工具开始/结束/失败事件。
确认事件。
事件脱敏。
事件不含 raw_output 全文。
tools.log 与 events 内容分离。
ExecutionResult 能反映用户待确认状态。
```

```powershell
python -m pytest tests/test_react_executor_events.py tests/test_react_executor_result.py tests/test_react_executor_logging.py -q
```

### 完成记录（2026-08-18）

修改文件：
```text
src/agent/react_executor_protocol.py
src/agent/react_executor_events.py
src/agent/react_executor.py
src/agent/output_feedback.py
src/agent/react_agent.py
tests/test_react_executor_events.py
tests/test_react_executor_tool_action.py
tests/test_react_executor_model_action.py
tests/test_output_feedback_processor.py
```

完成内容：
```text
ExecutionEvent 协议补齐 tool_failed、model_step_started、model_step_finished。
EventStream 的 timeline 映射、分组和完整性校验支持工具失败事件与模型步骤事件。
ReActExecutor 工具调用失败时生成 tool_failed，成功仍生成 tool_finished；异常失败也走 tool_failed。
ToolResult 进入用户事件时只暴露白名单摘要：message、data_summary、event_summary、event_details、affected_resources、raw_output_truncated，不把完整 raw_output 放入用户事件。
call_model 中间模型动作和主 ReAct ActionPacket 决策模型调用均生成 model_step_started / model_step_finished；用户事件只包含输入摘要、公开 action 摘要、错误列表、耗时和长度，不暴露 prompt 原文、thought_summary 或原始模型输出。
新增只读 OutputFeedbackProcessor，负责把 ExecutionEvent / ExecutionResult 转换为用户反馈结构；不导入 ToolManager，不执行工具，不重规划。
ReactAgent 新增 run_feedback() 作为上层消费输出反馈处理器的结构化入口，原 run / run_with_result / run_stream 行为不变。
```

测试命令与结果：
```powershell
python -m pytest tests/test_react_executor_events.py tests/test_react_executor_result.py tests/test_react_executor_logging.py tests/test_output_feedback_processor.py tests/test_react_executor_tool_action.py tests/test_react_executor_model_action.py -q
# 54 passed

$files = Get-ChildItem -Path tests -File -Filter 'test_react_executor*.py' | ForEach-Object { $_.FullName }; python -m pytest @files -q
# 229 passed

python -m py_compile src/agent/react_executor_protocol.py src/agent/react_executor_events.py src/agent/react_executor.py src/agent/output_feedback.py src/agent/react_agent.py tests/test_output_feedback_processor.py
# passed
```

边界与遗留：
```text
本 Step 不改变 Analyzer / Planner / ReActExecutor 主链路职责。
Tools 仍只返回真实 ToolResult，不生成 ExecutionEvent、Observation 或 ExecutionResult。
输出反馈处理器只读消费事件和最终结果，不重新执行工具、不调用模型、不重规划。
logs/tools.log 与用户事件继续分离；本 Step 只验证用户反馈不消费内部日志事件，不改变 ToolLogger 的文件写入策略。
用户事件默认只给摘要和可展开结构，仍不暴露完整 raw_output、prompt、thought_summary、明文凭证或异常堆栈。
当前工作树仍包含大量前序迁移期未跟踪文件，本 Step 未清理、未回退无关内容。
```

---

## Step 42：Tools 全量回归、安全矩阵与配置审计

**状态：已完成**

### 目标

将所有工具、错误码、权限、日志和别名放入统一回归，发现跨工具行为不一致。

### 回归分组

```text
协议:
  ToolResult / Request / Spec / Validation / errors

运行时:
  Registry / Policy / Manager / dry_run / logger

文件:
  path / read / write / patch / copy / move / rename / delete / document

命令:
  command_tool / shell_command_tool / blocked / timeout / output

搜索:
  fake / Tavily mock / model_builtin mock / evidence

MCP:
  config / STDIO fake / discovery / dynamic spec / call / policy
```

### 安全矩阵

至少逐项验证：

```text
workspace 外读取/写入/删除
敏感文件读取/写入/删除
overwrite 无确认
patch 模糊匹配
目录/glob 删除
command 删除
shell 复杂语法
admin/提权请求
network 未授权
MCP 未授权/远程高风险
code_executor 默认关闭
模型伪造 confirmed
```

### 配置审计

检查：

```text
缺配置默认保守
错误配置不开放权限
provider secret 不落盘日志
ToolSpec enabled 与 handler 一致
alias 不冲突
MCP server disabled 不暴露动态工具
```

### 明确不做

```text
不因回归方便而跳过真实权限检查。
不把真实网络 provider 默认纳入普通测试。
不为通过测试删除安全边界。
```

### 验收命令

```powershell
python -m pytest tests -q
```

如果全仓测试中存在与 Tools 无关的历史失败，必须在本 Step 记录：

```text
失败文件
失败原因
是否由 Tools 改动引入
是否阻断 Tools V1
```

### 完成记录（2026-08-18）

修改文件：
```text
tests/test_tools_step42_regression.py
tests/test_patch_file_tool.py
tests/test_delete_file_tool.py
tests/test_shell_command_tool.py
src/tools/Tools层开发步骤与进度(5)-集成验收.md
```

完成内容：
```text
新增 Step 42 聚合回归测试，覆盖协议/运行时/文件/命令/搜索/MCP 的跨工具一致性审计。
固定默认 Registry/ToolManager 的 ToolSpec enabled、implemented、handler 与 alias 一致性；确认 code_executor 默认不进入模型可见 specs。
补齐安全矩阵验收：
  workspace 外读取/写入/删除均被 workspace_out_of_scope 拦截。
  敏感路径读取进入 confirmation_required，敏感写入/删除 hard block。
  overwrite 无可信 confirmation ticket 返回 confirmation_required。
  patch 模糊匹配、glob 删除、目录删除返回对应专用错误码。
  command 删除、shell 复杂语法、admin/提权、network 未授权走命令安全矩阵。
  MCP 未授权被 ToolPolicy 拦截，Streamable HTTP 仅配置可加载、不作为 V1 可执行传输。
  code_executor 默认 disabled。
  模型在 args 中伪造 confirmed=true 不会释放高风险动作。
补齐配置审计验收：
  缺配置默认保守，写/网络/命令/shell/MCP 权限默认关闭。
  错误配置下 ToolManager fallback 到保守默认，并保留 config_error。
  provider 明文 secret 被拒绝，api_key_env 仅保留环境变量名，不持久化真实值。
  JsonlToolLogger 只记录输入/输出摘要、hash 和元数据，不落完整 raw_output 或明文 secret。
  disabled MCP server 不注册动态 ToolSpec，也不暴露给模型 specs。
同步修正 3 个旧测试与 Step 40 确认流程后的正式语义：
  无 session capability 时 dry_run preview 允许生成确认预览；
  真实执行仍被 permission_denied / command_blocked 拦截；
  workspace 越界、敏感路径、blocked 风险仍不能通过 preview 或 confirmation 放行。
```

测试命令与结果：
```powershell
python -m pytest tests/test_tools_step42_regression.py -q
# 8 passed

python -m pytest tests/test_tool_result_v1.py tests/test_tool_call_protocol.py tests/test_tool_errors_v1.py tests/test_tool_spec_v1.py tests/test_tool_registry_v1.py tests/test_tool_registry_dynamic.py tests/test_tool_policy_v1.py tests/test_tool_manager_v1.py tests/test_tool_output_control.py tests/test_tool_preview_v1.py tests/test_tool_logging_v1.py tests/test_tool_config_v1.py tests/test_tools_step42_regression.py -q
# 103 passed

python -m pytest tests/test_tool_path_resolver.py tests/test_file_listing_tools.py tests/test_find_files_tool.py tests/test_read_file_tool.py tests/test_write_file_tool.py tests/test_patch_file_tool.py tests/test_file_mutation_tools.py tests/test_delete_file_tool.py tests/test_command_tool_argv.py tests/test_command_tool_v1.py tests/test_command_policy_v1.py tests/test_shell_command_tool.py tests/test_document_parser_v1.py tests/test_utility_tools_v1.py -q
# 118 passed

python -m pytest tests/test_web_search_protocol.py tests/test_web_search_normalization.py tests/test_web_search_routing.py tests/test_web_search_tool.py tests/test_web_search_v1_acceptance.py tests/test_tavily_search_provider.py tests/test_model_builtin_search_provider.py tests/test_mcp_config_v1.py tests/test_mcp_protocol_v1.py tests/test_mcp_stdio_client.py tests/test_mcp_discovery.py tests/test_mcp_tool_adapter.py tests/test_mcp_tool_gateway.py tests/test_mcp_policy.py tests/test_mcp_v1_acceptance.py -q
# 94 passed

python -m pytest tests -q
# 739 passed, 4 skipped
```

边界与遗留：
```text
本 Step 只做全量回归、安全矩阵和配置审计，不新增真实网络 provider 默认联调。
真实 Tavily / model_builtin / MCP STDIO 外部联调仍按环境变量显式开启策略执行。
command_tool 的 dry_run 仍是统一预览，不执行 subprocess；命令删除等危险矩阵由 command_policy 和真实执行前 handler 校验保证。
MCP Streamable HTTP 在 V1 仅保留配置与不可执行边界；真实执行仍只支持本地 STDIO。
当前工作树仍包含大量前序迁移期未跟踪/已修改文件，本 Step 未清理、未回退无关内容。
```

---

## Step 43：Analyzer、Planner、ReActExecutor、Models、Tools 端到端验收

**状态：已完成**

### 目标

验证工具层正式化后没有破坏已经完成的上游层和 Models 基础服务，并证明模型仍是执行决策大脑。

### 端到端场景

至少包含：

```text
1. 用户要求读取文件：
   Analyzer 识别阅读能力
   Planner 生成 PlanStep
   ReActExecutor 生成 tool ActionPacket
   read_file -> ToolResult -> Observation -> Checker -> 用户反馈

2. 用户要求局部修改文件：
   模型选择 patch_file
   preview/confirmation
   用户确认
   resume 后真实修改
   ExecutionEvent 反馈影响行数

3. 用户要求运行测试：
   command_tool argv 执行
   非零退出结构化失败
   Checker 决定 retry/fallback/final

4. 用户要求复杂管道：
   模型选择 shell_command_tool
   high risk confirmation
   不由 command_tool 猜测或自动升级

5. 用户要求联网搜索：
   web_search fake provider
   WebSearchData 进入 Observation
   后续 call_model 负责总结

6. 用户要求调用 MCP：
   动态 mcp.* ToolSpec
   ReActExecutor ActionPacket
   Gateway tools/call
   ToolResult -> Observation

7. 工具失败：
   Tools 只返回失败
   Checker/模型选择重试、替代工具、询问用户或结束
   Tools 不自动改执行路径
```

### Models 关联

重点确认：

```text
Analyzer/Planner/ReActExecutor 仍通过 Models V1 结构化调用。
model_builtin search 通过 Models 层，不绕过 ModelManager。
Tools 不重做 provider route/retry。
工具结果需要模型总结时由 ReActExecutor 发起独立 model action。
```

### 测试文件建议

```text
tests/test_analyzer_planner_react_executor_pipeline.py
tests/test_react_agent_with_react_executor.py
tests/test_tools_end_to_end_fake.py
```

### 明确不做

```text
不重新实现 Analyzer/Planner。
不让 Tools 参与计划生成。
不把旧顺序 Executor 当端到端兜底。
不要求真实 API key 才能通过默认验收。
```

### 验收命令

```powershell
python -m pytest tests/test_analyzer_planner_react_executor_pipeline.py tests/test_react_agent_with_react_executor.py tests/test_tools_end_to_end_fake.py -q
```

### 完成记录

```text
修改文件：
- tests/test_tools_end_to_end_fake.py
- src/tools/Tools层开发步骤与进度(5)-集成验收.md

测试命令：
- python -m pytest tests/test_tools_end_to_end_fake.py -q
- python -m pytest tests/test_analyzer_planner_react_executor_pipeline.py tests/test_react_agent_with_react_executor.py tests/test_tools_end_to_end_fake.py -q

测试结果：
- 6 passed
- 18 passed

边界：
- patch_file / command_tool / shell_command_tool 采用 executor 确认票据流验证，不绕过正式 ToolRuntime。
- web_search 断言使用 Observation 中的归一化 dict 视图。
- 这一轮不接入真实外部 API，只验证 fake / 本地路径的端到端链路。

遗留问题：
- Step 44 仍待开发。
```

---

## Step 44：Tools V1 文档、状态和最终收尾

**状态：已完成**

### 目标

完成代码、测试、文档和当前边界的最终一致性，形成下一阶段可继续开发的稳定基线。

### 必须更新

```text
src/tools/Tools层开发步骤与进度.md
src/tools/Tools层开发步骤与进度(1)-协议运行时.md
src/tools/Tools层开发步骤与进度(2)-文件与命令工具.md
src/tools/Tools层开发步骤与进度(3)-联网搜索.md
src/tools/Tools层开发步骤与进度(4)-MCP扩展工具.md
src/tools/Tools层开发步骤与进度(5)-集成验收.md
```

如实现过程中改变了设计，必须同时更新：

```text
Tools层设计决策汇总(0)-索引.md
对应 Tools层设计决策汇总分卷
必要时 Tools设计问题回答(2).txt 之后新增补充说明文档
```

### 最终清单

```text
所有正式工具返回 ToolResult。
ToolResult data schema 与工具匹配。
ToolSpec 是模型可见声明唯一来源。
ToolManager 统一经过 Registry/Policy。
run_tool 只是兼容入口。
ReActExecutor 不直接 shell。
ActionPacket 不被 Tools 直接消费。
Observation 由 ReActExecutor 生成。
ExecutionEvent 与 tools.log 分离。
high 风险支持 preview/confirmation/resume。
blocked 不能通过确认放行。
workspace 和敏感路径边界有效。
web_search 双 provider 统一 WebSearchData。
MCP V1 只真实支持 STDIO。
真实 provider/MCP 测试默认 skip。
旧 Executor 不作为自动 fallback。
```

### 最终验收命令

```powershell
python -m pytest tests -q
```

可选真实测试按环境变量显式开启：

```text
RUN_WEB_SEARCH_INTEGRATION_TESTS=true
RUN_MODEL_BUILTIN_SEARCH_TESTS=true
RUN_MCP_INTEGRATION_TESTS=true
```

### 完成标准

Tools V1 只有在以下条件都满足时才能标记“已完成”：

```text
Step 0-44 的必做项已实现或明确记录为不适用。
核心和安全测试通过。
跨层 fake 端到端测试通过。
真实外部测试策略可复现。
文档状态与代码真实状态一致。
没有“legacy 自动回退”“Tools 生成 Observation”“模型伪造结果”等架构误导。
```

### 完成记录

```text
修改文件：
- src/tools/Tools层开发步骤与进度.md
- src/tools/Tools层开发步骤与进度(5)-集成验收.md
- 开发进度.md

测试命令：
- python -m pytest tests/test_tools_end_to_end_fake.py -q
- python -m pytest tests/test_analyzer_planner_react_executor_pipeline.py tests/test_react_agent_with_react_executor.py tests/test_tools_end_to_end_fake.py -q
- python -m pytest tests -q

测试结果：
- 6 passed
- 18 passed
- 745 passed, 4 skipped

边界：
- 只做 Tools V1 收尾和总览状态对齐，不扩张新能力。
- 真实外部 provider / MCP 联调仍保留显式环境变量开关。

遗留问题：
- 无。
```

### V1 之后

按照设计决策文档进入 V2/V3 后置能力，不在 Tools V1 收尾时顺手扩张：

```text
完整权限 UI
管理员提权
跨工作区授权
复杂沙箱
fetch_url/read_web_page/browser automation
搜索缓存和 RAG 入库
MCP Streamable HTTP/Resources/Prompts/Sampling
MCP 插件市场
异步跨进程工具状态
```
