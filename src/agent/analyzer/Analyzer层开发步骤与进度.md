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
trace_id
decision_summary
pending_intents_recorded
llm_fallback_status
llm_fallback_error
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

状态：已完成第一版

目标：

- 建立正式样例集。
- 覆盖 30-50 条 Analyzer 回归用例。
- 让后续规则调整有稳定基准。

已完成内容：

- 新增正式样例集：

```text
tests/fixtures/analyzer_cases.json
```

- 当前样例数：32 条。
- 扩展 Analyzer 测试为 fixture 驱动测试。
- 测试会复制真实 Analyzer 配置到临时目录，并替换日志和 pending intent 路径，避免污染真实运行数据。
- 保留 Step 9 的精确单元测试，继续覆盖参数提取、缺参澄清和 pending intents。

主要文件：

```text
tests/test_analyzer_v1.py
tests/fixtures/analyzer_cases.json
```

已覆盖场景：

- 单意图：计算、翻译、读取文件、写文件。
- 多意图：搜索+总结、读取+提取、分析+报告。
- 模式覆盖：solo、chat、同轮冲突。
- 文件操作：移动、复制、重命名、删除、覆盖、局部修改。
- 缺参澄清：缺 topic、缺语言、缺文件路径、缺目标路径。
- 高风险：法律、医疗、金融、隐私。
- 危险操作：删除系统目录、执行危险命令。
- 软件工程：开发、测试、调试、部署、文档。
- 技术栈：Python、Java、C++、前端、数据库、Docker、深度学习。

已验证：

```text
python -B -m unittest tests.test_analyzer_v1
```

结果：

```text
Ran 7 tests
OK
```

验收状态：

- 至少 30 条样例。
- 全部测试可通过。
- 每条样例至少断言关键字段，不只断言不报错。

### Step 11：进一步增强规则意图识别质量

状态：已完成第一版

目标：

- 减少关键词误判。
- 提高中英文混合输入识别质量。
- 优化多意图顺序。

已完成内容：

- 增强 `intent_keywords.json`：
  - 补充 `write`、`convert_format`、`create_project`、`design_project` 等更细粒度关键词。
  - 支持“创建一个/搭建项目/初始化项目/设计一个/转换为/转为”等表达。
- 增强规则意图排序：
  - 更具体的长关键词会获得轻微加权，减少通用意图抢占具体意图。
  - 多意图连接词场景下，按关键词在用户输入中的出现顺序排列意图。
- 增加反误判规则：
  - `write` 的“创建”不会在项目创建场景中误抢 `create_project`。
  - `report.md` 文件名不会误触发 `generate_report`。
  - 格式转换场景优先识别“转换为/转成/转为”后的目标格式，而不是源文件扩展名。
- 扩展回归样例：
  - 样例数从 32 条增加到 40 条。
  - 新增多意图顺序、项目创建/设计、格式转换、文件名反误判、chat 指导类执行代码等用例。

主要文件：

```text
src/agent/complexity_analyzer.py
config/analyzer/intent_keywords.json
tests/fixtures/analyzer_cases.json
tests/test_analyzer_v1.py
```

已验证：

```text
python -B -m unittest tests.test_analyzer_v1
```

结果：

```text
Ran 7 tests
OK
```

验收标准：

- 多意图场景能稳定保留 2-4 个主要意图。
- chat 类提问不会误走危险执行。
- 文件名中的关键词不会明显误触发。

### Step 12：完善 task_type、project_stage、tech_stacks

状态：已完成第一版

目标：

- 让 Planner 后续能基于更稳定的任务类型和工程阶段做规划。

已完成内容：

- 完善 `task_type` 映射规则：
  - 支持 `file_operation`、`document_understanding`、`software_engineering`、`data_analysis`、`content_generation`、`project_management`、`tool_operation`、`chat`、`qa`。
  - 对只有技术栈和工程词、但未命中明确 intent 的输入，可提升为 `software_engineering`。
  - 对数据、统计、趋势、图表、csv/xlsx/pandas/numpy 等输入，可识别为 `data_analysis`。
  - 对当前时间/日期类输入，可识别为 `tool_operation`。
