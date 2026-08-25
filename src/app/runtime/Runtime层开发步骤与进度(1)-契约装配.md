# Runtime 层开发步骤与进度（1）- 契约装配

> 覆盖步骤：Step 0-5  
> 当前状态：Step 0 已完成，Step 1-5 待开发  
> 上位设计：`Runtime架构与模块设计.md`、`Runtime公共契约与数据模型设计.md`、`Runtime依赖装配与生命周期设计.md`、`Runtime错误降级与健康检查设计.md`

本分卷先建立 Runtime V1 的基础契约、错误、序列化、pending registry 和依赖装配。没有完成本分卷前，不应开始正式 Runtime 主链路开发。

---

## Step 0：设计基线、真实接口快照与迁移保护

**状态：已完成**

### 目标

固定 Runtime 开发前的真实代码状态、跨层接口和测试基线，避免后续按设计文档臆造不存在的参数或破坏已有兼容模式。

### 对应设计文档

```text
Runtime架构与模块设计.md
  ## 6. Runtime 不应形成的依赖
  ## 7. 兼容模式
  ## 8. 必须联动检查的代码和文档

Runtime运行流程与Memory集成设计.md
  ## 2. 正式 Runtime 模式
  ## 13. 必须联动阅读

Runtime依赖装配与生命周期设计.md
  ## 8. 需要联动阅读
```

### 前置条件

```text
Runtime 设计文档已经完成。
Memory V1、ReactAgent、ReActExecutor、Models、Tools 的当前代码可读取。
不覆盖用户未提交修改。
```

### 涉及文件

```text
只读核对:
  src/memory/runtime_adapter.py
  src/memory/session_manager.py
  src/memory/context_builder.py
  src/memory/event_mapper.py
  src/agent/orchestrator/react_agent.py
  src/agent/react_executor/
  src/agent/output_feedback.py
  src/models/model_manager.py
  src/tools/tool_manager.py

可新增:
  tests/app/runtime/test_runtime_current_baseline.py
```

### 必做

1. 记录 `RuntimeMemoryAdapter.begin_turn()`、`complete_turn()`、`fail_turn()`、`event_callback()` 的真实签名和返回对象字段。
2. 记录 `RuntimeMemoryTurn.react_agent_kwargs()` 是否已存在以及真实返回字段。
3. 记录 `ReactAgent.run_with_result()`、`run_stream()` 的真实参数，特别是 `context_text`、`session_id`、`run_id`、`manage_memory`、`event_callback`。
4. 记录 ReActExecutor 的 `ExecutionResult`、`ExecutionEvent`、`PendingConfirmation` 字段。
5. 记录 OutputFeedbackProcessor 的真实构造和调用方式。
6. 记录 SessionManager 当前公开 session/list/timeline/delete/run 查询能力。
7. 记录 Models / Tools 的装配入口，避免 RuntimeFactory 使用旧实现。
8. 建立一个 baseline 测试，至少验证当前关键接口可 import、签名存在、ReactAgent 支持 `manage_memory=False`。

### 明确不做

```text
不实现 RuntimeResult。
不实现 RuntimeFactory。
不调用真实模型。
不修改 Memory / Agent / Tools / Models 行为。
不删除旧兼容入口。
```

### 测试与验收

建议新增并运行：

```powershell
python -m pytest tests/app/runtime/test_runtime_current_baseline.py -q
```

可选基线回归：

```powershell
python -m pytest tests/test_memory_runtime_adapter.py tests/test_memory_react_agent_adaptation.py -q
```

### Step 0 完成记录

```text
状态: 已完成
完成日期: 2026-08-24
```

#### 接口快照

