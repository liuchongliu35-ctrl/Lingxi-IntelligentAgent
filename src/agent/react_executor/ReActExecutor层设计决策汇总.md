# ReActExecutor 层设计决策汇总

本文档汇总 `ReActExecutor设计问题回答(1).txt`、`ReActExecutor设计问题回答(2).txt`、`ReActExecutor设计问题回答(3).txt` 中已经确认的 ReActExecutor 层设计决策。后续 ReActExecutor V1 开发以本文档作为需求基线。

> 状态说明：第 1-22 节是设计基线，第 23 节是 Step 24 的历史验收快照。Step 47 新增的第 24 节描述当前实现契约，并优先于历史快照；Analyzer / Planner 的职责和接口没有在第二阶段重新设计。

## 1. ReActExecutor 在整体 Agent 中的位置

当前 Agent 采用“任务型 Agent 分层 + ReAct 执行循环”的混合架构：

```text
User Input
  -> Analyzer
      理解意图、参数、风险、复杂度和执行策略
  -> Planner
      生成 TaskPlan / TaskUnit / PlanStep 初始结构化计划
  -> ReActExecutor
      以 Planner 计划为初始路线，执行 Thought -> Action -> Observation 循环
      使用 Checker 做工程兜底
  -> Response / Events
```

ReActExecutor 不重新承担 Analyzer 的自然语言理解职责，也不重新生成完整初始计划。它消费 Planner 的结构化计划，并在执行过程中根据 Observation 调整具体动作。

标准 ReAct 是：

```text
Thought -> Action -> Observation -> Thought -> ...
```

本项目工程化拆分为：

```text
Thought      ~= Reasoning
Action       ~= Decision + Tool/Model
Observation  = Observation
Checker      = 工程增强节点，负责兜底判断
```

也就是说，ReActExecutor 不是顺序执行器，而是“Planner 引导的 ReAct 执行引擎”。Planner 给初始路线、约束和边界；ReActExecutor 根据实际执行结果继续推理、选择动作、重试、fallback、询问用户或请求重规划。

## 2. 职责边界

ReActExecutor 负责：

- 消费 `TaskPlan / TaskUnit / PlanStep`。
- 维护执行状态。
- 调用模型生成结构化 `ActionPacket`。
- 校验模型输出 schema。
- 调用工具或模型动作。
- 生成和保存 Observation。
- 处理 `input_from / output_key` 的中间结果传递。
- 执行安全检查和确认暂停。
- 按失败策略重试、fallback、跳过或停止。
- 通过 Checker 判断是否继续、完成、失败或请求重规划。
- 输出用户可见事件流和最终结果。
- 写入 ReActExecutor 日志。

ReActExecutor 不负责：

- 重新识别用户意图。
- 重新做完整初始规划。
- 自由生成大范围新计划并直接执行。
- 直接执行 shell 命令。
- 绕过 Tool 层安全策略。
- 长任务后台调度。
- 并行 TaskUnit 执行。
- 完整跨对话断点续跑。

## 3. 核心执行粒度

Planner V1 已定义：

```text
TaskPlan
  -> TaskUnit
      -> PlanStep
```

含义：

- `TaskPlan`：一次用户请求对应的整体执行计划。
- `TaskUnit`：计划中的子任务单元，可能对应一个 intent，也可能由多个强依赖 intent 合成。
- `PlanStep`：子任务中的具体步骤，是 ReActExecutor 执行时消费的最小计划单位。

ReActExecutor V1 采用：

```text
Plan-guided Step ReAct
```

规则：

- 默认按 `task_units` 顺序执行。
- 每个 TaskUnit 内按 `step_ids` 找到对应 PlanStep。
- 如果没有 `task_units`，兼容旧的 `plan.steps` 扁平顺序。
- V1 不做并行，哪怕多个步骤没有依赖也先顺序执行。
- 当前 TaskUnit 内允许局部调整、重试、fallback 或跳过。
- 如果需要大范围改计划，输出 `request_replan`，不自动重写整个 TaskPlan。

## 4. 依赖字段定义

### 4.1 depends_on

`depends_on` 表示执行顺序依赖。

```text
step_2.depends_on = ["step_1"]
```

含义是：`step_1` 没有完成前，`step_2` 不能开始。

`depends_on` 只管“能不能执行”，不负责传递数据。

### 4.2 input_from

`input_from` 表示数据输入来源。

```text
step_2.input_from = ["step_1"]
```

含义是：`step_2` 需要使用 `step_1` 的 Observation 结果作为输入。

`input_from` 管“当前步骤要消费哪些前序结果”。

### 4.3 依赖失败

如果前置依赖失败：

- 当前步骤默认不能执行。
- 如果当前步骤无关该失败结果，可以继续。
- 如果有 fallback 成功，可以继续。
- 如果当前步骤标记 `on_failure=skip_optional`，可以跳过并继续无依赖步骤。

如果某一步缺少输入参数：

