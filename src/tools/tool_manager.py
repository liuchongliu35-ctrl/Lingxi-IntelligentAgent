from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from src.tools.base import ToolResult
from src.tools.code_executor import CodeExecutor
from src.tools.command_tool import CommandTool
from src.tools.document_parser import DocumentParser
from src.tools.file_tools.deletion import DeleteFileTool
from src.tools.file_tools.find import FindFilesTool
from src.tools.file_tools.listing import FileInfoTool, ListFilesTool
from src.tools.file_tools.mutation import CopyFileTool, MoveFileTool, RenameFileTool
from src.tools.file_tools.patching import PatchFileTool
from src.tools.file_tools.reading import (
    ReadFileChunkTool,
    ReadFileHeadTool,
    ReadFileLimits,
    ReadFileTailTool,
    ReadFileTool,
)
from src.tools.file_tools.writing import WriteFileTool
from src.tools.math_calculator import MathCalculator
from src.tools.shell_command_tool import ShellCommandTool
from src.tools.text_processor import TextProcessor
from src.tools.time_query import TimeQuery
from src.tools.translator import Translator
from src.tools.web_search.tool import WebSearchTool
from src.tools.mcp.gateway import MCPToolGateway
from src.tools.policy import ToolPolicy
from src.tools.protocol import ToolCallRequest
from src.tools.registry import ToolRegistry, ToolSpec, build_default_tool_registry
from src.tools.runtime import ToolRuntime
from src.tools.config import (
    ToolsConfig,
    ToolsConfigError,
    default_tools_config,
    load_tools_config,
)
from src.tools.output_control import OutputController
from src.tools.tool_logger import JsonlToolLogger, ToolLogger


