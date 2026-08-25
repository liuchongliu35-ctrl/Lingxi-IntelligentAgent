from __future__ import annotations
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from src.memory.config import MemoryConfig
from src.memory.event_mapper import event_log_preview, map_execution_event
from src.memory.ids import new_message_id, new_run_id, new_session_id, new_summary_id
from src.memory.models import (
    AgentRun,
    AgentRunStatus,
    ContentFormat,
    DisplayType,
    ExecutionEventRecord,
    Message,
    MessageRole,
    MessageStatus,
    SessionInfo,
    SessionState,
    SessionStatus,
    SessionSummary,
    SummarySource,
    TimelineItem,
)
from src.memory.storage import SQLiteSessionRepository


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _coerce_error_payload(error: Any) -> dict[str, str | None]:
    if error is None:
        return {"status": AgentRunStatus.FAILED.value, "error_code": None, "error_message": ""}
    if isinstance(error, Mapping):
        return {
            "status": str(error.get("status") or AgentRunStatus.FAILED.value),
            "error_code": (
                str(error.get("error_code") or error.get("code") or error.get("type"))
                if error.get("error_code") or error.get("code") or error.get("type")
                else None
            ),
            "error_message": str(
                error.get("error_message")
                or error.get("message")
                or error.get("detail")
                or ""
            ),
        }
    if isinstance(error, BaseException):
        return {
            "status": AgentRunStatus.FAILED.value,
            "error_code": error.__class__.__name__,
            "error_message": str(error),
        }
    return {
        "status": AgentRunStatus.FAILED.value,
        "error_code": error.__class__.__name__ if hasattr(error, "__class__") else None,
        "error_message": str(error),
    }


