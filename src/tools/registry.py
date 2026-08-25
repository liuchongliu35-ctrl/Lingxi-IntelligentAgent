from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from .base import _json_safe
from .errors import (
    ToolErrorCode,
    error_type_for_code,
    is_retryable_code,
    normalize_error_code,
)


TOOL_RISK_LEVELS = {"low", "medium", "high", "blocked"}
WORKSPACE_SCOPES = {
    "none",
    "read_workspace",
    "write_workspace",
    "network",
    "command",
    "shell_command",
    "code_execution",
    "mcp",
}
OBSERVATION_MODES = {"minimal", "standard", "full"}


@dataclass
class ToolValidationResult:
    success: bool
    tool_name: str
    canonical_tool_name: str | None = None
    code: str = ToolErrorCode.OK.value
    errors: List[str] = field(default_factory=list)
    missing_params: List[str] = field(default_factory=list)
    unknown_params: List[str] = field(default_factory=list)
    normalized_args: Dict[str, Any] = field(default_factory=dict)
    error_type: str | None = None
    retryable: bool = False

    def __post_init__(self) -> None:
        self.code = normalize_error_code(self.code)
        if self.canonical_tool_name is None and self.success:
            self.canonical_tool_name = self.tool_name
        self.errors = list(self.errors)
        self.missing_params = list(self.missing_params)
        self.unknown_params = list(self.unknown_params)
        self.normalized_args = dict(self.normalized_args)
        self.error_type = error_type_for_code(self.code) or None
        self.retryable = is_retryable_code(self.code)

    def to_dict(self) -> Dict[str, Any]:
        return _json_safe(self)


