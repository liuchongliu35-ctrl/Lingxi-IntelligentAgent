# Tools 层设计决策汇总（0）- 索引

> 文档状态：Tools 层 V1 正式化设计稿  
> 来源依据：`Tools设计问题回答(1).txt`、`Tools设计问题回答(2).txt`、`Tools层MCP工具设计方案.md`、`参考codex的自定义添加MCP的设计建议.md`  
> 当前阶段：设计收束，尚未进入代码实现  

## 1. 文档分卷

Tools 层 V1 设计拆分为多份文档，避免单个文件过大，也方便后续按模块开发和验收。

```text
Tools层设计决策汇总(0)-索引.md
  总入口，说明分卷、阅读顺序和核心结论。

Tools层设计决策汇总(1)-总纲与跨层边界.md
  定义 Tools 层在整个 Agent 架构中的位置，以及与 Analyzer / Planner / ReActExecutor / Models / Runtime / Safety / Memory / RAG 的边界。

Tools层设计决策汇总(2)-协议与运行时.md
  定义 ToolResult、ToolCallRequest、ToolCallContext、ToolCallOptions、ToolSpec、ToolRegistry、ToolManager、ToolPolicy、Observation 分级、dry_run、日志和错误码。

Tools层设计决策汇总(3)-基础工具设计.md
  定义阅读、编辑、终端、预览、删除、文档解析、计算、时间、文本处理、翻译和 code_executor 的 V1 细节。

Tools层设计决策汇总(4)-联网搜索设计.md
  定义 web_search、Tavily search_api provider、model_builtin provider、WebSearchData、证据等级、配置、测试和与 Models 层的关系。

Tools层设计决策汇总(5)-MCP扩展工具设计.md
  定义自定义 MCP Server 配置、STDIO 本地调用、工具发现、动态 ToolSpec 注册、MCPToolGateway、风险策略、日志和测试。

Tools层设计决策汇总(6)-集成验收与后续边界.md
  定义与 ReActExecutor 的集成验收、用户可见事件、logs/tools.log、测试矩阵、真实 provider skip 策略和 V2/V3 后置能力。
```

后续开发步骤文档建议也按模块拆分，例如：

```text
Tools层开发步骤与进度(1)-协议运行时.md
Tools层开发步骤与进度(2)-文件与命令工具.md
Tools层开发步骤与进度(3)-联网搜索.md
Tools层开发步骤与进度(4)-MCP扩展工具.md
Tools层开发步骤与进度(5)-集成验收.md
```

## 2. 当前固定主链路

Tools V1 必须服务当前已经完成的主链路，不重新设计 Analyzer / Planner / ReActExecutor。

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

必须特别注意：

```text
Tool 不是 ReActExecutor 之后的新主链路层。
Observation / Checker 是 ReActExecutor 内部循环。
ExecutionResult / ExecutionEvent 是 ReActExecutor / ReactAgent 对上层返回的结构化输出协议。
输出反馈处理器只消费事件和结果，不负责重新规划、工具执行或安全决策。
```

## 3. 旧顺序 Executor 口径

旧顺序 Executor 不是 ReActExecutor 运行失败后的正式 fallback。

当前准确口径：

```text
默认正式执行架构:
  ReActExecutor

legacy:
  仅表示显式兼容/迁移开关或历史诊断入口。

不允许理解为:
  ReActExecutor 执行失败 -> 自动切回旧 Executor。
```

Tools V1 因此不以兼容旧顺序 Executor 为目标。正式对接链路是：

```text
ReActExecutor
  -> ToolRegistry
  -> ToolManager / ToolRuntime
  -> 真实 Tool
  -> ToolResult
  -> ReActExecutor 生成 Observation
```

`ToolManager.run_tool` 这个方法名可以短期保留给当前代码迁移和测试调用，但它不代表旧 ToolManager 协议，也不允许形成第二套正式执行逻辑。

## 4. Tools V1 核心目标

Tools 层 V1 的目标是：

```text
让 ReActExecutor 可以安全、稳定、可审计地调用真实外部能力。
```

具体包括：

```text
统一 ToolResult 外壳
统一 ToolSpec 声明和参数校验
统一 ToolCallRequest / Context / Options
统一 ToolManager 执行入口
统一 ToolPolicy 权限和确认策略
统一 logs/tools.log JSONL
统一 Observation 受控视图
统一用户可见事件建议字段
正式化文件、命令、搜索、文档解析、MCP 等工具
```

Tools 层不做：

```text
不理解用户自然语言意图。
不生成复杂 TaskPlan。
不决定 ReActExecutor 下一步动作。
不生成真实 Observation。
不伪造模型总结、搜索结果或工具结果。
不让模型 provider 直接执行本地工具。
不重做 Models 层 provider、路由、retry 和结构化调用体系。
```

## 5. 七个核心工具能力

用户心智上，Tools V1 需要优先打磨七个核心能力：

```text
阅读:
  read_file / read_file_chunk / read_file_head / read_file_tail / document_parser / file_info

编辑:
  write_file / patch_file

终端:
  command_tool / shell_command_tool

联网搜索:
  web_search

预览:
  dry_run / preview event / confirmation preview

删除:
  delete_file

MCP:
  mcp.<server_id>.<tool_name>
```

其中“预览”不是模型可见的独立主工具，而是高风险动作执行前的 dry_run / confirmation preview 能力。

## 6. 设计关键词

后续实现必须反复对齐以下关键词：

```text
模型作为大脑，规则作为边界。
工具返回真实结果，不返回伪成功文本。
Observation 由 ReActExecutor 根据真实结果生成。
用户可见 events 与开发 logs 分离。
高风险动作先 preview，再确认，再执行。
blocked 动作直接拒绝，不通过确认放行。
真实 provider 测试默认 skip，fake provider 覆盖单元测试。
MCP 是工具来源，不是新的执行器或模型层。
```

