# Runtime / CLI / API 层设计总览

## 1. 文档目的

本文档是 `src/app/` 层的设计索引和总边界说明。它根据：

- `RuntimeCLIAPI设计问题回答(1).txt`
- `RuntimeCLIAPI设计问题回答(2).txt`

整理 Runtime、CLI、API 的整体职责、依赖关系、文档入口和开发约束。

本文档只描述应用入口层，不替代 Memory、Agent、Models、Tools 的设计文档。开发任何 `src/app/` 代码前，必须结合本文档和相关层的设计、实现、测试一起阅读。

## 2. 目录定位

```text
src/app/
  runtime/       Runtime 核心编排、依赖装配、运行契约
  cli/           Typer 命令行适配器
  api/           FastAPI REST / WebSocket 适配器
```

三者关系：

```text
CLI / API
   |
   v
Runtime
   |
   +--> RuntimeMemoryAdapter / SessionManager
   +--> ReactAgent
   |      +--> Analyzer
   |      +--> Planner
   |      +--> ReActExecutor
   |      +--> OutputFeedbackProcessor
   +--> ModelManager
   +--> ToolManager / ToolRegistry / ToolRuntime
   +--> health / errors / serialization
```

CLI 和 API 是两种对外适配方式，不应各自实现 Analyzer、Planner、ReActExecutor 的主流程。

## 3. 整体主链路

```text
用户输入
  -> CLI 或 API 接收并校验
  -> Runtime 解析运行参数
  -> Memory 创建或加载 session
  -> Memory 创建本轮 run，并写入 user message
  -> Memory 构建 context_text
  -> Runtime 调用 ReactAgent(manage_memory=False)
  -> ReactAgent 调用 Analyzer
  -> ReactAgent 调用 Planner
  -> ReactAgent 调用 ReActExecutor
  -> ReActExecutor 产生 ExecutionEvent
  -> Runtime 接收事件
  -> Memory 映射、脱敏、保存可见事件
  -> Runtime 向 CLI/API 转发可见事件
  -> ReactAgent 返回 ExecutionResult
  -> Runtime 构建 OutputFeedback
  -> Memory 保存 assistant message 和 run 状态
  -> Runtime 生成 RuntimeResult
  -> CLI/API 返回最终结果
```

## 4. 责任边界

### 4.1 Runtime 负责

1. 唯一的依赖装配入口。
2. 管理进程级依赖对象的生命周期。
3. 管理一次请求或一次 run 的运行上下文。
4. 创建、加载和校验 session。
5. 通过 Memory 创建 run 和 user message。
6. 获取上下文并传给 ReactAgent。
7. 使用 `manage_memory=False` 调用正式 Runtime 模式的 ReactAgent。
8. 接管 ReActExecutor 的事件回调。
9. 统一处理完成、失败、阻断、等待确认、请求重新规划、取消和中断。
10. 调用 OutputFeedbackProcessor，形成用户可见反馈。
11. 通过 Memory 保存 assistant message、事件和 run 状态。
12. 将底层对象和异常转换为稳定的 RuntimeResult。
13. 为 CLI 和 API 提供相同的核心入口。
14. 提供会话查询、timeline、health、删除和导出等应用能力。

### 4.2 Runtime 不负责

1. 不重新实现 Analyzer 的意图和复杂度分析。
2. 不重新实现 Planner 的计划生成。
3. 不自己决定或直接执行工具。
4. 不直接操作 SQLite、SQL 或 Memory 内部表。
5. 不让 ReactAgent 在正式 Runtime 模式下重复写消息。
6. 不自行拼接新的 Agent 最终自然语言回答。
7. 不把旧 LongTermMemory / RAG 原型当作会话持久化依赖。
8. 不把隐藏推理、raw prompt、raw tool result 暴露给普通用户。

### 4.3 CLI 负责

1. 将命令行参数转换为 Runtime 请求。
2. 提供单次对话和交互式 REPL。
3. 展示人类可读的执行过程和结果。
4. 处理 CLI 内的确认交互。
5. 提供 JSON 输出模式和退出码。
6. 提供 session、timeline、health、导出等命令。

CLI 不负责复制 Runtime 的装配和 Agent 流程。

### 4.4 API 负责

