# ReActExecutor 第二阶段开发步骤与进度

本文档用于规划并记录 ReActExecutor 第二开发阶段的开发进度。

第一阶段 Step 0-24 已经完成 ReActExecutor 工程基础层：协议、Prompt、ObservationStore、EventStream、ToolRegistry、dispatcher、tool/model/user action、Checker、Retry、Fallback、Safety、Logging、ExecutionResult、fixture 回归和 ReactAgent 默认接入。

第二阶段的目标是补齐三份问答文件中最核心但尚未完成的能力：**模型驱动的 Planner-guided ReAct 主循环**。

## 当前真实状态

截至 Step 48（2026-08-05），模型驱动的 Planner-guided ReAct 主循环已经接入默认执行路径：

```text
ReActExecutor.execute()
  -> 创建 ReActExecutionContext
  -> 订阅可选 event callback
  -> execution_started + progress event
  -> plan precheck / empty-plan short-circuit
  -> _execute_react_loop()
       -> TaskUnit 顺序循环
            -> PlanStep 顺序循环
                 -> ReActTurnState
                 -> build prompt
                 -> model.generate
                 -> ActionPacket parse / validation / repair
                 -> dispatch_action
                 -> ObservationStore
                 -> Checker transition
  -> final_answer + ExecutionResult
  -> 取消订阅 + execution_finished log
```

当前已经接入主路径的能力：

- `ActionPacket` 生成、解析、schema/step/tool/retry 校验和 repair retry。
- TaskUnit / PlanStep 顺序执行、依赖检查、`input_from / output_key` 传递。
- `call_tool / call_model / ask_user / retry_step / fallback_to_model / fallback_to_tool / finish / fail / request_replan / blocked / cancel`。
- ObservationStore、EventStream、Checker、RetryPolicy、FallbackPolicy、SafetyPolicy、JSONL 日志和 ExecutionResult。
- chat/model-only、确认暂停与恢复、command Tool 层闭环、Agent 结构化结果和同步事件流接口。

当前明确未做或不承诺的能力：

- 不做跨进程持久化 ObservationStore 和完整断点续跑。
- 不做并行 TaskUnit 调度和自动大范围重规划。
- 不提供异步流、背压、断线重连或复杂权限系统。
- 不要求真实外部 LLM、网络工具和复杂 UI 的稳定性验收。

## 第二阶段总目标

第二阶段完成后，ReActExecutor 应达到以下闭环：

```text
TaskPlan / TaskUnit / PlanStep
  -> build ReAct prompt
  -> model.generate(...)
  -> parse ActionPacket
  -> schema validation
  -> repair retry if needed
  -> dispatch_action(...)
  -> ObservationPacket
  -> ObservationStore
  -> Checker
  -> transition decision
  -> next Thought / next Action / retry / fallback / ask_user / request_replan / finish / fail
  -> ExecutionResult / Events / Logs / Final Response
```

第二阶段继续遵守边界：

- 不重新设计 Analyzer / Planner。
- 不删除旧 Executor，但旧 Executor 只能作为显式历史诊断/迁移兼容开关；它不是 ReActExecutor 失败后的正式 fallback。
- ReActExecutor 不直接执行 shell，命令必须走 Tool 层。
- 模型和执行器交互必须使用 `ActionPacket` 等结构化协议。
- Observation 必须由执行器根据真实结果生成，不允许模型伪造。
- 用户可见事件和开发日志继续分离。
- 不做完整跨对话断点续跑，不做并行 TaskUnit，不做自动大范围重规划。

## 跨 Session 更新规则

每完成一个可验收 Step，必须更新本文档对应 Step：

```text
状态
已完成内容
验证方式
当前边界
下一步
```

建议每个 Step 至少运行相关单元测试。影响主链路时运行：

```text
python -B -m unittest discover tests
```

## 当前进度

```text
第一阶段：Step 0-24 已完成工程基础层。
第二阶段：Step 25-48 已完成，普通可执行计划默认进入模型驱动 ReAct 主循环，具备 Turn/Loop 运行时状态、模型决策 Prompt、ActionPacket 生成与 repair、TaskUnit / PlanStep 顺序执行、Checker 驱动转移、自动 retry/fallback、终止语义、chat/model-only、Observation 上下文压缩、事件订阅 / execute_stream、确认暂停与恢复、命令行 action Tool 层闭环、Agent 结构化结果、安全不变量回归、文档回写和最终验收。
当前：第二阶段已完成；V1 ReActExecutor 不需要再开启第三阶段。后续持久化、异步流、并行调度、复杂权限、真实外部模型/工具稳定性工程属于 V2/V3 范围。
```

---

## Step 25：主循环入口契约与 skeleton 替换策略

状态：已完成（2026-08-04）

目标：

- 明确 `ReActExecutor.execute()` 的第二阶段入口行为。
- 保留 precheck、特殊计划短路、空计划处理。
- 将普通可执行计划从 `_traverse_plan_skeleton()` 切到新的模型驱动主循环。
- 保留 skeleton 兼容能力作为测试辅助或显式诊断入口，不再作为默认执行路径，也不作为正式 fallback。

实现要点：

- 新增内部入口，例如 `_execute_react_loop(context)`。
- `execute()` 流程调整为：

```text
create_context
log execution_started
emit progress_message
run_plan_precheck
if precheck result exists: return it
if no steps: return empty_plan_result
return _execute_react_loop(context)
```

- 明确主循环返回状态：
  - `completed`
  - `failed`
  - `blocked`
  - `waiting_user`
  - `request_replan`
  - `partial_failed`

已完成内容：

- 新增 `_execute_react_loop(context)` 作为第二阶段默认主循环入口。
- `execute()` 保留 context 创建、`execution_started` 日志、`progress_message`、plan precheck、空计划处理，然后将普通可执行计划切换到 `_execute_react_loop(context)`。
- 新入口明确记录可返回终止状态集合：
  - `completed`
  - `failed`
  - `blocked`
  - `waiting_user`
  - `request_replan`
  - `partial_failed`
- 新入口写入开发日志 `react_loop_started`，并通过用户可见 `progress_message/system_notice/final_answer` 返回当前边界。
- 新增 `REACT_LOOP_NOT_READY_CODE = "react_loop_not_ready"`，用于区分第二阶段主循环入口尚未实现完整 ActionPacket 决策闭环与旧 skeleton 占位。
- 保留 `_traverse_plan_skeleton(context)`，但只作为显式诊断/兼容测试辅助，不再由 `execute()` 默认调用。
- 更新测试：普通可执行计划不再期望 `react_action_loop_not_implemented`；显式调用 skeleton 的测试继续覆盖旧兼容函数。

验收标准：

- 原 plan precheck 测试继续通过。
- 普通可执行计划不再默认返回 `react_action_loop_not_implemented`。
- 仍可通过显式测试覆盖 skeleton 兼容函数。

验证方式：

```text
python -B -m unittest tests.test_react_executor_core tests.test_react_executor_plan_precheck
```

实际验证结果：

```text
Ran 17 tests in 0.050s
OK
```

当前边界：

- 本 Step 只切入口和契约，尚未实现模型 Prompt 构建、ActionPacket 生成/repair、dispatch、Observation、Checker 转移和多步骤闭环。
- chat/model-only 路径仍保持现有 precheck 短路，后续 Step 35 处理。
- `_execute_react_loop(context)` 当前返回 `blocked/react_loop_not_ready`，这是第二阶段入口边界，不是旧 skeleton traversal。

下一步：

- Step 26：补充 `ReActTurnState / ReActLoopState` 等运行时状态结构，为后续 Prompt、日志、事件、Checker 和 retry/fallback 提供统一上下文。

---

## Step 26：ReAct Turn 状态结构

状态：已完成（2026-08-04）

目标：

- 为每轮 Thought/Action/Observation 增加显式运行时状态。
- 支撑日志、事件、Checker、retry/fallback 和模型上下文构建。

实现要点：

- 评估是否新增 dataclass：

```text
ReActTurnState
ReActLoopState
```

- 建议字段：

```text
turn_id
execution_turn
step_turn
task_id
step_id
attempt
previous_action
previous_observation
last_checker_result
status
started_at
finished_at
```

- 不把完整推理链作为正式字段，只保存 `thought_summary` 和用户可见计划说明。

已完成内容：

- 在 `react_executor_protocol.py` 新增 `ReActTurnState`。
  - 字段覆盖 `turn_id / execution_turn / step_turn / task_id / step_id / attempt / previous_action / previous_observation / last_checker_result / status / started_at / finished_at`。
  - 增加 `thought_summary` 和 `user_visible_message`，不保存完整推理链。
  - 提供 `finish(...)`、`to_dict()`、`to_model_context()`。
- 在 `react_executor_protocol.py` 新增 `ReActLoopState`。
  - 记录 `execution_id / plan_id / status / execution_turn / step_turns / current_turn_id / current_task_id / current_step_id / previous_action / previous_observation / last_checker_result / turns / max_execution_turns / max_step_turns / started_at / finished_at`。
  - 提供 `start_turn(...)`、`record_action(...)`、`record_observation(...)`、`record_checker_result(...)`、`finish(...)`、`to_dict()`、`to_model_context()`。
- `to_model_context()` 只暴露 action/observation 摘要，不暴露 `action_args`、`raw_model_output`、`raw_observation` 等原始内部数据。
- `ReActExecutionContext` 新增 `loop_state`，在 `_create_context(...)` 中初始化。
- `_execute_react_loop(context)` 在 Step 25 入口边界内创建一个入口 `turn_state`，并将 `loop_state/turn_state` 摘要写入：
  - 开发日志 `react_loop_started`
  - 用户可见 `progress_message/system_notice` 事件 payload
- 未修改 `StepRuntimeState / TaskUnitRuntimeState` 结构和含义。

验收标准：

- Turn 状态可序列化。
- 可被日志、Prompt 上下文、事件流消费。
- 不破坏现有 `StepRuntimeState / TaskUnitRuntimeState`。

验证方式：

```text
python -B -m unittest tests.test_react_executor_protocol tests.test_react_executor_logging
```

实际验证结果：

```text
Ran 16 tests in 0.049s
OK
```

补充回归：

```text
python -B -m unittest tests.test_react_executor_core tests.test_react_executor_plan_precheck
Ran 17 tests in 0.040s
OK
```

当前边界：

- Turn/Loop 状态当前为内存态，不做持久化和跨对话恢复。
- 当前只在 `_execute_react_loop(context)` 入口创建一个边界 turn；真实多轮 Thought/Action/Observation 状态推进将在 Step 29-31 中接入。
- `to_model_context()` 已为 Step 27 Prompt 接入预留稳定结构，但 Step 26 不构建 Prompt、不调用模型、不解析 ActionPacket。

下一步：

- Step 27：实现 `_build_action_decision_prompt(context, task_unit, step, turn_state)`，把用户输入、Analyzer 摘要、TaskPlan、当前 TaskUnit/PlanStep、Observation/Event 摘要、Loop/Turn 状态、安全约束和可用工具接入模型决策 Prompt。

---

## Step 27：模型决策 Prompt 上下文接入

状态：已完成（2026-08-04）

目标：

- 在主循环中真正使用 `build_react_executor_prompt(...)`。
- 每轮模型输入包含用户输入、Analyzer 摘要、TaskPlan、当前 TaskUnit、当前 PlanStep、上一轮 Action、上一轮 Observation、执行进度、安全约束和可用工具。

实现要点：

