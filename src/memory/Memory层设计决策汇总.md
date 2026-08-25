# Memory / Context MVP 层设计决策汇总

本文档是 Memory / Context MVP 层的总设计入口。更细的设计拆分在同目录下的专题文档中：

```text
Memory层架构与跨层交互设计.md
Memory层数据模型与SQLite设计.md
Memory层执行事件与会话回放设计.md
Memory层上下文与自动摘要设计.md
Memory层开发步骤与验收标准.md
```

本设计基于 `Memory设计问题回答(1).txt` 中已经确认的回答。

## 1. 总目标

Memory / Context MVP 的目标不是做完整长期记忆、RAG 或复杂知识库，而是先让 Agent 具备最小但可靠的多轮会话能力：

```text
1. 支持 session_id。
2. 同一个 session 能连续多轮对话。
3. 不同 session 的历史互不混淆。
4. 程序重启后能恢复会话历史和 SessionState。
5. 能重新展示用户第一次对话时实际看到的会话时间线。
6. 能为 ReactAgent / ReActExecutor 提供 summary + 最近消息窗口。
7. 对话变长后自动摘要早期历史，避免模型上下文无限增长。
```

一句话定位：

```text
Memory 是会话状态、历史持久化、上下文构建和会话回放层。
Memory 不是 Analyzer、Planner 或 ReActExecutor 的替代品。
```

## 2. 核心边界

Memory 负责：

```text
Session 生命周期：
  创建、加载、保存、删除、列出 session。

消息历史：
  保存 user / assistant / system / tool 等聊天消息。

用户可见执行时间线：
  保存 ReActExecutor 中 visible_to_user=True 的 ExecutionEvent。

上下文构建：
  输出 ContextBuildResult，并兼容旧的 get_history_text()。

自动摘要：
  当会话消息超过阈值时，调用 Models 层 compress_context() 压缩早期历史。

持久化：
  使用 SQLite 保存 sessions、messages、agent_runs、execution_events、session_summaries。
```

Memory 不负责：

```text
不做意图识别。
不生成 TaskPlan / TaskUnit / PlanStep。
不决定工具调用。
不生成最终回答。
不保存隐藏推理 / Chain of Thought。
不把 API Key、令牌、Cookie、密码写入普通会话历史。
不把完整原始 ToolResult / raw_observation 当成用户聊天历史。
```

## 3. 分层结构

建议新 Memory 层结构：

```text
src/memory/
  __init__.py

  models.py
    Message
    SessionState
    SessionInfo
    AgentRun
    ExecutionEventRecord
    TimelineItem
    SessionSummary
    ContextBuildResult

  ids.py
    new_session_id()
    new_message_id()
    new_run_id()
    new_event_id()
    validate_session_id()

  storage.py
    SQLiteSessionRepository
    schema 初始化
    schema migration
    事务封装

  session_manager.py
    SessionManager
    session 生命周期
    message / run / event / summary 写入接口

  short_term_memory.py
    绑定单个 session 的兼容接口
    add_message()
    get_history()
    get_history_text()
    clear()

  context_builder.py
    ContextBuilder
    ContextBuildResult
    summary + recent_messages + current_input

  summarizer.py
    ConversationSummarizer
    调用 ModelManager.compress_context()
    失败降级

  event_mapper.py
    ReActExecutor ExecutionEvent -> Memory ExecutionEventRecord
    visible_to_user 过滤
    display_type 映射

  logging.py
    memory JSONL 日志
    不记录完整敏感正文

  config.py
    MemoryConfig
```

旧文件 `short_term_memory.py` 和 `long_term_memory.py` 是占位原型。实现时可以保留文件名但重写内容；不沿用旧的 pickle 长期记忆方案。

## 4. 持久化决策

确定使用 SQLite：

```text
storage/agent_memory.db
```

MVP 不使用：

```text
JSON 文件保存整个 session。
pickle 保存对话。
向量数据库保存普通会话历史。
```

SQLite 表：

```text
sessions
messages
agent_runs
execution_events
session_summaries
schema_migrations
```

关键原则：

```text
SessionState 是运行时对象，不是数据库中的一个大 JSON 字段。
SessionState 由 SQLite 中的 session、messages、summary 等记录加载并组装。
完整历史长期保存在 SQLite。
每轮传给模型的上下文只取 summary + 最近 10 条对话消息 + 当前输入。
```

## 5. 上下文策略

MVP 阶段使用消息条数控制，不做复杂 token 计算：

```text
max_recent_messages = 10
summary_trigger_messages = 14
summary_batch_messages = 6
```

构建规则：

```text
消息数量 <= 10:
  context = 全部消息 + 当前输入

消息数量 > 10:
  context = 当前 summary + 最近 10 条消息 + 当前输入

消息数量 > 14:
  触发自动摘要，压缩较早历史，最近 10 条原样保留。
```

自动摘要调用 Models 层：

```text
ConversationSummarizer
  -> ModelManager.compress_context()
  -> session_summaries 新版本
  -> sessions.current_summary_id
```

摘要失败不阻塞本轮对话。

## 6. 执行事件与回放

ReActExecutor 已经有事件流：

```text
ExecutionEvent.visible_to_user
EventStream.visible_events()
EventStream.internal_events()
EventStream.to_user_timeline()
```

Memory 的用户可见历史持久化规则：

```text
visible_to_user=True:
  可以进入 execution_events 表。

visible_to_user=False:
  只进日志或后续审计，不进入普通用户会话回放。
```

注意：不能只看事件类型判断。例如执行层中某些中间 `final_answer` / `request_replan` 会被显式标记为 `visible_to_user=False`，Memory 必须尊重这个字段。