class SessionManager:
    def __init__(
        self,
        repo: SQLiteSessionRepository | None = None,
        *,
        config: MemoryConfig | None = None,
        model_manager: Any | None = None,
    ) -> None:
        if repo is None:
            repo = SQLiteSessionRepository(config or MemoryConfig.default())
        self.repo = repo
        self.config = repo.config
        self.model_manager = model_manager

    def create_session(
        self,
        session_id: str | None = None,
        *,
        title: str | None = None,
        status: str | SessionStatus = SessionStatus.ACTIVE,
        metadata: dict[str, Any] | None = None,
    ) -> SessionState:
        resolved_session_id = new_session_id() if session_id is None else session_id
        return self.repo.create_session(
            resolved_session_id,
            title=title,
            status=status,
            metadata=metadata,
        )

    def load_session(self, session_id: str) -> SessionState:
        session = self.repo.load_session(session_id)
        if session is None:
            raise KeyError(f"Session not found: {session_id}")
        return session

    def get_or_create_session(
        self,
        session_id: str | None = None,
        *,
        title: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SessionState:
        if session_id is None:
            return self.create_session(None, title=title, metadata=metadata)
        session = self.repo.load_session(session_id)
        if session is not None:
            return session
        return self.create_session(session_id, title=title, metadata=metadata)

    def delete_session(self, session_id: str) -> bool:
        return self.repo.delete_session(session_id)

    def list_sessions(self) -> list[SessionInfo]:
        return self.repo.list_sessions()

    def recover_interrupted_runs(self) -> int:
        count = self.repo.mark_interrupted_runs()
        self.repo.record_memory_event(
            "recovery_completed",
            interrupted_run_count=count,
        )
        return count

    def create_user_turn(
        self,
        session_id: str | None,
        content: str,
        *,
        metadata: dict[str, Any] | None = None,
        title: str | None = None,
        session_metadata: dict[str, Any] | None = None,
        agent_version: str | None = None,
        model_profile: str | None = None,
    ) -> tuple[Message, AgentRun]:
        return self.repo.create_user_turn(
            session_id,
            content=content,
            metadata=metadata,
            title=title,
            session_metadata=session_metadata,
            agent_version=agent_version,
            model_profile=model_profile,
            role=MessageRole.USER,
            content_format=ContentFormat.TEXT,
            display_type=DisplayType.CHAT,
            visible_to_user=True,
            status=MessageStatus.COMPLETED,
            run_status=AgentRunStatus.RUNNING,
        )

    def append_message(
        self,
        session_id: str,
        role: str | MessageRole,
        content: str,
        metadata: dict[str, Any] | None = None,
        *,
        run_id: str | None = None,
        content_format: str | ContentFormat = ContentFormat.TEXT,
        display_type: str | DisplayType = DisplayType.CHAT,
        visible_to_user: bool = True,
        status: str | MessageStatus = MessageStatus.COMPLETED,
        parent_message_id: str | None = None,
    ) -> Message:
        self.load_session(session_id)
        if run_id is not None:
            run = self.repo.load_run(run_id)
            if run is None:
                raise KeyError(f"Run not found: {run_id}")
            if run.session_id != session_id:
                raise ValueError(f"Run {run_id} does not belong to session {session_id}")
        return self.repo.insert_message(
            {
                "message_id": new_message_id(),
                "session_id": session_id,
                "run_id": run_id,
                "role": role,
                "content": content,
                "content_format": content_format,
                "display_type": display_type,
                "visible_to_user": visible_to_user,
                "status": status,
                "parent_message_id": parent_message_id,
                "metadata": metadata or {},
            }
        )

    def create_run(
        self,
        session_id: str,
        user_message_id: str,
        *,
        status: str | AgentRunStatus = AgentRunStatus.RUNNING,
        started_at: str | None = None,
        agent_version: str | None = None,
        model_profile: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AgentRun:
        session = self.load_session(session_id)
        user_message = self.repo.load_message(user_message_id)
        if user_message is None:
            raise KeyError(f"Message not found: {user_message_id}")
        if user_message.session_id != session.session_id:
            raise ValueError(
                f"Message {user_message_id} does not belong to session {session_id}"
            )
        if user_message.role != MessageRole.USER.value:
            raise ValueError(f"Message {user_message_id} is not a user message")
        run_id = new_run_id()
        return self.repo.insert_run(
            AgentRun(
                run_id=run_id,
                session_id=session.session_id,
                user_message_id=user_message_id,
                status=status,
                started_at=started_at or user_message.created_at,
                agent_version=agent_version,
                model_profile=model_profile,
                metadata=metadata or {},
            )
        )

    def append_execution_event(
        self,
        session_id: str,
        run_id: str,
        event: ExecutionEventRecord | dict[str, Any] | Any,
    ) -> ExecutionEventRecord | None:
        session = self.load_session(session_id)
        run = self.repo.load_run(run_id)
        if run is None:
            raise KeyError(f"Run not found: {run_id}")
        if run.session_id != session.session_id:
            raise ValueError(f"Run {run_id} does not belong to session {session_id}")
        mapped_from_executor = False
        if isinstance(event, ExecutionEventRecord):
            payload = event.to_dict()
        elif isinstance(event, dict) and "event_type" in event and "display_content" in event:
            payload = dict(event)
        else:
            mapped_from_executor = True
            mapped = map_execution_event(
                event,
                session_id=session.session_id,
                run_id=run_id,
                config=self.config,
            )
            if mapped is None:
                self.repo.record_memory_event(
                    "event_skipped_internal",
                    session_id=session.session_id,
                    run_id=run_id,
                    **event_log_preview(event),
                )
                return None
            payload = mapped.to_dict()
            payload.pop("timeline_seq", None)
        event_session_id = payload.get("session_id")
        if event_session_id is not None and event_session_id != session.session_id:
            raise ValueError(
                f"Event session_id {event_session_id} does not match session {session_id}"
            )
        event_run_id = payload.get("run_id")
        if event_run_id is not None and event_run_id != run_id:
            raise ValueError(f"Event run_id {event_run_id} does not match run {run_id}")
        payload["session_id"] = session.session_id
        payload["run_id"] = run_id
        stored = self.repo.insert_execution_event(payload)
        if mapped_from_executor and stored is not None:
            self.repo.record_memory_event(
                "event_persisted",
                session_id=session.session_id,
                run_id=run_id,
                event_id=stored.event_id,
                persisted_event_type=stored.event_type,
                status=stored.status,
                content_length=len(stored.display_content),
                payload_keys=sorted(str(key) for key in stored.sanitized_payload.keys())[:20],
            )
        return stored

    def append_execution_events(
        self,
        session_id: str,
        run_id: str,
        events: list[Any] | tuple[Any, ...],
    ) -> list[ExecutionEventRecord]:
        stored_events: list[ExecutionEventRecord] = []
        for event in events:
            stored = self.append_execution_event(session_id, run_id, event)
            if stored is not None:
                stored_events.append(stored)
        return stored_events

    def complete_run(self, run_id: str, final_message_id: str) -> AgentRun | None:
        run = self.repo.load_run(run_id)
        if run is None:
            raise KeyError(f"Run not found: {run_id}")
        final_message = self.repo.load_message(final_message_id)
        if final_message is None:
            raise KeyError(f"Message not found: {final_message_id}")
        if final_message.session_id != run.session_id:
            raise ValueError(
                f"Message {final_message_id} does not belong to session {run.session_id}"
            )
        if final_message.role != MessageRole.ASSISTANT.value:
            raise ValueError(f"Message {final_message_id} is not an assistant message")
        if final_message.run_id != run_id:
            raise ValueError(f"Message {final_message_id} does not belong to run {run_id}")
        return self.repo.complete_run(run_id, final_message_id)

    def fail_run(self, run_id: str, error: Any) -> AgentRun | None:
        run = self.repo.load_run(run_id)
        if run is None:
            raise KeyError(f"Run not found: {run_id}")
        payload = _coerce_error_payload(error)
        return self.repo.fail_run(
            run_id,
            error_code=payload["error_code"],
            error_message=payload["error_message"],
            status=payload["status"] or AgentRunStatus.FAILED.value,
        )

    def get_session_timeline(self, session_id: str) -> list[TimelineItem]:
        self.load_session(session_id)
        return self.repo.load_session_timeline(session_id)

    def update_summary(
        self,
        session_id: str,
        summary: str,
        covered_to_timeline_seq: int,
        *,
        source: str | SummarySource = SummarySource.MANUAL,
        model_profile: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SessionSummary:
        self.load_session(session_id)
        current_summary = self.repo.load_current_summary(session_id)
        covered_from = 1 if current_summary is None else current_summary.covered_to_timeline_seq + 1
        if covered_to_timeline_seq < covered_from:
            raise ValueError(
                "covered_to_timeline_seq must not move backwards "
                f"(current start {covered_from}, got {covered_to_timeline_seq})"
            )
        session_row = self.repo.load_session_row(session_id)
        max_timeline_seq = (session_row.next_timeline_seq - 1) if session_row else 0
        if covered_to_timeline_seq > max_timeline_seq:
            raise ValueError(
                "covered_to_timeline_seq must not exceed the current session timeline "
                f"(max {max_timeline_seq}, got {covered_to_timeline_seq})"
            )
        return self.repo.insert_summary(
            {
                "summary_id": new_summary_id(),
                "session_id": session_id,
                "content": summary,
                "covered_from_timeline_seq": covered_from,
                "covered_to_timeline_seq": covered_to_timeline_seq,
                "created_at": _now_iso(),
                "source": source,
                "model_profile": model_profile,
                "metadata": metadata or {},
            }
        )

    def maybe_auto_summarize(self, session_id: str) -> SessionSummary | None:
        from src.memory.summarizer import ConversationSummarizer

        self.load_session(session_id)
        current_summary = self.repo.load_current_summary(session_id)
        message_count = self.repo.count_messages(
            session_id,
            roles=("user", "assistant"),
            statuses=("completed",),
        )
        if message_count <= self.config.summary_trigger_messages:
            self.repo.record_memory_event(
                "summary_deferred",
                session_id=session_id,
                reason="threshold_not_reached",
                message_count=message_count,
                threshold=self.config.summary_trigger_messages,
                current_summary_id=getattr(current_summary, "summary_id", None),
            )
            return current_summary
        if self.model_manager is None:
            self.repo.record_memory_event(
                "summary_deferred",
                session_id=session_id,
                reason="model_manager_unavailable",
                message_count=message_count,
                threshold=self.config.summary_trigger_messages,
                current_summary_id=getattr(current_summary, "summary_id", None),
            )
            return current_summary
        summarizer = ConversationSummarizer(
            repo=self.repo,
            config=self.config,
            model_manager=self.model_manager,
            update_summary=self.update_summary,
            record_event=self.repo.record_memory_event,
        )
        return summarizer.summarize(session_id)

    def get_short_term_memory(self, session_id: str) -> Any:
        self.load_session(session_id)
        from src.memory.short_term_memory import ShortTermMemory

        return ShortTermMemory(
            session_id=session_id,
            session_manager=self,
        )


__all__ = ["SessionManager"]
