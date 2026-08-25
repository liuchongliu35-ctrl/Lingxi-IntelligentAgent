# Memory 层架构与跨层交互设计

本文档描述 Memory / Context MVP 在整个 Agent 架构中的位置，以及它和已完成层之间如何交互。

## 1. 整体位置

当前 Agent 主链路是：

```text
用户输入
  -> Runtime / CLI / API
  -> SessionManager / Memory
  -> ReactAgent
      -> Analyzer
      -> Planner
          生成 TaskPlan / TaskUnit / PlanStep
      -> ReActExecutor
          Reasoning -> Decision -> Tool -> Observation -> Checker
      -> Response
  -> OutputFeedback
  -> Memory 更新
  -> 用户反馈
```

更准确地说，Memory 不是插在 Analyzer 和 Planner 中间的新决策层，而是贯穿一轮请求前后的状态层：

```text
请求前：
  加载 session
  写入用户消息
  构建上下文

请求中：
  保存用户可见执行事件

请求后：
  写入助手消息
  更新 run 状态
  触发摘要
  保存 session 状态
```

## 2. Runtime 与 Memory

Runtime 是 Memory 的第一调用方。

Runtime 应负责：

```text
1. 接收 session_id。
2. 调用 SessionManager.get_or_create_session()。
3. 调用 SessionManager.append_message() 保存用户输入。
4. 调用 SessionManager.create_run() 创建本轮 AgentRun。
5. 调用 ContextBuilder.build() 生成 context_text。
6. 把绑定 session 的 ShortTermMemory 或 context_text 注入 ReactAgent。
7. 把 ReActExecutor 的可见事件写入 Memory。
8. 保存最终 assistant message。
9. 标记 run completed / failed / waiting_user / blocked。
10. 调用 maybe_auto_summarize()。
```

Runtime 不应该直接写 SQL。所有持久化都经由 SessionManager 或 SQLiteSessionRepository。

## 3. ReactAgent 与 Memory

当前 `ReactAgent` 的关键调用习惯是：

```python
short_term_memory.add_message("user", user_input)
history = short_term_memory.get_history_text()
short_term_memory.add_message("assistant", response)
```

这个接口必须兼容，否则接入成本会变大。

MVP 接入策略：

```text
第一阶段：
  新 ShortTermMemory 保留 add_message / get_history_text / get_history / clear。
  这些接口背后不再是单纯列表，而是绑定 session 的 SQLite 数据。

第二阶段：
  Runtime 创建 session，并把 ShortTermMemory(session_id=xxx) 注入 ReactAgent。
  ReactAgent 继续调用旧接口，也能写入 SQLite 和读取上下文。

第三阶段：
  给 ReactAgent 增加可选 session-aware 接口，如 run_with_session()。
  Runtime 可以更精确地控制 run_id、event_callback 和 summary 更新。
```

推荐不要在第一版里大改 ReactAgent 的核心顺序。先让旧接口背后变成新 Memory 逻辑。

## 4. ReActExecutor 与 Memory

ReActExecutor 已经提供了 Memory 需要的三个关键接入口：

```text
execute(..., history=history)
execute(..., event_callback=callback, event_callback_visible_only=True)
execute_stream(..., include_internal=...)
```

设计决策：

```text
Memory 不直接调用 ReActExecutor。
ReactAgent / Runtime 调用 ReActExecutor。
Memory 通过 ContextBuilder 生成 history/context_text。
ReActExecutor 消费 history。
ReActExecutor 产生 ExecutionEvent。
Memory 保存用户可见 ExecutionEvent。
```

事件保存方式：

```text
非流式 run:
  可以在 execute() 返回后读取 result.events，再保存 visible_to_user=True 的事件。

更推荐的稳定方式:
  Runtime / ReactAgent 调用 execute() 时传入 event_callback。
  每个可见事件产生时立即写入 SQLite。
  这样进程中途异常时，用户已经看到的过程不会全丢。
```

这只需要小幅适配 ReactAgent / Runtime，因为 ReActExecutor 已经支持 event_callback。

