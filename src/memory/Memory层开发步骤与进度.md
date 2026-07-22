# Memory 层开发步骤与进度

本文档用于跨 Session 记录 Memory 层开发进度。

## 当前定位

Memory 层负责短期对话上下文和长期记忆。Session 生命周期、会话概要导出后续也会与 Memory 层协作。

## 重点必看：跨 Session 进度更新规则

后续每完成一个可验收开发步骤，都必须同步更新对应层的进度文档。

```text
完成一个 Step
  -> 跑测试或完成逻辑验证
  -> 确认没有明显问题
  -> 更新对应层的“开发步骤与进度.md”
  -> 下一个对话继续未完成步骤
```

不需要每改一个小函数都更新；但完成一个清晰阶段后必须更新，并记录已完成内容、验证方式、当前未完成项和下一轮建议。

## 已完成

### Step 0：基础记忆能力

状态：已完成原型版

主要文件：

```text
src/memory/short_term_memory.py
src/memory/long_term_memory.py
```

已完成内容：

- `ShortTermMemory` 可保存会话消息。
- `LongTermMemory` 保留基础持久化和检索方向。
- 当前能支撑主流程占位运行。

## 待开发

### Step 1：SessionManager

状态：待开发

目标：

- 支持 `session_id`。
- 管理会话生命周期。
- 为跨对话概要导出提供入口。

### Step 2：会话概要导出

状态：待开发

目标：

- 保存到：

```text
storage/sessions/{session_id}/summary.md
```

验收标准：

- 新 Session 可以读取上一个 Session 的概要继续开发。

### Step 3：短期记忆压缩

状态：待开发

目标：

- 支持消息条数限制、token 限制和摘要压缩。

### Step 4：长期记忆增强

状态：待开发

目标：

- 区分对话长期记忆和知识长期记忆。
- 增加 metadata、source、score。

## 暂停开发时更新格式

```text
已完成：
- ...

当前未完成：
- ...

下一轮建议：
- 从 Step X 开始，目标是 ...
```
