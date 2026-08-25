# Planner 层开发步骤与进度

本文档用于跨 Session 记录 Planner 层开发进度。后续切换新对话继续开发 Planner 时，优先阅读本文档、`src/agent/Planner层设计决策汇总.md` 和 `src/agent/Analyzer层开发步骤与进度.md`。

## 当前定位

Planner 负责消费 Analyzer 的结构化输出，把任务转换成可执行的结构化计划。Planner 不直接执行工具，也不真正重试、执行命令或修改文件。

目标架构：

```text
User Input
  -> Analyzer
  -> Planner
      生成 TaskPlan / TaskUnit / PlanStep
  -> ReAct Executor
      Reasoning -> Decision -> Tool -> Observation -> Checker
  -> Response
```

Planner V1 的核心抽象：

```text
一次用户输入
  -> 一个 TaskPlan 总计划
      -> 多个 TaskUnit 子任务
          -> 多个 PlanStep 执行步骤
```

关键原则：

```text
Intent 不等于 Task
Task 不等于 Step
```

Planner 必须支持：

- 一个 intent -> 一个 TaskUnit。
- 多个 intent -> 一个 TaskUnit。
- 一个复杂 intent -> 多个 PlanStep，必要时拆成多个 TaskUnit。

## 重点必看：跨 Session 进度更新规则

后续每完成一个可验收开发步骤，都必须同步更新对应层的进度文档。

```text
完成一个 Step
  -> 跑测试或完成逻辑验证
  -> 确认没有明显问题
  -> 更新对应层的“开发步骤与进度.md”
  -> 下一个对话继续未完成步骤
```

不需要每改一个小函数都更新；但完成一个清晰阶段后必须更新，并记录已完成内容、验证方式、当前未完成项和下一轮建议。

## 已完成

### Step -1：Planner 设计问题确认与设计基线

状态：已完成

已完成内容：

- 完成三轮 Planner 设计问题确认：
  - `Planner设计问题回答(1).txt`
  - `Planner设计问题回答(2).txt`
  - `Planner设计问题回答(3).txt`
- 确认整体架构为“任务型 Agent 分层 + ReAct 执行循环”的混合架构。
- 确认 Planner V1 采用“规则优先 + LLM 兜底 + 结构校验”的计划生成策略。
- 确认 Planner 只生成初始计划，动态重规划放到 V2。
- 确认 Executor 后续升级为 ReActExecutor。
- 确认 `TaskPlan -> TaskUnit -> PlanStep` 三层结构。
- 确认 Planner 需要支持 intent/task/step 三类关系。
- 新增正式设计汇总文档：

```text
src/agent/Planner层设计决策汇总.md
```

### Step 0：基础计划结构

状态：已完成

主要文件：

```text
src/agent/planner.py
```

已完成内容：

- 定义 `PlanStep`。
- 定义 `TaskPlan`。
- 根据 `task.execution_strategy` 创建基础计划。
- `micro` 任务可生成单步计划。
- `macro` 任务可生成澄清计划。
- `meso/meso_advanced` 任务可生成基础分析和回答步骤。
- `calculate` 可路由到 `math_calculator`。
- `read_file` 可路由到 `document_parser`。
- `translate` 可路由到 `translator`。

### Step 1：消费 Analyzer V1 新字段

状态：已完成第一版

目标：

- Planner 应使用 Analyzer 的 `mode`、`action_policy`、`requires_clarification`、`missing_tools`、`tool_strategy` 等字段。

已完成内容：

- `action_policy=block` 时优先生成 `mode=blocked` 计划，避免进入普通执行步骤。
- `requires_clarification=True` 时优先生成 `mode=clarify` 计划，并把 Analyzer 的 `clarification_questions` 写入步骤参数。
- `requires_confirmation=True` 或 `action_policy=confirm` 时生成 `mode=confirm` 计划。
- `tool_strategy=blocked_missing_tools` 时生成 `mode=missing_tools` 计划，并携带 `missing_tools`。
- `mode=chat` 时生成 `mode=chat` 计划，不生成执行型工具步骤。
- 保留原有 `micro/meso/macro` 基础计划行为，允许普通安全任务继续执行。

主要文件：

```text
src/agent/planner.py
tests/test_planner_executor_policy.py
```

已验证：

```text
python -B -m unittest tests.test_analyzer_v1 tests.test_planner_executor_policy
```

结果：

```text
Ran 13 tests
OK
```

验收标准：

- `requires_clarification=True` 时优先生成澄清计划。
- `action_policy=block` 时生成拒绝执行计划。
- `action_policy=confirm` 时生成确认计划。
- `mode=chat` 时不生成执行型工具计划。
- `tool_strategy=blocked_missing_tools` 时生成缺工具说明计划。

## 待开发

### Step 2：Planner 配置、枚举与数据结构

状态：已完成第一版

目标：

- 新增 Planner 配置入口。
- 固定 Planner V1 枚举。
- 扩展 `TaskPlan`。
- 新增 `TaskUnit`。
- 扩展 `PlanStep`。
- 保持与当前 Executor 的基础兼容。

建议新增配置文件：

```text
config/planner/planner_config.json
config/planner/rule_templates.json
config/planner/llm_planner_prompt.json
```

