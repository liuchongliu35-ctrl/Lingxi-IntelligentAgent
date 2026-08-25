# ReActExecutor 层开发步骤与进度

本文档用于跨 Session 记录 ReActExecutor 层开发步骤、进度、验收标准和后续边界。后续开发 ReActExecutor 时，优先阅读：

```text
src/agent/ReActExecutor层设计决策汇总.md
src/agent/ReActExecutor层开发步骤与进度.md
src/agent/Planner层开发步骤与进度.md
src/agent/Analyzer层开发步骤与进度.md
```

> 状态说明：本文档中的 Step 0-24 章节保留各开发阶段的历史快照。若历史章节提到 skeleton、`react_action_loop_not_implemented` 或“尚未接入主循环”，仅表示当时的阶段边界；当前默认执行契约以文档末尾的第二阶段回写为准。

## 当前定位

ReActExecutor 是第三层执行引擎，负责消费 Planner 生成的 `TaskPlan / TaskUnit / PlanStep`，并以 Planner 的结构化计划为初始路线执行任务。

当前整体架构为：

```text
User Input
  -> Analyzer
      输出 AnalysisResult
  -> Planner
      输出 TaskPlan / TaskUnit / PlanStep
  -> ReActExecutor
      Thought -> Action -> Observation -> Checker
  -> ExecutionResult / Events / Final Response
```

ReActExecutor 不是旧的顺序执行器，也不是完全自由的裸 ReAct。它采用“Planner 引导的 ReAct 执行循环”：

```text
Planner 给初始路线和约束
ReActExecutor 每轮调用模型生成 ActionPacket
ReActExecutor 校验 ActionPacket
ReActExecutor 调用工具或模型动作
ReActExecutor 生成 ObservationPacket
Checker 判断继续、重试、fallback、询问用户、完成或 request_replan
```

## 重点必看：跨 Session 进度更新规则

后续每完成一个可验收开发步骤，都必须同步更新本文档。

执行规则：

```text
完成一个 Step
  -> 跑测试或完成逻辑验证
  -> 确认没有明显问题
  -> 更新本文件对应 Step 的状态、已完成内容、验证方式、当前边界
  -> 下一轮对话继续未完成步骤
```

注意：

- 不需要每改一个小函数都更新。
- 完成一个清晰阶段后必须更新，例如“ActionPacket 协议落地”“ObservationStore 完成”“Action dispatcher 完成”。
- 如果 Session 中途暂停，也要优先记录当前进度，避免新对话丢失上下文。
- 运行 Python 验证时建议使用：

```text
python -B ...
```

避免继续产生 `__pycache__` 变更。

## V1 总目标

ReActExecutor V1 达标需要满足：

1. 能消费 Planner V1 的 `TaskPlan / TaskUnit / PlanStep`。
2. 使用 `ActionPacket` 实现模型大脑和执行器的结构化交互。
3. 每轮执行遵循 `Thought -> Action -> Observation`，并用 Checker 做工程兜底。
4. 支持 `ObservationStore` 内存版。
5. 支持工具调用、模型调用、用户询问、确认暂停。
6. 支持 `input_from / output_key` 的中间结果传递。
7. 支持 retry、fallback_to_model、fallback_to_tool、skip、request_replan。
8. 支持轻量版 `ToolSpec / ToolRegistry`，至少能让 ReActExecutor 校验工具入参和风险。
9. 支持命令行工具的结构化规划和确认，但真实执行必须走 Tool 层安全工具。
10. 支持用户可见事件流。
11. 输出结构化 `ExecutionResult`。
12. 写入 `logs/react_executor.log`。
13. 建立单元测试、fixture 回归和少量端到端测试。

## V1 暂不做

V1 暂不做：

- 不做完整跨对话断点续跑，只记录进度。
- 不做自动大范围重规划，只输出 `request_replan`。
- 不做并行 TaskUnit。
- 不做长期持久化 ObservationStore。
- 不做复杂权限系统，只做基础确认、用户配置和工作区限制。
- 不做复杂 UI，只输出事件结构，CLI 可简单展示。
- 不做多 Agent 协作。
- 不做完整项目自动开发全流程。

## 建议新增或改造文件

优先新增：

```text
src/agent/react_executor.py
src/agent/react_executor_config.py
src/agent/react_executor_protocol.py
src/agent/react_executor_events.py
src/agent/react_executor_checker.py
src/agent/react_executor_safety.py
src/tools/registry.py
config/react_executor/react_executor_config.json
config/react_executor/action_packet_schema.json
config/react_executor/model_prompts.json
tests/fixtures/react_executor_cases.json
tests/test_react_executor_protocol.py
tests/test_react_executor_observation.py
tests/test_react_executor_actions.py
tests/test_react_executor_safety.py
tests/test_react_executor_v1.py
```

可后续改造：

```text
src/agent/executor.py
src/agent/react_agent.py
src/tools/tool_manager.py
src/tools/base.py
```

开发原则：

- 先新增 ReActExecutor 相关文件，测试稳定后再替换旧 Executor 主链路。
- 旧 Executor 只作为开发期兼容和回归保护保留；它不是新链路失败后的正式 fallback，最终目标仍是由 ReActExecutor 替换主执行链路。
- 不直接删除旧 Executor，除非 ReActExecutor 已覆盖旧测试并完成主链路切换。
- ReActExecutor 不直接执行 shell，不直接操作具体工具类，统一走 ToolManager / ToolRegistry。

---

## Step 0：现状核对与入口策略

状态：已完成第一版

目标：

- 确认当前 `Executor`、`Planner`、`ToolManager`、`ToolResult` 的真实接口。
- 决定 ReActExecutor 与旧 Executor 的切换方式。
- 保证新增代码不破坏当前 71 条测试。

需要核对：

```text
src/agent/executor.py
src/agent/planner.py
src/agent/react_agent.py
src/tools/base.py
src/tools/tool_manager.py
tests/test_planner_executor_compatibility.py
```

建议方案：

- 先新增 `src/agent/react_executor.py`。
- 旧 `Executor` 暂时保留，避免一次性破坏回归。
- 新增测试先直接实例化 ReActExecutor。
- ReActExecutor V1 稳定后再改 `ReactAgent` 默认注入。

验收标准：

- 明确当前工具调用接口。
- 明确当前模型调用接口。
- 明确 `TaskPlan / PlanStep` 字段兼容方式。
- 不产生代码行为改动，或者仅新增文档/测试准备。

验证方式：

```text
python -B -m unittest discover tests
```

已完成内容：

- 已核对当前 `Planner` 真实接口：
  - `TaskPlan`、`TaskUnit`、`PlanStep` 均为 dataclass。
  - `TaskPlan` 已包含 `plan_id/source_trace_id/mode/can_execute/task_units/steps/plan_validation_status` 等 ReActExecutor 入口必需字段。
  - `PlanStep` 已包含 `task_id/step_type/depends_on/input_from/output_key/requires_confirmation/on_failure/retryable/max_retries/fallback_tools/allow_model_reasoning/metadata`。
  - 三类对象均支持 `to_dict()`。
- 已核对当前旧 `Executor` 真实接口：
  - 入口为 `execute(plan, task, user_input, history="") -> ExecutionResult`。
  - 当前 `ExecutionResult` 仍是旧结构：`success/output/steps`。
  - 当前旧 Executor 已支持基础 `input_from/output_key` 注入，但不是 ReAct 循环。
- 已核对当前模型接口：
  - `ModelManager.generate(prompt, **kwargs) -> str`。
  - `ModelManager.stream_generate(prompt, **kwargs)` 可返回生成器。
  - 当前默认 `MockModel` 返回占位文本，不返回 ActionPacket。
- 已核对当前工具接口：
  - `ToolManager.run_tool(tool_name, **kwargs) -> ToolResult`。
  - `ToolManager.list_tools() -> dict[str, str]` 当前只提供工具名和描述。
  - `ToolResult` 字段为 `success/data/message/error/code`，支持 `to_text()` 和 `to_dict()`。
- 已确认入口策略：
  - 先新增 ReActExecutor 相关文件。
- 暂不在第一阶段强制移除旧 Executor；它只作为历史兼容/迁移入口，不作为正式 fallback。
  - 后续 ReActExecutor 单测直接实例化新类或新协议对象。
  - 主链路切换放到 Step 23。

主要核对文件：

```text
src/agent/planner.py
src/agent/executor.py
src/agent/react_agent.py
src/tools/base.py
src/tools/tool_manager.py
src/models/model_manager.py
src/models/base_model.py
tests/test_planner_executor_compatibility.py
```

已验证：

```text
python -B -m unittest tests.test_planner_executor_compatibility tests.test_planner_executor_policy
python -B -m unittest discover tests
```

结果：

```text
Ran 12 tests
OK

Ran 75 tests
OK
```

当前边界：

- Step 0 只完成接口核对和入口策略确认，不产生旧执行链路行为改动。
- 当前项目测试数已从文档早期记录的 71 条增长到 75 条，其中 4 条来自 Step 1 新增配置测试。
- 旧 Executor 继续作为开发期兼容和回归保护保留，但不是 ReActExecutor 的正式失败回退目标，最终替换目标不变。

---

## Step 1：ReActExecutor 配置

状态：已完成第一版

目标：

- 新增 ReActExecutor 配置入口。
- 固定 V1 阈值、日志路径、重试次数、安全开关。

建议新增：

```text
src/agent/react_executor_config.py
config/react_executor/react_executor_config.json
```

建议配置项：

```json
{
  "max_execution_turns": 20,
  "max_step_turns": 5,
  "max_action_packet_repair_attempts": 5,
  "default_tool_max_retries": 3,
  "retry_backoff_base_seconds": 0.2,
  "retry_backoff_max_seconds": 2.0,
  "enable_llm_reasoning": true,
  "enable_llm_checker": true,
  "enable_command_tool": true,
  "command_confirmation_policy": "ask",
  "workspace_root": ".",
  "react_executor_log_path": "logs/react_executor.log",
  "event_stream_enabled": true,
  "log_full_prompt": false
}
```

字段说明：

- `max_execution_turns`：整次执行最大 ReAct 回合数，防止无限循环。
- `max_step_turns`：单个 PlanStep 最大回合数。
- `max_action_packet_repair_attempts`：模型输出 schema 修复次数，V1 为 5。
- `command_confirmation_policy`：命令确认策略，可选 `ask | low_risk_auto | session | always`，但危险命令仍必须 block。
- `log_full_prompt`：默认关闭完整 prompt 日志。

验收标准：

- 配置缺失时有稳定默认值。
- 配置文件不存在时可以启动。
- 日志路径可通过配置切换到临时目录，方便测试。

测试建议：

```text
tests/test_react_executor_config.py
```

已完成内容：

- 新增 ReActExecutor 配置加载器：

```text
src/agent/react_executor_config.py
```

- 新增默认配置文件：

```text
config/react_executor/react_executor_config.json
```

- 固定 V1 配置默认值：
  - `max_execution_turns=20`
  - `max_step_turns=5`
  - `max_action_packet_repair_attempts=5`
  - `default_tool_max_retries=3`
  - `retry_backoff_base_seconds=0.2`
  - `retry_backoff_max_seconds=2.0`
  - `enable_llm_reasoning=True`
  - `enable_llm_checker=True`
  - `enable_command_tool=True`
  - `command_confirmation_policy=ask`
  - `workspace_root=.`
  - `react_executor_log_path=logs/react_executor.log`
  - `event_stream_enabled=True`
  - `log_full_prompt=False`
- 固定命令确认策略枚举：

```text
ask
low_risk_auto
session
always
```

- 配置文件不存在时可使用稳定默认值启动。
- 配置文件只覆盖声明字段，未声明字段会合并默认值。
- 相对 `workspace_root` 和 `react_executor_log_path` 会按项目根目录解析为绝对路径。
- 非法数值做保守规范化：
  - 回合数下限为 1。
  - repair/retry 次数下限为 0。
  - backoff 秒数下限为 0.0。
  - 未知 `command_confirmation_policy` 回退为 `ask`。
- 配置对象支持 `to_dict()`，方便后续日志和测试断言。
- 新增专项测试：

```text
tests/test_react_executor_config.py
```

已验证：

```text
python -B -m unittest tests.test_react_executor_config
python -B -m unittest tests.test_planner_executor_compatibility tests.test_planner_executor_policy
python -B -m unittest discover tests
```

结果：

```text
Ran 4 tests
OK

Ran 12 tests
OK

Ran 75 tests
OK
```

当前边界：

- Step 1 只提供配置入口，不读取环境变量，不接入 `ReactAgent`。
- 当前配置加载器不创建日志目录，只负责解析路径；真正写日志在 Step 19 实现。
- 命令确认策略仅固定配置值，真正命令安全检查和确认执行在 Step 14 / Step 18 实现。

---

## Step 2：协议数据结构

状态：已完成第一版

目标：

- 固定 ReActExecutor V1 的核心数据类。
- 将问答中确认的字段落为可序列化对象。

建议新增：

```text
src/agent/react_executor_protocol.py
```

建议数据结构：

```python
ActionPacket
ObservationPacket
ExecutionEvent
PendingConfirmation
StepRuntimeState
TaskUnitRuntimeState
ExecutionResult
CommandAction
```

### ActionPacket

建议字段：

```text
packet_id: str
execution_id: str
plan_id: str
task_id: str | None
step_id: str | None
thought_summary: str
user_visible_message: str
action_type: str
action_target: str | None
action_args: dict
expected_observation: str
confidence: float
requires_confirmation: bool
confirmation_type: str | None
safety_notes: list[str]
fallback_plan: dict
request_replan_reason: str | None
final_answer: str | None
raw_model_output: Any | None
```

### ObservationPacket

建议字段：

```text
observation_id: str
execution_id: str
plan_id: str
task_id: str | None
step_id: str | None
packet_id: str | None
attempt: int
action_type: str
action_target: str | None
tool_name: str | None
input_args: dict
success: bool
data: Any
message: str
error: str | None
code: str | None
raw_observation: Any
model_consumable_observation: Any
started_at: str
finished_at: str
duration_ms: int
fallback_used: bool
fallback_type: str | None
checker_result: dict
visible_to_user: bool
```

### ExecutionEvent

建议字段：

```text
event_id: str
execution_id: str
plan_id: str
task_id: str | None
step_id: str | None
type: str
timestamp: str
visible_to_user: bool
message: str
payload: dict
```

### ExecutionResult

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

枚举建议：

```text
ActionType:
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

ActionType 兼容别名：

```text
retry -> retry_step
stop_success -> finish
stop_failed -> fail
```

模型 prompt 和 JSON Schema 中优先只暴露规范值；解析器可以兼容别名，但进入执行器内部前必须归一化。

```text
ExecutionStatus:
  pending
  running
  waiting_user
  completed
  failed
  partial_failed
  blocked
  cancelled
  request_replan

StepStatus:
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

要求：

- 所有结构支持 `to_dict()`。
- 尽量使用 dataclass，保持项目当前风格。
- 后续如引入 Pydantic，可再迁移。

验收标准：

- 数据结构可序列化为 JSON。
- 缺省字段有稳定默认值。
- 枚举非法值有校验或规范化。

测试建议：

```text
tests/test_react_executor_protocol.py
```

已完成内容：

- 新增 ReActExecutor 协议数据结构：

```text
src/agent/react_executor_protocol.py
```

- 已实现核心 dataclass：
  - `ActionPacket`
  - `ObservationPacket`
  - `ExecutionEvent`
  - `PendingConfirmation`
  - `StepRuntimeState`
  - `TaskUnitRuntimeState`
  - `ExecutionResult`
  - `CommandAction`
