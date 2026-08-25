"""Process-local storage for runs waiting for user confirmation.

The executor context is intentionally kept in this module only for the
same-process resume path.  Public snapshots never include that context.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any, Callable, Mapping

from src.memory.ids import validate_session_id

from .errors import RuntimeErrorCode, RuntimeException
from .serialization import (
    safe_serialize,
    serialize_metadata,
    serialize_pending_confirmation,
)


DEFAULT_PENDING_RUN_TTL_SECONDS = 15 * 60


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_run_id(run_id: Any) -> str:
    if not isinstance(run_id, str):
        raise TypeError("run_id must be a string")
    value = run_id.strip()
    if not value:
        raise ValueError("run_id must not be empty")
    return value


def _as_utc(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime")
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


@dataclass(slots=True)
class PendingRunRecord:
    """Internal record used by Runtime's same-process resume path.

    ``executor_context`` is deliberately absent from all public conversion
    methods.  Runtime code should use ``get`` only inside the process-local
    orchestration boundary and adapters should use ``get_public``.
    """

    session_id: str
    run_id: str
    executor_context: Any
    pending_confirmation: dict[str, Any] | None
    created_at: datetime
    expires_at: datetime
    owner: str | None
    metadata: dict[str, Any]

    @property
    def ttl_seconds(self) -> float:
        return max(0.0, (self.expires_at - self.created_at).total_seconds())

    def to_public_dict(self) -> dict[str, Any]:
        """Return the safe summary allowed to cross the Runtime boundary."""

        result: dict[str, Any] = {
            "session_id": self.session_id,
            "run_id": self.run_id,
            "pending_confirmation": dict(self.pending_confirmation)
            if self.pending_confirmation is not None
            else None,
            "created_at": _iso(self.created_at),
            "expires_at": _iso(self.expires_at),
            "metadata": dict(self.metadata),
        }
        if self.owner is not None:
            result["owner"] = self.owner
        return result

    # A short alias makes the boundary explicit at call sites.
    safe_summary = to_public_dict

    def to_dict(self) -> dict[str, Any]:
        """Serialize without ever including the executor context."""

        return self.to_public_dict()


class PendingRunRegistry:
    """Thread-safe, process-local registry for confirmation-pending runs."""

    def __init__(
        self,
        *,
        ttl_seconds: float = DEFAULT_PENDING_RUN_TTL_SECONDS,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if isinstance(ttl_seconds, bool) or ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be greater than zero")
        self.ttl_seconds = float(ttl_seconds)
        self._clock = clock or _utc_now
        self._records: dict[str, PendingRunRecord] = {}
        self._lock = RLock()

    def register(
        self,
        session_id: str,
        run_id: str,
        executor_context: Any,
        pending_confirmation: Any,
        *,
        owner: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        expires_at: datetime | None = None,
    ) -> PendingRunRecord:
        """Register a pending run and return its internal record.

        The confirmation and metadata are sanitized at registration time so a
        later public snapshot cannot accidentally expose the original object.
        """

        normalized_session_id = validate_session_id(session_id)
        normalized_run_id = _normalize_run_id(run_id)
        normalized_owner = _normalize_optional_text(owner, "owner")
        safe_owner = (
            safe_serialize(normalized_owner, max_text_chars=256)
            if normalized_owner is not None
            else None
        )
        created_at = _as_utc(self._clock(), "clock result")
        normalized_expires_at = (
            _as_utc(expires_at, "expires_at")
            if expires_at is not None
            else created_at + timedelta(seconds=self.ttl_seconds)
        )
        if normalized_expires_at <= created_at:
            raise ValueError("expires_at must be later than created_at")

        record = PendingRunRecord(
            session_id=normalized_session_id,
            run_id=normalized_run_id,
            executor_context=executor_context,
            pending_confirmation=serialize_pending_confirmation(
                pending_confirmation
            ),
            created_at=created_at,
            expires_at=normalized_expires_at,
            owner=safe_owner if isinstance(safe_owner, str) else None,
            metadata=serialize_metadata(metadata),
        )

        with self._lock:
            self._expire_locked(created_at)
            if normalized_run_id in self._records:
                raise RuntimeException(
                    RuntimeErrorCode.SESSION_CONFLICT,
                    "A pending run with this run_id is already registered.",
                    metadata={"run_id": normalized_run_id},
                )
            self._records[normalized_run_id] = record
        return record

    def get(
        self,
        run_id: str,
        *,
        session_id: str | None = None,
    ) -> PendingRunRecord | None:
        """Return the internal record for Runtime resume, or ``None``.

        This method is intentionally an internal orchestration API.  CLI/API
        adapters must use ``get_public`` so executor context cannot escape.
        """

        normalized_run_id = _normalize_run_id(run_id)
        normalized_session_id = (
            validate_session_id(session_id) if session_id is not None else None
        )
        with self._lock:
            record = self._get_valid_locked(normalized_run_id)
            if record is None:
                return None
            self._check_owner(record, normalized_session_id)
            return record

    def get_public(
        self,
        run_id: str,
        *,
        session_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Return only the safe public summary for a pending run."""

        record = self.get(run_id, session_id=session_id)
        return record.to_public_dict() if record is not None else None

    def pop(
        self,
        run_id: str,
        *,
        session_id: str | None = None,
    ) -> PendingRunRecord | None:
        """Atomically retrieve and remove a pending run for resume/cancel."""

        normalized_run_id = _normalize_run_id(run_id)
        normalized_session_id = (
            validate_session_id(session_id) if session_id is not None else None
        )
        with self._lock:
            record = self._get_valid_locked(normalized_run_id)
            if record is None:
                return None
            self._check_owner(record, normalized_session_id)
            del self._records[normalized_run_id]
            return record

    def remove(
        self,
        run_id: str,
        *,
        session_id: str | None = None,
    ) -> bool:
        """Remove a pending run; repeated cleanup is harmless."""

        normalized_run_id = _normalize_run_id(run_id)
        normalized_session_id = (
            validate_session_id(session_id) if session_id is not None else None
        )
        with self._lock:
            record = self._get_valid_locked(normalized_run_id)
            if record is None:
                return False
            self._check_owner(record, normalized_session_id)
            del self._records[normalized_run_id]
            return True

    def expire(self) -> list[str]:
        """Remove expired entries and return only their run IDs."""

        with self._lock:
            return self._expire_locked(_as_utc(self._clock(), "clock result"))

    def clear(self) -> int:
        """Clear this process-local registry and return the removed count."""

        with self._lock:
            count = len(self._records)
            self._records.clear()
            return count

    def __len__(self) -> int:
        with self._lock:
            self._expire_locked(_as_utc(self._clock(), "clock result"))
            return len(self._records)

    def _get_valid_locked(self, run_id: str) -> PendingRunRecord | None:
        record = self._records.get(run_id)
        if record is None:
            return None
        now = _as_utc(self._clock(), "clock result")
        if record.expires_at <= now:
            del self._records[run_id]
            return None
        return record

    def _expire_locked(self, now: datetime) -> list[str]:
        expired = [
            run_id
            for run_id, record in self._records.items()
            if record.expires_at <= now
        ]
        for run_id in expired:
            del self._records[run_id]
        return expired

    @staticmethod
    def _check_owner(
        record: PendingRunRecord,
        session_id: str | None,
    ) -> None:
        if session_id is not None and record.session_id != session_id:
            raise RuntimeException(
                RuntimeErrorCode.SESSION_CONFLICT,
                "The pending run does not belong to this session.",
                metadata={"run_id": record.run_id},
            )


def _normalize_optional_text(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


__all__ = [
    "DEFAULT_PENDING_RUN_TTL_SECONDS",
    "PendingRunRecord",
    "PendingRunRegistry",
]
