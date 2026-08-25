# Runtime 层开发步骤与进度（3）- 恢复健康验收

> 覆盖步骤：Step 12-18  
> 当前状态：Step 12-18 待开发  
> 前置分卷：`Runtime层开发步骤与进度(1)-契约装配.md`、`Runtime层开发步骤与进度(2)-运行主链路.md`  
> 上位设计：`Runtime事件流与确认恢复设计.md`、`Runtime错误降级与健康检查设计.md`、`Runtime依赖装配与生命周期设计.md`、`Runtime运行流程与Memory集成设计.md`

本分卷实现 Runtime 的确认恢复、取消、健康检查、会话查询、timeline、删除、导出、启动恢复和最终 Runtime 验收。完成本分卷后，Runtime V1 应具备供 CLI/API 开发使用的稳定核心能力。

---

## Step 12：resume 确认恢复

**状态：待开发**

### 目标

实现 Runtime.resume，用于 waiting_user / pending_confirmation 的同进程确认恢复。

### 对应设计文档

```text
Runtime事件流与确认恢复设计.md
  ## 7. PendingConfirmation
  ## 8. PendingRunRegistry
  ## 9. resume 流程

Runtime运行流程与Memory集成设计.md
  ## 10. waiting_user 路径

Runtime公共契约与数据模型设计.md
  ## 3. RuntimeResult
```

### 前置条件

```text
Step 4 PendingRunRegistry 已完成。
Step 10 waiting_user 收口已完成。
Step 11 普通 run 主链路已完成。
```

### 涉及文件

```text
修改:
  src/app/runtime/core.py
  src/app/runtime/pending_runs.py

新增/修改:
  tests/app/runtime/test_runtime_resume.py
```

### 必做

1. Runtime.resume 接收：

```text
session_id
run_id
approved
reason
confirmation_id
preview_hash
debug
metadata
```

2. 校验 session_id/run_id 和 pending registry 归属。
3. 校验 confirmation_id / preview_hash。
4. 调用 ReActExecutor 或 ReactAgent 暴露的确认恢复入口。若当前只能通过 ReActExecutor.resume_after_confirmation，则必须使用 Step 0 记录的真实上下文。
5. 继续接收恢复后的事件 callback。
6. 按 Step 10 的规则完成 RuntimeResult 收口。
7. resume 成功、拒绝、失败后清理 pending registry。
8. 找不到 pending context 时返回 `run_not_found` 或 `interrupted`，不得伪造恢复。

### 明确不做

```text
不支持进程重启后的断点续跑。
不从 SQLite 重建 ReActExecutor context。
不绕过 preview_hash 校验。
不自动确认危险动作。
```

### 测试与验收

```powershell
python -m pytest tests/app/runtime/test_runtime_resume.py -q
```

测试至少覆盖：

```text
approved=True 恢复并完成。
approved=False 拒绝并返回安全结果。
confirmation_id 不匹配失败。
preview_hash 不匹配失败。
pending context 不存在返回 interrupted/run_not_found。
resume 后 registry 清理。
恢复过程事件继续进入 timeline。
```

推荐回归：

```powershell
python -m pytest tests/test_react_executor_confirmation.py tests/test_react_executor_preview_resume.py -q
```

### 完成后回写

记录 resume 实际调用底层接口、上下文保存方式、测试结果和已知限制。

---

## Step 13：cancel 等待确认的 run

**状态：待开发**

### 目标

实现 Runtime.cancel，V1 只取消 waiting_user 的 pending confirmation，不强制中断正在执行的工具或线程。

### 对应设计文档

```text
Runtime事件流与确认恢复设计.md
  ## 10. cancel 流程

Runtime错误降级与健康检查设计.md
  ## 2. RuntimeErrorCode
  ## 3. 状态与错误的关系

Runtime公共契约与数据模型设计.md
  ## 5. 状态定义
```

### 前置条件

```text
Step 12 resume 已完成或至少 pending registry 可用。
```

### 涉及文件

```text
修改:
  src/app/runtime/core.py
  src/app/runtime/pending_runs.py
  src/app/runtime/errors.py

新增/修改:
  tests/app/runtime/test_runtime_cancel.py
```

