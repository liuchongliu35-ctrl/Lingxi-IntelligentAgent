from __future__ import annotations

import importlib

import pytest

from src.agent.react_executor.react_executor_protocol import ExecutionResult
from src.models.compat import ModelCallFailure
from src.models.protocol import ModelCallResult
from src.app.runtime.contracts import RuntimeStatus
from src.app.runtime.errors import (
    RUNTIME_ERROR_CODES,
    RuntimeErrorCode,
    RuntimeException,
    cli_exit_code_for_error,
    http_status_for_error,
    map_exception,
    normalize_runtime_error_code,
    runtime_result_from_exception,
    status_for_error_code,
)
from src.tools.base import ToolResult


def test_runtime_error_code_contains_the_design_v1_codes() -> None:
    expected = {
        "validation_error",
        "session_not_found",
        "run_not_found",
        "session_conflict",
        "memory_unavailable",
        "persistence_warning",
        "agent_execution_failed",
        "blocked_by_policy",
        "waiting_user",
        "request_replan",
        "cancelled",
        "interrupted",
        "dependency_init_failed",
        "export_failed",
        "api_error",
        "internal_error",
    }

    assert RUNTIME_ERROR_CODES == expected


@pytest.mark.parametrize(
    ("code", "status", "http_status", "exit_code"),
    [
        ("validation_error", "failed", 400, 1),
        ("session_not_found", "failed", 404, 1),
        ("run_not_found", "failed", 404, 1),
        ("session_conflict", "failed", 409, 1),
        ("blocked_by_policy", "blocked", 403, 2),
        ("waiting_user", "waiting_user", 202, 0),
        ("request_replan", "request_replan", 202, 0),
        ("cancelled", "cancelled", 409, 2),
        ("interrupted", "interrupted", 409, 2),
        ("memory_unavailable", "failed", 503, 3),
        ("dependency_init_failed", "failed", 503, 3),
        ("export_failed", "failed", 500, 3),
        ("internal_error", "failed", 500, 3),
    ],
)
def test_error_status_http_and_cli_mappings(
    code: str,
    status: str,
    http_status: int,
    exit_code: int,
) -> None:
    assert status_for_error_code(code) == status
    error = RuntimeException(code, "safe message")
    assert error.status == status
    assert http_status_for_error(error) == http_status
    assert cli_exit_code_for_error(error) == exit_code


def test_completed_and_success_statuses_have_success_mappings() -> None:
    assert http_status_for_error("completed") == 200
    assert http_status_for_error("success") == 200
    assert cli_exit_code_for_error("completed") == 0
    assert cli_exit_code_for_error("success") == 0
    assert http_status_for_error(status=RuntimeStatus.COMPLETED) == 200
    assert cli_exit_code_for_error(status=RuntimeStatus.COMPLETED) == 0


def test_runtime_exception_normalizes_code_and_sanitizes_metadata() -> None:
    error = RuntimeException(
        RuntimeErrorCode.BLOCKED_BY_POLICY,
        "authorization=secret-value",
        metadata={
            "trace_id": "trace_1",
            "api_key": "do-not-leak",
            "raw_prompt": "hidden",
        },
    )

    assert error.code == "blocked_by_policy"
    assert error.status == "blocked"
    assert "secret-value" not in error.message
    assert error.metadata == {"trace_id": "trace_1"}
    assert error.to_dict() == {
        "code": "blocked_by_policy",
        "status": "blocked",
        "message": "authorization=***REDACTED***",
        "metadata": {"trace_id": "trace_1"},
    }


def test_map_exception_classifies_validation_memory_and_unknown_errors() -> None:
    validation = map_exception(ValueError("input is invalid"))
    memory = map_exception(RuntimeError("database is unavailable"))
    unknown = map_exception(RuntimeError("unexpected internal failure"))

    assert validation.code == "validation_error"
    assert validation.status == "failed"
    assert memory.code == "memory_unavailable"
    assert memory.status == "failed"
    assert unknown.code == "internal_error"
    assert "unexpected internal failure" in unknown.message


def test_map_exception_adapts_model_result_and_tool_result_codes() -> None:
    model_blocked = ModelCallFailure(
        ModelCallResult.fail("blocked_by_policy", "model policy blocked the request")
    )
    model_dependency = ModelCallFailure(
        ModelCallResult.fail("missing_api_key", "provider is not configured")
    )
    tool_waiting = ToolResult.fail(
        "confirmation required",
        code="confirmation_required",
    )
    tool_blocked = ToolResult.fail(
        "path blocked",
        code="sensitive_path_blocked",
    )

    assert map_exception(model_blocked).code == "blocked_by_policy"
    assert map_exception(model_blocked).status == "blocked"
    assert map_exception(model_dependency).code == "dependency_init_failed"
    assert map_exception(tool_waiting).code == "waiting_user"
    assert map_exception(tool_waiting).status == "waiting_user"
    assert map_exception(tool_blocked).code == "blocked_by_policy"


def test_map_exception_adapts_executor_status_and_preserves_underlying_code() -> None:
    result = ExecutionResult(
        execution_id="execution_1",
        plan_id="plan_1",
        status="request_replan",
        success=False,
        error_code="request_replan",
        replan_reason="the plan needs a new tool",
    )
    mapped = map_exception(result)

    assert mapped.code == "request_replan"
    assert mapped.status == "request_replan"
    assert "the plan needs a new tool" in mapped.message

    blocked = map_exception(
        {"status": "blocked", "error_code": "task_policy_blocked", "message": "blocked"}
    )
    assert blocked.code == "blocked_by_policy"
    assert blocked.status == "blocked"
    assert "underlying_code" not in blocked.metadata


def test_runtime_result_from_exception_is_a_failure_result_not_success() -> None:
    result = runtime_result_from_exception(
        ToolResult.fail("confirmation required", code="confirmation_required"),
        session_id="session_20260824_120000_demo001",
        run_id="run_20260824_120000_demo001",
        metadata={"trace_id": "trace_1", "token": "hidden"},
    )

    assert result.success is False
    assert result.status == "waiting_user"
    assert result.error_code == "waiting_user"
    assert result.requires_user_input is True
    assert result.session_id == "session_20260824_120000_demo001"
    assert result.metadata == {"trace_id": "trace_1"}


def test_persistence_warning_keeps_agent_result_semantics_but_marks_persistence_unavailable() -> None:
    result = runtime_result_from_exception(
        RuntimeException(
            RuntimeErrorCode.PERSISTENCE_WARNING,
            "persistence temporarily unavailable",
        )
    )

    assert result.status == "failed"
    assert result.error_code == "persistence_warning"
    assert result.persistence_available is False
    assert result.persistence_warning == "persistence temporarily unavailable"


def test_map_exception_does_not_expose_secret_text() -> None:
    mapped = map_exception(RuntimeError("Bearer abc.def password=secret-value"))

    assert mapped.code == "internal_error"
    assert "abc.def" not in mapped.message
    assert "secret-value" not in mapped.message


def test_runtime_errors_do_not_depend_on_cli_api_or_sqlite_repository() -> None:
    module = importlib.import_module("src.app.runtime.errors")
    source = module.__file__
    assert source is not None
    text = open(source, encoding="utf-8").read().lower()
    assert "fastapi" not in text
    assert "typer" not in text
    assert "sqlite_session_repository" not in text


def test_unknown_runtime_error_codes_fall_back_to_internal_error() -> None:
    assert normalize_runtime_error_code("provider_private_code") == "internal_error"
    assert status_for_error_code("provider_private_code") == "failed"
