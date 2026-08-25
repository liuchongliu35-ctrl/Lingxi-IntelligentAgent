from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from src.memory.config import MemoryConfig
from src.memory.ids import validate_generated_id
from src.memory.models import DisplayType, ExecutionEventRecord, ExecutionEventStatus


REDACTED_VALUE = "***REDACTED***"
MAPPER_VERSION = "memory_event_mapper_v1"

_SENSITIVE_FIELD_MARKERS = {
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "password",
    "secret",
    "token",
}

_INTERNAL_PAYLOAD_KEYS = {
    "action_args",
    "chain_of_thought",
    "env",
    "exception",
    "full_prompt",
    "input_args",
    "prompt",
    "raw_input_args",
    "raw_model_output",
    "raw_observation",
    "raw_output",
    "raw_prompt",
    "raw_reasoning",
    "raw_result",
    "raw_tool_result",
    "reasoning",
    "stack_trace",
    "thought_summary",
    "traceback",
}

_PLAN_PROGRESS_EVENTS = {
    "action_selected",
    "fallback_finished",
    "fallback_started",
    "message_delta",
    "model_step_finished",
    "model_step_started",
    "progress_message",
    "request_replan",
    "retry_finished",
    "retry_scheduled",
    "step_completed",
    "step_started",
    "thought_visible",
}

_TOOL_PROGRESS_EVENTS = {
    "command_finished",
    "command_started",
    "file_edited",
    "observation_created",
    "tool_finished",
    "tool_started",
}

_ERROR_EVENTS = {
    "retry_exhausted",
    "step_failed",
    "tool_failed",
}

_TEXT_SECRET_PATTERNS = (
    re.compile(
        r"(?i)\b(api[_-]?key|authorization|cookie|password|secret|token)\s*[:=]\s*([^\s,;.!?]+)"
    ),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"),
)


def map_execution_event(
    event: Any,
    *,
    session_id: str,
    run_id: str,
    config: MemoryConfig | None = None,
    timeline_seq: int | None = None,
) -> ExecutionEventRecord | None:
    """Map a ReActExecutor ExecutionEvent-like object into a Memory record.

    Invisible events are intentionally returned as None so callers can keep
    them out of normal session replay while still writing a lightweight log.
    """

    if not event_visible_to_user(event):
        return None

    config = config or MemoryConfig.default()
    event_type = _event_type(event)
    timestamp = _event_timestamp(event)
    status = map_event_status(event_type)
    return ExecutionEventRecord(
        event_id=memory_event_id(event),
        session_id=session_id,
        run_id=run_id,
        timeline_seq=timeline_seq or 1,
        event_type=event_type,
        display_type=map_display_type(event_type),
        display_content=sanitize_display_content(
            _event_message(event),
            max_chars=config.max_event_display_chars,
        ),
        visible_to_user=True,
        status=status,
        created_at=timestamp,
        completed_at=timestamp if status in _TERMINAL_EVENT_STATUSES else None,
        sanitized_payload=sanitize_payload(
            _event_payload(event),
            max_text_chars=config.max_event_payload_chars,
        ),
        metadata={
            "mapper_version": MAPPER_VERSION,
            "source_event_id": _event_id(event),
            "execution_id": _event_field(event, "execution_id"),
            "plan_id": _event_field(event, "plan_id"),
            "task_id": _event_field(event, "task_id"),
            "step_id": _event_field(event, "step_id"),
        },
    )


def event_visible_to_user(event: Any) -> bool:
    return bool(_event_field(event, "visible_to_user", True))


def map_display_type(event_type: str) -> str:
    event_type = str(event_type or "").strip().lower()
    if event_type in _ERROR_EVENTS:
        return DisplayType.ERROR.value
    if event_type in _TOOL_PROGRESS_EVENTS:
        return DisplayType.TOOL_PROGRESS.value
    if event_type in _PLAN_PROGRESS_EVENTS:
        return DisplayType.PLAN_PROGRESS.value
    if event_type == "confirmation_requested":
        return DisplayType.CONFIRMATION.value
    if event_type == "system_notice":
        return DisplayType.SYSTEM_NOTICE.value
    if event_type == "final_answer":
        return DisplayType.FINAL_ANSWER.value
    return DisplayType.SYSTEM_NOTICE.value


_TERMINAL_EVENT_STATUSES = {
    ExecutionEventStatus.COMPLETED.value,
    ExecutionEventStatus.FAILED.value,
    ExecutionEventStatus.WAITING_USER.value,
    ExecutionEventStatus.BLOCKED.value,
    ExecutionEventStatus.SKIPPED.value,
    ExecutionEventStatus.REQUEST_REPLAN.value,
}


def map_event_status(event_type: str) -> str:
    event_type = str(event_type or "").strip().lower()
    if event_type.endswith("_started"):
        return ExecutionEventStatus.STARTED.value
    if event_type.endswith("_finished") or event_type.endswith("_completed"):
        return ExecutionEventStatus.COMPLETED.value
    if event_type.endswith("_failed"):
        return ExecutionEventStatus.FAILED.value
    if event_type == "final_answer":
        return ExecutionEventStatus.COMPLETED.value
    if event_type == "confirmation_requested":
        return ExecutionEventStatus.WAITING_USER.value
    if event_type == "request_replan":
        return ExecutionEventStatus.REQUEST_REPLAN.value
    if event_type == "retry_exhausted":
        return ExecutionEventStatus.FAILED.value
    return ExecutionEventStatus.RECORDED.value


