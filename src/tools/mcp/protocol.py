from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field as dataclass_field
from enum import Enum
from typing import Any

from ..base import _json_safe
from ..data_types import MCPToolData
from ..errors import ToolErrorCode, is_retryable_code, normalize_error_code


MCP_TRANSPORT_STDIO = "stdio"
MCP_TRANSPORT_STREAMABLE_HTTP = "streamable_http"
MCP_TRANSPORTS = frozenset({MCP_TRANSPORT_STDIO, MCP_TRANSPORT_STREAMABLE_HTTP})
MCP_RISK_LEVELS = frozenset({"low", "medium", "high", "blocked"})
MCP_PROTOCOL_VERSION = "mcp.protocol.v1"
MCP_CLIENT_NAME = "agentProject-tools"
MCP_CLIENT_VERSION = "1.0"
MCP_DEFAULT_WIRE_PROTOCOL_VERSION = "2025-03-26"
MCP_SUPPORTED_WIRE_PROTOCOL_VERSIONS = frozenset(
    {
        "2024-11-05",
        "2025-03-26",
        "2025-06-18",
    }
)
DEFAULT_MCP_TOOL_DESCRIPTION_CHARS = 1200


class MCPServerState(str, Enum):
    CONFIGURED = "configured"
    DISABLED = "disabled"
    STARTING = "starting"
    READY = "ready"
    FAILED = "failed"
    STOPPED = "stopped"
    TOOL_DISCOVERY_FAILED = "tool_discovery_failed"


MCP_SERVER_STATES = frozenset(state.value for state in MCPServerState)

MCP_ERROR_CODES = frozenset(
    {
        ToolErrorCode.MCP_NOT_CONFIGURED.value,
        ToolErrorCode.MCP_SERVER_DISABLED.value,
        ToolErrorCode.MCP_SERVER_NOT_FOUND.value,
        ToolErrorCode.MCP_TRANSPORT_NOT_SUPPORTED.value,
        ToolErrorCode.MCP_COMMAND_NOT_FOUND.value,
        ToolErrorCode.MCP_PROCESS_START_FAILED.value,
        ToolErrorCode.MCP_CONNECTION_FAILED.value,
        ToolErrorCode.MCP_INITIALIZATION_FAILED.value,
        ToolErrorCode.MCP_TOOL_LIST_FAILED.value,
        ToolErrorCode.MCP_TOOL_NOT_FOUND.value,
        ToolErrorCode.MCP_TOOL_NOT_ALLOWED.value,
        ToolErrorCode.MCP_SCHEMA_INVALID.value,
        ToolErrorCode.MCP_INVALID_ARGS.value,
        ToolErrorCode.MCP_TIMEOUT.value,
        ToolErrorCode.MCP_TRANSPORT_ERROR.value,
        ToolErrorCode.MCP_REMOTE_ERROR.value,
        ToolErrorCode.MCP_RESULT_INVALID.value,
        ToolErrorCode.MCP_OUTPUT_TOO_LARGE.value,
        ToolErrorCode.MCP_CONFIRMATION_REQUIRED.value,
        ToolErrorCode.MCP_BLOCKED.value,
        ToolErrorCode.MCP_STDOUT_INVALID_JSON.value,
        ToolErrorCode.MCP_PROCESS_EXITED.value,
    }
)


def normalize_mcp_server_state(value: str | MCPServerState) -> str:
    if isinstance(value, MCPServerState):
        return value.value
    state = str(value or "").strip().lower()
    if state not in MCP_SERVER_STATES:
        raise ValueError(f"Unsupported MCP server state: {value}")
    return state


def normalize_mcp_error_code(value: Any) -> str:
    code = normalize_error_code(value, default=ToolErrorCode.MCP_TRANSPORT_ERROR.value)
    if code not in MCP_ERROR_CODES:
        return ToolErrorCode.MCP_TRANSPORT_ERROR.value
    return code


