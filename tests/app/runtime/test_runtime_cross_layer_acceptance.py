from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.agent.output_feedback import OutputFeedbackProcessor
from src.agent.react_executor.react_executor_protocol import (
    ExecutionEvent,
    ExecutionResult,
)
from src.app.runtime import Runtime, RuntimeErrorCode, RuntimeRequest
from src.memory.config import MemoryConfig
from src.memory.runtime_adapter import RuntimeMemoryAdapter
from src.memory.session_manager import SessionManager

from tests.app.runtime.test_runtime_resume import (
    SESSION_ID as CONFIRMATION_SESSION_ID,
    _ResumeAgent,
    _runtime as confirmation_runtime,
    _start_waiting_run,
)
from src.app.runtime import CancelRequest, PendingRunRegistry, ResumeRequest


class _ScenarioAgent:
    """Fake Agent boundary with real RuntimeMemoryAdapter behind it."""

    manage_memory = False

    def __init__(self, outcomes: list[Any] | None = None) -> None:
        self.outcomes = list(outcomes or [])
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.memory_writes: list[tuple[str, str]] = []

    def run_with_result(self, user_input: str, **kwargs: Any) -> ExecutionResult:
        self.calls.append((user_input, kwargs))
        assert kwargs["manage_memory"] is False
        visible = ExecutionEvent(
            execution_id=f"execution_acceptance_{len(self.calls)}",
            plan_id="plan_acceptance",
            type="tool_finished",
            message=f"Safe tool progress for {user_input}.",
            event_id=f"event_acceptance_{len(self.calls):012d}",
            visible_to_user=True,
            payload={
                "tool_name": "fake_tool",
                "summary": "safe result",
                "raw_tool_result": "must-not-enter-public-boundary",
            },
        )
        hidden = ExecutionEvent(
            execution_id=f"execution_acceptance_{len(self.calls)}",
            plan_id="plan_acceptance",
            type="model_step_started",
            message="hidden reasoning",
            event_id=f"event_acceptance_hidden_{len(self.calls):012d}",
            visible_to_user=False,
            payload={"raw_prompt": "must-not-enter-timeline"},
        )
        callback = kwargs.get("event_callback")
        if callable(callback):
            callback(visible)
            callback(hidden)

        outcome = self.outcomes.pop(0) if self.outcomes else None
        if isinstance(outcome, BaseException):
            raise outcome
        if isinstance(outcome, ExecutionResult):
            return outcome
        return ExecutionResult(
            execution_id=f"execution_acceptance_{len(self.calls)}",
            plan_id="plan_acceptance",
            status="completed",
            success=True,
            output=f"Answer to {user_input}",
            summary="Safe acceptance answer.",
            events=[visible, hidden],
        )


class _HealthyModels:
    def health_check(self) -> Any:
        return SimpleNamespace(
            healthy=True,
            provider="mock",
            protocol="mock",
            model="mock-v1",
            configured=True,
            check_type="config_check",
        )


class _HealthyTools:
    runtime = SimpleNamespace(enabled=True)
    registry = SimpleNamespace(tool_names=lambda: ["fake_tool"])
    config_error = None


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
    adapter: RuntimeMemoryAdapter,
    agent: Any,
    *,
    model_manager: Any | None = None,
    tool_manager: Any | None = None,
    pending_run_registry: Any | None = None,
) -> Runtime:
    dependency = object()
    return Runtime(
        config=SimpleNamespace(workspace_root=tmp_path),
        model_manager=model_manager or dependency,
        tool_manager=tool_manager or dependency,
        tool_registry=getattr(tool_manager, "registry", dependency),
        session_manager=adapter.session_manager,
        context_builder=adapter.context_builder,
        memory_adapter=adapter,
        analyzer=dependency,
        planner=dependency,
        react_executor=agent,
        react_agent=agent,
        output_feedback_processor=OutputFeedbackProcessor(),
        pending_run_registry=pending_run_registry or PendingRunRegistry(),
        recover_on_startup=False,
    )


def test_new_session_and_multi_turn_runtime_memory_agent_closure(
    tmp_path: Path,
) -> None:
    adapter = _adapter(tmp_path)
    agent = _ScenarioAgent()
    runtime = _runtime(tmp_path, adapter, agent)

    first = runtime.run(RuntimeRequest(input="first cross-layer message"))
    second = runtime.run(
        RuntimeRequest(
            input="second cross-layer message",
            session_id=first.session_id,
        )
    )

    assert first.success is True
    assert second.success is True
    assert first.session_id == second.session_id
    assert first.run_id != second.run_id
    assert [message.content for message in adapter.get_session(first.session_id).messages] == [
        "first cross-layer message",
        "Answer to first cross-layer message",
        "second cross-layer message",
        "Answer to second cross-layer message",
    ]
    assert agent.memory_writes == []
    assert all(call[1]["manage_memory"] is False for call in agent.calls)
    assert "first cross-layer message" in agent.calls[1][1]["context_text"]
    assert "Answer to first cross-layer message" in agent.calls[1][1]["context_text"]


def test_event_distribution_is_visible_only_deduplicated_and_sanitized(
    tmp_path: Path,
) -> None:
    adapter = _adapter(tmp_path)
    agent = _ScenarioAgent()
    runtime = _runtime(tmp_path, adapter, agent)

    result = runtime.run(RuntimeRequest(input="event boundary"))
    timeline_events = [
        item for item in result.timeline if item.get("item_kind") == "execution_event"
    ]
    persisted_events = [
        item for item in adapter.get_timeline(result.session_id)
        if item.item_kind == "execution_event"
    ]

    assert len(timeline_events) == 1
    assert len(persisted_events) == 1
    assert timeline_events[0]["metadata"]["event_type"] == "tool_finished"
    assert "hidden reasoning" not in str(result.timeline)
    assert "raw_prompt" not in str(result.timeline)
    assert "raw_tool_result" not in str(result.timeline)


