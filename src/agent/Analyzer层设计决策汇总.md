# Analyzer 层设计决策汇总

Analyzer 层开发分阶段建议如下：第一轮建立 Analyzer 配置和稳定输出对象；第二轮补齐意图/参数/风险/复杂度的规则质量；第三轮接 Planner/Executor 的确认流程和缺工具分支；第四轮补测试集和回归用例。

本文档汇总 `回答Analyzer层的问题.txt`、`继续补充Analyzer层问题.txt`、`继续回答Analyzer问题2.0.txt`、`继续回答Analyzer问题3.0.txt` 中已经确认的 Analyzer 设计决策。后续 Analyzer V1 开发以本文档作为需求基线。

## 1. Analyzer 的定位

Analyzer 是任务型 Agent 的任务理解中枢，不负责实际执行工具，也不直接完成任务。

Analyzer 负责回答以下问题：

- 用户想做什么。
- 用户是想让 Agent 执行，还是只想要指导回答。
- 任务包含哪些意图。
- 任务是否是多意图。
- 参数是否完整。
- 是否需要追问澄清。
- 是否存在风险或危险操作。
- 任务复杂度是多少。
- 应该走 `micro / meso / meso_advanced / macro` 哪一种执行策略。
- 需要哪些工具，当前工具是否满足。
- 是否需要 Planner/Executor 暂停并请求确认。

标准链路：

```text
User Input
  -> Analyzer
  -> Planner
  -> Executor
  -> ToolResult
  -> Response
```

## 2. 使用场景

主要面向个人日常使用和软件工程任务处理。

核心场景包括：

- 日常个人效率助手。
- Word、Excel、PDF 等文件读取、理解、总结和写入。
- 文件目录和资源管理。
- 项目设计、开发、测试、调试、部署等软件工程全流程。
- 数据分析、报告生成、内容整理。
- 中英文混合输入的通用多功能任务处理。

V1 文件格式一等支持：

- `txt`
- `md`
- `pdf`
- `docx`
- `xlsx`
- `csv`
- `json`

`pptx` 暂不作为 V1 目标。

## 3. 模式设计

Analyzer 支持两种模式：

- `solo`：Agent 默认全权负责完成用户任务。
- `chat`：Agent 只给指导、解释、方案、步骤或回答，不主动执行任务。

全局默认模式通过配置控制：

```env
AGENT_MODE=solo
```

单轮输入可以临时覆盖模式，但只对当前轮生效，不修改全局配置。

示例：

- “只告诉我步骤，不要执行” -> 当前轮使用 `chat`。
- “直接帮我完成” -> 当前轮使用 `solo`。
- 如果同一轮中同时出现多个模式指令，以最后出现的明确指令为准。

Analyzer 需要区分：

- 用户想要结果。
- 用户想要 Agent 执行动作。
- 用户只想要文字指导。

## 4. 内部意图体系

内部意图名称统一使用英文枚举，展示给用户时再转中文。

V1 内置核心意图包括：

```text
calculate
search
summarize
translate
write
analyze
plan
read_file
write_file
execute_code
extract
compare
recommend
chat
classify
convert_format
organize_files
list_files
find_files
rename_file
copy_file
move_file
delete_file
create_project
design_project
debug_code
run_test
deploy_project
generate_report
```

这些只是系统固定可识别意图。若用户输入包含其他意图，Analyzer 仍需要通过分类器或 LLM 兜底提取，并进入新意图处理流程。

## 5. 多意图策略

多意图任务最多识别 4 个主要意图。

规则：

- 每个意图都需要有分数。
- 默认意图阈值为 50 分，可配置。
- 若只有 2 个意图超过阈值，则只保留这 2 个。
- 若超过 4 个意图都超过阈值，则取最高的 4 个。
- 对超过 4 个意图的情况，需要提示“已优先处理主要任务，其他任务可后续继续”。

多意图顺序：

- 如果用户给出的顺序明确且合理，则保留用户顺序。
- 如果用户顺序可能不合理，Agent 应根据任务逻辑重新整理顺序。
- Analyzer 输出整理后的 `intent_sequence`，供 Planner 使用。

## 6. 意图识别策略

V1 优先级：

```text
规则识别
  -> 意图分类器
  -> 不确定性检测
  -> LLM 兜底
  -> UNKNOWN / 澄清
```

分类器策略：

- 后续使用 Transformer 架构训练。
- Analyzer 只依赖统一接口。
- 分类器未训练完成前，接口保持占位。

LLM 兜底策略：

- 允许 LLM 兜底提取意图。
- LLM 放在规则和分类器之后，控制 token 成本。
- LLM 可以生成新意图。
- 新意图不应直接进入生产意图库。

新意图处理：

- 新意图先写入 `pending_intents`。
- V1 不自动合并相似意图。
- 记录 `raw_name` 和 `normalized_name`。
- 进入 `pending_intents` 的建议条件：LLM 提取出非内置意图，置信度 >= 0.65，且不是明显闲聊。
- 若低于阈值，只记录普通日志，不进入 pending。

