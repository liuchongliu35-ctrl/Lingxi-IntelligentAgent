from __future__ import annotations

from enum import Enum
from typing import Any


class ModelErrorCode(str, Enum):
    MISSING_MODEL_CONFIG = "missing_model_config"
    MISSING_API_KEY = "missing_api_key"
    UNSUPPORTED_PROVIDER = "unsupported_provider"
    UNSUPPORTED_PROTOCOL = "unsupported_protocol"
    AUTHENTICATION_FAILED = "authentication_failed"
    PERMISSION_DENIED = "permission_denied"
    MODEL_NOT_FOUND = "model_not_found"
    RATE_LIMITED = "rate_limited"
    QUOTA_EXCEEDED = "quota_exceeded"
    CONTEXT_LENGTH_EXCEEDED = "context_length_exceeded"
    TIMEOUT = "timeout"
    NETWORK_ERROR = "network_error"
    PROVIDER_SERVER_ERROR = "provider_server_error"
    TEMPORARY_UNAVAILABLE = "temporary_unavailable"
    INVALID_REQUEST = "invalid_request"
    INVALID_PROMPT = "invalid_prompt"
    INVALID_JSON = "invalid_json"
    SCHEMA_INVALID = "schema_invalid"
    JSON_REPAIR_FAILED = "json_repair_failed"
    BLOCKED_BY_POLICY = "blocked_by_policy"
    USER_CANCELLED = "user_cancelled"
    MODEL_CALL_FAILED = "model_call_failed"
    EMBEDDING_FAILED = "embedding_failed"
    COMPRESSION_FAILED = "compression_failed"
    UNKNOWN_ERROR = "unknown_error"


MODEL_ERROR_CODES = frozenset(error_code.value for error_code in ModelErrorCode)

RETRYABLE_MODEL_ERROR_CODES = frozenset(
    {
        ModelErrorCode.TIMEOUT.value,
        ModelErrorCode.RATE_LIMITED.value,
        ModelErrorCode.NETWORK_ERROR.value,
        ModelErrorCode.PROVIDER_SERVER_ERROR.value,
        ModelErrorCode.TEMPORARY_UNAVAILABLE.value,
    }
)

NON_RETRYABLE_MODEL_ERROR_CODES = frozenset(
    {
        ModelErrorCode.MISSING_MODEL_CONFIG.value,
        ModelErrorCode.MISSING_API_KEY.value,
        ModelErrorCode.UNSUPPORTED_PROVIDER.value,
        ModelErrorCode.UNSUPPORTED_PROTOCOL.value,
        ModelErrorCode.AUTHENTICATION_FAILED.value,
        ModelErrorCode.PERMISSION_DENIED.value,
        ModelErrorCode.MODEL_NOT_FOUND.value,
        ModelErrorCode.QUOTA_EXCEEDED.value,
        ModelErrorCode.INVALID_REQUEST.value,
        ModelErrorCode.INVALID_PROMPT.value,
        ModelErrorCode.INVALID_JSON.value,
        ModelErrorCode.SCHEMA_INVALID.value,
        ModelErrorCode.JSON_REPAIR_FAILED.value,
        ModelErrorCode.BLOCKED_BY_POLICY.value,
        ModelErrorCode.USER_CANCELLED.value,
        ModelErrorCode.EMBEDDING_FAILED.value,
        ModelErrorCode.COMPRESSION_FAILED.value,
        ModelErrorCode.UNKNOWN_ERROR.value,
    }
)

_HTTP_STATUS_ERROR_CODES: dict[int, str] = {
    400: ModelErrorCode.INVALID_REQUEST.value,
    401: ModelErrorCode.AUTHENTICATION_FAILED.value,
    403: ModelErrorCode.PERMISSION_DENIED.value,
    404: ModelErrorCode.MODEL_NOT_FOUND.value,
    408: ModelErrorCode.TIMEOUT.value,
    409: ModelErrorCode.INVALID_REQUEST.value,
    422: ModelErrorCode.INVALID_REQUEST.value,
    429: ModelErrorCode.RATE_LIMITED.value,
    500: ModelErrorCode.PROVIDER_SERVER_ERROR.value,
    501: ModelErrorCode.PROVIDER_SERVER_ERROR.value,
    502: ModelErrorCode.PROVIDER_SERVER_ERROR.value,
    503: ModelErrorCode.TEMPORARY_UNAVAILABLE.value,
    504: ModelErrorCode.TIMEOUT.value,
}

