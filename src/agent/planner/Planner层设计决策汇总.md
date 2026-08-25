# Planner 层设计决策汇总

本文档汇总 `Planner设计问题回答(1).txt`、`Planner设计问题回答(2).txt`、`Planner设计问题回答(3).txt` 中已经确认的 Planner 层设计决策。后续 Planner V1 开发以本文档作为需求基线。

## 1. Planner 在整体 Agent 中的位置

整体 Agent 采用“任务型 Agent 分层 + ReAct 执行循环”的混合架构：

```text
User Input
  -> Analyzer
      理解意图、参数、风险、复杂度
  -> Planner
      生成初始结构化计划
  -> ReAct Executor
      Reasoning Node: 根据当前状态思考
      Decision Branch: 决定调用工具、调用模型、继续、重试、询问用户或结束
      Tool Node: 执行工具
      Observation: 记录工具结果
      Checker: 判断完成、失败、重试或需要用户输入
  -> Response
```

当前项目不是裸 ReAct，而是工程化混合架构：

- Analyzer 负责任务理解和风险前置判断。
- Planner 负责结构化任务拆解和工具调用前计划。
- Executor 内部逐步升级为 ReActExecutor，负责执行过程中的思考、决策、工具调用、观察和检查。

这样既保留 ReAct 的动态执行能力，又避免完全依赖模型自由发挥。

## 2. Planner 职责边界

Planner 负责：

- 消费 Analyzer 的 `AnalysisResult`。
- 根据用户输入和 Analyzer 输出生成一个 `TaskPlan`。
- 将意图整理成 `TaskUnit`。
- 将任务单元拆成 `PlanStep`。
- 为步骤装配基础工具参数。
- 表达步骤依赖和中间结果传递。
- 输出 ReActExecutor 可消费的执行策略字段。
- 对 LLM 生成的计划做结构校验。
- 写入 Planner 日志。

Planner 不负责：

- 真正执行工具。
- 真正执行命令。
- 真正重试。
- 精确代码 patch。
- 动态重规划。
- 长任务后台调度。
- 多 Agent 协作。
- 完整项目自动开发全流程。

这些执行类能力属于 Executor / ReActExecutor 或后续专门工具。

## 3. 核心抽象关系

Planner V1 必须明确区分 `Intent`、`TaskUnit`、`PlanStep`：

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

三类关系都需要支持：

```text
一个 intent -> 一个 TaskUnit
多个 intent -> 一个 TaskUnit
一个 intent -> 多个 PlanStep，复杂时也可以拆成多个 TaskUnit
```

### 3.1 一个 intent 对应一个 TaskUnit

示例：

```text
用户输入：计算 2+3*4
intent: calculate
TaskUnit: 完成一次计算
PlanStep: 调用 math_calculator
```

这类通常是 `micro` 任务。

### 3.2 多个 intent 合成一个 TaskUnit

示例：

```text
用户输入：搜索 FastAPI 测试方案，总结后写入 docs/test.md
intents: search + summarize + write_file
TaskUnit: 生成 FastAPI 测试方案文档
steps:
  step_1: 搜索资料
  step_2: 总结资料
  step_3: 写入文件
```

这类是强依赖流水线任务。Planner 应生成一份连续步骤，而不是每个 intent 单独生成一份计划。

### 3.3 多个 intent 对应多个 TaskUnit

示例：

```text
用户输入：读取 a.pdf 总结重点，同时把 b.csv 统计一下

TaskUnit task_1: 总结 a.pdf
  step_1: 读取 a.pdf
  step_2: 总结内容

TaskUnit task_2: 统计 b.csv
  step_3: 读取 b.csv
  step_4: 分析数据
```

V1 中独立 TaskUnit 先顺序执行，不做并行。

### 3.4 一个复杂 intent 拆成多个步骤或多个任务

示例：

```text
用户输入：设计一个客户管理系统
intent: design_project
TaskUnit: 设计客户管理系统
steps:
  step_1: 明确需求边界
  step_2: 设计功能模块
  step_3: 设计数据模型
  step_4: 设计接口和页面流程
  step_5: 输出开发计划
```

如果用户输入明显包含多个阶段，例如“先设计系统，再生成测试计划，最后写部署方案”，则拆成多个 TaskUnit。

## 4. Planner 输入

Planner 输入保持：

```python
create_plan(user_input: str, task: AnalysisResult) -> TaskPlan
```

Planner 主要依赖 Analyzer 输出：

