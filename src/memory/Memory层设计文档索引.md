# Memory 层设计文档索引

本文档是 Memory / Context MVP 后续问答、设计和开发的阅读入口。新的开发对话应先读本文档，再按推荐顺序读取对应专题文档。

## 1. 文档列表

```text
Memory设计问题回答(1).txt
  第一轮问答记录，保存用户对 Memory 层边界、SQLite、SessionState、上下文窗口、
  自动摘要、事件持久化和跨层关系的确认。

Memory层设计决策汇总.md
  总入口，记录已经确定的核心设计决策、职责边界和最小验收标准。

Memory层架构与跨层交互设计.md
  说明 Memory 与 Runtime、ReactAgent、Analyzer、Planner、ReActExecutor、
  Models、Tools 的关系和适配原则。

Memory层数据模型与SQLite设计.md
  定义 Message、SessionState、AgentRun、ExecutionEventRecord、TimelineItem、
  SessionSummary、SQLite schema、枚举、事务和敏感数据边界。

Memory层上下文与自动摘要设计.md
  定义 ContextBuilder、summary + recent_messages + current_input、
  最近消息窗口、自动摘要触发、失败降级和摘要内容边界。

Memory层执行事件与会话回放设计.md
  定义如何消费 ReActExecutor 的 ExecutionEvent，如何区分用户可见历史和内部日志，
  以及如何按 timeline_seq 恢复会话回放。

Memory层开发步骤与验收标准.md
  正式开发执行文档，拆分 Step 0-14，写明每步目标、交付物、跨层影响和验收标准。

Memory层跨层联调开发计划.md
  专门记录 Memory 与 ReactAgent、ReActExecutor、Models、Tools、Runtime / CLI / API
  的联调方式和问题定位顺序。

Memory层开发步骤与进度.md
  跨 session 进度记录文档。每完成一个开发阶段后必须更新。
```

## 2. 推荐阅读顺序

后续如果是问答或设计：

```text
Memory设计问题回答(1).txt
  -> Memory层设计决策汇总.md
  -> Memory层架构与跨层交互设计.md
  -> 需要讨论的专题文档
```

后续如果是代码开发：

```text
Memory层开发步骤与进度.md
  -> Memory层开发步骤与验收标准.md
  -> 当前 Step 对应的专题设计文档
  -> Memory层跨层联调开发计划.md
```

后续如果是跨层接入或整体测试：

```text
Memory层架构与跨层交互设计.md
  -> Memory层跨层联调开发计划.md
  -> Memory层执行事件与会话回放设计.md
  -> Memory层上下文与自动摘要设计.md
  -> Memory层开发步骤与进度.md
```

## 3. 当前开发状态

```text
Memory / Context MVP 设计已完成。
正式代码实现尚未开始。
下一步应从 Memory层开发步骤与验收标准.md 的 Step 0 开始。
```

## 4. 关键设计结论速记

```text
Memory 是会话状态、历史持久化、上下文构建和会话回放层。
Memory 不是 Analyzer、Planner 或 ReActExecutor 的替代品。
SQLite 是当前唯一正式会话持久化源。
完整历史保存到 SQLite；模型上下文只传 summary + 最近 10 条消息 + 当前输入。
summary 超过阈值后自动更新，复用 Models 层 compress_context。
ReActExecutor 事件只保存 visible_to_user=True 的用户可见事件。
Runtime / CLI / API 负责装配和生命周期，不能直接写 SQL。
ReactAgent 优先通过 ShortTermMemory 兼容接口接入。
Analyzer / Planner MVP 阶段不直接依赖 Memory。
Tools 不直接依赖 Memory。
```

## 5. 开发更新规则

每完成一个 Step，必须更新：

```text
Memory层开发步骤与进度.md
```

如果实现与设计发生差异，视影响范围更新：

```text
Memory层设计决策汇总.md
Memory层架构与跨层交互设计.md
Memory层数据模型与SQLite设计.md
Memory层上下文与自动摘要设计.md
Memory层执行事件与会话回放设计.md
Memory层跨层联调开发计划.md
```