_PROVIDER_ERROR_CODE_ALIASES: dict[str, str] = {
    "bad_request": ModelErrorCode.INVALID_REQUEST.value,
    "authentication_failed": ModelErrorCode.AUTHENTICATION_FAILED.value,
    "unauthorized": ModelErrorCode.AUTHENTICATION_FAILED.value,
    "forbidden": ModelErrorCode.PERMISSION_DENIED.value,
    "permission_denied": ModelErrorCode.PERMISSION_DENIED.value,
    "not_found": ModelErrorCode.MODEL_NOT_FOUND.value,
    "model_not_found": ModelErrorCode.MODEL_NOT_FOUND.value,
    "rate_limited": ModelErrorCode.RATE_LIMITED.value,
    "rate_limit_exceeded": ModelErrorCode.RATE_LIMITED.value,
    "quota_exceeded": ModelErrorCode.QUOTA_EXCEEDED.value,
    "timeout": ModelErrorCode.TIMEOUT.value,
    "request_timeout": ModelErrorCode.TIMEOUT.value,
    "network_error": ModelErrorCode.NETWORK_ERROR.value,
    "server_error": ModelErrorCode.PROVIDER_SERVER_ERROR.value,
    "provider_server_error": ModelErrorCode.PROVIDER_SERVER_ERROR.value,
    "temporary_unavailable": ModelErrorCode.TEMPORARY_UNAVAILABLE.value,
    "service_unavailable": ModelErrorCode.TEMPORARY_UNAVAILABLE.value,
    "invalid_request": ModelErrorCode.INVALID_REQUEST.value,
    "invalid_prompt": ModelErrorCode.INVALID_PROMPT.value,
    "blocked_by_policy": ModelErrorCode.BLOCKED_BY_POLICY.value,
    "user_cancelled": ModelErrorCode.USER_CANCELLED.value,
    "json_repair_failed": ModelErrorCode.JSON_REPAIR_FAILED.value,
    "schema_invalid": ModelErrorCode.SCHEMA_INVALID.value,
}


def normalize_model_error_code(
    value: str | ModelErrorCode | None,
    *,
    default: ModelErrorCode = ModelErrorCode.UNKNOWN_ERROR,
) -> str:
    """Return a stable public error code without exposing provider-specific values."""
    if isinstance(value, ModelErrorCode):
        return value.value
    normalized = str(value or "").strip().lower()
    if normalized in MODEL_ERROR_CODES:
        return normalized
    return default.value


def normalize_model_error_category(code: str | ModelErrorCode | None) -> str:
    normalized = normalize_model_error_code(code)
    if normalized in {
        ModelErrorCode.AUTHENTICATION_FAILED.value,
        ModelErrorCode.PERMISSION_DENIED.value,
    }:
        return "auth"
    if normalized in {
        ModelErrorCode.TIMEOUT.value,
        ModelErrorCode.RATE_LIMITED.value,
        ModelErrorCode.NETWORK_ERROR.value,
        ModelErrorCode.PROVIDER_SERVER_ERROR.value,
        ModelErrorCode.TEMPORARY_UNAVAILABLE.value,
    }:
        return "transient"
    if normalized in {
        ModelErrorCode.INVALID_REQUEST.value,
        ModelErrorCode.INVALID_PROMPT.value,
        ModelErrorCode.MODEL_NOT_FOUND.value,
    }:
        return "request"
    if normalized in {
        ModelErrorCode.BLOCKED_BY_POLICY.value,
        ModelErrorCode.USER_CANCELLED.value,
    }:
        return "policy"
    if normalized in {
        ModelErrorCode.MISSING_MODEL_CONFIG.value,
        ModelErrorCode.MISSING_API_KEY.value,
        ModelErrorCode.UNSUPPORTED_PROVIDER.value,
        ModelErrorCode.UNSUPPORTED_PROTOCOL.value,
    }:
        return "config"
    if normalized == ModelErrorCode.QUOTA_EXCEEDED.value:
        return "quota"
    if normalized in {
        ModelErrorCode.INVALID_JSON.value,
        ModelErrorCode.SCHEMA_INVALID.value,
        ModelErrorCode.JSON_REPAIR_FAILED.value,
    }:
        return "parse"
    if normalized in {
        ModelErrorCode.EMBEDDING_FAILED.value,
        ModelErrorCode.COMPRESSION_FAILED.value,
    }:
        return "domain"
    return "unknown"


