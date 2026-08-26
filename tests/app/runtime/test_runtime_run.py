from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from src.agent.output_feedback import OutputFeedbackProcessor
from src.agent.react_executor.react_executor_protocol import (
    ExecutionEvent,
    ExecutionResult,
)
from src.app.runtime import Runtime, RuntimeEvent, RuntimeRequest


SESSION_ID = "session_20260825_110000_run001"
RUN_ID = "run_20260825_110000_run001"


class _TracingTurn:
    session_id = SESSION_ID
    run_id = RUN_ID
    persistence_available = True
    persistence_warning = None

    def __init__(self, trace: list[str]) -> None:
        self.trace = trace

    def react_agent_kwargs(self) -> dict[str, Any]:
        self.trace.append("turn.react_agent_kwargs")
        return {
            "context_text": (
                "[Session Summary]\nNo summary yet.\n\n"
                "[Current User Input]\ntrace this run"
            ),
            "session_id": self.session_id,
            "run_id": self.run_id,
            "manage_memory": False,
        }


class _TracingMemoryAdapter:
    def __init__(self, trace: list[str]) -> None:
        self.trace = trace
        self.turn = _TracingTurn(trace)
        self.recorded_events: list[Any] = []
        self.assistant_messages: list[str] = []

    def begin_turn(self, session_id: str | None, user_input: str, **kwargs: Any) -> _TracingTurn:
        self.trace.append("memory.begin_turn")
        assert session_id is None
        assert user_input == "trace this run"
        assert kwargs == {
            "user_metadata": {"entrypoint": "test"},
            "agent_version": "agent-v1",
            "model_profile": "mock",
        }
        return self.turn

    def record_event(self, turn: _TracingTurn, event: Any) -> Any:
        self.trace.append("memory.record_event")
        self.recorded_events.append(event)
        return event

    def complete_turn(self, turn: _TracingTurn, output: str, **kwargs: Any) -> Any:
        self.trace.append("memory.complete_turn")
        self.assistant_messages.append(output)
        return SimpleNamespace(
            session_id=turn.session_id,
            run_id=turn.run_id,
            success=True,
            timeline=[
                {
                    "item_kind": "message",
                    "role": "user",
                    "content": "trace this run",
                },
                {
                    "item_kind": "message",
                    "role": "assistant",
                    "content": output,
                },
            ],
            persistence_available=True,
        )


class _TracingAgent:
    def __init__(self, trace: list[str]) -> None:
        self.trace = trace
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.memory_writes: list[tuple[str, str]] = []
        self.event = ExecutionEvent(
            execution_id="execution_runtime_run",
            plan_id="plan_runtime_run",
            type="tool_finished",
            message="Tool finished.",
            event_id="event_aaaaaaaaaaaa",
            visible_to_user=True,
            payload={"tool_name": "fake_tool", "summary": "safe"},
        )

    def run_with_result(self, user_input: str, **kwargs: Any) -> ExecutionResult:
        self.trace.append("react_agent.run_with_result")
        self.calls.append((user_input, kwargs))
        if kwargs["manage_memory"]:
            self.memory_writes.extend(
                [
                    ("user", user_input),
                    ("assistant", "should not be written by Agent"),
                ]
            )
        callback = kwargs["event_callback"]
        callback(self.event)
        return ExecutionResult(
            execution_id="execution_runtime_run",
            plan_id="plan_runtime_run",
            status="completed",
            success=True,
            output="Runtime integration answer",
            summary="A safe fake answer.",
            events=[self.event],
        )


class _TracingFeedbackProcessor:
    def __init__(self, trace: list[str]) -> None:
        self.trace = trace
        self.processor = OutputFeedbackProcessor()

    def build(self, execution_result: Any, **kwargs: Any) -> Any:
        self.trace.append("output_feedback.build")
        return self.processor.build(execution_result, **kwargs)


def _runtime(
    tmp_path: Path,
    *,
    memory_adapter: Any,
    agent: Any,
    feedback_processor: Any,
) -> Runtime:
    dependency = object()
    return Runtime(
        config=SimpleNamespace(workspace_root=tmp_path),
        model_manager=dependency,
        tool_manager=dependency,
        tool_registry=dependency,
        session_manager=dependency,
        context_builder=dependency,
        memory_adapter=memory_adapter,
        analyzer=dependency,
        planner=dependency,
        react_executor=dependency,
        react_agent=agent,
        output_feedback_processor=feedback_processor,
        pending_run_registry=dependency,
        recover_on_startup=False,
    )


def test_normal_run_follows_runtime_core_order_and_contract(tmp_path: Path) -> None:
    trace: list[str] = []
    memory = _TracingMemoryAdapter(trace)
    agent = _TracingAgent(trace)
    feedback = _TracingFeedbackProcessor(trace)
    runtime = _runtime(
        tmp_path,
        memory_adapter=memory,
        agent=agent,
        feedback_processor=feedback,
    )

    sink_events: list[RuntimeEvent] = []
    result = runtime.run(
        RuntimeRequest(
            input="trace this run",
            metadata={"entrypoint": "test"},
            model_profile="mock",
            agent_version="agent-v1",
        )
    )

    assert result.success is True
    assert result.status == "completed"
    assert result.session_id == SESSION_ID
    assert result.run_id == RUN_ID
    assert result.output == "Runtime integration answer"
    assert result.execution_result is not None
    assert result.output_feedback is not None
    assert result.memory_result is not None
    assert result.timeline

    assert trace == [
        "memory.begin_turn",
        "turn.react_agent_kwargs",
        "react_agent.run_with_result",
        "memory.record_event",
        "output_feedback.build",
        "memory.complete_turn",
    ]
    assert memory.assistant_messages == ["Runtime integration answer"]
    assert agent.memory_writes == []
    assert len(memory.recorded_events) == 1

    user_input, agent_kwargs = agent.calls[0]
    assert user_input == "trace this run"
    assert agent_kwargs["context_text"].startswith("[Session Summary]")
    assert agent_kwargs["session_id"] == SESSION_ID
    assert agent_kwargs["run_id"] == RUN_ID
    assert agent_kwargs["manage_memory"] is False
    assert agent_kwargs["event_callback_visible_only"] is True
    assert callable(agent_kwargs["event_callback"])

    # A normal run has no externally supplied sink, but its event remains
    # available through the result/timeline contract after Memory mapping.
    assert result.timeline[0]["role"] == "user"
    assert result.timeline[1]["role"] == "assistant"


def test_normal_run_accepts_visible_events_without_exposing_internal_events(
    tmp_path: Path,
) -> None:
    trace: list[str] = []
    memory = _TracingMemoryAdapter(trace)
    agent = _TracingAgent(trace)
    runtime = _runtime(
        tmp_path,
        memory_adapter=memory,
        agent=agent,
        feedback_processor=OutputFeedbackProcessor(),
    )

    # The public `run()` facade has no sink parameter. Exercise the same
    # run-local callback used by streaming adapters without starting a stream.
    sink_events: list[RuntimeEvent] = []
    context = runtime._prepare_request_context(
        RuntimeRequest(
            input="trace this run",
            metadata={"entrypoint": "test"},
            model_profile="mock",
            agent_version="agent-v1",
        ),
        event_sink=sink_events.append,
    )
    context = runtime._begin_memory_turn(context)
    runtime._run_agent(context)

    assert len(sink_events) == 1
    assert isinstance(sink_events[0], RuntimeEvent)
    assert sink_events[0].visible_to_user is True
    assert sink_events[0].sequence == 1
