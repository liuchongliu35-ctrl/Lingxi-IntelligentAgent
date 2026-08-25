# Memory / Context MVP 开发步骤与验收标准

本文档是 Memory / Context MVP 的实际开发执行标准。后续开发每完成一个 Step，都应同时检查本文档和对应设计文档；如果实现过程中发现设计与其他层的真实接口不一致，应先记录差异，再按“最小改动、保持职责边界、优先兼容已有链路”的原则处理。

## 一、总体目标与开发边界

本阶段的目标是让 Agent 具备可运行、可恢复、可测试的多轮会话能力：

```text
Runtime / CLI / API
  -> SessionManager
  -> ContextBuilder
  -> ReactAgent
      -> Analyzer
      -> Planner
      -> ReActExecutor
          -> Models / Tools
  -> Response / OutputFeedback
  -> Memory 持久化消息和用户可见事件
```

Memory / Context MVP 负责：

```text
session 生命周期。
用户消息和 Agent 最终消息持久化。
用户可见执行事件持久化与会话回放。
SessionState 加载和恢复。
summary + recent_messages + current_input 上下文构建。
按消息数量触发自动摘要。
SQLite 事务、幂等、错误降级和轻量日志。
```

Memory / Context MVP 不负责：

```text
意图识别、任务规划、工具选择、工具执行和最终回答生成。
隐藏推理、Chain of Thought、原始 prompt 的保存。
向量数据库、RAG、长期知识记忆、多用户权限和云同步。
从进程中断位置继续执行未完成的 Python 调用栈。
```

跨层修改原则：

```text
1. Runtime 负责装配和调用，不直接写 SQL。
2. ReactAgent 保留 add_message / get_history / get_history_text 兼容接口。
3. ReActExecutor 优先复用已有 history、event_callback、execute_stream 接口。
4. Models 负责 compress_context，Memory 只负责选择、组织、保存和降级。
5. Analyzer、Planner、Tools 暂不强制依赖 Memory；只有出现明确多轮理解问题时才增加可选参数。
6. 如果小幅修改其他层即可接入，允许修改其他层；如果需要大改，则优先由 Memory 提供适配层。
```

## 二、设计文档对照关系

| 开发内容 | 必须对照的设计文档 |
| --- | --- |
| 总体职责和跨层调用 | `Memory层设计决策汇总.md`、`Memory层架构与跨层交互设计.md` |
| 数据类、状态、枚举、SQLite 表 | `Memory层数据模型与SQLite设计.md` |
| 上下文窗口、summary、自动压缩 | `Memory层上下文与自动摘要设计.md` |
| ExecutionEvent、timeline、回放和脱敏 | `Memory层执行事件与会话回放设计.md` |
| 每一步的目标和验收 | 本文档 |
| 跨层联调和问题定位 | `Memory层跨层联调开发计划.md` |
| 已确认的用户选择和边界 | `Memory设计问题回答(1).txt`、`Memory层设计决策汇总.md` |

## 三、开发步骤

### Step 0：开发前检查与旧原型隔离

目标：

```text
确认当前 Memory 代码是早期占位实现，建立新实现边界。
确认 ReactAgent、ReActExecutor、Models 的实际接口。
确认 src/agent 整理后的 import 路径和兼容问题。
```

执行内容：

```text
1. 阅读 ReactAgent 对 short_term_memory 的调用。
2. 阅读 ReActExecutor 的 ExecutionEvent、EventStream、event_callback 和 history 接口。
3. 确认 ModelManager.compress_context() 的参数和返回结果。
4. 记录旧 short_term_memory.py / long_term_memory.py 中仅需保留的兼容方法。
5. 建立 storage、logs、tests 的目录约定。
```

交付物：

```text
旧原型不再作为新方案的设计基础。
一份实现前接口差异记录，必要时写入跨层联调计划。
```

验收：

```text
能够明确每个 Memory 模块的调用方和下游消费者。
不会在新代码中继续引入 pickle、summary.md 文件式会话存储。
```

对照文档：`Memory层设计决策汇总.md`、`Memory层架构与跨层交互设计.md`。

### Step 1：数据模型、枚举、配置与 ID

目标：

```text
建立所有后续模块共同使用的稳定数据契约。
```

实现内容：

