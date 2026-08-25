# Runtime 层开发步骤与进度（2）- 运行主链路

> 覆盖步骤：Step 6-11  
> 当前状态：Step 6-10 已完成，Step 11 待开发  
> 前置分卷：`Runtime层开发步骤与进度(1)-契约装配.md`  
> 上位设计：`Runtime架构与模块设计.md`、`Runtime运行流程与Memory集成设计.md`、`Runtime事件流与确认恢复设计.md`、`Runtime公共契约与数据模型设计.md`

本分卷实现 Runtime V1 的普通 run 主链路。完成本分卷后，Runtime 应能在测试中串起 Memory、ReactAgent、OutputFeedback、事件回调和 RuntimeResult，但仍不要求 CLI/API 可用。

---

## Step 6：Runtime core facade 与请求校验

**状态：已完成（2026-08-24）**

### 目标

建立 Runtime 主对象的公开 facade 和请求校验流程，为 run、run_stream、resume、cancel、session 查询等入口提供统一骨架。

### 对应设计文档

```text
Runtime架构与模块设计.md
  ## 2. 建议模块结构
  ## 3. Runtime 对象的职责
  ## 4. Runtime 内部层次
  ### 4.1 请求校验

Runtime公共契约与数据模型设计.md
  ## 2. RuntimeRequest
  ## 5. 状态定义

Runtime错误降级与健康检查设计.md
  ### 4.1 请求校验错误
```

### 前置条件

```text
Step 1 contracts 已完成。
Step 3 errors 已完成。
Step 5 factory 至少能构建 Runtime。
```

### 涉及文件

```text
新增/修改:
  src/app/runtime/core.py
  tests/app/runtime/test_runtime_core_validation.py
```

### 必做

1. 定义 Runtime 主类及公开方法骨架：

```text
run(request)
run_stream(request, event_sink)
resume(request)
cancel(request)
get_session(session_id)
list_sessions()
get_timeline(session_id)
delete_session(session_id)
export_session(session_id, output_path=None)
health()
close()
```

2. 实现 RuntimeRequest 基础校验：

```text
input 非空
session_id 格式
metadata 类型和大小
debug/stream 类型
model_profile / agent_version 基础类型
```

3. 校验失败返回 RuntimeResult 或抛出 RuntimeException 的策略必须统一。
4. Runtime 不保存当前 session_id/run_id 到实例属性。
5. 为后续 run 主链路预留 request context，但不让其成为进程级共享状态。

### 明确不做

```text
不调用 ReactAgent。
不写 Memory。
不实现真实 run_stream。
不实现 CLI/API。
```

### 测试与验收

```powershell
python -m pytest tests/app/runtime/test_runtime_core_validation.py -q
```

测试至少覆盖：

```text
空 input -> validation_error。
非法 session_id -> validation_error。
metadata 非 dict -> validation_error。
Runtime 实例不持有当前 session_id/run_id。
公开方法存在且签名稳定。
```

### 完成后回写

记录公开方法签名、校验规则和与设计不一致的调整。

### Step 6 完成记录（2026-08-24）