- 完善工程阶段识别：
  - 明确 intent 优先映射：`design_project -> design`、`create_project -> develop`、`debug_code -> debug`、`run_test -> test`、`deploy_project -> deploy`。
  - 文本规则覆盖 `design/develop/test/debug/deploy/document`。
  - 新增“训练”作为 develop 阶段信号。
- 扩展技术栈词表：
  - Python、Java、C++。
  - 前端、后端、数据库、测试、部署、深度学习。
  - 增加 SQLAlchemy、MyBatis、Qt、Tailwind、NestJS、Oracle、SQL Server、Elasticsearch、Vitest、Jest、Jenkins、Scikit-learn 等常见关键词。
- 改进技术栈匹配：
  - 英文短词使用边界匹配，减少 `py` 等短关键词误判。
  - 保留中文和带符号关键词的直接匹配能力。
- 扩展回归样例：
  - 样例数从 40 条增加到 48 条。
  - 新增数据分析、项目管理、文档阶段、C++、数据库设计、前端测试、部署流水线、时间查询等用例。

主要文件：

```text
src/agent/complexity_analyzer.py
config/analyzer/tech_stacks.json
tests/fixtures/analyzer_cases.json
```

已验证：

```text
python -B -m unittest tests.test_analyzer_v1
```

结果：

```text
Ran 7 tests
OK
```

验收标准：

- 软件工程任务能稳定输出 `task_type=software_engineering`。
- 技术栈识别不明显漏掉常见关键词。

### Step 13：完善风险与确认策略

状态：已完成第一版

目标：

- 让危险动作在 Analyzer 层更早、更明确地被识别。

已完成内容：

- 扩展 `risk_rules.json`：
  - 高风险领域增加法律、医疗、金融、隐私相关关键词。
  - 增加 `dangerous_command_keywords`，覆盖 `rm -rf /`、`sudo rm -rf`、`del /s c:\`、`format c:`、`mkfs`、`shutdown`、`system32` 等危险命令。
  - 扩展敏感路径：`C:\Windows`、`C:\Program Files`、`C:\Users`、`/etc`、`/usr`、`/bin`、`/var`、`/root`、`/home`。
- 完善风险策略：
  - 高风险领域任务保持 `allow`，但输出 `risk_flags` 和中等风险等级。
  - `delete_file`、`execute_code` 在 solo 模式下输出 `confirm`。
  - 明显危险命令和敏感路径在 solo 模式下输出 `block`。
  - chat 模式下危险命令指导不会误标为执行请求，保持 `allow`，但输出风险标记。
- 修复带空格 Windows 路径识别：
  - 例如 `C:\Program Files\app\config.txt` 现在可以整体识别为路径，并触发敏感路径 block。
- 扩展回归样例：
  - 样例数从 48 条增加到 54 条。
  - 新增代码执行确认、危险命令阻断、chat 危险命令指导、Unix 敏感路径、Windows Program Files 敏感路径、API key 隐私风险等用例。

主要文件：

```text
config/analyzer/risk_rules.json
src/agent/complexity_analyzer.py
tests/fixtures/analyzer_cases.json
```

已验证：

```text
python -B -m unittest tests.test_analyzer_v1
```

结果：

```text
Ran 7 tests
OK
```

验收标准：

- `删除系统目录` 类请求输出 `block`。
- `删除普通工作区文件` 在 solo 模式输出 `confirm`。
- `告诉我怎么删除文件，不要执行` 输出 `chat` 且不直接执行。

### Step 14：Planner/Executor 消费 Analyzer 新字段

状态：已完成第一版

目标：

- 当前 Analyzer 已输出 `action_policy`、`requires_confirmation`、`missing_tools` 等字段，但 Planner/Executor 还没有完整消费。

已完成内容：

- Planner 已在普通计划生成前优先消费 Analyzer 新字段：
  - `action_policy=block` -> 生成 `mode=blocked` 的拒绝执行计划。
  - `requires_clarification=True` -> 生成 `mode=clarify` 的澄清计划，并携带 Analyzer 生成的 `clarification_questions`。
  - `requires_confirmation=True` 或 `action_policy=confirm` -> 生成 `mode=confirm` 的确认计划。
  - `tool_strategy=blocked_missing_tools` -> 生成 `mode=missing_tools` 的缺工具说明计划。
  - `mode=chat` -> 生成 `mode=chat` 的非工具执行回答计划。
- Executor 已在执行入口消费 Analyzer/Planner 策略：
  - `block/blocked` 直接返回拒绝执行说明，不调用工具。
  - `clarify/macro` 优先返回 Analyzer 的澄清问题，不调用工具。
  - `confirm` 返回确认提示并暂停执行，不调用危险工具。
  - `blocked_missing_tools/missing_tools` 返回缺失工具说明。
  - `chat` 模式只调用模型生成指导或答案，不调用工具。
- 新增 Planner/Executor 策略消费测试，覆盖 block、clarify、confirm、chat、missing tools 和正常 micro 工具执行。

主要文件：

```text
src/agent/planner.py
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

