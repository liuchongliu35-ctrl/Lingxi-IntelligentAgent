# Analyzer 层开发步骤与进度

本文档用于跨 Session 记录 Analyzer 层开发进度。后续新对话如果需要继续开发 Analyzer，优先阅读：

```text
src/agent/Analyzer层开发步骤与进度.md
src/agent/Analyzer层设计决策汇总.md
开发进度.md
```

当前开发原则：

- Analyzer 只负责理解、判断、路由和输出结构化分析结果，不直接执行工具。
- 真实意图分类器模型尚未训练完成，但接口按“未来可正常调用”保留。
- V1 先把规则、分类器占位、LLM 兜底、参数提取、风险策略、复杂度评分和测试闭环搭起来。
- 后续开发按小步提交，不在一次对话里尝试做完整 Analyzer。

## 重点必看：跨 Session 进度更新规则

后续每完成一个可验收开发步骤，都必须同步更新对应层的进度文档。

执行规则：

```text
完成一个 Step
  -> 跑测试或完成逻辑验证
  -> 确认没有明显问题
  -> 更新对应层的“开发步骤与进度.md”
  -> 下一个对话继续未完成步骤
```

注意：

- 不需要每改一个小函数都更新文档。
- 但完成一个清晰阶段后必须更新，例如“参数提取增强”“Planner 消费 action_policy”“Tools 统一 schema”。
- 文档必须记录已完成内容、验证方式、当前未完成项和下一轮建议。
- 如果 Session 中途暂停，也要优先把当前进度写入文档，避免新对话丢失上下文。

## 1. Analyzer V1 目标输出

Analyzer V1 输出对象至少包含：

```text
raw_input
cleaned_input
mode
mode_source
task_type
intents
intent_sequence
entities
parameters
missing_parameters
clarification_questions
file_info
edit_mode
project_stage
tech_stacks
risk_level
risk_flags
action_policy
requires_confirmation
confirmation_reason
dimension_scores
complexity_score
complexity_level
execution_strategy
recommended_tools
available_tools
missing_tools
tool_strategy
confidence_score
confidence_level
raw_analysis_trace
user_facing_summary
```

## 2. 已完成步骤

### Step 0：基础工程化底座

状态：已完成

已完成内容：

- 主链路已收敛为 `Analyzer -> Planner -> Executor -> ToolResult`。
- `main.py` 使用 `src.*` 标准导入。
- `ReactAgent` 已拆成轻量编排层。
- `Planner` 已有 `PlanStep`、`TaskPlan`。
- `Executor` 已有 `StepExecution`、`ExecutionResult`。
- 工具层已有统一 `ToolResult`。
- `ModelManager` 默认使用 `MockModel`，真实模型后续可替换。

主要文件：

```text
main.py
src/agent/react_agent.py
src/agent/planner.py
src/agent/executor.py
src/tools/base.py
src/models/model_manager.py
```

### Step 1：Analyzer 配置拆分

状态：已完成

已完成内容：

- 建立 `config/analyzer/` 配置目录。
- 建立 Analyzer 配置加载器。
- 配置拆分为多个 JSON 文件，避免单个大配置文件。

主要文件：

```text
src/agent/analyzer_config.py
config/analyzer/analyzer_config.json
config/analyzer/intents.json
config/analyzer/intent_keywords.json
config/analyzer/risk_rules.json
config/analyzer/complexity_weights.json
config/analyzer/tech_stacks.json
config/analyzer/tool_mapping.json
```

### Step 2：Analyzer V1 输出结构

状态：已完成

已完成内容：

- `src/agent/complexity_analyzer.py` 中已定义 `AnalysisResult`。
- 保留 `StructuredTask = AnalysisResult` 兼容当前 Planner/Executor。
- 已支持主要结构化字段。

主要文件：

```text
src/agent/complexity_analyzer.py
```

### Step 3：基础模式识别

状态：已完成

已完成内容：

- 支持 `solo/chat`。
- 全局默认模式通过 `AGENT_MODE` 或 Analyzer 配置控制。
- 单轮输入可临时覆盖模式。
- 同一轮多个模式指令冲突时，以最后出现的明确指令为准。

示例：

```text
只告诉我步骤，不要执行 -> chat
直接帮我完成 -> solo
```

### Step 4：基础意图识别

状态：已完成

已完成内容：

- 规则关键词意图识别已实现。
- 多意图阈值过滤已实现。
- 每轮最多保留 `max_intents` 个主要意图。
- 意图分类器占位接口已接入。
- 分类器未就绪时可稳定降级。

主要文件：

```text
src/agent/complexity_analyzer.py
src/agent/intent_classifier.py
src/agent/uncertainty_detector.py
```

### Step 5：基础实体、参数和文件识别

状态：已完成

已完成内容：

- 数字提取。
- 计算表达式提取。
- 文件路径提取。
- 文件类型识别。
- 基础实体输出。
- `edit_mode = full_overwrite | partial_edit` 基础识别。

### Step 6：风险策略与 action_policy

状态：已完成

已完成内容：

