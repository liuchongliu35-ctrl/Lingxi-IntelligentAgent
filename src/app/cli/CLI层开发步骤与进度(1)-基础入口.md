# CLI 层开发步骤与进度（1）- 基础入口

> 覆盖步骤：Step 0-5  
> 当前状态：Step 0-5 待开发  
> 上位设计：`CLI架构与命令设计.md`、`CLI交互式对话与流式输出设计.md`、`CLI会话管理与结果输出设计.md`

本分卷先建立 CLI V1 的 Typer 根应用、Runtime dependency、退出码、渲染和 JSON 输出底座。没有完成本分卷前，不应开始复杂 REPL、确认交互或导出删除。

---

## Step 0：设计基线、Runtime 契约快照与入口保护

**状态：待开发**

### 目标

固定 CLI 开发前的真实入口状态、Typer 依赖状态、Runtime 公开契约和旧 `main.py` 行为，避免 CLI 后续绕过 Runtime 或破坏项目启动入口。

### 对应设计文档

```text
CLI架构与命令设计.md
  ## 1. 定位
  ## 2. 建议目录
  ## 8. 命令到 Runtime 的映射
  ## 9. 启动入口
  ## 10. 必须联动阅读

CLI交互式对话与流式输出设计.md
  ## 10. 必须联动阅读
```

### Runtime 依赖

```text
RuntimeResult
RuntimeEvent
RuntimeErrorCode / exit code 映射
RuntimeFactory 或 get_runtime 入口
```

### 前置条件

```text
CLI 设计文档已完成。
Runtime 步骤进度文档已完成。
当前工作区现有变更已被识别，不覆盖用户未提交修改。
```

### 涉及文件

```text
只读核对:
  src/app/runtime/
  src/app/cli/
  main.py
  pyproject.toml / requirements 相关依赖文件

可新增:
  tests/app/cli/test_cli_current_baseline.py
```

### 必做

1. 记录当前 `src/app/cli` 文件状态。
2. 记录项目是否已安装 Typer，若未安装，标记后续依赖处理方式。
3. 记录 Runtime 当前可用的公开入口和 RuntimeResult / RuntimeEvent 字段。
4. 记录当前 `main.py` 的内容和后续薄启动器收口方式。
5. 新增 baseline 测试，验证 CLI 包可 import，且不会在 import 时初始化真实模型或执行工具。
6. 明确 CLI 只能调用 Runtime，不直接调用 Memory repo、ReactAgent 或 ReActExecutor。

### 明确不做

```text
不实现 Typer app。
不修改 main.py。
不运行真实模型。
不启动 Runtime 真实主链路。
不实现任何命令。
```

### 测试与验收

```powershell
python -m pytest tests/app/cli/test_cli_current_baseline.py -q
```

完成标准：

```text
入口基线已记录。
Typer 依赖状态已明确。
Runtime 契约快照已记录。
CLI import 不产生重型副作用。
```

### 完成后回写

```text
状态:
完成日期:
入口快照:
Runtime 契约快照:
实际新增/修改文件:
测试命令:
测试结果:
发现的偏差:
遗留问题:
下一步:
```

---

## Step 1：Typer 根应用与命令分组骨架

**状态：待开发**

### 目标

建立 CLI 的 Typer 根应用和命令分组骨架，固定命令名称和模块拆分，但暂不实现复杂业务逻辑。

### 对应设计文档

```text
CLI架构与命令设计.md
  ## 2. 建议目录
  ## 3. 命令总览
  ## 9. 启动入口
```

### Runtime 依赖

```text
无真实 Runtime 调用；只预留 dependency 接入点。
```

### 前置条件

```text
Step 0 已完成。
Typer 可用，或已明确依赖安装/降级策略。
```

### 涉及文件

```text
新增/修改:
  src/app/cli/main.py
  src/app/cli/commands_chat.py
  src/app/cli/commands_session.py
  src/app/cli/commands_system.py
  src/app/cli/commands_export.py
  src/app/cli/__init__.py
  tests/app/cli/test_cli_app.py
```

### 必做

1. 建立 Typer 根 app。
2. 注册命令骨架：

```text
chat
sessions
session show
timeline
health
export
delete-session
resume（可选骨架）
cancel（可选骨架）
```

