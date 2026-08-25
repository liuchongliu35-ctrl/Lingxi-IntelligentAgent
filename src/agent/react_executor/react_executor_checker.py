from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Dict, List

from src.agent.react_executor_protocol import ObservationPacket
from src.models.compat import ModelCallFailure, require_model_content


CHECKER_STATUSES = {
    "continue",
    "step_completed",
    "retry",
    "fallback_to_model",
    "fallback_to_tool",
    "ask_user",
    "request_replan",
    "fail",
}

USER_INPUT_CODES = {
    "confirmation_required",
    "confirmation_pending",
    "user_input_required",
}

REQUEST_REPLAN_CODES = {
    "request_replan",
}

SAFETY_VIOLATION_CODES = {
    "action_blocked",
    "command_blocked",
    "dangerous_command",
    "blocked_by_policy",
    "dangerous_operation",
    "permission_denied",
}

DEPENDENCY_FAILURE_CODES = {
    "missing_step",
    "plan_reference_error",
    "tool_input_ref_missing",
    "model_input_ref_missing",
}

VALIDATION_FAILURE_CODES = {
    "action_packet_invalid",
    "tool_argument_validation_failed",
    "missing_required_argument",
    "empty_command",
}

RESOURCE_UNAVAILABLE_CODES = {
    "tool_not_available",
    "tool_manager_unavailable",
    "model_manager_unavailable",
}

TIMEOUT_CODES = {
    "timeout",
    "command_timeout",
    "tool_timeout",
    "model_timeout",
}

RETRYABLE_CODES = {
    "temporary_network_error",
    "rate_limited",
    "tool_transient_error",
    "tool_execution_exception",
    "model_call_exception",
    "model_call_failed",
    "schema_invalid",
    "command_launch_failed",
    *TIMEOUT_CODES,
}

NON_RETRYABLE_CODES = {
    "action_cancelled",
    "action_failed",
    "confirmation_rejected",
    "file_not_found",
    "file_exists",
    "outside_workspace",
    "tool_not_found_after_repair",
    *USER_INPUT_CODES,
    *SAFETY_VIOLATION_CODES,
    *DEPENDENCY_FAILURE_CODES,
    *VALIDATION_FAILURE_CODES,
    *RESOURCE_UNAVAILABLE_CODES,
}

EMPTY_OUTPUT_CODE = "empty_output"
MAX_TURNS_REACHED_CODE = "max_turns_reached"
LLM_CHECKER_INVALID_OUTPUT_CODE = "llm_checker_invalid_output"
LLM_CHECKER_EXCEPTION_CODE = "llm_checker_exception"
LLM_CHECKER_UNAVAILABLE_CODE = "llm_checker_unavailable"


@dataclass
class CheckerResult:
    checker_status: str
    success: bool = False
    reason: str = ""
    code: str | None = None
    retryable: bool = False
    fallback_type: str | None = None
    fallback_tool: str | None = None
    request_replan: bool = False
    requires_user_input: bool = False
    step_status: str | None = None
    execution_status: str | None = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.checker_status not in CHECKER_STATUSES:
            raise ValueError(f"Unsupported checker_status: {self.checker_status}")

    def to_dict(self) -> Dict[str, Any]:
        return _json_safe(self)


