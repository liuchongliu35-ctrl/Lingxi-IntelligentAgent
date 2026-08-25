# API 层开发步骤与进度（3）- WebSocket 生命周期验收

> 覆盖步骤：Step 13-19  
> 当前状态：Step 13-19 待开发  
> 前置分卷：`API层开发步骤与进度(1)-基础协议.md`、`API层开发步骤与进度(2)-REST路由.md`  
> 上位设计：`API WebSocket流式协议设计.md`、`API本地安全与生命周期设计.md`、`API架构与REST路由设计.md`

本分卷实现 API V1 的 WebSocket 流式协议、本地 server 启动、生命周期安全和最终验收。完成本分卷后，API V1 应能作为 Runtime 的本地 HTTP/WebSocket 入口使用。

---

## Step 13：WebSocket 路径、连接建立与基础消息模型

**状态：待开发**

### 目标

实现 `WS /ws/sessions/{session_id}/runs` 的连接建立、session 边界和基础消息解析。

### 对应设计文档

```text
API WebSocket流式协议设计.md
  ## 1. 路径
  ## 2. 连接语义
  ## 3. 客户端消息
  ## 7. 错误处理

API架构与REST路由设计.md
  ## 4. REST 路由
```

### Runtime 依赖

```text
Runtime.get_session
Runtime.run_stream
Runtime.resume
Runtime.cancel
RuntimeEvent
RuntimeResult
```

### 前置条件

```text
Step 1 create_app 已完成。
Step 3 schemas 已完成。
Step 4 error handler 已完成。
Step 9 REST run 已完成。
```

### 涉及文件

```text
新增/修改:
  src/app/api/websocket.py
  src/app/api/app.py
  src/app/api/schemas.py
  tests/app/api/test_api_websocket_connect.py
```

### 必做

1. 注册路径：

```text
WS /ws/sessions/{session_id}/runs
```

2. 不提供 `WS /ws/runs` 备用路径。
3. 连接绑定一个 session_id。
4. 连接前或连接后校验 session 是否存在，错误返回明确 WebSocket error 或拒绝连接，策略固定并测试。
5. 解析客户端消息类型：

```text
run
resume
cancel
ping
```

6. invalid JSON 或未知 type 返回：

```json
{"type":"error","code":"invalid_message","message":"..."}
```

### 明确不做

```text
不实现 run_stream 执行。
不实现队列。
不实现跨 session 连接复用。
不实现备用 /ws/runs。
```

### 测试与验收

```powershell
python -m pytest tests/app/api/test_api_websocket_connect.py -q
```

测试至少覆盖：

```text
正确路径可连接。
错误路径不可用。
session_not_found 处理策略。
invalid JSON -> error。
unknown type -> error。
ping -> pong。
```

### 完成后回写

记录连接校验策略、错误消息格式、消息 type 列表和测试结果。

---

## Step 14：WebSocket run 流式 event/result 推送

**状态：待开发**

### 目标

实现 WebSocket `type=run`，通过 Runtime.run_stream 推送 event 和最终 result。

### 对应设计文档

```text
API WebSocket流式协议设计.md
  ## 3.1 run
  ## 4.1 event
  ## 4.2 result
  ## 8. 安全与脱敏

API请求响应模型与错误设计.md
  ## 7. 敏感信息
```

### Runtime 依赖

```text
Runtime.run_stream
RuntimeEvent.sequence
RuntimeEvent.event_type
RuntimeEvent.message
RuntimeEvent.visible_to_user
RuntimeResult
```

### 前置条件

```text
Step 13 已完成。
Runtime.run_stream 可用，或测试使用 fake Runtime stream。
```

### 涉及文件

```text
修改:
  src/app/api/websocket.py

新增/修改:
  tests/app/api/test_api_websocket_run.py
```

### 必做

客户端 run 消息：

```json
{
  "type": "run",
  "input": "用户输入",
  "stream": true,
  "debug": false,
  "metadata": {}
}
```

服务端 event：