建议配置项：

```text
max_plan_steps
max_task_units
max_llm_repair_attempts
default_step_max_retries
enable_llm_planner
enable_shell_fallback_plan
planner_log_path
```

需要固定的枚举：

```text
TaskPlan.mode:
  micro / meso / meso_advanced / macro / blocked / clarify / confirm / missing_tools / chat

planning_strategy:
  policy_rule / rule_template / llm_planner / llm_repaired / fallback_rule / fallback_model_only / invalid

TaskUnit.status:
  pending / running / completed / failed / skipped / blocked / waiting_user

plan_validation_status:
  valid / repaired / invalid / not_required
```

ID 规范：

```text
plan_id: plan_<uuid短串>
TaskUnit.id: task_1, task_2, task_3
PlanStep.id: step_1, step_2, step_3
```

建议新增/扩展字段：

```text
TaskPlan:
  plan_id
  source_trace_id
  task_type
  execution_strategy
  planning_strategy
  can_execute
  risk_policy
  required_tools
  available_tools
  missing_tools
  task_units
  plan_validation_status
  plan_validation_notes
  added_steps_reason
  user_facing_summary
  raw_planner_trace

TaskUnit:
  id
  title
  description
  intent_refs
  task_type
  status
  depends_on
  step_ids
  expected_outcome

PlanStep:
  task_id
  step_type
  depends_on
  input_from
  output_key
  requires_confirmation
  confirmation_reason
  on_failure
  retryable
  max_retries
  fallback_tools
  allow_model_reasoning
  metadata
```

验收标准：

- 现有 Planner/Executor 策略测试继续通过。
- 配置缺失时有稳定默认值。
- `TaskPlan/TaskUnit/PlanStep` 都支持 `to_dict()`。
- `TaskPlan` 同时包含 `task_units` 和扁平 `steps`。
- 每个 `PlanStep` 都能通过 `task_id` 关联到 TaskUnit。
- 每个 `TaskUnit` 都能通过 `step_ids` 关联到步骤。

已完成内容：

- 新增 Planner 配置加载器：

```text
src/agent/planner_config.py
```

- 新增 Planner 配置文件：

```text
config/planner/planner_config.json
config/planner/rule_templates.json
config/planner/llm_planner_prompt.json
```

- 固定 Planner V1 协议枚举：
  - `PLAN_MODES`
  - `PLANNING_STRATEGIES`
  - `TASK_UNIT_STATUSES`
  - `PLAN_VALIDATION_STATUSES`
- 扩展 `PlanStep`，新增 `task_id`、`step_type`、`depends_on`、`input_from`、`output_key`、确认、失败策略、重试、fallback 和元数据字段。
- 新增 `TaskUnit`。
- 扩展 `TaskPlan`，新增 `plan_id`、`source_trace_id`、`task_type`、`execution_strategy`、`planning_strategy`、`can_execute`、工具列表、`task_units`、校验字段、摘要和 trace 字段。
- 为 `TaskPlan/TaskUnit/PlanStep` 增加 `to_dict()`。
- 当前 Planner 生成的所有基础计划都会带上一个默认 `TaskUnit`，并让步骤通过 `task_id` 关联到该任务单元。
- 保持当前 Executor 兼容，旧的 `plan.mode`、`plan.steps`、`step.tool_name`、`step.args` 仍可继续使用。

主要文件：

```text
src/agent/planner.py
src/agent/planner_config.py
config/planner/planner_config.json
config/planner/rule_templates.json
config/planner/llm_planner_prompt.json
tests/test_planner_v1_structure.py
```

已验证：

```text
python -B -m unittest tests.test_planner_v1_structure tests.test_planner_executor_policy
python -B -m unittest tests.test_analyzer_v1 tests.test_analyzer_v1_acceptance tests.test_intent_classifier_protocol tests.test_llm_fallback_protocol
```

结果：

```text
Ran 11 tests
OK

Ran 20 tests
OK
```

### Step 3：特殊策略计划收敛

状态：已完成第一版

目标：

- 将 `block / clarify / confirm / missing_tools / chat` 统一生成新结构计划。
- 保证这些特殊策略优先于普通规划。
- 不让危险动作进入普通执行步骤。

验收标准：

- `block` 计划 `can_execute=False`，且不包含真实工具步骤。
- `clarify` 计划只包含澄清步骤。
- `confirm` 计划只包含确认步骤。
- `missing_tools` 计划携带缺失工具说明。
- `chat` 计划生成 model-only 回答步骤。

已完成内容：

- 将 Planner 中的特殊策略计划拆成独立生成函数：
  - `_blocked_plan`
  - `_clarify_plan`
  - `_confirm_plan`
  - `_missing_tools_plan`
  - `_chat_plan`
- 固定特殊策略优先级：

```text
block
  -> clarify
  -> confirm
  -> missing_tools
  -> chat
  -> normal planning
```

- `block` 计划：
  - `mode=blocked`
  - `can_execute=False`
  - `TaskUnit.status=blocked`
  - 只包含 `step_type=block` 步骤
  - 不包含真实工具步骤
