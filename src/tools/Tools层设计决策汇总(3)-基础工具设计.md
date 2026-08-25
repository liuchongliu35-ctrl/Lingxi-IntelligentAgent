# Tools 层设计决策汇总（3）- 基础工具设计

> 文档状态：Tools 层 V1 正式化设计稿  
> 适用范围：阅读、编辑、终端、预览、删除、文档解析、普通辅助工具和 code_executor  

## 1. 基础工具总览

Tools V1 的基础工具按用户心智分为：

```text
阅读:
  list_files
  find_files
  file_info
  read_file
  read_file_chunk
  read_file_head
  read_file_tail
  document_parser

编辑:
  write_file
  patch_file
  copy_file
  move_file
  rename_file

终端:
  command_tool
  shell_command_tool

预览:
  dry_run / preview / confirmation preview

删除:
  delete_file

普通辅助:
  math_calculator
  time_query
  text_processor
  translator

高风险后置:
  code_executor
```

每个工具都必须遵守：

```text
ToolCallRequest 输入
ToolSpec 声明
ToolPolicy 校验
ToolResult 输出
logs/tools.log 记录
```

## 2. 路径规则

模型可见参数推荐使用 workspace-relative path。

允许：

```text
README.md
src/app.py
tests/test_tools.py
docs/spec.md
```

默认不允许：

```text
C:\Windows\...
/etc/passwd
..\..\outside.txt
绝对路径指向 workspace 外
```

路径解析流程：

```text
1. 接收模型传入 path。
2. 规范化分隔符。
3. 拼接 workspace_root。
4. resolve 得到绝对路径。
5. 校验 resolved_path 是否仍在 workspace_root 内。
6. 校验敏感路径和忽略目录。
7. 执行工具。
```

需要记录：

```text
path_original
path_resolved
workspace_relative_path
is_inside_workspace
is_sensitive
```

## 3. 敏感路径

默认敏感路径：

```text
.env
.env.*
*.pem
*.key
*.p12
id_rsa
id_dsa
credentials*
secrets*
.git/
node_modules/
__pycache__/
.venv/
venv/
```

策略：

```text
读取敏感文件:
  high risk 或 blocked，V1 默认要求确认。

写入敏感文件:
  high risk 或 blocked，默认确认或拒绝。

删除敏感文件:
  high risk，默认确认；workspace 外敏感文件 blocked。

大规模读取 node_modules / .git:
  blocked 或 file_too_large / directory_ignored。
```

## 4. list_files

用途：

```text
列出目录下文件和目录。
```

参数：

```json
{
  "path": ".",
  "recursive": false,
  "max_entries": 200,
  "include_hidden": false
}
```

V1 边界：

```text
默认不 recursive。
recursive=true 时仍限制 max_entries 和忽略目录。
不进入 .git / node_modules / .venv / __pycache__ 等默认忽略目录，除非后续安全策略明确允许。
只在 workspace_root 内。
```

返回 data：

```json
{
  "path": ".",
  "entries": [
    {
      "name": "src",
      "path": "src",
      "type": "directory",
      "size_bytes": null,
      "modified_at": "..."
    }
  ],
  "entry_count": 1,
  "truncated": false,
  "ignored_count": 0
}
```

错误：

```text
path_not_found
not_a_directory
workspace_out_of_scope
too_many_entries
permission_denied
```

## 5. find_files

用途：

```text
按文件名、glob-like pattern 或文本关键词查找文件。
```

参数：

```json
{
  "path": ".",
  "name_pattern": "*.py",
  "text_pattern": "ToolResult",
  "case_sensitive": false,
  "max_results": 200
}
```

V1 建议：

```text
优先支持文件名查找。
文本查找可以使用 Python 标准库实现。
后续如果可用再接 rg，但不能依赖 rg 一定存在。
默认跳过大文件和敏感目录。
```

返回 data：

```json
{
  "matches": [
    {
      "path": "src/tools/base.py",
      "type": "file",
      "line_number": 12,
      "line_preview": "class ToolResult:"
    }
  ],
  "match_count": 1,
  "truncated": false
}
```

## 6. file_info

用途：

```text
获取文件或目录元数据，不读取全文。
```

参数：

```json
{
  "path": "src/tools/base.py",
  "include_hash": false
}
```

返回 data：

```json
{
  "path": "src/tools/base.py",
  "type": "file",
  "exists": true,
  "size_bytes": 1234,
  "modified_at": "...",
  "encoding_guess": "utf-8",
  "line_count": 80,
  "hash": null,
  "is_sensitive": false
}
```

风险：

```text
low / medium。
敏感路径只返回有限 metadata，不读取内容。
```

## 7. read_file

用途：

