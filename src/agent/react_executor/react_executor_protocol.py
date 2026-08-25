from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List
from uuid import uuid4


ACTION_TYPES = {
    "call_tool",
    "call_model",
    "ask_user",
    "retry_step",
    "fallback_to_model",
    "fallback_to_tool",
    "skip_step",
    "finish",
    "fail",
    "request_replan",
    "blocked",
    "cancel",
}

ACTION_TYPE_ALIASES = {
    "retry": "retry_step",
    "stop_success": "finish",
    "stop_failed": "fail",
}

ASK_TYPES = {"missing_info", "confirmation", "choice", "permission", "clarification"}

EXECUTION_STATUSES = {
    "pending",
    "running",
    "waiting_user",
    "completed",
    "failed",
    "partial_failed",
    "blocked",
    "cancelled",
    "request_replan",
}

REACT_TURN_STATUSES = {
    "pending",
    "running",
    "waiting_user",
    "completed",
    "failed",
    "blocked",
    "cancelled",
    "request_replan",
}

TASK_UNIT_STATUSES = {
    "pending",
    "running",
    "waiting_user",
    "completed",
    "failed",
    "skipped",
    "blocked",
    "cancelled",
}

STEP_STATUSES = {
    "pending",
    "running",
    "waiting_user",
    "completed",
    "failed",
    "skipped",
    "blocked",
    "cancelled",
    "retrying",
    "fallback_used",
}

EVENT_TYPES = {
    "message_delta",
    "progress_message",
    "thought_visible",
    "action_selected",
    "tool_started",
    "tool_finished",
    "tool_failed",
    "file_edited",
    "command_started",
    "command_finished",
    "model_step_started",
    "model_step_finished",
    "step_started",
    "step_completed",
    "step_failed",
    "confirmation_requested",
    "observation_created",
    "retry_scheduled",
    "retry_finished",
    "retry_exhausted",
    "fallback_started",
    "fallback_finished",
    "request_replan",
    "system_notice",
    "final_answer",
}

COMMAND_RISK_LEVELS = {"low", "medium", "high", "blocked", "unknown"}
OBSERVATION_MODES = {"minimal", "standard", "full"}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def normalize_action_type(action_type: str) -> str:
    normalized = ACTION_TYPE_ALIASES.get(action_type, action_type)
    if normalized not in ACTION_TYPES:
        raise ValueError(f"Unsupported action_type: {action_type}")
    return normalized


def clamp_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    if confidence < 0.0:
        return 0.0
    if confidence > 1.0:
        return 1.0
    return confidence


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


def _ensure_status(value: str, allowed: set[str], field_name: str) -> str:
    if value not in allowed:
        raise ValueError(f"Unsupported {field_name}: {value}")
    return value


@dataclass
class CommandAction:
    command: str
    cwd: str = "."
    purpose: str = ""
    risk_level: str = "unknown"
    requires_confirmation: bool = True
    expected_result: str = ""
    timeout_seconds: int = 30
    shell: str | None = None
    env_policy: str = "inherit_safe"
    network_required: bool = False
    writes_files: bool = False
    target_paths: List[str] = field(default_factory=list)
    destructive_risk: bool = False
    approval_scope: str | None = None

    def __post_init__(self) -> None:
        if self.risk_level not in COMMAND_RISK_LEVELS:
            self.risk_level = "unknown"
        self.timeout_seconds = max(int(self.timeout_seconds), 1)

    def to_dict(self) -> Dict[str, Any]:
        return _json_safe(self)