- 支持法律、医疗、金融、隐私等高风险领域识别。
- 支持危险命令关键词拦截。
- 支持敏感路径识别。
- 支持 `action_policy = allow | confirm | block`。
- 支持 `requires_confirmation` 和 `confirmation_reason`。

注意：

- Analyzer 目前只输出策略。
- Planner/Executor 暂未真正消费确认/拒绝策略，这是后续步骤。

### Step 7：七维复杂度评分

状态：已完成基础版

已完成内容：

- 已按七维模型输出 `dimension_scores`。
- 已根据配置权重计算 `complexity_score`。
- 已映射 `complexity_level`。
- 已映射 `execution_strategy = micro | meso | meso_advanced | macro`。

后续需要继续提升：

- 当前评分规则仍偏启发式。
- 还需要更多测试样例校准阈值。

### Step 8：工具能力评估

状态：已完成基础版

已完成内容：

- 根据 `tool_mapping.json` 输出推荐工具。
- 区分 `recommended_tools`、`available_tools`、`missing_tools`。
- 输出 `tool_strategy = tool | model_only | blocked_missing_tools`。

后续需要继续提升：

- 工具层还没有完整参数 schema 和风险等级。
- Analyzer 与未来 ToolRegistry 还未完全打通。

### Step 9：参数提取增强、缺参澄清、pending_intents

状态：已完成第一版

已完成内容：

- 增强参数提取：
  - `topic`
  - `target_language`
  - `source_language`
  - `count`
  - `time_range`
  - `output_format`
  - `content`
- 增强文件操作参数：
  - `source_path`
  - `target_path`
  - `all_paths`
- 完善缺参判断：
  - `search` 缺 `topic`
  - `translate` 缺 `target_language`
  - `read_file/delete_file` 缺 `file_path`
  - `write_file` 缺 `file_path` 或 `content/topic`
  - `move_file/copy_file/rename_file` 缺 `source_path/target_path`
  - `summarize/extract/analyze/compare` 缺 `content_or_file`
- 同步生成面向用户的 `clarification_questions`。
- 实现 `pending_intents` 写入机制：
  - 位置：`storage/analyzer/pending_intents.json`
  - 条件：LLM 兜底返回非内置意图，且置信度 >= `pending_intent_threshold`
  - V1 不自动合并相似意图。
  - 记录 `raw_name`、`normalized_name`、`confidence`、`examples`、出现次数和状态。

新增测试：

```text
tests/test_analyzer_v1.py
```

已验证：

```text
python -B -m unittest tests.test_analyzer_v1
```

结果：

```text
Ran 6 tests
OK
```

## 3. 未完成步骤

### Step 10：补齐 Analyzer 测试样例集

状态：待开发

目标：

- 建立正式样例集。
- 覆盖 30-50 条 Analyzer 回归用例。
- 让后续规则调整有稳定基准。

建议新增：

```text
tests/fixtures/analyzer_cases.json
```

建议扩展：

```text
tests/test_analyzer_v1.py
```

覆盖场景：

- 单意图：计算、翻译、读取文件、写文件。
- 多意图：搜索+总结、读取+提取、分析+报告。
- 模式覆盖：solo、chat、同轮冲突。
- 文件操作：移动、复制、重命名、删除、覆盖、局部修改。
- 缺参澄清：缺 topic、缺语言、缺文件路径、缺目标路径。
- 高风险：法律、医疗、金融、隐私。
- 危险操作：删除系统目录、执行危险命令。
- 软件工程：开发、测试、调试、部署、文档。
- 技术栈：Python、Java、C++、前端、数据库、Docker、深度学习。

验收标准：

- 至少 30 条样例。
- 全部测试可通过。
- 每条样例至少断言关键字段，不只断言不报错。

### Step 11：进一步增强规则意图识别质量

状态：待开发

目标：

- 减少关键词误判。
- 提高中英文混合输入识别质量。
- 优化多意图顺序。

建议内容：

- 为 `intent_keywords.json` 增加更细粒度关键词。
- 增加反误判规则，例如文件名、技术词、普通名词不应误触发意图。
- 区分“用户想执行”和“用户只是询问步骤”。
- 对显式顺序词“先/再/然后”进行顺序提取。
- 对明显不合理顺序做基础重排。

验收标准：

- 多意图场景能稳定保留 2-4 个主要意图。
- chat 类提问不会误走危险执行。
- 文件名中的关键词不会明显误触发。

### Step 12：完善 task_type、project_stage、tech_stacks

状态：待开发

目标：

- 让 Planner 后续能基于更稳定的任务类型和工程阶段做规划。

建议内容：

- 完善 `task_type` 映射规则。
- 扩展工程阶段识别：
  - `design`
  - `develop`
  - `test`
  - `debug`
  - `deploy`
  - `document`
- 扩展技术栈词表：
  - Python
  - Java
  - C++
  - 前端框架
  - 后端框架
  - 数据库
  - 测试框架
  - 部署工具
  - 深度学习技术栈

验收标准：

- 软件工程任务能稳定输出 `task_type=software_engineering`。
- 技术栈识别不明显漏掉常见关键词。

### Step 13：完善风险与确认策略

状态：待开发

目标：

- 让危险动作在 Analyzer 层更早、更明确地被识别。