class RuleChecker:
    """Deterministic engineering guard for ObservationPacket results."""

    def __init__(self, *, default_max_retries: int = 3, max_step_turns: int = 5, max_execution_turns: int = 20):
        self.default_max_retries = max(int(default_max_retries), 0)
        self.max_step_turns = max(int(max_step_turns), 1)
        self.max_execution_turns = max(int(max_execution_turns), 1)

    def check_observation(
        self,
        observation: ObservationPacket,
        *,
        step: Any | None = None,
        packet: Any | None = None,
        context: Any | None = None,
        step_state: Any | None = None,
        current_step_turn: int | None = None,
        current_execution_turn: int | None = None,
        max_step_turns: int | None = None,
        max_execution_turns: int | None = None,
    ) -> CheckerResult:
        step_turn = self._turn_count(observation, step_state, current_step_turn)
        execution_turn = self._execution_turn_count(context, current_execution_turn)
        step_limit = max_step_turns or self.max_step_turns
        execution_limit = max_execution_turns or self.max_execution_turns
        code = _normalized_code(observation.code)
        metadata = self._base_metadata(observation, step, step_turn, execution_turn, step_limit, execution_limit)

        if execution_turn >= execution_limit and not observation.success and observation.action_type != "finish":
            return self._fail(
                "Maximum execution turns reached.",
                code=MAX_TURNS_REACHED_CODE,
                metadata={**metadata, "limit_type": "execution"},
            )

        explicit = self._explicit_checker_result(observation, metadata)
        if explicit is not None:
            return explicit

        if self._is_user_input_required(observation, code):
            return CheckerResult(
                checker_status="ask_user",
                success=False,
                reason=observation.message or observation.error or "User input is required.",
                code=observation.code,
                requires_user_input=True,
                step_status="waiting_user",
                metadata=metadata,
            )

        if self._is_request_replan(observation, code):
            return CheckerResult(
                checker_status="request_replan",
                success=False,
                reason=observation.message or observation.error or "Replan requested.",
                code=observation.code,
                request_replan=True,
                execution_status="request_replan",
                metadata=metadata,
            )

        if self._is_safety_violation(code):
            return self._fail(
                observation.error or observation.message or "Action was blocked by safety policy.",
                code=observation.code,
                step_status="blocked",
                metadata={**metadata, "failure_class": "safety_violation"},
            )

        if observation.success:
            if not _has_observation_output(observation):
                return self._empty_output_result(observation, step, step_state, step_turn, step_limit, metadata)
            step_status = "skipped" if code == "step_skipped" else "completed"
            return CheckerResult(
                checker_status="step_completed",
                success=True,
                reason=observation.message or "Step completed.",
                code=observation.code,
                step_status=step_status,
                execution_status="completed" if observation.action_type == "finish" else None,
                metadata=metadata,
            )

        if step_turn >= step_limit:
            return self._fail(
                "Maximum step turns reached.",
                code=MAX_TURNS_REACHED_CODE,
                metadata={**metadata, "limit_type": "step"},
            )

        return self._failure_result(observation, step, step_state, step_turn, metadata)

    def classify_code(self, code: str | None) -> str:
        return classify_tool_result_code(code)

    def _explicit_checker_result(self, observation: ObservationPacket, metadata: Dict[str, Any]) -> CheckerResult | None:
        payload = observation.checker_result or {}
        execution_status = payload.get("execution_status")
        step_status = payload.get("step_status")
        if execution_status == "request_replan":
            return CheckerResult(
                checker_status="request_replan",
                success=False,
                reason=observation.message or "Replan requested.",
                code=observation.code,
                request_replan=True,
                execution_status="request_replan",
                metadata={**metadata, "explicit_checker_result": payload},
            )
        if execution_status == "completed":
            return CheckerResult(
                checker_status="step_completed",
                success=True,
                reason=observation.message or "Execution completed.",
                code=observation.code,
                step_status="completed",
                execution_status="completed",
                metadata={**metadata, "explicit_checker_result": payload},
            )
        if execution_status == "failed":
            return self._fail(
                observation.error or observation.message or "Execution failed.",
                code=observation.code,
                execution_status="failed",
                metadata={**metadata, "explicit_checker_result": payload},
            )
        if step_status == "waiting_user":
            return CheckerResult(
                checker_status="ask_user",
                success=False,
                reason=observation.message or observation.error or "User input is required.",
                code=observation.code,
                requires_user_input=True,
                step_status="waiting_user",
                metadata={**metadata, "explicit_checker_result": payload},
            )
        if step_status in {"blocked", "cancelled"}:
            return self._fail(
                observation.error or observation.message or f"Step {step_status}.",
                code=observation.code,
                step_status=step_status,
                metadata={**metadata, "explicit_checker_result": payload},
            )
        return None

    def _failure_result(
        self,
        observation: ObservationPacket,
        step: Any | None,
        step_state: Any | None,
        step_turn: int,
        metadata: Dict[str, Any],
    ) -> CheckerResult:
        code = _normalized_code(observation.code)
        failure_class = classify_tool_result_code(code)
        reason = observation.error or observation.message or "Action failed."
        enriched_metadata = {**metadata, "failure_class": failure_class}

        if failure_class == "dependency_failure":
            if self._wants_replan(step):
                return self._request_replan(reason, observation.code, enriched_metadata)
            return self._fail(reason, code=observation.code, metadata=enriched_metadata)

        if self._wants_continue(step):
            return CheckerResult(
                checker_status="continue",
                success=False,
                reason=reason,
                code=observation.code,
                metadata={**enriched_metadata, "on_failure": "continue"},
            )

        if self._can_retry(observation, step, step_state, step_turn, failure_class):
            return CheckerResult(
                checker_status="retry",
                success=False,
                reason=reason,
                code=observation.code,
                retryable=True,
                step_status="retrying",
                metadata=enriched_metadata,
            )

        fallback = self._fallback_result(observation, step, reason, enriched_metadata)
        if fallback is not None:
            return fallback

        if self._wants_replan(step) or failure_class == "resource_unavailable":
            return self._request_replan(reason, observation.code, enriched_metadata)

        return self._fail(reason, code=observation.code, metadata=enriched_metadata)

    def _empty_output_result(
        self,
        observation: ObservationPacket,
        step: Any | None,
        step_state: Any | None,
        step_turn: int,
        step_limit: int,
        metadata: Dict[str, Any],
    ) -> CheckerResult:
        reason = "Action succeeded but produced an empty observation."
        if step_turn >= step_limit:
            return self._fail(reason, code=EMPTY_OUTPUT_CODE, metadata={**metadata, "failure_class": "empty_output"})
        if self._can_retry(observation, step, step_state, step_turn, "empty_output"):
            return CheckerResult(
                checker_status="retry",
                success=False,
                reason=reason,
                code=EMPTY_OUTPUT_CODE,
                retryable=True,
                step_status="retrying",
                metadata={**metadata, "failure_class": "empty_output"},
            )
        fallback = self._fallback_result(observation, step, reason, {**metadata, "failure_class": "empty_output"})
        if fallback is not None:
            return fallback
        return self._fail(reason, code=EMPTY_OUTPUT_CODE, metadata={**metadata, "failure_class": "empty_output"})

    def _fallback_result(
        self,
        observation: ObservationPacket,
        step: Any | None,
        reason: str,
        metadata: Dict[str, Any],
    ) -> CheckerResult | None:
        fallback_tools = [str(tool) for tool in list(getattr(step, "fallback_tools", []) or []) if tool]
        on_failure = str(getattr(step, "on_failure", "") or "").lower()
        if fallback_tools:
            return CheckerResult(
                checker_status="fallback_to_tool",
                success=False,
                reason=reason,
                code=observation.code,
                fallback_type="tool",
                fallback_tool=fallback_tools[0],
                step_status="fallback_used",
                metadata={**metadata, "fallback_tools": fallback_tools},
            )
        if getattr(step, "allow_model_reasoning", False) or on_failure in {"fallback_to_model", "fallback_model", "model", "fallback"}:
            return CheckerResult(
                checker_status="fallback_to_model",
                success=False,
                reason=reason,
                code=observation.code,
                fallback_type="model",
                step_status="fallback_used",
                metadata=metadata,
            )
        return None

    def _can_retry(
        self,
        observation: ObservationPacket,
        step: Any | None,
        step_state: Any | None,
        step_turn: int,
        failure_class: str,
    ) -> bool:
        code = _normalized_code(observation.code)
        if code in NON_RETRYABLE_CODES or failure_class in {"safety_violation", "dependency_failure", "validation_failure", "user_input"}:
            return False
        max_retries = max(int(getattr(step, "max_retries", self.default_max_retries) or 0), 0)
        attempts_so_far = max(step_turn, int(getattr(step_state, "attempts", 0) or 0))
        retries_used = max(attempts_so_far - 1, 0)
        retryable_by_step = bool(getattr(step, "retryable", False))
        retryable_by_code = failure_class in {"retryable", "timeout", "unknown_failure"}
        return (retryable_by_step or retryable_by_code) and retries_used < max_retries

    def _base_metadata(
        self,
        observation: ObservationPacket,
        step: Any | None,
        step_turn: int,
        execution_turn: int,
        step_limit: int,
        execution_limit: int,
    ) -> Dict[str, Any]:
        return {
            "observation_id": observation.observation_id,
            "packet_id": observation.packet_id,
            "action_type": observation.action_type,
            "action_target": observation.action_target,
            "tool_name": observation.tool_name,
            "step_id": observation.step_id,
            "attempt": observation.attempt,
            "step_turn": step_turn,
            "execution_turn": execution_turn,
            "max_step_turns": step_limit,
            "max_execution_turns": execution_limit,
            "step_on_failure": getattr(step, "on_failure", None),
            "step_retryable": getattr(step, "retryable", None),
            "step_max_retries": getattr(step, "max_retries", None),
        }

    def _turn_count(self, observation: ObservationPacket, step_state: Any | None, current_step_turn: int | None) -> int:
        values = [observation.attempt, current_step_turn, getattr(step_state, "attempts", None)]
        return max(int(value or 0) for value in values)

    def _execution_turn_count(self, context: Any | None, current_execution_turn: int | None) -> int:
        if current_execution_turn is not None:
            return max(int(current_execution_turn), 0)
        observations = getattr(getattr(context, "observation_store", None), "observations", None)
        if isinstance(observations, list):
            return len(observations)
        return 0

    def _is_user_input_required(self, observation: ObservationPacket, code: str | None) -> bool:
        return code in USER_INPUT_CODES or bool(_as_dict(observation.model_consumable_observation).get("requires_user_input"))

    def _is_request_replan(self, observation: ObservationPacket, code: str | None) -> bool:
        if code in REQUEST_REPLAN_CODES or observation.action_type == "request_replan":
            return True
        data = _as_dict(observation.data)
        model_value = _as_dict(observation.model_consumable_observation)
        return bool(data.get("request_replan") or model_value.get("request_replan"))

    def _is_safety_violation(self, code: str | None) -> bool:
        return code in SAFETY_VIOLATION_CODES

    def _wants_continue(self, step: Any | None) -> bool:
        return str(getattr(step, "on_failure", "") or "").lower() == "continue"

    def _wants_replan(self, step: Any | None) -> bool:
        return str(getattr(step, "on_failure", "") or "").lower() in {"replan", "request_replan"}

    def _request_replan(self, reason: str, code: str | None, metadata: Dict[str, Any]) -> CheckerResult:
        return CheckerResult(
            checker_status="request_replan",
            success=False,
            reason=reason,
            code=code,
            request_replan=True,
            execution_status="request_replan",
            metadata=metadata,
        )

    def _fail(
        self,
        reason: str,
        *,
        code: str | None,
        step_status: str = "failed",
        execution_status: str | None = None,
        metadata: Dict[str, Any],
    ) -> CheckerResult:
        return CheckerResult(
            checker_status="fail",
            success=False,
            reason=reason,
            code=code,
            step_status=step_status,
            execution_status=execution_status,
            metadata=metadata,
        )


