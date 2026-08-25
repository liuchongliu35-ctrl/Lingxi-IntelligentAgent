# Memory Step 0 开发前接口差异记录

> 日期：2026-08-19  
> 阶段：Step 0 开发前检查与旧原型隔离  
> 目的：确认旧 Memory 原型边界、现有跨层接口、后续接入风险和目录约定。

## 1. 结论

Step 0 已完成开发前检查。当前 `src/memory/short_term_memory.py` 和 `src/memory/long_term_memory.py` 都不是新 Memory / Context MVP 的设计基础。

后续正式实现必须以设计文档确定的新结构为准：

```text
SQLiteSessionRepository
SessionManager
ShortTermMemory session 兼容层
ContextBuilder
ConversationSummarizer
ExecutionEvent mapper
memory JSONL logging
```

不得沿用旧原型中的普通列表截断历史、pickle 会话持久化或文件式 summary 方案。

## 2. 旧 Memory 原型检查

### short_term_memory.py

现状：

```text
只有进程内 list[dict]。
没有 session_id。
没有 SQLite。
没有 SessionState。
没有 AgentRun。
没有 timeline_seq。
没有 summary。
超过 max_history 后直接丢弃早期消息。
```

后续仅保留兼容方法名：

```text
add_message(role, content, metadata=None)
get_history()
get_history_text()
clear()
```

兼容边界：

```text
正式 Runtime 模式下，Runtime / SessionManager 是唯一消息写入者。
ReactAgent 需要以 manage_memory=False 或类似模式运行，避免重复写入。
旧兼容模式下，直接调用 ReactAgent.run() 时可以继续由 ReactAgent 通过 ShortTermMemory 写入。
clear() 只作为旧兼容接口，不删除 SQLite 历史，也不静默新建 session。
```

### long_term_memory.py

现状：

```text
使用 pickle 保存 documents。
用途更接近旧文档检索原型。
RAGSystem 依赖它的 search/add_document/get_document_count/clear。
它不是会话 Memory，也不是当前 SQLite 会话持久化源。
```

后续处理：

```text
暂时保留，不删除。
新 Memory MVP 和新 Runtime 不依赖它保存会话。
Runtime / CLI / API 正式入口开发前，只做预留，不把 LongTermMemory 当成主链路依赖。
```

## 3. ReactAgent 接口检查

当前 `ReactAgent` 已直接调用 `short_term_memory`：

```text
_execute_request():
  short_term_memory.add_message("user", user_input)
  history = short_term_memory.get_history_text()
  executor.execute(..., history=history)

run_with_result():
  _remember_assistant_response(result.output)

_remember_assistant_response():
  short_term_memory.add_message("assistant", response)

_build_execution_stream():
  short_term_memory.add_message("user", user_input)
  history = short_term_memory.get_history_text()
```

差异与风险：

```text
1. 正式 Runtime 如果也写 user/assistant Message，会和 ReactAgent 当前写入重复。
2. ReactAgent 当前没有向 executor.execute() 透传 event_callback。
3. run_stream() 会过滤事件再 yield，但 execute_stream() 当前并非真正逐事件实时执行。
4. ReactAgent 当前持有 long_term_memory / rag_system，但主流程未使用它们。
```

后续适配方向：

```text
Step 4：先让 ShortTermMemory 兼容 add_message/get_history_text。
Step 10：给 ReactAgent 增加双模式控制，例如 manage_memory=True/False。
Step 10-11：增加 event_callback 透传，Runtime 模式由外层保存可见事件。
```

## 4. ReActExecutor 接口检查

可复用接口：

```text
execute(plan, task, user_input, history="", event_callback=None, event_callback_visible_only=False)
execute_stream(plan, task, user_input, history="", include_internal=True)
ExecutionResult.events
ExecutionEvent.visible_to_user
EventStream.visible_events()
EventStream.internal_events()
EventStream.to_user_timeline()
EventStream.to_model_context()
```

事件对象字段：

```text
execution_id
plan_id
type
message
event_id
task_id
step_id
timestamp
visible_to_user
payload
```

后续 Memory 映射规则：

