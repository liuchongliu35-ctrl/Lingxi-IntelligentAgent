from __future__ import annotations

import importlib
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from src.agent.orchestrator.output_feedback import FeedbackItem, OutputFeedback
from src.agent.react_executor.react_executor_protocol import (
    ExecutionEvent,
    ExecutionResult,
    ObservationPacket,
    PendingConfirmation,
)
from src.app.runtime.contracts import RuntimeEvent, RuntimeResult
from src.app.runtime.serialization import (
    build_debug_metadata,
    safe_serialize,
    serialize_execution_result,
    serialize_memory_result,
    serialize_output_feedback,
    serialize_pending_confirmation,
    serialize_runtime_event,
    serialize_runtime_result,
)
from src.memory.models import (
    DisplayType,
    Message,
    MessageRole,
    SessionState,
    TimelineItem,
    TimelineItemKind,
)


class ExampleStatus(str, Enum):
    READY = "ready"


@dataclass
class ExampleValue:
    status: ExampleStatus
    created_at: datetime
    values: tuple[int, ...]
    credentials: dict[str, str]


class SecretObject:
    def __repr__(self) -> str:
        return "SecretObject(api_key=do-not-leak)"


def test_safe_serialize_supports_common_values_and_rejects_unknown_repr() -> None:
    value = ExampleValue(
        status=ExampleStatus.READY,
        created_at=datetime(2026, 8, 24, tzinfo=timezone.utc),
        values=(1, 2),
        credentials={"api_key": "secret", "visible": "ok"},
    )

    assert safe_serialize(value) == {
        "status": "ready",
        "created_at": "2026-08-24T00:00:00+00:00",
        "values": [1, 2],
        "credentials": {"visible": "ok"},
    }
    assert safe_serialize(SecretObject()) == {"type": "SecretObject"}


def test_safe_serialize_filters_sensitive_keys_and_inline_secrets_recursively() -> None:
    result = safe_serialize(
        {
            "outer": [
                {
                    "raw_prompt": "hidden",
                    "password": "secret",
                    "message": "Bearer abc.def and token=secret-value",
                    "command": "tool --password=secret-value",
                    "safe": "kept",
                }
            ]
        }
    )

    assert "raw_prompt" not in str(result)
    assert "password" not in str(result)
    assert "secret-value" not in str(result)
    assert "abc.def" not in str(result)
    assert result["outer"][0]["safe"] == "kept"
    assert "Bearer ***REDACTED***" in result["outer"][0]["message"]


def test_execution_result_serialization_filters_hidden_events_and_raw_fields() -> None:
    visible_event = ExecutionEvent(
        execution_id="execution_1",
        plan_id="plan_1",
        type="tool_finished",
        message="tool finished",
        payload={
            "result": "safe",
            "raw_tool_result": "do-not-leak",
            "authorization": "secret",
        },
    )
    hidden_event = ExecutionEvent(
        execution_id="execution_1",
        plan_id="plan_1",
        type="model_step_finished",
        message="internal",
        visible_to_user=False,
        payload={"hidden_reasoning": "do-not-leak"},
    )
    observation = ObservationPacket(
        execution_id="execution_1",
        plan_id="plan_1",
        action_type="call_tool",
        tool_name="safe_tool",
        data={"value": "safe"},
        raw_observation={"password": "do-not-leak"},
    )
    result = ExecutionResult(
        execution_id="execution_1",
        plan_id="plan_1",
        status="completed",
        success=True,
        output="done",
        observations=[observation],
        events=[visible_event, hidden_event],
    )

    serialized = serialize_execution_result(result)

    assert len(serialized["events"]) == 1
    assert serialized["events"][0]["type"] == "tool_finished"
    assert "raw_tool_result" not in str(serialized)
    assert "hidden_reasoning" not in str(serialized)
    assert "password" not in str(serialized)
    assert serialized["observations"][0]["data"] == {"value": "safe"}
    assert "raw_observation" not in str(serialized)


def test_pending_confirmation_is_whitelisted_without_executor_context() -> None:
    pending = PendingConfirmation(
        execution_id="execution_1",
        plan_id="plan_1",
        confirmation_type="confirmation",
        confirmation_message="Allow the safe file update?",
        pending_action={
            "action_type": "call_tool",
            "action_target": "file_writer",
            "action_args": {
                "path": "C:/private.txt",
                "raw_prompt": "hidden",
                "token": "secret",
            },
            "raw_executor_context": "must-not-leak",
        },
        confirmation_id="confirmation_1",
        preview_hash="preview_hash_1",
        preview_summary="Update one selected file.",
        expires_at="2026-08-24T01:00:00Z",
    )

    serialized = serialize_pending_confirmation(pending)

    assert serialized == {
        "confirmation_id": "confirmation_1",
        "preview_hash": "preview_hash_1",
        "confirmation_type": "confirmation",
        "action_name": "file_writer",
        "action_type": "call_tool",
        "confirmation_message": "Allow the safe file update?",
        "preview_summary": "Update one selected file.",
        "expires_at": "2026-08-24T01:00:00Z",
    }
    assert "raw_executor_context" not in str(serialized)
    assert "C:/private.txt" not in str(serialized)


