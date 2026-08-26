"""Production and test dependency assembly for the Runtime layer."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from src.agent.analyzer.complexity_analyzer import ComplexityAnalyzer
from src.agent.planner import Planner
from src.agent.react_executor import ReActExecutor
from src.agent.react_executor_config import (
    ReActExecutorConfig,
    load_react_executor_config,
)
from src.agent.orchestrator.react_agent import ReactAgent
from src.core.config import get_settings
from src.memory.config import MemoryConfig
from src.memory.context_builder import ContextBuilder
from src.memory.runtime_adapter import RuntimeMemoryAdapter
from src.memory.session_manager import SessionManager
from src.models.config import ModelsConfig, get_models_config
from src.models.model_manager import ModelManager
from src.tools.config import ToolsConfig, load_tools_config
from src.tools.tool_manager import ToolManager

from .core import Runtime
from .errors import RuntimeErrorCode, RuntimeException
from .health import HealthChecker
from .pending_runs import (
    DEFAULT_PENDING_RUN_TTL_SECONDS,
    PendingRunRegistry,
)


@dataclass(frozen=True)
class RuntimeConfig:
    """Small application-level configuration used by RuntimeFactory."""

    workspace_root: Path
    model_name: str = "mock"
    memory_config: MemoryConfig | None = None
    models_config: ModelsConfig | None = None
    tools_config: ToolsConfig | None = None
    react_executor_config: ReActExecutorConfig | None = None
    pending_run_ttl_seconds: float = DEFAULT_PENDING_RUN_TTL_SECONDS
    recover_on_startup: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "workspace_root",
            Path(self.workspace_root).expanduser().resolve(),
        )
        model_name = str(self.model_name or "mock").strip().lower()
        if not model_name:
            model_name = "mock"
        object.__setattr__(self, "model_name", model_name)
        if self.pending_run_ttl_seconds <= 0:
            raise ValueError("pending_run_ttl_seconds must be greater than zero")

    @classmethod
    def from_value(
        cls,
        value: "RuntimeConfig | Mapping[str, Any] | Any | None" = None,
        *,
        workspace_root: str | Path | None = None,
        model_name: str | None = None,
    ) -> "RuntimeConfig":
        if isinstance(value, cls):
            source: dict[str, Any] = {
                "workspace_root": value.workspace_root,
                "model_name": value.model_name,
                "memory_config": value.memory_config,
                "models_config": value.models_config,
                "tools_config": value.tools_config,
                "react_executor_config": value.react_executor_config,
                "pending_run_ttl_seconds": value.pending_run_ttl_seconds,
                "recover_on_startup": value.recover_on_startup,
            }
        elif isinstance(value, Mapping):
            source = dict(value)
        elif value is None:
            source = {}
        else:
            source = {
                name: getattr(value, name)
                for name in (
                    "workspace_root",
                    "model_name",
                    "memory_config",
                    "models_config",
                    "tools_config",
                    "react_executor_config",
                    "pending_run_ttl_seconds",
                    "recover_on_startup",
                )
                if hasattr(value, name)
            }

        settings = get_settings()
        source["workspace_root"] = (
            workspace_root
            or source.get("workspace_root")
            or settings.workspace_root
        )
        if model_name is not None:
            source["model_name"] = model_name
        source.setdefault("model_name", settings.model_name or "mock")
        return cls(**source)


class RuntimeFactory:
    """Single dependency assembly point shared by CLI, API, and tests."""

    @classmethod
    def build_production(
        cls,
        config: RuntimeConfig | Mapping[str, Any] | Any | None = None,
        **overrides: Any,
    ) -> Runtime:
        return cls._build(config, overrides)

    @classmethod
    def build_for_test(
        cls,
        config: RuntimeConfig | Mapping[str, Any] | Any | None = None,
        **overrides: Any,
    ) -> Runtime:
        return cls._build(config, overrides)

    @classmethod
    def build(
        cls,
        config: RuntimeConfig | Mapping[str, Any] | Any | None = None,
        **overrides: Any,
    ) -> Runtime:
        """Compatibility alias for the production assembly path."""

        return cls.build_production(config, **overrides)

    @classmethod
    def _build(
        cls,
        config_value: RuntimeConfig | Mapping[str, Any] | Any | None,
        overrides: Mapping[str, Any],
    ) -> Runtime:
        try:
            config = RuntimeConfig.from_value(
                config_value,
                workspace_root=overrides.get("workspace_root"),
                model_name=overrides.get("model_name"),
            )
            return cls._build_runtime(config, overrides)
        except RuntimeException:
            raise
        except Exception as exc:
            raise RuntimeException(
                RuntimeErrorCode.DEPENDENCY_INIT_FAILED,
                "Runtime dependency initialization failed.",
                metadata={"stage": "factory"},
                cause=exc,
            ) from exc

    @classmethod
    def _build_runtime(
        cls,
        config: RuntimeConfig,
        overrides: Mapping[str, Any],
    ) -> Runtime:
        injected_agent = overrides.get("react_agent")
        model_manager = overrides.get("model_manager") or _attr(
            injected_agent,
            "model_manager",
        )
        if model_manager is None:
            models_config = config.models_config or get_models_config(
                config.workspace_root
            )
            model_manager = ModelManager(
                model_name=config.model_name,
                models_config=models_config,
            )

        tool_manager = overrides.get("tool_manager") or _attr(
            injected_agent,
            "tool_manager",
        )
        if tool_manager is None:
            tools_config = config.tools_config or load_tools_config(
                config.workspace_root
            )
            tool_manager = ToolManager(
                tools_config=tools_config,
                workspace_root=config.workspace_root,
                model_manager=model_manager,
            )

        session_manager = overrides.get("session_manager")
        memory_adapter = overrides.get("memory_adapter")
        if session_manager is None:
            session_manager = _attr(memory_adapter, "session_manager")
        if session_manager is None:
            memory_config = config.memory_config or MemoryConfig.default(
                config.workspace_root
            )
            session_manager = SessionManager(
                config=memory_config,
                model_manager=model_manager,
            )

        context_builder = overrides.get("context_builder")
        if context_builder is None:
            context_builder = _attr(memory_adapter, "context_builder")
        if context_builder is None:
            context_builder = ContextBuilder(session_manager=session_manager)

        if memory_adapter is None:
            memory_adapter = RuntimeMemoryAdapter(
                session_manager=session_manager,
                context_builder=context_builder,
            )

        tool_registry = overrides.get("tool_registry")
        if tool_registry is None:
            tool_registry = _attr(tool_manager, "registry")
        if tool_registry is None:
            get_registry = getattr(tool_manager, "get_registry", None)
            if callable(get_registry):
                tool_registry = get_registry()
        if tool_registry is None:
            raise RuntimeException(
                RuntimeErrorCode.DEPENDENCY_INIT_FAILED,
                "ToolManager does not provide a ToolRegistry.",
                metadata={"dependency": "tool_registry"},
            )

        analyzer = overrides.get("analyzer") or _attr(
            injected_agent,
            "complexity_analyzer",
        )
        if analyzer is None:
            analyzer = ComplexityAnalyzer(
                model_manager=model_manager,
                tool_manager=tool_manager,
            )

        planner = overrides.get("planner") or _attr(injected_agent, "planner")
        if planner is None:
            planner = Planner(model_manager=model_manager)

        react_executor = overrides.get("react_executor") or _attr(
            injected_agent,
            "executor",
        )
        if react_executor is None:
            react_executor_config = (
                config.react_executor_config
                or load_react_executor_config(config.workspace_root)
            )
            react_executor = ReActExecutor(
                model_manager=model_manager,
                tool_manager=tool_manager,
                tool_registry=tool_registry,
                config=react_executor_config,
            )

        react_agent = injected_agent
        if react_agent is None:
            react_agent = ReactAgent(
                model_manager=model_manager,
                short_term_memory=None,
                long_term_memory=None,
                tool_manager=tool_manager,
                rag_system=None,
                complexity_analyzer=analyzer,
                planner=planner,
                executor=react_executor,
                tool_registry=tool_registry,
                manage_memory=False,
            )
        elif getattr(react_agent, "manage_memory", False):
            raise RuntimeException(
                RuntimeErrorCode.DEPENDENCY_INIT_FAILED,
                "Runtime requires ReactAgent manage_memory=False.",
                metadata={"dependency": "react_agent"},
            )

        output_feedback_processor = (
            overrides.get("output_feedback_processor")
            or _attr(react_agent, "output_feedback_processor")
        )
        if output_feedback_processor is None:
            raise RuntimeException(
                RuntimeErrorCode.DEPENDENCY_INIT_FAILED,
                "ReactAgent does not provide OutputFeedbackProcessor.",
                metadata={"dependency": "output_feedback_processor"},
            )

        pending_run_registry = overrides.get("pending_run_registry")
        if pending_run_registry is None:
            pending_run_registry = PendingRunRegistry(
                ttl_seconds=config.pending_run_ttl_seconds
            )

        return Runtime(
            config=config,
            model_manager=model_manager,
            tool_manager=tool_manager,
            tool_registry=tool_registry,
            session_manager=session_manager,
            context_builder=context_builder,
            memory_adapter=memory_adapter,
            analyzer=analyzer,
            planner=planner,
            react_executor=react_executor,
            react_agent=react_agent,
            output_feedback_processor=output_feedback_processor,
            pending_run_registry=pending_run_registry,
            health_checker=overrides.get("health_checker") or HealthChecker(),
            recover_on_startup=config.recover_on_startup,
        )


def build_production(
    config: RuntimeConfig | Mapping[str, Any] | Any | None = None,
    **overrides: Any,
) -> Runtime:
    return RuntimeFactory.build_production(config, **overrides)


def build_for_test(
    config: RuntimeConfig | Mapping[str, Any] | Any | None = None,
    **overrides: Any,
) -> Runtime:
    return RuntimeFactory.build_for_test(config, **overrides)


def _attr(value: Any, name: str) -> Any | None:
    return getattr(value, name, None) if value is not None else None


__all__ = [
    "RuntimeConfig",
    "RuntimeFactory",
    "build_for_test",
    "build_production",
]