```text
先判断 visible_to_user。
visible_to_user=True 才进入 execution_events 和用户回放。
visible_to_user=False 只允许进入日志或后续审计。
event_id 作为幂等键。
result.events 作为回调路径之外的兜底或补偿扫描来源。
```

现有脱敏可复用能力：

```text
src.agent.react_executor.react_executor_observation.sanitize_sensitive()
src.agent.react_executor.react_executor_events.sanitize_event_payload()
src.agent.react_executor.react_executor_events.payload_summary()
```

注意：

```text
当前 ReActExecutor 代码文件中存在若干导入路径仍按旧扁平结构书写，动态 import 未完全通过。
在 Memory 纯模块实现阶段可先不依赖动态导入 ReActExecutor。
到 Step 10-11 前必须处理 agent import 兼容问题。
```

## 5. Models 接口检查

`ModelManager.compress_context()` 已存在，签名可满足 Memory 自动摘要：

```python
compress_context(
    *,
    source_type="text",
    text=None,
    chunks=None,
    target_tokens=None,
    target_chars=None,
    preserve_keys=None,
    preserve_entities=None,
    trigger_reason=None,
    metadata=None,
    allow_rule_fallback=False,
    max_chunk_chars=None,
) -> ContextCompressionResult
```

`ContextCompressionResult` 关键字段：

```text
success
short_summary
compressed_text
compressed_chunks
source_refs
original_length
compressed_length
compression_ratio
trigger_reason
round_index
loss_risk
key_points
preserved_entities
warnings
code
error
metadata
```

Memory 责任边界：

```text
只选择待压缩消息、组织 chunks、调用 compress_context、保存 summary 版本、处理失败降级。
不直接访问 provider。
不重复实现 generate_json、模型路由、重试或健康检查。
```

## 6. Analyzer / Planner / Tools 边界

Analyzer：

```text
MVP 暂不直接依赖 Memory。
当前仍以 analyze(user_input) 为主。
后续如需处理“继续刚才”“按上面的方案”等指代，可加可选 context_text 参数。
```

Planner：

```text
MVP 暂不直接依赖 Memory。
当前仍以 create_plan(user_input, task) 为主。
后续如确需前文，可加可选 planning_context 参数。
```

Tools：

```text
不直接调用 Memory。
工具输出由 ReActExecutor 转为 Observation / ExecutionEvent。
Memory 只保存用户可见、脱敏、截断后的事件摘要。
```

## 7. import 路径与兼容问题

已确认 `src/agent` 存在整理后未完全迁移的导入问题：

```text
src.agent.orchestrator.react_agent 动态导入失败：
  cannot import name 'Executor' from 'src.agent.executor'

src.agent.react_executor.react_executor 动态导入失败：
  No module named 'src.agent.react_executor_checker'
```

原因判断：

```text
代码文件已经移动到 analyzer/planner/executor/orchestrator/react_executor 子目录。
部分导入仍按 src.agent.<module> 的旧扁平路径书写。
子目录缺少 __init__.py 或包级 re-export，导致 from src.agent.executor import Executor 等导入不能解析。
```

处理原则：

```text
Memory Step 1-9 的纯模块开发先不依赖这些动态导入。
Step 10 ReactAgent 适配前必须修复或提供兼容导出。
不在 Step 0 中进行 agent import 重构。
```

## 8. 目录约定

正式持久化与日志：

```text
storage/agent_memory.db
logs/memory.log
```

测试：

```text
tests/test_memory_*.py
```

测试约束：

```text
Memory 单元测试使用临时 SQLite。
不得污染 storage/agent_memory.db。
测试中如需模拟摘要，优先使用 mock ModelManager 或 stub compress_context。
```

## 9. Step 0 验收对照

```text
已确认 ReactAgent 对 short_term_memory 的调用方式。
已确认 ReActExecutor 的 history、ExecutionEvent、EventStream、event_callback 接口。
已确认 ModelManager.compress_context() 的参数与 ContextCompressionResult 返回字段。
已记录旧 short_term_memory.py / long_term_memory.py 的兼容边界。
已建立 storage、logs、tests 的目录约定。
已记录 agent import 兼容风险。
已确认 Runtime / CLI / API 尚未开发，Memory 阶段只预留接口。
```

