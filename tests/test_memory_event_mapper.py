from __future__ import annotations

from pathlib import Path

from src.agent.react_executor.react_executor_protocol import ExecutionEvent
from src.memory.config import MemoryConfig
from src.memory.event_mapper import (
    REDACTED_VALUE,
    map_display_type,
    map_event_status,
    map_execution_event,
)
from src.memory.ids import new_session_id
from src.memory.session_manager import SessionManager


def _make_config(tmp_path: Path) -> MemoryConfig:
    return MemoryConfig(
        database_path=tmp_path / "memory.db",
        log_path=tmp_path / "memory.log",
        max_recent_messages=10,
        summary_trigger_messages=14,
        summary_batch_messages=6,
        summary_target_chars=2000,
        max_message_content_chars=12000,
        max_event_display_chars=80,
        max_event_payload_chars=60,
    )


def test_mapper_converts_visible_react_event_to_memory_record(tmp_path: Path) -> None:
    event = ExecutionEvent(
        execution_id="exec_1",
        plan_id="plan_1",
        type="tool_started",
        message="Calling tool with token=secret-token",
        event_id="event_deadbeef1234",
        task_id="task_1",
        step_id="step_1",
        timestamp="2026-08-20T01:02:03Z",
        visible_to_user=True,
        payload={
            "tool_name": "math_calculator",
            "api_key": "secret",
            "safe": "ok",
            "action_args": {"expression": "2+3", "password": "pw"},
            "nested": {"authorization": "Bearer abc"},
        },
    )

    record = map_execution_event(
        event,
        session_id=new_session_id(),
        run_id="run_20260820_010203_abcdef",
        config=_make_config(tmp_path),
    )

    assert record is not None
    assert record.event_id == "event_20260820_010203_deadbeef1234"
    assert record.event_type == "tool_started"
    assert record.display_type == "tool_progress"
    assert record.status == "started"
    assert REDACTED_VALUE in record.display_content
    assert "secret-token" not in record.display_content
    assert record.sanitized_payload["api_key"] == REDACTED_VALUE
    assert record.sanitized_payload["action_args"] == REDACTED_VALUE
    assert record.sanitized_payload["nested"]["authorization"] == REDACTED_VALUE
    assert record.sanitized_payload["safe"] == "ok"
    assert record.metadata["source_event_id"] == "event_deadbeef1234"
    assert record.metadata["execution_id"] == "exec_1"


def test_mapper_skips_internal_events_even_when_type_is_final_answer(tmp_path: Path) -> None:
    event = ExecutionEvent(
        execution_id="exec_1",
        plan_id="plan_1",
        type="final_answer",
        message="Internal final answer.",
        event_id="event_feedface1234",
        timestamp="2026-08-20T01:02:03Z",
        visible_to_user=False,
        payload={"raw_model_output": "hidden"},
    )

    record = map_execution_event(
        event,
        session_id=new_session_id(),
        run_id="run_20260820_010203_abcdef",
        config=_make_config(tmp_path),
    )

    assert record is None


def test_mapper_display_and_status_rules_cover_replay_event_types() -> None:
    assert map_display_type("tool_finished") == "tool_progress"
    assert map_display_type("confirmation_requested") == "confirmation"
    assert map_display_type("step_started") == "plan_progress"
    assert map_display_type("fallback_finished") == "plan_progress"
    assert map_display_type("tool_failed") == "error"
    assert map_display_type("retry_exhausted") == "error"
    assert map_display_type("final_answer") == "final_answer"

    assert map_event_status("command_started") == "started"
    assert map_event_status("command_finished") == "completed"
    assert map_event_status("tool_failed") == "failed"
    assert map_event_status("confirmation_requested") == "waiting_user"
    assert map_event_status("request_replan") == "request_replan"


def test_session_manager_persists_visible_events_and_replays_timeline(tmp_path: Path) -> None:
    manager = SessionManager(config=_make_config(tmp_path))
    user_message, run = manager.create_user_turn(None, "calculate 2+3")
    visible = ExecutionEvent(
        execution_id="exec_1",
        plan_id="plan_1",
        type="tool_finished",
        message="Tool completed.",
        event_id="event_111111111111",
        task_id="task_1",
        step_id="step_1",
        timestamp="2026-08-20T01:02:03Z",
        visible_to_user=True,
        payload={"tool_name": "math_calculator", "summary": "5"},
    )
    hidden = ExecutionEvent(
        execution_id="exec_1",
        plan_id="plan_1",
        type="final_answer",
        message="Internal result.",
        event_id="event_222222222222",
        timestamp="2026-08-20T01:02:04Z",
        visible_to_user=False,
        payload={"raw_model_output": "hidden"},
    )

    first_store = manager.append_execution_event(user_message.session_id, run.run_id, visible)
    second_store = manager.append_execution_event(user_message.session_id, run.run_id, visible)
    hidden_store = manager.append_execution_event(user_message.session_id, run.run_id, hidden)
    assistant = manager.append_message(
        user_message.session_id,
        "assistant",
        "The answer is 5.",
        run_id=run.run_id,
    )

    timeline = manager.get_session_timeline(user_message.session_id)

    assert first_store is not None
    assert second_store is not None
    assert second_store.event_id == first_store.event_id
    assert hidden_store is None
    assert [item.item_kind for item in timeline] == ["message", "execution_event", "message"]
    assert [item.item_id for item in timeline] == [
        user_message.message_id,
        first_store.event_id,
        assistant.message_id,
    ]
    assert timeline[1].display_type == "tool_progress"
    assert timeline[1].metadata["event_type"] == "tool_finished"
    assert timeline[1].metadata["sanitized_payload"]["summary"] == "5"
    assert len([item for item in timeline if item.item_kind == "execution_event"]) == 1


def test_session_timeline_excludes_hidden_messages(tmp_path: Path) -> None:
    manager = SessionManager(config=_make_config(tmp_path))
    session = manager.create_session()

    visible = manager.append_message(session.session_id, "user", "hello")
    manager.append_message(
        session.session_id,
        "system",
        "hidden system note",
        visible_to_user=False,
    )

    timeline = manager.get_session_timeline(session.session_id)

    assert [item.item_id for item in timeline] == [visible.message_id]
