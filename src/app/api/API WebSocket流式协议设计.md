# API WebSocket 流式协议设计

## 1. 路径

V1 使用：

```text
WS /ws/sessions/{session_id}/runs
```

客户端必须先拥有 session_id。没有 session_id 时先调用：

```text
POST /sessions
```

V1 不提供 `WS /ws/runs` 备用路径。

## 2. 连接语义

一个 WebSocket 连接绑定一个 session。

```text
同一个连接
  -> 可以发送多条 type=run
  -> 同一时间只执行一个 run
  -> 新 run 可进入该连接内等待队列
```

队列只在当前连接和当前进程内有效。进程重启或连接断开后，不承诺队列恢复。

## 3. 客户端消息

### 3.1 run

```json
{
  "type": "run",
  "input": "用户输入",
  "stream": true,
  "debug": false,
  "metadata": {}
}
```

### 3.2 resume

```json
{
  "type": "resume",
  "run_id": "...",
  "approved": true,
  "confirmation_id": "...",
  "preview_hash": "..."
}
```

### 3.3 cancel

```json
{
  "type": "cancel",
  "run_id": "...",
  "reason": "用户取消"
}
```

### 3.4 ping

可以预留：

```json
{
  "type": "ping"
}
```

服务端返回 pong。

## 4. 服务端消息

### 4.1 event

```json
{
  "type": "event",
  "session_id": "...",
  "run_id": "...",
  "event_type": "tool_started",
  "message": "正在读取文件",
  "visible_to_user": true,
  "payload": {},
  "sequence": 1
}
```

### 4.2 result

```json
{
  "type": "result",
  "session_id": "...",
  "run_id": "...",
  "result": {}
}
```

`result` 内容是安全序列化后的 RuntimeResult。

### 4.3 error

```json
{
  "type": "error",
  "code": "runtime_error",
  "message": "...",
  "session_id": "...",
  "run_id": "..."
}
```

### 4.4 queued

当当前连接已有 run 正在执行，新的 run 可以进入队列：

```json
{
  "type": "queued",
  "session_id": "...",
  "queue_position": 1
}
```

### 4.5 pong

```json
{
  "type": "pong"
}
```

## 5. 队列规则

1. 每个连接维护自己的队列。
2. 同一个连接同一时间只有一个 running run。
3. 队列最大长度必须有限，例如 5 或由 Runtime/API 配置控制。
4. 超出队列长度返回 error。
5. 连接断开时清空未开始队列。
6. 正在执行的 run 是否继续由 API 层实现策略决定；V1 可以先等待 Runtime 自然结束。

## 6. waiting_user

当 run 进入 waiting_user：

1. 服务端推送 `event_type=confirmation_requested`。
2. 服务端推送 `result`，其中 status 为 `waiting_user`。
3. 连接保持打开。
4. 客户端发送 `resume` 或 `cancel`。
5. Runtime 继续执行或取消。

如果客户端断开：

- pending context 仍在 Runtime 进程内保留到过期。
- 客户端可以通过 REST resume 尝试恢复，但仅限当前进程内 pending registry 仍存在。

## 7. 错误处理

常见错误：

```text
invalid_message
validation_error
session_not_found
run_not_found
queue_full
runtime_error
internal_error
```

WebSocket 错误不使用 HTTP 状态码作为主语义。连接建立后的错误通过 `type=error` 发送。

## 8. 安全与脱敏

WebSocket 推送和 REST 返回遵循同一套 Runtime 序列化规则。

不能因为是内部本地连接就推送：

- raw prompt。
- hidden reasoning。
- raw tool result。
- 未脱敏命令输出。
- 密钥和认证信息。

## 9. 必须联动阅读

- RuntimeEvent 设计。
- Runtime pending registry 和确认恢复设计。
- FastAPI WebSocket 行为。
- ReActExecutor event callback 和 waiting_user 状态。