- 固定并导出 V1 枚举常量：
  - `ACTION_TYPES`
  - `ACTION_TYPE_ALIASES`
  - `ASK_TYPES`
  - `EXECUTION_STATUSES`
  - `TASK_UNIT_STATUSES`
  - `STEP_STATUSES`
  - `EVENT_TYPES`
  - `COMMAND_RISK_LEVELS`
- 已实现 `ActionType` 兼容别名归一化：

```text
retry -> retry_step
stop_success -> finish
stop_failed -> fail
```

- `ActionPacket` 进入协议对象时会归一化 `action_type`，未知 `action_type` 直接抛 `ValueError`，避免后续 dispatcher 误执行。
- `confidence` 会 clamp 到 `0.0~1.0`。
- `ObservationPacket` 会规范化 `attempt >= 1`、`duration_ms >= 0`。
- `CommandAction` 会规范化未知风险等级为 `unknown`，并保证 `timeout_seconds >= 1`。
- `ExecutionEvent` 会校验事件类型。
- `ExecutionResult`、`TaskUnitRuntimeState`、`StepRuntimeState` 会校验状态枚举。
- 所有协议对象均支持 `to_dict()`，并通过 `_json_safe()` 保证嵌套 dataclass、dict、list 和非 JSON 对象可转为 JSON 可序列化结构。
- 新增协议专项测试：

```text
tests/test_react_executor_protocol.py
```

测试覆盖：

- ActionType 别名归一化。
- 未知 ActionType 拒绝。
- `ActionPacket` 默认值、置信度归一化和 JSON 序列化。
- `ObservationPacket` 默认值、attempt/duration 归一化和 JSON 序列化。
- `ExecutionEvent` 类型校验。
- `PendingConfirmation` 嵌套 ActionPacket 序列化。
- Runtime state 状态枚举校验。
- `CommandAction` 风险等级和 timeout 归一化。
- `ExecutionResult` 嵌套 Observation/Event 序列化和状态校验。

已验证：

```text
python -B -m unittest tests.test_react_executor_protocol tests.test_react_executor_config
python -B -m unittest tests.test_planner_executor_compatibility tests.test_planner_executor_policy
python -B -m unittest discover tests
```

结果：

```text
Ran 13 tests
OK

Ran 12 tests
OK

Ran 84 tests
OK
```

当前边界：

- Step 2 只固定内存协议对象，不做模型 JSON 解析和 schema 文件。
- ActionPacket 按 action 类型的必填字段校验放到 Step 3。
- ObservationStore 的查询、引用解析、脱敏和上下文生成放到 Step 5。
- 事件发射器和事件流管理放到 Step 6。

---

## Step 3：ActionPacket JSON Schema 与模型输出解析

状态：已完成第一版

目标：

- 明确模型输出必须是结构化 ActionPacket。
- 支持 strict JSON、fenced JSON 和 dict 响应。
- 支持 schema 校验失败后的修复重试。

建议新增：

```text
config/react_executor/action_packet_schema.json
src/agent/react_executor_protocol.py
```

模型输出要求：

```text
只返回 JSON
必须包含 action_type
必须从允许 Action 枚举中选择
action_args 必须是 object
final_answer 只在 finish/fail 时使用
request_replan_reason 只在 request_replan 时必填
```

按 action 类型的字段要求：

```text
call_tool: action_target 必须是已注册工具，action_args 必须符合 ToolSpec
call_model: action_args 必须说明生成目标、输入来源和输出要求
ask_user: action_args.ask_type 必须合法，并提供 question/message
retry_step: 必须指向当前 step 或最近失败 action，且不能超过重试上限
fallback_to_model: 必须说明 fallback_reason
fallback_to_tool: 必须提供已存在的 fallback tool
finish: 必须填写 final_answer
fail: 必须填写失败原因
request_replan: 必须填写 request_replan_reason
blocked/cancel: 必须填写用户可理解的原因
```

解析能力：

- 支持模型直接返回 dict。
- 支持普通 JSON 字符串。
- 支持 Markdown fenced JSON。
- 支持从带说明文字中提取第一个 JSON object。
- 支持将非法置信度 clamp 到 `0.0~1.0`。
- 支持丢弃未知 action_type 或触发 repair。

修复策略：

```text
schema invalid
  -> 构造 repair prompt
  -> 最多 5 次
  -> 仍失败则 fail 或 request_replan
```

验收标准：

- 合法 JSON 能解析成 ActionPacket。
- 非 JSON 能触发 repair。
- 未知 action_type 不会被执行。
- 缺少必要字段不会被当作成功动作。

测试建议：

```text
tests/test_react_executor_action_packet_schema.py
```

已完成内容：

- 新增 ActionPacket JSON Schema 配置文件：

```text
config/react_executor/action_packet_schema.json
```

- 在协议层新增模型输出解析能力：

```text
src/agent/react_executor_protocol.py
```

- 新增解析与校验入口：
  - `parse_action_packet(...) -> ActionPacketParseResult`
  - `extract_action_packet_payload(...) -> dict`
  - `validate_action_packet(...) -> list[str]`
  - `build_action_packet_repair_prompt(...) -> str`
- 新增解析结果对象：
  - `ActionPacketParseResult`
- 当前支持的模型输出格式：
  - 直接返回 `dict`
  - 普通 JSON 字符串
  - Markdown fenced JSON
  - 带说明文字中的第一个 JSON object
- 当前基础 schema 校验：
  - 必须包含 `action_type`
  - `action_type` 必须是规范值或兼容别名
  - `action_args` 必须是 object
  - `fallback_plan` 必须是 object
  - `safety_notes` 必须是 array
  - 非 JSON、缺少必要字段、未知 action、字段类型错误都会返回 `needs_repair=True`
- 当前按 action 类型的字段级校验：
  - `call_tool`：必须有 `action_target`，传入 `available_tools` 时必须命中可用工具。
  - `call_model`：必须说明目标、输入来源和输出要求。
  - `ask_user`：必须有合法 `ask_type`，并提供 `question` 或 `message`。
  - `retry_step`：必须指向当前步骤或最近失败 action，且不能超过 retry 上限。
  - `fallback_to_model`：必须提供 `fallback_reason`。
  - `fallback_to_tool`：必须提供 fallback 工具和 `fallback_reason`，传入 `fallback_tools` 时必须命中。
  - `finish`：必须提供 `final_answer`。
  - `fail`：必须提供 `final_answer` 或失败原因。
  - `request_replan`：必须提供 `request_replan_reason`。
  - `blocked/cancel`：必须提供用户可理解的原因。
- `final_answer` 只允许用于 `finish/fail`。
- `request_replan_reason` 只允许用于 `request_replan`。
- 解析失败或校验失败不会抛给 dispatcher，而是返回：

```text
success=False
needs_repair=True
errors=[...]
repair_prompt="..."
```

- 新增专项测试：

```text
tests/test_react_executor_action_packet_schema.py
```

测试覆盖：

- schema 文件是合法 JSON，且只暴露规范 ActionType。
- dict、普通 JSON、fenced JSON、说明文字中的 JSON object 都能解析。
- 非 JSON 输出会触发 repair。
- 未知 `action_type` 不会被解析为可执行动作。
- 缺少 `action_type`、`action_args` 类型错误不会被当作成功。
- `final_answer`、`request_replan_reason` 的使用边界。
- `call_tool`、`ask_user`、`request_replan`、`retry_step`、`fallback_to_tool` 的字段级校验。
- `ActionPacketParseResult.to_dict()` 可 JSON 序列化。

已验证：

```text
python -B -m unittest tests.test_react_executor_action_packet_schema tests.test_react_executor_protocol tests.test_react_executor_config
python -B -m unittest tests.test_planner_executor_compatibility tests.test_planner_executor_policy
python -B -m unittest discover tests
```

结果：

```text
Ran 30 tests
OK

Ran 12 tests
OK

Ran 101 tests
OK
```

当前边界：

- Step 3 只实现单次解析和字段校验，不实现真实模型 repair 重试循环。
- repair prompt 已生成，但实际调用模型重试放到后续 ReActExecutor 主循环或模型交互步骤。
- `call_tool` 当前只按传入的 `available_tools` 校验工具名；严格 ToolSpec 参数 schema 校验放到 Step 7 / Step 11。
- `ActionPacket` schema 文件用于约束和 prompt 暴露，当前未引入第三方 JSON Schema validator。

---

## Step 4：模型 Prompt 模板

状态：已完成第一版

目标：

- 设计 ReActExecutor 调用大模型的 prompt。
- 让模型知道当前计划、当前步骤、Observation、工具 schema、安全边界和 ActionPacket schema。

建议新增：

```text
config/react_executor/model_prompts.json
```

Prompt 必须包含：

```text
system instruction
ActionPacket schema
allowed action types
safety rules
available tools / ToolSpec
user input
Analyzer summary
TaskPlan summary
current TaskUnit
current PlanStep
previous ActionPacket summary
previous Observation model_consumable_observation
execution progress summary
history summary
```

关键约束：

- 不要输出自由格式文本。
- 不要发明工具名。
- 不要绕过 `can_execute=False`。
- 不要执行未确认的危险动作。
- 如果无法继续，选择 `ask_user`、`fallback_to_model`、`request_replan` 或 `fail`。
- 如果任务已完成，选择 `finish` 并填写 `final_answer`。

上下文控制：

- 长 Observation 需要整理为结构化关键信息。
- 不把完整历史原样塞进 prompt。
- prompt 日志只记录摘要和长度。

验收标准：

- prompt 包含当前 step 和可用工具。
- prompt 包含 ActionPacket schema。
- prompt 包含安全约束。
- prompt 适配 MockModel 测试。

测试建议：

```text
tests/test_react_executor_prompt.py
```

已完成内容：

- 新增模型 Prompt 模板配置：

```text
config/react_executor/model_prompts.json
```

- 新增 Prompt 构造模块：

```text
src/agent/react_executor_prompt.py
```

- 新增核心结构和函数：
  - `ReActPromptContext`
  - `load_react_executor_model_prompts(...)`
  - `load_action_packet_schema(...)`
  - `build_react_executor_prompt(...)`
  - `build_prompt_log_summary(...)`
- Prompt 当前包含：
  - system instruction
  - output contract
  - safety rules
  - allowed action types
  - ActionPacket JSON Schema
  - user input
  - Analyzer summary
  - TaskPlan summary
  - current TaskUnit
  - current PlanStep
  - available tools / ToolSpec-like payload
  - previous ActionPacket
  - previous Observation
  - execution progress
  - history summary
  - extra context
- Prompt 模板明确要求模型：
  - 只返回一个 ActionPacket JSON object。
  - 不输出 Markdown、自由说明或混合自然语言。
  - 不发明工具名。
  - 不绕过 `can_execute=false` 或 invalid plan。
  - 不绕过危险动作确认。
  - 无法继续时使用 `ask_user/fallback_to_model/request_replan/fail`。
  - 任务完成时使用 `finish` 并填写 `final_answer`。
- 上下文控制：
  - `previous_observation` 会按 `max_observation_chars` 压缩。
  - `history_summary` 会按 `max_history_chars` 截断。
  - ActionPacket schema 和整体 execution context 会按 `max_context_chars` 截断。
  - `build_prompt_log_summary()` 只输出 prompt 长度和短 preview，避免默认记录完整 prompt。
- 配置文件不存在时会使用稳定默认 Prompt 配置。
- ActionPacket schema 文件不存在时会使用最小 fallback schema，避免 Prompt 构造失败。
- 新增专项测试：

```text
tests/test_react_executor_prompt.py
```

测试覆盖：

- `model_prompts.json` 是合法 JSON。
- prompt 包含当前 step、可用工具、ActionPacket schema、ActionType 和安全约束。
- prompt 包含 previous ActionPacket 和 previous Observation 中的模型可消费信息。
- 长 Observation 和 history 会被压缩。
- prompt log summary 不保存完整 prompt。
- Prompt 配置缺失时使用默认值。
- Prompt 可传给 `MockModel.generate()`，不会破坏当前模型接口。

已验证：

```text
python -B -m unittest tests.test_react_executor_prompt tests.test_react_executor_action_packet_schema tests.test_react_executor_protocol tests.test_react_executor_config
python -B -m unittest tests.test_planner_executor_compatibility tests.test_planner_executor_policy
python -B -m unittest discover tests
```

结果：

```text
Ran 37 tests
OK

Ran 12 tests
OK

Ran 108 tests
OK
```

当前边界：

- Step 4 只构造 Prompt，不调用模型。
- Prompt 当前可接收 Step 7 提供的 `ToolSpec / ToolRegistry.to_model_specs()` 输出。
- Prompt 日志摘要函数已提供，真正写入 `logs/react_executor.log` 在 Step 19。
- 事件流、ObservationStore 和 ReAct 主循环尚未接入。

---

## Step 5：ObservationStore 内存版

状态：已完成第一版

目标：

- 实现内存版 ObservationStore。
- 支持按 step、output_key、observation_id 查询中间结果。

建议能力：

```python
add(observation)
get_by_step(step_id)
get_by_output_key(output_key)
get_latest_for_step(step_id)
resolve_input_refs(input_from)
to_model_context(input_from)
to_dict()
```

注意：

- Observation 由执行器生成，不由模型伪造。
- `raw_observation` 保留真实结果。
- `model_consumable_observation` 给下一轮模型消费，必须尽量保留关键信息。
- V1 不持久化完整 ObservationStore。

脱敏：

- `api_key`
- `token`
- `password`
- `secret`
- `authorization`

验收标准：

- 可保存工具结果和模型结果。
- 可通过 `step.id` 和 `output_key` 查询。
- 可为后续步骤注入文本或结构化输入。
- 敏感字段会脱敏。

测试建议：

```text
tests/test_react_executor_observation.py
```

已完成内容：

- 新增内存版 ObservationStore：

```text
src/agent/react_executor_observation.py
```

- 新增核心能力：
  - `add(observation, output_key=None)`
  - `get(observation_id)`
  - `get_by_step(step_id)`
  - `get_by_output_key(output_key)`
  - `get_latest_for_step(step_id)`
  - `resolve_input_refs(input_from)`
  - `to_model_context(input_from)`
  - `to_dict()`
  - `sanitize_sensitive(...)`
  - `observation_to_text(...)`
- Store 内部保存真实 `ObservationPacket` 对象。
- `output_key` 不写入 `ObservationPacket` 协议字段，而由 `ObservationStore.add(..., output_key=...)` 建立索引。
- 如果未显式传入 `output_key`，Store 会尝试从以下字段推断：
  - `observation.checker_result["output_key"]`
  - `observation.input_args["output_key"]`
- `resolve_input_refs()` 支持按以下顺序解析引用：
  - `output_key`
  - `step_id` 最新 Observation
  - `observation_id`
- `to_model_context()` 输出结构化模型上下文，包含：
  - `ref`
  - `observation_id`
  - `step_id`
  - `success`
  - `message`
  - `code`
  - `model_consumable_observation`
- 缺失引用会返回结构化占位：

```text
{"missing": True, "ref": "..."}
```

- 已实现基础敏感字段脱敏：
  - `api_key`
  - `apikey`
  - `token`
  - `password`
  - `secret`
  - `authorization`
- `to_dict()` 和 `to_model_context()` 会脱敏；Store 内存中的 `raw_observation` 仍保留真实原始结果。
- 新增专项测试：

```text
tests/test_react_executor_observation.py
```

测试覆盖：

- 按 observation_id 查询。
- 按 step_id 查询和获取最新 step Observation。
- 显式和推断 `output_key` 索引。
- `input_from` 同时解析 step_id、output_key、observation_id 和缺失引用。
- 模型上下文脱敏。
- Store 导出脱敏但内存原始结果不变。
- `observation_to_text()` 优先使用 `model_consumable_observation`，其次 `data`，最后 `message`。
- 嵌套 dict/list 敏感字段脱敏。

已验证：