@dataclass(init=False)
class ToolSpec:
    name: str
    description: str
    category: str = "general"
    namespace: str = "builtin"
    parameters_schema: Dict[str, Any] = field(default_factory=dict)
    required_params: List[str] = field(default_factory=list)
    required_any_of: List[List[str]] = field(default_factory=list)
    returns_schema: Dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    risk_level: str = "low"
    requires_confirmation: bool = False
    workspace_scope: str = "none"
    timeout_seconds: int = 10
    max_output_chars: int | None = None
    default_observation_mode: str = "standard"
    supports_dry_run: bool = False
    fallback_tools: List[str] = field(default_factory=list)
    aliases: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __init__(
        self,
        name: str,
        description: str,
        parameters_schema: Dict[str, Any] | None = None,
        required_params: List[str] | None = None,
        returns_schema: Dict[str, Any] | None = None,
        risk_level: str = "low",
        requires_confirmation: bool = False,
        workspace_scope: str = "none",
        timeout_seconds: int = 10,
        fallback_tools: List[str] | None = None,
        category: str = "general",
        required_any_of: List[List[str]] | None = None,
        metadata: Dict[str, Any] | None = None,
        *,
        namespace: str = "builtin",
        enabled: bool = True,
        max_output_chars: int | None = None,
        default_observation_mode: str = "standard",
        supports_dry_run: bool = False,
        aliases: List[str] | None = None,
        timeout: int | None = None,
    ) -> None:
        # Keep the old positional order and timeout= keyword usable during migration.
        if timeout is not None:
            if timeout_seconds != 10 and int(timeout_seconds) != int(timeout):
                raise ValueError("timeout and timeout_seconds must match when both are provided")
            timeout_seconds = timeout

        self.name = str(name).strip()
        self.description = str(description or "")
        self.category = str(category or "general").strip() or "general"
        self.namespace = str(namespace or "builtin").strip() or "builtin"
        self.parameters_schema = _copy_mapping(parameters_schema, "parameters_schema")
        self.required_params = _copy_string_list(required_params)
        self.required_any_of = [
            _copy_string_list(group)
            for group in (required_any_of or [])
        ]
        self.returns_schema = _copy_mapping(returns_schema, "returns_schema")
        self.enabled = bool(enabled)
        self.risk_level = risk_level
        self.requires_confirmation = bool(requires_confirmation)
        self.workspace_scope = workspace_scope
        self.timeout_seconds = timeout_seconds
        self.max_output_chars = max_output_chars
        self.default_observation_mode = default_observation_mode
        self.supports_dry_run = bool(supports_dry_run)
        self.fallback_tools = _copy_string_list(fallback_tools)
        self.aliases = _copy_string_list(aliases)
        self.metadata = _copy_mapping(metadata, "metadata")
        self.__post_init__()

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("ToolSpec.name must be a non-empty string")
        if self.risk_level not in TOOL_RISK_LEVELS:
            self.risk_level = "medium"
        if self.workspace_scope not in WORKSPACE_SCOPES:
            self.workspace_scope = "none"
        self.timeout_seconds = max(int(self.timeout_seconds), 1)
        if self.max_output_chars is not None:
            self.max_output_chars = max(int(self.max_output_chars), 0)
        if self.default_observation_mode not in OBSERVATION_MODES:
            self.default_observation_mode = "standard"

    @property
    def timeout(self) -> int:
        """Legacy read/write alias for timeout_seconds."""
        return self.timeout_seconds

    @timeout.setter
    def timeout(self, value: int) -> None:
        self.timeout_seconds = max(int(value), 1)

    def validate_args(self, args: Dict[str, Any] | None) -> ToolValidationResult:
        if args is None:
            args = {}
        if not isinstance(args, dict):
            return ToolValidationResult(
                success=False,
                tool_name=self.name,
                canonical_tool_name=self.name,
                code=ToolErrorCode.INVALID_ARGS.value,
                errors=["tool args must be object"],
            )
        normalized_args = dict(args)
        errors: List[str] = []
        missing: List[str] = []
        for param in self.required_params:
            if not _has_value(args.get(param)):
                missing.append(param)
        for group in self.required_any_of:
            if not any(_has_value(args.get(param)) for param in group):
                errors.append(f"one of {', '.join(group)} is required")

        properties = self.parameters_schema.get("properties", {})
        unknown_params = [param for param in args if param not in properties]
        additional_properties = self.parameters_schema.get("additionalProperties", True)
        if unknown_params and additional_properties is False:
            errors.append(f"unknown parameters: {', '.join(unknown_params)}")
        for param, value in args.items():
            schema = properties.get(param)
            if not schema:
                continue
            expected_type = schema.get("type")
            if expected_type and not _matches_json_type(value, expected_type):
                errors.append(f"{param} must be {expected_type}")
            enum_values = schema.get("enum")
            if isinstance(enum_values, list) and enum_values and value not in enum_values:
                errors.append(f"{param} must be one of: {', '.join(str(item) for item in enum_values)}")

        errors.extend(f"{param} is required" for param in missing)
        if missing or any(" is required" in error for error in errors):
            code = ToolErrorCode.MISSING_REQUIRED_PARAM.value
        elif errors:
            code = ToolErrorCode.INVALID_ARGS.value
        else:
            code = ToolErrorCode.OK.value
        return ToolValidationResult(
            success=not errors,
            tool_name=self.name,
            canonical_tool_name=self.name,
            code=code,
            errors=errors,
            missing_params=missing,
            unknown_params=unknown_params,
            normalized_args=normalized_args,
        )

    def to_dict(self) -> Dict[str, Any]:
        return _json_safe(self)

    def to_model_spec(self) -> Dict[str, Any]:
        parameters_schema = self.metadata.get("model_parameters_schema")
        if not isinstance(parameters_schema, dict):
            parameters_schema = self.parameters_schema
        required_params = self.metadata.get("model_required_params")
        if not isinstance(required_params, list):
            required_params = self.required_params
        required_any_of = self.metadata.get("model_required_any_of")
        if not isinstance(required_any_of, list):
            required_any_of = self.required_any_of
        return _json_safe({
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "namespace": self.namespace,
            "parameters_schema": parameters_schema,
            "required_params": list(required_params),
            "required_any_of": [list(group) for group in required_any_of],
            "returns_schema": self.returns_schema,
            "risk_level": self.risk_level,
            "requires_confirmation": self.requires_confirmation,
            "workspace_scope": self.workspace_scope,
            "timeout_seconds": self.timeout_seconds,
            "max_output_chars": self.max_output_chars,
            "default_observation_mode": self.default_observation_mode,
            "supports_dry_run": self.supports_dry_run,
        })


