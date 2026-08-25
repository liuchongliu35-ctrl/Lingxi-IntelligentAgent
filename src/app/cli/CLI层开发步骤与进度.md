# CLI 层开发步骤与进度

> 文档性质：CLI V1 开发总入口  
> 当前日期：2026-08-24  
> 当前阶段：CLI V1 待开发  
> 上位设计：`CLI架构与命令设计.md`、`CLI交互式对话与流式输出设计.md`、`CLI会话管理与结果输出设计.md`

本文档只记录 CLI V1 的开发路线、分卷入口、步骤状态、依赖关系和跨 Session 更新规则，不替代 CLI 设计文档。开发时如果步骤文档与设计文档存在冲突，必须先停止实现、核对两轮问答、CLI 设计文档和 Runtime 契约，再同步修正文档；不得由开发者临时改变架构边界。

本次只规划 CLI，不规划 API 的具体开发步骤。

---

## 1. 开发前固定架构

CLI V1 必须服务以下正式主链路：

```text
用户终端输入
  -> Typer CLI
  -> CLI 参数解析 / 交互读取 / 输出渲染
  -> Runtime
      -> Memory / ReactAgent / ReActExecutor / OutputFeedback
  -> RuntimeResult / RuntimeEvent
  -> CLI 人类可读输出或 JSON 输出
```

必须始终保持：

```text
1. CLI 是 Runtime 的命令行适配器。
2. CLI 不装配 Agent 主链路，不直接调用 Analyzer / Planner / ReActExecutor。
3. CLI 不直接访问 SQLite，不调用 SessionManager.repo。
4. CLI 不直接读写 Memory 消息、事件或 summary。
5. CLI 不直接执行工具或绕过 Tools 安全策略。
6. CLI 默认通过 Runtime.run_stream 展示可见执行事件。
7. CLI --no-stream 可以通过 Runtime.run 等待完整结果。
8. CLI --json 输出安全结构化结果，stdout 不混入人类提示文本。
9. CLI 不展示 raw prompt、hidden reasoning、raw observation、raw tool result。
10. CLI 的确认交互必须通过 Runtime.resume / Runtime.cancel 完成。
```

---

## 2. 当前真实状态

### 2.1 已完成

```text
src/app/cli 目录已创建
CLI 设计文档 3 份
Runtime / CLI / API 总体设计已完成
Runtime 步骤进度文档已完成
CLI 预期使用 Typer
```

### 2.2 待开发

```text
Typer 根应用
Runtime factory 接入
命令分组
chat 单次输入
chat REPL
流式事件渲染
waiting_user 确认交互
JSON 输出
sessions / session show / timeline
health
export
delete-session
resume / cancel 可选命令
exit code 映射
main.py 薄启动器收口
CLI 单元测试和 Runtime 集成测试
```

### 2.3 Runtime 前置依赖

CLI 开发依赖 Runtime 稳定契约。原则上应在 Runtime V1 至少完成以下能力后进入 CLI 主开发：

```text
Runtime.run
Runtime.run_stream
Runtime.resume
Runtime.cancel
Runtime.list_sessions
Runtime.get_session
Runtime.get_timeline
Runtime.health
Runtime.export_session
Runtime.delete_session
RuntimeResult / RuntimeEvent / RuntimeErrorCode
```

如果 CLI 先行开发，只能使用 fake Runtime 做命令层测试，不能绕过 Runtime 直接调用 Memory 或 Agent。

---

## 3. 分卷与连续步骤

步骤编号跨分卷连续且唯一。开发时按依赖顺序推进，除非步骤文档明确允许并行。

| 分卷 | 步骤 | 内容 | 当前状态 |
|---|---:|---|---|
| [基础入口](CLI层开发步骤与进度(1)-基础入口.md) | Step 0-5 | 基线、Typer app、Runtime dependency、exit code、rendering、JSON 输出 | 待开发 |
| [Chat交互](CLI层开发步骤与进度(2)-Chat交互.md) | Step 6-11 | chat 单次、流式事件、REPL、waiting_user 确认、resume/cancel 可选命令 | 待开发 |
| [会话管理验收](CLI层开发步骤与进度(3)-会话管理验收.md) | Step 12-18 | sessions、session show、timeline、health、export、delete、main.py、最终验收 | 待开发 |

总体依赖：

```text
Step 0-5 CLI 底座、Runtime 接入、渲染和退出码
  -> Step 6-11 chat、REPL、流式输出和确认交互
  -> Step 12-18 会话管理、导出删除、main.py 和 CLI 验收
```

允许的有限并行：

```text
Step 12 sessions / Step 13 timeline / Step 14 health 可在 Step 0-5 后基于 fake Runtime 并行开发。
Step 8 REPL 必须在 Step 6 chat 单次和 Step 7 流式渲染稳定后开发。
Step 9 waiting_user 确认必须在 Runtime resume/cancel 契约稳定后开发。
```

---

## 4. 每个 Step 的固定栏目

每个 Step 至少包含：

```text
目标:
  本 Step 要解决的唯一主题。

对应设计文档:
  本 Step 开发必须参考的 CLI 设计文档和小节。

Runtime 依赖:
  本 Step 依赖哪些 Runtime 公开入口和 RuntimeResult/RuntimeEvent 字段。

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

CLI V1 开发周期内禁止：

```text
不在 CLI 中创建 Analyzer / Planner / ReActExecutor。
不在 CLI 中直接操作 SQL 或 SQLite。
不在 CLI 中直接调用 SessionManager.repo。
不在 CLI 中直接执行工具。
不绕过 Runtime 做消息写入。
不默认复用上一次 session。
不在非交互模式偷偷等待 y/n。
不默认自动确认危险动作。
不把 raw prompt、hidden reasoning、raw tool result 输出到终端或 JSON。
不把人类提示文本混入 --json 的 stdout。
不实现 API 路由或 WebSocket。
```

---

## 6. 当前进度

```text
当前分卷：CLI层开发步骤与进度(1)-基础入口.md
当前 Step：Step 0
当前状态：待开发
下一步：开发前执行 Step 0，固定 Typer、Runtime 契约和当前入口基线。
```

---

## 7. 回归测试池

CLI 每个关键阶段完成后，按影响范围选择运行：

```powershell
python -m pytest tests/app/cli -q
python -m pytest tests/app/runtime -q
python -m pytest tests/test_memory_runtime_adapter.py tests/test_memory_react_agent_adaptation.py -q
```

如果项目当前 pytest 环境存在迁移期失败，必须记录失败测试名、失败原因和是否与本 Step 相关，不能简单写“测试失败”。

