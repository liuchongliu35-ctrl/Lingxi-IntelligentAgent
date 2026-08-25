from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from .base import _json_safe
from .errors import ToolErrorCode, ToolErrorType


class ToolCallSource(str, Enum):
    REACT_EXECUTOR = "react_executor"
    TEST = "test"
    RUNTIME_API = "runtime_api"
    MCP_MANAGER = "mcp_manager"
    INTERNAL = "internal"
    HISTORICAL_EXECUTOR = "historical_executor"


TOOL_CALL_SOURCES = {source.value for source in ToolCallSource}
OBSERVATION_MODES = {"minimal", "standard", "full"}
APPROVAL_SCOPES = {"one_call", "current_step", "session"}
_SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "password",
    "private_key",
    "secret",
    "token",
}

# Re-export the stable error vocabulary alongside the call protocol.
__all__ = [
    "ToolCallContext",
    "ToolCallOptions",
    "ToolCallRequest",
    "ToolCallSource",
    "ToolErrorCode",
    "ToolErrorType",
]


def _normalize_workspace_root(value: str | Path | None) -> str:
    root = Path(value or ".").expanduser()
    return str(root.resolve(strict=False))


def _redact_sensitive(value: Any) -> Any:
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            key_text = str(key)
            normalized_key = key_text.lower().replace("-", "_")
            if normalized_key in _SENSITIVE_KEYS or any(
                marker in normalized_key
                for marker in ("api_key", "access_token", "auth_header", "credential")
            ):
                result[key_text] = "<redacted>"
            else:
                result[key_text] = _redact_sensitive(item)
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_redact_sensitive(item) for item in value]
    return value


@dataclass
class ToolCallContext:
    trace_id: str | None = None
    execution_id: str | None = None
    plan_id: str | None = None
    task_id: str | None = None
    step_id: str | None = None
    packet_id: str | None = None
    session_id: str | None = None
    user_id: str | None = None
    workspace_root: str = "."
    source: str = ToolCallSource.INTERNAL.value
    initiated_by: str = "runtime"

    def __post_init__(self) -> None:
        if isinstance(self.source, ToolCallSource):
            self.source = self.source.value
        if self.source not in TOOL_CALL_SOURCES:
            raise ValueError(f"Unsupported tool call source: {self.source}")
        if not isinstance(self.initiated_by, str) or not self.initiated_by.strip():
            raise ValueError("initiated_by must be a non-empty string")
        self.workspace_root = _normalize_workspace_root(self.workspace_root)

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))


@dataclass
class ToolCallOptions:
    timeout_seconds: int | None = None
    dry_run: bool = False
    require_confirmation: bool | None = None
    confirmed: bool = False
    approval_scope: str | None = None
    confirmation_id: str | None = None
    preview_hash: str | None = None
    approved_at: str | None = None
    approval_source: str | None = None
    allow_read_workspace: bool = True
    allow_write_workspace: bool = False
    allow_network: bool = False
    allow_command: bool = False
    allow_shell_command: bool = False
    allow_mcp: bool = False
    max_output_chars: int | None = None
    max_raw_output_chars: int | None = None
    max_observation_chars: int | None = None
    observation_mode: str | None = None

    def __post_init__(self) -> None:
        if self.timeout_seconds is not None:
            self.timeout_seconds = _positive_or_none(self.timeout_seconds, "timeout_seconds")
        self.max_output_chars = _non_negative_or_none(self.max_output_chars, "max_output_chars")
        self.max_raw_output_chars = _non_negative_or_none(
            self.max_raw_output_chars,
            "max_raw_output_chars",
        )
        self.max_observation_chars = _non_negative_or_none(
            self.max_observation_chars,
            "max_observation_chars",
        )
        if self.approval_scope is not None and self.approval_scope not in APPROVAL_SCOPES:
            raise ValueError(f"Unsupported approval scope: {self.approval_scope}")
        if self.observation_mode is not None and self.observation_mode not in OBSERVATION_MODES:
            raise ValueError(f"Unsupported observation mode: {self.observation_mode}")
        if self.approval_source is not None and not str(self.approval_source).strip():
            raise ValueError("approval_source must be non-empty when provided")

    @property
    def has_confirmation_ticket(self) -> bool:
        return bool(self.confirmed and self.confirmation_id and self.preview_hash)

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))


@dataclass
class ToolCallRequest:
    tool_name: str
    args: dict[str, Any] = field(default_factory=dict)
    context: ToolCallContext = field(default_factory=ToolCallContext)
    options: ToolCallOptions = field(default_factory=ToolCallOptions)

    def __post_init__(self) -> None:
        if not isinstance(self.tool_name, str) or not self.tool_name.strip():
            raise ValueError("tool_name must be a non-empty string")
        if not isinstance(self.args, dict):
            raise ValueError("tool args must be object")
        if not isinstance(self.context, ToolCallContext):
            raise ValueError("context must be ToolCallContext")
        if not isinstance(self.options, ToolCallOptions):
            raise ValueError("options must be ToolCallOptions")
        self.tool_name = self.tool_name.strip()
        self.args = dict(self.args)

    @classmethod
    def from_legacy(
        cls,
        tool_name: str,
        kwargs: Mapping[str, Any] | None = None,
        *,
        context: ToolCallContext | None = None,
        options: ToolCallOptions | None = None,
    ) -> "ToolCallRequest":
        legacy_context = context or ToolCallContext(source=ToolCallSource.HISTORICAL_EXECUTOR.value)
        legacy_options = options or ToolCallOptions()
        return cls(
            tool_name=tool_name,
            args=dict(kwargs or {}),
            context=legacy_context,
            options=legacy_options,
        )

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(
            {
                "tool_name": self.tool_name,
                "args": _redact_sensitive(self.args),
                "context": self.context.to_dict(),
                "options": self.options.to_dict(),
            }
        )


def _positive_or_none(value: Any, field_name: str) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a positive integer") from exc
    if normalized < 1:
        raise ValueError(f"{field_name} must be a positive integer")
    return normalized


def _non_negative_or_none(value: Any, field_name: str) -> int | None:
    if value is None:
        return None
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a non-negative integer") from exc
    if normalized < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return normalized