```text
raw_input
intent_sequence
parameters
file_info
task_type
project_stage
tech_stacks
mode
action_policy
requires_clarification
clarification_questions
requires_confirmation
confirmation_reason
execution_strategy
recommended_tools
available_tools
missing_tools
tool_strategy
risk_flags
risk_level
trace_id
```

Analyzer 的输出是 Planner 的重要参考，但不是不可调整的最终计划。Planner 可以基于规则或 LLM 调整任务顺序、补充必要步骤、合并或拆分任务，但必须记录原因。

V1 中 Planner 暂不直接依赖完整会话记忆。跨对话项目进度、会话摘要、长期记忆检索由后续 SessionManager / Memory / Context Builder 提供。Planner V1 可以预留 `planning_context` 扩展入口，但主接口先保持与当前代码兼容。

## 5. Planner 输出结构

### 5.1 TaskPlan

Planner V1 建议输出：

```python
class TaskPlan:
    plan_id: str
    source_trace_id: str | None
    goal: str
    mode: str
    task_type: str
    execution_strategy: str
    planning_strategy: str
    can_execute: bool
    risk_policy: str
    required_tools: list[str]
    available_tools: list[str]
    missing_tools: list[str]
    task_units: list[TaskUnit]
    steps: list[PlanStep]
    plan_validation_status: str
    plan_validation_notes: list[str]
    added_steps_reason: list[str]
    user_facing_summary: str
    raw_planner_trace: list[str]
```

说明：

- `task_units` 用于表达任务分组，方便人理解和 UI 展示。
- `steps` 是扁平步骤列表，方便 Executor 顺序执行。
- `planning_strategy` 标记计划来源，例如 `policy_rule`、`rule_template`、`llm_planner`、`llm_repaired`、`fallback_rule`。
- `can_execute` 表示计划是否允许进入 Executor 普通执行链路。
- `plan_validation_notes` 记录 Planner 校验、修正、拒绝或补充步骤的原因。

### 5.2 TaskUnit

建议新增：

```python
class TaskUnit:
    id: str
    title: str
    description: str
    intent_refs: list[str]
    task_type: str
    status: str
    depends_on: list[str]
    step_ids: list[str]
    expected_outcome: str
```

说明：

- `intent_refs` 表示这个任务由哪些 Analyzer intent 组成。
- `step_ids` 关联扁平步骤列表。
- V1 中 `status` 初始为 `pending`，真实状态由 Executor 更新。

### 5.3 PlanStep

建议扩展：

```python
class PlanStep:
    id: str
    task_id: str
    description: str
    step_type: str
    tool_name: str | None
    args: dict
    input_from: list[str]
    output_key: str | None
    depends_on: list[str]
    expected_output: str
    requires_confirmation: bool
    confirmation_reason: str | None
    on_failure: str
    retryable: bool
    max_retries: int
    fallback_tools: list[str]
    allow_model_reasoning: bool
    metadata: dict
```

建议 `step_type`：

```text
tool
model
clarify
confirm
block
shell
check
respond
```

建议 `on_failure`：

```text
stop
retry
fallback_to_model
fallback_to_shell
ask_user
skip_optional
fail
```

V1 默认 `max_retries=3`，但真正重试由 Executor 执行。

### 5.4 字段枚举和 ID 规范

V1 需要固定关键枚举，避免开发时各模块使用不同字符串。

建议 `TaskPlan.mode`：

```text
micro
meso
meso_advanced
macro
blocked
clarify
confirm
missing_tools
chat
```

建议 `planning_strategy`：

```text
policy_rule
rule_template
llm_planner
llm_repaired
fallback_rule
fallback_model_only
invalid
```

建议 `TaskUnit.status`：

```text
pending
running
completed
failed
skipped
blocked
waiting_user
```

建议 `plan_validation_status`：

```text
valid
repaired
invalid
not_required
```

ID 规范：

```text
plan_id: plan_<uuid短串>
TaskUnit.id: task_1, task_2, task_3
PlanStep.id: step_1, step_2, step_3
```

对象需要提供 `to_dict()`，方便日志、测试、后续 API/UI 展示。

### 5.5 Planner 配置

Planner V1 建议新增配置目录：

```text
config/planner/
  planner_config.json
  rule_templates.json
  llm_planner_prompt.json
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

V1 可以先把规则模板写在代码里，但配置入口需要预留，避免后续规则越来越多后难以维护。

## 6. 计划生成策略

Planner V1 使用“规则优先 + LLM 兜底 + 结构校验”的混合策略。

优先级：

```text
1. 特殊策略硬处理
   block / clarify / confirm / missing_tools / chat

