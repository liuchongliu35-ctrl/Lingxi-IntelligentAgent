# Tools 层开发步骤与进度（2）- 文件与命令工具

> 覆盖步骤：Step 10-23  
> 当前状态：Step 10-23 已完成，本分卷已完成
> 前置分卷：`Tools层开发步骤与进度(1)-协议运行时.md`  
> 上位设计：`Tools层设计决策汇总(3)-基础工具设计.md`

本分卷实现七个核心能力中的阅读、编辑、终端、预览和删除，并收束现有辅助工具。所有工具必须通过 Step 0-9 建立的正式协议执行。

---

## Step 10：路径解析、敏感路径与文件工具公共底座

**状态：已完成**

### 目标

为全部文件工具建立唯一的 workspace 路径解析和敏感资源判断逻辑，避免每个工具自行拼接路径造成越界差异。

### 涉及文件 / 建议新增

```text
src/tools/file_tools/__init__.py
src/tools/file_tools/path_resolver.py
src/tools/file_tools/common.py
tests/test_tool_path_resolver.py
```

也可使用同等清晰的文件布局，但不得复制多套路径校验。

### 执行顺序

```text
接收 workspace-relative path
  -> 拒绝空值/NUL/非法类型
  -> 规范化分隔符
  -> 与可信 workspace_root 拼接
  -> resolve
  -> 校验 resolved 仍在 workspace_root
  -> 判断目标是否存在、类型、symlink
  -> 判断敏感路径/忽略目录
  -> 返回 ResolvedPath
```

`ResolvedPath` 至少提供：

```text
path_original
path_resolved
workspace_relative_path
exists
resource_type
is_inside_workspace
is_sensitive
is_ignored
is_symlink
```

### Windows 细节

```text
路径大小写比较使用平台安全方式。
处理盘符、UNC、反斜杠和正斜杠。
拒绝通过 ..、junction 或 symlink 逃逸 workspace。
不得用简单字符串 startswith 判断目录归属。
```

### 默认敏感/忽略项

```text
.env / .env.*
*.pem / *.key / *.p12
id_rsa / id_dsa
credentials* / secrets*
.git/
node_modules/
__pycache__/
.venv/
venv/
```

敏感不等于一律不存在：

```text
读取:
  high 或 blocked，由 ToolPolicy 裁决。

写/删:
  high 或 blocked。

大量遍历忽略目录:
  默认跳过。
```

### 明确不做

```text
不允许 workspace 外授权。
不实现管理员权限。
不跟随逃逸 workspace 的 symlink。
不在本 Step 读写文件内容。
```

### 测试与验收

```text
普通相对路径。
包含 .. 但最终仍在 workspace。
逃逸 workspace。
绝对路径在内/在外。
Windows 分隔符。
symlink/junction 逃逸。
敏感路径和忽略目录识别。
```

```powershell
python -m pytest tests/test_tool_path_resolver.py -q
```

### Step 10 完成记录（2026-08-14）

#### 修改文件

```text
新增:
  src/tools/file_tools/__init__.py
  src/tools/file_tools/common.py
  src/tools/file_tools/path_resolver.py
  tests/test_tool_path_resolver.py

更新:
  src/tools/path_policy.py
  src/tools/policy.py
  src/tools/tool_manager.py
  src/tools/__init__.py
  src/tools/Tools层开发步骤与进度(2)-文件与命令工具.md
```

#### 实现结果

```text
PathResolver:
  建立文件工具共享的 workspace 路径解析底座。
  输入支持 str/Path，拒绝空路径、NUL 和非法类型。
  统一规范化 Windows 反斜杠和 POSIX 斜杠展示。
  使用可信 workspace_root 拼接相对路径，再 resolve(strict=False) 得到最终绝对路径。
  使用 os.path.commonpath + normcase 判断归属，不使用简单 startswith。
  支持绝对路径在 workspace 内通过，拒绝最终落到 workspace 外的相对/绝对路径。
  识别 exists、resource_type、workspace_relative_path、is_symlink。
  symlink/junction 若解析后逃逸 workspace，会返回 workspace_out_of_scope。

ResolvedPath:
  输出 path_original、path_resolved、workspace_relative_path、exists、resource_type、
  is_inside_workspace、is_sensitive、is_ignored、is_symlink，并额外提供 valid、
  is_blocked、error_code、reason 方便后续工具直接消费。

敏感与忽略规则:
  默认识别 .env/.env.*、*.pem、*.key、*.p12、id_rsa/id_dsa/id_ed25519、
  credentials*、secrets*、.git 等敏感路径。
  默认识别 .git、node_modules、__pycache__、.venv、venv 等忽略目录。
  支持 config/tools/policies.json 中的 sensitive_paths、blocked_paths、
  ignored_directories 注入。
  敏感/blocked 路径在公共底座中标记为 blocked；ignored 只标记不阻断，
  供后续 list/find 等遍历工具默认跳过。

PathPolicy:
  改为复用 PathResolver，保留原有 PathPolicyResult 字段和 affected_resource 行为。
  新增 ignored、is_symlink、resource_type 字段，供 ToolPolicy 和后续文件工具复用。
  ToolManager 会把 Step 9 的 ignored_directories 配置传入 ToolPolicy/PathPolicy。
```

#### 测试命令与结果

```powershell
python -B -m unittest tests.test_tool_path_resolver tests.test_tool_policy_v1 tests.test_tool_config_v1
# Ran 28 tests - OK

python -B -m unittest tests.test_tool_path_resolver tests.test_command_tool_v1 tests.test_tool_preview_v1 tests.test_tool_output_control tests.test_tool_manager_v1
# Ran 33 tests - OK

python -B -m unittest discover -s tests -p 'test_*.py'
# Ran 501 tests - OK (skipped=1)

python -B -m compileall -q src/tools tests/test_tool_path_resolver.py
# 通过
```

#### 本 Step 边界与遗留问题

```text
本 Step 不读写文件内容，不实现 list_files/read_file/write_file/delete_file 等具体工具。
ignored_directories 只在解析结果中标记，具体递归遍历如何跳过留给 Step 11/12。
敏感路径当前按公共底座和 PathPolicy 阻断；后续若需要“读敏感文件需 high + confirmation”
的更细粒度行为，可在具体 read/write/delete 工具和 ToolSpec 风险中扩展，但不能绕过
workspace_root 边界。
```

---

## Step 11：list_files 与 file_info

**状态：已完成**

### 目标

先实现低副作用的目录和元数据读取工具，用于后续模型了解工作区结构。

### 工具与 schema

`list_files`：

```text
path="."
recursive=false
max_entries=200
include_hidden=false
```

`file_info`：

```text
path
include_hash=false
```

### 实施细节

1. 注册独立 ToolSpec 和 handler。
2. `list_files` 默认只列一层；递归时应用最大条目数、忽略目录和深度保护。
3. 输出排序必须稳定，建议目录和文件按名称排序，避免测试与模型上下文抖动。
4. `include_hidden=false` 时按平台兼容规则过滤隐藏项。
5. `file_info` 不读取完整内容；行数和编码猜测只能在阈值内计算。
6. `include_hash=true` 时设置大小上限，超限可返回 metadata 而不计算 hash，并明确标记。
7. 两个工具只返回 workspace-relative path，绝对 resolved path 不默认进入用户事件。

### 错误码

```text
path_not_found
not_a_directory
workspace_out_of_scope
too_many_entries
permission_denied
directory_ignored
```

### 明确不做

```text
不无限递归。
不默认遍历 .git/node_modules/venv。
不读取文件正文。
不为目录生成整体 hash。
```

### 测试与验收

```text
空目录、普通目录、隐藏文件。
recursive=false/true。
max_entries 截断。
忽略目录。
file/directory metadata。
hash 开关。
路径越界。
```

```powershell
python -m pytest tests/test_file_listing_tools.py -q
```

### Step 11 完成记录（2026-08-14）

#### 修改文件

```text
新增:
  src/tools/file_tools/listing.py
  tests/test_file_listing_tools.py

更新:
  src/tools/errors.py
  src/tools/runtime.py
  src/tools/policy.py
  src/tools/registry.py
  src/tools/tool_manager.py
  src/tools/file_tools/__init__.py
  src/tools/__init__.py
  tests/test_tools_current_baseline.py
  src/tools/Tools层开发步骤与进度(2)-文件与命令工具.md
```

#### 实现结果