- 新增 `_build_action_decision_prompt(context, task_unit, step, turn_state)`。
- 使用 `ObservationStore.to_model_context(...)` 和 `EventStream.to_model_context(...)` 控制上下文长度。
- 当前 Step 信息需要包含：
  - `step.id`
  - `step.description`
  - `step.tool_name`
  - `step.args`
  - `input_from`
  - `output_key`
  - `depends_on`
  - `on_failure`
  - `requires_confirmation`
- 工具信息来自 `ToolRegistry`，不直接信任 Planner 的工具名。

已完成内容：

- 新增 `ReActExecutor._build_action_decision_prompt(context, task_unit, step, turn_state)`。
- 主循环入口 `_execute_react_loop(context)` 已在入口 turn 上调用 `build_react_executor_prompt(...)` 构建第一轮模型决策 Prompt，但不调用模型。
- Prompt 上下文包含：
  - 用户输入与 history 摘要。
  - Analyzer 稳定摘要字段：`trace_id / intent_sequence / parameters / risk_flags / action_policy / execution_strategy` 等，不包含 `raw_analysis_trace`。
  - TaskPlan 摘要：`plan_id / goal / mode / strategy / can_execute / validation / step outline` 等，不包含 raw planner trace。
  - 当前 TaskUnit 与 runtime state。
  - 当前 PlanStep 的 `id / description / tool_name / args / input_from / output_key / depends_on / on_failure / requires_confirmation` 等字段。
  - `ObservationStore.to_model_context(input_from)` 生成的输入 Observation 摘要。
  - `EventStream.to_model_context(max_events=20)` 生成的近期事件摘要。
  - `ReActLoopState / ReActTurnState` 的模型可消费摘要。
  - Safety/执行约束和允许的 Action 类型。
- Prompt 中可用工具规格只来自 `ToolRegistry.to_model_spec()`，并按 `_available_tool_names(context)` 过滤；Planner 声明但 registry 不存在的工具不会作为可用工具暴露给模型。
- Prompt payload 对 Analyzer 参数、Step args、Observation、Event payload 和工具 metadata 进行敏感字段脱敏。
- 调整 `ReActPromptContext.to_dict()` 输出顺序，让当前 step、previous observation、input observations 优先进入上下文，避免被较长工具 schema 或 loop state 挤出截断范围。
- 新增 `action_decision_prompt` 开发日志记录，只写 prompt 长度/预览和输入摘要；默认不写完整 prompt。

验收标准：

- fake model 可收到包含当前 step 和可用工具的 Prompt。
- Prompt 不暴露完整内部日志、敏感参数和完整 raw observation。
- Prompt 中只要求模型返回 ActionPacket JSON。

验证方式：

```text
python -B -m unittest tests.test_react_executor_prompt
```

实际验证结果：

```text
Ran 9 tests in 0.032s
OK
```

补充回归：

```text
python -B -m unittest tests.test_react_executor_protocol tests.test_react_executor_logging
Ran 16 tests in 0.052s
OK

python -B -m unittest tests.test_react_executor_core tests.test_react_executor_plan_precheck
Ran 17 tests in 0.058s
OK
```

当前边界：

- 本 Step 只负责 Prompt 构建和上下文接入，不负责 `model.generate(...)`、ActionPacket parse/validate/repair 或 dispatch。
- `_execute_react_loop(context)` 当前仍返回 `blocked/react_loop_not_ready`，但已经能构建并记录模型决策 Prompt。
- Prompt 截断仍由现有 `build_react_executor_prompt(...)` 的 `max_context_chars / max_observation_chars / max_history_chars` 控制；更精细的 Observation 上下文压缩放到 Step 36。

下一步：

- Step 28：实现 `_request_action_packet(...)`，在主路径中调用模型生成 ActionPacket，并打通 parse、schema validation 和 repair retry 边界；repair 耗尽时必须结构化失败且不得执行工具。

---

## Step 28：模型 ActionPacket 生成与 repair 闭环

状态：已完成（2026-08-04）

目标：

- 实现主路径中的：

```text
model.generate(prompt)
parse_action_packet(raw)
validate_action_packet(...)
repair retry
```

实现要点：

- 新增 `_request_action_packet(...)`。
- 新增 `_repair_action_packet(...)` 或在 `_request_action_packet` 内处理 repair。
- repair 最大次数来自配置，建议默认 2-3，绝对不超过问答中提到的 5。
- repair Prompt 必须包含：
  - schema 错误列表
  - 上一次模型输出摘要
  - 当前允许 action
  - 当前 step_id
  - 可用工具名
- 每次 parse/repair 都写日志：
  - `schema_valid`
  - `schema_errors`
  - `repair_attempts`
  - `raw_output_summary`
- repair 后仍失败时，生成结构化失败 Observation，并交给 Checker 或直接 `fail/request_replan`。

已完成内容：

- 新增 `ActionPacketRequestResult` 内部结果结构，用于承载：
  - `packet`
  - `observation`
  - `parse_result`
  - `raw_output`
  - `repair_attempts`
  - `prompt`
- 新增 `ReActExecutor._request_action_packet(...)`，在主路径中完成：
  - `model_manager.generate(prompt)`
  - 主路径 strict 输出检查
  - `parse_action_packet(...)`
  - `validate_action_packet(...)`
  - repair retry
  - 成功时返回结构化 `ActionPacket`
  - 失败时生成结构化失败 `ObservationPacket`
- repair 次数来自 `config.max_action_packet_repair_attempts`，执行器主路径封顶为 5。
- 新增 `_build_action_packet_repair_prompt(...)`，repair Prompt 包含：
  - schema/contract 错误列表
  - 当前 `step_id`
  - 允许的 action type
  - 当前可用工具名
  - 上一次模型输出摘要
- 新增主路径 strict 约束：
  - 接受 dict、纯 JSON object 字符串、完整 fenced JSON。
  - 不接受“自然语言中夹 JSON”的混合输出作为可执行动作。
  - 底层 `parse_action_packet(...)` 仍保留历史兼容能力，但 `_request_action_packet(...)` 不使用该兼容行为来猜动作。
- 新增日志：
  - `action_decision_prompt`
  - `react_loop_started`
  - `model_action_output`
  - `action_packet_repair`
  - `action_packet`
  - repair 耗尽时的 `observation`
- `_execute_react_loop(context)` 已接入 `_request_action_packet(...)`：
  - 如果模型输出合法 ActionPacket，记录 packet 和 `thought_visible`，然后停在 Step 29 前的 `blocked/react_loop_not_ready` 边界，不 dispatch。
  - 如果模型不可用、调用异常、parse/validation repair 耗尽，则返回 `failed`，并生成真实执行器 Observation；不会调用工具。
- 更新 ReactAgent 默认链路测试：默认 ReActExecutor 现在会调用模型生成 ActionPacket，但仍不执行工具。

验收标准：

- fake model 第一次输出非法文本、第二次输出合法 ActionPacket 时可继续执行。
- 连续非法输出达到上限时，返回结构化失败，不执行工具。
- 不允许执行混合自然语言中猜出来的动作。

验证方式：

```text
python -B -m unittest tests.test_react_executor_action_packet_schema tests.test_react_executor_logging
```

实际验证结果：

```text
Ran 26 tests in 0.094s
OK
```

补充回归：

```text
python -B -m unittest tests.test_react_executor_prompt
Ran 9 tests in 0.041s
OK

python -B -m unittest tests.test_react_executor_core tests.test_react_executor_plan_precheck
Ran 17 tests in 0.059s
OK

python -B -m unittest tests.test_react_agent_with_react_executor
Ran 5 tests in 0.053s
OK

python -B -m unittest tests.test_react_executor_action_packet_schema tests.test_react_executor_logging tests.test_react_executor_prompt tests.test_react_executor_core tests.test_react_executor_plan_precheck tests.test_react_agent_with_react_executor
Ran 57 tests in 0.219s
OK
```

当前边界：

- 本 Step 只完成模型输出到 ActionPacket 的生成、parse、validation、repair 和失败 Observation。
- 合法 ActionPacket 目前不会 dispatch；单步 `ActionPacket -> dispatch_action -> Observation -> Checker` 放到 Step 29。
- Checker 尚未驱动 retry/fallback/ask_user/request_replan/finish/fail 的全部转移。
- chat/model-only 仍按 Step 35 处理。

下一步：

- Step 29：新增 `_execute_step_react_loop(context, task_unit, step)`，打通单个 PlanStep 的最小闭环：Prompt -> ActionPacket -> dispatch_action -> Observation -> Checker -> step completed/failed/waiting_user/request_replan。

---

## Step 29：单步 ReAct 最小闭环

状态：已完成（2026-08-04）

目标：

- 打通一个 PlanStep 的最小闭环：

```text
prompt
-> ActionPacket
-> dispatch_action
-> Observation
-> Checker
-> step completed / failed / waiting_user / request_replan
```

实现要点：

- 新增 `_execute_step_react_loop(context, task_unit, step)`。
- 每个 step 内最多 `config.max_step_turns`。
- 每轮发出事件：
  - `thought_visible`：用户可见计划说明，来自 `packet.user_visible_message`，不是完整推理。
  - `action_selected`
  - tool/model/confirmation/observation 事件由 dispatcher 继续负责。
  - `step_completed` 或 `step_failed`
- 成功 Observation 后调用 Checker，Checker 通过则完成当前 step。
- `finish` action 可以直接结束整个 execution。

已完成内容：

- 新增 `ReActStepLoopResult` 内部结果结构。
- 新增 `_execute_step_react_loop(context, task_unit, step)`，打通单个 PlanStep 的最小闭环：
  - `build_react_executor_prompt(...)`
  - `_request_action_packet(...)`
  - `dispatch_action(...)`
  - `ObservationPacket`
  - `check_observation(...)`
  - step 状态收束
- `_execute_react_loop(context)` 现在调用 `_execute_step_react_loop(...)` 执行第一个 PlanStep。
- 单步成功时：
  - step 状态标记为 `completed`
  - TaskUnit 状态汇总为 `completed`
  - ExecutionResult 返回 `completed/success=True`
  - 发出 `step_started / thought_visible / action_selected / tool_started/tool_finished / observation_created / step_completed / final_answer`
- `finish` action 可以通过 dispatcher 和 Checker 直接完成 execution，不调用工具。
- `ask_user / request_replan / fail / blocked` 最小终止语义已接入。
- Checker 结果写入开发日志：
  - `checker_result`
  - `transition_decision`
- 更新 `_sync_task_statuses_from_steps(...)`，支持将全部完成的 TaskUnit 汇总为 `completed`。
- 多步骤计划在 Step 29 边界下只执行第一个 step；若仍有 pending step，则将剩余 step 标记为 `blocked/react_loop_not_ready` 并返回 blocked，明确等待 Step 30 顺序主循环。

验收标准：

- 单步计算工具计划能通过 fake model -> call_tool -> observation -> completed。
- 不再返回 skeleton blocked。
- step 状态、task 状态、ExecutionResult 状态一致。

验证方式：

```text
python -B -m unittest tests.test_react_executor_tool_action tests.test_react_executor_checker
```

实际验证结果：

```text
Ran 25 tests in 0.067s
OK
```

补充回归：

```text
python -B -m unittest tests.test_react_executor_action_packet_schema tests.test_react_executor_logging tests.test_react_executor_prompt
Ran 35 tests in 0.152s
OK

python -B -m unittest tests.test_react_executor_core tests.test_react_executor_plan_precheck tests.test_react_agent_with_react_executor
Ran 22 tests in 0.132s
OK

python -B -m unittest tests.test_react_executor_tool_action tests.test_react_executor_checker tests.test_react_executor_action_packet_schema tests.test_react_executor_logging tests.test_react_executor_prompt tests.test_react_executor_core tests.test_react_executor_plan_precheck tests.test_react_agent_with_react_executor
Ran 82 tests in 0.405s
OK
```