- 危险操作不会进入工具调用。
- 缺参任务不会误执行。
- chat 模式不会执行文件写入、删除、代码执行等动作。

### Step 15：Analyzer 日志增强

状态：已完成第一版

目标：

- 让 `logs/analyzer.log` 更适合跨轮调试。

已完成内容：

- `AnalysisResult` 新增 `trace_id`，每轮 Analyzer 调用都会生成唯一追踪 id。
- `AnalysisResult` 新增 `decision_summary`，用于保存自然语言判断摘要。
- `AnalysisResult` 新增 `pending_intents_recorded`，记录本轮写入的 pending intent 名称。
- `logs/analyzer.log` 增强为更完整的 JSONL 调试记录，新增：
  - `trace_id`
  - `mode_decision`
  - `intent_sequence`
  - `requires_clarification`
  - `clarification_decision`
  - `file_info`
  - `edit_mode`
  - `project_stage`
  - `tech_stacks`
  - `risk_level`
  - `requires_confirmation`
  - `confirmation_reason`
  - `risk_decision`
  - `tool_strategy`
  - `tool_decision`
  - `confidence_score`
  - `confidence_level`
  - `pending_intents_recorded`
  - `pending_intent_decision`
  - `user_facing_summary`
  - `decision_summary`
- 日志现在可以直接看出：
  - 模式来自默认配置还是用户输入覆盖。
  - 是否缺参以及缺哪些参数。
  - 风险策略为什么是 `allow/confirm/block`。
  - 工具策略为什么是 `tool/model_only/blocked_missing_tools`。
  - 本轮是否写入 pending intents。
- 新增日志增强测试：
  - 校验澄清场景日志会写入 `trace_id`、`decision_summary`、`clarification_decision`、`tool_decision`。
  - 校验 pending intent 场景日志会写入 `pending_intents_recorded` 和 pending 判断说明。

主要文件：

```text
src/agent/complexity_analyzer.py
tests/test_analyzer_v1.py
```

已验证：

```text
python -B -m unittest tests.test_analyzer_v1 tests.test_planner_executor_policy
```

结果：

```text
Ran 14 tests
OK
```

验收标准：

- 调试时可以从单条日志看出 Analyzer 的关键判断链路。

### Step 16：分类器接口对接准备

状态：已完成第一版

目标：

- 不训练模型，只把未来真实分类器的输入输出协议固定下来。

已完成内容：

- 明确 `IntentPrediction` 协议：
  - `probabilities` 是 `intent_name -> 0.0~1.0` 的概率/置信度分布。
  - `intents` 是有序候选意图列表；未提供时可由概率降序推导。
  - `source` 标记来源，例如 `classifier`、`classifier_stub`、真实模型名称。
  - `model_version` 预留真实模型版本。
  - `not_ready` 和 `error` 表示分类器未就绪或不可用原因。
  - `multi_label` 区分单意图分类和多意图/多标签分类。
  - `raw_output` 预留真实模型原始输出，便于调试。