```text
状态: 已完成
修改:
  - src/app/runtime/core.py
新增:
  - tests/app/runtime/test_runtime_core_validation.py
实现:
  - Runtime 提供 run、run_stream、resume、cancel、session/timeline/
    delete/export、health、close 的公开 facade 签名。
  - run / run_stream 在本 Step 仅建立局部 _RuntimeRequestContext 并执行
    入口校验；不调用 Memory、ReactAgent、Models、Tools 或外部 sink。
  - RuntimeRequest 入口复核 input、session_id、stream/debug、metadata、
    model_profile、agent_version，避免可变 dataclass 在构造后绕过 contracts
    基础校验。
  - metadata 限制为最多 50 个顶层字段、16 KiB 安全序列化大小，并拒绝
    API Key、Token、Cookie、密码等受限字段。
  - run / run_stream / resume / cancel 的校验失败统一返回
    RuntimeResult(status=failed, error_code=validation_error)；非运行型
    facade 的无效参数抛出 RuntimeException(validation_error)。
  - Runtime 未保存当前 session_id 或 run_id；请求上下文仅存在于调用栈。
设计调整:
  - 设计文档没有给出具体 input、metadata 限额。本实现将 input 限制为
    32,000 字符，将 metadata 限制为 50 个顶层字段和 16 KiB，作为入口层
    明确边界；model_profile 的注册验证仍留给后续 Models/运行接入步骤。
  - 有效请求当前返回 internal_error 的占位 RuntimeResult，明确表示普通
    运行链路尚未接入；这样既保持公开 run 的结果契约，也严格避免在 Step 6
    提前调用 Memory 或 ReactAgent。
测试:
  - python -m pytest tests/app/runtime/test_runtime_core_validation.py -q
    结果: 6 passed
  - python -m pytest tests/app/runtime/test_runtime_current_baseline.py
    tests/app/runtime/test_runtime_contracts.py
    tests/app/runtime/test_runtime_serialization.py
    tests/app/runtime/test_runtime_errors.py
    tests/app/runtime/test_runtime_pending_runs.py
    tests/app/runtime/test_runtime_factory.py -q
    结果: 74 passed
  - python -m compileall -q src/app/runtime tests/app/runtime
    结果: passed
  - python -m pytest tests/test_memory_runtime_adapter.py
    tests/test_memory_react_agent_adaptation.py -q
    结果: 13 passed
遗留:
  - Step 7 接入 RuntimeMemoryAdapter.begin_turn() 后替换有效 run 的占位
    结果，开始创建 session/run 和构建 context。
```

---

## Step 7：Memory begin_turn、session/run 创建与 context 接入

**状态：已完成（2026-08-24）**

### 目标

把 Runtime 普通 run 的前半段接入 Memory：创建或加载 session、写入 user message、创建 AgentRun、构建 context_text，并取得 `session_id` / `run_id`。

### 对应设计文档

```text
Runtime运行流程与Memory集成设计.md
  ## 3. 普通 run 的时序
  ## 4. begin_turn 阶段
  ## 5. Context 传递
  ## 6. 消息写入责任

Runtime架构与模块设计.md
  ### 4.2 会话运行协调

Runtime公共契约与数据模型设计.md
  ## 8. ID 责任
```

### 前置条件

```text
Step 6 已完成。
Step 0 已确认 RuntimeMemoryAdapter 真实接口。
```

### 涉及文件

```text
修改:
  src/app/runtime/core.py

新增/修改:
  tests/app/runtime/test_runtime_memory_begin_turn.py
```

### 必做

1. Runtime.run 调用 `RuntimeMemoryAdapter.begin_turn()`。
2. 不传 session_id 时由 Memory 生成新 session。
3. 传 session_id 时经过统一校验后传给 Memory。
4. user message 和 running AgentRun 由 Memory 创建。
5. Runtime 获取 context_text，并准备传给 ReactAgent。
6. Runtime 保存本轮 turn 对象到 request/run 局部变量，而不是 Runtime 实例共享字段。
7. 处理 Memory begin_turn 持久化降级：

```text
persistence_available=false
persistence_warning=...
仍可进入后续临时执行路径
```

### 明确不做

```text
不直接调用 SessionManager.repo。
不自己生成 run_id/message_id。
不在 CLI/API 拼接历史。
不触发 ReactAgent。
```

### 测试与验收

```powershell
python -m pytest tests/app/runtime/test_runtime_memory_begin_turn.py -q
```

测试至少覆盖：

```text
无 session_id 创建新 session。
指定 session_id 继续会话。
begin_turn 后 user message 存在。
run_id 由 Memory 返回。
context_text 传递准备正确。
Memory begin_turn 单次失败时 RuntimeResult 可携带 persistence_warning。
```

### 完成后回写

