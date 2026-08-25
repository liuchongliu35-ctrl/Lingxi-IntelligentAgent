from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import PurePath
from uuid import uuid4


_SAFE_SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
_ID_PATTERN = re.compile(
    r"^(?P<prefix>session|msg|run|event|summary)_"
    r"(?P<date>\d{8})(?:_(?P<time>\d{6}))?_(?P<random>[a-f0-9]+)$"
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id(prefix: str, *, include_time: bool, suffix_length: int) -> str:
    timestamp = _utc_now()
    date_part = timestamp.strftime("%Y%m%d")
    time_part = timestamp.strftime("%H%M%S")
    random_part = uuid4().hex[:suffix_length]
    if include_time:
        return f"{prefix}_{date_part}_{time_part}_{random_part}"
    return f"{prefix}_{date_part}_{random_part}"


def new_session_id() -> str:
    return _new_id("session", include_time=False, suffix_length=8)


def new_message_id() -> str:
    return _new_id("msg", include_time=True, suffix_length=6)


def new_run_id() -> str:
    return _new_id("run", include_time=True, suffix_length=6)


def new_event_id() -> str:
    return _new_id("event", include_time=True, suffix_length=6)


def new_summary_id() -> str:
    return _new_id("summary", include_time=True, suffix_length=6)


def validate_session_id(session_id: str) -> str:
    if not isinstance(session_id, str):
        raise TypeError("session_id must be a string")
    normalized = session_id.strip()
    if not normalized:
        raise ValueError("session_id must not be empty")
    if normalized in {".", ".."} or ".." in normalized:
        raise ValueError("session_id must not contain '..'")
    if "/" in normalized or "\\" in normalized:
        raise ValueError("session_id must not contain path separators")
    if PurePath(normalized).is_absolute():
        raise ValueError("session_id must not be an absolute path")
    if not _SAFE_SESSION_ID_PATTERN.fullmatch(normalized):
        raise ValueError(
            "session_id may contain only letters, numbers, underscores, and hyphens"
        )
    return normalized


def validate_generated_id(value: str, *, prefix: str | None = None) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("id must be a non-empty string")
    match = _ID_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError(f"Invalid Memory id: {value}")
    if prefix is not None and match.group("prefix") != prefix:
        raise ValueError(f"Expected {prefix} id, got {value}")
    return value


__all__ = [
    "new_event_id",
    "new_message_id",
    "new_run_id",
    "new_session_id",
    "new_summary_id",
    "validate_generated_id",
    "validate_session_id",
]
