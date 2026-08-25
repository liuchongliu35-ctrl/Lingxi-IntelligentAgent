# CLI 层开发步骤与进度（3）- 会话管理验收

> 覆盖步骤：Step 12-18  
> 当前状态：Step 12-18 待开发  
> 前置分卷：`CLI层开发步骤与进度(1)-基础入口.md`、`CLI层开发步骤与进度(2)-Chat交互.md`  
> 上位设计：`CLI架构与命令设计.md`、`CLI会话管理与结果输出设计.md`、`CLI交互式对话与流式输出设计.md`

本分卷实现 CLI 的会话管理、timeline、health、导出、删除、main.py 收口和最终验收。完成本分卷后，CLI V1 应能作为 Runtime 的本地命令行入口使用。

---

## Step 12：sessions 列表命令

**状态：待开发**

### 目标

实现 `agent sessions`，展示历史会话列表，帮助用户选择 session_id 继续对话。

### 对应设计文档

```text
CLI会话管理与结果输出设计.md
  ## 1. sessions

CLI架构与命令设计.md
  ## 3. 命令总览
  ## 8. 命令到 Runtime 的映射
```

### Runtime 依赖

```text
Runtime.list_sessions
SessionInfo 安全结构
Runtime serialization
```

### 前置条件

```text
Step 0-5 已完成。
Runtime.list_sessions 可用，或测试使用 fake Runtime。
```

### 涉及文件

```text
修改:
  src/app/cli/commands_session.py
  src/app/cli/rendering.py

新增/修改:
  tests/app/cli/test_cli_sessions.py
```

### 必做

1. `agent sessions` 默认展示人类可读表格。
2. 默认字段至少包含：

```text
session_id
title
status
created_at
last_activity_at
message_count
last_run_status
```

3. `agent sessions --json` 输出完整安全 JSON。
4. 空列表时输出友好提示或空 JSON list。
5. 不默认展示 raw metadata 或敏感字段。

### 明确不做

```text
不直接查询 SQLite。
不自己组合 SessionInfo。
不实现分页高级过滤，除非 Runtime 已提供并且设计补充。
```

### 测试与验收

```powershell
python -m pytest tests/app/cli/test_cli_sessions.py -q
```

测试至少覆盖：

```text
表格字段完整。
--json 可解析。
空列表行为。
Runtime error 映射退出码。
不访问 Memory repo。
```

### 完成后回写

记录展示字段、JSON 结构、空列表行为和测试结果。

---

## Step 13：session show 与 timeline 命令

**状态：待开发**

### 目标

实现 `agent session show <id>` 和 `agent timeline <id>`，让用户查看会话信息和 Memory 已脱敏的 timeline。

### 对应设计文档

```text
CLI会话管理与结果输出设计.md
  ## 2. session show
  ## 3. timeline

CLI架构与命令设计.md
  ## 3. 命令总览
  ## 8. 命令到 Runtime 的映射
```

### Runtime 依赖

```text
Runtime.get_session
Runtime.get_timeline
RuntimeResult / Runtime error
Memory TimelineItem 安全结构
```

### 前置条件

```text
Step 12 已完成。
Runtime.get_session / Runtime.get_timeline 可用。
```

### 涉及文件

```text
修改:
  src/app/cli/commands_session.py
  src/app/cli/rendering.py

新增/修改:
  tests/app/cli/test_cli_session_show_timeline.py
```

### 必做

1. `agent session show <session_id>` 展示：

```text
session 基本信息
状态
创建和最后活动时间
消息数量
最近 run 摘要
```

2. `agent timeline <session_id>` 展示 Runtime 返回的 timeline。
3. timeline 只使用 Memory 已映射、脱敏和排序的数据。
4. `--json` 输出安全结构。
5. session 不存在返回 exit 1。

### 明确不做

```text
不把内部事件补回 timeline。
不重新组合数据库 messages/events。
不展示 raw metadata。
```

### 测试与验收

```powershell
python -m pytest tests/app/cli/test_cli_session_show_timeline.py -q
```

测试至少覆盖：

```text
session show 字段。
timeline 顺序展示。
--json 可解析。
内部事件不会被 CLI 补充展示。
session_not_found -> exit 1。
```

### 完成后回写

记录 session 展示格式、timeline 渲染规则和测试结果。

---

## Step 14：health 命令

**状态：待开发**

### 目标

实现 `agent health`，展示 Runtime 聚合的依赖健康状态。

### 对应设计文档