记录 Memory 调用参数、降级行为、实际测试结果和发现的接口偏差。

### Step 7 完成记录（2026-08-24）

```text
状态: 已完成
修改:
  - src/app/runtime/core.py
新增:
  - tests/app/runtime/test_runtime_memory_begin_turn.py
实现:
  - Runtime.run 在入口校验通过后调用
    RuntimeMemoryAdapter.begin_turn(session_id, input, user_metadata,
    agent_version, model_profile)。
  - 未提供 session_id 时，Runtime 传递 None，由 Memory 生成 session ID；
    已提供 session_id 时，继续使用 Step 6 的统一校验值。
  - Runtime 从 RuntimeMemoryTurn 获取 Memory 生成的 session_id、run_id、
    context_text 和 manage_memory=False，并仅保存在本次调用的
    _RuntimeRequestContext 中，未写入 Runtime 实例属性。
  - user message 和 running AgentRun 完全由 RuntimeMemoryAdapter /
    SessionManager 创建；Runtime 未访问 repo/SQL，未生成实体 ID。
  - begin_turn 返回 persistence_available=false 时，RuntimeResult 保留
    session_id、run_id、persistence_available 和 persistence_warning，为后续
    临时执行路径保留状态。
  - begin_turn 抛出异常时映射为 memory_unavailable；不调用 ReactAgent。
接口核对:
  - RuntimeMemoryTurn.react_agent_kwargs() 的真实字段是 context_text、
    session_id、run_id、manage_memory；本实现逐项核对 session/run 对应关系
    和 manage_memory=False。
  - ContextBuilder 已在 RuntimeMemoryAdapter.begin_turn() 内组织 summary +
    recent_messages + current_input；Runtime 不额外拼接历史。
测试:
  - python -m pytest tests/app/runtime/test_runtime_memory_begin_turn.py -q
    结果: 5 passed
  - python -m pytest tests/app/runtime/test_runtime_core_validation.py
    tests/app/runtime/test_runtime_factory.py
    tests/test_memory_runtime_adapter.py
    tests/test_memory_context_builder.py
    tests/test_memory_react_agent_adaptation.py -q
    结果: 32 passed
  - python -m compileall -q src/app/runtime tests/app/runtime
    结果: passed
遗留:
  - 有效 run 当前仍返回占位 internal_error，因为 Step 8 才允许调用
    ReactAgent.run_with_result() 和 OutputFeedbackProcessor。
  - Step 7 不创建 event callback、不完成或失败 Memory turn、不读取 timeline。
```

---

## Step 8：ReactAgent 正式 Runtime 模式调用与 OutputFeedback

**状态：已完成（2026-08-24）**

### 目标

实现 Runtime 对 ReactAgent 的正式调用路径，确保 `manage_memory=False`，并把 ExecutionResult 转为 OutputFeedback。

### 对应设计文档

```text
Runtime运行流程与Memory集成设计.md
  ## 2. 正式 Runtime 模式
  ## 3. 普通 run 的时序
  ## 6. 消息写入责任

Runtime架构与模块设计.md
  ### 4.3 Agent 协调
  ### 4.4 结果协调
  ## 7. 兼容模式

Runtime公共契约与数据模型设计.md
  ## 3. RuntimeResult
```

### 前置条件

```text
Step 7 已完成。
Step 0 已确认 ReactAgent 和 OutputFeedbackProcessor 真实接口。
```

### 涉及文件

```text
修改:
  src/app/runtime/core.py

新增/修改:
  tests/app/runtime/test_runtime_agent_invocation.py
```

### 必做

1. 调用 ReactAgent 时传入：

```text
user_input
context_text
session_id
run_id
manage_memory=False
event_callback
event_callback_visible_only
```

2. 不调用 ReactAgent 旧兼容写消息路径。
3. 支持注入 fake ReactAgent，用于测试断言参数。
4. 获取 ExecutionResult 后调用 OutputFeedbackProcessor。
5. RuntimeResult 同时保留安全序列化后的 execution_result 和 output_feedback。
6. Runtime 不自行拼最终自然语言回答，默认使用 ExecutionResult / OutputFeedback 的结果。

