from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.app.runtime import (
    PendingRunRegistry,
    Runtime,
    RuntimeErrorCode,
    RuntimeException,
)
from src.memory.config import MemoryConfig
from src.memory.ids import new_event_id
from src.memory.models import ExecutionEventStatus
from src.memory.runtime_adapter import RuntimeMemoryAdapter
from src.memory.session_manager import SessionManager


class _NoOp:
    pass


def _runtime(tmp_path: Path) -> tuple[Runtime, SessionManager]:
    manager = SessionManager(
        config=MemoryConfig.default(tmp_path),
    )
    memory = RuntimeMemoryAdapter(session_manager=manager)
    runtime = Runtime(
        config=SimpleNamespace(workspace_root=tmp_path),
        model_manager=_NoOp(),
        tool_manager=_NoOp(),
        tool_registry=_NoOp(),
        session_manager=manager,
        context_builder=memory.context_builder,
        memory_adapter=memory,
        analyzer=_NoOp(),
        planner=_NoOp(),
        react_executor=_NoOp(),
        react_agent=_NoOp(),
        output_feedback_processor=_NoOp(),
        pending_run_registry=_NoOp(),
        recover_on_startup=False,
    )
    return runtime, manager


def test_runtime_list_sessions_returns_safe_session_fields(tmp_path: Path) -> None:
    runtime, manager = _runtime(tmp_path)
    session = manager.create_session(
        "session_list",
        title="A conversation",
        metadata={"channel": "test"},
    )

    sessions = runtime.list_sessions()

    assert sessions == [
        {
            "session_id": session.session_id,
            "status": "active",
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "last_activity_at": session.last_activity_at,
            "message_count": 0,
            "title": "A conversation",
            "current_summary_id": None,
            "metadata": {"channel": "test"},
        }
    ]


def test_runtime_get_session_and_timeline_map_missing_session(tmp_path: Path) -> None:
    runtime, manager = _runtime(tmp_path)
    session = manager.create_session("session_read")
    user_message, run = manager.create_user_turn(session.session_id, "hello")
    manager.append_execution_event(
        session.session_id,
        run.run_id,
        {
            "event_id": new_event_id(),
            "session_id": session.session_id,
            "run_id": run.run_id,
            "event_type": "tool_started",
            "display_type": "tool_progress",
            "display_content": "visible progress",
            "visible_to_user": True,
            "status": ExecutionEventStatus.STARTED.value,
            "created_at": "2026-08-26T00:00:01Z",
        },
    )
    manager.append_execution_event(
        session.session_id,
        run.run_id,
        {
            "event_id": new_event_id(),
            "session_id": session.session_id,
            "run_id": run.run_id,
            "event_type": "reasoning",
            "display_type": "plan_progress",
            "display_content": "hidden reasoning",
            "visible_to_user": False,
            "status": ExecutionEventStatus.RECORDED.value,
            "created_at": "2026-08-26T00:00:02Z",
        },
    )
    manager.append_message(
        session.session_id,
        "system",
        "hidden message",
        visible_to_user=False,
    )

    loaded = runtime.get_session(session.session_id)
    timeline = runtime.get_timeline(session.session_id)

    assert loaded["session_id"] == session.session_id
    assert "hidden message" not in str(loaded)
    assert [item["content"] for item in timeline] == [
        "hello",
        "visible progress",
    ]
    assert all(item["item_kind"] in {"message", "execution_event"} for item in timeline)
    assert "hidden reasoning" not in str(timeline)
    assert user_message.session_id == session.session_id

    with pytest.raises(RuntimeException) as exc_info:
        runtime.get_session("session_missing")
    assert exc_info.value.code == RuntimeErrorCode.SESSION_NOT_FOUND.value

    with pytest.raises(RuntimeException) as exc_info:
        runtime.get_timeline("session_missing")
    assert exc_info.value.code == RuntimeErrorCode.SESSION_NOT_FOUND.value


def test_runtime_delete_session_is_hard_delete(tmp_path: Path) -> None:
    runtime, manager = _runtime(tmp_path)
    manager.create_session("session_delete")

    assert runtime.delete_session("session_delete") is True
    with pytest.raises(RuntimeException) as exc_info:
        runtime.get_session("session_delete")
    assert exc_info.value.code == RuntimeErrorCode.SESSION_NOT_FOUND.value

    with pytest.raises(RuntimeException) as exc_info:
        runtime.delete_session("session_delete")
    assert exc_info.value.code == RuntimeErrorCode.SESSION_NOT_FOUND.value


def test_runtime_delete_session_clears_process_local_pending_state(
    tmp_path: Path,
) -> None:
    manager = SessionManager(config=MemoryConfig.default(tmp_path))
    memory = RuntimeMemoryAdapter(session_manager=manager)
    registry = PendingRunRegistry()
    runtime = Runtime(
        config=SimpleNamespace(workspace_root=tmp_path),
        model_manager=_NoOp(),
        tool_manager=_NoOp(),
        tool_registry=_NoOp(),
        session_manager=manager,
        context_builder=memory.context_builder,
        memory_adapter=memory,
        analyzer=_NoOp(),
        planner=_NoOp(),
        react_executor=_NoOp(),
        react_agent=_NoOp(),
        output_feedback_processor=_NoOp(),
        pending_run_registry=registry,
        recover_on_startup=False,
    )
    manager.create_session("session_pending_delete")
    registry.register(
        "session_pending_delete",
        "run_pending_delete",
        executor_context=object(),
        pending_confirmation={"confirmation_id": "confirm-1"},
    )

    assert runtime.delete_session("session_pending_delete") is True
    assert registry.get_public("run_pending_delete") is None
