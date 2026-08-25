# API 架构与 REST 路由设计

## 1. 定位

API 是 Runtime 的 HTTP / WebSocket 适配器。API 负责请求响应协议、HTTP 状态码、WebSocket 连接和本地服务生命周期，不负责 Agent 主流程。

V1 使用 FastAPI。

## 2. 建议目录

```text
src/app/api/
  __init__.py
  app.py              # create_app()
  routes_sessions.py
  routes_runs.py
  routes_health.py
  websocket.py
  schemas.py
  result.py
  dependencies.py     # 获取进程级 Runtime
  error_handlers.py
  server.py           # 本地 uvicorn 启动辅助
```

## 3. FastAPI 生命周期

```text
create_app()
  -> RuntimeFactory.build()
  -> Runtime 初始化和 recover_interrupted_runs()
  -> app.state.runtime = runtime

shutdown
  -> runtime.close()
```

所有路由通过 dependency 获取同一个进程级 Runtime。

## 4. REST 路由

V1 核心路由：

```text
GET  /health
POST /sessions
GET  /sessions
GET  /sessions/{session_id}
GET  /sessions/{session_id}/timeline
POST /sessions/{session_id}/runs
POST /sessions/{session_id}/resume
POST /sessions/{session_id}/cancel
DELETE /sessions/{session_id}
GET  /runs/{run_id}
GET  /sessions/{session_id}/export
```

流式 run 使用 WebSocket：

```text
WS /ws/sessions/{session_id}/runs
```

## 5. POST /sessions

请求：

```json
{
  "session_id": null,
  "title": "项目开发会话",
  "metadata": {}
}
```

行为：

1. 不传 session_id 时系统生成。
2. 传入 session_id 时校验格式。
3. 如果不存在，可以创建。
4. 如果已存在，API 不进行 y/n 交互。
5. 返回 `409 session_conflict`，并在 data 中返回已有 session 摘要。
6. 客户端如需进入已有 session，应直接调用 GET 或后续 run 路由。

CLI 可以做交互确认，API 不应该阻塞等待人类输入。

## 6. GET /sessions

返回 session 列表，默认字段：

```text
session_id
title
status
created_at
last_activity_at
message_count
last_run_status
metadata
```

可以预留分页参数：

```text
limit
offset
status
```

V1 即使暂不完整实现分页，也要避免接口设计只能返回无限列表。

## 7. GET /sessions/{session_id}

返回指定 session 的安全结构。

不存在：

```text
404 session_not_found
```

## 8. GET /sessions/{session_id}/timeline

返回 Memory 已映射和脱敏后的 timeline。

查询参数可预留：

```text
limit
after
before
include_events=true
```

默认只返回用户可见内容。

## 9. POST /sessions/{session_id}/runs

请求：

```json
{
  "input": "用户输入",
  "stream": false,
  "debug": false,
  "metadata": {},
  "model_profile": null,
  "agent_version": null
}
```

行为：

- `stream=false` 时同步返回 RuntimeResult。
- 如果请求希望流式，V1 推荐使用 WebSocket，不在此路由内做长连接流式。
- 路由必须把 `session_id` 作为会话边界传给 Runtime。

返回：

- 200 completed。
- 202 waiting_user / request_replan。
- 4xx/5xx 按错误映射。

## 10. resume / cancel

### POST /sessions/{session_id}/resume

请求：

```json
{
  "run_id": "...",
  "approved": true,
  "reason": "",
  "confirmation_id": "...",
  "preview_hash": "..."
}
```

V1 只支持当前进程内 pending confirmation 恢复。

### POST /sessions/{session_id}/cancel

请求：

```json
{
  "run_id": "...",
  "reason": "用户取消"
}
```

V1 只取消 waiting_user 的 run，不强杀正在运行的工具或线程。

## 11. DELETE /sessions/{session_id}

V1 硬删除。API 不做 y/n 交互，但删除操作必须使用明确的 DELETE 方法。

可选请求头或 query：

```text
confirm=true
```

如果设计为必须 confirm，缺少 confirm 返回 400 validation_error。

## 12. GET /runs/{run_id}

返回 run 摘要状态。

如果 Memory V1 暂无直接按 run_id 查询的公开 Runtime facade，可以在 Runtime 中通过 SessionManager 的公开接口适配，不能在 API 里直连 repo。

## 13. GET /sessions/{session_id}/export

V1 导出 Markdown。

可选：

```text
format=markdown
```

返回：

- 可以直接返回 Markdown 文本。
- 或返回 API Result，data 中包含 content、filename、content_type。

如果后续提供文件下载，再单独定义响应类型。

## 14. 必须联动阅读

开发 API 前必须阅读：

- Runtime 公共契约和错误设计。
- Memory session/timeline/delete/export 可用接口。
- FastAPI 当前项目依赖情况。
- Tools 文件/命令安全边界，因为 API 能间接触发工具。

