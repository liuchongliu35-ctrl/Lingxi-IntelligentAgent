# Runtime 公共契约与数据模型设计

## 1. 设计目标

Runtime 同时服务 CLI、REST API、WebSocket 和测试，因此必须提供稳定、可序列化、与底层对象解耦的公共契约。

公共契约不应直接暴露 SQLite model、ReActExecutor 内部 context 或 Provider 返回对象。

## 2. RuntimeRequest

普通运行请求建议包含：

```text
input: str
session_id: str | None
stream: bool
debug: bool
metadata: dict
model_profile: str | None
agent_version: str | None
```

约束：

1. `input` 必须是非空文本。
2. `metadata` 只允许入口级、可脱敏信息。
3. 不允许通过 metadata 传递 API Key、Token、Cookie、密码或完整原始 prompt。
4. `model_profile` 只能选择 Models 层已注册的 profile。
5. `session_id` 经过统一 ID 校验后才能传给 Memory。

## 3. RuntimeResult

第一版固定包含以下字段：

```text
success: bool
status: str
session_id: str | None
run_id: str | None
output: str
execution_result: dict | None
output_feedback: dict | None
memory_result: dict | None
timeline: list[dict]
requires_user_input: bool
pending_confirmation: dict | None
request_replan: bool
replan_reason: str | None
error_code: str | None
error_message: str | None
persistence_available: bool
persistence_warning: str | None
metadata: dict
```

### 3.1 字段语义

- `success`：本轮是否以成功结果结束。waiting_user 不应被错误当成 completed。
- `status`：统一使用 `completed`、`failed`、`blocked`、`waiting_user`、`request_replan`、`cancelled`、`interrupted` 等状态。
- `output`：默认给用户展示的文本。
- `execution_result`：经过安全序列化的执行器结构化结果。
- `output_feedback`：经过安全序列化的用户可见反馈。
- `memory_result`：Memory 完成本轮或失败本轮的摘要结果。
- `timeline`：按 Memory 规则映射和脱敏后的会话 timeline。
- `pending_confirmation`：仅在等待用户确认时存在，必须只包含安全预览信息。
- `persistence_available`：本轮是否能够正常持久化。
- `persistence_warning`：持久化降级时的提示，不得泄露数据库路径中的敏感信息或内部堆栈。
- `metadata`：稳定的非敏感诊断信息。

## 4. RuntimeEvent

Runtime 对外使用轻量包装事件：

```text
event_id: str | None
session_id: str
run_id: str
event_type: str
message: str
visible_to_user: bool
payload: dict
source_event: dict | None
sequence: int
created_at: str
```

规则：

1. `RuntimeEvent` 不是新的执行事件体系。
2. `event_type` 和原始 ExecutionEvent 的含义保持一致。
3. `source_event` 默认只保留安全的、裁剪后的来源信息；不直接暴露原始对象。
4. 事件的 `sequence` 在一个 run 内递增。
5. 内部事件可以在 Runtime 内部传递，但默认不能输出给用户，也不能进入普通 timeline。
6. `payload` 必须经过白名单字段过滤和敏感内容脱敏。

## 5. 状态定义

```text
completed
  Agent 已完成并产生最终输出。

failed
  运行发生未归类为 blocked 或 cancelled 的失败。

blocked
  Tools / policy / safety 阻止了操作。

waiting_user
  ReActExecutor 需要用户确认或补充输入。

request_replan
  执行器要求上层重新规划。V1 不在 Runtime 内无限自动重规划。

cancelled
  用户取消了等待确认的 run。

interrupted
  进程恢复时发现旧 run 处于 pending/running/waiting_user。
```

## 6. 序列化规则

Runtime 序列化层必须支持：

- dataclass
- Enum
- datetime
- Mapping
- list/tuple
- ReActExecutor 的结果对象
- Memory 的 SessionState、Message、TimelineItem

无法安全序列化的对象不得直接 `str()` 后返回给用户；应转换成有限的类型名或安全摘要。

## 7. 调试字段

只有显式 `debug=True` 时，`metadata.debug` 才可以包含：

```text
analyzer_summary
plan_summary
event_count
model_profile
tool_profile
```

无论 debug 是否开启，都禁止输出：

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
```

## 8. ID 责任

```text
session_id / run_id / message_id / event_id / summary_id
  -> 由 Memory / SessionManager 生成和管理
Runtime
  -> 接收、校验、传递和返回
```

Runtime 不自行生成数据库实体 ID。RuntimeEvent 的包装序号可以由 Runtime 维护，但不能替代 Memory 的 event_id。

## 9. 跨层约束

开发时必须核对：

- Memory `RuntimeMemoryTurn.react_agent_kwargs()` 的真实返回字段。
- ReactAgent `run_with_result()` 和 `run_stream()` 的真实参数。
- ReActExecutor `ExecutionResult`、`ExecutionEvent`、`PendingConfirmation` 的真实字段。
- OutputFeedbackProcessor 的输入和输出。
- Memory event mapper 的可见性和脱敏规则。

如果本文档字段与代码不一致，先以实际公开接口为事实来源，再通过适配层统一为本文档契约。