```text
读取普通文本文件。
```

参数：

```json
{
  "path": "README.md",
  "encoding": "utf-8",
  "max_bytes": null,
  "observation_mode": "standard"
}
```

默认策略：

```text
small 文件:
  例如 <= 64 KiB，直接返回内容。

medium 文件:
  例如 > 64 KiB 且 <= 512 KiB，可以返回内容，但 Observation 默认只给 preview，不默认塞全量正文。

large 文件:
  例如 > 512 KiB 且 <= 8 MiB，返回 file_too_large。
  data 中提供 size、line_count、建议 read_file_chunk/head/tail。

hard limit:
  例如 > 8 MiB，只保留 metadata，必须分段读取。

二进制文件:
  返回 binary_file_not_supported 或仅返回 metadata。
```

上述阈值是默认建议，正式实现必须放入 `config/tools/defaults.json`，不能硬编码散落在工具内部。

返回 data：

```json
{
  "path": "README.md",
  "encoding": "utf-8",
  "size_bytes": 1024,
  "line_count": 40,
  "content": "...",
  "content_preview": "...",
  "content_truncated": false,
  "content_hash": "...",
  "is_sensitive": false
}
```

Observation：

```text
minimal:
  path / size / line_count / hash

standard:
  path / size / line_count / content_preview

full:
  content，但仍受 max_observation_chars 和敏感策略限制
```

## 8. read_file_chunk / head / tail

这些工具用于控制上下文成本。

`read_file_chunk` 参数：

```json
{
  "path": "logs/app.log",
  "start_line": 100,
  "line_count": 80
}
```

也可以后续支持 byte offset：

```json
{
  "path": "large.txt",
  "offset": 4096,
  "length": 2048
}
```

V1 推荐优先 line range，因为更适合代码和日志。

`read_file_head` 参数：

```json
{
  "path": "logs/app.log",
  "line_count": 100
}
```

`read_file_tail` 参数：

```json
{
  "path": "logs/app.log",
  "line_count": 100
}
```

返回 data：

```json
{
  "path": "logs/app.log",
  "start_line": 100,
  "end_line": 179,
  "line_count": 80,
  "content": "...",
  "has_more_before": true,
  "has_more_after": true
}
```

## 9. write_file

用途：

```text
整体写入文件。
```

不负责局部编辑。局部编辑使用 `patch_file`。

参数：

```json
{
  "path": "src/app.py",
  "content": "...",
  "write_mode": "create",
  "encoding": "utf-8"
}
```

`write_mode`：

```text
create:
  只允许新建。如果文件已存在，返回 file_already_exists。

overwrite:
  覆盖已有文件。如果文件不存在，返回 file_not_found 或按 create_or_overwrite 处理。

append:
  追加到文件末尾。如果文件不存在，V1 可返回 file_not_found，或按配置允许 create。

create_or_overwrite:
  可选兼容模式，风险高于 create。
```

风险：

```text
新建普通文件:
  medium，allow_write_workspace=true 可自动执行。

覆盖:
  high，未授权时需要确认。

append:
  medium/high，取决于文件类型和目标路径。

敏感路径:
  high 或 blocked。
```

dry_run preview：

```text
目标路径
是否存在
write_mode
旧文件大小
新内容大小
content_hash
diff_preview 可选
requires_confirmation
```

返回 data：

```json
{
  "path": "src/app.py",
  "write_mode": "overwrite",
  "created": false,
  "overwritten": true,
  "bytes_written": 2048,
  "old_size_bytes": 1800,
  "new_size_bytes": 2048,
  "content_hash": "...",
  "diff_preview": "...",
  "backup_ref": null
}
```

## 10. patch_file

用途：

```text
对已有文件做局部修改。
```

正式参数：

```json
{
  "path": "src/app.py",
  "patches": [
    {
      "operation": "replace",
      "old_text": "a",
      "new_text": "b",
      "occurrence": 1
    }
  ]
}
```

支持操作：

```text
replace
insert_before
insert_after
delete_block
```

### 10.1 精准定位策略

局部编辑必须精准定位，不允许“差不多匹配就改”。

`insert_before` / `insert_after` 就是局部添加，不要求覆盖已有内容；`replace` 才是覆盖修改。

推荐 patch block 字段：

```json
{
  "operation": "replace",
  "old_text": "exact text",
  "new_text": "replacement",
  "occurrence": 1,
  "line_start": 10,
  "line_end": 12,
  "anchor_before": "def main():",
  "anchor_after": "if __name__ == '__main__':"
}
```

V1 定位优先级：

```text
1. line_start + line_end + old_text 校验
2. old_text 唯一匹配
3. old_text + occurrence
4. anchor_before / anchor_after 限定范围后匹配
```