```text
python -B -m unittest tests.test_react_executor_observation tests.test_react_executor_prompt tests.test_react_executor_action_packet_schema tests.test_react_executor_protocol tests.test_react_executor_config
python -B -m unittest tests.test_planner_executor_compatibility tests.test_planner_executor_policy
python -B -m unittest discover tests
```

结果：

```text
Ran 46 tests
OK

Ran 12 tests
OK

Ran 117 tests
OK
```

当前边界：

- Step 5 只实现内存版 Store，不做持久化。
- `output_key` 当前由 Store 维护索引，不改变 `ObservationPacket` 协议结构。
- 脱敏只覆盖基础敏感字段名，复杂内容级脱敏后续可在日志/安全层扩展。
- Store 尚未接入 ReActExecutor 主循环；真实写入时机放到后续 action dispatcher / tool action。

---

## Step 6：用户可见事件流

状态：已完成第一版

目标：

- 实现 ReActExecutor 对外事件结构。
- 支持类似 Codex 的执行过程展示。

建议事件类型：

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

事件要求：

- 事件不是日志。
- 事件默认给用户看。
- 敏感参数、完整工具入参、内部堆栈不进入用户可见事件。
- `visible_to_user=false` 的事件只进入日志或调试流。
- `thought_visible` 只能表示整理后的用户可见进度说明，不展示隐藏思维链、完整 prompt、长推理或敏感信息。
- `progress_message` 用于输出“下一步准备做什么/当前正在做什么/刚完成什么”的自然语言说明。
- `system_notice` 用于记录上下文压缩、自动续接、执行被系统暂停等运行时系统事件。

执行时间线渲染要求：

```text
progress_message/message_delta -> 普通助手说明文本
command_started/command_finished -> 渲染为 Ran commands 或可展开命令块
tool_started/tool_finished -> 渲染为工具调用记录和结果摘要
file_edited -> 渲染为 Edited file / patch 摘要
system_notice -> 渲染为系统提示，例如 Context automatically compacted
final_answer -> 渲染为最终总结
```

时间线由事件流生成，不从 debug 日志临时拼接。日志保存开发排查信息；事件流保存用户可见过程；ObservationStore 保存真实执行结果和模型可消费摘要。

实现建议：

```python
emit_event(type, message, payload, visible_to_user=True)
```

CLI V1 可简单打印：

```text
progress_message -> 直接打印
message_delta -> 直接打印
tool_started -> 打印工具开始
tool_finished -> 打印工具结果摘要
file_edited -> 打印文件和 diff 摘要
command_started -> 打印命令和 cwd
system_notice -> 打印系统提示
final_answer -> 打印最终回答
```

验收标准：

- 每个步骤至少有 `step_started` 和 `step_completed/step_failed`。
- 工具调用有 `tool_started/tool_finished`。
- 最终结果有 `final_answer`。
- 事件可序列化。
- 可以从 events 重建“说明 -> 执行记录 -> 结果摘要 -> 最终总结”的用户可见时间线。

测试建议：

```text
tests/test_react_executor_events.py
```

已完成内容：

- 新增事件流管理模块：

```text
src/agent/react_executor_events.py
```

- 新增核心对象和函数：
  - `EventStream`
  - `emit_event(...)`
  - `visible_events()`
  - `internal_events()`
  - `by_type(type)`
  - `for_step(step_id)`
  - `count_by_type()`
  - `validate_step_timeline(step_ids)`
  - `to_user_timeline()`
  - `to_model_context(max_events=20)`
  - `to_dict(include_internal=True)`
  - `timeline_item(event)`
  - `payload_summary(payload)`
  - `sanitize_event_payload(...)`
- 内部事件机制已固定：
  - `EventStream` 持有完整事件列表。
  - 每次 `emit_event()` 生成标准 `ExecutionEvent`。
  - 事件统一继承 `execution_id/plan_id/task_id/step_id`。
  - `visible_to_user=True` 的事件进入用户时间线。
  - `visible_to_user=False` 的事件只进入内部事件流，用于后续日志或调试。
  - `to_dict(include_internal=False)` 可只导出用户可见事件。
- 用户时间线渲染已固定映射：
  - `progress_message/message_delta/thought_visible` -> `assistant_message`
  - `command_started/command_finished` -> `ran_command`
  - `tool_started/tool_finished` -> `tool_record`
  - `file_edited` -> `file_edit`
  - `system_notice` -> `system_notice`
  - `final_answer` -> `final_answer`
  - `confirmation_requested` -> `confirmation`
  - `observation_created` -> `observation`
  - `request_replan` -> `request_replan`
  - `action_selected` -> `action`
- 已补充 Codex-like 稳定时间线分组机制：
  - `command_started/command_finished` 可通过 `command_id/correlation_id/step_id` 合并为一个 `Ran commands` 条目。
  - `tool_started/tool_finished` 可通过 `tool_call_id/correlation_id/step_id` 合并为一个工具调用条目。
  - `step_started/step_completed/step_failed` 可合并为单步执行条目。
  - 合并后的条目保留开始事件顺序、结束事件 id、结束时间、完成状态和合并 payload。
- 内部状态/质量校验：
  - `validate_step_timeline(step_ids)` 可检查每个 step 是否至少有 `step_started` 和 `step_completed/step_failed`。
  - `count_by_type()` 支持统计事件类型，后续可用于日志和调试。
  - `for_step(step_id)` 可查询单步相关事件。
  - `to_model_context(max_events)` 可生成压缩后的事件上下文，供后续模型消费。
- 安全与脱敏：
  - 事件 payload 默认经过 `sanitize_sensitive()`。
  - 用户可见 payload 会额外隐藏 `full_prompt/raw_prompt/prompt/stack_trace/traceback/exception/env` 等内部字段。
  - 用户可见 payload 会隐藏完整 `action_args/input_args/raw_observation/raw_output/raw_result/raw_tool_result/raw_reasoning/thought_summary/chain_of_thought` 等内部执行字段。
  - 内部事件 payload 仍默认脱敏密钥类字段，但保留调试字段形状。
  - 长 message 和长 payload 字符串会截断，避免 UI 或 prompt 被长输出撑爆。
- 支持事件流禁用模式：
  - `EventStream(enabled=False)` 时，发出的事件会记录为不可见内部禁用事件。
  - 不进入用户时间线。
- 新增专项测试：

```text
tests/test_react_executor_events.py
```

测试覆盖：

- `emit_event()` 生成可序列化 `ExecutionEvent`。
- 非法事件类型会拒绝。
- 用户可见事件和内部事件分离。
- 用户可见 payload 会脱敏并隐藏 prompt/traceback 等内部字段。
- 内部 payload 会脱敏密钥，但保留调试字段。
- 长 message 和长 payload 字符串会截断。
- Codex-like 时间线映射。
- started/finished 事件会按 correlation id 合并成稳定时间线条目。
- 用户可见事件会隐藏完整工具入参和隐藏推理字段。
- step_started / step_completed / step_failed 完整性校验。
- 事件类型统计、按类型查询、按 step 查询。
- `to_model_context()` 输出最近事件摘要。
- `payload_summary()` 会压缩对象、列表和长文本。
- disabled stream 不进入用户时间线。

已验证：

```text
python -B -m unittest tests.test_react_executor_events tests.test_react_executor_observation tests.test_react_executor_prompt tests.test_react_executor_action_packet_schema tests.test_react_executor_protocol tests.test_react_executor_config
python -B -m unittest tests.test_planner_executor_compatibility tests.test_planner_executor_policy
python -B -m unittest discover tests
```

结果：

```text
Ran 60 tests
OK

Ran 12 tests
OK

Ran 131 tests
OK
```

当前边界：

- Step 6 只实现事件流内部机制，不接入 ReActExecutor 主循环。
- 事件流当前只负责结构化记录和安全导出，不负责真实 CLI/UI 渲染。
- 文件 diff、命令 stdout/stderr、工具结果摘要的具体生成逻辑会在对应 action 执行步骤中补齐。
- 真正写日志仍放到 Step 19；事件流不是日志文件。

---

## Step 7：轻量 ToolSpec / ToolRegistry

状态：已完成第一版

目标：

- 让系统知道每个工具的参数、风险、确认策略。
- ReActExecutor 调用工具前可以校验参数和安全。

建议新增：

```text
src/tools/registry.py
```

ToolSpec 字段：

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

V1 可从现有 ToolManager 自动生成或手写注册：

```text
math_calculator
document_parser
text_processor
translator
file_writer
search_tool
command_tool / shell_tool
```

注意：

- 若当前工具没有完整 schema，V1 先写关键参数 schema。
- ReActExecutor 调用工具前必须检查 required_params。
- 模型选择不存在的工具，先 repair/retry，再 fallback 或 request_replan。

验收标准：

- 能列出可用工具。
- 能获取单个 ToolSpec。
- 能校验必填参数。
- 能识别工具风险等级。

测试建议：

```text
tests/test_tool_registry_v1.py
```

已完成内容：

- 新增轻量工具注册模块：

```text
src/tools/registry.py
```

- 新增 ToolRegistry 专项测试：

```text
tests/test_tool_registry_v1.py
```

- 已实现核心 dataclass：
  - `ToolSpec`
  - `ToolValidationResult`
  - `ToolRegistry`
- 已实现默认注册入口：
  - `build_default_tool_registry(tool_manager=None, include_command_tool=False)`
- 已为现有工具固定 V1 级别的轻量 ToolSpec：
  - `math_calculator`
  - `document_parser`
  - `text_processor`
  - `translator`
  - `time_query`
  - `search_tool`
  - `code_executor`
  - `file_writer`
- `command_tool` 当前作为可选 spec 注册，只有 `include_command_tool=True` 时才暴露，并标记：

```python
metadata={"implemented": False}
```

- `ToolSpec` 已覆盖以下能力：
  - 工具名、描述、分类。
  - `parameters_schema` / `returns_schema`。
  - `required_params` 必填参数校验。
  - `required_any_of` 互斥必选组校验，例如 `math_calculator` 要求 `expression` 或 `data` 至少一个。
  - 非 object 入参会返回结构化失败：`tool args must be object`，避免 dispatcher 因模型坏参数崩溃。
  - 轻量 JSON 类型校验：`string / integer / number / boolean / array / object / null`。
  - `risk_level` 规范化：`low / medium / high / blocked`。
  - `workspace_scope` 规范化：`none / read_workspace / write_workspace / network / code_execution / command`。
  - `requires_confirmation`、`timeout`、`fallback_tools` 元数据。
- `ToolRegistry` 已覆盖以下能力：
  - `register(spec)`
  - `get(tool_name)`
  - `has_tool(tool_name)`
  - `list_tools()`
  - `list_specs()`
  - `tool_names()`
  - `validate_tool_args(tool_name, args)`
  - `to_model_specs()`
  - `to_dict()`
- `build_default_tool_registry(tool_manager)` 支持从现有 `ToolManager.list_tools()` 过滤实际可运行工具，并使用 ToolManager 返回的描述覆盖默认描述。
- 测试中避免直接导入真实 `ToolManager`，防止环境缺少第三方依赖时影响 ReActExecutor 协议层回归。

已验证：

```text
python -B -m unittest tests.test_tool_registry_v1 tests.test_react_executor_events tests.test_react_executor_observation tests.test_react_executor_prompt tests.test_react_executor_action_packet_schema tests.test_react_executor_protocol tests.test_react_executor_config
python -B -m unittest tests.test_planner_executor_compatibility tests.test_planner_executor_policy
python -B -m unittest discover tests
```

结果：

```text
Ran 73 tests
OK

Ran 12 tests
OK

Ran 144 tests
OK
```

当前边界：

- Step 7 只提供轻量 ToolSpec / ToolRegistry，不改变现有 `ToolManager` 行为。
- `ToolRegistry` 只负责注册、暴露模型可消费工具说明、参数基础校验和风险元数据，不直接执行工具。
- `command_tool` 只完成协议层登记，当前不实现真实命令执行；后续 Step 14 必须通过 Tool 层安全工具落地，ReActExecutor 仍不能直接执行 shell。
- 当前 schema 校验是 V1 轻量实现，不引入第三方 JSON Schema validator；复杂嵌套参数和枚举约束后续可按工具风险逐步增强。
- ToolSpec 风险等级和确认策略是执行前安全检查的输入，不等同于完整安全策略；真正拦截和确认在 Step 13 / Step 18 落地。

---

## Step 8：ReActExecutor 主类骨架

状态：已完成第一版

目标：

- 新增 ReActExecutor 主类。
- 建立执行入口，不急于实现所有 Action。

建议新增：

```text
src/agent/react_executor.py
```

主接口：

```python
class ReActExecutor:
    def __init__(self, model_manager, tool_manager, tool_registry=None, config=None):
        ...

    def execute(self, plan: TaskPlan, task: Any, user_input: str, history: str = "") -> ExecutionResult:
        ...
```

主循环伪代码：

```text
create execution_id
validate plan can execute
initialize ObservationStore
for task_unit in plan.task_units:
  mark task_unit running
  for step_id in task_unit.step_ids:
    step = lookup step
    if dependencies failed:
      skip or fallback
    execute_step_react_loop(step)
  mark task_unit completed/failed/partial
build final ExecutionResult
```

单步 ReAct 伪代码：

```text
for turn in max_step_turns:
  precheck safety/dependencies/confirmation
  prompt = build_prompt(...)
  packet = call_model_for_action_packet(prompt)
  packet = validate_or_repair(packet)
  emit action_selected
  observation = dispatch_action(packet)
  observation_store.add(observation)
  checker_result = checker.check(...)
  if checker says continue next turn:
      continue
  if checker says step completed:
      return completed
  if checker says retry/fallback/ask_user/request_replan:
      handle
return failed by max turns
```

验收标准：

- 可以实例化。
- 可以拒绝 `plan.can_execute=False`。
- 可以遍历 TaskUnit/PlanStep。
- 可以生成基础 ExecutionResult。
- 暂不要求所有 Action 完整实现。

测试建议：

```text
tests/test_react_executor_core.py
```

已完成内容：

- 新增 ReActExecutor 主类骨架：

```text
src/agent/react_executor.py
```

- 新增主类骨架测试：

```text
tests/test_react_executor_core.py
```

- 已实现 `ReActExecutor.__init__(model_manager=None, tool_manager=None, tool_registry=None, config=None)`：
  - 保存 `model_manager` 和 `tool_manager`，但 Step 8 不调用模型和工具。
  - 未传入 config 时使用 `load_react_executor_config()`。
  - 未传入 `tool_registry` 时使用 Step 7 的 `build_default_tool_registry(...)`。
  - `command_tool` 是否暴露由 `config.enable_command_tool` 和当前 ToolManager 可用工具共同决定。
- 已实现 `execute(plan, task, user_input, history="") -> ExecutionResult` 基础入口：
  - 创建 `execution_id`。
  - 初始化 `ObservationStore`。
  - 初始化 `EventStream`。
  - 建立 `step_lookup`。
  - 初始化 `TaskUnitRuntimeState` / `StepRuntimeState`。
  - 拒绝 `plan.can_execute=False`。
  - 对空计划返回结构化失败。
  - 遍历 `TaskUnit.step_ids` 和 `PlanStep`。
  - 对缺失 step 引用返回结构化失败。
  - 对真实 step 明确标记为 `react_action_loop_not_implemented`，不偷偷执行工具或模型。
- 已新增内部执行上下文：

```python
ReActExecutionContext
```

- 当前事件流骨架会发出：
  - `progress_message`
  - `step_started`
  - `step_failed`
  - `system_notice`
  - `final_answer`
- 当前 `ExecutionResult` 已包含：
  - `execution_id`
  - `plan_id`
  - `source_trace_id`
  - `status`
  - `success`
  - `output`
  - `summary`
  - `task_statuses`
  - `step_statuses`
  - `observations`
  - `events`
  - `failed_step_id`
  - `error_code`