```text
list_files:
  注册为正式 builtin/read 工具，经 ToolCallRequest -> ToolRegistry -> ToolPolicy ->
  ToolRuntime -> ToolResult -> ToolLogger 管线执行。
  参数支持 path="."、recursive=false、max_entries=200、include_hidden=false。
  复用 PathResolver，只接受 workspace 内路径；目标不存在返回 file_not_found；
  目标不是目录返回 not_a_directory；目标是 ignored 目录返回 directory_ignored。
  输出 entries 使用稳定排序，目录优先、再按名称排序。
  默认过滤隐藏项，默认跳过 node_modules、__pycache__、.venv、venv 等 ignored 目录。
  recursive=true 时受 max_entries 和硬上限保护，达到上限返回 success=true 且 truncated=true。
  不读取文件正文，不为目录生成整体 hash，不返回默认绝对 resolved path。

file_info:
  注册为正式 builtin/read 工具，参数支持 path、include_hash=false。
  返回文件/目录有限 metadata：path、type、exists、size_bytes、modified_at、
  encoding_guess、line_count、hash/hash_skipped_reason、is_sensitive、is_ignored、is_symlink。
  对普通文本文件只在阈值内采样判断编码和行数；二进制文件返回 binary。
  include_hash=true 时只在 8 MiB 内计算 sha256，超过上限标记 file_too_large。
  对敏感路径允许有限 metadata 通过，但不读取正文、不计算 hash。

公共管线:
  Runtime 在 handler 显式声明时注入可信 workspace_root，工具不从模型参数猜工作区。
  ToolPolicy 支持 allow_sensitive_metadata 的工具级 metadata 例外，仅用于 metadata-only 工具；
  workspace 外路径、blocked_paths、权限和全局策略仍不能绕过。
  新增错误码 not_a_directory、too_many_entries、directory_ignored。
```

#### 测试命令与结果

```powershell
python -B -m unittest tests.test_file_listing_tools tests.test_tool_path_resolver tests.test_tools_current_baseline tests.test_tool_registry_v1 tests.test_tool_policy_v1
# Ran 51 tests - OK

python -B -m unittest tests.test_file_listing_tools tests.test_tool_path_resolver tests.test_tool_config_v1 tests.test_tool_logging_v1 tests.test_tool_manager_v1 tests.test_tool_registry_v1
# Ran 55 tests - OK

python -B -m unittest tests.test_react_executor_tool_action tests.test_react_executor_command_action tests.test_react_executor_safety tests.test_react_executor_confirmation
# Ran 34 tests - OK

python -B -m unittest discover -s tests -p 'test_*.py'
# Ran 509 tests - OK (skipped=1)

python -B -m compileall -q src/tools tests/test_file_listing_tools.py tests/test_tool_path_resolver.py
# 通过
```

#### 本 Step 边界与遗留问题

```text
本 Step 不实现 read_file/read_file_chunk/head/tail，不读取完整文件正文。
file_info 的行数与编码只是阈值内采样 metadata，不承诺完整文本解析。
list_files 只列 workspace-relative path 和有限 metadata；绝对 path_resolved 保留在内部解析结果中。
ignored 目录默认跳过；后续 find_files 将复用同一 ignored 策略。
```

---

## Step 12：find_files

**状态：已完成**

### 目标

支持按文件名和文本内容在 workspace 内受控搜索，不依赖 `rg` 必然存在。

### 参数

```text
path="."
name_pattern
text_pattern
case_sensitive=false
max_results=200
```

`name_pattern` 和 `text_pattern` 至少一个有值。

### 实施细节

1. V1 使用 Python 标准库作为稳定基础；若检测到 `rg` 可作为内部优化，但输出必须归一化且测试不能依赖它。
2. 文件名模式采用受控 glob-like 匹配，只用于搜索，不用于删除。
3. 文本搜索跳过二进制文件、超大文件、敏感和忽略目录。
4. 返回 `path/type/line_number/line_preview`，预览做长度限制和敏感脱除。
5. 达到 `max_results` 后停止并标记 `truncated=true`。
6. 读取错误按文件跳过或整体失败要有明确策略；建议记录 `skipped_count` 和有限错误摘要。

### 明确不做

```text
不实现正则表达式的全部高级特性，除非 schema 明确提供 regex 开关。
不跨 workspace 搜索。
不把搜索模式复用于 delete_file。
不索引或缓存整个项目。
```

### 测试与验收

```text
按名称、按文本、组合查找。
大小写策略。
二进制/大文件跳过。
忽略目录。
结果上限与稳定顺序。
```

```powershell
python -m pytest tests/test_find_files_tool.py -q
```

### Step 12 完成记录（2026-08-14）

#### 修改文件

```text
新增:
  src/tools/file_tools/find.py
  tests/test_find_files_tool.py

更新:
  src/tools/registry.py
  src/tools/tool_manager.py
  src/tools/file_tools/__init__.py
  src/tools/__init__.py
  tests/test_tools_current_baseline.py
  src/tools/Tools层开发步骤与进度(2)-文件与命令工具.md
```

#### 实现结果

```text
find_files:
  注册为正式 builtin/read 工具，经 ToolCallRequest -> ToolRegistry -> ToolPolicy ->
  ToolRuntime -> ToolResult -> ToolLogger 管线执行。
  参数支持 path="."、name_pattern、text_pattern、case_sensitive=false、max_results=200。
  name_pattern 与 text_pattern 至少一个有值；Registry 通过 required_any_of 进行前置校验。
  搜索根目录复用 PathResolver，只允许 workspace 内目录；缺失、非目录、ignored 根目录、
  workspace 越界均返回结构化 ToolResult 失败。
  文件名匹配采用受控 glob-like fnmatch，不引入删除语义，也不把搜索模式复用于变更工具。
  文本搜索使用 Python 标准库实现，不依赖 rg；内部可后续优化，但输出协议固定。
  遍历默认跳过 ignored 目录、敏感路径、symlink、二进制文件和超过 8 MiB 的文本扫描目标。
  输出只返回 workspace-relative path、type、line_number、line_preview，不暴露 resolved 绝对路径。
  line_preview 做长度限制；敏感文件不读取正文，不进入匹配结果，也不进入日志/结果明文。
  达到 max_results 后停止搜索并标记 truncated=true，同时返回 skipped_count 与 max_results。
  遍历顺序稳定，便于测试和模型上下文复现。
```

#### 测试命令与结果

```powershell
python -B -m unittest tests.test_find_files_tool
# Ran 7 tests - OK

python -B -m unittest tests.test_find_files_tool tests.test_file_listing_tools tests.test_tool_path_resolver tests.test_tools_current_baseline tests.test_tool_registry_v1 tests.test_tool_policy_v1
# Ran 58 tests - OK

python -B -m unittest tests.test_tool_manager_v1 tests.test_tool_config_v1 tests.test_tool_logging_v1
# Ran 21 tests - OK

python -B -m unittest tests.test_react_executor_tool_action tests.test_react_executor_command_action tests.test_react_executor_safety tests.test_react_executor_confirmation
# Ran 34 tests - OK

python -B -m unittest discover -s tests -p 'test_*.py'
# Ran 516 tests - OK (skipped=1)

python -B -m compileall -q src/tools tests/test_find_files_tool.py
# 通过
```

#### 本 Step 边界与遗留问题

```text
本 Step 不实现 read_file/read_file_chunk/head/tail，也不读取完整大文件。
本 Step 不实现 regex 开关、索引、缓存或跨 workspace 搜索。
文本匹配当前按 UTF-8 严格解码；无法解码、二进制和超大文件按跳过计数处理，不伪造命中。
隐藏目录未作为独立参数暴露；默认策略重点跳过 ignored/sensitive 路径，后续如需 include_hidden
应单独扩展 schema，而不是复用 list_files 的参数语义。
```

---

## Step 13：read_file

**状态：已完成**

### 目标

实现普通文本文件完整读取，并在文件过大或类型不支持时给出可操作的结构化失败。

### 参数与返回

参数：

```text
path
encoding="utf-8"
max_bytes=null
observation_mode 可作为请求建议
```

返回 `FileReadData`：

```text
path
encoding
size_bytes
line_count
content
content_preview
content_truncated
content_hash
is_sensitive
```

### 实施细节

1. 先取 metadata，再决定是否读取。
2. 按配置阈值分级读取：

```text
small <= 64 KiB:
  read_file 可直接返回 content。

medium <= 512 KiB:
  read_file 可返回 content，但 Observation 默认只放 preview。

large <= 8 MiB:
  返回 file_too_large + metadata，推荐 read_file_chunk/head/tail。

hard limit > 8 MiB:
  只返回 metadata 和分段读取建议。
```

3. 阈值必须来自 `config/tools/defaults.json`，上述数值只是默认建议。
4. 检测二进制内容，返回 `binary_file_not_supported`；不使用错误编码强行解码。
5. 编码失败可尝试有限、安全的候选编码，但必须返回实际使用编码；不得静默丢字符。
6. 敏感文件在 ToolPolicy 未确认前不读取。
7. `content` 进入 ToolResult.data，但是否进入 Observation 由 ReActExecutor 决定。

### 明确不做

```text
不解析 PDF/docx/xlsx。
不自动摘要。
不自动分块多次读取。
不把全文写入 tools.log。
```

### 测试与验收

```text
UTF-8/空文件/含换行文件。
大文件。
二进制。
编码失败。
敏感文件确认。
workspace 越界。
```

```powershell
python -m pytest tests/test_read_file_tool.py -q
```

### Step 13 完成记录（2026-08-14）

#### 修改文件

```text
新增:
  src/tools/file_tools/reading.py
  tests/test_read_file_tool.py

更新:
  config/tools/defaults.json
  src/tools/config.py
  src/tools/errors.py
  src/tools/policy.py
  src/tools/runtime.py
  src/tools/registry.py
  src/tools/tool_manager.py
  src/tools/file_tools/__init__.py
  src/tools/__init__.py
  tests/test_tool_config_v1.py
  tests/test_tool_policy_v1.py
  tests/test_tools_current_baseline.py
  src/tools/Tools层开发步骤与进度(2)-文件与命令工具.md
```

