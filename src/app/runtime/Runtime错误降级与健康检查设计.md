# Runtime 错误降级与健康检查设计

## 1. 目标

Runtime 对底层异常做统一的外部包装，但不掩盖真实状态。CLI 和 API 应能根据稳定的状态、错误码和 warning 做处理。

## 2. RuntimeErrorCode

第一版至少包括：

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

底层错误码可以保存在受控的 debug metadata 中，但对外主错误码由 Runtime 统一。

## 3. 状态与错误的关系

状态表示运行生命周期，错误码表示原因。两者不能混为一谈：

```text
status=blocked
error_code=blocked_by_policy

status=waiting_user
error_code=waiting_user

status=failed
error_code=agent_execution_failed
```

## 4. 异常分类

### 4.1 请求校验错误

例如：

- 输入为空。
- session_id 格式错误。
- run_id 和 session 不匹配。
- resume 缺少 confirmation_id。

映射：

```text
status=failed
error_code=validation_error
```

### 4.2 Memory 错误

如果无法读取 session 或创建 turn：

```text
memory_unavailable
```

如果单次保存失败但 Memory 能提供临时结果：

```text
status 仍按 Agent 结果处理
persistence_available=false
error_code=persistence_warning（或仅设置 warning）
```

### 4.3 Agent、Models、Tools 错误

Runtime 应先识别已有公开错误协议，再转换：

- policy/safety 阻断 -> `blocked_by_policy`。
- 需要用户确认 -> `waiting_user`。
- 执行器请求重规划 -> `request_replan`。
- 其他执行失败 -> `agent_execution_failed`。
- 无法归类 -> `internal_error`，并写入受控日志。

Runtime 不应吞掉原始异常后返回一个成功字符串。

## 5. HTTP 状态码映射

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

错误响应仍使用 API Result 包装体。

## 6. CLI 退出码映射

```text
completed / success       -> 0
waiting_user              -> 0
request_replan            -> 0
validation_error          -> 1
session_not_found         -> 1
run_not_found             -> 1
session_conflict          -> 1
blocked_by_policy         -> 2
cancelled                 -> 2
interrupted               -> 2
memory_unavailable        -> 3
dependency_init_failed    -> 3
internal_error            -> 3
```

人类可读输出和退出码必须同时保留。`--json` 模式不能因为输出 JSON 就改变退出码语义。

## 7. Health 检查

Runtime `health()` 应至少检查：

```text
runtime_initialized
memory
database
models
tools
react_agent
workspace
```

每项至少包含：

```text
name
status: healthy / degraded / unavailable
message
metadata
```

健康检查不得调用会产生真实副作用的工具。Models 健康检查应遵循 Models 层已有的轻量 verify 机制，不能无条件消耗真实 Provider 配额。

整体健康状态建议：

```text
healthy
  所有核心依赖可用。

degraded
  例如持久化暂时不可用，但 Runtime 可提供临时结果。

unavailable
  核心依赖无法运行，不能接受普通 run。
```

## 8. 日志和敏感信息

可以记录：

- trace_id。
- session_id、run_id。
- 状态和错误码。
- 事件数量。
- 依赖名称。
- 受控的错误摘要。

禁止记录：

- API Key、Token、Cookie、密码。
- raw prompt。
- hidden reasoning。
- raw tool result。
- 未脱敏的命令参数和环境变量。

## 9. 必须联动阅读

开发前必须检查：

- Models 错误协议、健康检查和 retry/fallback。
- Tools policy、安全、错误和输出控制。
- Memory 持久化降级、日志和恢复设计。
- ReActExecutor status、error_code、blocked、waiting_user、request_replan 字段。