- 当前步骤标记为 `failed` 或 `skipped`。
- 后续不依赖它的步骤可以继续。
- 后续依赖它的步骤不能继续，除非 fallback 成功。

## 5. 执行状态模型

ReActExecutor V1 需要三层状态。

### 5.1 Execution 状态

表示一次计划执行的整体状态：

```text
pending
running
waiting_user
completed
failed
partial_failed
blocked
cancelled
request_replan
```

### 5.2 TaskUnit 状态

表示一个子任务单元状态：

```text
pending
running
waiting_user
completed
failed
skipped
blocked
cancelled
```

### 5.3 PlanStep 状态

表示一个步骤状态：

```text
pending
running
waiting_user
completed
failed
skipped
blocked
cancelled
retrying
fallback_used
```

状态记录用于：

- 日志追踪。
- UI/CLI 展示。
- 最终总结。
- 下一轮对话理解上一次执行进度。

V1 不做完整“单次对话中断后继续执行”的断点续跑，但要记录执行进度。后续用户说“继续上次没完成的任务”时，模型可以基于记录判断上次做到哪里。

## 6. 确认暂停

用户确认和断点续跑是两个概念。

确认暂停是正常执行流程的一部分：

```text
执行到危险步骤
  -> 暂停
  -> 询问用户
  -> 用户允许则继续当前步骤
  -> 用户拒绝则取消当前步骤
```

同步确认场景可以只在当前执行上下文中保存待确认动作。异步确认或多轮确认需要保存 `pending_confirmation`。

建议字段：

```text
execution_id
plan_id
task_id
step_id
confirmation_type
confirmation_message
pending_action
expires_at
```

用户拒绝确认时：

- 当前步骤标记 `cancelled` 或 `blocked`。
- 依赖该步骤结果的后续步骤跳过。
- 无依赖的后续步骤可以继续。
- 最终回答必须说明哪些步骤因为用户拒绝而未执行。

## 7. 模型交互协议：ActionPacket

ReActExecutor 和大模型之间使用结构化协议交互。模型不能返回一大段混合文本让执行器猜。

`ActionPacket` 是模型给执行器下发的一次结构化指令。

V1 至少包含：

```text
packet_id
thought_summary
user_visible_message
action_type
action_target
action_args
expected_observation
confidence
requires_confirmation
safety_notes
fallback_plan
request_replan_reason
final_answer
```

字段含义：

- `packet_id`：本轮模型动作 id。
- `thought_summary`：模型对当前状态的推理摘要。
- `user_visible_message`：可以流式展示给用户的进度说明。
- `action_type`：动作类型。
- `action_target`：动作目标，例如工具名、模型节点、命令工具。
- `action_args`：执行动作所需结构化参数。
- `expected_observation`：模型预期执行后得到什么。
- `confidence`：模型对当前动作的置信度。
- `requires_confirmation`：是否需要用户确认。
- `safety_notes`：安全说明。
- `fallback_plan`：失败后建议的备选方案。
- `request_replan_reason`：请求重规划的原因。
- `final_answer`：任务完成时给用户的最终回答。

结构化短字段完整保存。长文本字段保存到会话记录，日志只记录摘要、长度和关联 id。

## 8. Action 类型

Action 类型先固定为枚举，模型必须从枚举中选择。模型不能在运行时自由发明 Action 类型。如果枚举不够，输出 `request_replan` 或记录 `unsupported_action_suggestion`。

V1 Action 枚举使用“规范值 + 兼容别名”的方式。模型 prompt 和 JSON Schema 中优先只暴露规范值，解析器内部可以兼容旧别名并归一化。

规范 Action 值：

```text
call_tool
call_model
ask_user
retry_step
fallback_to_model
fallback_to_tool
skip_step
finish
fail
request_replan
blocked
cancel
```

兼容别名：

```text
retry -> retry_step
stop_success -> finish
stop_failed -> fail
```

说明：

- `call_tool`：调用 Tool 层工具。
- `call_model`：调用模型生成中间步骤结果。
- `ask_user`：询问用户补参数或确认。
- `retry_step`：重试当前动作或步骤。
- `fallback_to_model`：工具失败后改用模型完成。
- `fallback_to_tool`：原工具失败后改用备用工具，优先 ToolRegistry 中声明的 fallback。
- `skip_step`：跳过当前步骤。
- `finish`：任务成功结束。
- `fail`：任务失败结束。
- `request_replan`：当前计划偏差过大，请求 Planner 或更高层重新规划。
- `blocked`：安全策略阻断。
- `cancel`：用户取消或执行器主动取消。

`call_model` 和 `finish` 必须区分：

- `call_model` 是中间模型调用。
- `finish` 是结束任务并生成最终回答。

`ask_user` 需要区分：

```text
ask_type = missing_info | confirmation | choice | permission | clarification
```

## 9. 模型往返流程

每轮 ReAct 执行流程：