2. 简单 micro 任务规则生成
   calculate / read_file / translate / time_query 等

3. 常见强依赖流水线规则生成
   search + summarize
   search + summarize + write_file
   read_file + summarize
   read_file + extract
   read_file + extract + write_file
   translate + write_file
   convert_format

4. 软件工程或开放复杂任务走 LLM Planner
   design_project / create_project / debug_code / run_test / deploy_project
   复杂 write / analyze / plan / recommend

5. LLM 计划校验失败时进行修复重试
   最多 3 次

6. 仍失败则回退规则计划或返回无法生成可靠计划
```

规则不是为了覆盖所有用户需求，而是处理明确、稳定、低成本的常见任务。LLM 是复杂规划的大脑，但必须受结构化协议和安全校验约束。

### 6.1 TaskUnit 分组规则

Planner 从 intent 到 TaskUnit 时，先判断 intent 之间是“强依赖流水线”还是“相互独立任务”。

强依赖流水线合并成一个 TaskUnit：

```text
search -> summarize -> write_file
read_file -> summarize
read_file -> extract -> write_file
translate -> write_file
analyze -> generate_report
```

独立任务拆成多个 TaskUnit：

```text
不同文件对象
不同输出目标
用户明确说“同时/另外/再帮我处理另一个”
任务之间没有 input_from/output_key 依赖
```

复杂单意图拆成多个 PlanStep，必要时拆 TaskUnit：

```text
单阶段开发/调试/设计任务 -> 一个 TaskUnit，多个 PlanStep
用户明确包含多个阶段，例如“先设计、再测试、最后部署” -> 多个 TaskUnit
```

如果规则无法判断任务边界，交给 LLM Planner 生成 TaskUnit，但必须通过校验。

## 7. LLM Planner 约束

LLM Planner 可以：

- 调整 Analyzer 的 intent 顺序。
- 合并多个 intent 为一个 TaskUnit。
- 将复杂 intent 拆成多个 PlanStep 或 TaskUnit。
- 补充 Analyzer 未识别但完成任务必需的步骤。
- 在工具缺失时尝试生成安全的 shell fallback 计划。

LLM Planner 必须：

- 返回严格 JSON。
- 只使用可用工具列表中的工具名，除非使用明确的 `fallback_to_shell` 策略。
- 不绕过 Analyzer 的 `block` 策略。
- 对 `confirm` 类型动作标记 `requires_confirmation=True`。
- 不生成系统目录修改、危险命令、删除文件、执行代码等未确认步骤。
- 为步骤依赖提供存在的 `depends_on` / `input_from`。
- 对调整 Analyzer 结果的地方写入 `plan_validation_notes`。

如果 LLM 输出与 Analyzer 不一致，可以以 LLM 结果为准，但必须记录：

```text
plan_validation_notes
added_steps_reason
raw_planner_trace
```

这是为了保留“大模型作为大脑”的灵活性，同时保证工程可追踪。

### 7.1 LLM 不可用或返回不可解析时的降级

当前项目默认可能使用 `MockModel`，真实 LLM 不一定可用。因此 Planner V1 必须定义降级策略：

```text
LLM 未配置 / 调用失败 / 返回非 JSON / 校验失败超过 3 次
  -> 如果有规则模板，回退规则计划
  -> 如果任务可 model_only 回答，生成 fallback_model_only 计划
  -> 如果缺少必要信息，生成 clarify 计划
  -> 如果仍无法可靠规划，生成 invalid 计划并说明无法生成可靠计划