- `IntentPrediction` 新增协议辅助能力：
  - `ready`
  - `top_intent`
  - `top_probability`
  - `from_probabilities()`
  - `unavailable()`
  - `normalized()`
  - `ordered_intents()`
- `IntentClassifier` 新增：
  - `is_ready`
  - `status()`
  - 默认 stub 返回 `classifier_not_ready`，保持 Analyzer 稳定降级。
- Analyzer 消费分类器输出时会：
  - 过滤未知意图，避免真实分类器异常输出污染 Analyzer 内置意图体系。
  - 规范化概率值，限制在 `0.0~1.0`。
  - 记录 `classifier_confidence`、`uncertainty_reason` 和分类器 trace。
  - 只在分类器 ready 且不确定性检测通过后采纳分类结果。
  - 分类器未就绪或不确定时继续降级到 LLM 兜底或 `chat` fallback。
- `UncertaintyDetector` 增强：
  - 单标签分类继续使用低置信度、小 margin、高熵判断。
  - 多标签分类通过 `multi_label=True` 跳过单标签 margin/entropy 误判，允许多个高置信意图并存。
- 新增分类器协议测试：
  - stub `not_ready` 状态。
  - 概率分布规范化、排序、非法值过滤。
  - Analyzer 消费 ready 单意图分类器。
  - Analyzer 消费 ready 多意图分类器。
  - Analyzer 在低置信分类器输出时稳定降级。
  - `UncertaintyDetector` 边界行为。

主要文件：

```text
src/agent/intent_classifier.py
src/agent/uncertainty_detector.py
src/agent/complexity_analyzer.py
tests/test_intent_classifier_protocol.py
```

已验证：

```text
python -B -m unittest tests.test_analyzer_v1 tests.test_planner_executor_policy tests.test_intent_classifier_protocol
```

结果：

```text
Ran 20 tests
OK
```

验收标准：

- 未来真实模型只要按接口返回概率分布，就能接入 Analyzer。
- 分类器未就绪时 Analyzer 稳定降级。

### Step 17：LLM 兜底结构化解析增强

状态：已完成第一版

目标：

- 让 LLM 兜底在真实模型接入后可靠返回结构化 JSON。

已完成内容：

- 独立封装 `_build_llm_intent_prompt()`：
  - 要求 LLM 只返回 strict JSON。
  - 明确返回 schema：`{"intents":[{"name":"...","confidence":0.0,"reason":"..."}]}`。
  - 明确最多返回 `max_intents` 个意图。
  - 明确优先使用已知意图。
  - 明确可返回 `unknown`。
  - 明确非内置真实意图使用简短 snake_case 名称。
- 增强 LLM JSON 解析：
  - 支持直接 dict。
  - 支持 list。
  - 支持 Markdown fenced JSON。
  - 支持从带前后说明文字的响应中提取第一个完整 JSON object/array。
  - 支持单意图 `{ "intent": "...", "confidence": ... }` 兼容格式。
- 增强 LLM 候选意图规范化：
  - 意图名统一 normalize。
  - 置信度限制在 `0.0~1.0`。
  - 非数字、NaN、无穷和非正置信度会被丢弃。
  - 候选排序按“已知意图优先 -> 高置信度 -> 原始顺序”。
  - 全局意图排序也增加已知意图优先，避免自定义 pending 意图抢占内置意图。
- 支持显式 `unknown`：
  - LLM 返回 `unknown/unclear/unsure` 时统一为 `unknown`。
  - `unknown` 会进入澄清流程，生成面向用户的补充问题。
- 解析失败不再静默 fallback 为 `chat`：
  - LLM 调用失败 -> `llm_fallback_status=call_failed`，返回 `unknown`。
  - JSON 解析失败 -> `llm_fallback_status=parse_failed`，返回 `unknown`。
  - 无有效候选 -> `llm_fallback_status=unknown`，返回 `unknown`。
  - 低置信自定义意图低于 pending 阈值 -> 返回 `unknown`，不写入 pending。
- `AnalysisResult` 新增：
  - `llm_fallback_status`
  - `llm_fallback_error`
