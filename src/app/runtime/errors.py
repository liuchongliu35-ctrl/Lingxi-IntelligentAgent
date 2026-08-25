"""Runtime error contracts and cross-layer error/status adaptation."""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping

from .contracts import RuntimeResult, RuntimeStatus
from .serialization import safe_serialize


class RuntimeErrorCode(str, Enum):
    """Stable error codes exposed by Runtime, CLI, and API."""

    VALIDATION_ERROR = "validation_error"
    SESSION_NOT_FOUND = "session_not_found"
    RUN_NOT_FOUND = "run_not_found"
    SESSION_CONFLICT = "session_conflict"
    MEMORY_UNAVAILABLE = "memory_unavailable"
    PERSISTENCE_WARNING = "persistence_warning"
    AGENT_EXECUTION_FAILED = "agent_execution_failed"
    BLOCKED_BY_POLICY = "blocked_by_policy"
    WAITING_USER = "waiting_user"
    REQUEST_REPLAN = "request_replan"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"
    DEPENDENCY_INIT_FAILED = "dependency_init_failed"
    EXPORT_FAILED = "export_failed"
    API_ERROR = "api_error"
    INTERNAL_ERROR = "internal_error"


RUNTIME_ERROR_CODES = frozenset(code.value for code in RuntimeErrorCode)

_ERROR_STATUS: dict[str, str] = {
    RuntimeErrorCode.BLOCKED_BY_POLICY.value: RuntimeStatus.BLOCKED.value,
    RuntimeErrorCode.WAITING_USER.value: RuntimeStatus.WAITING_USER.value,
    RuntimeErrorCode.REQUEST_REPLAN.value: RuntimeStatus.REQUEST_REPLAN.value,
    RuntimeErrorCode.CANCELLED.value: RuntimeStatus.CANCELLED.value,
    RuntimeErrorCode.INTERRUPTED.value: RuntimeStatus.INTERRUPTED.value,
}

_HTTP_STATUS_BY_CODE: dict[str, int] = {
    RuntimeErrorCode.VALIDATION_ERROR.value: 400,
    RuntimeErrorCode.SESSION_CONFLICT.value: 409,
    RuntimeErrorCode.SESSION_NOT_FOUND.value: 404,
    RuntimeErrorCode.RUN_NOT_FOUND.value: 404,
    RuntimeErrorCode.BLOCKED_BY_POLICY.value: 403,
    RuntimeErrorCode.CANCELLED.value: 409,
    RuntimeErrorCode.INTERRUPTED.value: 409,
    RuntimeErrorCode.MEMORY_UNAVAILABLE.value: 503,
    RuntimeErrorCode.DEPENDENCY_INIT_FAILED.value: 503,
    RuntimeErrorCode.WAITING_USER.value: 202,
    RuntimeErrorCode.REQUEST_REPLAN.value: 202,
    RuntimeErrorCode.EXPORT_FAILED.value: 500,
    RuntimeErrorCode.API_ERROR.value: 500,
    RuntimeErrorCode.INTERNAL_ERROR.value: 500,
    RuntimeErrorCode.AGENT_EXECUTION_FAILED.value: 500,
    RuntimeErrorCode.PERSISTENCE_WARNING.value: 200,
}

_CLI_EXIT_BY_CODE: dict[str, int] = {
    RuntimeErrorCode.VALIDATION_ERROR.value: 1,
    RuntimeErrorCode.SESSION_NOT_FOUND.value: 1,
    RuntimeErrorCode.RUN_NOT_FOUND.value: 1,
    RuntimeErrorCode.SESSION_CONFLICT.value: 1,
    RuntimeErrorCode.BLOCKED_BY_POLICY.value: 2,
    RuntimeErrorCode.CANCELLED.value: 2,
    RuntimeErrorCode.INTERRUPTED.value: 2,
    RuntimeErrorCode.MEMORY_UNAVAILABLE.value: 3,
    RuntimeErrorCode.DEPENDENCY_INIT_FAILED.value: 3,
    RuntimeErrorCode.EXPORT_FAILED.value: 3,
    RuntimeErrorCode.API_ERROR.value: 3,
    RuntimeErrorCode.INTERNAL_ERROR.value: 3,
    RuntimeErrorCode.AGENT_EXECUTION_FAILED.value: 3,
    RuntimeErrorCode.PERSISTENCE_WARNING.value: 0,
    RuntimeErrorCode.WAITING_USER.value: 0,
    RuntimeErrorCode.REQUEST_REPLAN.value: 0,
}