@dataclass
class ActionPacket:
    action_type: str
    packet_id: str = field(default_factory=lambda: new_id("action"))
    execution_id: str = ""
    plan_id: str = ""
    task_id: str | None = None
    step_id: str | None = None
    thought_summary: str = ""
    user_visible_message: str = ""
    action_target: str | None = None
    action_args: Dict[str, Any] = field(default_factory=dict)
    expected_observation: str = ""
    confidence: float = 0.0
    requires_confirmation: bool = False
    confirmation_type: str | None = None
    safety_notes: List[str] = field(default_factory=list)
    fallback_plan: Dict[str, Any] = field(default_factory=dict)
    request_replan_reason: str | None = None
    final_answer: str | None = None
    raw_model_output: Any | None = None

    def __post_init__(self) -> None:
        self.action_type = normalize_action_type(self.action_type)
        self.confidence = clamp_confidence(self.confidence)
        if self.confirmation_type is not None and self.confirmation_type not in ASK_TYPES:
            raise ValueError(f"Unsupported confirmation_type: {self.confirmation_type}")

    def to_dict(self) -> Dict[str, Any]:
        return _json_safe(self)


@dataclass
class ActionPacketParseResult:
    success: bool
    packet: ActionPacket | None = None
    errors: List[str] = field(default_factory=list)
    needs_repair: bool = False
    repair_prompt: str = ""
    raw_payload: Any = None
    raw_model_output: Any = None

    def to_dict(self) -> Dict[str, Any]:
        return _json_safe(self)


@dataclass
class ObservationPacket:
    execution_id: str
    plan_id: str
    action_type: str
    observation_id: str = field(default_factory=lambda: new_id("observation"))
    task_id: str | None = None
    step_id: str | None = None
    packet_id: str | None = None
    attempt: int = 1
    action_target: str | None = None
    tool_name: str | None = None
    input_args: Dict[str, Any] = field(default_factory=dict)
    success: bool = False
    data: Any = None
    message: str = ""
    error: str | None = None
    code: str | None = None
    raw_observation: Any = None
    model_consumable_observation: Any = None
    observation_mode: str | None = None
    data_summary: str | None = None
    included_fields: List[str] = field(default_factory=list)
    raw_ref: str | None = None
    artifact_ref: str | None = None
    started_at: str = field(default_factory=utc_now_iso)
    finished_at: str = field(default_factory=utc_now_iso)
    duration_ms: int = 0
    fallback_used: bool = False
    fallback_type: str | None = None
    checker_result: Dict[str, Any] = field(default_factory=dict)
    visible_to_user: bool = True

    def __post_init__(self) -> None:
        self.action_type = normalize_action_type(self.action_type)
        self.attempt = max(int(self.attempt), 1)
        self.duration_ms = max(int(self.duration_ms), 0)
        if self.observation_mode is not None and self.observation_mode not in OBSERVATION_MODES:
            self.observation_mode = "standard"
        self.included_fields = [str(field_name) for field_name in self.included_fields]

    def to_dict(self) -> Dict[str, Any]:
        return _json_safe(self)


def parse_action_packet(
    raw_output: Any,
    *,
    execution_id: str = "",
    plan_id: str = "",
    task_id: str | None = None,
    step_id: str | None = None,
    available_tools: List[str] | None = None,
    fallback_tools: List[str] | None = None,
    current_step_id: str | None = None,
    recent_failed_action_ids: List[str] | None = None,
    retry_attempts: int = 0,
    max_retries: int = 3,
) -> ActionPacketParseResult:
    try:
        payload = extract_action_packet_payload(raw_output)
    except ValueError as exc:
        errors = [str(exc)]
        return ActionPacketParseResult(
            success=False,
            errors=errors,
            needs_repair=True,
            repair_prompt=build_action_packet_repair_prompt(errors, raw_output),
            raw_model_output=raw_output,
        )

    errors = _base_action_packet_payload_errors(payload)
    if errors:
        return ActionPacketParseResult(
            success=False,
            errors=errors,
            needs_repair=True,
            repair_prompt=build_action_packet_repair_prompt(errors, raw_output),
            raw_payload=payload,
            raw_model_output=raw_output,
        )

    packet_kwargs = _action_packet_kwargs_from_payload(
        payload,
        execution_id=execution_id,
        plan_id=plan_id,
        task_id=task_id,
        step_id=step_id,
        raw_output=raw_output,
    )
    try:
        packet = ActionPacket(**packet_kwargs)
    except ValueError as exc:
        errors = [str(exc)]
        return ActionPacketParseResult(
            success=False,
            errors=errors,
            needs_repair=True,
            repair_prompt=build_action_packet_repair_prompt(errors, raw_output),
            raw_payload=payload,
            raw_model_output=raw_output,
        )

    errors = validate_action_packet(
        packet,
        available_tools=available_tools,
        fallback_tools=fallback_tools,
        current_step_id=current_step_id,
        recent_failed_action_ids=recent_failed_action_ids,
        retry_attempts=retry_attempts,
        max_retries=max_retries,
    )
    if errors:
        return ActionPacketParseResult(
            success=False,
            packet=packet,
            errors=errors,
            needs_repair=True,
            repair_prompt=build_action_packet_repair_prompt(errors, raw_output),
            raw_payload=payload,
            raw_model_output=raw_output,
        )

    return ActionPacketParseResult(
        success=True,
        packet=packet,
        raw_payload=payload,
        raw_model_output=raw_output,
    )


