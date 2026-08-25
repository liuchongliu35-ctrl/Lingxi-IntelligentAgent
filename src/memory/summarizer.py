from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from src.memory.config import MemoryConfig
from src.memory.event_mapper import sanitize_display_content
from src.memory.models import (
    Message,
    MessageRole,
    MessageStatus,
    SessionSummary,
    SummarySource,
)
from src.memory.storage import SQLiteSessionRepository


SUMMARY_PRESERVE_KEYS = [
    "user_goal",
    "decisions",
    "constraints",
    "file_paths",
    "open_tasks",
    "preferences",
]

SUMMARY_MESSAGE_ROLES = (MessageRole.USER.value, MessageRole.ASSISTANT.value)
SUMMARY_MESSAGE_STATUSES = (MessageStatus.COMPLETED.value,)


@dataclass(slots=True)
class ConversationSummaryCandidate:
    current_summary: SessionSummary | None
    recent_messages: list[Message]
    candidate_messages: list[Message]
    recent_window_start_timeline_seq: int | None


class ConversationSummarizer:
    def __init__(
        self,
        *,
        repo: SQLiteSessionRepository,
        config: MemoryConfig,
        model_manager: Any | None,
        update_summary: Callable[..., SessionSummary],
        record_event: Callable[..., None] | None = None,
    ) -> None:
        self.repo = repo
        self.config = config
        self.model_manager = model_manager
        self.update_summary = update_summary
        self.record_event = record_event

    def summarize(self, session_id: str) -> SessionSummary | None:
        session = self.repo.load_session(session_id)
        if session is None:
            raise KeyError(f"Session not found: {session_id}")
        current_summary = self.repo.load_current_summary(session_id)
        message_count = self.repo.count_messages(
            session_id,
            roles=SUMMARY_MESSAGE_ROLES,
            statuses=SUMMARY_MESSAGE_STATUSES,
        )
        if message_count <= self.config.summary_trigger_messages:
            self._record(
                "summary_deferred",
                session_id=session_id,
                reason="threshold_not_reached",
                message_count=message_count,
                threshold=self.config.summary_trigger_messages,
                current_summary_id=getattr(current_summary, "summary_id", None),
            )
            return current_summary
        if self.model_manager is None:
            self._record(
                "summary_deferred",
                session_id=session_id,
                reason="model_manager_unavailable",
                message_count=message_count,
                threshold=self.config.summary_trigger_messages,
                current_summary_id=getattr(current_summary, "summary_id", None),
            )
            return current_summary

        candidate = self._build_candidate(session_id, current_summary=current_summary)
        if len(candidate.candidate_messages) < self.config.summary_batch_messages:
            self._record(
                "summary_skipped",
                session_id=session_id,
                reason="insufficient_candidates",
                candidate_message_count=len(candidate.candidate_messages),
                summary_batch_messages=self.config.summary_batch_messages,
                current_summary_id=getattr(current_summary, "summary_id", None),
                recent_window_start_timeline_seq=candidate.recent_window_start_timeline_seq,
            )
            return current_summary

        chunks = self._build_chunks(candidate)
        summary_metadata = {
            "trigger_reason": "memory_auto_summary",
            "summary_trigger_messages": self.config.summary_trigger_messages,
            "summary_batch_messages": self.config.summary_batch_messages,
            "max_recent_messages": self.config.max_recent_messages,
            "candidate_message_count": len(candidate.candidate_messages),
            "candidate_message_ids": [message.message_id for message in candidate.candidate_messages],
            "recent_window_start_timeline_seq": candidate.recent_window_start_timeline_seq,
            "current_summary_id": getattr(current_summary, "summary_id", None),
        }
        self._record(
            "summary_started",
            session_id=session_id,
            candidate_message_count=len(candidate.candidate_messages),
            chunk_count=len(chunks),
            current_summary_id=getattr(current_summary, "summary_id", None),
            recent_window_start_timeline_seq=candidate.recent_window_start_timeline_seq,
        )
        try:
            result = self.model_manager.compress_context(
                source_type="conversation_summary",
                chunks=chunks,
                target_chars=self.config.summary_target_chars,
                preserve_keys=SUMMARY_PRESERVE_KEYS,
                trigger_reason="memory_auto_summary",
                allow_rule_fallback=self.config.summary_allow_rule_fallback,
                max_chunk_chars=self.config.max_message_content_chars,
                metadata=summary_metadata,
            )
        except Exception as exc:
            self._record(
                "summary_failed",
                session_id=session_id,
                reason="model_exception",
                error=str(exc),
                current_summary_id=getattr(current_summary, "summary_id", None),
                candidate_message_count=len(candidate.candidate_messages),
            )
            return current_summary

        if not result.success:
            self._record(
                "summary_failed",
                session_id=session_id,
                reason="model_compression_failed",
                code=result.code,
                error=result.error,
                current_summary_id=getattr(current_summary, "summary_id", None),
                candidate_message_count=len(candidate.candidate_messages),
                compression_method=result.metadata.get("compression_method"),
            )
            return current_summary

        summary_source = self._summary_source_from_result(result)
        model_profile = self._model_profile_from_result(result)
        covered_to = candidate.candidate_messages[-1].timeline_seq
        stored_metadata = {
            **dict(result.metadata or {}),
            **summary_metadata,
            "summary_source": summary_source.value,
            "model_profile": model_profile,
            "source_count": len(result.source_refs),
        }
        summary = self.update_summary(
            session_id,
            sanitize_display_content(
                result.compressed_text,
                max_chars=self.config.summary_target_chars,
            ),
            covered_to,
            source=summary_source,
            model_profile=model_profile,
            metadata=stored_metadata,
        )
        self._record(
            "summary_completed",
            session_id=session_id,
            summary_id=summary.summary_id,
            summary_source=summary_source.value,
            model_profile=model_profile,
            covered_from_timeline_seq=summary.covered_from_timeline_seq,
            covered_to_timeline_seq=summary.covered_to_timeline_seq,
            candidate_message_count=len(candidate.candidate_messages),
        )
        return summary

    def _build_candidate(
        self,
        session_id: str,
        *,
        current_summary: SessionSummary | None,
    ) -> ConversationSummaryCandidate:
        recent_messages = self.repo.load_recent_messages(
            session_id,
            self.config.max_recent_messages,
            roles=SUMMARY_MESSAGE_ROLES,
            statuses=SUMMARY_MESSAGE_STATUSES,
        )
        recent_window_start_timeline_seq = recent_messages[0].timeline_seq if recent_messages else None
        if recent_window_start_timeline_seq is None:
            return ConversationSummaryCandidate(
                current_summary=current_summary,
                recent_messages=recent_messages,
                candidate_messages=[],
                recent_window_start_timeline_seq=None,
            )

        candidate_messages = self.repo.load_messages_before(
            session_id,
            recent_window_start_timeline_seq,
            roles=SUMMARY_MESSAGE_ROLES,
            statuses=SUMMARY_MESSAGE_STATUSES,
        )
        if current_summary is not None:
            candidate_messages = [
                message
                for message in candidate_messages
                if message.timeline_seq > current_summary.covered_to_timeline_seq
            ]
        return ConversationSummaryCandidate(
            current_summary=current_summary,
            recent_messages=recent_messages,
            candidate_messages=candidate_messages,
            recent_window_start_timeline_seq=recent_window_start_timeline_seq,
        )

    def _build_chunks(self, candidate: ConversationSummaryCandidate) -> list[dict[str, Any]]:
        chunks: list[dict[str, Any]] = []
        if candidate.current_summary is not None:
            chunks.append(
                {
                    "id": candidate.current_summary.summary_id,
                    "source_ref": f"summary:{candidate.current_summary.summary_id}",
                    "chunk_id": candidate.current_summary.summary_id,
                    "text": candidate.current_summary.content,
                    "metadata": {
                        "summary_id": candidate.current_summary.summary_id,
                        "covered_from_timeline_seq": candidate.current_summary.covered_from_timeline_seq,
                        "covered_to_timeline_seq": candidate.current_summary.covered_to_timeline_seq,
                    },
                }
            )
        for message in candidate.candidate_messages:
            chunks.append(
                {
                    "id": message.message_id,
                    "source_ref": f"message:{message.message_id}",
                    "chunk_id": message.message_id,
                    "text": f"{message.role}: {message.content}",
                    "metadata": {
                        "message_id": message.message_id,
                        "timeline_seq": message.timeline_seq,
                        "role": message.role,
                        "run_id": message.run_id,
                    },
                }
            )
        return chunks

    def _summary_source_from_result(self, result: Any) -> SummarySource:
        compression_method = str((result.metadata or {}).get("compression_method") or "").strip().lower()
        if compression_method.startswith("rule_fallback"):
            return SummarySource.RULE_FALLBACK
        return SummarySource.MODEL

    def _model_profile_from_result(self, result: Any) -> str | None:
        model_result = getattr(result, "model_result", None)
        if model_result is None:
            return None
        return getattr(model_result, "model", None) or getattr(model_result, "provider", None)

    def _record(self, event_type: str, **payload: Any) -> None:
        if self.record_event is None:
            return
        try:
            self.record_event(event_type, **payload)
        except Exception:
            return


__all__ = ["ConversationSummarizer", "ConversationSummaryCandidate", "SUMMARY_PRESERVE_KEYS"]
