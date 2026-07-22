# Tools 层开发步骤与进度

本文档用于跨 Session 记录 Tools 层开发进度。

## 当前定位

Tools 层提供 Agent 可调用的外部能力，包括文档读取、文本处理、计算、翻译、搜索、代码执行、文件写入等。所有工具必须返回统一 `ToolResult`。

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

### Step 0：基础工具集合

状态：已完成基础版

主要文件：

```text
src/tools/base.py
src/tools/tool_manager.py
src/tools/document_parser.py
src/tools/text_processor.py
src/tools/math_calculator.py
src/tools/translator.py
src/tools/time_query.py
src/tools/search_tool.py
src/tools/code_executor.py
src/tools/file_writer.py
```

已完成内容：

- 定义 `ToolResult`。
- `ToolManager.run_tool()` 统一返回 `ToolResult`。
- 已注册基础工具：
  - `document_parser`
  - `text_processor`
  - `math_calculator`
  - `translator`
  - `time_query`
  - `search_tool`
  - `code_executor`
  - `file_writer`
- `CodeExecutor` 默认关闭。
- `FileWriter` 限制写入工作区。
- `SearchTool` 缺少 API key 时返回失败。

## 待开发

### Step 1：统一工具 schema

状态：待开发

目标：

- 每个工具声明名称、描述、参数 schema、返回 schema、风险等级。

### Step 2：正式 ToolRegistry

状态：待开发

目标：

- 替换硬编码工具字典。
- 支持工具能力查询。
- 支持 Analyzer/Planner 判断工具是否可用。

### Step 3：文件管理工具

状态：待开发

目标：

- 实现：
  - `list_files`
  - `find_files`
  - `copy_file`
  - `move_file`
  - `rename_file`
  - `delete_file`

验收标准：

- 所有文件操作限制在工作区内。
- 删除文件必须配合确认策略。

### Step 4：文档解析增强

状态：待开发

目标：

- `txt/md/pdf/docx/xlsx/csv/json` 成为 V1 一等支持格式。

### Step 5：工具测试集

状态：待开发

目标：

- 为每个工具补充单元测试和失败场景测试。

## 暂停开发时更新格式

```text
已完成：
- ...

当前未完成：
- ...

下一轮建议：
- 从 Step X 开始，目标是 ...
```