### 必做

1. Runtime.cancel 接收 session_id、run_id、reason。
2. 仅允许取消 pending registry 中的 waiting_user run。
3. 调用底层拒绝确认或取消确认逻辑，确保 pending action 不继续执行。
4. 记录用户取消事件。
5. 返回 `status=cancelled`、`error_code=cancelled`。
6. 清理 pending registry。
7. 如果 run 正在普通执行而非 waiting_user，返回明确错误，不承诺强杀。

### 明确不做

```text
不杀线程。
不杀子进程。
不取消正在进行中的模型调用。
不删除 session 历史。
```

### 测试与验收

```powershell
python -m pytest tests/app/runtime/test_runtime_cancel.py -q
```

测试至少覆盖：

```text
waiting_user run 可取消。
非 waiting_user run 不强制取消。
取消后 registry 清理。
取消事件进入 timeline。
取消后再次 resume 失败。
```

### 完成后回写

记录取消边界、底层调用方式、测试结果和后续异步取消预留。

---

## Step 14：Runtime health 检查

**状态：待开发**

### 目标

实现 Runtime.health，聚合 Runtime、Memory、数据库、Models、Tools、ReactAgent 和 workspace 的健康状态。

### 对应设计文档

```text
Runtime错误降级与健康检查设计.md
  ## 7. Health 检查
  ## 8. 日志和敏感信息

Runtime依赖装配与生命周期设计.md
  ## 3. 生命周期
  ## 7. 资源释放
```

### 前置条件

```text
Step 5 RuntimeFactory 已完成。
Step 6 Runtime core facade 已完成。
```

### 涉及文件

```text
新增/修改:
  src/app/runtime/health.py
  src/app/runtime/core.py
  tests/app/runtime/test_runtime_health.py
```

### 必做

1. Runtime.health 至少检查：

```text
runtime_initialized
memory
database
models
tools
react_agent
workspace
```

2. 每项包含：

```text
name
status: healthy / degraded / unavailable
message
metadata
```

3. 整体状态按核心依赖聚合为 healthy / degraded / unavailable。
4. 健康检查不得调用有副作用的工具。
5. Models 检查遵循 Models 层已有轻量 verify 或状态接口，不无条件消耗真实 Provider 配额。
6. 健康信息脱敏。

### 明确不做

```text
不调用真实高成本模型请求。
不执行 shell 命令探活。
不在 health 中修复数据库。
不实现 API /health 路由。
```

### 测试与验收

```powershell
python -m pytest tests/app/runtime/test_runtime_health.py -q
```

测试至少覆盖：

```text
所有依赖 healthy。
Memory 不可用 -> degraded 或 unavailable。
Model health 抛异常被安全包装。
health 不泄露敏感配置。
```

### 完成后回写

记录健康检查项、聚合规则、真实依赖能力和测试结果。

---

## Step 15：session/list/timeline/delete/export Runtime facade

**状态：待开发**

### 目标

实现 Runtime 对会话查询、列表、timeline、硬删除和 Markdown 导出的统一 facade，供后续 CLI/API 复用。

### 对应设计文档

```text
Runtime架构与模块设计.md
  ## 3. Runtime 对象的职责

Runtime运行流程与Memory集成设计.md
  ## 7. 事件写入

Runtime错误降级与健康检查设计.md
  ## 2. RuntimeErrorCode

Runtime公共契约与数据模型设计.md
  ## 6. 序列化规则
```

### 前置条件

```text
Step 2 serialization 已完成。
Step 3 errors 已完成。
Step 6 Runtime core facade 已完成。
```

### 涉及文件

```text
新增/修改:
  src/app/runtime/export.py
  src/app/runtime/core.py
  tests/app/runtime/test_runtime_sessions.py
  tests/app/runtime/test_runtime_export.py
```

### 必做

1. 实现：

```text
get_session(session_id)
list_sessions()
get_timeline(session_id)
delete_session(session_id)
export_session(session_id, output_path=None)
```