当前边界：

- 只保证单个 PlanStep 的最小闭环；多步骤顺序、依赖检查、`input_from/output_key` 跨 step 主循环稳定传递放 Step 30。
- Checker 已被调用并能收束基本状态，但 retry/fallback 的自动消费仍放 Step 31-33。
- chat/model-only 全面路径仍放 Step 35。

下一步：

- Step 30：实现 TaskUnit / PlanStep 顺序主循环，以 TaskUnit 为外层、PlanStep 为内层执行完整计划，并处理依赖、`input_from/output_key` 和前置失败跳过/阻塞。

---

## Step 30：TaskUnit / PlanStep 顺序主循环

状态：已完成（2026-08-04）

目标：

- 以 TaskUnit 为外层顺序，PlanStep 为内层顺序，执行完整计划。
- 保持 Planner-guided，不做跨 TaskUnit 并行。

实现要点：

- 新增或完善 `_execute_task_unit_loop(...)`。
- `task_units` 优先；缺失时兼容 `plan.steps` 扁平顺序。
- 每个 step 前检查：
  - `depends_on`
  - `input_from`
  - 前置 step 状态
  - 安全策略
  - 最大执行轮数
- 无依赖步骤可继续；依赖失败步骤默认 skipped 或 blocked，按 `on_failure` 和 Checker 决策处理。
- TaskUnit 状态由子 step 汇总：
  - 全完成 -> completed
  - 有 waiting_user -> waiting_user
  - 有 blocked -> blocked
  - 有 failed 且不可继续 -> failed
  - 部分失败但整体可继续 -> partial_failed

验收标准：

- `read_file -> summarize -> write_file` 的 fake model sequence 能顺序执行。
- `input_from / output_key` 可以传递上一轮 Observation。
- 前置失败时，依赖步骤不会误执行。

已完成内容：

- `_execute_react_loop(context)` 已调用 `_execute_task_unit_loop(context, task_units)` 执行完整普通计划，不再停在单个 PlanStep 边界。
- 新增/完善 `_execute_task_unit_loop(...)` 与 `_execute_single_task_unit_loop(...)`：
  - 以 `task_units` 为优先外层顺序。
  - 缺失 `task_units` 时继续通过 `_task_units_for_plan(...)` 兼容 `plan.steps` 扁平顺序。
  - 每个 TaskUnit 内按 `step_ids` 顺序执行 PlanStep。
- 每个 step 执行前检查 `depends_on`、`input_from` 与前置 step 状态；引用缺失或上游失败时生成结构化阻塞，不误调用模型/工具执行依赖步骤。
- `output_key` 已进入 `ObservationStore` 索引；后续 step 的 `input_from` 可解析上一轮 Observation 并注入工具参数。
- 工具参数注入时优先使用上一轮 Observation/ToolResult 的 `data`，其次使用 `message/error`，避免把完整 ToolResult JSON 直接塞入下游工具参数。
- 新增 `_block_dependent_remaining_steps(...)`，当前置 step 失败且不可继续时，将剩余依赖步骤标记为 `blocked` 并发出 `step_failed` 事件。
- TaskUnit 与整体 execution 状态已由子 step 状态汇总：
  - 全部完成或跳过 -> `completed`
  - 有 `waiting_user` -> `waiting_user`
  - 有 `blocked` -> `blocked`
  - 有失败且无成功 -> `failed`
  - 有成功也有失败 -> `partial_failed`
- 更新旧边界测试：普通可执行多步计划不再期望 `react_loop_not_ready`，而是期望顺序执行完成。

验证方式：

```text
python -B -m unittest tests.test_react_executor_observation tests.test_react_executor_v1
```

实际验证结果：

```text
Ran 12 tests in 0.495s
OK
```

补充回归：

```text
python -B -m unittest tests.test_react_executor_tool_action tests.test_react_executor_checker tests.test_react_executor_action_packet_schema tests.test_react_executor_logging tests.test_react_executor_prompt tests.test_react_executor_core tests.test_react_executor_plan_precheck tests.test_react_agent_with_react_executor
Ran 82 tests in 0.325s
OK
```

完整发现测试：

```text
python -B -m unittest discover tests
Ran 261 tests in 3.544s
FAILED (failures=5)
```

失败集中在 `tests.test_analyzer_planner_react_executor_pipeline`：

- `calculate/read_summarize/search_summarize/read_extract_write` 仍期望旧错误码 `react_action_loop_not_implemented`，当前真实行为已进入模型 ActionPacket 主循环并返回 `action_packet_invalid`。
- `test_pipeline_preserves_plan_dependencies_for_file_flow` 仍期望所有 step 为旧 skeleton `blocked`，当前真实行为为首个 step ActionPacket 失败后 `failed`，依赖 step `blocked`。
- 这些端到端 pipeline 预期更新已在 Step 44 规划，本 Step 不提前重写 Analyzer -> Planner -> ReActExecutor 端到端 fixture。

当前边界：

- 不做 TaskUnit 并行，不自动重写 TaskPlan。
- Checker 已能在 Step 30 中收束基本 step 状态，但 retry/fallback 的自动消费仍按计划留给 Step 31-33。
- chat/model-only 全面路径仍留给 Step 35。
- Analyzer -> Planner -> ReActExecutor 端到端测试仍有旧 skeleton 预期，按计划留给 Step 44 统一更新。

下一步：

- Step 31：实现 Checker 驱动转移策略，将 CheckerResult 系统映射为 continue/retry/fallback/ask_user/request_replan/fail 等主循环转移决策。

---

## Step 31：Checker 驱动转移策略

状态：已完成（2026-08-04）

目标：

- 将 Checker 从“可调用工具”升级为主循环的工程决策源。
- 每轮 Observation 后根据 Checker 决定下一步。

实现要点：

- 新增 `_apply_checker_decision(...)`。
- 明确 CheckerResult 到动作的映射：

```text
success + step_complete -> step_completed
success + continue -> next model turn
retryable failure -> retry_step
fallback_to_model -> fallback_to_model
fallback_to_tool -> fallback_to_tool
ask_user -> waiting_user
request_replan -> request_replan
blocked -> blocked
fatal failure -> failed
max_turns_exceeded -> blocked 或 failed
```

- 对 retry/fallback 需要避免无限循环：
  - 按 step 限制 retry 次数。
  - 记录 recent failed action ids。
  - fallback 后再次失败要能升级为 fail/request_replan。

验收标准：

- 工具失败后 Checker 能触发 retry。
- retry 成功后 step completed。
- retry 耗尽后按策略 fallback 或 failed。
- request_replan 能终止 execution 并返回结构化原因。

设计纠正：

- 原 Step 31 与 Step 32/33 存在边界重叠：如果 Step 31 只记录 CheckerResult 而不消费 `retry/fallback`，则无法满足“retry 成功后 step completed”和“retry 耗尽后按策略 fallback 或 failed”的验收标准。
- 本 Step 因此先把 CheckerResult 到主循环转移的统一入口打通，并复用第一阶段已有 `retry_step / fallback_to_model / fallback_to_tool` handler；Step 32/33 保留为 retry/fallback 专项强化、边界 fixture 扩展和更细事件/升级策略完善。

已完成内容：

- 新增 `_apply_checker_decision(...)` 作为主循环唯一 CheckerResult 转移入口。
- `_execute_step_react_loop(...)` 在每轮真实 Observation 后调用 `_apply_checker_decision(...)`，不再只把 Checker 结果当作状态标记。
- 保留 `_apply_step_checker_result(...)` 作为兼容包装，内部转发到 `_apply_checker_decision(...)`。
- 明确并实现 CheckerResult 映射：
  - `step_completed` -> 标记 step completed/skipped，汇总 output/summary，发出 `step_completed`。
  - `continue` -> 返回下一轮模型决策。
  - `ask_user` -> 标记 `waiting_user`，保留 `user_input_request/pending_confirmation`。
  - `request_replan` -> 标记 execution `request_replan`，保留 `replan_reason`，停止后续执行。
  - `retry` -> 执行器生成内部 `retry_step` ActionPacket，经 `dispatch_action(...)` 进入 RetryPolicy 和原 action 重放。
  - `fallback_to_model/fallback_to_tool` -> 执行器生成内部 fallback ActionPacket，经 `dispatch_action(...)` 进入 FallbackPolicy、Tool/Model handler 和 Safety/Confirmation 链路。
  - `fail/blocked/max_turns` -> 结构化收束为 failed/blocked，不继续执行依赖步骤。
- 新增 `_retry_action_packet_from_checker_decision(...)`、`_fallback_action_packet_from_checker_decision(...)`、`_dispatch_checker_transition_action(...)`、`_finalize_checker_failure(...)` 等内部辅助函数。
- retry/fallback 转移仍使用结构化 ActionPacket，Observation 仍由执行器根据真实 tool/model/user/control action 结果生成。
- 内部转移动作写入 `loop_state.record_action/record_observation/record_checker_result`，保持下一轮 Prompt 上下文可见最近动作和观察摘要。
- 对 fallback 后再次失败、retry/fallback 不可消费等情况增加防护，避免在 Step 31 引入无限循环；更完整的耗尽升级矩阵留给 Step 32/33。
- 新增主循环测试：
  - Checker retry 决策自动重放失败工具并完成 step。
  - Checker fallback_to_tool 决策自动调用 fallback tool 并完成 step。
  - Checker fallback_to_model 决策自动调用 model fallback 并完成 step。
  - request_replan ActionPacket 经 Checker 收束为 execution `request_replan`。

验证方式：

```text
python -B -m unittest tests.test_react_executor_checker tests.test_react_executor_retry tests.test_react_executor_fallback
```

实际验证结果：

```text
Ran 34 tests in 0.374s
OK
```

补充回归：

```text
python -B -m unittest tests.test_react_executor_observation tests.test_react_executor_v1 tests.test_react_executor_tool_action tests.test_react_executor_action_packet_schema tests.test_react_executor_logging tests.test_react_executor_prompt tests.test_react_executor_core tests.test_react_executor_plan_precheck tests.test_react_agent_with_react_executor
Ran 78 tests in 0.885s
OK

python -B -m unittest tests.test_react_executor_checker tests.test_react_executor_retry tests.test_react_executor_fallback tests.test_react_executor_observation tests.test_react_executor_v1 tests.test_react_executor_tool_action tests.test_react_executor_action_packet_schema tests.test_react_executor_logging tests.test_react_executor_prompt tests.test_react_executor_core tests.test_react_executor_plan_precheck tests.test_react_agent_with_react_executor
Ran 112 tests in 1.212s
OK
```

完整发现测试：

```text
python -B -m unittest discover tests
Ran 265 tests in 2.843s
FAILED (failures=5)
```

当前边界：

- Checker 仍以规则 Checker 为主，LLMChecker 可保持关闭或受配置控制。
- Step 31 已打通 retry/fallback 的主循环消费，但 Step 32/33 仍需要专项补强 retry 耗尽后的 fallback 升级矩阵、fallback 后再次失败的升级策略、更多 fixture 覆盖和事件细节。
- chat/model-only 全面路径仍留给 Step 35。
- 完整发现测试的 5 个失败仍集中在 `tests.test_analyzer_planner_react_executor_pipeline` 的旧 skeleton 预期，按计划留给 Step 44 统一更新。

下一步：