已验证：

```text
python -B -m unittest tests.test_react_executor_core
python -B -m unittest tests.test_react_executor_core tests.test_tool_registry_v1 tests.test_react_executor_events tests.test_react_executor_observation tests.test_react_executor_prompt tests.test_react_executor_action_packet_schema tests.test_react_executor_protocol tests.test_react_executor_config
python -B -m unittest tests.test_planner_executor_compatibility tests.test_planner_executor_policy
python -B -m unittest discover tests
```

结果：

```text
Ran 6 tests
OK

Ran 79 tests
OK

Ran 12 tests
OK

Ran 150 tests
OK
```

当前边界：

- Step 8 只完成主类骨架和结构化返回，不实现真实 Thought -> Action -> Observation 循环。
- Step 8 不调用 `model_manager.generate()`，也不调用 `tool_manager.run_tool()`。
- Step 8 不执行任何 shell；后续命令执行仍必须通过 Tool 层。
- 当前对 `can_execute=False`、空计划、缺失 step 引用做基础结构化处理；完整 plan precheck 放到 Step 9。
- 当前所有真实 step 都会被标记为 `blocked`，错误码为 `react_action_loop_not_implemented`；后续 Step 10 / Step 11 / Step 12 会逐步替换为真实 action dispatch。
- 当前事件流已经能展示骨架执行时间线，但工具/命令/文件编辑等细粒度事件要等对应 action 落地后接入。

---

## Step 9：Plan 执行前校验与状态初始化

状态：已完成第一版

目标：

- ReActExecutor 入口必须尊重 Planner 策略。
- 建立 Execution / TaskUnit / PlanStep 三层运行状态。

执行前必须检查：

```text
plan.can_execute
plan.plan_validation_status
plan.mode in blocked/clarify/confirm/missing_tools/chat
task.action_policy
available tools
step ids
task_unit.step_ids
depends_on/input_from 引用
```

特殊计划处理：

- `blocked`：不执行工具，返回阻断结果。
- `clarify`：返回问题，`requires_user_input=True`。
- `confirm`：返回确认请求，`waiting_user`。
- `missing_tools`：返回缺工具说明。
- `chat`：走 model/respond，不调用执行型工具。
- `invalid`：返回计划校验失败说明。

验收标准：

- block/clarify/confirm/missing_tools/chat 都不会误调用工具。
- invalid plan 不执行工具。
- step 引用错误会结构化失败。

测试建议：

```text
tests/test_react_executor_plan_precheck.py
```

已完成内容：

- 在 `src/agent/react_executor.py` 中新增执行前 precheck 层：
  - `ReActExecutor._run_plan_precheck(...)`
  - `ReActExecutor._plan_reference_errors(...)`
  - `ReActExecutor._plan_reference_error_result(...)`
  - `ReActExecutor._available_tool_names(...)`
- 新增专项测试：

```text
tests/test_react_executor_plan_precheck.py
```

- 已明确执行入口顺序：

```text
create execution context
emit progress_message
run plan precheck
  -> special mode / invalid / reference error 直接返回结构化结果
empty plan check
Step 8 skeleton traversal
```

- 已处理 Planner 特殊模式：
  - `blocked` / `can_execute=False`：返回 `blocked`，不遍历 step。
  - `clarify`：返回 `waiting_user`，设置 `requires_user_input=True` 和 `user_input_request`。
  - `confirm` / `task.action_policy=confirm` / `task.requires_confirmation=True`：返回 `waiting_user`，生成 `PendingConfirmation`，emit `confirmation_requested`。
  - `missing_tools`：返回 `blocked`，输出缺失工具列表。
  - `chat`：当前返回 `blocked`，错误码 `chat_mode_not_implemented`；明确不调用执行型工具，真正 model/respond 在 Step 12。
  - `plan_validation_status=invalid`：返回 `failed`，不遍历 step。
- 已处理 task policy：
  - `task.action_policy=block` 会在执行前阻断，错误码 `task_policy_blocked`。
- 已处理结构引用检查：
  - 重复 step id。
  - `TaskUnit.step_ids` 引用缺失 step。
  - `PlanStep.task_id` 引用缺失 TaskUnit。
  - `depends_on` 引用缺失 step。
  - `input_from` 引用缺失 step 或 `output_key`。
  - `input_from` 可引用前序 step 的 `output_key`。
- 已处理工具可用性检查：
  - 工具 step 的 `tool_name` 必须同时存在于 Step 7 `ToolRegistry` 和 Planner `available_tools` 中。
  - 如果 Planner 未提供 `available_tools`，则以 ToolRegistry 可用工具为准。
- 已新增/固定错误码：

```text
invalid_plan
clarification_required
confirmation_required
missing_tools
chat_mode_not_implemented
task_policy_blocked
tool_not_available
plan_reference_error
```

已验证：

```text
python -B -m unittest tests.test_react_executor_plan_precheck
python -B -m unittest tests.test_react_executor_core
python -B -m unittest tests.test_react_executor_plan_precheck tests.test_react_executor_core tests.test_tool_registry_v1 tests.test_react_executor_events tests.test_react_executor_observation tests.test_react_executor_prompt tests.test_react_executor_action_packet_schema tests.test_react_executor_protocol tests.test_react_executor_config
python -B -m unittest tests.test_planner_executor_compatibility tests.test_planner_executor_policy
python -B -m unittest discover tests
```

结果：

```text
Ran 10 tests
OK

Ran 6 tests
OK

Ran 89 tests
OK

Ran 12 tests
OK

Ran 160 tests
OK
```

当前边界：

- Step 9 只做执行前策略和结构校验，不执行任何 ActionPacket。
- `chat` 模式当前只保证不会误调用工具；真正模型 respond/action 在 Step 12 落地。
- 工具可用性只校验工具名是否可用；工具参数 schema 校验和参数注入放到 Step 11。
- 安全策略只尊重 Planner 和 task policy 的基础阻断/确认信号；更完整的文件/命令/危险动作安全检查在 Step 18。
- Step 9 的 `confirm` 只生成 `PendingConfirmation` 并返回等待用户，不实现用户确认后的恢复执行；恢复流程放到 Step 13。
- 对通过 precheck 的真实 step，仍沿用 Step 8 骨架行为，返回 `react_action_loop_not_implemented`。

---

## Step 10：Action dispatcher

状态：已完成第一版

目标：

- 实现 ActionPacket 到执行动作的分发。

分发映射：

```text
call_tool -> _handle_call_tool
call_model -> _handle_call_model
ask_user -> _handle_ask_user
retry_step -> _handle_retry
fallback_to_model -> _handle_fallback_to_model
fallback_to_tool -> _handle_fallback_to_tool
skip_step -> _handle_skip_step
finish -> _handle_finish
fail -> _handle_fail
request_replan -> _handle_request_replan
blocked -> _handle_blocked
cancel -> _handle_cancel
```

兼容别名必须在进入 dispatcher 前完成归一化，dispatcher 不再分散处理 `retry`、`stop_success`、`stop_failed`。

要求：

- 未知 action_type 不能执行。
- action_args 必须按 action_type 校验。
- 分发前做安全预检查。
- 分发后生成 ObservationPacket。

验收标准：

- 每种 action 都有明确处理结果。
- 未实现 action 返回结构化失败，不抛散乱异常。
- action 执行结果会写 ObservationStore。

测试建议：

```text
tests/test_react_executor_actions.py
```

已完成内容：

- 在 `src/agent/react_executor.py` 中新增独立 Action dispatcher：

```python
dispatch_action(context, packet, *, step=None, attempt=1, output_key=None) -> ObservationPacket
```

- dispatcher 当前职责：
  - 接收已结构化的 `ActionPacket`。
  - emit `action_selected`。
  - 按 action_type 做 dispatcher 级校验。
  - 分发到对应 handler。
  - 捕获 handler 异常并转成结构化 `ObservationPacket`。
  - 写入 `ObservationStore`。
  - emit `observation_created`。
- 已实现完整分发映射：
  - `call_tool -> _handle_call_tool`
  - `call_model -> _handle_call_model`
  - `ask_user -> _handle_ask_user`
  - `retry_step -> _handle_retry`
  - `fallback_to_model -> _handle_fallback_to_model`
  - `fallback_to_tool -> _handle_fallback_to_tool`
  - `skip_step -> _handle_skip_step`
  - `finish -> _handle_finish`
  - `fail -> _handle_fail`
  - `request_replan -> _handle_request_replan`
  - `blocked -> _handle_blocked`
  - `cancel -> _handle_cancel`
- 已接入 `validate_action_packet(...)` 做 dispatcher 级协议校验：
  - 校验 action_type。
  - 校验 call_tool 目标工具可用性。
  - 校验 call_model / ask_user / retry / fallback / finish / fail / request_replan / blocked / cancel 的基础入参。
  - fallback tool 会结合 `PlanStep.fallback_tools` 和 `ToolSpec.fallback_tools` 判断允许目标。
- 已实现未落地 action 的结构化兜底：
  - `call_tool` 兜底已在 Step 11 替换为真实 ToolManager 调用。
  - `call_model` 兜底已在 Step 12 替换为真实 ModelManager 调用。
  - `ask_user` 兜底已在 Step 13 替换为等待用户输入 / PendingConfirmation 行为。
  - `retry_step` 返回 `retry_not_implemented`。
  - `fallback_to_model` 返回 `fallback_to_model_not_implemented`。
  - `fallback_to_tool` 返回 `fallback_to_tool_not_implemented`。
- 已实现控制类 action 的第一版结构化结果：
  - `skip_step`：返回 success Observation，code=`step_skipped`。
  - `finish`：返回 success Observation，保留 `final_answer`。
  - `fail`：返回 failure Observation，code=`action_failed`。
  - `request_replan`：返回 failure Observation，code=`request_replan`，并 emit `request_replan`。
  - `blocked`：返回 failure Observation，code=`action_blocked`。
  - `cancel`：返回 failure Observation，code=`action_cancelled`。
- 已处理未知 action_type：
  - dispatcher 不执行未知 action。
  - 返回结构化失败 Observation，code=`action_packet_invalid`。
  - Observation 协议内 `action_type` 归档为 `fail`，避免未知枚举破坏协议。
- 新增专项测试：

```text
tests/test_react_executor_actions.py
```

已验证：

```text
python -B -m unittest tests.test_react_executor_actions
python -B -m unittest tests.test_react_executor_actions tests.test_react_executor_plan_precheck tests.test_react_executor_core tests.test_tool_registry_v1 tests.test_react_executor_events tests.test_react_executor_observation tests.test_react_executor_prompt tests.test_react_executor_action_packet_schema tests.test_react_executor_protocol tests.test_react_executor_config
python -B -m unittest tests.test_planner_executor_compatibility tests.test_planner_executor_policy
python -B -m unittest discover tests
```

结果：

```text
Ran 9 tests
OK

Ran 98 tests
OK

Ran 12 tests
OK

Ran 169 tests
OK
```

当前边界：

- Step 10 只实现 dispatcher，不把 dispatcher 接入 `execute()` 主循环。
- `call_tool` 已在 Step 11 替换为真实 ToolManager 调用。
- `call_model` 不调用模型，真实 Model action 在 Step 12。
- `ask_user` / confirmation 暂不创建完整等待恢复流程，Step 13 落地。
- retry / fallback 当前只返回结构化未实现 Observation，Step 16 / Step 17 落地。
- dispatcher 会写 ObservationStore 和事件流，但 Checker 尚未接入；Checker 在 Step 15。
- dispatcher 不执行 shell；命令 action 仍必须等 Step 14 通过 Tool 层安全工具落地。

---

## Step 11：Tool action 执行

状态：已完成第一版

目标：

- 实现 `call_tool`。
- 支持 ToolSpec 校验、参数注入、ToolResult 转 Observation。

执行流程：

```text
读取 action_target 作为 tool_name
检查 tool 是否存在
按 ToolSpec 校验 action_args
注入 input_from 依赖结果
安全检查
emit tool_started
tool_manager.run_tool(...)
emit tool_finished
ToolResult -> ObservationPacket
```

参数注入：

- 根据 `step.input_from` 从 ObservationStore 获取前序结果。
- 对常见工具注入：
  - `text`
  - `content`
  - `query`
  - `file_path`
- 不发明工具不存在的入参。

验收标准：

- 工具成功返回 success Observation。
- 工具失败返回 failure Observation。
- ToolResult.code 被保留。
- input_from 能注入到后续工具。
- 缺参数不会调用工具。

测试建议：

```text
tests/test_react_executor_tool_action.py
```

已完成内容：

- 在 `src/agent/react_executor.py` 中实现真实 `_handle_call_tool(...)`。
- `call_tool` 当前执行流程：

```text
读取 ActionPacket.action_target 作为 tool_name
检查 ToolRegistry 中是否存在 ToolSpec
检查 ToolManager 是否可用
合并 PlanStep.args 与 ActionPacket.action_args
过滤 ToolSpec 未声明参数，避免把控制字段传给工具
按 input_from 从 ObservationStore 解析前序 Observation
将前序结果注入 text / content / query / file_path / expression 等已声明工具参数
按 ToolSpec.validate_args(...) 校验最终入参
emit tool_started
tool_manager.run_tool(tool_name, **input_args)
emit tool_finished
ToolResult / 普通返回值 / 异常 -> ObservationPacket
写入 ObservationStore
emit observation_created
```

- 已实现参数合并策略：
  - `PlanStep.args` 作为 Planner 引导默认值。
  - `ActionPacket.action_args` 覆盖 Planner 默认值。
  - `input_from` 和 `output_key` 等控制字段不会传入工具。
- 已实现 input_from 注入：
  - 优先使用 `ActionPacket.action_args["input_from"]`。
  - 如果 ActionPacket 未声明，则使用 `PlanStep.input_from`。
  - 支持引用前序 `step_id`、`output_key` 或 `observation_id`，解析逻辑复用 Step 5 `ObservationStore`。
  - 缺失引用返回结构化失败，错误码 `tool_input_ref_missing`，不调用工具。
- 已实现常见工具注入目标：
  - `text_processor -> text`
  - `translator -> text`
  - `file_writer -> content`
  - `search_tool -> query`
  - `math_calculator -> expression`
  - `document_parser -> file_path`
  - 只有 ToolSpec 声明过的参数才会被注入。
- 已实现 ToolSpec 参数校验：
  - 缺必填参数返回 `tool_argument_validation_failed`。
  - 参数类型错误返回 `tool_argument_validation_failed`。
  - 校验失败不会 emit `tool_started`，也不会调用 ToolManager。
- 已实现 ToolResult 转 Observation：
  - 工具成功 -> success Observation。
  - 工具失败 -> failure Observation。
  - `ToolResult.code` 被保留。
  - 非 `ToolResult` 返回值会包装为 success ToolResult。
  - 工具抛异常会返回 `tool_execution_exception`，并保留结构化错误。
- 已新增错误码：

```text
tool_manager_unavailable
tool_argument_validation_failed
tool_input_ref_missing
tool_execution_failed
tool_execution_exception
```

- 新增专项测试：

```text
tests/test_react_executor_tool_action.py
```

- 同步更新 dispatcher 测试：
  - `call_tool` 现在验证真实 ToolManager 调用，而不是未实现兜底。

已验证：

```text
python -B -m unittest tests.test_react_executor_tool_action
python -B -m unittest tests.test_react_executor_actions
python -B -m unittest tests.test_react_executor_tool_action tests.test_react_executor_actions tests.test_react_executor_plan_precheck tests.test_react_executor_core tests.test_tool_registry_v1 tests.test_react_executor_events tests.test_react_executor_observation tests.test_react_executor_prompt tests.test_react_executor_action_packet_schema tests.test_react_executor_protocol tests.test_react_executor_config
python -B -m unittest tests.test_planner_executor_compatibility tests.test_planner_executor_policy
python -B -m unittest discover tests
```

