from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.core.config import get_settings


DEFAULT_DATABASE_RELATIVE_PATH = Path("storage") / "agent_memory.db"
DEFAULT_LOG_RELATIVE_PATH = Path("logs") / "memory.log"
DEFAULT_MAX_RECENT_MESSAGES = 10
DEFAULT_SUMMARY_TRIGGER_MESSAGES = 14
DEFAULT_SUMMARY_BATCH_MESSAGES = 6
DEFAULT_SUMMARY_TARGET_CHARS = 2000
DEFAULT_MAX_MESSAGE_CONTENT_CHARS = 12000
DEFAULT_MAX_EVENT_DISPLAY_CHARS = 1200
DEFAULT_MAX_EVENT_PAYLOAD_CHARS = 1000


@dataclass(frozen=True)
class MemoryConfig:
    """Configuration shared by the Memory / Context MVP modules.

    Path fields are resolved during construction, but directories are not
    created here. Storage setup belongs to the repository layer.
    """

    database_path: Path
    log_path: Path
    max_recent_messages: int = DEFAULT_MAX_RECENT_MESSAGES
    summary_trigger_messages: int = DEFAULT_SUMMARY_TRIGGER_MESSAGES
    summary_batch_messages: int = DEFAULT_SUMMARY_BATCH_MESSAGES
    summary_target_chars: int = DEFAULT_SUMMARY_TARGET_CHARS
    max_message_content_chars: int = DEFAULT_MAX_MESSAGE_CONTENT_CHARS
    max_event_display_chars: int = DEFAULT_MAX_EVENT_DISPLAY_CHARS
    max_event_payload_chars: int = DEFAULT_MAX_EVENT_PAYLOAD_CHARS
    summary_allow_rule_fallback: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "database_path", Path(self.database_path).expanduser().resolve())
        object.__setattr__(self, "log_path", Path(self.log_path).expanduser().resolve())

        positive_fields = (
            "max_recent_messages",
            "summary_trigger_messages",
            "summary_batch_messages",
            "summary_target_chars",
            "max_message_content_chars",
            "max_event_display_chars",
            "max_event_payload_chars",
        )
        for field_name in positive_fields:
            value = int(getattr(self, field_name))
            if value <= 0:
                raise ValueError(f"{field_name} must be > 0")
            object.__setattr__(self, field_name, value)

        if self.summary_trigger_messages < self.max_recent_messages:
            raise ValueError(
                "summary_trigger_messages must be >= max_recent_messages"
            )
        object.__setattr__(
            self,
            "summary_allow_rule_fallback",
            _coerce_bool(self.summary_allow_rule_fallback, "summary_allow_rule_fallback"),
        )

    @classmethod
    def default(cls, workspace_root: str | Path | None = None) -> "MemoryConfig":
        if workspace_root is None:
            workspace_root = get_settings().workspace_root
        root = Path(workspace_root).expanduser().resolve()
        return cls(
            database_path=root / DEFAULT_DATABASE_RELATIVE_PATH,
            log_path=root / DEFAULT_LOG_RELATIVE_PATH,
        )

    @classmethod
    def from_workspace_root(cls, workspace_root: str | Path) -> "MemoryConfig":
        return cls.default(workspace_root)

    def to_dict(self) -> dict[str, object]:
        return {
            "database_path": str(self.database_path),
            "log_path": str(self.log_path),
            "max_recent_messages": self.max_recent_messages,
            "summary_trigger_messages": self.summary_trigger_messages,
            "summary_batch_messages": self.summary_batch_messages,
            "summary_target_chars": self.summary_target_chars,
            "max_message_content_chars": self.max_message_content_chars,
            "max_event_display_chars": self.max_event_display_chars,
            "max_event_payload_chars": self.max_event_payload_chars,
            "summary_allow_rule_fallback": self.summary_allow_rule_fallback,
        }


def _coerce_bool(value: object, field_name: str) -> bool:
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


__all__ = [
    "DEFAULT_DATABASE_RELATIVE_PATH",
    "DEFAULT_LOG_RELATIVE_PATH",
    "DEFAULT_MAX_EVENT_DISPLAY_CHARS",
    "DEFAULT_MAX_EVENT_PAYLOAD_CHARS",
    "DEFAULT_MAX_MESSAGE_CONTENT_CHARS",
    "DEFAULT_MAX_RECENT_MESSAGES",
    "DEFAULT_SUMMARY_BATCH_MESSAGES",
    "DEFAULT_SUMMARY_TARGET_CHARS",
    "DEFAULT_SUMMARY_TRIGGER_MESSAGES",
    "MemoryConfig",
]