class LLMChecker:
    """Optional structured LLM checker for expected-observation quality checks."""

    def __init__(self, model_manager: Any | None = None, *, enabled: bool = True):
        self.model_manager = model_manager
        self.enabled = enabled

    def check_observation(
        self,
        observation: ObservationPacket,
        *,
        step: Any | None = None,
        rule_result: CheckerResult | None = None,
    ) -> CheckerResult:
        if not self.enabled or self.model_manager is None or not hasattr(self.model_manager, "generate"):
            return CheckerResult(
                checker_status="continue",
                success=True,
                reason="LLM checker is not configured.",
                code=LLM_CHECKER_UNAVAILABLE_CODE,
                metadata={"llm_checker_used": False},
            )

        prompt = build_llm_checker_prompt(observation, step=step, rule_result=rule_result)
        try:
            raw_output = require_model_content(self.model_manager.generate(prompt))
            payload = _extract_json_object(raw_output)
            status = str(payload.get("checker_status") or "")
            if status not in CHECKER_STATUSES:
                return CheckerResult(
                    checker_status="fail",
                    success=False,
                    reason=f"LLM checker returned invalid checker_status: {status}",
                    code=LLM_CHECKER_INVALID_OUTPUT_CODE,
                    metadata={"raw_output": raw_output, "llm_checker_used": True},
                )
            return CheckerResult(
                checker_status=status,
                success=bool(payload.get("success", status in {"continue", "step_completed"})),
                reason=str(payload.get("reason") or ""),
                code=payload.get("code"),
                retryable=bool(payload.get("retryable", False)),
                fallback_type=payload.get("fallback_type"),
                fallback_tool=payload.get("fallback_tool"),
                request_replan=bool(payload.get("request_replan", status == "request_replan")),
                requires_user_input=bool(payload.get("requires_user_input", status == "ask_user")),
                step_status=payload.get("step_status"),
                execution_status=payload.get("execution_status"),
                metadata={
                    "llm_checker_used": True,
                    "raw_output": raw_output,
                    **_as_dict(payload.get("metadata")),
                },
            )
        except ModelCallFailure as failure:
            return CheckerResult(
                checker_status="fail",
                success=False,
                reason=failure.result.error or "model call failed",
                code=failure.result.code,
                metadata={"llm_checker_used": True, "raw_output": failure.result.to_dict()},
            )
        except Exception as exc:
            return CheckerResult(
                checker_status="fail",
                success=False,
                reason=str(exc),
                code=LLM_CHECKER_EXCEPTION_CODE,
                metadata={"llm_checker_used": True},
            )


