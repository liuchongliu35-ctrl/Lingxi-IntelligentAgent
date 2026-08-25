# Runtime 层开发步骤与进度

> 文档性质：Runtime V1 开发总入口  
> 当前日期：2026-08-24  
> 当前阶段：Runtime V1 开发中（Step 0-8 已完成）  
> 上位设计：`Runtime架构与模块设计.md`、`Runtime公共契约与数据模型设计.md`、`Runtime依赖装配与生命周期设计.md`、`Runtime运行流程与Memory集成设计.md`、`Runtime事件流与确认恢复设计.md`、`Runtime错误降级与健康检查设计.md`

本文档只记录 Runtime V1 的开发路线、分卷入口、步骤状态、依赖关系和跨 Session 更新规则，不替代 Runtime 设计文档。开发时如果步骤文档与设计文档存在冲突，必须先停止实现、核对两轮问答和 Runtime 设计文档，再同步修正文档；不得由开发者临时改变架构边界。

本次只规划 Runtime，不规划 CLI / API 的具体开发步骤。CLI / API 后续必须基于 Runtime 完成后的稳定契约继续编写步骤进度文档。

---

## 1. 开发前固定架构

Runtime V1 必须服务以下正式主链路：

```text
用户输入
  -> CLI / API
  -> Runtime
  -> RuntimeMemoryAdapter / SessionManager
  -> ReactAgent(manage_memory=False)
      -> Analyzer
      -> Planner
      -> ReActExecutor
          Reasoning -> Decision -> Tool / Model / User / Control -> Observation -> Checker
      -> OutputFeedbackProcessor
  -> RuntimeMemoryAdapter complete/fail
  -> RuntimeResult
  -> CLI / API
```

必须始终保持：

```text
1. Runtime 是应用编排、依赖装配、session/run 生命周期和结果归一化层。
2. Runtime 不重新实现 Analyzer、Planner、ReActExecutor、Models 或 Tools 的内部职责。
3. Runtime 不直接操作 SQL，不直接访问 SQLite 表，不绕过 SessionManager / RuntimeMemoryAdapter。
4. 正式 Runtime 模式必须使用 ReactAgent 的 manage_memory=False。
5. user message、assistant message、AgentRun、ExecutionEvent、SessionSummary 由 Memory 负责持久化。
6. Runtime 统一接管 ReActExecutor 的 event_callback，并只对外转发安全事件。
7. 内部事件、隐藏推理、raw prompt、raw tool result 不进入普通用户 timeline。
8. RuntimeResult / RuntimeEvent 是 CLI/API 的稳定契约，不直接暴露底层对象。
9. Runtime 可以适配已有层，但不得为了入口层大范围重构核心层。
10. 旧 LongTermMemory / RAG 原型不作为 Runtime 会话持久化依赖。
```

---

## 2. 当前真实状态

### 2.1 已完成

```text
Runtime / CLI / API 第一轮设计问答
Runtime / CLI / API 第二轮设计问答
src/app/runtime、src/app/cli、src/app/api 目录创建
Runtime 设计文档 6 份
Runtime / CLI / API 总览文档
Runtime / CLI / API 总体验收文档
Memory V1 RuntimeMemoryAdapter 预留 facade
ReactAgent 支持 manage_memory=False 的正式 Runtime 模式
ReActExecutor 支持 event_callback、ExecutionResult、waiting_user、resume_after_confirmation
OutputFeedbackProcessor 已存在
Runtime contracts / errors / serialization 已完成
Runtime factory / dependency lifecycle 已完成
Runtime core facade 已完成
Runtime 与 Memory begin_turn / session/run / context 接入已完成
Runtime 与 ReactAgent 正式模式及 OutputFeedback 接入已完成
```

### 2.2 待开发

```text
  Runtime 事件回调包装与转发
  Runtime 与 Memory complete/fail/waiting/replan 收口
  Runtime resume / cancel
  Runtime health
  Runtime session/list/timeline/delete/export facade
  Runtime 单元测试、跨层集成测试和回归测试
```

### 2.3 迁移期处理口径

```text
ReactAgent:
  保留旧兼容模式，但 Runtime 只能走 manage_memory=False。

Memory:
  Runtime 不直接访问 repo 或 SQL，优先使用 RuntimeMemoryAdapter / SessionManager 公开接口。

ReActExecutor:
  Runtime 不自行执行工具，不自行构造 Observation，只处理 ExecutionEvent 和 ExecutionResult。

OutputFeedback:
  Runtime 不自己拼最终自然语言回答，优先复用 OutputFeedbackProcessor。

Tools:
  Runtime 不绕过 ToolManager / ToolRuntime / ToolPolicy。

Models:
  Runtime 不直接调用 Provider。上下文压缩由 Memory 通过 ModelManager.compress_context() 处理。
```

