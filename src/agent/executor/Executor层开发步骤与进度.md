# ReActExecutor 层开发步骤与进度

本文档用于跨 Session 记录 Executor 层开发进度。后续切换新对话继续开发 Executor 时，优先阅读本文档、Planner 进度文档和 Analyzer 进度文档。

## 当前定位

Executor 负责执行 Planner 输出的 `TaskPlan`。Executor 不重新理解用户自然语言，只消费结构化计划和 Analyzer 结果。

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

### Step 0：基础执行结构

状态：已完成

主要文件：

```text
src/agent/executor.py
```

已完成内容：

- 定义 `StepExecution`。
- 定义 `ExecutionResult`。
- 可以执行 `TaskPlan`。
- 有 `tool_name` 时调用 `ToolManager.run_tool()`。
- 无 `tool_name` 时调用模型生成回答。
- 任一步骤失败会提前返回失败结果。
- `macro` 澄清任务会返回固定澄清提示。

### Step 1：消费 action_policy 和确认状态

状态：已完成第一版

目标：

- Executor 应在执行前处理 `block/confirm/allow`。

已完成内容：

- `action_policy=block` 或 `plan.mode=blocked` 时直接返回拒绝执行说明，不调用工具。
- `requires_confirmation=True`、`action_policy=confirm` 或 `plan.mode=confirm` 时返回确认提示并暂停执行，不调用工具。
- `allow` 的普通计划保留原有执行路径。
- `tool_strategy=blocked_missing_tools` 或 `plan.mode=missing_tools` 时返回缺失工具说明。
- `mode=chat` 由 Planner 生成非工具步骤，Executor 只调用模型生成指导或答案。

主要文件：

```text
src/agent/executor.py
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

- `block` 直接拒绝执行。
- `confirm` 返回需要用户确认的结果，不调用危险工具。
- `allow` 正常执行。

### Step 2：澄清问题执行

状态：已完成第一版

目标：

- 当 Analyzer 输出 `clarification_questions` 时，Executor 应返回这些问题，而不是固定模板。

已完成内容：

- `requires_clarification=True` 或 `plan.mode=clarify` 时，Executor 优先返回 Analyzer 的 `clarification_questions`。
- 没有 Analyzer 澄清问题时，保留原有通用澄清提示作为兜底。
- 澄清状态不会调用工具。

主要文件：

```text
src/agent/executor.py
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

- 缺语言时问目标语言。
- 缺文件路径时问文件路径。
- 缺 topic 时问主题或对象。

## 待开发

### Step 3：步骤级日志与结果记录

状态：待开发

目标：

- 记录每一步输入、输出、成功/失败、耗时。

验收标准：

- 可以定位某次请求失败在哪个步骤。
- 后续可接入 trace_id/run_id。

### Step 4：失败处理与重试

状态：待开发

目标：

- 对可重试工具失败做有限重试。
- 对不可重试失败直接返回结构化失败结果。

验收标准：

- 工具未配置、权限拒绝、参数缺失能给出明确原因。

### Step 5：动态重评估入口

状态：待开发

目标：

- 当执行过程发现实际复杂度变化时，为后续重新 Analyzer/Planner 预留接口。

验收标准：

- V1 可先不实现完整重规划，但保留状态字段和中断点。

## 暂停开发时更新格式

每次结束 Executor 开发时，在本文档新增或更新：

```text
已完成：
- ...

当前未完成：
- ...

下一轮建议：
- 从 Step X 开始，目标是 ...
```