_RUNTIME_CODE_ALIASES = {
    "success": None,
    "completed": None,
    "validation": RuntimeErrorCode.VALIDATION_ERROR.value,
    "invalid_request": RuntimeErrorCode.VALIDATION_ERROR.value,
    "not_found": RuntimeErrorCode.SESSION_NOT_FOUND.value,
    "session_missing": RuntimeErrorCode.SESSION_NOT_FOUND.value,
    "run_missing": RuntimeErrorCode.RUN_NOT_FOUND.value,
    "conflict": RuntimeErrorCode.SESSION_CONFLICT.value,
    "database_unavailable": RuntimeErrorCode.MEMORY_UNAVAILABLE.value,
    "memory_error": RuntimeErrorCode.MEMORY_UNAVAILABLE.value,
    "persistence_unavailable": RuntimeErrorCode.PERSISTENCE_WARNING.value,
    "execution_failed": RuntimeErrorCode.AGENT_EXECUTION_FAILED.value,
    "agent_failed": RuntimeErrorCode.AGENT_EXECUTION_FAILED.value,
    "policy_blocked": RuntimeErrorCode.BLOCKED_BY_POLICY.value,
    "confirmation_required": RuntimeErrorCode.WAITING_USER.value,
    "user_input_required": RuntimeErrorCode.WAITING_USER.value,
    "confirmation_pending": RuntimeErrorCode.WAITING_USER.value,
    "user_cancelled": RuntimeErrorCode.CANCELLED.value,
    "replan": RuntimeErrorCode.REQUEST_REPLAN.value,
    "dependency_error": RuntimeErrorCode.DEPENDENCY_INIT_FAILED.value,
    "startup_failed": RuntimeErrorCode.DEPENDENCY_INIT_FAILED.value,
    "export_error": RuntimeErrorCode.EXPORT_FAILED.value,
    "unknown_error": RuntimeErrorCode.INTERNAL_ERROR.value,
}

_MODEL_DEPENDENCY_CODES = {
    "missing_model_config",
    "missing_api_key",
    "unsupported_provider",
    "unsupported_protocol",
    "model_manager_unavailable",
}

_TOOL_DEPENDENCY_CODES = {
    "tool_manager_unavailable",
    "dependency_not_available",
    "provider_not_configured",
    "mcp_not_configured",
}

_POLICY_CODES = {
    "blocked_by_policy",
    "action_blocked",
    "command_blocked",
    "task_policy_blocked",
    "safety_blocked",
    "sensitive_path_blocked",
    "workspace_out_of_scope",
    "permission_denied",
    "admin_permission_required",
    "network_not_allowed",
    "mcp_blocked",
    "mcp_tool_not_allowed",
}

_WAITING_CODES = {
    "waiting_user",
    "confirmation_required",
    "confirmation_pending",
    "clarification_required",
    "user_input_required",
    "mcp_confirmation_required",
}

_CANCELLED_CODES = {"cancelled", "user_cancelled", "action_cancelled", "confirmation_rejected", "user_rejected"}

_REPLAN_CODES = {"request_replan"}


class RuntimeException(RuntimeError):
    """Unified Runtime exception with a stable public code and status."""

    def __init__(
        self,
        code: str | RuntimeErrorCode,
        message: str = "",
        *,
        status: str | RuntimeStatus | None = None,
        metadata: Mapping[str, Any] | None = None,
        cause: BaseException | None = None,
    ) -> None:
        self.code = normalize_runtime_error_code(code)
        self.status = normalize_runtime_status(
            status or status_for_error_code(self.code)
        )
        self.message = sanitize_error_message(message or self.code)
        self.metadata = _safe_metadata(metadata)
        self.cause = cause
        super().__init__(self.message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "status": self.status,
            "message": self.message,
            "metadata": dict(self.metadata),
        }


def normalize_runtime_error_code(
    value: str | RuntimeErrorCode | None,
    *,
    default: RuntimeErrorCode = RuntimeErrorCode.INTERNAL_ERROR,
) -> str:
    candidate = value.value if isinstance(value, RuntimeErrorCode) else value
    normalized = str(candidate or "").strip().lower()
    if normalized in RUNTIME_ERROR_CODES:
        return normalized
    alias = _RUNTIME_CODE_ALIASES.get(normalized)
    if alias is not None:
        return alias
    return default.value


def normalize_runtime_status(
    value: str | RuntimeStatus,
    *,
    default: RuntimeStatus = RuntimeStatus.FAILED,
) -> str:
    candidate = value.value if isinstance(value, RuntimeStatus) else value
    normalized = str(candidate or "").strip().lower()
    if normalized in {status.value for status in RuntimeStatus}:
        return normalized
    return default.value