结果：

```text
Ran 7 tests
OK

Ran 9 tests
OK

Ran 105 tests
OK

Ran 12 tests
OK

Ran 176 tests
OK
```

当前边界：

- Step 11 只实现 `call_tool`，不实现 `call_model`、`ask_user`、命令 action、retry、fallback 或 Checker。
- `call_tool` 只通过 `ToolManager.run_tool(...)` 执行，不直接操作具体工具类。
- ReActExecutor 仍不直接执行 shell；命令行工具 action 放到 Step 14，并且必须通过 Tool 层安全工具执行。
- 工具调用已经写 ObservationStore 和 EventStream，但还未由 Checker 判定 step 是否完成；Checker 放到 Step 15。
- dispatcher 尚未接入完整 ReAct 主循环；Step 11 只保证分发层的 `call_tool` 可用。

---

## Step 12：Model action 执行

状态：已完成第一版

目标：

- 实现 `call_model` 和 `finish`。
- 区分中间模型结果和最终回答。

`call_model`：

- 用于生成中间内容。
- 结果可作为 Observation 被后续步骤消费。
- 不等于任务结束。

`finish`：

- 用于结束当前任务或整个计划。
- 必须生成 `final_answer`。
- 更新 ExecutionResult。

Prompt 输入：

```text
当前 step
输入依赖 Observation
用户需求
Analyzer/Planner 摘要
安全边界
输出要求
```

验收标准：

- model/respond 步骤可执行。
- 模型中间结果能写入 ObservationStore。
- finish 能生成最终回答。
- 最终回答包含完成/失败/跳过摘要。

测试建议：

```text
tests/test_react_executor_model_action.py
```

已完成内容：

- 在 `src/agent/react_executor.py` 中实现真实 `_handle_call_model(...)`。
- `call_model` 当前执行流程：

```text
检查 ModelManager 是否可用
解析 ActionPacket.action_args
解析 input / context / input_from
按 input_from 从 ObservationStore 获取前序结果
构造中间模型生成 prompt
emit progress_message
model_manager.generate(prompt)
emit message_delta
模型响应 -> ObservationPacket
写入 ObservationStore
emit observation_created
```

- 已明确 `call_model` 的语义：
  - 用于中间内容生成。
  - 不返回 ActionPacket。
  - 不代表整个执行完成。
  - 结果可通过 `output_key` 写入 ObservationStore，供后续 step 消费。
- 已实现模型输入来源：
  - `action_args.input`
  - `action_args.context`
  - `action_args.input_from`
  - `PlanStep.input_from`
- 已实现缺失引用处理：
  - 如果 `input_from` 无法解析，返回 `model_input_ref_missing`。
  - 缺失引用时不调用 `model_manager.generate()`。
- 已实现模型异常处理：
  - `model_manager.generate(...)` 抛异常会转为结构化 Observation。
  - 错误码为 `model_call_exception`。
- 已实现缺少 ModelManager 处理：
  - 返回 `model_manager_unavailable`。
- 已细化 `finish`：
  - 保留 `final_answer`。
  - emit `final_answer` 事件。
  - Observation data 中包含 `summary`。
  - summary 包含 task/step 状态、completed/failed/skipped/blocked step 列表和 observation 数量。
- 已新增错误码：

```text
model_manager_unavailable
model_input_ref_missing
model_call_exception
```

- 新增专项测试：

```text
tests/test_react_executor_model_action.py
```

- 同步更新 dispatcher 测试：
  - `call_model` 现在验证真实 ModelManager 调用，而不是未实现兜底。

已验证：

```text
python -B -m unittest tests.test_react_executor_model_action
python -B -m unittest tests.test_react_executor_actions
python -B -m unittest tests.test_react_executor_model_action tests.test_react_executor_tool_action tests.test_react_executor_actions tests.test_react_executor_plan_precheck tests.test_react_executor_core tests.test_tool_registry_v1 tests.test_react_executor_events tests.test_react_executor_observation tests.test_react_executor_prompt tests.test_react_executor_action_packet_schema tests.test_react_executor_protocol tests.test_react_executor_config
python -B -m unittest tests.test_planner_executor_compatibility tests.test_planner_executor_policy
python -B -m unittest discover tests
```

结果：

```text
Ran 6 tests
OK

Ran 9 tests
OK

Ran 111 tests
OK

Ran 12 tests
OK

Ran 182 tests
OK
```

当前边界：

- Step 12 只实现 dispatcher 层的 `call_model` 和细化 `finish`，仍未接入完整 ReAct 主循环。
- `call_model` 使用专用中间生成 prompt，不使用 ActionPacket 决策 prompt。
- `finish` 生成 final answer Observation 和事件，但完整 ExecutionResult 汇总仍放到 Step 20。
- `ask_user` / confirmation 仍是未实现兜底，Step 13 落地。
- 命令 action、Checker、retry/fallback、安全策略和日志仍在后续步骤。

---

## Step 13：确认与 ask_user

状态：已完成第一版

目标：

- 实现 `ask_user`、确认暂停和用户拒绝后的状态处理。

ask_user 类型：

```text
missing_info
confirmation
choice
permission
clarification
```

确认暂停流程：

```text
ActionPacket.requires_confirmation=True
  -> create PendingConfirmation
  -> emit confirmation_requested
  -> ExecutionResult.requires_user_input=True
  -> status=waiting_user
```

V1 同步确认：

- CLI 可阻塞询问。
- 如果当前环境不支持阻塞确认，则返回 pending_confirmation。

用户拒绝：

- 当前步骤 cancelled/blocked。
- 依赖步骤 skipped。
- 无依赖步骤可继续。
- 最终回答说明拒绝原因和影响。

验收标准：

- 需要确认的动作不会直接执行。
- 用户允许后可以执行当前动作。
- 用户拒绝后依赖步骤跳过。

测试建议：

```text
tests/test_react_executor_confirmation.py
```

已完成内容：

- 在 `src/agent/react_executor.py` 中实现 dispatcher 层 `_handle_ask_user(...)`。
- 新增 `dispatch_action(..., confirmed=False)` 参数：
  - 默认遇到需要确认的 action 会暂停。
  - `confirmed=True` 表示用户已允许，dispatcher 会继续执行原 action。
- 新增确认响应入口：

```python
handle_confirmation_response(context, *, approved: bool, reason: str = "") -> ObservationPacket
```

- `ask_user` 当前支持类型：
  - `missing_info`
  - `confirmation`
  - `choice`
  - `permission`
  - `clarification`
- `ask_user` 当前行为：
  - 设置 `context.requires_user_input=True`。
  - 设置 `context.user_input_request`。
  - 创建 `PendingConfirmation`，`confirmation_type` 使用 ask_type。
  - 当前 step 标记为 `waiting_user`。
  - emit `confirmation_requested`。
  - 返回 code=`user_input_required` 的 Observation。
- 需要确认的 action 当前行为：
  - `ActionPacket.requires_confirmation=True` 会暂停，不执行真实 handler。
  - `PlanStep.requires_confirmation=True` 会暂停。
  - `ToolSpec.requires_confirmation=True` 会暂停。
  - 创建 `PendingConfirmation`，保存原始 `ActionPacket`。
  - 当前 step 标记为 `waiting_user`。
  - emit `confirmation_requested`。
  - 返回 code=`confirmation_pending` 的 Observation。
- 用户允许：
  - `handle_confirmation_response(..., approved=True)` 会取出 pending action。
  - 清理 `context.pending_confirmation / requires_user_input / user_input_request`。
  - 使用 `confirmed=True` 重新 dispatch 原 action。
- 用户拒绝：
  - 当前 step 标记为 `cancelled`。
  - 依赖当前 step 或当前 step `output_key` 的后续 step 标记为 `skipped`。
  - 清理 pending confirmation 状态。
  - 返回 code=`confirmation_rejected` 的 Observation。
- 新增错误码：

```text
user_input_required
confirmation_pending
confirmation_rejected
```

- 新增专项测试：

```text
tests/test_react_executor_confirmation.py
```

- 同步更新 dispatcher 测试：
  - `ask_user` 现在验证 waiting-user 行为，而不是未实现兜底。

已验证：

```text
python -B -m unittest tests.test_react_executor_confirmation
python -B -m unittest tests.test_react_executor_actions
python -B -m unittest tests.test_react_executor_confirmation tests.test_react_executor_model_action tests.test_react_executor_tool_action tests.test_react_executor_actions tests.test_react_executor_plan_precheck tests.test_react_executor_core tests.test_tool_registry_v1 tests.test_react_executor_events tests.test_react_executor_observation tests.test_react_executor_prompt tests.test_react_executor_action_packet_schema tests.test_react_executor_protocol tests.test_react_executor_config
python -B -m unittest tests.test_planner_executor_compatibility tests.test_planner_executor_policy
python -B -m unittest discover tests
```

结果：

```text
Ran 5 tests
OK

Ran 9 tests
OK

Ran 116 tests
OK

Ran 12 tests
OK

Ran 187 tests
OK
```

当前边界：

- Step 13 实现 dispatcher 层确认暂停和响应处理，但仍未接入完整 ReAct 主循环。
- 当前不做真正 CLI 阻塞询问，只返回结构化 pending 状态，供外层 UI/CLI 处理。
- 用户允许后可以执行当前 pending action，但跨 session 持久恢复不在 V1 当前步骤内。
- 用户拒绝后的依赖跳过只基于 `depends_on` 和 `input_from` 对当前 step id / output_key 的直接引用；复杂图传播可后续增强。
- 命令 action 的安全确认仍放到 Step 14 / Step 18。

---

## Step 14：命令行工具 action

状态：已完成第一版

目标：

- 支持模型通过结构化 CommandAction 请求命令行工具。
- ReActExecutor 不直接执行 shell，只调用 Tool 层命令工具。

前置要求：

- ToolRegistry 中存在 `command_tool` 或 `shell_tool`。
- CommandAction schema 已定义。

CommandAction 字段：

```text
command
cwd
purpose
risk_level
requires_confirmation
expected_result
timeout_seconds
shell
env_policy
network_required
writes_files
target_paths
destructive_risk
approval_scope
```

安全检查：

- 工作区限制。
- 系统目录检测。
- 删除、覆盖、权限修改检测。
- 网络安装/下载执行检测。
- 危险关键词检测。
- 输出长度和超时限制。

确认策略：

```text
ask
low_risk_auto
session
always
```

`ask` 等价于早期讨论中的 `once`，表示每次单独询问。`low_risk_auto` 只允许低风险只读/测试/诊断命令自动执行。

危险命令必须 block 或强确认，不能被配置绕过。`session` 和 `always` 也只能放宽低风险或已确认范围内的命令。

验收标准：

- 结构化命令可以进入 command_tool。
- 危险命令被 block。
- 需要确认的命令不直接执行。
- 命令执行事件包含 command/cwd/exit_code/stdout_summary/stderr_summary。

测试建议：

```text
tests/test_react_executor_command_action.py
tests/test_react_executor_safety.py
```

已完成内容：

- 新增 Tool 层命令工具：

```text
src/tools/command_tool.py
```

- `CommandTool` 当前能力：
  - 只使用 `subprocess.run(..., shell=False)`。
  - 限制 `cwd` 必须在工作区内。
  - 拒绝 shell 元字符：管道、重定向、命令连接符和反引号。
  - 拒绝基础危险命令：`rm / del / erase / rmdir / rd / format / shutdown / reboot / reg / powershell / pwsh / cmd`。
  - 限制超时范围为 `1~60` 秒。
  - 输出 `command / cwd / purpose / exit_code / stdout_summary / stderr_summary / timeout_seconds`。
- `ToolManager` 已注册：

```text
command_tool
```

- `ToolRegistry` 的 `command_tool` 已更新：
  - schema 覆盖 Step 14 的 `CommandAction` 字段。
  - `metadata={"implemented": True}`。
  - 仍标记 `risk_level=high`、`requires_confirmation=True`、`workspace_scope=command`。
- ReActExecutor 已实现命令 action 预检：
  - 仅识别 `call_tool` / `fallback_to_tool` 且 `action_target in {"command_tool", "shell_tool"}`。
  - 使用 `CommandAction` dataclass 规范化结构化命令参数。
  - 危险命令直接返回 `command_blocked` Observation。
  - 工作区外 `cwd` / `target_paths` 直接 blocked。
  - `network_required=True` 在 Step 14 直接 blocked。
  - `destructive_risk=True` 直接 blocked。
  - 显式 `shell` 选择直接 blocked。
- 命令确认策略：
  - 默认配置 `ask`：需要确认，不直接执行。
  - `confirmed=True`：表示用户已允许，才进入 Tool 层。
  - `low_risk_auto`：仅允许低风险、非网络、非写文件、非破坏性命令自动执行。
  - blocked 风险不能被确认绕过。
- 命令执行事件：
  - 执行前 emit `command_started`。
  - 执行后 emit `command_finished`。
  - `command_finished` payload 包含：

```text
command
cwd
exit_code
stdout_summary
stderr_summary
success
code
duration_ms
```

- 新增专项测试：

```text
tests/test_react_executor_command_action.py
tests/test_command_tool_v1.py
```

已验证：

```text
python -B -m unittest tests.test_react_executor_command_action
python -B -m unittest tests.test_command_tool_v1
python -B -m unittest tests.test_tool_registry_v1
python -B -m unittest tests.test_command_tool_v1 tests.test_react_executor_command_action tests.test_react_executor_confirmation tests.test_react_executor_model_action tests.test_react_executor_tool_action tests.test_react_executor_actions tests.test_react_executor_plan_precheck tests.test_react_executor_core tests.test_tool_registry_v1 tests.test_react_executor_events tests.test_react_executor_observation tests.test_react_executor_prompt tests.test_react_executor_action_packet_schema tests.test_react_executor_protocol tests.test_react_executor_config
python -B -m unittest tests.test_planner_executor_compatibility tests.test_planner_executor_policy
python -B -m unittest discover tests
```

结果：

```text
Ran 4 tests
OK

Ran 2 tests
OK

Ran 13 tests
OK

Ran 122 tests
OK

Ran 12 tests
OK

Ran 193 tests
OK
```

当前边界：

- ReActExecutor 不直接执行 shell；命令只通过 `ToolManager.run_tool("command_tool", ...)` 进入 Tool 层。
- `CommandTool` 是 V1 轻量安全实现，只支持简单非 shell 命令，不支持管道、重定向、复合命令或显式 shell。
- Step 14 只实现命令 action 的结构化进入路径和基础安全/确认，不替代 Step 18 的完整安全策略。
- 默认 `ask` 策略下，命令需要用户确认；测试中使用 `confirmed=True` 模拟已确认执行。
- 当前安全检测是保守关键字/路径规则，后续 Step 18 可扩展更完整策略。

---

## Step 15：Checker

状态：已完成第一版

目标：

- 实现规则 Checker 和可选 LLM Checker。

规则 Checker 检查：

```text
success
error/code
empty output
max turns
dependency failure
safety violation
timeout
confirmation required
ToolResult.code 分类
```

LLM Checker 检查：

```text
Observation 是否满足 expected_observation
是否需要继续调用工具
是否应该 fallback
是否需要 request_replan
```

Checker 输出建议：

```text
checker_status:
  continue
  step_completed
  retry
  fallback_to_model
  fallback_to_tool
  ask_user
  request_replan
  fail
```

验收标准：

- 工具失败能触发 retry/fallback/stop。
- 空结果不会盲目进入下一步。
- 达到最大回合数会失败。
- request_replan 能正确返回。

测试建议：

```text
tests/test_react_executor_checker.py
```