- `clarify` 计划：
  - `mode=clarify`
  - `can_execute=False`
  - `TaskUnit.status=waiting_user`
  - 只包含 `step_type=clarify` 步骤
  - 携带 `clarification_questions` 和 `missing_parameters`
- `confirm` 计划：
  - `mode=confirm`
  - `can_execute=False`
  - `TaskUnit.status=waiting_user`
  - 只包含 `step_type=confirm` 步骤
  - 设置 `requires_confirmation=True`
  - 携带 `confirmation_reason`
- `missing_tools` 计划：
  - `mode=missing_tools`
  - `can_execute=False`
  - `TaskUnit.status=blocked`
  - 不包含真实工具步骤
  - 在 `TaskPlan.missing_tools` 和步骤参数中都携带缺失工具列表
- `chat` 计划：
  - `mode=chat`
  - 只包含 `step_type=respond` 步骤
  - 不包含真实工具步骤
  - `allow_model_reasoning=True`
- 为特殊策略步骤增加 `metadata.policy` 和 `plan_validation_notes`，方便后续日志、校验和 ReActExecutor 使用。
- 新增专项测试：

```text
tests/test_planner_v1_policy_convergence.py
```

已验证：

```text
python -B -m unittest tests.test_planner_v1_policy_convergence tests.test_planner_v1_structure tests.test_planner_executor_policy
python -B -m unittest tests.test_analyzer_v1 tests.test_analyzer_v1_acceptance tests.test_intent_classifier_protocol tests.test_llm_fallback_protocol
```

结果：

```text
Ran 16 tests
OK

Ran 20 tests
OK
```

### Step 4：规则 TaskUnit/PlanStep 生成器

状态：已完成第一版

目标：

- 为简单任务和常见流水线生成规则计划。
- 支持三类 intent/task/step 关系：
  - 一个 intent -> 一个 TaskUnit。
  - 多个 intent -> 一个 TaskUnit。
  - 一个复杂 intent -> 多个 PlanStep。
- 明确 TaskUnit 分组规则。

优先支持：

```text
calculate
read_file
translate
search + summarize
search + summarize + write_file
read_file + summarize
read_file + extract
read_file + extract + write_file
translate + write_file
convert_format
design_project / debug_code / run_test / deploy_project 的基础步骤模板
```

验收标准：

- `calculate` 生成一个 TaskUnit 和一个工具步骤。
- `search + summarize + write_file` 合成一个 TaskUnit，包含搜索、总结、写入步骤。
- `read_file + extract + write_file` 合成一个 TaskUnit，包含读取、提取、写入步骤。
- 软件工程单阶段任务可拆成多个 PlanStep。
- 不同文件对象或不同输出目标的独立任务可拆成多个 TaskUnit。
- 规则无法判断任务边界时，不强行套模板，应交给 LLM Planner 或生成安全降级计划。

已完成内容：

- 在特殊策略计划之后、普通 `micro/meso` 兜底之前新增规则模板生成入口：

```text
_rule_template_plan
```

- 当前已支持的规则模板：

```text
calculate
read_file
translate
search + summarize
search + summarize + write_file
read_file + summarize
read_file + extract + write_file
translate + write_file
convert_format
design_project / debug_code / run_test / deploy_project 基础软件工程计划
```

- 新增规则步骤生成 helper：
  - `_search_step`
  - `_read_file_step`
  - `_summarize_step`
  - `_extract_step`
  - `_translate_step`
  - `_write_file_step`
- 新增规则匹配与参数辅助 helper：
  - `_intent_sequence`
  - `_has_intents`
  - `_should_split_by_file`
  - `_file_path`
  - `_target_path`
- 支持多个 intent 合成一个 TaskUnit：
  - `search + summarize + write_file`
  - `read_file + extract + write_file`
  - `translate + write_file`
- 支持一个复杂 intent 拆成多个 PlanStep：
  - `convert_format` 可生成读取、模型转换、写入步骤。
  - 软件工程类任务可生成分析和执行方案两个模型步骤。
- 支持多文件独立任务拆分成多个 TaskUnit：
  - 多个 `file_paths` 且为 `read_file + summarize/extract` 时，每个文件生成一个独立 TaskUnit。
- 规则无法覆盖的 intent 会回退到现有 `meso` 模型计划，不会强行套模板。
- 当前仍保持与旧 Executor 兼容：
  - `plan.steps`
  - `step.tool_name`
  - `step.args`
  仍然可读。

主要文件：

```text
src/agent/planner.py
tests/test_planner_v1_rule_templates.py
```

已验证：

```text
python -B -m unittest tests.test_planner_v1_rule_templates tests.test_planner_v1_policy_convergence tests.test_planner_v1_structure tests.test_planner_executor_policy
python -B -m unittest tests.test_analyzer_v1 tests.test_analyzer_v1_acceptance tests.test_intent_classifier_protocol tests.test_llm_fallback_protocol
```

结果：

```text
Ran 24 tests
OK

Ran 20 tests
OK
```

当前边界：

- Step 4 主要固定“生成什么步骤”和“TaskUnit/PlanStep 如何组织”。
- `input_from/output_key` 已经生成基础依赖关系，但当前旧 Executor 还不会真正把上一步输出注入下一步工具参数。
- 文件写入、翻译、总结等步骤的参数装配只是 V1 基础版，严格参数校验和跨步骤输出注入放到 Step 5。

