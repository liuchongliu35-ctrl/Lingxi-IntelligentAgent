from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.agent.output_feedback import OutputFeedbackProcessor
from src.app.runtime import (
    Runtime,
    RuntimeConfig,
    RuntimeException,
    RuntimeFactory,
    RuntimeErrorCode,
    build_for_test,
)
from src.memory.config import MemoryConfig
from src.memory.ids import new_message_id, new_run_id, new_session_id
from src.memory.runtime_adapter import RuntimeMemoryAdapter
from src.memory.session_manager import SessionManager
from src.memory.storage import SQLiteSessionRepository


class _Closable:
    def __init__(self) -> None:
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


class _SessionManager(_Closable):
    def __init__(self, recovery_count: int = 0) -> None:
        super().__init__()
        self.recovery_count = recovery_count
        self.recover_calls = 0

    def recover_interrupted_runs(self) -> int:
        self.recover_calls += 1
        return self.recovery_count


class _MemoryAdapter(_Closable):
    def __init__(self, session_manager: Any) -> None:
        super().__init__()
        self.session_manager = session_manager
        self.context_builder = _Closable()


class _ToolManager(_Closable):
    def __init__(self) -> None:
        super().__init__()
        self.registry = object()


class _Agent(_Closable):
    def __init__(self, model_manager: Any, tool_manager: Any) -> None:
        super().__init__()
        self.model_manager = model_manager
        self.tool_manager = tool_manager
        self.complexity_analyzer = object()
        self.planner = object()
        self.executor = _Closable()
        self.output_feedback_processor = OutputFeedbackProcessor()
        self.manage_memory = False


def _injected_runtime(
    tmp_path: Path,
    *,
    session_manager: Any | None = None,
    recover_on_startup: bool = True,
) -> tuple[Runtime, Any, Any, Any]:
    session_manager = session_manager or _SessionManager(recovery_count=4)
    model_manager = _Closable()
    tool_manager = _ToolManager()
    memory_adapter = _MemoryAdapter(session_manager)
    agent = _Agent(model_manager, tool_manager)
    runtime = build_for_test(
        RuntimeConfig(
            workspace_root=tmp_path,
            model_name="mock",
            recover_on_startup=recover_on_startup,
        ),
        session_manager=session_manager,
        model_manager=model_manager,
        tool_manager=tool_manager,
        memory_adapter=memory_adapter,
        context_builder=memory_adapter.context_builder,
        analyzer=agent.complexity_analyzer,
        planner=agent.planner,
        react_executor=agent.executor,
        react_agent=agent,
        output_feedback_processor=agent.output_feedback_processor,
        tool_registry=tool_manager.registry,
    )
    return runtime, session_manager, tool_manager, agent


def test_runtime_calls_startup_recovery_and_records_count(tmp_path: Path) -> None:
    runtime, session_manager, _, _ = _injected_runtime(tmp_path)

    assert session_manager.recover_calls == 1
    assert runtime.recovery_count == 4
    runtime_metadata = runtime.health()["checks"]["runtime_initialized"]["metadata"]
    assert runtime_metadata["formal_runtime_mode"] is True
    assert runtime_metadata["recovered_interrupted_run_count"] == 4


def test_runtime_startup_recovery_failure_is_dependency_init_failed(
    tmp_path: Path,
) -> None:
    class BrokenSessionManager(_SessionManager):
        def recover_interrupted_runs(self) -> int:
            raise RuntimeException(
                RuntimeErrorCode.MEMORY_UNAVAILABLE,
                "database unavailable",
            )

    with pytest.raises(RuntimeException) as error:
        _injected_runtime(
            tmp_path,
            session_manager=BrokenSessionManager(),
        )

    assert error.value.code == RuntimeErrorCode.DEPENDENCY_INIT_FAILED.value
    assert error.value.metadata["stage"] == "startup_recovery"


