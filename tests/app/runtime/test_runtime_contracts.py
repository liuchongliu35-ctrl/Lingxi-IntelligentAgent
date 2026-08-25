from __future__ import annotations

import importlib
from datetime import datetime, timezone

import pytest

from src.app.runtime.contracts import (
    CancelRequest,
    ResumeRequest,
    RuntimeEvent,
    RuntimeRequest,
    RuntimeResult,
    RuntimeStatus,
)


def test_runtime_request_validates_and_dictifies_without_generating_ids() -> None:
    request = RuntimeRequest(
        input="  hello  ",
        session_id="session_20260824_120000_demo001",
        stream=True,
        debug=True,
        metadata={"entrypoint": "cli"},
        model_profile="mock",
        agent_version="v1",
    )

    assert request.input == "hello"
    assert request.session_id == "session_20260824_120000_demo001"
    assert request.to_dict() == {
        "input": "hello",
        "session_id": "session_20260824_120000_demo001",
        "stream": True,
        "debug": True,
        "metadata": {"entrypoint": "cli"},
        "model_profile": "mock",
        "agent_version": "v1",
    }


@pytest.mark.parametrize("value", ["", "   ", None])
def test_runtime_request_rejects_empty_or_non_text_input(value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        RuntimeRequest(input=value)  # type: ignore[arg-type]


def test_runtime_request_rejects_invalid_session_id() -> None:
    with pytest.raises(ValueError):
        RuntimeRequest(input="hello", session_id="../unsafe")


def test_runtime_result_has_complete_default_contract() -> None:
    result = RuntimeResult()

    assert result.success is False
    assert result.status == RuntimeStatus.FAILED.value
    assert result.session_id is None
    assert result.run_id is None
    assert result.output == ""
    assert result.execution_result is None
    assert result.output_feedback is None
    assert result.memory_result is None
    assert result.timeline == []
    assert result.requires_user_input is False
    assert result.pending_confirmation is None
    assert result.request_replan is False
    assert result.replan_reason is None
    assert result.error_code is None
    assert result.error_message is None
    assert result.persistence_available is True
    assert result.persistence_warning is None
    assert result.metadata == {}


def test_runtime_result_can_express_waiting_user_and_replan() -> None:
    waiting = RuntimeResult(
        success=False,
        status=RuntimeStatus.WAITING_USER,
        session_id="session_20260824_120000_demo001",
        run_id="run_20260824_120000_demo001",
        requires_user_input=True,
        pending_confirmation={"confirmation_id": "confirm_1", "preview_hash": "hash"},
    )
    replan = RuntimeResult(
        status="request_replan",
        request_replan=True,
        replan_reason="tool unavailable",
    )

    assert waiting.status == "waiting_user"
    assert waiting.to_dict()["pending_confirmation"]["confirmation_id"] == "confirm_1"
    assert replan.status == "request_replan"
    assert replan.request_replan is True


def test_runtime_event_dictifies_and_keeps_memory_event_id_optional() -> None:
    created_at = datetime(2026, 8, 24, tzinfo=timezone.utc)
    event = RuntimeEvent(
        session_id="session_20260824_120000_demo001",
        run_id="run_20260824_120000_demo001",
        event_type="progress_message",
        message="working",
        payload={"step": 1},
        source_event={"event_id": "event_source_1", "visible_to_user": True},
        sequence=1,
        created_at=created_at.isoformat(),
    )

    assert event.event_id is None
    assert event.to_dict() == {
        "session_id": "session_20260824_120000_demo001",
        "run_id": "run_20260824_120000_demo001",
        "event_type": "progress_message",
        "message": "working",
        "visible_to_user": True,
        "payload": {"step": 1},
        "source_event": {"event_id": "event_source_1", "visible_to_user": True},
        "sequence": 1,
        "event_id": None,
        "created_at": created_at.isoformat(),
    }


@pytest.mark.parametrize("status", ["completed", "failed", "blocked", "waiting_user", "request_replan", "cancelled", "interrupted"])
def test_all_runtime_statuses_are_supported(status: str) -> None:
    assert RuntimeResult(status=status).status == status


def test_internal_resume_and_cancel_requests_are_dictifiable() -> None:
    resume = ResumeRequest(
        session_id="session_20260824_120000_demo001",
        run_id="run_20260824_120000_demo001",
        approved=True,
        confirmation_id="confirm_1",
        preview_hash="hash",
    )
    cancel = CancelRequest(
        session_id="session_20260824_120000_demo001",
        run_id="run_20260824_120000_demo001",
        reason="user cancelled",
    )

    assert resume.to_dict()["approved"] is True
    assert cancel.to_dict()["reason"] == "user cancelled"


def test_contracts_do_not_depend_on_cli_api_or_sqlite_repository() -> None:
    module = importlib.import_module("src.app.runtime.contracts")
    source = module.__file__
    assert source is not None
    text = open(source, encoding="utf-8").read()
    assert "fastapi" not in text.lower()
    assert "typer" not in text.lower()
    assert "sqlite" not in text.lower()
    assert "SQLiteSessionRepository" not in text
