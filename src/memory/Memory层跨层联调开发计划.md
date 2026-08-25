# Memory / Context MVP 跨层联调开发计划

本文档专门描述 Memory 实现完成后，如何和已有 Agent 层以及后续 Runtime / CLI / API 联调。它不替代 `Memory层开发步骤与验收标准.md`，而是补充“改哪一层、传什么数据、出现问题先查哪里”。

## 1. 联调总链路

```text
CLI / API
  -> Runtime.run(session_id, user_input)
  -> SessionManager.get_or_create_session()
  -> 保存 user Message + 创建 AgentRun
  -> ContextBuilder.build()
  -> ReactAgent
      -> Analyzer.analyze(user_input)
      -> Planner.create_plan(user_input, task)
      -> ReActExecutor.execute(history=context_text, event_callback=...)
          -> Models
          -> Tools
      -> Response / OutputFeedback
  -> 保存可见 ExecutionEvent
  -> 保存 assistant Message
  -> 更新 AgentRun
  -> maybe_auto_summarize()
  -> 返回结果
```

关键边界：

```text
Memory 提供状态和上下文，不替代推理链。
Runtime 负责装配和生命周期，不直接写 SQL。
ReactAgent 负责调用 Analyzer、Planner、ReActExecutor。
ReActExecutor 负责执行和产生事件。
Models 负责模型调用和压缩能力。
Tools 不直接调用 Memory。
```

## 2. ReactAgent 联调方案

当前兼容目标：

```python
short_term_memory.add_message("user", user_input)
history = short_term_memory.get_history_text()
short_term_memory.add_message("assistant", response)
```

适配方式：

```text
Runtime 为每个 session 创建绑定 session_id 的 ShortTermMemory。
ReactAgent 保留已有方法调用方式。
ShortTermMemory 的内部实现改为委托 SessionManager。
get_history_text() 委托 ContextBuilder。
```

允许的小幅修改：

```text
为 ReactAgent 增加可选 session_id、run_id、event_callback。
将 Runtime 生成的 context_text 传入现有执行入口。
将执行器事件回调透传到 Memory。
```

不应修改：

```text
不让 ReactAgent 直接拼接 SQL。
不把摘要、SQLite 和事件持久化逻辑写进 Analyzer、Planner 或 ReActExecutor 主循环。
```

联调验收：

```text
同一 session 的第二轮可以引用第一轮内容。
切换 session 后上下文隔离。
ReactAgent 旧调用路径仍可运行。
```

## 3. Analyzer 联调方案

MVP 采用间接接入：

```text
Analyzer 继续接收当前 user_input。
Memory 生成的 context_text 先由 ReactAgent / Runtime 传给下游执行链路。
Analyzer 不直接读取 SessionManager。
```

原因：

```text
当前目标是先跑通 Agent，避免把会话存储职责扩散到 Analyzer。
```

后续发现以下问题时再扩展：

```text
“继续刚才的任务”
“按上面的条件修改”
“刚才那个文件”
```

可选扩展：

```python
analyze(user_input, context_text=None)
```

扩展要求：

```text
context_text 必须是可选参数。
原有 analyze(user_input) 调用不受影响。
新增参数的测试要覆盖空上下文和已有上下文。
```

## 4. Planner 联调方案

MVP 采用间接接入：

```text
Planner 继续消费 user_input 和 Analyzer 输出的 task。
不要求 Planner 自己查询 Memory。
```

Memory 需要确保 summary 和最近消息中能保留：

```text
用户目标。
已确定的约束。
文件路径。
已经完成的步骤。
未完成任务。
用户偏好。
```

如果任务规划确实需要完整前文，再考虑：

```python
create_plan(user_input, task, planning_context=None)
```

不建议在 MVP 阶段把整份 SessionState 直接传给 Planner。Planner 只应接收经过 ContextBuilder 整理的必要文本或结构。

## 5. ReActExecutor 联调方案

上下文方向：

```text
ContextBuilder -> context_text
ReactAgent / Runtime -> executor.execute(history=context_text)
```

事件方向：

```text
ReActExecutor -> ExecutionEvent
ReactAgent / Runtime -> Memory.event_mapper
Memory -> execution_events SQLite 表
```

优先使用：

```python
execute(..., history=history, event_callback=callback)
execute_stream(..., include_internal=False)
```

事件处理原则：

```text
先判断 visible_to_user。
再做 event_type -> display_type 映射。
最后脱敏、截断并持久化。
```

重点验证：

```text
tool_started/tool_finished。
command_started/command_finished。
file_edited 或 preview。
confirmation_requested。
retry、fallback、error。
final_answer。
```

禁止进入普通用户回放：

```text
隐藏 reasoning。
内部 ActionPacket。
原始 prompt。
原始 Observation。
未脱敏工具参数和输出。
```

## 6. Models 联调方案

Memory 不直接访问模型 provider，而是调用 Models 层现有能力：

```text
ConversationSummarizer
  -> ModelManager.compress_context()
  -> ContextCompressionResult
  -> 写入 session_summaries
```