class ToolRegistry:
    def __init__(self, specs: List[ToolSpec] | None = None):
        self._specs: Dict[str, ToolSpec] = {}
        self._aliases: Dict[str, str] = {}
        self._dynamic_sources: Dict[str, set[str]] = {}
        for spec in specs or []:
            self.register(spec)

    def register(
        self,
        spec: ToolSpec,
        *,
        source: str | None = None,
        aliases: List[str] | None = None,
    ) -> ToolSpec:
        if not isinstance(spec, ToolSpec):
            raise TypeError("spec must be ToolSpec")
        canonical_name = spec.name
        if canonical_name in self._specs:
            raise ValueError(f"tool already registered: {canonical_name}")
        if canonical_name in self._aliases:
            raise ValueError(f"canonical tool name conflicts with alias: {canonical_name}")

        requested_aliases = _unique_names([*spec.aliases, *(aliases or [])])
        for alias in requested_aliases:
            if alias == canonical_name:
                raise ValueError("tool alias must differ from canonical name")
            if alias in self._specs:
                raise ValueError(f"tool alias conflicts with canonical name: {alias}")
            if alias in self._aliases:
                raise ValueError(f"tool alias already registered: {alias}")

        spec.aliases = requested_aliases
        self._specs[spec.name] = spec
        for alias in requested_aliases:
            self._aliases[alias] = canonical_name

        resolved_source = source or _infer_dynamic_source(spec)
        if resolved_source:
            self._dynamic_sources.setdefault(resolved_source, set()).add(canonical_name)
        return spec

    def unregister(self, tool_name: str) -> ToolSpec | None:
        canonical_name = self.resolve_name(tool_name)
        if canonical_name is None:
            return None
        spec = self._specs.pop(canonical_name)
        for alias, target in list(self._aliases.items()):
            if target == canonical_name:
                del self._aliases[alias]
        for source, names in list(self._dynamic_sources.items()):
            names.discard(canonical_name)
            if not names:
                del self._dynamic_sources[source]
        return spec

    def register_alias(self, alias: str, canonical_tool_name: str) -> str:
        alias = _normalize_name(alias, "alias")
        canonical_name = self.resolve_name(canonical_tool_name)
        if canonical_name is None:
            raise ValueError(f"canonical tool not found: {canonical_tool_name}")
        if alias == canonical_name:
            raise ValueError("tool alias must differ from canonical name")
        if alias in self._specs:
            raise ValueError(f"tool alias conflicts with canonical name: {alias}")
        if alias in self._aliases:
            raise ValueError(f"tool alias already registered: {alias}")
        self._aliases[alias] = canonical_name
        spec = self._specs[canonical_name]
        if alias not in spec.aliases:
            spec.aliases.append(alias)
        return canonical_name

    def resolve_name(self, tool_name: str) -> str | None:
        if not isinstance(tool_name, str):
            return None
        normalized_name = tool_name.strip()
        if normalized_name in self._specs:
            return normalized_name
        return self._aliases.get(normalized_name)

    def get(self, tool_name: str) -> ToolSpec | None:
        canonical_name = self.resolve_name(tool_name)
        return self._specs.get(canonical_name) if canonical_name else None

    def has_tool(self, tool_name: str) -> bool:
        return self.get(tool_name) is not None

    def list_tools(self) -> Dict[str, str]:
        return {name: spec.description for name, spec in self._specs.items()}

    def list_specs(self) -> List[ToolSpec]:
        return list(self._specs.values())

    def tool_names(self) -> List[str]:
        return list(self._specs.keys())

    def list_aliases(self) -> Dict[str, str]:
        return dict(self._aliases)

    def list_dynamic_sources(self) -> Dict[str, List[str]]:
        return {
            source: sorted(names)
            for source, names in self._dynamic_sources.items()
        }

    def remove_dynamic_source(self, source: str) -> List[str]:
        names = sorted(self._dynamic_sources.get(source, set()))
        for name in names:
            self.unregister(name)
        return names

    def validate_tool_args(self, tool_name: str, args: Dict[str, Any] | None) -> ToolValidationResult:
        canonical_name = self.resolve_name(tool_name)
        if canonical_name is None:
            return ToolValidationResult(
                success=False,
                tool_name=tool_name,
                code=ToolErrorCode.TOOL_NOT_FOUND.value,
                errors=[f"tool not found: {tool_name}"],
            )
        spec = self._specs[canonical_name]
        if not spec.enabled:
            return ToolValidationResult(
                success=False,
                tool_name=tool_name,
                canonical_tool_name=canonical_name,
                code=ToolErrorCode.TOOL_DISABLED.value,
                errors=[f"tool disabled: {canonical_name}"],
            )

        result = spec.validate_args(args)
        result.tool_name = tool_name
        result.canonical_tool_name = canonical_name
        return result

    def to_model_specs(self) -> List[Dict[str, Any]]:
        return [
            spec.to_model_spec()
            for spec in self.list_specs()
            if spec.enabled and spec.metadata.get("implemented", True) is not False
        ]

    def to_dict(self) -> Dict[str, Any]:
        return {"tools": [spec.to_dict() for spec in self.list_specs()]}


