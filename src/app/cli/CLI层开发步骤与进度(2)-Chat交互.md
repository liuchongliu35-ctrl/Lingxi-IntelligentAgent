# CLI 层开发步骤与进度（2）- Chat 交互

> 覆盖步骤：Step 6-11  
> 当前状态：Step 6-11 待开发  
> 前置分卷：`CLI层开发步骤与进度(1)-基础入口.md`  
> 上位设计：`CLI架构与命令设计.md`、`CLI交互式对话与流式输出设计.md`

本分卷实现 CLI 最核心的 chat 能力：单次输入、默认流式、REPL、多轮同 session、waiting_user 确认，以及可选 resume/cancel 命令。

---

## Step 6：chat 单次输入同步模式

**状态：待开发**

### 目标

实现 `agent chat "用户输入"` 和 `agent chat --session-id <id> "用户输入"` 的单次执行基础能力，先跑通非流式路径。

### 对应设计文档

```text
CLI架构与命令设计.md
  ## 3. 命令总览
  ## 4.2 单次输入
  ## 5. session 语义
  ## 7. CLI 参数建议
  ## 8. 命令到 Runtime 的映射

CLI交互式对话与流式输出设计.md
  ## 5. OutputFeedback 和最终输出
```

### Runtime 依赖

```text
Runtime.run
RuntimeResult.status
RuntimeResult.output
RuntimeResult.session_id
RuntimeResult.run_id
RuntimeResult.error_code
RuntimeResult.persistence_warning
```

### 前置条件

```text
Step 0-5 已完成。
Runtime.run 可用，或测试使用 fake Runtime。
```

### 涉及文件

```text
修改:
  src/app/cli/commands_chat.py
  src/app/cli/rendering.py
  src/app/cli/exit_codes.py

新增/修改:
  tests/app/cli/test_cli_chat_single.py
```

### 必做

1. 支持：

```text
agent chat "输入"
agent chat --session-id <id> "输入"
agent chat "输入" --no-stream
agent chat "输入" --json --no-stream
```

2. 没有 `--session-id` 时不复用上一次 session，交给 Runtime 创建新 session。
3. 有 `--session-id` 时在指定 session 中继续。
4. 非流式模式调用 Runtime.run。
5. 输出最终结果和必要的 session_id/run_id。
6. 返回正确退出码。

### 明确不做

```text
不实现 REPL。
不实现默认流式。
不实现 waiting_user y/n。
不直接读取 SQLite 历史。
```

### 测试与验收

```powershell
python -m pytest tests/app/cli/test_cli_chat_single.py -q
```

测试至少覆盖：

```text
无 session_id 创建新会话请求。
指定 session_id 继续会话请求。
默认不复用上一次 session。
--no-stream 调用 Runtime.run。
--json 输出可解析。
错误状态返回正确退出码。
```

### 完成后回写

记录 chat 单次参数、Runtime 调用方式、测试结果和任何 session 语义偏差。

---

## Step 7：chat 默认流式事件输出

**状态：待开发**

### 目标

实现 chat 默认流式输出，让用户能边看可见执行事件，边等待最终 RuntimeResult。

### 对应设计文档

```text
CLI交互式对话与流式输出设计.md
  ## 2. 默认流式行为
  ## 3. 默认展示事件
  ## 4. 默认不展示事件
  ## 5. OutputFeedback 和最终输出
  ## 8. JSON 输出

CLI架构与命令设计.md
  ## 7. CLI 参数建议
```

### Runtime 依赖

```text
Runtime.run_stream
RuntimeEvent.sequence
RuntimeEvent.visible_to_user
RuntimeEvent.event_type
RuntimeEvent.message
RuntimeResult
```

### 前置条件

```text
Step 6 已完成。
Runtime.run_stream 可用，或测试使用 fake Runtime stream。
```

### 涉及文件

```text
修改:
  src/app/cli/commands_chat.py
  src/app/cli/rendering.py

新增/修改:
  tests/app/cli/test_cli_chat_stream.py
```

### 必做

1. `agent chat "输入"` 默认走 Runtime.run_stream。
2. `--no-stream` 才走 Runtime.run。
3. 事件按 RuntimeEvent.sequence 展示。
4. 只展示可见、安全事件。
5. 最终 result 只打印一次主输出。
6. `--json` 流式模式输出 JSON lines。
7. 流式 sink 渲染失败不伪装为 Agent 执行失败。

