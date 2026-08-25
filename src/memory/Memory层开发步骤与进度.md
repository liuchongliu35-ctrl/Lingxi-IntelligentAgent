# Memory 层开发步骤与进度

> 覆盖步骤：Step 0-14
> 当前状态：Step 14 已完成，Memory V1 验收通过
> 上位设计：`Memory层设计决策汇总.md`、`Memory层架构与跨层交互设计.md`、`Memory层数据模型与SQLite设计.md`、`Memory层上下文与自动摘要设计.md`、`Memory层执行事件与会话回放设计.md`
> 执行标准：`Memory层开发步骤与验收标准.md`
> 跨层联调：`Memory层跨层联调开发计划.md`

本文件只记录开发推进节奏与当前进度，不重复展开设计细节。每完成一个可验收步骤，都要回写本文件，并同步标记对应设计文档是否需要修订。

---

## 阶段一：会话与持久化底座

这一阶段先把 Memory 变成“能存、能载入、能隔离”的基础层，再向上接上下文。

### Step 0：开发前检查与旧原型隔离

**状态：已完成**

**目标**

```text
确认旧 short_term_memory.py / long_term_memory.py 只是占位原型。
确认 ReactAgent、ReActExecutor、Models 的真实接口。
确认新 Memory 方案的边界和兼容入口。
```

**对照设计**

```text
Memory层设计决策汇总.md
Memory层架构与跨层交互设计.md
```

**当前进度**

```text
已完成开发前检查与旧原型隔离，记录见：
`Memory层Step0开发前接口差异记录.md`

完成内容：
- 确认 `short_term_memory.py` 只是进程内 list 占位，后续仅保留兼容方法名。
- 确认 `long_term_memory.py` 是旧文档检索原型，使用 pickle，不作为 SQLite 会话 Memory。
- 确认 ReactAgent 当前会自行写入 user / assistant 消息，后续正式 Runtime 模式必须使用双模式避免重复写入。
- 确认 ReActExecutor 已具备 `history`、`event_callback`、`ExecutionEvent.visible_to_user` 和 EventStream 能力。
- 确认 Models 层已有 `ModelManager.compress_context()` 与 `ContextCompressionResult`。
- 确认 `src/agent` 存在整理后的 import 兼容问题，Step 10-11 前需处理。
- 确认 Runtime / CLI / API 正式入口尚未开发，Memory 阶段只做预留接口。
```

---

### Step 1：数据模型、枚举、配置与 ID

**状态：已完成**

**目标**

```text
建立 Message、SessionState、AgentRun、ExecutionEventRecord、SessionSummary、
ContextBuildResult 等稳定数据契约。
```

**对照设计**

```text
Memory层数据模型与SQLite设计.md
```

**当前进度**

```text
已完成 Step 1 正式编码与专项验收。

已新增：
- `src/memory/models.py`
  - 统一 Role、ContentFormat、DisplayType、MessageStatus、
    AgentRunStatus、ExecutionEventStatus、SessionStatus、
    TimelineItemKind、SummarySource。
  - 建立 `Message`、`SessionState`、`SessionInfo`、`AgentRun`、
    `ExecutionEventRecord`、`TimelineItem`、`SessionSummary`、
    `ContextBuildResult`。
  - 所有数据对象提供 `to_dict()`，支持枚举、嵌套 dataclass 和 metadata 的稳定序列化。
- `src/memory/config.py`
  - 建立 `MemoryConfig`。
  - 默认路径为 `storage/agent_memory.db` 和 `logs/memory.log`。
  - 默认窗口与摘要阈值为 `10 / 14 / 6`，摘要目标长度为 `2000`。
  - 增加消息、事件展示内容和事件 payload 的长度上限配置。
  - 配置构造不创建目录，不依赖尚未开发的 Runtime / CLI / API。
- `src/memory/ids.py`
  - 建立 session、message、run、event、summary ID 生成器。
  - 使用 UTC 时间和 UUID 随机后缀。
  - 对 `session_id` 做空值、路径穿越、路径分隔符、绝对路径和字符集校验。
- `src/memory/__init__.py`
  - 导出 Step 1 的公共类型、配置、ID 工具。

新增专项测试：
- `tests/test_memory_models.py`
- `tests/test_memory_config.py`
- `tests/test_memory_ids.py`

验收结果：
- `python -m pytest -q tests/test_memory_models.py tests/test_memory_config.py tests/test_memory_ids.py`
  - 20 passed
- `python -m compileall -q src/memory`
  - 通过

设计边界记录：
- 本步骤未实现 SQLite、Repository、SessionManager、ContextBuilder、
  自动摘要、事件映射和 Runtime / CLI / API。
- 旧 `short_term_memory.py` / `long_term_memory.py` 未修改，仍按 Step 0
  结论保留兼容入口和旧 RAG 原型隔离。
```

---

### Step 2：SQLite Schema、迁移与 Repository

**状态：已完成**

**目标**

```text
建立 SQLite 唯一持久化源，支持 session、message、run、event、summary 的持久化。
```

**对照设计**

```text
Memory层数据模型与SQLite设计.md
```

**当前进度**

```text
已完成 SQLite schema、迁移和 Repository 的基础实现。

已新增：
- `src/memory/storage.py`
  - 建立 `SQLiteSessionRepository`。
  - 启动时自动创建数据库目录、日志目录、表结构、索引和 schema 版本记录。
  - 覆盖 `sessions`、`messages`、`agent_runs`、`execution_events`、
    `session_summaries`、`schema_migrations`。
  - 提供事务、连接、时间戳和 `timeline_seq` 原子分配。
  - 支持会话创建、加载、列表、删除、消息写入、run 写入与更新、
    execution event 写入、summary 写入、timeline 读取、recent messages 读取、
    interrupted run 标记。
  - `visible_to_user=False` 的执行事件不进入普通回放，仅在入口跳过并记录日志。
  - 写入按 `message_id` / `run_id` / `event_id` / `summary_id` 幂等处理。
- `src/memory/__init__.py`
  - 导出 `SQLiteSessionRepository`、`SessionRow`、`SCHEMA_VERSION`。

新增专项测试：
- `tests/test_memory_storage.py`

验收结果：
- `python -m pytest -q tests/test_memory_storage.py tests/test_memory_models.py tests/test_memory_config.py tests/test_memory_ids.py`
  - 25 passed
- `python -m compileall -q src/memory`
  - 通过

设计边界记录：
- 本步骤只实现 SQLite schema / migration / repository，不引入 SessionManager、
  ContextBuilder、自动摘要决策和 Runtime / CLI / API。
- 仍保留旧 `short_term_memory.py` / `long_term_memory.py` 兼容边界，不在本步骤修改。
```

