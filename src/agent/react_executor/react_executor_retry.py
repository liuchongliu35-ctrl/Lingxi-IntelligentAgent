from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from time import sleep
from typing import Any, Callable, Dict

from src.agent.react_executor_checker import (
    CheckerResult,
    NON_RETRYABLE_CODES,
    RETRYABLE_CODES,
    classify_tool_result_code,
)
from src.agent.react_executor_protocol import ObservationPacket


RETRY_SCHEDULED_CODE = "retry_scheduled"
RETRY_TARGET_NOT_FOUND_CODE = "retry_target_not_found"
RETRY_NOT_ALLOWED_CODE = "retry_not_allowed"
RETRY_NOT_RETRYABLE_CODE = "retry_not_retryable"
RETRY_EXHAUSTED_CODE = "retry_exhausted"
RETRY_UNSUPPORTED_ACTION_CODE = "retry_unsupported_action"
RETRY_SLEEP_FAILED_CODE = "retry_sleep_failed"

RETRYABLE_FAILURE_CLASSES = {"timeout", "retryable", "unknown_failure"}
NON_RETRYABLE_FAILURE_CLASSES = {
    "user_input",
    "safety_violation",
    "dependency_failure",
    "validation_failure",
    "resource_unavailable",
    "non_retryable",
}
RETRYABLE_ACTION_TYPES = {"call_tool", "call_model", "fallback_to_tool", "fallback_to_model"}


@dataclass
class RetryDecision:
    can_retry: bool
    reason: str
    code: str
    failure_class: str = "unknown_failure"
    retry_count: int = 0
    retry_attempt: int = 0
    next_attempt: int = 1
    max_retries: int = 0
    backoff_seconds: float = 0.0
    source_observation_id: str | None = None
    source_packet_id: str | None = None
    action_type: str | None = None
    action_target: str | None = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return _json_safe(self)


class RetryPolicy:
    """Retry classification and exponential backoff policy."""

    def __init__(
        self,
        *,
        default_max_retries: int = 3,
        backoff_base_seconds: float = 0.2,
        backoff_max_seconds: float = 2.0,
        sleep_fn: Callable[[float], None] | None = sleep,
    ):
        self.default_max_retries = max(int(default_max_retries), 0)
        self.backoff_base_seconds = max(float(backoff_base_seconds), 0.0)
        self.backoff_max_seconds = max(float(backoff_max_seconds), 0.0)
        self.sleep_fn = sleep_fn

    def calculate_backoff_seconds(self, retry_attempt: int) -> float:
        attempt = max(int(retry_attempt), 0)
        value = self.backoff_base_seconds * (2**attempt)
        return min(value, self.backoff_max_seconds)

    def build_decision(
        self,
        observation: ObservationPacket,
        checker_result: CheckerResult,
        *,
        step: Any | None = None,
        step_state: Any | None = None,
    ) -> RetryDecision:
        failure_class = str(checker_result.metadata.get("failure_class") or classify_tool_result_code(observation.code))
        retry_attempt = max(
            int(observation.attempt or 0),
            int(getattr(step_state, "attempts", 0) or 0),
            int(checker_result.metadata.get("step_turn", 0) or 0),
        )
        retry_count = max(retry_attempt - 1, 0)
        max_retries = max(int(getattr(step, "max_retries", self.default_max_retries) or 0), 0)
        base = {
            "failure_class": failure_class,
            "retry_count": retry_count,
            "retry_attempt": retry_attempt,
            "next_attempt": retry_attempt + 1,
            "max_retries": max_retries,
            "source_observation_id": observation.observation_id,
            "source_packet_id": observation.packet_id,
            "action_type": observation.action_type,
            "action_target": observation.action_target,
            "metadata": {
                "checker_result": checker_result.to_dict(),
                "source_code": observation.code,
            },
        }

        if observation.action_type not in RETRYABLE_ACTION_TYPES:
            return self._blocked(
                "Action type is not retryable.",
                RETRY_UNSUPPORTED_ACTION_CODE,
                **base,
            )

        if checker_result.checker_status == "retry":
            if retry_count >= max_retries:
                return self._blocked("Retry attempts are exhausted.", RETRY_EXHAUSTED_CODE, **base)
            return RetryDecision(
                can_retry=True,
                reason=checker_result.reason or "Retry allowed by checker.",
                code=RETRY_SCHEDULED_CODE,
                backoff_seconds=self.calculate_backoff_seconds(retry_count),
                **base,
            )

        if self._is_retryable_failure(observation.code, failure_class):
            if retry_count >= max_retries:
                return self._blocked("Retry attempts are exhausted.", RETRY_EXHAUSTED_CODE, **base)
            return self._blocked("Checker did not allow retry.", RETRY_NOT_ALLOWED_CODE, **base)

        return self._blocked("Failure code is not retryable.", RETRY_NOT_RETRYABLE_CODE, **base)

    def wait(self, decision: RetryDecision) -> None:
        if not decision.can_retry or decision.backoff_seconds <= 0:
            return
        if self.sleep_fn is not None:
            self.sleep_fn(decision.backoff_seconds)

    def _is_retryable_failure(self, code: str | None, failure_class: str) -> bool:
        normalized = str(code or "").strip().lower()
        if normalized in NON_RETRYABLE_CODES or failure_class in NON_RETRYABLE_FAILURE_CLASSES:
            return False
        if normalized in RETRYABLE_CODES or failure_class in RETRYABLE_FAILURE_CLASSES:
            return True
        return False

    def _blocked(self, reason: str, code: str, **kwargs: Any) -> RetryDecision:
        return RetryDecision(can_retry=False, reason=reason, code=code, **kwargs)


def _json_safe(value: Any) -> Any:
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
