"""Stable public contracts for the Runtime application layer."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Mapping

from src.memory.ids import validate_session_id


class RuntimeStatus(str, Enum):
    """Statuses exposed by Runtime, CLI, and API adapters."""

    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    WAITING_USER = "waiting_user"
    REQUEST_REPLAN = "request_replan"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


RUNTIME_STATUSES = frozenset(status.value for status in RuntimeStatus)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00",
        "Z",
    )


def _normalize_text(value: Any, field_name: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    normalized = value.strip()
    if not normalized and not allow_empty:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def _normalize_optional_text(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    return _normalize_text(value, field_name)


def _normalize_bool(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{field_name} must be a boolean")
    return value


def _normalize_mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a mapping")
    return {str(key): item for key, item in value.items()}


def _normalize_status(value: str | RuntimeStatus) -> str:
    normalized = value.value if isinstance(value, RuntimeStatus) else value
    normalized = _normalize_text(normalized, "status", allow_empty=False).lower()
    if normalized not in RUNTIME_STATUSES:
        raise ValueError(f"Unsupported Runtime status: {value}")
    return normalized


def _contract_to_dict(value: Any) -> Any:
    """Convert contract values without generating IDs or importing adapter models."""

    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime | date):
        return value.isoformat()
    if is_dataclass(value):
        return _contract_to_dict(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _contract_to_dict(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_contract_to_dict(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise TypeError(f"Unsupported contract value: {type(value).__name__}")


class _ContractMixin:
    def to_dict(self) -> dict[str, Any]:
        result = _contract_to_dict(self)
        if not isinstance(result, dict):
            raise TypeError("contract serialization must produce a mapping")
        return result


@dataclass
class RuntimeRequest(_ContractMixin):
    """A single user-facing Runtime run request."""

    input: str
    session_id: str | None = None
    stream: bool = False
    debug: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    model_profile: str | None = None
    agent_version: str | None = None

    def __post_init__(self) -> None:
        self.input = _normalize_text(self.input, "input")
        if self.session_id is not None:
            self.session_id = validate_session_id(self.session_id)
        self.stream = _normalize_bool(self.stream, "stream")
        self.debug = _normalize_bool(self.debug, "debug")
        self.metadata = _normalize_mapping(self.metadata, "metadata")
        self.model_profile = _normalize_optional_text(self.model_profile, "model_profile")
        self.agent_version = _normalize_optional_text(self.agent_version, "agent_version")


@dataclass
class RuntimeResult(_ContractMixin):
    """Stable result returned by Runtime to CLI/API adapters."""

    success: bool = False
    status: str | RuntimeStatus = RuntimeStatus.FAILED
    session_id: str | None = None
    run_id: str | None = None
    output: str = ""
    execution_result: dict[str, Any] | None = None
    output_feedback: dict[str, Any] | None = None
    memory_result: dict[str, Any] | None = None
    timeline: list[dict[str, Any]] = field(default_factory=list)
    requires_user_input: bool = False
    pending_confirmation: dict[str, Any] | None = None
    request_replan: bool = False
    replan_reason: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    persistence_available: bool = True
    persistence_warning: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.success = _normalize_bool(self.success, "success")
        self.status = _normalize_status(self.status)
        if self.session_id is not None:
            self.session_id = validate_session_id(self.session_id)
        self.run_id = _normalize_optional_text(self.run_id, "run_id")
        self.output = _normalize_text(self.output, "output", allow_empty=True)
        self.execution_result = (
            _normalize_mapping(self.execution_result, "execution_result")
            if self.execution_result is not None
            else None
        )
        self.output_feedback = (
            _normalize_mapping(self.output_feedback, "output_feedback")
            if self.output_feedback is not None
            else None
        )
        self.memory_result = (
            _normalize_mapping(self.memory_result, "memory_result")
            if self.memory_result is not None
            else None
        )
        if not isinstance(self.timeline, list):
            raise TypeError("timeline must be a list")
        self.timeline = [
            _normalize_mapping(item, "timeline item") for item in self.timeline
        ]
        self.requires_user_input = _normalize_bool(
            self.requires_user_input,
            "requires_user_input",
        )
        self.pending_confirmation = (
            _normalize_mapping(self.pending_confirmation, "pending_confirmation")
            if self.pending_confirmation is not None
            else None
        )
        self.request_replan = _normalize_bool(self.request_replan, "request_replan")
        self.replan_reason = _normalize_optional_text(self.replan_reason, "replan_reason")
        self.error_code = _normalize_optional_text(self.error_code, "error_code")
        self.error_message = _normalize_optional_text(self.error_message, "error_message")
        self.persistence_available = _normalize_bool(
            self.persistence_available,
            "persistence_available",
        )
        self.persistence_warning = _normalize_optional_text(
            self.persistence_warning,
            "persistence_warning",
        )
        self.metadata = _normalize_mapping(self.metadata, "metadata")


@dataclass
class RuntimeEvent(_ContractMixin):
    """A safe, lightweight wrapper around an executor event."""

    session_id: str
    run_id: str
    event_type: str
    message: str = ""
    visible_to_user: bool = True
    payload: dict[str, Any] = field(default_factory=dict)
    source_event: dict[str, Any] | None = None
    sequence: int = 0
    event_id: str | None = None
    created_at: str = field(default_factory=_utc_now_iso)

    def __post_init__(self) -> None:
        self.session_id = validate_session_id(self.session_id)
        self.run_id = _normalize_text(self.run_id, "run_id")
        self.event_type = _normalize_text(self.event_type, "event_type")
        self.message = _normalize_text(self.message, "message", allow_empty=True)
        self.visible_to_user = _normalize_bool(
            self.visible_to_user,
            "visible_to_user",
        )
        self.payload = _normalize_mapping(self.payload, "payload")
        self.source_event = (
            _normalize_mapping(self.source_event, "source_event")
            if self.source_event is not None
            else None
        )
        if not isinstance(self.sequence, int) or isinstance(self.sequence, bool):
            raise TypeError("sequence must be an integer")
        if self.sequence < 0:
            raise ValueError("sequence must be >= 0")
        self.event_id = _normalize_optional_text(self.event_id, "event_id")
        self.created_at = _normalize_text(self.created_at, "created_at")


@dataclass
class ResumeRequest(_ContractMixin):
    """Internal request shape for same-process confirmation recovery."""

    session_id: str
    run_id: str
    approved: bool
    reason: str = ""
    confirmation_id: str | None = None
    preview_hash: str | None = None
    debug: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.session_id = validate_session_id(self.session_id)
        self.run_id = _normalize_text(self.run_id, "run_id")
        self.approved = _normalize_bool(self.approved, "approved")
        self.reason = _normalize_text(self.reason, "reason", allow_empty=True)
        self.confirmation_id = _normalize_optional_text(
            self.confirmation_id,
            "confirmation_id",
        )
        self.preview_hash = _normalize_optional_text(self.preview_hash, "preview_hash")
        self.debug = _normalize_bool(self.debug, "debug")
        self.metadata = _normalize_mapping(self.metadata, "metadata")


@dataclass
class CancelRequest(_ContractMixin):
    """Internal request shape for cancelling a pending Runtime run."""

    session_id: str
    run_id: str
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.session_id = validate_session_id(self.session_id)
        self.run_id = _normalize_text(self.run_id, "run_id")
        self.reason = _normalize_text(self.reason, "reason", allow_empty=True)
        self.metadata = _normalize_mapping(self.metadata, "metadata")


__all__ = [
    "CancelRequest",
    "RUNTIME_STATUSES",
    "ResumeRequest",
    "RuntimeEvent",
    "RuntimeRequest",
    "RuntimeResult",
    "RuntimeStatus",
]