```

不能因为 LLM 不可用就生成不完整工具步骤，也不能静默把复杂执行任务降级为看似成功的回答。

## 8. 计划校验

LLM 或规则生成计划后必须进入校验。

V1 至少校验：

- 所有步骤字段是否完整。
- `task_id` 是否能对应到已有 TaskUnit。
- `depends_on` / `input_from` 是否引用已有步骤。
- `tool_name` 是否存在于工具列表。
- 缺失工具是否有安全 fallback。
- 是否越过 Analyzer 的 `block` 策略。
- `requires_confirmation` 是否覆盖删除文件、执行代码、危险命令等动作。
- 文件路径是否缺失。
- 写文件是否具备 `file_path` 和 `content` 或 `input_from`。
- `output_key` 是否能支持后续步骤输入。
- `step_type=tool` 时是否有 `tool_name`。
- `step_type=model/respond` 时是否没有误填执行型工具。
- `step_type=shell` 是否只作为候选计划，且必须交给后续 CommandTool/ShellTool 做安全校验。

校验结果写入：

```text
plan_validation_status = "valid" | "repaired" | "invalid"
plan_validation_notes = [...]
```

工具参数校验在 V1 分两级：

```text
Planner V1 基础校验：按已知工具名检查关键参数是否存在。
ToolRegistry/ToolSpec 完成后：按工具 schema 做严格参数校验。
```

也就是说，Planner 设计不能假设所有工具都已经有完整参数 schema，但需要为未来 ToolRegistry 预留接入点。

## 9. 特殊策略处理

Planner 必须优先处理 Analyzer 的特殊策略：

```text
action_policy=block
requires_clarification=True
requires_confirmation=True
tool_strategy=blocked_missing_tools
mode=chat
```

V1 规则：

- `block`：生成阻断计划，不包含任何真实工具步骤。
- `clarify`：生成一个澄清步骤，不继续生成执行步骤。
- `confirm`：生成确认计划，不把危险执行步骤直接放入普通执行链路。
- `missing_tools`：生成缺工具说明计划；如果存在安全 shell fallback，可标记为候选但不绕过安全校验。
- `chat`：生成 model-only 回答计划，不调用执行型工具。

注意：
用户曾提出删除文件可以由 Executor 执行时拦截确认。但在 V1 中，为了避免当前 CLI 缺少完整确认状态管理导致误执行，Planner 仍应优先生成确认计划。后续引入确认状态管理后，可以把危险步骤保留为 `pending_confirmation` 状态。

## 10. 工具和命令行兜底

Planner 参考 Analyzer 的 `recommended_tools`，但最终工具步骤由 Planner 规划和校验。

Planner 需要能读取当前可用工具列表。V1 可通过 `ToolManager.list_tools()` 获取工具名；后续应升级为 `ToolRegistry`，提供：

```text
tool_name
description
parameters_schema
risk_level
requires_confirmation
fallback_tools
```

工具缺失时：

- solo 模式下不能假装可执行。
- 如果可以使用安全命令行完成，可以生成 `fallback_to_shell` 候选步骤。
- 如果不能安全兜底，则生成文字方案或缺工具说明。

命令行兜底需要后续 ShellTool / CommandTool 支持安全校验：

- 工作区限制。
- 危险命令检测。
- 超时限制。
- 删除、系统目录、权限修改、安装卸载、执行不明脚本必须确认或 block。

V1 可预留 `fallback_to_shell` 字段，但真正命令执行策略主要属于 Executor / Tools 层。

### 10.1 项目级基础工具约定

后续 Tools 层需要优先补齐一组项目级基础工具，作为 Planner、Executor 和 ReActExecutor 的共同能力基线：

```text
阅读工具
编辑工具
终端工具
预览工具
联网搜索工具
```

这五类工具是任务型 Agent 正常处理项目级任务的基础能力：

- 阅读工具：负责读取工作区内文件、目录、文档内容和必要的项目上下文。
- 编辑工具：负责创建、修改、替换、追加文件内容，后续应支持 patch 级编辑和写入安全校验。
- 终端工具：负责执行受控命令、测试、构建、脚本和诊断命令，必须有工作区限制、危险命令检测、超时和确认策略。
- 预览工具：负责预览 HTML、前端页面、图片、文档或运行结果，便于 Executor/Checker 判断任务是否完成。
- 联网搜索工具：负责搜索外部资料、文档、新闻或实时信息，必须记录来源，并受网络可用性和安全策略约束。

项目级约定：

```text
Planner 只规划这些工具的使用，不直接执行。
Executor / ReActExecutor 负责按计划调用工具并记录 Observation。
Tools 层负责工具真实实现、参数 schema、安全校验和错误返回。
Checker 负责根据工具结果判断是否完成、失败、重试、需要用户输入或需要重新规划。
```

后续 ToolRegistry 完成后，这五类基础工具应提供统一 ToolSpec：

```text
tool_name
category
description
parameters_schema
returns_schema
risk_level
requires_confirmation
workspace_scope
timeout
fallback_tools
```

Planner 生成计划时应优先使用这些基础工具名，而不是临时发明工具名。若 Analyzer 或 LLM Planner 需要的工具不在 ToolRegistry 中，Planner 必须生成 `missing_tools`、`fallback_model_only` 或 `invalid` 计划，不能伪装成可执行计划。

## 11. 文件和代码任务

文件任务：

- `read_file` 可规划为 `document_parser`。
- `write_file` 可默认生成写入步骤，但必须具备 `file_path` 和 `content` 或 `input_from`。
- `partial_edit` V1 生成“读取文件 -> 模型生成修改方案 -> 交给 CodeEdit/FileWriter 执行”的计划，不由 Planner 精确 patch。
- `delete_file` 必须确认。
- 系统目录或敏感路径必须 block。

代码执行：

- `execute_code` 必须确认。
- 生成脚本和执行脚本应区分。
- 执行脚本本质上仍是命令或代码执行，必须经过安全策略。

软件工程任务：

- V1 不追求一次对话从 0 到 1 完成完整项目。
- V1 支持单次开发步骤的规划，例如开发某个模块、调试某个 bug、设计某个方案、运行测试、生成部署步骤。
- `project_stage` 和 `tech_stacks` 应影响计划模板和 LLM Planner prompt。

## 12. ReActExecutor 消费方式

Executor 接收一个 TaskPlan：

```text
按 task_units 顺序处理
  -> 每个 TaskUnit 内按 PlanStep 顺序处理
      -> 每个 PlanStep 执行时走 ReAct 小循环