```text
1. 构造模型输入
2. 模型返回 ActionPacket
3. 校验 ActionPacket schema
4. 根据 action_type 执行动作
5. 生成 ObservationPacket
6. Checker 判断下一步
7. 将 Observation 放回下一轮模型输入
```

模型输入至少包含：

```text
用户输入
Analyzer 摘要
TaskPlan 摘要
当前 TaskUnit
当前 PlanStep
前置 Observation 摘要
上一轮 ActionPacket 摘要
历史必要上下文
安全约束
可用工具和 ToolSpec
Action 枚举
```

上下文必须控制长度。长历史不原样塞回模型，而是转换为结构化关键信息，避免遗漏关键状态。

如果模型输出不符合 schema：

- 最多重试 5 次。
- 重试 prompt 需要包含 schema 错误原因。
- 仍失败时进入 `fallback_to_model`、`ask_user`、`fail` 或 `request_replan`。

ActionPacket 还需要按 action 类型做字段级约束：

- `call_tool`：`action_target` 必须是 ToolRegistry 中存在的工具名，`action_args` 必须符合 ToolSpec。
- `call_model`：`action_args` 至少说明生成目标、输入来源和输出要求。
- `ask_user`：`action_args.ask_type` 必须是 `missing_info | confirmation | choice | permission | clarification` 之一，并提供问题文本。
- `retry_step`：必须指向当前 step 或最近失败的 action，且不能超过重试上限。
- `fallback_to_model` / `fallback_to_tool`：必须说明 fallback 原因；`fallback_to_tool` 必须提供存在的备用工具。
- `finish`：必须填写 `final_answer`，不能再包含待执行工具动作。
- `fail`：必须填写失败原因，最终结果中要保留已完成/失败/跳过步骤。
- `request_replan`：必须填写 `request_replan_reason`。
- `blocked` / `cancel`：必须填写可给用户看的原因。

## 10. Observation 与 ObservationStore

Observation 由执行器统一生成，不能由模型编造。

V1 新增内存版 `ObservationStore`。

建议 `ObservationPacket` 字段：

```text
observation_id
execution_id
plan_id
task_id
step_id
packet_id
attempt
action_type
action_target
tool_name
input_args
success
data
message
error
code
raw_observation
model_consumable_observation
started_at
finished_at
duration_ms
fallback_used
fallback_type
checker_result
visible_to_user
```

说明：

- `raw_observation`：真实工具或模型原始结果。
- `model_consumable_observation`：整理后给下一轮模型消费的结构化信息。它不是简单短摘要，必须尽量保留关键信息。
- `visible_to_user`：是否允许展示给用户。
- `fallback_used/fallback_type`：记录是否使用 fallback。

工具输入和输出记录时必须预留脱敏函数。明显敏感字段如 `api_key`、`token`、`password`、`secret` 默认脱敏。

V1 中 ObservationStore 只保存在内存。日志记录 Observation 摘要和关键结构化字段。长期持久化放到 V2。

## 11. Checker 设计

Checker 是工程增强节点，用来避免完全依赖模型自由判断。

每轮 Action/Observation 后都需要 Checker。

Checker 判断：

```text
是否成功
输出是否为空
是否满足 expected_observation / expected_output
是否需要重试
是否需要 fallback
是否需要询问用户
是否可以进入下一步
是否需要 request_replan
是否达到终止条件
是否违反安全策略
是否超过最大循环次数
```

V1 使用“规则底线 + LLM 辅助”：

- 规则 Checker 负责硬约束：失败码、空输出、超时、安全策略、依赖缺失、最大循环次数。
- LLM Checker 负责语义判断：Observation 是否满足预期、是否需要继续查找、是否需要 fallback、是否需要 request_replan。

文件写入、文件修改、命令执行这类副作用需要基础验证：

- 文件写入成功后检查文件是否存在。
- 命令执行成功后检查 exit code。
- 更复杂的结果验证后续交给 Preview/Check 工具。

## 12. 失败处理与重试

Planner 给默认 `on_failure`，ReActExecutor 根据实际 `ToolResult.code` 和 Observation 动态调整。

V1 支持：

```text
stop
retry
fallback_to_model
fallback_to_tool
ask_user
skip_optional
fail
request_replan
```

允许重试的典型失败：

```text
timeout
temporary_network_error
rate_limited
model_call_failed
tool_transient_error
schema_invalid
```

不建议重试的典型失败：

```text
permission_denied
blocked_by_policy
missing_required_argument
file_not_found
dangerous_operation
tool_not_found_after_repair
```

重试策略：

- V1 使用指数退避。
- 模型结构化输出校验最多重试 5 次。
- 工具步骤使用 `step.max_retries`，默认来自 Planner 配置。

任务失败时也需要给用户完整交代：

- 已完成哪些步骤。
- 失败在哪一步。
- 失败原因。
- 是否可以重试。
- 用户需要补充什么。
- 后续可以如何继续。