```python
RuntimeMemoryAdapter(
    session_manager: SessionManager | None = None,
    *,
    context_builder: ContextBuilder | None = None,
)

RuntimeMemoryAdapter.begin_turn(
    session_id,
    user_input,
    *,
    user_metadata=None,
    session_title=None,
    session_metadata=None,
    max_recent_messages=None,
    agent_version=None,
    model_profile=None,
) -> RuntimeMemoryTurn

RuntimeMemoryAdapter.complete_turn(
    turn,
    assistant_content,
    *,
    assistant_metadata=None,
    content_format=ContentFormat.TEXT,
    display_type=DisplayType.FINAL_ANSWER,
    maybe_summarize=True,
    include_timeline=True,
) -> RuntimeMemoryResult

RuntimeMemoryAdapter.fail_turn(
    turn,
    error,
    *,
    maybe_summarize=True,
    include_timeline=True,
) -> RuntimeMemoryResult

RuntimeMemoryAdapter.event_callback(
    turn,
    *,
    external_callback=None,
    external_visible_only=True,
) -> Callable
```

`RuntimeMemoryTurn` 当前包含：

```text
session
user_message
run
context
short_term_memory
persistence_available
persistence_warning
```

`RuntimeMemoryTurn.react_agent_kwargs()` 已存在，真实返回：

```python
{
    "context_text": turn.context_text,
    "session_id": turn.session_id,
    "run_id": turn.run_id,
    "manage_memory": False,
}
```

`ReactAgent` 当前仍要求以下旧兼容构造参数：

```python
ReactAgent(
    model_manager,
    short_term_memory,
    long_term_memory,
    tool_manager,
    rag_system,
    complexity_analyzer,
    planner=None,
    executor=None,
    executor_type=None,
    react_executor_config=None,
    tool_registry=None,
    manage_memory=True,
)
```

正式 Runtime 调用接口：

```python
run_with_result(
    user_input,
    *,
    history=None,
    context_text=None,
    event_callback=None,
    event_callback_visible_only=True,
    manage_memory=None,
    session_id=None,
    run_id=None,
)

run_stream(
    user_input,
    *,
    include_internal=False,
    history=None,
    context_text=None,
    event_callback=None,
    event_callback_visible_only=True,
    manage_memory=None,
    session_id=None,
    run_id=None,
)
```

`ExecutionResult` 关键字段：

```text
execution_id, plan_id, status, success, output, source_trace_id, summary
task_statuses, step_statuses, observations, events
failed_step_id, error_code
requires_user_input, user_input_request, pending_confirmation
request_replan, replan_reason
```

`ExecutionEvent` 字段：

```text
execution_id, plan_id, type, message, event_id
task_id, step_id, timestamp, visible_to_user, payload
```

`PendingConfirmation` 字段：

```text
execution_id, plan_id, confirmation_type, confirmation_message
pending_action, session_id, packet_id, confirmation_id, call_id
preview_hash, preview_summary, affected_resources
task_id, step_id, expires_at
```

其他真实入口：

```python
OutputFeedbackProcessor()
OutputFeedbackProcessor.build(
    execution_result,
    *,
    include_internal=False,
    group_related=True,
)

SessionManager.list_sessions()
SessionManager.get_session_timeline(session_id)
SessionManager.delete_session(session_id)
SessionManager.recover_interrupted_runs()

ModelManager(...)
ModelManager.health_check()
ModelManager.compress_context(...)

ToolManager(...)
ToolManager.list_tools()
ToolManager.get_registry()
ToolManager.execute(...)
ToolManager.run_tool(...)
```

#### 实际新增/修改文件

```text
新增:
  tests/app/runtime/test_runtime_current_baseline.py

修改:
  src/app/runtime/Runtime层开发步骤与进度(1)-契约装配.md
  src/app/runtime/Runtime层开发步骤与进度.md
```

#### 测试命令与结果

```text
python -m pytest tests/app/runtime/test_runtime_current_baseline.py -q
结果: 6 passed

python -m pytest tests/test_memory_runtime_adapter.py tests/test_memory_react_agent_adaptation.py -q
结果: 13 passed
```

#### 发现的设计偏差