def test_memory_models_and_output_feedback_are_safe_to_serialize() -> None:
    message = Message(
        message_id="message_1",
        session_id="session_20260824_120000_demo001",
        timeline_seq=1,
        role=MessageRole.USER,
        content="hello",
        metadata={"api_key": "hidden", "channel": "cli"},
    )
    session = SessionState(
        session_id="session_20260824_120000_demo001",
        messages=[message],
        metadata={"token": "hidden", "source": "test"},
    )
    timeline = TimelineItem(
        item_id="message_1",
        item_kind=TimelineItemKind.MESSAGE,
        session_id=session.session_id,
        timeline_seq=1,
        display_type=DisplayType.CHAT,
        content="hello",
        status="completed",
        created_at="2026-08-24T00:00:00Z",
    )
    feedback = OutputFeedback(
        execution_id="execution_1",
        plan_id="plan_1",
        status="completed",
        success=True,
        final_output="done",
        timeline=[{"message": "safe", "raw_prompt": "hidden"}],
        items=[FeedbackItem(source="execution_event", type="final_answer")],
    )

    memory = serialize_memory_result(
        {"session": session, "timeline": [timeline], "raw_observation": "hidden"}
    )
    output = serialize_output_feedback(feedback)

    assert memory["session"]["metadata"] == {"source": "test"}
    assert memory["timeline"][0]["content"] == "hello"
    assert "raw_observation" not in str(memory)
    assert output["final_output"] == "done"
    assert "raw_prompt" not in str(output)


def test_debug_metadata_is_explicit_and_allowlisted() -> None:
    assert build_debug_metadata(
        debug=False,
        metadata={
            "debug": {
                "event_count": 2,
                "raw_prompt": "hidden",
            }
        },
    ) == {}

    debug = build_debug_metadata(
        debug=True,
        metadata={
            "event_count": 2,
            "raw_prompt": "hidden",
            "debug_only_unknown": "drop",
        },
        model_profile="mock",
    )

    assert debug == {"event_count": 2, "model_profile": "mock"}
    assert "raw_prompt" not in str(debug)
    assert "debug_only_unknown" not in debug


def test_runtime_result_serialization_enforces_debug_and_nested_boundaries() -> None:
    result = RuntimeResult(
        success=True,
        status="completed",
        session_id="session_20260824_120000_demo001",
        run_id="run_20260824_120000_demo001",
        output="done",
        metadata={
            "entrypoint": "test",
            "debug": {
                "event_count": 3,
                "plan_summary": "safe summary",
                "raw_prompt": "hidden",
            },
        },
        execution_result={
            "execution_id": "execution_1",
            "status": "completed",
            "success": True,
            "output": "done",
            "events": [],
            "observations": [],
            "pending_confirmation": {
                "confirmation_id": "confirmation_1",
                "preview_hash": "hash",
                "pending_action": {"raw_prompt": "hidden"},
            },
        },
    )

    without_debug = serialize_runtime_result(result, debug=False)
    with_debug = serialize_runtime_result(result, debug=True)

    assert "debug" not in without_debug["metadata"]
    assert with_debug["metadata"]["debug"] == {
        "event_count": 3,
        "plan_summary": "safe summary",
    }
    assert "raw_prompt" not in str(with_debug)
    assert "pending_action" not in str(with_debug)


def test_runtime_event_visibility_is_preserved() -> None:
    event = RuntimeEvent(
        session_id="session_20260824_120000_demo001",
        run_id="run_20260824_120000_demo001",
        event_type="model_step_finished",
        visible_to_user=False,
        payload={"raw_observation": "hidden"},
    )

    assert serialize_runtime_event(event) is None
    internal = serialize_runtime_event(event, include_invisible=True)
    assert internal is not None
    assert internal["visible_to_user"] is False
    assert "raw_observation" not in str(internal)


def test_serialization_module_does_not_depend_on_adapters_or_frameworks() -> None:
    module = importlib.import_module("src.app.runtime.serialization")
    source = module.__file__
    assert source is not None
    text = open(source, encoding="utf-8").read().lower()
    assert "fastapi" not in text
    assert "typer" not in text
    assert "sqlite" not in text
    assert "sqlite_session_repository" not in text