---

### Step 3：SessionManager 会话生命周期

**状态：已完成**

**目标**

```text
为 Runtime / CLI / API 提供统一 session 业务接口，隔离 SQL 细节。
```

**对照设计**

```text
Memory层架构与跨层交互设计.md
Memory层数据模型与SQLite设计.md
```

**当前进度**

```text
已完成 SessionManager 会话生命周期实现与专项验收。

已新增：
- `src/memory/session_manager.py`
  - 提供 session 创建、加载、获取或创建、删除、列表接口。
  - 提供消息、AgentRun、用户可见 ExecutionEvent、会话 timeline 和 summary
    的业务层接口，SessionManager 不直接写 SQL。
  - 校验消息、run、event 的 session 归属，完成 run 时校验最终消息属于当前
    run 且角色为 assistant。
  - `update_summary()` 生成新的 summary 版本，维护连续的覆盖范围，默认
    `source=manual`，不覆盖旧 summary。
  - `maybe_auto_summarize()` 保留阈值判断和后续 Step 6 hook，不提前调用
    Models 层 `compress_context`。
  - `get_short_term_memory()` 明确保留为 Step 4 兼容层接口，当前不接回旧的
    全局 list 原型。
- `src/memory/storage.py`
  - 增加 `create_user_turn()` 原子业务底层方法，在一个 SQLite 事务中完成
    session 创建、user Message 写入、running AgentRun 创建和
    `sessions.last_run_id` 更新。
- `src/memory/__init__.py`
  - 导出 `SessionManager`。

新增专项测试：
- `tests/test_memory_session_manager.py`

验收结果：
- `python -m pytest -q tests/test_memory_session_manager.py tests/test_memory_storage.py tests/test_memory_models.py tests/test_memory_config.py tests/test_memory_ids.py`
  - 31 passed
- `python -m compileall -q src/memory`
  - 通过

设计边界记录：
- Runtime / CLI / API 入口尚未开发，本步骤只提供稳定预留接口，不接入入口层。
- 未修改 Analyzer、Planner、ReActExecutor、Tools 和旧 long-term RAG 原型。
- 未实现 ShortTermMemory、ContextBuilder、正式自动摘要、ReActExecutor
  事件 mapper 和跨层联调，这些分别留给后续 Step 4-12。
- 未改变旧 `short_term_memory.py` 的列表实现；正式 Memory 仍以 SQLite 为唯一
  持久化源。
```

---

## 阶段二：上下文、摘要与回放

这一阶段让 Agent 能“记住前文”，并能把用户看见的过程保存下来。

### Step 4：ShortTermMemory 兼容层

**状态：已完成**

**目标**

```text
兼容 ReactAgent 现有 add_message / get_history_text 调用方式。
```

**对照设计**

```text
Memory层架构与跨层交互设计.md
Memory层上下文与自动摘要设计.md
```

**当前进度**

```text
已完成 SQLite session 绑定的 ShortTermMemory 兼容层与专项验收。

已修改：
- `src/memory/short_term_memory.py`
  - 将旧的进程内 `history` list 原型替换为单一 session 的 SQLite-backed
    兼容 facade。
  - 保留 `add_message()`、`get_history()`、`get_history_text()`、`clear()`、
    `get_last_message()`、`get_history_length()` 调用习惯。
  - `add_message()` 委托 `SessionManager.append_message()`；完整历史仍保留在
    SQLite，读取窗口仅返回当前 session 最近的 user / assistant 消息。
  - `clear()` 保留为无破坏性的兼容接口：不删除 SQLite 历史，也不静默创建或
    切换 session。
  - 支持注入后续 Step 5 的 ContextBuilder；注入后 `get_history_text()` 将直接
    返回 `ContextBuilder.build(session_id).context_text`。在 ContextBuilder
    尚未实现前，使用当前 session 的可读 recent-message 文本作为过渡。
- `src/memory/session_manager.py`
  - `get_short_term_memory(session_id)` 已接成绑定当前 SessionManager 和
    session_id 的 ShortTermMemory 工厂接口。
- `src/memory/storage.py`
  - `load_recent_messages()` 增加可选 role 过滤，保证 ShortTermMemory 的消息
    窗口按 user / assistant 计数，不受 system / tool 消息干扰。
- `src/memory/__init__.py`
  - 导出 `ShortTermMemory`。

新增专项测试：
- `tests/test_memory_short_term_memory.py`

验收结果：
- `python -m pytest -q tests/test_memory_short_term_memory.py tests/test_memory_session_manager.py tests/test_memory_storage.py tests/test_memory_models.py tests/test_memory_config.py tests/test_memory_ids.py`
  - 37 passed
- `python -m compileall -q src/memory`
  - 通过

设计边界记录：
- 未修改 ReactAgent、Analyzer、Planner、ReActExecutor、Tools、Runtime / CLI / API。
- `get_history_text()` 的正式 summary + recent_messages + current_input 组装仍属于
  Step 5 ContextBuilder；本步骤只完成兼容层注入点及没有 ContextBuilder 时的
  SQLite recent-message 过渡读取。
- 额外执行 `tests/test_react_agent_with_react_executor.py` 时，在测试收集阶段因
  既有 `src.agent.executor.Executor` import 兼容问题失败；该问题与本步骤的
  ShortTermMemory 改动无关，按 Step 0 记录留待 Agent 跨层联调前处理。
```

---

### Step 5：ContextBuilder 上下文构建

**状态：已完成**

**目标**

```text
统一拼接 summary + recent_messages + current_input。
```

**对照设计**

```text
Memory层上下文与自动摘要设计.md
```

**当前进度**

