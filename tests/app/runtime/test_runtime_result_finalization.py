from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from src.agent.output_feedback import OutputFeedbackProcessor
from src.agent.react_executor.react_executor_protocol import (
    ExecutionResult,
    PendingConfirmation,
)
from src.app.runtime import PendingRunRegistry, Runtime, RuntimeRequest
from src.memory.config import MemoryConfig
from src.memory.runtime_adapter import RuntimeMemoryAdapter
from src.memory.session_manager import SessionManager


SESSION_ID = "session_20260825_100000_finalize01"


class _ResultAgent:
    def __init__(self, result: ExecutionResult | None = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error

    def run_with_result(self, user_input: str, **kwargs: Any) -> ExecutionResult:
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


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
    agent: _ResultAgent,
    registry: PendingRunRegistry | None = None,
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
        pending_run_registry=(
            registry if registry is not None else PendingRunRegistry()
        ),
        recover_on_startup=False,
    )


def test_completed_result_closes_memory_turn_and_returns_timeline(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    agent = _ResultAgent(
        ExecutionResult(
            execution_id="execution_finalize_completed",
            plan_id="plan_finalize",
            status="completed",
            success=True,
            output="completed answer",
            summary="completed summary",
        )
    )
    runtime = _runtime(tmp_path, adapter, agent)

    result = runtime.run(RuntimeRequest(input="complete me", session_id=SESSION_ID))

    assert result.success is True
    assert result.status == "completed"
    assert result.memory_result is not None
    assert result.timeline
    assert [item["role"] for item in result.timeline if "role" in item] == [
        "user",
        "assistant",
    ]
    run = adapter.session_manager.repo.load_run(result.run_id)
    assert run is not None
    assert run.status == "completed"
    assert run.final_message_id is not None


def test_failed_result_calls_fail_turn_and_preserves_agent_failure(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    agent = _ResultAgent(
        ExecutionResult(
            execution_id="execution_finalize_failed",
            plan_id="plan_finalize",
            status="failed",
            success=False,
            output="The tool failed safely.",
            error_code="tool_execution_failed",
        )
    )
    runtime = _runtime(tmp_path, adapter, agent)

    result = runtime.run(RuntimeRequest(input="fail me", session_id=SESSION_ID))

    assert result.success is False
    assert result.status == "failed"
    assert result.error_code == "agent_execution_failed"
    assert result.memory_result is not None
    run = adapter.session_manager.repo.load_run(result.run_id)
    assert run is not None
    assert run.status == "failed"


def test_blocked_result_maps_policy_error_and_closes_run(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    agent = _ResultAgent(
        ExecutionResult(
            execution_id="execution_finalize_blocked",
            plan_id="plan_finalize",
            status="blocked",
            success=False,
            output="This action is blocked by policy.",
            error_code="action_blocked",
        )
    )
    runtime = _runtime(tmp_path, adapter, agent)

    result = runtime.run(RuntimeRequest(input="block me", session_id=SESSION_ID))

    assert result.success is False
    assert result.status == "blocked"
    assert result.error_code == "blocked_by_policy"
    assert result.output == "This action is blocked by policy."
    run = adapter.session_manager.repo.load_run(result.run_id)
    assert run is not None
    assert run.status == "blocked"


def test_waiting_user_registers_safe_pending_run_without_failing_turn(
    tmp_path: Path,
) -> None:
    adapter = _adapter(tmp_path)
    pending = PendingConfirmation(
        execution_id="execution_finalize_waiting",
        plan_id="plan_finalize",
        confirmation_type="confirmation",
        confirmation_message="Confirm the file change.",
        pending_action={
            "action_type": "call_tool",
            "action_target": "write_file",
            "action_args": {"content": "secret should not be public"},
        },
        session_id=SESSION_ID,
        confirmation_id="confirmation_finalize",
        preview_hash="preview_finalize",
        preview_summary="Write one file.",
    )
    agent = _ResultAgent(
        ExecutionResult(
            execution_id="execution_finalize_waiting",
            plan_id="plan_finalize",
            status="waiting_user",
            success=False,
            output="Confirmation is required.",
            requires_user_input=True,
            pending_confirmation=pending,
        )
    )
    registry = PendingRunRegistry()
    runtime = _runtime(tmp_path, adapter, agent, registry)

    result = runtime.run(RuntimeRequest(input="wait for me", session_id=SESSION_ID))

    assert result.success is False
    assert result.status == "waiting_user"
    assert result.requires_user_input is True
    assert result.pending_confirmation is not None
    assert "action_args" not in str(result.pending_confirmation)
    public = registry.get_public(result.run_id, session_id=SESSION_ID)
    assert public is not None
    assert public["pending_confirmation"]["confirmation_id"] == "confirmation_finalize"
    run = adapter.session_manager.repo.load_run(result.run_id)
    assert run is not None
    assert run.status == "running"


def test_request_replan_is_returned_without_automatic_loop(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    agent = _ResultAgent(
        ExecutionResult(
            execution_id="execution_finalize_replan",
            plan_id="plan_finalize",
            status="request_replan",
            success=False,
            output="The current plan needs revision.",
            request_replan=True,
            replan_reason="The dependency is unavailable.",
        )
    )
    runtime = _runtime(tmp_path, adapter, agent)

    result = runtime.run(RuntimeRequest(input="replan me", session_id=SESSION_ID))

    assert result.success is False
    assert result.status == "request_replan"
    assert result.request_replan is True
    assert result.replan_reason == "The dependency is unavailable."
    run = adapter.session_manager.repo.load_run(result.run_id)
    assert run is not None
    assert run.status == "request_replan"


def test_agent_exception_is_failed_and_memory_run_is_closed(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    runtime = _runtime(
        tmp_path,
        adapter,
        _ResultAgent(error=RuntimeError("model call failed")),
    )

    result = runtime.run(RuntimeRequest(input="exception", session_id=SESSION_ID))

    assert result.success is False
    assert result.error_code == "agent_execution_failed"
    assert result.memory_result is not None
    run = adapter.session_manager.repo.load_run(result.run_id)
    assert run is not None
    assert run.status == "failed"