```

V1 推荐：

```text
PlanStep 级 ReAct 小循环
```

V2 再支持：

```text
TaskUnit 级循环
动态重规划
并行任务
```

Planner 为 ReActExecutor 预留：

```text
input_from
output_key
depends_on
on_failure
retryable
max_retries
fallback_tools
allow_model_reasoning
requires_confirmation
```

## 13. 日志和测试

Planner V1 需要独立日志：

```text
logs/planner.log
```

日志记录：

- `plan_id`
- Analyzer `trace_id`
- 原始输入
- intent_sequence
- TaskUnit 分组
- PlanStep 列表
- 工具参数
- 计划来源
- LLM Planner 状态
- 计划校验结果
- 特殊策略处理
- 失败策略
- 可执行性判断

Planner V1 需要 20-40 条 fixture 回归样例：

```text
tests/fixtures/planner_cases.json
```

覆盖：

- micro 单步任务。
- 强依赖多意图流水线。
- 多个独立 TaskUnit。
- 复杂软件工程单意图拆多步骤。
- block / clarify / confirm / missing_tools / chat。
- model_only。
- 工具参数装配。
- 步骤依赖和 output_key。
- LLM Planner JSON 校验和修复。
- LLM 不可用时的降级。
- LLM 调整 Analyzer intent 顺序时的 `plan_validation_notes`。
- 规则无法判断任务边界时交给 LLM Planner。

## 14. Planner V1 完成标准

Planner V1 达标需要满足：

- 能消费 Analyzer V1 输出。
- 能优先处理 block / clarify / confirm / missing_tools / chat。
- 能输出 `TaskPlan -> TaskUnit -> PlanStep` 三层结构。
- 能同时保留 `task_units` 和扁平 `steps`。
- 能处理三类 intent/task/step 关系：
  - 一个 intent -> 一个 TaskUnit。
  - 多个 intent -> 一个 TaskUnit。
  - 一个复杂 intent -> 多个 PlanStep，必要时多个 TaskUnit。
- 能为 micro 任务生成单步工具计划。
- 能为常见强依赖多意图任务生成有序多步计划。
- 能表达步骤依赖和中间结果传递。
- 能为工具步骤装配基础参数。
- 能为 model_only 步骤生成模型调用计划。
- 能调用 LLM Planner 处理规则覆盖不了的复杂任务。
- 能校验 LLM 计划并最多修复重试 3 次。
- 能输出 `plan_id/source_trace_id/required_tools/missing_tools/planning_strategy` 等调试字段。
- 能在 LLM 不可用时明确降级，而不是生成不完整计划。
- 能固定关键枚举和 ID 规范，保证 Planner/Executor/测试使用同一套协议。
- 有 Planner 单元测试和 fixture 回归样例。
- 与当前 Executor 兼容，同时为后续 ReActExecutor 预留字段。

## 15. 面试讲法

可以这样讲 Planner：

> Planner 是结构化任务拆解层和工具调用前的计划层。它不直接执行工具，而是消费 Analyzer 的结构化分析结果，把用户意图组织成 TaskUnit，再拆成 Executor 可执行的 PlanStep。简单稳定任务优先走规则模板，复杂开放任务使用 LLM Planner 生成结构化计划，并通过规则校验保证工具存在、依赖正确、安全策略不被绕过。Executor 后续按计划执行，每个步骤内部可以采用 ReAct 循环完成 Reason-Act-Observe-Check。这样既保留大模型作为大脑的灵活性，也保证工程上的可控、可测试和可追踪。