class ToolManager:
    """Facade over the single formal ToolRuntime execution pipeline."""

    def __init__(
        self,
        *,
        tools: Dict[str, Any] | None = None,
        registry: ToolRegistry | None = None,
        policy: ToolPolicy | None = None,
        logger: ToolLogger | None = None,
        output_controller: OutputController | None = None,
        tools_config: ToolsConfig | None = None,
        workspace_root: str | Path | None = None,
        model_manager: Any | None = None,
        mcp_gateway: MCPToolGateway | None = None,
    ):
        self.config_error: ToolsConfigError | None = None
        if tools_config is None:
            try:
                tools_config = load_tools_config(workspace_root)
            except ToolsConfigError as exc:
                # A malformed local file must not make the manager permissive.
                self.config_error = exc
                tools_config = default_tools_config(workspace_root)
        self.tools_config = tools_config
        self.tools: Dict[str, Any] = {
            "document_parser": DocumentParser(),
            "text_processor": TextProcessor(),
            "math_calculator": MathCalculator(),
            "translator": Translator(),
            "time_query": TimeQuery(),
            "web_search": WebSearchTool(
                self.tools_config.providers.get("web_search"),
                model_manager=model_manager,
            ),
            "list_files": ListFilesTool(),
            "file_info": FileInfoTool(),
            "find_files": FindFilesTool(),
            "read_file": ReadFileTool(self._read_file_limits()),
            "read_file_chunk": ReadFileChunkTool(self._read_file_limits()),
            "read_file_head": ReadFileHeadTool(self._read_file_limits()),
            "read_file_tail": ReadFileTailTool(self._read_file_limits()),
            "code_executor": CodeExecutor(),
            "write_file": WriteFileTool(),
            "patch_file": PatchFileTool(),
            "copy_file": CopyFileTool(),
            "move_file": MoveFileTool(),
            "rename_file": RenameFileTool(),
            "delete_file": DeleteFileTool(),
            "command_tool": CommandTool(),
            "shell_command_tool": ShellCommandTool(),
        }
        if tools is not None:
            self.tools = dict(tools)
        self.registry = registry or build_default_tool_registry(include_command_tool=True)
        self.mcp_gateway = mcp_gateway or MCPToolGateway()
        effective_policy = policy or ToolPolicy(
            default_permissions=self.tools_config.policy.default_permissions,
            sensitive_paths=self.tools_config.policy.sensitive_paths,
            blocked_paths=self.tools_config.policy.blocked_paths,
            ignored_paths=self.tools_config.policy.ignored_directories,
            blocked_tools=self.tools_config.policy.blocked_tools,
            blocked_scopes=self.tools_config.policy.blocked_scopes,
            risk_policy=self.tools_config.policy.risk_policy,
        )
        effective_output_controller = output_controller or OutputController(
            max_output_chars=self.tools_config.runtime.max_output_chars,
            max_raw_output_chars=self.tools_config.runtime.max_raw_output_chars,
            max_observation_chars=self.tools_config.runtime.max_observation_chars,
            default_observation_mode=self.tools_config.runtime.default_observation_mode,
        )
        effective_logger = logger or JsonlToolLogger(
            self.tools_config.runtime.logs_path
        )
        self.runtime = ToolRuntime(
            registry=self.registry,
            policy=effective_policy,
            handler_resolver=self.get_tool,
            logger=effective_logger,
            output_controller=effective_output_controller,
            max_timeout_seconds=self.tools_config.runtime.default_timeout_seconds,
            enabled=self.tools_config.runtime.enabled,
        )

    def get_tool(self, tool_name: str) -> Any | None:
        canonical_name = self.registry.resolve_name(tool_name) or tool_name
        handler = self.tools.get(canonical_name)
        if handler is not None:
            return handler
        spec = self.registry.get(canonical_name)
        if spec is not None and spec.metadata.get("source_type") == "mcp":
            return self.mcp_gateway.handler_for(spec)
        return None

    def list_tools(self) -> Dict[str, str]:
        return {
            "document_parser": "Parse workspace documents into structured text and table data.",
            "text_processor": "Rule-based text formatting, keyword extraction, and simple statistics.",
            "math_calculator": "Calculate expressions and simple statistics.",
            "translator": "Compatibility translation placeholder; not model-backed.",
            "time_query": "Current time and date conversion.",
            "web_search": "Search the web through configured providers and return structured evidence.",
            "list_files": "List workspace directory entries without reading file contents.",
            "file_info": "Return bounded metadata for a workspace file or directory.",
            "find_files": "Find workspace files by name and bounded text content.",
            "read_file": "Read ordinary workspace text files within configured limits.",
            "read_file_chunk": "Read a bounded 1-based line range from a workspace text file.",
            "read_file_head": "Read the first bounded lines from a workspace text file.",
            "read_file_tail": "Read the last bounded lines from a workspace text file.",
            "code_executor": "Disabled code execution protocol shell.",
            "write_file": "Write complete files inside the workspace with an explicit write mode.",
            "patch_file": "Apply exact local text patches to an existing workspace file.",
            "copy_file": "Copy one explicit workspace file to another workspace file.",
            "move_file": "Move one explicit workspace file to another workspace file.",
            "rename_file": "Rename one workspace file within its current directory.",
            "delete_file": "Delete one or more explicit workspace files.",
            "command_tool": "Execute controlled workspace commands through the Tool layer.",
            "shell_command_tool": "Execute confirmed complex shell commands through an explicit shell channel.",
        }

    def get_registry(self) -> ToolRegistry:
        return self.registry

    def _read_file_limits(self) -> ReadFileLimits:
        return ReadFileLimits(
            small_bytes=self.tools_config.runtime.read_file_small_bytes,
            medium_bytes=self.tools_config.runtime.read_file_medium_bytes,
            hard_bytes=self.tools_config.runtime.read_file_hard_bytes,
            preview_chars=self.tools_config.runtime.read_file_preview_chars,
            range_max_lines=self.tools_config.runtime.read_file_range_max_lines,
        )

    def register_tool_spec(
        self,
        spec: ToolSpec,
        *,
        source: str | None = None,
        aliases: list[str] | None = None,
        handler: Any | None = None,
    ) -> ToolSpec:
        registered = self.registry.register(spec, source=source, aliases=aliases)
        if handler is not None:
            self.tools[registered.name] = handler
        return registered

    def register_tool_handler(self, tool_name: str, handler: Any) -> str:
        canonical_name = self.registry.resolve_name(tool_name)
        if canonical_name is None:
            raise ValueError(f"tool not found: {tool_name}")
        self.tools[canonical_name] = handler
        if canonical_name == "web_search" and "search_tool" in self.registry.list_aliases():
            self.tools["search_tool"] = handler
        return canonical_name

    register_handler = register_tool_handler

    def unregister_tool_spec(self, tool_name: str) -> ToolSpec | None:
        canonical_name = self.registry.resolve_name(tool_name)
        removed = self.registry.unregister(tool_name)
        if canonical_name is not None:
            self.tools.pop(canonical_name, None)
            if canonical_name == "web_search":
                self.tools.pop("search_tool", None)
        return removed

    def execute(self, request: ToolCallRequest) -> ToolResult:
        return self.runtime.execute(request)

    def run_tool(self, tool_name: str, **kwargs: Any) -> ToolResult:
        self._register_legacy_tool_if_needed(tool_name)
        request = ToolCallRequest.from_legacy(tool_name, kwargs)
        return self.execute(request)

    def _register_legacy_tool_if_needed(self, tool_name: str) -> None:
        if self.registry.resolve_name(tool_name) is not None:
            return
        if tool_name not in self.tools:
            return
        self.registry.register(
            ToolSpec(
                name=tool_name,
                description=f"Legacy compatibility tool: {tool_name}",
                parameters_schema={
                    "type": "object",
                    "additionalProperties": True,
                },
                metadata={"legacy_compat": True, "implemented": True},
            )
        )
