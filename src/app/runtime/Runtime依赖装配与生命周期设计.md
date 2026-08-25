# Runtime 依赖装配与生命周期设计

## 1. 目标

Runtime 是唯一的依赖装配中心。CLI 和 API 不各自创建一套 Agent 主链路，避免模型、工具、Memory、日志和配置不一致。

## 2. 生产依赖图

```text
RuntimeFactory
  +--> CoreConfig / RuntimeConfig
  +--> ModelManager
  +--> ToolRegistry
  +--> ToolManager / ToolRuntime
  +--> SessionManager
  +--> ContextBuilder
  +--> RuntimeMemoryAdapter
  +--> Analyzer
  +--> Planner
  +--> ReActExecutor
  +--> ReactAgent(manage_memory=False)
  +--> OutputFeedbackProcessor
  +--> HealthChecker
  +--> Serializer
```

实际装配时，必须依据各层当前公开构造函数和工厂接口调整，不可仅凭名称猜测参数。

## 3. 生命周期

### 3.1 API 进程

```text
API 进程启动
  -> 加载配置
  -> RuntimeFactory.build()
  -> Runtime 初始化依赖
  -> SessionManager.recover_interrupted_runs()
  -> FastAPI app 保存 Runtime
  -> 请求复用同一个 Runtime
```

### 3.2 CLI 进程

```text
agent 命令启动
  -> 创建一个 Runtime
  -> 执行当前命令或 REPL
  -> 退出时释放可释放资源
```

CLI 每次进程启动创建 Runtime；REPL 内所有输入复用该 Runtime。

### 3.3 测试

测试可以注入 fake：

```text
model_manager
tool_manager
session_manager
memory_adapter
react_agent
workspace_root
config
```

测试优先使用临时 SQLite 和 fake 模型/工具，避免单元测试依赖真实 Provider。

## 4. 进程级共享和 run 级隔离

### 4.1 可以共享

- Runtime 实例。
- ModelManager。
- ToolManager、ToolRegistry 和只读工具配置。
- SessionManager。
- 数据库基础设施。
- 配置和健康检查器。
- 日志器。

### 4.2 不能作为共享可变状态

- 当前 `session_id`。
- 当前 `run_id`。
- 当前用户输入。
- 当前 context_text。
- 当前事件 sink。
- 当前 ExecutionResult。
- 某个用户的 pending confirmation。
- 某个 WebSocket 连接的队列。

这些数据必须放到 request/run context、pending registry 或连接对象中，并按 ID 隔离。

## 5. Factory 设计

Factory 至少支持两类构建方式：

```text
build_production(config) -> Runtime
build_for_test(overrides...) -> Runtime
```

覆盖规则：

1. 显式注入对象优先于默认创建。
2. 只注入部分依赖时，未注入部分仍按生产装配规则创建。
3. 注入的对象必须满足 Runtime 所需的最小协议。
4. Factory 不应悄悄创建第二个 SessionManager 或第二个数据库仓库。
5. Factory 初始化失败必须转换成 `dependency_init_failed`。

## 6. Runtime 初始化恢复

Runtime 初始化时调用：

```text
SessionManager.recover_interrupted_runs()
```

将数据库中处于以下状态的旧 run 标记为 `interrupted`：

```text
pending
running
waiting_user
```

V1 不进行跨进程断点续跑。原因是 ReActExecutor 的确认恢复需要原始执行上下文，而该上下文不在 SQLite 中完整持久化。

## 7. 资源释放

Runtime 需要为可释放依赖提供统一关闭入口，例如：

```text
Runtime.close()
```

关闭顺序由实际依赖决定，但至少要考虑：

- MCP 或外部工具连接。
- WebSocket 相关任务。
- 数据库连接或仓库资源。
- 后台线程/执行器。
- 日志 handler。

关闭必须幂等，且不能删除 Memory 历史。

## 8. 需要联动阅读

装配实现前必须阅读：

- `src/models/` 的 ModelManager 构造、配置和健康接口。
- `src/tools/` 的 ToolManager、ToolRegistry、ToolRuntime 和安全策略。
- `src/agent/orchestrator/react_agent.py` 的构造函数。
- `src/agent/planner/`、`src/agent/analyzer/` 的真实构造方式。
- `src/agent/react_executor/` 的配置和依赖。
- `src/memory/` 的 SessionManager、RuntimeMemoryAdapter 和 MemoryConfig。

特别注意当前项目可能处于迁移期，不能因为某个旧文件仍存在就把旧实现自动装配进正式 Runtime。