def status_for_error_code(
    code: str | RuntimeErrorCode | None,
) -> str:
    normalized = normalize_runtime_error_code(code)
    return _ERROR_STATUS.get(normalized, RuntimeStatus.FAILED.value)


def http_status_for_error(
    value: RuntimeException | RuntimeErrorCode | str | None = None,
    *,
    status: str | RuntimeStatus | None = None,
) -> int:
    """Map a Runtime error or status to the designed HTTP status."""

    raw_status = _enum_or_text(value) if isinstance(value, (RuntimeStatus, str)) else ""
    if raw_status in {"completed", "success"}:
        return 200
    if raw_status == RuntimeStatus.WAITING_USER.value:
        return 202
    if raw_status == RuntimeStatus.REQUEST_REPLAN.value:
        return 202
    if raw_status == RuntimeStatus.BLOCKED.value:
        return 403
    if raw_status in {RuntimeStatus.CANCELLED.value, RuntimeStatus.INTERRUPTED.value}:
        return 409
    if status is not None:
        normalized_status = normalize_runtime_status(status)
        if normalized_status == RuntimeStatus.COMPLETED.value:
            return 200
        if normalized_status in {
            RuntimeStatus.WAITING_USER.value,
            RuntimeStatus.REQUEST_REPLAN.value,
        }:
            return 202
        if normalized_status in {
            RuntimeStatus.BLOCKED.value,
        }:
            return 403
        if normalized_status in {
            RuntimeStatus.CANCELLED.value,
            RuntimeStatus.INTERRUPTED.value,
        }:
            return 409
    code = _code_from_value(value)
    if code is None:
        return 200 if status == RuntimeStatus.COMPLETED.value else 500
    return _HTTP_STATUS_BY_CODE.get(code, 500)


def cli_exit_code_for_error(
    value: RuntimeException | RuntimeErrorCode | str | None = None,
    *,
    status: str | RuntimeStatus | None = None,
) -> int:
    """Map a Runtime error or status to the stable CLI exit code."""

    raw_status = _enum_or_text(value) if isinstance(value, (RuntimeStatus, str)) else ""
    if raw_status in {
        "completed",
        "success",
        RuntimeStatus.WAITING_USER.value,
        RuntimeStatus.REQUEST_REPLAN.value,
    }:
        return 0
    if raw_status in {
        RuntimeStatus.BLOCKED.value,
        RuntimeStatus.CANCELLED.value,
        RuntimeStatus.INTERRUPTED.value,
    }:
        return 2
    if status is not None:
        normalized_status = normalize_runtime_status(status)
        if normalized_status in {
            RuntimeStatus.COMPLETED.value,
            RuntimeStatus.WAITING_USER.value,
            RuntimeStatus.REQUEST_REPLAN.value,
        }:
            return 0
        if normalized_status in {
            RuntimeStatus.BLOCKED.value,
            RuntimeStatus.CANCELLED.value,
            RuntimeStatus.INTERRUPTED.value,
        }:
            return 2
    code = _code_from_value(value)
    if code is None:
        return 0 if status == RuntimeStatus.COMPLETED.value else 3
    return _CLI_EXIT_BY_CODE.get(code, 3)


def map_exception(
    error: Any,
    *,
    metadata: Mapping[str, Any] | None = None,
    default_code: RuntimeErrorCode = RuntimeErrorCode.INTERNAL_ERROR,
) -> RuntimeException:
    """Adapt Memory, Models, Tools, Agent, and ordinary exceptions."""

    if isinstance(error, RuntimeException):
        if not metadata:
            return error
        merged = {**error.metadata, **dict(metadata)}
        return RuntimeException(
            error.code,
            error.message,
            status=error.status,
            metadata=merged,
            cause=error.cause,
        )

    candidate_code = _candidate_code(error)
    code = _map_cross_layer_code(candidate_code)
    if code is None:
        code = _classify_exception(error, default_code=default_code)
    status = _status_from_error_value(error, code)
    message = _message_from_error(error, code)
    combined_metadata: dict[str, Any] = {}
    source_metadata = _read_field(error, "metadata")
    if isinstance(source_metadata, Mapping):
        combined_metadata.update(source_metadata)
    if metadata:
        combined_metadata.update(metadata)
    return RuntimeException(
        code,
        message,
        status=status,
        metadata=combined_metadata,
        cause=error if isinstance(error, BaseException) else None,
    )


