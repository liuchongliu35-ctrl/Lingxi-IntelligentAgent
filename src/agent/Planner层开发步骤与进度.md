# Planner 层开发步骤与进度

本文档用于跨 Session 记录 Planner 层开发进度。后续切换新对话继续开发 Planner 时，优先阅读本文档和 `src/agent/Analyzer层开发步骤与进度.md`。

## 当前定位

Planner 负责消费 Analyzer 的结构化输出，把任务转换成可执行的结构化计划。Planner 不直接执行工具，也不直接调用真实工具。

当前链路：

```text
Analyzer -> Planner -> Executor
```

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

## 待开发

### Step 1：消费 Analyzer V1 新字段

状态：待开发

目标：

- Planner 应使用 Analyzer 的 `mode`、`action_policy`、`requires_clarification`、`missing_tools`、`tool_strategy` 等字段。

验收标准：

- `requires_clarification=True` 时优先生成澄清计划。
- `action_policy=block` 时生成拒绝执行计划。
- `action_policy=confirm` 时生成确认计划。
- `mode=chat` 时不生成执行型工具计划。
- `tool_strategy=blocked_missing_tools` 时生成缺工具说明计划。

### Step 2：结构化多步计划

状态：待开发

目标：

- 对多意图任务生成有顺序的多步计划。
- 使用 `intent_sequence` 决定执行顺序。

验收标准：

- `search + summarize` 生成搜索、整理、总结步骤。
- `read_file + extract + write_file` 生成读取、提取、写入步骤。
- 每个步骤有稳定 `id`、`description`、`tool_name`、`args`、`expected_output`。

### Step 3：工具参数装配

状态：待开发

目标：

- 根据 Analyzer 的 `parameters/file_info` 为工具步骤装配参数。

验收标准：

- 文件读取使用 `file_path`。
- 翻译使用 `content` 和 `target_language`。
- 文件写入使用 `file_path`、`content`、`overwrite`。
- 文件移动/复制/重命名使用 `source_path`、`target_path`。

### Step 4：确认动作计划

状态：待开发

目标：

- 对删除文件、执行代码、执行命令等动作生成暂停确认步骤。

验收标准：

- Planner 不让高风险步骤直接进入普通执行步骤。
- 确认步骤能被 Executor 识别并暂停。

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
