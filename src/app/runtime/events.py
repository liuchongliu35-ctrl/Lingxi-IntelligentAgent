"""Run-local Runtime event coordination helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from typing import Any, Mapping

from .serialization import safe_serialize


@dataclass
class RuntimeEventCoordinator:
    """Track event order and delivery within one Runtime run."""

    sequence: int = 0
    processed_keys: set[str] = field(default_factory=set)
    callback_event_count: int = 0

    def key_for(self, event: Any) -> str:
        source_event_id = self._field(event, "event_id")
        if isinstance(source_event_id, str) and source_event_id.strip():
            return f"id:{source_event_id.strip()}"

        fingerprint = safe_serialize(
            {
                "execution_id": self._field(event, "execution_id"),
                "plan_id": self._field(event, "plan_id"),
                "type": self._field(event, "type", self._field(event, "event_type")),
                "message": self._field(event, "message"),
                "task_id": self._field(event, "task_id"),
                "step_id": self._field(event, "step_id"),
                "timestamp": self._field(event, "timestamp", self._field(event, "created_at")),
                "visible_to_user": self._field(event, "visible_to_user", True),
                "payload": self._field(event, "payload", {}),
            },
            max_depth=8,
            max_items=200,
            max_text_chars=4000,
        )
        encoded = json.dumps(
            fingerprint,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return f"fingerprint:{hashlib.sha256(encoded).hexdigest()}"

    def mark_processed(self, key: str) -> bool:
        if key in self.processed_keys:
            return False
        self.processed_keys.add(key)
        self.sequence += 1
        return True

    @staticmethod
    def _field(event: Any, name: str, default: Any = None) -> Any:
        if isinstance(event, Mapping):
            return event.get(name, default)
        return getattr(event, name, default)


__all__ = ["RuntimeEventCoordinator"]
