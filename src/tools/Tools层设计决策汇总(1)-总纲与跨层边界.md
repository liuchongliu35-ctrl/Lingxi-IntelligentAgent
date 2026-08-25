# Tools 层设计决策汇总（1）- 总纲与跨层边界

> 文档状态：Tools 层 V1 正式化设计稿  
> 适用范围：Tools 层整体职责、跨层协作、正式主链路边界  

## 1. Tools 层定位

Tools 层是项目级工具能力服务层，负责把已经通过 ReActExecutor 校验的结构化工具调用转换为真实外部能力执行，并返回真实、结构化、可审计的 `ToolResult`。

正式定位：

```text
接收 ToolCallRequest
  -> 查询 ToolRegistry / ToolSpec
  -> 执行 ToolPolicy 校验
  -> 处理 timeout / dry_run / confirmation / output limits
  -> 调用真实 Tool
  -> 归一化 ToolResult
  -> 写入 logs/tools.log
  -> 返回给 ReActExecutor
```

Tools 层不是：

```text
不是 Analyzer。
不是 Planner。
不是 ReActExecutor。
不是 Checker。
不是输出反馈处理器。
不是 Models provider 层。
不是安全权限层的最终完整形态。
```

## 2. 固定主链路

Tools V1 必须对齐当前主链路：

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

这个链路里的关键边界：

```text
Analyzer:
  理解用户输入，输出 AnalysisResult。

Planner:
  生成 TaskPlan / TaskUnit / PlanStep。

ReActExecutor:
  模型驱动 Reasoning / Decision。
  校验 ActionPacket。
  调用 Tool / Model / User / Control。
  根据真实结果生成 Observation。
  通过 Checker 决定继续、重试、fallback、ask_user、finish、fail、request_replan。

Tools:
  执行真实工具能力。
  返回 ToolResult。
  不生成 Observation。
  不决定下一步动作。

输出反馈处理器:
  消费 ExecutionEvent / ExecutionResult。
  展示执行过程、确认请求和最终回答。
  不执行工具。
```

## 3. 模型作为大脑，规则作为边界

项目目标是模型作为大脑的灵活任务型 Agent，不是规则硬编码结果的固定流程助手。

需要 Models 层参与的场景：

```text
Analyzer:
  复杂意图、参数、风险、复杂度和执行策略兜底。

Planner:
  复杂计划生成、规则模板无法覆盖时生成 TaskPlan。

ReActExecutor:
  Thought / ActionPacket 决策。
  ActionPacket repair。
  call_model。
  fallback_to_model。
  工具结果、搜索结果、文档内容总结。
  final answer。

Tools:
  web_search 的 model_builtin provider。
  后续模型翻译、模型摘要、模型改写类工具。
```

规则适合处理：

```text
schema 校验
工具参数校验
权限与安全边界
错误分类
timeout
retry 策略
fallback 可行性边界
日志脱敏
fake/mock 测试
```

规则不应处理：

```text
复杂用户意图理解
复杂任务规划
ReAct Action 决策
最终自然语言回答
伪造模型总结
伪造工具结果
伪造 Observation
```

## 4. 与 Analyzer 的关系

Analyzer 可以给出工具相关的建议，例如：

```text
recommended_tools
required_capabilities
risk_level
needs_confirmation
missing_params
tool_strategy
```

但 Analyzer 不调用 Tools 层，也不直接生成 ToolCallRequest。

Tools V1 需要为 Analyzer 提供间接支持：

```text
稳定工具名
工具分类
工具能力描述
风险等级枚举
可用/不可用状态
```

后续 Analyzer 可以基于 `ToolRegistry.to_model_specs()` 或更轻量的工具能力清单判断用户请求是否需要阅读、编辑、终端、联网搜索、MCP 等能力。

## 5. 与 Planner 的关系

Planner 负责生成计划，不执行工具。

Planner 可以在 PlanStep 中写：

```text
tool_name
step_type
input_from
output_key
requires_confirmation
risk_level
fallback_tools
observation_mode
```

但 Planner 不负责：

```text
打开文件
写文件
执行命令
调用 MCP
生成真实 ToolResult
判断工具调用真实成功
```

Tools 层对 Planner 的要求：

```text
工具名必须稳定。
工具 schema 必须可被 Planner / 模型看到。
缺失工具必须可被明确识别。
工具分类和描述要足够清晰，避免 Planner 选错工具。
```

例如：

```text
读普通文本:
  read_file

解析 PDF / docx / xlsx:
  document_parser

整体写入:
  write_file

局部修改:
  patch_file

删除明确文件:
  delete_file

复杂 shell:
  shell_command_tool

联网搜索:
  web_search

MCP 动态工具:
  mcp.<server_id>.<tool_name>
```

## 6. 与 ReActExecutor 的关系

ReActExecutor 是 Tools 层的主要调用方。

正式调用链：

```text
Model 生成 ActionPacket
  -> ReActExecutor parse / schema 校验
  -> ReActExecutor 做 step / tool / retry / safety 业务校验
  -> ReActExecutor 构造 ToolCallRequest
  -> ToolManager 执行工具级校验和调用
  -> ToolResult
  -> ReActExecutor 生成 Observation
  -> Checker 决策
```

