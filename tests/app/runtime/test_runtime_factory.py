from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.app.runtime import (
    RuntimeConfig,
    RuntimeException,
    RuntimeFactory,
    build_for_test,
)


class FakeClosable:
    def __init__(self) -> None:
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


class FakeSessionManager(FakeClosable):
    def __init__(self, recovery_count: int = 3) -> None:
        super().__init__()
        self.recovery_count = recovery_count
        self.recover_calls = 0

    def recover_interrupted_runs(self) -> int:
        self.recover_calls += 1
        return self.recovery_count


class FakeMemoryAdapter(FakeClosable):
    def __init__(self, session_manager: FakeSessionManager) -> None:
        super().__init__()
        self.session_manager = session_manager
        self.context_builder = FakeClosable()


class FakeToolManager(FakeClosable):
    def __init__(self) -> None:
        super().__init__()
        self.registry = object()


class FakeAgent(FakeClosable):
    def __init__(
        self,
        model_manager: object,
        tool_manager: FakeToolManager,
        executor: FakeClosable,
    ) -> None:
        super().__init__()
        self.model_manager = model_manager
        self.tool_manager = tool_manager
        self.complexity_analyzer = object()
        self.planner = object()
        self.executor = executor
        self.output_feedback_processor = object()
        self.manage_memory = False


def _injected_runtime(
    tmp_path: Path,
    *,
    session_manager: FakeSessionManager | None = None,
    model_manager: object | None = None,
    tool_manager: FakeToolManager | None = None,
    memory_adapter: FakeMemoryAdapter | None = None,
    react_agent: FakeAgent | None = None,
) -> tuple[object, FakeSessionManager, FakeToolManager, FakeAgent]:
    session_manager = session_manager or FakeSessionManager()
    model_manager = model_manager or object()
    tool_manager = tool_manager or FakeToolManager()
    executor = FakeClosable()
    memory_adapter = memory_adapter or FakeMemoryAdapter(session_manager)
    react_agent = react_agent or FakeAgent(model_manager, tool_manager, executor)

    runtime = build_for_test(
        RuntimeConfig(
            workspace_root=tmp_path,
            model_name="mock",
        ),
        session_manager=session_manager,
        model_manager=model_manager,
        tool_manager=tool_manager,
        memory_adapter=memory_adapter,
        react_agent=react_agent,
        context_builder=memory_adapter.context_builder,
        analyzer=react_agent.complexity_analyzer,
        planner=react_agent.planner,
        react_executor=react_agent.executor,
        tool_registry=tool_manager.registry,
        recover_on_startup=True,
    )
    return runtime, session_manager, tool_manager, react_agent


def test_production_factory_builds_real_dependency_graph(tmp_path: Path) -> None:
    runtime = RuntimeFactory.build_production(
        RuntimeConfig(workspace_root=tmp_path, model_name="mock")
    )

    try:
        assert runtime.workspace_root == tmp_path.resolve()
        assert runtime.model_manager.model_name == "mock"
        assert runtime.tool_manager.get_registry() is runtime.tool_registry
        assert runtime.memory_adapter.session_manager is runtime.session_manager
        assert runtime.react_agent.manage_memory is False
        assert runtime.react_agent.executor is runtime.react_executor
        assert runtime.pending_run_registry is not None
        assert runtime.recovery_count == 0
    finally:
        runtime.close()


def test_injected_dependencies_are_reused_without_second_session_manager(
    tmp_path: Path,
) -> None:
    session_manager = FakeSessionManager(recovery_count=7)
    memory_adapter = FakeMemoryAdapter(session_manager)
    runtime, used_session_manager, used_tool_manager, used_agent = _injected_runtime(
        tmp_path,
        session_manager=session_manager,
        memory_adapter=memory_adapter,
    )

    assert runtime.session_manager is used_session_manager
    assert runtime.memory_adapter is memory_adapter
    assert runtime.tool_manager is used_tool_manager
    assert runtime.react_agent is used_agent
    assert session_manager.recover_calls == 1
    assert runtime.recovery_count == 7


def test_partial_memory_adapter_injection_reuses_its_session_manager(
    tmp_path: Path,
) -> None:
    session_manager = FakeSessionManager()
    memory_adapter = FakeMemoryAdapter(session_manager)
    runtime = build_for_test(
        RuntimeConfig(workspace_root=tmp_path),
        memory_adapter=memory_adapter,
        model_manager=object(),
        tool_manager=FakeToolManager(),
        react_agent=FakeAgent(object(), FakeToolManager(), FakeClosable()),
        tool_registry=object(),
        context_builder=memory_adapter.context_builder,
    )

    assert runtime.session_manager is session_manager
    assert session_manager.recover_calls == 1
    runtime.close()


def test_close_is_idempotent_and_closes_releasable_injected_dependencies(
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


def test_close_failure_is_recorded_without_second_close_attempt(tmp_path: Path) -> None:
    class BrokenClosable:
        def __init__(self) -> None:
            self.calls = 0

        def close(self) -> None:
            self.calls += 1
            raise RuntimeError("secret=should-not-be-public")

    broken = BrokenClosable()
    runtime, _, _, _ = _injected_runtime(tmp_path)
    runtime.model_manager = broken

    runtime.close()
    runtime.close()

    assert broken.calls == 1
    assert len(runtime.close_errors) == 1
    assert runtime.close_errors[0].code == "internal_error"
    assert "should-not-be-public" not in runtime.close_errors[0].message


def test_factory_maps_construction_failure_to_dependency_init_failed(
    tmp_path: Path,
) -> None:
    with pytest.raises(RuntimeException) as error:
        RuntimeFactory.build_production(
            {
                "workspace_root": tmp_path,
                "pending_run_ttl_seconds": 0,
            }
        )

    assert error.value.code == "dependency_init_failed"
    assert error.value.metadata["stage"] == "factory"


def test_runtime_rejects_agent_that_manages_memory(tmp_path: Path) -> None:
    agent = SimpleNamespace(
        manage_memory=True,
        model_manager=object(),
        tool_manager=FakeToolManager(),
        complexity_analyzer=object(),
        planner=object(),
        executor=FakeClosable(),
        output_feedback_processor=object(),
    )

    with pytest.raises(RuntimeException) as error:
        build_for_test(
            RuntimeConfig(workspace_root=tmp_path),
            session_manager=FakeSessionManager(),
            model_manager=object(),
            tool_manager=agent.tool_manager,
            memory_adapter=FakeMemoryAdapter(FakeSessionManager()),
            react_agent=agent,
            tool_registry=agent.tool_manager.registry,
            context_builder=FakeClosable(),
            analyzer=agent.complexity_analyzer,
            planner=agent.planner,
            react_executor=agent.executor,
        )

    assert error.value.code == "dependency_init_failed"
