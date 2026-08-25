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
from src.memory.config import MemoryConfig
from src.memory.runtime_adapter import RuntimeMemoryAdapter
from src.memory.session_manager import SessionManager


SESSION_ID = "session_20260824_130000_event001"


class _EventAgent:
    def __init__(self, *, callback_events: bool = True) -> None:
        self.callback_events = callback_events
        self.calls: list[dict[str, Any]] = []

    def run_with_result(self, user_input: str, **kwargs: Any) -> ExecutionResult:
        self.calls.append(kwargs)
        event_suffix = "111111111111" if len(self.calls) == 1 else "333333333333"
        visible = ExecutionEvent(
            execution_id="execution_runtime_events",
            plan_id="plan_runtime_events",
            type="tool_finished",
            message="Tool finished with token=secret-token.",
            event_id=f"event_{event_suffix}",
            timestamp="2026-08-24T13:00:01Z",
            visible_to_user=True,
            payload={
                "tool_name": "demo",
                "safe": "ok",
                "raw_tool_result": "do-not-leak",
                "api_key": "secret",
            },
        )
        hidden = ExecutionEvent(
            execution_id="execution_runtime_events",
            plan_id="plan_runtime_events",
            type="model_step_started",
            message="hidden",
            event_id=f"event_{'222222222222' if len(self.calls) == 1 else '444444444444'}",
            timestamp="2026-08-24T13:00:02Z",
            visible_to_user=False,
            payload={"raw_observation": "do-not-leak"},
        )
        if self.callback_events and kwargs.get("event_callback") is not None:
            callback = kwargs["event_callback"]
            callback(visible)
            callback(hidden)
        return ExecutionResult(
            execution_id="execution_runtime_events",
            plan_id="plan_runtime_events",
            status="completed",
            success=True,
            output=f"response to {user_input}",
            events=[visible, hidden],
        )


class _BrokenEventMemoryAdapter(RuntimeMemoryAdapter):
    def record_event(self, turn: Any, event: Any) -> None:
        raise RuntimeError("database token=secret")


def _adapter(tmp_path: Path) -> RuntimeMemoryAdapter:
    manager = SessionManager(
        config=MemoryConfig(
            database_path=tmp_path / "memory.db",
            log_path=tmp_path / "memory.log",
            max_recent_messages=4,
        )
    )
    return RuntimeMemoryAdapter(session_manager=manager)


def _runtime(
    tmp_path: Path,
    *,
    adapter: RuntimeMemoryAdapter,
    agent: Any,
) -> Runtime:
    dependency = object()
    return Runtime(
        config=SimpleNamespace(workspace_root=tmp_path),
        model_manager=dependency,
        tool_manager=dependency,
        tool_registry=dependency,
        session_manager=adapter.session_manager,
        context_builder=adapter.context_builder,
        memory_adapter=adapter,
        analyzer=dependency,
        planner=dependency,
        react_executor=dependency,
        react_agent=agent,
        output_feedback_processor=OutputFeedbackProcessor(),
        pending_run_registry=dependency,
        recover_on_startup=False,
    )


def test_runtime_events_are_persisted_wrapped_filtered_and_deduplicated(
    tmp_path: Path,
) -> None:
    adapter = _adapter(tmp_path)
    agent = _EventAgent(callback_events=True)
    runtime = _runtime(tmp_path, adapter=adapter, agent=agent)
    sink_events: list[RuntimeEvent] = []

    result = runtime.run(
        RuntimeRequest(input="hello", session_id=SESSION_ID),
    )

    assert result.success is True
    assert result.session_id == SESSION_ID

    # Exercise the sink through the same run-local callback used by Runtime.
    context = runtime._prepare_request_context(
        RuntimeRequest(input="hello again", session_id=SESSION_ID),
        event_sink=sink_events.append,
    )
    context = runtime._begin_memory_turn(context)
    runtime._run_agent(context)

    assert len(sink_events) == 1
    event = sink_events[0]
    assert isinstance(event, RuntimeEvent)
    assert event.event_type == "tool_finished"
    assert event.sequence == 1
    assert event.event_id is not None
    assert event.event_id.endswith("_333333333333")
    assert event.visible_to_user is True
    assert event.payload["safe"] == "ok"
    assert "raw_tool_result" not in str(event.payload)
    assert "secret-token" not in event.message
    assert event.source_event is not None
    assert "payload" not in event.source_event

    timeline = adapter.get_timeline(SESSION_ID)
    event_items = [item for item in timeline if item.item_kind == "execution_event"]
    assert len(event_items) == 2
    assert [item.metadata["event_type"] for item in event_items] == [
        "tool_finished",
        "tool_finished",
    ]


def test_runtime_result_events_fill_in_when_agent_does_not_use_callback(
    tmp_path: Path,
) -> None:
    adapter = _adapter(tmp_path)
    agent = _EventAgent(callback_events=False)
    runtime = _runtime(tmp_path, adapter=adapter, agent=agent)
    sink_events: list[RuntimeEvent] = []

    context = runtime._prepare_request_context(
        RuntimeRequest(input="fallback", session_id=SESSION_ID),
        event_sink=sink_events.append,
    )
    context = runtime._begin_memory_turn(context)
    runtime._run_agent(context)

    assert [event.event_type for event in sink_events] == ["tool_finished"]
    assert [event.sequence for event in sink_events] == [1]
    timeline = adapter.get_timeline(SESSION_ID)
    assert len([item for item in timeline if item.item_kind == "execution_event"]) == 1


def test_runtime_event_sink_failure_does_not_fail_agent(
    tmp_path: Path,
) -> None:
    adapter = _adapter(tmp_path)
    runtime = _runtime(tmp_path, adapter=adapter, agent=_EventAgent())

    context = runtime._prepare_request_context(
        RuntimeRequest(input="sink failure", session_id=SESSION_ID),
        event_sink=lambda event: (_ for _ in ()).throw(RuntimeError("sink down")),
    )
    context = runtime._begin_memory_turn(context)
    execution_result, _feedback = runtime._run_agent(context)

    assert execution_result.success is True
    timeline = adapter.get_timeline(SESSION_ID)
    assert len([item for item in timeline if item.item_kind == "execution_event"]) == 1


def test_runtime_event_memory_failure_is_degraded_and_does_not_fail_agent(
    tmp_path: Path,
) -> None:
    base_adapter = _adapter(tmp_path)
    adapter = _BrokenEventMemoryAdapter(
        session_manager=base_adapter.session_manager,
        context_builder=base_adapter.context_builder,
    )
    runtime = _runtime(tmp_path, adapter=adapter, agent=_EventAgent())
    sink_events: list[RuntimeEvent] = []

    context = runtime._prepare_request_context(
        RuntimeRequest(input="memory failure", session_id=SESSION_ID),
        event_sink=sink_events.append,
    )
    context = runtime._begin_memory_turn(context)
    execution_result, _feedback = runtime._run_agent(context)

    assert execution_result.success is True
    assert len(sink_events) == 1
    assert context.memory_turn.persistence_available is False
    assert context.memory_turn.persistence_warning is not None
    assert "secret" not in context.memory_turn.persistence_warning