#### 实现结果

```text
read_file:
  注册为正式 builtin/read 工具，经 ToolCallRequest -> ToolRegistry -> ToolPolicy ->
  ToolRuntime -> ToolResult -> ToolLogger 管线执行。
  参数支持 path、encoding="utf-8"、max_bytes=null、observation_mode 请求建议字段。
  复用 PathResolver，只读取 workspace 内普通文件；缺失、目录、workspace 越界、symlink、
  敏感未确认路径均返回结构化失败。
  输出为 FileReadData 兼容结构：path、encoding、size_bytes、line_count、content、
  content_preview、content_truncated、content_hash、is_sensitive。
  对 UTF-8/空文件/普通换行文本完整读取，计算 sha256，并只返回 workspace-relative path。
  max_bytes 只限制返回 content，不改变完整文件 content_hash；content_truncated 明确标记。
  编码失败时有限尝试 utf-8/utf-8-sig/gb18030/cp1252，返回实际使用 encoding；
  不使用 errors=replace 静默丢字符。
  二进制文件返回 binary_file_not_supported，不强行解码。
  超过 medium 阈值返回 file_too_large + metadata + read_file_chunk/head/tail 建议；
  超过 hard 阈值不扫描全文行数，只返回有限 metadata 和后续建议。
  tools.log 只记录摘要/hash/长度，不记录 read_file 返回的完整 content。
```

#### 配置与策略

```text
config/tools/defaults.json:
  新增 read_file_small_bytes=65536
  新增 read_file_medium_bytes=524288
  新增 read_file_hard_bytes=8388608
  新增 read_file_preview_chars=4000

ToolsRuntimeConfig:
  正式承载 read_file 阈值并校验 small <= medium <= hard。

ToolPolicy:
  默认敏感路径仍是硬阻断。
  仅当 ToolSpec metadata 声明 allow_sensitive_read_with_confirmation=true 时，
  敏感读提升为 high confirmation；确认必须绑定 confirmation_id + preview_hash。
  workspace 外、blocked_paths、权限关闭和 preview 冲突仍不能通过确认绕过。
```

#### 测试命令与结果

```powershell
python -B -m unittest tests.test_read_file_tool
# Ran 7 tests - OK

python -B -m unittest tests.test_read_file_tool tests.test_file_listing_tools tests.test_find_files_tool tests.test_tool_path_resolver tests.test_tools_current_baseline tests.test_tool_registry_v1 tests.test_tool_policy_v1
# Ran 66 tests - OK

python -B -m unittest tests.test_tool_manager_v1 tests.test_tool_config_v1 tests.test_tool_logging_v1 tests.test_tool_errors_v1 tests.test_tool_result_v1 tests.test_tool_spec_v1
# Ran 43 tests - OK

python -B -m unittest tests.test_react_executor_tool_action tests.test_react_executor_command_action tests.test_react_executor_safety tests.test_react_executor_confirmation
# Ran 34 tests - OK

python -B -m unittest discover -s tests -p 'test_*.py'
# Ran 524 tests - OK (skipped=1)

python -B -m compileall -q src/tools tests/test_read_file_tool.py
# 通过
```

#### 本 Step 边界与遗留问题

```text
本 Step 不实现 read_file_chunk/read_file_head/read_file_tail；大文件只给出可操作建议。
本 Step 不解析 PDF/docx/xlsx，不自动摘要，不调用 Models，不做 RAG 入库。
read_file 对 large/hard 文件不返回部分正文；分段读取留给 Step 14。
敏感文件确认后可读仅限 read_file 的显式 ToolSpec metadata 分支，不影响 list/find/file_info。
content 进入 ToolResult.data，但 Observation 最终放多少仍由 ReActExecutor/OutputController 决定。
```

---

## Step 14：read_file_chunk、read_file_head 与 read_file_tail

**状态：已完成**

### 目标

为代码、日志和大文本提供明确的局部读取能力，减少不必要的上下文占用。

### 参数

```text
read_file_chunk:
  path
  start_line
  line_count

read_file_head/read_file_tail:
  path
  line_count
```

### 实施细节

1. V1 以行范围为正式协议，不同时引入 byte offset 造成两套边界。
2. 行号语义统一为 1-based，并在 schema、返回值和测试中固定。
3. 返回：

```text
path
start_line
end_line
line_count
content
has_more_before
has_more_after
```

4. `line_count` 设置硬上限，防止模型请求极大范围绕过 read_file 限制。
5. 对超大文件避免 `readlines()` 一次性载入全部；tail 可使用受控算法。
6. 三个工具复用 read_file 的编码、敏感和二进制策略。

### 明确不做

```text
不让一个调用返回无限行。
不自动连续读取下一块。
不在 Tools 内判断哪一块最相关。
```

### 测试与验收

```text
首块、中间块、末块、越界起始行。
head/tail 少于请求行数。
1-based 行号。
大文件内存边界。
```

```powershell
python -m pytest tests/test_read_file_ranges.py -q
```

### Step 14 完成记录（2026-08-15）

#### 修改文件

```text
新增:
  tests/test_read_file_ranges.py

更新:
  config/tools/defaults.json
  src/tools/config.py
  src/tools/errors.py
  src/tools/policy.py
  src/tools/runtime.py
  src/tools/registry.py
  src/tools/tool_manager.py
  src/tools/file_tools/reading.py
  src/tools/file_tools/__init__.py
  src/tools/__init__.py
  tests/test_tool_config_v1.py
  tests/test_tool_policy_v1.py
  tests/test_tools_current_baseline.py
  src/tools/Tools层开发步骤与进度(2)-文件与命令工具.md
```

#### 实现结果

```text
read_file_chunk:
  参数 path/start_line/line_count/encoding，可按 1-based 行号读取中间区间。
  返回 path、encoding、size_bytes、start_line、end_line、line_count、content、
  has_more_before、has_more_after、line_count_limit、line_count_capped。
  采用流式逐行读取，不使用 readlines() 载入整文件。

read_file_head:
  读取首段内容，复用同一范围读取底座，返回与 chunk 一致的结构。

read_file_tail:
  读取尾段内容，采用 deque 固定窗口实现，不为大文件构建完整行列表。

共享底座:
  三个工具复用 read_file 的 PathResolver、敏感路径策略、二进制检测和编码选择逻辑。
  `read_file_range_max_lines` 作为 range 工具硬上限，默认 400 行，超出时钳制并标记。
  敏感文件仍默认阻断；仅当 ToolSpec metadata 声明 allow_sensitive_read_with_confirmation=true
  且确认票据有效时，才允许读取正文。
  越界起始行返回空内容但保留 has_more_before 语义，方便后续定位。
```

#### 测试命令与结果

```powershell
python -B -m unittest tests.test_read_file_ranges
# Ran 8 tests - OK

python -B -m unittest tests.test_read_file_tool tests.test_read_file_ranges tests.test_file_listing_tools tests.test_find_files_tool tests.test_tool_path_resolver tests.test_tools_current_baseline tests.test_tool_registry_v1 tests.test_tool_policy_v1
# Ran 74 tests - OK

python -B -m unittest tests.test_tool_manager_v1 tests.test_tool_config_v1 tests.test_tool_logging_v1 tests.test_tool_errors_v1 tests.test_tool_result_v1 tests.test_tool_spec_v1
# Ran 43 tests - OK

python -B -m unittest tests.test_react_executor_tool_action tests.test_react_executor_command_action tests.test_react_executor_safety tests.test_react_executor_confirmation
# Ran 34 tests - OK

python -B -m unittest discover -s tests -p 'test_*.py'
# Ran 532 tests - OK (skipped=1)

python -B -m compileall -q src/tools tests/test_read_file_tool.py tests/test_read_file_ranges.py
# 通过
```

#### 本 Step 边界与遗留问题

```text
本 Step 只做行范围读取，不做 byte offset，不做全文摘要，不做 regex 搜索。
本 Step 不实现 read_file 以外的文件编辑能力，也不自动承接 patch 定位策略。
超大文件仍然保留行式流读取路径，但不在 Tools 内做复杂索引或缓存。
输出结构已为 Step 15/16 的 write/patch 定位准备好行号和区间语义，但不替代后续工具本身。
```

---

## Step 15：write_file 的 create、overwrite 与 append

**状态：已完成**

### 目标

用一个明确工具处理整体写入，并用 `write_mode` 显式区分新建、覆盖和追加，不能靠 ToolManager 猜测模型意图。

### 参数

```text
path
content
write_mode=create|overwrite|append|create_or_overwrite
encoding=utf-8
```

### 核心回答

工具如何判断新增、覆盖或追加：

```text
由 ActionPacket.action_args.write_mode 显式声明。
ReActExecutor 将该字段放入 ToolCallRequest.args。
ToolManager 不通过自然语言或命令字符串猜测。
工具再结合目标是否存在校验模式是否合法。
```

### 执行规则

```text
create + 已存在:
  file_already_exists

overwrite + 不存在:
  file_not_found

append + 不存在:
  V1 默认 file_not_found

create_or_overwrite:
  兼容模式，按更高风险处理
```