补充实现：

```text
Runtime 复用 src/app/runtime/serialization.py 的既有安全序列化边界，
没有新增底层对象的直接 to_dict() 暴露路径。
RuntimeResult 的 pending_confirmation 使用白名单序列化。
```

### 明确不做

```text
不实现 resume。
不实现 run_stream 实时生成器。
不让 ReactAgent 直接写 SQLite 消息。
不直接调用 Analyzer / Planner / ReActExecutor。
```

### 测试与验收

```powershell
python -m pytest tests/app/runtime/test_runtime_agent_invocation.py -q
```

测试至少覆盖：

```text
ReactAgent 收到 manage_memory=False。
ReactAgent 收到 session_id/run_id/context_text。
fake short_term_memory 未被写入 user/assistant。
ExecutionResult 被保留。
OutputFeedback 被构建。
```

推荐回归：

```powershell
python -m pytest tests/test_memory_react_agent_adaptation.py tests/test_react_agent_with_react_executor.py -q
```

### 完成后回写

记录 ReactAgent 实际调用参数、OutputFeedback 真实字段和测试结果。

### Step 8 完成记录（2026-08-24）

```text
状态：已完成
修改：
  - src/app/runtime/core.py
  - tests/app/runtime/test_runtime_agent_invocation.py
  - tests/app/runtime/test_runtime_memory_begin_turn.py

实现：
  - Runtime.run 在 Memory begin_turn 成功后调用
    ReactAgent.run_with_result(request.input, ...)，传入：
      context_text
      session_id
      run_id
      manage_memory=False
      event_callback
      event_callback_visible_only=True
  - Runtime 创建本次 run 的局部事件回调；本 Step 只做可见事件筛选和可选
    sink 转发，不提前实现 Memory 事件持久化、RuntimeEvent 包装或去重。
  - Agent 缺少 run_with_result 时映射为 dependency_init_failed；
    Agent 执行或 OutputFeedback 构建异常映射为 agent_execution_failed。
  - ExecutionResult 交给 OutputFeedbackProcessor.build(
    include_internal=False, group_related=True)。
  - RuntimeResult.output 优先使用 OutputFeedback.final_output，其次使用
    ExecutionResult.output；不在 Runtime 内拼接自然语言回答。
  - RuntimeResult 保留 execution_result、output_feedback 的安全序列化结果，
    pending_confirmation 使用白名单预览序列化，并保留本轮持久化降级标记。
  - 更新 Step 7 旧占位测试，使其适配已进入 Step 8 但尚未 complete/fail
    收口的实际状态；ReactAgent 旧兼容模式未修改。

真实 OutputFeedback 字段：
  execution_id、plan_id、status、success、final_output、summary、
  requires_user_input、user_input_request、request_replan、replan_reason、
  pending_confirmation、timeline、items。

测试：
  - python -m pytest tests/app/runtime/test_runtime_agent_invocation.py -q
    结果：2 passed
  - python -m pytest tests/app/runtime -q
    结果：87 passed
  - python -m pytest tests/test_memory_react_agent_adaptation.py
      tests/test_react_agent_with_react_executor.py -q
    结果：18 passed
  - python -m pytest tests/test_output_feedback_processor.py -q
    结果：2 passed
  - python -m compileall -q src/app/runtime tests/app/runtime
    结果：passed

偏差和遗留：
  - event_callback 当前仍是 run-local 适配回调；Memory event callback、
    RuntimeEvent sequence、持久化幂等和 sink 异常策略留给 Step 9。
  - Runtime.run 当前尚未调用 Memory complete_turn/fail_turn，也未登记
    waiting_user pending run；结果收口留给 Step 10。
  - run_stream、resume 和 CLI/API 仍按步骤文档保持未实现。

下一步：
  - Step 9：ExecutionEvent 回调、RuntimeEvent 包装与去重。
```