## 13. ToolSpec / ToolRegistry

需要尽快引入轻量版 `ToolSpec / ToolRegistry`，因为模型必须知道每个工具的调用方式。

V1 ToolSpec 至少包含：

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
```

ReActExecutor 调用工具前必须按 ToolSpec 校验参数：

- 缺少必填参数时不调用工具。
- 参数类型不符合时返回 schema 错误。
- 模型选择不存在的工具时，让模型重试。
- 重试后仍不存在，进入 `fallback_to_model` 或 `request_replan`。

ReActExecutor 统一通过 ToolManager 或未来 ToolRegistry 调工具，不直接调用具体工具类。

## 14. 命令行工具与安全边界

V1 支持命令行工具，但必须作为 Tool 层能力，例如：

```text
command_tool
shell_tool
terminal_tool
```

ReActExecutor 不直接执行 shell，只调用 Tool 层命令工具。

命令来源：

- Planner 如果规划了命令行步骤，可以使用。
- 工具失败后，模型可以通过 ActionPacket 请求命令行 fallback。
- 模型生成命令必须使用结构化 `CommandAction`。

CommandAction 至少包含：

```text
command
cwd
purpose
risk_level
requires_confirmation
expected_result
timeout_seconds
```

建议扩展：

```text
shell
env_policy
network_required
writes_files
target_paths
destructive_risk
approval_scope
```

命令执行前必须安全检查：

```text
工作区限制
危险命令关键词
删除/覆盖/权限修改检测
系统目录检测
网络安装类命令检测
下载执行检测
超时限制
输出长度限制
确认策略
```

用户可以配置命令权限策略：

```text
ask
low_risk_auto
session
always
```

其中 `ask` 对应每次询问，等价于早期讨论中的 `once`；`low_risk_auto` 只允许低风险只读/测试/诊断命令自动执行；`session` 和 `always` 也只能放宽低风险或已确认范围内的命令。

但工程底线不能交给模型或用户随意绕过：

- 工作区外修改默认禁止。
- 系统目录修改默认 block。
- 权限提升、格式化磁盘、危险删除默认 block。
- 网络下载并执行脚本默认 block 或强确认。

低风险命令主要是只读、测试、诊断类，例如：

```text
dir
Get-ChildItem
rg
python -B -m unittest
pytest
```

涉及安装、删除、移动、系统路径、权限修改、网络下载的不视为低风险。

## 15. 安全策略

ReActExecutor 是最后一道执行防线。

必须尊重：

```text
plan.can_execute=False
plan.plan_validation_status=invalid
Analyzer action_policy=block
Planner confirm / blocked / missing_tools
```

执行前再次检查：

- 是否删除文件。
- 是否执行代码。
- 是否执行 shell。
- 是否修改工作区外文件。
- 是否访问敏感系统路径。
- 是否覆盖重要文件。
- 是否使用危险命令。

写文件策略：

- V1 默认允许工作区内普通写文件。
- 用户明确禁止编辑的文件只能输出修改建议，不直接写入。
- 覆盖文件、敏感路径、工作区外路径需要确认或 block。
- 工作区父级目录外只允许读取，不允许修改。

删除文件、执行代码、shell 命令必须走步骤级确认。即使 Analyzer/Planner 漏掉，ReActExecutor 也必须拦截。

## 16. 用户可见事件流

为了支持类似 Codex 的交互体验，ReActExecutor 需要输出事件流。

事件是执行过程对外输出的进度记录，不等于日志。

- 事件：给用户看，简洁、自然、可展开。
- 日志：给开发者排查，结构化、详细、可包含内部字段。

V1 事件类型建议：

```text
message_delta
progress_message
thought_visible
action_selected
tool_started
tool_finished
file_edited
command_started
command_finished
step_started
step_completed
step_failed
confirmation_requested
observation_created
request_replan
system_notice
final_answer
```

`thought_visible` 不是隐藏思维链。它只能表示经过整理的用户可见进度说明，例如“正在检查计划依赖”或“工具结果不满足预期，准备重试”。内部长推理、完整 prompt、敏感参数和调试堆栈不能通过该事件展示。

为了支持类似 Codex 的“说明和执行记录交替出现”的体验，ReActExecutor 需要维护一条用户可见的执行时间线。时间线由事件流渲染，不从日志里临时拼接。

典型映射：

```text
“我会先核对接口...” -> progress_message 或 message_delta
“Ran commands” -> command_started + command_finished 渲染后的折叠摘要
“Edited xxx.md” -> file_edited
“Context automatically compacted” -> system_notice
“工具执行结果/Observation 摘要” -> tool_finished 或 observation_created
“最终总结” -> final_answer
```

这条时间线和内部日志不同：

- 时间线用于给用户理解执行进度，可以跨轮摘要给模型消费。
- 日志用于开发排查，字段更细，但默认不展示完整 prompt、敏感参数和长输出。
- ObservationStore 保存真实执行结果，事件流保存用户可见过程。

事件通用字段：

```text
event_id
execution_id
plan_id
task_id
step_id
type
timestamp
visible_to_user
message
payload
```

文件修改事件 V1 展示：

- 文件路径。
- 大致增删行数。
- patch/diff 摘要。
- 完整 diff 可展开或进入日志。

命令执行事件展示：

```text
command
cwd
exit_code
stdout_summary
stderr_summary
duration_ms
```

敏感环境变量不展示。

## 17. ExecutionResult

ReActExecutor V1 输出结构化结果。

建议字段：

```text
execution_id
plan_id
source_trace_id
status
success
output
summary
task_statuses
step_statuses
observations
events
failed_step_id
error_code
requires_user_input
user_input_request
pending_confirmation
request_replan
replan_reason
```

部分成功、后续失败时，不能只返回最后一个错误。最终回答需要说明：

- 已完成哪些步骤。
- 哪些步骤失败或跳过。
- 失败原因。
- 是否用了 fallback。
- 用户后续可以怎么继续。

成功完成多步骤任务时，需要总结关键 Observation 和最终结果。

## 18. 日志与追踪

ReActExecutor 需要独立日志：

```text
logs/react_executor.log
```

日志格式建议 JSONL。

每轮记录：

```text
timestamp
execution_id
source_trace_id
plan_id
task_id
step_id
packet_id
action_type
action_target
tool_name
attempt
schema_valid
repair_attempts
success
error
code
duration_ms
checker_result
fallback_used
request_replan
```

一次执行 trace 应串联：

```text
Analyzer trace_id
  -> Planner plan_id
      -> ReActExecutor execution_id
          -> TaskUnit
              -> PlanStep
                  -> ActionPacket
                      -> ObservationPacket