### dry_run

必须提供：

```text
目标路径
是否存在
write_mode
旧/新大小
content_hash
diff_preview（在阈值内）
requires_confirmation
```

### 真实写入

1. 写前重新校验 preview 依据。
2. 优先使用临时文件 + 原子替换完成 overwrite，避免半写文件。
3. 返回 created/overwritten/appended、bytes_written、前后 hash。
4. 是否创建父目录必须由参数或固定策略明确；V1 建议默认只创建已存在父目录，避免隐式创建大量目录。
5. 旧 `file_writer` 迁移为 alias 或禁用占位，不再作为模型正式主工具。

### 权限

```text
用户未开启总写权限:
  create/overwrite/append 均进入 confirmation 流程。

allow_write_workspace=true:
  普通 workspace 文件可按策略执行。

敏感路径/blocked:
  不因总开关自动放行。
```

### 明确不做

```text
不负责局部修改。
不自动合并并发冲突。
不写 workspace 外文件。
不默认修改 .git 内部或密钥文件。
```

### 测试与验收

```text
四种 write_mode。
未授权确认。
会话写权限。
dry_run 无副作用。
preview 后文件变化冲突。
原子 overwrite。
敏感路径。
file_writer 迁移。
```

```powershell
python -m pytest tests/test_write_file_tool.py tests/test_tool_preview_v1.py -q
```

### Step 15 完成记录（2026-08-16）

#### 修改文件

```text
新增:
  src/tools/file_tools/writing.py
  tests/test_write_file_tool.py

更新:
  src/tools/errors.py
  src/tools/policy.py
  src/tools/output_control.py
  src/tools/registry.py
  src/tools/tool_manager.py
  src/tools/file_tools/__init__.py
  src/tools/__init__.py
  src/agent/react_executor.py
  src/agent/executor.py
  tests/test_tool_registry_v1.py
  tests/test_tool_policy_v1.py
  tests/test_tools_current_baseline.py
  src/tools/Tools层开发步骤与进度(2)-文件与命令工具.md
```

#### 实现结果

```text
write_file:
  正式注册为 builtin/write 工具，canonical name 为 write_file。
  支持 path、content、write_mode=create|overwrite|append|create_or_overwrite、encoding=utf-8。
  所有目标路径复用 PathResolver，只允许 workspace 内路径。
  不隐式创建缺失父目录；父目录缺失返回 parent_directory_not_found。
  create + 目标存在返回 file_already_exists。
  overwrite + 目标缺失返回 file_not_found。
  append + 目标缺失返回 file_not_found。
  create_or_overwrite 作为高风险兼容模式处理。
  overwrite 与 create_or_overwrite 覆盖已有文件时使用同目录临时文件 + os.replace。
  返回 FileWriteData 兼容结构，包含 created/overwritten/appended、bytes_written、
  old/new size、content_hash_before/content_hash_after、content_preview/content_truncated。

迁移兼容:
  file_writer 不再作为第二套正式 handler 暴露。
  ToolSpec 将 file_writer 注册为 write_file alias。
  运行时兼容旧 file_path/overwrite 参数，但 to_model_specs 只暴露正式 write_file schema。
  ReActExecutor 的可用工具验证会同时识别 registry alias，旧 file_writer ActionPacket 仍可迁移执行。

策略与预览:
  ToolPolicy 支持 metadata.risk_by_arg 动态提升风险。
  write_mode=create/append 为 medium；overwrite/create_or_overwrite 为 high。
  allow_write_workspace=false 直接拒绝，不进入伪确认。
  敏感/blocked 路径仍由 PathPolicy/ToolPolicy 阻断，不因写权限或确认自动放行。
  dry_run 由 ToolRuntime 统一拦截，无副作用。
  OutputController 对 preview_kind=file_write 生成写入预览：
    path、exists、write_mode、old_size_bytes、new_size_bytes、content_hash、
    before_hash、content_preview、content_truncated、requires_confirmation。
  preview_hash 包含资源快照，确认执行前目标变化会返回 preview_conflict。
```

#### 测试命令与结果

```powershell
python -B -m unittest tests.test_write_file_tool
# Ran 12 tests - OK

python -B -m unittest tests.test_write_file_tool tests.test_tool_preview_v1 tests.test_tool_manager_v1 tests.test_tool_registry_v1 tests.test_tool_policy_v1 tests.test_tools_current_baseline
# Ran 64 tests - OK

python -B -m unittest tests.test_react_executor_tool_action tests.test_react_executor_command_action tests.test_react_executor_safety tests.test_react_executor_confirmation
# Ran 34 tests - OK

python -B -m unittest discover -s tests -p 'test_*.py'
# Ran 545 tests - OK (skipped=1)

python -B -m compileall -q src/tools src/agent/react_executor.py src/agent/executor.py tests/test_write_file_tool.py
# 通过
```

#### 本 Step 边界与遗留问题

```text
本 Step 只实现整文件写入，不实现局部 patch；精准定位和局部增删改留给 Step 16。
本 Step 不实现目录创建参数；V1 默认要求父目录已存在。
本 Step 不实现跨文件事务、自动冲突合并、备份恢复或格式化。
file_writer 仅作为 alias/迁移兼容存在，不作为模型正式等价工具暴露。
Planner 当前仍可能产生旧 file_writer 步骤，已由 registry alias 与 ReActExecutor 可用工具验证兼容；
后续若迁移 Planner 输出，应统一改为 write_file + path/write_mode，但不在本 Step 重做 Planner 主链路。
```

---

## Step 16：patch_file 精准定位、局部增删改与 dry_run

**状态：已完成**

### 目标

实现对已有文本文件的局部替换、前插、后插和块删除，并拒绝模糊匹配。

### 支持操作

```text
replace
insert_before
insert_after
delete_block
```

这些操作覆盖：

```text
修改已有行。
在文件头、中间或尾部插入内容。
删除精确文本块。
不需要整体覆盖文件。
```

### 定位优先级

```text
1. line_start + line_end + old_text 校验
2. old_text 唯一匹配
3. old_text + occurrence
4. anchor_before / anchor_after 限定范围后匹配
```

### 必须拒绝

```text
old_text 找不到
多处匹配但无 occurrence/anchor
line range 与 old_text 不一致
anchor 找不到
多个 patch 相互重叠或冲突
preview 后源文件 hash 改变
```

### dry_run

dry_run 和执行必须调用同一定位函数，返回：

```text
每个 patch 命中状态
命中行号
影响行数
diff_preview
before_hash
requires_confirmation
```

真实执行时再次验证 `before_hash` 或对应内容。

### 返回

```text
path
patch_count
applied_count
changed_lines
diff_preview
content_hash_before
content_hash_after
patch_results
```

### 明确不做

```text
不做模糊相似度替换。
不做 AST patch。
不做跨文件事务。
不自动解决冲突。
不保证自动格式化。
```

### 测试与验收

```text
四种 operation。
文件头/中间/尾部插入。
唯一、多次、指定 occurrence。
anchor 限定。
模糊匹配拒绝。
多个 patch 冲突。
dry_run 与真实执行一致。
```

```powershell
python -m pytest tests/test_patch_file_tool.py -q
```

### Step 16 完成记录（2026-08-16）

#### 修改文件

```text
src/tools/errors.py
src/tools/file_tools/patching.py
src/tools/file_tools/__init__.py
src/tools/output_control.py
src/tools/registry.py
src/tools/runtime.py
src/tools/tool_manager.py
src/tools/__init__.py
tests/test_patch_file_tool.py
tests/test_tools_current_baseline.py
```

#### 实现说明

```text
新增正式 patch_file 工具，挂入 ToolManager / ToolRuntime / ToolRegistry，作为高风险写工具，需要 confirmation_id + preview_hash 后执行。
支持 replace、insert_before、insert_after、delete_block 四种局部编辑。
定位逻辑统一由 build_patch_plan 承担，dry_run preview 与真实执行共用同一套精确匹配算法。
支持 line_start + line_end + old_text 校验、唯一 old_text、old_text + occurrence、anchor_before / anchor_after 限定范围。
拒绝 old_text 找不到、多处匹配无消歧、line range 内容不一致、anchor 找不到、patch 范围重叠或同位置多插入。
preview_kind=file_patch 接入 OutputController，返回 diff_preview、before_hash/after_hash、patch_results，并纳入 preview_hash。
ToolRuntime 在执行前先对确认票据绑定当前 preview_hash，文件内容变化会返回 preview_conflict。
写入使用临时文件 + os.replace，避免半写入；仍受 PathResolver、ToolPolicy workspace/sensitive 路径规则约束。
```

#### 测试命令与结果

```powershell
python -B -m unittest tests.test_patch_file_tool
# Ran 9 tests in 0.297s
# OK

python -B -m unittest tests.test_patch_file_tool tests.test_write_file_tool tests.test_tool_preview_v1 tests.test_tool_manager_v1 tests.test_tool_registry_v1 tests.test_tool_policy_v1 tests.test_tools_current_baseline
# Ran 73 tests in 0.673s
# OK

python -B -m unittest tests.test_react_executor_tool_action tests.test_react_executor_command_action tests.test_react_executor_safety tests.test_react_executor_confirmation
# Ran 34 tests in 0.513s
# OK

python -B -m unittest discover -s tests -p 'test_*.py'
# Ran 554 tests in 5.722s
# OK (skipped=1)

python -B -m compileall -q src/tools tests/test_patch_file_tool.py
# OK
```