def extract_action_packet_payload(raw_output: Any) -> Dict[str, Any]:
    if isinstance(raw_output, dict):
        return raw_output
    if not isinstance(raw_output, str):
        raise ValueError("model output must be a dict or JSON string")

    text = raw_output.strip()
    if not text:
        raise ValueError("model output is empty")

    try:
        payload = json.loads(text)
        if isinstance(payload, dict):
            return payload
        raise ValueError("top-level JSON must be an object")
    except json.JSONDecodeError:
        pass

    fenced_match = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
    if fenced_match:
        fenced_text = fenced_match.group(1).strip()
        try:
            payload = json.loads(fenced_text)
            if isinstance(payload, dict):
                return payload
            raise ValueError("top-level fenced JSON must be an object")
        except json.JSONDecodeError:
            pass

    object_text = _first_json_object_text(text)
    if object_text is None:
        raise ValueError("no JSON object found in model output")
    try:
        payload = json.loads(object_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON object: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("top-level JSON must be an object")
    return payload


def validate_action_packet(
    packet: ActionPacket,
    *,
    available_tools: List[str] | None = None,
    fallback_tools: List[str] | None = None,
    current_step_id: str | None = None,
    recent_failed_action_ids: List[str] | None = None,
    retry_attempts: int = 0,
    max_retries: int = 3,
) -> List[str]:
    errors: List[str] = []
    action_args = packet.action_args
    fallback_plan = packet.fallback_plan
    action_type = packet.action_type

    if not isinstance(action_args, dict):
        errors.append("action_args must be an object")
    if not isinstance(fallback_plan, dict):
        errors.append("fallback_plan must be an object")
    if errors:
        return errors

    if packet.final_answer and action_type not in {"finish", "fail"}:
        errors.append("final_answer is only allowed for finish or fail")
    if packet.request_replan_reason and action_type != "request_replan":
        errors.append("request_replan_reason is only allowed for request_replan")

    if action_type == "call_tool":
        if not packet.action_target:
            errors.append("call_tool requires action_target")
        elif available_tools is not None and packet.action_target not in set(available_tools):
            errors.append(f"call_tool target is not available: {packet.action_target}")
    elif action_type == "call_model":
        if not _has_any_key(action_args, ["goal", "task", "prompt", "instruction"]):
            errors.append("call_model requires action_args.goal/task/prompt/instruction")
        if not _has_any_key(action_args, ["input_from", "input", "context"]):
            errors.append("call_model requires action_args.input_from/input/context")
        if not _has_any_key(action_args, ["output_requirements", "expected_output", "format"]):
            errors.append("call_model requires action_args.output_requirements/expected_output/format")
    elif action_type == "ask_user":
        ask_type = action_args.get("ask_type")
        if ask_type not in ASK_TYPES:
            errors.append("ask_user requires action_args.ask_type to be valid")
        if not _has_any_key(action_args, ["question", "message"]):
            errors.append("ask_user requires action_args.question or action_args.message")
    elif action_type == "retry_step":
        if retry_attempts >= max_retries:
            errors.append("retry_step exceeds max_retries")
        target_step_id = action_args.get("step_id") or packet.step_id
        target_action_id = action_args.get("packet_id") or action_args.get("action_id")
        recent_failed = set(recent_failed_action_ids or [])
        if current_step_id and target_step_id and target_step_id != current_step_id:
            errors.append("retry_step must target the current step")
        if not target_step_id and not target_action_id:
            errors.append("retry_step requires a step_id or failed action id")
        if target_action_id and recent_failed and target_action_id not in recent_failed:
            errors.append("retry_step action id is not in recent failed actions")
    elif action_type == "fallback_to_model":
        if not _has_any_key(action_args, ["fallback_reason"]) and not fallback_plan.get("reason"):
            errors.append("fallback_to_model requires fallback_reason")
    elif action_type == "fallback_to_tool":
        if not packet.action_target:
            errors.append("fallback_to_tool requires action_target")
        elif fallback_tools is not None and packet.action_target not in set(fallback_tools):
            errors.append(f"fallback_to_tool target is not available: {packet.action_target}")
        if not _has_any_key(action_args, ["fallback_reason"]) and not fallback_plan.get("reason"):
            errors.append("fallback_to_tool requires fallback_reason")
    elif action_type == "finish":
        if not _has_text(packet.final_answer):
            errors.append("finish requires final_answer")
    elif action_type == "fail":
        if not _has_text(packet.final_answer) and not _has_any_key(action_args, ["reason", "message", "error"]):
            errors.append("fail requires final_answer or failure reason")
    elif action_type == "request_replan":
        if not _has_text(packet.request_replan_reason):
            errors.append("request_replan requires request_replan_reason")
    elif action_type in {"blocked", "cancel"}:
        if not _has_text(packet.user_visible_message) and not _has_any_key(action_args, ["reason", "message"]):
            errors.append(f"{action_type} requires a user-visible reason")

    return errors


def build_action_packet_repair_prompt(errors: List[str], raw_output: Any) -> str:
    error_text = "\n".join(f"- {error}" for error in errors)
    raw_text = raw_output if isinstance(raw_output, str) else json.dumps(_json_safe(raw_output), ensure_ascii=False)
    return (
        "Your previous response was not a valid ActionPacket.\n"
        "Return only one strict JSON object. Do not include Markdown or explanation.\n"
        "Fix these errors:\n"
        f"{error_text}\n"
        "Previous response:\n"
        f"{raw_text}"
    )


def _base_action_packet_payload_errors(payload: Any) -> List[str]:
    if not isinstance(payload, dict):
        return ["ActionPacket payload must be an object"]
    errors: List[str] = []
    if "action_type" not in payload or not _has_text(payload.get("action_type")):
        errors.append("ActionPacket requires action_type")
    if "action_args" in payload and not isinstance(payload["action_args"], dict):
        errors.append("action_args must be an object")
    if "fallback_plan" in payload and not isinstance(payload["fallback_plan"], dict):
        errors.append("fallback_plan must be an object")
    if "safety_notes" in payload and not isinstance(payload["safety_notes"], list):
        errors.append("safety_notes must be an array")
    return errors


def _action_packet_kwargs_from_payload(
    payload: Dict[str, Any],
    *,
    execution_id: str,
    plan_id: str,
    task_id: str | None,
    step_id: str | None,
    raw_output: Any,
) -> Dict[str, Any]:
    field_names = {
        "packet_id",
        "execution_id",
        "plan_id",
        "task_id",
        "step_id",
        "thought_summary",
        "user_visible_message",
        "action_type",
        "action_target",
        "action_args",
        "expected_observation",
        "confidence",
        "requires_confirmation",
        "confirmation_type",
        "safety_notes",
        "fallback_plan",
        "request_replan_reason",
        "final_answer",
        "raw_model_output",
    }
    kwargs = {key: value for key, value in payload.items() if key in field_names}
    kwargs.setdefault("execution_id", execution_id)
    kwargs.setdefault("plan_id", plan_id)
    kwargs.setdefault("task_id", task_id)
    kwargs.setdefault("step_id", step_id)
    kwargs.setdefault("action_args", {})
    kwargs.setdefault("fallback_plan", {})
    kwargs.setdefault("safety_notes", [])
    kwargs["raw_model_output"] = raw_output
    return kwargs


def _first_json_object_text(text: str) -> str | None:
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def _has_any_key(values: Dict[str, Any], keys: List[str]) -> bool:
    return any(_has_value(values.get(key)) for key in keys)


def _has_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict, tuple, set)):
        return bool(value)
    return True


