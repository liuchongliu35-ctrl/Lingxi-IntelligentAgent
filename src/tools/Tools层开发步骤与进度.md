# Tools 层开发步骤与进度

> 文档性质：Tools 层 V1 正式化开发总入口  
> 当前日期：2026-08-18  
> 当前阶段：Tools V1 已完成，进入后续边界维护阶段  
> 详细设计依据：`Tools层设计决策汇总(0)-索引.md` 至 `Tools层设计决策汇总(6)-集成验收与后续边界.md`

本文档只记录 Tools V1 的开发路线、分卷入口、步骤状态、依赖关系和跨 Session 更新规则，不替代设计决策文档。开发时如果步骤文档与设计决策文档存在冲突，必须先停止实现、核对两轮问答和设计汇总，再同步修正文档；不得由开发者临时改变架构边界。

---

## 1. 开发前固定架构

Tools V1 必须服务以下正式主链路：

```text
用户输入
  -> ReactAgent
  -> Analyzer
  -> Planner
      生成 TaskPlan / TaskUnit / PlanStep
  -> ReActExecutor
      Reasoning -> Decision -> Tool / Model / User / Control -> Observation -> Checker
  -> 输出反馈处理器
  -> 用户反馈
```

必须始终保持：

```text
1. Tool / Model / User / Control 是 ReActExecutor 内部动作类型。
2. Observation / Checker 属于 ReActExecutor 内部循环。
3. Tools 层只执行真实工具并返回 ToolResult，不生成 Observation。
4. 模型通过 ActionPacket 提议动作，不能直接调用工具或伪造 ToolResult。
5. ReActExecutor 将 ActionPacket 映射为 ToolCallRequest。
6. ReActExecutor 不直接执行 shell。
7. 用户可见 ExecutionEvent 与开发日志 logs/tools.log 分离。
8. 旧顺序 Executor 不是 ReActExecutor 失败后的自动 fallback。
9. Tools V1 不重新设计 Analyzer、Planner、ReActExecutor 或 Models。
10. 模型负责理解、判断、生成和决策；规则负责协议、安全、权限和错误边界。
```

旧顺序 Executor 仅可视为历史原型、显式兼容开关或诊断入口，不进入 Tools V1 正式验收。`ToolManager.run_tool` 方法名可以在迁移期保留，但内部必须进入新的 `ToolCallRequest -> ToolRegistry -> ToolPolicy -> ToolResult` 路径，不允许维护第二套旧工具运行时。

---

## 2. 当前真实状态

### 2.1 已完成

```text
Tools 第一轮设计问答
Tools 第二轮设计问答
MCP 专项设计
Tools 设计决策汇总 0-6
Tools V1：Step 0-44 全部完成
早期 ToolResult / ToolRegistry / ToolManager / CommandTool 雏形
ReActExecutor 对 ToolRegistry / ToolManager.run_tool 的基础接入
部分 command、registry、ReActExecutor 工具动作测试
```

### 2.2 历史缺口（已闭合）

以下内容保留为 Tools V1 开发前的历史背景，现已由 Step 0-44 全部覆盖完成。

```text
正式 ToolResult V1
ToolCallRequest / Context / Options
统一 ToolPolicy
新 ToolManager 运行时
config/tools 配置中心
logs/tools.log
完整文件工具
shell_command_tool
正式 document_parser
web_search provider 架构
Tavily adapter
model_builtin search adapter
MCP STDIO client 与动态工具注册
ReActExecutor 新协议集成
Tools V1 全量验收
```

### 2.3 早期实现的处理口径

以下为 Tools V1 开发前的迁移背景记录，现仅保留历史上下文：

```text
base.py:
  保留文件位置并扩展 ToolResult；不得假定现有 5 字段协议已满足 V1。

registry.py:
  保留已有校验基础，补齐 enabled、alias、namespace、dry_run、动态注册等正式能力。

tool_manager.py:
  从硬编码字典迁移为统一运行时；run_tool 仅保留兼容调用形式。

file_writer.py:
  旧占位工具。正式能力迁移为 write_file / patch_file 等，不再作为模型可见主工具。

search_tool.py:
  旧 Bing 包装。迁移为 web_search provider 架构，search_tool 只作为 alias。

command_tool.py:
  从字符串命令雏形迁移为 argv / shell=False 的正式普通命令工具。

translator.py:
  当前 mock，不作为 V1 核心验收主路径。

code_executor.py:
  默认关闭，不在 V1 建立完整代码沙箱。
```

---

## 3. 分卷与连续步骤

步骤编号跨分卷连续且唯一。开发时按依赖顺序推进，除非步骤文档明确允许并行；不得因为某个工具看起来简单而绕过协议、策略、日志底座提前实现。

