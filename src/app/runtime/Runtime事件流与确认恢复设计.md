# Runtime 事件流与确认恢复设计

## 1. 事件回调的含义

ReActExecutor 执行过程中会产生事件。Runtime 调用 ReactAgent 时传入一个函数，执行链每产生一个事件就调用这个函数：

```text
Runtime 创建 callback
  -> 传给 ReactAgent
  -> ReactAgent 传给 ReActExecutor
  -> ReActExecutor 产生 ExecutionEvent
  -> callback 被调用
  -> Runtime 处理、保存、转换并转发
```

回调是通知机制，不是 Runtime 主动轮询执行器。

## 2. 事件处理链

```text
ReActExecutor ExecutionEvent
  -> ReactAgent 透传
  -> Runtime event coordinator
      +--> Runtime sequence
      +--> Memory event mapper / persistence
      +--> RuntimeEvent serialization
      +--> CLI sink
      +--> API WebSocket sink
```

## 3. 事件可见性

默认用户可见事件：

```text
progress_message
step_started
step_completed
tool_started
tool_finished
command_started
command_finished
file_edited
confirmation_requested
final_answer
system_notice
```

默认不可见事件：

```text
model_step_started
model_step_finished
action_selected 的内部细节
raw_observation
raw_prompt
hidden reasoning
```

实际事件类型以 ReActExecutor 当前协议和测试为准。设计中的白名单不能覆盖底层更严格的安全规则。

## 4. 事件分发规则

Runtime 应同时支持：

```text
memory_callback
external_callback
```

推荐处理顺序：

1. 先经过 Memory 的映射、脱敏和持久化。
2. 再将安全的用户可见事件发送给 CLI/API sink。
3. 外部 sink 失败不能反向破坏 Agent 执行。
4. Memory 事件持久化失败时记录 warning，但允许继续执行，除非 Memory 明确要求中止。

Memory 已经提供事件 facade 时，Runtime 不应再建立第二套 SQL 写入路径。

## 5. RuntimeEvent sequence

每个 run 的事件从 1 开始递增：

```text
run_id=A
  event sequence 1
  event sequence 2
  event sequence 3
```

sequence 只用于当前 Runtime 输出顺序，不替代 Memory 的 event_id。

当事件来自结果回放而不是实时 callback 时，Runtime 需要避免重复发送：

- callback 已发送的事件不能因为 ExecutionResult.events 再发送一次。
- 没有 callback 的旧兼容执行路径才可以从最终结果补发事件。

## 6. 事件 sink 异常

### CLI sink

终端写入失败通常只影响展示，不应修改 run 的执行状态。应记录本地错误并继续等待最终结果。

### API WebSocket sink

连接断开后：

- Runtime 仍可继续同步 run，或者由 API 层根据连接生命周期决定是否停止等待。
- V1 不承诺把实时事件重新推送给已断开的客户端。
- Memory 已保存的事件仍可通过 timeline 查询。

### Memory 持久化

按照 Memory V1 的降级机制处理：

- 本轮临时结果仍可以返回。
- 返回 `persistence_available=false`。
- 返回安全的 `persistence_warning`。

## 7. PendingConfirmation

当工具策略或执行器要求用户确认时，RuntimeResult 至少包含：

```text
status=waiting_user
requires_user_input=true
pending_confirmation
session_id
run_id
```

pending confirmation 的对外内容应包括：

- confirmation_id。
- preview_hash。
- 工具或动作的安全名称。
- 用户可理解的动作说明。
- 必要的安全预览。
- 过期时间（如果底层协议支持）。

不应包括：

- API Key、Token、Cookie、密码。
- 隐藏 prompt。
- 完整 raw tool result。
- 未经脱敏的命令、路径或环境变量。

## 8. PendingRunRegistry

由于 ReActExecutor 的 `resume_after_confirmation()` 需要原始执行上下文，Runtime 需要进程内登记：

```text
run_id
  -> session_id
  -> executor context
  -> pending confirmation
  -> created_at
  -> owner / connection metadata（可选）
```

要求：

1. 只保存当前进程内继续 resume 所需的最小对象。
2. 不能把该对象直接返回给 CLI/API。
3. 使用锁或其他并发控制保护 registry。
4. resume 成功、拒绝、取消、异常后删除登记。
5. 进程重启后 registry 丢失，旧 run 由 Memory 标记为 interrupted。
6. 应设置过期清理，避免等待确认对象无限增长。

## 9. resume 流程

```text
CLI/API 提交 resume
  -> 校验 session_id / run_id
  -> 从 PendingRunRegistry 查找上下文
  -> 校验 confirmation_id / preview_hash
  -> 调用 ReActExecutor.resume_after_confirmation()
  -> 继续接收事件
  -> 处理新的 ExecutionResult
  -> complete_turn / fail_turn
  -> 清理 registry
  -> 返回 RuntimeResult
```

如果找不到上下文：

```text
run_not_found 或 interrupted
```

不能重新构造一个看似可以继续的执行上下文。

## 10. cancel 流程

V1 只取消等待确认的 run：

```text
waiting_user
  -> Runtime.cancel()
  -> 调用底层拒绝/取消确认逻辑
  -> 记录用户取消事件
  -> run 状态 cancelled
  -> 清理 pending registry
```

V1 不承诺强制终止正在运行的线程、模型调用或工具进程。

## 11. 必须联动阅读

开发前必须阅读：

- `src/agent/react_executor/react_executor_events.py`
- `src/agent/react_executor/react_executor_protocol.py`
- `src/agent/react_executor/react_executor_result.py`
- `src/agent/react_executor/react_executor.py`
- ReActExecutor 确认和 preview/resume 测试
- Memory `event_mapper.py` 和事件测试
- Tools 的安全策略、确认和输出控制设计