```

日志不默认记录完整 prompt，只记录：

- prompt 摘要。
- prompt 长度。
- 模型返回结构化字段。
- 长文本摘要和长度。
- schema 校验结果。

开发模式可以配置打开完整 prompt 日志，但默认关闭。

## 19. V1 完成标准

ReActExecutor V1 达标需要满足：

1. 使用 `ActionPacket` 实现模型和执行器的结构化交互。
2. 能消费 Planner V1 的 `TaskPlan / TaskUnit / PlanStep`。
3. 能按 TaskUnit 和 PlanStep 作为初始路线执行。
4. 每轮执行走 `Thought -> Action -> Observation`，并通过 Checker 工程兜底。
5. 支持 `ObservationStore` 内存版。
6. 支持工具调用、模型调用、用户询问、确认暂停。
7. 支持 `input_from / output_key` 的中间结果传递。
8. 支持 retry、fallback_to_model、fallback_to_tool、skip、request_replan。
9. 支持命令行工具的结构化规划和确认，但真实执行必须走 Tool 层安全工具。
10. 支持轻量 ToolSpec / ToolRegistry。
11. 尊重 `can_execute`、`plan_validation_status` 和所有安全策略。
12. 输出用户可见事件流。
13. 输出结构化 `ExecutionResult`。
14. 写入 `logs/react_executor.log`。
15. 有单元测试、fixture 回归和少量端到端测试。

## 20. V1 暂不做

V1 暂不做：

- 不做完整跨对话断点续跑，只记录进度。
- 不做自动大范围重规划，只输出 `request_replan`。
- 不做并行 TaskUnit。
- 不做长期持久化 ObservationStore。
- 不做复杂权限系统，只做基础确认、用户配置和工作区限制。
- 不做复杂 UI，只输出事件结构，CLI 可简单展示。
- 不做多 Agent 协作。
- 不做完整项目自动开发全流程。

## 21. 测试要求

ReActExecutor V1 需要 fixture 回归样例，建议 20-40 条。

优先覆盖：

```text
calculate 单步工具成功
read_file -> summarize
read_file -> extract -> write_file
search -> summarize -> write_file
model/respond 步骤
工具失败 -> retry -> success
工具失败 -> fallback_to_model
工具失败 -> fallback_to_tool
命令行步骤 -> confirmation_requested
用户拒绝确认 -> 依赖步骤跳过
plan.can_execute=False -> 不执行
plan_validation_status=invalid -> 不执行
input_from 缺失 -> 结构化失败
ActionPacket schema invalid -> repair retry
模型请求 request_replan
文件写入后基础校验
block 策略不执行
部分步骤失败但无依赖步骤继续
```

还需要少量端到端测试：

```text
Analyzer -> Planner -> ReActExecutor
```

优先 5-10 条主链路用例，ReActExecutor 单元测试和 fixture 先做扎实。

## 22. 面试讲法

可以这样讲 ReActExecutor：

> ReActExecutor 是计划执行和观察检查层，内部采用 ReAct 执行循环。它消费 Planner 给出的 TaskPlan、TaskUnit 和 PlanStep，但不会死板地按步骤跑，而是让模型通过结构化 ActionPacket 进行 Thought 和 Action 决策。执行器调用工具或模型后生成 Observation，再由 Checker 判断是否继续、重试、fallback、询问用户或请求重规划。这样既保留大模型作为“大脑”的灵活性，又通过 ToolSpec、安全校验、ObservationStore、事件流和结构化日志保证工程上的可控、可测试和可追踪。

## 23. Step 24 回写：V1 实现验收状态

截至 Step 24，当前代码已经完成 ReActExecutor V1 的工程基础层和主链路接入，但完整模型驱动 `Thought -> Action -> Observation` 主循环仍是后续阶段工作。

已落地模块：

```text
src/agent/react_executor_protocol.py      # ActionPacket / ObservationPacket / ExecutionResult / PendingConfirmation
src/agent/react_executor_prompt.py        # 模型 Prompt 模板
src/agent/react_executor_observation.py   # 内存版 ObservationStore
src/agent/react_executor_events.py        # 用户可见 EventStream
src/tools/registry.py                     # ToolSpec / ToolRegistry
src/tools/command_tool.py                 # 命令工具层封装
src/agent/react_executor.py               # ReActExecutor 主类、plan precheck、dispatcher、action handlers
src/agent/react_executor_checker.py       # RuleChecker / LLMChecker / ReActChecker
src/agent/react_executor_retry.py         # RetryPolicy
src/agent/react_executor_fallback.py      # FallbackPolicy
src/agent/react_executor_safety.py        # SafetyPolicy
src/agent/react_executor_logging.py       # JSONL 日志
src/agent/react_executor_result.py        # ExecutionResult 汇总
src/agent/react_agent.py                  # 默认主链路接入 ReActExecutor；旧 Executor 仅为历史诊断参考，legacy 仅作显式兼容开关
```

已通过测试覆盖：

```text
tests/test_react_executor_protocol.py
tests/test_react_executor_action_packet_schema.py
tests/test_react_executor_prompt.py
tests/test_react_executor_observation.py
tests/test_react_executor_events.py
tests/test_tool_registry_v1.py
tests/test_react_executor_core.py
tests/test_react_executor_plan_precheck.py
tests/test_react_executor_actions.py
tests/test_react_executor_tool_action.py
tests/test_react_executor_model_action.py
tests/test_react_executor_confirmation.py
tests/test_react_executor_command_action.py
tests/test_react_executor_checker.py
tests/test_react_executor_retry.py
tests/test_react_executor_fallback.py
tests/test_react_executor_safety.py
tests/test_react_executor_logging.py
tests/test_react_executor_result.py
tests/test_react_executor_v1.py
tests/test_analyzer_planner_react_executor_pipeline.py
tests/test_react_agent_with_react_executor.py
```

当前已满足：

- `ActionPacket` 结构化协议、JSON Schema、解析、校验可用。
- `ObservationPacket / ObservationStore` 可保存真实执行结果和模型可消费摘要。
- `EventStream` 可输出用户可见执行时间线，并与日志分离。
- `ToolSpec / ToolRegistry` 可描述工具参数、风险、确认、workspace scope 和 fallback。
- `call_tool / call_model / ask_user / command / retry / fallback / finish / fail / request_replan / blocked / cancel` 等 dispatcher 级 action 可执行或返回结构化 Observation。
- `Checker` 可对 Observation 做成功、空输出、失败分类、retry/fallback/request_replan/ask_user/终止判断。
- `SafetyPolicy` 已接入 plan precheck 和 action dispatcher。
- `ReActExecutorLogger` 已按 JSONL 写入执行、action、observation、checker、retry、fallback、safety、result 等记录。
- `ExecutionResultBuilder` 已能汇总 Observation、Event、RuntimeState、失败原因、fallback、waiting_user、request_replan 和下一步建议。
- `ReactAgent.run()` 默认使用 ReActExecutor；旧顺序 `Executor` 不再作为正式 fallback。如果代码中仍存在 `executor_type="legacy"` 或 `EXECUTOR_TYPE=legacy`，也只能视为历史兼容/迁移开关，不能作为新链路失败后的自动回退策略。
- Tools V1 只服务 ReActExecutor 的结构化链路：`ActionPacket -> ToolRegistry -> ToolManager/ToolRuntime -> ToolResult -> Observation`。短期保留的 `ToolManager.run_tool(...)` 方法名只是迁移期调用入口，不代表旧顺序 Executor 的工具协议继续作为第二套正式运行时。
- fixture 回归和 Analyzer -> Planner -> ReActExecutor 端到端串联测试通过。

当前未满足目标态：

- `ReActExecutor.execute()` 对普通可执行计划仍使用 skeleton traversal，不会自动调用模型生成下一轮 `ActionPacket`。
- 完整 `Thought -> Action -> Observation` 循环尚未接入主执行路径。
- 模型输出非法 ActionPacket 后的自动 repair 循环尚未接入主执行路径。
- chat 模式当前仍返回 `chat_mode_not_implemented` 的骨架边界，而不是自动执行 `call_model`。
- `ReactAgent.run()` 仍只返回 `execution.output`，暂不直接暴露结构化 `ExecutionResult/events/observations`。

因此，当前 Step 24 的验收结论是：

```text
ReActExecutor V1 工程基础层完成，默认主链路已接入 ReActExecutor。
完整模型驱动 ReAct 主循环和 ActionPacket repair 主路径留作下一阶段。
```

---

## 24. Step 47 回写：第二阶段当前实现契约

状态：已完成（2026-08-05）

Step 25-46 已将上面的 Step 24 历史边界推进为模型驱动的 Planner-guided ReAct 主循环。以下内容是当前代码、测试和公开 API 的统一说明。

### 24.1 默认入口与主循环

`ReActExecutor.execute()` 的默认路径为：

```text
create ReActExecutionContext
  -> subscribe optional event callback
  -> execution_started log + progress event
  -> plan precheck / empty-plan short-circuit
  -> _execute_react_loop
       -> TaskUnit 顺序循环
            -> PlanStep 顺序循环
                 -> start ReActTurnState
                 -> build model decision prompt
                 -> model.generate
                 -> parse / validate / repair ActionPacket
                 -> dispatch_action
                 -> generate real ObservationPacket
                 -> ObservationStore
                 -> Checker
                 -> continue / step_completed / retry / fallback
                    / ask_user / request_replan / finish / fail
  -> final_answer event + ExecutionResult
  -> unsubscribe callback + execution_finished log