---

## 3. 分卷与连续步骤

步骤编号跨分卷连续且唯一。开发时按依赖顺序推进，除非步骤文档明确允许并行。

| 分卷 | 步骤 | 内容 | 当前状态 |
|---|---:|---|---|
| [契约装配](Runtime层开发步骤与进度(1)-契约装配.md) | Step 0-5 | 基线、contracts、serialization、errors、pending registry、factory 生命周期 | 已完成 |
| [运行主链路](Runtime层开发步骤与进度(2)-运行主链路.md) | Step 6-11 | Runtime core、Memory begin、ReactAgent 调用、事件流、结果收口、主链路测试 | Step 6-10 已完成，Step 11 待开发 |
| [恢复健康验收](Runtime层开发步骤与进度(3)-恢复健康验收.md) | Step 12-18 | resume/cancel、health、session facade、export、启动恢复、跨层验收 | 待开发 |

总体依赖：

```text
Step 0-5 契约、错误、序列化、装配底座
  -> Step 6-11 普通 run、Memory、ReactAgent、事件和结果闭环
  -> Step 12-18 确认恢复、health、session 管理、导出和 Runtime 验收
```

允许的有限并行：

```text
Step 14 health 与 Step 15 session/export facade 可以在 Step 6-11 主链路稳定后并行研究。
Step 12 resume 与 Step 13 cancel 必须在 Step 10 waiting_user 收口稳定后开发。
```

---

## 4. 每个 Step 的固定栏目

每个 Step 至少包含：

```text
目标:
  本 Step 要解决的唯一主题。

对应设计文档:
  本 Step 开发必须参考的 Runtime 设计文档和小节。

前置条件:
  必须已完成的 Step、已有接口和测试。

涉及文件:
  预计新增/修改的源码、测试和文档。

必做:
  必须落地的行为、协议、安全边界和错误处理。

明确不做:
  本 Step 不允许顺手扩张的能力。

测试与验收:
  本 Step 需要新增/修改的测试和建议命令。

完成后回写:
  状态、日期、实际修改、测试结果、偏差、遗留问题和下一步。
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

---

## 5. 全局禁止偏离项

Runtime V1 开发周期内禁止：

```text
不在 Runtime 中重新实现 Analyzer / Planner / ReActExecutor 主流程。
不让 Runtime 直接执行工具或调用 Provider。
不直接操作 SQL 或 SQLite 表。
不把旧 LongTermMemory / RAG 当作新会话 Memory。
不让 ReactAgent 在 Runtime 正式模式下重复写消息。
不把 raw prompt、hidden reasoning、raw tool result、raw observation 暴露给 CLI/API。
不把内部事件写入普通用户 timeline。
不承诺进程重启后的断点续跑。
不承诺强制中断正在执行的工具进程。
不实现 CLI / API 的具体命令和路由。
不为适配 Runtime 大范围重构其他层。
```

---

## 6. 当前进度

```text
当前分卷：Runtime层开发步骤与进度(2)-运行主链路.md
当前 Step：Step 10
当前状态：已完成（2026-08-25）
下一步：进入 Step 11，完成 Runtime 普通 run 主链路集成验收；继续保持 manage_memory=False，且不绕过 Memory/Tools/Models 的公开接口。
```

---

## 7. 跨层回归测试池

Runtime 每个关键阶段完成后，按影响范围选择运行：

```powershell
python -m pytest tests/test_memory_runtime_adapter.py -q
python -m pytest tests/test_memory_v1_end_to_end_acceptance.py -q
python -m pytest tests/test_memory_react_agent_adaptation.py -q
python -m pytest tests/test_react_agent_with_react_executor.py -q
python -m pytest tests/test_react_executor_events.py tests/test_react_executor_confirmation.py tests/test_react_executor_preview_resume.py -q
python -m pytest tests/test_output_feedback_processor.py -q
```

如果项目当前 pytest 环境存在迁移期失败，必须记录失败测试名、失败原因和是否与本 Step 相关，不能简单写“测试失败”。
## Step 2 Completion Record (2026-08-24)

```text
Status: completed
Implementation:
  - Added src/app/runtime/serialization.py.
  - Added tests/app/runtime/test_runtime_serialization.py.
  - Exported the serialization boundary from src/app/runtime/__init__.py.
  - Added safe recursive serialization for dataclass, Enum, datetime,
    Mapping, list/tuple/set, Executor results, OutputFeedback, Memory models,
    RuntimeEvent, and RuntimeResult.
  - Added recursive filtering for raw prompts, hidden reasoning, raw tool
    results, raw observations, credentials, internal context, command
    arguments, and environment values.
  - Added explicit debug allowlist and PendingConfirmation public-field
    whitelist.
