from __future__ import annotations

from typing import Any

from src.memory.config import MemoryConfig
from src.memory.models import ContextBuildResult, Message, MessageRole, MessageStatus
from src.memory.session_manager import SessionManager
from src.memory.storage import SQLiteSessionRepository


CONTEXT_MESSAGE_ROLES = (MessageRole.USER.value, MessageRole.ASSISTANT.value)
CONTEXT_MESSAGE_STATUSES = (MessageStatus.COMPLETED.value,)
NO_SUMMARY_TEXT = "No summary yet."
NO_RECENT_MESSAGES_TEXT = "No recent messages."
NO_CURRENT_USER_INPUT_TEXT = "No current user input."


class ContextBuilder:
    def __init__(
        self,
        session_manager: SessionManager | None = None,
        *,
        repo: SQLiteSessionRepository | None = None,
        config: MemoryConfig | None = None,
    ) -> None:
        if session_manager is not None and (repo is not None or config is not None):
            raise ValueError("Provide session_manager or repo/config, not both")
        self.session_manager = session_manager or SessionManager(repo=repo, config=config)
        self.config = self.session_manager.config

    def build(
        self,
        session_id: str,
        *,
        current_user_input: str | None = None,
        max_recent_messages: int | None = None,
    ) -> ContextBuildResult:
        session = self.session_manager.load_session(session_id)
        limit = self.config.max_recent_messages if max_recent_messages is None else max_recent_messages
        if limit <= 0:
            raise ValueError("max_recent_messages must be > 0")

        message_count = self.session_manager.repo.count_messages(
            session_id,
            roles=CONTEXT_MESSAGE_ROLES,
            statuses=CONTEXT_MESSAGE_STATUSES,
        )
        recent_messages = self.session_manager.repo.load_recent_messages(
            session_id,
            limit,
            roles=CONTEXT_MESSAGE_ROLES,
            statuses=CONTEXT_MESSAGE_STATUSES,
        )
        truncated = message_count > len(recent_messages)
        normalized_input = (current_user_input or "").strip()
        should_append_current_input = bool(normalized_input) and not self._current_input_already_present(
            recent_messages,
            normalized_input,
        )
        summary_text = session.summary or ""
        context_text = self._format_context_text(
            summary=summary_text,
            recent_messages=recent_messages,
            current_user_input=normalized_input if should_append_current_input else "",
        )
        result = ContextBuildResult(
            session_id=session_id,
            context_text=context_text,
            summary=summary_text,
            recent_messages=recent_messages,
            included_message_ids=[message.message_id for message in recent_messages],
            included_event_ids=[],
            truncated=truncated,
            current_user_input_included=should_append_current_input,
            token_estimate=None,
            char_count=len(context_text),
            metadata={
                "max_recent_messages": limit,
                "message_count": message_count,
                "context_message_roles": list(CONTEXT_MESSAGE_ROLES),
                "event_context_policy": "excluded_by_default",
            },
        )
        self.session_manager.repo.record_memory_event(
            "context_built",
            session_id=session_id,
            message_count=message_count,
            included_message_count=len(result.included_message_ids),
            included_message_ids=result.included_message_ids,
            included_event_count=len(result.included_event_ids),
            truncated=result.truncated,
            current_user_input_included=result.current_user_input_included,
            char_count=result.char_count,
            has_summary=bool(result.summary),
        )
        return result

    def _format_context_text(
        self,
        *,
        summary: str,
        recent_messages: list[Message],
        current_user_input: str,
    ) -> str:
        lines: list[str] = [
            "[Session Summary]",
            summary if summary else NO_SUMMARY_TEXT,
            "",
            "[Recent Messages]",
        ]
        if recent_messages:
            lines.extend(f"{message.role}: {message.content}" for message in recent_messages)
        else:
            lines.append(NO_RECENT_MESSAGES_TEXT)
        lines.extend(
            [
                "",
                "[Current User Input]",
                current_user_input if current_user_input else NO_CURRENT_USER_INPUT_TEXT,
            ]
        )
        return "\n".join(lines)

    def _current_input_already_present(
        self,
        recent_messages: list[Message],
        current_user_input: str,
    ) -> bool:
        if not recent_messages:
            return False
        last_message = recent_messages[-1]
        return (
            last_message.role == MessageRole.USER.value
            and last_message.content.strip() == current_user_input
        )


__all__ = [
    "CONTEXT_MESSAGE_ROLES",
    "CONTEXT_MESSAGE_STATUSES",
    "ContextBuilder",
    "NO_CURRENT_USER_INPUT_TEXT",
    "NO_RECENT_MESSAGES_TEXT",
    "NO_SUMMARY_TEXT",
]
