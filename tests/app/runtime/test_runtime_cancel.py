from __future__ import annotations

from pathlib import Path

from src.app.runtime import CancelRequest, PendingRunRegistry, ResumeRequest, RuntimeErrorCode

from tests.app.runtime.test_runtime_resume import (
    SESSION_ID,
    _ResumeAgent,
    _memory_adapter,
    _runtime,
    _start_waiting_run,
)


def test_cancel_waiting_user_run_rejects_confirmation_and_cleans_registry(
    tmp_path: Path,
) -> None:
    runtime, adapter, agent, registry, waiting = _start_waiting_run(tmp_path)

    result = runtime.cancel(
        CancelRequest(
            session_id=SESSION_ID,
            run_id=waiting.run_id,
            reason="User cancelled the protected action.",
        )
    )

    assert result.success is False
    assert result.status == "cancelled"
    assert result.error_code == RuntimeErrorCode.CANCELLED.value
    assert result.output == "User cancelled the protected action."
    assert result.pending_confirmation is None
    assert result.metadata["cancelled"] is True
    assert registry.get(waiting.run_id, session_id=SESSION_ID) is None
    assert len(agent.resume_calls) == 1
    assert agent.resume_calls[0]["approved"] is False

    event_items = [
        item
        for item in adapter.get_timeline(SESSION_ID)
        if getattr(item, "item_kind", None) == "execution_event"
    ]
    assert len(event_items) >= 2
    assert any(
        isinstance(getattr(item, "metadata", None), dict)
        and item.metadata.get("event_type") == "system_notice"
        and "cancelled" in str(getattr(item, "content", "")).lower()
        for item in event_items
    )


def test_cancel_missing_or_non_waiting_run_does_not_force_execution(
    tmp_path: Path,
) -> None:
    adapter = _memory_adapter(tmp_path)
    agent = _ResumeAgent()
    registry = PendingRunRegistry()
    runtime = _runtime(tmp_path, adapter, agent, registry)

    result = runtime.cancel(
        CancelRequest(
            session_id=SESSION_ID,
            run_id="run_not_waiting",
            reason="Stop it.",
        )
    )

    assert result.success is False
    assert result.error_code == RuntimeErrorCode.RUN_NOT_FOUND.value
    assert agent.resume_calls == []


def test_cancelled_run_cannot_be_resumed_again(tmp_path: Path) -> None:
    runtime, _adapter, agent, registry, waiting = _start_waiting_run(tmp_path)

    cancelled = runtime.cancel(
        CancelRequest(session_id=SESSION_ID, run_id=waiting.run_id)
    )
    resumed = runtime.resume(
        ResumeRequest(
            session_id=SESSION_ID,
            run_id=waiting.run_id,
            approved=True,
            confirmation_id="confirm_resume_1",
            preview_hash="preview_resume_1",
        )
    )

    assert cancelled.status == "cancelled"
    assert resumed.error_code == RuntimeErrorCode.RUN_NOT_FOUND.value
    assert len(agent.resume_calls) == 1
    assert registry.get(waiting.run_id, session_id=SESSION_ID) is None