def build_default_tool_registry(tool_manager: Any | None = None, *, include_command_tool: bool = False) -> ToolRegistry:
    specs = _default_tool_specs(include_command_tool=include_command_tool)
    if tool_manager is None or not hasattr(tool_manager, "list_tools"):
        return ToolRegistry(specs)

    available = tool_manager.list_tools()
    registry = ToolRegistry()
    for spec in specs:
        if spec.name not in available and not any(alias in available for alias in spec.aliases):
            continue
        registry.register(spec)
    return registry


def _default_tool_specs(*, include_command_tool: bool) -> List[ToolSpec]:
    specs = [
        ToolSpec(
            name="math_calculator",
            description="Calculate expressions and simple statistics.",
            category="calculation",
            parameters_schema={
                "type": "object",
                "properties": {
                    "expression": {"type": "string"},
                    "data": {"type": "array"},
                    "operation": {"type": "string"},
                },
                "additionalProperties": False,
            },
            required_any_of=[["expression", "data"]],
            returns_schema={"type": "object"},
            risk_level="low",
            workspace_scope="none",
            timeout_seconds=5,
            metadata={"implemented": True},
        ),
        ToolSpec(
            name="document_parser",
            description="Parse workspace documents into structured text and table data.",
            category="read",
            parameters_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "file_path": {"type": "string"},
                    "max_pages": {"type": "integer"},
                    "max_chars": {"type": "integer"},
                    "include_metadata": {"type": "boolean"},
                },
                "additionalProperties": False,
            },
            required_any_of=[["path", "file_path"]],
            returns_schema={"type": "object"},
            risk_level="medium",
            workspace_scope="read_workspace",
            timeout_seconds=20,
            metadata={
                "implemented": True,
                "allow_sensitive_read_with_confirmation": True,
                "model_parameters_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "max_pages": {"type": "integer"},
                        "max_chars": {"type": "integer"},
                        "include_metadata": {"type": "boolean"},
                    },
                    "additionalProperties": False,
                },
                "model_required_params": ["path"],
                "model_required_any_of": [],
            },
        ),
        ToolSpec(
            name="text_processor",
            description="Rule-based text formatting, keyword extraction, and simple statistics.",
            category="text",
            parameters_schema={
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "operation": {"type": "string"},
                    "max_length": {"type": "integer"},
                    "top_n": {"type": "integer"},
                },
                "additionalProperties": False,
            },
            required_params=["text"],
            returns_schema={"type": "object"},
            risk_level="low",
            workspace_scope="none",
            timeout_seconds=10,
            metadata={"implemented": True},
        ),
        ToolSpec(
            name="translator",
            description="Compatibility translation placeholder; not model-backed.",
            category="text",
            parameters_schema={
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "source_language": {"type": "string"},
                    "target_language": {"type": "string"},
                },
                "additionalProperties": False,
            },
            required_params=["text", "target_language"],
            returns_schema={"type": "object"},
            risk_level="low",
            workspace_scope="none",
            timeout_seconds=10,
            metadata={"implemented": False, "mock": True},
        ),
        ToolSpec(
            name="time_query",
            description="Current time and date conversion.",
            category="time",
            parameters_schema={
                "type": "object",
                "properties": {
                    "operation": {"type": "string"},
                    "date": {"type": ["string", "null"]},
                    "timezone": {"type": "string"},
                },
                "additionalProperties": False,
            },
            returns_schema={"type": "object"},
            risk_level="low",
            workspace_scope="none",
            timeout_seconds=5,
            metadata={"implemented": True},
        ),
        ToolSpec(
            name="web_search",
            description="Search the web through configured providers and return structured evidence.",
            category="search",
            parameters_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer"},
                    "topic": {"type": "string"},
                    "search_depth": {
                        "type": "string",
                        "enum": ["basic", "advanced"],
                    },
                    "time_range": {"type": "string"},
                    "start_date": {"type": "string"},
                    "end_date": {"type": "string"},
                    "include_answer": {"type": "boolean"},
                    "include_raw_content": {"type": "boolean"},
                    "include_domains": {"type": "array"},
                    "exclude_domains": {"type": "array"},
                    "provider": {
                        "type": "string",
                        "enum": ["auto", "search_api", "model_builtin", "fake", "disabled"],
                    },
                    "observation_mode": {
                        "type": "string",
                        "enum": ["minimal", "standard", "full"],
                    },
                },
                "additionalProperties": False,
            },
            required_params=["query"],
            returns_schema={"type": "object"},
            risk_level="medium",
            workspace_scope="network",
            timeout_seconds=30,
            default_observation_mode="standard",
            supports_dry_run=True,
            aliases=["search_tool"],
            metadata={
                "implemented": True,
                "preview_kind": "web_search",
                "provider": "auto",
                "auto_order": ["search_api", "model_builtin"],
                "model_parameters_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "max_results": {"type": "integer"},
                        "topic": {"type": "string"},
                        "search_depth": {
                            "type": "string",
                            "enum": ["basic", "advanced"],
                        },
                        "time_range": {"type": "string"},
                        "start_date": {"type": "string"},
                        "end_date": {"type": "string"},
                        "include_answer": {"type": "boolean"},
                        "include_raw_content": {"type": "boolean"},
                        "include_domains": {"type": "array"},
                        "exclude_domains": {"type": "array"},
                        "provider": {
                            "type": "string",
                            "enum": ["auto", "search_api", "model_builtin", "fake", "disabled"],
                        },
                        "observation_mode": {
                            "type": "string",
                            "enum": ["minimal", "standard", "full"],
                        },
                    },
                    "additionalProperties": False,
                },
                "model_required_params": ["query"],
                "model_required_any_of": [],
            },
        ),
        ToolSpec(
            name="list_files",
            description="List workspace directory entries without reading file contents.",
            category="read",
            parameters_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "recursive": {"type": "boolean"},
                    "max_entries": {"type": "integer"},
                    "include_hidden": {"type": "boolean"},
                },
            },
            returns_schema={"type": "object"},
            risk_level="low",
            workspace_scope="read_workspace",
            timeout_seconds=10,
            metadata={"implemented": True},
        ),
        ToolSpec(
            name="file_info",
            description="Return bounded metadata for a workspace file or directory.",
            category="read",
            parameters_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "include_hash": {"type": "boolean"},
                },
            },
            required_params=["path"],
            returns_schema={"type": "object"},
            risk_level="low",
            workspace_scope="read_workspace",
            timeout_seconds=10,
            metadata={
                "implemented": True,
                "allow_sensitive_metadata": True,
            },
        ),
        ToolSpec(
            name="find_files",
            description="Find workspace files by name and bounded text content.",
            category="read",
            parameters_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "name_pattern": {"type": "string"},
                    "text_pattern": {"type": "string"},
                    "case_sensitive": {"type": "boolean"},
                    "max_results": {"type": "integer"},
                },
            },
            required_any_of=[["name_pattern", "text_pattern"]],
            returns_schema={"type": "object"},
            risk_level="low",
            workspace_scope="read_workspace",
            timeout_seconds=20,
            metadata={"implemented": True},
        ),
        ToolSpec(
            name="read_file",
            description="Read ordinary workspace text files within configured limits.",
            category="read",
            parameters_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "encoding": {"type": "string"},
                    "max_bytes": {"type": "integer"},
                    "observation_mode": {"type": "string"},
                },
            },
            required_params=["path"],
            returns_schema={"type": "object"},
            risk_level="low",
            workspace_scope="read_workspace",
            timeout_seconds=20,
            metadata={
                "implemented": True,
                "allow_sensitive_read_with_confirmation": True,
            },
        ),
        ToolSpec(
            name="read_file_chunk",
            description="Read a bounded 1-based line range from a workspace text file.",
            category="read",
            parameters_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer"},
                    "line_count": {"type": "integer"},
                    "encoding": {"type": "string"},
                },
            },
            required_params=["path", "start_line", "line_count"],
            returns_schema={"type": "object"},
            risk_level="low",
            workspace_scope="read_workspace",
            timeout_seconds=20,
            metadata={
                "implemented": True,
                "allow_sensitive_read_with_confirmation": True,
            },
        ),
        ToolSpec(
            name="read_file_head",
            description="Read the first bounded lines from a workspace text file.",
            category="read",
            parameters_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "line_count": {"type": "integer"},
                    "encoding": {"type": "string"},
                },
            },
            required_params=["path", "line_count"],
            returns_schema={"type": "object"},
            risk_level="low",
            workspace_scope="read_workspace",
            timeout_seconds=20,
            metadata={
                "implemented": True,
                "allow_sensitive_read_with_confirmation": True,
            },
        ),
        ToolSpec(
            name="read_file_tail",
            description="Read the last bounded lines from a workspace text file.",
            category="read",
            parameters_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "line_count": {"type": "integer"},
                    "encoding": {"type": "string"},
                },
            },
            required_params=["path", "line_count"],
            returns_schema={"type": "object"},
            risk_level="low",
            workspace_scope="read_workspace",
            timeout_seconds=20,
            metadata={
                "implemented": True,
                "allow_sensitive_read_with_confirmation": True,
            },
        ),
        ToolSpec(
            name="code_executor",
            description="Disabled code execution protocol shell.",
            category="code",
            parameters_schema={
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                    "timeout": {"type": "integer"},
                },
                "additionalProperties": False,
            },
            required_params=["code"],
            returns_schema={"type": "object"},
            enabled=False,
            risk_level="high",
            requires_confirmation=True,
            workspace_scope="code_execution",
            timeout_seconds=10,
            metadata={"implemented": False},
        ),
        ToolSpec(
            name="write_file",
            description="Write complete files inside the workspace with an explicit write mode.",
            category="write",
            parameters_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "write_mode": {
                        "type": "string",
                        "enum": ["create", "overwrite", "append", "create_or_overwrite"],
                    },
                    "encoding": {"type": "string"},
                    "file_path": {"type": "string"},
                    "overwrite": {"type": "boolean"},
                },
                "additionalProperties": False,
            },
            required_params=["content"],
            required_any_of=[["path", "file_path"]],
            returns_schema={"type": "object"},
            risk_level="medium",
            requires_confirmation=False,
            workspace_scope="write_workspace",
            timeout_seconds=10,
            supports_dry_run=True,
            aliases=["file_writer"],
            metadata={
                "implemented": True,
                "preview_kind": "file_write",
                "model_parameters_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                        "write_mode": {
                            "type": "string",
                            "enum": [
                                "create",
                                "overwrite",
                                "append",
                                "create_or_overwrite",
                            ],
                        },
                        "encoding": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
                "model_required_params": ["path", "content", "write_mode"],
                "model_required_any_of": [],
                "risk_by_arg": {
                    "write_mode": {
                        "create": "medium",
                        "append": "medium",
                        "overwrite": "high",
                        "create_or_overwrite": "high",
                    },
                    "overwrite": {
                        True: "high",
                    },
                },
            },
        ),
        ToolSpec(
            name="patch_file",
            description="Apply exact local text patches to an existing workspace file.",
            category="write",
            parameters_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "patches": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "operation": {
                                    "type": "string",
                                    "enum": [
                                        "replace",
                                        "insert_before",
                                        "insert_after",
                                        "delete_block",
                                    ],
                                },
                                "old_text": {"type": "string"},
                                "new_text": {"type": "string"},
                                "occurrence": {"type": "integer"},
                                "line_start": {"type": "integer"},
                                "line_end": {"type": "integer"},
                                "anchor_before": {"type": "string"},
                                "anchor_after": {"type": "string"},
                            },
                        },
                    },
                    "encoding": {"type": "string"},
                },
                "additionalProperties": False,
            },
            required_params=["path", "patches"],
            returns_schema={"type": "object"},
            risk_level="high",
            requires_confirmation=True,
            workspace_scope="write_workspace",
            timeout_seconds=20,
            supports_dry_run=True,
            metadata={
                "implemented": True,
                "preview_kind": "file_patch",
            },
        ),
        ToolSpec(
            name="copy_file",
            description="Copy one explicit workspace file to another workspace file.",
            category="write",
            parameters_schema={
                "type": "object",
                "properties": {
                    "source_path": {"type": "string"},
                    "target_path": {"type": "string"},
                    "overwrite": {"type": "boolean"},
                },
                "additionalProperties": False,
            },
            required_params=["source_path", "target_path"],
            returns_schema={"type": "object"},
            risk_level="medium",
            requires_confirmation=False,
            workspace_scope="write_workspace",
            timeout_seconds=20,
            supports_dry_run=True,
            metadata={
                "implemented": True,
                "preview_kind": "file_mutation",
                "mutation_operation": "copy",
                "risk_by_arg": {
                    "overwrite": {
                        True: "high",
                    },
                },
            },
        ),
        ToolSpec(
            name="move_file",
            description="Move one explicit workspace file to another workspace file.",
            category="write",
            parameters_schema={
                "type": "object",
                "properties": {
                    "source_path": {"type": "string"},
                    "target_path": {"type": "string"},
                    "overwrite": {"type": "boolean"},
                },
                "additionalProperties": False,
            },
            required_params=["source_path", "target_path"],
            returns_schema={"type": "object"},
            risk_level="high",
            requires_confirmation=True,
            workspace_scope="write_workspace",
            timeout_seconds=20,
            supports_dry_run=True,
            metadata={
                "implemented": True,
                "preview_kind": "file_mutation",
                "mutation_operation": "move",
            },
        ),
        ToolSpec(
            name="rename_file",
            description="Rename one workspace file within its current directory.",
            category="write",
            parameters_schema={
                "type": "object",
                "properties": {
                    "source_path": {"type": "string"},
                    "new_name": {"type": "string"},
                    "overwrite": {"type": "boolean"},
                },
                "additionalProperties": False,
            },
            required_params=["source_path", "new_name"],
            returns_schema={"type": "object"},
            risk_level="high",
            requires_confirmation=True,
            workspace_scope="write_workspace",
            timeout_seconds=20,
            supports_dry_run=True,
            metadata={
                "implemented": True,
                "preview_kind": "file_mutation",
                "mutation_operation": "rename",
            },
        ),
        ToolSpec(
            name="delete_file",
            description="Delete one or more explicit workspace files.",
            category="delete",
            parameters_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "file_paths": {"type": "array"},
                },
                "additionalProperties": False,
            },
            required_any_of=[["path", "file_paths"]],
            returns_schema={"type": "object"},
            risk_level="high",
            requires_confirmation=True,
            workspace_scope="write_workspace",
            timeout_seconds=20,
            supports_dry_run=True,
            metadata={
                "implemented": True,
                "preview_kind": "file_delete",
            },
        ),
    ]
    if include_command_tool:
        specs.append(
            ToolSpec(
                name="command_tool",
                description="Execute controlled workspace commands through the Tool layer.",
                category="command",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "program": {"type": "string"},
                        "args": {"type": "array"},
                        "command": {"type": "string"},
                        "cwd": {"type": "string"},
                        "purpose": {"type": "string"},
                        "risk_level": {"type": "string"},
                        "requires_confirmation": {"type": "boolean"},
                        "expected_result": {"type": "string"},
                        "timeout_seconds": {"type": "integer"},
                        "shell": {"type": ["string", "null"]},
                        "env_policy": {"type": "string"},
                        "network_required": {"type": "boolean"},
                        "writes_files": {"type": "boolean"},
                        "target_paths": {"type": "array"},
                        "destructive_risk": {"type": "boolean"},
                        "approval_scope": {"type": ["string", "null"]},
                    },
                    "additionalProperties": False,
                },
                required_params=["cwd", "purpose", "timeout_seconds"],
                required_any_of=[["program", "command"]],
                returns_schema={"type": "object"},
                risk_level="high",
                requires_confirmation=True,
                workspace_scope="command",
                timeout_seconds=30,
                metadata={
                    "implemented": True,
                    "model_parameters_schema": {
                        "type": "object",
                        "properties": {
                            "program": {"type": "string"},
                            "args": {"type": "array"},
                            "cwd": {"type": "string"},
                            "purpose": {"type": "string"},
                            "timeout_seconds": {"type": "integer"},
                            "network_required": {"type": "boolean"},
                            "writes_files": {"type": "boolean"},
                            "target_paths": {"type": "array"},
                            "command": {"type": "string"},
                            "risk_level": {"type": "string"},
                            "requires_confirmation": {"type": "boolean"},
                            "expected_result": {"type": "string"},
                            "destructive_risk": {"type": "boolean"},
                            "approval_scope": {"type": ["string", "null"]},
                        },
                        "additionalProperties": False,
                    },
                    "model_required_params": ["cwd", "purpose", "timeout_seconds"],
                    "model_required_any_of": [["program", "command"]],
                },
            )
        )
        specs.append(
            ToolSpec(
                name="shell_command_tool",
                description="Execute confirmed complex shell commands through an explicit shell channel.",
                category="command",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                        "shell": {
                            "type": "string",
                            "enum": ["powershell", "cmd", "bash"],
                        },
                        "cwd": {"type": "string"},
                        "purpose": {"type": "string"},
                        "timeout_seconds": {"type": "integer"},
                        "network_required": {"type": "boolean"},
                        "writes_files": {"type": "boolean"},
                        "target_paths": {"type": "array"},
                    },
                    "additionalProperties": False,
                },
                required_params=["command", "cwd", "purpose", "timeout_seconds"],
                returns_schema={"type": "object"},
                risk_level="high",
                requires_confirmation=True,
                workspace_scope="shell_command",
                timeout_seconds=30,
                supports_dry_run=True,
                aliases=["shell_tool"],
                metadata={
                    "implemented": True,
                    "preview_kind": "shell_command",
                },
            )
        )
    return specs


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict, tuple, set)):
        return bool(value)
    return True


def _copy_mapping(value: Dict[str, Any] | None, field_name: str) -> Dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be an object")
    return dict(value)


def _copy_string_list(value: List[str] | None) -> List[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("ToolSpec list fields must be arrays")
    return [str(item).strip() for item in value if str(item).strip()]


def _unique_names(values: List[str]) -> List[str]:
    result: List[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = _normalize_name(value, "tool name")
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _normalize_name(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _infer_dynamic_source(spec: ToolSpec) -> str | None:
    metadata_source = spec.metadata.get("source")
    if isinstance(metadata_source, str) and metadata_source.strip():
        return metadata_source.strip()
    if spec.metadata.get("source_type") == "mcp":
        server_id = spec.metadata.get("server_id")
        if isinstance(server_id, str) and server_id.strip():
            return f"mcp:{server_id.strip()}"
    return None


def _matches_json_type(value: Any, expected_type: str | List[str]) -> bool:
    expected = expected_type if isinstance(expected_type, list) else [expected_type]
    return any(_matches_single_json_type(value, item) for item in expected)


def _matches_single_json_type(value: Any, expected_type: str) -> bool:
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "object":
        return isinstance(value, dict)
    if expected_type == "null":
        return value is None
    return True