@dataclass
class ExecutionEvent:
    execution_id: str
    plan_id: str
    type: str
    message: str = ""
    event_id: str = field(default_factory=lambda: new_id("event"))
    task_id: str | None = None
    step_id: str | None = None
    timestamp: str = field(default_factory=utc_now_iso)
    visible_to_user: bool = True
    payload: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.type not in EVENT_TYPES:
            raise ValueError(f"Unsupported event type: {self.type}")

    def to_dict(self) -> Dict[str, Any]:
        return _json_safe(self)


@dataclass
class PendingConfirmation:
    execution_id: str
    plan_id: str
    confirmation_type: str
    confirmation_message: str
    pending_action: ActionPacket | Dict[str, Any]
    session_id: str | None = None
    packet_id: str | None = None
    confirmation_id: str | None = None
    call_id: str | None = None
    preview_hash: str | None = None
    preview_summary: str | None = None
    affected_resources: List[str] = field(default_factory=list)
    task_id: str | None = None
    step_id: str | None = None
    expires_at: str | None = None

    def __post_init__(self) -> None:
        if self.confirmation_type not in ASK_TYPES:
            raise ValueError(f"Unsupported confirmation_type: {self.confirmation_type}")
        self.affected_resources = [str(item) for item in self.affected_resources if str(item).strip()]

    def to_dict(self) -> Dict[str, Any]:
        return _json_safe(
            {
                "execution_id": self.execution_id,
                "plan_id": self.plan_id,
                "confirmation_type": self.confirmation_type,
                "confirmation_message": self.confirmation_message,
                "pending_action": _action_context_summary(self.pending_action),
                "session_id": self.session_id,
                "packet_id": self.packet_id,
                "confirmation_id": self.confirmation_id,
                "call_id": self.call_id,
                "preview_hash": self.preview_hash,
                "preview_summary": self.preview_summary,
                "affected_resources": list(self.affected_resources),
                "task_id": self.task_id,
                "step_id": self.step_id,
                "expires_at": self.expires_at,
            }
        )