### Step 5：工具参数装配与步骤依赖

状态：已完成第一版

目标：

- 根据 Analyzer 的 `parameters/file_info` 为工具步骤装配参数。
- 为强依赖步骤生成 `input_from` 和 `output_key`。

验收标准：

- 文件读取使用 `file_path`。
- 翻译使用 `content`、`target_language` 或 `input_from`。
- 文件写入使用 `file_path`、`content` 或 `input_from`、`edit_mode`。
- 搜索输出 `search_results`。
- 总结步骤输入来自搜索或读取步骤。
- 写入步骤输入来自总结、提取、翻译或模型生成步骤。
- `step_type=tool` 的步骤必须有 `tool_name`。
- `step_type=model/respond` 不应误填执行型工具。
- V1 按关键参数做基础校验，后续 ToolRegistry 完成后再接严格 schema 校验。

已完成内容：

- Planner 侧补全规则步骤的基础参数装配：
  - `document_parser` 使用 `file_path`。
  - `search_tool` 使用 `query` 和 `max_results`。
  - `text_processor` 使用 `operation`，内容可由 `input_from` 注入。
  - `translator` 使用 `text`、`source_language`、`target_language`，内容可由 `input_from` 注入。
  - `file_writer` 使用 `file_path`、`content`、`overwrite`，内容可由 `input_from` 注入。
- Planner 侧新增基础计划校验：

```text
_basic_validation_notes
```

- V1 校验覆盖：
  - `step_type=tool` 必须有 `tool_name`。
  - `step_type=model/respond` 不应有 `tool_name`。
  - `math_calculator` 必须有 `expression` 或 `data`。
  - `document_parser` 必须有 `file_path`。
  - `search_tool` 必须有 `query`。
  - `text_processor` 必须有 `text` 或 `input_from`。
  - `translator` 必须有 `target_language`，并且必须有 `text` 或 `input_from`。
  - `file_writer` 必须有 `file_path`，并且必须有 `content` 或 `input_from`。
- 校验失败时：
  - `plan_validation_status=invalid`
  - `can_execute=False`
  - 错误原因写入 `plan_validation_notes`
- Executor 侧补充基础依赖执行能力：
  - 执行步骤后用 `step.id` 和 `step.output_key` 记录输出。
  - 下一步骤的 `input_from` 可引用前序 `step.id` 或 `output_key`。
  - 对 `text_processor/translator` 自动把依赖输出注入 `text`。
  - 对 `file_writer` 自动把依赖输出注入 `content`。
  - 模型步骤会在 prompt 中携带依赖输出。
  - `plan.can_execute=False` 或 `plan_validation_status=invalid` 时 Executor 不会调用工具。
- 新增专项测试：

```text
tests/test_planner_v1_step_dependencies.py
```

测试覆盖：

- `search -> summarize -> write_file` 的输出逐步注入。
- `read_file -> extract -> write_file` 的输出逐步注入。
- `read_file -> model convert -> write_file` 中模型步骤输出写入文件。
- 缺少 `file_path` 的无效计划不会执行工具。

主要文件：

```text
src/agent/planner.py
src/agent/executor.py
tests/test_planner_v1_step_dependencies.py
```

已验证：

```text
python -B -m unittest tests.test_planner_v1_step_dependencies tests.test_planner_v1_rule_templates tests.test_planner_v1_policy_convergence tests.test_planner_v1_structure tests.test_planner_executor_policy
python -B -m unittest tests.test_analyzer_v1 tests.test_analyzer_v1_acceptance tests.test_intent_classifier_protocol tests.test_llm_fallback_protocol
```

结果：

```text
Ran 28 tests
OK

Ran 20 tests
OK
```

当前边界：

- Step 5 是 V1 基础校验，不是完整 ToolRegistry schema 校验。
- 当前 Executor 只支持把依赖输出注入常见参数：
  - `text`
  - `content`
- 多输入合并策略目前是按 `input_from` 顺序用空行拼接文本。
- 更严格的引用校验、工具存在性校验、LLM 计划修复重试放到 Step 7。

### Step 6：LLM Planner 协议与结构化解析

状态：已完成第一版

目标：

- 增加 LLM Planner 兜底。
- 规则无法覆盖的软件工程/开放复杂任务走 LLM 规划。
- LLM 必须返回结构化 JSON。
- 定义 LLM 不可用时的降级行为。

验收标准：

- LLM prompt 包含 Analyzer 结果、可用工具列表、计划 schema 和安全约束。
- 支持解析 LLM 返回的 `TaskPlan / TaskUnit / PlanStep` JSON。
- LLM 可调整 Analyzer intent 顺序，但必须写入 `plan_validation_notes`。
- LLM 可补充必要步骤，但必须写入 `added_steps_reason`。
- LLM 未配置、调用失败、返回非 JSON 时不会生成伪成功计划。
- LLM 不可用时能回退规则计划、model_only、clarify 或 invalid 计划。

已完成内容：

- Planner 构造函数支持注入 `model_manager`：

```text
Planner(model_manager=...)
```

- LLM Planner 触发顺序：