```text
1. ReactAgent 的正式 Runtime 调用参数已经存在，但构造函数仍保留
   long_term_memory、rag_system、complexity_analyzer 等旧兼容参数。
2. RuntimeMemoryAdapter.event_callback() 的外部回调接收原始 ExecutionEvent；
   Runtime 后续需要自行包装为 RuntimeEvent。
3. ReActExecutor.resume_after_confirmation() 需要进程内
   ReActExecutionContext，不能从 SQLite 或用户输入伪造恢复上下文。
4. Planner/ToolManager 仍存在 search_tool、file_writer 等兼容别名，
   Runtime 不应直接依据别名执行工具。
```

#### 遗留问题

```text
1. RuntimeResult、RuntimeFactory、Runtime 核心主链路尚未实现。
2. 当前没有公开的 SessionManager.get_run() facade，后续 Run 查询需在
   Runtime 适配边界内处理，不能直接让 CLI/API 访问 repo。
3. execute_stream() 的事件产出语义仍需在后续主链路 Step 10-11 中验证，
   不能预设为真正的实时 provider token 流。
4. 本 Step 只建立接口基线，尚未进行真实模型、工具或 Runtime 装配。
```

#### 下一步

```text
进入 Step 1：Runtime contracts 基础数据模型。
只实现 RuntimeRequest、RuntimeResult、RuntimeEvent、状态和内部请求对象，
不提前实现 Runtime.run 或 RuntimeFactory。
```

完成标准：

```text
真实接口快照已记录到本 Step 完成记录。
新增 baseline 测试通过，或明确记录与当前迁移状态有关的失败。
后续 Step 可以基于真实签名开发。
```

### 完成后回写

```text
状态:
完成日期:
接口快照:
实际新增/修改文件:
测试命令:
测试结果:
发现的设计偏差:
遗留问题:
下一步:
```

---

## Step 1：Runtime contracts 基础数据模型

**状态：已完成**

### 目标

建立 Runtime 对 CLI/API 稳定暴露的基础数据契约，包括 RuntimeRequest、RuntimeResult、RuntimeEvent、运行状态和内部 request context。

### 对应设计文档

```text
Runtime公共契约与数据模型设计.md
  ## 2. RuntimeRequest
  ## 3. RuntimeResult
  ## 4. RuntimeEvent
  ## 5. 状态定义
  ## 8. ID 责任

Runtime架构与模块设计.md
  ## 2. 建议模块结构
  ## 3. Runtime 对象的职责
```

### 前置条件

```text
Step 0 已完成。
已确认 Memory 和 ReactAgent 的真实参数。
```

### 涉及文件

```text
新增/修改:
  src/app/runtime/contracts.py
  src/app/runtime/__init__.py
  tests/app/runtime/test_runtime_contracts.py
```

### 必做

1. 定义 RuntimeStatus，覆盖：

```text
completed
failed
blocked
waiting_user
request_replan
cancelled
interrupted
```

2. 定义 RuntimeRequest，至少包含：

```text
input
session_id
stream
debug
metadata
model_profile
agent_version
```

3. 定义 RuntimeResult，字段固定为设计文档要求的完整集合。
4. 定义 RuntimeEvent，包含 event_id、session_id、run_id、event_type、message、visible_to_user、payload、source_event、sequence、created_at。
5. 定义 ResumeRequest、CancelRequest 等 Runtime 内部请求对象，供后续 Step 使用。
6. 所有契约必须可转换为 dict，且不得依赖 FastAPI、Typer 或数据库模型。
7. ID 只接收和传递，不在 contracts 中生成数据库实体 ID。

### 明确不做

```text
不实现 Runtime.run。
不实现序列化脱敏细节。
不引入 Pydantic 作为 Runtime 强依赖，除非后续确认项目统一采用。
不暴露 ReActExecutor context。
```

### 测试与验收

```powershell
python -m pytest tests/app/runtime/test_runtime_contracts.py -q
```

测试至少覆盖：

