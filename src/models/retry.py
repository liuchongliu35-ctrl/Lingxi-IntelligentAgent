from __future__ import annotations

from dataclasses import dataclass

from src.models.errors import (
    RETRYABLE_MODEL_ERROR_CODES,
    ModelErrorCode,
    normalize_model_error_code,
)


MAX_RETRIES = 5
FALLBACK_BLOCKED_ERROR_CODES = frozenset(
    {
        ModelErrorCode.BLOCKED_BY_POLICY.value,
        ModelErrorCode.INVALID_PROMPT.value,
        ModelErrorCode.USER_CANCELLED.value,
    }
)


@dataclass
class RetryPolicy:
    """Bounded exponential-backoff policy for one model call."""

    max_retries: int = MAX_RETRIES
    base_delay_seconds: float = 0.5
    max_delay_seconds: float = 8.0
    timeout_seconds: float | None = None

    def __post_init__(self) -> None:
        self.max_retries = min(max(int(self.max_retries), 0), MAX_RETRIES)
        self.base_delay_seconds = max(float(self.base_delay_seconds), 0.0)
        self.max_delay_seconds = max(float(self.max_delay_seconds), 0.0)
        if self.max_delay_seconds and self.base_delay_seconds > self.max_delay_seconds:
            self.base_delay_seconds = self.max_delay_seconds
        if self.timeout_seconds is not None:
            self.timeout_seconds = max(float(self.timeout_seconds), 0.0)

    @property
    def max_attempts(self) -> int:
        return self.max_retries + 1

    def delay_seconds(self, retry_index: int) -> float:
        """Return the delay before retry number 1..N."""
        index = max(int(retry_index), 1) - 1
        delay = self.base_delay_seconds * (2**index)
        if self.max_delay_seconds:
            delay = min(delay, self.max_delay_seconds)
        return max(delay, 0.0)


def is_retryable_error(
    code: str | ModelErrorCode | None,
    *,
    retriable: bool | None = None,
) -> bool:
    if retriable is not None:
        return bool(retriable)
    return normalize_model_error_code(code) in RETRYABLE_MODEL_ERROR_CODES


def is_fallback_allowed(code: str | ModelErrorCode | None) -> bool:
    return normalize_model_error_code(code) not in FALLBACK_BLOCKED_ERROR_CODES


__all__ = [
    "MAX_RETRIES",
    "FALLBACK_BLOCKED_ERROR_CODES",
    "RETRYABLE_MODEL_ERROR_CODES",
    "RetryPolicy",
    "is_fallback_allowed",
    "is_retryable_error",
]