#### 本 Step 边界与遗留问题

```text
本 Step 只实现单文件精确文本 patch，不实现 AST patch、格式化、跨文件事务或自动合并冲突。
old_text 匹配是精确字符串匹配；换行符、空白和缩进不做宽松归一化。
同一文件内多个 patch 会先基于原始内容定位，再统一应用；重叠范围和同位置多插入按冲突拒绝。
preview_hash 绑定当前 preview 内容和目标文件快照；确认后源文件变化会被 ToolRuntime 拒绝为 preview_conflict。
patch_file 是正式 canonical 工具；未新增旧式第二运行时，也未改 Analyzer / Planner / ReActExecutor 主链路。
```

---

## Step 17：copy_file、move_file 与 rename_file

**状态：已完成**

### 目标

实现 workspace 内明确文件的复制、移动和重命名，并对覆盖和源目标冲突进行结构化处理。

### 实施细节

```text
copy_file:
  source_path
  target_path
  overwrite=false

move_file:
  source_path
  target_path
  overwrite=false

rename_file:
  source_path
  new_name
```

1. 源和目标都经过统一路径解析。
2. `rename_file.new_name` 只能是名称，不能夹带路径逃逸；跨目录使用 `move_file`。
3. 目标存在且未允许覆盖时返回 `file_already_exists`。
4. 覆盖属于 high risk，支持 dry_run 和确认。
5. move/rename 默认 high；copy 普通新目标可为 medium。
6. 不允许目录级 copy/move/rename，除非后续单独设计。

### 明确不做

```text
不跨 workspace。
不递归复制或移动目录。
不自动覆盖。
不实现撤销系统。
```

### 测试与验收

```text
普通 copy/move/rename。
目标冲突。
overwrite 确认。
源不存在。
new_name 路径注入。
敏感文件。
dry_run。
```

```powershell
python -m pytest tests/test_file_mutation_tools.py -q
```

### Step 17 完成记录（2026-08-16）

#### 修改文件

```text
src/tools/file_tools/mutation.py
src/tools/file_tools/__init__.py
src/tools/output_control.py
src/tools/registry.py
src/tools/tool_manager.py
src/tools/__init__.py
tests/test_file_mutation_tools.py
tests/test_tools_current_baseline.py
src/tools/Tools层开发步骤与进度(2)-文件与命令工具.md
```

#### 实现说明

```text
新增 copy_file、move_file、rename_file 三个正式文件变更工具，均通过 ToolManager facade 接入 ToolRuntime。
三个工具复用 PathResolver 进行源路径、目标路径、敏感路径、workspace 越界、symlink、父目录存在性和目录拒绝校验。
copy_file 普通新目标为 medium risk；overwrite=true 通过 risk_by_arg 提升为 high，需要 dry_run preview + confirmation_id + preview_hash。
move_file 与 rename_file 默认 high risk，并要求确认；rename_file.new_name 只允许纯文件名，跨目录必须使用 move_file。
OutputController 新增 preview_kind=file_mutation，dry_run 与确认预览会返回源/目标快照、hash、大小、是否覆盖、是否移除源文件等信息。
真实执行前 ToolRuntime 重新生成 preview_hash；源或目标在确认后变化会返回 preview_conflict，不进入 handler。
copy 覆盖使用临时文件 + os.replace；move/rename 使用 os.replace；V1 不声明跨文件事务或撤销。
```

#### 测试命令与结果

```powershell
python -B -m unittest tests.test_file_mutation_tools
# Ran 8 tests in 0.273s
# OK

python -B -m unittest tests.test_file_mutation_tools tests.test_patch_file_tool tests.test_write_file_tool tests.test_tool_preview_v1 tests.test_tool_manager_v1 tests.test_tool_registry_v1 tests.test_tool_policy_v1 tests.test_tools_current_baseline
# Ran 81 tests in 0.934s
# OK

python -B -m unittest tests.test_react_executor_tool_action tests.test_react_executor_command_action tests.test_react_executor_safety tests.test_react_executor_confirmation
# Ran 34 tests in 0.544s
# OK

python -B -m unittest discover -s tests -p 'test_*.py'
# Ran 562 tests in 6.042s
# OK (skipped=1)

python -B -m compileall -q src/tools tests/test_file_mutation_tools.py
# OK
```

#### 本 Step 边界与遗留问题

```text
本 Step 只支持单个明确文件的 copy/move/rename，不支持目录、递归、glob、批量事务或撤销。
rename_file 不接受路径片段；跨目录移动统一走 move_file。
覆盖目标必须显式 overwrite=true，并通过 high risk 确认链路；敏感目标或敏感源仍由 PathResolver/Policy 阻断。
move/rename 使用 os.replace，V1 不额外实现跨设备 fallback；若底层文件系统拒绝则返回 file_write_failed。
未修改 Analyzer / Planner / ReActExecutor 主链路；如后续 Planner 需要显式产出 copy_file/move_file/rename_file，可在 Planner 层单独迁移。
```

---

## Step 18：delete_file 明确目标删除

**状态：已完成**

### 目标

实现受控文件删除，并把删除能力从命令工具中分离出来，以便精确 preview、确认和审计。

### 参数

仅允许其一：

```text
path: 单个明确文件
file_paths: 明确文件列表
```

### 执行规则

1. `path/file_paths` 不能包含 glob。
2. 每个目标都必须是文件，不允许目录。
3. V1 不支持 `recursive` 参数；若收到则返回 invalid_args 或 delete_directory_not_allowed。
4. dry_run 返回每个目标存在性、大小、mtime、敏感状态、总数量和总大小。
5. 真实删除前重新解析所有目标并检查 preview 一致性。
6. 多文件删除的部分失败策略必须固定。建议 V1 在执行前完成全部校验，任一目标 blocked/非法则整体不开始；执行期意外失败返回已删除和未删除列表，不伪称原子事务。

### 错误码

```text
glob_delete_not_allowed
delete_directory_not_allowed
file_not_found
workspace_out_of_scope
sensitive_path_blocked
file_conflict
```

### 为什么不是复杂化

专用删除工具使模型参数、权限、preview 和审计对象都是明确文件，避免从任意 shell 字符串中不可靠地推断删除范围。命令工具仍保留通用能力，但删除这一高风险行为在 V1 走专门通道。

### 明确不做

```text
不删目录。
不递归。
不 glob。
不通过确认放行 workspace 外删除。
不实现回收站/恢复。
```

### 测试与验收

```text
单文件/明确列表。
目录和 glob 拒绝。
整体预校验。
dry_run。
敏感文件。
部分执行失败的真实结果。
```

```powershell
python -m pytest tests/test_delete_file_tool.py -q
```

### Step 18 完成记录（2026-08-16）

#### 修改文件

```text
src/tools/errors.py
src/tools/file_tools/deletion.py
src/tools/file_tools/__init__.py
src/tools/output_control.py
src/tools/registry.py
src/tools/tool_manager.py
src/tools/__init__.py
tests/test_delete_file_tool.py
tests/test_tools_current_baseline.py
src/tools/Tools层开发步骤与进度(2)-文件与命令工具.md
```

#### 实现说明

```text
新增 delete_file 正式删除工具，支持 path 单文件或 file_paths 明确文件列表，且二者必须且只能使用其一。
delete_file 注册为 high risk、requires_confirmation=true、workspace_scope=write_workspace、supports_dry_run=true。
新增 file_delete preview，dry_run 返回目标路径、逐文件大小/mtime/hash/敏感标识、总数量、总大小和 requires_confirmation。
真实执行前由 ToolRuntime 重新生成 preview_hash；确认后目标内容或快照变化会返回 preview_conflict，不进入 handler。
删除前统一完成全量预校验；任一目标非法、missing、目录、glob、重复、敏感或 workspace 越界时整体不开始删除。
执行期若发生部分失败，返回 file_delete_failed，并报告 deleted_files、failed_files、pending_files，不伪称事务原子性。
新增 glob_delete_not_allowed、delete_directory_not_allowed、file_delete_failed 错误码。
```

#### 测试命令与结果

```powershell
python -B -m unittest tests.test_delete_file_tool
# Ran 9 tests in 0.195s
# OK

python -B -m unittest tests.test_delete_file_tool tests.test_file_mutation_tools tests.test_patch_file_tool tests.test_write_file_tool tests.test_tool_preview_v1 tests.test_tool_manager_v1 tests.test_tool_registry_v1 tests.test_tool_policy_v1 tests.test_tools_current_baseline
# Ran 90 tests in 1.098s
# OK

python -B -m unittest tests.test_react_executor_tool_action tests.test_react_executor_command_action tests.test_react_executor_safety tests.test_react_executor_confirmation
# Ran 34 tests in 0.527s
# OK

python -B -m unittest discover -s tests -p 'test_*.py'
# Ran 571 tests in 6.205s
# OK (skipped=1)

python -B -m compileall -q src/tools tests/test_delete_file_tool.py
# OK
```

