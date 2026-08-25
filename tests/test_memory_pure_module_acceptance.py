from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.memory.config import MemoryConfig
from src.memory.context_builder import ContextBuilder
from src.memory.event_mapper import REDACTED_VALUE
from src.memory.ids import new_event_id, new_message_id, new_run_id
from src.memory.session_manager import SessionManager
from src.memory.storage import SQLiteSessionRepository


def _make_config(
    tmp_path: Path,
    *,
    max_recent_messages: int = 10,
    summary_trigger_messages: int = 14,
    summary_batch_messages: int = 6,
) -> MemoryConfig:
    return MemoryConfig(
        database_path=tmp_path / "memory.db",
        log_path=tmp_path / "memory.log",
        max_recent_messages=max_recent_messages,
        summary_trigger_messages=summary_trigger_messages,
        summary_batch_messages=summary_batch_messages,
        summary_target_chars=2000,
        max_message_content_chars=12000,
        max_event_display_chars=1200,
        max_event_payload_chars=1000,
    )


class FakeModelManager:
    def __init__(self, result: object | None = None, *, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[dict[str, object]] = []

    def compress_context(self, **kwargs):
        self.calls.append(dict(kwargs))
        if self.error is not None:
            raise self.error
        return self.result


def _compression_result(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        success=True,
        code=None,
        error=None,
        compressed_text=text,
        source_refs=["message:first"],
        metadata={"compression_method": "single_model_call"},
        model_result=SimpleNamespace(model="mock-summary", provider="mock"),
    )


def _executor_event(**overrides) -> SimpleNamespace:
    values = {
        "execution_id": "exec_1",
        "plan_id": "plan_1",
        "type": "tool_finished",
        "message": "Tool completed with token=secret-token.",
        "event_id": "event_acceptance111",
        "task_id": "task_1",
        "step_id": "step_1",
        "timestamp": "2026-08-20T01:02:03Z",
        "visible_to_user": True,
        "payload": {
            "tool_name": "math_calculator",
            "summary": "5",
            "api_key": "secret",
            "raw_observation": "hidden raw result",
        },
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_pure_memory_session_context_and_sqlite_recovery_acceptance(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    manager = SessionManager(config=config)
    assert config.database_path.parent == tmp_path

    auto_message, auto_run = manager.create_user_turn(None, "auto session first message")
    specified = manager.create_session("specified_session")
    manager.append_message(specified.session_id, "user", "specified only")

    for index in range(11):
        role = "assistant" if index % 2 else "user"
        manager.append_message(auto_message.session_id, role, f"auto-{index}", run_id=None)
    manager.update_summary(auto_message.session_id, "seed summary", auto_message.timeline_seq)

    builder = ContextBuilder(session_manager=manager)
    context = builder.build(auto_message.session_id, current_user_input="auto-10")
    specified_context = builder.build(specified.session_id)

    assert auto_message.session_id != specified.session_id
    assert auto_run.session_id == auto_message.session_id
    assert context.summary == "seed summary"
    assert len(context.recent_messages) == 10
    assert context.truncated is True
    assert context.current_user_input_included is False
    assert context.context_text.count("auto-10") == 1
    assert "specified only" not in context.context_text
    assert "specified only" in specified_context.context_text

    reopened = SessionManager(config=config)
    restored = reopened.load_session(auto_message.session_id)
    restored_context = ContextBuilder(session_manager=reopened).build(auto_message.session_id)

    assert restored.message_count == 12
    assert restored.summary == "seed summary"
    assert restored_context.summary == "seed summary"
    assert [message.content for message in restored_context.recent_messages][-1] == "auto-10"


def test_pure_memory_timeline_events_idempotency_visibility_and_redaction(tmp_path: Path) -> None:
    manager = SessionManager(config=_make_config(tmp_path))
    user_message, run = manager.create_user_turn(None, "calculate")

    visible_event = _executor_event()
    stored_first = manager.append_execution_event(user_message.session_id, run.run_id, visible_event)
    stored_second = manager.append_execution_event(user_message.session_id, run.run_id, visible_event)
    hidden = manager.append_execution_event(
        user_message.session_id,
        run.run_id,
        _executor_event(
            type="final_answer",
            event_id="event_hiddenacceptance",
            visible_to_user=False,
            message="hidden final",
            payload={"raw_model_output": "hidden"},
        ),
    )
    assistant = manager.append_message(
        user_message.session_id,
        "assistant",
        "answer is 5",
        run_id=run.run_id,
    )
    manager.complete_run(run.run_id, assistant.message_id)

    timeline = manager.get_session_timeline(user_message.session_id)

    assert stored_first is not None
    assert stored_second is not None
    assert stored_second.event_id == stored_first.event_id
    assert hidden is None
    assert [item.item_kind for item in timeline] == ["message", "execution_event", "message"]
    assert [item.timeline_seq for item in timeline] == sorted(item.timeline_seq for item in timeline)
    assert timeline[1].item_id == stored_first.event_id
    assert timeline[1].metadata["event_type"] == "tool_finished"
    assert timeline[1].metadata["sanitized_payload"]["api_key"] == REDACTED_VALUE
    assert timeline[1].metadata["sanitized_payload"]["raw_observation"] == REDACTED_VALUE
    assert "secret-token" not in timeline[1].content
    assert REDACTED_VALUE in timeline[1].content
    assert len([item for item in timeline if item.item_kind == "execution_event"]) == 1


def test_pure_memory_auto_summary_success_failure_and_recovery_acceptance(tmp_path: Path) -> None:
    success_model = FakeModelManager(_compression_result("compressed summary"))
    manager = SessionManager(
        config=_make_config(
            tmp_path / "success",
            max_recent_messages=2,
            summary_trigger_messages=4,
            summary_batch_messages=2,
        ),
        model_manager=success_model,
    )
    session = manager.create_session("summary_success")
    for index in range(6):
        role = "user" if index % 2 == 0 else "assistant"
        manager.append_message(session.session_id, role, f"message-{index}")

    summary = manager.maybe_auto_summarize(session.session_id)

    assert summary is not None
    assert summary.content == "compressed summary"
    assert manager.load_session(session.session_id).summary == "compressed summary"
    assert success_model.calls

    failure_model = FakeModelManager(error=RuntimeError("summary failed token=secret-token"))
    failing = SessionManager(
        config=_make_config(
            tmp_path / "failure",
            max_recent_messages=2,
            summary_trigger_messages=4,
            summary_batch_messages=2,
        ),
        model_manager=failure_model,
    )
    failure_session = failing.create_session("summary_failure")
    first = failing.append_message(failure_session.session_id, "user", "seed")
    failing.update_summary(failure_session.session_id, "old summary", first.timeline_seq)
    for index in range(6):
        role = "user" if index % 2 == 0 else "assistant"
        failing.append_message(failure_session.session_id, role, f"later-{index}")

    preserved = failing.maybe_auto_summarize(failure_session.session_id)

    assert preserved is not None
    assert failing.load_session(failure_session.session_id).summary == "old summary"
    log_text = failing.config.log_path.read_text(encoding="utf-8")
    assert "summary_failed" in log_text
    assert "secret-token" not in log_text

    repo = SQLiteSessionRepository(_make_config(tmp_path / "recovery"))
    recovery_session = repo.create_session("recovery_session")
    running_run_id = new_run_id()
    completed_run_id = new_run_id()
    repo.insert_run(
        {
            "run_id": running_run_id,
            "session_id": recovery_session.session_id,
            "user_message_id": new_message_id(),
            "status": "running",
            "started_at": "2026-08-20T00:00:00Z",
        }
    )
    repo.insert_run(
        {
            "run_id": completed_run_id,
            "session_id": recovery_session.session_id,
            "user_message_id": new_message_id(),
            "status": "completed",
            "started_at": "2026-08-20T00:00:00Z",
        }
    )
    recovery = SessionManager(repo=repo)

    assert recovery.recover_interrupted_runs() == 1
    assert repo.load_run(running_run_id).status == "interrupted"
    assert repo.load_run(completed_run_id).status == "completed"


def test_pure_memory_database_error_is_readable_and_preserves_original(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    config.database_path.write_text("not sqlite", encoding="utf-8")

    with pytest.raises(RuntimeError) as exc_info:
        SQLiteSessionRepository(config)

    assert "could not be opened" in str(exc_info.value)
    assert config.database_path.read_text(encoding="utf-8") == "not sqlite"
    log_text = config.log_path.read_text(encoding="utf-8")
    assert "persistence_warning" in log_text
    assert "original_preserved" in log_text