```text
models.py：
  Message
  SessionState
  SessionInfo
  AgentRun
  ExecutionEventRecord
  TimelineItem
  SessionSummary
  ContextBuildResult

config.py：
  database_path
  log_path
  max_recent_messages = 10
  summary_trigger_messages = 14
  summary_batch_messages = 6
  summary_target_chars
  payload/content 最大长度
  是否允许 rule fallback

ids.py：
  session_id、message_id、run_id、event_id、summary_id 生成。
  session_id 合法性校验。
```

必须明确：

```text
timeline_seq 是同一 session 的用户可见时间线全局顺序。
run_id 表示一次用户输入触发的一轮 Agent 执行。
SessionState 是从 SQLite 记录组装的运行时对象，不是一个大 JSON。
```

验收：

```text
非法 session_id 被拒绝。
所有数据对象可以序列化为 SQLite 所需字段。
默认窗口和摘要阈值符合设计。
枚举值和状态值有统一来源，不在各模块中散落硬编码。
```

对照文档：`Memory层数据模型与SQLite设计.md`。

### Step 2：SQLite Schema、迁移和 Repository

目标：

```text
建立 SQLite 唯一持久化源，使重启后仍能恢复完整会话数据。
```

实现内容：

```text
storage.py：
  SQLiteSessionRepository
  数据库目录自动创建
  schema 初始化和版本迁移
  连接、事务和外键配置
  timeline_seq 原子分配
```

表必须覆盖：

```text
sessions
messages
agent_runs
execution_events
session_summaries
schema_migrations
```

Repository 最小接口：

```python
create_session
load_session_row
list_sessions
delete_session
insert_message
insert_run
update_run
insert_execution_event
insert_summary
load_current_summary
load_recent_messages
load_session_timeline
mark_interrupted_runs
```

实现边界：

```text
所有写入使用事务。
event_id、message_id、run_id、summary_id 唯一。
同 session 的 messages 和 execution_events 共用 timeline_seq。
重复事件写入必须幂等。
数据库损坏时不覆盖原数据库。
```

验收：

```text
数据库不存在时自动建库。
关闭并重新打开 Repository 后数据仍存在。
同一个 session 的 timeline_seq 不冲突。
删除 session 时按设计删除关联数据。
重启恢复时 pending / running / waiting_user run 可标记为 interrupted。
```

对照文档：`Memory层数据模型与SQLite设计.md`。

### Step 3：SessionManager 会话生命周期

目标：

```text
为 Runtime、CLI、API 提供稳定的会话业务接口，并隔离 SQL 细节。
```

最小接口：

```python
create_session(session_id=None) -> SessionState
load_session(session_id) -> SessionState
get_or_create_session(session_id=None) -> SessionState
delete_session(session_id) -> bool
list_sessions() -> list[SessionInfo]
append_message(session_id, role, content, metadata=None) -> Message
create_run(session_id, user_message_id) -> AgentRun
append_execution_event(session_id, run_id, event) -> ExecutionEventRecord
complete_run(run_id, final_message_id) -> None
fail_run(run_id, error) -> None
get_session_timeline(session_id) -> list[TimelineItem]
update_summary(session_id, summary, covered_to_timeline_seq) -> SessionSummary
maybe_auto_summarize(session_id) -> SessionSummary | None
get_short_term_memory(session_id) -> ShortTermMemory
```

事务语义：

```text
收到用户输入：
  创建/加载 session，写入 user Message，创建 AgentRun，一次事务提交。

执行事件到达：
  用户可见事件分配 timeline_seq 并立即保存。

最终回答：
  写入 assistant Message，更新 run 和 session，一次事务提交。
```

验收：

```text
同 session 可以连续写入多轮消息。
不同 session 历史完全隔离。
重启后可以恢复 SessionState、消息、摘要和 run 状态。
Runtime 不需要知道 SQL 语句。
```

对照文档：`Memory层架构与跨层交互设计.md`、`Memory层数据模型与SQLite设计.md`。

### Step 4：ShortTermMemory 兼容层

目标：

```text
在不大改 ReactAgent 的前提下，把旧的短期记忆调用习惯接到新的 SessionManager 和 SQLite。
```

保留接口：

```python
add_message(role, content, metadata=None)
get_history()
get_history_text()
clear()
```

实现要求：

```text
ShortTermMemory 只绑定一个 session_id。
SessionManager 管多个 session，ShortTermMemory 不重复管理 session 生命周期。
add_message 背后调用 SessionManager。
get_history_text 背后调用 ContextBuilder。
clear 不直接删除数据库或 session；需要明确为清空当前会话消息或新建 session 的兼容行为。
```

