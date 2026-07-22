# Models 层开发步骤与进度

本文档用于跨 Session 记录 Models 层开发进度。

## 当前定位

Models 层统一封装不同模型提供方，主流程通过 `ModelManager` 调用模型，不直接依赖具体 provider。

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

### Step 0：基础模型适配

状态：已完成基础版

主要文件：

```text
src/models/base_model.py
src/models/mock_model.py
src/models/model_manager.py
src/models/openai_model.py
src/models/qianwen_model.py
src/models/doubao_model.py
```

已完成内容：

- 定义 `BaseModel`。
- `MockModel` 可用于无 API key 场景。
- `ModelManager` 支持 `mock/openai/qianwen/doubao`。
- `generate()` 有异常兜底。
- `stream_generate()` 有异常兜底。
- 支持 `health_check()`。
- 支持 `get_model_info()`。

## 待开发

### Step 1：模型调用配置中心

状态：待开发

目标：

- 统一模型名、endpoint、API key、超时、重试配置。

### Step 2：模型路由

状态：待开发

目标：

- 简单分类、结构化提取、总结、规划等不同任务可路由到不同模型。

### Step 3：超时、重试、熔断

状态：待开发

目标：

- 提升模型调用稳定性。

### Step 4：结构化输出支持

状态：待开发

目标：

- 为 Analyzer LLM 兜底、Planner 规划等场景提供 JSON 输出解析能力。

### Step 5：embedding 模型分离

状态：待开发

目标：

- RAG embedding 与 chat model 分离配置。

## 暂停开发时更新格式

```text
已完成：
- ...

当前未完成：
- ...

下一轮建议：
- 从 Step X 开始，目标是 ...
```