```text
已完成 ContextBuilder 上下文构建与专项验收。

已新增：
- `src/memory/context_builder.py`
  - 提供 `ContextBuilder` 和稳定的 `ContextBuildResult` 输出路径。
  - 按设计拼接 `[Session Summary]`、`[Recent Messages]`、
    `[Current User Input]` 三段式上下文。
  - 默认仅纳入当前 session 的 user / assistant completed 消息，不把
    execution_events 直接送入模型上下文。
  - `current_user_input` 已在 recent_messages 中时不重复追加。
  - 通过 `included_message_ids`、`included_event_ids`、`truncated`、
    `current_user_input_included`、`token_estimate`、`char_count`、
    `metadata` 暴露结构化信息，供后续 API / 调试 / 测试使用。
  - 目前不触发模型摘要，不调用 Models 层 `compress_context()`。
- `src/memory/short_term_memory.py`
  - `get_history_text()` 现在默认委托 `ContextBuilder.build(session_id)`。
  - 仍保留旧兼容接口，但正式文本由 ContextBuilder 生成。
- `src/memory/storage.py`
  - 增加 `count_messages()`，支持 ContextBuilder 的窗口判断。
  - `load_recent_messages()` 支持按 role / status 过滤，确保只读取稳定
    的 user / assistant completed 消息。
- `src/memory/__init__.py`
  - 导出 `ContextBuilder`。

新增专项测试：
- `tests/test_memory_context_builder.py`

验收结果：
- `python -m pytest -q tests/test_memory_context_builder.py tests/test_memory_short_term_memory.py tests/test_memory_session_manager.py tests/test_memory_storage.py tests/test_memory_models.py tests/test_memory_config.py tests/test_memory_ids.py`
  - 43 passed
- `python -m compileall -q src/memory`
  - 通过

设计边界记录：
- 未接入 Models 层自动摘要调用，Step 6 再实现压缩流程。
- 未修改 ReactAgent、Analyzer、Planner、ReActExecutor、Tools、Runtime / CLI / API。
- execution_events 默认不进入上下文，回放和审计仍由后续 Step 7 负责。
```

---

### Step 6：ConversationSummarizer 自动摘要

**状态：已完成**

**目标**

```text
消息超过阈值后自动压缩早期历史，保留最近窗口原文。
```

**对照设计**

```text
Memory层上下文与自动摘要设计.md
Memory层数据模型与SQLite设计.md
```

**当前进度**

```text
已完成 ConversationSummarizer 自动摘要实现与专项验收。
```

---

### Step 7：ExecutionEvent 映射与会话回放

**状态：已完成**

**目标**

```text
保存用户可见执行事件，区分 timeline 和内部日志。
```

**对照设计**

```text
Memory层执行事件与会话回放设计.md
Memory层数据模型与SQLite设计.md
```

**当前进度**

```text
已完成 ExecutionEvent 映射与会话回放实现。

已新增：
- `src/memory/event_mapper.py`
  - 提供 ReActExecutor `ExecutionEvent` 到 Memory `ExecutionEventRecord` 的映射。
  - 优先尊重 `visible_to_user`；隐藏事件返回 `None`，不进入普通用户回放。
  - 实现 `event_type -> display_type` 与 `event_type -> status` 映射。
  - 对 `display_content` 与 `sanitized_payload` 做脱敏和长度限制。
  - 将 ReActExecutor 短格式 `event_id` 稳定转换为 Memory 正式 ID，同时在 metadata 中保留 `source_event_id`。
- `src/memory/session_manager.py`
  - `append_execution_event()` 现在可接收 Memory 记录、dict 记录或 ReActExecutor 原始事件。
  - 新增 `append_execution_events()` 批量保存辅助接口，供后续 Runtime / ReactAgent 非流式兜底扫描使用。
  - 内部事件只写轻量 memory log，不写入 `execution_events`。
- `src/memory/storage.py`
  - `load_session_timeline()` 只回放用户可见消息和用户可见 execution_events。
  - execution event 的 `event_type` 与 `sanitized_payload` 放入 TimelineItem.metadata，便于 CLI/API 回放。
- `src/memory/__init__.py`
  - 导出 Step 7 mapper 公共接口。

新增专项测试：
- `tests/test_memory_event_mapper.py`

验收结果：
- `python -m pytest -q tests/test_memory_event_mapper.py tests/test_memory_storage.py tests/test_memory_session_manager.py tests/test_memory_summarizer.py tests/test_memory_context_builder.py tests/test_memory_short_term_memory.py tests/test_memory_config.py tests/test_memory_ids.py tests/test_memory_models.py`
  - 54 passed
- `python -m compileall -q src/memory`
  - 通过

边界记录：
- 本步只完成 Memory 侧事件映射、持久化和回放能力，不改 Runtime / CLI / API。
- ReactAgent 向 ReActExecutor 透传 `event_callback` 仍留到 Step 10-11 跨层适配。
- ReActExecutor 的 `execute_stream()` 当前仍是先执行收集再 yield，保持既有设计记录，不在 Step 7 改执行器主循环。
```

---

### Step 8：日志、异常降级与恢复

**状态：已完成**

**目标**

```text
补齐轻量日志、持久化失败降级和重启恢复策略。
```

**对照设计**

```text
Memory层数据模型与SQLite设计.md
Memory层执行事件与会话回放设计.md
```

**当前进度**

