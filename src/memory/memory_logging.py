from __future__ import annotations

import json
import re
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


REDACTED_VALUE = "***REDACTED***"
MAX_LOG_TEXT_CHARS = 240
MAX_LOG_LIST_ITEMS = 20

_SENSITIVE_KEY_MARKERS = {
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "password",
    "secret",
    "token",
}

_SENSITIVE_TEXT_PATTERNS = (
    re.compile(
        r"(?i)\b(api[_-]?key|authorization|cookie|password|secret|token)\s*[:=]\s*([^\s,;.!?]+)"
    ),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"),
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_memory_log_record(event_type: str, **payload: Any) -> dict[str, Any]:
    return {
        "event_type": str(event_type or "").strip().lower() or "memory_event",
        "created_at": now_iso(),
        **sanitize_log_payload(payload),
    }


def write_memory_log(log_path: str | Path, event_type: str, **payload: Any) -> dict[str, Any]:
    record = build_memory_log_record(event_type, **payload)
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return record


def sanitize_log_payload(payload: Any) -> Any:
    return _sanitize_value(payload)


def log_text_preview(value: Any, *, max_chars: int = MAX_LOG_TEXT_CHARS) -> str:
    return _truncate_text(_sanitize_text(str(value or "")), max_chars)


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return _sanitize_value(asdict(value))
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_sensitive_key(key_text):
                sanitized[key_text] = REDACTED_VALUE
            else:
                sanitized[key_text] = _sanitize_value(item)
        return sanitized
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        result = [_sanitize_value(item) for item in items[:MAX_LOG_LIST_ITEMS]]
        if len(items) > MAX_LOG_LIST_ITEMS:
            result.append({"truncated_items": len(items) - MAX_LOG_LIST_ITEMS})
        return result
    if isinstance(value, str):
        return _truncate_text(_sanitize_text(value), MAX_LOG_TEXT_CHARS)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _truncate_text(_sanitize_text(str(value)), MAX_LOG_TEXT_CHARS)


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in _SENSITIVE_KEY_MARKERS)


def _sanitize_text(text: str) -> str:
    sanitized = text
    for pattern in _SENSITIVE_TEXT_PATTERNS:
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


__all__ = [
    "MAX_LOG_TEXT_CHARS",
    "REDACTED_VALUE",
    "build_memory_log_record",
    "log_text_preview",
    "now_iso",
    "sanitize_log_payload",
    "write_memory_log",
]
