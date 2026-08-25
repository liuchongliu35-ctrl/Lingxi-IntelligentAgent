from __future__ import annotations

from pathlib import Path

from src.memory.config import MemoryConfig
from src.memory.context_builder import NO_CURRENT_USER_INPUT_TEXT, NO_SUMMARY_TEXT
from src.memory.session_manager import SessionManager
from src.memory.short_term_memory import ShortTermMemory


def _make_manager(tmp_path: Path) -> SessionManager:
    return SessionManager(
        config=MemoryConfig(
            database_path=tmp_path / "memory.db",
            log_path=tmp_path / "memory.log",
            max_recent_messages=3,
            summary_trigger_messages=3,
            summary_batch_messages=1,
            summary_target_chars=2000,
            max_message_content_chars=12000,
            max_event_display_chars=1200,
            max_event_payload_chars=1000,
        )
    )


def test_short_term_memory_persists_messages_and_returns_legacy_history(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)
    memory = ShortTermMemory(session_id="compat_session", session_manager=manager)

    memory.add_message("user", "hello", metadata={"source": "legacy"})
    memory.add_message("assistant", "hi")

    assert memory.get_history() == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    assert memory.get_history_text() == "\n".join(
        [
            "[Session Summary]",
            NO_SUMMARY_TEXT,
            "",
            "[Recent Messages]",
            "user: hello",
            "assistant: hi",
            "",
            "[Current User Input]",
            NO_CURRENT_USER_INPUT_TEXT,
        ]
    )
    restored = manager.load_session("compat_session")
    assert restored.message_count == 2
    assert restored.messages[0].metadata == {"source": "legacy"}


def test_short_term_memory_uses_current_session_only(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)
    first = ShortTermMemory(session_id="first_session", session_manager=manager)
    second = ShortTermMemory(session_id="second_session", session_manager=manager)

    first.add_message("user", "first-only")
    second.add_message("user", "second-only")

    assert "user: first-only" in first.get_history_text()
    assert "user: second-only" in second.get_history_text()
    assert first.get_history_text().count("[Current User Input]") == 1
    assert second.get_history_text().count("[Current User Input]") == 1


def test_short_term_memory_limits_window_without_deleting_persisted_history(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)
    memory = ShortTermMemory(
        session_id="window_session",
        session_manager=manager,
        max_history=2,
    )

    memory.add_message("user", "one")
    memory.add_message("assistant", "two")
    memory.add_message("user", "three")

    assert memory.get_history() == [
        {"role": "assistant", "content": "two"},
        {"role": "user", "content": "three"},
    ]
    assert manager.load_session("window_session").message_count == 3


def test_short_term_memory_clear_does_not_delete_history_or_change_session(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)
    memory = ShortTermMemory(session_id="clear_session", session_manager=manager)
    memory.add_message("user", "keep me")

    memory.clear()

    assert memory.session_id == "clear_session"
    assert "user: keep me" in memory.get_history_text()
    assert manager.load_session("clear_session").message_count == 1


def test_session_manager_returns_bound_short_term_memory(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)
    manager.create_session("factory_session")

    memory = manager.get_short_term_memory("factory_session")
    memory.add_message("user", "factory works")

    assert isinstance(memory, ShortTermMemory)
    assert memory.session_id == "factory_session"
    assert manager.load_session("factory_session").message_count == 1


def test_short_term_memory_delegates_history_text_to_context_builder(tmp_path: Path) -> None:
    class FakeContextBuilder:
        def __init__(self) -> None:
            self.session_ids: list[str] = []

        def build(self, session_id: str) -> object:
            self.session_ids.append(session_id)
            return type("Result", (), {"context_text": "[Session Summary]\nready"})()

    manager = _make_manager(tmp_path)
    builder = FakeContextBuilder()
    memory = ShortTermMemory(
        session_id="context_session",
        session_manager=manager,
        context_builder=builder,
    )

    assert memory.get_history_text() == "[Session Summary]\nready"
    assert builder.session_ids == ["context_session"]