#### 本 Step 边界与遗留问题

```text
本 Step 只支持明确普通文件删除，不支持目录、递归、glob、回收站/恢复、批量事务或 workspace 外删除。
敏感路径仍按现有 PathResolver / ToolPolicy 默认阻断，不能通过 confirmation_id 放行。
多文件删除在执行前尽量全量校验；执行期 OS 权限、文件锁等失败会返回部分结果，不保证事务回滚。
命令行删除拦截属于 Step 19 command_tool 工作，不在本 Step 重写 ReActExecutor 或命令主链路。
```

---

## Step 19：command_tool argv / shell=False 正式化

**状态：已完成**

### 目标

让普通命令工具执行绝大多数不需要 shell 语法的程序，同时保持参数可审计和跨平台边界。

### 正式参数

```text
program
args[]
cwd="."
purpose
timeout_seconds
network_required=false
writes_files=false
target_paths=[]
```

兼容：

```text
command: "python -m pytest tests/test_tools.py"
```

兼容字符串必须解析为 argv；发现复杂 shell 元字符时返回 `shell_required`，由 ReActExecutor/模型明确改用 `shell_command_tool`。

### 设计解释

这不是“禁止执行所有复杂命令”，而是分成两条可审计通道：

```text
command_tool:
  argv + shell=False，适合大多数程序调用。

shell_command_tool:
  复杂管道、重定向、变量展开等，风险更高且需要确认。
```

### 实施细节

1. 优先使用 `program + args[]`，不对数组再次按空格拆分。
2. Windows 兼容字符串解析要有明确测试；路径含空格不能被拆坏。
3. cwd 必须在 workspace 内。
4. 使用 `subprocess.run(..., shell=False)`。
5. timeout 范围由 options、spec 和全局上限共同裁决。
6. 输出 data 统一为 `CommandExecutionData`，区分 stdout/stderr、bytes、preview、truncated、exit_code、timed_out。
7. 非零 exit code 返回 `success=false/code=command_nonzero_exit`，但保留真实 stdout/stderr 数据。

### 明确不做

```text
不执行管道、重定向、&&、;、命令替换。
不自动切换 shell=True。
不提供管理员提权。
不支持交互 TTY。
不启动长期后台服务。
```

### 测试与验收

```text
program+args。
兼容 command 字符串。
含空格路径。
shell 元字符 -> shell_required。
cwd 越界。
timeout。
非零退出。
stdout/stderr 截断。
```

```powershell
python -m pytest tests/test_command_tool_v1.py tests/test_command_tool_argv.py -q
```

### Step 19 完成记录（2026-08-16）

#### 修改文件

```text
src/tools/command_tool.py
src/tools/errors.py
src/tools/output_control.py
src/tools/registry.py
src/tools/runtime.py
tests/test_command_tool_v1.py
tests/test_command_tool_argv.py
tests/test_tool_errors_v1.py
src/tools/Tools层开发步骤与进度(2)-文件与命令工具.md
```

#### 实现说明

```text
command_tool 正式改为 program + args[] 优先，兼容 command 字符串输入并解析为 argv。
所有执行统一使用 subprocess.run(..., shell=False)，不自动切换 shell=True。
兼容 command 字符串中发现管道、重定向、&&、||、;、命令替换、环境变量展开等 shell 控制语义时返回 shell_required。
删除命令 rm/del/erase/rmdir/rd/Remove-Item 返回 command_delete_not_allowed，并提示删除文件请使用 delete_file 工具。
危险命令 format/shutdown/reboot、reg delete、force flags 返回 command_blocked。
cwd 使用 PathResolver 校验，必须在 workspace 内且必须是目录。
network_required=true 需要 allow_network；writes_files=true 需要 allow_write_workspace；target_paths 也会做 workspace/sensitive 校验。
输出统一为 CommandExecutionData 形态，包含 command/program/args/cwd/exit_code/stdout/stderr/preview/bytes/truncated/timed_out/duration_ms。
非零退出返回 command_nonzero_exit，但保留真实 stdout/stderr/exit_code。
Timeout 裁决纳入 request.args.timeout_seconds，并由 ToolRuntime 传递给 handler 时覆盖为 spec/options/global 的有效值。
通用 preview 对目录资源不再记录 mtime/hash，避免 command_tool dry_run 日志写入导致 cwd 目录 mtime 改变而误报 preview_conflict。
```

#### 测试命令与结果

```powershell
python -B -m unittest tests.test_command_tool_v1 tests.test_command_tool_argv
# Ran 12 tests in 1.390s
# OK

python -B -m unittest tests.test_command_tool_v1 tests.test_command_tool_argv tests.test_tool_errors_v1 tests.test_tool_preview_v1 tests.test_tool_manager_v1 tests.test_tool_registry_v1 tests.test_tool_policy_v1 tests.test_tools_current_baseline tests.test_delete_file_tool tests.test_file_mutation_tools tests.test_patch_file_tool tests.test_write_file_tool
# Ran 110 tests in 2.522s
# OK

python -B -m unittest tests.test_react_executor_command_action tests.test_react_executor_safety tests.test_react_executor_confirmation tests.test_react_executor_tool_action
# Ran 34 tests in 0.532s
# OK

python -B -m unittest discover -s tests -p 'test_*.py'
# Ran 581 tests in 7.647s
# OK (skipped=1)

python -B -m compileall -q src/tools tests/test_command_tool_v1.py tests/test_command_tool_argv.py
# OK
```

#### 本 Step 边界与遗留问题

```text
本 Step 只实现普通 command_tool 的 argv/shell=False 通道，不实现 shell_command_tool。
复杂 shell 语法不自动执行，不自动改用 shell=True；由后续 Step 20 的 shell_command_tool 承接。
不支持交互 TTY、管理员提权、长期后台服务托管。
command_tool 默认仍为 high risk，由 ToolPolicy 和 ReActExecutor 现有确认链路控制；未重做 Analyzer / Planner / ReActExecutor 主链路。
命令字符串兼容解析依赖 shlex，复杂 Windows shell 表达式应走 Step 20，而不是在 command_tool 内兼容。
```

---

## Step 20：shell_command_tool 最小可用

**状态：已完成**

### 目标

提供复杂 shell 语法的正式工具通道，使 Agent 可以执行管道和重定向等真实任务，但始终处于 high risk、确认和策略约束下。

### 参数

```text
command
shell=powershell|cmd|bash
cwd
purpose
timeout_seconds
```

### 实施细节

1. Windows 默认 shell 由配置明确，不根据模型自由指定任意可执行文件。
2. `shell_command_tool` ToolSpec：

```text
risk_level=high
requires_confirmation=true
workspace_scope=shell_command
supports_dry_run=true
```

3. dry_run 只解析和展示 command、shell、cwd、风险命中，不执行。
4. 真实执行必须 `confirmed=true` 且 `allow_shell_command=true` 或符合确认恢复策略。
5. 仍执行 blocked 规则；确认不能放行 format/shutdown/递归删除/工作区外破坏等。
6. 输出与 command_tool 使用同一 `CommandExecutionData`。
7. 正式名为 `shell_command_tool`；现有 `shell_tool` 作为迁移 alias，并同步 ReActExecutor 常量与测试，不能长期暴露两个等价模型工具。

### 明确不做

```text
不允许任意 shell executable 路径。
不提供管理员权限。
不支持交互式命令。
不托管后台进程。
不把 shell 工具当普通低风险工具。
```

### 测试与验收

```text
未确认拒绝。
dry_run 不执行。
允许的管道/重定向最小案例。
blocked 命令。
cwd 越界。
timeout/输出截断。
shell_tool alias。
```

```powershell
python -m pytest tests/test_shell_command_tool.py tests/test_react_executor_command_action.py tests/test_react_executor_confirmation.py -q
```

### Step 20 完成记录（2026-08-16）

#### 修改文件

```text
src/tools/shell_command_tool.py
src/tools/output_control.py
src/tools/registry.py
src/tools/tool_manager.py
src/tools/__init__.py
src/agent/react_executor.py
tests/test_shell_command_tool.py
tests/test_tool_registry_v1.py
tests/test_tools_current_baseline.py
src/tools/Tools层开发步骤与进度(2)-文件与命令工具.md
```

#### 实现说明

```text
新增 shell_command_tool 正式工具，提供复杂 shell 语法的高风险确认通道。
支持 shell=powershell|cmd|bash 三个枚举值，不接受任意 shell executable 路径。
ToolSpec 设置 risk_level=high、requires_confirmation=true、workspace_scope=shell_command、supports_dry_run=true。
shell_tool 仅作为 shell_command_tool 的 alias；ToolManager/list_tools 只暴露 canonical shell_command_tool。
dry_run 接入 preview_kind=shell_command，只解析 command/shell/cwd/purpose/timeout 和 blocked 命中，不执行命令。
真实执行使用明确 shell argv + subprocess.run(..., shell=False)，由 shell 程序自身解释管道、重定向、&&、; 等复杂语法。
cwd 复用 PathResolver 校验，必须在 workspace 内且必须为目录。
输出复用 CommandExecutionData 形态，包含 stdout/stderr、preview、bytes、truncated、exit_code、timed_out、duration_ms。
仍执行最小 blocked 规则：删除命令返回 command_delete_not_allowed；format/shutdown/reboot/reg delete 返回 command_blocked。
ReActExecutor COMMAND_TOOL_NAMES 同步加入 shell_command_tool，同时保留 shell_tool 迁移兼容。
```

