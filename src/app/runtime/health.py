"""Side-effect-light health checks for the Runtime application boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .serialization import safe_serialize


HEALTH_STATUSES = frozenset({"healthy", "degraded", "unavailable"})


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00",
        "Z",
    )


def _read(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _safe_metadata(value: Mapping[str, Any] | None) -> dict[str, Any]:
    serialized = safe_serialize(
        dict(value or {}),
        debug=False,
        max_depth=5,
        max_items=50,
        max_text_chars=240,
    )
    return serialized if isinstance(serialized, dict) else {}


@dataclass(frozen=True)
class HealthCheck:
    """Public, sanitized status for one Runtime dependency."""

    name: str
    status: str
    message: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in HEALTH_STATUSES:
            raise ValueError(f"Unsupported health status: {self.status}")
        object.__setattr__(self, "metadata", _safe_metadata(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "message": self.message,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class RuntimeHealthReport:
    """Aggregated Runtime health result suitable for CLI/API adapters."""

    status: str
    checks: dict[str, HealthCheck]
    checked_at: str = field(default_factory=_utc_now_iso)

    def __post_init__(self) -> None:
        if self.status not in HEALTH_STATUSES:
            raise ValueError(f"Unsupported health status: {self.status}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "healthy": self.status == "healthy",
            "checked_at": self.checked_at,
            "checks": {
                name: check.to_dict() for name, check in self.checks.items()
            },
            "metadata": {
                "check_count": len(self.checks),
            },
        }


class HealthChecker:
    """Check Runtime dependencies without executing tools or model requests."""

    CHECK_NAMES = (
        "runtime_initialized",
        "memory",
        "database",
        "models",
        "tools",
        "react_agent",
        "workspace",
    )

    def check(self, runtime: Any) -> RuntimeHealthReport:
        if bool(getattr(runtime, "closed", False)):
            checks = {
                name: HealthCheck(
                    name=name,
                    status="unavailable",
                    message="Runtime is closed.",
                    metadata={"runtime_closed": True},
                )
                for name in self.CHECK_NAMES
            }
            return RuntimeHealthReport(status="unavailable", checks=checks)

        checks: dict[str, HealthCheck] = {}
        checks["runtime_initialized"] = self._runtime_initialized(runtime)
        memory_check, memory_status = self._memory(runtime)
        checks["memory"] = memory_check
        checks["database"] = self._database(memory_status)
        checks["models"] = self._models(runtime)
        checks["tools"] = self._tools(runtime)
        checks["react_agent"] = self._react_agent(runtime)
        checks["workspace"] = self._workspace(runtime)
        return RuntimeHealthReport(
            status=self._aggregate(checks.values()),
            checks=checks,
        )

    def failure(self, runtime: Any, error: Any) -> RuntimeHealthReport:
        """Return a safe report when a custom health checker fails."""

        message = "Runtime health check failed."
        error_type = error.__class__.__name__ if error is not None else "HealthError"
        checks = {
            name: HealthCheck(
                name=name,
                status="unavailable",
                message=message,
                metadata={"error_type": error_type},
            )
            for name in self.CHECK_NAMES
        }
        return RuntimeHealthReport(status="unavailable", checks=checks)

    def _runtime_initialized(self, runtime: Any) -> HealthCheck:
        try:
            dependencies = runtime.dependencies
            required = (
                "model_manager",
                "tool_manager",
                "memory_adapter",
                "react_agent",
                "react_executor",
            )
            missing = [name for name in required if dependencies.get(name) is None]
        except Exception as exc:
            return HealthCheck(
                name="runtime_initialized",
                status="unavailable",
                message="Runtime dependencies could not be inspected.",
                metadata={"error_type": exc.__class__.__name__},
            )
        if missing:
            return HealthCheck(
                name="runtime_initialized",
                status="unavailable",
                message="Runtime is missing required dependencies.",
                metadata={"missing_dependencies": missing},
            )
        close_errors = len(getattr(runtime, "close_errors", []) or [])
        if close_errors:
            return HealthCheck(
                name="runtime_initialized",
                status="degraded",
                message="Runtime is initialized with resource close warnings.",
                metadata={"close_error_count": close_errors},
            )
        recovery_count = getattr(runtime, "recovery_count", None)
        metadata = {"formal_runtime_mode": True}
        if isinstance(recovery_count, int) and not isinstance(recovery_count, bool):
            metadata["recovered_interrupted_run_count"] = recovery_count
        return HealthCheck(
            name="runtime_initialized",
            status="healthy",
            message="Runtime dependencies are initialized.",
            metadata=metadata,
        )

    def _memory(self, runtime: Any) -> tuple[HealthCheck, Any | None]:
        adapter = getattr(runtime, "memory_adapter", None)
        method = getattr(adapter, "health", None)
        if not callable(method):
            return (
                HealthCheck(
                    name="memory",
                    status="unavailable",
                    message="Memory health interface is unavailable.",
                    metadata={"interface": "health"},
                ),
                None,
            )
        try:
            health = method()
        except Exception as exc:
            return (
                HealthCheck(
                    name="memory",
                    status="degraded",
                    message="Memory persistence is temporarily unavailable.",
                    metadata={
                        "health_call_failed": True,
                        "error_type": exc.__class__.__name__,
                    },
                ),
                None,
            )

        ok = bool(_read(health, "ok", False))
        metadata = {
            "schema_version": _read(health, "schema_version"),
            "session_count": _read(health, "session_count", 0),
            "database_configured": bool(_read(health, "database_path", None)),
        }
        error_code = _enum_value(_read(health, "error_code"))
        if error_code:
            metadata["error_code"] = error_code
        if ok:
            return (
                HealthCheck(
                    name="memory",
                    status="healthy",
                    message="Memory persistence is available.",
                    metadata=metadata,
                ),
                health,
            )
        return (
            HealthCheck(
                name="memory",
                status="degraded",
                message="Memory persistence is unavailable; temporary results may be used.",
                metadata=metadata,
            ),
            health,
        )

    def _database(self, memory_health: Any | None) -> HealthCheck:
        if memory_health is None:
            return HealthCheck(
                name="database",
                status="degraded",
                message="Database status could not be verified through Memory.",
                metadata={"verification": "memory_health"},
            )
        if bool(_read(memory_health, "ok", False)):
            return HealthCheck(
                name="database",
                status="healthy",
                message="SQLite session storage is available.",
                metadata={
                    "schema_version": _read(memory_health, "schema_version"),
                },
            )
        return HealthCheck(
            name="database",
            status="degraded",
            message="SQLite session storage is unavailable.",
            metadata={
                "error_code": _enum_value(_read(memory_health, "error_code")),
            },
        )

    def _models(self, runtime: Any) -> HealthCheck:
        manager = getattr(runtime, "model_manager", None)
        method = getattr(manager, "health_check", None)
        if not callable(method):
            return HealthCheck(
                name="models",
                status="unavailable",
                message="Models health interface is unavailable.",
                metadata={"interface": "health_check"},
            )
        try:
            health = method()
        except Exception as exc:
            return HealthCheck(
                name="models",
                status="unavailable",
                message="Models health check failed.",
                metadata={"error_type": exc.__class__.__name__},
            )
        healthy = bool(_read(health, "healthy", False))
        metadata = {
            key: _enum_value(_read(health, key))
            for key in (
                "provider",
                "protocol",
                "model",
                "configured",
                "check_type",
                "latency_ms",
                "code",
            )
            if _read(health, key, None) is not None
        }
        if healthy:
            return HealthCheck(
                name="models",
                status="healthy",
                message="Models configuration is available.",
                metadata=metadata,
            )
        return HealthCheck(
            name="models",
            status="unavailable",
            message="Models are not available for Runtime execution.",
            metadata=metadata,
        )

    def _tools(self, runtime: Any) -> HealthCheck:
        manager = getattr(runtime, "tool_manager", None)
        tool_runtime = getattr(manager, "runtime", None)
        registry = getattr(manager, "registry", None)
        list_names = getattr(registry, "tool_names", None)
        try:
            tool_count = len(list_names()) if callable(list_names) else 0
        except Exception as exc:
            return HealthCheck(
                name="tools",
                status="unavailable",
                message="Tools registry could not be inspected.",
                metadata={"error_type": exc.__class__.__name__},
            )
        if tool_runtime is None or registry is None:
            return HealthCheck(
                name="tools",
                status="unavailable",
                message="Tools runtime is not initialized.",
                metadata={"tool_count": tool_count},
            )
        enabled = bool(getattr(tool_runtime, "enabled", True))
        config_error = getattr(manager, "config_error", None)
        metadata = {
            "tool_count": tool_count,
            "enabled": enabled,
            "config_error_type": (
                config_error.__class__.__name__ if config_error is not None else None
            ),
            "side_effect_free": True,
        }
        if not enabled:
            return HealthCheck(
                name="tools",
                status="degraded",
                message="Tools runtime is disabled by configuration.",
                metadata=metadata,
            )
        if config_error is not None:
            return HealthCheck(
                name="tools",
                status="degraded",
                message="Tools loaded with a configuration warning.",
                metadata=metadata,
            )
        return HealthCheck(
            name="tools",
            status="healthy",
            message="Tools registry and runtime are available.",
            metadata=metadata,
        )

    def _react_agent(self, runtime: Any) -> HealthCheck:
        agent = getattr(runtime, "react_agent", None)
        executor = getattr(runtime, "react_executor", None)
        manage_memory = getattr(agent, "manage_memory", None)
        runnable = callable(getattr(agent, "run_with_result", None))
        if agent is None or executor is None or not runnable or manage_memory is not False:
            return HealthCheck(
                name="react_agent",
                status="unavailable",
                message="ReactAgent is not ready for formal Runtime mode.",
                metadata={
                    "executor_available": executor is not None,
                    "run_with_result_available": runnable,
                    "manage_memory": manage_memory,
                },
            )
        return HealthCheck(
            name="react_agent",
            status="healthy",
            message="ReactAgent is ready for formal Runtime mode.",
            metadata={
                "executor_available": True,
                "manage_memory": False,
            },
        )

    def _workspace(self, runtime: Any) -> HealthCheck:
        try:
            root = Path(runtime.workspace_root)
            exists = root.exists()
            is_dir = root.is_dir()
        except Exception as exc:
            return HealthCheck(
                name="workspace",
                status="unavailable",
                message="Workspace could not be inspected.",
                metadata={"error_type": exc.__class__.__name__},
            )
        if not exists or not is_dir:
            return HealthCheck(
                name="workspace",
                status="unavailable",
                message="Workspace directory is unavailable.",
                metadata={"exists": exists, "is_dir": is_dir},
            )
        return HealthCheck(
            name="workspace",
            status="healthy",
            message="Workspace directory is available.",
            metadata={"exists": True, "is_dir": True},
        )

    @staticmethod
    def _aggregate(checks: Any) -> str:
        statuses = {check.status for check in checks}
        if "unavailable" in statuses:
            return "unavailable"
        if "degraded" in statuses:
            return "degraded"
        return "healthy"


__all__ = [
    "HEALTH_STATUSES",
    "HealthCheck",
    "HealthChecker",
    "RuntimeHealthReport",
]