建议内容：

- 扩展 `risk_rules.json`。
- 明确区分：
  - 高风险领域：允许回答，但提示风险。
  - 危险执行：需要确认。
  - 明显危险操作：直接 block。
- 对 `delete_file`、`execute_code`、系统命令做更严格判断。
- 对 chat 模式下的危险操作指导，避免误标为执行请求。

验收标准：

- `删除系统目录` 类请求输出 `block`。
- `删除普通工作区文件` 在 solo 模式输出 `confirm`。
- `告诉我怎么删除文件，不要执行` 输出 `chat` 且不直接执行。

### Step 14：Planner/Executor 消费 Analyzer 新字段

状态：待开发

目标：

- 当前 Analyzer 已输出 `action_policy`、`requires_confirmation`、`missing_tools` 等字段，但 Planner/Executor 还没有完整消费。

建议内容：

- `action_policy=block`：Planner 或 Executor 直接返回拒绝执行说明。
- `action_policy=confirm`：生成确认步骤，暂停执行。
- `requires_clarification=True`：优先返回 Analyzer 生成的澄清问题。
- `tool_strategy=blocked_missing_tools`：返回缺失工具说明和可替代文字方案。
- `mode=chat`：不调用执行型工具，只生成指导或答案。

验收标准：

- 危险操作不会进入工具调用。
- 缺参任务不会误执行。
- chat 模式不会执行文件写入、删除、代码执行等动作。

### Step 15：Analyzer 日志增强

状态：待开发

目标：

- 让 `logs/analyzer.log` 更适合跨轮调试。

建议内容：

- 日志增加自然语言说明字段。
- 记录 pending intent 是否写入。
- 记录模式覆盖原因。
- 记录缺参澄清原因。
- 记录工具缺失原因。
- 可选：增加 `trace_id`，后续和 Orchestrator 对齐。

验收标准：

- 调试时可以从单条日志看出 Analyzer 的关键判断链路。

### Step 16：分类器接口对接准备

状态：待开发

目标：

- 不训练模型，只把未来真实分类器的输入输出协议固定下来。

建议内容：

- 明确 `IntentClassifier.predict_single()` 输出协议。
- 明确 `IntentClassifier.predict_multi()` 输出协议。
- 增加分类器 ready/not_ready 状态。
- 增加分类器输出概率分布测试。
- 接入 `UncertaintyDetector` 的更多边界测试。

验收标准：

- 未来真实模型只要按接口返回概率分布，就能接入 Analyzer。
- 分类器未就绪时 Analyzer 稳定降级。

### Step 17：LLM 兜底结构化解析增强

状态：待开发

目标：

- 让 LLM 兜底在真实模型接入后可靠返回结构化 JSON。

建议内容：

- 独立封装 LLM intent fallback prompt。
- 限定最大返回意图数。
- 限定已知意图优先。
- 允许 `unknown`。
- 允许新意图进入 pending。
- 解析失败时进入 UNKNOWN/澄清，不 silently fallback 成默认意图。

验收标准：

- MockModel 下不影响主链路。
- 真实模型返回 JSON 时可被稳定解析。
- 非内置意图满足阈值时可写入 pending。

### Step 18：Analyzer V1 验收回归

状态：待开发

目标：

- 确认 Analyzer V1 达到可以支撑 Planner/Executor 继续开发的程度。

验收标准：

- 30-50 条 Analyzer 回归测试通过。
- 能稳定输出所有 V1 必需字段。
- 缺参任务能进入澄清。
- 高风险任务能提示或阻断。
- 工具缺失能被识别。
- 多意图任务能输出合理顺序。
- 分类器未就绪时能降级。
- pending intents 能写入。
- analyzer.log 能记录关键过程。

## 4. 建议下一轮对话优先任务

下一轮建议从 Step 10 开始：

```text
请阅读 src/agent/Analyzer层开发步骤与进度.md 和 src/agent/Analyzer层设计决策汇总.md。
继续开发 Analyzer 层的 Step 10：补齐 Analyzer 测试样例集。
建立 tests/fixtures/analyzer_cases.json，并扩展 tests/test_analyzer_v1.py，
先覆盖 30 条典型 Analyzer 用例，确保当前已实现的参数提取、缺参澄清、风险策略、多意图和 pending_intents 不回归。
```

原因：

- 当前 Analyzer 已经开始有较多规则。
- 后续继续加规则前，需要先建立回归样例集。
- 没有测试集时，继续扩展意图和参数规则很容易引入误判。

## 5. 当前注意事项

- 运行 Python 验证时建议使用：

```text
python -B ...
```

避免继续产生 `__pycache__` 变更。

- 当前主 Agent 启动依赖 `requests` 等 requirements 中的包。如果环境未安装依赖，`main.py` 可能因 `SearchTool` 导入失败而无法启动。
- Analyzer 单元测试使用临时配置目录，不应该污染真实 `logs/` 和 `storage/`。
- 后续不要优先训练 Transformer 意图分类器；先完成 Analyzer V1 工程闭环。
- 后续不要优先做 UI、完整 FastAPI、复杂 RAG 或多 Agent。
