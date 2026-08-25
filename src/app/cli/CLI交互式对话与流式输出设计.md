# CLI 交互式对话与流式输出设计

## 1. 目标

CLI V1 要能让用户：

- 看到 Agent 的可见执行进度。
- 看到工具调用的安全摘要。
- 在需要时完成确认。
- 得到最终回答。
- 在脚本环境中使用 JSON 和退出码。

## 2. 默认流式行为

交互式 chat 默认调用 Runtime 的流式入口：

```text
Runtime.run_stream()
  -> CLI event sink
  -> 最终 RuntimeResult
```

事件显示顺序必须按照 RuntimeEvent.sequence。

CLI 不应直接消费 ReActExecutor 原始对象。

## 3. 默认展示事件

```text
progress_message
step_started
step_completed
tool_started
tool_finished
command_started
command_finished
file_edited
confirmation_requested
final_answer
system_notice
```

展示内容应是用户可理解的安全摘要，例如：

```text
正在分析任务
正在执行工具：读取文件
步骤已完成
需要确认：是否执行高风险操作
```

## 4. 默认不展示事件

```text
model_step_started
model_step_finished
raw_prompt
raw_observation
hidden_reasoning
未经脱敏的 action_selected 细节
```

即使 `--debug` 开启，也不能展示禁止字段。

## 5. OutputFeedback 和最终输出

默认终端展示：

1. 过程事件。
2. warning 或状态提示。
3. 最终 `output` 或安全的 OutputFeedback 文本。
4. 必要时展示 session_id、run_id，方便之后继续会话。

不要同时重复打印：

- ExecutionResult.output。
- OutputFeedback.output。
- Memory assistant message。

CLI 应选择一个主展示文本，其他结构化内容只在 JSON 或 debug 摘要中提供。

## 6. waiting_user 确认交互

当 RuntimeResult 为 `waiting_user`：

```text
1. 停止当前 run 的普通输出。
2. 展示安全的 pending_confirmation。
3. 读取用户 y/n。
4. y -> Runtime.resume(approved=True)
5. n -> Runtime.resume(approved=False) 或 Runtime.cancel()
6. 继续展示后续事件和最终结果。
```

输入解析：

- `y`、`yes`、`是` 视为同意。
- `n`、`no`、`否` 视为拒绝。
- 其他输入重新提示，不自动默认同意。

非交互模式：

- V1 默认不自动确认。
- 没有明确的确认输入时，返回 `waiting_user`。
- 后续可增加 `--yes`，但需要单独定义安全边界。

## 7. REPL 特殊行为

REPL 中：

- 空输入默认忽略，不创建 run。
- `exit`、`quit` 或 EOF 退出。
- 当前 run 未结束时不读取下一条普通输入。
- 用户确认属于当前 run，不创建新的普通聊天 run。
- 某轮失败后可以继续在同一个 session 中输入下一轮。

## 8. JSON 输出

`--json` 模式输出一个完整的安全 JSON：

```text
{
  "success": true,
  "status": "completed",
  "session_id": "...",
  "run_id": "...",
  "output": "...",
  "execution_result": {...},
  "output_feedback": {...},
  "timeline": [...],
  "persistence_available": true,
  "metadata": {...}
}
```

流式 JSON 模式可以按行输出：

```text
{"type":"event", ...}
{"type":"result", ...}
```

stdout 只输出机器可消费内容；诊断日志写 stderr 或日志文件。

## 9. 事件和终端输出异常

终端渲染失败不应伪装成 Agent 执行失败。应：

- 记录渲染异常。
- 尽量继续接收最终结果。
- 最终无法展示时返回非零退出码并给出安全错误。

## 10. 必须联动阅读

开发前必须核对：

- RuntimeEvent 的字段和可见性。
- ReActExecutor 当前事件类型和确认 payload。
- Memory timeline 的展示字段。
- Tools preview/output control 的安全限制。
- CLI 依赖 Typer 的版本和项目现有依赖管理方式。

