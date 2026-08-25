# Runtime 架构与模块设计

## 1. 目标

Runtime 是 `src/app/` 层的核心。它把已有的 Memory、ReactAgent、Models、Tools 和输出反馈能力装配成一个可以被 CLI、API 和测试稳定调用的应用运行对象。

Runtime 不是新的 Agent 推理层，而是应用编排层。

## 2. 建议模块结构

```text
src/app/runtime/
  __init__.py
  core.py              # Runtime 主对象和公开入口
  contracts.py         # RuntimeRequest / RuntimeResult / RuntimeEvent 等
  factory.py            # 生产依赖装配和测试依赖注入
  errors.py             # RuntimeErrorCode 和统一异常
  health.py             # 健康检查和依赖状态聚合
  serialization.py      # 对外序列化、脱敏和字段裁剪
  pending_runs.py       # waiting_user 运行上下文登记
  queue.py              # 可选的运行排队基础设施
  export.py              # session Markdown 导出
```

实际拆分可以根据现有代码调整，但不能把所有逻辑长期堆到一个文件。

## 3. Runtime 对象的职责

Runtime 至少需要提供以下公开能力：

```text
run(request) -> RuntimeResult
run_stream(request, event_sink) -> RuntimeRunHandle 或事件迭代器
resume(request) -> RuntimeResult
cancel(request) -> RuntimeResult
get_session(session_id)
list_sessions()
get_timeline(session_id)
delete_session(session_id)
export_session(session_id, output_path)
health()
```

公开入口的参数应使用稳定的请求对象或明确的关键字参数，不让 CLI/API 直接依赖内部 Agent 对象。

## 4. Runtime 内部层次

```text
Runtime public facade
  -> request validation
  -> session/run coordinator
  -> memory coordinator
  -> agent coordinator
  -> event coordinator
  -> result coordinator
  -> serialization / error mapping
```

### 4.1 请求校验

校验：

- 输入是否为空。
- 输入长度是否超过 Runtime 限制。
- `session_id` 格式是否合法。
- `run_id` 是否存在且属于目标 session。
- debug、stream、metadata 等字段类型是否正确。
- resume 参数是否完整。
- 删除和导出路径是否符合安全策略。

### 4.2 会话运行协调

负责：

- 新建 session 或加载已有 session。
- 调用 Memory 创建本轮 turn。
- 获取 `session_id`、`run_id`、context。
- 处理 session/run 不存在或归属错误。

### 4.3 Agent 协调

负责：

- 组装 ReactAgent 调用参数。
- 传入 `context_text`、`session_id`、`run_id`。
- 固定传入 `manage_memory=False`。
- 连接统一事件回调。
- 获取 ExecutionResult。

### 4.4 结果协调

负责：

- 识别 ExecutionResult 状态。
- 构建 OutputFeedback。
- 调用 Memory 完成或失败接口。
- 形成 RuntimeResult。
- 标记 waiting_user 的 pending run。

## 5. 一轮普通运行的核心顺序

```text
1. 接收 RuntimeRequest。
2. 校验输入和 session_id。
3. 创建或加载 session。
4. RuntimeMemoryAdapter.begin_turn()。
5. 取得 session_id / run_id / context_text。
6. 创建带外部事件 sink 的 Memory event callback。
7. ReactAgent.run_with_result(
       context_text=context_text,
       session_id=session_id,
       run_id=run_id,
       manage_memory=False,
       event_callback=callback
   )
8. 将 ExecutionResult 转为 OutputFeedback。
9. 按结果状态完成、失败或挂起本轮 Memory turn。
10. 读取需要返回的 timeline。
11. 构建 RuntimeResult。
12. 返回给 CLI/API。
```

## 6. Runtime 不应形成的依赖

以下依赖方向禁止出现：

```text
Analyzer -> Runtime
Planner -> Runtime
ReActExecutor -> Runtime
Tool -> Runtime
Models Provider -> Runtime
Memory storage -> CLI
Memory storage -> API
```

Runtime 可以调用这些层的公开接口，但这些层不应为了 Runtime 入口反向依赖 CLI/API。

## 7. 兼容模式

ReactAgent 当前存在旧兼容模式和正式 Runtime 模式：

```text
旧兼容模式:
  ReactAgent 自行管理短期消息。

正式 Runtime 模式:
  RuntimeMemoryAdapter 管理 user/assistant message、run、event。
  ReactAgent 使用 manage_memory=False。
```

Runtime 只能使用正式 Runtime 模式。旧模式继续保留给既有测试、原型和直接调用方，不能通过 Runtime 自动混用。

## 8. 必须联动检查的代码和文档

开发本模块时必须同时检查：

- `src/memory/runtime_adapter.py`
- `src/memory/session_manager.py`
- `src/agent/orchestrator/react_agent.py`
- `src/agent/react_executor/`
- `src/agent/output_feedback.py`
- Memory、ReactAgent、ReActExecutor 的设计决策和验收文档
- 相关跨层测试

尤其不能只根据本文档假设 ReactAgent 的参数、ReActExecutor 的结果字段或 Memory 的异常行为。

