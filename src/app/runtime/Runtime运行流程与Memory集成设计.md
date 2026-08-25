# Runtime 运行流程与 Memory 集成设计

## 1. 目标

本文档规定一轮 Agent 请求如何经过 Runtime、Memory 和 ReactAgent，重点解决：

- session/run 的生命周期。
- 消息由谁写入。
- context 如何构建。
- 执行事件如何进入 Memory。
- 结果完成、失败和降级时如何收口。

## 2. 正式 Runtime 模式

正式入口必须使用：

```text
ReactAgent(..., manage_memory=False)
```

每次调用仍要显式传递：

```text
context_text
session_id
run_id
manage_memory=False
event_callback
```

Runtime 不应该只依赖 ReactAgent 内部的短期记忆自动推断当前上下文。

## 3. 普通 run 的时序

```text
CLI/API
  -> Runtime.run(request)
      -> validate request
      -> resolve session
      -> memory.begin_turn()
          -> create/load session
          -> create user message
          -> create running AgentRun
          -> build summary + recent_messages + current_input
      -> build event callback
      -> ReactAgent.run_with_result(
             context_text,
             session_id,
             run_id,
             manage_memory=False,
             event_callback
         )
          -> Analyzer
          -> Planner
          -> ReActExecutor
      -> convert ExecutionResult
      -> OutputFeedbackProcessor
      -> memory.complete_turn() 或 memory.fail_turn()
      -> build RuntimeResult
```

## 4. begin_turn 阶段

Runtime 通过 `RuntimeMemoryAdapter.begin_turn()` 进入本轮：

1. 如果没有 `session_id`，由 Memory 生成新 ID。
2. 如果有 `session_id`，先校验格式，再加载已有 session。
3. 创建 user message。
4. 创建 `running` 状态的 AgentRun。
5. 构建本轮 context。
6. 获取本轮 `session_id` 和 `run_id`。
7. 记录持久化是否可用。

Runtime 不直接调用 SQLite，也不自行创建数据库实体 ID。

## 5. Context 传递

Memory 负责组织：

```text
summary
  + recent_messages
  + current_input
```

Runtime 只把 Memory 构建好的 `context_text` 传给 ReactAgent：

```text
context_text -> ReactAgent -> Analyzer / Planner / ReActExecutor
```

Runtime 不应在 CLI/API 层拼接历史，也不应将完整 SQLite 消息列表直接塞给模型。

上下文阈值、自动摘要和 Models 层的 `compress_context()` 都属于 Memory / Models 的职责。

## 6. 消息写入责任

### 6.1 user message

由 `RuntimeMemoryAdapter.begin_turn()` 或其内部 SessionManager 写入。

### 6.2 assistant message

执行成功并获得最终输出后，由 `RuntimeMemoryAdapter.complete_turn()` 写入。

### 6.3 失败消息和状态

执行失败时由 `RuntimeMemoryAdapter.fail_turn()` 负责：

- 更新 run 状态。
- 保存安全的错误摘要。
- 获取可回放的 timeline。

### 6.4 禁止重复写入

正式 Runtime 模式下：

```text
Runtime/Memory 写 user message
Runtime/Memory 写 assistant message
ReactAgent 不写消息
```

如果测试发现消息重复，先检查 `manage_memory=False` 是否真正传到了 ReactAgent，而不是先修改数据库逻辑。

## 7. 事件写入

Runtime 将 ReActExecutor 的事件交给 Memory event callback：

```text
ExecutionEvent
  -> Runtime callback
  -> Memory event mapper
  -> visible_to_user 判断
  -> 脱敏
  -> 幂等 append
  -> timeline
```

默认规则：

- 可见事件保存到用户 timeline。
- 内部事件不进入普通 timeline。
- 外部 sink 默认只接收可见事件。
- debug 模式仍不能绕过敏感信息过滤。

## 8. 完成路径

当执行器返回 `completed`：

1. 从 ExecutionResult 取得 output。
2. 构建 OutputFeedback。
3. 将用户可见最终文本交给 `complete_turn()`。
4. Memory 更新 assistant message 和 run 状态。
5. 根据配置触发自动摘要。
6. 获取 timeline。
7. 生成成功 RuntimeResult。

如果 `complete_turn()` 的持久化失败，但 Memory 返回了临时内存结果：

- Runtime 仍返回本轮 output。
- `success` 可以保持为 true。
- `persistence_available=false`。
- 填写 `persistence_warning`。
- 不宣称该结果已经可靠持久化。

## 9. 失败路径

以下情况进入 `fail_turn()` 或统一失败包装：

- Analyzer 异常。
- Planner 异常。
- ReActExecutor 未分类异常。
- Models 调用异常。
- Tools 层抛出未被执行器转换的异常。
- Memory 在允许降级前无法建立本轮。

Runtime 需要区分：

```text
Agent 执行失败
持久化失败
依赖初始化失败
请求参数失败
```

不能把所有异常都包装成同一个 `internal_error`。

## 10. waiting_user 路径

当 ExecutionResult 表示：

```text
status=waiting_user
requires_user_input=true
pending_confirmation != None
```

Runtime 必须：

1. 保存安全的 pending confirmation 信息。
2. 将本轮状态保留为等待用户处理的状态。
3. 将 `pending_confirmation` 放入 RuntimeResult。
4. 不保存隐藏推理和原始工具结果。
5. 将可见确认事件保存并转发。
6. 将执行上下文登记到 pending registry，供同一进程内 resume 使用。

## 11. request_replan 路径

V1 Runtime 不应该无限自动重规划。

当执行器返回 `request_replan`：

- 保留 `request_replan=true`。
- 返回 `replan_reason` 的安全摘要。
- 按 Memory 规则完成或失败当前 turn。
- 由上层决定是否发起新的 run。

如果未来支持自动重规划，必须单独定义最大次数、上下文更新、事件和 run 关系。

## 12. 启动恢复

Runtime 初始化调用：

```text
SessionManager.recover_interrupted_runs()
```

旧进程留下的 `pending`、`running`、`waiting_user` run 标记为 `interrupted`。

V1 不从 SQLite 自动重建 ReActExecutor 执行上下文，也不自动继续危险操作。

## 13. 必须联动阅读

开发前必须核对：

- `src/memory/runtime_adapter.py` 的 `begin_turn`、`complete_turn`、`fail_turn` 和 event callback。
- `src/memory/context_builder.py` 的 context 实际格式。
- `src/memory/event_mapper.py` 的 visible 和脱敏规则。
- `src/agent/orchestrator/react_agent.py` 的 `run_with_result`、`run_stream` 和 `manage_memory` 行为。
- `src/agent/react_executor/` 的结果状态和确认字段。
- Memory 与 Agent 现有测试。