```text
CLI架构与命令设计.md
  ## 3. 命令总览
  ## 8. 命令到 Runtime 的映射

CLI会话管理与结果输出设计.md
  ## 6. CLI 退出码
```

### Runtime 依赖

```text
Runtime.health
health overall status
health component list
Runtime error / exit code mapping
```

### 前置条件

```text
Step 3 exit code 已完成。
Runtime.health 可用。
```

### 涉及文件

```text
修改:
  src/app/cli/commands_system.py
  src/app/cli/rendering.py

新增/修改:
  tests/app/cli/test_cli_health.py
```

### 必做

1. `agent health` 展示整体状态。
2. 展示 memory、database、models、tools、react_agent、workspace 等组件。
3. `--json` 输出完整安全结构。
4. degraded 可以返回 exit 0 或按 Runtime 设计映射，必须固定并测试。
5. unavailable / dependency_init_failed 返回 exit 3。
6. 不泄露敏感配置。

### 明确不做

```text
不在 CLI health 中执行工具探活。
不绕过 Runtime 直接调用 Models/Tools。
不自动修复依赖。
```

### 测试与验收

```powershell
python -m pytest tests/app/cli/test_cli_health.py -q
```

测试至少覆盖：

```text
healthy 输出。
degraded 输出。
unavailable exit 3。
--json 可解析。
敏感字段过滤。
```

### 完成后回写

记录 health 展示格式、degraded 退出码策略和测试结果。

---

## Step 15：export Markdown 命令

**状态：待开发**

### 目标

实现 `agent export <session_id> --output <path>`，将 Runtime 导出的安全 Markdown 写入文件。

### 对应设计文档

```text
CLI会话管理与结果输出设计.md
  ## 4. export

CLI架构与命令设计.md
  ## 3. 命令总览
  ## 8. 命令到 Runtime 的映射

CLI交互式对话与流式输出设计.md
  ## 8. JSON 输出
```

### Runtime 依赖

```text
Runtime.export_session
RuntimeResult / export content
Runtime error export_failed / session_not_found
```

### 前置条件

```text
Step 13 已完成。
Runtime.export_session 可用。
Tools path policy 或 Runtime export 路径策略已明确。
```

### 涉及文件

```text
修改:
  src/app/cli/commands_export.py
  src/app/cli/rendering.py

新增/修改:
  tests/app/cli/test_cli_export.py
```

### 必做

1. 支持：

```text
agent export <session_id> --output <path>
```

2. 导出 Markdown 内容来自 Runtime，不由 CLI 重新查询 Memory。
3. 只导出用户可见消息和事件。
4. 不导出 hidden reasoning、raw prompt、raw tool result。
5. 输出路径必须经过 workspace/path policy 或 Runtime 明确的安全策略。
6. 文件已存在时默认不静默覆盖。
7. 后续如支持 `--overwrite`，必须显式参数和测试覆盖。
8. `--json` 返回导出结果结构，不混入人类提示。

### 明确不做

```text
不直接读 SQLite。
不自己组装 timeline。
不导出内部事件。
不默认覆盖文件。
```

### 测试与验收

```powershell
python -m pytest tests/app/cli/test_cli_export.py -q
```

测试至少覆盖：

```text
Markdown 写入成功。
文件已存在默认拒绝。
路径不安全时失败。
导出内容不含敏感字段。
session_not_found -> exit 1。
export_failed -> exit 3 或设计映射。
```

### 完成后回写

记录路径策略、覆盖策略、导出格式和测试结果。

---

## Step 16：delete-session 命令

**状态：待开发**

### 目标

实现 `agent delete-session <session_id>`，通过 Runtime 执行 V1 硬删除，并在交互模式下要求确认。

### 对应设计文档

```text
CLI会话管理与结果输出设计.md
  ## 5. delete-session

CLI架构与命令设计.md
  ## 3. 命令总览
  ## 8. 命令到 Runtime 的映射
```

### Runtime 依赖

```text
Runtime.delete_session
Runtime.get_session（删除前展示摘要可选）
Runtime error session_not_found / validation_error
```

### 前置条件

```text
Step 13 已完成。
Runtime.delete_session 可用。
prompts y/n 能力已完成。
```

### 涉及文件

```text
修改:
  src/app/cli/commands_session.py
  src/app/cli/prompts.py
  src/app/cli/rendering.py

新增/修改:
  tests/app/cli/test_cli_delete_session.py
```

### 必做

1. 删除前展示 session 摘要。
2. 交互模式要求确认。
3. 只有明确确认后调用 Runtime.delete_session。
4. 拒绝确认时不删除，返回 cancelled 或 exit 2，具体按 Runtime 映射。
5. `--json` 输出结构化结果。
6. 删除失败返回明确错误。

