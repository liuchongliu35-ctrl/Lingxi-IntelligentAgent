# API 层开发步骤与进度（1）- 基础协议

> 覆盖步骤：Step 0-5  
> 当前状态：Step 0-5 待开发  
> 上位设计：`API架构与REST路由设计.md`、`API请求响应模型与错误设计.md`、`API本地安全与生命周期设计.md`

本分卷先建立 API V1 的 FastAPI app、Runtime dependency、API Result、Pydantic schema、错误处理、trace_id 和本地安全底座。没有完成本分卷前，不应开始业务 REST 路由或 WebSocket。

---

## Step 0：设计基线、FastAPI 依赖与 Runtime 契约快照

**状态：待开发**

### 目标

固定 API 开发前的真实入口状态、FastAPI/uvicorn 依赖状态、Runtime 公开契约和当前目录基线，避免 API 后续绕过 Runtime 或直接访问 Memory/Agent。

### 对应设计文档

```text
API架构与REST路由设计.md
  ## 1. 定位
  ## 2. 建议目录
  ## 14. 必须联动阅读

API请求响应模型与错误设计.md
  ## 9. 必须联动阅读

API本地安全与生命周期设计.md
  ## 9. 必须联动阅读
```

### Runtime 依赖

```text
RuntimeResult
RuntimeEvent
RuntimeErrorCode
RuntimeFactory 或 get_runtime 入口
Runtime REST facade 和 stream facade
```

### 前置条件

```text
API 设计文档已完成。
Runtime 步骤进度文档已完成。
当前工作区现有变更已被识别，不覆盖用户未提交修改。
```

### 涉及文件

```text
只读核对:
  src/app/api/
  src/app/runtime/
  pyproject.toml / requirements 相关依赖文件

可新增:
  tests/app/api/test_api_current_baseline.py
```

### 必做

1. 记录当前 `src/app/api` 文件状态。
2. 记录 FastAPI、Pydantic、uvicorn 是否已安装，若未安装，标记后续依赖处理方式。
3. 记录 Runtime 当前可用的公开入口和 RuntimeResult / RuntimeEvent 字段。
4. 新增 baseline 测试，验证 API 包可 import，且不会在 import 时初始化真实模型或执行工具。
5. 明确 API 只能调用 Runtime，不直接调用 Memory repo、ReactAgent 或 ReActExecutor。

### 明确不做

```text
不实现 FastAPI app。
不启动 uvicorn。
不运行真实模型。
不实现任何路由。
不修改 Runtime。
```

### 测试与验收

```powershell
python -m pytest tests/app/api/test_api_current_baseline.py -q
```

完成标准：

```text
API 入口基线已记录。
FastAPI / uvicorn 依赖状态已明确。
Runtime 契约快照已记录。
API import 不产生重型副作用。
```

### 完成后回写

```text
状态:
完成日期:
入口快照:
依赖快照:
Runtime 契约快照:
实际新增/修改文件:
测试命令:
测试结果:
发现的偏差:
遗留问题:
下一步:
```

---

## Step 1：FastAPI create_app 与 lifespan 生命周期

**状态：待开发**

### 目标

建立 FastAPI `create_app()`，固定应用生命周期、路由注册入口和 Runtime 进程级实例保存方式。

### 对应设计文档

```text
API架构与REST路由设计.md
  ## 2. 建议目录
  ## 3. FastAPI 生命周期

API本地安全与生命周期设计.md
  ## 3. Runtime 生命周期
  ## 7. 并发
```

### Runtime 依赖

```text
RuntimeFactory
Runtime.close
Runtime.recover_interrupted_runs 由 factory/init 触发
```

### 前置条件

```text
Step 0 已完成。
FastAPI 可用。
RuntimeFactory 或 fake Runtime 注入策略已明确。
```

### 涉及文件

```text
新增/修改:
  src/app/api/app.py
  src/app/api/__init__.py
  tests/app/api/test_api_app_lifespan.py
```

### 必做