| 分卷 | 步骤 | 内容 | 当前状态 |
|---|---:|---|---|
| [协议运行时](Tools层开发步骤与进度(1)-协议运行时.md) | Step 0-9 | 基线、协议、Registry、Policy、Runtime、preview、配置日志 | 已完成 |
| [文件与命令工具](Tools层开发步骤与进度(2)-文件与命令工具.md) | Step 10-23 | 路径、阅读、编辑、删除、命令、解析和辅助工具 | 已完成 |
| [联网搜索](Tools层开发步骤与进度(3)-联网搜索.md) | Step 24-29 | WebSearchData、路由、Tavily、model_builtin、证据与测试 | 已完成 |
| [MCP 扩展工具](Tools层开发步骤与进度(4)-MCP扩展工具.md) | Step 30-37 | 配置、STDIO、发现、动态注册、调用、安全与测试 | 已完成 |
| [集成验收](Tools层开发步骤与进度(5)-集成验收.md) | Step 38-44 | ReActExecutor、Observation、确认、事件、回归和收尾 | 已完成 |

总体依赖：

```text
Step 0-9 协议和运行时底座
  -> Step 10-23 本地基础工具
  -> Step 24-29 联网搜索
  -> Step 30-37 MCP
  -> Step 38-44 跨层集成与最终验收
```

允许的有限并行：

```text
Step 24-29 与 Step 30-37 在 Step 0-9 完成后可以由不同分支研究，
但合入正式主线前都必须基于同一 ToolResult、ToolSpec、ToolPolicy 和 ToolManager。
```

---

## 4. 开发与验收规则

每个 Step 必须遵循：

```text
阅读该 Step 及其引用的设计文档
  -> 核对前置条件
  -> 只实现“本 Step 必做”
  -> 不实现“本 Step 明确不做”
  -> 新增或修改对应测试
  -> 运行本 Step 验收命令
  -> 记录真实测试结果
  -> 更新本分卷 Step 状态
  -> 更新本总索引当前进度
  -> 再进入下一 Step
```

每个 Step 的正文至少应包含以下栏目；若某栏目不适用，也要写明“不适用”和原因：

```text
目标:
  本 Step 要解决的唯一主题。

前置条件:
  必须已完成的 Step、依赖的设计决策、已有测试或配置。

涉及文件:
  预计新增/修改的源码、配置、测试和文档。

必做:
  本 Step 必须落地的协议、行为、安全边界、错误码和日志要求。

明确不做:
  本 Step 不能顺手扩张的能力。

错误码:
  新增或需要验证的 ToolErrorCode / provider code。

兼容迁移:
  旧入口、alias、历史测试如何过渡。

日志 / Observation / Event 边界:
  哪些信息进入 tools.log，哪些只作为 metadata 候选，哪些由 ReActExecutor 生成用户可见事件。

测试与验收:
  必跑命令、离线测试、真实 provider opt-in 条件和失败记录方式。

完成后回写:
  状态、日期、修改文件、测试结果、偏差、遗留问题和下一步。
```

状态只允许使用：

```text
待开发
开发中
部分完成
已完成
阻塞
```

不得仅因代码已经存在就标记“已完成”。只有满足该 Step 的完成标准并运行规定测试后，才能标记完成。

每个 Step 完成后必须回写：

```text
状态
完成日期
已完成内容
实际修改文件
新增/修改测试
验收命令
测试结果
发现的偏差
当前边界
遗留问题
下一步
```

如果实现过程中发现设计冲突：

```text
1. 不自行选择新架构。
2. 在对应 Step 标记“阻塞”或“部分完成”。
3. 写明冲突文件、冲突字段和影响范围。
4. 给出可选方案。
5. 用户确认后先更新设计决策，再继续代码。
```

---

## 5. 全局禁止偏离项

整个 Tools V1 开发周期内，以下事项不得顺手实现：

```text
不引入旧顺序 Executor 自动 fallback。
不让 Tools 直接接收和解释完整 ActionPacket。
不信任模型提供的 confirmed、管理员权限或 credential。
不在 Tools 中生成 Checker 决策或最终回答。
不为每次 Observation 或 input_summary 额外调用模型。
不把完整文件内容、stdout、网页正文、MCP 响应写入 tools.log。
不允许递归目录删除或 glob 删除。
不允许普通 command_tool 执行复杂 shell 语法。
不实现管理员提权、交互 TTY、长期后台服务。
不重做 Models provider、route、retry 和结构化调用。
不实现完整网页抓取、浏览器自动化、RAG 入库或 MCP 插件市场。
```

---

## 6. 当前进度

```text
设计问答：已完成
设计决策汇总：已完成
开发步骤规划：已完成
代码正式化：已完成
当前应执行：无，Tools V1 已收官
```

如果需要继续后续层开发，应先阅读：

```text
src/tools/Tools层开发步骤与进度.md
src/tools/Tools层开发步骤与进度(1)-协议运行时.md
src/tools/Tools层设计决策汇总(1)-总纲与跨层边界.md
src/tools/Tools层设计决策汇总(2)-协议与运行时.md
```

Tools V1 已完成后，新的开发入口应转向 Runtime / API / Session 层或其他后续能力层，而不是回到 Tools V1 起点重复施工。
