from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from src.memory.config import MemoryConfig
from src.memory.models import AgentRunStatus, DisplayType
from src.memory.runtime_adapter import RuntimeMemoryAdapter
from src.memory.session_manager import SessionManager


def _make_adapter(tmp_path: Path, *, summary_trigger_messages: int = 14) -> RuntimeMemoryAdapter:
    manager = SessionManager(
        config=MemoryConfig(
            database_path=tmp_path / "memory.db",
            log_path=tmp_path / "memory.log",
            max_recent_messages=4,
            summary_trigger_messages=summary_trigger_messages,
            summary_batch_messages=1,
        )
    )
    return RuntimeMemoryAdapter(session_manager=manager)


def test_runtime_adapter_begin_turn_prepares_react_agent_contract(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)

    turn = adapter.begin_turn(
        None,
        "hello runtime",
        user_metadata={"entrypoint": "cli"},
        session_metadata={"channel": "cli"},
        agent_version="agent-v1",
        model_profile="mock-model",
    )
    kwargs = turn.react_agent_kwargs()
    session = adapter.get_session(turn.session_id)
    loaded_run = adapter.session_manager.repo.load_run(turn.run_id)

    assert turn.session_id.startswith("session_")
    assert turn.user_message.role == "user"
    assert turn.user_message.metadata["entrypoint"] == "cli"
    assert session.metadata["channel"] == "cli"
    assert loaded_run is not None
    assert loaded_run.agent_version == "agent-v1"
    assert loaded_run.model_profile == "mock-model"
    assert kwargs == {
        "context_text": turn.context_text,
        "session_id": turn.session_id,
        "run_id": turn.run_id,
        "manage_memory": False,
    }
    assert "hello runtime" in turn.context_text
    assert turn.short_term_memory.session_id == turn.session_id


def test_runtime_adapter_records_events_and_completes_turn(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    turn = adapter.begin_turn("runtime_session", "run a tool")
    visible = SimpleNamespace(
        execution_id="execution_visible",
        plan_id="plan_runtime",
        type="progress_message",
        message="visible progress",
        visible_to_user=True,
    )
    internal = SimpleNamespace(
        execution_id="execution_internal",
        plan_id="plan_runtime",
        type="model_step_started",
        message="internal reasoning",
        visible_to_user=False,
    )

    stored_visible = adapter.record_event(turn, visible)
    stored_internal = adapter.record_event(turn, internal)
    result = adapter.complete_turn(
        turn,
        "final answer",
        assistant_metadata={"source": "runtime"},
        maybe_summarize=False,
    )

    assert stored_visible is not None
    assert stored_internal is None
    assert result.success is True
    assert result.assistant_message is not None
    assert result.assistant_message.display_type == DisplayType.FINAL_ANSWER.value
    assert result.assistant_message.metadata["source"] == "runtime"
    assert result.run.status == AgentRunStatus.COMPLETED.value
    assert result.run.final_message_id == result.assistant_message.message_id
    assert result.timeline is not None
    assert [item.item_kind for item in result.timeline] == [
        "message",
        "execution_event",
        "message",
    ]
    assert result.timeline[1].content == "visible progress"


def test_runtime_adapter_event_callback_persists_and_forwards_visible_events(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    turn = adapter.begin_turn("callback_session", "observe events")
    seen: list[object] = []
    callback = adapter.event_callback(turn, external_callback=seen.append)
    visible = SimpleNamespace(
        execution_id="execution_callback_visible",
        plan_id="plan_runtime",
        type="progress_message",
        message="visible progress",
        visible_to_user=True,
    )
    internal = SimpleNamespace(
        execution_id="execution_callback_internal",
        plan_id="plan_runtime",
        type="model_step_started",
        message="internal reasoning",
        visible_to_user=False,
    )

    callback(visible)
    callback(internal)

    timeline = adapter.get_timeline(turn.session_id)
    assert seen == [visible]
    assert [item.item_kind for item in timeline] == ["message", "execution_event"]
    assert timeline[1].content == "visible progress"


def test_runtime_adapter_fail_turn_records_run_failure(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    turn = adapter.begin_turn("failure_session", "fail please")

    result = adapter.fail_turn(turn, RuntimeError("boom"), maybe_summarize=False)

    assert result.success is False
    assert result.assistant_message is None
    assert result.run.status == AgentRunStatus.FAILED.value
    assert result.error_code == "RuntimeError"
    assert result.error_message == "boom"
    assert result.timeline is not None
    assert [item.item_kind for item in result.timeline] == ["message"]


def test_runtime_adapter_health_and_session_timeline_facade(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    turn = adapter.begin_turn("health_session", "hello")
    adapter.complete_turn(turn, "answer", maybe_summarize=False)

    health = adapter.health()
    session = adapter.get_session("health_session")
    timeline = adapter.get_timeline("health_session")

    assert health.ok is True
    assert health.session_count == 1
    assert health.database_path.endswith("memory.db")
    assert health.schema_version >= 1
    assert session.session_id == "health_session"
    assert [item.role for item in timeline] == ["user", "assistant"]
