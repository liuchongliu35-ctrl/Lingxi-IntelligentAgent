from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass, replace
from datetime import datetime, timezone
from typing import Any, Callable

from src.memory.context_builder import ContextBuilder
from src.memory.event_mapper import sanitize_display_content, sanitize_payload
from src.memory.ids import new_message_id, new_run_id, new_session_id, validate_session_id
from src.memory.models import (
    AgentRun,
    AgentRunStatus,
    ContentFormat,
    ContextBuildResult,
    DisplayType,
    ExecutionEventRecord,
    Message,
    MessageRole,
    MessageStatus,
    SessionState,
    SessionSummary,
    TimelineItem,
)
from src.memory.session_manager import SessionManager
from src.memory.storage import SCHEMA_VERSION


def _json_safe(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _persistence_warning(error: Any) -> str:
    error_type = error.__class__.__name__ if hasattr(error, "__class__") else "PersistenceError"
    detail = sanitize_display_content(str(error or ""), max_chars=240)
    suffix = f": {detail}" if detail else ""
    return f"Memory persistence unavailable; this turn will not be persisted ({error_type}{suffix})"


@dataclass
class RuntimeMemoryTurn:
    session: SessionState
    user_message: Message
    run: AgentRun
    context: ContextBuildResult
    short_term_memory: Any
    persistence_available: bool = True
    persistence_warning: str | None = None

    @property
    def session_id(self) -> str:
        return self.session.session_id

    @property
    def run_id(self) -> str:
        return self.run.run_id

    @property
    def user_message_id(self) -> str:
        return self.user_message.message_id

    @property
    def context_text(self) -> str:
        return self.context.context_text

    def react_agent_kwargs(self) -> dict[str, Any]:
        return {
            "context_text": self.context_text,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "manage_memory": False,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "session": _json_safe(self.session),
            "user_message": _json_safe(self.user_message),
            "run": _json_safe(self.run),
            "context": _json_safe(self.context),
            "react_agent_kwargs": self.react_agent_kwargs(),
            "persistence_available": self.persistence_available,
            "persistence_warning": self.persistence_warning,
        }


@dataclass
class RuntimeMemoryResult:
    session_id: str
    run_id: str
    success: bool
    run: AgentRun
    user_message: Message
    assistant_message: Message | None = None
    summary: SessionSummary | None = None
    timeline: list[TimelineItem] | None = None
    error_code: str | None = None
    error_message: str | None = None
    persistence_available: bool = True
    persistence_warning: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(self)


@dataclass
class MemoryHealthStatus:
    ok: bool
    database_path: str
    schema_version: int
    session_count: int = 0
    error_code: str | None = None
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(self)


class RuntimeMemoryAdapter:
    """Memory-side facade reserved for future Runtime / CLI / API entrypoints."""

    def __init__(
        self,
        session_manager: SessionManager | None = None,
        *,
        context_builder: ContextBuilder | None = None,
    ) -> None:
        self.session_manager = session_manager or SessionManager()
        self.context_builder = context_builder or ContextBuilder(session_manager=self.session_manager)

    def begin_turn(
        self,
        session_id: str | None,
        user_input: str,
        *,
        user_metadata: dict[str, Any] | None = None,
        session_title: str | None = None,
        session_metadata: dict[str, Any] | None = None,
        max_recent_messages: int | None = None,
        agent_version: str | None = None,
        model_profile: str | None = None,
    ) -> RuntimeMemoryTurn:
        resolved_session_id = (
            new_session_id() if session_id is None else validate_session_id(session_id)
        )
        try:
            user_message, run = self.session_manager.create_user_turn(
                resolved_session_id,
                user_input,
                metadata=user_metadata,
                title=session_title,
                session_metadata=session_metadata,
                agent_version=agent_version,
                model_profile=model_profile,
            )
            session = self.session_manager.load_session(user_message.session_id)
            context = self.context_builder.build(
                user_message.session_id,
                current_user_input=user_input,
                max_recent_messages=max_recent_messages,
            )
            short_term_memory = self.session_manager.get_short_term_memory(
                user_message.session_id
            )
            persistence_available = True
            persistence_warning = None
        except Exception as exc:
            persistence_available = False
            persistence_warning = _persistence_warning(exc)
            self._record_persistence_warning(
                resolved_session_id,
                operation="begin_turn",
                error=exc,
            )
            user_message, run, session, context = self._build_ephemeral_turn(
                resolved_session_id,
                user_input,
                user_metadata=user_metadata,
                session_metadata=session_metadata,
                agent_version=agent_version,
                model_profile=model_profile,
            )
            short_term_memory = None
        turn = RuntimeMemoryTurn(
            session=session,
            user_message=user_message,
            run=run,
            context=context,
            short_term_memory=short_term_memory,
            persistence_available=persistence_available,
            persistence_warning=persistence_warning,
        )
        self._record_memory_event(
            "runtime_turn_started",
            session_id=turn.session_id,
            run_id=turn.run_id,
            user_message_id=turn.user_message_id,
            context_char_count=turn.context.char_count,
            included_message_count=len(turn.context.included_message_ids),
            persistence_available=turn.persistence_available,
        )
        return turn

    def event_callback(
        self,
        turn: RuntimeMemoryTurn,
        *,
        external_callback: Callable[[Any], None] | None = None,
        external_visible_only: bool = True,
    ) -> Callable[[Any], None]:
        def callback(event: Any) -> None:
            self.record_event(turn, event)
            if external_callback is not None and (
                not external_visible_only or self._event_visible_to_user(event)
            ):
                external_callback(event)

        return callback

    def record_event(
        self,
        turn: RuntimeMemoryTurn,
        event: ExecutionEventRecord | dict[str, Any] | Any,
    ) -> ExecutionEventRecord | None:
        if not turn.persistence_available:
            return None
        try:
            return self.session_manager.append_execution_event(turn.session_id, turn.run_id, event)
        except Exception as exc:
            self._mark_persistence_failure(turn, "event", exc)
            return None

    def record_events(
        self,
        turn: RuntimeMemoryTurn,
        events: list[Any] | tuple[Any, ...],
    ) -> list[ExecutionEventRecord]:
        return self.session_manager.append_execution_events(turn.session_id, turn.run_id, events)

    def complete_turn(
        self,
        turn: RuntimeMemoryTurn,
        assistant_content: str,
        *,
        assistant_metadata: dict[str, Any] | None = None,
        content_format: str | ContentFormat = ContentFormat.TEXT,
        display_type: str | DisplayType = DisplayType.FINAL_ANSWER,
        maybe_summarize: bool = True,
        include_timeline: bool = True,
    ) -> RuntimeMemoryResult:
        if not turn.persistence_available:
            assistant_message = self._build_ephemeral_assistant(turn, assistant_content)
            completed_run = replace(
                turn.run,
                status=AgentRunStatus.COMPLETED.value,
                finished_at=_now_iso(),
                final_message_id=assistant_message.message_id,
            )
            return RuntimeMemoryResult(
                session_id=turn.session_id,
                run_id=turn.run_id,
                success=True,
                run=completed_run,
                user_message=turn.user_message,
                assistant_message=assistant_message,
                persistence_available=False,
                persistence_warning=turn.persistence_warning,
            )
        try:
            assistant_message = self.session_manager.append_message(
                turn.session_id,
                "assistant",
                assistant_content,
                metadata=assistant_metadata,
                run_id=turn.run_id,
                content_format=content_format,
                display_type=display_type,
            )
        except Exception as exc:
            self._mark_persistence_failure(turn, "assistant_message", exc)
            assistant_message = self._build_ephemeral_assistant(
                turn,
                assistant_content,
                metadata=assistant_metadata,
                content_format=content_format,
                display_type=display_type,
            )
            completed_run = replace(
                turn.run,
                status=AgentRunStatus.COMPLETED.value,
                finished_at=_now_iso(),
                final_message_id=assistant_message.message_id,
            )
            return RuntimeMemoryResult(
                session_id=turn.session_id,
                run_id=turn.run_id,
                success=True,
                run=completed_run,
                user_message=turn.user_message,
                assistant_message=assistant_message,
                persistence_available=False,
                persistence_warning=turn.persistence_warning,
            )
        try:
            completed_run = self.session_manager.complete_run(turn.run_id, assistant_message.message_id)
        except Exception as exc:
            self._mark_persistence_failure(turn, "complete_run", exc)
            completed_run = replace(
                turn.run,
                status=AgentRunStatus.COMPLETED.value,
                finished_at=_now_iso(),
                final_message_id=assistant_message.message_id,
            )
            return RuntimeMemoryResult(
                session_id=turn.session_id,
                run_id=turn.run_id,
                success=True,
                run=completed_run,
                user_message=turn.user_message,
                assistant_message=assistant_message,
                persistence_available=False,
                persistence_warning=turn.persistence_warning,
            )
        try:
            summary = self.session_manager.maybe_auto_summarize(turn.session_id) if maybe_summarize else None
            timeline = self.session_manager.get_session_timeline(turn.session_id) if include_timeline else None
        except Exception as exc:
            self._mark_persistence_failure(turn, "post_turn_finalize", exc)
            summary = None
            timeline = None
        self.session_manager.repo.record_memory_event(
            "runtime_turn_completed",
            session_id=turn.session_id,
            run_id=turn.run_id,
            assistant_message_id=assistant_message.message_id,
            summary_id=getattr(summary, "summary_id", None),
        )
        return RuntimeMemoryResult(
            session_id=turn.session_id,
            run_id=turn.run_id,
            success=True,
            run=completed_run or turn.run,
            user_message=turn.user_message,
            assistant_message=assistant_message,
            summary=summary,
            timeline=timeline,
            persistence_available=turn.persistence_available,
            persistence_warning=turn.persistence_warning,
        )

    def fail_turn(
        self,
        turn: RuntimeMemoryTurn,
        error: Any,
        *,
        maybe_summarize: bool = True,
        include_timeline: bool = True,
    ) -> RuntimeMemoryResult:
        if not turn.persistence_available:
            failed_run = replace(
                turn.run,
                status=AgentRunStatus.FAILED.value,
                finished_at=_now_iso(),
                error_code=getattr(error, "__class__", type(error)).__name__,
                error_message=sanitize_display_content(str(error), max_chars=240),
            )
            return RuntimeMemoryResult(
                session_id=turn.session_id,
                run_id=turn.run_id,
                success=False,
                run=failed_run,
                user_message=turn.user_message,
                error_code=failed_run.error_code,
                error_message=failed_run.error_message,
                persistence_available=False,
                persistence_warning=turn.persistence_warning,
            )
        try:
            failed_run = self.session_manager.fail_run(turn.run_id, error)
            summary = self.session_manager.maybe_auto_summarize(turn.session_id) if maybe_summarize else None
            timeline = self.session_manager.get_session_timeline(turn.session_id) if include_timeline else None
        except Exception as exc:
            self._mark_persistence_failure(turn, "fail_turn", exc)
            failed_run = None
            summary = None
            timeline = None
        run = failed_run or turn.run
        self.session_manager.repo.record_memory_event(
            "runtime_turn_failed",
            session_id=turn.session_id,
            run_id=turn.run_id,
            error_code=getattr(run, "error_code", None),
            error_message=getattr(run, "error_message", None),
            summary_id=getattr(summary, "summary_id", None),
        )
        return RuntimeMemoryResult(
            session_id=turn.session_id,
            run_id=turn.run_id,
            success=False,
            run=run,
            user_message=turn.user_message,
            summary=summary,
            timeline=timeline,
            error_code=getattr(run, "error_code", None),
            error_message=getattr(run, "error_message", None),
            persistence_available=turn.persistence_available,
            persistence_warning=turn.persistence_warning,
        )

    def get_session(self, session_id: str) -> SessionState:
        return self.session_manager.load_session(session_id)

    def get_timeline(self, session_id: str) -> list[TimelineItem]:
        return self.session_manager.get_session_timeline(session_id)

    def health(self) -> MemoryHealthStatus:
        try:
            sessions = self.session_manager.list_sessions()
            return MemoryHealthStatus(
                ok=True,
                database_path=str(self.session_manager.config.database_path),
                schema_version=SCHEMA_VERSION,
                session_count=len(sessions),
            )
        except Exception as exc:
            return MemoryHealthStatus(
                ok=False,
                database_path=str(self.session_manager.config.database_path),
                schema_version=SCHEMA_VERSION,
                error_code=exc.__class__.__name__,
                error_message=str(exc),
            )

    def _event_visible_to_user(self, event: Any) -> bool:
        if isinstance(event, dict):
            return bool(event.get("visible_to_user", True))
        return bool(getattr(event, "visible_to_user", True))

    def _record_memory_event(self, event_type: str, **payload: Any) -> None:
        try:
            self.session_manager.repo.record_memory_event(event_type, **payload)
        except Exception:
            return

    def _record_persistence_warning(
        self,
        session_id: str,
        *,
        operation: str,
        error: Any,
    ) -> None:
        self._record_memory_event(
            "persistence_warning",
            session_id=session_id,
            operation=operation,
            error_code=error.__class__.__name__,
            error_message=sanitize_display_content(str(error), max_chars=240),
            original_preserved=True,
            persisted=False,
        )

    def _mark_persistence_failure(
        self,
        turn: RuntimeMemoryTurn,
        operation: str,
        error: Any,
    ) -> None:
        turn.persistence_available = False
        turn.persistence_warning = _persistence_warning(error)
        self._record_persistence_warning(
            turn.session_id,
            operation=operation,
            error=error,
        )

    def _build_ephemeral_turn(
        self,
        session_id: str,
        user_input: str,
        *,
        user_metadata: dict[str, Any] | None,
        session_metadata: dict[str, Any] | None,
        agent_version: str | None,
        model_profile: str | None,
    ) -> tuple[Message, AgentRun, SessionState, ContextBuildResult]:
        created_at = _now_iso()
        user_message = Message(
            message_id=new_message_id(),
            session_id=session_id,
            timeline_seq=1,
            role=MessageRole.USER.value,
            content=user_input,
            content_format=ContentFormat.TEXT.value,
            display_type=DisplayType.CHAT.value,
            visible_to_user=True,
            status=MessageStatus.COMPLETED.value,
            run_id=new_run_id(),
            created_at=created_at,
            metadata=sanitize_payload(
                user_metadata or {},
                max_text_chars=self.session_manager.config.max_event_payload_chars,
            ),
        )
        run = AgentRun(
            run_id=user_message.run_id or new_run_id(),
            session_id=session_id,
            user_message_id=user_message.message_id,
            status=AgentRunStatus.RUNNING.value,
            started_at=created_at,
            agent_version=agent_version,
            model_profile=model_profile,
        )
        session = SessionState(
            session_id=session_id,
            messages=[user_message],
            metadata=sanitize_payload(
                session_metadata or {},
                max_text_chars=self.session_manager.config.max_event_payload_chars,
            ),
        )
        context_text = "\n".join(
            [
                "[Session Summary]",
                "No summary yet.",
                "",
                "[Recent Messages]",
                f"user: {user_input}",
                "",
                "[Current User Input]",
                "No current user input.",
            ]
        )
        context = ContextBuildResult(
            session_id=session_id,
            context_text=context_text,
            recent_messages=[user_message],
            included_message_ids=[user_message.message_id],
            current_user_input_included=False,
            char_count=len(context_text),
            metadata={
                "persistence_available": False,
                "persistence_warning": "turn_will_not_be_persisted",
            },
        )
        return user_message, run, session, context

    def _build_ephemeral_assistant(
        self,
        turn: RuntimeMemoryTurn,
        content: str,
        *,
        metadata: dict[str, Any] | None = None,
        content_format: str | ContentFormat = ContentFormat.TEXT,
        display_type: str | DisplayType = DisplayType.FINAL_ANSWER,
    ) -> Message:
        return Message(
            message_id=new_message_id(),
            session_id=turn.session_id,
            timeline_seq=2,
            role=MessageRole.ASSISTANT.value,
            content=content,
            content_format=content_format,
            display_type=display_type,
            visible_to_user=True,
            status=MessageStatus.COMPLETED.value,
            run_id=turn.run_id,
            created_at=_now_iso(),
            metadata=sanitize_payload(
                metadata or {},
                max_text_chars=self.session_manager.config.max_event_payload_chars,
            ),
        )


__all__ = [
    "MemoryHealthStatus",
    "RuntimeMemoryAdapter",
    "RuntimeMemoryResult",
    "RuntimeMemoryTurn",
]