#### 测试命令与结果

```powershell
python -B -m unittest tests.test_shell_command_tool
# Ran 10 tests in 2.287s
# OK

python -B -m unittest tests.test_shell_command_tool tests.test_command_tool_v1 tests.test_command_tool_argv tests.test_tool_registry_v1 tests.test_tool_manager_v1 tests.test_tool_policy_v1 tests.test_tools_current_baseline tests.test_react_executor_command_action tests.test_react_executor_confirmation
# Ran 87 tests in 4.196s
# OK

python -B -m unittest tests.test_react_executor_safety tests.test_react_executor_tool_action tests.test_tool_preview_v1 tests.test_tool_errors_v1 tests.test_delete_file_tool tests.test_file_mutation_tools tests.test_patch_file_tool tests.test_write_file_tool
# Ran 67 tests in 1.147s
# OK

python -B -m unittest discover -s tests -p 'test_*.py'
# Ran 591 tests in 9.980s
# OK (skipped=1)

python -B -m compileall -q src/tools src/agent/react_executor.py tests/test_shell_command_tool.py
# OK
```

#### 本 Step 边界与遗留问题

```text
本 Step 只实现 shell_command_tool 最小可用，不实现完整危险命令矩阵；更细的删除、递归、网络、外传和输出边界留给 Step 21。
不支持管理员提权、交互式 TTY、长期后台服务托管或任意 shell executable 路径。
shell_command_tool 默认仍是 high risk，必须通过 ToolPolicy 的 allow_shell_command 和 confirmation_id/preview_hash。
cmd/powershell/bash 是否真实可执行取决于本机环境；工具只限制允许的 shell 名，不动态安装或下载 shell。
未重做 Analyzer / Planner / ReActExecutor 主链路；仅同步命令工具名称集合以识别 canonical shell_command_tool。
```

---

## Step 21：命令删除拦截、危险矩阵、网络与输出边界

**状态：已完成**

### 目标

完成两个命令工具共用的命令策略，避免仅靠少量关键字或模型自报 `risk_level`。

### 建议新增

```text
src/tools/command_policy.py
tests/test_command_policy_v1.py
```

### 必做

1. 删除命令拦截：

```text
rm / del / erase / rmdir / rd / Remove-Item
  -> command_delete_not_allowed
  -> message 提示使用 delete_file
```

2. blocked 至少覆盖：

```text
format
shutdown/reboot
reg delete
递归目录删除
工作区外破坏
明显密钥读取/外传
权限策略绕过
```

3. 风险分析同时看：

```text
program
args
shell command
cwd
target_paths
network_required
writes_files
```

4. `network_required=true` 受 allow_network 控制，但模型写 false 不能证明命令一定不联网；明显网络程序应由规则提升风险。
5. 不再简单阻止 `powershell/pwsh/cmd` 作为所有场景的 executable；复杂 shell 必须路由到 `shell_command_tool` 并确认。
6. 输出分别限制 stdout/stderr，记录原始字节数和截断标记。
7. tools.log 只记录 preview/hash/length，不记录完整输出。

### 明确不做

```text
不承诺通过字符串分析识别所有危险命令。
不因“所有命令都能执行”的目标而允许 blocked 行为。
不允许用户确认绕过 workspace 外破坏和 V1 不支持能力。
```

### 测试与验收

```text
各平台删除命令拦截。
PowerShell 普通命令路由。
blocked 矩阵。
模型虚报低风险无效。
网络命令权限。
输出超限。
```

```powershell
python -m pytest tests/test_command_policy_v1.py tests/test_command_tool_v1.py tests/test_shell_command_tool.py -q
```

### Step 21 完成记录（2026-08-16）

#### 修改文件

```text
新增:
  src/tools/command_policy.py
  tests/test_command_policy_v1.py

更新:
  src/tools/command_tool.py
  src/tools/shell_command_tool.py
  src/tools/output_control.py
  src/tools/registry.py
  src/tools/__init__.py
  src/tools/Tools层开发步骤与进度(2)-文件与命令工具.md
```

#### 实现说明

```text
新增 command_policy 作为 command_tool 与 shell_command_tool 的共享命令策略底座。
普通 command_tool 继续保持 argv + shell=False，不直接执行 shell 语义；遇到 powershell/pwsh/cmd 的 eval 参数时返回 shell_required，引导改走 shell_command_tool。
删除命令 rm/del/erase/rmdir/rd/Remove-Item 在普通命令和 shell 命令通道中统一返回 command_delete_not_allowed，并提示使用 delete_file。
危险矩阵覆盖 format、shutdown/reboot、reg delete、robocopy/xcopy purge/mirror、权限策略绕过命令、明显敏感材料读取/外传、target_paths 越界和 shell 重定向越界。
网络风险不依赖模型自报；network_required=true、curl/wget/ssh/ping 等明显网络程序、git/npm/pip 等联网子命令和 URL 命中都会检查 allow_network。
writes_files=true 会检查 allow_write_workspace；target_paths 复用 PathResolver，不能越过 workspace_root 或敏感/blocked 路径规则。
shell_command_tool 的 dry_run preview 与真实执行共用同一策略，避免预览放行、执行拒绝的策略分叉。
shell_command_tool ToolSpec 正式接受 network_required、writes_files、target_paths，供 ReActExecutor 的结构化 ActionPacket 传递风险信号。
命令输出仍按 stdout/stderr 分别截断，并记录 bytes/truncated/preview；tools.log 只记录长度和 hash 级摘要，不记录完整 stdout/stderr/stdout_preview/stderr_preview。
ToolManager / ToolRuntime 入口保持不变，命令仍通过 ToolCallRequest -> ToolPolicy -> ToolRuntime -> ToolResult 管线执行。
```

#### 测试命令与结果

```powershell
python -B -m unittest tests.test_command_policy_v1 tests.test_command_tool_v1 tests.test_command_tool_argv tests.test_shell_command_tool
# Ran 30 tests - OK

python -B -m unittest tests.test_command_policy_v1 tests.test_shell_command_tool tests.test_command_tool_v1 tests.test_command_tool_argv tests.test_tool_registry_v1 tests.test_tool_manager_v1 tests.test_tool_policy_v1 tests.test_tool_logging_v1 tests.test_tool_preview_v1 tests.test_tools_current_baseline tests.test_react_executor_command_action tests.test_react_executor_confirmation tests.test_react_executor_safety tests.test_react_executor_tool_action
# Ran 119 tests - OK

python -B -m unittest discover -s tests -p 'test_*.py'
# Ran 599 tests - OK (skipped=1)

python -B -m compileall -q src\tools tests\test_command_policy_v1.py
# OK
```

#### 本 Step 边界与遗留问题

```text
字符串级 shell 分析只覆盖 V1 明确危险矩阵，不承诺识别所有危险命令组合。
shell 重定向只做显式目标路径校验；是否允许写入仍以 writes_files 结构化信号和 ToolPolicy capability 为准，未在本 Step 推翻 Step 20 的最小可用 shell 行为。
confirmed=true 不能绕过 command_policy 的 blocked 裁决；workspace 外破坏和权限策略绕过仍直接失败。
未重做 Analyzer / Planner / ReActExecutor 主链路；后续步骤继续通过 ToolSpec schema 和 ToolResult 结构与上层协作。
```

---

## Step 22：DocumentParser V1

**状态：已完成**

### 目标

把当前解析占位升级为结构化文档解析工具，支持 `txt/md/json/csv/pdf/docx/xlsx`，但不承担总结和 RAG 入库。

### 子步骤拆分

```text
Step 22.1 文本类解析:
  txt / md / json / csv。
  先完成纯标准库或低依赖解析、大小限制、编码错误和结构化返回。

Step 22.2 Office/PDF 解析:
  pdf / docx / xlsx。
  先检查项目可用依赖；缺依赖时返回 dependency_not_available。
  不因为解析复杂而引入总结、RAG 入库或 OCR。
```

### 参数与返回

```text
path
max_pages=20
max_chars=20000
include_metadata=true
```

`DocumentParseData`：

```text
path
file_type
title
page_count
sheet_count
text
text_preview
text_truncated
tables
metadata
parser
```

### 实施细节

1. txt/md/json/csv 优先使用标准库。
2. pdf/docx/xlsx 使用项目已有依赖；若依赖不存在，先核对项目依赖管理后再增加，不能在工具内部动态安装。
3. JSON 返回格式化文本或结构摘要时保留原始类型信息。
4. CSV/XLSX 表格做行列和字符上限，避免整本工作簿进入内存或上下文。
5. PDF 限制页数；加密文件返回 `document_encrypted`。
6. 所有解析应用输出截断策略。
7. 不为解析结果调用模型。需要摘要时由 ReActExecutor 后续 `call_model`。

### 错误码

```text
document_parse_failed
unsupported_document_type
document_too_large
document_encrypted
dependency_not_available
```

