from __future__ import annotations

from enum import Enum
from typing import Any


class ToolErrorType(str, Enum):
    VALIDATION = "validation"
    PERMISSION = "permission"
    CONFIRMATION = "confirmation"
    TIMEOUT = "timeout"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    PROVIDER = "provider"
    NETWORK = "network"
    TOOL_RUNTIME = "tool_runtime"
    INTERNAL = "internal"


class ToolErrorCode(str, Enum):
    OK = "ok"
    TOOL_NOT_FOUND = "tool_not_found"
    TOOL_DISABLED = "tool_disabled"
    TOOL_NOT_IMPLEMENTED = "tool_not_implemented"
    INVALID_ARGS = "invalid_args"
    MISSING_REQUIRED_PARAM = "missing_required_param"
    PERMISSION_DENIED = "permission_denied"
    CONFIRMATION_REQUIRED = "confirmation_required"
    USER_REJECTED = "user_rejected"
    BLOCKED_BY_POLICY = "blocked_by_policy"
    WORKSPACE_OUT_OF_SCOPE = "workspace_out_of_scope"
    SENSITIVE_PATH_BLOCKED = "sensitive_path_blocked"
    ADMIN_PERMISSION_REQUIRED = "admin_permission_required"
    TIMEOUT = "timeout"
    INTERNAL_ERROR = "internal_error"
    PROVIDER_NOT_CONFIGURED = "provider_not_configured"
    PROVIDER_AUTH_FAILED = "provider_auth_failed"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_RATE_LIMITED = "provider_rate_limited"
    PROVIDER_RESPONSE_INVALID = "provider_response_invalid"
    PROVIDER_ERROR = "provider_error"
    MODEL_SEARCH_PARSE_FAILED = "model_search_parse_failed"
    MODEL_SEARCH_SCHEMA_INVALID = "model_search_schema_invalid"
    MODEL_SEARCH_NO_SOURCES = "model_search_no_sources"
    NETWORK_NOT_ALLOWED = "network_not_allowed"
    DRY_RUN_PREVIEW = "dry_run_preview"
    DRY_RUN_NOT_SUPPORTED = "dry_run_not_supported"
    SEARCH_NOT_CONFIGURED = "search_not_configured"
    FILE_NOT_FOUND = "file_not_found"
    FILE_ALREADY_EXISTS = "file_already_exists"
    FILE_WRITE_FAILED = "file_write_failed"
    INVALID_ENCODING = "invalid_encoding"
    PARENT_DIRECTORY_NOT_FOUND = "parent_directory_not_found"
    NOT_A_FILE = "not_a_file"
    NOT_A_DIRECTORY = "not_a_directory"
    FILE_TOO_LARGE = "file_too_large"
    BINARY_FILE_NOT_SUPPORTED = "binary_file_not_supported"
    ENCODING_ERROR = "encoding_error"
    TOO_MANY_ENTRIES = "too_many_entries"
    DIRECTORY_IGNORED = "directory_ignored"
    FILE_CONFLICT = "file_conflict"
    PREVIEW_CONFLICT = "preview_conflict"
    TEMPORARY_FILE_LOCK = "temporary_file_lock"
    GLOB_DELETE_NOT_ALLOWED = "glob_delete_not_allowed"
    DELETE_DIRECTORY_NOT_ALLOWED = "delete_directory_not_allowed"
    FILE_DELETE_FAILED = "file_delete_failed"
    PATCH_ANCHOR_NOT_FOUND = "patch_anchor_not_found"
    PATCH_OLD_TEXT_NOT_FOUND = "patch_old_text_not_found"
    PATCH_AMBIGUOUS_MATCH = "patch_ambiguous_match"
    PATCH_LINE_MISMATCH = "patch_line_mismatch"
    PATCH_CONFLICT = "patch_conflict"
    COMMAND_BLOCKED = "command_blocked"
    SHELL_REQUIRED = "shell_required"
    COMMAND_DELETE_NOT_ALLOWED = "command_delete_not_allowed"
    COMMAND_TIMEOUT = "command_timeout"
    COMMAND_NONZERO_EXIT = "command_nonzero_exit"
    COMMAND_LAUNCH_FAILED = "command_launch_failed"
    DOCUMENT_PARSE_FAILED = "document_parse_failed"
    UNSUPPORTED_DOCUMENT_TYPE = "unsupported_document_type"
    DOCUMENT_TOO_LARGE = "document_too_large"
    DOCUMENT_ENCRYPTED = "document_encrypted"
    DEPENDENCY_NOT_AVAILABLE = "dependency_not_available"
    MCP_NOT_CONFIGURED = "mcp_not_configured"
    MCP_SERVER_DISABLED = "mcp_server_disabled"
    MCP_SERVER_NOT_FOUND = "mcp_server_not_found"
    MCP_TRANSPORT_NOT_SUPPORTED = "mcp_transport_not_supported"
    MCP_COMMAND_NOT_FOUND = "mcp_command_not_found"
    MCP_PROCESS_START_FAILED = "mcp_process_start_failed"
    MCP_CONNECTION_FAILED = "mcp_connection_failed"
    MCP_INITIALIZATION_FAILED = "mcp_initialization_failed"
    MCP_TOOL_LIST_FAILED = "mcp_tool_list_failed"
    MCP_TOOL_NOT_FOUND = "mcp_tool_not_found"
    MCP_TOOL_NOT_ALLOWED = "mcp_tool_not_allowed"
    MCP_SCHEMA_INVALID = "mcp_schema_invalid"
    MCP_INVALID_ARGS = "mcp_invalid_args"
    MCP_TIMEOUT = "mcp_timeout"
    MCP_TRANSPORT_ERROR = "mcp_transport_error"
    MCP_REMOTE_ERROR = "mcp_remote_error"
    MCP_RESULT_INVALID = "mcp_result_invalid"
    MCP_OUTPUT_TOO_LARGE = "mcp_output_too_large"
    MCP_CONFIRMATION_REQUIRED = "mcp_confirmation_required"
    MCP_BLOCKED = "mcp_blocked"
    MCP_STDOUT_INVALID_JSON = "mcp_stdout_invalid_json"
    MCP_PROCESS_EXITED = "mcp_process_exited"