3. 命令定义、输出渲染和 Runtime 调用保持分离。
4. import CLI 不应创建真实 Runtime，真实 Runtime 在命令执行时按 dependency 获取。
5. `src.app.cli` 暴露可被 `main.py` 调用的入口函数或 app。

### 明确不做

```text
不实现真实 chat。
不实现 REPL。
不实现 Runtime factory。
不访问 Memory。
不写 main.py 收口。
```

### 测试与验收

```powershell
python -m pytest tests/app/cli/test_cli_app.py -q
```

测试至少覆盖：

```text
Typer app 可创建。
help 输出包含核心命令。
import 不初始化 Runtime。
命令骨架不会调用 Memory/Agent。
```

### 完成后回写

记录命令结构、模块路径、help 输出测试结果和任何命令命名调整。

---

## Step 2：Runtime dependency 与 fake Runtime 测试注入

**状态：待开发**

### 目标

建立 CLI 获取 Runtime 的唯一方式，并支持测试注入 fake Runtime，保证 CLI 不各自装配 Agent 主流程。

### 对应设计文档

```text
CLI架构与命令设计.md
  ## 1. 定位
  ## 8. 命令到 Runtime 的映射

CLI交互式对话与流式输出设计.md
  ## 2. 默认流式行为
```

### Runtime 依赖

```text
RuntimeFactory
Runtime.run
Runtime.run_stream
Runtime.resume
Runtime.cancel
Runtime session/timeline/health/export/delete facade
```

### 前置条件

```text
Step 1 已完成。
Runtime Factory 或 fake Runtime 协议已明确。
```

### 涉及文件

```text
新增/修改:
  src/app/cli/runtime_dependency.py
  src/app/cli/main.py
  tests/app/cli/test_cli_runtime_dependency.py
```

### 必做

1. 提供 `get_runtime()` 或同等 dependency。
2. 默认路径调用 RuntimeFactory 创建 Runtime。
3. 测试允许注入 fake Runtime。
4. 单个 CLI 进程内复用 Runtime，REPL 中不每轮重建。
5. Runtime 初始化失败映射到 CLI 错误输出和退出码 3。
6. dependency 层不保存当前 session_id/run_id。

### 明确不做

```text
不在 CLI 中手动创建 ModelManager / ToolManager / SessionManager。
不直接 import Memory repo。
不为每个 run 重建所有依赖。
不实现 API dependency。
```

### 测试与验收

```powershell
python -m pytest tests/app/cli/test_cli_runtime_dependency.py -q
```

测试至少覆盖：

```text
命令使用 fake Runtime。
dependency 初始化失败 -> exit 3。
REPL 可复用同一个 fake Runtime。
CLI 不直接装配 Agent。
```

### 完成后回写

记录 dependency 接口、fake 注入方式、Runtime 创建时机和测试结果。

---

## Step 3：CLI exit code 映射与错误输出

**状态：待开发**

### 目标

实现 Runtime 状态/错误码到 CLI 退出码和错误输出的统一映射。

### 对应设计文档

```text
CLI会话管理与结果输出设计.md
  ## 6. CLI 退出码

CLI交互式对话与流式输出设计.md
  ## 9. 事件和终端输出异常

CLI架构与命令设计.md
  ## 7. CLI 参数建议
```

### Runtime 依赖

```text
RuntimeResult.status
RuntimeResult.error_code
RuntimeResult.error_message
Runtime errors 的 CLI exit code 映射
```

### 前置条件

```text
Step 2 已完成。
Runtime error/exit 映射已稳定。
```

### 涉及文件

```text
新增/修改:
  src/app/cli/exit_codes.py
  src/app/cli/rendering.py
  tests/app/cli/test_cli_exit_codes.py
```

### 必做

映射：

```text
0  completed / waiting_user / request_replan
1  validation_error / not_found / session_conflict
2  blocked / cancelled / interrupted
3  memory_unavailable / dependency_init_failed / internal_error
```

1. 人类可读模式下错误输出写 stderr。
2. JSON 模式下 stdout 输出结构化错误 JSON。
3. 退出码不因人类可读或 JSON 模式改变。
4. 渲染错误不能被伪装为 Agent 执行失败。

### 明确不做

```text
不实现命令业务。
不吞掉 Runtime error_code。
不在 stdout 混入 stderr 提示。
```

### 测试与验收

