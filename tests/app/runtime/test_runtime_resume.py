from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.agent.output_feedback import OutputFeedbackProcessor
from src.agent.react_executor.react_executor_events import EventStream
from src.agent.react_executor.react_executor_protocol import (
    ExecutionEvent,
    ExecutionResult,
    PendingConfirmation,
)
from src.app.runtime import (
    PendingRunRegistry,
    ResumeRequest,
    Runtime,
    RuntimeErrorCode,
    RuntimeRequest,
)
from src.memory.config import MemoryConfig
from src.memory.runtime_adapter import RuntimeMemoryAdapter
from src.memory.session_manager import SessionManager


SESSION_ID = "session_20260825_120000_resume01"


class _ResumeAgent:
    def __init__(self) -> None:
        self.run_calls: list[tuple[str, dict[str, Any]]] = []
        self.resume_calls: list[dict[str, Any]] = []
        self.executor_context = SimpleNamespace(
            event_stream=EventStream(
                execution_id="execution_resume",
                plan_id="plan_resume",
            )
        )
        self.pending = PendingConfirmation(
            execution_id="execution_resume",
            plan_id="plan_resume",
            confirmation_type="confirmation",
            confirmation_message="Confirm the protected action.",
            pending_action={
                "action_type": "call_tool",
                "action_target": "protected_tool",
                "action_args": {
                    "path": "notes.txt",
                    "api_key": "must-not-leak",
                },
            },
            session_id=SESSION_ID,
            confirmation_id="confirm_resume_1",
            preview_hash="preview_resume_1",
            preview_summary="Update one protected file.",
        )

    def run_with_result(self, user_input: str, **kwargs: Any) -> ExecutionResult:
        self.run_calls.append((user_input, kwargs))
        event = ExecutionEvent(
            execution_id="execution_resume",
            plan_id="plan_resume",
            type="confirmation_requested",
            message="Confirmation is required.",
            event_id="event_resume_pending",
            visible_to_user=True,
            payload={
                "confirmation_id": "confirm_resume_1",
                "preview_hash": "preview_resume_1",
            },
        )
        kwargs["event_callback"](event)
        result = ExecutionResult(
            execution_id="execution_resume",
            plan_id="plan_resume",
            status="waiting_user",
            success=False,
            output="Please confirm the protected action.",
            summary="Waiting for confirmation.",
            requires_user_input=True,
            user_input_request="Please confirm the protected action.",
            pending_confirmation=self.pending,
            events=[event],
        )
        result.executor_context = self.executor_context
        return result

    def resume_after_confirmation(
        self,
        context: Any,
        *,
        approved: bool,
        reason: str = "",
        confirmation_id: str | None = None,
        preview_hash: str | None = None,
    ) -> ExecutionResult:
        self.resume_calls.append(
            {
                "context": context,
                "approved": approved,
                "reason": reason,
                "confirmation_id": confirmation_id,
                "preview_hash": preview_hash,
            }
        )
        if (
            confirmation_id != self.pending.confirmation_id
            or preview_hash != self.pending.preview_hash
        ):
            return ExecutionResult(
                execution_id="execution_resume",
                plan_id="plan_resume",
                status="failed",
                success=False,
                output="The confirmation ticket does not match.",
                summary="Confirmation mismatch.",
                error_code="confirmation_rejected",
            )
        if not approved:
            return ExecutionResult(
                execution_id="execution_resume",
                plan_id="plan_resume",
                status="failed",
                success=False,
                output=reason or "The action was rejected.",
                summary="Confirmation rejected.",
                error_code="confirmation_rejected",
            )

        event = context.event_stream.emit_event(
            "tool_finished",
            "Protected action completed.",
            payload={"tool_name": "protected_tool", "summary": "safe"},
            visible_to_user=True,
        )
        return ExecutionResult(
            execution_id="execution_resume",
            plan_id="plan_resume",
            status="completed",
            success=True,
            output="Resumed answer.",
            summary="Resumed answer.",
            events=[event],
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
    agent: _ResumeAgent,
    registry: PendingRunRegistry,
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
        react_executor=agent,
        react_agent=agent,
        output_feedback_processor=OutputFeedbackProcessor(),
        pending_run_registry=registry,
        recover_on_startup=False,
    )


def _start_waiting_run(
    tmp_path: Path,
) -> tuple[Runtime, RuntimeMemoryAdapter, _ResumeAgent, PendingRunRegistry, Any]:
    adapter = _memory_adapter(tmp_path)
    agent = _ResumeAgent()
    registry = PendingRunRegistry()
    runtime = _runtime(tmp_path, adapter, agent, registry)
    waiting = runtime.run(
        RuntimeRequest(
            input="prepare a protected action",
            session_id=SESSION_ID,
        )
    )
    assert waiting.status == "waiting_user"
    assert waiting.run_id is not None
    assert registry.get_public(waiting.run_id, session_id=SESSION_ID) is not None
    return runtime, adapter, agent, registry, waiting