- Step 32：自动 retry 主路径专项强化，重点完善 retry 耗尽、retry metadata、日志和 fixture 覆盖。

---

## Step 32：自动 retry 主路径

状态：已完成（2026-08-04）

目标：

- 主循环中自动消费 Checker 的 retry 决策。
- 不要求模型显式输出 `retry_step` 才能 retry。

实现要点：

- 当 Checker 判断 retryable 时，主循环可生成内部 `retry_step` ActionPacket。
- retry 仍通过 `dispatch_action()` 执行，保证日志、事件、安全一致。
- retry 前后发出：
  - `retry_scheduled`
  - `retry_finished`
  - `retry_exhausted`
- retry 不应绕过确认和安全策略。

验收标准：

- fake tool 第一次失败、第二次成功时，主循环自动完成。
- retry 次数进入 StepRuntimeState.attempts。
- 日志中记录 retry metadata。

已完成内容：

- 主循环自动消费 Checker 的 `retry` 决策，不要求模型显式输出 `retry_step`。
- 自动 retry 由执行器生成内部结构化 `retry_step` ActionPacket，并继续通过 `dispatch_action(...)` 执行，复用原有 RetryPolicy、SafetyPolicy、Tool/Model handler、ObservationStore、EventStream 和日志链路。
- retry 成功路径：
  - 首次工具失败后 Checker 返回 `retry`。
  - 执行器生成内部 retry action。
  - retry action 重放失败的真实 tool/model action。
  - retry 成功后 Checker 收束为 `step_completed`，ExecutionResult 为 `completed`。
- retry metadata 强化：
  - 新增开发日志 `retry_decision`，记录 scheduled/finished/rejected/exhausted 等 outcome。
  - `retry_decision.metadata.retry` 保存 RetryDecision 结构化内容，包括 retry_count、retry_attempt、next_attempt、max_retries、source_observation_id、source_packet_id、backoff_seconds 等。
  - retry Observation 的 `checker_result.retry` 在主循环二次 Checker 写入时不再被覆盖。
- retry 状态强化：
  - `StepRuntimeState.attempts` 会随 retry 更新到最新 attempt。
  - retry 后仍失败且达到 max_retries 时发出 `retry_exhausted` 事件。
  - retry 耗尽后不会再次重放工具；后续 fallback/fail/request_replan 由 Checker/FallbackPolicy 在 Step 33 继续专项完善。
- 保持边界：
  - retry 不对 `ask_user`、`finish`、`request_replan` 等控制动作生效。
  - retry 不绕过 confirmation 和 SafetyPolicy；重放 action 仍走 dispatcher。

验证方式：

```text
python -B -m unittest tests.test_react_executor_retry tests.test_react_executor_v1
```

实际验证结果：

```text
Ran 13 tests in 0.810s
OK
```

补充回归：

```text
python -B -m unittest tests.test_react_executor_checker tests.test_react_executor_fallback tests.test_react_executor_logging tests.test_react_executor_observation tests.test_react_executor_tool_action tests.test_react_executor_action_packet_schema tests.test_react_executor_prompt tests.test_react_executor_core tests.test_react_executor_plan_precheck tests.test_react_agent_with_react_executor
Ran 101 tests in 0.464s
OK
```

完整发现测试：

```text
python -B -m unittest discover tests
Ran 267 tests in 2.951s
FAILED (failures=5)
```

当前边界：

- retry 只对可重试 action 生效，不对 ask_user、finish、request_replan 等控制动作重试。
- retry 耗尽后的 fallback 升级矩阵、fallback 后再次失败的升级策略和 command fallback 的专项边界留给 Step 33。
- 完整发现测试的 5 个失败仍集中在 `tests.test_analyzer_planner_react_executor_pipeline` 的旧 skeleton 预期，按计划留给 Step 44 统一更新。

下一步：

- Step 33：自动 fallback 主路径，重点完善 retry 不可用/耗尽后的 fallback_to_tool/fallback_to_model 消费、fallback metadata 和不可用 fallback 的结构化失败/request_replan。

---

## Step 33：自动 fallback 主路径

状态：已完成（2026-08-04）

目标：

- 主循环中自动消费 Checker 或 FallbackPolicy 的 fallback 决策。

实现要点：

- 工具失败且 retry 不可用或耗尽时：
  - 优先使用 step 或 ToolSpec 中声明的 fallback tool。
  - 可退回 `fallback_to_model`。
  - 命令行 fallback 必须经过 ToolSpec、SafetyPolicy 和确认策略。
- fallback 仍通过 `dispatch_action()` 进入统一链路。
- fallback 成功后可标记 step completed，但 Observation 需要记录 `fallback_used=True`。

验收标准：

- primary tool 失败后 fallback tool 成功。
- primary tool 失败后 fallback_to_model 成功。
- fallback 工具不可用时返回结构化失败或 request_replan。

已完成内容：

- 主循环已自动消费 Checker/FallbackPolicy 的 `fallback_to_tool` 与 `fallback_to_model` 决策，不要求模型显式输出 fallback action。
- fallback 由执行器生成内部结构化 ActionPacket，并继续通过 `dispatch_action(...)` 进入统一 dispatcher，保持 ToolRegistry、SafetyPolicy、confirmation、ObservationStore、EventStream 和开发日志链路一致。
- retry 不可用或 retry 耗尽后的 fallback 升级已覆盖：
  - primary tool 首次失败 -> Checker retry。
  - retry 后仍失败并达到 max_retries -> `retry_exhausted`。
  - Checker/FallbackPolicy 继续选择 fallback tool。
  - fallback tool 成功后 step completed，ExecutionResult completed。
- fallback_to_tool 主路径已覆盖：
  - 优先使用 step 或 ToolSpec 中声明的 fallback tool。
  - fallback Observation 标记 `fallback_used=True`、`fallback_type="tool"`。
  - output_key 指向 fallback Observation。
- fallback_to_model 主路径已覆盖：
  - 当 step 允许 `allow_model_reasoning` 或策略允许 model fallback 时，自动调用 model fallback。
  - fallback Observation 标记 `fallback_used=True`、`fallback_type="model"`。
- fallback 不可用路径已覆盖：
  - fallback tool 不存在且不允许 model fallback 时返回结构化失败 `fallback_tool_not_available`。
  - 自动主循环不会反复 fallback 或 retry 失败的 fallback action。
- 新增开发日志 `fallback_decision`，记录 scheduled/finished/blocked 等 outcome，并保留 FallbackDecision metadata。
- 命令 fallback 仍必须通过 ToolSpec、SafetyPolicy 和 confirmation；现有 command fallback 确认测试继续通过。

设计纠正：

- 自动 retry 主路径不再消费失败的 `fallback_to_tool/fallback_to_model` action，避免 fallback 不可用时进入 retry/fallback 循环。显式 `retry_step` handler 的兼容能力保留，但主循环自动策略只 retry 原始 `call_tool/call_model`。

验证方式：

```text
python -B -m unittest tests.test_react_executor_fallback tests.test_react_executor_command_action
```

实际验证结果：

```text
Ran 16 tests in 0.214s
OK
```

补充回归：

```text
python -B -m unittest tests.test_react_executor_retry tests.test_react_executor_checker tests.test_react_executor_logging tests.test_react_executor_v1 tests.test_react_executor_tool_action tests.test_react_executor_observation tests.test_react_executor_action_packet_schema tests.test_react_executor_prompt tests.test_react_executor_core tests.test_react_executor_plan_precheck tests.test_react_agent_with_react_executor
Ran 105 tests in 1.244s
OK
```

完整发现测试：

```text
python -B -m unittest discover tests
Ran 270 tests in 3.071s
FAILED (failures=5)
```

当前边界：

- 不做复杂自动工具发现，只使用 ToolRegistry 和 Planner 已给出的可用工具信息。
- fallback 后再次失败的更细升级策略目前收束为 failed/blocked，后续可在 fixture 扩展中继续细化。
- 完整发现测试的 5 个失败仍集中在 `tests.test_analyzer_planner_react_executor_pipeline` 的旧 skeleton 预期，按计划留给 Step 44 统一更新。

下一步：

- Step 34：finish / fail / request_replan 终止语义，重点统一模型/Checker/control action 触发终止时的状态、事件、结果汇总和部分完成信息。

---

## Step 34：finish / fail / request_replan 终止语义

状态：已完成（2026-08-04）

目标：

- 明确模型或 Checker 触发终止时，主循环如何收束状态、事件和结果。

实现要点：

- `finish`：
  - 写入 final_answer 事件。
  - 标记 execution completed。
  - 未执行但不必要的后续 step 可标记 skipped 或 pending，需在结果中解释。
- `fail`：
  - 标记 failed。
  - 输出已完成、失败位置、失败原因和后续建议。
- `request_replan`：
  - 标记 request_replan。
  - 保留 replan_reason。
  - 不继续执行后续步骤。
- `blocked`：
  - 区分安全 blocked、策略 blocked、最大轮数 blocked。

已完成内容：

- 在 `_apply_checker_decision(...)` 中新增 terminal action 统一收束入口，覆盖 `finish / fail / request_replan / blocked / cancel`。
- 为 `ReActStepLoopResult` 增加内部 `terminal` 标记，区分普通 step completed 和控制动作触发的整段执行终止，避免 `finish` 后继续执行后续 PlanStep。
- `finish`：
  - 保留 handler 生成的真实 Observation 和 `final_answer` 事件。
  - 将当前 step 标记为 `completed`，execution status 返回 `completed`。
  - 将后续未执行 step 标记为 `skipped` 并写入不可见 step event，结果中可解释“未执行但不需要继续”。
- `fail`：
  - 将当前 step 标记为 `failed`，execution status 返回 `failed`。
  - 不再被 TaskUnit 聚合逻辑改写成 `partial_failed`；同时 `ExecutionResultBuilder` 仍汇总已成功 Observation 和失败位置/原因。
- `request_replan`：
  - 将当前 step 标记为 `failed`，execution status 返回 `request_replan`。
  - 保留 `request_replan=True` 与 `replan_reason`，后续 step 标记为 `skipped`，不继续执行。
- `blocked/cancel`：
  - 进入同一 terminal 收束路径，当前 step 分别标记为 `blocked/cancelled`，后续 step 标记为 `skipped`。
  - 安全、策略、最大轮数等 blocked 的具体 `error_code` 继续由 Safety/Checker/现有 precheck 产生，Step 34 不重写这些分类来源。
- 新增 `tests/test_react_executor_termination.py`，覆盖：
  - 模型输出 `finish` 时停止后续步骤并标记 skipped。
  - 模型输出 `request_replan` 时停止后续步骤并保留 replan reason。
  - 前置 step 成功后模型输出 `fail` 时，结果同时包含成功 Observation 与失败原因。

验收标准：

- 模型输出 finish 可正常完成 chat/tool plan。
- 模型输出 request_replan 可终止并返回 replan reason。
- 部分步骤完成后 fail 输出不能只给最后一个错误。

验证方式：

```text
python -B -m unittest tests.test_react_executor_result tests.test_react_executor_actions
Ran 15 tests in 0.091s
OK

python -B -m unittest tests.test_react_executor_termination
Ran 3 tests in 0.056s
OK

python -B -m unittest tests.test_react_executor_checker tests.test_react_executor_retry tests.test_react_executor_fallback
Ran 39 tests in 0.554s
OK

python -B -m unittest tests.test_react_executor_v1 tests.test_react_executor_tool_action tests.test_react_executor_action_packet_schema tests.test_react_executor_command_action
Ran 36 tests in 0.649s
OK

python -B -m unittest tests.test_react_executor_core tests.test_react_executor_plan_precheck tests.test_react_executor_model_action tests.test_react_agent_with_react_executor
Ran 28 tests in 0.244s
OK
```