```

主循环的固定边界如下：

- Planner 仍提供 `TaskPlan / TaskUnit / PlanStep` 初始路线；ReActExecutor 不重新承担 Analyzer 的意图识别和 Planner 的完整初始规划。
- TaskUnit 和 PlanStep 按计划顺序执行；依赖通过 `depends_on`、`input_from`、`output_key` 传递和检查。
- 已完成、已跳过、已取消、已失败或已阻断的 step 在确认恢复后不会被重复执行。
- `max_execution_turns` 和 `max_step_turns` 是主循环硬上限，达到上限后停止继续产生模型或工具轮次。
- `_traverse_plan_skeleton()` 只保留为显式 legacy diagnostic traversal，不是 `execute()` 的默认路径。

### 24.2 ActionPacket 生成与 repair

每个 ReAct turn 都要求模型返回一个结构化 `ActionPacket`：

1. Prompt 提供当前 Analyzer 摘要、Planner 计划、当前 TaskUnit / PlanStep、可用工具、最近 Observation、事件摘要、Turn 状态和安全约束。
2. 执行器先检查模型输出是否为单个 JSON 对象，再执行 ActionPacket parse、schema 校验和当前 step / tool / retry 约束校验。
3. 非法输出进入 repair prompt；repair 仍要求只返回一个严格 JSON 对象。
4. repair 次数受 `max_action_packet_repair_attempts` 限制。
5. repair 耗尽后生成 `action_packet_invalid` 的失败 Observation，交给 Checker 收束；不会猜测动作，也不会调用 ToolManager。

Checker 生成的 retry/fallback 也必须重建为内部 `ActionPacket`，再经过统一 dispatcher、ToolSpec、SafetyPolicy、confirmation 和 Observation 流程。

### 24.3 Observation 与 Checker 转移

Observation 只能由执行器根据真实结果生成：

- Tool action 的结果来自 ToolManager / ToolResult。
- Model action 的结果来自 ModelManager 的模型内容。
- User / confirmation / control action 的结果来自执行器状态和用户响应。
- Observation 进入 ObservationStore，并同时更新 step runtime、事件流和 JSONL 日志。

Checker 是每轮 Observation 后的工程决策源：

```text
continue
  -> 当前 step 继续下一轮模型决策
