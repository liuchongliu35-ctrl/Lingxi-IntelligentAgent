from __future__ import annotations

from pathlib import Path

from src.memory.config import MemoryConfig
from src.memory.context_builder import (
    ContextBuilder,
    NO_CURRENT_USER_INPUT_TEXT,
    NO_RECENT_MESSAGES_TEXT,
    NO_SUMMARY_TEXT,
)
from src.memory.ids import new_event_id
from src.memory.models import ExecutionEventStatus
from src.memory.session_manager import SessionManager
from src.memory.short_term_memory import ShortTermMemory


def _make_manager(tmp_path: Path, *, max_recent_messages: int = 10, summary_trigger_messages: int = 14) -> SessionManager:
    return SessionManager(
        config=MemoryConfig(
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
    )


def test_context_builder_returns_stable_empty_context(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)
    manager.create_session("empty_session")
    builder = ContextBuilder(session_manager=manager)

    result = builder.build("empty_session")

    assert result.session_id == "empty_session"
    assert result.summary == ""
    assert result.recent_messages == []
    assert result.included_message_ids == []
    assert result.included_event_ids == []
    assert result.truncated is False
    assert result.current_user_input_included is False
    assert result.context_text == "\n".join(
        [
            "[Session Summary]",
            NO_SUMMARY_TEXT,
            "",
            "[Recent Messages]",
            NO_RECENT_MESSAGES_TEXT,
            "",
            "[Current User Input]",
            NO_CURRENT_USER_INPUT_TEXT,
        ]
    )
    assert result.metadata["context_message_roles"] == ["user", "assistant"]
    assert result.metadata["event_context_policy"] == "excluded_by_default"


def test_context_builder_includes_recent_messages_and_current_input(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)
    session = manager.create_session("basic_session")
    user = manager.append_message(session.session_id, "user", "hello")
    manager.append_message(session.session_id, "assistant", "hi")
    builder = ContextBuilder(session_manager=manager)

    result = builder.build(session.session_id, current_user_input="hello again")

    assert result.summary == ""
    assert [message.content for message in result.recent_messages] == ["hello", "hi"]
    assert result.current_user_input_included is True
    assert "[Current User Input]\nhello again" in result.context_text
    assert result.truncated is False
    assert result.token_estimate is None
    assert result.char_count == len(result.context_text)


def test_context_builder_truncates_to_recent_window(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path, max_recent_messages=3)
    session = manager.create_session("window_session")
    for index in range(5):
        role = "user" if index % 2 == 0 else "assistant"
        manager.append_message(session.session_id, role, f"message-{index}")
    builder = ContextBuilder(session_manager=manager)

    result = builder.build(session.session_id)

    assert len(result.recent_messages) == 3
    assert result.truncated is True
    assert [message.content for message in result.recent_messages] == ["message-2", "message-3", "message-4"]


def test_context_builder_does_not_duplicate_existing_current_input(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)
    session = manager.create_session("dup_session")
    manager.append_message(session.session_id, "user", "repeat me")
    builder = ContextBuilder(session_manager=manager)

    result = builder.build(session.session_id, current_user_input="repeat me")

    assert result.current_user_input_included is False
    assert result.context_text.count("repeat me") == 1


def test_context_builder_ignores_visible_events_by_default(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)
    session = manager.create_session("event_session")
    user = manager.append_message(session.session_id, "user", "hello")
    run = manager.create_run(session.session_id, user.message_id)
    manager.append_execution_event(
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
    builder = ContextBuilder(session_manager=manager)

    result = builder.build(session.session_id)

    assert result.included_event_ids == []
    assert "tool_started" not in result.context_text


def test_short_term_memory_uses_context_builder_by_default(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)
    memory = ShortTermMemory(session_id="builder_session", session_manager=manager)
    memory.add_message("user", "hello")

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