当前边界：

- 最终回答先由 ReActExecutor 汇总，后续是否接 Responder 另行规划。
- `finish` 提前终止时，后续未执行 step 统一标记为 `skipped`；不保留 `pending`，避免下一轮误以为可在同一 execution 中继续。
- Step 34 不改变 chat precheck，因此 `mode == "chat"` 仍按 Step 35 处理。
- `blocked` 的来源分类不在 terminal helper 内重新判断，仍依赖 SafetyPolicy、TaskPolicy、Checker 和 max-turn guard 产生的 code/metadata。
- Step 34 完成时，`python -B -m unittest discover tests` 曾有 5 个旧失败，集中在 `tests.test_analyzer_planner_react_executor_pipeline` 的旧 skeleton/旧阻塞预期；该边界已在 Step 35 通过更新 pipeline fixture 和期望消除。

下一步：

- Step 35：chat / model-only 主路径，移除 chat 模式 `chat_mode_not_implemented` 的主路径阻塞，让无需工具的任务通过结构化 `call_model` 或 `finish` 完成。

---

## Step 35：chat / model-only 主路径

状态：已完成（2026-08-04）

目标：

- 修复 chat 模式当前返回 `chat_mode_not_implemented` 的问题。
- 对无需工具的任务，通过结构化 `call_model` 或 `finish` 完成。

实现要点：

- `mode == "chat"` 不再在 precheck 阶段 blocked。
- chat 计划可以走一个合成 step 或直接进入 `_execute_react_loop`。
- 模型仍必须先输出 ActionPacket：
  - `call_model` 生成中间回答，再 `finish`。
  - 或直接 `finish(final_answer=...)`。
- 不允许 chat 模式让模型返回混合自然语言给执行器猜。

已完成内容：

- `mode == "chat"` 不再在 `_run_plan_precheck(...)` 中返回 `chat_mode_not_implemented`，可执行 chat plan 会进入 Planner-guided ReAct 主循环。
- chat plan 的工具可用集在执行器层收束为空：
  - `_available_tool_names(context)` 对 `plan.mode == "chat"` 返回空集合。
  - ActionPacket 解析与 dispatcher 校验会拒绝 chat 模式下的 `call_tool`。
  - Prompt 上下文中写入 `tool_calls_allowed=false`，并且 `available_tools=[]`。
- chat/model-only 主路径保持结构化协议：
  - 模型必须先返回 `ActionPacket`。
  - 直接 `finish(final_answer=...)` 可完成 chat。
  - `respond/model` step 仍可通过现有 `call_model` handler 生成中间 Observation 后完成。
- ReactAgent 默认 ReActExecutor 链路已更新：
  - chat 任务现在返回 `completed` 结果。
  - 不调用工具。
  - 至少包含一次模型 ActionPacket 决策调用。
- 更新 pipeline fixture，使 Analyzer -> Planner -> ReActExecutor 集成测试不再使用旧 skeleton 预期，而是返回结构化 ActionPacket 并验证真实工具/模型主循环。

验收标准：

- chat 用户输入通过 ReactAgent 默认链路返回正常回答。
- `model_manager.generate_calls` 至少包含 ActionPacket 决策调用。
- chat 不调用工具。

验证方式：

```text
python -B -m unittest tests.test_react_executor_model_action tests.test_react_agent_with_react_executor
Ran 12 tests in 0.095s
OK

python -B -m unittest tests.test_react_executor_plan_precheck
Ran 10 tests in 0.058s
OK

python -B -m unittest tests.test_analyzer_planner_react_executor_pipeline
Ran 2 tests in 0.343s
OK

python -B -m unittest tests.test_react_executor_checker tests.test_react_executor_retry tests.test_react_executor_fallback tests.test_react_executor_termination tests.test_react_executor_v1 tests.test_react_executor_tool_action tests.test_react_executor_action_packet_schema tests.test_react_executor_command_action
Ran 78 tests in 1.256s
OK

python -B -m unittest discover tests
Ran 274 tests in 3.313s
OK
```

当前边界：

- chat 仍是 ReActExecutor 内的结构化执行，不新增 Responder 层。
- chat/tool 禁用策略目前以 `plan.mode == "chat"` 为准；其他 model-only 非 chat 场景仍按 Planner 给出的 step/tool 策略执行。
- `CHAT_MODE_NOT_IMPLEMENTED_CODE` 与旧 `_chat_mode_pending_result(...)` 暂时保留为历史常量/兼容边界，但主路径不再使用。

下一步：

- Step 36：Observation 上下文压缩与 `input_from` 稳定性，重点处理多轮 Prompt 中 Observation 摘要、长输出截断、缺失引用结构化失败或 request_replan。

---

## Step 36：Observation 上下文压缩与 input_from 稳定性

状态：已完成（2026-08-04）

目标：

- 保证多轮主循环中 Observation 能稳定进入下一轮模型 Prompt。
- 控制上下文长度，避免 raw result 全量塞回模型。

实现要点：

- 主循环每轮构建 Prompt 时使用：
  - 当前 step 的 `input_from`
  - 最近 Observation 摘要
  - 当前 task unit 的进度
  - 最近事件摘要
- `raw_observation` 进入日志和 ObservationStore；模型侧优先消费 `model_consumable_observation`。
- 缺失 input refs 时，在执行前生成结构化失败 Observation 或 request_replan。

已完成内容：

- `ObservationStore` 新增面向模型上下文的压缩输出能力：
  - `to_model_context(...)` 返回 Observation 摘要，不暴露 `raw_observation`。
  - `recent_model_context(...)` 返回最近 Observation 摘要，支持数量限制。
  - `resolve_input_refs(..., compact=True)` 支持为模型输入返回脱敏、截断后的 `input_from` 值。
- 新增配置项：
  - `max_model_observation_chars`，控制单个模型可消费 Observation 值的最大字符数，最小值归一为 100。
  - `max_recent_observations`，控制 Prompt 中最近 Observation 摘要数量，允许配置为 0。
- `_build_action_decision_prompt(...)` 将当前 step 的 `input_from` Observation 摘要和最近 Observation 摘要加入 `execution_progress`。
- `_build_model_action_prompt(...)` 的 `input_from` 解析改为使用压缩后的模型输入，避免长输出全量进入模型调用 Prompt。
- `_prepare_tool_args(...)` 仍使用未压缩的 `resolve_input_refs(...)`，保证真实 Tool 执行拿到完整上游输出，压缩只作用于模型上下文。
- 缺失引用保持结构化错误路径，不会异常穿透，也不会错误调用工具或模型。

验收标准：

- 多步骤任务中第二步能拿到第一步输出。
- 长输出会被截断或结构化压缩。
- 缺失引用不会导致异常或错误工具调用。

验证方式：

```text
python -B -m unittest tests.test_react_executor_observation tests.test_react_executor_prompt
```

实际验证结果：

```text
python -B -m unittest tests.test_react_executor_observation tests.test_react_executor_prompt tests.test_react_executor_config
Ran 26 tests in 0.074s
OK

python -B -m unittest tests.test_react_executor_model_action tests.test_react_executor_tool_action tests.test_react_executor_v1 tests.test_react_executor_plan_precheck tests.test_analyzer_planner_react_executor_pipeline
Ran 31 tests in 0.928s
OK

python -B -m unittest discover tests
Ran 278 tests in 3.164s
OK
```

当前边界：

- 不做长期持久化 ObservationStore；本 Step 只处理当前执行过程中的内存态 Observation 上下文压缩。
- 不改变 Tool 层真实入参语义；只有模型 Prompt / 模型动作输入使用压缩视图。
- 不新增复杂摘要模型或长期记忆策略；长输出当前采用结构化 preview + original_chars 的确定性压缩。

下一步：

- Step 37：用户可见事件流主循环增强，增强主循环事件粒度和稳定时间线，但继续保持用户可见 events 与开发 logs 分离。

---

## Step 37：用户可见事件流主循环增强

状态：已完成（2026-08-04）

目标：

- 让第二阶段主循环发出的事件足以支撑类似 Codex 的稳定执行时间线。

实现要点：

- 主循环需要规范发出：
  - `progress_message`
  - `thought_visible`
  - `action_selected`
  - `tool_started/tool_finished`
  - `command_started/command_finished`
  - `message_delta`
  - `observation_created`
  - `retry_*`
  - `fallback_*`
  - `step_started/step_completed/step_failed`
  - `confirmation_requested`
  - `request_replan`
  - `final_answer`
- `thought_visible` 只展示用户可见计划说明，不展示完整推理链。
- 工具入参、raw observation、prompt、异常堆栈等内部字段默认脱敏或只进日志。
- 检查 timeline grouping，确保 started/finished 能稳定合并。

已完成内容：

- 主循环主路径事件增强：
  - `execute()` / `_execute_react_loop(...)` 保持 `progress_message` 与 `final_answer` 的主时间线。
  - `_request_action_packet(...)` 在每次模型决策前发出用户可见 `progress_message`，但不暴露 prompt 或 raw output。
  - `_execute_step_react_loop(...)` 继续发出 `thought_visible`，只展示用户可见动作摘要。
- action / tool / model / observation 事件摘要化：
  - `action_selected` 改为输出摘要化 `action_args_summary`，不直接暴露原始完整参数对象。
  - `tool_started` / `tool_finished` / `message_delta` / `observation_created` 统一改为摘要 payload，避免 raw result、prompt、raw observation、异常堆栈进入用户 timeline。
  - `command_started` / `command_finished` 仍保留，且与 tool 事件可通过 timeline grouping 合并。
- 终止事件顺序修正：
  - `finish` 与 `request_replan` 的直接 action 记录改为内部事件，不再抢在最终 Observation 之前形成用户可见终点。
  - 主循环末尾的 `final_answer` 维持唯一用户可见终点，避免 `final_answer` 早于关键 Observation。
- timeline 校验增强：
  - `EventStream.validate_timeline_integrity()` 新增对 `final_answer` 提前和 started/finished 事件缺组的检查。
  - `step_started` 的匹配按 step_id 校验，避免被 observation_id 干扰。
- 补充了主路径事件回归测试，覆盖：
  - 单步 / 多步安全 timeline
  - retry
  - fallback
  - confirmation
  - request_replan
  - final_answer 顺序稳定性

验收标准：

- 单步、多步、retry、fallback、confirmation、request_replan 都有清晰事件序列。
- 用户可见 timeline 不包含 raw prompt、完整 action_args、敏感字段。
- 事件顺序稳定，不出现 final_answer 早于关键观察的情况。

验证方式：

```text
python -B -m unittest tests.test_react_executor_events tests.test_react_executor_v1
```

实际验证结果：

```text
python -B -m unittest tests.test_react_executor_events tests.test_react_executor_v1
Ran 21 tests in 0.593s
OK

python -B -m unittest tests.test_react_executor_actions tests.test_react_executor_tool_action tests.test_react_executor_model_action tests.test_react_executor_retry tests.test_react_executor_fallback tests.test_react_executor_confirmation tests.test_react_executor_termination tests.test_react_executor_command_action
Ran 59 tests in 0.761s
OK

python -B -m unittest discover tests
Ran 282 tests in 3.324s
OK
```

当前边界：

- 本 Step 先完善事件数据与顺序，不实现实时 streaming API。
- 事件仍然是同步收集式输出，下一步再补订阅 / 流式消费接口。
- 用户可见 timeline 只提供摘要，不承诺完整内部调试上下文。

