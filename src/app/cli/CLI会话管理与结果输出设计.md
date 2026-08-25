# CLI 会话管理与结果输出设计

## 1. sessions

```text
agent sessions
```

默认人类可读表格至少显示：

```text
session_id
title
status
created_at
last_activity_at
message_count
last_run_status
```

`--json` 返回 Runtime/Memory 的完整安全结构。

## 2. session show

```text
agent session show <session_id>
```

展示：

- session 基本信息。
- 状态。
- 创建和最后活动时间。
- 消息数量。
- 最近 run 摘要。

不默认把全部隐藏字段或 raw metadata 打印出来。

## 3. timeline

```text
agent timeline <session_id>
```

展示 Memory 已映射、脱敏和排序后的 timeline。

CLI 不自己重新组合数据库消息和事件，也不把内部事件补回展示结果。

## 4. export

```text
agent export <session_id> --output <path>
```

V1 导出 Markdown：

```text
# Session
...

## User
...

## Assistant
...

## Execution Events
...
```

导出内容来自 Runtime 通过 Memory 获取的安全 session/timeline 数据。

导出规则：

- 只导出用户可见消息和事件。
- 过滤敏感字段。
- 不导出隐藏推理、raw prompt、raw tool result。
- 输出路径必须经过 workspace/path policy 校验。
- 文件已存在时是否覆盖应由明确参数控制，默认不静默覆盖。

## 5. delete-session

```text
agent delete-session <session_id>
```

V1 采用硬删除：

- 删除 session 及其 messages、runs、events、summaries。
- 删除前要求交互确认。
- `--yes` 后续可作为显式自动确认选项。
- 删除失败必须返回明确错误。

删除后不能继续使用该 session_id 读取历史。

## 6. CLI 退出码

```text
0  completed / waiting_user / request_replan
1  validation_error / not_found / session_conflict
2  blocked / cancelled / interrupted
3  memory_unavailable / dependency_init_failed / internal_error
```

具体映射以 Runtime 错误设计为准。

## 7. 必须联动阅读

- Memory SessionInfo、TimelineItem、delete_session 和导出相关能力。
- Tools path policy 和文件写入安全规则。
- Runtime 序列化和错误契约。