```text
已完成 Memory 日志、异常降级与恢复实现。

已新增：
- `src/memory/memory_logging.py`
  - 统一生成 JSONL 日志记录。
  - 对日志 payload 中的 API Key、token、Cookie、密码、authorization 等敏感字段和文本做脱敏。
  - 对长文本和长列表做截断，避免日志保存完整敏感正文。
- `tests/test_memory_logging_recovery.py`
  - 覆盖日志脱敏、关键路径日志、run interrupted 恢复、损坏数据库可读错误和原库不覆盖。

已修改：
- `src/memory/storage.py`
  - `record_memory_event()` 改为统一 JSONL 写入。
  - 关键路径记录 `session_created`、`session_loaded`、`message_appended`、`run_created`、
    `event_persisted`、`event_skipped_internal`、`summary_completed`、`persistence_warning`。
  - 数据库无法打开或损坏时记录 `persistence_warning`，返回可读 RuntimeError，并不覆盖原数据库。
  - `mark_interrupted_runs()` 记录恢复日志。
- `src/memory/context_builder.py`
  - 每次上下文构建记录 `context_built`，包括消息计数、是否截断、是否含 summary 和上下文长度。
- `src/memory/session_manager.py`
  - 新增 `recover_interrupted_runs()`，用于 Runtime 启动/恢复时显式标记未完成 run。

验收结果：
- `python -m pytest -q tests/test_memory_logging_recovery.py tests/test_memory_event_mapper.py tests/test_memory_storage.py tests/test_memory_session_manager.py tests/test_memory_summarizer.py tests/test_memory_context_builder.py tests/test_memory_short_term_memory.py tests/test_memory_config.py tests/test_memory_ids.py tests/test_memory_models.py`
  - 58 passed
- `python -m compileall -q src/memory`
  - 通过

边界记录：
- 本步完成 Memory 侧诊断、损坏数据库可读错误和恢复标记能力。
- Runtime / CLI / API 仍未开发；向用户返回“本轮未持久化”等结构化降级标记留到入口层接入时处理。
- 不实现临时内存会话自动降级，避免隐式造成“看似持久化、实际未保存”的行为。
```

---

### Step 9：Memory 纯模块测试

**状态：已完成**

**目标**

```text
先证明 Memory 自身的存储、恢复、上下文和回放闭环正确。
```

**对照设计**

```text
Memory层开发步骤与验收标准.md
Memory层数据模型与SQLite设计.md
Memory层上下文与自动摘要设计.md
Memory层执行事件与会话回放设计.md
```

**当前进度**

```text
已完成 Memory 纯模块闭环验收。

已新增：
- `tests/test_memory_pure_module_acceptance.py`
  - 覆盖自动生成 session_id 和指定 session_id。
  - 覆盖 session 隔离、消息写入、SQLite 重启恢复。
  - 覆盖最近消息窗口、summary + recent_messages + current_input 拼接。
  - 覆盖 current_user_input 不重复进入上下文。
  - 覆盖 messages 与 execution_events 按 `timeline_seq` 合并回放。
  - 覆盖 `visible_to_user=False` 事件过滤和 `event_id` 幂等。
  - 覆盖事件展示文本与 payload 脱敏。
  - 覆盖自动摘要成功、摘要失败保留旧 summary。
  - 覆盖未完成 run 恢复为 `interrupted`。
  - 覆盖数据库损坏时错误可读且原库不被覆盖。

验收结果：
- `python -m pytest -q tests/test_memory_pure_module_acceptance.py tests/test_memory_logging_recovery.py tests/test_memory_event_mapper.py tests/test_memory_storage.py tests/test_memory_session_manager.py tests/test_memory_summarizer.py tests/test_memory_context_builder.py tests/test_memory_short_term_memory.py tests/test_memory_config.py tests/test_memory_ids.py tests/test_memory_models.py`
  - 62 passed
- `python -m compileall -q src/memory`
  - 通过

正式库污染检查：
- 新增验收测试全部使用 pytest `tmp_path` 临时 SQLite。
- `storage/agent_memory.db` 本次测试后未更新，未污染正式默认数据库。
```

---

## 阶段三：跨层联调与运行入口

这一阶段把 Memory 接到现有 Agent 链路，完成可运行最小架构。

### Step 10：ReactAgent 适配

**状态：已完成**

**目标**

```text
让 ReactAgent 使用新的 session-aware Memory 兼容层。
```

**对照设计**

```text
Memory层架构与跨层交互设计.md
Memory层跨层联调开发计划.md
```

**当前进度**