下一步：

- Step 38：execute_stream / 事件订阅接口。

---

## Step 38：execute_stream / 事件订阅接口

状态：已完成（2026-08-04）

目标：

- 在内部机制上支持外部实时消费事件，为 UI/CLI 实现类似 Codex 的交互效果打基础。

实现要点：

- 评估并实现一种轻量接口：

```text
execute_stream(plan, task, user_input, history="")
  -> yield ExecutionEvent
  -> 最后 yield final_answer 或返回 ExecutionResult
```

或：

```text
execute(..., event_callback=callback)
EventStream.subscribe(callback)
```

- 优先保持同步、可测试，不引入复杂异步调度。
- `execute()` 继续保留，内部可复用 streaming 核心后收集事件和结果。

已完成内容：

- `EventStream` 新增 `subscribe(callback, visible_only=False)`：
  - `emit_event(...)` 在事件 append 后同步通知订阅者。
  - 支持 `visible_only=True` 只接收用户可见事件。
  - 返回 `unsubscribe()`，调用方可取消订阅。
- `ReActExecutor.execute(...)` 新增可选参数：
  - `event_callback`
  - `event_callback_visible_only`
  - 默认行为不变；不传 callback 时与此前 `execute()` 完全兼容。
- 新增 `ReActExecutor.execute_stream(...)`：
  - 以生成器形式按顺序 yield `ExecutionEvent`。
  - generator return 值为最终 `ExecutionResult`。
  - 支持 `include_internal` 控制是否产出内部事件。
- waiting_user / pending confirmation 场景可通过 stream 收到已产生事件，并在 generator return 的 `ExecutionResult` 中读取 `pending_confirmation`。
- 保持同步实现，不引入线程、异步调度或 UI 依赖。

验收标准：

- 调用方可以按顺序收到事件。
- `execute()` 与 `execute_stream()` 结果一致。
- 中途 waiting_user 时能停止流并返回 pending confirmation。

验证方式：

```text
python -B -m unittest tests.test_react_executor_events tests.test_react_executor_core
```

实际验证结果：

```text
python -B -m unittest tests.test_react_executor_events tests.test_react_executor_core
Ran 29 tests in 0.214s
OK

python -B -m unittest tests.test_react_executor_events tests.test_react_executor_v1 tests.test_react_executor_tool_action tests.test_react_executor_model_action tests.test_react_executor_plan_precheck tests.test_analyzer_planner_react_executor_pipeline
Ran 53 tests in 1.213s
OK

python -B -m unittest discover tests
Ran 286 tests in 3.324s
OK

python -B -m py_compile src\agent\react_executor.py src\agent\react_executor_events.py
OK
```

当前边界：

- 不做复杂 UI，只提供稳定事件接口。
- `event_callback` 是本 Step 的实时同步通知主接口。
- `execute_stream(...)` 当前保持同步、可测试实现；事件按顺序产出，最终结果通过 generator return 返回。
- 不做跨线程/异步背压、中断取消和外部 UI 协议封装。

下一步：

- Step 39：确认暂停与恢复闭环。

---

## Step 39：确认暂停与恢复闭环

状态：已完成（2026-08-04）

目标：

- 让 ActionPacket 级 confirmation 在主循环中完整暂停和恢复。
- 明确 ReactAgent/API 层如何交付 pending confirmation。

实现要点：

- 当 dispatcher 返回 `confirmation_pending`：
  - 主循环停止。
  - ExecutionResult.status = `waiting_user`。
  - 保留 `pending_confirmation`。
  - 不继续执行后续步骤。
- 用户批准后：
  - 使用原 `pending_action` 恢复执行。
  - 后续 Observation 继续进入 Checker。
- 用户拒绝后：
  - 当前 step cancelled。
  - 依赖步骤 skipped。
  - 无依赖步骤是否继续由主循环策略决定。
- 修复计划级 confirmation 的 pending_action 字典无法恢复真实执行的问题。

已完成内容：

- 新增 `ReActExecutor.resume_after_confirmation(context, approved, reason="")`，作为同一进程内 pending confirmation 的主循环恢复入口。
- ActionPacket 级 confirmation 批准后：
  - 清除 `pending_confirmation / requires_user_input / user_input_request`。
  - 使用原始 `pending_action` 以 `confirmed=True` 重新进入 dispatcher。
  - 将真实 `ObservationPacket` 继续交给 Checker。
  - 当前 step 完成后回到 `_execute_react_loop(context)`，继续后续 TaskUnit / PlanStep。
- ActionPacket 级 confirmation 拒绝后：
  - 复用 `handle_confirmation_response(...)` 标记当前 step 为 `cancelled`。
  - 依赖当前 step 或其 `output_key` 的步骤标记为 `skipped`。
  - 再回到 `_execute_react_loop(context)`，由顺序主循环决定是否继续无依赖步骤。
- 补齐 plan-level pending_action dict 恢复：
  - `plan_confirmation` 批准后清除 pending，并进入 ReAct 主循环继续模型决策。
  - `plan_safety_confirmation` 批准后从现有 `PlanStep` 重建真实 `call_tool` ActionPacket，直接走 Tool 层执行，不退化为 fake `blocked` action。
- `handle_confirmation_response(...)` 保持 ObservationPacket 兼容返回，同时复用 pending action 解析，支持 `plan_safety_confirmation` 的真实工具恢复。
- `_execute_single_task_unit_loop(...)` 增加已终止 step 跳过逻辑，恢复主循环时不会重跑已经 `completed / skipped / cancelled / failed / blocked` 的步骤。
- 拒绝确认时同步 context 级 `output / summary / error_code / failed_step_id`，避免最终结果继续保留旧的 `confirmation_pending` 状态。

验收标准：

- 危险命令进入 waiting_user，不执行工具。
- approved 后继续原 action。
- rejected 后不执行原 action，依赖步骤跳过。

验证方式：

```text
python -B -m unittest tests.test_react_executor_confirmation tests.test_react_executor_safety
```

实际验证结果：

```text
Ran 16 tests in 0.134s
OK
```

补充回归：

```text
python -B -m unittest tests.test_react_executor_events tests.test_react_executor_v1 tests.test_react_executor_tool_action tests.test_react_executor_model_action
Ran 41 tests in 0.725s
OK
```

当前边界：

- 不做完整跨对话断点续跑；只做同一进程内 pending confirmation 恢复。
- `resume_after_confirmation(...)` 需要调用方持有原 `ReActExecutionContext`；跨进程 / 跨对话 checkpoint 仍不在本 Step 范围内。
- 普通 `plan_confirmation` 本身不代表某个具体工具动作，批准后进入模型驱动主循环；`plan_safety_confirmation` 才会直接从 `PlanStep` 重建并执行工具动作。

下一步：

- Step 40：ReactAgent/API 层结构化结果暴露，让上层能拿到 `ExecutionResult / events / observations / pending_confirmation`，并明确如何交付确认恢复入口。

---

## Step 40：命令行 action 主循环闭环

状态：已完成（2026-08-04）

目标：

- 让模型在主循环中可以通过结构化 ActionPacket 请求命令工具，并完整经过安全、确认、执行、Observation、Checker。

实现要点：

- Prompt 中暴露 `command_tool` ToolSpec，而不是让模型输出裸 shell。
- 命令必须包含结构化参数：
  - `command`
  - `cwd`
  - `purpose`
  - `risk_level`
  - `requires_confirmation`
  - `expected_result`
  - `timeout_seconds`
- 主循环不得直接调用 `subprocess`。
- command 事件需要展示 command、cwd、exit_code、stdout/stderr 摘要、duration。

已完成内容：

- 收紧 `command_tool` ActionPacket 结构校验：
  - `command`
  - `cwd`
  - `purpose`
  - `risk_level`
  - `requires_confirmation`
  - `expected_result`
  - `timeout_seconds`
- 更新默认 `command_tool` ToolSpec，让 Prompt 中暴露完整结构化命令参数，而不是让模型输出裸 shell。
- 在当前 step 为 `command_tool` 时，将当前工具 ToolSpec 放入 `current_step.registered_tool_spec`，避免长上下文截断时 command schema 被挤出 Prompt。
- 修复 plan precheck 对 `command_tool` 空 `step.args` 的过早阻断：
  - Planner 只声明 `tool_name=command_tool` 且命令参数由模型生成时，plan precheck 不再因为缺少 `command` 直接 blocked。
  - 真正的命令参数仍必须在模型生成的 ActionPacket dispatcher 阶段通过结构校验和 SafetyPolicy。
- 主循环命令执行链路已覆盖：
  - 模型生成 `call_tool / command_tool` ActionPacket。
  - dispatcher 先做结构校验，再做 command safety。
  - command scope 默认进入 confirmation pending，不直接执行。
  - 用户批准后通过 Step 39 的恢复入口执行原 ActionPacket。
  - 执行结果生成真实 Observation，并进入 Checker。
  - 命令失败可由 Checker 触发 retry 或 request_replan。
- 修正确认恢复后的 attempt / step_turn 计数：
  - 用户批准 pending command 后执行的是原 action，不把“等待确认”额外消耗为一次 step turn。
  - command retry 不会因为确认恢复误耗 retry budget。
- command retry 由执行器生成内部 `retry_step` ActionPacket，并在 retry 同一已确认命令时携带内部 `confirmed` 标记，避免同一命令 retry 反复要求二次确认。
- command 事件在主循环路径中包含：
  - `command`
  - `cwd`
  - `exit_code`
  - `stdout_summary`
  - `stderr_summary`
  - `duration_ms`
- 同步更新 command fallback 和 v1 fixture 的命令参数，使其符合新的结构化协议。

验收标准：

- command scope 命令默认 confirmation-gated；低风险测试命令可在用户确认后执行。
- 高风险、网络、shell metachar、workspace 外路径被拦截。
- 命令失败后 Checker 可 retry/fallback/request_replan。

验证方式：

```text
python -B -m unittest tests.test_react_executor_command_action tests.test_react_executor_safety
```

实际验证结果：

```text
Ran 16 tests in 0.357s
OK
```

补充回归：

```text
python -B -m unittest tests.test_react_executor_prompt tests.test_react_executor_command_action tests.test_react_executor_fallback tests.test_tool_registry_v1 tests.test_command_tool_v1
Ran 46 tests in 0.557s
OK

python -B -m unittest tests.test_react_executor_v1 tests.test_react_executor_prompt tests.test_react_executor_fallback tests.test_react_executor_command_action
Ran 34 tests in 1.067s
OK

python -B -m unittest discover tests
Ran 295 tests in 4.139s
OK
```

当前边界：

- 不做高风险 shell 自动执行。
- ReActExecutor 仍不直接调用 `subprocess`；真实命令执行只允许通过 `command_tool` / ToolManager / Tool 层。
- `command_tool` 的 `workspace_scope=command` 默认需要用户确认，即使 `risk_level=low`；本 Step 不放开低风险命令自动执行。
- 不实现任意 shell、管道、重定向、网络下载或 workspace 外路径访问。

下一步：

- Step 41 已完成；下一步进入 Step 42：主循环日志完善。

---

## Step 41：Agent API 结构化结果暴露

状态：已完成（2026-08-05）

目标：

- 解决 `ReactAgent.run()` 只返回字符串的问题。
- 为上层 UI/CLI/API 获取 events、observations、pending confirmation 提供入口。

实现要点：

- 保留 `run(user_input) -> str` 兼容旧调用。
- 新增推荐接口，例如：