class ReActChecker:
    """Facade that runs rule checking and optionally asks an LLM checker."""

    def __init__(
        self,
        *,
        rule_checker: RuleChecker | None = None,
        llm_checker: LLMChecker | None = None,
        enable_llm_checker: bool = False,
    ):
        self.rule_checker = rule_checker or RuleChecker()
        self.llm_checker = llm_checker
        self.enable_llm_checker = enable_llm_checker

    def check_observation(self, observation: ObservationPacket, **kwargs: Any) -> CheckerResult:
        rule_result = self.rule_checker.check_observation(observation, **kwargs)
        if not self._should_run_llm_checker(rule_result, kwargs.get("step")):
            return rule_result
        llm_result = self.llm_checker.check_observation(observation, step=kwargs.get("step"), rule_result=rule_result)
        if llm_result.code in {LLM_CHECKER_UNAVAILABLE_CODE, LLM_CHECKER_INVALID_OUTPUT_CODE, LLM_CHECKER_EXCEPTION_CODE}:
            rule_result.metadata["llm_checker_result"] = llm_result.to_dict()
            return rule_result
        return llm_result

    def _should_run_llm_checker(self, rule_result: CheckerResult, step: Any | None) -> bool:
        if not self.enable_llm_checker or self.llm_checker is None:
            return False
        if rule_result.checker_status not in {"continue", "step_completed"}:
            return False
        return bool(str(getattr(step, "expected_output", "") or "").strip())