@dataclass(frozen=True)
class MCPResolvedServerConfig:
    """Runtime-only MCP server values after env/cwd resolution."""

    server_id: str
    display_name: str
    enabled: bool
    transport: str
    command: str | None = None
    args: list[str] = dataclass_field(default_factory=list)
    env: dict[str, str] = dataclass_field(default_factory=dict)
    cwd: str | None = None
    pass_env: bool = False
    allowed_tools: list[str] = dataclass_field(default_factory=list)
    tool_policies: dict[str, Any] = dataclass_field(default_factory=dict)
    default_risk_level: str = "medium"
    timeout_seconds: int = 30
    endpoint_url: str | None = None
    headers: dict[str, str] = dataclass_field(default_factory=dict)
    credential_ref: str | None = None

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "server_id": self.server_id,
            "display_name": self.display_name,
            "enabled": self.enabled,
            "transport": self.transport,
            "command": self.command,
            "args": list(self.args),
            "env": {key: "<resolved>" for key in self.env},
            "cwd": self.cwd,
            "passEnv": self.pass_env,
            "allowed_tools": list(self.allowed_tools),
            "tool_policies": dict(self.tool_policies),
            "default_risk_level": self.default_risk_level,
            "timeout_seconds": self.timeout_seconds,
            "endpoint_url": self.endpoint_url,
            "headers": {key: "<resolved>" for key in self.headers},
            "credential_ref": self.credential_ref,
        }


@dataclass
class MCPProtocolError(Exception):
    code: str
    message: str
    server_id: str | None = None
    remote_tool_name: str | None = None
    retryable: bool | None = None
    details: dict[str, Any] = dataclass_field(default_factory=dict)

    def __post_init__(self) -> None:
        self.code = normalize_mcp_error_code(self.code)
        self.message = str(self.message or self.code)
        self.server_id = _optional_text(self.server_id)
        self.remote_tool_name = _optional_text(self.remote_tool_name)
        self.details = dict(self.details or {})
        if self.retryable is None:
            self.retryable = is_retryable_code(self.code)
        Exception.__init__(self, self.message)

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(
            {
                "code": self.code,
                "message": self.message,
                "server_id": self.server_id,
                "remote_tool_name": self.remote_tool_name,
                "retryable": self.retryable,
                "details": self.details,
            }
        )


@dataclass
class MCPConnectionInfo:
    server_id: str
    state: str = MCPServerState.CONFIGURED.value
    transport: str = MCP_TRANSPORT_STDIO
    pid: int | None = None
    started_at: str | None = None
    stopped_at: str | None = None
    last_error_code: str | None = None
    last_error_message: str | None = None
    protocol_version: str | None = None
    server_name: str | None = None
    server_version: str | None = None
    capabilities: dict[str, Any] = dataclass_field(default_factory=dict)
    metadata: dict[str, Any] = dataclass_field(default_factory=dict)

    def __post_init__(self) -> None:
        self.server_id = _required_text(self.server_id, "server_id")
        self.state = normalize_mcp_server_state(self.state)
        self.transport = _normalize_transport(self.transport)
        if self.pid is not None:
            self.pid = max(int(self.pid), 0)
        self.started_at = _optional_text(self.started_at)
        self.stopped_at = _optional_text(self.stopped_at)
        if self.last_error_code is not None:
            self.last_error_code = normalize_mcp_error_code(self.last_error_code)
        self.last_error_message = _optional_text(self.last_error_message)
        self.protocol_version = _optional_text(self.protocol_version)
        self.server_name = _optional_text(self.server_name)
        self.server_version = _optional_text(self.server_version)
        self.capabilities = dict(self.capabilities or {})
        self.metadata = dict(self.metadata or {})

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(self)