已完成内容：

- 新增 Checker 模块：

```text
src/agent/react_executor_checker.py
```

- 已实现 `CheckerResult` dataclass，字段覆盖：
  - `checker_status`
  - `success`
  - `reason`
  - `code`
  - `retryable`
  - `fallback_type`
  - `fallback_tool`
  - `request_replan`
  - `requires_user_input`
  - `step_status`
  - `execution_status`
  - `metadata`
- 已固定 Checker 状态集合：

```text
continue
step_completed
retry
fallback_to_model
fallback_to_tool
ask_user
request_replan
fail
```

- 已实现 `RuleChecker`：
  - 成功且有输出 -> `step_completed`。
  - 成功但空输出 -> 不盲目进入下一步；按 step retry/fallback 策略处理，否则 `fail`。
  - `confirmation_pending / user_input_required / confirmation_required` -> `ask_user`。
  - `request_replan` code、action 或 Observation 数据标记 -> `request_replan`。
  - `command_blocked / action_blocked / dangerous_command / blocked_by_policy` 等安全类错误 -> `fail`，不 retry、不 fallback。
  - `tool_input_ref_missing / model_input_ref_missing / plan_reference_error / missing_step` 等依赖失败 -> 默认 `fail`，当 `step.on_failure=request_replan` 时返回 `request_replan`。
  - `command_timeout / timeout / tool_execution_exception / model_call_exception / temporary_network_error / rate_limited` 等可重试类错误 -> 在 step 或 code 允许且未超限时返回 `retry`。
  - `fallback_tools` 存在时返回 `fallback_to_tool`，并给出第一个 `fallback_tool`。
  - `allow_model_reasoning=True` 或 `on_failure=fallback_to_model` 时返回 `fallback_to_model`。
  - 达到 `max_step_turns` 或 `max_execution_turns` -> `fail`，code 为 `max_turns_reached`。
  - 保留并输出 `ToolResult.code` 分类，供 Step 16/17 继续消费。
- 已实现 `classify_tool_result_code(code)`，分类包括：

```text
none
user_input
request_replan
safety_violation
dependency_failure
validation_failure
resource_unavailable
timeout
retryable
non_retryable
unknown_failure
```

- 已实现可选 `LLMChecker`：
  - 只接受结构化 JSON 输出，不解析混合自然语言。
  - 检查目标聚焦 `Observation` 是否满足 `PlanStep.expected_output`、是否需要继续、fallback 或 replan。
  - LLM checker 不作为 Step 15 主路径强依赖；不可用、输出非法或异常时可由 facade 保留规则 Checker 结果。
- 已实现 `ReActChecker` facade：
  - 默认先执行 `RuleChecker`。
  - LLM Checker 仅在显式启用且规则结果允许继续/完成、并存在 `expected_output` 时参与。
- `ReActExecutor` 已新增：
  - `self.checker`
  - `check_observation(...)` 便捷入口
  - 当前未改变 dispatcher 和旧 Executor 行为。
- 新增专项测试：

```text
tests/test_react_executor_checker.py
```

测试覆盖：

- 成功 Observation -> `step_completed`。
- 空成功 Observation -> `fail` 或在 step 明确可重试时 `retry`。
- timeout / 可重试 code -> `retry`。
- 最大 step/execution turns -> `fail`。
- 成功 Observation 在 execution turn 边界仍可完成。
- 非重试失败 + `fallback_tools` -> `fallback_to_tool`。
- `allow_model_reasoning` -> `fallback_to_model`。
- 确认/用户输入 -> `ask_user`。
- request_replan -> `request_replan`。
- 安全阻断 -> `fail`，不 retry/fallback。
- 依赖失败 + `on_failure=request_replan` -> `request_replan`。
- ToolResult.code 分类。
- LLM Checker 结构化 JSON 输出解析。
- `ReActExecutor.check_observation(...)` 可用。

已验证：

```text
python -B -m unittest tests.test_react_executor_checker
python -B -m unittest tests.test_react_executor_checker tests.test_command_tool_v1 tests.test_react_executor_command_action tests.test_react_executor_confirmation tests.test_react_executor_model_action tests.test_react_executor_tool_action tests.test_react_executor_actions tests.test_react_executor_plan_precheck tests.test_react_executor_core tests.test_tool_registry_v1 tests.test_react_executor_events tests.test_react_executor_observation tests.test_react_executor_prompt tests.test_react_executor_action_packet_schema tests.test_react_executor_protocol tests.test_react_executor_config
python -B -m unittest tests.test_planner_executor_compatibility tests.test_planner_executor_policy
python -B -m unittest discover tests
```

结果：

```text
Ran 16 tests
OK

Ran 138 tests
OK

Ran 12 tests
OK

Ran 209 tests
OK
```

当前边界：

- Step 15 只完成 Checker 判定层，不实现真正 retry sleep、重试调度或 fallback action 执行；这些进入 Step 16/17。
- `ReActExecutor.check_observation(...)` 已可被后续主循环调用，但当前 `execute()` 仍是骨架遍历，尚未接入完整 Thought -> Action -> Observation -> Checker 主循环。
- LLM Checker 是可选结构化入口，默认不参与主路径，避免测试和本地开发依赖真实模型。
- 安全策略仍沿用 Step 14 的命令前置检查；更完整安全策略放到 Step 18。

---

## Step 16：失败处理与重试

状态：已完成第一版

目标：

- 实现指数退避重试。
- 实现按 `ToolResult.code` 分类处理。

可重试：

```text
timeout
temporary_network_error
rate_limited
model_call_failed
tool_transient_error
schema_invalid
```

不重试：

```text
permission_denied
blocked_by_policy
missing_required_argument
file_not_found
dangerous_operation
tool_not_found_after_repair
```

指数退避：

```text
sleep = min(base * 2 ** attempt, max)
```

测试中应避免真实 sleep，可通过配置设为 0 或 mock。

验收标准：

- retryable 步骤按 max_retries 重试。
- 不可重试错误不会浪费重试次数。
- 重试记录写入 Observation 和日志。

测试建议：

```text
tests/test_react_executor_retry.py
```

已完成内容：

- 新增 retry 策略模块：

```text
src/agent/react_executor_retry.py
```

- 已实现 `RetryDecision` dataclass，字段覆盖：
  - `can_retry`
  - `reason`
  - `code`
  - `failure_class`
  - `retry_count`
  - `retry_attempt`
  - `next_attempt`
  - `max_retries`
  - `backoff_seconds`
  - `source_observation_id`
  - `source_packet_id`
  - `action_type`
  - `action_target`
  - `metadata`
- 已实现 `RetryPolicy`：
  - 按 `ToolResult.code` / Checker `failure_class` 判断是否可重试。
  - 支持指数退避：

```text
sleep = min(base * 2 ** retry_count, max)
```

  - 支持注入 `sleep_fn`；测试中使用 `None` 或 list append，避免真实 sleep。
  - 明确 `max_retries` 语义：表示失败后的“重试次数”，不是总 action attempt 数。
- 已补充 Checker 可重试 code：

```text
schema_invalid
```

- 已固定 retry 相关 code：

```text
retry_scheduled
retry_target_not_found
retry_not_allowed
retry_not_retryable
retry_exhausted
retry_unsupported_action
retry_sleep_failed
```

- 已扩展事件协议：

```text
retry_scheduled
retry_finished
retry_exhausted
```

- `EventStream.to_user_timeline()` 已支持 retry 事件映射为：

```text
render_as=retry_record
title=Retry scheduled / Retry finished / Retry exhausted
```

- `ReActExecutor` 已新增：
  - `self.retry_policy`
  - 构造参数 `retry_policy`
  - 构造参数 `retry_sleep_fn`
  - `_handle_retry(...)` 第一版真实实现
  - `_find_retry_target_observation(...)`
  - `_is_retryable_observation_target(...)`
  - `_retry_packet_from_observation(...)`
- `retry_step` 当前执行流程：

```text
定位当前 step 最近一次失败的 call_tool / call_model / fallback action Observation
调用 Checker 得到 CheckerResult
调用 RetryPolicy 生成 RetryDecision
不可重试 -> 生成 retry_not_retryable / retry_exhausted 等 Observation
可重试 -> emit retry_scheduled
可选执行 backoff sleep_fn
重建原 call_tool / call_model ActionPacket
再次走 dispatch_action(...)
emit retry_finished
把 retry metadata 写入新 Observation.checker_result["retry"]
```

- 重试不会绕过已有边界：
  - 工具调用仍走 `ToolManager.run_tool(...)`。
  - 模型调用仍走 `ModelManager.generate(...)`。
  - 命令 action 仍走 command Tool 和确认/安全检查。
  - ActionPacket dispatcher 校验仍生效。
- `_record_observation(...)` 已更新 step runtime 状态：
  - `last_action_id`
  - `last_observation_id`
  - `attempts`
  - `error_code`
  - `message`
- `_record_observation(...)` 已避免同一 Observation 被重复写入 ObservationStore。

新增专项测试：

```text
tests/test_react_executor_retry.py
```

测试覆盖：

- 指数退避公式与最大值截断。
- timeout / retryable failure -> retry decision。
- 不可重试 code -> 不重试。
- retry exhausted 使用 `retry_count` 判断，不把 action attempt 误当重试次数。
- `retry_step` 能重放失败工具 action 并成功。
- 重试 metadata 写入 Observation。
- `retry_scheduled / retry_finished` 事件写入 EventStream。
- 不可重试错误不会再次调用工具。
- 可通过 failed packet id 精确定位重试目标。

已验证：

```text
python -B -m unittest tests.test_react_executor_retry
python -B -m unittest tests.test_react_executor_retry tests.test_react_executor_checker tests.test_react_executor_actions tests.test_react_executor_events tests.test_react_executor_tool_action tests.test_react_executor_model_action tests.test_react_executor_command_action tests.test_react_executor_confirmation
python -B -m unittest tests.test_react_executor_retry tests.test_react_executor_checker tests.test_command_tool_v1 tests.test_react_executor_command_action tests.test_react_executor_confirmation tests.test_react_executor_model_action tests.test_react_executor_tool_action tests.test_react_executor_actions tests.test_react_executor_plan_precheck tests.test_react_executor_core tests.test_tool_registry_v1 tests.test_react_executor_events tests.test_react_executor_observation tests.test_react_executor_prompt tests.test_react_executor_action_packet_schema tests.test_react_executor_protocol tests.test_react_executor_config
python -B -m unittest tests.test_planner_executor_compatibility tests.test_planner_executor_policy
python -B -m unittest discover tests
```

结果：

```text
Ran 7 tests
OK

Ran 68 tests
OK

Ran 145 tests
OK

Ran 12 tests
OK

Ran 216 tests
OK
```

当前边界：

- Step 16 已实现 retry 决策和 `retry_step` action 重放，但完整主循环尚未自动消费 CheckerResult；后续主循环接入时可直接调用 `_handle_retry` 或 RetryPolicy。
- 当前 retry 记录已写入 Observation 和 EventStream；真正 JSONL 日志写入仍按原计划放到 Step 19。
- fallback action 仍是 Step 17 内容；Step 16 不实现 `fallback_to_model` / `fallback_to_tool` 的真实执行。
- retry 只重试失败的 tool/model/fallback action，不重试 `ask_user`、`finish`、`fail`、`blocked`、`cancel` 等控制动作。

---

## Step 17：Fallback

状态：已完成第一版

目标：

- 实现 `fallback_to_model` 和 `fallback_to_tool`。

fallback_to_model：

- 原工具失败后，调用模型尝试完成当前步骤。
- Observation 标记：

```text
fallback_used=True
fallback_type=model
```

- 最终回答说明结果可能不如工具结果可靠。

fallback_to_tool：

- 优先使用 PlanStep.fallback_tools 或 ToolSpec.fallback_tools。
- 不允许模型发明不存在的工具。
- 如果 fallback tool 也不存在，进入 `fallback_to_model` 或 `request_replan`。

命令行 fallback：

- 视为 fallback_to_tool，目标工具是 `command_tool`。
- 必须结构化 CommandAction。
- 必须安全检查和确认策略。

验收标准：

- 工具失败可转模型。
- 工具失败可转备用工具。
- fallback 结果可被后续 input_from 消费。
- fallback 事件和日志完整记录。

测试建议：

```text
tests/test_react_executor_fallback.py
```

已完成内容：

- 新增 `src/agent/react_executor_fallback.py`：
  - `FallbackPolicy` 只负责决策，不执行模型或工具。
  - `FallbackDecision` 使用 dataclass，可序列化为结构化 metadata。
  - 固定 fallback 状态码：`fallback_scheduled`、`fallback_target_not_found`、`fallback_tool_not_available`、`fallback_tool_not_allowed`、`fallback_model_not_allowed`、`fallback_not_allowed`、`fallback_unsupported_action`。
- `ReActExecutor` 已接入 `fallback_to_model` / `fallback_to_tool`：
  - fallback 源 Observation 只允许定位失败的真实 `call_tool` / `call_model` 结果，避免把 `ask_user`、`retry_step`、`fallback_to_*` 控制动作误当作失败源。
  - `fallback_to_model` 会基于失败 Observation、PlanStep、用户输入和历史构造结构化模型 prompt，并通过 `_handle_call_model` 调用模型管理器。
  - `fallback_to_tool` 优先使用请求目标、`PlanStep.fallback_tools`、`ToolSpec.fallback_tools` 中可用的 fallback tool；不存在时按策略降级到 model fallback 或返回结构化失败。
  - fallback tool 内部执行会转换成 `call_tool`，仍经过 `ToolRegistry`、`ToolManager`、ToolSpec 参数校验、命令安全检查和确认流程。
  - 命令 fallback 视为 `fallback_to_tool(command_tool)`，不会绕过 Step 14 的命令安全和确认机制。
- Observation / EventStream 已记录 fallback 语义：
  - fallback 成功或等待确认的最终 Observation 会标记 `fallback_used=True`、`fallback_type=model/tool`。
  - `checker_result["fallback"]` 保存决策 metadata、源 Observation、fallback Observation、执行成功状态和 `result_code`。
  - fallback 决策码 `code` 保持为 `fallback_scheduled`；实际执行结果码记录为 `result_code`，避免覆盖调度语义。
  - 新增用户可见事件 `fallback_started` / `fallback_finished`，并在 timeline 中渲染为 `fallback_record`。

验证方式：

```text
python -B -m unittest tests.test_react_executor_actions
python -B -m unittest tests.test_react_executor_fallback
python -B -m unittest tests.test_react_executor_retry
python -B -m unittest discover tests
```

验证结果：

```text
tests.test_react_executor_actions: Ran 9 tests, OK
tests.test_react_executor_fallback: Ran 7 tests, OK
tests.test_react_executor_retry: Ran 7 tests, OK
unittest discover tests: Ran 223 tests, OK
```

当前边界：

- Step 17 已实现 fallback action 的真实执行路径，但完整主循环尚未自动根据 CheckerResult 消费 `fallback_to_model` / `fallback_to_tool`；主循环接入时可直接调用当前 handler 和 `FallbackPolicy`。
- fallback 日志目前进入 Observation / EventStream metadata；真正 JSONL 日志落盘仍按 Step 19 实现。
- fallback 不删除、不替换旧顺序 Executor，也不改变 Analyzer / Planner 已有接口。
- fallback tool 不允许绕过 ToolRegistry / ToolManager；模型输出仍必须是结构化 ActionPacket，不能用混合自然语言让执行器猜。
- 文件/命令的更完整安全策略仍进入 Step 18；当前命令 fallback 复用 Step 14 的基础命令安全和确认机制。

---

## Step 18：安全策略

状态：已完成第一版

目标：

- 实现 ReActExecutor 执行前最后一道安全检查。

必须尊重：