ReActExecutor 负责：

```text
选择是否调用工具
确认 ActionPacket 是否适用于当前 step
确认 tool_name 是否在 ToolRegistry 中
处理用户确认暂停与恢复
根据 ToolResult 生成 Observation
将工具事件加入 ExecutionEvent
让 Checker 判断 retry / fallback / finish / fail
```

Tools 层负责：

```text
执行工具级 schema 校验
执行 workspace / risk / timeout / network / command 边界
执行真实工具
归一化 ToolResult
提供 preview / dry_run 数据
写 logs/tools.log
```

ReActExecutor 不直接执行 shell。命令必须经过：

```text
ActionPacket
  -> command_tool / shell_command_tool
  -> ToolPolicy
  -> ToolResult
  -> Observation
```

## 7. 与 Models 层的关系

Models 层是项目级基础模型服务层。Tools 层不得重做 Models 层已经完成的 provider、路由、retry、结构化返回和日志体系。

Tools 可以调用 Models 层的场景非常有限：

```text
web_search.model_builtin provider
translator 后续从 mock 升级为模型翻译
text_processor 后续升级为模型摘要/改写
document_parser 后续接模型抽取时
```

调用原则：

```text
必须使用 Models V1 的结构化结果。
必须传递 trace 信息。
不得让模型 provider 直接执行本地工具。
不得让模型 provider 自行读取本地文件。
不得把敏感文件内容自动发给外部模型。
```

例如 `web_search.model_builtin`：

```text
WebSearchTool
  -> Models.generate_json(call_type="web_search")
  -> provider adapter 打开具体模型的联网参数
  -> 模型返回结构化 JSON
  -> Tools 校验 JSON schema
  -> 归一化 WebSearchData
  -> ToolResult
```

Tools 层不硬编码 GPT / Kimi / DeepSeek 的联网参数，具体 provider 参数由 Models 层 provider adapter 处理。

## 8. 与 Runtime / API / Session 的关系

Runtime / API / Session 层后续会负责：

```text
session_id
执行状态查询
流式事件订阅
确认 / 恢复接口
工具权限配置入口
MCP 配置入口
真实 provider 配置入口
```

Tools V1 需要提前准备好这些层会消费的数据：

```text
ToolCallContext.session_id
ToolCallContext.execution_id
ToolCallContext.step_id
ToolResult.metadata.preview_summary
ToolResult.metadata.affected_resources
ToolResult.metadata.confirmation
ToolResult.metadata.artifacts
ToolResult.code / error_type / retryable
```

但 Tools V1 不实现完整 Runtime UI，也不负责用户确认交互本身。用户确认由 ReActExecutor 和后续 Runtime / API / Session 共同完成。

## 9. 与安全与权限层的关系

Tools V1 需要实现基础安全边界，但不等于完整安全与权限层。

V1 必做：

```text
workspace_root 限制
敏感路径保护
风险等级 low / medium / high / blocked
high 默认确认
blocked 直接拒绝
命令危险规则
网络 allow_network 控制
会话级权限开关
dry_run / preview 数据
日志脱敏
```

V1 预留但不真正实现：

```text
管理员提权
跨工作区任意文件写入
复杂权限 UI
复杂沙箱虚拟机
长期授权审计后台
```

安全策略优先级：

```text
blocked 规则
  > workspace / sensitive path
  > session permission
  > ToolSpec risk
  > ActionPacket request
  > Tool 默认行为
```

## 10. 与 Memory / RAG 的关系

Tools V1 不做完整 Memory 和 RAG。

但它会为后续提供基础能力：

```text
document_parser:
  解析 txt/md/json/csv/pdf/docx/xlsx。

read_file_chunk:
  支持大文件分段读取。

web_search:
  返回结构化 WebSearchData 和来源字段。

ToolResult.metadata.artifacts:
  记录生成文件和解析结果引用。
```

后续 RAG 文档入库、embedding、chunk 索引、source citation 和知识库检索由 RAG 层增强负责，不塞进 Tools V1。

## 11. 七个核心能力与正式工具名

Tools V1 的用户心智能力与正式工具名关系：

```text
阅读:
  read_file
  read_file_chunk
  read_file_head
  read_file_tail
  list_files
  find_files
  file_info
  document_parser

编辑:
  write_file
  patch_file
  copy_file
  move_file
  rename_file

终端:
  command_tool
  shell_command_tool

联网搜索:
  web_search
  search_tool legacy alias

预览:
  ToolCallOptions.dry_run
  preview_summary
  confirmation_required event

删除:
  delete_file

MCP:
  mcp.<server_id>.<tool_name>
```

## 12. V1 不做事项

Tools V1 暂不做：

```text
完整插件市场。
自动安装未知 MCP Server。
MCP Resources / Prompts / Sampling。
完整网页正文抓取和浏览器自动化。
递归目录删除。
跨工作区任意文件访问。
管理员权限执行。
复杂沙箱虚拟机。
长期搜索缓存和网页索引。
完整 RAG 文档入库。
把 Analyzer / Planner / ReActExecutor 逻辑迁移到 Tools 层。
重做 Models provider。
每次 ToolResult 自动调用模型摘要。
```
