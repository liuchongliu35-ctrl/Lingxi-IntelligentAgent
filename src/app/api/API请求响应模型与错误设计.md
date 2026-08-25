# API 请求响应模型与错误设计

## 1. API Result

所有 REST 路由返回统一包装体：

```text
success: bool
data: dict | list | str | null
error: str | null
code: str | null
trace_id: str | null
session_id: str | null
run_id: str | null
metadata: dict
```

WebSocket 不使用该 REST 壳，但错误消息结构要保持 code/message 语义一致。

## 2. Result 示例

成功：

```json
{
  "success": true,
  "data": {
    "status": "completed",
    "output": "..."
  },
  "error": null,
  "code": null,
  "trace_id": "...",
  "session_id": "...",
  "run_id": "...",
  "metadata": {}
}
```

失败：

```json
{
  "success": false,
  "data": null,
  "error": "Session not found",
  "code": "session_not_found",
  "trace_id": "...",
  "session_id": "...",
  "run_id": null,
  "metadata": {}
}
```

## 3. Pydantic schema

建议定义：

```text
CreateSessionRequest
CreateSessionResponseData
RunRequest
ResumeRequest
CancelRequest
SessionData
TimelineData
RuntimeResultData
HealthData
ApiResult
```

Schema 层只描述 API 协议，不要 import ReActExecutor 的内部 context 类型。

## 4. HTTP 状态码

```text
completed / success       -> 200
waiting_user              -> 202
request_replan            -> 202
validation_error          -> 400
session_conflict          -> 409
session_not_found         -> 404
run_not_found             -> 404
blocked_by_policy         -> 403
cancelled                 -> 409
interrupted               -> 409
memory_unavailable        -> 503
dependency_init_failed    -> 503
export_failed             -> 500
internal_error            -> 500
```

FastAPI exception handler 应统一包装异常，避免返回默认 HTML 或不一致的错误 JSON。

## 5. trace_id

每个 HTTP 请求建议生成一个 trace_id。

trace_id 用于：

- API Result。
- Runtime metadata。
- 日志关联。

trace_id 不替代 session_id 或 run_id。

## 6. validation

API 层负责请求体和路径参数基础校验：

- input 非空。
- session_id 格式。
- metadata 是对象。
- debug 是布尔值。
- run_id 格式。

更深层的 session/run 归属校验由 Runtime 统一处理。

## 7. 敏感信息

API 不返回：

- raw prompt。
- hidden reasoning。
- raw tool result。
- raw observation。
- API Key、Token、Cookie、密码。
- 未脱敏的环境变量。

即使 debug=true，也只返回安全诊断摘要。

## 8. 本地 V1 安全边界

V1 不做认证，按本地运行系统设计。

但仍应：

- 默认绑定 `127.0.0.1`。
- 不默认监听公网地址。
- 保留未来 API Key middleware 的结构空间。
- 在启动日志里提示当前无认证，仅适合本地使用。

## 9. 必须联动阅读

- Runtime 错误码和 RuntimeResult 设计。
- Memory ID 校验和 session 规则。
- Models/Tools 错误码，确认如何映射到底层 status。