def _action_context_summary(action: ActionPacket | Dict[str, Any] | None) -> Dict[str, Any] | None:
    if action is None:
        return None
    payload = action.to_dict() if hasattr(action, "to_dict") and callable(action.to_dict) else dict(action)
    return {
        "packet_id": payload.get("packet_id"),
        "action_type": payload.get("action_type"),
        "action_target": payload.get("action_target"),
        "task_id": payload.get("task_id"),
        "step_id": payload.get("step_id"),
        "success_criteria": payload.get("success_criteria"),
        "expected_observation": payload.get("expected_observation"),
        "confidence": payload.get("confidence"),
        "requires_confirmation": payload.get("requires_confirmation"),
        "confirmation_type": payload.get("confirmation_type"),
        "user_visible_message": payload.get("user_visible_message"),
    }


def _observation_context_summary(observation: ObservationPacket | Dict[str, Any] | None) -> Dict[str, Any] | None:
    if observation is None:
        return None
    payload = observation.to_dict() if hasattr(observation, "to_dict") and callable(observation.to_dict) else dict(observation)
    return {
        "observation_id": payload.get("observation_id"),
        "packet_id": payload.get("packet_id"),
        "action_type": payload.get("action_type"),
        "action_target": payload.get("action_target"),
        "task_id": payload.get("task_id"),
        "step_id": payload.get("step_id"),
        "success": payload.get("success"),
        "code": payload.get("code"),
        "message": payload.get("message"),
        "model_consumable_observation": payload.get("model_consumable_observation"),
    }


