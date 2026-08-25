from __future__ import annotations

from typing import Any

from src.models.protocol import ModelCallResult


class ModelCallFailure(RuntimeError):
    """Raised when a structured model call failed and the caller needs text."""

    def __init__(self, result: ModelCallResult):
        self.result = result
        super().__init__(result.error or "model call failed")


def require_model_content(value: Any) -> Any:
    """Unwrap structured model results while keeping legacy fixtures intact."""
    if isinstance(value, ModelCallResult):
        if not value.success:
            raise ModelCallFailure(value)
        return value.content
    return value