def test_startup_recovery_marks_pending_running_and_waiting_user_as_interrupted(
    tmp_path: Path,
) -> None:
    config = MemoryConfig(
        database_path=tmp_path / "memory.db",
        log_path=tmp_path / "memory.log",
    )
    repo = SQLiteSessionRepository(config)
    session_id = new_session_id()
    repo.create_session(session_id)
    run_ids: dict[str, str] = {}
    for status in ("pending", "running", "waiting_user", "completed"):
        run_id = new_run_id()
        run_ids[status] = run_id
        repo.insert_run(
            {
                "run_id": run_id,
                "session_id": session_id,
                "user_message_id": new_message_id(),
                "status": status,
                "started_at": "2026-08-26T00:00:00Z",
            }
        )

    manager = SessionManager(repo=repo)
    runtime = RuntimeFactory.build_for_test(
        RuntimeConfig(
            workspace_root=tmp_path,
            memory_config=config,
            model_name="mock",
        ),
        session_manager=manager,
    )

    try:
        assert runtime.recovery_count == 3
        assert repo.load_run(run_ids["pending"]).status == "interrupted"
        assert repo.load_run(run_ids["running"]).status == "interrupted"
        assert repo.load_run(run_ids["waiting_user"]).status == "interrupted"
        assert repo.load_run(run_ids["completed"]).status == "completed"
    finally:
        runtime.close()


def test_close_is_idempotent_and_does_not_delete_memory_history(
    tmp_path: Path,
) -> None:
    runtime, session_manager, tool_manager, agent = _injected_runtime(tmp_path)

    runtime.close()
    runtime.close()

    assert runtime.closed is True
    assert session_manager.close_calls == 1
    assert tool_manager.close_calls == 1
    assert agent.close_calls == 1
    assert runtime.close_errors == []


def test_close_keeps_sqlite_history_available_to_a_new_runtime(
    tmp_path: Path,
) -> None:
    config = MemoryConfig(
        database_path=tmp_path / "memory.db",
        log_path=tmp_path / "memory.log",
    )
    manager = SessionManager(config=config)
    session = manager.create_session(new_session_id())
    runtime, _, _, _ = _injected_runtime(
        tmp_path,
        session_manager=manager,
        recover_on_startup=False,
    )

    runtime.close()

    reloaded = SessionManager(config=config)
    assert reloaded.load_session(session.session_id).session_id == session.session_id


def test_factory_maps_non_dependency_runtime_exception_to_dependency_init_failed(
    tmp_path: Path,
) -> None:
    class BrokenSessionManager(_SessionManager):
        def recover_interrupted_runs(self) -> int:
            raise RuntimeException(RuntimeErrorCode.INTERNAL_ERROR, "internal")

    model_manager = _Closable()
    tool_manager = _ToolManager()
    memory_adapter = _MemoryAdapter(BrokenSessionManager())
    agent = _Agent(model_manager, tool_manager)
    with pytest.raises(RuntimeException) as error:
        RuntimeFactory.build_for_test(
            RuntimeConfig(workspace_root=tmp_path, model_name="mock"),
            session_manager=memory_adapter.session_manager,
            model_manager=model_manager,
            tool_manager=tool_manager,
            memory_adapter=memory_adapter,
            context_builder=memory_adapter.context_builder,
            analyzer=agent.complexity_analyzer,
            planner=agent.planner,
            react_executor=agent.executor,
            react_agent=agent,
            output_feedback_processor=agent.output_feedback_processor,
            tool_registry=tool_manager.registry,
        )

    assert error.value.code == RuntimeErrorCode.DEPENDENCY_INIT_FAILED.value
    assert error.value.metadata["stage"] == "startup_recovery"


def test_factory_configuration_failure_is_dependency_init_failed(
    tmp_path: Path,
) -> None:
    with pytest.raises(RuntimeException) as error:
        RuntimeFactory.build_production(
            {
                "workspace_root": tmp_path,
                "pending_run_ttl_seconds": 0,
            }
        )

    assert error.value.code == RuntimeErrorCode.DEPENDENCY_INIT_FAILED.value
    assert error.value.metadata["stage"] == "factory"