def build_llm_checker_prompt(
    observation: ObservationPacket,
    *,
    step: Any | None = None,
    rule_result: CheckerResult | None = None,
) -> str:
    payload = {
        "instruction": (
            "Judge whether the Observation satisfies the PlanStep expected output. "
            "Return only one strict JSON object."
        ),
        "allowed_checker_statuses": sorted(CHECKER_STATUSES),
        "required_schema": {
            "checker_status": "continue|step_completed|retry|fallback_to_model|fallback_to_tool|ask_user|request_replan|fail",
            "success": True,
            "reason": "short engineering reason",
            "code": None,
            "retryable": False,
            "fallback_type": None,
            "fallback_tool": None,
            "request_replan": False,
            "requires_user_input": False,
            "step_status": "completed|failed|waiting_user|retrying|fallback_used|null",
            "execution_status": None,
            "metadata": {},
        },
        "step": _to_dict(step),
        "observation": observation.to_dict(),
        "rule_result": rule_result.to_dict() if rule_result is not None else None,
    }
    return json.dumps(payload, ensure_ascii=False)


def classify_tool_result_code(code: str | None) -> str:
    normalized = _normalized_code(code)
    if normalized is None:
        return "none"
    if normalized in USER_INPUT_CODES:
        return "user_input"
    if normalized in REQUEST_REPLAN_CODES:
        return "request_replan"
    if normalized in SAFETY_VIOLATION_CODES:
        return "safety_violation"
    if normalized in DEPENDENCY_FAILURE_CODES:
        return "dependency_failure"
    if normalized in VALIDATION_FAILURE_CODES:
        return "validation_failure"
    if normalized in RESOURCE_UNAVAILABLE_CODES:
        return "resource_unavailable"
    if normalized in TIMEOUT_CODES or normalized.endswith("_timeout") or "timeout" in normalized:
        return "timeout"
    if normalized in RETRYABLE_CODES:
        return "retryable"
    if normalized in NON_RETRYABLE_CODES:
        return "non_retryable"
    return "unknown_failure"


def _has_observation_output(observation: ObservationPacket) -> bool:
    return _has_value(observation.model_consumable_observation) or _has_value(observation.data) or _has_value(observation.message)


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict, tuple, set)):
        return bool(value)
    return True


def _normalized_code(code: str | None) -> str | None:
    if code is None:
        return None
    text = str(code).strip().lower()
    return text or None


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _to_dict(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return value.to_dict()
    return _json_safe(value)


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


def _extract_json_object(raw_output: Any) -> Dict[str, Any]:
    if isinstance(raw_output, dict):
        return raw_output
    if not isinstance(raw_output, str):
        raise ValueError("LLM checker output must be a dict or JSON string")
    text = raw_output.strip()
    if not text:
        raise ValueError("LLM checker output is empty")
    try:
        payload = json.loads(text)
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        pass
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        payload = json.loads(fenced.group(1).strip())
        if isinstance(payload, dict):
            return payload
    raise ValueError("LLM checker output must contain one JSON object")
