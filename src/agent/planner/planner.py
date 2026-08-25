from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List
from uuid import uuid4

from src.agent.planner_config import PlannerConfig, load_planner_config
from src.models.compat import ModelCallFailure, require_model_content
from src.models.protocol import ModelCallResult, StructuredModelResult


PLAN_MODES = {
    "micro",
    "meso",
    "meso_advanced",
    "macro",
    "blocked",
    "clarify",
    "confirm",
    "missing_tools",
    "chat",
}
PLANNING_STRATEGIES = {
    "policy_rule",
    "rule_template",
    "llm_planner",
    "llm_repaired",
    "fallback_rule",
    "fallback_model_only",
    "invalid",
}
TASK_UNIT_STATUSES = {
    "pending",
    "running",
    "completed",
    "failed",
    "skipped",
    "blocked",
    "waiting_user",
}
PLAN_VALIDATION_STATUSES = {"valid", "repaired", "invalid", "not_required"}
STRUCTURED_JSON_FAILURE_CODES = {"invalid_json", "schema_invalid", "json_repair_failed"}


def _new_plan_id() -> str:
    return f"plan_{uuid4().hex[:12]}"


@dataclass
class PlanStep:
    id: str
    description: str
    tool_name: str | None = None
    args: Dict[str, Any] = field(default_factory=dict)
    expected_output: str = ""
    task_id: str = "task_1"
    step_type: str = "tool"
    depends_on: List[str] = field(default_factory=list)
    input_from: List[str] = field(default_factory=list)
    output_key: str | None = None
    requires_confirmation: bool = False
    confirmation_reason: str | None = None
    on_failure: str = "stop"
    retryable: bool = False
    max_retries: int = 3
    fallback_tools: List[str] = field(default_factory=list)
    allow_model_reasoning: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TaskUnit:
    id: str
    title: str
    description: str = ""
    intent_refs: List[str] = field(default_factory=list)
    task_type: str = "qa"
    status: str = "pending"
    depends_on: List[str] = field(default_factory=list)
    step_ids: List[str] = field(default_factory=list)
    expected_outcome: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TaskPlan:
    goal: str
    mode: str
    steps: List[PlanStep] = field(default_factory=list)
    plan_id: str = field(default_factory=_new_plan_id)
    source_trace_id: str | None = None
    task_type: str = "qa"
    execution_strategy: str = "micro"
    planning_strategy: str = "rule_template"
    can_execute: bool = True
    risk_policy: str = "allow"
    required_tools: List[str] = field(default_factory=list)
    available_tools: List[str] = field(default_factory=list)
    missing_tools: List[str] = field(default_factory=list)
    task_units: List[TaskUnit] = field(default_factory=list)
    plan_validation_status: str = "valid"
    plan_validation_notes: List[str] = field(default_factory=list)
    added_steps_reason: List[str] = field(default_factory=list)
    user_facing_summary: str = ""
    raw_planner_trace: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class Planner:
    """Create deterministic baseline plans before LLM planning is added."""

    def __init__(self, planner_config: PlannerConfig | None = None, model_manager: Any | None = None):
        self.config = planner_config or load_planner_config()
        self.model_manager = model_manager

    def create_plan(self, user_input: str, task: Any) -> TaskPlan:
        plan = self._create_plan(user_input, task)
        self._write_log(user_input, task, plan)
        return plan

    def _create_plan(self, user_input: str, task: Any) -> TaskPlan:
        if getattr(task, "action_policy", "allow") == "block":
            return self._blocked_plan(user_input, task)
        if getattr(task, "requires_clarification", False):
            return self._clarify_plan(user_input, task)
        if getattr(task, "requires_confirmation", False) or getattr(task, "action_policy", "allow") == "confirm":
            return self._confirm_plan(user_input, task)
        if getattr(task, "tool_strategy", None) == "blocked_missing_tools":
            return self._missing_tools_plan(user_input, task)
        if getattr(task, "mode", "solo") == "chat":
            return self._chat_plan(user_input, task)
        rule_plan = self._rule_template_plan(user_input, task)
        if rule_plan is not None:
            return rule_plan
        llm_plan = self._llm_planner_plan(user_input, task)
        if llm_plan is not None:
            return llm_plan
        mode = task.execution_strategy
        if mode == "micro":
            return self._micro_plan(user_input, task)
        if mode == "macro":
            return self._build_plan(
                user_input,
                task,
                mode="macro",
                planning_strategy="policy_rule",
                can_execute=False,
                steps=[
                    self._step(
                        "step_1",
                        "Ask the user for missing goal, scope, input material, or output format.",
                        task_id="task_1",
                        step_type="clarify",
                        expected_output="Clarification question",
                    )
                ],
                task_title="Clarify ambiguous task",
                task_status="waiting_user",
                plan_validation_status="not_required",
                user_facing_summary="这个需求目前还不够明确，需要先澄清。",
            )
        return self._meso_plan(user_input, task)

    def _blocked_plan(self, user_input: str, task: Any) -> TaskPlan:
        reason = getattr(task, "confirmation_reason", None) or "blocked_risk"
        return self._build_plan(
            user_input,
            task,
            mode="blocked",
            planning_strategy="policy_rule",
            can_execute=False,
            steps=[
                self._step(
                    "step_1",
                    "Reject a blocked high-risk request.",
                    task_id="task_1",
                    step_type="block",
                    args={"reason": reason, "risk_flags": list(getattr(task, "risk_flags", []) or [])},
                    expected_output="Blocked request message",
                    metadata={"policy": "block"},
                )
            ],
            task_title="Blocked high-risk request",
            task_status="blocked",
            plan_validation_status="not_required",
            plan_validation_notes=["policy_rule:block prevents normal planning"],
            user_facing_summary="这个请求包含高风险操作，当前不会执行。",
        )

    def _clarify_plan(self, user_input: str, task: Any) -> TaskPlan:
        questions = list(getattr(task, "clarification_questions", []) or [])
        return self._build_plan(
            user_input,
            task,
            mode="clarify",
            planning_strategy="policy_rule",
            can_execute=False,
            steps=[
                self._step(
                    "step_1",
                    "Ask the user to provide missing parameters before execution.",
                    task_id="task_1",
                    step_type="clarify",
                    args={
                        "questions": questions,
                        "missing_parameters": list(getattr(task, "missing_parameters", []) or []),
                    },
                    expected_output="Clarification questions",
                    metadata={"policy": "clarify"},
                )
            ],
            task_title="Clarify missing task information",
            task_status="waiting_user",
            plan_validation_status="not_required",
            plan_validation_notes=["policy_rule:clarification required before execution"],
            user_facing_summary="这个请求还缺少必要信息，需要先补充后再继续。",
        )

    def _confirm_plan(self, user_input: str, task: Any) -> TaskPlan:
        confirmation_reason = getattr(task, "confirmation_reason", None) or "risky_action"
        return self._build_plan(
            user_input,
            task,
            mode="confirm",
            planning_strategy="policy_rule",
            can_execute=False,
            steps=[
                self._step(
                    "step_1",
                    "Ask the user to confirm a risky action before execution.",
                    task_id="task_1",
                    step_type="confirm",
                    args={"reason": confirmation_reason, "risk_flags": list(getattr(task, "risk_flags", []) or [])},
                    expected_output="Confirmation request",
                    requires_confirmation=True,
                    confirmation_reason=confirmation_reason,
                    metadata={"policy": "confirm"},
                )
            ],
            task_title="Confirm risky action",
            task_status="waiting_user",
            plan_validation_status="not_required",
            plan_validation_notes=["policy_rule:confirmation required before execution"],
            user_facing_summary="这个请求需要确认后才能继续执行。",
        )

    def _missing_tools_plan(self, user_input: str, task: Any) -> TaskPlan:
        missing_tools = list(getattr(task, "missing_tools", []) or [])
        return self._build_plan(
            user_input,
            task,
            mode="missing_tools",
            planning_strategy="policy_rule",
            can_execute=False,
            steps=[
                self._step(
                    "step_1",
                    "Explain missing tools and provide a non-executing fallback.",
                    task_id="task_1",
                    step_type="respond",
                    args={"missing_tools": missing_tools},
                    expected_output="Missing tools message",
                    allow_model_reasoning=True,
                    metadata={"policy": "missing_tools"},
                )
            ],
            task_title="Explain missing tools",
            task_status="blocked",
            plan_validation_status="not_required",
            plan_validation_notes=["policy_rule:missing tools prevent executable planning"],
            user_facing_summary="当前缺少完成该任务所需的工具。",
        )

    def _chat_plan(self, user_input: str, task: Any) -> TaskPlan:
        return self._build_plan(
            user_input,
            task,
            mode="chat",
            planning_strategy="policy_rule",
            can_execute=True,
            steps=[
                self._step(
                    "step_1",
                    "Generate guidance or an answer without executing tools.",
                    task_id="task_1",
                    step_type="respond",
                    expected_output="Guidance response",
                    allow_model_reasoning=True,
                    metadata={"policy": "chat"},
                )
            ],
            task_title="Generate guidance response",
            plan_validation_status="not_required",
            plan_validation_notes=["policy_rule:chat mode uses model-only response planning"],
            user_facing_summary="将以回答或指导形式处理，不执行工具动作。",
        )

    def _rule_template_plan(self, user_input: str, task: Any) -> TaskPlan | None:
        intents = self._intent_sequence(task)
        if not intents:
            return None

        if self._should_split_by_file(intents, task):
            return self._multi_file_rule_plan(user_input, task, intents)

        if intents == ["calculate"]:
            return self._micro_plan(user_input, task)

        if self._has_intents(intents, ["search", "summarize", "write_file"]):
            return self._pipeline_plan(
                user_input,
                task,
                intents,
                task_title="Search, summarize, and write result",
                steps=[
                    self._search_step("step_1", task_id="task_1", task=task, user_input=user_input),
                    self._summarize_step("step_2", task_id="task_1", depends_on=["step_1"], input_from=["step_1"]),
                    self._write_file_step("step_3", task_id="task_1", task=task, depends_on=["step_2"], input_from=["step_2"]),
                ],
                added_steps_reason=["rule_template:search_summarize_write_file"],
            )

        if self._has_intents(intents, ["search", "summarize"]):
            return self._pipeline_plan(
                user_input,
                task,
                intents,
                task_title="Search and summarize information",
                steps=[
                    self._search_step("step_1", task_id="task_1", task=task, user_input=user_input),
                    self._summarize_step("step_2", task_id="task_1", depends_on=["step_1"], input_from=["step_1"]),
                ],
                added_steps_reason=["rule_template:search_summarize"],
            )

        if self._has_intents(intents, ["read_file", "extract", "write_file"]):
            return self._pipeline_plan(
                user_input,
                task,
                intents,
                task_title="Read, extract, and write result",
                steps=[
                    self._read_file_step("step_1", task_id="task_1", task=task),
                    self._extract_step("step_2", task_id="task_1", depends_on=["step_1"], input_from=["step_1"]),
                    self._write_file_step("step_3", task_id="task_1", task=task, depends_on=["step_2"], input_from=["step_2"]),
                ],
                added_steps_reason=["rule_template:read_file_extract_write_file"],
            )

        if self._has_intents(intents, ["read_file", "summarize"]):
            return self._pipeline_plan(
                user_input,
                task,
                intents,
                task_title="Read and summarize file",
                steps=[
                    self._read_file_step("step_1", task_id="task_1", task=task),
                    self._summarize_step("step_2", task_id="task_1", depends_on=["step_1"], input_from=["step_1"]),
                ],
                added_steps_reason=["rule_template:read_file_summarize"],
            )

        if self._has_intents(intents, ["translate", "write_file"]):
            return self._pipeline_plan(
                user_input,
                task,
                intents,
                task_title="Translate and write result",
                steps=[
                    self._translate_step("step_1", task_id="task_1", task=task, user_input=user_input),
                    self._write_file_step("step_2", task_id="task_1", task=task, depends_on=["step_1"], input_from=["step_1"]),
                ],
                added_steps_reason=["rule_template:translate_write_file"],
            )

        if intents == ["read_file"]:
            return self._pipeline_plan(
                user_input,
                task,
                intents,
                task_title="Read file",
                steps=[self._read_file_step("step_1", task_id="task_1", task=task)],
                added_steps_reason=["rule_template:read_file"],
            )

        if intents == ["translate"]:
            return self._pipeline_plan(
                user_input,
                task,
                intents,
                task_title="Translate content",
                steps=[self._translate_step("step_1", task_id="task_1", task=task, user_input=user_input)],
                added_steps_reason=["rule_template:translate"],
            )

        if "convert_format" in intents:
            return self._convert_format_plan(user_input, task, intents)

        if any(intent in intents for intent in {"design_project", "debug_code", "run_test", "deploy_project"}):
            return self._software_engineering_plan(user_input, task, intents)

        return None

    def _llm_planner_plan(self, user_input: str, task: Any) -> TaskPlan | None:
        if not self.config.enable_llm_planner or self.model_manager is None:
            return None
        prompt = self._build_llm_planner_prompt(user_input, task)
        last_error = "unknown_llm_planner_error"
        try:
            for attempt in range(self.config.max_llm_repair_attempts + 1):
                try:
                    payload, response = self._llm_planner_json_payload(prompt)
                    plan = self._task_plan_from_llm_payload(user_input, task, payload, raw_response=response)
                except ModelCallFailure as failure:
                    return self._llm_unavailable_fallback_plan(
                        user_input,
                        task,
                        failure.result.error or "model call failed",
                    )
                except Exception as exc:
                    last_error = str(exc)
                    if attempt >= self.config.max_llm_repair_attempts:
                        return self._llm_unavailable_fallback_plan(user_input, task, last_error)
                    prompt = self._build_llm_repair_prompt(user_input, task, last_error, None)
                    continue

                if plan.plan_validation_status != "invalid":
                    if attempt > 0:
                        plan.planning_strategy = "llm_repaired"
                        plan.plan_validation_status = "repaired"
                        plan.plan_validation_notes.append(f"llm_planner repaired after {attempt} attempt(s)")
                        plan.raw_planner_trace.append(f"llm_repair_attempts={attempt}")
                    return plan

                last_error = "; ".join(plan.plan_validation_notes)
                if attempt >= self.config.max_llm_repair_attempts:
                    plan.planning_strategy = "invalid"
                    plan.raw_planner_trace.append(f"llm_repair_attempts={attempt}")
                    plan.raw_planner_trace.append("llm_planner_status=invalid_after_repair")
                    return plan
                prompt = self._build_llm_repair_prompt(user_input, task, last_error, plan)
            return self._llm_unavailable_fallback_plan(user_input, task, last_error)
        except Exception as exc:
            return self._llm_unavailable_fallback_plan(user_input, task, str(exc))

    def _build_llm_planner_prompt(self, user_input: str, task: Any) -> str:
        prompt_config = self.config.llm_planner_prompt or {}
        analyzer_payload = self._json_safe(getattr(task, "__dict__", {}))
        available_tools = list(getattr(task, "available_tools", []) or [])
        schema = {
            "mode": "micro|meso|meso_advanced|macro",
            "task_type": "string",
            "execution_strategy": "string",
            "can_execute": True,
            "user_facing_summary": "string",
            "plan_validation_notes": ["string"],
            "added_steps_reason": ["string"],
            "task_units": [
                {
                    "id": "task_1",
                    "title": "string",
                    "description": "string",
                    "intent_refs": ["intent_name"],
                    "task_type": "string",
                    "status": "pending",
                    "depends_on": [],
                    "step_ids": ["step_1"],
                    "expected_outcome": "string",
                }
            ],
            "steps": [
                {
                    "id": "step_1",
                    "task_id": "task_1",
                    "step_type": "tool|model|respond",
                    "description": "string",
                    "tool_name": None,
                    "args": {},
                    "depends_on": [],
                    "input_from": [],
                    "output_key": "optional_output_key",
                    "expected_output": "string",
                    "on_failure": "stop",
                    "retryable": False,
                    "max_retries": self.config.default_step_max_retries,
                    "requires_confirmation": False,
                    "confirmation_reason": None,
                    "allow_model_reasoning": True,
                    "metadata": {},
                }
            ],
        }
        sections = [
            str(prompt_config.get("system", "You are the Planner for a task-oriented agent. Return strict JSON only.")),
            str(prompt_config.get("schema_hint", "")),
            "Return strict JSON only. Do not wrap the JSON in explanations.",
            "If you add steps not directly present in Analyzer intents, explain them in added_steps_reason.",
            "If you reorder or reinterpret Analyzer intents, explain it in plan_validation_notes.",
            "Safety rules:",
            json.dumps(prompt_config.get("safety_rules", []), ensure_ascii=False, indent=2),
            "Available tools:",
            json.dumps(available_tools, ensure_ascii=False, indent=2),
            "Plan JSON schema:",
            json.dumps(schema, ensure_ascii=False, indent=2),
            "Analyzer result:",
            json.dumps(analyzer_payload, ensure_ascii=False, indent=2),
            "User input:",
            user_input,
        ]
        return "\n\n".join(section for section in sections if section)

    def _build_llm_repair_prompt(
        self,
        user_input: str,
        task: Any,
        validation_error: str,
        invalid_plan: TaskPlan | None,
    ) -> str:
        sections = [
            self._build_llm_planner_prompt(user_input, task),
            "The previous plan failed validation. Return a repaired strict JSON plan only.",
            "Validation errors:",
            validation_error,
        ]
        if invalid_plan is not None:
            sections.extend(
                [
                    "Invalid plan JSON:",
                    json.dumps(invalid_plan.to_dict(), ensure_ascii=False, indent=2),
                ]
            )
        return "\n\n".join(sections)

    def _llm_planner_json_payload(self, prompt: str) -> tuple[Dict[str, Any], Any]:
        generate_json = getattr(self.model_manager, "generate_json", None)
        if callable(generate_json):
            result = generate_json(prompt, call_type="planner_structured_plan")
            if not isinstance(result, StructuredModelResult):
                return self._extract_json_object(result), result
            if not result.success:
                if result.code in STRUCTURED_JSON_FAILURE_CODES:
                    raise ValueError(result.error or result.code or "no_parseable_json_plan")
                if isinstance(result.model_result, ModelCallResult):
                    raise ModelCallFailure(result.model_result)
                raise RuntimeError(result.error or result.code or "model call failed")
            return self._extract_json_object(result.data), result.raw_json_text or result.content or result.data

        response = require_model_content(self.model_manager.generate(prompt))
        return self._extract_json_object(response), response

    def _extract_json_object(self, response: Any) -> Dict[str, Any]:
        if isinstance(response, dict):
            return response
        if not isinstance(response, str):
            raise ValueError("llm_response_not_string_or_dict")

        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", response, re.S)
        candidates = [fenced.group(1)] if fenced else []
        candidates.append(response.strip())
        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
        raise ValueError("no_parseable_json_plan")

    def _task_plan_from_llm_payload(
        self,
        user_input: str,
        task: Any,
        payload: Dict[str, Any],
        *,
        raw_response: Any,
    ) -> TaskPlan:
        steps_payload = payload.get("steps")
        if not isinstance(steps_payload, list) or not steps_payload:
            raise ValueError("llm_plan_missing_steps")

        steps = [self._step_from_payload(index + 1, item) for index, item in enumerate(steps_payload)]
        task_units_payload = payload.get("task_units")
        task_units = (
            [self._task_unit_from_payload(index + 1, item) for index, item in enumerate(task_units_payload)]
            if isinstance(task_units_payload, list) and task_units_payload
            else None
        )
        if task_units is None:
            step_ids = [step.id for step in steps]
            task_units = [
                TaskUnit(
                    id="task_1",
                    title=str(payload.get("task_title") or payload.get("title") or "LLM planned task"),
                    description=user_input,
                    intent_refs=self._intent_sequence(task),
                    task_type=str(payload.get("task_type") or getattr(task, "task_type", "qa")),
                    status="pending",
                    step_ids=step_ids,
                    expected_outcome=steps[-1].expected_output,
                )
            ]

        notes = list(payload.get("plan_validation_notes", []) or [])
        notes.append("llm_planner parsed structured JSON plan")
        added_reasons = list(payload.get("added_steps_reason", []) or [])

        plan = self._build_plan(
            user_input,
            task,
            mode=str(payload.get("mode") or getattr(task, "execution_strategy", "meso")),
            planning_strategy="llm_planner",
            steps=steps,
            task_title=str(payload.get("task_title") or payload.get("title") or "LLM planned task"),
            can_execute=bool(payload.get("can_execute", True)),
            plan_validation_status="valid",
            user_facing_summary=str(payload.get("user_facing_summary") or "已生成 LLM 初始执行计划。"),
            plan_validation_notes=notes,
            added_steps_reason=added_reasons,
            task_units=task_units,
        )
        plan.raw_planner_trace.append("llm_planner_status=parsed")
        plan.raw_planner_trace.append(f"llm_raw_response={raw_response}")
        return plan

    def _step_from_payload(self, index: int, payload: Any) -> PlanStep:
        if not isinstance(payload, dict):
            raise ValueError(f"llm_step_{index}_not_object")
        step_type = str(payload.get("step_type") or ("tool" if payload.get("tool_name") else "model"))
        tool_name = payload.get("tool_name")
        return self._step(
            str(payload.get("id") or f"step_{index}"),
            str(payload.get("description") or f"LLM planned step {index}."),
            task_id=str(payload.get("task_id") or "task_1"),
            step_type=step_type,
            tool_name=str(tool_name) if tool_name else None,
            args=dict(payload.get("args") or {}),
            expected_output=str(payload.get("expected_output") or ""),
            depends_on=list(payload.get("depends_on") or []),
            input_from=list(payload.get("input_from") or []),
            output_key=payload.get("output_key"),
            requires_confirmation=bool(payload.get("requires_confirmation", False)),
            confirmation_reason=payload.get("confirmation_reason"),
            on_failure=str(payload.get("on_failure") or "stop"),
            retryable=bool(payload.get("retryable", False)),
            max_retries=int(payload.get("max_retries", self.config.default_step_max_retries)),
            fallback_tools=list(payload.get("fallback_tools") or []),
            allow_model_reasoning=bool(payload.get("allow_model_reasoning", step_type != "tool")),
            metadata=dict(payload.get("metadata") or {}),
        )

    def _task_unit_from_payload(self, index: int, payload: Any) -> TaskUnit:
        if not isinstance(payload, dict):
            raise ValueError(f"llm_task_unit_{index}_not_object")
        return TaskUnit(
            id=str(payload.get("id") or f"task_{index}"),
            title=str(payload.get("title") or f"LLM task {index}"),
            description=str(payload.get("description") or ""),
            intent_refs=list(payload.get("intent_refs") or []),
            task_type=str(payload.get("task_type") or "qa"),
            status=str(payload.get("status") or "pending"),
            depends_on=list(payload.get("depends_on") or []),
            step_ids=list(payload.get("step_ids") or []),
            expected_outcome=str(payload.get("expected_outcome") or ""),
        )

    def _llm_unavailable_fallback_plan(self, user_input: str, task: Any, error: str) -> TaskPlan:
        fallback = self._meso_plan(user_input, task)
        fallback.planning_strategy = "fallback_model_only"
        fallback.plan_validation_notes.append(f"llm_planner_unavailable: {error}")
        fallback.raw_planner_trace.append("llm_planner_status=failed")
        fallback.raw_planner_trace.append(f"llm_planner_error={error}")
        return fallback

    def _json_safe(self, value: Any) -> Any:
        try:
            json.dumps(value, ensure_ascii=False)
            return value
        except TypeError:
            if isinstance(value, dict):
                return {str(key): self._json_safe(item) for key, item in value.items()}
            if isinstance(value, (list, tuple, set)):
                return [self._json_safe(item) for item in value]
            return str(value)

    def _write_log(self, user_input: str, task: Any, plan: TaskPlan) -> None:
        try:
            path = self.config.planner_log_path
            path.parent.mkdir(parents=True, exist_ok=True)
            step_entries = [step.to_dict() for step in plan.steps]
            entry = {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "plan_id": plan.plan_id,
                "source_trace_id": plan.source_trace_id,
                "raw_input": user_input,
                "intent_sequence": list(getattr(task, "intent_sequence", getattr(task, "intent", [])) or []),
                "task_type": plan.task_type,
                "execution_strategy": plan.execution_strategy,
                "mode": plan.mode,
                "planning_strategy": plan.planning_strategy,
                "can_execute": plan.can_execute,
                "risk_policy": plan.risk_policy,
                "risk_flags": list(getattr(task, "risk_flags", []) or []),
                "required_tools": plan.required_tools,
                "available_tools": plan.available_tools,
                "missing_tools": plan.missing_tools,
                "task_units": [unit.to_dict() for unit in plan.task_units],
                "steps": step_entries,
                "tool_args": {
                    step.id: {
                        "tool_name": step.tool_name,
                        "args": step.args,
                    }
                    for step in plan.steps
                    if step.tool_name
                },
                "plan_validation_status": plan.plan_validation_status,
                "plan_validation_notes": plan.plan_validation_notes,
                "added_steps_reason": plan.added_steps_reason,
                "special_policy": self._special_policy_name(plan),
                "llm_planner_trace": [
                    item for item in plan.raw_planner_trace if item.startswith("llm_")
                ],
                "raw_planner_trace": plan.raw_planner_trace,
                "user_facing_summary": plan.user_facing_summary,
            }
            with path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(self._json_safe(entry), ensure_ascii=False) + "\n")
        except Exception:
            return

    def _special_policy_name(self, plan: TaskPlan) -> str | None:
        if plan.mode in {"blocked", "clarify", "confirm", "missing_tools", "chat"}:
            return plan.mode
        return None

    def _pipeline_plan(
        self,
        user_input: str,
        task: Any,
        intents: List[str],
        *,
        task_title: str,
        steps: List[PlanStep],
        added_steps_reason: List[str],
    ) -> TaskPlan:
        return self._build_plan(
            user_input,
            task,
            mode=getattr(task, "execution_strategy", "meso"),
            planning_strategy="rule_template",
            steps=steps,
            task_title=task_title,
            added_steps_reason=added_steps_reason,
            plan_validation_notes=[f"rule_template matched intents: {', '.join(intents)}"],
        )

    def _multi_file_rule_plan(self, user_input: str, task: Any, intents: List[str]) -> TaskPlan:
        steps: List[PlanStep] = []
        task_units: List[TaskUnit] = []
        file_paths = list(getattr(task, "parameters", {}).get("file_paths", []) or [])
        operation = "extract" if "extract" in intents else "summarize" if "summarize" in intents else None

        for index, file_path in enumerate(file_paths, 1):
            task_id = f"task_{index}"
            read_id = f"step_{len(steps) + 1}"
            read_step = self._read_file_step(read_id, task_id=task_id, task=task, file_path=file_path)
            steps.append(read_step)

            if operation:
                process_id = f"step_{len(steps) + 1}"
                process_step = (
                    self._extract_step(process_id, task_id=task_id, depends_on=[read_id], input_from=[read_id])
                    if operation == "extract"
                    else self._summarize_step(process_id, task_id=task_id, depends_on=[read_id], input_from=[read_id])
                )
                steps.append(process_step)

            task_step_ids = [step.id for step in steps if step.task_id == task_id]
            task_units.append(
                TaskUnit(
                    id=task_id,
                    title=f"Process file {index}",
                    description=file_path,
                    intent_refs=intents,
                    task_type=getattr(task, "task_type", "document_understanding"),
                    status="pending",
                    step_ids=task_step_ids,
                    expected_outcome=steps[-1].expected_output,
                )
            )

        return self._build_plan(
            user_input,
            task,
            mode=getattr(task, "execution_strategy", "meso"),
            planning_strategy="rule_template",
            steps=steps,
            task_title="Process multiple files",
            task_units=task_units,
            added_steps_reason=["rule_template:multi_file_processing"],
            plan_validation_notes=["rule_template split independent files into separate TaskUnit objects"],
        )

    def _convert_format_plan(self, user_input: str, task: Any, intents: List[str]) -> TaskPlan:
        steps: List[PlanStep] = []
        if self._file_path(task):
            steps.append(self._read_file_step("step_1", task_id="task_1", task=task))
        convert_id = f"step_{len(steps) + 1}"
        depends_on = [steps[-1].id] if steps else []
        steps.append(
            self._step(
                convert_id,
                "Convert content to the requested target format.",
                task_id="task_1",
                step_type="model",
                args={
                    "target_format": getattr(task, "parameters", {}).get("target_format"),
                    "source_format": getattr(task, "parameters", {}).get("file_type"),
                },
                depends_on=depends_on,
                input_from=depends_on,
                expected_output="Converted content",
                output_key="converted_content",
                allow_model_reasoning=True,
            )
        )
        if self._target_path(task):
            steps.append(
                self._write_file_step(
                    f"step_{len(steps) + 1}",
                    task_id="task_1",
                    task=task,
                    depends_on=[convert_id],
                    input_from=[convert_id],
                )
            )
        return self._pipeline_plan(
            user_input,
            task,
            intents,
            task_title="Convert file format",
            steps=steps,
            added_steps_reason=["rule_template:convert_format"],
        )

    def _software_engineering_plan(self, user_input: str, task: Any, intents: List[str]) -> TaskPlan:
        steps: List[PlanStep] = [
            self._step(
                "step_1",
                "Analyze the software engineering request, target files, stage, and constraints.",
                task_id="task_1",
                step_type="model",
                args={
                    "project_stage": getattr(task, "project_stage", None),
                    "tech_stacks": list(getattr(task, "tech_stacks", []) or []),
                },
                expected_output="Engineering task analysis",
                output_key="engineering_analysis",
                allow_model_reasoning=True,
            ),
            self._step(
                "step_2",
                "Produce an implementation-oriented plan for the requested engineering work.",
                task_id="task_1",
                step_type="respond",
                depends_on=["step_1"],
                input_from=["step_1"],
                expected_output="Engineering plan",
                output_key="engineering_plan",
                allow_model_reasoning=True,
            ),
        ]
        return self._pipeline_plan(
            user_input,
            task,
            intents,
            task_title="Plan software engineering task",
            steps=steps,
            added_steps_reason=["rule_template:software_engineering_basic"],
        )

    def _micro_plan(self, user_input: str, task: Any) -> TaskPlan:
        intent = task.intent[0] if task.intent else "direct"
        tool_name: str | None = None
        args: Dict[str, Any] = {}

        if intent == "calculate":
            tool_name = "math_calculator"
            args = {"expression": task.parameters.get("expression", user_input)}
        elif intent in {"read", "read_file"} and task.parameters.get("file"):
            tool_name = "document_parser"
            args = {"file_path": task.parameters["file"]}
        elif intent == "translate":
            tool_name = "translator"
            args = {
                "text": task.parameters.get("content", user_input),
                "source_language": task.parameters.get("source_language", "auto"),
                "target_language": task.parameters.get("target_language", "zh"),
            }

        step_type = "tool" if tool_name else "model"
        return self._build_plan(
            user_input,
            task,
            mode="micro",
            planning_strategy="rule_template",
            steps=[
                self._step(
                    id="step_1",
                    description=f"Execute {intent} task.",
                    tool_name=tool_name,
                    args=args,
                    expected_output="Direct task result",
                    task_id="task_1",
                    step_type=step_type,
                    output_key=f"{intent}_result",
                    allow_model_reasoning=tool_name is None,
                )
            ],
            task_title=f"Execute {intent} task",
        )

    def _search_step(self, id: str, *, task_id: str, task: Any, user_input: str) -> PlanStep:
        parameters = getattr(task, "parameters", {}) or {}
        return self._step(
            id,
            "Search for source information.",
            task_id=task_id,
            step_type="tool",
            tool_name="search_tool",
            args={"query": parameters.get("topic") or parameters.get("query") or user_input, "max_results": 5},
            expected_output="Search results",
            output_key="search_results",
        )

    def _read_file_step(self, id: str, *, task_id: str, task: Any, file_path: str | None = None) -> PlanStep:
        return self._step(
            id,
            "Read file content.",
            task_id=task_id,
            step_type="tool",
            tool_name="document_parser",
            args={"file_path": file_path or self._file_path(task)},
            expected_output="File content",
            output_key=f"{id}_file_content",
        )

    def _summarize_step(
        self,
        id: str,
        *,
        task_id: str,
        depends_on: List[str] | None = None,
        input_from: List[str] | None = None,
    ) -> PlanStep:
        return self._step(
            id,
            "Summarize the collected content.",
            task_id=task_id,
            step_type="tool",
            tool_name="text_processor",
            args={"operation": "summary"},
            depends_on=depends_on,
            input_from=input_from,
            expected_output="Summary",
            output_key=f"{id}_summary",
        )

    def _extract_step(
        self,
        id: str,
        *,
        task_id: str,
        depends_on: List[str] | None = None,
        input_from: List[str] | None = None,
    ) -> PlanStep:
        return self._step(
            id,
            "Extract key information from the collected content.",
            task_id=task_id,
            step_type="tool",
            tool_name="text_processor",
            args={"operation": "keywords"},
            depends_on=depends_on,
            input_from=input_from,
            expected_output="Extracted information",
            output_key=f"{id}_extract",
        )

    def _translate_step(self, id: str, *, task_id: str, task: Any, user_input: str) -> PlanStep:
        parameters = getattr(task, "parameters", {}) or {}
        return self._step(
            id,
            "Translate content to the requested target language.",
            task_id=task_id,
            step_type="tool",
            tool_name="translator",
            args={
                "text": parameters.get("content") or user_input,
                "source_language": parameters.get("source_language", "auto"),
                "target_language": parameters.get("target_language", "zh"),
            },
            expected_output="Translated content",
            output_key="translated_content",
        )

    def _write_file_step(
        self,
        id: str,
        *,
        task_id: str,
        task: Any,
        depends_on: List[str] | None = None,
        input_from: List[str] | None = None,
    ) -> PlanStep:
        parameters = getattr(task, "parameters", {}) or {}
        file_path = self._target_path(task) or parameters.get("file") or parameters.get("file_path")
        return self._step(
            id,
            "Write generated content to a file.",
            task_id=task_id,
            step_type="tool",
            tool_name="file_writer",
            args={
                "file_path": file_path,
                "content": parameters.get("content", ""),
                "overwrite": getattr(task, "edit_mode", None) == "full_overwrite",
            },
            depends_on=depends_on,
            input_from=input_from,
            expected_output="Written file path",
            output_key=f"{id}_written_file",
        )

    def _intent_sequence(self, task: Any) -> List[str]:
        intents = list(getattr(task, "intent_sequence", []) or getattr(task, "intent", []) or [])
        return [intent for intent in intents if intent and intent != "chat"]

    def _has_intents(self, intents: List[str], expected: List[str]) -> bool:
        remaining = list(intents)
        for intent in expected:
            if intent not in remaining:
                return False
            remaining = remaining[remaining.index(intent) + 1 :]
        return True

    def _should_split_by_file(self, intents: List[str], task: Any) -> bool:
        file_paths = list(getattr(task, "parameters", {}).get("file_paths", []) or [])
        if len(file_paths) <= 1:
            return False
        return "read_file" in intents and "write_file" not in intents

    def _file_path(self, task: Any) -> str | None:
        parameters = getattr(task, "parameters", {}) or {}
        file_info = getattr(task, "file_info", {}) or {}
        return parameters.get("file_path") or parameters.get("file") or file_info.get("file_path")

    def _target_path(self, task: Any) -> str | None:
        parameters = getattr(task, "parameters", {}) or {}
        file_info = getattr(task, "file_info", {}) or {}
        return (
            parameters.get("target_path")
            or parameters.get("output_path")
            or file_info.get("target_path")
            or parameters.get("file_path")
        )

    def _meso_plan(self, user_input: str, task: Any) -> TaskPlan:
        return self._build_plan(
            user_input,
            task,
            mode=task.execution_strategy,
            planning_strategy="rule_template",
            steps=[
                self._step(
                    id="step_1",
                    description="Summarize task intent, parameters, risks, and missing information.",
                    task_id="task_1",
                    step_type="model",
                    expected_output="Task analysis",
                    output_key="task_analysis",
                    allow_model_reasoning=True,
                ),
                self._step(
                    id="step_2",
                    description="Generate a user-facing answer or next-step proposal.",
                    task_id="task_1",
                    step_type="respond",
                    depends_on=["step_1"],
                    input_from=["step_1"],
                    expected_output="Final response",
                    output_key="final_response",
                    allow_model_reasoning=True,
                ),
            ],
            task_title="Handle medium-complexity task",
        )

    def _step(
        self,
        id: str,
        description: str,
        *,
        task_id: str,
        step_type: str,
        tool_name: str | None = None,
        args: Dict[str, Any] | None = None,
        expected_output: str = "",
        depends_on: List[str] | None = None,
        input_from: List[str] | None = None,
        output_key: str | None = None,
        requires_confirmation: bool = False,
        confirmation_reason: str | None = None,
        on_failure: str = "stop",
        retryable: bool = False,
        max_retries: int | None = None,
        fallback_tools: List[str] | None = None,
        allow_model_reasoning: bool = False,
        metadata: Dict[str, Any] | None = None,
    ) -> PlanStep:
        return PlanStep(
            id=id,
            description=description,
            tool_name=tool_name,
            args=args or {},
            expected_output=expected_output,
            task_id=task_id,
            step_type=step_type,
            depends_on=depends_on or [],
            input_from=input_from or [],
            output_key=output_key,
            requires_confirmation=requires_confirmation,
            confirmation_reason=confirmation_reason,
            on_failure=on_failure,
            retryable=retryable,
            max_retries=max_retries if max_retries is not None else self.config.default_step_max_retries,
            fallback_tools=fallback_tools or [],
            allow_model_reasoning=allow_model_reasoning,
            metadata=metadata or {},
        )

    def _build_plan(
        self,
        user_input: str,
        task: Any,
        *,
        mode: str,
        planning_strategy: str,
        steps: List[PlanStep],
        task_title: str,
        can_execute: bool = True,
        task_status: str = "pending",
        plan_validation_status: str = "valid",
        user_facing_summary: str = "",
        plan_validation_notes: List[str] | None = None,
        added_steps_reason: List[str] | None = None,
        task_units: List[TaskUnit] | None = None,
    ) -> TaskPlan:
        if task_units is None:
            task_units = [
                TaskUnit(
                    id="task_1",
                    title=task_title,
                    description=user_input,
                    intent_refs=list(getattr(task, "intent_sequence", getattr(task, "intent", [])) or []),
                    task_type=getattr(task, "task_type", "qa"),
                    status=task_status,
                    step_ids=[step.id for step in steps],
                    expected_outcome=steps[-1].expected_output if steps else "",
                )
            ]
        required_tools = list(dict.fromkeys(step.tool_name for step in steps if step.tool_name))
        validation_status = plan_validation_status if plan_validation_status in PLAN_VALIDATION_STATUSES else "valid"
        validation_notes = list(plan_validation_notes or [])
        executable = can_execute
        if validation_status == "valid":
            plan_notes = self._plan_validation_notes(steps, task_units, task)
            if plan_notes:
                validation_status = "invalid"
                executable = False
                validation_notes.extend(plan_notes)
        return TaskPlan(
            goal=user_input,
            mode=mode if mode in PLAN_MODES else "meso",
            steps=steps,
            source_trace_id=getattr(task, "trace_id", None),
            task_type=getattr(task, "task_type", "qa"),
            execution_strategy=getattr(task, "execution_strategy", mode),
            planning_strategy=planning_strategy if planning_strategy in PLANNING_STRATEGIES else "rule_template",
            can_execute=executable,
            risk_policy=getattr(task, "action_policy", "allow"),
            required_tools=required_tools,
            available_tools=list(getattr(task, "available_tools", []) or []),
            missing_tools=list(getattr(task, "missing_tools", []) or []),
            task_units=task_units,
            plan_validation_status=validation_status,
            plan_validation_notes=validation_notes,
            added_steps_reason=added_steps_reason or [],
            user_facing_summary=user_facing_summary or "已生成初始执行计划。",
            raw_planner_trace=[
                f"planning_strategy={planning_strategy}",
                f"mode={mode}",
                f"steps={len(steps)}",
            ],
        )

    def _plan_validation_notes(self, steps: List[PlanStep], task_units: List[TaskUnit], task: Any) -> List[str]:
        notes: List[str] = []
        step_ids = [step.id for step in steps]
        step_id_set = set(step_ids)
        output_keys = {step.output_key for step in steps if step.output_key}
        task_ids = {unit.id for unit in task_units}
        available_tools = set(getattr(task, "available_tools", []) or [])

        if len(step_ids) != len(step_id_set):
            notes.append("plan: duplicate step ids are not allowed")
        if not task_units:
            notes.append("plan: at least one TaskUnit is required")
        if len(steps) > self.config.max_plan_steps:
            notes.append(f"plan: step count exceeds max_plan_steps={self.config.max_plan_steps}")
        if len(task_units) > self.config.max_task_units:
            notes.append(f"plan: task unit count exceeds max_task_units={self.config.max_task_units}")

        for unit in task_units:
            if unit.status not in TASK_UNIT_STATUSES:
                notes.append(f"{unit.id}: invalid TaskUnit.status={unit.status}")
            for step_id in unit.step_ids:
                if step_id not in step_id_set:
                    notes.append(f"{unit.id}: step_ids references missing step {step_id}")

        for step in steps:
            if step.step_type not in {"tool", "model", "respond", "block", "clarify", "confirm", "shell"}:
                notes.append(f"{step.id}: invalid step_type={step.step_type}")
            if step.task_id not in task_ids:
                notes.append(f"{step.id}: task_id references missing TaskUnit {step.task_id}")
            if step.step_type == "tool" and not step.tool_name:
                notes.append(f"{step.id}: step_type=tool requires tool_name")
            if step.step_type not in {"tool", "shell"} and step.tool_name:
                notes.append(f"{step.id}: non-tool step must not define tool_name")
            if available_tools and step.tool_name and step.tool_name not in available_tools:
                notes.append(f"{step.id}: tool_name {step.tool_name} is not in available_tools")
            for ref in step.depends_on:
                if ref not in step_id_set:
                    notes.append(f"{step.id}: depends_on references missing step {ref}")
            for ref in step.input_from:
                if ref not in step_id_set and ref not in output_keys:
                    notes.append(f"{step.id}: input_from references missing step or output_key {ref}")
            if step.step_type == "shell":
                notes.append(f"{step.id}: step_type=shell is not executable in Planner V1 without shell safety policy")

            args = step.args or {}
            has_input = bool(step.input_from)
            if step.tool_name == "math_calculator" and not self._has_value(args.get("expression")) and not self._has_value(args.get("data")):
                notes.append(f"{step.id}: math_calculator requires expression or data")
            elif step.tool_name == "document_parser" and not self._has_value(args.get("file_path")):
                notes.append(f"{step.id}: document_parser requires file_path")
            elif step.tool_name == "search_tool" and not self._has_value(args.get("query")):
                notes.append(f"{step.id}: search_tool requires query")
            elif step.tool_name == "text_processor" and not self._has_value(args.get("text")) and not has_input:
                notes.append(f"{step.id}: text_processor requires text or input_from")
            elif step.tool_name == "translator":
                if not self._has_value(args.get("target_language")):
                    notes.append(f"{step.id}: translator requires target_language")
                if not self._has_value(args.get("text")) and not has_input:
                    notes.append(f"{step.id}: translator requires text or input_from")
            elif step.tool_name == "file_writer":
                if not self._has_value(args.get("file_path")):
                    notes.append(f"{step.id}: file_writer requires file_path")
                if not self._has_value(args.get("content")) and not has_input:
                    notes.append(f"{step.id}: file_writer requires content or input_from")
        notes.extend(self._safety_policy_validation_notes(steps, task))
        return notes

    def _safety_policy_validation_notes(self, steps: List[PlanStep], task: Any) -> List[str]:
        notes: List[str] = []
        if getattr(task, "action_policy", "allow") == "block":
            if any(step.step_type not in {"block", "respond"} for step in steps):
                notes.append("plan: block policy cannot contain executable steps")
        if getattr(task, "requires_confirmation", False) or getattr(task, "action_policy", "allow") == "confirm":
            if any(step.step_type == "tool" and not step.requires_confirmation for step in steps):
                notes.append("plan: confirm policy cannot contain unconfirmed tool steps")
        return notes

    def _has_value(self, value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, (list, dict, tuple, set)):
            return bool(value)
        return True