---

## Step 9：ExecutionEvent 回调、RuntimeEvent 包装与去重

**状态：已完成（2026-08-24）**

### 目标

实现 Runtime 对 ReActExecutor 事件回调的统一接管：保存到 Memory、包装为 RuntimeEvent、按可见性转发给外部 sink，并避免重复保存或重复发送。

### 对应设计文档

```text
Runtime事件流与确认恢复设计.md
  ## 1. 事件回调的含义
  ## 2. 事件处理链
  ## 3. 事件可见性
  ## 4. 事件分发规则
  ## 5. RuntimeEvent sequence
  ## 6. 事件 sink 异常

Runtime运行流程与Memory集成设计.md
  ## 7. 事件写入

Runtime公共契约与数据模型设计.md
  ## 4. RuntimeEvent
```

### 前置条件

```text
Step 2 serialization 已完成。
Step 7 Memory begin_turn 已完成。
Step 8 ReactAgent 调用路径已完成。
```

### 涉及文件

```text
修改:
  src/app/runtime/core.py
  src/app/runtime/serialization.py

可新增:
  src/app/runtime/events.py

新增/修改:
  tests/app/runtime/test_runtime_events.py
```

### 必做

1. Runtime 创建 event callback 并传给 ReactAgent。
2. callback 内调用 Memory event callback 或 RuntimeMemoryAdapter.record_event。
3. callback 将事件转换为 RuntimeEvent。
4. RuntimeEvent sequence 在单个 run 内从 1 递增。
5. 默认只向外部 sink 转发 `visible_to_user=True` 事件。
6. debug 模式也必须经过脱敏。
7. 外部 sink 失败不导致 Agent 执行失败。
8. 如果 ReactAgent 已通过 callback 发送事件，不能再从 ExecutionResult.events 重复保存和重复发送。

### 明确不做

```text
不让内部事件进入普通 timeline。
不输出 raw_observation/raw_prompt。
不实现 WebSocket 协议。
不实现 CLI 渲染。
```

### 测试与验收

```powershell
python -m pytest tests/app/runtime/test_runtime_events.py -q
```

测试至少覆盖：

```text
可见事件进入 Memory timeline。
内部事件不进入普通 timeline。
外部 sink 只收到可见事件。
sequence 递增。
sink 抛异常不破坏 run。
callback 已使用时不重复持久化 result.events。
```

推荐回归：

```powershell
python -m pytest tests/test_memory_event_mapper.py tests/test_react_executor_events.py -q
```

### 完成后回写

记录事件可见性规则、去重策略、sink 异常策略和测试结果。

### Step 9 完成记录（2026-08-24）

```text
状态：已完成

修改：
  - src/app/runtime/core.py
  - src/app/runtime/events.py
  - src/app/runtime/__init__.py
  - tests/app/runtime/test_runtime_events.py
  - tests/app/runtime/test_runtime_agent_invocation.py

实现：
  - Runtime 接管 ReactAgent 的 event_callback，并将 ExecutionEvent 先交给
    RuntimeMemoryAdapter.record_event，再构造安全 RuntimeEvent。
  - Memory event mapper 负责可见性过滤、脱敏和持久化；内部事件不会进入普通
    timeline，也不会发送到外部 sink。
  - RuntimeEvent 的 sequence 在单个 run 内从 1 递增；以 source event_id 为主，
    缺失时使用安全字段指纹去重。ExecutionResult.events 仅用于补发没有经过
    callback 的旧兼容执行路径。
  - RuntimeEvent 的 message、payload、source_event 均经过 safe_serialize；
    debug 不绕过 raw prompt、hidden reasoning、raw tool result、凭据等敏感
    字段的过滤。
  - CLI/API sink 异常与 Memory 事件持久化异常均隔离于 Agent 主执行；持久化
    异常通过本轮 persistence_available=false 和安全 warning 降级。
  - 未实现 Step 10 的 complete/fail/waiting_user 收口，也未实现 resume、
    CLI/API/WebSocket。

测试：
  - python -m pytest tests/app/runtime/test_runtime_events.py tests/app/runtime/test_runtime_agent_invocation.py -q
    结果：6 passed
  - python -m pytest tests/app/runtime -q
    结果：91 passed
  - python -m pytest tests/test_memory_event_mapper.py tests/test_react_executor_events.py -q
    结果：28 passed
  - python -m compileall -q src/app/runtime tests/app/runtime
    结果：passed

偏差和遗留：
  - Memory 的 execution_events.event_id 是全局主键；测试夹具已使用跨 run
    唯一的源 event_id，Runtime 的去重仍严格保持 run-local。
  - 下一步进入 Step 10，处理 RuntimeResult 收口及 Memory complete/fail/
    waiting_user/replan 状态路径。
```

