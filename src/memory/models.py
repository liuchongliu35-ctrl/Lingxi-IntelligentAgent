from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def _copy_metadata(value: dict[str, Any] | None) -> dict[str, Any]:
    return dict(value or {})


def _json_safe(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _normalize_text(
    value: Any,
    field_name: str,
    *,
    lower: bool = False,
    allow_empty: bool = False,
) -> str:
    normalized = str(value.value if isinstance(value, Enum) else value or "").strip()
    if lower:
        normalized = normalized.lower()
    if not normalized and not allow_empty:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def _normalize_choice(value: Any, allowed: set[str], field_name: str) -> str:
    normalized = _normalize_text(value, field_name, lower=True)
    if normalized not in allowed:
        raise ValueError(f"Unsupported {field_name}: {value}")
    return normalized


def _normalize_bool(value: Any, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise TypeError(f"{field_name} must be a boolean value")


def _normalize_int(value: Any, field_name: str, *, minimum: int | None = None) -> int:
    normalized = int(value)
    if minimum is not None and normalized < minimum:
        raise ValueError(f"{field_name} must be >= {minimum}")
    return normalized


def _normalize_optional_int(
    value: Any,
    field_name: str,
    *,
    minimum: int | None = None,
) -> int | None:
    if value is None:
        return None
    return _normalize_int(value, field_name, minimum=minimum)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class _ToDictMixin:
    def to_dict(self) -> dict[str, Any]:
        return _json_safe(self)


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class ContentFormat(str, Enum):
    TEXT = "text"
    MARKDOWN = "markdown"
    JSON = "json"


class DisplayType(str, Enum):
    CHAT = "chat"
    FINAL_ANSWER = "final_answer"
    CLARIFICATION = "clarification"
    CONFIRMATION = "confirmation"
    TOOL_PROGRESS = "tool_progress"
    PLAN_PROGRESS = "plan_progress"
    SYSTEM_NOTICE = "system_notice"
    ERROR = "error"
    SUMMARY = "summary"


class MessageStatus(str, Enum):
    PENDING = "pending"
    STREAMING = "streaming"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentRunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_USER = "waiting_user"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL_FAILED = "partial_failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    REQUEST_REPLAN = "request_replan"
    INTERRUPTED = "interrupted"


class ExecutionEventStatus(str, Enum):
    RECORDED = "recorded"
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"
    WAITING_USER = "waiting_user"
    BLOCKED = "blocked"
    SKIPPED = "skipped"
    REQUEST_REPLAN = "request_replan"


class SessionStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    DELETED = "deleted"


class TimelineItemKind(str, Enum):
    MESSAGE = "message"
    EXECUTION_EVENT = "execution_event"


class SummarySource(str, Enum):
    MODEL = "model"
    RULE_FALLBACK = "rule_fallback"
    MANUAL = "manual"


# Short aliases keep the public contract readable while retaining the
# explicit MessageRole name used by the rest of the codebase.
Role = MessageRole


MESSAGE_ROLES = frozenset(item.value for item in MessageRole)
CONTENT_FORMATS = frozenset(item.value for item in ContentFormat)
DISPLAY_TYPES = frozenset(item.value for item in DisplayType)
MESSAGE_STATUSES = frozenset(item.value for item in MessageStatus)
AGENT_RUN_STATUSES = frozenset(item.value for item in AgentRunStatus)
EXECUTION_EVENT_STATUSES = frozenset(item.value for item in ExecutionEventStatus)
SESSION_STATUSES = frozenset(item.value for item in SessionStatus)
TIMELINE_ITEM_KINDS = frozenset(item.value for item in TimelineItemKind)
SUMMARY_SOURCES = frozenset(item.value for item in SummarySource)
TIMELINE_STATUSES = MESSAGE_STATUSES | EXECUTION_EVENT_STATUSES


@dataclass
class Message(_ToDictMixin):
    message_id: str
    session_id: str
    timeline_seq: int
    role: str | MessageRole
    content: str
    content_format: str | ContentFormat = ContentFormat.TEXT
    display_type: str | DisplayType = DisplayType.CHAT
    visible_to_user: bool = True
    status: str | MessageStatus = MessageStatus.COMPLETED
    run_id: str | None = None
    parent_message_id: str | None = None
    created_at: str = field(default_factory=_utc_now_iso)
    updated_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.message_id = _normalize_text(self.message_id, "message_id")
        self.session_id = _normalize_text(self.session_id, "session_id")
        self.timeline_seq = _normalize_int(self.timeline_seq, "timeline_seq", minimum=1)
        self.role = _normalize_choice(self.role, MESSAGE_ROLES, "role")
        self.content = _normalize_text(self.content, "content", allow_empty=True)
        self.content_format = _normalize_choice(self.content_format, CONTENT_FORMATS, "content_format")
        self.display_type = _normalize_choice(self.display_type, DISPLAY_TYPES, "display_type")
        self.visible_to_user = _normalize_bool(self.visible_to_user, "visible_to_user")
        self.status = _normalize_choice(self.status, MESSAGE_STATUSES, "status")
        self.run_id = _normalize_text(self.run_id, "run_id") if self.run_id is not None else None
        self.parent_message_id = (
            _normalize_text(self.parent_message_id, "parent_message_id")
            if self.parent_message_id is not None
            else None
        )
        self.created_at = _normalize_text(self.created_at, "created_at")
        self.updated_at = _normalize_text(self.updated_at, "updated_at") if self.updated_at is not None else None
        self.metadata = _copy_metadata(self.metadata)


@dataclass
class SessionState(_ToDictMixin):
    session_id: str
    messages: list[Message] | list[dict[str, Any]] = field(default_factory=list)
    summary: str = ""
    current_summary_id: str | None = None
    created_at: str = field(default_factory=_utc_now_iso)
    updated_at: str = field(default_factory=_utc_now_iso)
    last_activity_at: str = field(default_factory=_utc_now_iso)
    status: str | SessionStatus = SessionStatus.ACTIVE
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.session_id = _normalize_text(self.session_id, "session_id")
        self.messages = [
            item if isinstance(item, Message) else Message(**item)
            for item in self.messages
        ]
        self.summary = _normalize_text(self.summary, "summary", allow_empty=True)
        self.current_summary_id = (
            _normalize_text(self.current_summary_id, "current_summary_id")
            if self.current_summary_id is not None
            else None
        )
        self.created_at = _normalize_text(self.created_at, "created_at")
        self.updated_at = _normalize_text(self.updated_at, "updated_at")
        self.last_activity_at = _normalize_text(self.last_activity_at, "last_activity_at")
        self.status = _normalize_choice(self.status, SESSION_STATUSES, "status")
        self.metadata = _copy_metadata(self.metadata)

    @property
    def message_count(self) -> int:
        return len(self.messages)


@dataclass
class SessionInfo(_ToDictMixin):
    session_id: str
    status: str | SessionStatus
    created_at: str
    updated_at: str
    last_activity_at: str
    message_count: int
    title: str | None = None
    current_summary_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.session_id = _normalize_text(self.session_id, "session_id")
        self.status = _normalize_choice(self.status, SESSION_STATUSES, "status")
        self.created_at = _normalize_text(self.created_at, "created_at")
        self.updated_at = _normalize_text(self.updated_at, "updated_at")
        self.last_activity_at = _normalize_text(self.last_activity_at, "last_activity_at")
        self.message_count = _normalize_int(self.message_count, "message_count", minimum=0)
        self.title = (
            _normalize_text(self.title, "title", allow_empty=True)
            if self.title is not None
            else None
        )
        self.current_summary_id = (
            _normalize_text(self.current_summary_id, "current_summary_id")
            if self.current_summary_id is not None
            else None
        )
        self.metadata = _copy_metadata(self.metadata)


@dataclass
class AgentRun(_ToDictMixin):
    run_id: str
    session_id: str
    user_message_id: str
    status: str | AgentRunStatus
    started_at: str
    finished_at: str | None = None
    final_message_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    agent_version: str | None = None
    model_profile: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.run_id = _normalize_text(self.run_id, "run_id")
        self.session_id = _normalize_text(self.session_id, "session_id")
        self.user_message_id = _normalize_text(self.user_message_id, "user_message_id")
        self.status = _normalize_choice(self.status, AGENT_RUN_STATUSES, "status")
        self.started_at = _normalize_text(self.started_at, "started_at")
        self.finished_at = _normalize_text(self.finished_at, "finished_at") if self.finished_at is not None else None
        self.final_message_id = (
            _normalize_text(self.final_message_id, "final_message_id")
            if self.final_message_id is not None
            else None
        )
        self.error_code = _normalize_text(self.error_code, "error_code") if self.error_code is not None else None
        self.error_message = (
            _normalize_text(self.error_message, "error_message")
            if self.error_message is not None
            else None
        )
        self.agent_version = _normalize_text(self.agent_version, "agent_version") if self.agent_version is not None else None
        self.model_profile = _normalize_text(self.model_profile, "model_profile") if self.model_profile is not None else None
        self.metadata = _copy_metadata(self.metadata)


@dataclass
class ExecutionEventRecord(_ToDictMixin):
    event_id: str
    session_id: str
    run_id: str
    timeline_seq: int
    event_type: str
    display_type: str | DisplayType
    display_content: str
    visible_to_user: bool
    status: str | ExecutionEventStatus
    created_at: str
    completed_at: str | None = None
    parent_event_id: str | None = None
    sanitized_payload: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.event_id = _normalize_text(self.event_id, "event_id")
        self.session_id = _normalize_text(self.session_id, "session_id")
        self.run_id = _normalize_text(self.run_id, "run_id")
        self.timeline_seq = _normalize_int(self.timeline_seq, "timeline_seq", minimum=1)
        self.event_type = _normalize_text(self.event_type, "event_type", lower=True)
        self.display_type = _normalize_choice(self.display_type, DISPLAY_TYPES, "display_type")
        self.display_content = _normalize_text(self.display_content, "display_content", allow_empty=True)
        self.visible_to_user = _normalize_bool(self.visible_to_user, "visible_to_user")
        self.status = _normalize_choice(self.status, EXECUTION_EVENT_STATUSES, "status")
        self.created_at = _normalize_text(self.created_at, "created_at")
        self.completed_at = _normalize_text(self.completed_at, "completed_at") if self.completed_at is not None else None
        self.parent_event_id = (
            _normalize_text(self.parent_event_id, "parent_event_id")
            if self.parent_event_id is not None
            else None
        )
        self.sanitized_payload = _copy_metadata(self.sanitized_payload)
        self.metadata = _copy_metadata(self.metadata)


@dataclass
class TimelineItem(_ToDictMixin):
    item_id: str
    item_kind: str | TimelineItemKind
    session_id: str
    timeline_seq: int
    display_type: str | DisplayType
    content: str
    status: str
    created_at: str
    run_id: str | None = None
    role: str | MessageRole | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.item_id = _normalize_text(self.item_id, "item_id")
        self.item_kind = _normalize_choice(self.item_kind, TIMELINE_ITEM_KINDS, "item_kind")
        self.session_id = _normalize_text(self.session_id, "session_id")
        self.timeline_seq = _normalize_int(self.timeline_seq, "timeline_seq", minimum=1)
        self.display_type = _normalize_choice(self.display_type, DISPLAY_TYPES, "display_type")
        self.content = _normalize_text(self.content, "content", allow_empty=True)
        self.status = _normalize_choice(self.status, TIMELINE_STATUSES, "status")
        self.created_at = _normalize_text(self.created_at, "created_at")
        self.run_id = _normalize_text(self.run_id, "run_id") if self.run_id is not None else None
        self.role = _normalize_choice(self.role, MESSAGE_ROLES, "role") if self.role is not None else None
        self.metadata = _copy_metadata(self.metadata)


@dataclass
class SessionSummary(_ToDictMixin):
    summary_id: str
    session_id: str
    content: str
    covered_from_timeline_seq: int
    covered_to_timeline_seq: int
    created_at: str
    source: str | SummarySource
    model_profile: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.summary_id = _normalize_text(self.summary_id, "summary_id")
        self.session_id = _normalize_text(self.session_id, "session_id")
        self.content = _normalize_text(self.content, "content", allow_empty=True)
        self.covered_from_timeline_seq = _normalize_int(
            self.covered_from_timeline_seq,
            "covered_from_timeline_seq",
            minimum=1,
        )
        self.covered_to_timeline_seq = _normalize_int(
            self.covered_to_timeline_seq,
            "covered_to_timeline_seq",
            minimum=self.covered_from_timeline_seq,
        )
        self.created_at = _normalize_text(self.created_at, "created_at")
        self.source = _normalize_choice(self.source, SUMMARY_SOURCES, "source")
        self.model_profile = _normalize_text(self.model_profile, "model_profile") if self.model_profile is not None else None
        self.metadata = _copy_metadata(self.metadata)


@dataclass
class ContextBuildResult(_ToDictMixin):
    session_id: str
    context_text: str
    summary: str = ""
    recent_messages: list[Message] | list[dict[str, Any]] = field(default_factory=list)
    included_message_ids: list[str] = field(default_factory=list)
    included_event_ids: list[str] = field(default_factory=list)
    truncated: bool = False
    current_user_input_included: bool = False
    token_estimate: int | None = None
    char_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.session_id = _normalize_text(self.session_id, "session_id")
        self.context_text = _normalize_text(self.context_text, "context_text", allow_empty=True)
        self.summary = _normalize_text(self.summary, "summary", allow_empty=True)
        self.recent_messages = [
            item if isinstance(item, Message) else Message(**item)
            for item in self.recent_messages
        ]
        self.included_message_ids = [
            _normalize_text(item, "included_message_ids item")
            for item in self.included_message_ids
        ]
        self.included_event_ids = [
            _normalize_text(item, "included_event_ids item")
            for item in self.included_event_ids
        ]
        self.truncated = _normalize_bool(self.truncated, "truncated")
        self.current_user_input_included = _normalize_bool(
            self.current_user_input_included,
            "current_user_input_included",
        )
        self.token_estimate = _normalize_optional_int(self.token_estimate, "token_estimate", minimum=0)
        self.char_count = _normalize_int(self.char_count, "char_count", minimum=0)
        self.metadata = _copy_metadata(self.metadata)


__all__ = [
    "AgentRun",
    "AgentRunStatus",
    "ContentFormat",
    "ContextBuildResult",
    "DisplayType",
    "ExecutionEventRecord",
    "ExecutionEventStatus",
    "MESSAGE_ROLES",
    "MESSAGE_STATUSES",
    "Message",
    "MessageRole",
    "MessageStatus",
    "Role",
    "SESSION_STATUSES",
    "SessionInfo",
    "SessionState",
    "SessionStatus",
    "SessionSummary",
    "SummarySource",
    "TIMELINE_ITEM_KINDS",
    "TimelineItem",
    "TimelineItemKind",
]