_ERROR_TYPE_BY_CODE: dict[str, str] = {
    ToolErrorCode.OK.value: "",
    ToolErrorCode.TOOL_NOT_FOUND.value: ToolErrorType.NOT_FOUND.value,
    ToolErrorCode.TOOL_DISABLED.value: ToolErrorType.TOOL_RUNTIME.value,
    ToolErrorCode.TOOL_NOT_IMPLEMENTED.value: ToolErrorType.TOOL_RUNTIME.value,
    ToolErrorCode.INVALID_ARGS.value: ToolErrorType.VALIDATION.value,
    ToolErrorCode.MISSING_REQUIRED_PARAM.value: ToolErrorType.VALIDATION.value,
    ToolErrorCode.PERMISSION_DENIED.value: ToolErrorType.PERMISSION.value,
    ToolErrorCode.CONFIRMATION_REQUIRED.value: ToolErrorType.CONFIRMATION.value,
    ToolErrorCode.USER_REJECTED.value: ToolErrorType.CONFIRMATION.value,
    ToolErrorCode.BLOCKED_BY_POLICY.value: ToolErrorType.PERMISSION.value,
    ToolErrorCode.WORKSPACE_OUT_OF_SCOPE.value: ToolErrorType.PERMISSION.value,
    ToolErrorCode.SENSITIVE_PATH_BLOCKED.value: ToolErrorType.PERMISSION.value,
    ToolErrorCode.ADMIN_PERMISSION_REQUIRED.value: ToolErrorType.PERMISSION.value,
    ToolErrorCode.TIMEOUT.value: ToolErrorType.TIMEOUT.value,
    ToolErrorCode.INTERNAL_ERROR.value: ToolErrorType.INTERNAL.value,
    ToolErrorCode.PROVIDER_NOT_CONFIGURED.value: ToolErrorType.PROVIDER.value,
    ToolErrorCode.PROVIDER_AUTH_FAILED.value: ToolErrorType.PROVIDER.value,
    ToolErrorCode.PROVIDER_TIMEOUT.value: ToolErrorType.TIMEOUT.value,
    ToolErrorCode.PROVIDER_RATE_LIMITED.value: ToolErrorType.PROVIDER.value,
    ToolErrorCode.PROVIDER_RESPONSE_INVALID.value: ToolErrorType.PROVIDER.value,
    ToolErrorCode.PROVIDER_ERROR.value: ToolErrorType.PROVIDER.value,
    ToolErrorCode.MODEL_SEARCH_PARSE_FAILED.value: ToolErrorType.PROVIDER.value,
    ToolErrorCode.MODEL_SEARCH_SCHEMA_INVALID.value: ToolErrorType.PROVIDER.value,
    ToolErrorCode.MODEL_SEARCH_NO_SOURCES.value: ToolErrorType.PROVIDER.value,
    ToolErrorCode.NETWORK_NOT_ALLOWED.value: ToolErrorType.NETWORK.value,
    ToolErrorCode.DRY_RUN_PREVIEW.value: "",
    ToolErrorCode.DRY_RUN_NOT_SUPPORTED.value: ToolErrorType.PERMISSION.value,
    ToolErrorCode.SEARCH_NOT_CONFIGURED.value: ToolErrorType.PROVIDER.value,
    ToolErrorCode.FILE_NOT_FOUND.value: ToolErrorType.NOT_FOUND.value,
    ToolErrorCode.FILE_ALREADY_EXISTS.value: ToolErrorType.CONFLICT.value,
    ToolErrorCode.FILE_WRITE_FAILED.value: ToolErrorType.TOOL_RUNTIME.value,
    ToolErrorCode.INVALID_ENCODING.value: ToolErrorType.VALIDATION.value,
    ToolErrorCode.PARENT_DIRECTORY_NOT_FOUND.value: ToolErrorType.NOT_FOUND.value,
    ToolErrorCode.NOT_A_FILE.value: ToolErrorType.VALIDATION.value,
    ToolErrorCode.NOT_A_DIRECTORY.value: ToolErrorType.VALIDATION.value,
    ToolErrorCode.FILE_TOO_LARGE.value: ToolErrorType.VALIDATION.value,
    ToolErrorCode.BINARY_FILE_NOT_SUPPORTED.value: ToolErrorType.VALIDATION.value,
    ToolErrorCode.ENCODING_ERROR.value: ToolErrorType.VALIDATION.value,
    ToolErrorCode.TOO_MANY_ENTRIES.value: ToolErrorType.VALIDATION.value,
    ToolErrorCode.DIRECTORY_IGNORED.value: ToolErrorType.PERMISSION.value,
    ToolErrorCode.FILE_CONFLICT.value: ToolErrorType.CONFLICT.value,
    ToolErrorCode.PREVIEW_CONFLICT.value: ToolErrorType.CONFLICT.value,
    ToolErrorCode.TEMPORARY_FILE_LOCK.value: ToolErrorType.CONFLICT.value,
    ToolErrorCode.GLOB_DELETE_NOT_ALLOWED.value: ToolErrorType.VALIDATION.value,
    ToolErrorCode.DELETE_DIRECTORY_NOT_ALLOWED.value: ToolErrorType.VALIDATION.value,
    ToolErrorCode.FILE_DELETE_FAILED.value: ToolErrorType.TOOL_RUNTIME.value,
    ToolErrorCode.PATCH_ANCHOR_NOT_FOUND.value: ToolErrorType.VALIDATION.value,
    ToolErrorCode.PATCH_OLD_TEXT_NOT_FOUND.value: ToolErrorType.VALIDATION.value,
    ToolErrorCode.PATCH_AMBIGUOUS_MATCH.value: ToolErrorType.CONFLICT.value,
    ToolErrorCode.PATCH_LINE_MISMATCH.value: ToolErrorType.CONFLICT.value,
    ToolErrorCode.PATCH_CONFLICT.value: ToolErrorType.CONFLICT.value,
    ToolErrorCode.COMMAND_BLOCKED.value: ToolErrorType.PERMISSION.value,
    ToolErrorCode.SHELL_REQUIRED.value: ToolErrorType.PERMISSION.value,
    ToolErrorCode.COMMAND_DELETE_NOT_ALLOWED.value: ToolErrorType.PERMISSION.value,
    ToolErrorCode.COMMAND_TIMEOUT.value: ToolErrorType.TIMEOUT.value,
    ToolErrorCode.COMMAND_NONZERO_EXIT.value: ToolErrorType.TOOL_RUNTIME.value,
    ToolErrorCode.COMMAND_LAUNCH_FAILED.value: ToolErrorType.TOOL_RUNTIME.value,
    ToolErrorCode.DOCUMENT_PARSE_FAILED.value: ToolErrorType.TOOL_RUNTIME.value,
    ToolErrorCode.UNSUPPORTED_DOCUMENT_TYPE.value: ToolErrorType.VALIDATION.value,
    ToolErrorCode.DOCUMENT_TOO_LARGE.value: ToolErrorType.VALIDATION.value,
    ToolErrorCode.DOCUMENT_ENCRYPTED.value: ToolErrorType.VALIDATION.value,
    ToolErrorCode.DEPENDENCY_NOT_AVAILABLE.value: ToolErrorType.PROVIDER.value,
    ToolErrorCode.MCP_NOT_CONFIGURED.value: ToolErrorType.PROVIDER.value,
    ToolErrorCode.MCP_SERVER_DISABLED.value: ToolErrorType.TOOL_RUNTIME.value,
    ToolErrorCode.MCP_SERVER_NOT_FOUND.value: ToolErrorType.NOT_FOUND.value,
    ToolErrorCode.MCP_TRANSPORT_NOT_SUPPORTED.value: ToolErrorType.PROVIDER.value,
    ToolErrorCode.MCP_COMMAND_NOT_FOUND.value: ToolErrorType.NOT_FOUND.value,
    ToolErrorCode.MCP_PROCESS_START_FAILED.value: ToolErrorType.TOOL_RUNTIME.value,
    ToolErrorCode.MCP_CONNECTION_FAILED.value: ToolErrorType.TOOL_RUNTIME.value,
    ToolErrorCode.MCP_INITIALIZATION_FAILED.value: ToolErrorType.TOOL_RUNTIME.value,
    ToolErrorCode.MCP_TOOL_LIST_FAILED.value: ToolErrorType.TOOL_RUNTIME.value,
    ToolErrorCode.MCP_TOOL_NOT_FOUND.value: ToolErrorType.NOT_FOUND.value,
    ToolErrorCode.MCP_TOOL_NOT_ALLOWED.value: ToolErrorType.PERMISSION.value,
    ToolErrorCode.MCP_SCHEMA_INVALID.value: ToolErrorType.VALIDATION.value,
    ToolErrorCode.MCP_INVALID_ARGS.value: ToolErrorType.VALIDATION.value,
    ToolErrorCode.MCP_TIMEOUT.value: ToolErrorType.TIMEOUT.value,
    ToolErrorCode.MCP_TRANSPORT_ERROR.value: ToolErrorType.TOOL_RUNTIME.value,
    ToolErrorCode.MCP_REMOTE_ERROR.value: ToolErrorType.PROVIDER.value,
    ToolErrorCode.MCP_RESULT_INVALID.value: ToolErrorType.VALIDATION.value,
    ToolErrorCode.MCP_OUTPUT_TOO_LARGE.value: ToolErrorType.VALIDATION.value,
    ToolErrorCode.MCP_CONFIRMATION_REQUIRED.value: ToolErrorType.CONFIRMATION.value,
    ToolErrorCode.MCP_BLOCKED.value: ToolErrorType.PERMISSION.value,
    ToolErrorCode.MCP_STDOUT_INVALID_JSON.value: ToolErrorType.TOOL_RUNTIME.value,
    ToolErrorCode.MCP_PROCESS_EXITED.value: ToolErrorType.TOOL_RUNTIME.value,
}