必须拒绝：

```text
old_text 找不到
old_text 匹配多处但没有 occurrence 或 anchor
line range 内容与 old_text 不一致
anchor 找不到
patch 后文件校验失败
```

错误码：

```text
patch_anchor_not_found
patch_old_text_not_found
patch_ambiguous_match
patch_line_mismatch
patch_conflict
```

### 10.2 patch dry_run

`patch_file` 默认支持 dry_run。

dry_run 返回：

```text
每个 patch 是否能命中
命中行号
影响行数
diff_preview
是否会修改敏感路径
是否需要确认
```

只有 dry_run 校验和真实执行使用同一套定位算法，才允许用户确认后执行。

### 10.3 patch 返回 data

```json
{
  "path": "src/app.py",
  "patch_count": 2,
  "applied_count": 2,
  "changed_lines": 5,
  "diff_preview": "...",
  "content_hash_before": "...",
  "content_hash_after": "...",
  "patch_results": [
    {
      "operation": "replace",
      "success": true,
      "line_start": 10,
      "line_end": 12
    }
  ]
}
```

V1 暂不做：

```text
AST patch
跨文件事务
自动冲突合并
自动格式化保证
```

## 11. copy_file

参数：

```json
{
  "source_path": "a.txt",
  "target_path": "b.txt",
  "overwrite": false
}
```

策略：

```text
source 和 target 都必须在 workspace 内。
target 已存在且 overwrite=false 返回 file_already_exists。
overwrite=true 按 high 或需要确认处理。
敏感文件 copy 默认 high。
```

返回 data：

```json
{
  "source_path": "a.txt",
  "target_path": "b.txt",
  "bytes_copied": 1234,
  "overwritten": false
}
```

## 12. move_file / rename_file

`move_file` 参数：

```json
{
  "source_path": "old/a.txt",
  "target_path": "new/a.txt",
  "overwrite": false
}
```

`rename_file` 参数：

```json
{
  "source_path": "old_name.txt",
  "new_name": "new_name.txt"
}
```

策略：

```text
默认 high risk。
需要确认，除非会话权限明确允许。
不允许移动到 workspace 外。
不允许覆盖敏感文件。
```

## 13. delete_file

用途：

```text
删除 workspace 内明确文件。
```

参数：

```json
{
  "path": "tmp.txt"
}
```

或：

```json
{
  "file_paths": ["a.txt", "b.txt"]
}
```

V1 支持：

```text
单文件删除
明确文件列表删除
```

V1 不支持：

```text
目录删除
递归删除
glob 删除
工作区外删除
```

如果模型传入：

```text
*.log
**/*.tmp
recursive=true
directory path
```

返回：

```text
glob_delete_not_allowed
delete_directory_not_allowed
```

dry_run preview：

```text
目标路径列表
每个文件是否存在
文件大小
修改时间
是否敏感
总文件数
总大小
requires_confirmation
```

返回 data：

```json
{
  "deleted_files": [
    {
      "path": "tmp.txt",
      "size_bytes": 123,
      "existed": true
    }
  ],
  "deleted_count": 1,
  "skipped": [],
  "total_size_bytes": 123
}
```

命令行删除必须被 command_tool 拦截，引导模型或 Checker 改用 `delete_file`。

## 14. command_tool

普通 `command_tool` 使用 argv / shell=False。

参数：

```json
{
  "program": "python",
  "args": ["-m", "pytest", "tests/test_tools.py"],
  "cwd": ".",
  "purpose": "运行工具层测试",
  "timeout_seconds": 30,
  "network_required": false,
  "writes_files": false,
  "target_paths": []
}
```

兼容输入：

```json
{
  "command": "python -m pytest tests/test_tools.py"
}
```

兼容输入内部应解析成 argv。如果发现复杂 shell 语法，应返回：

```text
shell_required
```

普通通道禁止：

```text
|
>
>>
<
&&
||
;
命令替换
环境变量展开作为控制语义
```

风险：

```text
默认 medium/high。
低风险命令在 allow_command=true 时可自动执行。
高风险命令需要确认。
blocked 命令直接拒绝。
```

输出 data：

```json
{
  "program": "python",
  "args": ["-m", "pytest"],
  "cwd": ".",
  "exit_code": 0,
  "stdout": "...",
  "stderr": "",
  "stdout_preview": "...",
  "stderr_preview": "",
  "stdout_truncated": false,
  "stderr_truncated": false,
  "stdout_bytes": 100,
  "stderr_bytes": 0,
  "duration_ms": 1200,
  "timed_out": false
}
```

## 15. shell_command_tool

`shell_command_tool` 用于复杂 shell 语法。

参数：