## 5. Analyzer 与 Memory

当前 Analyzer 主要接口是：

```text
analyze(user_input)
```

MVP 决策：

```text
Analyzer 不直接依赖 Memory。
Analyzer 仍然消费当前用户输入。
```

原因：

```text
1. 直接给 Analyzer 加 Memory 依赖会扩大改动面。
2. 当前最重要目标是让 Agent 先可跑、多轮上下文能进入执行器。
3. 多轮指代理解可以先通过 Executor 的 history 缓解。
```

后续增强方向：

```text
analyze(user_input, context_text: str | None = None)
```

如果后续发现 Analyzer 对“继续刚才那个任务”“按上面的方案实现”等指代识别不足，可以小幅增加可选 context_text 参数，而不是让 Analyzer 自己读取 Memory 类。

## 6. Planner 与 Memory

当前 Planner 主要接口是：

```text
create_plan(user_input, task)
```

MVP 决策：

```text
Planner 不直接依赖 Memory。
Planner 仍由 Analyzer 输出的 task 和当前 user_input 生成计划。
```

原因：

```text
1. Planner 当前内部方法大量围绕 user_input/task 展开。
2. 强行让 Planner 读取 SessionState 会造成大改。
3. ReActExecutor 已经能通过 history 理解前文约束。
```

后续增强方向：

```text
create_plan(user_input, task, planning_context: str | None = None)
```

或由 Runtime 构造一个增强输入：

```text
contextual_user_input = context_text + "\n\n[Current User Input]\n" + user_input
```

但这类方案会影响 Planner 的日志、测试和规划稳定性，MVP 不优先做。

## 7. Models 与 Memory

Models 层已经提供：

```text
ModelManager.compress_context()
```

Memory 的自动摘要必须复用这个接口。

Memory 不应该：

```text
不直接调用底层 provider。
不自己实现 generate_json。
不自己实现模型路由、重试、健康检查。
```

Memory 应该：

```text
1. 选择要压缩的早期消息。
2. 组织成 chunks 或 text。
3. 调用 model_manager.compress_context()。
4. 检查 ContextCompressionResult.success。
5. 成功则写入 session_summaries。
6. 失败则记录日志并保留旧 summary。
```

## 8. Tools 与 Memory

Tools 不直接调用 Memory。

工具调用链路：

```text
ReActExecutor
  -> ToolManager / ToolRegistry
  -> ToolResult
  -> ObservationPacket
  -> ExecutionEvent
  -> Memory 保存可见摘要
```

Memory 保存的是：

```text
工具名称。
脱敏后的参数摘要。
用户可见的结果摘要。
错误码和用户可见错误信息。
artifact_ref / raw_ref 的引用。
```

Memory 不保存：

```text
原始命令输出全文。
未经脱敏的工具参数。
原始 ToolResult 大对象。
API Key / Cookie / token。
```

## 9. import 路径与集成风险

当前 `src/agent` 已按层整理过，但部分旧 import 路径可能还没迁移。Memory 设计和单元测试可以先独立完成，但整体接入测试前需要处理：

```text
src.agent.planner
src.agent.react_executor
src.agent.output_feedback
src.agent.executor
```

这些 import 的兼容问题不属于 Memory 的核心职责，但会影响 Runtime / ReactAgent 集成。

建议顺序：

```text
1. 先完成 Memory 纯模块测试。
2. 再做 agent 层 import 兼容或路径迁移。
3. 再接 Runtime / CLI。
4. 最后做端到端多轮对话测试。
```

## 10. 跨层适配优先级

适配优先级如下：

```text
1. Memory 兼容 ReactAgent 旧 short_term_memory 接口。
2. Runtime 负责 session_id、run_id 和事件持久化。
3. ReactAgent 小幅增加 session-aware / event_callback 接入能力。
4. ReActExecutor 尽量不改，因为它已有 history 和 event_callback。
5. Analyzer / Planner 暂不改，后续按需要增加可选 context 参数。
```

这样能把改动控制在最小可跑范围内，同时不牺牲后续扩展空间。

