from __future__ import annotations

import enum
import json
import uuid
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, Dict, Mapping


def _json_safe(value: Any, seen: set[int] | None = None) -> Any:
    """Convert tool data into a JSON-safe, non-executable representation."""
    if seen is None:
        seen = set()

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, enum.Enum):
        return _json_safe(value.value, seen)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, BaseException):
        return str(value)

    object_id = id(value)
    if object_id in seen:
        return "<circular_reference>"

    if is_dataclass(value) and not isinstance(value, type):
        seen.add(object_id)
        result = {
            field.name: _json_safe(getattr(value, field.name), seen)
            for field in fields(value)
        }
        seen.remove(object_id)
        return result
    if isinstance(value, Mapping):
        seen.add(object_id)
        result = {
            str(key): _json_safe(item, seen)
            for key, item in value.items()
        }
        seen.remove(object_id)
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        seen.add(object_id)
        result = [_json_safe(item, seen) for item in value]
        seen.remove(object_id)
        return result

    return str(value)


def _short_summary(value: Any, limit: int = 600) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(
                _json_safe(value),
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
        except (TypeError, ValueError):
            text = str(value)
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 3)]}..."


def new_tool_call_id() -> str:
    """Provide a conservative compatibility id until ToolRuntime owns identity."""
    return f"tool_call_{uuid.uuid4().hex}"


@dataclass
class ToolResult:
    success: bool
    # Keep the legacy positional order intact during migration.
    data: Any = None
    message: str = ""
    error: str | None = None
    code: str | None = None
    tool_name: str = ""
    tool_category: str = ""
    tool_namespace: str = ""
    error_type: str | None = None
    retryable: bool = False
    provider: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    duration_ms: int | None = None
    trace_id: str | None = None
    execution_id: str | None = None
    step_id: str | None = None
    call_id: str = ""
    raw_output: Any = None
    raw_output_truncated: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.call_id:
            self.call_id = new_tool_call_id()
        self.metadata = dict(self.metadata)

    def to_text(self) -> str:
        if self.success:
            if self.message:
                return _short_summary(self.message)
            return _short_summary(self.data)
        return _short_summary(self.error or self.message or "Tool execution failed")

    def to_dict(self) -> Dict[str, Any]:
        return _json_safe(self)

    @classmethod
    def ok(cls, data: Any = None, message: str = "", **kwargs: Any) -> "ToolResult":
        kwargs.pop("success", None)
        return cls(
            success=True,
            data=data,
            message=message or _short_summary(data),
            **kwargs,
        )

    @classmethod
    def fail(
        cls,
        error: str,
        code: str | None = None,
        data: Any = None,
        **kwargs: Any,
    ) -> "ToolResult":
        kwargs.pop("success", None)
        error_text = str(error)
        kwargs.pop("message", None)
        return cls(
            success=False,
            data=data,
            error=error_text,
            message=error_text,
            code=code,
            **kwargs,
        )