Verification:
  python -m pytest tests/app/runtime/test_runtime_serialization.py -q
  9 passed
  python -m pytest tests/app/runtime/test_runtime_current_baseline.py tests/app/runtime/test_runtime_contracts.py tests/app/runtime/test_runtime_serialization.py tests/test_memory_runtime_adapter.py tests/test_memory_react_agent_adaptation.py -q
  45 passed
Scope:
  Memory event_mapper, Runtime.run, PendingRunRegistry, error mapping,
  Factory, CLI, and API remain unchanged and are deferred to their steps.
Next: Step 3 Runtime errors, status mapping, and exception adaptation.
```
## Step 3 Completion Record (2026-08-24)

```text
Status: completed
Added:
  - src/app/runtime/errors.py
  - tests/app/runtime/test_runtime_errors.py
Updated:
  - src/app/runtime/__init__.py
Implemented:
  - Stable RuntimeErrorCode enum covering the design V1 codes.
  - RuntimeException with status/error separation and sanitized metadata.
  - Cross-layer mapping for Memory, Models, Tools, ReActExecutor, and
    ordinary Python exceptions.
  - HTTP status and CLI exit-code mappings.
  - Failure-shaped RuntimeResult conversion.
  - No default exposure of provider/tool internal error codes or secrets.
Verification:
  - Step 3 tests: 24 passed.
  - Runtime and cross-layer regression: 69 passed.
  - compileall: passed.
Next:
  - Step 4 PendingRunRegistry and run-level temporary state isolation.
```

## Step 4 Completion Record (2026-08-24)

```text
Status: completed
Added:
  - src/app/runtime/pending_runs.py
  - tests/app/runtime/test_runtime_pending_runs.py
Updated:
  - src/app/runtime/__init__.py
Verification:
  - Step 4 tests: 11 passed.
  - Runtime regression: 67 passed.
  - Memory/Agent cross-layer regression: 13 passed.
  - compileall: passed.
Design boundary:
  - Registry is process-local and thread-safe.
  - Executor context is available only to same-process Runtime internals.
  - Public snapshots contain only safe confirmation and metadata fields.
  - No SQLite persistence, cross-process recovery, WebSocket queue, or
    force-stop behavior was added.
Step 3 progress correction:
  - Step 3 is completed and its implementation/test record is preserved
    in this document and the Step 1-5 progress volume.
Next:
  - Step 5 RuntimeFactory, dependency lifecycle, and startup recovery base.
```

## Step 5 Completion Record (2026-08-24)

```text
Status: completed
Added:
  - src/app/runtime/core.py
  - src/app/runtime/factory.py
  - tests/app/runtime/test_runtime_factory.py
  - src/agent/analyzer_config.py
  - src/agent/complexity_analyzer.py
  - src/agent/intent_classifier.py
  - src/agent/uncertainty_detector.py
Updated:
  - src/app/runtime/__init__.py
  - src/agent/analyzer/complexity_analyzer.py
  - src/agent/react_executor/__init__.py
Implemented:
  - RuntimeConfig with workspace, model, Memory, Tools, and
    ReActExecutor configuration injection.
  - RuntimeFactory.build_production(), build_for_test(), and build().
  - Explicit dependency reuse for partial test injection, including
    RuntimeMemoryAdapter.session_manager/context_builder reuse.
  - Formal Runtime ReactAgent assembly with manage_memory=False.
  - Startup SessionManager.recover_interrupted_runs() invocation.
  - Runtime.close() with idempotence, deduplicated releasable dependency
    shutdown, and sanitized close error recording.
  - Dependency construction failures mapped to dependency_init_failed.
  - Thin Analyzer compatibility imports preserved for existing callers
    during the current package migration.
Verification:
  - python -m pytest tests/app/runtime/test_runtime_factory.py -q
    7 passed
  - Step 5 Runtime and cross-layer regression:
    115 passed
  - python -m compileall -q src/app/runtime src/agent tests/app/runtime
    passed
Scope:
  - Runtime.run, run_stream, resume, cancel, health checker,
    CLI/API adapters, and cross-process recovery remain deferred.
Known migration note:
  - Analyzer implementation imports were corrected to package-local paths;
    root-level compatibility modules preserve legacy import paths without
    changing Analyzer behavior. ReActExecutor package exports preserve the
    existing constant import surface during the package migration.
Next:
  - Step 6 Runtime main execution chain.
```