验收：

```text
ReactAgent 原有 add_message / get_history_text 调用不崩溃。
消息实际写入 SQLite。
同一个 ShortTermMemory 不会读到其他 session 的历史。
```

对照文档：`Memory层架构与跨层交互设计.md`、`Memory层上下文与自动摘要设计.md`。

### Step 5：ContextBuilder 上下文构建

目标：

```text
让短对话、中等长度对话都能稳定把必要前文交给 Agent，而不把完整历史无限传给模型。
```

输出：

```python
ContextBuildResult(
    session_id,
    context_text,
    summary,
    recent_messages,
    included_message_ids,
    included_event_ids,
    truncated,
    current_user_input_included,
    token_estimate,
    char_count,
    metadata,
)
```

MVP 规则：

```text
消息数 <= 10：
  传全部 user / assistant 消息和当前输入。

消息数 > 10：
  传 current summary + 最近 10 条 user / assistant 消息和当前输入。

如果 current_user_input 已经落库：
  不在上下文中重复追加。

execution_events 默认只用于回放，不直接进入模型上下文。
```

固定文本格式：

```text
[Session Summary]
{summary 或 No summary yet.}

[Recent Messages]
user: ...
assistant: ...

[Current User Input]
...
```

验收：

```text
空 session 输出稳定。
少于窗口时不丢历史。
超过窗口时只保留 summary 和最近消息。
included_message_ids 与实际文本一致。
truncated、char_count 等元数据正确。
```

对照文档：`Memory层上下文与自动摘要设计.md`。

### Step 6：ConversationSummarizer 自动摘要

目标：

```text
对话变长后自动压缩早期历史，同时保留最近 10 条消息原文。
```

实现流程：

```text
SessionManager.maybe_auto_summarize()
  -> 查询当前 summary 覆盖范围
  -> 找出最近窗口以前、尚未被覆盖的早期消息
  -> 数量达到 summary_batch_messages 才触发
  -> 调用 ModelManager.compress_context()
  -> 成功后新增 session_summaries 版本
  -> 更新 sessions.current_summary_id
```

必须保留：

```text
用户目标、关键决策、文件路径、约束、偏好、未完成事项、重要错误和下一步。
```

不能写入：

```text
隐藏推理、密钥、Cookie、token、完整工具原始输出和无关噪声。
```

失败降级：

```text
摘要失败不阻塞本轮对话。
保留旧 summary。
继续用最近 10 条消息构建上下文。
记录 summary_failed。
下一轮允许再次尝试。
```

验收：

```text
超过阈值可以生成摘要版本。
covered_from/covered_to 范围正确。
当前 summary 指针正确更新。
compress_context 失败时旧 summary 不损坏。
```

对照文档：`Memory层上下文与自动摘要设计.md`、`Memory层数据模型与SQLite设计.md`。

### Step 7：ExecutionEvent 映射与会话回放

目标：

```text
把 ReActExecutor 已产生的事件转成用户可见的会话时间线，同时排除内部事件和敏感内容。
```

实现内容：

```text
event_mapper.py：
  ExecutionEvent -> ExecutionEventRecord
  优先尊重 visible_to_user
  event_type -> display_type
  内容脱敏和长度限制
  sanitized_payload_json 生成
```

保存规则：

```text
visible_to_user=True：
  写入 execution_events，参与用户回放。

visible_to_user=False：
  不进入普通 timeline，只写轻量日志或后续审计。
```

重点映射：

```text
tool_started/tool_finished -> tool_progress
confirmation_requested -> confirmation
step/retry/fallback -> plan_progress
tool_failed/retry_exhausted -> error
final_answer -> final_answer
```

流式边界：

```text
message_delta 不按 token 永久保存。
最终合并为一条 assistant Message。
```

验收：

```text
用户可见事件可以回放。
内部事件不会误进入用户历史。
messages 和 execution_events 按 timeline_seq 合并顺序正确。
重复 event_id 不会重复显示。
敏感 payload 不会进入普通数据库记录。
```

对照文档：`Memory层执行事件与会话回放设计.md`。

### Step 8：Memory 日志、异常降级与恢复

目标：

```text
让持久化、上下文和摘要问题可诊断；单次保存失败不直接摧毁 Agent 主链路。
```