```text
RuntimeResult 默认字段完整。
RuntimeEvent 可 dict 化。
waiting_user / request_replan 字段可表达。
contracts 不 import CLI/API。
contracts 不直接 import SQLite repository。
```

### 完成后回写

记录字段、默认值、dict 序列化结果和任何与设计不一致的调整。

### Step 1 完成记录

```text
状态: 已完成
完成日期: 2026-08-24
```

#### 实际实现

```text
新增:
  src/app/runtime/contracts.py
  tests/app/runtime/test_runtime_contracts.py

修改:
  src/app/runtime/__init__.py
```

已完成内容：

```text
1. RuntimeStatus 覆盖 completed、failed、blocked、waiting_user、
   request_replan、cancelled、interrupted。
2. RuntimeRequest 包含 input、session_id、stream、debug、metadata、
   model_profile、agent_version。
3. RuntimeRequest 校验非空 input、session_id 格式、基础布尔值和 mapping 类型。
4. RuntimeResult 覆盖设计文档要求的全部字段，并支持默认构造。
5. RuntimeEvent 包含 event_id、session_id、run_id、event_type、message、
   visible_to_user、payload、source_event、sequence、created_at。
6. ResumeRequest、CancelRequest 已建立，供后续恢复和取消步骤使用。
7. 契约均提供基础 to_dict()，可处理 Enum、datetime、dataclass、mapping、
   list/tuple；本 Step 不承担敏感字段脱敏。
8. contracts 仅依赖标准库和 Memory 的 session_id 校验函数，不依赖
   FastAPI、Typer、SQLite repository、Provider 或底层执行上下文。
9. contracts 不生成任何数据库实体 ID；event_id、session_id、run_id 均由
   调用方接收和传递。
```

#### 默认值与设计偏差

```text
1. RuntimeResult 默认 status 为 failed、success=False，表示未完成的空结果
   不应被误判为 completed。
2. RuntimeEvent.sequence 默认值为 0，表示尚未由 Runtime 事件协调器分配；
   正式事件流应在后续步骤中按 run 从 1 开始递增。
3. RuntimeEvent.event_id 默认 None，严格遵守 Runtime 不生成 Memory 实体 ID。
4. RuntimeResult 的 execution_result、output_feedback、memory_result、
   pending_confirmation 和 timeline 只做 mapping/list 形状校验，不在本 Step
   暴露或裁剪底层对象。
```

#### 测试命令与结果

```text
python -m pytest tests/app/runtime/test_runtime_contracts.py -q
结果: 17 passed

python -m pytest tests/app/runtime/test_runtime_current_baseline.py tests/app/runtime/test_runtime_contracts.py tests/test_memory_runtime_adapter.py tests/test_memory_react_agent_adaptation.py -q
结果: 36 passed
```

#### 遗留问题

```text
1. 安全序列化、敏感字段过滤和 debug 边界留给 Step 2。
2. RuntimeStatus 与底层 AgentRunStatus 的映射留给 Step 3/运行主链路。
3. RuntimeEvent 的 source_event 白名单、payload 脱敏和事件去重留给后续事件步骤。
4. RuntimeResult 当前只是公共数据契约，尚未由 Runtime.run 生成。
```

#### 下一步

```text
进入 Step 2：Runtime serialization、脱敏与 debug 边界。
```

---

## Step 2：Runtime serialization、脱敏与 debug 边界

**状态：待开发**

### 目标

建立统一的安全序列化和脱敏能力，确保 RuntimeResult、RuntimeEvent、ExecutionResult、OutputFeedback、Memory 对象可以安全返回给 CLI/API。

### 对应设计文档

```text
Runtime公共契约与数据模型设计.md
  ## 6. 序列化规则
  ## 7. 调试字段
  ## 9. 跨层约束

Runtime事件流与确认恢复设计.md
  ## 3. 事件可见性
  ## 8. PendingRunRegistry

Runtime错误降级与健康检查设计.md
  ## 8. 日志和敏感信息
```