_RETRYABLE_CODES = {
    ToolErrorCode.PROVIDER_TIMEOUT.value,
    ToolErrorCode.PROVIDER_RATE_LIMITED.value,
    ToolErrorCode.MCP_CONNECTION_FAILED.value,
    ToolErrorCode.MCP_PROCESS_START_FAILED.value,
    ToolErrorCode.MCP_INITIALIZATION_FAILED.value,
    ToolErrorCode.MCP_TOOL_LIST_FAILED.value,
    ToolErrorCode.TEMPORARY_FILE_LOCK.value,
    ToolErrorCode.MCP_TIMEOUT.value,
    ToolErrorCode.MCP_TRANSPORT_ERROR.value,
    ToolErrorCode.MCP_PROCESS_EXITED.value,
}


def normalize_error_code(code: Any, default: str = ToolErrorCode.INTERNAL_ERROR.value) -> str:
    """Return a known string code; unknown values become a stable internal error."""
    candidate = code.value if isinstance(code, Enum) else code
    if isinstance(candidate, str):
        candidate = candidate.strip()
    if candidate in _ERROR_TYPE_BY_CODE:
        return candidate

    fallback = default.value if isinstance(default, Enum) else default
    if isinstance(fallback, str) and fallback in _ERROR_TYPE_BY_CODE:
        return fallback
    return ToolErrorCode.INTERNAL_ERROR.value


def error_type_for_code(code: Any) -> str:
    normalized = normalize_error_code(code)
    return _ERROR_TYPE_BY_CODE.get(normalized, ToolErrorType.INTERNAL.value)


def is_retryable_code(code: Any) -> bool:
    return normalize_error_code(code) in _RETRYABLE_CODES


__all__ = [
    "ToolErrorCode",
    "ToolErrorType",
    "error_type_for_code",
    "is_retryable_code",
    "normalize_error_code",
]
