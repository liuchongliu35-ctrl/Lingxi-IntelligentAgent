from __future__ import annotations

from pathlib import Path

from src.memory.config import MemoryConfig
from src.memory.ids import (
    new_event_id,
    new_message_id,
    new_run_id,
    new_session_id,
    new_summary_id,
)
from src.memory.models import AgentRunStatus, ExecutionEventStatus, SessionStatus
from src.memory.storage import SQLiteSessionRepository


def _make_repo(tmp_path: Path) -> SQLiteSessionRepository:
    return SQLiteSessionRepository(MemoryConfig.default(tmp_path))


def test_repository_creates_schema_and_persists_roundtrip(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    session_id = new_session_id()
    user_message_id = new_message_id()
    run_id = new_run_id()
    summary_id = new_summary_id()

    repo.create_session(session_id)
    message = repo.insert_message(
        {
            "message_id": user_message_id,
            "session_id": session_id,
            "role": "user",
            "content": "hello",
            "visible_to_user": True,
        }
    )
    run = repo.insert_run(
        {
            "run_id": run_id,
            "session_id": session_id,
            "user_message_id": message.message_id,
            "status": AgentRunStatus.RUNNING,
            "started_at": "2026-08-20T00:00:00Z",
        }
    )
    summary = repo.insert_summary(
        {
            "summary_id": summary_id,
            "session_id": session_id,
            "content": "short summary",
            "covered_from_timeline_seq": 1,
            "covered_to_timeline_seq": 1,
            "created_at": "2026-08-20T00:00:01Z",
            "source": "model",
        }
    )

    assert message.timeline_seq == 1
    assert run.status == "running"
    assert summary.source == "model"

    reopened = _make_repo(tmp_path)
    restored = reopened.load_session(session_id)
    assert restored is not None
    assert restored.session_id == session_id
    assert restored.message_count == 1
    assert restored.summary == "short summary"
    assert reopened.load_current_summary(session_id).summary_id == summary_id


def test_repository_allocates_timeline_seq_and_timeline_order(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    session_id = new_session_id()
    run_id = new_run_id()
    repo.create_session(session_id)
    repo.insert_run(
        {
            "run_id": run_id,
            "session_id": session_id,
            "user_message_id": new_message_id(),
            "status": "running",
            "started_at": "2026-08-20T00:00:00Z",
        }
    )
    first = repo.insert_message(
        {
            "message_id": new_message_id(),
            "session_id": session_id,
            "role": "user",
            "content": "hello",
            "visible_to_user": True,
        }
    )
    event = repo.insert_execution_event(
        {
            "event_id": new_event_id(),
            "session_id": session_id,
            "run_id": run_id,
            "event_type": "tool_started",
            "display_type": "tool_progress",
            "display_content": "running",
            "visible_to_user": True,
            "status": ExecutionEventStatus.STARTED,
            "created_at": "2026-08-20T00:00:01Z",
        }
    )
    second = repo.insert_message(
        {
            "message_id": new_message_id(),
            "session_id": session_id,
            "role": "assistant",
            "content": "done",
            "visible_to_user": True,
        }
    )

    assert [item.timeline_seq for item in repo.load_session_timeline(session_id)] == [
        first.timeline_seq,
        event.timeline_seq,
        second.timeline_seq,
    ]


def test_repository_skips_internal_events(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    session_id = new_session_id()
    run_id = new_run_id()
    repo.create_session(session_id)
    repo.insert_run(
        {
            "run_id": run_id,
            "session_id": session_id,
            "user_message_id": new_message_id(),
            "status": "running",
            "started_at": "2026-08-20T00:00:00Z",
        }
    )

    result = repo.insert_execution_event(
        {
            "event_id": new_event_id(),
            "session_id": session_id,
            "run_id": run_id,
            "event_type": "reasoning",
            "display_type": "plan_progress",
            "display_content": "hidden",
            "visible_to_user": False,
            "status": "recorded",
            "created_at": "2026-08-20T00:00:01Z",
        }
    )

    assert result is None
    assert repo.load_session_timeline(session_id) == []


def test_repository_marks_interrupted_runs(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    session_id = new_session_id()
    run_id = new_run_id()
    repo.create_session(session_id)
    repo.insert_run(
        {
            "run_id": run_id,
            "session_id": session_id,
            "user_message_id": new_message_id(),
            "status": "running",
            "started_at": "2026-08-20T00:00:00Z",
        }
    )

    count = repo.mark_interrupted_runs()
    restored = repo.load_run(run_id)

    assert count == 1
    assert restored.status == "interrupted"


def test_repository_deletes_session_cascade(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    session_id = new_session_id()
    message_id = new_message_id()
    repo.create_session(session_id)
    repo.insert_message(
        {
            "message_id": message_id,
            "session_id": session_id,
            "role": "user",
            "content": "hello",
            "visible_to_user": True,
        }
    )

    assert repo.delete_session(session_id) is True
    assert repo.load_session(session_id) is None
    assert repo.load_message(message_id) is None
    assert repo.delete_session(session_id) is False


def test_repository_load_messages_before_filters_by_boundary(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    session_id = new_session_id()
    repo.create_session(session_id)
    first = repo.insert_message(
        {
            "message_id": new_message_id(),
            "session_id": session_id,
            "role": "user",
            "content": "first",
            "visible_to_user": True,
        }
    )
    second = repo.insert_message(
        {
            "message_id": new_message_id(),
            "session_id": session_id,
            "role": "assistant",
            "content": "second",
            "visible_to_user": True,
        }
    )
    third = repo.insert_message(
        {
            "message_id": new_message_id(),
            "session_id": session_id,
            "role": "user",
            "content": "third",
            "visible_to_user": True,
        }
    )

    result = repo.load_messages_before(session_id, third.timeline_seq)

    assert [message.content for message in result] == ["first", "second"]
    assert result[-1].timeline_seq == second.timeline_seq