step_completed
  -> 完成当前 step，进入下一个计划 step
retry
  -> 内部 retry_step ActionPacket，经 dispatcher 重放原动作
fallback_to_model / fallback_to_tool
  -> 内部 fallback ActionPacket，经对应 handler 和安全链路执行
ask_user
  -> waiting_user，保留用户输入或 confirmation 状态
request_replan
  -> 终止当前执行并暴露 request_replan / reason
finish / fail / blocked / cancel
  -> 收束 execution，并按终止语义处理后续 step
```

安全阻断、非法动作、工具不存在和缺少输入引用都在实际动作执行前或 Checker 收束阶段被处理，不允许通过异常路径绕过 Tool 层。

### 24.4 chat / model-only 行为

当 Planner 输出 `mode="chat"` 的计划时：

- Prompt 明确 `tool_calls_allowed=false`，可用工具集合为空。
- 模型仍通过 `ActionPacket` 选择 `call_model` 或 `finish`，中间模型结果形成真实 Observation。
- chat 路径不调用 ToolManager，不再返回 `chat_mode_not_implemented`。
- `clarify`、`confirm`、`block` 等 Analyzer / Planner 特殊策略仍由 precheck 短路，避免误进入普通工具执行路径。

### 24.5 事件流、日志和结构化 API

用户事件和开发日志是两条独立通道：

- `EventStream` 保存结构化 `ExecutionEvent`，支持可见事件、内部事件、timeline 分组、顺序校验和同步订阅。
- `EventStream.subscribe(...)` 在事件 append 后立即通知订阅者，并支持 `visible_only=True`。
- `ReActExecutor.execute_stream(...)` 是同步 generator：按事件产生顺序 yield，最终 `ExecutionResult` 通过 generator return 返回。
- `ReactAgent.run_stream(...)` 默认只向上层 yield 用户可见事件；`include_internal=True` 时才允许上层接收内部事件。
- `final_answer` 是用户可见主路径的终点，事件时间线校验会拒绝关键执行事件出现在它之后。
- `ReActExecutorLogger` 以 JSONL 记录 `execution / prompt / model output / repair / action / observation / checker / transition / retry / fallback / safety / result`，日志不会进入用户事件流或短期记忆。

Agent 层公开三种使用方式：

```text
ReactAgent.run(user_input)
  -> 兼容的字符串最终回答

