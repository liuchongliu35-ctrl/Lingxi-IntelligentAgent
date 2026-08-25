from __future__ import annotations

from pathlib import Path

from src.memory.config import MemoryConfig
from src.memory.context_builder import (
    NO_CURRENT_USER_INPUT_TEXT,
    NO_RECENT_MESSAGES_TEXT,
    NO_SUMMARY_TEXT,
)
from src.memory.ids import new_event_id
from src.memory.models import AgentRunStatus, ExecutionEventStatus
from src.memory.session_manager import SessionManager


def _make_manager(tmp_path: Path, *, max_recent_messages: int = 10, summary_trigger_messages: int = 14) -> SessionManager:
    config = MemoryConfig(
        database_path=tmp_path / "memory.db",
        log_path=tmp_path / "memory.log",
        max_recent_messages=max_recent_messages,
        summary_trigger_messages=summary_trigger_messages,
        summary_batch_messages=1,
        summary_target_chars=2000,
        max_message_content_chars=12000,
        max_event_display_chars=1200,
        max_event_payload_chars=1000,
    )
    return SessionManager(config=config)


def test_session_manager_create_load_and_delete_session(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)

    session = manager.create_session()
    loaded = manager.load_session(session.session_id)
    sessions = manager.list_sessions()

    assert loaded.session_id == session.session_id
    assert loaded.message_count == 0
    assert sessions[0].session_id == session.session_id
    assert manager.delete_session(session.session_id) is True
    assert manager.delete_session(session.session_id) is False


def test_session_manager_get_or_create_session(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)

    session = manager.get_or_create_session("custom_session")

    assert session.session_id == "custom_session"
    assert manager.load_session("custom_session").session_id == "custom_session"


def test_session_manager_create_user_turn_is_atomic(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)

    message, run = manager.create_user_turn(
        None,
        "hello",
        metadata={"source": "cli"},
        session_metadata={"channel": "cli"},
    )
    session = manager.load_session(message.session_id)
    row = manager.repo.load_session_row(message.session_id)

    assert message.session_id == run.session_id
    assert message.role == "user"
    assert run.status == AgentRunStatus.RUNNING.value
    assert session.message_count == 1
    assert session.messages[0].message_id == message.message_id
    assert session.messages[0].run_id == run.run_id
    assert row is not None
    assert row.last_run_id == run.run_id


def test_session_manager_message_run_event_and_timeline(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)
    session = manager.create_session("timeline_session")

    user_message = manager.append_message(session.session_id, "user", "hello")
    run = manager.create_run(session.session_id, user_message.message_id)
    visible_event = manager.append_execution_event(
        session.session_id,
        run.run_id,
        {
            "event_id": new_event_id(),
            "session_id": session.session_id,
            "run_id": run.run_id,
            "event_type": "tool_started",
            "display_type": "tool_progress",
            "display_content": "running",
            "visible_to_user": True,
            "status": ExecutionEventStatus.STARTED.value,
            "created_at": "2026-08-20T00:00:01Z",
        },
    )
    hidden_event = manager.append_execution_event(
        session.session_id,
        run.run_id,
        {
            "event_id": new_event_id(),
            "session_id": session.session_id,
            "run_id": run.run_id,
            "event_type": "reasoning",
            "display_type": "plan_progress",
            "display_content": "hidden",
            "visible_to_user": False,
            "status": ExecutionEventStatus.RECORDED.value,
            "created_at": "2026-08-20T00:00:02Z",
        },
    )
    assistant_message = manager.append_message(
        session.session_id,
        "assistant",
        "done",
        run_id=run.run_id,
    )
    completed = manager.complete_run(run.run_id, assistant_message.message_id)

    timeline = manager.get_session_timeline(session.session_id)

    assert visible_event is not None
    assert hidden_event is None
    assert completed is not None
    assert completed.status == "completed"
    assert completed.final_message_id == assistant_message.message_id
    assert [item.item_kind for item in timeline] == ["message", "execution_event", "message"]
    assert timeline[1].item_id == visible_event.event_id


def test_session_manager_update_summary_tracks_coverage(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)
    session = manager.create_session("summary_session")

    first = manager.append_message(session.session_id, "user", "first")
    run = manager.create_run(session.session_id, first.message_id)
    assistant = manager.append_message(session.session_id, "assistant", "reply", run_id=run.run_id)
    summary_one = manager.update_summary(session.session_id, "first summary", 2)
    second = manager.append_message(session.session_id, "user", "second")
    summary_two = manager.update_summary(session.session_id, "second summary", 3)

    assert summary_one.covered_from_timeline_seq == 1
    assert summary_one.covered_to_timeline_seq == 2
    assert summary_two.covered_from_timeline_seq == 3
    assert summary_two.covered_to_timeline_seq == 3
    assert manager.load_session(session.session_id).summary == "second summary"
    assert manager.repo.load_current_summary(session.session_id).summary_id == summary_two.summary_id
    assert assistant.session_id == second.session_id == session.session_id


def test_session_manager_deferred_auto_summary_and_short_term_memory_factory(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path, max_recent_messages=1, summary_trigger_messages=1)
    session = manager.create_session("hook_session")
    manager.append_message(session.session_id, "user", "hello")

    assert manager.maybe_auto_summarize(session.session_id) is None
    memory = manager.get_short_term_memory(session.session_id)
    assert memory.session_id == session.session_id
    assert memory.get_history_text() == "\n".join(
        [
            "[Session Summary]",
            NO_SUMMARY_TEXT,
            "",
            "[Recent Messages]",
            "user: hello",
            "",
            "[Current User Input]",
            NO_CURRENT_USER_INPUT_TEXT,
        ]
    )