def is_retryable_model_error_code(value: str | ModelErrorCode | None) -> bool:
    return normalize_model_error_code(value) in RETRYABLE_MODEL_ERROR_CODES


def _normalize_provider_error_token(value: str | None) -> str | None:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return None
    if normalized in MODEL_ERROR_CODES:
        return normalized
    return _PROVIDER_ERROR_CODE_ALIASES.get(normalized)


def _normalize_message_error_token(value: str | None) -> str | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    phrase_map: list[tuple[str, str]] = [
        ("temporarily unavailable", ModelErrorCode.TEMPORARY_UNAVAILABLE.value),
        ("temporary unavailable", ModelErrorCode.TEMPORARY_UNAVAILABLE.value),
        ("service unavailable", ModelErrorCode.TEMPORARY_UNAVAILABLE.value),
        ("rate limit", ModelErrorCode.RATE_LIMITED.value),
        ("too many requests", ModelErrorCode.RATE_LIMITED.value),
        ("timeout", ModelErrorCode.TIMEOUT.value),
        ("timed out", ModelErrorCode.TIMEOUT.value),
        ("network", ModelErrorCode.NETWORK_ERROR.value),
        ("connection reset", ModelErrorCode.NETWORK_ERROR.value),
        ("unauthorized", ModelErrorCode.AUTHENTICATION_FAILED.value),
        ("authentication failed", ModelErrorCode.AUTHENTICATION_FAILED.value),
        ("forbidden", ModelErrorCode.PERMISSION_DENIED.value),
        ("permission denied", ModelErrorCode.PERMISSION_DENIED.value),
        ("model not found", ModelErrorCode.MODEL_NOT_FOUND.value),
        ("invalid prompt", ModelErrorCode.INVALID_PROMPT.value),
        ("blocked by policy", ModelErrorCode.BLOCKED_BY_POLICY.value),
        ("quota exceeded", ModelErrorCode.QUOTA_EXCEEDED.value),
        ("invalid request", ModelErrorCode.INVALID_REQUEST.value),
        ("invalid json", ModelErrorCode.INVALID_JSON.value),
        ("schema invalid", ModelErrorCode.SCHEMA_INVALID.value),
        ("json repair failed", ModelErrorCode.JSON_REPAIR_FAILED.value),
        ("server error", ModelErrorCode.PROVIDER_SERVER_ERROR.value),
    ]
    for phrase, code in phrase_map:
        if phrase in text:
            return code
    return None


def classify_model_error_code(
    code: str | ModelErrorCode | None = None,
    *,
    http_status: int | None = None,
    provider_error_code: str | None = None,
    provider_error_message: str | None = None,
    provider_error_hint: str | None = None,
    default: ModelErrorCode = ModelErrorCode.UNKNOWN_ERROR,
) -> str:
    if http_status is not None:
        try:
            status = int(http_status)
        except (TypeError, ValueError):
            status = None
        if status is not None:
            if status >= 500 and status != 503:
                return ModelErrorCode.PROVIDER_SERVER_ERROR.value
            if status in _HTTP_STATUS_ERROR_CODES:
                return _HTTP_STATUS_ERROR_CODES[status]
    normalized_provider = _normalize_provider_error_token(provider_error_code)
    if normalized_provider is not None:
        return normalized_provider
    normalized_message = _normalize_message_error_token(provider_error_hint)
    if normalized_message is not None:
        return normalized_message
    normalized_message = _normalize_message_error_token(provider_error_message)
    if normalized_message is not None:
        return normalized_message
    return normalize_model_error_code(code, default=default)