---

## Step 10：RuntimeResult 收口、complete/fail/waiting/replan 处理

**状态：已完成（2026-08-25）**

### 目标

完成 Runtime.run 的结果收口：根据 ExecutionResult 状态调用 Memory complete/fail 或登记 waiting_user，并生成完整 RuntimeResult。

### 对应设计文档

```text
Runtime运行流程与Memory集成设计.md
  ## 8. 完成路径
  ## 9. 失败路径
  ## 10. waiting_user 路径
  ## 11. request_replan 路径

Runtime公共契约与数据模型设计.md
  ## 3. RuntimeResult
  ## 5. 状态定义

Runtime错误降级与健康检查设计.md
  ## 3. 状态与错误的关系
  ## 4. 异常分类
```

### 前置条件

```text
Step 4 pending registry 已完成。
Step 8 ReactAgent 调用已完成。
Step 9 事件流已完成。
```

### 涉及文件

```text
修改:
  src/app/runtime/core.py
  src/app/runtime/errors.py
  src/app/runtime/serialization.py

新增/修改:
  tests/app/runtime/test_runtime_result_finalization.py
```

### 必做

1. `completed`：

```text
调用 memory.complete_turn()
保存 assistant message
保存 run completed
返回 success=True
```

2. `failed` / 未分类异常：

```text
调用 memory.fail_turn()
返回 success=False
error_code=agent_execution_failed 或更具体错误
```

3. `blocked`：

```text
转换为 blocked_by_policy
保留安全 output/error
```

4. `waiting_user`：

```text
登记 PendingRunRegistry
返回 pending_confirmation 安全摘要
requires_user_input=True
```

5. `request_replan`：

```text
返回 request_replan=True
保留 replan_reason 安全摘要
V1 不自动无限重规划
```

6. Memory complete/fail 降级：

```text
persistence_available=false
persistence_warning=...
仍返回本轮临时结果
```

7. RuntimeResult 必须包含 session_id、run_id、output、execution_result、output_feedback、memory_result、timeline。

### 明确不做

```text
不实现跨进程断点续跑。
不自动重规划循环。
不把 waiting_user 当失败。
不吞掉 persistence_warning。
```

### 测试与验收

```powershell
python -m pytest tests/app/runtime/test_runtime_result_finalization.py -q
```

测试至少覆盖：

```text
completed complete_turn。
failed fail_turn。
blocked_by_policy 映射。
waiting_user pending_confirmation 和 registry。
request_replan 字段。
persistence_warning 保留。
timeline 返回。
```

### 完成后回写

记录各状态处理、Memory 调用结果和 RuntimeResult 最终字段。

### Step 10 完成记录（2026-08-25）