ReactAgent.run_with_result(user_input)
  -> 结构化 ExecutionResult

ReactAgent.run_stream(user_input)
  -> 顺序事件流 + generator return 的 ExecutionResult
```

`ExecutionResult` 暴露 `status / success / output / summary / task_statuses / step_statuses / observations / events / pending_confirmation / request_replan` 等字段。确认暂停后，可通过 ReActExecutor 的同一进程 `resume_after_confirmation(...)` 继续执行。

### 24.6 兼容边界与未实现的 V2/V3 能力

当前仍保持的工程边界：

- 旧 `Executor` 不作为正式回退路径；如保留文件或诊断入口，仅用于历史参考、测试迁移或后续清理。`legacy` 仅表示显式兼容切换，不参与 Tools V1 正式验收。
- Tools V1 不对接旧顺序 Executor，也不保留旧 ToolManager 旧执行逻辑作为第二套正式工具运行时。后续工具层应收敛到 `ToolCallRequest / ToolRegistry / ToolPolicy / ToolResult` 这类结构化协议；`ToolManager.run_tool(...)` 可以短期作为兼容入口，但内部必须逐步迁移到新协议。
- ReActExecutor 不直接执行 shell；命令必须生成结构化 command ActionPacket，并经 Tool 层、安全检查和确认后执行。
- ObservationStore 仍是内存版；没有跨进程的持久化执行状态和断点续跑。
- `execute_stream` / `run_stream` 当前是同步、可测试的事件接口，不提供异步传输、背压或断线重连。
- 不实现并行 TaskUnit 调度。
- 不自动大范围重新调用 Planner；当前通过 `request_replan` 暴露原因，后续由上层决定是否重新规划。
- 不实现复杂权限系统、完整 UI、多 Agent 协作或真实外部 LLM / 网络工具的稳定性认证。

### 24.7 Step 47 验证

```text
python -B -m unittest discover tests
Ran 310 tests in 4.352s
OK
```

### 24.8 Step 48 最终验收结论

Step 48 已完成（2026-08-05）。最终回看第二阶段目标后，结论如下：

- Step 25-48 已覆盖模型驱动 ReAct 主循环、ActionPacket repair、TaskUnit / PlanStep 顺序执行、Checker transition、retry/fallback、终止语义、chat/model-only、Observation 压缩、事件流、确认恢复、command Tool 层、Agent API、日志、fixture、端到端、安全不变量和文档回写。
- 当前 Python 源码和测试中已无 `react_loop_not_ready` / `chat_mode_not_implemented` 主路径边界。
- `react_action_loop_not_implemented` 仅保留给显式 legacy diagnostic skeleton，不代表默认执行能力。
- V1 ReActExecutor 架构已经具备接入真实模型和真实 ToolManager 的完整链路：模型只能输出结构化 ActionPacket，执行器负责真实 Observation，Checker 负责工程转移，SafetyPolicy 和 confirmation 负责动作前约束。
- V1 不需要再开启第三阶段开发；后续持久化、异步流、并行 TaskUnit、复杂权限、真实模型质量评估和真实外部工具稳定性属于 V2/V3 范围。

最终验证：

```text
python -B -m unittest discover tests
Ran 310 tests in 4.851s
OK
```

因此，当前可以准确描述为：

```text
Planner-guided ReAct 执行引擎在 V1 范围内已经闭环。
模型通过结构化 ActionPacket 指挥执行器。
执行器通过 Tool/Model/User action 得到真实 Observation。
Checker 负责工程兜底和转移决策。
事件、日志、ObservationStore、ExecutionResult 能支撑用户可见时间线和开发排查。
```