```text
已完成 ReactAgent 与 Memory / Context MVP 的最小跨层适配。

已修改：
- `src/agent/orchestrator/react_agent.py`
  - 保留旧兼容模式：默认 `manage_memory=True`，ReactAgent 继续通过
    `short_term_memory.add_message("user", ...)`、`get_history_text()`、
    `add_message("assistant", ...)` 完成旧链路写入。
  - 新增 Runtime 预留模式：构造或单次调用可设置 `manage_memory=False`，
    并传入 `context_text` / `history`，ReactAgent 不再写 user / assistant，
    避免后续 Runtime / SessionManager 重复写入。
  - `run()`、`run_with_result()`、`run_feedback()`、`run_stream()` 支持可选
    `context_text`、`history`、`event_callback`、`event_callback_visible_only`。
  - 非流式执行优先把 `event_callback` 透传给支持该参数的执行器；不支持该参数的
    legacy / injected executor 保持原调用方式。
  - Analyzer / Planner 仍只接收当前 `user_input`，未引入 Memory 依赖。
  - ReactAgent 不直接操作 SQL、summary、event_mapper 或 SQLite repository。
- `src/agent/*` 兼容导出
  - 补齐整理后的 Agent 子包到旧平铺 import 路径的兼容 wrapper / package export。
  - 覆盖 `src.agent.executor`、`src.agent.planner`、`src.agent.react_executor`、
    `src.agent.react_agent`、`src.agent.output_feedback` 以及 ReActExecutor 相关
    helper 模块，解决 Step 0 记录的 import 兼容风险。

新增专项测试：
- `tests/test_memory_react_agent_adaptation.py`
  - 同一 session 第二轮可读取第一轮 user / assistant 历史。
  - 切换 session 后上下文隔离。
  - `manage_memory=False + context_text` 不写入 ShortTermMemory。
  - 单次调用 `manage_memory=False` 可覆盖旧默认模式。
  - `event_callback` 可透传给执行器，并遵守 `event_callback_visible_only`。

验收结果：
- `python -m pytest -q tests/test_react_agent_with_react_executor.py tests/test_memory_react_agent_adaptation.py`
  - 16 passed
- `python -m pytest -q tests/test_memory_pure_module_acceptance.py`
  - 4 passed
- `python -m pytest -q tests/test_memory_config.py tests/test_memory_context_builder.py tests/test_memory_event_mapper.py tests/test_memory_ids.py tests/test_memory_logging_recovery.py tests/test_memory_models.py tests/test_memory_pure_module_acceptance.py tests/test_memory_session_manager.py tests/test_memory_short_term_memory.py tests/test_memory_storage.py tests/test_memory_summarizer.py tests/test_memory_react_agent_adaptation.py`
  - 68 passed
- `python -m compileall -q src/agent src/memory`
  - 通过

跨层变更记录：
- 变更原因：让 Memory 已完成的 session-aware ShortTermMemory / ContextBuilder 能接入
  ReactAgent，同时为后续 Runtime 统一写入消息和保存事件预留受控入口。
- 原接口：`run(user_input)`、`run_with_result(user_input)` 内部固定写入 user / assistant，
  并只从 `short_term_memory.get_history_text()` 取上下文。
- 新接口：旧调用不变；新增可选 `manage_memory`、`context_text`、`history`、
  `event_callback`、`event_callback_visible_only`。
- 影响调用方：旧 ReactAgent 调用方保持兼容；后续 Runtime 可用
  `manage_memory=False` 接管消息写入和 run 生命周期。
- 是否保持向后兼容：是。

边界记录：
- Runtime / CLI / API 入口层仍未开发，本步只做 ReactAgent 预留接口和兼容适配。
- ReActExecutor 主循环未改；事件实时持久化与 result.events 兜底扫描留到 Step 11 继续联调。
- Analyzer / Planner / Tools 未新增 Memory 直接依赖。
```

---

### Step 11：ReActExecutor 事件联调

**状态：已完成**

**目标**

```text
把执行器事件实时或准实时写入 Memory，并保持可回放。
```

**对照设计**

```text
Memory层执行事件与会话回放设计.md
Memory层跨层联调开发计划.md
```

**当前进度**

```text
已完成 ReActExecutor 事件联调与 Memory 持久化闭环。

已修改：
- `src/agent/orchestrator/react_agent.py`
  - 保留 Step 10 的双模式接入，并补上 ReActExecutor 事件联调通路。
  - 当调用方提供 `session_id / run_id` 时，ReactAgent 会构造 Memory 事件回调，
    将 ReActExecutor 产生的可见事件即时写入 SessionManager / SQLite。
  - 若执行器不支持回调，ReactAgent 会在执行结束后回扫 `result.events` 作为兜底。
  - 通过 `event_id` 幂等和 Memory repository 唯一约束，避免回调写入和结果兜底重复入库。
  - `visible_to_user=False` 的事件不会进入普通会话回放。
  - 外部 `event_callback` 仍可按 visible-only 规则透传给上层调用方。
- `src/memory/session_manager.py`
  - 继续复用 `append_execution_event()` / `append_execution_events()`，承接 ReactAgent
    传来的原始 `ExecutionEvent`。
- `src/memory/storage.py`
  - 保持 `execution_events` 的 `event_id` 幂等插入、`timeline_seq` 顺序分配和
    `load_session_timeline()` 合并回放。

新增专项测试：
- `tests/test_memory_react_agent_adaptation.py`
  - 验证 visible 事件可通过 callback 进入 Memory。
  - 验证执行器不支持 callback 时，`result.events` 仍可作为兜底写入。
  - 验证内部事件不会进入普通用户 timeline。

验收结果：
- `python -m pytest -q tests/test_memory_react_agent_adaptation.py tests/test_react_agent_with_react_executor.py`
  - 18 passed
- `python -m pytest -q tests/test_react_executor_events.py tests/test_memory_event_mapper.py tests/test_memory_pure_module_acceptance.py`
  - 32 passed
- `python -m pytest -q tests/test_memory_config.py tests/test_memory_context_builder.py tests/test_memory_event_mapper.py tests/test_memory_ids.py tests/test_memory_logging_recovery.py tests/test_memory_models.py tests/test_memory_pure_module_acceptance.py tests/test_memory_session_manager.py tests/test_memory_short_term_memory.py tests/test_memory_storage.py tests/test_memory_summarizer.py tests/test_memory_react_agent_adaptation.py`
  - 70 passed
- `python -m compileall -q src/agent src/memory`
  - 通过

跨层变更记录：
- 变更原因：让 ReActExecutor 已有的事件体系真正进入 Memory 会话回放，而不重写
  执行器主循环。
- 原接口：ReactAgent 仅把 `result.events` 当返回值使用，事件持久化留在外部入口。
- 新接口：当存在 session/run 信息时，ReactAgent 可直接将 visible 事件写入 Memory，
  并对无 callback 的执行器执行结果做兜底持久化。
- 影响调用方：旧 ReactAgent 调用仍兼容；后续 Runtime 只需接管 session/run 生成与
  最终事务编排。
- 是否保持向后兼容：是。

边界记录：
- 未改 ReActExecutor 核心事件发射逻辑，只消费其已有 `event_callback` / `events`。
- 未接入 Runtime / CLI / API，Step 13 再做入口编排。
- `message_delta` 仍按执行器现有协议保留，不做 token 级永久落库。
```

---

### Step 12：Models 压缩联调

**状态：已完成**

**目标**

```text
让 Memory 的摘要流程复用 Models 层 compress_context。
```

**对照设计**

```text
Memory层上下文与自动摘要设计.md
Memory层跨层联调开发计划.md
```

**当前进度**

```text
- `ConversationSummarizer` 已通过 `ModelManager.compress_context()` 复用 Models 层的正式压缩能力。
- 已补充真实 `ModelManager` 联调测试，覆盖分块压缩、合并摘要、模型元数据回写和规则降级。
- 未在 Memory 内直接调用 Provider，也未复制 Models 层的路由、结构化输出或降级逻辑。
```

---

### Step 13：Runtime / CLI / API 接入

**状态：已完成（Memory 侧预留接口）**

**目标**

```text
把 Memory 和现有 Agent 链路装配成真正可启动的最小运行系统。
```

**对照设计**

```text
Memory层架构与跨层交互设计.md
Memory层跨层联调开发计划.md
```

**当前进度**

```text
- Runtime / CLI / API 正式入口层尚未开发，本 Step 不实现入口层。
- Memory 侧已提供 `RuntimeMemoryAdapter`，预留 Runtime 一轮对话的稳定调用契约。
- 已覆盖 begin_turn、ReactAgent 参数生成、事件回调保存、complete_turn、fail_turn、timeline 和 health 查询。
```

---

### Step 14：最小可跑架构端到端验收

**状态：已完成（Memory V1 验收通过）**

**目标**

```text
验证 Agent 能正常对话、能继续旧 session、能完成基础任务。
```

**对照设计**

```text
Memory层开发步骤与验收标准.md
Memory层跨层联调开发计划.md
```

**当前进度**

```text
- 已完成以 Memory 为主的最小端到端闭环验收，不依赖完整 Agent、CLI 或 API 入口。
- 已验证 session 创建与隔离、多轮消息、SQLite 重启恢复、ContextBuilder、自动摘要、
  用户可见事件回放、隐藏事件过滤、幂等、脱敏、schema 保护、interrupted run 恢复、
  RuntimeMemoryAdapter 预留契约和持久化失败显式降级。
- Memory V1 核心设计已实现；Runtime / CLI / API 正式入口仍等待后续层开发。
```

---

## 进度更新规则

每完成一个 Step，必须同步更新：

```text
1. 本文件对应 Step 的状态。
2. 当前进度或完成记录。
3. 是否出现设计与实现偏差。
4. 是否需要回写设计文档。
```

建议记录格式：

```text
### Step X 完成记录（日期）

#### 修改文件
...

#### 实现结果
...

#### 测试命令与结果
...

#### 本 Step 边界与遗留问题
...
```

---

## 当前已完成

```text
- Memory 设计文档体系已拆分完成。
- Step 0-14 的开发顺序和验收边界已确定。
- SQLite、SessionManager、ContextBuilder、自动摘要、事件回放、Runtime 接入的职责边界已定。
- Step 0-14 已完成。
```

## 当前未完成

```text
- Memory V1 已完成；Runtime / CLI / API 正式入口层尚未开发，后续仍需进行入口层联调。
- Runtime / CLI / API 入口层尚未开发，当前仅保留 Memory 侧预留接口。
```

## 最近一次更新

```text
日期：2026-08-21
更新内容：
- 完成 Step 14 Memory V1 最小端到端验收。
- 新增完整 Memory V1 闭环测试，验证持久化、上下文、摘要、事件回放、恢复和安全边界。
- 修复 metadata/summary 脱敏、schema mismatch 原库保护和持久化失败显式降级。
```
### Step 14 完成记录（2026-08-21）

#### 修改文件
- `src/memory/storage.py`
- `src/memory/summarizer.py`
- `src/memory/event_mapper.py`
- `src/memory/memory_logging.py`
- `src/memory/runtime_adapter.py`
- `tests/test_memory_v1_end_to_end_acceptance.py`
- `src/memory/Memory层开发步骤与进度.md`

#### 设计对照与验收结果
- 对照 `Memory层设计决策汇总.md`
  - SQLite 是唯一正式会话持久化源。
  - session_id 作为会话边界，完整历史、AgentRun、可见 ExecutionEvent、SessionSummary 均可恢复。
  - 上下文使用 summary + recent_messages + current_input。
  - 用户可见事件进入回放，内部事件不进入普通 timeline。
- 对照 `Memory层架构与跨层交互设计.md`
  - Runtime 侧可通过 `RuntimeMemoryAdapter` 完成 begin/event/complete/fail/session/timeline/health 调用。
  - ReactAgent Runtime 模式使用 `manage_memory=False`，避免消息重复写入。
  - Analyzer、Planner、ReActExecutor、Models、Tools 没有被加入不必要的 Memory 直接依赖。
- 对照 `Memory层数据模型与SQLite设计.md`
  - 验证六张正式表、schema version、事务写入、timeline_seq、外键、唯一 ID 和重复事件幂等。
  - schema 版本不匹配时在 DDL 前拒绝打开，记录 warning，原数据库字节内容保持不变。
- 对照 `Memory层上下文与自动摘要设计.md`
  - 验证空上下文、最近消息窗口、current input 去重、system/tool/event 默认不进入模型上下文。
  - 验证超过阈值后经 `ModelManager.compress_context()` 生成新 summary 版本，失败保留旧 summary。
  - 摘要文本和 summary metadata 入库前完成脱敏与长度限制。
- 对照 `Memory层执行事件与会话回放设计.md`
  - 验证事件映射、display_type/status、用户可见过滤、timeline_seq 合并、event_id 幂等和 payload 脱敏。
  - 验证中断重启后可见事件仍可回放，隐藏 reasoning/raw output 不进入 timeline。
- 对照 `Memory层跨层联调开发计划.md`
  - 验证 Runtime 预留调用顺序和 ReactAgent 所需 `context_text/session_id/run_id/manage_memory=False` 参数契约。
  - 正式 CLI/API 路由和完整 Agent 运行仍不在本 Step 范围内。
- 对照 `Memory层开发步骤与验收标准.md` 和 `Memory设计问题回答(1).txt`
  - 覆盖 session 生命周期、消息和 run、摘要、事件、日志、恢复、敏感边界和异常降级约定。
  - 单条持久化失败时，`RuntimeMemoryAdapter` 返回临时内存结果并显式标记 `persistence_available=False` 与 warning，不伪装成已持久化。

#### 验证命令与结果
- `python -m pytest -q tests/test_memory_v1_end_to_end_acceptance.py`
  - `5 passed`
- `python -m pytest -q tests/test_memory_v1_end_to_end_acceptance.py tests/test_memory_config.py tests/test_memory_context_builder.py tests/test_memory_event_mapper.py tests/test_memory_ids.py tests/test_memory_logging_recovery.py tests/test_memory_models.py tests/test_memory_pure_module_acceptance.py tests/test_memory_runtime_adapter.py tests/test_memory_session_manager.py tests/test_memory_short_term_memory.py tests/test_memory_storage.py tests/test_memory_summarizer.py tests/test_memory_react_agent_adaptation.py tests/test_models_context_compression.py`
  - `88 passed`
- `python -m pytest -q tests/test_models_v1_acceptance.py`
  - `3 passed`
- `python -m compileall -q src/memory src/models src/agent`
  - 通过
- `python -c "from src.memory import RuntimeMemoryAdapter, MemoryHealthStatus, SessionManager, ContextBuilder; print('memory imports ok')"`
  - 公共导入通过

#### 修复内容
- `storage.py`
  - session/message/run/event/summary metadata 统一经过 Memory 脱敏和长度限制后写入 `metadata_json`。
  - schema mismatch 在初始化 DDL 前检查，避免修改不兼容原库。
  - 移除打开数据库时的 WAL 模式副作用，保证拒绝不兼容库时原库不被改写。
- `summarizer.py`
  - 模型生成的 summary 正文入库前经过敏感文本脱敏和目标长度限制。
- `event_mapper.py`、`memory_logging.py`
  - 收窄敏感值正则，保留正常句末标点，避免脱敏破坏用户可见文本。
- `runtime_adapter.py`
  - 增加显式持久化降级状态；SQLite/单次写入失败时仍可返回临时 assistant 结果，并标记本轮不会持久化。
- `tests/test_memory_v1_end_to_end_acceptance.py`
  - 新增 Memory V1 完整生命周期、schema mismatch、上下文边界、健康检查、恢复和持久化失败降级验收。

#### V1 结论与边界
- Memory / Context MVP 的 V1 核心设计已实现并通过本地验收。
- SQLite 是唯一正式持久化源；临时内存降级只用于明确标记的异常本轮，不替代 SQLite。
- Runtime / CLI / API 正式入口仍未开发，因此“完整 Agent 启动、CLI 多轮交互、API 路由”不宣称已完成。
- 未实现复杂 token 计数、向量/RAG 长期记忆、多进程并发、云同步、权限、artifact 存储和从进程栈断点继续执行；这些均属于设计明确排除项或后续范围。
- 本 Step 无需回写详细设计文档；实现与设计边界保持一致。

### Step 13 完成记录（2026-08-21）

#### 修改文件
- `src/memory/runtime_adapter.py`
- `src/memory/session_manager.py`
- `src/memory/__init__.py`
- `tests/test_memory_runtime_adapter.py`
- `src/memory/Memory层开发步骤与进度.md`

#### 实现结果
- 新增 `RuntimeMemoryAdapter` 作为 Memory 侧 Runtime 预留 facade，不启动 CLI / API，也不直接调用 Agent。
- `begin_turn()` 完成未来 Runtime 一轮调用的前半段：接收或生成 `session_id`、保存 user message、创建 running `AgentRun`、构建 `context_text`、返回绑定 session 的 `ShortTermMemory`。
- `RuntimeMemoryTurn.react_agent_kwargs()` 固定未来 Runtime 调用 ReactAgent 的关键参数：`context_text`、`session_id`、`run_id`、`manage_memory=False`，避免 Runtime 模式下重复写消息。
- `event_callback()`、`record_event()`、`record_events()` 预留 ReActExecutor 可见事件保存路径，继续经由 `SessionManager.append_execution_event()`，不绕过 Memory 事件映射与脱敏。
- `complete_turn()` 保存最终 assistant message、完成 run、可选触发自动摘要，并返回结构化 `RuntimeMemoryResult`。
- `fail_turn()` 标记 run 失败，返回可读错误信息，并保留已写入的用户消息与可见 timeline。
- `get_session()`、`get_timeline()`、`health()` 预留 CLI / API 查询接口所需的 Memory 侧能力。
- `SessionManager.create_user_turn()` 增加 `agent_version`、`model_profile` 参数透传，便于未来 Runtime 记录本轮执行环境。

#### 验证结果
- `python -m pytest -q tests/test_memory_runtime_adapter.py`
  - `5 passed`
- `python -m pytest -q tests/test_memory_config.py tests/test_memory_context_builder.py tests/test_memory_event_mapper.py tests/test_memory_ids.py tests/test_memory_logging_recovery.py tests/test_memory_models.py tests/test_memory_pure_module_acceptance.py tests/test_memory_runtime_adapter.py tests/test_memory_session_manager.py tests/test_memory_short_term_memory.py tests/test_memory_storage.py tests/test_memory_summarizer.py`
  - `69 passed`
- `python -m pytest -q tests/test_memory_config.py tests/test_memory_context_builder.py tests/test_memory_event_mapper.py tests/test_memory_ids.py tests/test_memory_logging_recovery.py tests/test_memory_models.py tests/test_memory_pure_module_acceptance.py tests/test_memory_runtime_adapter.py tests/test_memory_session_manager.py tests/test_memory_short_term_memory.py tests/test_memory_storage.py tests/test_memory_summarizer.py tests/test_memory_react_agent_adaptation.py tests/test_models_context_compression.py`
  - `82 passed`
- `python -m compileall -q src/memory`
  - 通过
- `python -c "from src.memory import RuntimeMemoryAdapter, RuntimeMemoryTurn, RuntimeMemoryResult, MemoryHealthStatus; print(...)"`
  - 公共导入通过

#### 本 Step 边界与遗留问题
- Runtime / CLI / API 正式入口层仍未开发；本 Step 只完成 Memory 侧可调用契约。
- 未修改 Analyzer、Planner、ReActExecutor、Models、Tools 的核心职责。
- `RuntimeMemoryAdapter` 不直接写 SQL，不替代 `SessionManager`，不把入口层逻辑提前塞进 Memory。
- Step 14 的端到端验收需要等 Runtime / CLI / API 或最小入口开发后再完整执行。
- 本 Step 无需回写详细设计文档。

### Step 12 完成记录（2026-08-21）

#### 修改文件
- `tests/test_memory_summarizer.py`
- `src/memory/Memory层开发步骤与进度.md`

#### 实现结果
- 确认 `ConversationSummarizer` 通过 `ModelManager.compress_context()` 调用 Models 层上下文压缩能力。
- 自动摘要保持 Memory 侧职责：只准备 summary 和早期消息 chunks、保存成功摘要并保留模型返回元数据。
- Models 层继续负责 Provider 路由、结构化输出、分块压缩与合并、失败降级；Memory 不直接调用 Provider。
- 新增真实 `ModelManager` 联调测试，验证两段消息压缩后合并为摘要、`context_compression` 调用类型、模型信息和 `compression_method` 回写。
- 新增模型调用失败时的规则降级测试，确认 Memory 将成功的 `rule_fallback` 结果保存为 `rule_fallback` 来源，而不阻塞会话。

#### 验证结果
- `python -m pytest -q tests/test_memory_config.py tests/test_memory_context_builder.py tests/test_memory_event_mapper.py tests/test_memory_ids.py tests/test_memory_logging_recovery.py tests/test_memory_models.py tests/test_memory_pure_module_acceptance.py tests/test_memory_session_manager.py tests/test_memory_short_term_memory.py tests/test_memory_storage.py tests/test_memory_summarizer.py tests/test_memory_react_agent_adaptation.py tests/test_models_context_compression.py`
  - `77 passed`
- `python -m pytest -q tests/test_models_v1_acceptance.py`
  - `3 passed`
- `python -m compileall -q src/memory src/models`
  - 通过

#### 本 Step 边界与遗留问题
- 未接入 Runtime / CLI / API；入口编排仍属于 Step 13。
- `tests/test_models_callers_adaptation.py` 当前在收集阶段引用已移除的 `src.agent.complexity_analyzer`，因此无法执行；它不覆盖本 Step 的压缩实现，未为本次联调修改旧 Agent 测试兼容面。
- 本 Step 无需回写详细设计文档。

### Step 6 完成记录（2026-08-20）

#### 修改文件
- `src/memory/summarizer.py`
- `src/memory/session_manager.py`
- `src/memory/storage.py`
- `src/memory/__init__.py`
- `tests/test_memory_summarizer.py`
- `tests/test_memory_storage.py`

#### 实现结果
- 完成 `ConversationSummarizer` 自动摘要。
- 摘要只处理 `user / assistant + completed` 消息。
- 失败不阻塞对话，保留旧 summary。
- `SessionManager.maybe_auto_summarize()` 已接入委托。
- `execution_events` 仍默认不进入摘要候选。

#### 验证结果
- `python -m pytest -q tests/test_memory_summarizer.py tests/test_memory_storage.py tests/test_memory_session_manager.py tests/test_memory_context_builder.py tests/test_memory_short_term_memory.py tests/test_memory_config.py tests/test_memory_ids.py tests/test_memory_models.py`
  - `49 passed`
- `python -m compileall -q src/memory`
  - 通过

#### 下一步
- Step 7：`ExecutionEvent` 映射与会话回放

### Step 7 完成记录（2026-08-21）

#### 修改文件
- `src/memory/event_mapper.py`
- `src/memory/session_manager.py`
- `src/memory/storage.py`
- `src/memory/__init__.py`
- `tests/test_memory_event_mapper.py`
- `src/memory/Memory层开发步骤与进度.md`

#### 实现结果
- 完成 ReActExecutor `ExecutionEvent` 到 Memory `ExecutionEventRecord` 的映射。
- `visible_to_user=False` 的事件不进入用户 timeline，只写轻量日志。
- 用户可见事件按 `timeline_seq` 与 messages 合并回放。
- payload 和展示文本在写入普通会话记录前完成脱敏与截断。
- 重复 `event_id` 通过 Memory 正式 ID 的唯一约束保持幂等。

#### 验证结果
- `python -m pytest -q tests/test_memory_event_mapper.py tests/test_memory_storage.py tests/test_memory_session_manager.py tests/test_memory_summarizer.py tests/test_memory_context_builder.py tests/test_memory_short_term_memory.py tests/test_memory_config.py tests/test_memory_ids.py tests/test_memory_models.py`
  - `54 passed`
- `python -m compileall -q src/memory`
  - 通过

#### 下一步
- Step 8：Memory 日志、异常降级与恢复

### Step 8 完成记录（2026-08-21）

#### 修改文件
- `src/memory/memory_logging.py`
- `src/memory/storage.py`
- `src/memory/context_builder.py`
- `src/memory/session_manager.py`
- `tests/test_memory_logging_recovery.py`
- `src/memory/Memory层开发步骤与进度.md`

#### 实现结果
- 完成受控 JSONL memory log。
- 日志只记录 ID、状态、长度、preview、payload keys 等诊断信息，并统一脱敏敏感字段。
- 关键路径补齐 `session_created`、`session_loaded`、`message_appended`、`run_created`、
  `event_persisted`、`event_skipped_internal`、`context_built`、`summary_completed`、
  `persistence_warning` 等记录。
- 数据库损坏或无法打开时不覆盖原库，记录 `persistence_warning` 并抛出可读错误。
- 新增 `SessionManager.recover_interrupted_runs()`，显式把 `pending / running / waiting_user`
  标记为 `interrupted`。

#### 验证结果
- `python -m pytest -q tests/test_memory_logging_recovery.py tests/test_memory_event_mapper.py tests/test_memory_storage.py tests/test_memory_session_manager.py tests/test_memory_summarizer.py tests/test_memory_context_builder.py tests/test_memory_short_term_memory.py tests/test_memory_config.py tests/test_memory_ids.py tests/test_memory_models.py`
  - `58 passed`
- `python -m compileall -q src/memory`
  - 通过

#### 下一步
- Step 9：Memory 纯模块测试

### Step 9 完成记录（2026-08-21）

#### 修改文件
- `tests/test_memory_pure_module_acceptance.py`
- `src/memory/Memory层开发步骤与进度.md`

#### 实现结果
- 完成 Memory 纯模块闭环验收测试。
- 验证自动生成和指定 `session_id`、session 隔离、消息写入、SQLite 重启恢复。
- 验证最近消息窗口、summary + recent_messages + current_input 拼接和 current input 去重。
- 验证 messages 与 execution_events 的 `timeline_seq` 合并回放。
- 验证 `visible_to_user` 过滤、`event_id` 幂等和敏感字段脱敏。
- 验证自动摘要成功、摘要失败保留旧 summary。
- 验证未完成 run 恢复为 `interrupted`。
- 验证数据库损坏时错误可读且原库不被覆盖。
- 确认测试均使用临时 SQLite，未污染 `storage/agent_memory.db`。

#### 验证结果
- `python -m pytest -q tests/test_memory_pure_module_acceptance.py tests/test_memory_logging_recovery.py tests/test_memory_event_mapper.py tests/test_memory_storage.py tests/test_memory_session_manager.py tests/test_memory_summarizer.py tests/test_memory_context_builder.py tests/test_memory_short_term_memory.py tests/test_memory_config.py tests/test_memory_ids.py tests/test_memory_models.py`
  - `62 passed`
- `python -m compileall -q src/memory`
  - 通过

#### 下一步
- Step 10：ReactAgent 适配
