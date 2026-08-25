# Memory 层上下文构建与自动摘要设计

本文档定义 ContextBuilder、最近消息窗口和自动摘要机制。

## 1. 目标

ContextBuilder 的目标是让 Agent 在多轮对话中知道前文，同时避免把完整历史无限塞给模型。

它输出给模型的是：

```text
summary + recent_messages + current_user_input
```

它不负责：

```text
不生成最终回答。
不规划任务。
不执行工具。
不做向量检索。
```

## 2. 完整历史与模型上下文的区别

完整历史：

```text
长期保存在 SQLite。
用于 session 恢复、用户回放、调试和后续摘要。
可以包含所有 user/assistant 消息和用户可见 execution_events。
```

模型上下文：

```text
每轮临时构建。
只包含模型当前需要看的内容。
默认不包含完整 execution_events。
默认不包含大段工具输出。
默认不包含所有历史消息。
```

这是 Memory 层最重要的边界之一。

## 3. ContextBuildResult

内部使用结构化对象：

```python
ContextBuildResult(
    session_id: str,
    context_text: str,
    summary: str,
    recent_messages: list[Message],
    included_message_ids: list[str],
    included_event_ids: list[str],
    truncated: bool,
    current_user_input_included: bool,
    token_estimate: int | None,
    char_count: int,
    metadata: dict,
)
```

对外兼容：

```python
context_builder.build(...).context_text
short_term_memory.get_history_text()
```

## 4. 上下文文本格式

MVP 使用稳定可读格式：

```text
[Session Summary]
{summary 或 "No summary yet."}

[Recent Messages]
user: ...
assistant: ...

[Current User Input]
...
```

规则：

```text
1. 每轮都构建 context。
2. current_user_input 如果已经作为最后一条 user message 写入，不重复追加。
3. recent_messages 默认只取 user / assistant。
4. system/tool 消息只有确实需要模型理解时才加入。
5. execution_events 默认不加入。
```

## 5. 最近消息窗口

MVP 配置：

```text
max_recent_messages = 10
```

含义：

```text
最近 10 条消息，不是最近 10 轮。
一轮 user + assistant 通常是 2 条消息。
```

如果未来想改成最近 10 轮，可以新增配置：

```text
max_recent_turns = 10
```

MVP 先按消息条数实现，降低复杂度。

## 6. 构建规则

消息数量少：

```text
message_count <= max_recent_messages
context = all user/assistant messages + current input
truncated = False
```

消息数量中等：

```text
message_count > max_recent_messages
context = current summary + recent 10 messages + current input
truncated = True
```

消息数量较多且超过摘要阈值：

```text
message_count > summary_trigger_messages
先尝试自动摘要早期历史
再 context = new summary + recent 10 messages + current input
```

## 7. 自动摘要

用户已经选择：

```text
触发阈值后自动更新 summary。
```

MVP 配置：

```text
summary_trigger_messages = 14
summary_batch_messages = 6
summary_target_chars = 2000
summary_allow_rule_fallback = True
```

通俗解释：

```text
最近 10 条消息保留原文。
更早的若干条消息压缩进 summary。
旧 summary 和新压缩内容合并成新版 summary。
```

## 8. 自动摘要触发流程

```text
SessionManager.maybe_auto_summarize(session_id)
  -> 查询当前 summary 覆盖到的 timeline_seq
  -> 查询尚未被 summary 覆盖、且不属于最近 10 条的早期 user/assistant 消息
  -> 如果数量不足 summary_batch_messages，不触发
  -> 组织 chunks
  -> ConversationSummarizer.compress()
  -> ModelManager.compress_context()
  -> 写入 session_summaries
  -> 更新 sessions.current_summary_id
```

伪流程：

```python
if message_count <= summary_trigger_messages:
    return old_summary

candidate_messages = messages_before_recent_window()
candidate_messages = messages_not_covered_by_current_summary(candidate_messages)

if len(candidate_messages) < summary_batch_messages:
    return old_summary

result = model_manager.compress_context(
    source_type="conversation_summary",
    chunks=summary_chunks,
    target_chars=summary_target_chars,
    preserve_keys=[
        "user_goal",
        "decisions",
        "constraints",
        "file_paths",
        "open_tasks",
        "preferences",
    ],
    trigger_reason="memory_auto_summary",
    allow_rule_fallback=True,
)

if result.success:
    save_new_summary(result.compressed_text)
else:
    log_summary_failed()
```

## 9. 摘要内容要求

summary 应保留：

```text
用户长期目标。
项目背景。
关键文件路径。
已经确认的设计决策。
用户偏好。
未完成事项。
重要约束。
重要错误和阻塞。
下一步计划。
```

summary 不应保留：

```text
隐藏推理。
API Key、token、Cookie、密码。
大段工具原始输出。
重复寒暄。
短期无用过程噪声。
内部 ActionPacket。
未展示的模型原始输出。
```

## 10. 摘要版本

每次摘要写一条新记录：

```text
session_summaries
  summary_id
  session_id
  content
  covered_from_timeline_seq
  covered_to_timeline_seq
  created_at
  source
  model_profile
  metadata_json
```

`sessions.current_summary_id` 指向当前使用的版本。

不要直接覆盖旧 summary。原因：

```text
摘要失败可以回退。
后续可以调试摘要质量。
可以知道 summary 覆盖到哪段历史。
```

## 11. 摘要失败降级

如果 `ModelManager.compress_context()` 失败：

```text
1. 不阻塞本轮对话。
2. 保留旧 summary。
3. 继续使用最近 10 条消息构建上下文。
4. 写 logs/memory.log: summary_failed。
5. 下轮达到条件时可再次尝试。
```

如果没有模型可用：

```text
allow_rule_fallback=True 时使用规则 fallback。
规则 fallback 仍失败时保持旧 summary。
```

## 12. execution_events 是否进入上下文

默认不进入。

原因：

```text
完整事件时间线通常噪声大。
工具过程、模型步骤、重试记录会干扰当前回答。
```

例外：

```text
关键工具结果摘要影响后续任务。
例如：“上一步读取的文件路径”“测试失败错误码”“用户确认了某个方案”。
```

MVP 可先不实现事件选择器，只保留扩展字段：

```text
included_event_ids
metadata["event_context_policy"]
```

## 13. 与 ReactAgent 的兼容

当前 ReactAgent 调用：

```python
history = short_term_memory.get_history_text()
executor.execute(..., history=history)
```

新 ShortTermMemory 的 `get_history_text()` 应内部调用 ContextBuilder。

```text
ShortTermMemory.get_history_text()
  -> ContextBuilder.build(session_id)
  -> return context_text
```

这样可以在不大改 ReactAgent 的前提下让 ReActExecutor 拿到新上下文。

## 14. 测试重点

```text
1. 空 session 能构建稳定 context。
2. 少于 10 条消息时返回完整消息。
3. 超过 10 条时返回 summary + 最近 10 条。
4. current_user_input 不重复出现。
5. 自动摘要超过阈值后触发。
6. 摘要覆盖范围正确记录。
7. 摘要失败不影响 context 构建。
8. 敏感字段不会出现在 summary。
9. ContextBuildResult.included_message_ids 正确。
```

