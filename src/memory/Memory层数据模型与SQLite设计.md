# Memory 层数据模型与 SQLite 设计

本文档定义 Memory / Context MVP 的核心数据结构、SQLite 表设计、枚举和持久化边界。

## 1. 数据设计原则

```text
1. SQLite 是唯一会话持久化源。
2. SessionState 是运行时对象，由 SQLite 记录组装，不直接保存为一个大 JSON。
3. 完整会话历史长期保存。
4. 模型上下文只读取必要窗口。
5. 用户可见历史和内部日志分开。
6. 所有可扩展字段使用 metadata_json / payload_json。
7. 所有 id 必须稳定、唯一、可追踪。
8. 所有写入使用事务。
```

## 2. 核心数据对象

### Message

表示真正的聊天消息。

```python
Message(
    message_id: str,
    session_id: str,
    run_id: str | None,
    timeline_seq: int,
    role: str,
    content: str,
    content_format: str,
    display_type: str,
    visible_to_user: bool,
    status: str,
    parent_message_id: str | None,
    created_at: str,
    updated_at: str | None,
    metadata: dict,
)
```

适合放入 Message：

```text
用户原始输入。
Agent 最终回答。
澄清问题。
确认请求。
用户可见错误。
```

不适合放入 Message：

```text
工具调用开始/结束。
步骤进度。
原始 Observation。
原始 ActionPacket。
模型隐藏推理。
```

这些应进入 execution_events 或日志。

### SessionState

运行时加载后的会话状态。

```python
SessionState(
    session_id: str,
    messages: list[Message],
    summary: str,
    current_summary_id: str | None,
    created_at: str,
    updated_at: str,
    last_activity_at: str,
    status: str,
    metadata: dict,
)
```

SessionState 用于：

```text
恢复旧 session。
构建上下文。
给 Runtime / CLI / API 返回会话状态。
测试会话是否正确隔离和恢复。
```

### AgentRun

一次用户输入触发的一整轮 Agent 执行。

```python
AgentRun(
    run_id: str,
    session_id: str,
    user_message_id: str,
    status: str,
    started_at: str,
    finished_at: str | None,
    final_message_id: str | None,
    error_code: str | None,
    error_message: str | None,
    agent_version: str | None,
    model_profile: str | None,
    metadata: dict,
)
```

### ExecutionEventRecord

保存用户可见执行事件。

```python
ExecutionEventRecord(
    event_id: str,
    session_id: str,
    run_id: str,
    timeline_seq: int,
    event_type: str,
    display_type: str,
    display_content: str,
    visible_to_user: bool,
    status: str,
    created_at: str,
    completed_at: str | None,
    parent_event_id: str | None,
    sanitized_payload: dict,
    metadata: dict,
)
```

### SessionSummary

摘要版本。

```python
SessionSummary(
    summary_id: str,
    session_id: str,
    content: str,
    covered_from_timeline_seq: int,
    covered_to_timeline_seq: int,
    created_at: str,
    source: str,
    model_profile: str | None,
    metadata: dict,
)
```

## 3. id 规则

建议格式：

```text
session_id:
  session_YYYYMMDD_xxxxxxxx

message_id:
  msg_YYYYMMDD_HHMMSS_xxxxxx

run_id:
  run_YYYYMMDD_HHMMSS_xxxxxx

event_id:
  event_YYYYMMDD_HHMMSS_xxxxxx

summary_id:
  summary_YYYYMMDD_HHMMSS_xxxxxx
```

`session_id` 校验规则：

```text
只允许字母、数字、下划线、短横线。
禁止空字符串。
禁止 ..。
禁止 / 和 \。
禁止绝对路径。
```

虽然 SQLite 不像文件路径那样容易目录穿越，但 session_id 仍然必须校验，避免后续导出文件、日志路径或 API 参数出现安全问题。

## 4. 枚举

### role

```text
user
assistant
system
tool
```

MVP 主要使用 `user` 和 `assistant`。

### content_format

```text
text
markdown
json
```

### display_type

```text
chat
final_answer
clarification
confirmation
tool_progress
plan_progress
system_notice
error
summary
```

使用规则：

```text
chat:
  普通用户/助手消息。

final_answer:
  本轮最终回答。

clarification:
  Agent 追问缺失信息。

confirmation:
  Agent 请求确认、授权或选择。

tool_progress:
  工具、命令、文件编辑相关可见过程。

plan_progress:
  计划、步骤、重试、fallback 等进展。

system_notice:
  系统状态、阻塞、安全拦截等提示。

error:
  用户可见错误。

summary:
  摘要记录，默认不作为聊天消息显示。
```

### message.status

```text
pending
streaming
completed
failed
cancelled
```

### agent_run.status

```text
pending
running
waiting_user
completed
failed
partial_failed
blocked
cancelled
request_replan
interrupted
```

`interrupted` 是 Memory 恢复时使用的状态：如果程序重启后发现 run 仍处于 pending / running / waiting_user，但原执行进程不存在，则标记为 interrupted。

### execution_event.status

```text
recorded
started
completed
failed
waiting_user
blocked
skipped
request_replan
```

推导规则：

```text
xxx_started -> started
xxx_finished / xxx_completed / final_answer -> completed
xxx_failed -> failed
confirmation_requested -> waiting_user
request_replan -> request_replan
其他 -> recorded
```