- `logs/analyzer.log` 新增：
  - `llm_fallback_status`
  - `llm_fallback_error`
  - `llm_fallback_decision`
- 新增 LLM fallback 协议测试：
  - prompt 约束。
  - fenced JSON 解析。
  - 已知意图优先。
  - 非内置意图满足阈值写入 pending。
  - parse failed 进入 `unknown` 和澄清。
  - 显式 `unknown` 被允许。
  - 低置信自定义意图不写 pending，并进入 `unknown`。

主要文件：

```text
src/agent/complexity_analyzer.py
tests/test_llm_fallback_protocol.py
```

已验证：

```text
python -B -m unittest tests.test_analyzer_v1 tests.test_planner_executor_policy tests.test_intent_classifier_protocol tests.test_llm_fallback_protocol
```

结果：

```text
Ran 24 tests
OK
```

验收标准：

- MockModel 下不影响主链路。
- 真实模型返回 JSON 时可被稳定解析。
- 非内置意图满足阈值时可写入 pending。

### Step 18：Analyzer V1 验收回归

状态：已完成

目标：

- 确认 Analyzer V1 达到可以支撑 Planner/Executor 继续开发的程度。

已完成内容：

- 新增 Analyzer V1 总体验收测试：
  - 逐条运行当前 54 条 fixture 回归样例。
  - 校验每条 Analyzer 输出都包含 V1 必需字段。
  - 校验 `raw_input/cleaned_input/trace_id/intent_sequence/intent` 等基础一致性。
  - 校验 `mode/action_policy/tool_strategy` 均落在允许枚举范围内。
  - 校验七维 `dimension_scores`、`decision_summary`、`raw_analysis_trace` 和 `user_facing_summary` 均稳定输出。
  - 校验 `logs/analyzer.log` 写入 JSONL，并包含 `trace_id`、`decision_summary`、`raw_analysis_trace` 等关键调试字段。
- 新增关键能力闭环验收：
  - 缺参任务能进入澄清。
  - 高风险命令能输出 `block/high/dangerous_command`。
  - 工具缺失能输出 `tool_strategy=blocked_missing_tools` 和缺失工具名。
  - 分类器未就绪时能稳定降级到 fallback。
  - LLM 兜底新增 intent 能写入 pending intents。
  - 日志能记录风险、工具缺失、pending intent 等关键判断。

主要文件：

```text
tests/test_analyzer_v1_acceptance.py
src/agent/Analyzer层开发步骤与进度.md
```

已验证：

```text
python -B -m unittest tests.test_analyzer_v1 tests.test_analyzer_v1_acceptance tests.test_planner_executor_policy tests.test_intent_classifier_protocol tests.test_llm_fallback_protocol
```

结果：

```text
Ran 26 tests
OK
```

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

验收结论：

- Analyzer V1 基线已完成，可以支撑 Planner/Executor 后续开发。
- 当前 fixture 数量为 54 条，已超过原定 30-50 条基准范围，作为 V1 扩展回归集继续保留。

## 4. 建议下一轮对话优先任务

下一轮建议切换到 Planner Step 2：

```text
请阅读 src/agent/Analyzer层开发步骤与进度.md、src/agent/Planner层开发步骤与进度.md 和 src/agent/Analyzer层设计决策汇总.md。
Analyzer V1 基线已经完成，下一步建议继续开发 Planner 层 Step 2：结构化多步计划。
目标是让 Planner 基于 Analyzer 的 intent_sequence、parameters、file_info、task_type、project_stage、tool_strategy 等字段，为多意图任务生成稳定的有序多步计划。
```

原因：

- Analyzer V1 的配置、意图识别、参数提取、风险策略、工具评估、日志、分类器协议、LLM 兜底和验收回归都已完成第一版。
- Planner Step 1 已经消费 Analyzer 的关键策略字段。
- 下一步应让 Planner 使用 `intent_sequence` 生成真正的结构化多步计划，而不是继续停留在基础 meso 模板。
- 现有 54 条 Analyzer 回归样例和 26 个相关单元测试可以保护后续 Planner 开发。

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