### 前置条件

```text
Step 1 已完成。
已知 ExecutionResult / OutputFeedback / TimelineItem 的真实结构。
```

### 涉及文件

```text
新增/修改:
  src/app/runtime/serialization.py
  tests/app/runtime/test_runtime_serialization.py
```

### 必做

1. 支持 dataclass、Enum、datetime、Mapping、list/tuple 的安全转换。
2. 支持 ExecutionResult、ExecutionEvent、OutputFeedback、Memory result 的安全摘要转换。
3. 建立敏感字段过滤：

```text
raw_prompt
full_prompt
hidden_reasoning
raw_tool_result
raw_observation
api_key
token
cookie
password
authorization
```

4. debug=True 时只允许输出安全诊断摘要：

```text
analyzer_summary
plan_summary
event_count
model_profile
tool_profile
```

5. 对无法安全序列化的对象输出类型名或受控摘要，不直接返回 repr 中的敏感内容。
6. 对 pending_confirmation 做安全裁剪，只保留 confirmation_id、preview_hash、动作说明、安全预览等字段。

### 明确不做

```text
不改变 Memory event_mapper 的脱敏规则。
不把 debug 做成隐藏推理开关。
不返回 raw tool output。
不实现 API schema。
```

### 测试与验收

```powershell
python -m pytest tests/app/runtime/test_runtime_serialization.py -q
```

测试至少覆盖：

```text
敏感字段被过滤。
debug=False 不出现 debug metadata。
debug=True 仍不出现 raw_prompt / token / password。
ExecutionResult 可安全转换。
PendingConfirmation 不暴露原始 context。
```

### 完成后回写

记录敏感字段清单、已支持对象类型和任何暂不支持对象。

---

## Step 3：Runtime errors、状态映射与统一异常

**状态：已完成（2026-08-24）**

### 目标

建立 RuntimeErrorCode、RuntimeException 和底层错误归一化逻辑，为 RuntimeResult、CLI 退出码和 API HTTP 状态码提供统一来源。

### 对应设计文档

```text
Runtime错误降级与健康检查设计.md
  ## 2. RuntimeErrorCode
  ## 3. 状态与错误的关系
  ## 4. 异常分类
  ## 5. HTTP 状态码映射
  ## 6. CLI 退出码映射

Runtime公共契约与数据模型设计.md
  ## 5. 状态定义
```

### 前置条件

```text
Step 1 已完成。
Step 2 已完成或至少已定义安全错误摘要函数。
```

### 涉及文件

```text
新增/修改:
  src/app/runtime/errors.py
  tests/app/runtime/test_runtime_errors.py
```

### 必做

1. 定义 RuntimeErrorCode，至少包括：

```text
validation_error
session_not_found
run_not_found
session_conflict
memory_unavailable
persistence_warning
agent_execution_failed
blocked_by_policy
waiting_user
request_replan
cancelled
interrupted
dependency_init_failed
export_failed
api_error
internal_error
```

2. 定义 RuntimeException，包含 code、message、status、metadata。
3. 定义 `map_exception()` 或同等函数，把 Memory、Agent、Tools、Models 的常见异常转换成 Runtime 错误。
4. 定义 HTTP 状态码和 CLI 退出码映射函数，但不实现 CLI/API。
5. 保证错误消息经过脱敏。
6. 区分 persistence warning 和 agent execution failed。

### 明确不做

```text
不吞掉所有异常后返回 success。
不把 waiting_user 当成 internal_error。
不在 Runtime errors 中 import FastAPI 或 Typer。
不强依赖底层私有异常类型。
```

### 测试与验收

```powershell
python -m pytest tests/app/runtime/test_runtime_errors.py -q
```

测试至少覆盖：

```text
validation_error -> 400 / exit 1。
session_not_found -> 404 / exit 1。
blocked_by_policy -> 403 / exit 2。
waiting_user -> 202 / exit 0。
memory_unavailable -> 503 / exit 3。
dependency_init_failed -> 503 / exit 3。
```