```json
{
  "command": "python -m pytest tests | tee result.txt",
  "shell": "powershell",
  "cwd": ".",
  "purpose": "运行测试并保存输出",
  "timeout_seconds": 30
}
```

V1 实现最小可用：

```text
允许管道、重定向、&&、; 等复杂语法。
默认 high risk。
必须确认。
必须限制 cwd 在 workspace 内。
必须限制 timeout。
必须截断输出。
必须记录日志。
必须执行 blocked 规则。
```

V1 不做：

```text
管理员提权
交互式 TTY
长期后台服务托管
任意工作区外破坏性操作
```

危险命令 blocked 示例：

```text
format
shutdown
reboot
reg delete
删除 workspace 外路径
递归删除目录
明显读取或泄露密钥
绕过权限策略的命令
```

## 16. 删除命令拦截

普通删除必须走 `delete_file`。

`command_tool` 和 `shell_command_tool` 遇到：

```text
rm
del
erase
rmdir
rd
Remove-Item
```

默认返回：

```text
command_delete_not_allowed
```

并在 message 中提示：

```text
删除文件请使用 delete_file 工具。
```

如果未来确实要允许脚本中包含删除，应进入后续安全与权限层设计，不在 Tools V1 默认放行。

## 17. document_parser

用途：

```text
解析文档格式，返回结构化 DocumentParseData。
```

支持格式：

```text
txt
md
json
csv
pdf
docx
xlsx
```

职责边界：

```text
document_parser:
  解析文档内容和结构。

read_file:
  读取普通文本文件。

RAG:
  负责文档入库、embedding、chunk 索引和问答。

Models:
  负责总结、抽取、改写等语义处理。
```

参数：

```json
{
  "path": "docs/report.pdf",
  "max_pages": 20,
  "max_chars": 20000,
  "include_metadata": true
}
```

返回 data：

```json
{
  "path": "docs/report.pdf",
  "file_type": "pdf",
  "title": null,
  "page_count": 12,
  "sheet_count": null,
  "text": "...",
  "text_preview": "...",
  "text_truncated": false,
  "tables": [],
  "metadata": {},
  "parser": "pypdf"
}
```

解析失败返回：

```text
document_parse_failed
unsupported_document_type
document_too_large
document_encrypted
```

DocumentParser 不负责总结文档。

## 18. math_calculator

用途：

```text
表达式计算和简单统计。
```

参数：

```json
{
  "expression": "2 + 3 * 4",
  "data": [1, 2, 3],
  "operation": "mean"
}
```

安全：

```text
不得使用 eval 执行任意 Python。
表达式解析必须受限。
```

返回 data：

```json
{
  "result": 14,
  "operation": "expression",
  "normalized_expression": "2 + 3 * 4"
}
```

## 19. time_query

用途：

```text
当前时间、日期转换、简单时间查询。
```

参数：

```json
{
  "operation": "current",
  "timezone": "Asia/Shanghai"
}
```

返回 data：

```json
{
  "operation": "current",
  "timezone": "Asia/Shanghai",
  "iso": "2026-08-13T..."
}
```

## 20. text_processor

用途：

```text
规则型文本处理，例如提取关键词、格式化、简单统计。
```

V1 不默认调用模型。

参数：

```json
{
  "text": "...",
  "operation": "keywords"
}
```

如果未来要做模型摘要/改写，应通过 Models 层并标注 provider、usage、cost。

## 21. translator

当前 translator 是 mock placeholder。

V1 可选策略：

```text
保留为兼容工具，但明确标记 mock。
或升级为 Models-backed translator。
```

建议：

```text
V1 先不把 translator 作为关键验收主路径。
如果保留，必须在 ToolSpec.metadata 中标记 implemented=false 或 mock=true。
```

## 22. code_executor

`code_executor` 属于高风险工具。

V1 策略：

```text
默认 disabled。
默认 high risk。
需要确认。
不作为优先打磨工具。
```

原因：

```text
Agent 已经可以通过 command_tool 运行测试和脚本。
code_executor 沙箱复杂度高。
V1 不做完整虚拟机沙箱。
```

如果调用：

```text
未启用 -> tool_disabled
未确认 -> confirmation_required
超时 -> timeout
```

## 23. 基础工具测试重点

测试必须覆盖：

```text
路径越界
敏感路径
大文件限制
读文件分段
写入模式
patch 精准定位
patch 模糊匹配拒绝
删除目录拒绝
glob 删除拒绝
命令 argv 解析
shell 元字符拒绝或转 shell_command_tool
危险命令 blocked
命令 timeout
输出截断
DocumentParser 成功/失败
translator mock 标识
code_executor disabled
dry_run 不产生副作用
```