```text
状态：已完成

修改：
  - src/app/runtime/core.py
  - tests/app/runtime/test_runtime_memory_begin_turn.py
  - tests/app/runtime/test_runtime_result_finalization.py

实现：
  - completed 路径调用 RuntimeMemoryAdapter.complete_turn()，
    写入 assistant message，收口 run=completed，并返回 timeline。
  - failed 和未分类 Agent 异常调用 RuntimeMemoryAdapter.fail_turn()，
    保留 agent_execution_failed 或更具体的 Runtime 错误码。
  - blocked 映射为 status=blocked、error_code=blocked_by_policy。
  - waiting_user 不进入 fail_turn()，安全序列化 pending_confirmation，
    设置 requires_user_input=true，并登记 PendingRunRegistry。
  - request_replan 保留 request_replan=true 和安全 replan_reason，
    不在 Runtime 内自动重规划。
  - complete/fail 持久化失败时保留临时结果，返回
    persistence_available=false 和 persistence_warning。
  - RuntimeResult 统一补齐 session_id、run_id、output、
    execution_result、output_feedback、memory_result 和 timeline。
  - 修正测试夹具对空 PendingRunRegistry 的布尔判断，确保验证的是
    Runtime 实际登记的 registry 实例。

测试：
  - python -m pytest tests/app/runtime/test_runtime_result_finalization.py -q
    结果：6 passed
  - python -m pytest tests/app/runtime -q
    结果：97 passed
  - python -m pytest tests/test_memory_runtime_adapter.py
      tests/test_memory_v1_end_to_end_acceptance.py
      tests/test_memory_react_agent_adaptation.py -q
    结果：19 passed
  - python -m compileall -q src/app/runtime tests/app/runtime
    结果：passed

偏差：
  - 当前 RuntimeMemoryAdapter 没有独立 wait_turn() 公共接口。
    为避免把 waiting_user 错误收口为 failed，本 Step 保留 run 的
    running 状态，并将等待状态交给 PendingRunRegistry 表达。

遗留：
  - Step 12 resume / Step 13 cancel 需要定义 waiting run 的持久化状态
    更新、pending registry 清理和恢复失败路径。
  - Step 11 仍需补充普通 run 集成验收。
```

---

## Step 11：Runtime 普通 run 主链路集成测试

**状态：待开发**

### 目标

用 fake 依赖和最小真实 Memory 验证 Runtime 普通 run 主链路闭环，为后续 resume/health/session facade 开发提供稳定基础。

### 对应设计文档

```text
Runtime架构与模块设计.md
  ## 5. 一轮普通运行的核心顺序

Runtime运行流程与Memory集成设计.md
  ## 3. 普通 run 的时序

Runtime公共契约与数据模型设计.md
  ## 9. 跨层约束
```

### 前置条件

```text
Step 6-10 已完成。
```

### 涉及文件

```text
新增/修改:
  tests/app/runtime/test_runtime_run.py
  tests/app/runtime/test_runtime_run_integration.py
```

### 必做

1. 覆盖新 session 普通对话。
2. 覆盖指定 session 多轮对话。
3. 覆盖 Memory context 进入 ReactAgent。
4. 覆盖 manage_memory=False 不重复写消息。
5. 覆盖可见事件进入 timeline。
6. 覆盖内部事件不进入 timeline。
7. 覆盖 RuntimeResult 完整字段。
8. 使用 fake model / fake tool / fake ReactAgent 或临时 SQLite，避免单元测试依赖真实 Provider。

### 明确不做

```text
不启动 CLI。
不启动 FastAPI。
不调用真实外部模型。
不测试 WebSocket。
```

### 测试与验收

```powershell
python -m pytest tests/app/runtime/test_runtime_run.py tests/app/runtime/test_runtime_run_integration.py -q
```

建议回归：

```powershell
python -m pytest tests/test_memory_runtime_adapter.py tests/test_memory_v1_end_to_end_acceptance.py tests/test_memory_react_agent_adaptation.py -q
```

完成标准：

```text
Runtime 普通 run 闭环稳定。
没有重复 user/assistant message。
RuntimeResult 可被 CLI/API 后续直接消费。
```

### 完成后回写

记录集成测试结果、发现的跨层偏差、是否需要修改设计或其他层。