### 完成后回写

记录错误码枚举、映射表和与设计不同的实际补充。

---

## Step 4：PendingRunRegistry 与 run 级临时状态隔离

**状态：已完成（2026-08-24）**

### 目标

建立 waiting_user 确认恢复所需的进程内 pending registry，并固定进程级共享对象与 run 级临时状态的隔离边界。

### 对应设计文档

```text
Runtime事件流与确认恢复设计.md
  ## 7. PendingConfirmation
  ## 8. PendingRunRegistry
  ## 9. resume 流程
  ## 10. cancel 流程

Runtime依赖装配与生命周期设计.md
  ## 4. 进程级共享和 run 级隔离
```

### 前置条件

```text
Step 1 已完成。
Step 2 已完成。
Step 3 已完成。
```

### 涉及文件

```text
新增/修改:
  src/app/runtime/pending_runs.py
  tests/app/runtime/test_runtime_pending_runs.py
```

### 必做

1. 支持按 run_id 登记 pending run。
2. 每条记录至少包含：

```text
session_id
run_id
executor context 或 resume 所需对象
pending_confirmation 安全摘要
created_at
expires_at 或 ttl
metadata
```

3. 支持 get、pop、remove、expire 清理。
4. 保证 resume / cancel 后清理。
5. 保证并发安全，至少使用锁保护 registry。
6. 不把原始 executor context 暴露给 RuntimeResult。
7. 进程重启后 registry 为空，后续由 Memory recovery 标记 interrupted。

### 明确不做

```text
不实现跨进程恢复。
不把 pending context 写入 SQLite。
不实现 WebSocket 队列。
不强杀正在运行的工具。
```

### 测试与验收

```powershell
python -m pytest tests/app/runtime/test_runtime_pending_runs.py -q
```

测试至少覆盖：

```text
register/get/pop。
session_id/run_id 不匹配拒绝。
过期清理。
pop 后不能再次 resume。
返回的 pending_confirmation 是安全摘要。
```

### 完成后回写

记录 registry 字段、ttl 策略、并发保护方式和已知限制。

---

## Step 5：RuntimeFactory、依赖生命周期与启动恢复底座

**状态：已完成（2026-08-24）**

### 目标

建立 RuntimeFactory，支持生产装配和测试依赖注入，同时固定进程级 Runtime 生命周期、close 行为和启动恢复调用位置。

### 对应设计文档

```text
Runtime依赖装配与生命周期设计.md
  ## 2. 生产依赖图
  ## 3. 生命周期
  ## 5. Factory 设计
  ## 6. Runtime 初始化恢复
  ## 7. 资源释放

Runtime架构与模块设计.md
  ## 2. 建议模块结构
  ## 3. Runtime 对象的职责
```

### 前置条件

```text
Step 0-4 已完成。
已确认各层真实构造函数和配置入口。
```

### 涉及文件

```text
新增/修改:
  src/app/runtime/factory.py
  src/app/runtime/core.py
  src/app/runtime/__init__.py
  tests/app/runtime/test_runtime_factory.py
```

### 必做

1. Factory 支持生产路径默认创建：

```text
ModelManager
ToolManager / ToolRegistry
SessionManager
ContextBuilder
RuntimeMemoryAdapter
Analyzer / complexity_analyzer
Planner
ReActExecutor
ReactAgent(manage_memory=False)
OutputFeedbackProcessor
PendingRunRegistry
```

2. Factory 支持测试注入：

```text
model_manager
tool_manager
session_manager
memory_adapter
react_agent
workspace_root
config
```

3. Runtime 初始化时调用 `SessionManager.recover_interrupted_runs()`。
4. 进程级 Runtime 不保存当前 session_id/run_id。
5. 实现 `Runtime.close()`，对可关闭依赖做幂等关闭。
6. 装配失败转换为 `dependency_init_failed`。
7. 不自动使用旧 LongTermMemory / RAG 作为会话持久化依赖。