```json
{
  "type": "event",
  "session_id": "...",
  "run_id": "...",
  "event_type": "tool_started",
  "message": "...",
  "visible_to_user": true,
  "payload": {},
  "sequence": 1
}
```

服务端 result：

```json
{
  "type": "result",
  "session_id": "...",
  "run_id": "...",
  "result": {}
}
```

1. event 按 RuntimeEvent.sequence 推送。
2. result 内容为安全 RuntimeResult。
3. 不推送 raw prompt、hidden reasoning、raw tool result。
4. Runtime 异常转换为 WebSocket `type=error`。

### 明确不做

```text
不在 WebSocket 层直接调用 ReactAgent。
不自己生成 RuntimeEvent。
不实现多 run 队列。
不把 REST API Result 壳用于 event 消息。
```

### 测试与验收

```powershell
python -m pytest tests/app/api/test_api_websocket_run.py -q
```

测试至少覆盖：

```text
run 推送 event。
run 推送 result。
event 顺序正确。
Runtime error -> error message。
敏感字段不推送。
```

### 完成后回写

记录 run 消息格式、event/result 推送格式、脱敏验证和测试结果。

---

## Step 15：WebSocket 同连接串行执行与有限等待队列

**状态：待开发**

### 目标

实现同一个 WebSocket 连接内同一时间只运行一个 run，后续 run 进入有限等待队列。

### 对应设计文档

```text
API WebSocket流式协议设计.md
  ## 2. 连接语义
  ## 4.4 queued
  ## 5. 队列规则

API本地安全与生命周期设计.md
  ## 6. 请求大小和超时
  ## 7. 并发
```

### Runtime 依赖

```text
Runtime.run_stream
API config websocket queue length
```

### 前置条件

```text
Step 14 已完成。
WebSocket run 执行路径可测试。
```

### 涉及文件

```text
修改:
  src/app/api/websocket.py
  src/app/api/config.py

可新增:
  src/app/api/websocket_queue.py

新增/修改:
  tests/app/api/test_api_websocket_queue.py
```

### 必做

1. 每个连接维护自己的队列。
2. 同一个连接同一时间只有一个 running run。
3. 当前 run 未结束时，新 run 进入队列。
4. 返回 queued 消息：

```json
{
  "type": "queued",
  "session_id": "...",
  "queue_position": 1
}
```

5. 队列最大长度有限，例如配置默认 5。
6. 超出队列返回 `queue_full` error。
7. 连接断开时清空未开始队列。
8. 队列只保证当前连接、当前进程有效。

### 明确不做

```text
不实现多进程共享队列。
不持久化 WebSocket 队列。
不跨连接排队。
不强杀当前正在运行的 run。
```

### 测试与验收

```powershell
python -m pytest tests/app/api/test_api_websocket_queue.py -q
```

测试至少覆盖：

```text
第二个 run 进入 queued。
当前 run 完成后执行队列下一项。
queue_position 正确。
queue_full 返回 error。
连接断开清空未开始队列。
不同连接队列互不影响。
```

### 完成后回写

记录队列长度、队列生命周期、断开处理策略和测试结果。

---

## Step 16：WebSocket resume/cancel 与 waiting_user

**状态：待开发**

### 目标

实现 WebSocket 内 waiting_user 后的 resume/cancel 消息处理。

### 对应设计文档

```text
API WebSocket流式协议设计.md
  ## 3.2 resume
  ## 3.3 cancel
  ## 6. waiting_user
  ## 7. 错误处理

API架构与REST路由设计.md
  ## 10. resume / cancel
```

### Runtime 依赖

```text
Runtime.resume
Runtime.cancel
RuntimeResult.status=waiting_user
RuntimeResult.pending_confirmation
RuntimeEvent confirmation_requested
```

### 前置条件

```text
Step 10 REST resume/cancel 已完成。
Step 14 WebSocket run 已完成。
Runtime pending registry 可用。
```

### 涉及文件