```text
plan.can_execute=False
plan.plan_validation_status=invalid
task.action_policy=block
step.requires_confirmation
ActionPacket.requires_confirmation
ToolSpec.requires_confirmation
```

文件安全：

- 工作区内普通写文件默认允许。
- 用户明确禁止的文件不能修改，只输出建议。
- 工作区外禁止写入、删除、移动、重命名。
- 工作区父级目录外只允许读取。
- 敏感系统路径 block。

危险动作：

- 删除文件。
- 执行代码。
- shell 命令。
- 修改权限。
- 安装卸载。
- 网络下载并执行。

这些必须确认或 block。

验收标准：

- 危险动作不会绕过确认。
- 工作区外修改被拒绝。
- 系统路径被 block。
- 安全拒绝会生成用户可理解的说明。

测试建议：

```text
tests/test_react_executor_safety.py
```

已完成内容：

- 新增 `src/agent/react_executor_safety.py`：
  - `SafetyIssue`、`SafetyDecision`、`SafetyPolicy` 均使用 dataclass。
  - 固定安全状态码：`safety_allowed`、`safety_confirmation_required`、`safety_blocked`。
  - 安全策略只做纯决策，不执行模型、工具、命令或文件操作。
- `ReActExecutor` 已接入统一执行前安全口：
  - `dispatch_action(...)` 在 ActionPacket schema 校验之后、ToolManager / ModelManager 调用之前运行 `SafetyPolicy`。
  - safety `block` 会生成结构化 Observation，code 为 `safety_blocked`，并发出用户可见 `system_notice`。
  - safety `confirm` 会进入现有 PendingConfirmation / `confirmation_requested` 流程，不会直接调用 ToolManager。
  - `COMMAND_BLOCKED_CODE` 兼容保留为 `safety_blocked` 别名。
- 安全策略当前覆盖：
  - `task.action_policy=block`。
  - `step.requires_confirmation`、`ActionPacket.requires_confirmation`、`ToolSpec.requires_confirmation`。
  - `ToolSpec.risk_level=blocked` 直接 block。
  - `risk_level=high`、`workspace_scope=code_execution`、`workspace_scope=command` 需要确认。
  - `write_workspace` / `command` / `code_execution` 的写入目标必须在 workspace 内。
  - 用户显式禁止路径：读取 `task` / `plan` / `step` 的 `forbidden_paths`、`disallowed_paths`、`protected_paths`，以及对应 `metadata` 字段。
  - Windows / POSIX 敏感系统路径 block。
  - 命令 action 的空命令、shell 元字符、显式 shell、网络命令、destructive risk、危险 executable、下载命令 block。
  - 命令 action 在默认 `ask` 策略下需要确认，已确认后仍会再次经过 block 级安全检查。
- `execute(...)` 的 plan precheck 已接入安全策略：
  - Planner 已输出的明显不安全 step 会在骨架遍历前 block。
  - 需要确认的 step 会在骨架遍历前进入 waiting_user。
  - 既有 `plan.can_execute=False`、`plan.plan_validation_status=invalid`、`task.action_policy=block` 等前置策略保持原有行为。

验证方式：

```text
python -B -m unittest tests.test_react_executor_safety
python -B -m unittest tests.test_react_executor_command_action
python -B -m unittest tests.test_react_executor_confirmation
python -B -m unittest tests.test_react_executor_plan_precheck
python -B -m unittest discover tests
```

验证结果：

```text
tests.test_react_executor_safety: Ran 7 tests, OK
tests.test_react_executor_command_action: Ran 4 tests, OK
tests.test_react_executor_confirmation: Ran 5 tests, OK
tests.test_react_executor_plan_precheck: Ran 10 tests, OK
unittest discover tests: Ran 230 tests, OK
```

当前边界：

- Step 18 是 V1 工程安全兜底，不替代 Tool 层自己的安全实现；例如 `CommandTool` 仍保留二次防护。
- 当前文件安全基于结构化参数中的常见路径字段识别：`file_path`、`path`、`target_path`、`target_paths`、`output_path` 等；后续新增工具时需要在 ToolSpec 参数约定中保持结构化路径字段。
- 工作区内普通写文件默认允许；删除、移动、重命名、权限修改等危险文件操作当前进入确认，不直接执行。
- 网络搜索类普通工具不默认 block；命令形式的网络下载/执行会 block。更细的网络白名单可后续在配置层扩展。
- 真正 JSONL 安全日志仍在 Step 19 实现；当前安全结果进入 Observation、EventStream 和 checker_result metadata。

---

## Step 19：日志

状态：已完成第一版

目标：

- 写入 `logs/react_executor.log` JSONL。

日志字段：

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
event_count
observation_id
```

要求：

- 不默认记录完整 prompt。
- 不默认记录敏感参数。
- 记录 prompt 摘要和长度。
- 记录 schema 校验结果。
- 日志写入失败不影响执行。

验收标准：

- 每次执行至少写一条开始和结束日志。
- 每个 ActionPacket/Observation 有日志记录。
- 特殊策略和失败策略有日志记录。

测试建议：

```text
tests/test_react_executor_logging.py
```

已完成内容：

- 新增 `src/agent/react_executor_logging.py`：
  - `ReActExecutorLogger` 负责 JSONL 写入。
  - `LogWriteResult` 用于记录日志写入是否成功。
  - 日志写入异常会被 logger 捕获并记录到 `write_error_count` / `last_write_error`，不会影响执行链路。
- 默认日志路径使用 `ReActExecutorConfig.react_executor_log_path`，即配置中的 `logs/react_executor.log`。
- `ReActExecutor` 已接入日志：
  - `execute(...)` 写 `execution_started` / `execution_finished`。
  - 执行异常写 `execution_exception` 后继续抛出原异常。
  - `dispatch_action(...)` 写 `action_packet`，包含 schema 校验结果、schema errors、attempt、repair_attempts 预留字段。
  - `dispatch_action(...)` 写 `safety_decision`，包括 safety allow / confirm / block。
  - plan precheck 的 safety block / confirmation 也写 `safety_decision`，metadata 标记 `scope=plan_precheck`。
  - `_handle_call_model(...)` 写 `model_prompt`，默认只写 `prompt_length`、`prompt_summary`、`input_summary`。
  - `_record_observation(...)` 写 `observation`，覆盖 tool/model/user/retry/fallback/safety 等最终 Observation。
- JSONL 记录包含 Step 19 要求字段：

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
event_count
observation_id
```

- 日志安全处理：
  - 默认不写完整 prompt；只有 `config.log_full_prompt=True` 时才写 `full_prompt`。
  - action args、Observation data、model consumable observation 都只写摘要。
  - 调用 `sanitize_sensitive(...)` 脱敏 `api_key`、`token`、`password`、`secret`、`authorization` 等字段。
  - raw tool result、raw model output、raw observation 不直接写入日志。

验证方式：

```text
python -B -m unittest tests.test_react_executor_logging
python -B -m unittest tests.test_react_executor_actions
python -B -m unittest tests.test_react_executor_model_action
python -B -m unittest tests.test_react_executor_safety
python -B -m unittest discover tests
```

验证结果：

```text
tests.test_react_executor_logging: Ran 6 tests, OK
tests.test_react_executor_actions: Ran 9 tests, OK
tests.test_react_executor_model_action: Ran 6 tests, OK
tests.test_react_executor_safety: Ran 7 tests, OK
unittest discover tests: Ran 236 tests, OK
```

当前边界：

- Step 19 是 JSONL 开发日志，不替代用户可见 `EventStream`，也不替代 `ObservationStore` 的真实结果保存。
- 当前主循环仍未完成，日志只覆盖当前已存在的 `execute` 骨架、`dispatch_action`、tool/model/user/retry/fallback/safety 路径；后续主循环接入 ActionPacket repair 时需要补充 `repair_attempts` 的真实计数。
- 日志是追加写入；暂未实现轮转、按 execution_id 分文件或日志清理策略。
- 日志写入失败不会影响执行，但当前只记录在 logger 实例状态中；如果需要运维告警，可在后续增加内部 event 或监控钩子。

---

## Step 20：ExecutionResult 汇总

状态：已完成第一版

目标：

- 最终输出结构化 ExecutionResult。
- 不只返回最后一步文本。

汇总规则：

- 全部成功：总结关键 Observation 和最终结果。
- 部分失败：说明已完成、失败、跳过、fallback。
- 等待用户：返回确认/补参请求。
- request_replan：返回重规划原因和当前进度。
- block：返回阻断原因。

用户输出要求：

```text
做了什么
做到哪里
哪些成功
哪些失败
为什么失败
后续怎么继续
```

验收标准：

- 成功任务输出最终答案。
- 部分失败任务输出详细说明。
- 等待用户任务输出明确问题。
- request_replan 任务输出明确原因。

测试建议：

```text
tests/test_react_executor_result.py
```

已完成内容：

- 新增 `src/agent/react_executor_result.py`：
  - `ExecutionResultBuilder` 负责从运行时上下文汇总最终结果。
  - `ResultSummary` 承载 `output`、`summary`、`request_replan`、`replan_reason` 和 metadata。
- `ReActExecutionContext` 新增：
  - `request_replan`
  - `replan_reason`
- `ReActExecutor` 已接入：
  - `_build_result(...)` 统一调用 `ExecutionResultBuilder.build(...)`。
  - `_handle_request_replan(...)` 会写入 `context.request_replan=True`、`context.replan_reason` 和 `context.error_code=request_replan`。
  - `ExecutionResult.request_replan` / `ExecutionResult.replan_reason` 由汇总器统一填充。
- 汇总内容覆盖：
  - 当前 status / goal。
  - 已有 context output / summary 的关键说明。
  - step/task runtime status 进度计数。
  - 成功 Observation 摘要。
  - 失败 Observation 摘要和失败原因。
  - skipped / blocked / waiting_user step。
  - fallback 使用情况。
  - request_replan 原因。
  - 下一步建议。
- 用户输出语义覆盖：
  - 做了什么：goal、success/failure observation。
  - 做到哪里：progress/status counts。
  - 哪些成功：Succeeded 行。
  - 哪些失败：Failed / Blocked / Skipped 行。
  - 为什么失败：Observation error/code/message。
  - 后续怎么继续：Next 行。

验证方式：

```text
python -B -m unittest tests.test_react_executor_result
python -B -m unittest tests.test_react_executor_actions tests.test_react_executor_fallback tests.test_react_executor_safety
python -B -m unittest tests.test_react_executor_plan_precheck tests.test_react_executor_confirmation
python -B -m unittest discover tests
```

验证结果：

```text
tests.test_react_executor_result: Ran 6 tests, OK
actions/fallback/safety: Ran 23 tests, OK
plan_precheck/confirmation: Ran 15 tests, OK
unittest discover tests: Ran 242 tests, OK
```

当前边界：

- Step 20 只负责 ExecutionResult 汇总，不实现真正 Thought -> Action -> Observation 主循环。
- 当前 execute 主路径仍是骨架遍历，因此骨架模式下会汇总 `react_action_loop_not_implemented` 的 blocked 状态；后续主循环接入后，汇总器可直接消费真实运行状态。
- 当前 output 是稳定的多行文本格式，后续可在 API 层按 UI 需要拆成更结构化的展示块。
- final_answer 事件仍由现有执行路径发出；Step 20 不重写 EventStream 事件协议。

---

## Step 21：ReActExecutor fixture 回归

状态：已完成第一版

目标：

- 建立 ReActExecutor 回归样例集。

建议新增：

```text
tests/fixtures/react_executor_cases.json
tests/test_react_executor_v1.py
```

建议覆盖 20-40 条：

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
用户禁止编辑某文件 -> 只输出建议
不存在工具 -> repair 后 fallback
```

fixture 断言：

- `status`
- `success`
- `events`
- `observations`
- `failed_step_id`
- `requires_user_input`
- `request_replan`
- `fallback_used`
- 工具调用次数
- 模型调用次数

验收标准：

- fixture 不污染真实 logs/storage。
- 每条样例断言关键行为，不只断言不报错。

已完成内容：

- 新增 `tests/fixtures/react_executor_cases.json`：
  - 当前包含 22 条 ReActExecutor V1 fixture。
  - 覆盖 tool 成功、model 成功、read -> summarize、read -> extract -> write、retry 成功、fallback_to_model、fallback_to_tool、命令确认、确认拒绝与依赖跳过、不可执行 plan、invalid plan、input_from 缺失、schema invalid、request_replan、file write、task policy block、部分失败、禁止路径、安全 precheck、缺失工具、finish action、fallback 工具不可用等路径。
- 新增 `tests/test_react_executor_v1.py`：
  - 通过 fixture 参数化构造 `TaskPlan / TaskUnit / PlanStep / ActionPacket`。
  - 使用临时 workspace、临时 `react_executor.log`、fake ToolManager 和 fake ModelManager，避免污染真实 logs/storage。
  - 支持 `execute`、`dispatch`、`handle_confirmation`、`set_step_status`、`build_result` 多类 fixture 操作。
  - 断言 `ExecutionResult.status/success/error_code/failed_step_id/requires_user_input/request_replan/output`。
  - 断言事件类型、Observation 数量与 code、fallback 使用次数、工具/模型调用次数、step 状态和 JSONL 日志记录类型。
- 修正 `ExecutionResultBuilder`：
  - 当 precheck 或状态初始化阶段没有 failed Observation 时，仍会把 failed/blocked/cancelled `StepRuntimeState.message` 汇总进最终输出，避免不可执行 plan 的失败原因丢失。

验证方式：

```text
python -B -m unittest tests.test_react_executor_v1
Ran 1 test in 0.393s
OK

python -B -m unittest discover tests
Ran 243 tests in 2.048s
OK
```

当前边界：

- Step 21 是 fixture 回归层，重点保护已有 dispatcher、action、checker、retry、fallback、safety、logging、result 行为组合。
- 当前 fixture 会直接调用 `execute` 或 dispatcher 级操作，尚未覆盖完整 Thought -> Action -> Observation 主循环中的模型 ActionPacket 生成与 repair 循环。
- `ActionPacket schema invalid` 覆盖的是执行器收到非法结构化 packet 后的失败处理，不等同于完整模型输出 repair 流。
- fixture 使用临时 workspace 和临时日志文件；完整测试套件中的其他日志测试仍可能创建默认 `logs/react_executor.log`，该文件不属于 Step 21 fixture 输出。

---

## Step 22：端到端串联测试

状态：已完成第一版

目标：

- 验证 Analyzer -> Planner -> ReActExecutor 主链路。

建议新增：

```text
tests/test_analyzer_planner_react_executor_pipeline.py
```

优先覆盖 5-10 条：

```text
计算 2+3*4
读取 README.md 并总结
搜索主题并总结
读取文件提取重点后写入文件
chat 模式不执行工具
危险命令 block
缺参澄清
确认暂停
```

验收标准：

- Analyzer 输出能被 Planner 消费。
- Planner 输出能被 ReActExecutor 消费。
- ReActExecutor 能产生 ExecutionResult 和事件。
- 特殊策略不误执行工具。

验证命令：

```text
python -B -m unittest tests.test_analyzer_planner_react_executor_pipeline
python -B -m unittest discover tests
```

已完成内容：

- 新增 `tests/test_analyzer_planner_react_executor_pipeline.py`：
  - 使用真实 `ComplexityAnalyzer`、真实 `Planner` 和 `ReActExecutor` 串联。
  - 使用临时 analyzer/planner/react_executor 配置、临时 workspace、临时日志路径。
  - 使用 fake ToolManager / fake ModelManager，确保端到端测试不触发真实外部工具或模型调用。
- 覆盖 8 条 Analyzer -> Planner -> ReActExecutor 链路：
  - `计算 2+3*4` -> Analyzer 命中 calculate，Planner 生成 math_calculator micro plan，ReActExecutor 接收 plan 并产生 ExecutionResult/events。
  - `读取 README.md 并总结` -> read_file + summarize，Planner 生成 document_parser -> text_processor 依赖计划。
  - `搜索关于 Python 测试框架的资料并总结` -> search + summarize，Planner 生成 search_tool -> text_processor 依赖计划。
  - `读取 README.md 提取重点并写入文件 summary.md，关于 README 重点` -> read_file + extract + write_file，Planner 生成 document_parser -> text_processor -> file_writer 依赖计划。
  - chat 模式 -> Planner 输出 model-only respond plan，ReActExecutor 进入 chat skeleton 边界，不调用工具。
  - 危险命令 -> Analyzer block，Planner 输出 blocked plan，ReActExecutor blocked，不调用工具。
  - 缺参澄清 -> Analyzer requires_clarification，Planner 输出 clarify plan，ReActExecutor waiting_user。
  - 删除文件确认 -> Analyzer requires_confirmation，Planner 输出 confirm plan，ReActExecutor waiting_user 并生成 pending_confirmation。
- 断言重点：
  - `plan.source_trace_id == task.trace_id`，确认 Analyzer 输出被 Planner 保留并传递给 ReActExecutor。
  - `result.plan_id == plan.plan_id`、`result.source_trace_id == task.trace_id`，确认 ReActExecutor 消费 Planner plan。
  - 可执行计划当前稳定返回 `react_action_loop_not_implemented`，对应 Step 8 骨架边界。
  - 策略类计划返回 `chat_mode_not_implemented / task_policy_blocked / clarification_required / confirmation_required`。
  - 特殊策略场景不调用 ToolManager、不调用 ModelManager，避免误执行。
  - 文件流水线保留 `depends_on / input_from` 依赖关系。

验证方式：

```text
python -B -m unittest tests.test_analyzer_planner_react_executor_pipeline
Ran 2 tests in 0.154s
OK

