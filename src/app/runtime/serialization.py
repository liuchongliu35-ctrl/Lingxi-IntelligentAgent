"""安全的 Runtime 对外序列化适配。

本模块只负责把底层对象转换为 CLI/API 可以消费的普通 Python 值。
它不改变 Memory 的 event mapper 规则，也不把 debug 变成隐藏推理开关。
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import date, datetime
from enum import Enum
from math import isfinite
import re
from typing import Any, Mapping

from .contracts import RuntimeEvent, RuntimeResult


REDACTED_VALUE = "***REDACTED***"

SENSITIVE_FIELD_NAMES = frozenset(
    {
        "raw_prompt",
        "full_prompt",
        "hidden_reasoning",
        "raw_tool_result",
        "raw_observation",
        "api_key",
        "token",
        "cookie",
        "password",
        "authorization",
    }
)

# These fields are internal even when they do not contain a credential.
_INTERNAL_FIELD_NAMES = frozenset(
    {
        "action_args",
        "args",
        "arguments",
        "chain_of_thought",
        "command",
        "command_args",
        "cwd",
        "env",
        "environment",
        "exception",
        "input_args",
        "prompt",
        "raw_input_args",
        "raw_model_output",
        "raw_output",
        "raw_reasoning",
        "raw_result",
        "reasoning",
        "stack_trace",
        "target_paths",
        "thought_summary",
        "traceback",
    }
)

DEBUG_FIELD_NAMES = frozenset(
    {
        "analyzer_summary",
        "plan_summary",
        "event_count",
        "model_profile",
        "tool_profile",
    }
)

_SECRET_TEXT_MARKERS = (
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "password",
    "secret",
    "token",
)


def safe_serialize(
    value: Any,
    *,
    debug: bool = False,
    max_depth: int = 8,
    max_items: int = 200,
    max_text_chars: int = 4000,
) -> Any:
    """Convert supported values into safe JSON-compatible Python values.

    Unknown objects are represented by a type-only summary. In particular,
    this function never falls back to ``str(value)`` or ``repr(value)`` for
    arbitrary objects, because those representations may contain secrets or
    executor context.
    """

    return _safe_value(
        value,
        debug=debug,
        depth=0,
        max_depth=max_depth,
        max_items=max_items,
        max_text_chars=max_text_chars,
    )


def serialize_runtime_event(
    event: RuntimeEvent | Mapping[str, Any] | Any,
    *,
    include_invisible: bool = False,
    debug: bool = False,
) -> dict[str, Any] | None:
    """Serialize a RuntimeEvent while respecting its visibility flag."""

    visible = bool(_read_field(event, "visible_to_user", True))
    if not visible and not include_invisible:
        return None

    result = {
        "event_id": _read_field(event, "event_id"),
        "session_id": _read_field(event, "session_id"),
        "run_id": _read_field(event, "run_id"),
        "event_type": _read_field(event, "event_type", _read_field(event, "type")),
        "message": _read_field(event, "message", ""),
        "visible_to_user": visible,
        "payload": _safe_value(
            _read_field(event, "payload", {}),
            debug=debug,
            depth=0,
            max_depth=8,
            max_items=200,
            max_text_chars=4000,
        ),
        "source_event": _safe_value(
            _read_field(event, "source_event"),
            debug=debug,
            depth=0,
            max_depth=8,
            max_items=200,
            max_text_chars=4000,
        ),
        "sequence": _read_field(event, "sequence", 0),
        "created_at": _read_field(event, "created_at"),
    }
    return _safe_value(
        result,
        debug=debug,
        depth=0,
        max_depth=8,
        max_items=200,
        max_text_chars=4000,
    )


def serialize_pending_confirmation(
    pending: Any,
    *,
    debug: bool = False,
) -> dict[str, Any] | None:
    """Return the small public confirmation preview.

    ``pending_action`` is intentionally reduced to safe action identity fields;
    its complete packet and arguments are never serialized.
    """

    if pending is None:
        return None

    pending_action = _read_field(pending, "pending_action")
    action_type = _read_field(pending_action, "action_type")
    action_target = _read_field(pending_action, "action_target")
    action_name = action_target or action_type or _read_field(pending, "confirmation_type")

    preview_summary = _read_field(pending, "preview_summary")
    if preview_summary is None:
        preview_summary = _read_field(pending, "confirmation_message", "")

    result = {
        "confirmation_id": _read_field(pending, "confirmation_id"),
        "preview_hash": _read_field(pending, "preview_hash"),
        "confirmation_type": _read_field(pending, "confirmation_type"),
        "action_name": action_name,
        "action_type": action_type,
        "confirmation_message": _read_field(pending, "confirmation_message", ""),
        "preview_summary": preview_summary,
        "expires_at": _read_field(pending, "expires_at"),
    }

    # Resource names may be useful to a user, but are only included after the
    # same bounded text and secret sanitization as every other public string.
    resources = _read_field(pending, "affected_resources")
    if resources:
        result["affected_resources"] = resources

    return _safe_value(
        result,
        debug=debug,
        depth=0,
        max_depth=4,
        max_items=50,
        max_text_chars=1000,
    )


def serialize_execution_result(
    result: Any,
    *,
    debug: bool = False,
) -> dict[str, Any]:
    """Serialize the public portion of an Executor ExecutionResult."""

    observations = []
    for observation in _read_field(result, "observations", []) or []:
        if not bool(_read_field(observation, "visible_to_user", True)):
            continue
        observations.append(
            _safe_value(
                observation,
                debug=debug,
                depth=0,
                max_depth=8,
                max_items=200,
                max_text_chars=4000,
            )
        )

    events = []
    for event in _read_field(result, "events", []) or []:
        if not bool(_read_field(event, "visible_to_user", True)):
            continue
        events.append(
            _safe_value(
                event,
                debug=debug,
                depth=0,
                max_depth=8,
                max_items=200,
                max_text_chars=4000,
            )
        )

    public_fields = (
        "execution_id",
        "plan_id",
        "status",
        "success",
        "output",
        "source_trace_id",
        "summary",
        "task_statuses",
        "step_statuses",
        "failed_step_id",
        "error_code",
        "requires_user_input",
        "user_input_request",
        "request_replan",
        "replan_reason",
    )
    serialized = {
        name: _safe_value(
            _read_field(result, name),
            debug=debug,
            depth=0,
            max_depth=8,
            max_items=200,
            max_text_chars=4000,
        )
        for name in public_fields
        if _read_field(result, name) is not None
    }
    serialized["observations"] = observations
    serialized["events"] = events
    serialized["pending_confirmation"] = serialize_pending_confirmation(
        _read_field(result, "pending_confirmation"),
        debug=debug,
    )
    return serialized


def serialize_output_feedback(
    feedback: Any,
    *,
    debug: bool = False,
) -> dict[str, Any]:
    """Serialize OutputFeedback without re-opening internal event payloads."""

    result: dict[str, Any] = {}
    for name in (
        "execution_id",
        "plan_id",
        "status",
        "success",
        "final_output",
        "summary",
        "requires_user_input",
        "user_input_request",
        "request_replan",
        "replan_reason",
    ):
        value = _read_field(feedback, name)
        if value is not None:
            result[name] = _safe_value(
                value,
                debug=debug,
                depth=0,
                max_depth=8,
                max_items=200,
                max_text_chars=4000,
            )

    result["pending_confirmation"] = serialize_pending_confirmation(
        _read_field(feedback, "pending_confirmation"),
        debug=debug,
    )
    result["timeline"] = _safe_value(
        _read_field(feedback, "timeline", []) or [],
        debug=debug,
        depth=0,
        max_depth=8,
        max_items=200,
        max_text_chars=4000,
    )
    result["items"] = _safe_value(
        _read_field(feedback, "items", []) or [],
        debug=debug,
        depth=0,
        max_depth=8,
        max_items=200,
        max_text_chars=4000,
    )
    return result


def serialize_memory_result(
    value: Any,
    *,
    debug: bool = False,
) -> Any:
    """Serialize Memory results and models through the common safe walker."""

    return safe_serialize(value, debug=debug)


def serialize_runtime_result(
    result: RuntimeResult | Mapping[str, Any] | Any,
    *,
    debug: bool = False,
) -> dict[str, Any]:
    """Serialize a RuntimeResult and enforce the debug metadata boundary."""

    public_fields = (
        "success",
        "status",
        "session_id",
        "run_id",
        "output",
        "requires_user_input",
        "request_replan",
        "replan_reason",
        "error_code",
        "error_message",
        "persistence_available",
        "persistence_warning",
    )
    serialized = {
        name: _safe_value(
            _read_field(result, name),
            debug=debug,
            depth=0,
            max_depth=8,
            max_items=200,
            max_text_chars=4000,
        )
        for name in public_fields
        if _read_field(result, name) is not None
    }
    serialized["execution_result"] = (
        serialize_execution_result(_read_field(result, "execution_result"), debug=debug)
        if _read_field(result, "execution_result") is not None
        else None
    )
    serialized["output_feedback"] = (
        serialize_output_feedback(_read_field(result, "output_feedback"), debug=debug)
        if _read_field(result, "output_feedback") is not None
        else None
    )
    serialized["memory_result"] = (
        serialize_memory_result(_read_field(result, "memory_result"), debug=debug)
        if _read_field(result, "memory_result") is not None
        else None
    )
    serialized["timeline"] = _safe_value(
        _read_field(result, "timeline", []) or [],
        debug=debug,
        depth=0,
        max_depth=8,
        max_items=200,
        max_text_chars=4000,
    )
    serialized["pending_confirmation"] = serialize_pending_confirmation(
        _read_field(result, "pending_confirmation"),
        debug=debug,
    )
    serialized["metadata"] = serialize_metadata(
        _read_field(result, "metadata", {}),
        debug=debug,
    )
    return serialized


def build_debug_metadata(
    *,
    debug: bool,
    metadata: Mapping[str, Any] | None = None,
    analyzer_summary: Any = None,
    plan_summary: Any = None,
    event_count: Any = None,
    model_profile: Any = None,
    tool_profile: Any = None,
) -> dict[str, Any]:
    """Build the only diagnostic fields permitted in a public result."""

    if not debug:
        return {}

    values = dict(metadata or {})
    for name, value in (
        ("analyzer_summary", analyzer_summary),
        ("plan_summary", plan_summary),
        ("event_count", event_count),
        ("model_profile", model_profile),
        ("tool_profile", tool_profile),
    ):
        if value is not None:
            values[name] = value
    return {
        name: _safe_value(
            values[name],
            debug=True,
            depth=0,
            max_depth=4,
            max_items=50,
            max_text_chars=1000,
        )
        for name in DEBUG_FIELD_NAMES
        if name in values and values[name] is not None
    }


def serialize_metadata(
    metadata: Mapping[str, Any] | None,
    *,
    debug: bool = False,
) -> dict[str, Any]:
    """Serialize stable metadata and optionally its bounded debug section."""

    if not isinstance(metadata, Mapping):
        return {}

    result: dict[str, Any] = {}
    for key, value in metadata.items():
        key_text = str(key)
        if key_text.lower() == "debug":
            if debug and isinstance(value, Mapping):
                result["debug"] = build_debug_metadata(
                    debug=True,
                    metadata=value,
                )
            continue
        if _is_forbidden_key(key_text):
            continue
        result[key_text] = _safe_value(
            value,
            debug=debug,
            depth=0,
            max_depth=8,
            max_items=200,
            max_text_chars=4000,
        )
    return result


def _safe_value(
    value: Any,
    *,
    debug: bool,
    depth: int,
    max_depth: int,
    max_items: int,
    max_text_chars: int,
) -> Any:
    if depth > max_depth:
        return {"type": type(value).__name__, "truncated": True}
    if isinstance(value, Enum):
        return _safe_value(
            value.value,
            debug=debug,
            depth=depth + 1,
            max_depth=max_depth,
            max_items=max_items,
            max_text_chars=max_text_chars,
        )
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if isfinite(value) else {"type": "float", "value": "non_finite"}
    if isinstance(value, str):
        return _sanitize_text(value, max_text_chars=max_text_chars)
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= max_items:
                result["__truncated__"] = True
                break
            key_text = str(key)
            if key_text.strip().lower() == "debug":
                if debug and isinstance(item, Mapping):
                    result["debug"] = build_debug_metadata(
                        debug=True,
                        metadata=item,
                    )
                continue
            if _is_forbidden_key(key_text):
                continue
            result[key_text] = _safe_value(
                item,
                debug=debug,
                depth=depth + 1,
                max_depth=max_depth,
                max_items=max_items,
                max_text_chars=max_text_chars,
            )
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        result = []
        for index, item in enumerate(value):
            if index >= max_items:
                result.append({"__truncated__": True})
                break
            result.append(
                _safe_value(
                    item,
                    debug=debug,
                    depth=depth + 1,
                    max_depth=max_depth,
                    max_items=max_items,
                    max_text_chars=max_text_chars,
                )
            )
        return result
    if is_dataclass(value):
        result = {}
        for item in fields(value):
            key_text = item.name
            if key_text.strip().lower() == "debug":
                if debug:
                    debug_value = getattr(value, key_text)
                    if isinstance(debug_value, Mapping):
                        result["debug"] = build_debug_metadata(
                            debug=True,
                            metadata=debug_value,
                        )
                continue
            if _is_forbidden_key(key_text):
                continue
            result[key_text] = _safe_value(
                getattr(value, key_text),
                debug=debug,
                depth=depth + 1,
                max_depth=max_depth,
                max_items=max_items,
                max_text_chars=max_text_chars,
            )
        return result
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            converted = to_dict()
        except Exception:
            converted = None
        if converted is not None and converted is not value:
            return _safe_value(
                converted,
                debug=debug,
                depth=depth + 1,
                max_depth=max_depth,
                max_items=max_items,
                max_text_chars=max_text_chars,
            )
    return {"type": type(value).__name__}


def _read_field(value: Any, name: str, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _is_forbidden_key(key: str) -> bool:
    lowered = key.strip().lower()
    return (
        lowered in SENSITIVE_FIELD_NAMES
        or lowered in _INTERNAL_FIELD_NAMES
        or any(marker in lowered for marker in _SECRET_TEXT_MARKERS)
    )


def _sanitize_text(value: str, *, max_text_chars: int) -> str:
    text = re.sub(
        r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+",
        "Bearer " + REDACTED_VALUE,
        value,
    )
    lowered = text.lower()
    # Keep user-facing prose, but neutralize common inline credential forms.
    for marker in _SECRET_TEXT_MARKERS:
        start = 0
        while True:
            index = lowered.find(marker, start)
            if index < 0:
                break
            separator = index + len(marker)
            while separator < len(text) and text[separator] in " \t":
                separator += 1
            if separator < len(text) and text[separator] in ":=":
                end = separator + 1
                while end < len(text) and text[end] not in " \t\r\n,;":
                    end += 1
                text = text[:separator] + "=" + REDACTED_VALUE + text[end:]
                lowered = text.lower()
                start = separator + len(REDACTED_VALUE) + 1
            else:
                start = separator
    if len(text) <= max_text_chars:
        return text
    return text[:max_text_chars] + f"... [truncated {len(text) - max_text_chars} chars]"


__all__ = [
    "DEBUG_FIELD_NAMES",
    "REDACTED_VALUE",
    "SENSITIVE_FIELD_NAMES",
    "build_debug_metadata",
    "safe_serialize",
    "serialize_execution_result",
    "serialize_memory_result",
    "serialize_metadata",
    "serialize_output_feedback",
    "serialize_pending_confirmation",
    "serialize_runtime_event",
    "serialize_runtime_result",
]
