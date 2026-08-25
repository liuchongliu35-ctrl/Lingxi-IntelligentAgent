# API 本地安全与生命周期设计

## 1. V1 安全定位

V1 API 只面向本地开发运行，不面向公网开放。

默认：

```text
host = 127.0.0.1
auth = disabled
```

但这不代表可以忽略安全边界，因为 API 能间接触发工具、文件写入和命令执行。

## 2. 启动方式

建议提供：

```text
agent api
```

或薄启动模块：

```text
python -m src.app.api.server
```

启动参数可包括：

```text
--host 127.0.0.1
--port 8000
--reload
--debug
```

如果 host 不是 127.0.0.1，V1 应显示醒目的本地安全提示。

## 3. Runtime 生命周期

```text
startup
  -> create Runtime
  -> recover interrupted runs
  -> register on app.state

request
  -> get Runtime from app.state
  -> call Runtime

shutdown
  -> runtime.close()
```

不能每个请求重建 Runtime，也不能在全局变量里保存当前 session/run。

## 4. CORS

V1 如需支持本地前端，可以允许本地 origin：

```text
http://127.0.0.1:*
http://localhost:*
```

默认不应开放 `*` 到公网场景。完整 CORS 策略后续单独设计。

## 5. 认证预留

V1 不启用 API Key。

但代码结构应预留：

```text
APP_API_KEY
Authorization: Bearer <key>
```

不要把认证逻辑散落到每个路由里，后续应通过 middleware 或 dependency 添加。

## 6. 请求大小和超时

V1 应考虑基础限制：

- 单次 input 最大长度。
- metadata 最大大小。
- WebSocket 队列长度。
- 单个 run 的最大执行时间可以后置，但接口要预留配置。

不要让一个无界请求直接塞满上下文或日志。

## 7. 并发

API 进程级 Runtime 可被多个请求共享。

并发要求：

- Runtime 共享依赖不能保存当前用户状态。
- pending registry 必须并发安全。
- WebSocket 连接队列只属于单个连接。
- 同一个 session 并发多 run 的策略需要谨慎。V1 可先通过 Runtime 配置限制同 session 串行，避免上下文写入乱序。

如果暂不实现全局 session 锁，设计和测试必须说明该限制。

## 8. 日志

API 日志记录：

- trace_id。
- route。
- method。
- status_code。
- session_id。
- run_id。
- runtime status。

不记录：

- 完整用户输入。
- raw prompt。
- raw tool result。
- 认证信息。

## 9. 必须联动阅读

- Runtime lifecycle 和 health 设计。
- Tools 安全策略。
- Memory 并发和 SQLite 写入行为。
- FastAPI/uvicorn 当前依赖和测试方式。