## 7. 与其他层的适配原则

Memory 不是孤立开发。适配规则如下：

```text
如果其他层只需小改即可更好接入 Memory：
  允许修改其他层。

如果其他层需要大改：
  优先让 Memory 提供兼容接口。
```

当前判断：

```text
ReactAgent:
  需要小幅适配。
  先兼容 add_message() / get_history_text()。
  后续 Runtime 接入后，由 Runtime 创建 session 并注入绑定 session 的 ShortTermMemory。

ReActExecutor:
  已支持 history 参数、event_callback、execute_stream。
  Memory 可以利用这些接口，尽量少改执行器。

Models:
  已有 compress_context()。
  Memory 只调用，不重复造模型压缩能力。

Analyzer / Planner:
  MVP 不直接依赖 Memory。
  后续如果要让 Analyzer / Planner 理解“继续上文”“刚才那个文件”等指代，可增加可选 context_text 参数。
  在实现最小可跑版本时，优先把上下文稳定传给 ReActExecutor。

Tools:
  不直接对接 Memory。
  工具结果经 ReActExecutor 转成 Observation / ExecutionEvent 后，再由 Memory 保存用户可见摘要。
```

## 8. 最小闭环

最小可跑链路：

```text
Runtime / CLI / API
  -> SessionManager.get_or_create_session(session_id)
  -> append user Message
  -> create AgentRun
  -> ContextBuilder.build()
  -> ReactAgent / ReActExecutor
      -> Analyzer
      -> Planner
      -> ReActExecutor(history=context_text)
      -> event_callback 保存 visible events
  -> append assistant Message
  -> complete / fail AgentRun
  -> maybe_auto_summarize()
  -> 返回结果
```

## 9. 验收标准

Memory MVP 完成后必须满足：

```text
1. 创建 session 后可以写入消息。
2. 同 session 多轮对话可以读取前文。
3. 不同 session 历史隔离。
4. 重启 SessionManager 后仍能恢复 SessionState。
5. SQLite 中保存用户原文、Agent 最终回答和用户可见执行事件。
6. timeline_seq 能恢复原始展示顺序。
7. ContextBuilder 能输出 summary + 最近 10 条消息。
8. 超过阈值后能触发自动摘要。
9. 摘要失败时不阻塞对话。
10. visible_to_user=False 的执行事件不进入用户会话回放。
11. 敏感字段不会写入普通消息、事件或日志。
12. Memory 单元测试通过后，再接 Runtime / CLI / API。



1.消息写入责任重复：建议成立，但要明确成“双模式”。
当前 ReactAgent 确实会自己写入 user 和 assistant 消息。正式 Runtime 如果也写，就会重复。可采用：
- 旧兼容模式：直接调用 ReactAgent.run() 时，ReactAgent 保持当前自行写入。
- 正式 Runtime 模式：Runtime / SessionManager 是唯一写入者；ReactAgent 以 manage_memory=False 或类似执行模式运行，只接收已构建的 history/context_text，不再调用 add_message()。
这比“受控避免重复”更明确。当前重复风险可见于 [react_agent.py (line 99)](H:/project/agentProject/src/agent/orchestrator/react_agent.py:99)。

2.ShortTermMemory.clear()：不建议默认新建 session。
clear() 静默换到新 session 会让 Runtime 仍持有旧 session_id，行为很容易混乱。建议：
- 新会话：SessionManager.create_session() / Runtime 显式切换 session_id
- 删除会话：SessionManager.delete_session(session_id)
- ShortTermMemory.clear()：仅保留为旧兼容接口，清理临时缓存或标记为废弃；不删除 SQLite 历史，也不偷偷创建新 session。
目前项目里没有看到正式链路依赖 clear()，所以可以安全地把它降为兼容边界。

3.事件保存主路径：建议成立。
正式路径应以 event_callback 为主，事件产生时立即保存 visible_to_user=True 的事件；result.events 仅用于非流式兜底或补偿。用 event_id 的唯一约束保证回调和兜底扫描不会重复写入。  
需要注意一个现实情况：当前 ReActExecutor.execute() 已支持回调，但 ReactAgent 还没有把回调向下透传；并且当前 execute_stream() 是先执行、收集事件、再依次 yield，不是真正逐事件实时流式。这个属于 Step 10-11 必须处理的小幅跨层适配。见 [react_executor.py (line 279)](H:/project/agentProject/src/agent/react_executor/react_executor.py:279) 和 [react_agent.py (line 116)](H:/project/agentProject/src/agent/orchestrator/react_agent.py:116)。

4.摘要消息计数：建议成立。
上下文窗口和摘要阈值只统计可进入对话上下文的 user / assistant 消息；system / tool 和 ExecutionEvent 不参与计数。建议再补一条：只统计稳定、可用的消息，例如 completed 状态的消息，避免未完成流式片段干扰摘要判断。

5.旧 LongTermMemory / RAG 原型：建议成立。
它不是会话 Memory，而是旧的文档检索原型：当前使用 pickle 保存 documents，RAGSystem 也依赖它。暂时不要删除。  
但新的 Memory MVP 和新 Runtime 不应再把它当作会话持久化依赖。当前 main.py 会装配它，但 ReactAgent 实际只是保存引用，现有主流程没有使用 RAG。可以在 Runtime 接入时逐步让 long_term_memory、rag_system 变为可选依赖，保留旧入口直到正式 RAG 层开发。见 [main.py (line 10)](H:/project/agentProject/main.py:10)、[long_term_memory.py (line 10)](H:/project/agentProject/src/memory/long_term_memory.py:10)、[rag_system.py (line 8)](H:/project/agentProject/src/rag/rag_system.py:8)。
```