### 明确不做

```text
不实现软删除。
不绕过 Runtime 调用 SessionManager.delete_session。
不在没有确认时删除。
不默认提供 --yes，除非后续明确设计补充。
```

### 测试与验收

```powershell
python -m pytest tests/app/cli/test_cli_delete_session.py -q
```

测试至少覆盖：

```text
y 确认后删除。
n 拒绝不删除。
非交互模式缺少确认不删除。
session_not_found -> exit 1。
--json 可解析。
```

### 完成后回写

记录删除确认策略、非交互行为、Runtime 调用和测试结果。

---

## Step 17：main.py 薄启动器收口

**状态：待开发**

### 目标

将项目根部 `main.py` 收口为 CLI 薄启动器，避免旧入口继续承担依赖装配和业务编排。

### 对应设计文档

```text
CLI架构与命令设计.md
  ## 9. 启动入口

CLI层开发步骤与进度.md
  ## 1. 开发前固定架构
```

### Runtime 依赖

```text
无直接 Runtime 依赖；main.py 只调用 CLI app。
```

### 前置条件

```text
Step 1 Typer 根应用已完成。
Step 6-16 核心 CLI 命令已完成或至少入口可用。
```

### 涉及文件

```text
修改:
  main.py

新增/修改:
  tests/app/cli/test_cli_main_entry.py
```

### 必做

1. `main.py` 只保留薄启动逻辑。
2. 不在 `main.py` 中装配 ModelManager、ToolManager、SessionManager 或 ReactAgent。
3. 不在 import `main.py` 时执行 CLI。
4. 保留兼容的 `if __name__ == "__main__"` 入口。

### 明确不做

```text
不删除旧代码历史，除非用户明确要求。
不把 API 启动放进 main.py。
不在 main.py 实现命令业务。
```

### 测试与验收

```powershell
python -m pytest tests/app/cli/test_cli_main_entry.py -q
```

测试至少覆盖：

```text
import main.py 无副作用。
main.py 调用 src.app.cli 入口。
main.py 不装配 Agent 依赖。
```

### 完成后回写

记录 main.py 最终内容、兼容入口和测试结果。

---

## Step 18：CLI V1 最终验收与文档回写

**状态：待开发**

### 目标

完成 CLI V1 文档回写和最终验收，确认 CLI 可以作为 Runtime 的本地命令行入口。

### 对应设计文档

```text
CLI架构与命令设计.md
CLI交互式对话与流式输出设计.md
CLI会话管理与结果输出设计.md
```

### Runtime 依赖

```text
Runtime V1 核心能力完成。
RuntimeResult / RuntimeEvent 字段稳定。
Runtime session/timeline/health/export/delete facade 稳定。
```

### 前置条件

```text
Step 0-17 已完成。
```

### 涉及文件

```text
修改:
  src/app/cli/CLI层开发步骤与进度.md
  src/app/cli/CLI层开发步骤与进度(1)-基础入口.md
  src/app/cli/CLI层开发步骤与进度(2)-Chat交互.md
  src/app/cli/CLI层开发步骤与进度(3)-会话管理验收.md

可新增:
  src/app/cli/CLI V1交接说明.md
```

### 必做

最终验收至少覆盖：

```text
1. agent chat "输入" 默认流式。
2. agent chat "输入" --no-stream。
3. agent chat --session-id <id> "输入"。
4. agent chat REPL 多轮同 session。
5. waiting_user y/n 确认。
6. sessions 列表。
7. session show。
8. timeline。
9. health。
10. export Markdown。
11. delete-session 确认删除。
12. --json 输出。
13. 错误退出码。
14. main.py 薄启动器。
```

### 明确不做

```text
不启动 API。
不测试 WebSocket。
不默认使用真实外部模型，除非用户明确要求整体真实验收。
不添加未设计的长期会话自动选择功能。
```

### 测试与验收

建议最终命令：

```powershell
python -m pytest tests/app/cli -q
python -m pytest tests/app/runtime -q
```

如果进入最小真实链路验收，可追加：

```powershell
python main.py chat "你好" --no-stream
python main.py sessions
python main.py health
```

如果某些测试因真实 Provider、平台差异或迁移期问题失败，必须记录：

```text
失败测试
失败原因
是否与 CLI 修改相关
是否需要后续修复
临时验收口径
```

### 完成后回写

记录 CLI V1 最终状态、测试总览、已知限制、对 API 阶段的交接事项。