1. 提供 `create_app()`。
2. startup/lifespan 创建 Runtime，保存到 `app.state.runtime`。
3. shutdown 调用 `runtime.close()`。
4. 所有路由未来通过 dependency 获取同一个 Runtime。
5. import `src.app.api.app` 不应立即创建真实 Runtime。
6. 测试支持注入 fake Runtime / fake factory。

### 明确不做

```text
不实现业务路由。
不每个请求重建 Runtime。
不在全局变量保存当前 session/run。
不启动 uvicorn。
```

### 测试与验收

```powershell
python -m pytest tests/app/api/test_api_app_lifespan.py -q
```

测试至少覆盖：

```text
create_app 可创建 FastAPI app。
lifespan 创建 Runtime。
app.state.runtime 存在。
shutdown 调用 close。
import app 无重型副作用。
```

### 完成后回写

记录 create_app 签名、Runtime 注入方式、lifespan 行为和测试结果。

---

## Step 2：Runtime dependency 与请求级 trace_id

**状态：待开发**

### 目标

建立 API 获取 Runtime 的唯一 dependency，并为每个 HTTP 请求生成 trace_id，用于 API Result、Runtime metadata 和日志关联。

### 对应设计文档

```text
API架构与REST路由设计.md
  ## 3. FastAPI 生命周期

API请求响应模型与错误设计.md
  ## 5. trace_id

API本地安全与生命周期设计.md
  ## 8. 日志
```

### Runtime 依赖

```text
app.state.runtime
Runtime public facade
```

### 前置条件

```text
Step 1 已完成。
```

### 涉及文件

```text
新增/修改:
  src/app/api/dependencies.py
  tests/app/api/test_api_dependencies.py
```

### 必做

1. 提供 `get_runtime()` dependency。
2. Runtime 不存在时返回统一错误，而不是 AttributeError。
3. 提供 trace_id 获取/生成机制。
4. trace_id 不替代 session_id/run_id。
5. dependency 不直接创建第二个 Runtime。
6. dependency 不保存当前 session/run 到全局变量。

### 明确不做

```text
不实现认证。
不实现路由业务。
不直接访问 Memory。
```

### 测试与验收

```powershell
python -m pytest tests/app/api/test_api_dependencies.py -q
```

测试至少覆盖：

```text
dependency 返回 app.state.runtime。
缺失 Runtime 返回 dependency_init_failed。
trace_id 每请求可用。
trace_id 出现在响应 metadata 或上下文中。
```

### 完成后回写

记录 dependency 接口、trace_id 生成策略和测试结果。

---

## Step 3：API Result、Pydantic schemas 与序列化边界

**状态：待开发**

### 目标

实现 REST 统一 API Result 和基础 Pydantic schema，确保所有 HTTP 路由有稳定请求/响应模型。

### 对应设计文档

```text
API请求响应模型与错误设计.md
  ## 1. API Result
  ## 2. Result 示例
  ## 3. Pydantic schema
  ## 6. validation
  ## 7. 敏感信息
```

### Runtime 依赖

```text
RuntimeResult 安全 dict
RuntimeErrorCode
Runtime serialization 输出
```

### 前置条件

```text
Step 2 已完成。
Pydantic 可用。
RuntimeResult 字段稳定。
```

### 涉及文件

```text
新增/修改:
  src/app/api/result.py
  src/app/api/schemas.py
  tests/app/api/test_api_result_schema.py
```

### 必做

1. 定义 API Result 字段：

```text
success
data
error
code
trace_id
session_id
run_id
metadata
```

2. 定义基础 schema：

```text
CreateSessionRequest
RunRequest
ResumeRequest
CancelRequest
SessionData
TimelineData
RuntimeResultData
HealthData
ApiResult
```

3. Schema 层不 import ReActExecutor 内部 context。
4. 请求校验覆盖 input 非空、metadata 对象、debug 布尔值等。
5. Result 不返回 raw prompt、hidden reasoning、raw tool result、raw observation、密钥。

### 明确不做

```text
不实现路由。
不定义 WebSocket 消息模型为 REST Result。
不暴露 Runtime 内部对象。
```

### 测试与验收