```text
特殊策略计划
  -> 规则模板计划
  -> LLM Planner
  -> 旧 meso/micro/macro 兜底
```

- LLM Planner 只在以下条件满足时尝试：
  - `planner_config.enable_llm_planner=True`
  - `model_manager` 已注入
  - 特殊策略和规则模板都没有命中
- 新增 LLM Planner prompt 构造：

```text
_build_llm_planner_prompt
```

- prompt 包含：
  - Planner system 指令
  - JSON schema 示例
  - Analyzer 结构化结果
  - 可用工具列表
  - safety rules
  - 用户原始输入
  - 要求 strict JSON only
- 新增 JSON 解析：

```text
_extract_json_object
```

- 支持：
  - 模型直接返回 `dict`
  - 模型返回普通 JSON 字符串
  - 模型返回 fenced JSON，例如 ```json ... ```
- 新增 LLM plan 结构转换：

```text
_task_plan_from_llm_payload
_step_from_payload
_task_unit_from_payload
```

- LLM 返回的 JSON 会被转换为正式的：

```text
TaskPlan
TaskUnit
PlanStep
```

- LLM 计划会继续走现有基础校验：
  - 工具步骤必须有工具名。
  - 模型/回复步骤不应携带工具名。
  - 常见工具关键参数必须存在，或通过 `input_from` 注入。
- LLM 可以：
  - 调整 Analyzer intent 顺序，但必须在 `plan_validation_notes` 说明。
  - 补充必要步骤，但必须在 `added_steps_reason` 说明。
- LLM 失败降级：

```text
_llm_unavailable_fallback_plan
```

- 失败情况包括：
  - 未返回 JSON。
  - JSON 缺少 `steps`。
  - 步骤或 TaskUnit 结构不是对象。
  - 其他解析/转换异常。
- 失败时不会生成伪成功 LLM 计划，而是回退为：

```text
planning_strategy=fallback_model_only
```

并把失败原因写入：

```text
plan_validation_notes
raw_planner_trace
```

- 新增专项测试：

```text
tests/test_planner_v1_llm_planner.py
```

测试覆盖：

- LLM Planner prompt 包含 Analyzer 结果、可用工具、schema 和安全规则。
- fenced JSON 可以解析成 `TaskPlan / TaskUnit / PlanStep`。
- dict 响应可以直接解析。
- 非 JSON 响应会降级，不会伪装成成功计划。
- 缺少 `steps` 的结构会降级，不会伪装成成功计划。
- 规则模板命中时不会调用 LLM Planner。

主要文件：

```text
src/agent/planner.py
config/planner/llm_planner_prompt.json
tests/test_planner_v1_llm_planner.py
```

已验证：

```text
python -B -m unittest tests.test_planner_v1_llm_planner tests.test_planner_v1_step_dependencies tests.test_planner_v1_rule_templates tests.test_planner_v1_policy_convergence tests.test_planner_v1_structure tests.test_planner_executor_policy
python -B -m unittest tests.test_analyzer_v1 tests.test_analyzer_v1_acceptance tests.test_intent_classifier_protocol tests.test_llm_fallback_protocol
```

结果：

```text
Ran 33 tests
OK

Ran 20 tests
OK
```

当前边界：

- Step 6 只负责 LLM Planner 协议、解析和失败降级。
- LLM 计划的深度引用校验、工具存在性校验、绕过安全策略检查、修复重试还未做，放到 Step 7。
- 当前 LLM 失败后回退为 `fallback_model_only` 的 meso 计划；更细粒度的回退策略可在 Step 7/Step 9 继续扩展。

### Step 7：计划校验与修复重试

状态：已完成第一版

目标：

- 对规则计划和 LLM 计划做统一校验。
- LLM 计划校验失败时最多修复重试 3 次。

验收标准：

- 校验工具是否存在。
- 校验步骤字段是否完整。
- 校验 `task_id/depends_on/input_from` 引用是否存在。
- 校验是否绕过 `block/confirm` 策略。
- 校验写文件是否有 `file_path` 和 `content` 或 `input_from`。
- 校验 `step_type=shell` 不会绕过后续 CommandTool/ShellTool 安全策略。
- 输出 `plan_validation_status = valid | repaired | invalid`。
- LLM 计划最多修复重试 3 次。

已完成内容：

- 将 Step 5 的基础校验升级为统一计划校验入口：

```text
_plan_validation_notes
```

- 规则计划和 LLM 计划都会经过本地统一校验。
- 当前统一校验覆盖：
  - step id 不能重复。
  - 至少存在一个 `TaskUnit`。
  - `TaskUnit.status` 必须属于 V1 枚举。
  - `TaskUnit.step_ids` 必须引用真实存在的步骤。
  - `PlanStep.step_type` 必须属于允许集合。
  - `PlanStep.task_id` 必须引用真实存在的 TaskUnit。
  - `step_type=tool` 必须有 `tool_name`。
  - `step_type=model/respond/block/clarify/confirm` 不应携带执行型 `tool_name`。
  - 当 Analyzer 提供 `available_tools` 时，`tool_name` 必须存在于可用工具列表。
  - `depends_on` 必须引用真实步骤 id。
  - `input_from` 必须引用真实步骤 id 或真实 `output_key`。
  - `step_type=shell` 在 V1 中一律标记 invalid，不能绕过后续 ShellTool/CommandTool 安全策略。
  - 常见工具关键参数继续校验：
    - `math_calculator`: `expression` 或 `data`
    - `document_parser`: `file_path`
    - `search_tool`: `query`
    - `text_processor`: `text` 或 `input_from`
    - `translator`: `target_language`，以及 `text` 或 `input_from`
    - `file_writer`: `file_path`，以及 `content` 或 `input_from`
