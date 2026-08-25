# Memory 层执行事件与会话回放设计

本文档定义 Memory 如何消费 ReActExecutor 的事件流，并把用户实际看到的执行过程保存为可恢复的会话时间线。

## 1. 设计目标

用户重新打开旧 session 时，应能看到：

```text
1. 用户当时发送的原始消息。
2. Agent 当时展示的最终回答。
3. Agent 当时展示过的计划、进度、工具调用摘要、确认请求和错误。
4. 这些内容的原始顺序。
```

但不能看到：

```text
隐藏推理。
内部 ActionPacket。
未经脱敏的工具结果。
原始 prompt。
模型供应商请求细节。
API Key、Cookie、token。
```

## 2. ReActExecutor 现有事件能力

当前执行层已经有：

```text
ExecutionEvent:
  execution_id
  plan_id
  type
  message
  event_id
  task_id
  step_id
  timestamp
  visible_to_user
  payload

EventStream:
  emit_event()
  visible_events()
  internal_events()
  to_user_timeline()
  to_model_context()
```

Memory 不重新定义执行事件体系，而是做映射和持久化。

## 3. 核心规则

第一判断标准：

```text
event.visible_to_user
```

规则：

```text
visible_to_user=True:
  进入 execution_events 表。
  参与 session timeline 回放。

visible_to_user=False:
  不进入普通用户会话回放。
  只写 memory 日志或后续 debug/audit 存储。
```

注意：

```text
不能只看 event.type。
某些 final_answer / request_replan / step_completed 可能是内部中间事件。
只要 visible_to_user=False，就不能进入用户可见历史。
```

## 4. event_type 到 display_type 的映射

建议映射：

```text
progress_message        -> plan_progress
thought_visible         -> plan_progress
step_started            -> plan_progress
step_completed          -> plan_progress
step_failed             -> error 或 plan_progress
retry_scheduled         -> plan_progress
retry_finished          -> plan_progress
retry_exhausted         -> error
fallback_started        -> plan_progress
fallback_finished       -> plan_progress
request_replan          -> plan_progress

tool_started            -> tool_progress
tool_finished           -> tool_progress
tool_failed             -> error
command_started         -> tool_progress
command_finished        -> tool_progress
file_edited             -> tool_progress
observation_created     -> tool_progress

confirmation_requested  -> confirmation
system_notice           -> system_notice
final_answer            -> final_answer
message_delta           -> chat 或 plan_progress
model_step_started      -> plan_progress 或 internal
model_step_finished     -> plan_progress 或 internal
action_selected         -> plan_progress 或 internal
```

更细规则：

```text
step_failed / tool_failed / retry_exhausted:
  如果 message 是用户可见错误，用 display_type=error。
  如果只是过程状态，用 display_type=plan_progress 或 tool_progress。

message_delta:
  如果是用户界面实际展示的流式回答，最终合并为 assistant Message。
  如果只是中间模型输出，默认只进内部日志。

model_step_started / model_step_finished:
  如果界面显示“正在调用模型”，保存为 plan_progress。
  如果只是内部模型调用细节，不进入用户时间线。

action_selected:
  如果 packet.user_visible_message 是给用户看的计划说明，保存为 plan_progress。
  如果只是内部 ActionPacket 决策，不进入用户时间线。
```

## 5. payload 保存边界

`display_content` 保存：

```text
event.message 的脱敏、截断后文本。
```

`sanitized_payload_json` 保存：

```text
event.payload 的脱敏摘要。
```

不能保存：

```text
raw_prompt
full_prompt
raw_model_output
raw_tool_result
raw_observation
action_args 中的敏感字段
stack_trace
traceback
env
```

如果 payload 很大：

```text
只保存摘要。
大文件或大输出以后通过 artifact_ref / raw_ref 引用。
MVP 不实现 artifact 存储，但字段预留。
```

## 6. 时间线合并

回放时查询：

```sql
SELECT timeline_seq, 'message' AS item_kind, ...
FROM messages
WHERE session_id = ?
UNION ALL
SELECT timeline_seq, 'event' AS item_kind, ...
FROM execution_events
WHERE session_id = ?
ORDER BY timeline_seq ASC;
```

返回结构建议：

```python
TimelineItem(
    item_id: str,
    item_kind: str,       # message / execution_event
    session_id: str,
    run_id: str | None,
    timeline_seq: int,
    display_type: str,
    role: str | None,
    content: str,
    status: str,
    created_at: str,
    metadata: dict,
)
```

## 7. 流式事件

如果后续支持流式输出：

```text
message_delta 不要每个 token 都保存成永久 Message。
可以先在内存中缓存 delta。
最终合并为一条 assistant Message。
必要时保存一个 execution_event 表示“流式回复已完成”。
```

MVP 如果暂时没有真正 token 级流式 UI：

```text
message_delta 默认不作为用户可见聊天消息保存。
最终 assistant response 才进入 messages。
```

## 8. 非流式与流式运行的保存方案

非流式：

```text
execute() 返回 ExecutionResult。
Runtime / ReactAgent 遍历 result.events。
保存 visible_to_user=True 的事件。
保存最终 assistant Message。
```

缺点：

```text
如果执行过程中崩溃，返回前的事件可能丢失。
```

推荐方案：

```text
execute(..., event_callback=memory_event_callback, event_callback_visible_only=True)
```

事件产生时立即保存。由于 ReActExecutor 已有该参数，这属于小幅适配。

流式：

```text
execute_stream(..., include_internal=False)
Runtime 边 yield 给用户，边保存事件。
最终 StopIteration.value 中的 ExecutionResult 用于保存 assistant Message 和 run 状态。
```

## 9. AgentRun 状态更新

运行开始：

```text
agent_runs.status = running
```

等待用户确认：

```text
agent_runs.status = waiting_user
```

完成：

```text
agent_runs.status = completed
final_message_id = assistant message id
```

失败：

```text
agent_runs.status = failed
error_code / error_message 写入
```

阻塞：

```text
agent_runs.status = blocked
```

请求重规划：

```text
agent_runs.status = request_replan
```

重启恢复时：

```text
pending / running / waiting_user -> interrupted
```

## 10. 日志与会话历史区别

会话历史用于用户回放：

```text
messages
execution_events visible_to_user=True
```

日志用于开发排查：

```text
logs/memory.log
```

日志可以记录：

```text
session_created
session_loaded
message_appended
run_created
event_persisted
event_skipped_internal
context_built
summary_started
summary_completed
summary_failed
persistence_warning
```

日志不要记录完整敏感正文，最多记录：

```text
id
session_id
run_id
event_type
status
content_length
payload_keys
error_code
短 preview
```

## 11. 测试重点

```text
1. visible_to_user=True 的事件会进入 execution_events。
2. visible_to_user=False 的事件不会进入用户 timeline。
3. messages 与 execution_events 按 timeline_seq 正确合并。
4. final_answer 中间内部事件不会误保存。
5. tool_started/tool_finished 能被映射成 tool_progress。
6. confirmation_requested 能被映射成 confirmation。
7. 敏感 payload key 会被脱敏或丢弃。
8. 重复 event_id 不会重复插入。
9. run interrupted 状态恢复正确。
```