```text
run_with_result(user_input) -> ExecutionResult
run_stream(user_input) -> iterator[ExecutionEvent]
```

- 短期记忆仍保存最终 assistant 文本，不保存内部日志。
- `executor_type="legacy"` 显式诊断/迁移兼容开关继续有效，但不作为 ReActExecutor 失败后的自动回退。

验收标准：

- 原 `ReactAgent.run()` 测试不破坏。
- 新接口能拿到 ExecutionResult.events/observations/pending_confirmation。
- chat/tool plan 都能通过新接口返回结构化结果。

已完成内容：

- `ReactAgent.run()` 保持原有 `str` 返回契约，并委托到结构化执行路径。
- 新增 `ReactAgent.run_with_result(user_input)`：
  - ReAct executor 直接返回协议层 `ExecutionResult`。
  - legacy Executor 和注入的旧式 executor 结果在 Agent 层适配为同一结构。
  - 统一暴露 `status / success / output / events / observations / pending_confirmation` 等字段。
- 新增 `ReactAgent.run_stream(user_input, include_internal=False)`：
  - 复用 ReActExecutor 的 `execute_stream()`。
  - 默认只向上层 yield 用户可见事件，开发日志不进入用户事件流。
  - 迭代器结束时通过 `StopIteration.value` 返回最终结构化 `ExecutionResult`。
- chat plan、tool plan、确认暂停和 legacy 显式兼容路径均覆盖结构化接口。
- 短期记忆仍只写入最终 assistant 文本，不写入内部 events 或开发日志。

验证方式：

```text
python -B -m unittest tests.test_react_agent_with_react_executor
```

实际验证结果：

```text
Ran 10 tests in 0.127s
OK
```

补充回归：

```text
python -B -m unittest tests.test_react_agent_with_react_executor tests.test_react_executor_events tests.test_react_executor_confirmation
Ran 41 tests in 0.361s
OK
```

当前边界：

- 不新增复杂 UI，仅提供 Agent 层结构化接口。
- `run_stream()` 的最终结果通过生成器返回值交付，调用方需要完整消费迭代器并读取 `StopIteration.value`。
- 当前不新增跨进程/跨对话 checkpoint；`pending_confirmation` 只作为结果暴露，确认恢复仍受 ReActExecutor 原有的同进程 `ReActExecutionContext` 生命周期约束。
- Agent 层不把内部开发日志转换为短期记忆，也不绕过 ReActExecutor 的 Tool/Safety/Checker 链路。

下一步：

- Step 42：主循环日志完善，补齐模型决策、ActionPacket、Observation、Checker、transition 和 execution_finished 的可追踪链路。

---

## Step 42：主循环日志完善

状态：已完成（2026-08-05）

目标：

- 日志完整覆盖第二阶段主循环中的模型决策、repair、Checker 转移和终止原因。

实现要点：

- 新增或复用日志记录：
  - `model_prompt`
  - `model_action_output`
  - `action_packet`
  - `action_packet_repair`
  - `observation`
  - `checker_result`
  - `transition_decision`
  - `execution_finished`
- 默认不记录完整 prompt。
- 记录 source_trace_id -> plan_id -> task_id -> step_id -> turn -> packet_id -> observation_id。

验收标准：

- 一次完整多步骤执行能在 JSONL 中串起 trace。
- schema repair、retry、fallback、request_replan 均有日志记录。
- 敏感字段被脱敏。

已完成内容：

- 主循环日志已覆盖：
  - `model_prompt`
  - `model_action_output`
  - `action_packet`
  - `action_packet_repair`
  - `observation`
  - `checker_result`
  - `transition_decision`
  - `execution_finished`
- 日志记录已补充顶层 `turn_id` 字段，和 `execution_id / source_trace_id / plan_id / task_id / step_id / packet_id / observation_id` 一起串起主循环 trace。
- `model_prompt` 继续默认只写摘要，不写完整 prompt；完整 prompt 仅在显式配置下记录。
- 新增主循环日志回归，覆盖修复后的 `request_replan` 路径，并验证 trace 字段在同一轮执行中可串联。
- 敏感字段继续通过 `sanitize_sensitive(...)` 脱敏。

验证方式：

```text
python -B -m unittest tests.test_react_executor_logging
```

实际验证结果：

```text
Ran 7 tests in 0.083s
OK
```

补充回归：

```text
python -B -m unittest tests.test_react_executor_retry tests.test_react_executor_fallback
Ran 22 tests in 0.500s
OK

python -B -m unittest discover tests
Ran 301 tests in 3.948s
OK
```

当前边界：

- 日志用于开发排查，不直接作为用户 UI 数据源。
- `turn_id` 记录的是当前主循环轮次的标识，不做跨进程持久化恢复。
- 仅覆盖当前已实现的主循环路径，后续新增动作类型时需要同步补充日志记录。

下一步：

- Step 43：fake model sequence fixture 回归。

---

## Step 43：fake model sequence fixture 回归

状态：已完成（2026-08-05）

目标：

- 建立真正覆盖模型 ActionPacket 主循环的 fixture 回归。

实现要点：

- 扩展或新增 fixture：

```text
tests/fixtures/react_executor_loop_cases.json
```

- 覆盖：
  - 单步 tool success
  - 多步 input_from/output_key
  - chat finish
  - call_model intermediate -> finish
  - invalid JSON -> repair -> success
  - invalid JSON repair exhausted
  - unknown tool -> repair/fallback/request_replan
  - tool failure -> retry -> success
  - tool failure -> fallback_to_model
  - tool failure -> fallback_to_tool
  - command confirmation
  - user reject confirmation
  - request_replan
  - max_step_turns exhausted
  - partial_failed with independent later step

验收标准：

- fixture 至少 20 条。
- 每条 fixture 明确期望：
  - result.status
  - success
  - error_code
  - tool/model call count
  - step_statuses
  - observations
  - events
  - log records

验证方式：

```text
python -B -m unittest tests.test_react_executor_v1
```

实际验证结果：

```text
Ran 4 tests in 1.107s
OK
```

已完成内容：

- 新增 `tests/fixtures/react_executor_loop_cases.json`，共 20 条 fake model sequence 案例。
- 在 `tests/test_react_executor_v1.py` 增加 loop fixture 加载、计划构造、执行/确认恢复、配置覆盖和统一断言。
- 断言覆盖 `ExecutionResult` 的 `status`、`success`、`error_code`、tool/model 调用次数、step 状态、Observation 数量/成功顺序、用户事件和开发日志类型。
- 覆盖普通 tool 主循环、多 TaskUnit 顺序、`input_from/output_key`、chat/model-only、ActionPacket repair、retry、fallback、request_replan、command confirmation、安全阻断和 partial failure。
- 按当前协议校准边界语义：用户拒绝确认返回 `cancelled`；`max_step_turns` 耗尽返回 `failed` 且 step 为 `failed`；独立 TaskUnit 后续成功时整体返回 `partial_failed`。

当前边界：

- fake model sequence 优先，不依赖真实外部模型稳定性。
- fixture 验证的是当前 Planner 输出与 ReActExecutor 主循环契约，不替代 Analyzer -> Planner -> ReActExecutor 端到端回归。
- `partial_failed` 仅验证独立 TaskUnit 可继续执行；依赖失败仍按主循环规则阻塞后续 step。

全量回归：

```text
python -B -m unittest discover tests
Ran 302 tests in 4.616s
OK
```

下一步：

- Step 44：将 Analyzer -> Planner -> ReActExecutor 端到端测试从旧 skeleton 预期改为真实模型 ActionPacket 闭环。

---

## Step 44：端到端测试从 skeleton 改为真实闭环

状态：已完成（2026-08-05）

目标：

- 更新 Analyzer -> Planner -> ReActExecutor 端到端测试，不再期望 `react_action_loop_not_implemented`。

实现要点：

- 修改 `tests/test_analyzer_planner_react_executor_pipeline.py`。
- fake model 根据 Planner 生成的 step sequence 返回对应 ActionPacket。
- 覆盖：
  - calculate
  - read_summarize
  - read_extract_write
  - search_summarize
  - chat
  - clarify
  - confirm
  - block
- 保留特殊策略短路测试。

验收标准：

- 普通可执行计划真实调用 fake tool 或 fake model。
- `model_manager.generate_calls` 不再是 0。
- `tool_manager.run_calls` 与 plan tool sequence 一致。
- 结果为 completed 或符合预期的 waiting_user/blocked/request_replan。

验证方式：

```text
python -B -m unittest tests.test_analyzer_planner_react_executor_pipeline
```

实际验证结果：

```text
Ran 2 tests in 0.357s
OK
```

已完成内容：

- 更新 `tests/test_analyzer_planner_react_executor_pipeline.py` 的 `PipelineModelManager`：
  - Analyzer 完成、Planner 生成 `TaskPlan` 后绑定真实 plan。
  - 按 Planner 的 `PlanStep` 顺序生成 `call_tool`、`call_model` 或 `finish` ActionPacket。
  - tool step 的计划参数与 `input_from` 引用进入 ActionPacket。
  - `call_model` 的中间结果单独返回，不消耗后续 ActionPacket。
- 端到端覆盖 calculate、read/summarize、read/extract/write、search/summarize、chat，以及 clarify、confirm、block 特殊策略。
- 普通可执行计划验证：
  - 模型确实生成 ActionPacket。
  - ActionPacket 的 tool target 顺序与 Planner step 顺序一致。
  - Tool 调用顺序与 plan tool sequence 一致。
  - 文件流中的 `input_from` 已注入下游 `text_processor` 和 `file_writer` 参数。
- 保留并验证特殊策略短路：clarify、confirm、block 不进入模型 ActionPacket/Tool 执行路径。

当前边界：

- 端到端测试仍使用 fake model/tool，不依赖外部网络或真实 LLM。
- fake model 只验证当前 Planner 输出的结构化 ActionPacket 闭环，不替代真实模型质量评估。
- 端到端测试覆盖的是已有 Analyzer/Planner 策略和当前 ToolRegistry；新增 Planner step 类型时需要同步扩展 fixture model 映射。

全量回归：

```text
python -B -m unittest discover tests
Ran 302 tests in 4.594s
OK
```

下一步：

- Step 45：清理旧 skeleton 默认边界，并调整仍期望 `react_action_loop_not_implemented` 的兼容测试。

---

## Step 45：旧 skeleton 边界清理与兼容测试调整

状态：已完成（2026-08-05）

目标：

- 将第一阶段 skeleton 行为从默认主路径移除。
- 调整所有仍期望 `react_action_loop_not_implemented` 的测试。

实现要点：

- 搜索并更新：

```text
react_action_loop_not_implemented
chat_mode_not_implemented
ActionPacket loop is not implemented
```

- 可保留 `_traverse_plan_skeleton()` 作为调试辅助，但不能被默认 `execute()` 调用。
- 如果保留 skeleton，需要明确命名为 legacy skeleton 或 diagnostic traversal。

验收标准：

- 默认 ReActExecutor 不再把普通可执行计划标记为 skeleton blocked。
- 测试不再把 skeleton 当作成功边界。

验证方式：

```text
python -B -m unittest discover tests
```

实际验证结果：

```text
Ran 302 tests in 4.002s
OK
```

已完成内容：