- 新增安全策略校验：

```text
_safety_policy_validation_notes
```

- 当前安全策略校验覆盖：
  - `action_policy=block` 的计划不能包含普通可执行步骤。
  - `action_policy=confirm` 或 `requires_confirmation=True` 的计划不能包含未确认的工具步骤。
- 校验失败时：
  - `plan_validation_status=invalid`
  - `can_execute=False`
  - 失败原因写入 `plan_validation_notes`
- LLM Planner 增加修复重试循环：
  - 首次 LLM 计划解析成功但校验失败时，构造 repair prompt。
  - repair prompt 包含原始 Planner prompt、校验错误和无效计划 JSON。
  - 最多按 `config.planner.max_llm_repair_attempts` 重试。
  - 修复成功后：

```text
planning_strategy=llm_repaired
plan_validation_status=repaired
```

  - 修复耗尽后：

```text
planning_strategy=invalid
plan_validation_status=invalid
can_execute=False
```

- LLM 模型自己返回的 `plan_validation_status` 不会被直接信任，解析后仍由本地校验决定最终状态。
- 扩展专项测试：

```text
tests/test_planner_v1_llm_planner.py
```

新增测试覆盖：

- LLM 计划中缺失的 `depends_on/input_from` 引用可通过 repair prompt 修复。
- LLM 修复成功后标记为 `llm_repaired/repaired`。
- LLM 修复耗尽后保留 invalid 计划，不会伪装成功。
- LLM 使用不可用工具会被校验拦截。
- LLM 生成 `step_type=shell` 会被校验拦截。
- LLM step 引用不存在的 TaskUnit 会被校验拦截。
- LLM 工具步骤不能绕过 `confirm` 策略。

主要文件：

```text
src/agent/planner.py
tests/test_planner_v1_llm_planner.py
```

已验证：

```text
python -B -m unittest tests.test_planner_v1_llm_planner tests.test_planner_v1_step_dependencies tests.test_planner_v1_rule_templates tests.test_planner_v1_policy_convergence tests.test_planner_v1_structure tests.test_planner_executor_policy
python -B -m unittest tests.test_analyzer_v1 tests.test_analyzer_v1_acceptance tests.test_intent_classifier_protocol tests.test_llm_fallback_protocol
```

结果：

```text
Ran 38 tests
OK

Ran 20 tests
OK
```

当前边界：

- Step 7 的工具存在性校验依赖 Analyzer 提供的 `available_tools`；如果该字段为空，当前视为“工具上下文未知”，不会强行判 invalid。
- Step 7 还没有接真正的 ToolRegistry schema；严格工具 schema 校验后续可在 ToolRegistry 完整后接入。
- Step 7 只修复 LLM Planner 计划，不对规则模板计划做自动修复；规则模板计划 invalid 时直接返回 invalid，便于暴露模板缺陷。

### Step 8：Planner 日志

状态：已完成第一版

目标：

- 增加 `logs/planner.log` JSONL 日志。

验收标准：

- 每次生成计划写入一条日志。
- 日志包含 `plan_id`、Analyzer `trace_id`、intent_sequence、TaskUnit、PlanStep、工具参数、计划来源、校验结果和特殊策略。

已完成内容：

- Planner `create_plan()` 统一出口写入 JSONL 日志，避免特殊策略、规则模板、LLM Planner、fallback 计划漏记。
- 新增日志方法：

```text
_write_log
```

- 日志路径使用 Planner 配置：

```text
config/planner/planner_config.json
planner_log_path = logs/planner.log
```

- 日志写入失败不会影响计划返回。
- 当前日志字段包括：

```text
timestamp
plan_id
source_trace_id
raw_input
intent_sequence
task_type
execution_strategy
mode
planning_strategy
can_execute
risk_policy
risk_flags
required_tools
available_tools
missing_tools
task_units
steps
tool_args
plan_validation_status
plan_validation_notes
added_steps_reason
special_policy
llm_planner_trace
raw_planner_trace
user_facing_summary
```

- 新增专项测试：

```text
tests/test_planner_v1_logging.py
```

测试覆盖：

- `create_plan()` 会写入 JSONL 日志。
- 日志包含 `plan_id/source_trace_id/raw_input/intent_sequence/planning_strategy/plan_validation_status/required_tools/tool_args/task_units/steps`。
- 特殊策略计划会记录 `special_policy`、`can_execute=False`、`risk_policy` 和风险标记。

主要文件：

```text
src/agent/planner.py
tests/test_planner_v1_logging.py
src/agent/Planner层设计决策汇总.md
```

额外设计更新：

- 在 `src/agent/Planner层设计决策汇总.md` 中新增“项目级基础工具约定”。
- 约定后续 Tools 层优先补齐：