def test_resume_approved_reuses_context_completes_turn_and_continues_timeline(
    tmp_path: Path,
) -> None:
    runtime, adapter, agent, registry, waiting = _start_waiting_run(tmp_path)

    result = runtime.resume(
        ResumeRequest(
            session_id=SESSION_ID,
            run_id=waiting.run_id,
            approved=True,
            confirmation_id="confirm_resume_1",
            preview_hash="preview_resume_1",
        )
    )

    assert result.success is True
    assert result.status == "completed"
    assert result.session_id == SESSION_ID
    assert result.run_id == waiting.run_id
    assert result.output == "Resumed answer."
    assert result.execution_result is not None
    assert result.output_feedback is not None
    assert result.memory_result is not None
    assert result.metadata["resumed"] is True
    assert registry.get(waiting.run_id, session_id=SESSION_ID) is None
    assert len(agent.resume_calls) == 1
    assert agent.resume_calls[0]["context"] is agent.executor_context

    event_items = [
        item for item in adapter.get_timeline(SESSION_ID)
        if getattr(item, "item_kind", None) == "execution_event"
    ]
    assert len(event_items) == 2
    assert "must-not-leak" not in str(result)


def test_resume_rejected_returns_safe_failed_result_and_clears_registry(
    tmp_path: Path,
) -> None:
    runtime, _adapter, agent, registry, waiting = _start_waiting_run(tmp_path)

    result = runtime.resume(
        ResumeRequest(
            session_id=SESSION_ID,
            run_id=waiting.run_id,
            approved=False,
            reason="User declined.",
            confirmation_id="confirm_resume_1",
            preview_hash="preview_resume_1",
        )
    )

    assert result.success is False
    assert result.status == "failed"
    assert result.error_code == RuntimeErrorCode.CANCELLED.value
    assert result.output == "User declined."
    assert registry.get(waiting.run_id, session_id=SESSION_ID) is None
    assert len(agent.resume_calls) == 1


@pytest.mark.parametrize(
    ("confirmation_id", "preview_hash"),
    [
        ("wrong-confirmation", "preview_resume_1"),
        ("confirm_resume_1", "wrong-preview"),
    ],
)
def test_resume_ticket_mismatch_is_failed_without_executing_protected_action(
    tmp_path: Path,
    confirmation_id: str,
    preview_hash: str,
) -> None:
    runtime, adapter, agent, registry, waiting = _start_waiting_run(tmp_path)

    result = runtime.resume(
        ResumeRequest(
            session_id=SESSION_ID,
            run_id=waiting.run_id,
            approved=True,
            confirmation_id=confirmation_id,
            preview_hash=preview_hash,
        )
    )

    assert result.success is False
    assert result.status == "failed"
    assert result.output == "The confirmation ticket does not match."
    assert registry.get(waiting.run_id, session_id=SESSION_ID) is None
    assert len(agent.resume_calls) == 1
    assert len(agent.executor_context.event_stream.visible_events()) == 0
    event_items = [
        item for item in adapter.get_timeline(SESSION_ID)
        if getattr(item, "item_kind", None) == "execution_event"
    ]
    assert len(event_items) == 1


def test_resume_missing_pending_run_returns_run_not_found(tmp_path: Path) -> None:
    adapter = _memory_adapter(tmp_path)
    agent = _ResumeAgent()
    registry = PendingRunRegistry()
    runtime = _runtime(tmp_path, adapter, agent, registry)

    result = runtime.resume(
        ResumeRequest(
            session_id=SESSION_ID,
            run_id="run_missing_resume",
            approved=True,
            confirmation_id="confirm_resume_1",
            preview_hash="preview_resume_1",
        )
    )

    assert result.success is False
    assert result.status == "failed"
    assert result.error_code == RuntimeErrorCode.RUN_NOT_FOUND.value
    assert result.session_id == SESSION_ID
    assert result.run_id == "run_missing_resume"
    assert agent.resume_calls == []


def test_resume_public_pending_confirmation_does_not_expose_action_args(
    tmp_path: Path,
) -> None:
    runtime, _adapter, _agent, registry, waiting = _start_waiting_run(tmp_path)

    public = registry.get_public(waiting.run_id, session_id=SESSION_ID)
    assert public is not None
    assert public["pending_confirmation"]["confirmation_id"] == "confirm_resume_1"
    assert "action_args" not in str(public)
    assert "must-not-leak" not in str(public)
    assert "executor_context" not in public

    # Keep the runtime alive through the same process-local path; this also
    # confirms the public snapshot did not consume the pending record.
    assert runtime.pending_run_registry.get(waiting.run_id, session_id=SESSION_ID)
