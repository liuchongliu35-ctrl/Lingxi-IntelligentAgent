# Runtime V1 交接说明

## 1. 已完成范围

Runtime V1 已完成以下核心能力：

- 统一依赖装配与生命周期管理
- `RuntimeRequest` / `RuntimeResult` / `RuntimeEvent` / `ResumeRequest` / `CancelRequest`
- 普通 run 主链路
- Memory `begin_turn` / `complete_turn` / `fail_turn`
- `waiting_user`、resume、cancel
- session / timeline / delete / export facade
- startup recovery、close、health、错误映射
- 跨层回归测试

对应设计文档已全部落地到当前实现：

- [Runtime架构与模块设计.md](./Runtime架构与模块设计.md)
- [Runtime公共契约与数据模型设计.md](./Runtime公共契约与数据模型设计.md)
- [Runtime依赖装配与生命周期设计.md](./Runtime依赖装配与生命周期设计.md)
- [Runtime运行流程与Memory集成设计.md](./Runtime运行流程与Memory集成设计.md)
- [Runtime事件流与确认恢复设计.md](./Runtime事件流与确认恢复设计.md)
- [Runtime错误降级与健康检查设计.md](./Runtime错误降级与健康检查设计.md)

## 2. 稳定公开入口

```text
Runtime.run(request) -> RuntimeResult
Runtime.run_stream(request, event_sink) -> reserved / validation-only in V1
Runtime.resume(request) -> RuntimeResult
Runtime.cancel(request) -> RuntimeResult
Runtime.get_session(session_id)
Runtime.list_sessions()
Runtime.get_timeline(session_id)
Runtime.delete_session(session_id)
Runtime.export_session(session_id, output_path=None)
Runtime.health()
Runtime.close()
```

## 3. 稳定契约

### RuntimeRequest

```text
input
session_id
stream
debug
metadata
model_profile
agent_version
```

### RuntimeResult

```text
success
status
session_id
run_id
output
execution_result
output_feedback
memory_result
timeline
requires_user_input
pending_confirmation
request_replan
replan_reason
error_code
error_message
persistence_available
persistence_warning
metadata
```

### RuntimeEvent

```text
session_id
run_id
event_type
message
visible_to_user
payload
source_event
sequence
event_id
created_at
```

## 4. CLI/API 交接规则

CLI/API 后续必须遵守：

1. 只共享同一个 Runtime，不各自复制主链路。
2. 正式模式保持 `ReactAgent(manage_memory=False)`。
3. 不直接访问 SQLite、SQL、Model Provider 或工具执行器。
4. 不把 raw prompt、hidden reasoning、raw tool result、API Key、Token、Cookie、密码带到普通输出。
5. 只消费 RuntimeResult / RuntimeEvent / health 的稳定字段。
6. 继续尊重 Memory 的脱敏、timeline 和持久化降级边界。

## 5. 已知限制

```text
不支持跨进程断点续跑。
不强制终止正在运行的工具进程。
不实现 CLI/API 具体入口。
不实现 API 认证。
run_stream 在 V1 仍是保留入口，不是完整流式运行通道。
pending_user 的恢复仍依赖同进程 PendingRunRegistry。
```

## 6. 验收结论

Runtime V1 核心设计已实现并通过回归验证。  
后续 CLI/API 应按本说明对接，不再重新实现 Agent 主链路。