### 明确不做

```text
不实现 Runtime.run 主链路。
不实现 CLI/API 启动。
不每个请求重新创建全部依赖。
不把 fake 依赖用于生产路径。
```

### 测试与验收

```powershell
python -m pytest tests/app/runtime/test_runtime_factory.py -q
```

测试至少覆盖：

```text
默认 factory 可以构建 Runtime。
注入 fake 依赖时不会创建第二套 SessionManager。
recover_interrupted_runs 被调用。
close 幂等。
构造失败映射 dependency_init_failed。
```

### 完成后回写

记录真实装配路径、注入参数、恢复调用结果和任何迁移期兼容处理。
#### Step 2 完成记录（2026-08-24）

```text
状态: 已完成
新增文件:
  src/app/runtime/serialization.py
  tests/app/runtime/test_runtime_serialization.py
修改文件:
  src/app/runtime/__init__.py
实现:
  1. 支持 dataclass、Enum、datetime、Mapping、list/tuple/set 的安全递归转换。
  2. 支持 ExecutionResult、ExecutionEvent、OutputFeedback、Memory result 和 RuntimeResult 的公开摘要转换。
  3. 递归过滤 raw_prompt、full_prompt、hidden_reasoning、raw_tool_result、
     raw_observation、api_key、token、cookie、password、authorization 及内部 raw/context 字段。
  4. 对命令、参数、环境变量和 Bearer 凭据文本做安全处理；未知对象只返回类型摘要。
  5. debug=False 不输出 metadata.debug；debug=True 仅允许 analyzer_summary、
     plan_summary、event_count、model_profile、tool_profile。
  6. PendingConfirmation 采用公开字段白名单，绝不暴露 pending_action 或 executor context。
  7. RuntimeEvent 和 ExecutionResult 默认遵守 visible_to_user，隐藏事件不进入对外结果。
测试命令:
  python -m pytest tests/app/runtime/test_runtime_serialization.py -q
测试结果:
  9 passed
回归命令:
  python -m pytest tests/app/runtime/test_runtime_current_baseline.py tests/app/runtime/test_runtime_contracts.py tests/app/runtime/test_runtime_serialization.py tests/test_memory_runtime_adapter.py tests/test_memory_react_agent_adaptation.py -q
回归结果:
  45 passed
偏差和遗留问题:
  1. 未修改 Memory event_mapper，Runtime 仅在对外适配边界增加更严格的序列化和白名单。
  2. 未实现 PendingRunRegistry；本 Step 只保证其原始 executor context 不会被序列化层返回。
  3. 未实现 Runtime.run、错误映射、Factory、CLI/API schema。
下一步:
  进入 Step 3，先阅读错误降级与健康检查设计，再实现 Runtime 错误码、状态映射和异常适配。
```
Step 2 status: completed (2026-08-24).
Step 3 status: completed (2026-08-24).

Step 3 completion record:

```text
Added:
  src/app/runtime/errors.py
  tests/app/runtime/test_runtime_errors.py
Updated:
  src/app/runtime/__init__.py

Implemented:
  - RuntimeErrorCode with all design V1 error codes.
  - RuntimeException with code, status, message, and sanitized metadata.
  - Cross-layer mapping for Memory, Models, Tools, ReActExecutor, and
    ordinary Python exceptions.
  - Explicit status/error separation for blocked, waiting_user,
    request_replan, cancelled, interrupted, and failed cases.
  - HTTP status mapping and CLI exit-code mapping.
  - Failure-shaped RuntimeResult construction without converting failures
    into success.
  - Sensitive error text and raw internal metadata are not exposed.

Tests:
  python -m pytest tests/app/runtime/test_runtime_errors.py -q
  24 passed
  python -m pytest tests/app/runtime/test_runtime_current_baseline.py tests/app/runtime/test_runtime_contracts.py tests/app/runtime/test_runtime_serialization.py tests/app/runtime/test_runtime_errors.py tests/test_memory_runtime_adapter.py tests/test_memory_react_agent_adaptation.py -q
  69 passed
  python -m compileall -q src/app/runtime tests/app/runtime
  passed

Deferred:
  Runtime core execution, Factory, CLI/API adapters,
  and health checks remain deferred to their planned steps.
```

