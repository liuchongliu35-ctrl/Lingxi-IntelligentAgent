from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from src.agent.output_feedback import OutputFeedbackProcessor
from src.agent.react_executor.react_executor_protocol import ExecutionResult
from src.app.runtime import Runtime, RuntimeRequest
from src.memory.config import MemoryConfig
from src.memory.runtime_adapter import RuntimeMemoryAdapter
from src.memory.session_manager import SessionManager


class _Step8Agent:
    def run_with_result(self, user_input: str, **kwargs: Any) -> ExecutionResult:
        return ExecutionResult(
            execution_id="execution_runtime_begin_turn",
            plan_id="plan_runtime_begin_turn",
            status="completed",
            success=True,
            output=f"response to {user_input}",
            summary="runtime begin turn response",
        )


class _RecordingMemoryAdapter:
    def __init__(self, turn: Any) -> None:
        self.turn = turn
        self.calls: list[tuple[str | None, str, dict[str, Any]]] = []

    def begin_turn(
        self,
        session_id: str | None,
        user_input: str,
        **kwargs: Any,
    ) -> Any:
        self.calls.append((session_id, user_input, kwargs))
        return self.turn


class _RecordingTurn:
    session_id = "session_20260824_120000_demo001"
    run_id = "run_20260824_120000_demo001"
    persistence_available = True
    persistence_warning = None

    def __init__(self) -> None:
        self.kwargs_calls = 0

    def react_agent_kwargs(self) -> dict[str, Any]:
        self.kwargs_calls += 1
        return {
            "context_text": "[Session Summary]\nNo summary yet.",
            "session_id": self.session_id,
            "run_id": self.run_id,
            "manage_memory": False,
        }


def _runtime(
    tmp_path: Path,
    *,
    memory_adapter: Any,
    session_manager: Any | None = None,
    react_agent: Any | None = None,
) -> Runtime:
    dependency = object()
    return Runtime(
        config=SimpleNamespace(workspace_root=tmp_path),
        model_manager=dependency,
        tool_manager=dependency,
        tool_registry=dependency,
        session_manager=session_manager or dependency,
        context_builder=dependency,
        memory_adapter=memory_adapter,
        analyzer=dependency,
        planner=dependency,
        react_executor=dependency,
        react_agent=react_agent or _Step8Agent(),
        output_feedback_processor=OutputFeedbackProcessor(),
        pending_run_registry=dependency,
        recover_on_startup=False,
    )


def _real_memory_adapter(tmp_path: Path) -> RuntimeMemoryAdapter:
    manager = SessionManager(
        config=MemoryConfig(
            database_path=tmp_path / "memory.db",
            log_path=tmp_path / "memory.log",
            max_recent_messages=4,
        )
    )
    return RuntimeMemoryAdapter(session_manager=manager)


def test_run_without_session_lets_memory_create_session_and_running_run(
    tmp_path: Path,
) -> None:
    adapter = _real_memory_adapter(tmp_path)
    runtime = _runtime(
        tmp_path,
        memory_adapter=adapter,
        session_manager=adapter.session_manager,
    )

    result = runtime.run(
        RuntimeRequest(
            input="hello Runtime",
            metadata={"entrypoint": "runtime-test"},
            agent_version="agent-v1",
            model_profile="mock",
        )
    )

    assert result.success is True
    assert result.status == "completed"
    assert result.output == "response to hello Runtime"
    assert result.session_id is not None
    assert result.run_id is not None
    session = adapter.get_session(result.session_id)
    loaded_run = adapter.session_manager.repo.load_run(result.run_id)
    assert [message.content for message in session.messages] == [
        "hello Runtime",
        "response to hello Runtime",
    ]
    assert session.messages[0].metadata == {"entrypoint": "runtime-test"}
    assert loaded_run is not None
    assert loaded_run.status == "completed"
    assert loaded_run.final_message_id == session.messages[1].message_id
    assert loaded_run.agent_version == "agent-v1"
    assert loaded_run.model_profile == "mock"
    assert "context_text" not in result.metadata
    assert "session_id" not in runtime.__dict__
    assert "run_id" not in runtime.__dict__


def test_run_with_session_id_continues_the_memory_session(tmp_path: Path) -> None:
    adapter = _real_memory_adapter(tmp_path)
    runtime = _runtime(
        tmp_path,
        memory_adapter=adapter,
        session_manager=adapter.session_manager,
    )
    first = runtime.run(RuntimeRequest(input="first message"))

    second = runtime.run(
        RuntimeRequest(input="second message", session_id=first.session_id)
    )

    assert second.session_id == first.session_id
    assert second.run_id is not None and second.run_id != first.run_id
    session = adapter.get_session(first.session_id)
    assert [message.content for message in session.messages] == [
        "first message",
        "response to first message",
        "second message",
        "response to second message",
    ]


def test_run_passes_request_fields_and_prepares_memory_context_for_react_agent(
    tmp_path: Path,
) -> None:
    turn = _RecordingTurn()
    adapter = _RecordingMemoryAdapter(turn)
    runtime = _runtime(tmp_path, memory_adapter=adapter)

    result = runtime.run(
        RuntimeRequest(
            input="prepare context",
            session_id="session_20260824_120000_demo001",
            metadata={"entrypoint": "test"},
            agent_version="agent-v1",
            model_profile="mock",
        )
    )

    assert result.session_id == turn.session_id
    assert result.run_id == turn.run_id
    assert adapter.calls == [
        (
            "session_20260824_120000_demo001",
            "prepare context",
            {
                "user_metadata": {"entrypoint": "test"},
                "agent_version": "agent-v1",
                "model_profile": "mock",
            },
        )
    ]
    assert turn.kwargs_calls == 1


def test_begin_turn_persistence_degradation_is_preserved_in_runtime_result(
    tmp_path: Path,
) -> None:
    turn = _RecordingTurn()
    turn.persistence_available = False
    turn.persistence_warning = "Memory persistence temporarily unavailable."
    runtime = _runtime(
        tmp_path,
        memory_adapter=_RecordingMemoryAdapter(turn),
    )

    result = runtime.run(RuntimeRequest(input="continue ephemerally"))

    assert result.session_id == turn.session_id
    assert result.run_id == turn.run_id
    assert result.success is True
    assert result.persistence_available is False
    assert result.persistence_warning == "Memory persistence temporarily unavailable."


def test_begin_turn_failure_maps_to_memory_unavailable(tmp_path: Path) -> None:
    class _BrokenMemoryAdapter:
        def begin_turn(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("database unavailable")

    runtime = _runtime(tmp_path, memory_adapter=_BrokenMemoryAdapter())

    result = runtime.run(RuntimeRequest(input="cannot persist"))

    assert result.success is False
    assert result.error_code == "memory_unavailable"
    assert result.persistence_available is False