@dataclass
class MCPInitializeResult:
    server_id: str
    protocol_version: str
    capabilities: dict[str, Any] = dataclass_field(default_factory=dict)
    server_name: str | None = None
    server_version: str | None = None
    metadata: dict[str, Any] = dataclass_field(default_factory=dict)

    def __post_init__(self) -> None:
        self.server_id = _required_text(self.server_id, "server_id")
        self.protocol_version = _required_text(self.protocol_version, "protocol_version")
        self.capabilities = dict(self.capabilities or {})
        self.server_name = _optional_text(self.server_name)
        self.server_version = _optional_text(self.server_version)
        self.metadata = dict(self.metadata or {})

    def to_connection_info(
        self,
        *,
        state: str = MCPServerState.READY.value,
        transport: str = MCP_TRANSPORT_STDIO,
        pid: int | None = None,
        started_at: str | None = None,
        stopped_at: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> MCPConnectionInfo:
        return MCPConnectionInfo(
            server_id=self.server_id,
            state=state,
            transport=transport,
            pid=pid,
            started_at=started_at,
            stopped_at=stopped_at,
            protocol_version=self.protocol_version,
            server_name=self.server_name,
            server_version=self.server_version,
            capabilities=dict(self.capabilities),
            metadata={**dict(self.metadata), **dict(metadata or {})},
        )

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(self)


@dataclass
class MCPRemoteTool:
    server_id: str
    name: str
    description: str = ""
    input_schema: dict[str, Any] = dataclass_field(default_factory=dict)
    title: str | None = None
    annotations: dict[str, Any] = dataclass_field(default_factory=dict)
    metadata: dict[str, Any] = dataclass_field(default_factory=dict)

    def __post_init__(self) -> None:
        self.server_id = _required_text(self.server_id, "server_id")
        self.name = _required_text(self.name, "name")
        self.description = str(self.description or "")
        if not isinstance(self.input_schema, dict):
            raise ValueError("input_schema must be an object")
        self.input_schema = dict(self.input_schema or {})
        if self.input_schema.get("type", "object") != "object":
            raise ValueError("input_schema must be an object schema")
        required = self.input_schema.get("required")
        if required is not None and not isinstance(required, list):
            raise ValueError("input_schema.required must be an array")
        self.title = _optional_text(self.title)
        self.annotations = dict(self.annotations or {})
        self.metadata = dict(self.metadata or {})

    @property
    def schema_hash(self) -> str:
        encoded = json.dumps(
            _json_safe(self.input_schema),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        value = _json_safe(self)
        value["schema_hash"] = self.schema_hash
        return value


@dataclass
class MCPToolDiscoveryResult:
    server_id: str
    tools: list[MCPRemoteTool] = dataclass_field(default_factory=list)
    skipped_tools: list[dict[str, Any]] = dataclass_field(default_factory=list)
    raw_tool_count: int = 0
    allowed_tools: list[str] = dataclass_field(default_factory=list)
    refreshed: bool = False
    metadata: dict[str, Any] = dataclass_field(default_factory=dict)

    def __post_init__(self) -> None:
        self.server_id = _required_text(self.server_id, "server_id")
        self.tools = list(self.tools or [])
        for tool in self.tools:
            if not isinstance(tool, MCPRemoteTool):
                raise ValueError("tools must contain MCPRemoteTool objects")
        self.skipped_tools = [dict(item or {}) for item in self.skipped_tools]
        self.raw_tool_count = max(int(self.raw_tool_count or len(self.tools)), 0)
        self.allowed_tools = [str(name) for name in self.allowed_tools]
        self.refreshed = bool(self.refreshed)
        self.metadata = dict(self.metadata or {})

    @property
    def schema_hashes(self) -> dict[str, str]:
        return {tool.name: tool.schema_hash for tool in self.tools}

    def to_dict(self) -> dict[str, Any]:
        value = _json_safe(self)
        value["schema_hashes"] = self.schema_hashes
        return value


@dataclass
class MCPCallRequest:
    server_id: str
    remote_tool_name: str
    arguments: dict[str, Any] = dataclass_field(default_factory=dict)
    timeout_seconds: int | None = None
    trace_id: str | None = None
    metadata: dict[str, Any] = dataclass_field(default_factory=dict)

    def __post_init__(self) -> None:
        self.server_id = _required_text(self.server_id, "server_id")
        self.remote_tool_name = _required_text(self.remote_tool_name, "remote_tool_name")
        if not isinstance(self.arguments, dict):
            raise ValueError("arguments must be an object")
        self.arguments = dict(self.arguments or {})
        if self.timeout_seconds is not None:
            self.timeout_seconds = max(int(self.timeout_seconds), 1)
        self.trace_id = _optional_text(self.trace_id)
        self.metadata = dict(self.metadata or {})

    def to_json_rpc_params(self) -> dict[str, Any]:
        return {
            "name": self.remote_tool_name,
            "arguments": _json_safe(self.arguments),
        }

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(self)


@dataclass
class MCPCallResult:
    success: bool
    server_id: str
    remote_tool_name: str
    content: list[Any] = dataclass_field(default_factory=list)
    structured_content: Any = None
    resource_links: list[Any] = dataclass_field(default_factory=list)
    is_error: bool = False
    stderr_preview: str | None = None
    output_truncated: bool = False
    code: str = ToolErrorCode.OK.value
    error: str | None = None
    retryable: bool = False
    duration_ms: int | None = None
    metadata: dict[str, Any] = dataclass_field(default_factory=dict)

    def __post_init__(self) -> None:
        self.success = bool(self.success)
        self.server_id = _required_text(self.server_id, "server_id")
        self.remote_tool_name = _required_text(self.remote_tool_name, "remote_tool_name")
        self.content = list(self.content or [])
        self.resource_links = list(self.resource_links or [])
        self.is_error = bool(self.is_error)
        self.stderr_preview = _optional_text(self.stderr_preview)
        self.output_truncated = bool(self.output_truncated)
        if self.success and not self.is_error:
            self.code = ToolErrorCode.OK.value
            self.error = None
        else:
            self.code = normalize_mcp_error_code(self.code or ToolErrorCode.MCP_REMOTE_ERROR.value)
            self.error = _optional_text(self.error)
            self.success = False
        if not self.retryable:
            self.retryable = is_retryable_code(self.code)
        if self.duration_ms is not None:
            self.duration_ms = max(int(self.duration_ms), 0)
        self.metadata = dict(self.metadata or {})

    @classmethod
    def ok(
        cls,
        *,
        server_id: str,
        remote_tool_name: str,
        content: list[Any] | None = None,
        structured_content: Any = None,
        resource_links: list[Any] | None = None,
        **kwargs: Any,
    ) -> "MCPCallResult":
        return cls(
            success=True,
            server_id=server_id,
            remote_tool_name=remote_tool_name,
            content=list(content or []),
            structured_content=structured_content,
            resource_links=list(resource_links or []),
            **kwargs,
        )

    @classmethod
    def fail(
        cls,
        *,
        server_id: str,
        remote_tool_name: str,
        code: str,
        error: str,
        content: list[Any] | None = None,
        **kwargs: Any,
    ) -> "MCPCallResult":
        return cls(
            success=False,
            server_id=server_id,
            remote_tool_name=remote_tool_name,
            code=code,
            error=error,
            content=list(content or []),
            is_error=True,
            **kwargs,
        )

    def to_tool_data(self, *, max_content_chars: int | None = None) -> MCPToolData:
        data = MCPToolData(
            server_id=self.server_id,
            remote_tool_name=self.remote_tool_name,
            content=list(self.content),
            structured_content=self.structured_content,
            resource_links=list(self.resource_links),
            is_error=self.is_error,
            stderr_preview=self.stderr_preview,
            output_truncated=self.output_truncated,
            metadata={
                **dict(self.metadata),
                "code": self.code,
                "duration_ms": self.duration_ms,
                "protocol_version": MCP_PROTOCOL_VERSION,
            },
        )
        return truncate_mcp_tool_data(data, max_content_chars=max_content_chars)

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(self)


def truncate_mcp_tool_data(
    data: MCPToolData,
    *,
    max_content_chars: int | None,
) -> MCPToolData:
    if max_content_chars is None:
        return data
    limit = max(int(max_content_chars), 0)
    payload = data.to_dict()
    if _json_chars(payload) <= limit:
        return data
    data.output_truncated = True
    data.metadata["output_limit_chars"] = limit
    data.metadata["output_limit_applied"] = True
    if limit <= 0:
        data.content = []
        data.structured_content = None
        data.resource_links = []
        return data
    while data.content and _json_chars(data.to_dict()) > limit:
        data.content.pop()
    if _json_chars(data.to_dict()) > limit:
        data.structured_content = None
    if _json_chars(data.to_dict()) > limit:
        data.resource_links = []
    if _json_chars(data.to_dict()) > limit:
        data.content = []
    return data


def _normalize_transport(value: Any) -> str:
    transport = str(value or MCP_TRANSPORT_STDIO).strip().lower()
    if transport not in MCP_TRANSPORTS:
        raise ValueError(f"Unsupported MCP transport: {value}")
    return transport


def _required_text(value: Any, field_name: str) -> str:
    text = _optional_text(value)
    if text is None:
        raise ValueError(f"{field_name} must be a non-empty string")
    return text


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _json_chars(value: Any) -> int:
    return len(json.dumps(_json_safe(value), ensure_ascii=False, sort_keys=True, default=str))


__all__ = [
    "DEFAULT_MCP_TOOL_DESCRIPTION_CHARS",
    "MCP_CLIENT_NAME",
    "MCP_CLIENT_VERSION",
    "MCP_ERROR_CODES",
    "MCP_PROTOCOL_VERSION",
    "MCP_RISK_LEVELS",
    "MCP_SERVER_STATES",
    "MCP_DEFAULT_WIRE_PROTOCOL_VERSION",
    "MCP_SUPPORTED_WIRE_PROTOCOL_VERSIONS",
    "MCP_TRANSPORTS",
    "MCP_TRANSPORT_STDIO",
    "MCP_TRANSPORT_STREAMABLE_HTTP",
    "MCPCallRequest",
    "MCPCallResult",
    "MCPConnectionInfo",
    "MCPInitializeResult",
    "MCPProtocolError",
    "MCPRemoteTool",
    "MCPResolvedServerConfig",
    "MCPServerState",
    "MCPToolDiscoveryResult",
    "MCPToolData",
    "normalize_mcp_error_code",
    "normalize_mcp_server_state",
    "truncate_mcp_tool_data",
]