## Step 4 Completion Record (2026-08-24)

```text
Status: completed
Added:
  - src/app/runtime/pending_runs.py
  - tests/app/runtime/test_runtime_pending_runs.py
Updated:
  - src/app/runtime/__init__.py
Implemented:
  - Process-local PendingRunRegistry protected by RLock.
  - PendingRunRecord fields: session_id, run_id, executor_context,
    safe pending_confirmation, created_at, expires_at, owner, and metadata.
  - Atomic register, get, pop, remove, expire, clear, and length operations.
  - Session ownership checks and duplicate run_id conflict handling.
  - Configurable positive TTL with injectable clock and explicit expiry support.
  - Public snapshots exclude executor_context and use the Step 2
    pending-confirmation and metadata serialization boundaries.
  - Registry remains in-memory only; no SQLite, cross-process recovery,
    WebSocket queues, or force-stop behavior were added.
Verification:
  - python -m pytest tests/app/runtime/test_runtime_pending_runs.py -q
    11 passed
  - Runtime regression:
    python -m pytest tests/app/runtime/test_runtime_current_baseline.py
    tests/app/runtime/test_runtime_contracts.py
    tests/app/runtime/test_runtime_serialization.py
    tests/app/runtime/test_runtime_errors.py
    tests/app/runtime/test_runtime_pending_runs.py -q
    67 passed
  - Cross-layer regression:
    python -m pytest tests/test_memory_runtime_adapter.py
    tests/test_memory_react_agent_adaptation.py -q
    13 passed
  - python -m compileall -q src/app/runtime tests/app/runtime
    passed
Step 3 progress correction:
  - Step 3 status is explicitly marked completed above.
  - Step 3 implementation and verification record remains preserved below.
Known limitations:
  - Runtime.run, resume/cancel orchestration, CLI/API adapters,
    and health checks remain deferred to their planned steps.
Next:
  - Step 6 Runtime main execution chain.
```

## Step 5 Completion Record (2026-08-24)

```text
Status: completed
Added:
  - src/app/runtime/core.py
  - src/app/runtime/factory.py
  - tests/app/runtime/test_runtime_factory.py
  - src/agent/analyzer_config.py
  - src/agent/complexity_analyzer.py
  - src/agent/intent_classifier.py
  - src/agent/uncertainty_detector.py
Updated:
  - src/app/runtime/__init__.py
  - src/agent/analyzer/complexity_analyzer.py
  - src/agent/react_executor/__init__.py
Implemented:
  - RuntimeConfig and RuntimeFactory production/test assembly paths.
  - Partial dependency injection with SessionManager and
    RuntimeMemoryAdapter reuse.
  - Formal ReactAgent assembly with manage_memory=False.
  - Startup recovery through SessionManager.recover_interrupted_runs().
  - Idempotent Runtime.close() with deduplicated close handling.
  - dependency_init_failed mapping for factory initialization failures.
  - Thin Analyzer compatibility imports for the current package migration.
Verification:
  - python -m pytest tests/app/runtime/test_runtime_factory.py -q
    7 passed
  - Runtime, Memory, Agent, Analyzer, and Planner regression:
    115 passed
  - python -m compileall -q src/app/runtime src/agent tests/app/runtime
    passed
Scope:
  - Runtime.run, run_stream, resume, cancel, health checker,
    CLI/API adapters, and cross-process recovery remain deferred.
Migration note:
  - Analyzer and ReActExecutor compatibility exports preserve existing
    import paths while the Agent package is being reorganized.
Next:
  - Step 6 Runtime main execution chain.
```