1. 定义 FastAPI 路由和 Pydantic schema。
2. 将 HTTP / WebSocket 请求转换为 Runtime 请求。
3. 将 RuntimeResult / RuntimeEvent 转换为 API Result 和流式消息。
4. 映射 HTTP 状态码。
5. 管理 WebSocket 连接、同连接串行执行和有限等待队列。
6. 保持第一版本地运行边界。

API 不直接调用 Memory、ReactAgent、Models 或 Tools。

## 5. 必须联动阅读的其他层文档

开发 `src/app/` 任何模块时，至少需要阅读以下目录中的当前有效设计文档、实现和测试：

### Memory

```text
src/memory/
```

重点关注：

- `runtime_adapter.py`
- `session_manager.py`
- `context_builder.py`
- `event_mapper.py`
- `models.py`
- `storage.py`
- Memory 层设计决策、开发进度和验收文档
- `tests/test_memory_runtime_adapter.py`
- `tests/test_memory_v1_end_to_end_acceptance.py`

### Agent

```text
src/agent/
```

重点关注：

- `orchestrator/react_agent.py`
- Analyzer 当前实现和设计文档
- Planner 当前实现和设计文档
- `react_executor/` 下的协议、事件、结果、确认恢复和安全设计
- `output_feedback.py`
- ReactAgent、ReActExecutor、跨层流水线测试

### Models

```text
src/models/
```

重点关注：

- `model_manager.py`
- `compression.py`
- Models 层协议、配置、错误、健康检查和上下文压缩设计
- Models 相关测试

Runtime 只能通过 Models 层的公开接口使用模型能力，尤其是上下文压缩必须经过 `ModelManager.compress_context()`。

### Tools

```text
src/tools/
```

重点关注：

- `tool_manager.py`
- `registry.py`
- `runtime.py`
- 工具协议、权限、安全策略、输出控制、MCP 和文件/命令工具设计
- Tools 相关测试

Runtime 不绕过 ToolManager、ToolRuntime、ToolRegistry 或工具安全策略。

## 6. 设计文档索引

### Runtime

1. `runtime/Runtime架构与模块设计.md`
2. `runtime/Runtime公共契约与数据模型设计.md`
3. `runtime/Runtime依赖装配与生命周期设计.md`
4. `runtime/Runtime运行流程与Memory集成设计.md`
5. `runtime/Runtime事件流与确认恢复设计.md`
6. `runtime/Runtime错误降级与健康检查设计.md`

### CLI

1. `cli/CLI架构与命令设计.md`
2. `cli/CLI交互式对话与流式输出设计.md`
3. `cli/CLI会话管理与结果输出设计.md`

### API

1. `api/API架构与REST路由设计.md`
2. `api/API请求响应模型与错误设计.md`
3. `api/API WebSocket流式协议设计.md`
4. `api/API本地安全与生命周期设计.md`

### 开发与验收

1. `RuntimeCLIAPI开发步骤与验收标准.md`

## 7. 统一开发原则

1. 先读设计和现有代码，再修改代码。
2. 优先在 Runtime 做适配；只有真实存在跨层契约不兼容时，才小范围修改其他层。
3. 正式 Runtime 模式固定使用 `manage_memory=False`。
4. 消息写入只能有一个责任方，不能由 Runtime 和 ReactAgent 重复写入。
5. Runtime 不直接操作 SQL。
6. 所有对外结构都必须经过序列化、脱敏和字段裁剪。
7. 内部事件默认不进入用户 timeline，也不默认返回给 CLI/API。
8. 进程级共享对象不能保存某个 session 或某个 run 的临时状态。
9. 运行中的 session/run 上下文必须按 `session_id`、`run_id` 隔离。
10. 每个阶段完成后运行该阶段测试和受影响层回归测试。
11. 文档中的字段和状态如果与实际现有接口冲突，先确认真实边界，再更新设计或增加兼容适配，不能静默猜测。

## 8. V1 不承诺的能力

1. 进程重启后的断点续跑。
2. 强制中断正在运行的工具进程。
3. 多进程共享的 WebSocket 等待队列。
4. 远程部署所需的完整认证、授权和租户隔离。
5. 将隐藏推理完整展示给用户。
6. 将旧 RAG 原型整合为会话 Memory。
7. 通过 Runtime 直接暴露底层 Provider 或工具原始结果。