### 明确不做

```text
不实现 WebSocket。
不展示内部事件。
不直接消费 ReActExecutor 原始事件。
不实现 REPL。
```

### 测试与验收

```powershell
python -m pytest tests/app/cli/test_cli_chat_stream.py -q
```

测试至少覆盖：

```text
默认调用 run_stream。
--no-stream 调用 run。
可见事件输出。
内部事件不输出。
JSON lines 可解析。
最终 result 输出一次。
```

### 完成后回写

记录流式输出格式、事件过滤、JSON lines 行为和测试结果。

---

## Step 8：chat 交互式 REPL

**状态：待开发**

### 目标

实现 `agent chat` 和 `agent chat --session-id <id>` 的交互式多轮对话，同一个 REPL 生命周期内默认使用同一个 session。

### 对应设计文档

```text
CLI架构与命令设计.md
  ## 4.1 交互式 REPL
  ## 5. session 语义
  ## 6. 可选的 session 选择

CLI交互式对话与流式输出设计.md
  ## 6. waiting_user 确认交互
  ## 7. REPL 特殊行为
```

### Runtime 依赖

```text
Runtime.run_stream
Runtime.run
Runtime.get_session
RuntimeResult.session_id
RuntimeResult.status
```

### 前置条件

```text
Step 7 已完成。
prompts 基础输入能力可用。
```

### 涉及文件

```text
新增/修改:
  src/app/cli/prompts.py
  src/app/cli/commands_chat.py
  tests/app/cli/test_cli_chat_repl.py
```

### 必做

1. `agent chat` 进入 REPL。
2. 第一条有效输入创建新 session，后续输入复用该 session_id。
3. `agent chat --session-id <id>` 加载指定 session 并在其中多轮对话。
4. 空输入忽略，不创建 run。
5. `exit`、`quit` 或 EOF 退出。
6. 当前 run 未结束前不读取下一条普通输入。
7. 某轮失败后允许继续同 session 下一轮。
8. REPL 退出不删除 session 历史。

### 明确不做

```text
不默认复用上次 CLI 进程的 session。
不实现跨进程 REPL 状态。
不在 REPL 中直接拼接上下文。
不绕过 Runtime 创建 session。
```

### 测试与验收

```powershell
python -m pytest tests/app/cli/test_cli_chat_repl.py -q
```

测试至少覆盖：

```text
agent chat 进入 REPL。
多轮复用同一个 session_id。
指定 session_id 进入旧会话。
空输入不创建 run。
exit/quit/EOF 退出。
失败后仍可继续下一轮。
```

### 完成后回写

记录 REPL 输入控制、session 复用规则、退出规则和测试结果。

---

## Step 9：waiting_user 确认交互

**状态：待开发**

### 目标

实现 CLI 在 RuntimeResult.status 为 `waiting_user` 时的 y/n 确认交互，并通过 Runtime.resume 或 Runtime.cancel 继续处理。

### 对应设计文档

```text
CLI交互式对话与流式输出设计.md
  ## 6. waiting_user 确认交互
  ## 7. REPL 特殊行为

CLI架构与命令设计.md
  ## 8. 命令到 Runtime 的映射
```

### Runtime 依赖

```text
RuntimeResult.status
RuntimeResult.pending_confirmation
RuntimeResult.run_id
RuntimeResult.session_id
Runtime.resume
Runtime.cancel
```

### 前置条件

```text
Step 8 已完成。
Runtime.resume / Runtime.cancel 可用，或测试使用 fake Runtime。
```

### 涉及文件

```text
修改:
  src/app/cli/prompts.py
  src/app/cli/commands_chat.py
  src/app/cli/rendering.py

新增/修改:
  tests/app/cli/test_cli_confirmation.py
```

### 必做

1. waiting_user 时停止普通 run 输出。
2. 展示安全 pending_confirmation。
3. 读取 y/n：

```text
y / yes / 是 -> approved=True
n / no / 否 -> approved=False 或 cancel
其他输入重新提示
```

4. approved=True 调用 Runtime.resume。
5. 拒绝路径调用 Runtime.resume(approved=False) 或 Runtime.cancel，具体以 Runtime 设计实现为准。
6. resume 后继续展示后续事件和最终结果。
7. 非交互模式不自动确认，返回 waiting_user 结果。

### 明确不做

