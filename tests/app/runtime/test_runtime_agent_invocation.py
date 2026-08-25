from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from src.agent.orchestrator.output_feedback import OutputFeedbackProcessor
from src.agent.react_executor.react_executor_protocol import (
    ExecutionEvent,
    ExecutionResult,
)
from src.app.runtime import Runtime, RuntimeRequest


SESSION_ID = "session_20260824_120000_step8a001"
RUN_ID = "run_20260824_120000_step80001"


class RecordingMemory:
    def __init__(self) -> None:
        self.add_calls: list[tuple[str, str]] = []

    def add_message(self, role: str, content: str) -> None:
        self.add_calls.append((role, content))


class RecordingTurn:
    session_id = SESSION_ID
    run_id = RUN_ID
    persistence_available = True
    persistence_warning = None

    def react_agent_kwargs(self) -> dict[str, Any]:
        return {
            "context_text": "[Session Summary]\nsummary\n\n[Current User Input]\nhello",
            "session_id": self.session_id,
            "run_id": self.run_id,
            "manage_memory": False,
        }


class RecordingMemoryAdapter:
    def __init__(self) -> None:
        self.turn = RecordingTurn()

    def begin_turn(self, session_id: str | None, user_input: str, **kwargs: Any) -> RecordingTurn:
        return self.turn


class RecordingAgent:
    def __init__(self) -> None:
        self.memory = RecordingMemory()
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.result = ExecutionResult(
            execution_id="execution_step8",
            plan_id="plan_step8",
            status="completed",
            success=True,
            output="safe final answer",
            summary="safe summary",
            events=[
                ExecutionEvent(
                    execution_id="execution_step8",
                    plan_id="plan_step8",
                    type="final_answer",
                    message="safe final answer",
                    payload={
                        "raw_prompt": "must not leak",
                        "visible": "ok",
                    },
                )
            ],
        )

    def run_with_result(self, user_input: str, **kwargs: Any) -> ExecutionResult:
        self.calls.append((user_input, kwargs))
        if kwargs["manage_memory"]:
            self.memory.add_message("user", user_input)
            self.memory.add_message("assistant", self.result.output)
        callback = kwargs.get("event_callback")
        if callback is not None:
            callback(self.result.events[0])
            callback(
                ExecutionEvent(
                    execution_id="execution_step8",
                    plan_id="plan_step8",
                    type="model_step_started",
                    message="internal",
                    visible_to_user=False,
                )
            )
        return self.result


def _runtime(tmp_path: Path, agent: RecordingAgent) -> Runtime:
    dependency = object()
    return Runtime(
        config=SimpleNamespace(workspace_root=tmp_path),
        model_manager=dependency,
        tool_manager=dependency,
        tool_registry=dependency,
        session_manager=dependency,
        context_builder=dependency,
        memory_adapter=RecordingMemoryAdapter(),
        analyzer=dependency,
        planner=dependency,
        react_executor=dependency,
        react_agent=agent,
        output_feedback_processor=OutputFeedbackProcessor(),
        pending_run_registry=dependency,
        recover_on_startup=False,
    )


def test_runtime_invokes_formal_react_agent_mode_and_builds_safe_feedback(
    tmp_path: Path,
) -> None:
    agent = RecordingAgent()
    runtime = _runtime(tmp_path, agent)

    result = runtime.run(RuntimeRequest(input="hello Runtime"))

    assert result.success is True
    assert result.status == "completed"
    assert result.session_id == SESSION_ID
    assert result.run_id == RUN_ID
    assert result.output == "safe final answer"
    assert result.execution_result is not None
    assert result.output_feedback is not None
    assert result.execution_result["execution_id"] == "execution_step8"
    assert result.output_feedback["final_output"] == "safe final answer"
    assert "raw_prompt" not in str(result.execution_result)
    assert "raw_prompt" not in str(result.output_feedback)

    assert len(agent.calls) == 1
    user_input, kwargs = agent.calls[0]
    assert user_input == "hello Runtime"
    assert kwargs["context_text"].startswith("[Session Summary]")
    assert kwargs["session_id"] == SESSION_ID
    assert kwargs["run_id"] == RUN_ID
    assert kwargs["manage_memory"] is False
    assert kwargs["event_callback_visible_only"] is True
    assert callable(kwargs["event_callback"])
    assert agent.memory.add_calls == []


def test_runtime_event_sink_receives_only_visible_events_in_step8(
    tmp_path: Path,
) -> None:
    agent = RecordingAgent()
    events: list[Any] = []
    runtime = _runtime(tmp_path, agent)

    request_context = runtime._prepare_request_context(
        RuntimeRequest(input="hello Runtime"),
        event_sink=events.append,
    )
    request_context = runtime._begin_memory_turn(request_context)
    runtime._run_agent(request_context)

    assert len(events) == 1
    assert events[0].event_type == "final_answer"
    assert events[0].sequence == 1