存储位置：

```text
storage/analyzer/pending_intents.json
```

是否自动加入意图库通过配置控制。默认建议写入 pending，由后续人工或管理界面处理。

## 7. 参数与实体提取

Analyzer 需要输出：

- `entities`
- `parameters`
- `missing_parameters`
- `clarification_questions`

参数缺失时：

- Analyzer 同时输出缺失字段和澄清问题。
- Planner/Executor 可直接使用这些澄清问题。
- 对用户展示使用自然语言。
- 日志中保留具体字段和错误信息。

文件识别需要同时提取：

- `file_path`
- `file_type`

示例：

```python
file_path = "data/report.xlsx"
file_type = "xlsx"
```

## 8. 文件操作策略

文件操作意图需要区分：

- 创建文件。
- 写入文件。
- 覆盖文件。
- 局部修改文件。
- 移动文件。
- 复制文件。
- 重命名文件。
- 删除文件。

默认允许执行：

- `write_file`
- `overwrite_file`
- `move_file`
- `copy_file`
- `rename_file`

需要确认：

- `delete_file`
- `execute_code`
- 执行命令或代码。

危险操作应由 Analyzer 输出：

```python
requires_confirmation = True
confirmation_reason = "delete_file"
action_policy = "confirm"
```

Planner/Executor 负责暂停执行并提示用户确认。

更危险的操作应直接拒绝：

```python
action_policy = "block"
```

例如：

- 删除系统目录。
- 执行明显危险命令。
- 访问工作区外敏感路径。

文件修改模式：

```python
edit_mode = "full_overwrite" | "partial_edit"
```

V1 中 Analyzer 只负责识别是整文件覆盖还是局部修改。精确定位代码行、生成 patch、应用 patch 由后续 Planner/Executor 实现。

## 9. 工具能力判断

Analyzer 需要判断完成任务所需工具是否存在。

输出字段建议：

```python
recommended_tools = []
available_tools = []
missing_tools = []
tool_strategy = "tool" | "model_only" | "blocked_missing_tools"
```

用户不需要看到内部工具决策细节，但日志中需要记录。

若缺少工具：

- solo 模式不能继续全权执行。
- 需要提示用户缺少哪些工具。
- 给出后续完成步骤或文字方案。
- V1 先做结构字段，V2 再做 UI 展示。

## 10. 风险与执行策略

高风险领域包括：

- 法律。
- 医疗。
- 金融。
- 隐私。
- 文件删除。
- 代码执行。
- 系统命令。

法律、医疗、金融、隐私类任务：

- 允许回答。
- 需要提示风险。

危险执行类任务：

- `delete_file` 和 `execute_code` 需要确认。
- 危险命令、系统目录删除等需要直接 block。

Analyzer 输出：

```python
risk_flags = []
risk_level = "low" | "medium" | "high"
action_policy = "allow" | "confirm" | "block"
```

## 11. 七维复杂度评分

V1 严格按照 `复杂度判定.md` 中的七维模型实现。

七维包括：

- `uncertainty`
- `steps`
- `domain_risk`
- `tools`
- `information`
- `data_processing`
- `creativity`

权重配置化，建议放在：

```text
config/analyzer/complexity_weights.json
```

评分明细输出到日志，不直接展示给普通用户。

Analyzer 输出：

```python
dimension_scores = {}
complexity_score = 0.0
complexity_level = "simple" | "medium" | "complex" | "ambiguous" | "high_risk"
execution_strategy = "micro" | "meso" | "meso_advanced" | "macro"
```

执行策略保留：

- `micro`
- `meso`
- `meso_advanced`
- `macro`

## 12. 项目工程任务识别

Analyzer 需要识别项目工程任务的技术栈和阶段。

工程阶段包括：

```text
design
develop
test
debug
deploy
document
```

技术栈 V1 使用固定词表规则识别，LLM 兜底补充。

技术栈词表需要覆盖：

- Python 生态。
- Java 生态。
- C++ 生态。
- 前端框架。
- 后端框架。
- 数据库。
- 测试框架。
- 部署工具。
- 深度学习相关技术栈。

建议包含：

```text
python
java
c++
fastapi
django
flask
spring
springboot
node
express
react
vue
nextjs
typescript
javascript
mysql
postgresql
redis
mongodb
docker
linux
nginx
pytest
unittest
playwright
selenium
pytorch
tensorflow
numpy
pandas
langchain
transformers
faiss
```

项目工程任务不要求一次对话完成全流程。后续需要 SessionManager 支持会话概要导出。

概要保存位置：

```text
storage/sessions/{session_id}/summary.md
```

概要生成模块：

- `SessionManager` 负责会话生命周期和概要导出。
- `MemoryManager` 负责单个 Session 的记忆管理。
- Analyzer 只输出当前任务阶段和结构化状态。