```text
不实现 --yes 自动确认。
不默认同意危险动作。
不展示 raw tool result。
不在 CLI 中调用 ReActExecutor.resume_after_confirmation。
```

### 测试与验收

```powershell
python -m pytest tests/app/cli/test_cli_confirmation.py -q
```

测试至少覆盖：

```text
y 调用 Runtime.resume approved=True。
n 调用拒绝或 cancel 路径。
非法输入重新提示。
非交互模式不自动确认。
pending_confirmation 安全展示。
resume 后最终结果输出。
```

### 完成后回写

记录确认输入规则、拒绝路径选择、非交互行为和测试结果。

---

## Step 10：resume / cancel 可选命令

**状态：待开发**

### 目标

实现可选的 `agent resume` 和 `agent cancel` 命令，供用户在 chat 外显式处理当前进程内 pending run。

### 对应设计文档

```text
CLI架构与命令设计.md
  ## 3. 命令总览
  ## 8. 命令到 Runtime 的映射

CLI交互式对话与流式输出设计.md
  ## 6. waiting_user 确认交互
```

### Runtime 依赖

```text
Runtime.resume
Runtime.cancel
RuntimeResult.status
RuntimeResult.pending_confirmation
```

### 前置条件

```text
Step 9 已完成。
Runtime resume/cancel 契约稳定。
```

### 涉及文件

```text
修改:
  src/app/cli/commands_chat.py 或独立 commands_run.py
  src/app/cli/rendering.py

新增/修改:
  tests/app/cli/test_cli_resume_cancel.py
```

### 必做

1. 支持显式 resume：

```text
agent resume <run_id> --session-id <id> --approve
agent resume <run_id> --session-id <id> --reject
```

2. 支持 cancel：

```text
agent cancel <run_id> --session-id <id>
```

3. 命令只调用 Runtime，不调用 ReActExecutor。
4. 缺少必要参数返回 validation_error / exit 1。
5. 输出 RuntimeResult 并使用统一退出码。

### 明确不做

```text
不承诺跨进程断点续跑。
不强杀正在执行的工具。
不绕过 Runtime pending registry。
```

### 测试与验收

```powershell
python -m pytest tests/app/cli/test_cli_resume_cancel.py -q
```

测试至少覆盖：

```text
approve 调用 Runtime.resume。
reject 调用 Runtime.resume 或 cancel 策略。
cancel 调用 Runtime.cancel。
缺少 session_id/run_id 返回 exit 1。
interrupted/run_not_found 映射正确退出码。
```

### 完成后回写

记录命令参数、拒绝策略、错误码映射和测试结果。

---

## Step 11：chat 端到端 CLI 集成验收

**状态：待开发**

### 目标

在 fake Runtime 和可选临时 Runtime 集成环境中验收 chat、REPL、流式、JSON 和确认交互。

### 对应设计文档

```text
CLI架构与命令设计.md
  ## 4. chat 语义
  ## 5. session 语义

CLI交互式对话与流式输出设计.md
  ## 2. 默认流式行为
  ## 6. waiting_user 确认交互
  ## 8. JSON 输出
```

### Runtime 依赖

```text
Runtime.run
Runtime.run_stream
Runtime.resume
Runtime.cancel
RuntimeResult
RuntimeEvent
```

### 前置条件

```text
Step 6-10 已完成。
```

### 涉及文件

```text
新增/修改:
  tests/app/cli/test_cli_chat_acceptance.py
```

### 必做

至少覆盖：

```text
1. agent chat "输入" 默认流式。
2. agent chat "输入" --no-stream。
3. agent chat --session-id <id> "输入"。
4. agent chat REPL 多轮同 session。
5. --json 普通输出。
6. --json 流式 JSON lines。
7. waiting_user y/n。
8. 错误状态退出码。
9. 默认不复用上一次 session。
```

### 明确不做

```text
不测试 sessions/timeline/export/delete。
不启动 API。
不依赖真实外部模型。
```

### 测试与验收

```powershell
python -m pytest tests/app/cli/test_cli_chat_acceptance.py -q
```

建议同时运行：

```powershell
python -m pytest tests/app/cli/test_cli_chat_single.py tests/app/cli/test_cli_chat_stream.py tests/app/cli/test_cli_chat_repl.py tests/app/cli/test_cli_confirmation.py -q
```

### 完成后回写

记录 chat 验收结果、失败清单、Runtime 偏差和下一分卷前置状态。