实现内容：

```text
logs/memory.log 使用轻量 JSONL。
记录 session_created、session_loaded、message_appended、
run_created、event_persisted、event_skipped_internal、
context_built、summary_started、summary_completed、
summary_failed、persistence_warning。
```

降级规则：

```text
数据库不存在：自动初始化。
单条持久化失败：记录 warning，允许本轮继续执行并明确标记未持久化。
数据库损坏：不覆盖原库，返回可读错误。
敏感正文不写日志，只记录 ID、类型、长度和状态。
```

验收：

```text
日志能定位“没有保存”“没有加载”“没有拼入上下文”三类问题。
摘要失败和持久化警告不会被伪装成成功。
不出现完整 token、密码、Cookie、原始 prompt 或隐藏推理。
```

对照文档：`Memory层数据模型与SQLite设计.md`、`Memory层执行事件与会话回放设计.md`。

### Step 9：Memory 纯模块测试

目标：

```text
在接入 Runtime 前，先证明 Memory 自身的状态、持久化、上下文和回放闭环正确。
```

测试必须覆盖：

```text
1. 自动生成和指定 session_id。
2. session 隔离。
3. 消息写入、读取和恢复。
4. 最近 10 条窗口。
5. summary + recent_messages 拼接。
6. current_user_input 不重复。
7. SQLite 重启恢复。
8. timeline_seq 合并排序。
9. running run 重启后变为 interrupted。
10. 自动摘要成功和失败降级。
11. visible_to_user 过滤。
12. event_id 幂等。
13. 敏感字段脱敏。
14. 数据库异常时错误可读且原库不被覆盖。
```

验收：

```text
Memory 纯模块测试全部通过。
测试使用临时 SQLite，不污染正式 storage/agent_memory.db。
```

对照文档：所有设计文档，重点是 `Memory层数据模型与SQLite设计.md` 和 `Memory层上下文与自动摘要设计.md`。

### Step 10：ReactAgent 适配

前置：

```text
Step 0-9 完成。
agent import 路径问题已记录并具备处理方案。
```

目标：

```text
让现有 ReactAgent 使用新的 session-aware ShortTermMemory 和 ContextBuilder。
```

适配内容：

```text
Runtime 创建指定 session_id 的 ShortTermMemory。
ReactAgent 继续使用 add_message / get_history_text。
每轮执行前读取 ContextBuilder 输出。
每轮完成后保存最终 assistant Message。
必要时给 ReactAgent 增加可选 run_id、event_callback 或 session 参数。
```

限制：

```text
不重写 Analyzer、Planner、ReActExecutor 的核心职责。
不让 ReactAgent 直接操作 SQL。
```

验收：

```text
ReactAgent 单轮可以运行。
同一 session 第二轮能引用前文。
切换 session 后不会携带旧 session 历史。
```

对照文档：`Memory层架构与跨层交互设计.md`、`Memory层上下文与自动摘要设计.md`。

### Step 11：ReActExecutor 事件联调

目标：

```text
验证执行器产生的事件能够实时或准实时进入 Memory，并可正确回放。
```

联调内容：

```text
优先使用 execute(..., event_callback=...) 保存可见事件。
非流式结果作为兜底，检查 result.events。
流式模式不保存每个 delta，最终合并 assistant Message。
确认事件中的 visible_to_user、type、message、payload 映射正确。
```

需要重点验证：

```text
工具调用、命令执行、文件修改 preview、确认请求、重试、失败、最终回答。
内部 reasoning、ActionPacket、原始 Observation 不进入用户回放。
```

允许的小幅修改：

```text
ReactAgent 或 Runtime 增加 event_callback 透传。
对事件字段做兼容读取。
```

不应做的修改：

```text
不把 Memory 逻辑塞进 ReActExecutor 主循环。
不改变 ReActExecutor 的决策和工具执行语义。
```

验收：

```text
一次任务的用户可见时间线可以完整恢复。
中途异常时，已经保存的可见事件仍存在。
内部事件不会泄露到用户历史。
```

对照文档：`Memory层执行事件与会话回放设计.md`、`Memory层架构与跨层交互设计.md`。

### Step 12：Models 上下文压缩联调

目标：

```text
验证 Memory 的 ConversationSummarizer 能正确复用 Models 层 compress_context，而不是重复实现模型调用。
```

联调内容：

