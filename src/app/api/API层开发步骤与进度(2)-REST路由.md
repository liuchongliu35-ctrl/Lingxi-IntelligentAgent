# API 层开发步骤与进度（2）- REST 路由

> 覆盖步骤：Step 6-12  
> 当前状态：Step 6-12 待开发  
> 前置分卷：`API层开发步骤与进度(1)-基础协议.md`  
> 上位设计：`API架构与REST路由设计.md`、`API请求响应模型与错误设计.md`

本分卷实现 API V1 的 REST 路由。所有路由必须通过 Runtime dependency 调用 Runtime，不直接访问 Memory、ReactAgent、ReActExecutor、Models 或 Tools。

---

## Step 6：GET /health

**状态：待开发**

### 目标

实现 `GET /health`，返回 Runtime 聚合的依赖健康状态。

### 对应设计文档

```text
API架构与REST路由设计.md
  ## 4. REST 路由

API请求响应模型与错误设计.md
  ## 1. API Result
  ## 3. Pydantic schema
  ## 4. HTTP 状态码

API本地安全与生命周期设计.md
  ## 3. Runtime 生命周期
```

### Runtime 依赖

```text
Runtime.health
HealthData
RuntimeErrorCode.dependency_init_failed
```

### 前置条件

```text
Step 1 create_app 已完成。
Step 3 API Result / schema 已完成。
Step 4 error handler 已完成。
Runtime.health 可用，或测试使用 fake Runtime。
```

### 涉及文件

```text
新增/修改:
  src/app/api/routes_health.py
  src/app/api/app.py
  tests/app/api/test_api_health.py
```

### 必做

1. 注册 `GET /health`。
2. 调用 Runtime.health。
3. 返回 API Result。
4. healthy 返回 200。
5. degraded/unavailable 的 HTTP 状态码按 Runtime 映射或固定策略处理，并写入测试。
6. 不泄露敏感配置。

### 明确不做

```text
不在 API health 中直接调用 Models/Tools。
不执行有副作用的工具探活。
不绕过 Runtime。
```

### 测试与验收

```powershell
python -m pytest tests/app/api/test_api_health.py -q
```

测试至少覆盖：

```text
healthy 响应。
degraded 响应。
dependency_init_failed 响应。
API Result 字段完整。
敏感字段不返回。
```

### 完成后回写

记录 health 路由响应结构、状态码策略和测试结果。

---

## Step 7：sessions 创建、列表与详情路由

**状态：待开发**

### 目标

实现 `POST /sessions`、`GET /sessions`、`GET /sessions/{session_id}`，固定 API 的 session 资源模型。

### 对应设计文档

```text
API架构与REST路由设计.md
  ## 5. POST /sessions
  ## 6. GET /sessions
  ## 7. GET /sessions/{session_id}

API请求响应模型与错误设计.md
  ## 3. Pydantic schema
  ## 4. HTTP 状态码
  ## 6. validation
```

### Runtime 依赖

```text
Runtime.create_session 或 Runtime session facade（如已提供）
Runtime.list_sessions
Runtime.get_session
RuntimeErrorCode.session_conflict
RuntimeErrorCode.session_not_found
```

### 前置条件

```text
Step 6 已完成。
Runtime session facade 可用，或测试使用 fake Runtime。
```

### 涉及文件

```text
新增/修改:
  src/app/api/routes_sessions.py
  src/app/api/schemas.py
  src/app/api/app.py
  tests/app/api/test_api_sessions.py
```

### 必做

1. `POST /sessions` 请求：

```json
{
  "session_id": null,
  "title": "项目开发会话",
  "metadata": {}
}
```

2. 不传 session_id 时由系统/Runtime 创建。
3. 传入 session_id 时校验格式。
4. session_id 已存在时 API 不等待 y/n，返回 `409 session_conflict`，data 中带已有 session 摘要。
5. `GET /sessions` 返回列表，默认字段包含：

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

6. 预留 limit、offset、status 参数。
7. `GET /sessions/{session_id}` 不存在返回 404。
8. 所有返回使用 API Result。

### 明确不做

```text
不在 API 中做 y/n 交互。
不直接调用 SQLite。
不让 API 自己生成数据库实体 ID，除非 Runtime 明确暴露此能力。
不实现复杂分页，除非 Runtime 已支持。
```

### 测试与验收

```powershell
python -m pytest tests/app/api/test_api_sessions.py -q
```

测试至少覆盖：

```text
POST /sessions 创建成功。
session_id 冲突 -> 409 session_conflict。
GET /sessions 列表字段。
GET /sessions/{id} 成功。
GET /sessions/{id} 不存在 -> 404。
metadata 类型校验。
```

### 完成后回写

记录 session 创建 facade、冲突策略、列表字段和测试结果。

---

## Step 8：timeline 路由

**状态：待开发**

### 目标

实现 `GET /sessions/{session_id}/timeline`，返回 Memory 已映射和脱敏后的 timeline。

### 对应设计文档

```text
API架构与REST路由设计.md
  ## 8. GET /sessions/{session_id}/timeline

API请求响应模型与错误设计.md
  ## 7. 敏感信息
```

