# API 层开发步骤与进度

> 文档性质：API V1 开发总入口  
> 当前日期：2026-08-24  
> 当前阶段：API V1 待开发  
> 上位设计：`API架构与REST路由设计.md`、`API请求响应模型与错误设计.md`、`API WebSocket流式协议设计.md`、`API本地安全与生命周期设计.md`

本文档只记录 API V1 的开发路线、分卷入口、步骤状态、依赖关系和跨 Session 更新规则，不替代 API 设计文档。开发时如果步骤文档与设计文档存在冲突，必须先停止实现、核对两轮问答、API 设计文档和 Runtime 契约，再同步修正文档；不得由开发者临时改变架构边界。

---

## 1. 开发前固定架构

API V1 必须服务以下正式主链路：

```text
HTTP / WebSocket 请求
  -> FastAPI API 层
  -> Pydantic schema / API Result / error handler
  -> Runtime dependency
  -> Runtime
      -> Memory / ReactAgent / ReActExecutor / OutputFeedback
  -> RuntimeResult / RuntimeEvent
  -> API JSON Result 或 WebSocket message
```

必须始终保持：

```text
1. API 是 Runtime 的 HTTP / WebSocket 适配器。
2. API 不装配 Agent 主链路，不直接调用 Analyzer / Planner / ReActExecutor。
3. API 不直接访问 SQLite，不调用 SessionManager.repo。
4. API 不直接执行工具或绕过 Tools 安全策略。
5. 所有 REST 路由返回统一 API Result。
6. WebSocket 不使用 REST Result 壳，但错误消息保持 code/message 语义一致。
7. API 默认只面向本地运行，默认绑定 127.0.0.1。
8. API V1 不启用认证，但预留 API Key middleware/dependency 结构。
9. API 不返回 raw prompt、hidden reasoning、raw tool result、raw observation、密钥或认证信息。
10. API 进程级 Runtime 共享依赖，不能保存当前 session/run 到全局变量。
```

---

## 2. 当前真实状态

### 2.1 已完成

```text
src/app/api 目录已创建
API 设计文档 4 份
Runtime / CLI / API 总体设计已完成
Runtime 步骤进度文档已完成
CLI 步骤进度文档已完成
API 预期使用 FastAPI
API 第一版要求 WebSocket 流式能力
```

### 2.2 待开发

```text
FastAPI create_app
lifespan / startup / shutdown
Runtime dependency
API Result
Pydantic schema
error handler
trace_id
REST sessions 路由
REST runs 路由
resume / cancel
delete / export
health
WebSocket /ws/sessions/{session_id}/runs
WebSocket run/resume/cancel/ping 消息
WebSocket event/result/error/queued/pong 推送
本地 server 启动辅助
API 测试和端到端验收
```

### 2.3 Runtime 前置依赖

API 开发依赖 Runtime 稳定契约。原则上应在 Runtime V1 至少完成以下能力后进入 API 主开发：

```text
Runtime.run
Runtime.run_stream
Runtime.resume
Runtime.cancel
Runtime.list_sessions
Runtime.get_session
Runtime.get_timeline
Runtime.health
Runtime.export_session
Runtime.delete_session
RuntimeResult / RuntimeEvent / RuntimeErrorCode
```

如果 API 先行开发，只能使用 fake Runtime 做路由层测试，不能绕过 Runtime 直接调用 Memory 或 Agent。

---

## 3. 分卷与连续步骤

步骤编号跨分卷连续且唯一。开发时按依赖顺序推进，除非步骤文档明确允许并行。

| 分卷 | 步骤 | 内容 | 当前状态 |
|---|---:|---|---|
| [基础协议](API层开发步骤与进度(1)-基础协议.md) | Step 0-5 | 基线、FastAPI app、Runtime dependency、API Result、schema、error/trace/security | 待开发 |
| [REST路由](API层开发步骤与进度(2)-REST路由.md) | Step 6-12 | health、sessions、timeline、runs、resume/cancel、delete/export、REST 验收 | 待开发 |
| [WebSocket生命周期验收](API层开发步骤与进度(3)-WebSocket生命周期验收.md) | Step 13-19 | WebSocket 路径、消息协议、队列、waiting_user、server、本地安全、最终验收 | 待开发 |

总体依赖：

```text
Step 0-5 API 基础协议、app、dependency、schema、error
  -> Step 6-12 REST 路由和 Runtime 集成
  -> Step 13-19 WebSocket、server、本地生命周期和 API 验收
```

允许的有限并行：

```text
Step 6 health 与 Step 7 sessions 可在 Step 0-5 完成后基于 fake Runtime 并行开发。
Step 13 WebSocket 基础连接可在 REST Step 9 runs 稳定后开发。
Step 16 waiting_user WebSocket 恢复必须在 REST resume/cancel 和 Runtime pending registry 稳定后开发。
```

---

## 4. 每个 Step 的固定栏目

每个 Step 至少包含：

```text
目标:
  本 Step 要解决的唯一主题。

对应设计文档:
  本 Step 开发必须参考的 API 设计文档和小节。

Runtime 依赖:
  本 Step 依赖哪些 Runtime 公开入口和 RuntimeResult/RuntimeEvent 字段。

前置条件:
  必须已完成的 Step、已有接口和测试。

涉及文件:
  预计新增/修改的源码、测试和文档。

必做:
  必须落地的行为、协议、安全边界和错误处理。

明确不做:
  本 Step 不允许顺手扩张的能力。

测试与验收:
  本 Step 需要新增/修改的测试和建议命令。

完成后回写:
  状态、日期、实际修改、测试结果、偏差、遗留问题和下一步。
```

状态只允许使用：

```text
待开发
开发中
部分完成
已完成
阻塞
```

不得仅因代码已经存在就标记“已完成”。只有满足该 Step 的完成标准并运行规定测试后，才能标记完成。

---

## 5. 全局禁止偏离项

API V1 开发周期内禁止：

```text
不在 API 中创建 Analyzer / Planner / ReActExecutor。
不在 API 中直接操作 SQL 或 SQLite。
不在 API 中直接调用 SessionManager.repo。
不在 API 中直接执行工具。
不绕过 Runtime 做消息写入。
不把 FastAPI 路由写成 Runtime 的替代编排层。
不默认监听 0.0.0.0。
不在 V1 强制实现认证，但要保留认证预留结构。
不把 raw prompt、hidden reasoning、raw tool result 输出到 REST 或 WebSocket。
不在 POST /sessions 的 session_id 冲突中等待 y/n。
不实现 WS /ws/runs 备用路径。
不实现 CLI 命令。
```

---

## 6. 当前进度

```text
当前分卷：API层开发步骤与进度(1)-基础协议.md
当前 Step：Step 0
当前状态：待开发
下一步：开发前执行 Step 0，固定 FastAPI、Runtime 契约、依赖和当前入口基线。
```

---

## 7. 回归测试池

API 每个关键阶段完成后，按影响范围选择运行：

```powershell
python -m pytest tests/app/api -q
python -m pytest tests/app/runtime -q
python -m pytest tests/test_memory_runtime_adapter.py tests/test_memory_react_agent_adaptation.py -q
```

如果项目当前 pytest 环境存在迁移期失败，必须记录失败测试名、失败原因和是否与本 Step 相关，不能简单写“测试失败”。