def sanitize_display_content(value: Any, *, max_chars: int) -> str:
    return _truncate_text(_sanitize_text(str(value or "")), max_chars)


def sanitize_payload(payload: Any, *, max_text_chars: int) -> dict[str, Any]:
    sanitized = _sanitize_payload_value(payload, max_text_chars=max_text_chars)
    return sanitized if isinstance(sanitized, dict) else {"value": sanitized}


def memory_event_id(event: Any) -> str:
    source_event_id = _event_id(event)
    try:
        return validate_generated_id(source_event_id, prefix="event")
    except ValueError:
        pass

    date_part, time_part = _event_time_parts(_event_timestamp(event))
    suffix_source = source_event_id
    if suffix_source.startswith("event_"):
        suffix_source = suffix_source[len("event_") :]
    suffix_source = suffix_source or _event_type(event)
    suffix = "".join(re.findall(r"[a-fA-F0-9]+", suffix_source)).lower()
    if len(suffix) < 6:
        suffix = hashlib.sha256(suffix_source.encode("utf-8")).hexdigest()[:12]
    else:
        suffix = suffix[:12]
    return f"event_{date_part}_{time_part}_{suffix}"


def event_log_preview(event: Any) -> dict[str, Any]:
    payload = _event_payload(event)
    payload_keys = sorted(str(key) for key in payload.keys()) if isinstance(payload, dict) else []
    message = sanitize_display_content(_event_message(event), max_chars=120)
    return {
        "source_event_id": _event_id(event),
        "source_event_type": _event_type(event),
        "visible_to_user": event_visible_to_user(event),
        "message_length": len(str(_event_message(event) or "")),
        "message_preview": message,
        "payload_keys": payload_keys[:20],
    }


def _sanitize_payload_value(value: Any, *, max_text_chars: int) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return _sanitize_payload_value(asdict(value), max_text_chars=max_text_chars)
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_sensitive_key(key_text) or _is_internal_key(key_text):
                result[key_text] = REDACTED_VALUE
            else:
                result[key_text] = _sanitize_payload_value(
                    item,
                    max_text_chars=max_text_chars,
                )
        return result
    if isinstance(value, (list, tuple, set)):
        return [
            _sanitize_payload_value(item, max_text_chars=max_text_chars)
            for item in value
        ]
    if isinstance(value, str):
        return _truncate_text(_sanitize_text(value), max_text_chars)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _truncate_text(_sanitize_text(str(value)), max_text_chars)


def _sanitize_text(text: str) -> str:
    sanitized = text
    for pattern in _TEXT_SECRET_PATTERNS:
        if pattern.pattern.startswith("(?i)\\bBearer"):
            sanitized = pattern.sub("Bearer " + REDACTED_VALUE, sanitized)
        else:
            sanitized = pattern.sub(lambda match: f"{match.group(1)}={REDACTED_VALUE}", sanitized)
    return sanitized


def _truncate_text(text: str, max_chars: int) -> str:
    limit = max(int(max_chars or 1), 1)
    if len(text) <= limit:
        return text
    return text[:limit] + f"... [truncated {len(text) - limit} chars]"


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in _SENSITIVE_FIELD_MARKERS)


def _is_internal_key(key: str) -> bool:
    lowered = key.lower()
    return (
        lowered in _INTERNAL_PAYLOAD_KEYS
        or lowered.endswith("_full")
        or lowered.endswith("_trace")
    )


def _event_field(event: Any, key: str, default: Any = None) -> Any:
    if isinstance(event, dict):
        return event.get(key, default)
    return getattr(event, key, default)


def _event_id(event: Any) -> str:
    return str(_event_field(event, "event_id", "") or "")


def _event_type(event: Any) -> str:
    return str(
        _event_field(event, "type", None)
        or _event_field(event, "event_type", None)
        or "system_notice"
    ).strip().lower()


def _event_message(event: Any) -> str:
    return str(
        _event_field(event, "message", None)
        or _event_field(event, "display_content", None)
        or ""
    )


def _event_payload(event: Any) -> Any:
    payload = _event_field(event, "payload", None)
    if payload is None:
        payload = _event_field(event, "sanitized_payload", None)
    return payload if payload is not None else {}


def _event_timestamp(event: Any) -> str:
    return str(
        _event_field(event, "timestamp", None)
        or _event_field(event, "created_at", None)
        or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )


def _event_time_parts(timestamp: str) -> tuple[str, str]:
    try:
        normalized = timestamp.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        parsed = parsed.astimezone(timezone.utc)
        return parsed.strftime("%Y%m%d"), parsed.strftime("%H%M%S")
    except ValueError:
        return "19700101", "000000"


__all__ = [
    "MAPPER_VERSION",
    "REDACTED_VALUE",
    "event_log_preview",
    "event_visible_to_user",
    "map_display_type",
    "map_event_status",
    "map_execution_event",
    "memory_event_id",
    "sanitize_display_content",
    "sanitize_payload",
]
