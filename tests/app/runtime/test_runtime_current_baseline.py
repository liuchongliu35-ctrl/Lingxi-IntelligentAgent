from __future__ import annotations

import inspect

from src.agent.output_feedback import OutputFeedbackProcessor
from src.agent.orchestrator.react_agent import ReactAgent
from src.agent.react_executor.react_executor import ReActExecutor
from src.agent.react_executor.react_executor_protocol import (
    ExecutionEvent,
    ExecutionResult,
    PendingConfirmation,
)
from src.memory.runtime_adapter import RuntimeMemoryAdapter, RuntimeMemoryTurn
from src.memory.session_manager import SessionManager
from src.models.model_manager import ModelManager
from src.tools.tool_manager import ToolManager


def _parameter_names(callable_object: object) -> set[str]:
    return set(inspect.signature(callable_object).parameters)


def test_runtime_cross_layer_interfaces_are_importable() -> None:
    assert RuntimeMemoryAdapter is not None
    assert RuntimeMemoryTurn is not None
    assert SessionManager is not None
    assert ReactAgent is not None
    assert ReActExecutor is not None
    assert OutputFeedbackProcessor is not None
    assert ModelManager is not None
    assert ToolManager is not None


def test_runtime_memory_adapter_snapshot() -> None:
    assert {
        "session_id",
        "user_input",
        "user_metadata",
        "session_title",
        "session_metadata",
        "max_recent_messages",
        "agent_version",
        "model_profile",
    }.issubset(_parameter_names(RuntimeMemoryAdapter.begin_turn))
    assert {"turn", "assistant_content", "assistant_metadata", "maybe_summarize", "include_timeline"}.issubset(
        _parameter_names(RuntimeMemoryAdapter.complete_turn)
    )
    assert {"turn", "error", "maybe_summarize", "include_timeline"}.issubset(
        _parameter_names(RuntimeMemoryAdapter.fail_turn)
    )
    assert {"turn", "external_callback", "external_visible_only"}.issubset(
        _parameter_names(RuntimeMemoryAdapter.event_callback)
    )

    assert hasattr(RuntimeMemoryTurn, "react_agent_kwargs")
    assert _parameter_names(RuntimeMemoryTurn.react_agent_kwargs) == {"self"}


def test_runtime_memory_turn_react_agent_kwargs_contract() -> None:
    fields = RuntimeMemoryTurn.__dataclass_fields__
    assert {
        "session",
        "user_message",
        "run",
        "context",
        "short_term_memory",
        "persistence_available",
        "persistence_warning",
    }.issubset(fields)


def test_react_agent_runtime_mode_snapshot() -> None:
    constructor_parameters = _parameter_names(ReactAgent)
    assert {
        "model_manager",
        "short_term_memory",
        "long_term_memory",
        "tool_manager",
        "rag_system",
        "complexity_analyzer",
        "planner",
        "executor",
        "executor_type",
        "react_executor_config",
        "tool_registry",
        "manage_memory",
    }.issubset(constructor_parameters)

    run_parameters = _parameter_names(ReactAgent.run_with_result)
    assert {
        "user_input",
        "history",
        "context_text",
        "event_callback",
        "event_callback_visible_only",
        "manage_memory",
        "session_id",
        "run_id",
    }.issubset(run_parameters)

    stream_parameters = _parameter_names(ReactAgent.run_stream)
    assert {
        "user_input",
        "include_internal",
        "history",
        "context_text",
        "event_callback",
        "event_callback_visible_only",
        "manage_memory",
        "session_id",
        "run_id",
    }.issubset(stream_parameters)


def test_react_executor_result_event_and_confirmation_snapshot() -> None:
    assert {
        "execution_id",
        "plan_id",
        "status",
        "success",
        "output",
        "summary",
        "events",
        "requires_user_input",
        "user_input_request",
        "pending_confirmation",
        "request_replan",
        "replan_reason",
    }.issubset(ExecutionResult.__dataclass_fields__)
    assert {
        "execution_id",
        "plan_id",
        "type",
        "message",
        "event_id",
        "task_id",
        "step_id",
        "timestamp",
        "visible_to_user",
        "payload",
    }.issubset(ExecutionEvent.__dataclass_fields__)
    assert {
        "execution_id",
        "plan_id",
        "confirmation_type",
        "confirmation_message",
        "pending_action",
        "session_id",
        "confirmation_id",
        "preview_hash",
        "preview_summary",
        "affected_resources",
        "expires_at",
    }.issubset(PendingConfirmation.__dataclass_fields__)

    assert {"plan", "task", "user_input", "history", "event_callback", "event_callback_visible_only"}.issubset(
        _parameter_names(ReActExecutor.execute)
    )
    assert {
        "context",
        "approved",
        "reason",
        "confirmation_id",
        "preview_hash",
    }.issubset(_parameter_names(ReActExecutor.resume_after_confirmation))


def test_output_feedback_models_and_dependency_entrypoints_snapshot() -> None:
    assert _parameter_names(OutputFeedbackProcessor) == set()
    assert {"result", "include_internal", "group_related"}.issubset(
        _parameter_names(OutputFeedbackProcessor.build)
    )
    assert {"model_name", "models_config", "provider_conf_id", "credential_slug"}.issubset(
        _parameter_names(ModelManager)
    )
    assert {"tools", "registry", "policy", "tools_config", "workspace_root", "model_manager"}.issubset(
        _parameter_names(ToolManager)
    )
    assert {"repo", "config", "model_manager"}.issubset(_parameter_names(SessionManager))
