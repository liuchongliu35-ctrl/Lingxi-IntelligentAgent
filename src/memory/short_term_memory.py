from __future__ import annotations

from typing import Any, Protocol

from src.memory.config import MemoryConfig
from src.memory.ids import new_session_id
from src.memory.models import Message, MessageRole, MessageStatus
from src.memory.session_manager import SessionManager


class _ContextBuilderProtocol(Protocol):
    def build(self, session_id: str, **kwargs: Any) -> Any:
        ...


class ShortTermMemory:
    """Compatibility facade for one SQLite-backed session.

    The facade keeps ReactAgent's existing add/read methods stable while
    SessionManager remains responsible for session lifecycle and persistence.
    """

    def __init__(
        self,
        max_history: int | None = None,
        *,
        session_id: str | None = None,
        session_manager: SessionManager | None = None,
        config: MemoryConfig | None = None,
        context_builder: _ContextBuilderProtocol | None = None,
    ) -> None:
        if session_manager is not None and config is not None:
            raise ValueError("Provide either session_manager or config, not both")
        self.session_manager = session_manager or SessionManager(config=config)
        self.session_id = session_id or new_session_id()
        self.session_manager.get_or_create_session(self.session_id)
        self.max_history = (
            self.session_manager.config.max_recent_messages
            if max_history is None
            else max_history
        )
        if self.max_history <= 0:
            raise ValueError("max_history must be > 0")
        if context_builder is None:
            from src.memory.context_builder import ContextBuilder

            context_builder = ContextBuilder(session_manager=self.session_manager)
        self.context_builder = context_builder

    def add_message(
        self,
        role: str | MessageRole,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> Message:
        return self.session_manager.append_message(
            self.session_id,
            role,
            content,
            metadata,
        )

    def get_history(self) -> list[dict[str, Any]]:
        return [
            {
                "role": message.role,
                "content": message.content,
            }
            for message in self._load_recent_messages()
        ]

    def get_history_text(self) -> str:
        return str(self.context_builder.build(self.session_id).context_text)

    def clear(self) -> None:
        """Preserve the legacy call without deleting SQLite history or switching sessions."""
        return None

    def get_last_message(self) -> dict[str, str]:
        messages = self._load_recent_messages()
        if not messages:
            return {"role": "", "content": ""}
        message = messages[-1]
        return {"role": message.role, "content": message.content}

    def get_history_length(self) -> int:
        return len(self._load_recent_messages())

    def _load_recent_messages(self) -> list[Message]:
        messages = self.session_manager.repo.load_recent_messages(
            self.session_id,
            self.max_history,
            roles=(MessageRole.USER.value, MessageRole.ASSISTANT.value),
            statuses=(MessageStatus.COMPLETED.value,),
        )
        return messages


__all__ = ["ShortTermMemory"]