```powershell
python -m pytest tests/app/api/test_api_result_schema.py -q
```

测试至少覆盖：

```text
成功 Result 字段完整。
失败 Result 字段完整。
RunRequest input 非空校验。
metadata 类型校验。
schema 不 import ReActExecutor context。
敏感字段过滤或不进入 schema。
```

### 完成后回写

记录 schema 字段、校验规则、Result 示例和测试结果。

---

## Step 4：错误处理器与 HTTP 状态码映射

**状态：待开发**

### 目标

实现 FastAPI 统一异常处理，将 Runtime 错误和请求校验错误转换为一致的 API Result 和 HTTP 状态码。

### 对应设计文档

```text
API请求响应模型与错误设计.md
  ## 4. HTTP 状态码
  ## 6. validation
  ## 7. 敏感信息

API架构与REST路由设计.md
  ## 9. POST /sessions/{session_id}/runs
```

### Runtime 依赖

```text
RuntimeErrorCode
RuntimeException
RuntimeResult.status
RuntimeResult.error_code
```

### 前置条件

```text
Step 3 已完成。
Runtime 错误码映射稳定。
```

### 涉及文件

```text
新增/修改:
  src/app/api/error_handlers.py
  src/app/api/app.py
  tests/app/api/test_api_error_handlers.py
```

### 必做

实现映射：

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

1. FastAPI validation error 返回 API Result。
2. RuntimeException 返回 API Result。
3. 未捕获异常返回 internal_error，不泄露堆栈。
4. 不返回默认 HTML 错误页。
5. trace_id 出现在错误响应中。

### 明确不做

```text
不吞掉所有错误返回 200。
不把 waiting_user 当 500。
不暴露 traceback 给调用方。
```

### 测试与验收

```powershell
python -m pytest tests/app/api/test_api_error_handlers.py -q
```

测试至少覆盖：

```text
validation_error -> 400。
session_not_found -> 404。
blocked_by_policy -> 403。
waiting_user -> 202。
memory_unavailable -> 503。
未知异常 -> 500 API Result。
```

### 完成后回写

记录状态码映射、异常处理注册方式和测试结果。

---

## Step 5：本地安全、CORS 预留与请求限制底座

**状态：待开发**

### 目标

建立 API V1 的本地安全边界、CORS 预留、认证预留和基础请求限制配置。

### 对应设计文档

```text
API本地安全与生命周期设计.md
  ## 1. V1 安全定位
  ## 4. CORS
  ## 5. 认证预留
  ## 6. 请求大小和超时
  ## 8. 日志

API请求响应模型与错误设计.md
  ## 8. 本地 V1 安全边界
```

### Runtime 依赖

```text
无直接 Runtime 调用；保护 API 到 Runtime 的入口参数。
```

### 前置条件

```text
Step 1-4 已完成。
```

### 涉及文件

```text
新增/修改:
  src/app/api/app.py
  src/app/api/security.py
  src/app/api/config.py
  tests/app/api/test_api_local_security.py
```

### 必做

1. 默认 host 设计为 `127.0.0.1`，server Step 中落实。
2. V1 不启用认证，但预留 API Key middleware/dependency 结构。
3. 如果配置未来存在 `APP_API_KEY`，结构上可以接入 `Authorization: Bearer <key>`。
4. CORS 默认只面向 localhost/127.0.0.1，本地前端可配置。
5. 预留 input 最大长度、metadata 最大大小、WebSocket 队列长度等配置。
6. 日志不记录完整用户输入、raw prompt、raw tool result、认证信息。

### 明确不做

```text
不实现完整公网认证。
不默认开放 CORS *。
不把认证逻辑散落到每个路由。
不实现生产部署安全方案。
```

### 测试与验收

```powershell
python -m pytest tests/app/api/test_api_local_security.py -q
```

测试至少覆盖：

```text
默认 host 配置为 127.0.0.1。
CORS 不默认公网 *。
认证预留 dependency 可 import。
请求限制配置存在。
日志/错误响应不含敏感字段。
```

### 完成后回写

记录安全默认值、认证预留位置、请求限制配置和测试结果。