```text
确认输入 chunks、目标长度、保留字段和失败结果协议。
确认 ModelManager 的模型路由、重试和健康检查仍由 Models 层负责。
确认 summary 版本写入 SQLite，Memory 不直接依赖 provider。
```

验收：

```text
真实模型或 mock 模型均能触发摘要流程。
压缩成功后 ContextBuilder 使用新 summary。
模型不可用、返回失败或超时时，本轮对话仍能继续。
```

对照文档：`Memory层上下文与自动摘要设计.md`、`Memory层架构与跨层交互设计.md`。

### Step 13：Runtime / CLI / API 接入

目标：

```text
把 Memory 和已有 Agent 核心链路装配成真正可启动的最小运行系统。
```

Runtime 必须负责：

```text
build_runtime() 统一创建 Model、Tools、Memory、ReactAgent。
接收或生成 session_id。
加载/创建 SessionState。
保存 user Message 并创建 AgentRun。
构建 context_text。
调用 ReactAgent。
接收并保存可见 ExecutionEvent。
保存最终 assistant Message。
更新 run 状态并触发摘要。
返回结构化结果和可读错误。
```

CLI 最小能力：

```text
启动 Agent。
新建 session。
指定 session_id 继续对话。
退出并重新启动后恢复历史。
显示最终回答和必要的用户可见进度。
```

API 可在 CLI 跑通后接入，最小接口建议：

```text
POST /sessions
GET /sessions/{session_id}
POST /sessions/{session_id}/runs
GET /sessions/{session_id}/timeline
GET /health
```

验收：

```text
能启动。
能进行单轮对话。
同一 session 能进行多轮对话。
重启后能继续旧 session。
能查看用户可见会话时间线。
模型、工具、数据库不可用时有基础健康检查和错误信息。
```

对照文档：`Memory层架构与跨层交互设计.md`、`Memory层执行事件与会话回放设计.md`。

### Step 14：最小可跑架构端到端验收

目标：

```text
证明整条 Agent 链路不是只能单测，而是真能对话并完成基础任务。
```

测试场景：

```text
场景 A：普通多轮问答
  第一轮建立事实，第二轮使用“上面/刚才”继续提问。

场景 B：任务型多轮执行
  Agent 读取文件、生成计划、调用工具、返回结果。

场景 C：确认和恢复
  任务需要确认时进入 waiting_user，继续后能完成或明确阻塞。

场景 D：长对话
  超过 10 条消息后使用窗口，超过 14 条后触发 summary。

场景 E：异常恢复
  模拟模型失败、工具失败、持久化警告和进程重启。
```

验收标准：

```text
1. CLI 或 API 可以重复运行，不依赖一次性脚本。
2. 用户看到的会话回放顺序与实际交互一致。
3. Agent 能基于前文完成基础任务。
4. 长对话不会无限增长模型输入。
5. 摘要失败不阻塞普通对话。
6. 内部推理和敏感数据不泄露。
7. 同一 session 与不同 session 行为符合预期。
```

对照文档：全部 Memory 设计文档，以及各层自己的开发步骤和测试文档。

## 四、开发依赖顺序

```text
Step 0
  -> Step 1
  -> Step 2
  -> Step 3
  -> Step 4 + Step 5
  -> Step 6 + Step 7 + Step 8
  -> Step 9
  -> Step 10
  -> Step 11 + Step 12
  -> Step 13
  -> Step 14
```

说明：

```text
Step 4 和 Step 5 可以在接口确定后并行设计，但实现时应先保证 SessionManager 能提供数据。
Step 6、Step 7、Step 8 都依赖基础持久化完成。
Step 10-12 是跨层联调，不能在 Memory 单测未通过时直接跳过。
Step 13 是真正启动 Agent 的入口，不等于 Memory 已完成；它需要前面所有核心契约稳定。
```

## 五、每个 Step 完成后的固定记录格式

在 `Memory层开发步骤与进度.md` 中同步：

```text
已完成：
- Step X：...
- 对照设计文档：...
- 已实现接口/表/测试：...

验证结果：
- 测试命令或手动验收方式：...
- 结果：通过 / 部分通过 / 未通过

当前未完成：
- ...

下一步：
- Step X+1：...
```

如果实现和设计文档出现差异，必须在进度文档中记录：

```text
差异内容：
原因：
影响的其他层：
采用的适配方案：
是否需要回写设计文档：
```