python -B -m unittest discover tests
Ran 245 tests in 2.187s
OK
```

当前边界：

- Step 22 验证的是三层结构可串联和策略边界可闭环，不表示 `ReactAgent.run()` 默认主链路已经切到 ReActExecutor；主链路切换仍放在 Step 23。
- 普通可执行计划仍停在 ReActExecutor skeleton traversal，尚未通过模型生成 ActionPacket 执行真实 Thought -> Action -> Observation 主循环。
- 为避免污染和外部副作用，测试使用 fake ToolManager / fake ModelManager；因此 Step 22 不验证真实工具输出内容，只验证三层协议、计划、状态和事件衔接。
- chat 模式当前进入 `chat_mode_not_implemented` 骨架边界，后续完整主循环接入后应改为结构化 model action 执行。

---

## Step 23：主链路替换旧 Executor

状态：已完成第一版

目标：

- 将 `ReactAgent` 默认执行器从旧 Executor 切换到 ReActExecutor。

前置条件：

- ReActExecutor fixture 通过。
- 当前旧 Executor 兼容测试有替代覆盖。
- Analyzer/Planner 所有测试通过。

建议策略：

- 先支持配置开关：

```text
EXECUTOR_TYPE=react
```

- 默认仍可在开发阶段显式切回旧 Executor 用于迁移验证，但不作为失败自动回退。
- 稳定后再移除旧 Executor 或标记 deprecated。

验收标准：

- `ReactAgent.run()` 主链路可走 ReActExecutor。
- 原有策略场景 block/clarify/confirm/chat/missing_tools 仍正确。
- 不破坏现有 71 条测试。

测试建议：

```text
tests/test_react_agent_with_react_executor.py
```

已完成内容：

- 更新 `src/agent/react_agent.py`：
  - `ReactAgent` 默认执行器从旧 `Executor` 切换为 `ReActExecutor`。
  - 新增 `executor_type` 构造参数，支持 `react / react_executor / reactexecutor` 和 `legacy / old / executor / sequential`。
  - 新增环境变量开关 `EXECUTOR_TYPE`；未显式传入时默认值为 `react`。
  - 显式传入 `executor` 实例时仍保持最高优先级，便于测试、兼容和外部注入。
  - 支持传入 `react_executor_config` 和 `tool_registry`，方便测试或运行时约束 ReActExecutor 的 workspace/log/tool 范围。
- 更新 `tests/test_analyzer_planner_pipeline.py`：
  - 依赖旧顺序 Executor 行为的现有 `ReactAgent` 测试显式指定 `executor_type="legacy"`，保留旧链路回归保护。
- 新增 `tests/test_react_agent_with_react_executor.py`：
  - 验证 `ReactAgent.run()` 默认创建并使用 `ReActExecutor`。
  - 验证 `executor_type="legacy"` 能切回旧 Executor。
  - 验证 `EXECUTOR_TYPE=legacy` 环境变量能切回旧 Executor。
  - 验证外部注入 executor 仍优先于类型选择。
  - 覆盖 ReActExecutor 主链路策略短路：block / clarify / confirm / chat / missing_tools。
  - 覆盖普通可执行计划当前进入 ReActExecutor skeleton 边界，且不误调用 ToolManager / ModelManager。

验证方式：

```text
python -B -m unittest tests.test_react_agent_with_react_executor tests.test_analyzer_planner_pipeline
Ran 9 tests in 0.190s
OK

python -B -m unittest discover tests
Ran 250 tests in 2.194s
OK
```

当前边界：

- 旧 `Executor` 未删除，仍作为开发期兼容和回归保护，可通过 `executor_type="legacy"` 或 `EXECUTOR_TYPE=legacy` 显式切换，但这不是 ReActExecutor 失败后的自动回退。
- `ReactAgent.run()` 默认主链路已经走 `ReActExecutor`，但普通可执行计划仍停在 ReActExecutor skeleton traversal，尚未进入完整模型 ActionPacket repair + Thought -> Action -> Observation 主循环。
- Agent 层只返回 `execution.output`；需要结构化 `ExecutionResult/events/observations` 的调用方后续应直接使用 ReActExecutor 或扩展 Agent API。
- 当前默认 `ReActExecutor` 会使用默认配置路径；测试中通过 `react_executor_config` 指向临时 workspace/log，避免污染真实运行数据。

---

## Step 24：文档回写与 V1 验收

状态：已完成第一版

目标：

- 完成 ReActExecutor V1 阶段总结。
- 更新设计文档和开发进度。

需要更新：

```text
src/agent/ReActExecutor层开发步骤与进度.md
src/agent/ReActExecutor层设计决策汇总.md
README.md 可选
```

V1 验收命令：

```text
python -B -m unittest discover tests
```

V1 验收内容：

- ActionPacket 协议可用。
- ObservationStore 可用。
- 事件流可用。
- 工具调用可用。
- 模型调用可用。
- retry/fallback 可用。
- 安全确认可用。
- 日志可用。
- 主链路可用。

已完成内容：

- 更新 `src/agent/ReActExecutor层设计决策汇总.md`：
  - 新增“Step 24 回写：V1 实现验收状态”。
  - 汇总当前已落地模块：protocol、prompt、ObservationStore、EventStream、ToolRegistry、command tool、ReActExecutor 主类、Checker、Retry、Fallback、Safety、Logging、ExecutionResult、ReactAgent 主链路。
  - 汇总当前测试覆盖：ReActExecutor 单元测试、fixture 回归、Analyzer -> Planner -> ReActExecutor 端到端串联、ReactAgent + ReActExecutor 主链路测试。
  - 明确已满足能力：ActionPacket 协议、Observation、事件流、工具/模型/user/command action、retry/fallback/safety/checker/log/result、默认主链路接入。
  - 明确当前未满足目标态：完整模型驱动 `Thought -> Action -> Observation` 主循环、ActionPacket repair 主路径、chat 自动 `call_model`、Agent API 直接返回结构化 ExecutionResult/events。
- 更新 `src/agent/ReActExecutor层开发步骤与进度.md`：
  - Step 24 状态更新为已完成第一版。
  - 当前总体进度推进到 Step 24。
  - 下一轮建议改为进入下一阶段：实现 ReActExecutor 主循环中的模型 ActionPacket 决策与 repair。
- README 状态：
  - 项目根目录当前没有 `README.md`。
  - Step 24 中 README 为可选项，本轮不新增 README，避免在 ReActExecutor 阶段收尾时扩大文档范围。

验证方式：

```text
python -B -m unittest discover tests
Ran 250 tests in 2.322s
OK
```

当前边界：

- Step 24 是 ReActExecutor V1 工程基础层的文档回写和验收，不补做完整模型驱动 ReAct 主循环。
- `ReactAgent.run()` 默认已经走 ReActExecutor；旧 Executor 仅保留显式兼容切换，不作为正式 fallback。
- 普通可执行计划当前仍停在 skeleton traversal，并稳定返回 `react_action_loop_not_implemented` 边界信息。
- 下一阶段应优先实现 `execute()` 主路径中的模型 ActionPacket 生成、schema repair、Checker 驱动循环和 chat/model-only 正常完成路径。

---

## 第二阶段最终状态回写（截至 Step 48）

状态：Step 25-48 已完成，第二阶段最终验收已完成（2026-08-05）

第二阶段已经把第一阶段的工程基础层接入为默认模型驱动主循环：

```text
Analyzer
  -> Planner
      -> TaskPlan / TaskUnit / PlanStep
          -> ReActExecutor.execute()
              -> plan precheck
              -> TaskUnit / PlanStep 顺序循环
              -> Prompt -> model.generate
              -> ActionPacket parse / validation / repair
              -> Tool / Model / User / control dispatcher
              -> ObservationStore
              -> Checker transition
              -> ExecutionResult / EventStream / JSONL logs
```

已完成能力：

- 普通可执行计划默认不再调用 skeleton traversal，而是进入 `_execute_react_loop()`。
- 每轮模型交互使用结构化 `ActionPacket`；非法输出按配置进入 repair，耗尽后返回结构化失败 Observation，不猜测动作。
- TaskUnit / PlanStep 按顺序执行，支持依赖检查、`input_from / output_key` 传递和终止后的后续步骤处理。
- Checker 已驱动 `continue / step_completed / retry / fallback_to_model / fallback_to_tool / ask_user / request_replan / finish / fail / blocked / cancel`。
- retry / fallback 使用内部 ActionPacket，经统一 dispatcher、ToolSpec、SafetyPolicy、confirmation 和 Observation 链路。
- chat/model-only 正常调用模型，不调用 ToolManager；clarify / confirm / block 等特殊策略继续由 precheck 短路。
- `execute_stream()`、EventStream 订阅和 `ReactAgent.run_stream()` 已提供同步事件消费；用户事件与开发日志分离。
- `ReactAgent.run()` 保持字符串兼容接口，`run_with_result()` 暴露结构化 `ExecutionResult`，并保留 legacy Executor 回退。
- 命令 action 必须通过 Tool 层，且经过结构化参数校验、安全策略、workspace 限制和确认流程。
- Step 46 已完成完整安全回归与主循环不变量验证。
- Step 47 已完成第二阶段文档回写。
- Step 48 已完成第二阶段最终验收。

当前实现契约：

- `ExecutionResult` 是一次执行的结构化汇总，包含 execution/task/step 状态、Observation、事件、错误、确认和 request_replan 信息。
- `EventStream` 是用户可见时间线的来源；JSONL 日志只用于开发排查，不进入用户事件流或短期记忆。
- `final_answer` 是用户可见执行时间线的终点；事件流提供同步 callback / generator 接口，不承诺异步传输。
- SafetyPolicy 阻断后不得执行当前 action；未开始的独立后续 step 保持 `pending`，依赖传播或显式终止动作按协议标记 `blocked` / `skipped`。

验证结果：

```text
python -B -m unittest discover tests
Ran 310 tests in 4.851s
OK
```

当前边界与后续能力：

- ObservationStore 仍是内存版，不支持跨进程持久化和完整断点续跑。
- 不实现并行 TaskUnit 调度。
- 不自动大范围重规划；当前通过 `request_replan` 向上层暴露原因。
- `execute_stream / run_stream` 是同步、可测试接口，不提供异步背压和断线重连。
- 不实现复杂权限系统、完整 UI、多 Agent 协作，也不要求真实外部 LLM / 网络工具稳定通过。

最终结论：

- ReActExecutor 第二阶段已完成。
- V1 版本的 ReActExecutor 不需要再开启第三阶段开发。
- 后续持久化、异步流、并行 TaskUnit、自动重规划、复杂权限、真实模型质量评估和真实外部工具稳定性工程属于 V2/V3 范围。

---

## Step 24 时的未完成项总览（历史快照）

当前 ReActExecutor 层已完成 Step 0 / Step 1 / Step 2 / Step 3 / Step 4 / Step 5 / Step 6 / Step 7 / Step 8 / Step 9 / Step 10 / Step 11 / Step 12 / Step 13 / Step 14 / Step 15 / Step 16 / Step 17 / Step 18 / Step 19 / Step 20 / Step 21 / Step 22 / Step 23 / Step 24。

后续下一阶段未完成项包括：

- ReActExecutor 主循环中的 ActionPacket 调用与 repair。
- chat/model-only 主路径正常完成。
- Agent API 对结构化 `ExecutionResult/events/observations` 的对外暴露。

第二阶段开发规划已新建：

```text
src/agent/ReActExecutor第二阶段开发步骤与进度.md
```

## Step 24 时的下一轮建议（历史快照）

下一轮建议进入下一阶段主循环开发：

```text
1. 在 ReActExecutor.execute() 中接入模型 ActionPacket 决策主循环。
2. 实现模型输出 parse -> schema validation -> repair retry -> dispatcher 的闭环。
3. 让 Checker 决策驱动 continue/retry/fallback/ask_user/request_replan/finish。
4. 优先补通 chat/model-only 和普通 tool plan 的最小可完成路径。
```

原因：

- Step 10 已经建立 ActionPacket 到执行动作的统一分发边界。
- Step 11 已经在 `_handle_call_tool` 中接入 ToolManager、ToolSpec、ObservationStore 和 EventStream。
- Step 12 已经在 `_handle_call_model` 中接入 ModelManager，并细化了 `finish`。
- Step 13 已经替换 `_handle_ask_user` 未实现兜底，并接入 PendingConfirmation 协议。
- Step 14 已经在不直接执行 shell 的前提下，让结构化命令进入 Tool 层命令工具。
- Step 15 已经提供 RuleChecker / LLMChecker / ReActChecker，并能对 Observation 做工程兜底分类。
- Step 16 已经提供 RetryPolicy 和 `retry_step` 真实执行路径，retry metadata 已进入 Observation / EventStream。
- Step 17 已经消费 Checker 的 `fallback_to_model` / `fallback_to_tool` 结果，补上备用模型和备用工具执行路径。
- Step 18 已经提供 `SafetyPolicy`，并将安全 block / confirmation 接入 Action dispatcher 和 plan precheck。
- Step 19 已经把上述执行、检查、重试、fallback、安全结果稳定写入 JSONL 日志，供开发排查和跨 Session 回归分析。
- Step 20 已经把当前 Observation / Event / RuntimeState 汇总成更完整的 ExecutionResult 和 final response。
- Step 21 已经建立 fixture 回归集，为后续主循环和主链路切换提供覆盖面更宽的保护。
- Step 22 已经完成 Analyzer -> Planner -> ReActExecutor 端到端串联测试，确认三层协议、状态和事件能贯通。
- Step 23 已经将 `ReactAgent.run()` 默认执行器切到 ReActExecutor，并保留旧 Executor 可配置回退路径。
- Step 24 已经完成 V1 文档回写与验收状态总结，明确当前工程基础层完成、完整 ReAct 主循环留作下一阶段。
- 继续保持旧 Executor 文件不删除，作为开发期兼容和回归保护，但不作为新链路失败后的正式 fallback。