### 明确不做

```text
不做 OCR。
不总结文档。
不做 embedding/chunk 入库。
不抓取网页。
不解析任意可执行宏。
```

### 测试与验收

```text
七种格式的最小 fixture。
空文档。
损坏/加密/不支持文件。
页数/字符/表格上限。
依赖缺失结构化失败。
```

```powershell
python -m pytest tests/test_document_parser_v1.py -q
```

### Step 22 完成记录（2026-08-16）

#### 修改文件

```text
新增:
  tests/test_document_parser_v1.py

更新:
  src/tools/document_parser.py
  src/tools/errors.py
  src/tools/registry.py
  src/tools/tool_manager.py
  src/tools/__init__.py
  tests/test_tool_errors_v1.py
  tests/test_tool_registry_v1.py
  src/tools/Tools层开发步骤与进度(2)-文件与命令工具.md
```

#### 实现说明

```text
DocumentParser 从旧字符串占位升级为正式 ToolResult V1 工具，返回 DocumentParseData 结构。
正式参数为 path、max_pages、max_chars、include_metadata；file_path 作为兼容参数保留，但对模型暴露的 ToolSpec 只展示 path。
所有文件路径复用 PathResolver，只允许 workspace 内文件；workspace 越界、目录、symlink、敏感/blocked 路径均返回结构化失败。
txt/md/json/csv 使用标准库解析；JSON 保留顶层类型和 key 信息，CSV 返回受限 rows/tables。
docx/xlsx 使用标准库 zipfile + XML 做 V1 最小 OOXML 解析，不解析宏、不执行内容、不做复杂格式还原。
pdf 使用项目声明依赖 PyPDF2；当前环境缺依赖时返回 dependency_not_available，不在工具内部动态安装。
新增文档解析错误码 document_parse_failed、unsupported_document_type、document_too_large、document_encrypted、dependency_not_available，并接入错误类型映射。
max_chars 控制解析文本进入 ToolResult 的上限；metadata 记录 text_chars/text_hash/size/limit 等审计信息，include_metadata=false 时不返回 metadata。
DocumentParser 不调用 Models 层，不做总结、OCR、embedding、RAG 入库或网页抓取；需要语义处理时由 ReActExecutor 后续 call_model。
```

#### 测试命令与结果

```powershell
python -B -m unittest tests.test_document_parser_v1 tests.test_tool_registry_v1 tests.test_tool_errors_v1 tests.test_tools_current_baseline
# Ran 39 tests - OK

python -B -m unittest tests.test_document_parser_v1 tests.test_tool_registry_v1 tests.test_tool_manager_v1 tests.test_tool_policy_v1 tests.test_tool_logging_v1 tests.test_tool_output_control tests.test_planner_executor_compatibility tests.test_planner_v1_step_dependencies tests.test_react_executor_tool_action tests.test_react_executor_v1
# Ran 82 tests - OK

python -B -m unittest discover -s tests -p 'test_*.py'
# Ran 609 tests - OK (skipped=1)

python -B -m compileall -q src\tools tests\test_document_parser_v1.py
# OK
```

#### 本 Step 边界与遗留问题

```text
PDF 解析依赖 PyPDF2；当前环境不可用时按 dependency_not_available 返回，未动态安装依赖。
DOCX/XLSX 只实现最小 OOXML 文本和表格提取，不解析样式、批注、图片、公式计算、宏或嵌入对象。
CSV/XLSX 表格只返回受限行列，避免整本工作簿进入上下文。
加密 PDF 返回 document_encrypted 的路径已实现，但当前环境缺 PyPDF2 时无法在本轮测试中构造真实加密 PDF 回归。
旧 file_path 参数仅保留迁移兼容；后续 Planner/Analyzer 模板应逐步迁移到 path。
```

---

## Step 23：math、time、text、translator 与 code_executor 收束

**状态：已完成**

### 目标

让现有辅助工具服从正式协议，并明确哪些是 V1 可用能力、兼容占位或默认禁用。

### 子步骤拆分

```text
Step 23.1 math/time/text:
  math_calculator、time_query、text_processor 先统一 ToolResult、ToolSpec 和测试。

Step 23.2 translator:
  保持兼容占位或转为不暴露 mock；不在本 Step 默认升级为模型翻译。

Step 23.3 code_executor:
  默认 disabled/high risk，只完成协议外壳、安全拒绝和测试，不建立完整沙箱。
```

### math_calculator

```text
禁止任意 eval。
支持受限表达式和简单统计。
返回结构化 result/operation/normalized_expression。
```

### time_query

```text
支持 current 和设计内明确的简单转换。
时区使用标准库 zoneinfo。
无效时区结构化失败。
```

### text_processor

```text
只做规则型关键词、格式化和简单统计。
不把“summary”描述成模型级高质量摘要。
不默认调用 Models。
```

### translator

V1 采取兼容口径：

```text
如果仍是 mock:
  metadata.mock=true
  metadata.implemented=false 或不暴露给模型
  不进入关键验收

如果升级为 Models-backed:
  必须另开已确认 Step，复用 Models V1，不在本 Step 临时重做 provider
```

默认本 Step 不升级模型翻译。

### code_executor

```text
enabled=false
risk_level=high
requires_confirmation=true
未启用返回 tool_disabled
不建立完整沙箱
```

### 测试与验收

```text
calculator 不可执行 Python。
time timezone。
text_processor 规则输出。
translator 不伪装真实翻译。
code_executor 默认不出现在模型 specs。
全部返回 ToolResult V1。
```

```powershell
python -m pytest tests/test_utility_tools_v1.py tests/test_tool_registry_v1.py -q
```

### Step 23 完成记录（2026-08-16）

#### 修改文件

```text
新增:
  tests/test_utility_tools_v1.py

更新:
  src/tools/math_calculator.py
  src/tools/time_query.py
  src/tools/text_processor.py
  src/tools/translator.py
  src/tools/code_executor.py
  src/tools/registry.py
  src/tools/tool_manager.py
  src/tools/Tools层开发步骤与进度(2)-文件与命令工具.md
```

#### 实现说明

```text
math_calculator 改为返回 ToolResult V1，使用 AST 白名单执行受限表达式，不使用 eval/exec；支持 expression 和 statistics 两类结构化结果。
time_query 改为返回 ToolResult V1，使用 zoneinfo 优先解析时区；在无 IANA tzdata 的 Windows 环境为 UTC/Asia-Shanghai 等常用时区提供固定 offset fallback，无效时区返回 invalid_args。
text_processor 改为返回 ToolResult V1，只做规则型 format、keywords、statistics 和 rule_based_truncation；保留 summary 兼容名，但明确质量为 rule_based_truncation，不伪装模型摘要。
translator 保留兼容工具，但返回 metadata.mock=true、implemented=false、translated_text=None，不执行真实翻译，不调用 Models。
code_executor 改为禁用协议外壳，直接返回 tool_disabled，不再保留真实执行分支，也不建立沙箱。
ToolSpec 同步更新：math/time/text 返回 object；translator metadata.implemented=false/mock=true，因此不进入 model specs；code_executor enabled=false/implemented=false，因此默认不进入 model specs。
ToolManager 展示文案同步收束，不再描述 translator 为真实翻译能力或 code_executor 为可用执行能力。
```

#### 测试命令与结果

```powershell
python -B -m unittest tests.test_utility_tools_v1 tests.test_tool_registry_v1 tests.test_tools_current_baseline tests.test_tool_errors_v1
# Ran 37 tests - OK

python -B -m unittest tests.test_utility_tools_v1 tests.test_tool_registry_v1 tests.test_tool_manager_v1 tests.test_tool_policy_v1 tests.test_tool_logging_v1 tests.test_react_executor_tool_action tests.test_react_executor_safety tests.test_react_executor_v1 tests.test_planner_executor_compatibility tests.test_planner_v1_rule_templates tests.test_planner_v1_step_dependencies
# Ran 91 tests - OK

python -B -m unittest discover -s tests -p 'test_*.py'
# Ran 617 tests - OK (skipped=1)

python -B -m compileall -q src\tools tests\test_utility_tools_v1.py
# OK
```

#### 本 Step 边界与遗留问题

```text
math_calculator 只支持受限表达式和简单统计，不支持任意 Python、变量赋值、属性访问、列表推导或导入。
time_query 不做复杂自然语言时间解析；当前只支持 current/date_info/convert 兼容入口和明确 timezone。
text_processor 不做模型级摘要、改写、翻译或语义抽取；需要这些能力时由 ReActExecutor 后续 call_model，并通过 Models 层。
translator 仍是兼容占位，不进入关键验收和模型工具 specs；后续若升级为 Models-backed translator，需另开明确 Step。
code_executor 默认 disabled，不进入模型工具 specs；后续若要启用必须先设计沙箱、权限和审计，不在 Tools V1 本分卷完成。
```

### 分卷完成标准

```text
阅读、编辑、终端、预览和删除具备正式实现。
文件操作全部限制在 workspace。
write/patch/delete 的动作类型由结构化字段声明，不靠工具猜测。
普通命令与复杂 shell 有清晰通道。
辅助工具不再以 mock 文本伪装真实能力。
```