### Runtime 依赖

```text
Runtime.get_timeline
TimelineData
RuntimeErrorCode.session_not_found
```

### 前置条件

```text
Step 7 已完成。
Runtime.get_timeline 可用。
```

### 涉及文件

```text
修改:
  src/app/api/routes_sessions.py
  src/app/api/schemas.py

新增/修改:
  tests/app/api/test_api_timeline.py
```

### 必做

1. 注册 `GET /sessions/{session_id}/timeline`。
2. 查询参数预留：

```text
limit
after
before
include_events=true
```

3. 默认只返回用户可见内容。
4. 返回 Runtime/Memory 已脱敏 timeline。
5. 不存在 session 返回 404。
6. 不返回 raw prompt、hidden reasoning、raw tool result。

### 明确不做

```text
不重新组合数据库 messages/events。
不把内部事件补回返回。
不直接访问 SessionManager.repo。
```

### 测试与验收

```powershell
python -m pytest tests/app/api/test_api_timeline.py -q
```

测试至少覆盖：

```text
timeline 成功返回。
只包含可见事件。
敏感字段不返回。
session_not_found -> 404。
query 参数基础校验。
```

### 完成后回写

记录 timeline query 参数、返回结构、脱敏策略和测试结果。

---

## Step 9：POST /sessions/{session_id}/runs

**状态：待开发**

### 目标

实现 API 最核心的同步 run 路由，把用户输入传给 Runtime.run，并返回 RuntimeResult。

### 对应设计文档

```text
API架构与REST路由设计.md
  ## 9. POST /sessions/{session_id}/runs

API请求响应模型与错误设计.md
  ## 3. Pydantic schema
  ## 4. HTTP 状态码
  ## 6. validation
  ## 7. 敏感信息
```

### Runtime 依赖

```text
Runtime.run
RuntimeResult.status
RuntimeResult.session_id
RuntimeResult.run_id
RuntimeResult.output
RuntimeResult.pending_confirmation
RuntimeResult.request_replan
```

### 前置条件

```text
Step 7 已完成。
Runtime.run 可用。
```

### 涉及文件

```text
新增/修改:
  src/app/api/routes_runs.py
  src/app/api/schemas.py
  src/app/api/app.py
  tests/app/api/test_api_runs.py
```

### 必做

1. 请求体：

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

2. 路径 `session_id` 作为会话边界传给 Runtime。
3. `stream=false` 同步返回 JSON。
4. 如果 `stream=true`，V1 应提示使用 WebSocket 或仍按同步处理但明确文档和测试；不得在此路由伪造长连接流式。
5. completed -> 200。
6. waiting_user / request_replan -> 202。
7. validation_error -> 400。
8. session_not_found -> 404。
9. blocked_by_policy -> 403。
10. 返回 API Result，data 中为安全 RuntimeResult。

### 明确不做

```text
不在 REST run 中做 WebSocket 流式。
不直接调用 ReactAgent。
不直接构建 context。
不返回 raw prompt 或 hidden reasoning。
```

### 测试与验收

```powershell
python -m pytest tests/app/api/test_api_runs.py -q
```

测试至少覆盖：

```text
普通 completed -> 200。
waiting_user -> 202。
request_replan -> 202。
validation_error -> 400。
session_not_found -> 404。
blocked_by_policy -> 403。
RuntimeResult 安全包装。
```

### 完成后回写

记录 run 请求/响应结构、stream=true 处理口径、状态码和测试结果。

---

## Step 10：resume / cancel REST 路由

**状态：待开发**

### 目标

实现 `POST /sessions/{session_id}/resume` 和 `POST /sessions/{session_id}/cancel`，支持当前进程内 pending confirmation 的确认恢复和取消。

### 对应设计文档

```text
API架构与REST路由设计.md
  ## 10. resume / cancel

API请求响应模型与错误设计.md
  ## 4. HTTP 状态码
  ## 6. validation
```

### Runtime 依赖

```text
Runtime.resume
Runtime.cancel
RuntimeResult.status
RuntimeResult.pending_confirmation
RuntimeErrorCode.run_not_found
RuntimeErrorCode.interrupted
RuntimeErrorCode.cancelled
```

### 前置条件

```text
Step 9 已完成。
Runtime.resume / Runtime.cancel 可用。
```

### 涉及文件

```text
修改:
  src/app/api/routes_runs.py
  src/app/api/schemas.py

新增/修改:
  tests/app/api/test_api_resume_cancel.py
```

### 必做

`POST /sessions/{session_id}/resume` 请求：

```json
{
  "run_id": "...",
  "approved": true,
  "reason": "",
  "confirmation_id": "...",
  "preview_hash": "..."
}
```

`POST /sessions/{session_id}/cancel` 请求：

```json
{
  "run_id": "...",
  "reason": "用户取消"
}
```

1. V1 只支持当前进程内 pending confirmation 恢复。
2. cancel 只取消 waiting_user run，不强杀正在运行线程或工具。
3. run_id 缺失或格式错误返回 400。
4. pending context 不存在返回 run_not_found 或 interrupted。
5. cancelled 返回 409。
6. 所有返回使用 API Result。