@dataclass
class ReActTurnState:
    turn_id: str = field(default_factory=lambda: new_id("turn"))
    execution_turn: int = 0
    step_turn: int = 0
    task_id: str | None = None
    step_id: str | None = None
    attempt: int = 1
    previous_action: ActionPacket | Dict[str, Any] | None = None
    previous_observation: ObservationPacket | Dict[str, Any] | None = None
    last_checker_result: Dict[str, Any] = field(default_factory=dict)
    status: str = "pending"
    thought_summary: str = ""
    user_visible_message: str = ""
    started_at: str = field(default_factory=utc_now_iso)
    finished_at: str | None = None

    def __post_init__(self) -> None:
        self.status = _ensure_status(self.status, REACT_TURN_STATUSES, "ReAct turn status")
        self.execution_turn = max(int(self.execution_turn or 0), 0)
        self.step_turn = max(int(self.step_turn or 0), 0)
        self.attempt = max(int(self.attempt or 1), 1)

    def finish(self, status: str, *, finished_at: str | None = None) -> None:
        self.status = _ensure_status(status, REACT_TURN_STATUSES, "ReAct turn status")
        self.finished_at = finished_at or utc_now_iso()

    def to_model_context(self) -> Dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "execution_turn": self.execution_turn,
            "step_turn": self.step_turn,
            "task_id": self.task_id,
            "step_id": self.step_id,
            "attempt": self.attempt,
            "status": self.status,
            "thought_summary": self.thought_summary,
            "user_visible_message": self.user_visible_message,
            "previous_action": _action_context_summary(self.previous_action),
            "previous_observation": _observation_context_summary(self.previous_observation),
            "last_checker_result": _json_safe(self.last_checker_result),
        }

    def to_dict(self) -> Dict[str, Any]:
        return _json_safe(self)


@dataclass
class ReActLoopState:
    execution_id: str
    plan_id: str
    status: str = "pending"
    execution_turn: int = 0
    step_turns: Dict[str, int] = field(default_factory=dict)
    current_turn_id: str | None = None
    current_task_id: str | None = None
    current_step_id: str | None = None
    previous_action: ActionPacket | Dict[str, Any] | None = None
    previous_observation: ObservationPacket | Dict[str, Any] | None = None
    last_checker_result: Dict[str, Any] = field(default_factory=dict)
    turns: List[ReActTurnState] = field(default_factory=list)
    max_execution_turns: int | None = None
    max_step_turns: int | None = None
    started_at: str = field(default_factory=utc_now_iso)
    finished_at: str | None = None

    def __post_init__(self) -> None:
        self.status = _ensure_status(self.status, EXECUTION_STATUSES, "ReAct loop status")
        self.execution_turn = max(int(self.execution_turn or 0), 0)
        normalized_step_turns: Dict[str, int] = {}
        for step_id, count in self.step_turns.items():
            normalized_step_turns[str(step_id)] = max(int(count or 0), 0)
        self.step_turns = normalized_step_turns

    def start_turn(
        self,
        *,
        task_id: str | None = None,
        step_id: str | None = None,
        attempt: int = 1,
        thought_summary: str = "",
        user_visible_message: str = "",
    ) -> ReActTurnState:
        self.execution_turn += 1
        step_turn = 0
        if step_id:
            step_key = str(step_id)
            step_turn = self.step_turns.get(step_key, 0) + 1
            self.step_turns[step_key] = step_turn
        turn = ReActTurnState(
            execution_turn=self.execution_turn,
            step_turn=step_turn,
            task_id=task_id,
            step_id=step_id,
            attempt=attempt,
            previous_action=self.previous_action,
            previous_observation=self.previous_observation,
            last_checker_result=dict(self.last_checker_result),
            status="running",
            thought_summary=thought_summary,
            user_visible_message=user_visible_message,
        )
        self.turns.append(turn)
        self.status = "running"
        self.current_turn_id = turn.turn_id
        self.current_task_id = task_id
        self.current_step_id = step_id
        return turn

    def record_action(self, action: ActionPacket | Dict[str, Any] | None) -> None:
        self.previous_action = action

    def record_observation(self, observation: ObservationPacket | Dict[str, Any] | None) -> None:
        self.previous_observation = observation

    def record_checker_result(self, checker_result: Dict[str, Any] | None) -> None:
        self.last_checker_result = dict(checker_result or {})

    def finish(self, status: str, *, finished_at: str | None = None) -> None:
        self.status = _ensure_status(status, EXECUTION_STATUSES, "ReAct loop status")
        self.finished_at = finished_at or utc_now_iso()

    def to_model_context(self, *, recent_turn_limit: int = 5) -> Dict[str, Any]:
        recent_turns = self.turns[-max(int(recent_turn_limit or 0), 0) :] if recent_turn_limit else []
        return {
            "execution_id": self.execution_id,
            "plan_id": self.plan_id,
            "status": self.status,
            "execution_turn": self.execution_turn,
            "current_turn_id": self.current_turn_id,
            "current_task_id": self.current_task_id,
            "current_step_id": self.current_step_id,
            "step_turns": dict(self.step_turns),
            "max_execution_turns": self.max_execution_turns,
            "max_step_turns": self.max_step_turns,
            "previous_action": _action_context_summary(self.previous_action),
            "previous_observation": _observation_context_summary(self.previous_observation),
            "last_checker_result": _json_safe(self.last_checker_result),
            "recent_turns": [turn.to_model_context() for turn in recent_turns],
        }

    def to_dict(self) -> Dict[str, Any]:
        return _json_safe(self)


