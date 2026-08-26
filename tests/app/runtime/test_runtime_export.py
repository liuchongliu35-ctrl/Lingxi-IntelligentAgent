from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.app.runtime import Runtime, RuntimeErrorCode, RuntimeException
from src.memory.config import MemoryConfig
from src.memory.ids import new_event_id
from src.memory.models import ExecutionEventStatus
from src.memory.runtime_adapter import RuntimeMemoryAdapter
from src.memory.session_manager import SessionManager


class _NoOp:
    pass


def _runtime(tmp_path: Path) -> tuple[Runtime, SessionManager]:
    manager = SessionManager(config=MemoryConfig.default(tmp_path))
    memory = RuntimeMemoryAdapter(session_manager=manager)
    return (
        Runtime(
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
        ),
        manager,
    )


def test_runtime_export_returns_safe_markdown_and_writes_without_overwrite(
    tmp_path: Path,
) -> None:
    runtime, manager = _runtime(tmp_path)
    session = manager.create_session("session_export", title="Export me")
    user_message, run = manager.create_user_turn(
        session.session_id,
        "What is the answer?",
    )
    manager.append_execution_event(
        session.session_id,
        run.run_id,
        {
            "event_id": new_event_id(),
            "session_id": session.session_id,
            "run_id": run.run_id,
            "event_type": "tool_completed",
            "display_type": "tool_progress",
            "display_content": "A safe result",
            "visible_to_user": True,
            "status": ExecutionEventStatus.COMPLETED.value,
            "created_at": "2026-08-26T00:00:01Z",
            "sanitized_payload": {
                "raw_tool_result": "secret result",
                "api_key": "secret key",
            },
        },
    )
    assistant = manager.append_message(
        session.session_id,
        "assistant",
        "The answer is ready.",
        run_id=run.run_id,
    )
    manager.complete_run(run.run_id, assistant.message_id)

    markdown = runtime.export_session(session.session_id)
    assert "# Session" in markdown
    assert "### User" in markdown
    assert "What is the answer?" in markdown
    assert "### Assistant" in markdown
    assert "The answer is ready." in markdown
    assert "## Execution Events" in markdown
    assert "A safe result" in markdown
    assert "raw_tool_result" not in markdown
    assert "secret result" not in markdown
    assert "api_key" not in markdown
    assert "secret key" not in markdown

    destination = tmp_path / "export.md"
    assert runtime.export_session(session.session_id, destination) == markdown
    assert destination.read_text(encoding="utf-8") == markdown

    with pytest.raises(RuntimeException) as exc_info:
        runtime.export_session(session.session_id, destination)
    assert exc_info.value.code == RuntimeErrorCode.EXPORT_FAILED.value


def test_runtime_export_rejects_paths_outside_workspace(tmp_path: Path) -> None:
    runtime, manager = _runtime(tmp_path)
    manager.create_session("session_export_path")

    with pytest.raises(RuntimeException) as exc_info:
        runtime.export_session(
            "session_export_path",
            tmp_path.parent / "outside.md",
        )
    assert exc_info.value.code == RuntimeErrorCode.EXPORT_FAILED.value