```text
修改:
  src/app/api/websocket.py

新增/修改:
  tests/app/api/test_api_websocket_resume_cancel.py
```

### 必做

1. waiting_user 时推送 confirmation_requested event。
2. 推送 result，status 为 waiting_user。
3. 连接保持打开。
4. 客户端可发送 resume：

```json
{
  "type": "resume",
  "run_id": "...",
  "approved": true,
  "confirmation_id": "...",
  "preview_hash": "..."
}
```

5. 客户端可发送 cancel：

```json
{
  "type": "cancel",
  "run_id": "...",
  "reason": "用户取消"
}
```

6. resume/cancel 后继续推送 event 和最终 result。
7. pending context 不存在返回 run_not_found 或 interrupted error。
8. 客户端断开后不承诺重新推送实时事件。

### 明确不做

```text
不跨进程恢复。
不从 SQLite 重建执行上下文。
不强杀正在运行工具。
不在 WebSocket 层绕过 Runtime pending registry。
```

### 测试与验收

```powershell
python -m pytest tests/app/api/test_api_websocket_resume_cancel.py -q
```

测试至少覆盖：

```text
waiting_user 推送 confirmation_requested 和 result。
resume approved 后完成。
resume preview_hash 错误 -> error。
cancel 后返回 cancelled result。
pending context 不存在 -> error。
断开连接后的限制有测试或文档记录。
```

### 完成后回写

记录 waiting_user 推送顺序、resume/cancel 消息行为、断开策略和测试结果。

---

## Step 17：本地 server 启动辅助与安全提示

**状态：待开发**

### 目标

实现 API 本地启动辅助，支持 `python -m src.app.api.server` 或后续 CLI `agent api` 复用，并落实默认绑定 127.0.0.1。

### 对应设计文档

```text
API本地安全与生命周期设计.md
  ## 1. V1 安全定位
  ## 2. 启动方式
  ## 3. Runtime 生命周期
  ## 5. 认证预留

API请求响应模型与错误设计.md
  ## 8. 本地 V1 安全边界
```

### Runtime 依赖

```text
create_app -> RuntimeFactory
Runtime.close via lifespan
```

### 前置条件

```text
Step 1 create_app 已完成。
Step 5 本地安全底座已完成。
REST 与 WebSocket 基础路由已注册。
```

### 涉及文件

```text
新增/修改:
  src/app/api/server.py
  tests/app/api/test_api_server.py
```

### 必做

1. 提供本地启动辅助：

```text
python -m src.app.api.server
```

2. 默认参数：

```text
--host 127.0.0.1
--port 8000
--reload
--debug
```

3. 默认不监听公网。
4. host 非 127.0.0.1 / localhost 时输出本地安全提示。
5. 不在 server import 时启动 uvicorn。
6. 预留后续 CLI `agent api` 调用 server 的函数。

### 明确不做

```text
不实现生产部署脚本。
不默认启用认证。
不把 API 启动塞进 main.py。
不在测试中真实长期启动服务。
```

### 测试与验收

```powershell
python -m pytest tests/app/api/test_api_server.py -q
```

测试至少覆盖：

```text
默认 host 为 127.0.0.1。
import server 不启动 uvicorn。
非本地 host 有安全提示。
server 调用 create_app。
```

### 完成后回写

记录启动命令、默认参数、安全提示策略和测试结果。

---

## Step 18：API 本地安全、并发与生命周期回归

**状态：待开发**

### 目标

回归验证 API 的本地安全边界、Runtime 生命周期、请求限制、日志脱敏和并发约束。

### 对应设计文档

```text
API本地安全与生命周期设计.md
  ## 1. V1 安全定位
  ## 3. Runtime 生命周期
  ## 4. CORS
  ## 5. 认证预留
  ## 6. 请求大小和超时
  ## 7. 并发
  ## 8. 日志
```

### Runtime 依赖

```text
Runtime shared process instance
Runtime pending registry concurrency safety
Runtime.close
```

### 前置条件

