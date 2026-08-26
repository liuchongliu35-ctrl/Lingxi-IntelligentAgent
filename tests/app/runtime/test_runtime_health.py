from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from src.agent.output_feedback import OutputFeedbackProcessor
from src.app.runtime import Runtime


class _Memory:
    def __init__(self, *, ok: bool = True) -> None:
        self.status = SimpleNamespace(
            ok=ok,
            database_path="/tmp/should-not-be-public/memory.db",
            schema_version=1,
            session_count=2,
            error_code="persistence_error" if not ok else None,
            error_message="token=should-not-leak" if not ok else None,
        )
        self.health_calls = 0

    def health(self) -> SimpleNamespace:
        self.health_calls += 1
        return self.status


class _Models:
    def __init__(self, *, healthy: bool = True, raises: bool = False) -> None:
        self.healthy = healthy
        self.raises = raises
        self.health_calls = 0

    def health_check(self) -> SimpleNamespace:
        self.health_calls += 1
        if self.raises:
            raise RuntimeError("api_key=should-not-leak")
        return SimpleNamespace(
            healthy=self.healthy,
            provider="mock",
            protocol="mock",
            model="mock-v1",
            configured=self.healthy,
            check_type="config_check",
            code=None if self.healthy else "missing_api_key",
        )


class _Tools:
    def __init__(self) -> None:
        self.runtime = SimpleNamespace(enabled=True)
        self.registry = SimpleNamespace(tool_names=lambda: ["read_file"])
        self.config_error = None
        self.execute_calls = 0

    def execute(self, *_args, **_kwargs):
        self.execute_calls += 1


class _Agent:
    manage_memory = False

    def run_with_result(self, *_args, **_kwargs):
        raise AssertionError("health must not execute ReactAgent")


def _runtime(
    tmp_path: Path,
    *,
    memory: _Memory | None = None,
    models: _Models | None = None,
    tools: _Tools | None = None,
) -> Runtime:
    dependency = object()
    memory = memory or _Memory()
    models = models or _Models()
    tools = tools or _Tools()
    agent = _Agent()
    return Runtime(
        config=SimpleNamespace(workspace_root=tmp_path),
        model_manager=models,
        tool_manager=tools,
        tool_registry=tools.registry,
        session_manager=dependency,
        context_builder=dependency,
        memory_adapter=memory,
        analyzer=dependency,
        planner=dependency,
        react_executor=dependency,
        react_agent=agent,
        output_feedback_processor=OutputFeedbackProcessor(),
        pending_run_registry=dependency,
        recover_on_startup=False,
    )


def test_runtime_health_reports_all_core_dependencies_healthy(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)

    report = runtime.health()

    assert report["status"] == "healthy"
    assert report["healthy"] is True
    assert set(report["checks"]) == {
        "runtime_initialized",
        "memory",
        "database",
        "models",
        "tools",
        "react_agent",
        "workspace",
    }
    assert all(
        item["status"] == "healthy" for item in report["checks"].values()
    )


def test_memory_unavailable_is_degraded_and_does_not_execute_tools(
    tmp_path: Path,
) -> None:
    tools = _Tools()
    memory = _Memory(ok=False)
    runtime = _runtime(tmp_path, memory=memory, tools=tools)

    report = runtime.health()

    assert report["status"] == "degraded"
    assert report["checks"]["memory"]["status"] == "degraded"
    assert report["checks"]["database"]["status"] == "degraded"
    assert tools.execute_calls == 0
    assert memory.health_calls == 1


def test_model_health_exception_is_safely_wrapped(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, models=_Models(raises=True))

    report = runtime.health()

    model_check = report["checks"]["models"]
    assert report["status"] == "unavailable"
    assert model_check["status"] == "unavailable"
    assert model_check["metadata"]["error_type"] == "RuntimeError"
    assert "should-not-leak" not in str(report)


def test_health_does_not_leak_sensitive_configuration(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, memory=_Memory(ok=False))

    report = runtime.health()

    text = str(report)
    assert "should-not-leak" not in text
    assert "api_key" not in text
    assert "token=" not in text


def test_closed_runtime_reports_unavailable_without_calling_dependencies(
    tmp_path: Path,
) -> None:
    memory = _Memory()
    models = _Models()
    runtime = _runtime(tmp_path, memory=memory, models=models)
    runtime.close()

    report = runtime.health()

    assert report["status"] == "unavailable"
    assert all(
        item["status"] == "unavailable" for item in report["checks"].values()
    )
    assert memory.health_calls == 0
    assert models.health_calls == 0