2. 所有接口通过 SessionManager / RuntimeMemoryAdapter 公开能力完成，不直接访问 SQL。
3. timeline 只返回 Memory 已映射、脱敏后的用户可见内容。
4. delete_session V1 硬删除，调用 SessionManager.delete_session。
5. export_session V1 生成 Markdown 内容或写入指定路径。
6. Markdown 导出只包含用户可见消息和事件。
7. 导出不得包含 hidden reasoning、raw prompt、raw tool result。
8. 如果写文件，必须进行路径安全处理；若 Runtime 没有统一 path policy，则 V1 可以先只返回 Markdown 字符串，把文件写入留给 CLI/API 明确处理。

### 明确不做

```text
不直接操作 SQLite。
不做软删除/archive。
不导出内部事件。
不覆盖已有文件，除非后续调用方显式要求并完成路径安全设计。
```

### 测试与验收

```powershell
python -m pytest tests/app/runtime/test_runtime_sessions.py tests/app/runtime/test_runtime_export.py -q
```

测试至少覆盖：

```text
list_sessions 字段。
get_session 不存在 -> session_not_found。
timeline 只含可见内容。
delete_session 后无法读取。
export Markdown 包含用户/助手消息。
export 不包含敏感字段。
```

推荐回归：

```powershell
python -m pytest tests/test_memory_session_manager.py tests/test_memory_storage.py -q
```

### 完成后回写

记录 facade 调用方式、导出格式、删除边界和测试结果。

---

## Step 16：启动恢复、close 与依赖失败降级

**状态：待开发**

### 目标

完善 Runtime 生命周期：启动时恢复 interrupted runs，关闭时释放资源，依赖初始化或运行时依赖失败时返回统一错误。

### 对应设计文档

```text
Runtime依赖装配与生命周期设计.md
  ## 3. 生命周期
  ## 6. Runtime 初始化恢复
  ## 7. 资源释放

Runtime运行流程与Memory集成设计.md
  ## 12. 启动恢复

Runtime错误降级与健康检查设计.md
  ## 4. 异常分类
```

### 前置条件

```text
Step 5 RuntimeFactory 已完成。
Step 14 health 已完成。
```

### 涉及文件

```text
修改:
  src/app/runtime/factory.py
  src/app/runtime/core.py
  src/app/runtime/errors.py

新增/修改:
  tests/app/runtime/test_runtime_lifecycle.py
```

### 必做

1. Runtime 初始化时调用 `recover_interrupted_runs()`。
2. 启动恢复结果记录到 Runtime metadata 或 health 中。
3. close 幂等。
4. close 不删除 SQLite 历史。
5. Factory 装配失败返回 `dependency_init_failed`。
6. 运行中 Memory 不可用返回 `memory_unavailable` 或 persistence warning。
7. 运行中 Agent 未分类异常返回 `agent_execution_failed` 或 `internal_error`。

### 明确不做

```text
不跨进程恢复 pending confirmation。
不自动继续 interrupted run。
不在 close 中删除 session。
不实现 API lifespan。
```

### 测试与验收

```powershell
python -m pytest tests/app/runtime/test_runtime_lifecycle.py -q
```

测试至少覆盖：

```text
recover_interrupted_runs 被调用。
running/waiting_user 旧 run 标记 interrupted。
close 可重复调用。
close 不删除历史。
dependency_init_failed 映射。
```

推荐回归：

```powershell
python -m pytest tests/test_memory_logging_recovery.py tests/test_memory_session_manager.py -q
```

### 完成后回写

记录恢复数量、close 释放资源列表、失败映射和测试结果。

---

## Step 17：Runtime 跨层集成回归

**状态：待开发**

### 目标

在不启动 CLI/API 的前提下，验证 Runtime 与 Memory、ReactAgent、ReActExecutor、OutputFeedback、Models/Tools fake 能正确协作。

### 对应设计文档

```text
Runtime架构与模块设计.md
  ## 5. 一轮普通运行的核心顺序
  ## 8. 必须联动检查的代码和文档

Runtime运行流程与Memory集成设计.md
  ## 3. 普通 run 的时序

Runtime事件流与确认恢复设计.md
  ## 4. 事件分发规则
```

### 前置条件

```text
Step 0-16 已完成。
```

### 涉及文件

```text
新增/修改:
  tests/app/runtime/test_runtime_cross_layer_acceptance.py
```

### 必做