@dataclass
class StepRuntimeState:
    step_id: str
    status: str = "pending"
    attempts: int = 0
    last_action_id: str | None = None
    last_observation_id: str | None = None
    output_key: str | None = None
    error_code: str | None = None
    message: str = ""

    def __post_init__(self) -> None:
        self.status = _ensure_status(self.status, STEP_STATUSES, "step status")
        self.attempts = max(int(self.attempts), 0)

    def to_dict(self) -> Dict[str, Any]:
        return _json_safe(self)


@dataclass
class TaskUnitRuntimeState:
    task_id: str
    status: str = "pending"
    step_statuses: Dict[str, str] = field(default_factory=dict)
    message: str = ""

    def __post_init__(self) -> None:
        self.status = _ensure_status(self.status, TASK_UNIT_STATUSES, "task unit status")
        for step_id, status in self.step_statuses.items():
            _ensure_status(status, STEP_STATUSES, f"status for step {step_id}")

    def to_dict(self) -> Dict[str, Any]:
        return _json_safe(self)


@dataclass
class ExecutionResult:
    execution_id: str
    plan_id: str
    status: str
    success: bool
    output: str = ""
    source_trace_id: str | None = None
    summary: str = ""
    task_statuses: Dict[str, str] = field(default_factory=dict)
    step_statuses: Dict[str, str] = field(default_factory=dict)
    observations: List[ObservationPacket | Dict[str, Any]] = field(default_factory=list)
    events: List[ExecutionEvent | Dict[str, Any]] = field(default_factory=list)
    failed_step_id: str | None = None
    error_code: str | None = None
    requires_user_input: bool = False
    user_input_request: str | None = None
    pending_confirmation: PendingConfirmation | Dict[str, Any] | None = None
    request_replan: bool = False
    replan_reason: str | None = None

    def __post_init__(self) -> None:
        self.status = _ensure_status(self.status, EXECUTION_STATUSES, "execution status")
        for task_id, status in self.task_statuses.items():
            _ensure_status(status, TASK_UNIT_STATUSES, f"status for task {task_id}")
        for step_id, status in self.step_statuses.items():
            _ensure_status(status, STEP_STATUSES, f"status for step {step_id}")

    def to_dict(self) -> Dict[str, Any]:
        return _json_safe(self)