## 5. timeline_seq

`timeline_seq` 是同一个 session 中用户可见时间线的全局顺序号。

规则：

```text
1. 从 1 开始递增。
2. messages 和 execution_events 共用同一套 timeline_seq。
3. 在 SQLite 事务中分配。
4. 不靠 created_at 代替 timeline_seq。
5. 重新打开 session 时按 timeline_seq 合并排序。
```

原因：

```text
同一秒可能写入多条事件。
流式、工具事件和最终回答需要混合回放。
用户看到的是时间线，不是单纯 messages 列表。
```

## 6. SQLite 表

### sessions

```sql
CREATE TABLE sessions (
  session_id TEXT PRIMARY KEY,
  title TEXT,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_activity_at TEXT NOT NULL,
  current_summary_id TEXT,
  last_run_id TEXT,
  next_timeline_seq INTEGER NOT NULL DEFAULT 1,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  schema_version INTEGER NOT NULL
);
```

### messages

```sql
CREATE TABLE messages (
  message_id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  run_id TEXT,
  timeline_seq INTEGER NOT NULL,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  content_format TEXT NOT NULL,
  display_type TEXT NOT NULL,
  visible_to_user INTEGER NOT NULL,
  status TEXT NOT NULL,
  parent_message_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  UNIQUE(session_id, timeline_seq),
  FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
);
```

### agent_runs

```sql
CREATE TABLE agent_runs (
  run_id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  user_message_id TEXT NOT NULL,
  status TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  final_message_id TEXT,
  error_code TEXT,
  error_message TEXT,
  agent_version TEXT,
  model_profile TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
);
```

### execution_events

```sql
CREATE TABLE execution_events (
  event_id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  timeline_seq INTEGER NOT NULL,
  event_type TEXT NOT NULL,
  display_type TEXT NOT NULL,
  display_content TEXT NOT NULL,
  visible_to_user INTEGER NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  completed_at TEXT,
  parent_event_id TEXT,
  sanitized_payload_json TEXT NOT NULL DEFAULT '{}',
  metadata_json TEXT NOT NULL DEFAULT '{}',
  UNIQUE(session_id, timeline_seq),
  FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE,
  FOREIGN KEY(run_id) REFERENCES agent_runs(run_id) ON DELETE CASCADE
);
```

### session_summaries

```sql
CREATE TABLE session_summaries (
  summary_id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  content TEXT NOT NULL,
  covered_from_timeline_seq INTEGER NOT NULL,
  covered_to_timeline_seq INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  source TEXT NOT NULL,
  model_profile TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
);
```

### schema_migrations

```sql
CREATE TABLE schema_migrations (
  version INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  applied_at TEXT NOT NULL
);
```

## 7. 索引

建议索引：

```sql
CREATE INDEX idx_messages_session_seq
  ON messages(session_id, timeline_seq);

CREATE INDEX idx_messages_session_role_seq
  ON messages(session_id, role, timeline_seq);

CREATE INDEX idx_runs_session_started
  ON agent_runs(session_id, started_at);

CREATE INDEX idx_events_session_seq
  ON execution_events(session_id, timeline_seq);

CREATE INDEX idx_events_run_seq
  ON execution_events(run_id, timeline_seq);

CREATE INDEX idx_summaries_session_created
  ON session_summaries(session_id, created_at);
```

## 8. 事务边界

用户输入到达：

```text
BEGIN
  get_or_create session
  allocate timeline_seq
  insert user message
  insert agent_run(status=running)
  update sessions.last_run_id / updated_at / last_activity_at
COMMIT
```

执行事件到达：

```text
BEGIN
  if visible_to_user=True:
    allocate timeline_seq
    insert execution_event
  else:
    write memory log only
  update sessions.updated_at / last_activity_at
COMMIT
```

最终回答：

```text
BEGIN
  allocate timeline_seq
  insert assistant message
  update agent_run completed/failed/blocked/waiting_user
  update sessions.updated_at / last_activity_at
COMMIT
```

摘要更新：

```text
BEGIN
  insert session_summary
  update sessions.current_summary_id
COMMIT
```

## 9. 幂等与重复写入

必须保证：

```text
message_id UNIQUE
run_id UNIQUE
event_id UNIQUE
summary_id UNIQUE
UNIQUE(session_id, timeline_seq)
```

重复写入策略：

```text
同 event_id 重复到达：
  不重复插入。
  如果状态更完整，可以更新原记录。

同 message_id 重复到达：
  不重复插入。

同 run_id 重复 create：
  返回已有 run 或报可读错误。
```

## 10. 错误和损坏恢复

数据库不存在：

```text
自动创建目录和 schema。
```

数据库无法打开或损坏：

```text
不覆盖原数据库。
记录 logs/memory.log。
Runtime 返回可读错误。
是否启用临时内存会话必须显式标记为本轮不会持久化。
```

程序重启后发现未完成 run：

```text
pending / running / waiting_user -> interrupted
completed / failed / blocked / cancelled -> 保持原状态
```

## 11. 敏感数据边界

普通会话数据库不保存：

```text
API Key
token
Cookie
密码
完整认证头
隐藏推理 / Chain of Thought
完整 system prompt
未经脱敏的工具原始输出
traceback 中的敏感上下文
```

`metadata_json` 和 `sanitized_payload_json` 写入前必须经过脱敏和长度限制。