Memory 负责：

```text
选择待压缩的早期消息。
组织 chunks。
指定需要保留的关键信息。
保存 summary 版本和覆盖范围。
失败降级。
```

Models 负责：

```text
模型选择。
模型调用。
重试。
健康检查。
压缩结果协议。
```

联调验收：

```text
mock 模型可测试摘要流程。
真实模型可选验证摘要质量。
压缩失败时本轮不被阻塞。
Memory 不重复实现 generate_json、路由和 provider 调用。
```

## 7. Tools 联调方案

Tools 不直接对接 Memory：

```text
Tools -> ToolResult
ReActExecutor -> Observation / ExecutionEvent
Memory -> 保存用户可见事件摘要
```

Memory 保存：

```text
工具名称。
用户可见参数摘要。
用户可见结果摘要。
错误码。
artifact_ref / raw_ref（如未来启用）。
```

Memory 不保存：

```text
完整命令输出。
完整文件内容。
API Key、Cookie、token。
未脱敏参数。
```

联调验收：

```text
工具成功、失败和需要确认的三类情况都能回放。
工具原始结果不会误进入模型上下文或用户历史。
```

## 8. Runtime / CLI / API 联调方案

Runtime 是最终装配层，应提供：

```python
build_runtime() -> Runtime
Runtime.run(session_id, user_input) -> RunResult
Runtime.run_stream(session_id, user_input) -> Iterator[ExecutionEvent | RunResult]
Runtime.get_session(session_id) -> SessionState
Runtime.get_timeline(session_id) -> list[TimelineItem]
Runtime.health() -> HealthStatus
```

一次 `run` 的固定顺序：

```text
1. 校验或生成 session_id。
2. 加载/创建 SessionState。
3. 保存用户输入。
4. 创建 AgentRun。
5. 构建上下文。
6. 调用 ReactAgent。
7. 回调保存可见事件。
8. 保存最终助手消息。
9. 更新 AgentRun 和 SessionState。
10. 触发摘要。
11. 返回结构化结果。
```

CLI 最小验收：

```text
agent chat
agent chat --session-id <id>
agent sessions
agent timeline --session-id <id>
```

API 最小验收：

```text
POST /sessions
POST /sessions/{session_id}/runs
GET /sessions/{session_id}
GET /sessions/{session_id}/timeline
GET /health
```

CLI 可以先完成，API 在 CLI 多轮对话稳定后接入。

## 9. 问题定位顺序

当用户说“Agent 忘记前文”时，按以下顺序排查：

```text
1. session_id 是否在两轮中一致。
2. user/assistant Message 是否写入 SQLite。
3. 重启后 SessionManager 是否加载到消息。
4. ContextBuilder 是否选中了正确消息。
5. context_text 是否传入 ReactAgent / ReActExecutor。
6. Analyzer / Planner 是否需要可选上下文参数。
7. 模型 prompt 是否实际包含 history。
```

当用户说“回放内容不完整”时：

```text
1. ExecutionEvent.visible_to_user 是否为 True。
2. event_callback 是否被透传。
3. event_mapper 是否识别 event_type。
4. event_id 是否因幂等逻辑被误跳过。
5. timeline_seq 是否正确分配。
6. messages 和 execution_events 是否按同一 session 合并查询。
```

当用户说“长对话又忘记早期内容”时：

```text
1. 是否达到 summary_trigger_messages。
2. 是否找到了 summary 未覆盖的早期消息。
3. compress_context 是否成功。
4. session_summaries 是否写入新版本。
5. current_summary_id 是否更新。
6. ContextBuilder 是否读取当前 summary。
```

## 10. 跨层变更记录要求

任何跨层适配都要记录：

```text
变更文件：
变更原因：
原接口：
新接口：
影响的调用方：
是否保持向后兼容：
新增测试：
对应设计文档：
```

如果变更超过“小幅适配”范围，应暂停编码并先更新：

```text
Memory层架构与跨层交互设计.md
Memory层设计决策汇总.md
Memory层开发步骤与进度.md
```

## 11. Step 0 接口差异记录

2026-08-19 已完成开发前接口核对，详细记录见：

```text
Memory层Step0开发前接口差异记录.md
```

后续跨层联调必须特别注意：

```text
1. ReactAgent 需要双模式，旧兼容模式自行写入消息，正式 Runtime 模式不自行写入消息。
2. ReactAgent 当前未向 ReActExecutor 透传 event_callback，Step 10-11 前必须适配。
3. ReActExecutor 已有 history、event_callback 和 visible_to_user 事件能力，Memory 不应重写执行器事件体系。
4. Models 已有 compress_context，Memory 摘要只调用该接口，不直接访问 provider。
5. src/agent 存在导入路径兼容问题，Step 10 ReactAgent 适配前必须修复或提供包级兼容导出。
6. Runtime / CLI / API 正式入口尚未开发，Memory 当前阶段只提供预留接口。
```