```text
阅读工具
编辑工具
终端工具
预览工具
联网搜索工具
```

- 明确 Planner 只规划工具使用，Executor/ReActExecutor 负责调用工具，Tools 层负责真实实现、参数 schema、安全校验和错误返回。

已验证：

```text
python -B -m unittest tests.test_planner_v1_logging tests.test_planner_v1_llm_planner tests.test_planner_v1_step_dependencies tests.test_planner_v1_rule_templates tests.test_planner_v1_policy_convergence tests.test_planner_v1_structure tests.test_planner_executor_policy
python -B -m unittest tests.test_analyzer_v1 tests.test_analyzer_v1_acceptance tests.test_intent_classifier_protocol tests.test_llm_fallback_protocol
```

结果：

```text
Ran 40 tests
OK

Ran 20 tests
OK
```

当前边界：

- 当前日志是 JSONL 追加写入，不做日志轮转。
- 日志会记录工具参数，后续如果出现密钥、token 或用户隐私字段，需要接入脱敏策略。
- 当前日志写入失败会静默跳过，后续可以接入统一 logger 或错误监控。

### Step 9：Planner 回归样例与验收测试

状态：已完成第一版

目标：

- 建立 Planner fixture 测试集。

建议文件：

```text
tests/fixtures/planner_cases.json
tests/test_planner_v1.py
```

验收标准：

- 覆盖 20-40 条 Planner 用例。
- 覆盖 micro、流水线、多 TaskUnit、软件工程、block、clarify、confirm、missing_tools、chat、model_only。
- 所有用例断言关键结构，不只断言不报错。
- 覆盖 LLM 不可用降级。
- 覆盖 LLM 调整 Analyzer intent 顺序并记录 `plan_validation_notes`。
- 覆盖工具缺失和 shell fallback 候选计划。

已完成内容：

- 新增 Planner fixture 回归样例：

```text
tests/fixtures/planner_cases.json
```

- 新增统一 fixture 回归测试：

```text
tests/test_planner_v1.py
```

- 当前 fixture 共 24 条用例，覆盖：
  - micro 单步任务：
    - `calculate`
    - `read_file`
    - `translate`
  - 强依赖流水线：
    - `search + summarize`
    - `search + summarize + write_file`
    - `read_file + summarize`
    - `read_file + extract + write_file`
    - `translate + write_file`
    - `convert_format`
  - 多 TaskUnit：
    - 多文件 `read_file + summarize`
  - 软件工程：
    - `debug_code` 基础软件工程计划
  - model-only / fallback：
    - 未知 intent 回退到 model/respond 计划
    - LLM 不可用时 `fallback_model_only`
  - 特殊策略：
    - `block`
    - `clarify`
    - `confirm`
    - `missing_tools`
    - `chat`
  - LLM Planner：
    - LLM 调整 Analyzer intent 顺序并写入 `plan_validation_notes`
    - LLM 生成 shell fallback 候选并被 V1 校验标记 invalid
    - LLM 使用缺失工具并被校验标记 invalid
    - LLM 首次计划非法，repair 后变成 `llm_repaired`
  - 参数/校验：
    - 缺少 `read_file.file_path` 时计划 invalid

- `tests/test_planner_v1.py` 对每条 fixture 断言关键结构，而不是只断言不报错：
  - `mode`
  - `planning_strategy`
  - `plan_validation_status`
  - `can_execute`
  - `TaskUnit` 数量
  - `PlanStep` 数量
  - `step_type`
  - `tool_name`
  - `required_tools`
  - `missing_tools`
  - `plan_validation_notes`
  - `added_steps_reason`
- 测试使用临时 Planner 配置目录和临时日志路径，避免 fixture 回归污染项目日志。

主要文件：

```text
tests/fixtures/planner_cases.json
tests/test_planner_v1.py
src/agent/Planner层开发步骤与进度.md
```

已验证：

```text
python -B -m unittest tests.test_planner_v1
python -B -m unittest tests.test_planner_v1 tests.test_planner_v1_logging tests.test_planner_v1_llm_planner tests.test_planner_v1_step_dependencies tests.test_planner_v1_rule_templates tests.test_planner_v1_policy_convergence tests.test_planner_v1_structure tests.test_planner_executor_policy
python -B -m unittest tests.test_analyzer_v1 tests.test_analyzer_v1_acceptance tests.test_intent_classifier_protocol tests.test_llm_fallback_protocol
```

结果：

```text
Ran 1 test
OK

Ran 41 tests
OK

Ran 20 tests
OK
```

当前边界：

- fixture 当前聚焦 Planner V1 结构协议和关键行为，不覆盖真实工具执行结果。
- 后续 ToolRegistry 完成后，可以继续扩展 fixture，加入严格工具 schema 断言。
- 后续 ReActExecutor 完成后，可以新增端到端 fixture，覆盖 Analyzer -> Planner -> ReActExecutor -> Response。

### Step 10：与当前 Executor 兼容验证

状态：已完成第一版

目标：

- 保证新的计划结构不破坏当前 Executor。
- 为后续 ReActExecutor 开发留下字段。

验收标准：

