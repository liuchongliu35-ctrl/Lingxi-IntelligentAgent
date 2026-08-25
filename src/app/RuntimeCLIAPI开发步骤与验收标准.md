# Runtime / CLI / API 开发步骤与验收标准

## 1. 开发顺序

V1 同一轮设计覆盖 Runtime、CLI、API，但开发按以下顺序推进：

```text
阶段 1：Runtime 核心 + 单元测试
阶段 2：CLI + Runtime 集成测试
阶段 3：API REST + Runtime 集成测试
阶段 4：WebSocket 最小流式 + 端到端测试
阶段 5：main.py 收口 + 文档回写
```

每个阶段完成后运行本阶段测试和受影响层回归测试。

## 2. 阶段 1：Runtime 核心

### 2.1 目标

实现 Runtime 的核心能力：

- 依赖装配。
- RuntimeRequest / RuntimeResult / RuntimeEvent。
- 普通 run。
- Memory begin/complete/fail。
- ReactAgent 正式 Runtime 模式。
- 事件回调。
- 错误包装。
- health。
- session/timeline/list/delete/export facade。

### 2.2 开发任务

1. 建立 contracts。
2. 建立 errors。
3. 建立 serialization。
4. 建立 factory。
5. 建立 Runtime core。
6. 接入 RuntimeMemoryAdapter。
7. 接入 ReactAgent。
8. 接入 OutputFeedbackProcessor。
9. 实现 pending registry。
10. 实现 health。
11. 实现 Markdown export。

### 2.3 测试

新增测试建议：

```text
tests/app/runtime/test_runtime_contracts.py
tests/app/runtime/test_runtime_factory.py
tests/app/runtime/test_runtime_run.py
tests/app/runtime/test_runtime_events.py
tests/app/runtime/test_runtime_resume_cancel.py
tests/app/runtime/test_runtime_health_export.py
```

至少覆盖：

- 新 session 普通对话。
- 指定 session 多轮对话。
- `manage_memory=False` 不重复写 user/assistant message。
- 可见事件进入 timeline。
- 内部事件不进入普通 timeline。
- waiting_user 和 pending_confirmation。
- cancel waiting_user。
- blocked_by_policy。
- request_replan。
- Memory 持久化失败 warning。
- dependency init failed。

### 2.4 回归测试

至少运行：

- Memory Runtime adapter 测试。
- Memory V1 end-to-end 测试。
- ReactAgent 与 ReActExecutor 集成测试。
- ReActExecutor events / confirmation 测试。
- OutputFeedback 测试。

## 3. 阶段 2：CLI

### 3.1 目标

实现 Typer CLI：

- chat 单次输入。
- chat REPL。
- 指定 session 继续对话。
- 流式展示可见事件。
- waiting_user 确认交互。
- sessions / session show / timeline。
- health。
- export。
- delete-session。
- JSON 输出。
- 退出码。

### 3.2 测试

新增测试建议：

```text
tests/app/cli/test_cli_chat.py
tests/app/cli/test_cli_sessions.py
tests/app/cli/test_cli_timeline.py
tests/app/cli/test_cli_confirmation.py
tests/app/cli/test_cli_json_exit_codes.py
```

至少覆盖：

- `agent chat "输入"` 创建新 session。
- `agent chat --session-id <id> "输入"` 继续旧 session。
- `agent chat` REPL 中多轮同 session。
- 默认不复用上一次 session。
- 流式事件展示。
- JSON 输出结构。
- waiting_user 下 y/n 确认。
- sessions 表格字段。
- timeline 回放。
- 错误退出码。

## 4. 阶段 3：API REST

### 4.1 目标

实现 FastAPI REST：

- create_app。
- Runtime dependency。
- API Result。
- `/health`。
- `/sessions`。
- `/sessions/{session_id}`。
- `/sessions/{session_id}/timeline`。
- `/sessions/{session_id}/runs`。
- resume / cancel。
- delete。
- export。
- error handler。

### 4.2 测试

新增测试建议：

```text
tests/app/api/test_api_sessions.py
tests/app/api/test_api_runs.py
tests/app/api/test_api_errors.py
tests/app/api/test_api_health_export.py
```

至少覆盖：

- 创建 session。
- session_id 冲突返回 409，不等待 y/n。
- session 列表。
- 发起 run。
- waiting_user 返回 202。
- validation_error 返回 400。
- session_not_found 返回 404。
- blocked_by_policy 返回 403。
- delete 硬删除。
- export Markdown。

## 5. 阶段 4：WebSocket

### 5.1 目标

实现：

- `WS /ws/sessions/{session_id}/runs`。
- type=run。
- type=resume。
- type=cancel。
- event 推送。
- result 推送。
- error 推送。
- 同连接串行执行。
- 有限等待队列。

### 5.2 测试

新增测试建议：

```text
tests/app/api/test_api_websocket.py
```

至少覆盖：

- WebSocket run 推送 event 和 result。
- 同连接第二条 run 进入 queued。
- queue full 返回 error。
- waiting_user 后 resume。
- invalid message 返回 error。
- session_not_found 处理。

## 6. 阶段 5：main.py 收口和文档回写

### 6.1 目标

- 根入口 `main.py` 只保留薄启动器。
- 文档更新真实实现路径。
- 开发进度文档记录完成情况。
- 明确已知限制。

### 6.2 验收

最小端到端：

1. CLI 发起新 session 普通对话。
2. CLI 指定 session 多轮对话。
3. CLI 查看 sessions 和 timeline。
4. API 创建 session。
5. API 发起 run。
6. WebSocket 推送 event 和 result。
7. 可见事件进入 timeline。
8. 内部事件不进入 timeline。
9. waiting_user / pending_confirmation 可恢复或取消。
10. Memory 持久化失败时返回 warning。
11. health 能展示依赖状态。
12. main.py 能启动 CLI。

## 7. 跨层修改原则

如果发现 Runtime 与其他层不适配：

1. 先确认真实接口和测试。
2. 优先在 Runtime 适配。
3. 如果 Runtime 无法合理适配，再小范围修改对应层公开接口。
4. 修改其他层必须运行该层相关测试。
5. 不做大范围重构。
6. 不破坏 ReactAgent 旧兼容模式。
7. 不让 Analyzer、Planner、ReActExecutor、Tools 直接依赖 Memory 或 Runtime。

## 8. 需要持续检查的高风险点

1. ReactAgent 是否真的以 `manage_memory=False` 运行。
2. user/assistant message 是否重复写入。
3. event callback 是否导致事件重复保存。
4. waiting_user 是否能保留原始执行上下文。
5. resume 是否错误尝试跨进程续跑。
6. RuntimeResult 是否暴露内部对象。
7. WebSocket 队列是否无界。
8. API 是否默认监听公网。
9. delete/export 是否绕过 Memory 或路径安全。
10. health 是否触发昂贵或有副作用的真实调用。