- 确认默认 `ReActExecutor.execute()` 只进入 `_execute_react_loop()`，不调用 `_traverse_plan_skeleton()`。
- 保留 `_traverse_plan_skeleton()` / `_traverse_step_skeleton()` 作为显式 legacy diagnostic traversal，不作为默认执行路径。
- 为 legacy diagnostic traversal 增加明确 docstring 和诊断语义文案，不再使用“Step 8 尚未实现”的过时描述。
- 删除已无调用方的 `chat_mode_not_implemented` 常量和 `_chat_mode_pending_result()` dead path；chat 继续走已完成的模型主循环。
- 更新核心兼容测试：
  - 默认多步执行通过 mock 明确禁止调用 skeleton traversal。
  - legacy skeleton 仅通过显式诊断调用验证。
- 在 `src/agent` 和 `tests` 的 Python 源码中确认不再存在 `chat_mode_not_implemented`、旧 Step 8 未实现文案或默认 skeleton 预期。

专项验证：

```text
python -B -m unittest tests.test_react_executor_core
Ran 7 tests in 0.056s
OK

python -B -m unittest tests.test_react_executor_tool_action tests.test_analyzer_planner_react_executor_pipeline tests.test_react_executor_v1
Ran 15 tests in 1.394s
OK
```

当前边界：

- 不删除旧顺序 Executor。
- `ACTION_LOOP_NOT_IMPLEMENTED_CODE` 仅保留给显式 legacy diagnostic traversal，不代表默认执行能力。
- 历史进度文档中的旧 skeleton 记录保留作为开发历史；当前实现、测试和默认主路径以 Step 25-45 的真实闭环为准。

下一步：

- Step 46：执行完整安全回归，验证模型主循环不绕过 Tool 层、安全策略、确认暂停和最大轮次限制。

---

## Step 46：完整安全回归与主循环不变量

状态：已完成（2026-08-05）

目标：

- 确认模型驱动主循环不会绕过第一阶段安全策略。

实现要点：

- 不变量：
  - 模型 ActionPacket 解析失败不得执行动作。
  - 工具不存在不得直接调用 ToolManager。
  - 命令不得绕过 Tool 层。
  - SafetyPolicy blocked 后不得继续执行该 action。
  - confirmation pending 后不得继续执行后续步骤。
  - max_execution_turns 后必须停止。
  - request_replan 后不得继续执行。
- 为不变量增加测试，新增 `tests/test_react_executor_invariants.py`，覆盖：
  - 非法 ActionPacket、未知工具、命令 Tool 路由。
  - SafetyPolicy 阻断、网络下载阻断、确认暂停与恢复。
  - `max_execution_turns` 停止边界。
  - `request_replan` 终止后续步骤。
- 核对主循环状态语义：
  - SafetyPolicy 阻断当前 step 后立即停止执行，不产生 Tool 调用或命令事件。
  - 未被依赖传播处理的后续独立 step 保持 `pending`，并且不产生 `step_started` 等执行事件。
  - 显式 `finish`、`fail`、`blocked`、`cancel`、`request_replan` 终止动作仍按终止语义将后续步骤标记为 `skipped`。

验收标准：

- 危险命令、workspace 外路径、网络下载、shell metachar 全部被拦截。
- retry/fallback 不绕过确认。
- 不会出现无限循环。

验证方式：

```text
python -B -m unittest tests.test_react_executor_safety tests.test_react_executor_command_action
python -B -m unittest tests.test_react_executor_invariants
python -B -m unittest tests.test_react_executor_core tests.test_react_executor_confirmation tests.test_react_executor_termination tests.test_react_executor_retry tests.test_react_executor_fallback
python -B -m unittest discover tests
```

实际验证结果：

```text
python -B -m unittest tests.test_react_executor_invariants
Ran 8 tests in 0.166s
OK

python -B -m unittest tests.test_react_executor_safety tests.test_react_executor_command_action
Ran 16 tests in 0.364s
OK

python -B -m unittest tests.test_react_executor_core tests.test_react_executor_confirmation tests.test_react_executor_termination tests.test_react_executor_retry tests.test_react_executor_fallback
Ran 41 tests in 0.696s
OK

python -B -m unittest discover tests
Ran 310 tests in 4.352s
OK
```

当前边界：

- 不实现复杂权限系统，只保证现有策略在主循环内严格生效。
- 本 Step 验证的是本地 fake model、fake tool 和既有安全策略，不等价于真实外部 LLM 或网络环境安全认证。
- SafetyPolicy 阻断后未开始的独立后续步骤保留 `pending`；只有显式终止动作或依赖传播才会改写为 `skipped`/`blocked`。

下一步：

- Step 47：第二阶段文档回写。

---

## Step 47：第二阶段文档回写

状态：已完成（2026-08-05）

目标：

- 将第二阶段主循环实现状态回写到设计文档和本进度文档。

需要更新：

```text
src/agent/ReActExecutor层设计决策汇总.md
src/agent/ReActExecutor层开发步骤与进度.md
src/agent/ReActExecutor第二阶段开发步骤与进度.md
```

回写内容：

- 主循环最终流程。
- ActionPacket repair 策略。
- Checker transition 策略。
- chat/model-only 行为。
- 事件流实时接口边界。
- Agent API 暴露方式。
- 仍未做的 V2/V3 能力。

已完成内容：

- 更新 `src/agent/ReActExecutor层设计决策汇总.md`：
  - 增加当前状态说明，区分 Step 24 历史快照和第二阶段当前契约。
  - 回写 `execute()`、`execute_stream()`、TaskUnit / PlanStep 顺序主循环和确认恢复流程。
  - 回写 ActionPacket parse / validation / repair、Observation 真实来源和 Checker transition 映射。
  - 回写 chat/model-only、用户事件与开发日志分离、事件订阅、Agent 结构化 API。
  - 明确 legacy Executor、同步流式接口、内存 ObservationStore、无并行 TaskUnit、无自动大范围重规划等 V2/V3 边界。
- 更新 `src/agent/ReActExecutor层开发步骤与进度.md`：
  - 明确 Step 0-24 章节是历史快照。
  - 新增 Step 25-46 当前实现状态回写、验证结果和下一步 Step 48。
  - 将旧的“当前未完成项”和“下一轮建议”改为 Step 24 历史快照标题，避免与当前实现冲突。
- 更新本文件的“当前真实状态”和“当前进度”：
  - 当前默认路径以 Step 25-46 的真实模型驱动主循环为准。
  - Step 47 标记为已完成，下一步切换为 Step 48 最终验收。

验收标准：

- 后续 Session 只读文档即可继续开发或维护。
- 文档不再把 skeleton 作为默认主路径。

验证方式：

```text
python -B -m unittest discover tests
Ran 310 tests in 4.352s
OK
```

当前边界：

- Step 47 只回写当前代码已经实现并由测试覆盖的契约，不新增执行功能。
- 文档中的 Step 0-24 skeleton 描述保留为历史记录；当前 `execute()` 默认路径以 Step 25-46 和本 Step 回写为准。
- 第二阶段最终是否可以对外宣称整体完成，仍由 Step 48 验收决定。

下一步：

- Step 48：第二阶段最终验收。

---

## Step 48：第二阶段最终验收

状态：已完成（2026-08-05）

目标：

- 验收 ReActExecutor 是否已经全面实现三份问答中最核心的 ReAct 主循环。

最终验收清单：

- `[通过]` `execute()` 默认走模型驱动 ReAct 主循环。
- `[通过]` 每轮都有结构化 `ActionPacket`。
- `[通过]` 非法模型输出进入 repair retry。
- `[通过]` repair 耗尽后结构化失败，不猜动作。
- `[通过]` `dispatch_action` 执行真实 tool/model/user/control action。
- `[通过]` ObservationStore 保存真实结果和模型可消费结果。
- `[通过]` Checker 驱动 continue/retry/fallback/ask_user/request_replan/finish/fail。
- `[通过]` chat/model-only 正常完成。
- `[通过]` 普通 tool plan 正常完成。
- `[通过]` 多步骤 input_from/output_key 正常传递。
- `[通过]` 命令 action 走 Tool 层并受安全策略约束。
- `[通过]` 用户可见事件时间线稳定有序。
- `[通过]` 日志可追踪模型看到什么、选择什么、执行器做什么。
- `[通过]` ExecutionResult 汇总完整。
- `[通过]` ReactAgent 默认链路可返回正常最终回答。
- `[通过]` legacy Executor 显式兼容/诊断切换可用，且不作为失败自动 fallback。
- `[通过]` Analyzer / Planner 原有测试不破坏。

最终核查内容：

- 搜索当前 Python 源码和测试中的旧边界：
  - `chat_mode_not_implemented`：无残留。
  - `react_loop_not_ready` / `REACT_LOOP_NOT_READY_CODE`：已从当前代码和测试移除。
  - `react_action_loop_not_implemented`：仅保留给显式 `_traverse_plan_skeleton()` legacy diagnostic traversal 及其兼容测试。
- 修正 `src/agent/react_executor.py`：
  - `_execute_react_loop()` docstring 从 Step 25 入口占位描述更新为当前真实主循环描述。
  - 删除已无调用方的 `REACT_LOOP_NOT_READY_CODE` 常量和测试无用导入。
- 回看第二阶段目标：
  - Step 25-48 覆盖了主循环入口、运行态、Prompt、ActionPacket repair、单步/多步闭环、Checker 转移、retry、fallback、终止、chat、Observation 压缩、事件流、确认恢复、命令 Tool 层、Agent API、日志、fixture、端到端、skeleton 清理、安全不变量、文档和最终验收。
  - 未发现第二阶段目标内的功能遗漏。
- 架构判断：
  - 当前 V1 已具备接入后续真实模型和真实 ToolManager 的结构完整性；模型只通过 ActionPacket 指挥执行器，执行器只通过 Tool/Model/User/control handler 生成真实 Observation。
  - 接入真实模型时仍需要模型提示词质量、工具注册完整性和运行环境配置，但这属于集成调优，不是 ReActExecutor V1 架构缺口。
  - 接入真实命令工具时仍受当前 SafetyPolicy 和 confirmation 约束，ReActExecutor 不直接执行 shell。

最终验证命令：

```text
python -B -m unittest discover tests
Ran 310 tests in 4.851s
OK
```

当前边界：

- 不要求真实外部 LLM 和真实网络工具稳定通过。
- 不要求复杂 UI。
- 不要求完整跨对话断点续跑。
- 不要求自动大范围重规划。
- 不要求并行 TaskUnit、持久化 ObservationStore、异步事件传输、复杂权限系统或多 Agent 协作。

最终结论：

- ReActExecutor 第二阶段已经完成。
- 三份问答文件中要求的核心 Planner-guided ReAct 主循环，在 V1 范围内已经完整实现并通过本地 fake model / fake tool 回归。
- V1 版本的 ReActExecutor 不需要再开启第三阶段开发。
- 后续若继续增强，建议直接进入 V2/V3 规划，而不是继续拆出 V1 第三阶段。

---

## 第二阶段完成后的预期结论

第二阶段已完成，ReActExecutor 可以被描述为：

```text
Planner-guided ReAct 执行引擎已经闭环。
模型通过结构化 ActionPacket 指挥执行器。
执行器通过 Tool/Model/User action 得到真实 Observation。
Checker 负责工程兜底和转移决策。
事件、日志、ObservationStore、ExecutionResult 能支撑用户可见时间线和开发排查。
```

V1 不再新增第三阶段。建议 V2/V3 才考虑：

- ObservationStore 持久化和跨进程恢复。
- 异步 streaming、背压、断线重连和 UI 集成。
- 并行 TaskUnit 调度。
- 自动重新调用 Planner 的大范围 replan 编排。
- 更细的权限模型、审计和真实外部工具安全认证。
- 真实模型提示词质量评估、工具覆盖矩阵和长任务运行稳定性工程。