```powershell
python -m pytest tests/app/cli/test_cli_exit_codes.py -q
```

测试至少覆盖：

```text
completed -> 0。
validation_error -> 1。
blocked_by_policy -> 2。
dependency_init_failed -> 3。
JSON 和非 JSON 退出码一致。
```

### 完成后回写

记录最终映射表和任何新增错误码的处理。

---

## Step 4：人类可读渲染与安全事件展示底座

**状态：待开发**

### 目标

建立 CLI 的人类可读渲染层，支持 RuntimeResult、RuntimeEvent、warning、session_id/run_id 和错误摘要展示。

### 对应设计文档

```text
CLI交互式对话与流式输出设计.md
  ## 3. 默认展示事件
  ## 4. 默认不展示事件
  ## 5. OutputFeedback 和最终输出
  ## 9. 事件和终端输出异常

CLI架构与命令设计.md
  ## 7. CLI 参数建议
```

### Runtime 依赖

```text
RuntimeEvent.sequence
RuntimeEvent.event_type
RuntimeEvent.message
RuntimeEvent.visible_to_user
RuntimeResult.output
RuntimeResult.output_feedback
RuntimeResult.persistence_warning
RuntimeResult.session_id
RuntimeResult.run_id
```

### 前置条件

```text
Step 3 已完成。
RuntimeEvent 字段稳定。
```

### 涉及文件

```text
新增/修改:
  src/app/cli/rendering.py
  tests/app/cli/test_cli_rendering.py
```

### 必做

1. 默认展示事件：

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

2. 默认不展示：

```text
model_step_started
model_step_finished
raw_prompt
raw_observation
hidden_reasoning
未经脱敏的 action_selected 细节
```

3. 最终输出只选择一个主展示文本，避免重复打印 ExecutionResult.output、OutputFeedback.output 和 Memory assistant message。
4. 显示 persistence_warning 和必要的 session_id/run_id。
5. 渲染函数不得直接调用 Runtime 或 Memory。

### 明确不做

```text
不实现 JSON 输出。
不实现 REPL。
不展示隐藏推理。
不直接消费 ReActExecutor 原始对象。
```

### 测试与验收

```powershell
python -m pytest tests/app/cli/test_cli_rendering.py -q
```

测试至少覆盖：

```text
可见事件渲染。
内部事件被跳过。
final output 不重复。
persistence_warning 显示。
敏感字段不出现在输出。
```

### 完成后回写

记录事件白名单、最终输出选择策略和测试结果。

---

## Step 5：JSON 输出与机器可消费模式

**状态：待开发**

### 目标

实现 `--json` 输出模式，包括完整 RuntimeResult JSON 和流式 JSON lines。

### 对应设计文档

```text
CLI架构与命令设计.md
  ## 7. CLI 参数建议

CLI交互式对话与流式输出设计.md
  ## 8. JSON 输出
  ## 9. 事件和终端输出异常
```

### Runtime 依赖

```text
RuntimeResult.to_dict 或等价安全结构
RuntimeEvent.to_dict 或等价安全结构
Runtime serialization 输出
```

### 前置条件

```text
Step 4 已完成。
Runtime 序列化和脱敏已稳定。
```

### 涉及文件

```text
修改:
  src/app/cli/rendering.py

新增/修改:
  tests/app/cli/test_cli_json_output.py
```

### 必做

1. `--json` 普通模式输出完整安全 RuntimeResult。
2. 流式 JSON 模式按行输出：

```text
{"type":"event", ...}
{"type":"result", ...}
```

3. stdout 只输出机器可消费 JSON。
4. 诊断、人类提示和错误日志写 stderr 或日志文件。
5. JSON 中不得出现 raw_prompt、hidden_reasoning、raw_tool_result、token、password。
6. JSON 输出失败应返回 CLI 内部错误退出码。

### 明确不做

```text
不定义 API Result。
不实现 WebSocket。
不把非 JSON 提示混入 stdout。
```

### 测试与验收

```powershell
python -m pytest tests/app/cli/test_cli_json_output.py -q
```

测试至少覆盖：

```text
普通 JSON 可解析。
JSON lines 每行可解析。
错误 JSON 可解析。
stdout/stderr 分离。
敏感字段过滤。
```

### 完成后回写

记录 JSON 结构、stdout/stderr 策略和测试结果。