### 明确不做

```text
不支持跨进程断点续跑。
不从 SQLite 重建执行上下文。
不强制中断运行中的工具。
不在 API 中直接调用 ReActExecutor.resume_after_confirmation。
```

### 测试与验收

```powershell
python -m pytest tests/app/api/test_api_resume_cancel.py -q
```

测试至少覆盖：

```text
resume approved 成功。
resume rejected 成功返回安全结果。
confirmation_id/preview_hash 缺失或错误。
pending context 不存在。
cancel waiting_user。
cancel 非 waiting_user 不强杀。
```

### 完成后回写

记录 resume/cancel 请求结构、错误码、状态码和测试结果。

---

## Step 11：delete、run 查询与 export REST 路由

**状态：待开发**

### 目标

实现 `DELETE /sessions/{session_id}`、`GET /runs/{run_id}`、`GET /sessions/{session_id}/export`。

### 对应设计文档

```text
API架构与REST路由设计.md
  ## 11. DELETE /sessions/{session_id}
  ## 12. GET /runs/{run_id}
  ## 13. GET /sessions/{session_id}/export

API请求响应模型与错误设计.md
  ## 4. HTTP 状态码
  ## 7. 敏感信息
```

### Runtime 依赖

```text
Runtime.delete_session
Runtime.get_run 或 Runtime run facade（如已提供）
Runtime.export_session
RuntimeErrorCode.session_not_found
RuntimeErrorCode.run_not_found
RuntimeErrorCode.export_failed
```

### 前置条件

```text
Step 7-10 已完成。
Runtime delete/export/run 查询 facade 可用，或先基于 fake Runtime 测试。
```

### 涉及文件

```text
修改:
  src/app/api/routes_sessions.py
  src/app/api/routes_runs.py
  src/app/api/schemas.py

新增/修改:
  tests/app/api/test_api_delete_export_runs.py
```

### 必做

1. `DELETE /sessions/{session_id}` V1 硬删除。
2. API 不做 y/n 交互。
3. 如果采用 `confirm=true`，缺少 confirm 返回 400。
4. `GET /runs/{run_id}` 返回 run 摘要状态。
5. 如果 Runtime 暂无 run 查询 facade，不在 API 里直连 repo；应标记阻塞或先补 Runtime facade。
6. `GET /sessions/{session_id}/export?format=markdown` 返回 Markdown 内容或 API Result data 中的 content/filename/content_type。
7. export 不返回内部事件和敏感字段。

### 明确不做

```text
不实现软删除。
不直接调用 SQLite。
不实现文件下载高级响应，除非设计补充。
不导出 raw prompt / raw tool result。
```

### 测试与验收

```powershell
python -m pytest tests/app/api/test_api_delete_export_runs.py -q
```

测试至少覆盖：

```text
DELETE 成功。
DELETE 缺少 confirm 时的固定策略。
GET /runs/{run_id} 成功。
run_not_found -> 404。
export Markdown 成功。
export 不含敏感字段。
export_failed -> 500。
```

### 完成后回写

记录 DELETE confirm 策略、run 查询 facade、export 返回形式和测试结果。

---

## Step 12：REST API 集成验收

**状态：待开发**

### 目标

完成 REST 路由层整体验收，确认 REST API 可以通过 Runtime 完成 session、run、timeline、resume、cancel、delete、export 和 health。

### 对应设计文档

```text
API架构与REST路由设计.md
  ## 4. REST 路由

API请求响应模型与错误设计.md
  ## 1. API Result
  ## 4. HTTP 状态码
```

### Runtime 依赖

```text
Runtime REST 全部 facade
RuntimeResult
RuntimeErrorCode
```

### 前置条件

```text
Step 6-11 已完成。
```

### 涉及文件

```text
新增/修改:
  tests/app/api/test_api_rest_acceptance.py
```

### 必做

至少覆盖：

```text
1. GET /health。
2. POST /sessions。
3. session_id 冲突 -> 409。
4. GET /sessions。
5. GET /sessions/{session_id}。
6. POST /sessions/{session_id}/runs。
7. waiting_user -> 202。
8. resume / cancel。
9. GET /sessions/{session_id}/timeline。
10. GET /runs/{run_id}。
11. GET /sessions/{session_id}/export。
12. DELETE /sessions/{session_id}。
13. validation / not_found / blocked / memory_unavailable 状态码。
```

### 明确不做

```text
不测试 WebSocket。
不启动真实 uvicorn 服务。
不调用真实外部模型，除非用户明确要求整体真实验收。
```

### 测试与验收

```powershell
python -m pytest tests/app/api/test_api_rest_acceptance.py -q
python -m pytest tests/app/api/test_api_health.py tests/app/api/test_api_sessions.py tests/app/api/test_api_runs.py tests/app/api/test_api_resume_cancel.py -q
```

完成标准：

```text
REST 路由统一使用 API Result。
所有路由通过 Runtime dependency。
状态码映射稳定。
敏感字段不返回。
```

### 完成后回写

记录 REST 验收结果、失败清单、Runtime 偏差和进入 WebSocket 分卷前置状态。

