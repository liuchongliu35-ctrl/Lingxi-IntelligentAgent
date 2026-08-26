from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from src.agent.output_feedback import OutputFeedbackProcessor
from src.agent.react_executor.react_executor_protocol import (
    ExecutionEvent,
    ExecutionResult,
)
from src.app.runtime import Runtime, RuntimeRequest
from src.memory.config import MemoryConfig
from src.memory.runtime_adapter import RuntimeMemoryAdapter
from src.memory.session_manager import SessionManager


SESSION_ID = "session_20260825_110000_integ01"


class _FakeRuntimeAgent:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.memory_writes: list[tuple[str, str]] = []
        self.call_count = 0

    def run_with_result(self, user_input: str, **kwargs: Any) -> ExecutionResult:
        self.call_count += 1
        self.calls.append((user_input, kwargs))
        if kwargs["manage_memory"]:
            self.memory_writes.append(("user", user_input))
            self.memory_writes.append(("assistant", "duplicate"))

        event = ExecutionEvent(
            execution_id=f"execution_integration_{self.call_count}",
            plan_id="plan_integration",
            type="tool_finished",
            message=f"Fake tool completed for {user_input}.",
            event_id=f"event_{self.call_count:012d}",
            visible_to_user=True,
            payload={
                "tool_name": "fake_tool",
                "summary": f"safe result {self.call_count}",
            },
        )
        hidden = ExecutionEvent(
            execution_id=f"execution_integration_{self.call_count}",
            plan_id="plan_integration",
            type="model_step_started",
            message="hidden reasoning",
            event_id=f"event_{self.call_count + 100:012d}",
            visible_to_user=False,
            payload={"raw_prompt": "must not enter timeline"},
        )
        callback = kwargs["event_callback"]
        callback(event)
        callback(hidden)
        return ExecutionResult(
            execution_id=f"execution_integration_{self.call_count}",
            plan_id="plan_integration",
            status="completed",
            success=True,
            output=f"Answer to {user_input}",
            summary="Fake model/tool result.",
            events=[event, hidden],
        )


def _memory_adapter(tmp_path: Path) -> RuntimeMemoryAdapter:
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
    adapter: RuntimeMemoryAdapter,
    agent: _FakeRuntimeAgent,
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


def test_normal_run_closes_the_real_memory_turn_and_reuses_session(
    tmp_path: Path,
) -> None:
    adapter = _memory_adapter(tmp_path)
    agent = _FakeRuntimeAgent()
    runtime = _runtime(tmp_path, adapter, agent)

    first = runtime.run(
        RuntimeRequest(
            input="first integration message",
            metadata={"entrypoint": "integration"},
            model_profile="mock",
            agent_version="agent-v1",
        )
    )
    second = runtime.run(
        RuntimeRequest(
            input="second integration message",
            session_id=first.session_id,
        )
    )

    assert first.success is True
    assert second.success is True
    assert first.status == "completed"
    assert second.status == "completed"
    assert first.session_id is not None
    assert second.session_id == first.session_id
    assert first.run_id is not None
    assert second.run_id is not None
    assert first.run_id != second.run_id
    assert agent.memory_writes == []
    assert len(agent.calls) == 2

    first_input, first_kwargs = agent.calls[0]
    second_input, second_kwargs = agent.calls[1]
    assert first_input == "first integration message"
    assert second_input == "second integration message"
    assert first_kwargs["manage_memory"] is False
    assert second_kwargs["manage_memory"] is False
    assert first_kwargs["session_id"] == first.session_id
    assert second_kwargs["session_id"] == first.session_id
    assert first_kwargs["run_id"] == first.run_id
    assert second_kwargs["run_id"] == second.run_id
    assert "first integration message" in second_kwargs["context_text"]
    assert "Answer to first integration message" in second_kwargs["context_text"]

    session = adapter.get_session(first.session_id)
    assert [message.role for message in session.messages] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert [message.content for message in session.messages] == [
        "first integration message",
        "Answer to first integration message",
        "second integration message",
        "Answer to second integration message",
    ]
    assert session.messages[0].metadata == {"entrypoint": "integration"}

    first_run = adapter.session_manager.repo.load_run(first.run_id)
    second_run = adapter.session_manager.repo.load_run(second.run_id)
    assert first_run is not None and first_run.status == "completed"
    assert second_run is not None and second_run.status == "completed"
    assert first_run.final_message_id == session.messages[1].message_id
    assert second_run.final_message_id == session.messages[3].message_id


def test_normal_run_result_and_timeline_keep_visible_events_only(
    tmp_path: Path,
) -> None:
    adapter = _memory_adapter(tmp_path)
    agent = _FakeRuntimeAgent()
    runtime = _runtime(tmp_path, adapter, agent)

    result = runtime.run(
        RuntimeRequest(input="event integration", session_id=SESSION_ID)
    )

    assert result.success is True
    assert result.status == "completed"
    assert result.output == "Answer to event integration"
    assert result.execution_result is not None
    assert result.output_feedback is not None
    assert result.memory_result is not None
    assert result.timeline

    event_items = [
        item for item in result.timeline if item.get("item_kind") == "execution_event"
    ]
    assert len(event_items) == 1
    assert event_items[0]["metadata"]["event_type"] == "tool_finished"
    assert "hidden reasoning" not in str(result.timeline)
    assert "raw_prompt" not in str(result.timeline)

    persisted_timeline = adapter.get_timeline(SESSION_ID)
    persisted_events = [
        item for item in persisted_timeline if item.item_kind == "execution_event"
    ]
    assert len(persisted_events) == 1
    assert persisted_events[0].display_type == "tool_progress"
    assert persisted_events[0].metadata["event_type"] == "tool_finished"
    assert "raw_prompt" not in str(persisted_events[0].metadata)

    assert result.session_id == SESSION_ID
    assert result.run_id is not None
    assert result.execution_result["execution_id"] == "execution_integration_1"
    assert result.output_feedback["final_output"] == "Answer to event integration"