至少覆盖：

```text
1. 新 session 普通对话。
2. 指定 session 多轮对话。
3. 可见事件进入 timeline。
4. 内部事件不进入 timeline。
5. manage_memory=False 不重复写消息。
6. waiting_user / pending_confirmation。
7. resume approved / rejected。
8. cancel waiting_user。
9. blocked_by_policy。
10. request_replan。
11. Memory 持久化失败 warning。
12. health 基础状态。
```

### 明确不做

```text
不启动 Typer CLI。
不启动 FastAPI。
不连接真实外部 Provider，除非用户明确要求做整体真实验收。
不测试 WebSocket。
```

### 测试与验收

```powershell
python -m pytest tests/app/runtime/test_runtime_cross_layer_acceptance.py -q
```

建议回归池：

```powershell
python -m pytest tests/app/runtime -q
python -m pytest tests/test_memory_runtime_adapter.py tests/test_memory_v1_end_to_end_acceptance.py tests/test_memory_react_agent_adaptation.py -q
python -m pytest tests/test_react_agent_with_react_executor.py tests/test_react_executor_events.py tests/test_react_executor_confirmation.py tests/test_output_feedback_processor.py -q
```

完成标准：

```text
Runtime V1 核心能力不依赖 CLI/API 即可闭环。
跨层失败已归类并记录。
没有破坏 Memory V1 和 ReactAgent 旧兼容模式。
```

### 完成后回写

记录所有验收场景结果、失败清单、偏差和是否需要修改其他层。

---

## Step 18：Runtime V1 收尾、文档回写与对 CLI/API 交接

**状态：待开发**

### 目标

完成 Runtime V1 文档、进度、已知限制和对 CLI/API 的交接说明，为后续 CLI 设计开发步骤提供稳定依据。

### 对应设计文档

```text
Runtime架构与模块设计.md
Runtime公共契约与数据模型设计.md
Runtime依赖装配与生命周期设计.md
Runtime运行流程与Memory集成设计.md
Runtime事件流与确认恢复设计.md
Runtime错误降级与健康检查设计.md
```

### 前置条件

```text
Step 0-17 已完成。
```

### 涉及文件

```text
修改:
  src/app/runtime/Runtime层开发步骤与进度.md
  src/app/runtime/Runtime层开发步骤与进度(1)-契约装配.md
  src/app/runtime/Runtime层开发步骤与进度(2)-运行主链路.md
  src/app/runtime/Runtime层开发步骤与进度(3)-恢复健康验收.md

可新增:
  src/app/runtime/Runtime V1交接说明.md
```

### 必做

1. 更新总入口当前进度为 Runtime V1 已完成或部分完成。
2. 每个 Step 写入真实完成记录。
3. 记录 Runtime 对 CLI/API 的稳定公开入口。
4. 记录 RuntimeResult / RuntimeEvent 最终字段。
5. 记录 CLI/API 后续必须遵守的 Runtime 调用方式。
6. 记录已知限制：

```text
不支持跨进程断点续跑。
不强杀正在运行工具。
不实现 API 认证。
不实现 CLI/API 具体入口。
```

7. 记录完整测试命令和结果。

### 明确不做

```text
不开始 CLI 开发。
不开始 API 开发。
不重写 Runtime 设计文档的大方向，除非实现中确认设计必须修订。
```

### 测试与验收

最终 Runtime 验收建议：

```powershell
python -m pytest tests/app/runtime -q
python -m pytest tests/test_memory_runtime_adapter.py tests/test_memory_v1_end_to_end_acceptance.py tests/test_memory_react_agent_adaptation.py -q
python -m pytest tests/test_react_agent_with_react_executor.py tests/test_react_executor_events.py tests/test_react_executor_confirmation.py tests/test_react_executor_preview_resume.py -q
python -m pytest tests/test_output_feedback_processor.py -q
```

如果某些测试因真实 Provider、平台差异或迁移期问题失败，必须记录：

```text
失败测试
失败原因
是否与 Runtime 修改相关
是否需要后续修复
临时验收口径
```

### 完成后回写

记录 Runtime V1 最终状态、测试总览、交接给 CLI/API 的接口清单和下一阶段建议。