```text
Step 13-17 已完成。
```

### 涉及文件

```text
新增/修改:
  tests/app/api/test_api_lifecycle_security_acceptance.py
```

### 必做

1. 验证 API 进程级 Runtime 复用。
2. 验证每个请求不创建第二个 Runtime。
3. 验证 shutdown 调用 Runtime.close。
4. 验证 app/global 不保存当前 session/run。
5. 验证 WebSocket 队列只属于单连接。
6. 验证请求大小/metadata 限制。
7. 验证 CORS 不默认公网 `*`。
8. 验证日志/错误响应不含敏感内容。
9. 如果同 session 并发策略暂不实现全局锁，必须在测试或文档中记录限制。

### 明确不做

```text
不实现完整租户隔离。
不实现生产认证授权。
不做性能压测。
不实现多进程队列共享。
```

### 测试与验收

```powershell
python -m pytest tests/app/api/test_api_lifecycle_security_acceptance.py -q
```

测试至少覆盖：

```text
Runtime 复用。
close 调用。
敏感字段脱敏。
请求限制。
CORS 默认。
WebSocket 队列隔离。
同 session 并发限制说明。
```

### 完成后回写

记录安全回归结果、并发限制、生命周期行为和后续生产化待办。

---

## Step 19：API V1 最终验收与文档回写

**状态：待开发**

### 目标

完成 API V1 文档回写和最终验收，确认 API 可以作为 Runtime 的本地 HTTP/WebSocket 入口。

### 对应设计文档

```text
API架构与REST路由设计.md
API请求响应模型与错误设计.md
API WebSocket流式协议设计.md
API本地安全与生命周期设计.md
```

### Runtime 依赖

```text
Runtime V1 核心能力完成。
RuntimeResult / RuntimeEvent 字段稳定。
Runtime REST facade 和 stream facade 稳定。
```

### 前置条件

```text
Step 0-18 已完成。
```

### 涉及文件

```text
修改:
  src/app/api/API层开发步骤与进度.md
  src/app/api/API层开发步骤与进度(1)-基础协议.md
  src/app/api/API层开发步骤与进度(2)-REST路由.md
  src/app/api/API层开发步骤与进度(3)-WebSocket生命周期验收.md

可新增:
  src/app/api/API V1交接说明.md
```

### 必做

最终验收至少覆盖：

```text
1. GET /health。
2. POST /sessions。
3. GET /sessions。
4. GET /sessions/{session_id}。
5. GET /sessions/{session_id}/timeline。
6. POST /sessions/{session_id}/runs。
7. POST /sessions/{session_id}/resume。
8. POST /sessions/{session_id}/cancel。
9. DELETE /sessions/{session_id}。
10. GET /runs/{run_id}。
11. GET /sessions/{session_id}/export。
12. WS /ws/sessions/{session_id}/runs 推送 event/result。
13. WebSocket queued / queue_full。
14. WebSocket waiting_user resume/cancel。
15. API Result 统一包装。
16. HTTP 状态码映射。
17. 本地安全默认值。
18. Runtime 生命周期 close。
```

### 明确不做

```text
不实现公网生产部署。
不默认启用认证。
不测试 CLI。
不调用真实外部模型，除非用户明确要求整体真实验收。
```

### 测试与验收

建议最终命令：

```powershell
python -m pytest tests/app/api -q
python -m pytest tests/app/runtime -q
```

如果进入最小本地服务验收，可追加：

```powershell
python -m src.app.api.server --host 127.0.0.1 --port 8000
```

并用测试客户端或脚本验证：

```text
GET /health
POST /sessions
POST /sessions/{session_id}/runs
WS /ws/sessions/{session_id}/runs
```

如果某些测试因真实 Provider、平台差异或迁移期问题失败，必须记录：

```text
失败测试
失败原因
是否与 API 修改相关
是否需要后续修复
临时验收口径
```

### 完成后回写

记录 API V1 最终状态、测试总览、已知限制、后续生产化安全事项。