## 13. task_type

Analyzer 需要输出 `task_type`，比单个 intent 更适合 Planner 使用。

建议枚举：

```text
qa
file_operation
document_understanding
software_engineering
data_analysis
content_generation
project_management
tool_operation
chat
```

## 14. 日志与过程展示

V1 需要日志文件。

Analyzer 日志位置：

```text
logs/analyzer.log
```

日志需要记录：

- 原始输入。
- 清洗后输入。
- 模式判断。
- 意图及分数。
- 多意图截断情况。
- 参数提取结果。
- 缺失参数。
- 澄清问题。
- 七维复杂度明细。
- 风险标记。
- action_policy。
- 推荐工具、可用工具、缺失工具。
- raw_analysis_trace。

普通用户 UI 不展示系统级细节。开发模式可以展示完整 Analyzer 判断过程。

用户界面的过程展示更偏向：

- Agent 正在做什么。
- 正在调用什么工具。
- 工具调用结果。
- 是否修改了文件。
- 是否执行了代码。

## 15. 配置文件设计

Analyzer 配置 V1 使用 JSON，但不放在单个巨大文件中，而是拆分为多个职责清晰的文件。

建议结构：

```text
config/analyzer/
  analyzer_config.json
  intents.json
  intent_keywords.json
  risk_rules.json
  complexity_weights.json
  tech_stacks.json
  tool_mapping.json
```

各文件职责：

- `analyzer_config.json`：全局配置。
- `intents.json`：系统内置意图定义。
- `intent_keywords.json`：关键词匹配词表。
- `risk_rules.json`：高风险规则、危险操作规则。
- `complexity_weights.json`：七维复杂度权重和阈值。
- `tech_stacks.json`：技术栈词表。
- `tool_mapping.json`：意图到工具的映射关系。

示例全局配置：

```json
{
  "agent_mode": "solo",
  "max_intents": 4,
  "intent_score_threshold": 50,
  "pending_intent_threshold": 0.65,
  "allow_auto_pending_intents": false
}
```

## 16. V1 完成标准

Analyzer V1 需要完成：

- 规则意图识别。
- 多意图识别。
- 中英文混合输入。
- 实体参数提取。
- 参数缺失判断。
- 澄清问题生成。
- 风险识别。
- action_policy 判断。
- 七维复杂度评分。
- 执行路由。
- task_type 输出。
- file_path/file_type/edit_mode 识别。
- 技术栈识别。
- 工具能力判断。
- pending_intents 记录。
- analyzer.log 日志。
- 配置文件拆分。
- 30-50 条 Analyzer 测试样例。

## 17. 暂无新增阻塞问题

目前 Analyzer V1 的产品目标、工程边界、配置策略、日志策略、风险策略、模式策略和完成标准已经明确。

后续可以直接进入开发，不需要继续补充大问题。

如果开发过程中发现具体字段或规则冲突，再按实现问题单独确认。

## 18. 下一轮开发入口

下一轮可以直接进入 Analyzer V1 编码，不需要继续做需求追问。

建议开发顺序：

1. 建立 Analyzer 配置目录。
2. 定义 Analyzer 输出数据结构。
3. 实现配置加载器。
4. 实现规则意图识别。
5. 接入占位意图分类器接口。
6. 实现模式识别和单轮覆盖。
7. 实现实体、参数、文件路径、文件类型、编辑模式识别。
8. 实现 task_type、工程阶段、技术栈识别。
9. 实现风险识别、确认策略、拒绝策略。
10. 实现七维复杂度评分。
11. 实现工具能力评估。
12. 实现 pending_intents 记录。
13. 实现 analyzer.log 日志。
14. 补充 30-50 条 Analyzer 测试样例。

建议新增配置文件：

```text
config/analyzer/
  analyzer_config.json
  intents.json
  intent_keywords.json
  risk_rules.json
  complexity_weights.json
  tech_stacks.json
  tool_mapping.json
```

建议新增运行数据文件：

```text
storage/analyzer/pending_intents.json
logs/analyzer.log
tests/fixtures/analyzer_cases.json
```

Analyzer V1 输出对象需要至少包含：

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

字段设计原则：

- 面向系统的字段必须结构化，方便 Planner、Executor、日志和测试使用。
- 面向用户的字段必须自然语言化，避免暴露过多系统内部枚举和分数。
- `raw_analysis_trace` 只用于开发调试和日志，不直接展示给普通用户。
- Analyzer 只做判断和路由，不直接执行工具。
- 删除文件、执行代码、危险命令必须交给 Planner/Executor 暂停确认或拒绝。

## 19. 当前结论

目前不需要继续新增 Analyzer 需求问题。

下一步应进入实现阶段：按照本文档重构 `src/agent/complexity_analyzer.py`，并补齐 Analyzer 配置、日志、pending intent、测试样例和与 Planner/Executor 的结构化衔接。