def runtime_result_from_exception(
    error: Any,
    *,
    session_id: str | None = None,
    run_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> RuntimeResult:
    """Build a failure-shaped RuntimeResult without swallowing the failure."""

    runtime_error = map_exception(error, metadata=metadata)
    valid_session_id = _valid_session_id_or_none(session_id)
    result_metadata = dict(runtime_error.metadata)
    return RuntimeResult(
        success=False,
        status=runtime_error.status,
        session_id=valid_session_id,
        run_id=run_id,
        error_code=runtime_error.code,
        error_message=runtime_error.message,
        requires_user_input=runtime_error.status == RuntimeStatus.WAITING_USER.value,
        request_replan=runtime_error.status == RuntimeStatus.REQUEST_REPLAN.value,
        persistence_available=runtime_error.code
        not in {
            RuntimeErrorCode.MEMORY_UNAVAILABLE.value,
            RuntimeErrorCode.PERSISTENCE_WARNING.value,
        },
        persistence_warning=(
            runtime_error.message
            if runtime_error.code == RuntimeErrorCode.PERSISTENCE_WARNING.value
            else None
        ),
        metadata=result_metadata,
    )


def map_http_status(
    value: RuntimeException | RuntimeErrorCode | str | None,
    *,
    status: str | RuntimeStatus | None = None,
) -> int:
    """Compatibility alias for HTTP mapping callers."""

    return http_status_for_error(value, status=status)


def map_cli_exit_code(
    value: RuntimeException | RuntimeErrorCode | str | None,
    *,
    status: str | RuntimeStatus | None = None,
) -> int:
    """Compatibility alias for CLI mapping callers."""

    return cli_exit_code_for_error(value, status=status)


def _candidate_code(error: Any) -> str | None:
    for field_name in ("code", "error_code"):
        value = _read_field(error, field_name)
        if value:
            return _enum_or_text(value)
    error_info = _read_field(error, "error_info")
    if error_info is not None:
        value = _read_field(error_info, "code")
        if value:
            return _enum_or_text(value)
    result = _read_field(error, "result")
    if result is not None:
        value = _read_field(result, "code") or _read_field(result, "error_code")
        if value is None:
            error_info = _read_field(result, "error_info")
            value = _read_field(error_info, "code") if error_info is not None else None
        if value:
            return _enum_or_text(value)
    return None


def _map_cross_layer_code(candidate: str | None) -> str | None:
    if not candidate:
        return None
    normalized = candidate.strip().lower()
    if normalized in RUNTIME_ERROR_CODES:
        return normalized
    if normalized in _MODEL_DEPENDENCY_CODES or normalized in _TOOL_DEPENDENCY_CODES:
        return RuntimeErrorCode.DEPENDENCY_INIT_FAILED.value
    if normalized in _POLICY_CODES:
        return RuntimeErrorCode.BLOCKED_BY_POLICY.value
    if normalized in _WAITING_CODES:
        return RuntimeErrorCode.WAITING_USER.value
    if normalized in _CANCELLED_CODES:
        return RuntimeErrorCode.CANCELLED.value
    if normalized in _REPLAN_CODES:
        return RuntimeErrorCode.REQUEST_REPLAN.value
    if normalized in {"interrupted", "run_interrupted"}:
        return RuntimeErrorCode.INTERRUPTED.value
    if normalized in {"persistence_warning", "persistence_unavailable"}:
        return RuntimeErrorCode.PERSISTENCE_WARNING.value
    if normalized in {"memory_unavailable", "database_unavailable"}:
        return RuntimeErrorCode.MEMORY_UNAVAILABLE.value
    return None


def _classify_exception(
    error: Any,
    *,
    default_code: RuntimeErrorCode,
) -> str:
    status = _read_field(error, "status")
    status_code = _map_cross_layer_code(_enum_or_text(status) if status else None)
    if status_code is not None:
        return status_code

    text = _message_from_error(error, "")
    lowered = text.lower()
    class_name = error.__class__.__name__.lower() if error is not None else ""
    module_name = error.__class__.__module__.lower() if error is not None else ""

    if "session" in lowered and ("not found" in lowered or "missing" in lowered):
        return RuntimeErrorCode.SESSION_NOT_FOUND.value
    if re_match_any(lowered, ("run not found", "run missing", "unknown run")):
        return RuntimeErrorCode.RUN_NOT_FOUND.value
    if "session" in lowered and "conflict" in lowered:
        return RuntimeErrorCode.SESSION_CONFLICT.value
    if "persistence" in lowered or "database" in lowered or "sqlite" in module_name:
        return RuntimeErrorCode.MEMORY_UNAVAILABLE.value
    if "blocked" in lowered or "permission denied" in lowered:
        return RuntimeErrorCode.BLOCKED_BY_POLICY.value
    if "confirm" in lowered or "waiting user" in lowered:
        return RuntimeErrorCode.WAITING_USER.value
    if "replan" in lowered:
        return RuntimeErrorCode.REQUEST_REPLAN.value
    if "cancel" in lowered:
        return RuntimeErrorCode.CANCELLED.value
    if "interrupt" in lowered:
        return RuntimeErrorCode.INTERRUPTED.value
    if isinstance(error, (TypeError, ValueError, AssertionError)):
        return RuntimeErrorCode.VALIDATION_ERROR.value
    if isinstance(error, PermissionError):
        return RuntimeErrorCode.BLOCKED_BY_POLICY.value
    if isinstance(error, (ImportError, ModuleNotFoundError)):
        return RuntimeErrorCode.DEPENDENCY_INIT_FAILED.value
    if isinstance(error, TimeoutError) or "timeout" in class_name:
        return RuntimeErrorCode.AGENT_EXECUTION_FAILED.value
    return _map_cross_layer_code(default_code.value) or default_code.value


def _status_from_error_value(error: Any, code: str) -> str:
    status = _read_field(error, "status")
    if status:
        normalized = normalize_runtime_status(_enum_or_text(status))
        if normalized != RuntimeStatus.FAILED.value or code in {
            RuntimeErrorCode.BLOCKED_BY_POLICY.value,
            RuntimeErrorCode.WAITING_USER.value,
            RuntimeErrorCode.REQUEST_REPLAN.value,
            RuntimeErrorCode.CANCELLED.value,
            RuntimeErrorCode.INTERRUPTED.value,
        }:
            return normalized
    return status_for_error_code(code)


def _message_from_error(error: Any, code: str) -> str:
    for field_name in (
        "error",
        "message",
        "error_message",
        "replan_reason",
        "user_input_request",
        "output",
        "summary",
    ):
        value = _read_field(error, field_name)
        if value:
            return sanitize_error_message(value)
    result = _read_field(error, "result")
    if result is not None:
        for field_name in ("error", "message", "error_message"):
            value = _read_field(result, field_name)
            if value:
                return sanitize_error_message(value)
    if isinstance(error, BaseException):
        return sanitize_error_message(error)
    return sanitize_error_message(code or "Runtime error")


def sanitize_error_message(value: Any, *, max_chars: int = 240) -> str:
    """Bound and sanitize an exception message for Runtime output/logging."""

    if isinstance(value, BaseException):
        text = str(value)
    else:
        text = str(value or "")
    serialized = safe_serialize(text, max_text_chars=max_chars)
    return serialized if isinstance(serialized, str) else "Runtime error"


def _safe_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(metadata, Mapping):
        return {}
    serialized = safe_serialize(metadata, max_depth=5, max_items=50, max_text_chars=500)
    return serialized if isinstance(serialized, dict) else {}


def _read_field(value: Any, field_name: str, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, Mapping):
        return value.get(field_name, default)
    return getattr(value, field_name, default)


def _enum_or_text(value: Any) -> str:
    return value.value if isinstance(value, Enum) else str(value).strip().lower()


def _code_from_value(value: Any) -> str | None:
    if isinstance(value, RuntimeException):
        return value.code
    if isinstance(value, (RuntimeErrorCode, str)):
        candidate = value.value if isinstance(value, RuntimeErrorCode) else value
        normalized = normalize_runtime_error_code(candidate, default=RuntimeErrorCode.INTERNAL_ERROR)
        if candidate is None:
            return None
        if str(candidate).strip().lower() in {"completed", "success"}:
            return None
        return normalized
    return _candidate_code(value)


def _valid_session_id_or_none(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        from src.memory.ids import validate_session_id

        return validate_session_id(value)
    except (TypeError, ValueError):
        return None


def re_match_any(value: str, phrases: tuple[str, ...]) -> bool:
    return any(phrase in value for phrase in phrases)


__all__ = [
    "RUNTIME_ERROR_CODES",
    "RuntimeErrorCode",
    "RuntimeException",
    "cli_exit_code_for_error",
    "http_status_for_error",
    "map_cli_exit_code",
    "map_exception",
    "map_http_status",
    "normalize_runtime_error_code",
    "normalize_runtime_status",
    "runtime_result_from_exception",
    "sanitize_error_message",
    "status_for_error_code",
]