@pytest.mark.parametrize(
    ("status", "error_code", "expected_run_status"),
    [
        ("blocked", "blocked_by_policy", "blocked"),
        ("request_replan", "request_replan", "request_replan"),
    ],
)
def test_policy_block_and_replan_cross_layer_results_are_closed_without_loop(
    tmp_path: Path,
    status: str,
    error_code: str,
    expected_run_status: str,
) -> None:
    adapter = _adapter(tmp_path)
    result = ExecutionResult(
        execution_id=f"execution_{status}",
        plan_id="plan_acceptance",
        status=status,
        success=False,
        output=(
            "Blocked by policy."
            if status == "blocked"
            else "The plan needs revision."
        ),
        error_code=("action_blocked" if status == "blocked" else error_code),
        request_replan=status == "request_replan",
        replan_reason="The current dependency needs a different plan."
        if status == "request_replan"
        else None,
    )
    agent = _ScenarioAgent([result])
    runtime = _runtime(tmp_path, adapter, agent)

    runtime_result = runtime.run(RuntimeRequest(input=f"{status} this"))

    assert runtime_result.success is False
    assert runtime_result.status == status
    assert runtime_result.error_code == error_code
    if status == "request_replan":
        assert runtime_result.request_replan is True
        assert runtime_result.replan_reason == (
            "The current dependency needs a different plan."
        )
        assert len(agent.calls) == 1
    run = adapter.session_manager.repo.load_run(runtime_result.run_id)
    assert run is not None
    assert run.status == expected_run_status


def test_memory_event_persistence_failure_keeps_output_with_warning(
    tmp_path: Path,
) -> None:
    base = _adapter(tmp_path)

    class BrokenEventAdapter(RuntimeMemoryAdapter):
        def record_event(self, turn: Any, event: Any) -> Any:
            raise RuntimeError("database token=must-not-leak")

    adapter = BrokenEventAdapter(
        session_manager=base.session_manager,
        context_builder=base.context_builder,
    )
    runtime = _runtime(tmp_path, adapter, _ScenarioAgent())

    result = runtime.run(RuntimeRequest(input="temporary persistence"))

    assert result.success is True
    assert result.output == "Answer to temporary persistence"
    assert result.persistence_available is False
    assert result.persistence_warning is not None
    assert "must-not-leak" not in result.persistence_warning
    assert "token=***REDACTED***" in result.persistence_warning


def test_waiting_resume_approved_and_rejected_share_the_memory_run_boundary(
    tmp_path: Path,
) -> None:
    runtime, adapter, agent, registry, waiting = _start_waiting_run(tmp_path)

    approved = runtime.resume(
        ResumeRequest(
            session_id=CONFIRMATION_SESSION_ID,
            run_id=waiting.run_id,
            approved=True,
            confirmation_id="confirm_resume_1",
            preview_hash="preview_resume_1",
        )
    )

    assert approved.success is True
    assert approved.status == "completed"
    assert approved.metadata["resumed"] is True
    assert registry.get(waiting.run_id, session_id=CONFIRMATION_SESSION_ID) is None
    assert len(
        [
            item
            for item in adapter.get_timeline(CONFIRMATION_SESSION_ID)
            if item.item_kind == "execution_event"
        ]
    ) == 2
    assert isinstance(agent, _ResumeAgent)

    # A second independent waiting run exercises the rejection path without
    # mixing its pending context with the completed run above.
    waiting_again = runtime.run(
        RuntimeRequest(
            input="prepare another protected action",
            session_id=CONFIRMATION_SESSION_ID,
        )
    )
    rejected = runtime.resume(
        ResumeRequest(
            session_id=CONFIRMATION_SESSION_ID,
            run_id=waiting_again.run_id,
            approved=False,
            reason="User declined.",
            confirmation_id="confirm_resume_1",
            preview_hash="preview_resume_1",
        )
    )
    assert rejected.success is False
    assert rejected.error_code == RuntimeErrorCode.CANCELLED.value


def test_cancel_waiting_confirmation_cleans_pending_state_and_records_event(
    tmp_path: Path,
) -> None:
    runtime, adapter, _agent, registry, waiting = _start_waiting_run(tmp_path)

    cancelled = runtime.cancel(
        CancelRequest(
            session_id=CONFIRMATION_SESSION_ID,
            run_id=waiting.run_id,
            reason="User cancelled.",
        )
    )

    assert cancelled.success is False
    assert cancelled.status == "cancelled"
    assert cancelled.error_code == RuntimeErrorCode.CANCELLED.value
    assert registry.get(waiting.run_id, session_id=CONFIRMATION_SESSION_ID) is None
    assert any(
        item.item_kind == "execution_event"
        and item.metadata.get("event_type") == "system_notice"
        for item in adapter.get_timeline(CONFIRMATION_SESSION_ID)
    )


def test_runtime_health_reports_basic_cross_layer_dependencies(
    tmp_path: Path,
) -> None:
    adapter = _adapter(tmp_path)
    runtime = _runtime(
        tmp_path,
        adapter,
        _ScenarioAgent(),
        model_manager=_HealthyModels(),
        tool_manager=_HealthyTools(),
    )

    report = runtime.health()

    assert report["status"] == "healthy"
    assert report["checks"]["memory"]["status"] == "healthy"
    assert report["checks"]["models"]["status"] == "healthy"
    assert report["checks"]["tools"]["status"] == "healthy"
    assert report["checks"]["react_agent"]["metadata"]["manage_memory"] is False
