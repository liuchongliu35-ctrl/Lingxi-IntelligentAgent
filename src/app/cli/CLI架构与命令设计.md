# CLI 架构与命令设计

## 1. 定位

CLI 是 Runtime 的命令行适配器。CLI 负责解析参数、展示结果和承载终端交互，不负责装配 Agent，也不直接访问 Memory、Models、Tools 或 SQLite。

建议使用 Typer。

## 2. 建议目录

```text
src/app/cli/
  __init__.py
  main.py             # Typer 根应用
  commands_chat.py   # chat 和 REPL
  commands_session.py
  commands_system.py  # health
  commands_export.py
  rendering.py        # 文本、JSON、事件展示
  prompts.py          # y/n、输入读取和确认
  exit_codes.py
```

文件名可根据实现调整，但命令定义、输出渲染和 Runtime 调用应保持分离。

## 3. 命令总览

V1 至少支持：

```text
agent chat
agent chat "用户输入"
agent chat --session-id <id>
agent sessions
agent session show <id>
agent timeline <id>
agent health
agent export <id> --output <path>
agent delete-session <id>
```

可以提供：

```text
agent resume <run_id>
agent cancel <run_id>
```

但 resume/cancel 的具体行为必须复用 Runtime，不在 CLI 中直接调用 ReActExecutor。

## 4. chat 语义

### 4.1 交互式 REPL

```text
agent chat
```

行为：

1. 创建一个新 session。
2. 进入循环读取用户输入。
3. 每条输入创建同一 session 下的新 run。
4. 每一轮都从 Memory 获取该 session 的最新 context。
5. REPL 退出后，session 和历史仍然保存在 SQLite。

```text
agent chat --session-id <id>
```

行为：

1. 加载指定 session。
2. 展示必要的会话信息。
3. 在该 session 内进入多轮 REPL。

### 4.2 单次输入

```text
agent chat "总结 README"
```

没有 session_id 时：

- 创建一个新 session。
- 执行一个 run。
- 输出结果后退出。

指定 session_id 时：

```text
agent chat --session-id <id> "继续上次任务"
```

- 在指定 session 中创建新 run。
- 使用该 session 历史上下文。

单次命令默认不偷偷复用上一次 session。这样可以避免不同任务上下文混淆。

## 5. session 语义

```text
session
  多轮会话容器。

run
  一次用户输入触发的一轮 Agent 执行。

message
  user 或 assistant 消息。

event
  一轮执行过程中的可回放事件。
```

用户每次都执行：

```text
agent chat "输入"
```

默认得到不同 session。要继续已有会话，必须使用：

```text
agent chat --session-id <id> "输入"
```

## 6. 可选的 session 选择

如果 CLI 允许用户提供要创建的 session_id：

1. 校验格式和长度。
2. 如果不存在，按该 ID 创建。
3. 如果已存在，展示 session 的基本信息。
4. 交互模式询问 `y/n` 是否进入已有 session。
5. 选择 `y` 执行切换。
6. 选择 `n` 忽略用户传入 ID，按正常流程生成新 session。

非交互模式不能等待输入。应返回 `session_conflict`，并要求调用方显式提供继续使用已有 session 的参数。

## 7. CLI 参数建议

普通 chat 可支持：

```text
--session-id TEXT
--json
--debug
--no-stream
--model-profile TEXT
--agent-version TEXT
```

参数含义：

- 默认使用流式展示。
- `--no-stream` 等待完整 RuntimeResult。
- `--json` 输出可序列化结构。
- `--debug` 只开启安全诊断字段，不允许输出敏感内部数据。

## 8. 命令到 Runtime 的映射

```text
chat                  -> runtime.run / runtime.run_stream
resume                -> runtime.resume
cancel                -> runtime.cancel
sessions              -> runtime.list_sessions
session show          -> runtime.get_session
timeline              -> runtime.get_timeline
health                -> runtime.health
export                -> runtime.export_session
delete-session        -> runtime.delete_session
```

CLI 不应从 RuntimeResult 中自行推断底层对象，更不应调用 `session_manager.repo`。

## 9. 启动入口

项目根部的 `main.py` 只作为薄启动器：

```text
加载 src.app.cli.main
调用 Typer app
```

依赖初始化和命令业务都放到 `src/app/`。

## 10. 必须联动阅读

开发 CLI 前必须阅读：

- Runtime 全部设计文档和公共契约。
- `src/memory/` 的 SessionInfo、TimelineItem 实际字段。
- ReActExecutor 的确认事件和 pending confirmation 字段。
- Tools 的安全提示和 preview 规则。
- 项目当前 `main.py` 和旧入口测试。