- 当前 `tests/test_planner_executor_policy.py` 继续通过。
- 简单计算、澄清、阻断、确认、chat 仍可通过主链路返回。
- 新增结构字段不会导致 Executor 读取旧字段失败。

已完成内容：

- 新增当前 Executor 兼容性专项测试：

```text
tests/test_planner_executor_compatibility.py
```

- 验证当前 Executor 仍能消费 Planner V1 新结构：
  - `TaskPlan.task_units`
  - `TaskPlan.steps`
  - `PlanStep.task_id`
  - `PlanStep.step_type`
  - `PlanStep.depends_on`
  - `PlanStep.input_from`
  - `PlanStep.output_key`
  - `PlanStep.tool_name`
  - `PlanStep.args`
- 验证旧字段仍兼容：
  - `plan.mode`
  - `plan.steps`
  - `step.tool_name`
  - `step.args`
- 验证主链路兼容：
  - 简单 `calculate` 计划仍可调用工具。
  - `block` 计划仍会短路，不调用工具。
  - `clarify` 计划仍会返回澄清问题，不调用工具。
  - `confirm` 计划仍会暂停确认，不调用工具。
  - `missing_tools` 计划仍会停止执行，不调用工具。
  - `chat` 计划仍走 model-only 响应。
  - `invalid` 计划会在 Executor 入口被拒绝，不调用工具。
  - `search -> summarize -> write_file` 这种新依赖结构可通过当前 Executor 的 `input_from` 注入执行。
  - LLM Planner 生成的 model-only/respond 计划可由当前 Executor 执行。
- 当前 Executor 不理解的复杂 ReAct 字段仍保持为 Planner 预留字段，不影响旧执行路径：
  - `retryable`
  - `max_retries`
  - `fallback_tools`
  - `allow_model_reasoning`
  - `requires_confirmation`
  - `metadata`

主要文件：

```text
tests/test_planner_executor_compatibility.py
src/agent/Planner层开发步骤与进度.md
```

已验证：

```text
python -B -m unittest tests.test_planner_executor_compatibility tests.test_planner_executor_policy
python -B -m unittest tests.test_planner_executor_compatibility tests.test_planner_v1 tests.test_planner_v1_logging tests.test_planner_v1_llm_planner tests.test_planner_v1_step_dependencies tests.test_planner_v1_rule_templates tests.test_planner_v1_policy_convergence tests.test_planner_v1_structure tests.test_planner_executor_policy
python -B -m unittest tests.test_analyzer_v1 tests.test_analyzer_v1_acceptance tests.test_intent_classifier_protocol tests.test_llm_fallback_protocol
```

结果：

```text
Ran 12 tests
OK

Ran 47 tests
OK

Ran 20 tests
OK
```

当前边界：

- Step 10 验证的是“当前旧 Executor 与 Planner V1 结构兼容”，不是 ReActExecutor 完整执行能力。
- 当前 Executor 仍是顺序执行器，尚未实现 Reasoning Node / Decision Branch / Tool Node / Observation / Checker 的完整 ReAct 循环。
- `retry/fallback/replan/checker` 等高级字段已保留，但真正执行语义放到后续 ReActExecutor 开发。

### 跨层串联验证补充（2026-07-27）

状态：已完成

本轮对当前主链路进行了实际串联检查：

```text
ReactAgent.run()
  -> ComplexityAnalyzer.analyze()
  -> Planner.create_plan()
  -> Executor.execute()
  -> 返回响应并写入短期记忆
```

已确认：

- `AnalysisResult.intent_sequence`、`parameters`、`file_info`、`execution_strategy`、`action_policy`、`tool_strategy`、`available_tools`、`missing_tools` 能被 Planner 正常消费。
- Analyzer 的澄清、阻断、确认和缺工具策略会在 Planner 中优先收敛，不会误生成普通工具执行计划。
- Analyzer 的多意图结果可以进入规则模板，例如 `search -> summarize` 会生成带 `input_from/depends_on` 的两步计划。
- `TaskPlan.source_trace_id` 能关联 Analyzer 的 `trace_id`，Planner 日志能记录意图、TaskUnit、PlanStep 和工具参数。
- `ReactAgent` 在未显式注入 Planner 时，会把同一个 `model_manager` 注入默认 Planner，保证复杂开放任务可以触发 LLM Planner 兜底。
- Planner 会拒绝超过 `max_plan_steps` 或 `max_task_units` 配置上限的计划。

新增串联测试：

```text
tests/test_analyzer_planner_pipeline.py
```

验证命令：

```text
python -B -m unittest tests.test_analyzer_planner_pipeline
python -B -m unittest discover tests
```

结果：

```text
Ran 4 tests
OK

Ran 71 tests
OK
```

当前仍未完成：

- 当前 `Executor` 仍是顺序执行器，不是完整的 ReActExecutor。
- Reasoning Node、Decision Branch、Observation Store、Checker、步骤重试、fallback tool、动态 replan 和多轮确认状态仍需在 Executor/ReActExecutor 阶段实现。

## 暂停开发时更新格式

每次结束 Planner 开发时，在本文档新增或更新：

```text
已完成：
- ...

当前未完成：
- ...

下一轮建议：
- 从 Step X 开始，目标是 ...
```
