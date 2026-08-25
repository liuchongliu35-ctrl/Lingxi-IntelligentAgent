from __future__ import annotations

import json
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Callable, Dict, Generator, List

from src.agent.react_executor_checker import LLMChecker, ReActChecker, RuleChecker
from src.agent.react_executor_config import ReActExecutorConfig, load_react_executor_config
from src.agent.react_executor_events import EventStream
from src.agent.react_executor_fallback import (
    FALLBACK_MODEL_NOT_ALLOWED_CODE,
    FALLBACK_NOT_ALLOWED_CODE,
    FALLBACK_SCHEDULED_CODE,
    FALLBACK_TARGET_NOT_FOUND_CODE,
    FALLBACK_TOOL_NOT_ALLOWED_CODE,
    FALLBACK_TOOL_NOT_AVAILABLE_CODE,
    FALLBACK_UNSUPPORTED_ACTION_CODE,
    FallbackPolicy,
)
from src.agent.react_executor_logging import ReActExecutorLogger
from src.agent.react_executor_observation import ObservationStore, sanitize_sensitive
from src.agent.react_executor_protocol import (
    ACTION_TYPES,
    ActionPacket,
    ActionPacketParseResult,
    ExecutionEvent,
    CommandAction,
    ExecutionResult,
    ObservationPacket,
    PendingConfirmation,
    ReActLoopState,
    ReActTurnState,
    StepRuntimeState,
    TaskUnitRuntimeState,
    new_id,
    parse_action_packet,
    utc_now_iso,
    validate_action_packet,
)
from src.agent.react_executor_prompt import ReActPromptContext, build_prompt_log_summary, build_react_executor_prompt
from src.agent.react_executor_retry import (
    RETRY_EXHAUSTED_CODE,
    RETRY_NOT_ALLOWED_CODE,
    RETRY_NOT_RETRYABLE_CODE,
    RETRY_SCHEDULED_CODE,
    RETRY_SLEEP_FAILED_CODE,
    RETRY_TARGET_NOT_FOUND_CODE,
    RETRY_UNSUPPORTED_ACTION_CODE,
    RetryPolicy,
)
from src.agent.react_executor_result import ExecutionResultBuilder
from src.agent.react_executor_safety import (
    SAFETY_BLOCKED_CODE,
    SAFETY_CONFIRMATION_REQUIRED_CODE,
    SafetyDecision,
    SafetyPolicy,
)
from src.models.compat import ModelCallFailure, require_model_content
from src.models.protocol import ModelCallResult, StructuredModelResult
from src.agent.observation_builder import build_tool_observation_view
from src.tools.base import ToolResult
from src.tools.errors import ToolErrorCode
from src.tools.protocol import ToolCallContext, ToolCallOptions, ToolCallRequest, ToolCallSource
from src.tools.registry import ToolRegistry, build_default_tool_registry


ACTION_LOOP_NOT_IMPLEMENTED_CODE = "react_action_loop_not_implemented"
PLAN_NOT_EXECUTABLE_CODE = "plan_not_executable"
EMPTY_PLAN_CODE = "empty_plan"
MISSING_STEP_CODE = "missing_step"
INVALID_PLAN_CODE = "invalid_plan"
CLARIFICATION_REQUIRED_CODE = "clarification_required"
CONFIRMATION_REQUIRED_CODE = "confirmation_required"
MISSING_TOOLS_CODE = "missing_tools"
TASK_POLICY_BLOCKED_CODE = "task_policy_blocked"
TOOL_NOT_AVAILABLE_CODE = "tool_not_available"
PLAN_REFERENCE_ERROR_CODE = "plan_reference_error"
ACTION_PACKET_INVALID_CODE = "action_packet_invalid"
ACTION_PACKET_MODEL_UNAVAILABLE_CODE = "action_packet_model_unavailable"
ACTION_PACKET_MODEL_EXCEPTION_CODE = "action_packet_model_exception"
ACTION_DISPATCH_FAILED_CODE = "action_dispatch_failed"
UNSUPPORTED_ACTION_TYPE_CODE = "unsupported_action_type"
RETRY_NOT_IMPLEMENTED_CODE = RETRY_TARGET_NOT_FOUND_CODE
FALLBACK_TO_MODEL_NOT_IMPLEMENTED_CODE = FALLBACK_TARGET_NOT_FOUND_CODE
FALLBACK_TO_TOOL_NOT_IMPLEMENTED_CODE = FALLBACK_TARGET_NOT_FOUND_CODE
STEP_SKIPPED_CODE = "step_skipped"
ACTION_FAILED_CODE = "action_failed"
ACTION_BLOCKED_CODE = "action_blocked"
ACTION_CANCELLED_CODE = "action_cancelled"
REQUEST_REPLAN_CODE = "request_replan"
TOOL_MANAGER_UNAVAILABLE_CODE = "tool_manager_unavailable"
TOOL_ARGUMENT_VALIDATION_FAILED_CODE = "tool_argument_validation_failed"
TOOL_INPUT_REF_MISSING_CODE = "tool_input_ref_missing"
TOOL_EXECUTION_FAILED_CODE = "tool_execution_failed"
TOOL_EXECUTION_EXCEPTION_CODE = "tool_execution_exception"
MODEL_MANAGER_UNAVAILABLE_CODE = "model_manager_unavailable"
MODEL_INPUT_REF_MISSING_CODE = "model_input_ref_missing"
MODEL_CALL_EXCEPTION_CODE = "model_call_exception"
USER_INPUT_REQUIRED_CODE = "user_input_required"
CONFIRMATION_PENDING_CODE = "confirmation_pending"
CONFIRMATION_REJECTED_CODE = "confirmation_rejected"
PREVIEW_CONFLICT_CODE = ToolErrorCode.PREVIEW_CONFLICT.value
COMMAND_BLOCKED_CODE = SAFETY_BLOCKED_CODE

COMMAND_TOOL_NAMES = {"command_tool", "shell_command_tool", "shell_tool"}
COMMAND_DANGEROUS_KEYWORDS = {
    "rm ",
    "del ",
    "erase ",
    "format ",
    "shutdown",
    "reboot",
    "reg ",
    "chmod ",
    "chown ",
    "sudo ",
    "curl ",
    "wget ",
    "Invoke-WebRequest",
}
COMMAND_SHELL_METACHARS = {"|", "&", ";", "<", ">", "`"}
COMMAND_ACTION_REQUIRED_ARGS = {
    "command",
    "cwd",
    "purpose",
    "risk_level",
    "requires_confirmation",
    "expected_result",
    "timeout_seconds",
}
COMMAND_ACTION_RISK_LEVELS = {"low", "medium", "high", "blocked"}
STRUCTURED_JSON_FAILURE_CODES = {"invalid_json", "schema_invalid", "json_repair_failed"}
TOOL_EXECUTOR_CONTROL_FIELDS = {
    "input_from",
    "output_key",
    "fallback_reason",
    "packet_id",
    "observation_id",
    "action_id",
    "step_id",
    "confirmed",
    "confirmation_id",
    "preview_hash",
    "action_type",
    "action_target",
    "thought_summary",
    "user_visible_message",
    "expected_observation",
    "confidence",
    "confirmation_type",
    "safety_notes",
    "fallback_plan",
    "request_replan_reason",
    "final_answer",
    "dry_run",
    "observation_mode",
}


@dataclass
class ReActExecutionContext:
    execution_id: str
    plan: Any
    task: Any
    user_input: str
    history: str
    observation_store: ObservationStore
    event_stream: EventStream
    loop_state: ReActLoopState
    task_states: Dict[str, TaskUnitRuntimeState] = field(default_factory=dict)
    step_states: Dict[str, StepRuntimeState] = field(default_factory=dict)
    step_lookup: Dict[str, Any] = field(default_factory=dict)
    failed_step_id: str | None = None
    error_code: str | None = None
    output: str = ""
    summary: str = ""
    requires_user_input: bool = False
    user_input_request: str | None = None
    pending_confirmation: PendingConfirmation | None = None
    request_replan: bool = False
    replan_reason: str | None = None

    @property
    def plan_id(self) -> str:
        return str(getattr(self.plan, "plan_id", ""))

    @property
    def source_trace_id(self) -> str | None:
        return getattr(self.plan, "source_trace_id", None)


@dataclass
class ActionPacketRequestResult:
    packet: ActionPacket | None = None
    observation: ObservationPacket | None = None
    parse_result: ActionPacketParseResult | None = None
    raw_output: Any = None
    repair_attempts: int = 0
    prompt: str = ""

    @property
    def success(self) -> bool:
        return self.packet is not None and self.observation is None


@dataclass
class ReActStepLoopResult:
    status: str
    success: bool
    message: str = ""
    error_code: str | None = None
    packet: ActionPacket | None = None
    observation: ObservationPacket | None = None
    checker_result: Any | None = None
    terminal: bool = False


class ReActExecutor:
    """Planner-guided ReAct executor.

    The default execute path preserves plan precheck and policy short-circuits,
    then enters the second-phase ReAct loop entry point. The old skeleton
    traversal remains available as an explicit diagnostic helper.
    """

    def __init__(
        self,
        model_manager: Any | None = None,
        tool_manager: Any | None = None,
        tool_registry: ToolRegistry | None = None,
        config: ReActExecutorConfig | None = None,
        retry_policy: RetryPolicy | None = None,
        retry_sleep_fn: Any | None = None,
        fallback_policy: FallbackPolicy | None = None,
        safety_policy: SafetyPolicy | None = None,
        execution_logger: ReActExecutorLogger | None = None,
        result_builder: ExecutionResultBuilder | None = None,
    ):
        self.model_manager = model_manager
        self.tool_manager = tool_manager
        self.config = config or load_react_executor_config()
        self.tool_registry = tool_registry or build_default_tool_registry(
            tool_manager,
            include_command_tool=self.config.enable_command_tool,
        )
        self.checker = ReActChecker(
            rule_checker=RuleChecker(
                default_max_retries=self.config.default_tool_max_retries,
                max_step_turns=self.config.max_step_turns,
                max_execution_turns=self.config.max_execution_turns,
            ),
            llm_checker=LLMChecker(model_manager, enabled=self.config.enable_llm_checker),
            enable_llm_checker=False,
        )
        if retry_policy is not None:
            self.retry_policy = retry_policy
        elif retry_sleep_fn is None:
            self.retry_policy = RetryPolicy(
                default_max_retries=self.config.default_tool_max_retries,
                backoff_base_seconds=self.config.retry_backoff_base_seconds,
                backoff_max_seconds=self.config.retry_backoff_max_seconds,
            )
        else:
            self.retry_policy = RetryPolicy(
                default_max_retries=self.config.default_tool_max_retries,
                backoff_base_seconds=self.config.retry_backoff_base_seconds,
                backoff_max_seconds=self.config.retry_backoff_max_seconds,
                sleep_fn=retry_sleep_fn,
            )
        self.fallback_policy = fallback_policy or FallbackPolicy()
        self.safety_policy = safety_policy or SafetyPolicy()
        self.execution_logger = execution_logger or ReActExecutorLogger(
            self.config.react_executor_log_path,
            log_full_prompt=self.config.log_full_prompt,
        )
        self.result_builder = result_builder or ExecutionResultBuilder()

    def execute(
        self,
        plan: Any,
        task: Any,
        user_input: str,
        history: str = "",
        *,
        event_callback: Callable[[ExecutionEvent], None] | None = None,
        event_callback_visible_only: bool = False,
    ) -> ExecutionResult:
        context = self._create_context(plan, task, user_input, history)
        result: ExecutionResult | None = None
        unsubscribe_event_callback = None
        if event_callback is not None:
            unsubscribe_event_callback = context.event_stream.subscribe(
                event_callback,
                visible_only=event_callback_visible_only,
            )
        self.execution_logger.log_execution_started(context)
        try:
            context.event_stream.emit_event(
                "progress_message",
                "ReActExecutor execution started.",
                payload={
                    "plan_id": context.plan_id,
                    "mode": getattr(plan, "mode", None),
                    "step_count": len(getattr(plan, "steps", []) or []),
                    "task_unit_count": len(getattr(plan, "task_units", []) or []),
                },
            )

            precheck_result = self._run_plan_precheck(context)
            if precheck_result is not None:
                result = precheck_result
            elif not context.step_lookup:
                result = self._empty_plan_result(context)
            else:
                result = self._execute_react_loop(context)
            return result
        except Exception as exc:
            self.execution_logger.log_execution_exception(context, exc)
            raise
        finally:
            if unsubscribe_event_callback is not None:
                unsubscribe_event_callback()
            if result is not None:
                self.execution_logger.log_execution_finished(context, result)

    def execute_stream(
        self,
        plan: Any,
        task: Any,
        user_input: str,
        history: str = "",
        *,
        include_internal: bool = True,
    ) -> Generator[ExecutionEvent, None, ExecutionResult]:
        streamed_events: List[ExecutionEvent] = []

        def collect(event: ExecutionEvent) -> None:
            if include_internal or event.visible_to_user:
                streamed_events.append(event)

        result = self.execute(
            plan,
            task,
            user_input,
            history,
            event_callback=collect,
            event_callback_visible_only=not include_internal,
        )
        for event in streamed_events:
            yield event
        return result

    def dispatch_action(
        self,
        context: ReActExecutionContext,
        packet: ActionPacket,
        *,
        step: Any | None = None,
        attempt: int = 1,
        output_key: str | None = None,
        confirmed: bool = False,
        trusted_confirmation: bool = False,
        confirmation_ticket: PendingConfirmation | None = None,
    ) -> ObservationPacket:
        context.event_stream.emit_event(
            "action_selected",
            packet.user_visible_message or f"Selected action: {packet.action_type}",
            payload=self._action_event_payload(packet, step=step),
            task_id=packet.task_id or getattr(step, "task_id", None),
            step_id=packet.step_id or getattr(step, "id", None),
        )

        validation_errors = self._dispatch_validation_errors(context, packet, step)
        self.execution_logger.log_action_packet(
            context,
            packet,
            attempt=attempt,
            schema_valid=not validation_errors,
            schema_errors=validation_errors,
        )
        if validation_errors:
            observation = self._observation_from_packet(
                context,
                packet,
                attempt=attempt,
                success=False,
                message="ActionPacket failed dispatcher validation.",
                error="; ".join(validation_errors),
                code=ACTION_PACKET_INVALID_CODE,
                data={"errors": validation_errors},
                model_consumable_observation={"success": False, "errors": validation_errors, "code": ACTION_PACKET_INVALID_CODE},
            )
            return self._record_observation(context, observation, output_key=output_key or getattr(step, "output_key", None))

        safety_decision = self._evaluate_action_safety(context, packet, step)
        self.execution_logger.log_safety_decision(context, packet, safety_decision, attempt=attempt)
        if safety_decision.blocked:
            observation = self._safety_blocked_observation(context, packet, safety_decision, attempt=attempt)
            return self._record_observation(context, observation, output_key=output_key or getattr(step, "output_key", None))

        if safety_decision.needs_confirmation and not confirmed:
            observation = self._confirmation_pending_observation(context, packet, step=step, attempt=attempt, safety_decision=safety_decision)
            return self._record_observation(context, observation, output_key=output_key or getattr(step, "output_key", None))

        handler = self._action_handlers().get(packet.action_type)
        if handler is None:
            observation = self._observation_from_packet(
                context,
                packet,
                attempt=attempt,
                success=False,
                message=f"Unsupported action type: {packet.action_type}",
                error=f"Unsupported action type: {packet.action_type}",
                code=UNSUPPORTED_ACTION_TYPE_CODE,
                model_consumable_observation={"success": False, "code": UNSUPPORTED_ACTION_TYPE_CODE},
            )
            return self._record_observation(context, observation, output_key=output_key or getattr(step, "output_key", None))

        try:
            if packet.action_type == "call_tool":
                observation = handler(
                    context,
                    packet,
                    step=step,
                    attempt=attempt,
                    trusted_confirmation=trusted_confirmation,
                    confirmation_ticket=confirmation_ticket,
                )
            else:
                observation = handler(context, packet, step=step, attempt=attempt)
        except Exception as exc:
            observation = self._observation_from_packet(
                context,
                packet,
                attempt=attempt,
                success=False,
                message="Action dispatch failed.",
                error=str(exc),
                code=ACTION_DISPATCH_FAILED_CODE,
                raw_observation=exc,
                model_consumable_observation={"success": False, "error": str(exc), "code": ACTION_DISPATCH_FAILED_CODE},
            )
        return self._record_observation(context, observation, output_key=output_key or getattr(step, "output_key", None))

    def handle_confirmation_response(
        self,
        context: ReActExecutionContext,
        *,
        approved: bool,
        reason: str = "",
        confirmation_id: str | None = None,
        preview_hash: str | None = None,
    ) -> ObservationPacket:
        pending = context.pending_confirmation
        if pending is None:
            packet = ActionPacket(
                execution_id=context.execution_id,
                plan_id=context.plan_id,
                action_type="fail",
                action_args={"reason": "No pending confirmation."},
            )
            observation = self._observation_from_packet(
                context,
                packet,
                attempt=1,
                success=False,
                message="No pending confirmation.",
                error="No pending confirmation.",
                code=CONFIRMATION_REJECTED_CODE,
                model_consumable_observation={"success": False, "code": CONFIRMATION_REJECTED_CODE},
            )
            return self._record_observation(context, observation)

        mismatch = self._confirmation_response_mismatch(
            context,
            pending,
            confirmation_id=confirmation_id,
            preview_hash=preview_hash,
        )
        if mismatch:
            observation = self._observation_from_packet(
                context,
                self._pending_confirmation_action_packet(context, pending)
                or ActionPacket(
                    execution_id=context.execution_id,
                    plan_id=context.plan_id,
                    task_id=pending.task_id,
                    step_id=pending.step_id,
                    action_type="blocked",
                ),
                attempt=1,
                success=False,
                message=mismatch,
                error=mismatch,
                code=CONFIRMATION_REJECTED_CODE,
                model_consumable_observation={
                    "success": False,
                    "code": CONFIRMATION_REJECTED_CODE,
                    "message": mismatch,
                },
            )
            return self._record_observation(context, observation)

        packet = self._pending_confirmation_action_packet(context, pending)
        if packet is None:
            packet = ActionPacket(
                execution_id=context.execution_id,
                plan_id=context.plan_id,
                task_id=pending.task_id,
                step_id=pending.step_id,
                action_type="blocked",
                action_args={"reason": pending.confirmation_message},
            )

        if approved:
            context.pending_confirmation = None
            context.requires_user_input = False
            context.user_input_request = None
            return self.dispatch_action(
                context,
                packet,
                step=context.step_lookup.get(packet.step_id or ""),
                confirmed=True,
                trusted_confirmation=True,
                confirmation_ticket=pending,
            )

        message = reason or "User rejected confirmation."
        self._mark_rejected_confirmation_states(context, pending.step_id, message)
        context.pending_confirmation = None
        context.requires_user_input = False
        context.user_input_request = None
        context.output = message
        context.summary = message
        context.error_code = CONFIRMATION_REJECTED_CODE
        context.failed_step_id = pending.step_id or context.failed_step_id
        observation = self._observation_from_packet(
            context,
            packet,
            attempt=1,
            success=False,
            message=message,
            error=message,
            code=CONFIRMATION_REJECTED_CODE,
            model_consumable_observation={"success": False, "code": CONFIRMATION_REJECTED_CODE, "reason": message},
            checker_result={"step_status": "cancelled"},
        )
        context.event_stream.emit_event(
            "step_failed",
            message,
            payload={"reason": message, "status": "cancelled"},
            task_id=pending.task_id,
            step_id=pending.step_id,
        )
        return self._record_observation(context, observation)

    def resume_after_confirmation(
        self,
        context: ReActExecutionContext,
        *,
        approved: bool,
        reason: str = "",
        confirmation_id: str | None = None,
        preview_hash: str | None = None,
    ) -> ExecutionResult:
        pending = context.pending_confirmation
        if pending is None:
            observation = self.handle_confirmation_response(
                context,
                approved=approved,
                reason=reason,
                confirmation_id=confirmation_id,
                preview_hash=preview_hash,
            )
            context.loop_state.record_observation(observation)
            context.event_stream.emit_event(
                "final_answer",
                observation.message or observation.error or "Confirmation response received.",
                payload={"status": "failed", "error_code": observation.code},
            )
            return self._build_result(context, status="failed", success=False)

        mismatch = self._confirmation_response_mismatch(
            context,
            pending,
            confirmation_id=confirmation_id,
            preview_hash=preview_hash,
        )
        if mismatch:
            observation = self.handle_confirmation_response(
                context,
                approved=False,
                reason=mismatch,
                confirmation_id=confirmation_id,
                preview_hash=preview_hash,
            )
            context.loop_state.record_observation(observation)
            return self._build_result(context, status="failed", success=False)

        if not approved:
            observation = self.handle_confirmation_response(
                context,
                approved=False,
                reason=reason,
                confirmation_id=confirmation_id,
                preview_hash=preview_hash,
            )
            context.loop_state.record_observation(observation)
            return self._execute_react_loop(context)

        pending_action = pending.pending_action
        if isinstance(pending_action, dict) and str(pending_action.get("type", "")) == "plan_confirmation":
            self._clear_pending_confirmation(context)
            return self._execute_react_loop(context)

        packet = self._pending_confirmation_action_packet(context, pending)
        if packet is None:
            message = "Pending confirmation cannot be resumed."
            context.error_code = CONFIRMATION_REJECTED_CODE
            context.output = message
            context.summary = message
            self._clear_pending_confirmation(context)
            context.event_stream.emit_event("system_notice", message, payload={"status": "blocked"})
            context.event_stream.emit_event("final_answer", message, payload={"status": "blocked"})
            return self._build_result(context, status="blocked", success=False)

        self._clear_pending_confirmation(context)
        step = context.step_lookup.get(packet.step_id or pending.step_id or "")
        task_id = packet.task_id or pending.task_id or getattr(step, "task_id", None)
        step_id = packet.step_id or pending.step_id or getattr(step, "id", None)
        previous_step_turn = context.loop_state.step_turns.get(str(step_id or ""), 0)
        if step_id:
            self._mark_step_running(context, str(task_id or ""), str(step_id))

        step_state = context.step_states.get(str(step_id or ""))
        attempt = max(int(getattr(step_state, "attempts", 0) or 0), 1)
        turn_state = context.loop_state.start_turn(
            task_id=str(task_id or "") or None,
            step_id=str(step_id or "") or None,
            attempt=attempt,
            thought_summary="Resuming after user confirmation.",
            user_visible_message="Resuming confirmed action.",
        )
        if step_id:
            restored_step_turn = max(int(previous_step_turn or 0), 1)
            turn_state.step_turn = restored_step_turn
            context.loop_state.step_turns[str(step_id)] = restored_step_turn
        context.loop_state.record_action(packet)
        observation = self.dispatch_action(
            context,
            packet,
            step=step,
            attempt=turn_state.attempt,
            output_key=getattr(step, "output_key", None),
            confirmed=True,
            trusted_confirmation=True,
            confirmation_ticket=pending,
        )
        context.loop_state.record_observation(observation)
        if observation.code == PREVIEW_CONFLICT_CODE:
            result = self._finalize_confirmation_preview_conflict(
                context,
                step=step,
                packet=packet,
                observation=observation,
                turn_state=turn_state,
            )
            return result
        checker_result = self.check_observation(
            context,
            observation,
            step=step,
            packet=packet,
            current_step_turn=turn_state.step_turn,
            current_execution_turn=context.loop_state.execution_turn,
        )
        context.loop_state.record_checker_result(checker_result.to_dict())
        observation.checker_result = checker_result.to_dict()
        result = self._apply_checker_decision(
            context,
            step=step,
            packet=packet,
            observation=observation,
            checker_result=checker_result,
            turn_state=turn_state,
        )
        turn_state.finish(result.status if result.status in {"completed", "failed", "blocked", "waiting_user", "request_replan", "cancelled"} else "failed")
        if result.status == "waiting_user":
            context.loop_state.finish("waiting_user")
            return self._build_result(context, status="waiting_user", success=False)
        resumed_result = self._execute_react_loop(context)
        if context.request_replan and resumed_result.status == "failed":
            return self._build_result(context, status="request_replan", success=False)
        return resumed_result

    def check_observation(
        self,
        context: ReActExecutionContext,
        observation: ObservationPacket,
        *,
        step: Any | None = None,
        packet: ActionPacket | None = None,
        current_step_turn: int | None = None,
        current_execution_turn: int | None = None,
    ):
        step_id = observation.step_id or (packet.step_id if packet else None) or getattr(step, "id", None)
        step_state = context.step_states.get(str(step_id)) if step_id else None
        return self.checker.check_observation(
            observation,
            step=step,
            packet=packet,
            context=context,
            step_state=step_state,
            current_step_turn=current_step_turn,
            current_execution_turn=current_execution_turn,
            max_step_turns=self.config.max_step_turns,
            max_execution_turns=self.config.max_execution_turns,
        )

    def _create_context(self, plan: Any, task: Any, user_input: str, history: str) -> ReActExecutionContext:
        execution_id = new_id("execution")
        plan_id = str(getattr(plan, "plan_id", ""))
        observation_store = ObservationStore()
        event_stream = EventStream(
            execution_id=execution_id,
            plan_id=plan_id,
            enabled=self.config.event_stream_enabled,
        )
        steps = list(getattr(plan, "steps", []) or [])
        step_lookup = {str(getattr(step, "id", "")): step for step in steps if getattr(step, "id", None)}
        task_units = self._task_units_for_plan(plan)
        task_states = {
            str(getattr(unit, "id", "")): TaskUnitRuntimeState(
                task_id=str(getattr(unit, "id", "")),
                status="pending",
                step_statuses={str(step_id): "pending" for step_id in list(getattr(unit, "step_ids", []) or [])},
            )
            for unit in task_units
            if getattr(unit, "id", None)
        }
        step_states = {
            step_id: StepRuntimeState(
                step_id=step_id,
                status="pending",
                output_key=getattr(step, "output_key", None),
            )
            for step_id, step in step_lookup.items()
        }
        return ReActExecutionContext(
            execution_id=execution_id,
            plan=plan,
            task=task,
            user_input=user_input,
            history=history,
            observation_store=observation_store,
            event_stream=event_stream,
            loop_state=ReActLoopState(
                execution_id=execution_id,
                plan_id=plan_id,
                max_execution_turns=self.config.max_execution_turns,
                max_step_turns=self.config.max_step_turns,
            ),
            task_states=task_states,
            step_states=step_states,
            step_lookup=step_lookup,
        )

    def _action_handlers(self):
        return {
            "call_tool": self._handle_call_tool,
            "call_model": self._handle_call_model,
            "ask_user": self._handle_ask_user,
            "retry_step": self._handle_retry,
            "fallback_to_model": self._handle_fallback_to_model,
            "fallback_to_tool": self._handle_fallback_to_tool,
            "skip_step": self._handle_skip_step,
            "finish": self._handle_finish,
            "fail": self._handle_fail,
            "request_replan": self._handle_request_replan,
            "blocked": self._handle_blocked,
            "cancel": self._handle_cancel,
        }

    def _dispatch_validation_errors(self, context: ReActExecutionContext, packet: ActionPacket, step: Any | None) -> List[str]:
        if packet.action_type not in ACTION_TYPES:
            return [f"Unsupported action_type: {packet.action_type}"]
        step_id = getattr(step, "id", None) or packet.step_id
        step_state = context.step_states.get(str(step_id)) if step_id else None
        retry_attempts = max(
            int(getattr(step, "attempts", 0) or 0),
            int(getattr(step_state, "attempts", 0) or 0),
            0,
        )
        if packet.action_type == "retry_step":
            retry_attempts = max(retry_attempts - 1, 0)
        errors = validate_action_packet(
            packet,
            available_tools=sorted(self._available_tool_names(context)),
            fallback_tools=None if packet.action_type == "fallback_to_tool" else self._fallback_tool_names(step),
            current_step_id=step_id,
            retry_attempts=retry_attempts,
            max_retries=max(int(getattr(step, "max_retries", self.config.default_tool_max_retries) or 0), 0),
        )
        if self._is_formal_command_tool_packet(packet):
            errors.extend(self._command_action_structure_errors(packet))
        return errors

    def _action_requires_confirmation(self, packet: ActionPacket, step: Any | None) -> bool:
        if packet.action_type == "fallback_to_tool":
            return False
        if self._is_command_packet(packet):
            return self._command_requires_confirmation(packet)
        if packet.requires_confirmation:
            return True
        if getattr(step, "requires_confirmation", False):
            return True
        if packet.action_type in {"call_tool", "fallback_to_tool"} and packet.action_target:
            spec = self.tool_registry.get(str(packet.action_target))
            return bool(spec and spec.requires_confirmation)
        return False

    def _evaluate_action_safety(self, context: ReActExecutionContext, packet: ActionPacket, step: Any | None) -> SafetyDecision:
        spec = self.tool_registry.get(str(packet.action_target or "")) if packet.action_type == "call_tool" and packet.action_target else None
        input_args = self._safety_input_args(context, packet, step, spec)
        return self.safety_policy.evaluate_action(
            packet=packet,
            step=step,
            plan=context.plan,
            task=context.task,
            tool_spec=spec,
            input_args=input_args,
            workspace_root=self.config.workspace_root,
            command_confirmation_policy=self.config.command_confirmation_policy,
        )

    def _safety_input_args(self, context: ReActExecutionContext, packet: ActionPacket, step: Any | None, spec: Any | None) -> Dict[str, Any]:
        if packet.action_type == "call_tool" and spec is not None:
            input_args, _errors = self._prepare_tool_args(context, packet, step, spec)
            return input_args
        args: Dict[str, Any] = {}
        if step is not None:
            args.update(getattr(step, "args", {}) or {})
        args.update(packet.action_args or {})
        return args

    def _is_command_packet(self, packet: ActionPacket) -> bool:
        return packet.action_type in {"call_tool", "fallback_to_tool"} and str(packet.action_target or "") in COMMAND_TOOL_NAMES

    def _is_formal_command_tool_packet(self, packet: ActionPacket) -> bool:
        if packet.action_type not in {"call_tool", "fallback_to_tool"}:
            return False
        target = str(packet.action_target or "")
        canonical_name = self.tool_registry.resolve_name(target) if hasattr(self.tool_registry, "resolve_name") else target
        return canonical_name == "command_tool"

    def _command_action_from_packet(self, packet: ActionPacket) -> CommandAction:
        args = dict(packet.action_args or {})
        return CommandAction(
            command=str(args.get("command", "")),
            cwd=str(args.get("cwd", ".")),
            purpose=str(args.get("purpose", "")),
            risk_level=str(args.get("risk_level", "unknown")),
            requires_confirmation=bool(args.get("requires_confirmation", False)),
            expected_result=str(args.get("expected_result", "")),
            timeout_seconds=int(args.get("timeout_seconds", 30) or 30),
            shell=args.get("shell"),
            env_policy=str(args.get("env_policy", "inherit_safe")),
            network_required=bool(args.get("network_required", False)),
            writes_files=bool(args.get("writes_files", False)),
            target_paths=list(args.get("target_paths", []) or []),
            destructive_risk=bool(args.get("destructive_risk", False)),
            approval_scope=args.get("approval_scope"),
        )

    def _command_action_structure_errors(self, packet: ActionPacket) -> List[str]:
        args = dict(packet.action_args or {})
        errors: List[str] = []
        for field_name in sorted(COMMAND_ACTION_REQUIRED_ARGS):
            if field_name not in args:
                errors.append(f"command_tool action_args.{field_name} is required")
                continue
            value = args.get(field_name)
            if value is None:
                errors.append(f"command_tool action_args.{field_name} is required")
            elif isinstance(value, str) and not value.strip():
                errors.append(f"command_tool action_args.{field_name} is required")
        risk_level = str(args.get("risk_level", "") or "")
        if risk_level and risk_level not in COMMAND_ACTION_RISK_LEVELS:
            errors.append("command_tool action_args.risk_level must be low, medium, high, or blocked")
        timeout = args.get("timeout_seconds")
        if timeout is not None:
            try:
                if int(timeout) < 1:
                    errors.append("command_tool action_args.timeout_seconds must be at least 1")
            except (TypeError, ValueError):
                errors.append("command_tool action_args.timeout_seconds must be an integer")
        if "requires_confirmation" in args and not isinstance(args.get("requires_confirmation"), bool):
            errors.append("command_tool action_args.requires_confirmation must be boolean")
        return errors

    def _command_requires_confirmation(self, packet: ActionPacket) -> bool:
        action = self._command_action_from_packet(packet)
        if packet.requires_confirmation or action.requires_confirmation:
            return True
        if self.config.command_confirmation_policy == "low_risk_auto":
            return action.risk_level != "low" or action.network_required or action.writes_files or action.destructive_risk
        return self.config.command_confirmation_policy in {"ask", "session", "always"}

    def _command_safety_errors(self, context: ReActExecutionContext, packet: ActionPacket) -> List[str]:
        action = self._command_action_from_packet(packet)
        errors: List[str] = []
        if not action.command.strip():
            errors.append("command is required")
        if action.risk_level == "blocked":
            errors.append("command risk_level is blocked")
        if action.destructive_risk:
            errors.append("destructive command risk is blocked")
        if action.shell:
            errors.append("direct shell selection is not allowed")
        if action.network_required:
            errors.append("network command execution is blocked in Step 14")
        if any(marker in action.command for marker in COMMAND_SHELL_METACHARS):
            errors.append("shell metacharacters are not allowed")
        lowered = f" {action.command.lower()} "
        for keyword in COMMAND_DANGEROUS_KEYWORDS:
            if keyword.lower() in lowered:
                errors.append(f"dangerous command keyword: {keyword.strip()}")
                break

        cwd_path = self._resolve_workspace_path(context, action.cwd)
        if cwd_path is None:
            errors.append(f"cwd is outside workspace: {action.cwd}")
        for target in action.target_paths:
            if self._resolve_workspace_path(context, str(target)) is None:
                errors.append(f"target path is outside workspace: {target}")
        return errors

    def _resolve_workspace_path(self, context: ReActExecutionContext, path_text: str) -> Any | None:
        from pathlib import Path

        root = self.config.workspace_root.resolve()
        path = Path(path_text or ".")
        resolved = (root / path).resolve() if not path.is_absolute() else path.resolve()
        if root not in [resolved, *resolved.parents]:
            return None
        return resolved

    def _command_blocked_observation(
        self,
        context: ReActExecutionContext,
        packet: ActionPacket,
        errors: List[str],
        *,
        attempt: int,
    ) -> ObservationPacket:
        message = "Command action blocked before Tool layer execution."
        return self._observation_from_packet(
            context,
            packet,
            attempt=attempt,
            success=False,
            message=message,
            error="; ".join(errors),
            code=COMMAND_BLOCKED_CODE,
            data={"errors": errors, "command_action": self._command_action_from_packet(packet).to_dict()},
            model_consumable_observation={"success": False, "code": COMMAND_BLOCKED_CODE, "errors": errors},
            checker_result={"step_status": "blocked"},
        )

    def _confirmation_pending_observation(
        self,
        context: ReActExecutionContext,
        packet: ActionPacket,
        *,
        step: Any | None,
        attempt: int,
        safety_decision: SafetyDecision | None = None,
    ) -> ObservationPacket:
        confirmation_type = packet.confirmation_type or "confirmation"
        message = (
            packet.user_visible_message
            or getattr(step, "confirmation_reason", None)
            or (safety_decision.reason if safety_decision is not None else "")
            or packet.action_args.get("reason")
            or packet.action_args.get("message")
            or f"Confirmation required before {packet.action_type}."
        )
        preview_result: ToolResult | None = None
        preview_payload: Dict[str, Any] | None = None
        preview_ticket: Dict[str, Any] = {}
        if packet.action_type == "call_tool" and self._supports_confirmation_preview():
            preview_result = self._prepare_tool_confirmation_preview(context, packet, step=step)
            if preview_result is not None:
                if not preview_result.success or preview_result.code != ToolErrorCode.DRY_RUN_PREVIEW.value:
                    preview_error = preview_result.error or preview_result.message or "Tool preview failed."
                    return self._observation_from_packet(
                        context,
                        packet,
                        attempt=attempt,
                        success=False,
                        message="Confirmation preview could not be prepared.",
                        error=str(preview_error),
                        code=preview_result.code or TOOL_EXECUTION_FAILED_CODE,
                        data=preview_result.data,
                        raw_observation=preview_result,
                        model_consumable_observation={
                            "success": False,
                            "code": preview_result.code or TOOL_EXECUTION_FAILED_CODE,
                            "message": "Confirmation preview could not be prepared.",
                            "error": str(preview_error),
                        },
                        checker_result={"step_status": "failed", "preview_required": True},
                    )
                preview_ticket = self._confirmation_preview_ticket(preview_result)
                if not preview_ticket.get("confirmation_id") or not preview_ticket.get("call_id") or not preview_ticket.get("preview_hash"):
                    return self._observation_from_packet(
                        context,
                        packet,
                        attempt=attempt,
                        success=False,
                        message="Confirmation preview did not produce a verifiable ticket.",
                        error="Confirmation preview must include confirmation_id, call_id, and preview_hash.",
                        code=TOOL_EXECUTION_FAILED_CODE,
                        data={"preview": preview_ticket.get("preview")},
                        model_consumable_observation={
                            "success": False,
                            "code": TOOL_EXECUTION_FAILED_CODE,
                            "message": "Confirmation preview did not produce a verifiable ticket.",
                        },
                        checker_result={"step_status": "failed", "preview_required": True},
                    )
                preview_payload = preview_ticket.get("preview")
        context.requires_user_input = True
        context.user_input_request = str(message)
        context.pending_confirmation = PendingConfirmation(
            execution_id=context.execution_id,
            plan_id=context.plan_id,
            task_id=packet.task_id or getattr(step, "task_id", None),
            step_id=packet.step_id or getattr(step, "id", None),
            confirmation_type=confirmation_type,
            confirmation_message=str(message),
            pending_action=packet,
            session_id=self._task_value(context.task, "session_id"),
            packet_id=packet.packet_id,
            confirmation_id=preview_ticket.get("confirmation_id"),
            call_id=preview_ticket.get("call_id"),
            preview_hash=preview_ticket.get("preview_hash"),
            preview_summary=preview_ticket.get("preview_summary"),
            affected_resources=list(preview_ticket.get("affected_resources") or []),
        )
        self._mark_step_waiting(context, context.pending_confirmation.step_id, str(message), CONFIRMATION_PENDING_CODE)
        context.event_stream.emit_event(
            "confirmation_requested",
            str(message),
            payload={
                "confirmation_type": confirmation_type,
                "pending_confirmation": context.pending_confirmation.to_dict(),
                "preview": preview_payload,
                "confirmation_id": context.pending_confirmation.confirmation_id,
                "call_id": context.pending_confirmation.call_id,
                "preview_hash": context.pending_confirmation.preview_hash,
                "affected_resources": list(context.pending_confirmation.affected_resources),
                "safety": safety_decision.to_dict() if safety_decision is not None else None,
            },
            task_id=context.pending_confirmation.task_id,
            step_id=context.pending_confirmation.step_id,
        )
        pending_data: Dict[str, Any] = {
            "pending_confirmation": context.pending_confirmation.to_dict(),
        }
        if preview_payload is not None:
            pending_data["preview"] = preview_payload
        return self._observation_from_packet(
            context,
            packet,
            attempt=attempt,
            success=False,
            message=str(message),
            error=str(message),
            code=CONFIRMATION_PENDING_CODE,
            data=pending_data,
            model_consumable_observation={
                "success": False,
                "requires_user_input": True,
                "code": CONFIRMATION_PENDING_CODE,
                "message": str(message),
                "confirmation_id": context.pending_confirmation.confirmation_id,
                "call_id": context.pending_confirmation.call_id,
                "preview_hash": context.pending_confirmation.preview_hash,
                "affected_resources": list(context.pending_confirmation.affected_resources),
                "preview": preview_payload,
                "safety": safety_decision.to_dict() if safety_decision is not None else None,
            },
            checker_result={
                "step_status": "waiting_user",
                "preview_required": preview_result is not None,
                "safety": safety_decision.to_dict() if safety_decision is not None else {"code": SAFETY_CONFIRMATION_REQUIRED_CODE},
            },
        )

    def _supports_confirmation_preview(self) -> bool:
        manager = self.tool_manager
        return bool(
            manager is not None
            and hasattr(manager, "execute")
            and getattr(manager, "runtime", None) is not None
        )

    def _prepare_tool_confirmation_preview(
        self,
        context: ReActExecutionContext,
        packet: ActionPacket,
        *,
        step: Any | None,
    ) -> ToolResult | None:
        spec = self.tool_registry.get(str(packet.action_target or ""))
        if spec is None:
            return None
        input_args, input_errors = self._prepare_tool_args(context, packet, step, spec)
        if input_errors:
            return ToolResult.fail(
                "Tool input references could not be resolved for confirmation preview.",
                code=TOOL_INPUT_REF_MISSING_CODE,
                data={"errors": input_errors},
            )
        try:
            request = self._build_tool_call_request(
                context,
                packet,
                step=step,
                args=input_args,
                trusted_confirmation=False,
                dry_run=True,
            )
            return self._coerce_tool_result(self.tool_manager.execute(request))
        except Exception as exc:
            return ToolResult.fail(
                f"Confirmation preview failed: {exc}",
                code=TOOL_EXECUTION_EXCEPTION_CODE,
            )

    def _confirmation_preview_ticket(self, preview_result: ToolResult) -> Dict[str, Any]:
        metadata = preview_result.metadata if isinstance(preview_result.metadata, dict) else {}
        output_control = metadata.get("output_control")
        output_control = output_control if isinstance(output_control, dict) else {}
        preview = output_control.get("preview")
        if preview is None and isinstance(preview_result.data, dict):
            preview = preview_result.data.get("preview")
        affected_resources = output_control.get("affected_resources")
        if not isinstance(affected_resources, list) and isinstance(preview_result.data, dict):
            affected_resources = preview_result.data.get("affected_resources")
        if not isinstance(affected_resources, list):
            affected_resources = []
        return {
            "confirmation_id": new_id("confirmation"),
            "call_id": preview_result.call_id or None,
            "preview_hash": output_control.get("preview_hash"),
            "preview_summary": preview_result.to_text(),
            "affected_resources": [str(item) for item in affected_resources],
            "preview": sanitize_sensitive(preview),
        }

    def _safety_blocked_observation(
        self,
        context: ReActExecutionContext,
        packet: ActionPacket,
        decision: SafetyDecision,
        *,
        attempt: int,
    ) -> ObservationPacket:
        context.event_stream.emit_event(
            "system_notice",
            decision.reason,
            payload={"safety": decision.to_dict(), "status": "blocked"},
            task_id=packet.task_id,
            step_id=packet.step_id,
        )
        return self._observation_from_packet(
            context,
            packet,
            attempt=attempt,
            success=False,
            message=decision.reason,
            error=decision.reason,
            code=SAFETY_BLOCKED_CODE,
            data={"safety": decision.to_dict()},
            model_consumable_observation={
                "success": False,
                "code": SAFETY_BLOCKED_CODE,
                "message": decision.reason,
                "safety": decision.to_dict(),
            },
            checker_result={"step_status": "blocked", "safety": decision.to_dict()},
        )

    def _fallback_tool_names(self, step: Any | None) -> List[str] | None:
        names = set(str(tool) for tool in list(getattr(step, "fallback_tools", []) or []) if tool)
        tool_name = getattr(step, "tool_name", None)
        if tool_name:
            spec = self.tool_registry.get(str(tool_name))
            if spec is not None:
                names.update(spec.fallback_tools)
        return sorted(names) if names else None

    def _record_observation(
        self,
        context: ReActExecutionContext,
        observation: ObservationPacket,
        *,
        output_key: str | None = None,
    ) -> ObservationPacket:
        existing = context.observation_store.get(observation.observation_id)
        if existing is not None:
            return existing
        context.observation_store.add(observation, output_key=output_key)
        state = context.step_states.get(str(observation.step_id)) if observation.step_id else None
        if state is not None:
            state.last_action_id = observation.packet_id
            state.last_observation_id = observation.observation_id
            state.attempts = max(state.attempts, observation.attempt)
            if observation.code:
                state.error_code = observation.code
            if observation.message or observation.error:
                state.message = observation.message or observation.error or ""
        context.event_stream.emit_event(
            "observation_created",
            observation.message or observation.error or "Observation created.",
            payload=self._observation_event_payload(observation),
            visible_to_user=observation.visible_to_user,
            task_id=observation.task_id,
            step_id=observation.step_id,
        )
        self.execution_logger.log_observation(context, observation)
        return observation

    def _observation_from_packet(
        self,
        context: ReActExecutionContext,
        packet: ActionPacket,
        *,
        attempt: int,
        success: bool,
        message: str = "",
        error: str | None = None,
        code: str | None = None,
        data: Any = None,
        raw_observation: Any = None,
        model_consumable_observation: Any = None,
        observation_mode: str | None = None,
        data_summary: str | None = None,
        included_fields: List[str] | None = None,
        raw_ref: str | None = None,
        artifact_ref: str | None = None,
        checker_result: Dict[str, Any] | None = None,
        fallback_used: bool = False,
        fallback_type: str | None = None,
        visible_to_user: bool = True,
        input_args: Dict[str, Any] | None = None,
        started_at: str | None = None,
        finished_at: str | None = None,
        duration_ms: int = 0,
    ) -> ObservationPacket:
        started = started_at or utc_now_iso()
        finished = finished_at or started
        observation_action_type = packet.action_type if packet.action_type in ACTION_TYPES else "fail"
        return ObservationPacket(
            execution_id=context.execution_id,
            plan_id=context.plan_id,
            task_id=packet.task_id,
            step_id=packet.step_id,
            packet_id=packet.packet_id,
            attempt=attempt,
            action_type=observation_action_type,
            action_target=packet.action_target,
            tool_name=packet.action_target if packet.action_type in {"call_tool", "fallback_to_tool"} else None,
            input_args=input_args if input_args is not None else dict(packet.action_args),
            success=success,
            data=data,
            message=message,
            error=error,
            code=code,
            raw_observation=raw_observation,
            model_consumable_observation=model_consumable_observation if model_consumable_observation is not None else data or message,
            observation_mode=observation_mode,
            data_summary=data_summary,
            included_fields=list(included_fields or []),
            raw_ref=raw_ref,
            artifact_ref=artifact_ref,
            started_at=started,
            finished_at=finished,
            duration_ms=duration_ms,
            fallback_used=fallback_used,
            fallback_type=fallback_type,
            checker_result=checker_result or {},
            visible_to_user=visible_to_user,
        )

    def _unimplemented_action_observation(
        self,
        context: ReActExecutionContext,
        packet: ActionPacket,
        *,
        attempt: int,
        code: str,
        next_step: str,
    ) -> ObservationPacket:
        message = f"{packet.action_type} is routed by dispatcher, but real execution is not implemented before {next_step}."
        return self._observation_from_packet(
            context,
            packet,
            attempt=attempt,
            success=False,
            message=message,
            error=message,
            code=code,
            model_consumable_observation={"success": False, "code": code, "message": message},
        )

    def _build_tool_call_request(
        self,
        context: ReActExecutionContext,
        packet: ActionPacket,
        *,
        step: Any | None,
        args: Dict[str, Any],
        trusted_confirmation: bool,
        confirmation_ticket: PendingConfirmation | None = None,
        dry_run: bool = False,
    ) -> ToolCallRequest:
        """Translate an executor-validated ActionPacket into the Tools protocol."""
        task_id = packet.task_id or getattr(step, "task_id", None)
        step_id = packet.step_id or getattr(step, "id", None)
        task_value = context.task

        request_args = {
            key: value
            for key, value in dict(args).items()
            if key not in TOOL_EXECUTOR_CONTROL_FIELDS
        }
        capability_values = self._tool_session_capabilities(task_value)
        spec = self.tool_registry.get(str(packet.action_target or ""))
        requested_timeout = request_args.get("timeout_seconds")
        timeout_seconds = None
        if requested_timeout is not None and not isinstance(requested_timeout, bool):
            try:
                timeout_seconds = max(int(requested_timeout), 1)
            except (TypeError, ValueError):
                timeout_seconds = None

        context_payload = ToolCallContext(
            trace_id=context.source_trace_id or self._task_value(task_value, "trace_id"),
            execution_id=context.execution_id,
            plan_id=context.plan_id,
            task_id=str(task_id) if task_id else None,
            step_id=str(step_id) if step_id else None,
            packet_id=packet.packet_id,
            session_id=self._task_value(task_value, "session_id"),
            user_id=self._task_value(task_value, "user_id"),
            workspace_root=self.config.workspace_root,
            source=ToolCallSource.REACT_EXECUTOR.value,
            initiated_by="system" if trusted_confirmation else "model",
        )
        options = ToolCallOptions(
            timeout_seconds=timeout_seconds,
            dry_run=bool(dry_run or self._task_value(task_value, "dry_run", False)),
            require_confirmation=self._action_requires_confirmation(packet, step),
            confirmed=bool(trusted_confirmation),
            approval_scope=(
                str(args.get("approval_scope"))
                if args.get("approval_scope") in {"one_call", "current_step", "session"}
                else None
            ),
            confirmation_id=(
                confirmation_ticket.confirmation_id
                if trusted_confirmation and confirmation_ticket is not None
                else None
            ),
            preview_hash=(
                confirmation_ticket.preview_hash
                if trusted_confirmation and confirmation_ticket is not None
                else None
            ),
            approved_at=utc_now_iso() if trusted_confirmation and confirmation_ticket is not None else None,
            approval_source="user" if trusted_confirmation else None,
            allow_read_workspace=capability_values["allow_read_workspace"],
            allow_write_workspace=capability_values["allow_write_workspace"],
            allow_network=capability_values["allow_network"],
            allow_command=capability_values["allow_command"],
            allow_shell_command=capability_values["allow_shell_command"],
            allow_mcp=capability_values["allow_mcp"],
            max_output_chars=self._task_int(task_value, "max_output_chars"),
            max_raw_output_chars=self._task_int(task_value, "max_raw_output_chars"),
            max_observation_chars=self._task_int(task_value, "max_observation_chars"),
            observation_mode=self._resolve_tool_observation_mode(task_value, step, packet, spec),
        )
        return ToolCallRequest(
            tool_name=str(packet.action_target or ""),
            args=request_args,
            context=context_payload,
            options=options,
        )

    def _tool_session_capabilities(self, task: Any) -> Dict[str, bool]:
        defaults = {
            "allow_read_workspace": True,
            "allow_write_workspace": False,
            "allow_network": False,
            "allow_command": False,
            "allow_shell_command": False,
            "allow_mcp": False,
        }
        values: Dict[str, Any] = {}
        for field_name in ("session_capabilities", "capabilities", "permissions", "tool_permissions"):
            candidate = self._task_value(task, field_name)
            if isinstance(candidate, dict):
                values.update(candidate)
        for field_name in defaults:
            direct_value = self._task_value(task, field_name)
            if isinstance(direct_value, bool):
                values[field_name] = direct_value
        return {
            field_name: values.get(field_name, default)
            if isinstance(values.get(field_name, default), bool)
            else default
            for field_name, default in defaults.items()
        }

    def _task_value(self, task: Any, field_name: str, default: Any = None) -> Any:
        if isinstance(task, dict):
            return task.get(field_name, default)
        return getattr(task, field_name, default)

    def _task_int(self, task: Any, field_name: str) -> int | None:
        value = self._task_value(task, field_name)
        if value is None or isinstance(value, bool):
            return None
        try:
            return max(int(value), 0)
        except (TypeError, ValueError):
            return None

    def _resolve_tool_observation_mode(
        self,
        task: Any,
        step: Any | None,
        packet: ActionPacket,
        spec: Any,
    ) -> str | None:
        for value in (
            self._task_value(task, "observation_mode"),
            self._step_observation_mode(step),
            (packet.action_args or {}).get("observation_mode"),
            getattr(spec, "default_observation_mode", None),
        ):
            if isinstance(value, str):
                normalized = value.strip().lower()
                if normalized in {"minimal", "standard", "full"}:
                    return normalized
        return None

    def _step_observation_mode(self, step: Any | None) -> str | None:
        if step is None:
            return None
        value = getattr(step, "observation_mode", None)
        if value is None:
            metadata = getattr(step, "metadata", None)
            if isinstance(metadata, dict):
                value = metadata.get("observation_mode")
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"minimal", "standard", "full"}:
                return normalized
        return None

    def _prepare_tool_args(self, context: ReActExecutionContext, packet: ActionPacket, step: Any | None, spec: Any) -> tuple[Dict[str, Any], List[str]]:
        merged_args: Dict[str, Any] = {}
        if step is not None:
            merged_args.update(getattr(step, "args", {}) or {})
        merged_args.update(packet.action_args or {})

        properties = spec.parameters_schema.get("properties", {}) if isinstance(spec.parameters_schema, dict) else {}
        control_keys = TOOL_EXECUTOR_CONTROL_FIELDS
        if properties:
            tool_args = {key: value for key, value in merged_args.items() if key in properties and key not in control_keys}
        else:
            tool_args = {key: value for key, value in merged_args.items() if key not in control_keys}

        refs = self._tool_input_refs(packet, step)
        if not refs:
            return tool_args, []

        resolved = context.observation_store.resolve_input_refs(refs)
        missing = [ref for ref, value in resolved.items() if isinstance(value, dict) and value.get("missing")]
        if missing:
            return tool_args, [f"input_from reference is missing: {ref}" for ref in missing]

        injection_text = self._resolved_refs_to_text(resolved)
        target_param = self._tool_injection_param(spec.name, properties, tool_args)
        if target_param and not self._has_value(tool_args.get(target_param)):
            tool_args[target_param] = injection_text
        return tool_args, []

    def _tool_input_refs(self, packet: ActionPacket, step: Any | None) -> List[str]:
        packet_refs = packet.action_args.get("input_from") if isinstance(packet.action_args, dict) else None
        if isinstance(packet_refs, str):
            return [packet_refs]
        if isinstance(packet_refs, list):
            return [str(ref) for ref in packet_refs if str(ref).strip()]
        return [str(ref) for ref in list(getattr(step, "input_from", []) or []) if str(ref).strip()]

    def _tool_injection_param(self, tool_name: str, properties: Dict[str, Any], tool_args: Dict[str, Any]) -> str | None:
        preferred = {
            "text_processor": ["text"],
            "translator": ["text"],
            "file_writer": ["content"],
            "write_file": ["content"],
            "search_tool": ["query"],
            "math_calculator": ["expression"],
            "document_parser": ["file_path"],
        }
        candidates = preferred.get(tool_name, []) + ["text", "content", "query", "file_path", "expression"]
        for param in candidates:
            if properties and param not in properties:
                continue
            if not self._has_value(tool_args.get(param)):
                return param
        return None

    def _resolved_refs_to_text(self, resolved: Dict[str, Any]) -> str:
        values: List[str] = []
        for ref, value in resolved.items():
            text = self._resolved_ref_value_to_text(value)
            values.append(f"{ref}: {text}" if len(resolved) > 1 else text)
        return "\n".join(values)

    def _resolved_ref_value_to_text(self, value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            if self._has_value(value.get("data")):
                data = value.get("data")
                return data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
            if self._has_value(value.get("message")):
                return str(value.get("message"))
            if self._has_value(value.get("error")):
                return str(value.get("error"))
        return json.dumps(value, ensure_ascii=False)

    def _coerce_tool_result(self, raw_result: Any) -> ToolResult:
        if isinstance(raw_result, ToolResult):
            return raw_result
        return ToolResult.ok(data=raw_result, message=str(raw_result) if raw_result is not None else "")

    def _emit_command_finished(
        self,
        context: ReActExecutionContext,
        packet: ActionPacket,
        command_id: str,
        tool_result: ToolResult,
        duration_ms: int,
    ) -> None:
        data = tool_result.data if isinstance(tool_result.data, dict) else {}
        context.event_stream.emit_event(
            "command_finished",
            tool_result.to_text(),
            payload={
                "command_id": command_id,
                "command": data.get("command", packet.action_args.get("command")),
                "cwd": data.get("cwd", packet.action_args.get("cwd")),
                "exit_code": data.get("exit_code"),
                "stdout_summary": data.get("stdout_summary", ""),
                "stderr_summary": data.get("stderr_summary", tool_result.error or ""),
                "success": tool_result.success,
                "code": tool_result.code,
                "duration_ms": duration_ms,
            },
            task_id=packet.task_id,
            step_id=packet.step_id,
        )

    def _build_model_action_prompt(self, context: ReActExecutionContext, packet: ActionPacket, step: Any | None) -> tuple[str, Dict[str, Any], List[str]]:
        input_payload, input_errors = self._model_action_input_payload(context, packet, step)
        action_args = packet.action_args or {}
        prompt = action_args.get("prompt") or action_args.get("instruction") or action_args.get("task") or action_args.get("goal")
        if not isinstance(prompt, str) or not prompt.strip():
            prompt = getattr(step, "description", "") or context.user_input
        output_requirements = (
            action_args.get("output_requirements")
            or action_args.get("expected_output")
            or action_args.get("format")
            or getattr(step, "expected_output", "")
        )
        prompt_payload = {
            "instruction": prompt,
            "output_requirements": output_requirements,
            "user_input": context.user_input,
            "history": context.history,
            "plan": {
                "plan_id": context.plan_id,
                "goal": getattr(context.plan, "goal", ""),
                "mode": getattr(context.plan, "mode", ""),
                "summary": getattr(context.plan, "user_facing_summary", ""),
            },
            "current_step": self._step_payload(step),
            "input": input_payload,
            "safety": {
                "do_not_execute_tools": True,
                "do_not_return_action_packet": True,
                "return_only_requested_content": True,
            },
        }
        return (
            "Generate an intermediate model result for ReActExecutor.\n"
            "Do not call tools. Do not return an ActionPacket. Return only the requested content.\n"
            f"{json.dumps(prompt_payload, ensure_ascii=False, indent=2)}"
        ), input_payload, input_errors

    def _model_action_input_payload(self, context: ReActExecutionContext, packet: ActionPacket, step: Any | None) -> tuple[Dict[str, Any], List[str]]:
        action_args = packet.action_args or {}
        payload: Dict[str, Any] = {}
        for key in ("input", "context"):
            if self._has_value(action_args.get(key)):
                payload[key] = action_args[key]

        refs = self._tool_input_refs(packet, step)
        if not refs:
            return payload, []

        resolved = context.observation_store.resolve_input_refs(
            refs,
            compact=True,
            max_value_chars=self.config.max_model_observation_chars,
        )
        missing = [ref for ref, value in resolved.items() if isinstance(value, dict) and value.get("missing")]
        if missing:
            return payload, [f"input_from reference is missing: {ref}" for ref in missing]
        payload["input_from"] = resolved
        return payload, []

    def _step_payload(self, step: Any | None) -> Dict[str, Any] | None:
        if step is None:
            return None
        if hasattr(step, "to_dict") and callable(step.to_dict):
            return step.to_dict()
        return {
            "id": getattr(step, "id", None),
            "task_id": getattr(step, "task_id", None),
            "description": getattr(step, "description", ""),
            "step_type": getattr(step, "step_type", None),
            "expected_output": getattr(step, "expected_output", ""),
        }

    def _payload_summary(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        summary: Dict[str, Any] = {}
        for key, value in sanitize_sensitive(payload).items():
            if isinstance(value, str):
                summary[key] = self._truncate_summary_text(value, 200)
            elif isinstance(value, dict):
                summary[key] = {"type": "object", "keys": sorted(str(item) for item in value.keys())[:10]}
            elif isinstance(value, list):
                summary[key] = {"type": "list", "items": len(value)}
            else:
                summary[key] = value
        return summary

    def _action_event_payload(self, packet: ActionPacket, *, step: Any | None) -> Dict[str, Any]:
        action_args = packet.action_args or {}
        input_from = action_args.get("input_from")
        if input_from is None and step is not None:
            input_from = list(getattr(step, "input_from", []) or [])
        return {
            "packet_id": packet.packet_id,
            "action_type": packet.action_type,
            "action_target": packet.action_target,
            "action_args_summary": self._payload_summary(action_args),
            "action_arg_keys": sorted(str(key) for key in action_args.keys()),
            "input_from": input_from,
            "confidence": packet.confidence,
            "requires_confirmation": packet.requires_confirmation,
            "confirmation_type": packet.confirmation_type if packet.requires_confirmation else None,
        }

    def _action_packet_public_summary(self, packet: ActionPacket | None) -> Dict[str, Any]:
        if packet is None:
            return {}
        return {
            "packet_id": packet.packet_id,
            "action_type": packet.action_type,
            "action_target": packet.action_target,
            "action_arg_keys": sorted(str(key) for key in (packet.action_args or {}).keys()),
            "requires_confirmation": packet.requires_confirmation,
            "confidence": packet.confidence,
        }

    def _tool_started_event_payload(self, tool_call_id: str, tool_name: str, input_args: Dict[str, Any], spec: Any) -> Dict[str, Any]:
        return {
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "input_summary": self._payload_summary(input_args),
            "input_arg_keys": sorted(str(key) for key in input_args.keys()),
            "risk_level": getattr(spec, "risk_level", None),
            "requires_confirmation": bool(getattr(spec, "requires_confirmation", False)),
        }

    def _tool_finished_event_payload(
        self,
        tool_call_id: str,
        tool_name: str,
        tool_result: ToolResult,
        code: str | None,
        duration_ms: int,
    ) -> Dict[str, Any]:
        metadata = tool_result.metadata if isinstance(tool_result.metadata, dict) else {}
        return {
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "success": tool_result.success,
            "code": code,
            "duration_ms": duration_ms,
            "message": self._truncate_summary_text(tool_result.to_text(), 300),
            "data_summary": self._tool_result_data_summary(tool_result.data),
            "event_summary": metadata.get("event_summary") or metadata.get("preview_summary"),
            "event_details": metadata.get("event_details"),
            "affected_resources": metadata.get("affected_resources"),
            "raw_output_truncated": bool(tool_result.raw_output_truncated),
        }

    def _model_message_event_payload(self, packet: ActionPacket, text: str, raw_response: Any, duration_ms: int) -> Dict[str, Any]:
        return {
            "packet_id": packet.packet_id,
            "duration_ms": duration_ms,
            "output_summary": self._raw_output_summary(raw_response if raw_response is not None else text, max_chars=600),
        }

    def _model_step_event_payload(
        self,
        packet: ActionPacket,
        *,
        model_call_id: str,
        success: bool | None = None,
        code: str | None = None,
        duration_ms: int | None = None,
        output: Any = None,
        input_payload: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model_call_id": model_call_id,
            "packet_id": packet.packet_id,
            "action_type": packet.action_type,
            "action_target": packet.action_target,
        }
        if success is not None:
            payload["success"] = success
        if code:
            payload["code"] = code
        if duration_ms is not None:
            payload["duration_ms"] = duration_ms
        if input_payload is not None:
            payload["input_summary"] = self._payload_summary(input_payload)
        if output is not None:
            payload["output_summary"] = self._raw_output_summary(output, max_chars=600)
        return payload

    def _tool_result_data_summary(self, data: Any) -> Any:
        sanitized = sanitize_sensitive(data)
        if isinstance(sanitized, dict):
            return self._payload_summary(sanitized)
        if isinstance(sanitized, list):
            return {"type": "list", "items": len(sanitized)}
        if isinstance(sanitized, str):
            return self._truncate_summary_text(sanitized, 300)
        return sanitized

    def _observation_event_payload(self, observation: ObservationPacket) -> Dict[str, Any]:
        return {
            "observation_id": observation.observation_id,
            "packet_id": observation.packet_id,
            "action_type": observation.action_type,
            "action_target": observation.action_target,
            "tool_name": observation.tool_name,
            "success": observation.success,
            "code": observation.code,
            "attempt": observation.attempt,
            "duration_ms": observation.duration_ms,
            "observation_mode": observation.observation_mode,
            "data_summary": observation.data_summary,
            "included_fields": list(observation.included_fields),
            "raw_ref": observation.raw_ref,
            "artifact_ref": observation.artifact_ref,
            "model_observation_summary": self._raw_output_summary(observation.model_consumable_observation, max_chars=600),
        }

    def _truncate_summary_text(self, text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        return text[:max_chars] + f"... [truncated {len(text) - max_chars} chars]"

    def _execution_summary_for_finish(self, context: ReActExecutionContext) -> Dict[str, Any]:
        step_statuses = {step_id: state.status for step_id, state in context.step_states.items()}
        task_statuses = {task_id: state.status for task_id, state in context.task_states.items()}
        return {
            "tasks": task_statuses,
            "steps": step_statuses,
            "completed_steps": [step_id for step_id, status in step_statuses.items() if status == "completed"],
            "failed_steps": [step_id for step_id, status in step_statuses.items() if status == "failed"],
            "skipped_steps": [step_id for step_id, status in step_statuses.items() if status == "skipped"],
            "blocked_steps": [step_id for step_id, status in step_statuses.items() if status == "blocked"],
            "observation_count": len(context.observation_store.observations),
        }

    def _handle_call_tool(
        self,
        context: ReActExecutionContext,
        packet: ActionPacket,
        *,
        step: Any | None,
        attempt: int,
        trusted_confirmation: bool = False,
        confirmation_ticket: PendingConfirmation | None = None,
    ) -> ObservationPacket:
        tool_name = packet.action_target or ""
        spec = self.tool_registry.get(tool_name)
        if spec is None:
            return self._observation_from_packet(
                context,
                packet,
                attempt=attempt,
                success=False,
                message=f"Tool is not registered: {tool_name}",
                error=f"Tool is not registered: {tool_name}",
                code=TOOL_NOT_AVAILABLE_CODE,
                model_consumable_observation={"success": False, "code": TOOL_NOT_AVAILABLE_CODE, "tool_name": tool_name},
            )
        if self.tool_manager is None or not hasattr(self.tool_manager, "execute"):
            return self._observation_from_packet(
                context,
                packet,
                attempt=attempt,
                success=False,
                message="ToolManager is unavailable.",
                error="ToolManager is unavailable.",
                code=TOOL_MANAGER_UNAVAILABLE_CODE,
                model_consumable_observation={"success": False, "code": TOOL_MANAGER_UNAVAILABLE_CODE, "tool_name": tool_name},
            )

        input_args, input_errors = self._prepare_tool_args(context, packet, step, spec)
        if input_errors:
            return self._observation_from_packet(
                context,
                packet,
                attempt=attempt,
                success=False,
                message="Tool input references could not be resolved.",
                error="; ".join(input_errors),
                code=TOOL_INPUT_REF_MISSING_CODE,
                data={"errors": input_errors},
                input_args=input_args,
                model_consumable_observation={"success": False, "code": TOOL_INPUT_REF_MISSING_CODE, "errors": input_errors},
            )

        validation = spec.validate_args(input_args)
        if not validation.success:
            return self._observation_from_packet(
                context,
                packet,
                attempt=attempt,
                success=False,
                message="Tool argument validation failed.",
                error="; ".join(validation.errors),
                code=TOOL_ARGUMENT_VALIDATION_FAILED_CODE,
                data=validation.to_dict(),
                input_args=input_args,
                model_consumable_observation={"success": False, "code": TOOL_ARGUMENT_VALIDATION_FAILED_CODE, "errors": validation.errors},
            )

        tool_call_id = packet.packet_id
        is_command = self._is_command_packet(packet)
        if is_command:
            context.event_stream.emit_event(
                "command_started",
                f"Running command: {input_args.get('command', '')}",
                payload={
                    "command_id": tool_call_id,
                    "command": input_args.get("command"),
                    "cwd": input_args.get("cwd"),
                    "purpose": input_args.get("purpose"),
                },
                task_id=packet.task_id,
                step_id=packet.step_id,
            )
        context.event_stream.emit_event(
            "tool_started",
            f"Running tool: {tool_name}",
            payload=self._tool_started_event_payload(tool_call_id, tool_name, input_args, spec),
            task_id=packet.task_id,
            step_id=packet.step_id,
        )
        started_at = utc_now_iso()
        started = perf_counter()
        try:
            request = self._build_tool_call_request(
                context,
                packet,
                step=step,
                args=input_args,
                trusted_confirmation=trusted_confirmation,
                confirmation_ticket=confirmation_ticket,
            )
            raw_result = self.tool_manager.execute(request)
            duration_ms = int((perf_counter() - started) * 1000)
            finished_at = utc_now_iso()
            tool_result = self._coerce_tool_result(raw_result)
            code = tool_result.code if tool_result.success else tool_result.code or TOOL_EXECUTION_FAILED_CODE
            message = tool_result.to_text()
            observation_view = build_tool_observation_view(tool_result, spec=spec, request=request, code=code)
            context.event_stream.emit_event(
                "tool_finished" if tool_result.success else "tool_failed",
                message,
                payload=self._tool_finished_event_payload(tool_call_id, tool_name, tool_result, code, duration_ms),
                task_id=packet.task_id,
                step_id=packet.step_id,
            )
            if is_command:
                self._emit_command_finished(context, packet, tool_call_id, tool_result, duration_ms)
            return self._observation_from_packet(
                context,
                packet,
                attempt=attempt,
                success=tool_result.success,
                message=message,
                error=tool_result.error,
                code=code,
                data=observation_view.data,
                raw_observation=raw_result,
                model_consumable_observation=observation_view.model_consumable_observation,
                observation_mode=observation_view.observation_mode,
                data_summary=observation_view.data_summary,
                included_fields=observation_view.included_fields,
                raw_ref=observation_view.raw_ref,
                artifact_ref=observation_view.artifact_ref,
                input_args=input_args,
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=duration_ms,
            )
        except Exception as exc:
            duration_ms = int((perf_counter() - started) * 1000)
            finished_at = utc_now_iso()
            error = str(exc)
            context.event_stream.emit_event(
                "tool_failed",
                error,
                payload={
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "success": False,
                    "code": TOOL_EXECUTION_EXCEPTION_CODE,
                    "duration_ms": duration_ms,
                    "error_summary": error,
                },
                task_id=packet.task_id,
                step_id=packet.step_id,
            )
            if is_command:
                self._emit_command_finished(
                    context,
                    packet,
                    tool_call_id,
                    ToolResult.fail(error, code=TOOL_EXECUTION_EXCEPTION_CODE),
                    duration_ms,
                )
            return self._observation_from_packet(
                context,
                packet,
                attempt=attempt,
                success=False,
                message=error,
                error=error,
                code=TOOL_EXECUTION_EXCEPTION_CODE,
                raw_observation=exc,
                model_consumable_observation={"success": False, "error": error, "code": TOOL_EXECUTION_EXCEPTION_CODE},
                input_args=input_args,
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=duration_ms,
            )

    def _handle_call_model(self, context: ReActExecutionContext, packet: ActionPacket, *, step: Any | None, attempt: int) -> ObservationPacket:
        if self.model_manager is None or not hasattr(self.model_manager, "generate"):
            return self._observation_from_packet(
                context,
                packet,
                attempt=attempt,
                success=False,
                message="ModelManager is unavailable.",
                error="ModelManager is unavailable.",
                code=MODEL_MANAGER_UNAVAILABLE_CODE,
                model_consumable_observation={"success": False, "code": MODEL_MANAGER_UNAVAILABLE_CODE},
            )

        prompt, input_payload, input_errors = self._build_model_action_prompt(context, packet, step)
        self.execution_logger.log_model_prompt(context, packet, prompt, input_payload)
        if input_errors:
            return self._observation_from_packet(
                context,
                packet,
                attempt=attempt,
                success=False,
                message="Model input references could not be resolved.",
                error="; ".join(input_errors),
                code=MODEL_INPUT_REF_MISSING_CODE,
                data={"errors": input_errors},
                input_args=dict(packet.action_args),
                model_consumable_observation={"success": False, "code": MODEL_INPUT_REF_MISSING_CODE, "errors": input_errors},
            )

        model_call_id = packet.packet_id
        context.event_stream.emit_event(
            "model_step_started",
            "Calling model for intermediate result.",
            payload=self._model_step_event_payload(
                packet,
                model_call_id=model_call_id,
                input_payload=input_payload,
            ),
            task_id=packet.task_id,
            step_id=packet.step_id,
        )
        context.event_stream.emit_event(
            "progress_message",
            "Calling model for intermediate result.",
            payload={
                "packet_id": packet.packet_id,
                "prompt_chars": len(prompt),
                "input_summary": self._payload_summary(input_payload),
            },
            task_id=packet.task_id,
            step_id=packet.step_id,
        )
        started_at = utc_now_iso()
        started = perf_counter()
        try:
            raw_response = require_model_content(self.model_manager.generate(prompt))
            duration_ms = int((perf_counter() - started) * 1000)
            finished_at = utc_now_iso()
            text = raw_response if isinstance(raw_response, str) else str(raw_response)
            context.event_stream.emit_event(
                "message_delta",
                text,
                payload=self._model_message_event_payload(packet, text, raw_response, duration_ms),
                task_id=packet.task_id,
                step_id=packet.step_id,
            )
            context.event_stream.emit_event(
                "model_step_finished",
                "Model step finished.",
                payload=self._model_step_event_payload(
                    packet,
                    model_call_id=model_call_id,
                    success=True,
                    duration_ms=duration_ms,
                    output=text,
                ),
                task_id=packet.task_id,
                step_id=packet.step_id,
            )
            return self._observation_from_packet(
                context,
                packet,
                attempt=attempt,
                success=True,
                message=text,
                data=text,
                raw_observation=raw_response,
                model_consumable_observation={"success": True, "content": text},
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=duration_ms,
                checker_result={"model_action": "intermediate_result"},
            )
        except ModelCallFailure as failure:
            duration_ms = int((perf_counter() - started) * 1000)
            finished_at = utc_now_iso()
            error = failure.result.error or "model call failed"
            context.event_stream.emit_event(
                "model_step_finished",
                error,
                payload=self._model_step_event_payload(
                    packet,
                    model_call_id=model_call_id,
                    success=False,
                    code=MODEL_CALL_EXCEPTION_CODE,
                    duration_ms=duration_ms,
                    output={"error": error},
                ),
                task_id=packet.task_id,
                step_id=packet.step_id,
            )
            return self._observation_from_packet(
                context,
                packet,
                attempt=attempt,
                success=False,
                message=error,
                error=error,
                code=MODEL_CALL_EXCEPTION_CODE,
                raw_observation=failure.result,
                model_consumable_observation={
                    "success": False,
                    "error": error,
                    "code": MODEL_CALL_EXCEPTION_CODE,
                },
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=duration_ms,
            )
        except Exception as exc:
            duration_ms = int((perf_counter() - started) * 1000)
            finished_at = utc_now_iso()
            error = str(exc)
            context.event_stream.emit_event(
                "model_step_finished",
                error,
                payload=self._model_step_event_payload(
                    packet,
                    model_call_id=model_call_id,
                    success=False,
                    code=MODEL_CALL_EXCEPTION_CODE,
                    duration_ms=duration_ms,
                    output={"error": error},
                ),
                task_id=packet.task_id,
                step_id=packet.step_id,
            )
            return self._observation_from_packet(
                context,
                packet,
                attempt=attempt,
                success=False,
                message=error,
                error=error,
                code=MODEL_CALL_EXCEPTION_CODE,
                raw_observation=exc,
                model_consumable_observation={"success": False, "error": error, "code": MODEL_CALL_EXCEPTION_CODE},
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=duration_ms,
            )

    def _handle_ask_user(self, context: ReActExecutionContext, packet: ActionPacket, *, step: Any | None, attempt: int) -> ObservationPacket:
        ask_type = str(packet.action_args.get("ask_type") or "clarification")
        message = str(packet.action_args.get("question") or packet.action_args.get("message") or packet.user_visible_message)
        context.requires_user_input = True
        context.user_input_request = message
        context.pending_confirmation = PendingConfirmation(
            execution_id=context.execution_id,
            plan_id=context.plan_id,
            task_id=packet.task_id or getattr(step, "task_id", None),
            step_id=packet.step_id or getattr(step, "id", None),
            confirmation_type=ask_type,
            confirmation_message=message,
            pending_action=packet,
        )
        self._mark_step_waiting(context, context.pending_confirmation.step_id, message, USER_INPUT_REQUIRED_CODE)
        context.event_stream.emit_event(
            "confirmation_requested",
            message,
            payload={
                "ask_type": ask_type,
                "pending_confirmation": context.pending_confirmation.to_dict(),
                "requires_user_input": True,
            },
            task_id=context.pending_confirmation.task_id,
            step_id=context.pending_confirmation.step_id,
        )
        return self._observation_from_packet(
            context,
            packet,
            attempt=attempt,
            success=False,
            message=message,
            error=message,
            code=USER_INPUT_REQUIRED_CODE,
            data={
                "ask_type": ask_type,
                "question": message,
                "pending_confirmation": context.pending_confirmation.to_dict(),
            },
            model_consumable_observation={
                "success": False,
                "requires_user_input": True,
                "ask_type": ask_type,
                "question": message,
                "code": USER_INPUT_REQUIRED_CODE,
            },
            checker_result={"step_status": "waiting_user"},
        )

    def _handle_retry(self, context: ReActExecutionContext, packet: ActionPacket, *, step: Any | None, attempt: int) -> ObservationPacket:
        target_step_id = str(packet.action_args.get("step_id") or packet.step_id or getattr(step, "id", "") or "")
        target_step = context.step_lookup.get(target_step_id) or step
        failed_observation = self._find_retry_target_observation(context, packet, target_step_id)
        if failed_observation is None:
            return self._observation_from_packet(
                context,
                packet,
                attempt=attempt,
                success=False,
                message="No failed tool or model action is available for retry.",
                error="No failed tool or model action is available for retry.",
                code=RETRY_TARGET_NOT_FOUND_CODE,
                model_consumable_observation={"success": False, "code": RETRY_TARGET_NOT_FOUND_CODE},
                checker_result={"step_status": "failed", "retry": {"target_step_id": target_step_id}},
            )

        checker_result = self.check_observation(
            context,
            failed_observation,
            step=target_step,
            current_step_turn=failed_observation.attempt,
        )
        decision = self.retry_policy.build_decision(
            failed_observation,
            checker_result,
            step=target_step,
            step_state=context.step_states.get(target_step_id),
        )
        if not decision.can_retry:
            self._log_retry_decision(
                context,
                packet=packet,
                failed_observation=failed_observation,
                decision=decision,
                outcome="exhausted" if decision.code == RETRY_EXHAUSTED_CODE else "rejected",
                success=False,
            )
            event_type = "retry_exhausted" if decision.code == RETRY_EXHAUSTED_CODE else "retry_scheduled"
            context.event_stream.emit_event(
                event_type,
                decision.reason,
                payload=decision.to_dict(),
                task_id=failed_observation.task_id,
                step_id=failed_observation.step_id,
            )
            return self._observation_from_packet(
                context,
                packet,
                attempt=attempt,
                success=False,
                message=decision.reason,
                error=decision.reason,
                code=decision.code,
                data=decision.to_dict(),
                model_consumable_observation={"success": False, "retry": decision.to_dict()},
                checker_result={"step_status": "failed", "retry": decision.to_dict()},
            )

        self._log_retry_decision(
            context,
            packet=packet,
            failed_observation=failed_observation,
            decision=decision,
            outcome="scheduled",
            success=None,
        )
        context.event_stream.emit_event(
            "retry_scheduled",
            f"Retrying step {target_step_id or failed_observation.step_id or ''}.",
            payload={
                **decision.to_dict(),
                "scheduled": True,
            },
            task_id=failed_observation.task_id,
            step_id=failed_observation.step_id,
        )
        state = context.step_states.get(target_step_id)
        if state is not None:
            state.status = "retrying"
            state.attempts = max(state.attempts, decision.next_attempt)
            state.error_code = failed_observation.code
            state.message = decision.reason
        try:
            self.retry_policy.wait(decision)
        except Exception as exc:
            sleep_error = str(exc)
            context.event_stream.emit_event(
                "retry_exhausted",
                sleep_error,
                payload={
                    **decision.to_dict(),
                    "code": RETRY_SLEEP_FAILED_CODE,
                    "sleep_error": sleep_error,
                },
                task_id=failed_observation.task_id,
                step_id=failed_observation.step_id,
            )
            return self._observation_from_packet(
                context,
                packet,
                attempt=attempt,
                success=False,
                message="Retry backoff failed.",
                error=sleep_error,
                code=RETRY_SLEEP_FAILED_CODE,
                data={"retry": decision.to_dict(), "sleep_error": sleep_error},
                model_consumable_observation={"success": False, "code": RETRY_SLEEP_FAILED_CODE},
                checker_result={"step_status": "failed", "retry": decision.to_dict()},
            )

        retry_packet = self._retry_packet_from_observation(context, packet, failed_observation)
        retry_observation = self.dispatch_action(
            context,
            retry_packet,
            step=target_step,
            attempt=decision.next_attempt,
            output_key=getattr(target_step, "output_key", None),
            confirmed=bool(packet.action_args.get("confirmed", False)),
        )
        retry_metadata = {
            **decision.to_dict(),
            "scheduled": True,
            "retried_from_observation_id": failed_observation.observation_id,
            "retried_packet_id": retry_packet.packet_id,
        }
        retry_observation.checker_result = {
            **dict(retry_observation.checker_result or {}),
            "retry": retry_metadata,
        }
        self._log_retry_decision(
            context,
            packet=packet,
            failed_observation=failed_observation,
            decision=decision,
            outcome="finished",
            success=retry_observation.success,
            retry_observation=retry_observation,
        )
        context.event_stream.emit_event(
            "retry_finished",
            retry_observation.message or retry_observation.error or "Retry finished.",
            payload={
                **retry_metadata,
                "success": retry_observation.success,
                "code": retry_observation.code,
                "observation_id": retry_observation.observation_id,
            },
            task_id=retry_observation.task_id,
            step_id=retry_observation.step_id,
        )
        return retry_observation

    def _log_retry_decision(
        self,
        context: ReActExecutionContext,
        *,
        packet: ActionPacket,
        failed_observation: ObservationPacket,
        decision: Any,
        outcome: str,
        success: bool | None,
        retry_observation: ObservationPacket | None = None,
    ) -> None:
        self.execution_logger.write_record(
            context,
            record_type="retry_decision",
            task_id=failed_observation.task_id or packet.task_id,
            step_id=failed_observation.step_id or packet.step_id,
            packet_id=packet.packet_id,
            action_type=packet.action_type,
            action_target=failed_observation.action_target,
            attempt=decision.next_attempt if outcome in {"scheduled", "finished"} else decision.retry_attempt,
            success=success,
            code=decision.code,
            observation_id=retry_observation.observation_id if retry_observation is not None else failed_observation.observation_id,
            metadata={
                "outcome": outcome,
                "retry": decision.to_dict(),
                "source_observation_id": failed_observation.observation_id,
                "source_packet_id": failed_observation.packet_id,
                "retry_observation_id": retry_observation.observation_id if retry_observation is not None else None,
                "retry_success": retry_observation.success if retry_observation is not None else None,
                "retry_code": retry_observation.code if retry_observation is not None else None,
            },
        )

    def _find_retry_target_observation(
        self,
        context: ReActExecutionContext,
        packet: ActionPacket,
        target_step_id: str,
    ) -> ObservationPacket | None:
        requested_observation_id = packet.action_args.get("observation_id")
        requested_packet_id = packet.action_args.get("packet_id") or packet.action_args.get("action_id")
        candidates = list(reversed(context.observation_store.observations))
        for observation in candidates:
            if requested_observation_id and observation.observation_id == requested_observation_id:
                return observation if self._is_retryable_observation_target(observation) else None
            if requested_packet_id and observation.packet_id == requested_packet_id:
                return observation if self._is_retryable_observation_target(observation) else None
        for observation in candidates:
            if target_step_id and observation.step_id != target_step_id:
                continue
            if self._is_retryable_observation_target(observation):
                return observation
        return None

    def _is_retryable_observation_target(self, observation: ObservationPacket) -> bool:
        return (
            not observation.success
            and observation.action_type in {"call_tool", "call_model", "fallback_to_tool", "fallback_to_model"}
        )

    def _retry_packet_from_observation(
        self,
        context: ReActExecutionContext,
        retry_packet: ActionPacket,
        failed_observation: ObservationPacket,
    ) -> ActionPacket:
        return ActionPacket(
            execution_id=context.execution_id,
            plan_id=context.plan_id,
            task_id=failed_observation.task_id or retry_packet.task_id,
            step_id=failed_observation.step_id or retry_packet.step_id,
            thought_summary=retry_packet.thought_summary or "Retry the previous failed action.",
            user_visible_message=f"Retrying {failed_observation.action_type}.",
            action_type=failed_observation.action_type,
            action_target=failed_observation.action_target,
            action_args=dict(failed_observation.input_args or {}),
            expected_observation=retry_packet.expected_observation,
            confidence=retry_packet.confidence,
            requires_confirmation=retry_packet.requires_confirmation,
            confirmation_type=retry_packet.confirmation_type,
            safety_notes=list(retry_packet.safety_notes or []),
            fallback_plan=dict(retry_packet.fallback_plan or {}),
        )

    def _handle_fallback_to_model(self, context: ReActExecutionContext, packet: ActionPacket, *, step: Any | None, attempt: int) -> ObservationPacket:
        failed_observation = self._find_fallback_target_observation(context, packet, step)
        if failed_observation is None:
            return self._fallback_target_missing_observation(context, packet, attempt=attempt)
        target_step = context.step_lookup.get(str(failed_observation.step_id or "")) or step
        checker_result = self.check_observation(context, failed_observation, step=target_step)
        decision = self.fallback_policy.build_decision(
            failed_observation,
            checker_result,
            step=target_step,
            requested_type="model",
            available_tools=self._available_tool_names(context),
            registry_fallback_tools=self._registry_fallback_tools(failed_observation.action_target),
        )
        if not decision.can_fallback:
            self._log_fallback_decision(
                context,
                packet=packet,
                failed_observation=failed_observation,
                decision=decision,
                outcome="blocked",
                success=False,
            )
            return self._fallback_blocked_observation(context, packet, decision, attempt=attempt)

        self._log_fallback_decision(
            context,
            packet=packet,
            failed_observation=failed_observation,
            decision=decision,
            outcome="scheduled",
            success=None,
        )
        context.event_stream.emit_event(
            "fallback_started",
            "Fallback to model started.",
            payload=decision.to_dict(),
            task_id=failed_observation.task_id,
            step_id=failed_observation.step_id,
        )
        fallback_packet = self._fallback_model_packet_from_observation(context, packet, failed_observation, decision)
        observation = self.dispatch_action(
            context,
            fallback_packet,
            step=target_step,
            attempt=max(failed_observation.attempt + 1, attempt),
            output_key=getattr(target_step, "output_key", None),
        )
        return self._finalize_fallback_observation(context, observation, decision)

    def _handle_fallback_to_tool(self, context: ReActExecutionContext, packet: ActionPacket, *, step: Any | None, attempt: int) -> ObservationPacket:
        failed_observation = self._find_fallback_target_observation(context, packet, step)
        if failed_observation is None:
            return self._fallback_target_missing_observation(context, packet, attempt=attempt)
        target_step = context.step_lookup.get(str(failed_observation.step_id or "")) or step
        checker_result = self.check_observation(context, failed_observation, step=target_step)
        decision = self.fallback_policy.build_decision(
            failed_observation,
            checker_result,
            step=target_step,
            requested_type="tool",
            requested_tool=packet.action_target,
            available_tools=self._available_tool_names(context),
            registry_fallback_tools=self._registry_fallback_tools(failed_observation.action_target),
        )
        if not decision.can_fallback:
            if decision.code == FALLBACK_TOOL_NOT_AVAILABLE_CODE:
                if self._step_allows_model_fallback(target_step, packet):
                    model_decision = self.fallback_policy.build_decision(
                        failed_observation,
                        checker_result,
                        step=target_step,
                        requested_type="model",
                        available_tools=self._available_tool_names(context),
                        registry_fallback_tools=self._registry_fallback_tools(failed_observation.action_target),
                    )
                    if model_decision.can_fallback:
                        self._log_fallback_decision(
                            context,
                            packet=packet,
                            failed_observation=failed_observation,
                            decision=model_decision,
                            outcome="scheduled",
                            success=None,
                        )
                        context.event_stream.emit_event(
                            "fallback_started",
                            "Fallback tool unavailable; fallback to model started.",
                            payload=model_decision.to_dict(),
                            task_id=failed_observation.task_id,
                            step_id=failed_observation.step_id,
                        )
                        fallback_packet = self._fallback_model_packet_from_observation(context, packet, failed_observation, model_decision)
                        observation = self.dispatch_action(
                            context,
                            fallback_packet,
                            step=target_step,
                            attempt=max(failed_observation.attempt + 1, attempt),
                            output_key=getattr(target_step, "output_key", None),
                        )
                        return self._finalize_fallback_observation(context, observation, model_decision)
            self._log_fallback_decision(
                context,
                packet=packet,
                failed_observation=failed_observation,
                decision=decision,
                outcome="blocked",
                success=False,
            )
            return self._fallback_blocked_observation(context, packet, decision, attempt=attempt)

        if decision.fallback_type == "model":
            self._log_fallback_decision(
                context,
                packet=packet,
                failed_observation=failed_observation,
                decision=decision,
                outcome="scheduled",
                success=None,
            )
            context.event_stream.emit_event(
                "fallback_started",
                "Fallback to model started.",
                payload=decision.to_dict(),
                task_id=failed_observation.task_id,
                step_id=failed_observation.step_id,
            )
            fallback_packet = self._fallback_model_packet_from_observation(context, packet, failed_observation, decision)
            observation = self.dispatch_action(
                context,
                fallback_packet,
                step=target_step,
                attempt=max(failed_observation.attempt + 1, attempt),
                output_key=getattr(target_step, "output_key", None),
            )
            return self._finalize_fallback_observation(context, observation, decision)

        self._log_fallback_decision(
            context,
            packet=packet,
            failed_observation=failed_observation,
            decision=decision,
            outcome="scheduled",
            success=None,
        )
        context.event_stream.emit_event(
            "fallback_started",
            f"Fallback to tool started: {decision.fallback_tool}.",
            payload=decision.to_dict(),
            task_id=failed_observation.task_id,
            step_id=failed_observation.step_id,
        )
        fallback_packet = self._fallback_tool_packet_from_observation(context, packet, failed_observation, decision)
        observation = self.dispatch_action(
            context,
            fallback_packet,
            step=target_step,
            attempt=max(failed_observation.attempt + 1, attempt),
            output_key=getattr(target_step, "output_key", None),
            confirmed=bool(packet.action_args.get("confirmed", False)),
        )
        return self._finalize_fallback_observation(context, observation, decision)

    def _log_fallback_decision(
        self,
        context: ReActExecutionContext,
        *,
        packet: ActionPacket,
        failed_observation: ObservationPacket,
        decision: Any,
        outcome: str,
        success: bool | None,
        fallback_observation: ObservationPacket | None = None,
    ) -> None:
        self.execution_logger.write_record(
            context,
            record_type="fallback_decision",
            task_id=failed_observation.task_id or packet.task_id,
            step_id=failed_observation.step_id or packet.step_id,
            packet_id=packet.packet_id,
            action_type=packet.action_type,
            action_target=decision.fallback_tool or packet.action_target or failed_observation.action_target,
            attempt=fallback_observation.attempt if fallback_observation is not None else failed_observation.attempt + 1,
            success=success,
            code=decision.code,
            observation_id=fallback_observation.observation_id if fallback_observation is not None else failed_observation.observation_id,
            metadata={
                "outcome": outcome,
                "fallback": decision.to_dict(),
                "source_observation_id": failed_observation.observation_id,
                "source_packet_id": failed_observation.packet_id,
                "fallback_observation_id": fallback_observation.observation_id if fallback_observation is not None else None,
                "fallback_success": fallback_observation.success if fallback_observation is not None else None,
                "fallback_code": fallback_observation.code if fallback_observation is not None else None,
            },
        )

    def _step_allows_model_fallback(self, step: Any | None, packet: ActionPacket) -> bool:
        if bool(packet.action_args.get("allow_model_fallback", False)):
            return True
        on_failure = str(getattr(step, "on_failure", "") or "").lower()
        return bool(getattr(step, "allow_model_reasoning", False) or on_failure in {"fallback_to_model", "fallback_model", "model", "fallback"})

    def _find_fallback_target_observation(
        self,
        context: ReActExecutionContext,
        packet: ActionPacket,
        step: Any | None,
    ) -> ObservationPacket | None:
        target_step_id = str(packet.action_args.get("step_id") or packet.step_id or getattr(step, "id", "") or "")
        requested_observation_id = packet.action_args.get("observation_id")
        requested_packet_id = packet.action_args.get("packet_id") or packet.action_args.get("action_id")
        candidates = list(reversed(context.observation_store.observations))
        for observation in candidates:
            if requested_observation_id and observation.observation_id == requested_observation_id:
                return observation if self._is_fallback_observation_target(observation) else None
            if requested_packet_id and observation.packet_id == requested_packet_id:
                return observation if self._is_fallback_observation_target(observation) else None
        for observation in candidates:
            if target_step_id and observation.step_id != target_step_id:
                continue
            if self._is_fallback_observation_target(observation):
                return observation
        return None

    def _is_fallback_observation_target(self, observation: ObservationPacket) -> bool:
        return (
            not observation.success
            and observation.action_type in {"call_tool", "call_model"}
        )

    def _fallback_target_missing_observation(self, context: ReActExecutionContext, packet: ActionPacket, *, attempt: int) -> ObservationPacket:
        message = "No failed tool or model action is available for fallback."
        return self._observation_from_packet(
            context,
            packet,
            attempt=attempt,
            success=False,
            message=message,
            error=message,
            code=FALLBACK_TARGET_NOT_FOUND_CODE,
            model_consumable_observation={"success": False, "code": FALLBACK_TARGET_NOT_FOUND_CODE},
            checker_result={"step_status": "failed", "fallback": {"code": FALLBACK_TARGET_NOT_FOUND_CODE}},
        )

    def _fallback_blocked_observation(self, context: ReActExecutionContext, packet: ActionPacket, decision: Any, *, attempt: int) -> ObservationPacket:
        context.event_stream.emit_event(
            "fallback_finished",
            decision.reason,
            payload={**decision.to_dict(), "success": False},
            task_id=packet.task_id,
            step_id=packet.step_id,
        )
        return self._observation_from_packet(
            context,
            packet,
            attempt=attempt,
            success=False,
            message=decision.reason,
            error=decision.reason,
            code=decision.code,
            data=decision.to_dict(),
            model_consumable_observation={"success": False, "fallback": decision.to_dict()},
            checker_result={"step_status": "failed", "fallback": decision.to_dict()},
        )

    def _finalize_fallback_observation(self, context: ReActExecutionContext, observation: ObservationPacket, decision: Any) -> ObservationPacket:
        fallback_metadata = {
            **decision.to_dict(),
            "scheduled": True,
            "fallback_observation_id": observation.observation_id,
            "success": observation.success,
            "result_code": observation.code,
        }
        observation.fallback_used = True
        observation.fallback_type = decision.fallback_type
        observation.checker_result = {
            **dict(observation.checker_result or {}),
            "fallback": fallback_metadata,
        }
        context.event_stream.emit_event(
            "fallback_finished",
            observation.message or observation.error or "Fallback finished.",
            payload=fallback_metadata,
            task_id=observation.task_id,
            step_id=observation.step_id,
        )
        source_observation = context.observation_store.get(str(decision.source_observation_id or ""))
        if source_observation is not None:
            packet = ActionPacket(
                execution_id=context.execution_id,
                plan_id=context.plan_id,
                task_id=observation.task_id,
                step_id=observation.step_id,
                action_type="fallback_to_tool" if decision.fallback_type == "tool" else "fallback_to_model",
                action_target=decision.fallback_tool,
                action_args={"fallback_reason": decision.reason},
            )
            self._log_fallback_decision(
                context,
                packet=packet,
                failed_observation=source_observation,
                decision=decision,
                outcome="finished",
                success=observation.success,
                fallback_observation=observation,
            )
        return observation

    def _fallback_tool_packet_from_observation(
        self,
        context: ReActExecutionContext,
        packet: ActionPacket,
        failed_observation: ObservationPacket,
        decision: Any,
    ) -> ActionPacket:
        args = self._fallback_tool_args(packet, failed_observation, decision)
        return ActionPacket(
            execution_id=context.execution_id,
            plan_id=context.plan_id,
            task_id=failed_observation.task_id or packet.task_id,
            step_id=failed_observation.step_id or packet.step_id,
            thought_summary=packet.thought_summary or "Use a fallback tool for the failed action.",
            user_visible_message=f"Using fallback tool: {decision.fallback_tool}.",
            action_type="call_tool",
            action_target=decision.fallback_tool,
            action_args=args,
            expected_observation=packet.expected_observation,
            confidence=packet.confidence,
            requires_confirmation=packet.requires_confirmation,
            confirmation_type=packet.confirmation_type,
            safety_notes=list(packet.safety_notes or []),
            fallback_plan=dict(packet.fallback_plan or {}),
        )

    def _fallback_tool_args(self, packet: ActionPacket, failed_observation: ObservationPacket, decision: Any) -> Dict[str, Any]:
        args = dict(failed_observation.input_args or {})
        args.update(packet.action_args or {})
        for key in ("step_id", "packet_id", "action_id", "observation_id"):
            args.pop(key, None)
        args.setdefault("fallback_reason", decision.reason)
        return args

    def _fallback_model_packet_from_observation(
        self,
        context: ReActExecutionContext,
        packet: ActionPacket,
        failed_observation: ObservationPacket,
        decision: Any,
    ) -> ActionPacket:
        source_payload = {
            "failed_action_type": failed_observation.action_type,
            "failed_action_target": failed_observation.action_target,
            "failed_input_args": failed_observation.input_args,
            "failed_code": failed_observation.code,
            "failed_error": failed_observation.error,
            "failed_message": failed_observation.message,
            "failed_model_observation": failed_observation.model_consumable_observation,
        }
        return ActionPacket(
            execution_id=context.execution_id,
            plan_id=context.plan_id,
            task_id=failed_observation.task_id or packet.task_id,
            step_id=failed_observation.step_id or packet.step_id,
            thought_summary=packet.thought_summary or "Use model fallback for the failed action.",
            user_visible_message="Using model fallback.",
            action_type="call_model",
            action_args={
                "goal": packet.action_args.get("goal") or f"Complete the step after {failed_observation.action_target or failed_observation.action_type} failed.",
                "input": source_payload,
                "context": {
                    "fallback_reason": decision.reason,
                    "user_input": context.user_input,
                },
                "input_from": list(packet.action_args.get("input_from", []) or []),
                "output_requirements": (
                    packet.action_args.get("output_requirements")
                    or packet.action_args.get("expected_output")
                    or packet.action_args.get("format")
                    or "Produce the best possible result and mention that a fallback was used."
                ),
            },
            expected_observation=packet.expected_observation,
            confidence=packet.confidence,
            requires_confirmation=False,
            safety_notes=list(packet.safety_notes or []),
            fallback_plan=dict(packet.fallback_plan or {}),
        )

    def _registry_fallback_tools(self, tool_name: str | None) -> List[str]:
        if not tool_name:
            return []
        spec = self.tool_registry.get(str(tool_name))
        if spec is None:
            return []
        return list(spec.fallback_tools)

    def _handle_skip_step(self, context: ReActExecutionContext, packet: ActionPacket, *, step: Any | None, attempt: int) -> ObservationPacket:
        reason = str(packet.action_args.get("reason") or packet.user_visible_message or "Step skipped.")
        return self._observation_from_packet(
            context,
            packet,
            attempt=attempt,
            success=True,
            message=reason,
            code=STEP_SKIPPED_CODE,
            data={"skipped": True, "reason": reason},
            model_consumable_observation={"success": True, "skipped": True, "reason": reason},
            checker_result={"step_status": "skipped"},
        )

    def _handle_finish(self, context: ReActExecutionContext, packet: ActionPacket, *, step: Any | None, attempt: int) -> ObservationPacket:
        final_answer = packet.final_answer or packet.user_visible_message or ""
        summary = self._execution_summary_for_finish(context)
        data = {
            "final_answer": final_answer,
            "summary": summary,
        }
        context.event_stream.emit_event(
            "final_answer",
            final_answer,
            payload=data,
            visible_to_user=False,
            task_id=packet.task_id,
            step_id=packet.step_id,
        )
        return self._observation_from_packet(
            context,
            packet,
            attempt=attempt,
            success=True,
            message=final_answer,
            data=data,
            model_consumable_observation={"success": True, "final_answer": final_answer, "summary": summary},
            checker_result={"execution_status": "completed", "summary": summary},
        )

    def _handle_fail(self, context: ReActExecutionContext, packet: ActionPacket, *, step: Any | None, attempt: int) -> ObservationPacket:
        reason = packet.final_answer or packet.action_args.get("reason") or packet.action_args.get("message") or packet.action_args.get("error") or "Action failed."
        return self._observation_from_packet(
            context,
            packet,
            attempt=attempt,
            success=False,
            message=str(reason),
            error=str(reason),
            code=ACTION_FAILED_CODE,
            model_consumable_observation={"success": False, "reason": str(reason), "code": ACTION_FAILED_CODE},
            checker_result={"execution_status": "failed"},
        )

    def _handle_request_replan(self, context: ReActExecutionContext, packet: ActionPacket, *, step: Any | None, attempt: int) -> ObservationPacket:
        reason = packet.request_replan_reason or packet.action_args.get("reason") or "Replan requested."
        context.request_replan = True
        context.replan_reason = str(reason)
        context.error_code = REQUEST_REPLAN_CODE
        context.event_stream.emit_event(
            "request_replan",
            str(reason),
            payload={"packet_id": packet.packet_id, "reason": str(reason)},
            visible_to_user=False,
            task_id=packet.task_id,
            step_id=packet.step_id,
        )
        return self._observation_from_packet(
            context,
            packet,
            attempt=attempt,
            success=False,
            message=str(reason),
            error=str(reason),
            code=REQUEST_REPLAN_CODE,
            data={"request_replan": True, "reason": str(reason)},
            model_consumable_observation={"success": False, "request_replan": True, "reason": str(reason), "code": REQUEST_REPLAN_CODE},
            checker_result={"execution_status": "request_replan"},
        )

    def _handle_blocked(self, context: ReActExecutionContext, packet: ActionPacket, *, step: Any | None, attempt: int) -> ObservationPacket:
        reason = packet.user_visible_message or packet.action_args.get("reason") or packet.action_args.get("message") or "Action blocked."
        return self._observation_from_packet(
            context,
            packet,
            attempt=attempt,
            success=False,
            message=str(reason),
            error=str(reason),
            code=ACTION_BLOCKED_CODE,
            model_consumable_observation={"success": False, "blocked": True, "reason": str(reason), "code": ACTION_BLOCKED_CODE},
            checker_result={"step_status": "blocked"},
        )

    def _handle_cancel(self, context: ReActExecutionContext, packet: ActionPacket, *, step: Any | None, attempt: int) -> ObservationPacket:
        reason = packet.user_visible_message or packet.action_args.get("reason") or packet.action_args.get("message") or "Action cancelled."
        return self._observation_from_packet(
            context,
            packet,
            attempt=attempt,
            success=False,
            message=str(reason),
            error=str(reason),
            code=ACTION_CANCELLED_CODE,
            model_consumable_observation={"success": False, "cancelled": True, "reason": str(reason), "code": ACTION_CANCELLED_CODE},
            checker_result={"step_status": "cancelled"},
        )

    def _run_plan_precheck(self, context: ReActExecutionContext) -> ExecutionResult | None:
        plan = context.plan
        mode = str(getattr(plan, "mode", "") or "")
        validation_status = str(getattr(plan, "plan_validation_status", "valid") or "valid")
        action_policy = str(getattr(context.task, "action_policy", getattr(plan, "risk_policy", "allow")) or "allow")

        if validation_status == "invalid":
            return self._invalid_plan_result(context)
        if action_policy == "block":
            return self._blocked_by_task_policy_result(context)
        if mode == "clarify":
            return self._clarify_result(context)
        if mode == "confirm" or action_policy == "confirm" or getattr(context.task, "requires_confirmation", False):
            return self._confirm_result(context)
        if mode == "missing_tools":
            return self._missing_tools_result(context)
        if mode == "blocked" or not getattr(plan, "can_execute", False):
            return self._plan_not_executable_result(context)

        reference_errors = self._plan_reference_errors(context)
        if reference_errors:
            return self._plan_reference_error_result(context, reference_errors)

        plan_safety = self._plan_safety_decision(context)
        if plan_safety is not None:
            step, decision = plan_safety
            if decision.blocked:
                return self._safety_blocked_result(context, step, decision)
            if decision.needs_confirmation:
                return self._safety_confirmation_result(context, step, decision)
        return None

    def _task_units_for_plan(self, plan: Any) -> List[Any]:
        task_units = list(getattr(plan, "task_units", []) or [])
        if task_units:
            return task_units

        steps = list(getattr(plan, "steps", []) or [])
        if not steps:
            return []

        return [_SyntheticTaskUnit(step_ids=[str(getattr(step, "id", "")) for step in steps if getattr(step, "id", None)])]

    def _plan_not_executable_result(self, context: ReActExecutionContext) -> ExecutionResult:
        mode = getattr(context.plan, "mode", "unknown")
        notes = list(getattr(context.plan, "plan_validation_notes", []) or [])
        message = f"Plan is not executable in mode={mode}."
        if notes:
            message = f"{message} Notes: {'; '.join(str(note) for note in notes)}"
        context.output = message
        context.summary = message
        context.error_code = PLAN_NOT_EXECUTABLE_CODE
        context.event_stream.emit_event(
            "system_notice",
            message,
            payload={"mode": mode, "plan_validation_notes": notes},
        )
        context.event_stream.emit_event("final_answer", message, payload={"status": "blocked"})
        return self._build_result(context, status="blocked", success=False)

    def _invalid_plan_result(self, context: ReActExecutionContext) -> ExecutionResult:
        notes = list(getattr(context.plan, "plan_validation_notes", []) or [])
        message = "Plan validation failed before execution."
        if notes:
            message = f"{message} Notes: {'; '.join(str(note) for note in notes)}"
        context.output = message
        context.summary = message
        context.error_code = INVALID_PLAN_CODE
        self._mark_all_states(context, "failed", message=message, error_code=INVALID_PLAN_CODE)
        context.event_stream.emit_event(
            "system_notice",
            message,
            payload={"plan_validation_status": "invalid", "plan_validation_notes": notes},
        )
        context.event_stream.emit_event("final_answer", message, payload={"status": "failed"})
        return self._build_result(context, status="failed", success=False)

    def _blocked_by_task_policy_result(self, context: ReActExecutionContext) -> ExecutionResult:
        message = "Task policy blocks execution before ReActExecutor starts."
        context.output = message
        context.summary = message
        context.error_code = TASK_POLICY_BLOCKED_CODE
        self._mark_all_states(context, "blocked", message=message, error_code=TASK_POLICY_BLOCKED_CODE)
        context.event_stream.emit_event("system_notice", message, payload={"action_policy": "block"})
        context.event_stream.emit_event("final_answer", message, payload={"status": "blocked"})
        return self._build_result(context, status="blocked", success=False)

    def _clarify_result(self, context: ReActExecutionContext) -> ExecutionResult:
        request = self._clarification_request(context)
        message = request or "The plan requires clarification before execution."
        context.output = message
        context.summary = message
        context.error_code = CLARIFICATION_REQUIRED_CODE
        context.requires_user_input = True
        context.user_input_request = message
        self._mark_all_states(context, "waiting_user", message=message, error_code=CLARIFICATION_REQUIRED_CODE)
        context.event_stream.emit_event(
            "system_notice",
            message,
            payload={"mode": "clarify", "requires_user_input": True},
        )
        context.event_stream.emit_event("final_answer", message, payload={"status": "waiting_user"})
        return self._build_result(context, status="waiting_user", success=False)

    def _confirm_result(self, context: ReActExecutionContext) -> ExecutionResult:
        message = self._confirmation_message(context)
        context.output = message
        context.summary = message
        context.error_code = CONFIRMATION_REQUIRED_CODE
        context.requires_user_input = True
        context.user_input_request = message
        context.pending_confirmation = PendingConfirmation(
            execution_id=context.execution_id,
            plan_id=context.plan_id,
            task_id=self._first_task_id(context),
            step_id=self._first_step_id(context),
            confirmation_type="confirmation",
            confirmation_message=message,
            pending_action={
                "type": "plan_confirmation",
                "plan_id": context.plan_id,
                "mode": getattr(context.plan, "mode", None),
                "reason": message,
            },
        )
        self._mark_all_states(context, "waiting_user", message=message, error_code=CONFIRMATION_REQUIRED_CODE)
        context.event_stream.emit_event(
            "confirmation_requested",
            message,
            payload={"confirmation_type": "confirmation", "pending_confirmation": context.pending_confirmation.to_dict()},
            task_id=context.pending_confirmation.task_id,
            step_id=context.pending_confirmation.step_id,
        )
        return self._build_result(context, status="waiting_user", success=False)

    def _missing_tools_result(self, context: ReActExecutionContext) -> ExecutionResult:
        missing_tools = list(getattr(context.plan, "missing_tools", []) or [])
        message = "Plan requires tools that are not available."
        if missing_tools:
            message = f"{message} Missing tools: {', '.join(str(tool) for tool in missing_tools)}."
        context.output = message
        context.summary = message
        context.error_code = MISSING_TOOLS_CODE
        self._mark_all_states(context, "blocked", message=message, error_code=MISSING_TOOLS_CODE)
        context.event_stream.emit_event("system_notice", message, payload={"missing_tools": missing_tools})
        context.event_stream.emit_event("final_answer", message, payload={"status": "blocked"})
        return self._build_result(context, status="blocked", success=False)

    def _empty_plan_result(self, context: ReActExecutionContext) -> ExecutionResult:
        message = "Plan contains no executable steps."
        context.output = message
        context.summary = message
        context.error_code = EMPTY_PLAN_CODE
        context.event_stream.emit_event("system_notice", message, payload={"reason": EMPTY_PLAN_CODE})
        context.event_stream.emit_event("final_answer", message, payload={"status": "failed"})
        return self._build_result(context, status="failed", success=False)

    def _plan_reference_errors(self, context: ReActExecutionContext) -> List[Dict[str, Any]]:
        errors: List[Dict[str, Any]] = []
        steps = list(getattr(context.plan, "steps", []) or [])
        step_ids = [str(getattr(step, "id", "")) for step in steps if getattr(step, "id", None)]
        step_id_set = set(step_ids)
        output_keys = {str(getattr(step, "output_key", "")) for step in steps if getattr(step, "output_key", None)}
        task_units = self._task_units_for_plan(context.plan)
        task_ids = {str(getattr(unit, "id", "")) for unit in task_units if getattr(unit, "id", None)}
        available_tools = self._available_tool_names(context)

        if len(step_ids) != len(step_id_set):
            errors.append({"code": PLAN_REFERENCE_ERROR_CODE, "message": "duplicate step ids are not allowed"})

        for task_unit in task_units:
            task_id = str(getattr(task_unit, "id", ""))
            for step_id in list(getattr(task_unit, "step_ids", []) or []):
                if str(step_id) not in step_id_set:
                    errors.append(
                        {
                            "code": MISSING_STEP_CODE,
                            "task_id": task_id,
                            "step_id": str(step_id),
                            "message": f"{task_id}: step_ids references missing step {step_id}",
                        }
                    )

        for step in steps:
            step_id = str(getattr(step, "id", ""))
            task_id = str(getattr(step, "task_id", ""))
            if task_id and task_ids and task_id not in task_ids:
                errors.append(
                    {
                        "code": PLAN_REFERENCE_ERROR_CODE,
                        "task_id": task_id,
                        "step_id": step_id,
                        "message": f"{step_id}: task_id references missing TaskUnit {task_id}",
                    }
                )
            for ref in list(getattr(step, "depends_on", []) or []):
                if str(ref) not in step_id_set:
                    errors.append(
                        {
                            "code": PLAN_REFERENCE_ERROR_CODE,
                            "step_id": step_id,
                            "ref": str(ref),
                            "message": f"{step_id}: depends_on references missing step {ref}",
                        }
                    )
            for ref in list(getattr(step, "input_from", []) or []):
                if str(ref) not in step_id_set and str(ref) not in output_keys:
                    errors.append(
                        {
                            "code": PLAN_REFERENCE_ERROR_CODE,
                            "step_id": step_id,
                            "ref": str(ref),
                            "message": f"{step_id}: input_from references missing step or output_key {ref}",
                        }
                    )
            tool_name = getattr(step, "tool_name", None)
            if getattr(step, "step_type", None) == "tool" and tool_name and str(tool_name) not in available_tools:
                errors.append(
                    {
                        "code": TOOL_NOT_AVAILABLE_CODE,
                        "step_id": step_id,
                        "tool_name": str(tool_name),
                        "message": f"{step_id}: tool is not available: {tool_name}",
                    }
                )
        return errors

    def _plan_safety_decision(self, context: ReActExecutionContext) -> tuple[Any, SafetyDecision] | None:
        for step in list(getattr(context.plan, "steps", []) or []):
            if str(getattr(step, "step_type", "") or "") != "tool":
                continue
            tool_name = str(getattr(step, "tool_name", "") or "")
            if not tool_name:
                continue
            if self._command_plan_step_defers_to_model(step):
                continue
            packet = ActionPacket(
                execution_id=context.execution_id,
                plan_id=context.plan_id,
                task_id=getattr(step, "task_id", None),
                step_id=getattr(step, "id", None),
                action_type="call_tool",
                action_target=tool_name,
                action_args=dict(getattr(step, "args", {}) or {}),
                requires_confirmation=bool(getattr(step, "requires_confirmation", False)),
                confirmation_type="confirmation",
            )
            decision = self._evaluate_action_safety(context, packet, step)
            if decision.blocked or decision.needs_confirmation:
                return step, decision
        return None

    def _safety_blocked_result(self, context: ReActExecutionContext, step: Any, decision: SafetyDecision) -> ExecutionResult:
        step_id = str(getattr(step, "id", "") or "")
        task_id = str(getattr(step, "task_id", "") or "")
        message = decision.reason
        self.execution_logger.write_record(
            context,
            record_type="safety_decision",
            task_id=task_id or None,
            step_id=step_id or None,
            action_type="call_tool",
            action_target=getattr(step, "tool_name", None),
            success=False,
            error=message,
            code=decision.code,
            metadata={"safety": decision.to_dict(), "scope": "plan_precheck"},
        )
        context.output = message
        context.summary = message
        context.error_code = SAFETY_BLOCKED_CODE
        context.failed_step_id = step_id or None
        self._mark_step_blocked(context, step_id, message, SAFETY_BLOCKED_CODE)
        self._sync_task_statuses_from_steps(context)
        context.event_stream.emit_event(
            "system_notice",
            message,
            payload={"safety": decision.to_dict(), "status": "blocked"},
            task_id=task_id or None,
            step_id=step_id or None,
        )
        context.event_stream.emit_event("final_answer", message, payload={"status": "blocked", "error_code": SAFETY_BLOCKED_CODE})
        return self._build_result(context, status="blocked", success=False)

    def _safety_confirmation_result(self, context: ReActExecutionContext, step: Any, decision: SafetyDecision) -> ExecutionResult:
        step_id = str(getattr(step, "id", "") or "")
        task_id = str(getattr(step, "task_id", "") or "")
        message = decision.reason
        self.execution_logger.write_record(
            context,
            record_type="safety_decision",
            task_id=task_id or None,
            step_id=step_id or None,
            action_type="call_tool",
            action_target=getattr(step, "tool_name", None),
            success=True,
            code=decision.code,
            metadata={"safety": decision.to_dict(), "scope": "plan_precheck"},
        )
        context.output = message
        context.summary = message
        context.error_code = CONFIRMATION_REQUIRED_CODE
        context.requires_user_input = True
        context.user_input_request = message
        context.pending_confirmation = PendingConfirmation(
            execution_id=context.execution_id,
            plan_id=context.plan_id,
            task_id=task_id or None,
            step_id=step_id or None,
            confirmation_type="confirmation",
            confirmation_message=message,
            pending_action={
                "type": "plan_safety_confirmation",
                "plan_id": context.plan_id,
                "step_id": step_id,
                "tool_name": getattr(step, "tool_name", None),
                "safety": decision.to_dict(),
            },
        )
        self._mark_step_waiting(context, step_id or None, message, CONFIRMATION_REQUIRED_CODE)
        context.event_stream.emit_event(
            "confirmation_requested",
            message,
            payload={
                "confirmation_type": "confirmation",
                "pending_confirmation": context.pending_confirmation.to_dict(),
                "safety": decision.to_dict(),
            },
            task_id=task_id or None,
            step_id=step_id or None,
        )
        return self._build_result(context, status="waiting_user", success=False)

    def _plan_reference_error_result(self, context: ReActExecutionContext, errors: List[Dict[str, Any]]) -> ExecutionResult:
        first_error = errors[0]
        message = "Plan precheck failed before execution."
        context.output = message
        context.summary = "; ".join(str(error.get("message", "")) for error in errors if error.get("message"))
        context.error_code = str(first_error.get("code") or PLAN_REFERENCE_ERROR_CODE)
        failed_step_id = first_error.get("step_id")
        if isinstance(failed_step_id, str) and failed_step_id:
            context.failed_step_id = failed_step_id
            state = context.step_states.get(failed_step_id)
            if state is None:
                state = StepRuntimeState(step_id=failed_step_id, status="failed")
                context.step_states[failed_step_id] = state
            state.status = "failed"
            state.error_code = context.error_code
            state.message = str(first_error.get("message", message))
        else:
            self._mark_all_states(context, "failed", message=message, error_code=context.error_code)
        self._sync_task_statuses_from_steps(context)
        context.event_stream.emit_event("system_notice", message, payload={"errors": errors})
        context.event_stream.emit_event(
            "step_failed",
            str(first_error.get("message", message)),
            payload={"errors": errors, "error_code": context.error_code},
            task_id=first_error.get("task_id") if isinstance(first_error.get("task_id"), str) else None,
            step_id=context.failed_step_id,
        )
        context.event_stream.emit_event("final_answer", context.summary or message, payload={"status": "failed"})
        return self._build_result(context, status="failed", success=False)

    def _available_tool_names(self, context: ReActExecutionContext) -> set[str]:
        if str(getattr(context.plan, "mode", "") or "") == "chat":
            return set()
        registry_tools = set(self.tool_registry.tool_names())
        aliases = self.tool_registry.list_aliases() if hasattr(self.tool_registry, "list_aliases") else {}
        if aliases:
            registry_tools.update(aliases.keys())
        plan_tools = set(str(tool) for tool in list(getattr(context.plan, "available_tools", []) or []) if tool)
        if plan_tools:
            expanded_plan_tools = set(plan_tools)
            for alias, canonical_name in aliases.items():
                if alias in plan_tools or canonical_name in plan_tools:
                    expanded_plan_tools.update({alias, canonical_name})
            return registry_tools.intersection(expanded_plan_tools)
        return registry_tools

    def _execute_react_loop(self, context: ReActExecutionContext) -> ExecutionResult:
        """Run the Planner-guided ReAct loop for executable plans."""
        terminal_statuses = [
            "completed",
            "failed",
            "blocked",
            "waiting_user",
            "request_replan",
            "partial_failed",
            "cancelled",
        ]
        task_units = self._task_units_for_plan(context.plan)
        first_task_id = self._first_task_id(context)
        first_step_id = self._first_step_id(context)
        self.execution_logger.write_record(
            context,
            record_type="react_loop_started",
            task_id=first_task_id,
            step_id=first_step_id,
            success=None,
            code=None,
            metadata={
                "terminal_statuses": terminal_statuses,
                "task_unit_count": len(task_units),
                "step_count": len(context.step_lookup),
                "skeleton_default_path": False,
            },
        )
        context.event_stream.emit_event(
            "progress_message",
            "Planner-guided ReAct main loop started.",
            payload={
                "phase": "react_loop",
                "terminal_statuses": terminal_statuses,
                "task_unit_count": len(task_units),
                "step_count": len(context.step_lookup),
                "loop_state": context.loop_state.to_model_context(),
            },
            task_id=first_task_id,
            step_id=first_step_id,
        )

        loop_result = self._execute_task_unit_loop(context, task_units)
        status = loop_result.status
        success = loop_result.success
        context.loop_state.finish(status)
        if status == "completed":
            context.error_code = None
        elif status == "request_replan":
            context.request_replan = True
            context.replan_reason = loop_result.message
        elif status == "partial_failed":
            context.error_code = context.error_code or loop_result.error_code
        else:
            context.error_code = loop_result.error_code or context.error_code

        context.event_stream.emit_event(
            "final_answer",
            context.output or loop_result.message,
            payload={"status": status, "error_code": context.error_code, "request_replan": context.request_replan},
            task_id=first_task_id,
            step_id=first_step_id,
        )
        return self._build_result(context, status=status, success=success)

    def _execute_task_unit_loop(self, context: ReActExecutionContext, task_units: List[Any]) -> ReActStepLoopResult:
        last_result: ReActStepLoopResult | None = None
        for task_unit in task_units:
            task_result = self._execute_single_task_unit_loop(context, task_unit)
            last_result = task_result
            if task_result.terminal:
                return task_result
            if task_result.status in {"waiting_user", "request_replan", "blocked", "failed", "cancelled"}:
                if task_result.status == "failed" and self._can_continue_after_task_failure(context, task_unit):
                    continue
                return task_result

        status = self._aggregate_execution_status(context)
        success = status == "completed"
        if status == "completed":
            context.output = context.output or "Execution completed."
            context.summary = context.summary or context.output
            return ReActStepLoopResult("completed", True, message=context.output)
        if status == "partial_failed":
            message = context.output or "Execution partially completed with failed steps."
            context.output = message
            context.summary = message
            return ReActStepLoopResult("partial_failed", False, message=message, error_code=context.error_code)
        if status == "blocked":
            message = context.output or "Execution blocked."
            context.output = message
            context.summary = message
            return ReActStepLoopResult("blocked", False, message=message, error_code=context.error_code)
        if status == "failed":
            message = context.output or "Execution failed."
            context.output = message
            context.summary = message
            return ReActStepLoopResult("failed", False, message=message, error_code=context.error_code)
        if status == "cancelled":
            message = context.output or "Execution cancelled."
            context.output = message
            context.summary = message
            return ReActStepLoopResult("cancelled", False, message=message, error_code=context.error_code)
        return last_result or ReActStepLoopResult("completed", True, message=context.output)

    def _execute_single_task_unit_loop(self, context: ReActExecutionContext, task_unit: Any) -> ReActStepLoopResult:
        task_id = str(getattr(task_unit, "id", "") or "task_1")
        task_state = context.task_states.get(task_id)
        if task_state is None:
            task_state = TaskUnitRuntimeState(task_id=task_id, status="running")
            context.task_states[task_id] = task_state
        task_state.status = "running"
        last_result: ReActStepLoopResult | None = None

        for step_id in list(getattr(task_unit, "step_ids", []) or []):
            step_id = str(step_id)
            step = context.step_lookup.get(step_id)
            if step is None:
                message = f"TaskUnit references missing step: {step_id}"
                self._set_step_status(context, step_id, "failed", message=message, error_code=MISSING_STEP_CODE)
                context.failed_step_id = step_id
                context.error_code = MISSING_STEP_CODE
                self._sync_task_statuses_from_steps(context)
                context.event_stream.emit_event("step_failed", message, payload={"error_code": MISSING_STEP_CODE}, task_id=task_id, step_id=step_id)
                return ReActStepLoopResult("failed", False, message=message, error_code=MISSING_STEP_CODE)

            current_state = context.step_states.get(step_id)
            if current_state is not None and current_state.status in {"completed", "skipped", "cancelled", "failed", "blocked"}:
                task_state.step_statuses[step_id] = current_state.status
                last_result = ReActStepLoopResult(
                    current_state.status,
                    current_state.status in {"completed", "skipped"},
                    message=current_state.message,
                    error_code=current_state.error_code,
                )
                continue

            dependency_issue = self._step_dependency_issue(context, step)
            if dependency_issue is not None:
                blocked_result = self._block_step_for_dependency(context, task_id, step, dependency_issue)
                last_result = blocked_result
                if str(getattr(step, "on_failure", "") or "").lower() != "continue":
                    return blocked_result
                continue

            result = self._execute_step_react_loop(context, task_unit, step)
            last_result = result
            if result.terminal:
                return result
            if result.status in {"completed"}:
                continue
            if result.status in {"waiting_user", "request_replan", "cancelled"}:
                return result
            if result.status in {"failed", "blocked"}:
                if str(getattr(step, "on_failure", "") or "").lower() == "continue":
                    continue
                self._block_dependent_remaining_steps(context, task_id, task_unit, after_step_id=step_id)
                return result

        self._sync_task_statuses_from_steps(context)
        statuses = set(task_state.step_statuses.values())
        if statuses and statuses.issubset({"completed", "skipped"}):
            task_state.status = "completed"
            return ReActStepLoopResult("completed", True, message=context.output)
        if "waiting_user" in statuses:
            task_state.status = "waiting_user"
            return ReActStepLoopResult("waiting_user", False, message=context.output, error_code=context.error_code)
        if "blocked" in statuses:
            task_state.status = "blocked"
            return ReActStepLoopResult("blocked", False, message=context.output, error_code=context.error_code)
        if "failed" in statuses:
            task_state.status = "failed"
            return ReActStepLoopResult("failed", False, message=context.output, error_code=context.error_code)
        return last_result or ReActStepLoopResult("completed", True, message=context.output)

    def _execute_step_react_loop(
        self,
        context: ReActExecutionContext,
        task_unit: Any | None,
        step: Any,
    ) -> ReActStepLoopResult:
        step_id = str(getattr(step, "id", "") or "")
        task_id = str(getattr(step, "task_id", "") or getattr(task_unit, "id", "") or "")
        self._mark_step_running(context, task_id, step_id)
        context.event_stream.emit_event(
            "step_started",
            str(getattr(step, "description", "") or step_id),
            payload={
                "step_id": step_id,
                "step_type": getattr(step, "step_type", None),
                "tool_name": getattr(step, "tool_name", None),
                "output_key": getattr(step, "output_key", None),
            },
            task_id=task_id or None,
            step_id=step_id or None,
        )

        for _ in range(self.config.max_step_turns):
            step_state = context.step_states.get(step_id)
            attempt = (step_state.attempts + 1) if step_state is not None else 1
            turn_state = context.loop_state.start_turn(
                task_id=task_id or None,
                step_id=step_id or None,
                attempt=attempt,
                thought_summary="Preparing the next structured action.",
                user_visible_message="Selecting the next action.",
            )
            prompt, prompt_input = self._build_action_decision_prompt(context, task_unit, step, turn_state)
            self._log_action_decision_prompt(
                context,
                task_id=task_id or None,
                step_id=step_id or None,
                turn_state=turn_state,
                prompt=prompt,
                input_payload=prompt_input,
            )
            action_packet_result = self._request_action_packet(
                context,
                prompt=prompt,
                task_unit=task_unit,
                step=step,
                turn_state=turn_state,
            )
            if action_packet_result.observation is not None:
                observation = action_packet_result.observation
                checker_result = self.check_observation(
                    context,
                    observation,
                    step=step,
                    packet=action_packet_result.packet,
                    current_step_turn=turn_state.step_turn,
                    current_execution_turn=context.loop_state.execution_turn,
                )
                context.loop_state.record_observation(observation)
                context.loop_state.record_checker_result(checker_result.to_dict())
                observation.checker_result = checker_result.to_dict()
                result = self._apply_checker_decision(
                    context,
                    step=step,
                    packet=action_packet_result.packet,
                    observation=observation,
                    checker_result=checker_result,
                    turn_state=turn_state,
                )
                if result.status == "continue":
                    turn_state.finish("completed")
                    continue
                turn_state.finish(result.status if result.status in {"completed", "failed", "blocked", "waiting_user", "request_replan", "cancelled"} else "failed")
                return result

            packet = action_packet_result.packet
            if packet is None:
                turn_state.finish("failed")
                return self._fail_step_without_observation(
                    context,
                    step,
                    "ActionPacket generation returned neither packet nor observation.",
                    ACTION_PACKET_INVALID_CODE,
                )

            context.event_stream.emit_event(
                "thought_visible",
                packet.user_visible_message or "Model selected the next structured action.",
                payload={
                    "packet_id": packet.packet_id,
                    "action_type": packet.action_type,
                    "action_target": packet.action_target,
                    "repair_attempts": action_packet_result.repair_attempts,
                },
                task_id=packet.task_id,
                step_id=packet.step_id,
            )
            observation = self.dispatch_action(
                context,
                packet,
                step=step,
                attempt=turn_state.attempt,
                output_key=getattr(step, "output_key", None),
            )
            context.loop_state.record_observation(observation)
            checker_result = self.check_observation(
                context,
                observation,
                step=step,
                packet=packet,
                current_step_turn=turn_state.step_turn,
                current_execution_turn=context.loop_state.execution_turn,
            )
            context.loop_state.record_checker_result(checker_result.to_dict())
            observation.checker_result = checker_result.to_dict()

            if checker_result.checker_status == "continue":
                self._log_checker_transition(context, step=step, packet=packet, observation=observation, checker_result=checker_result, transition="continue")
                turn_state.finish("completed")
                continue

            result = self._apply_checker_decision(
                context,
                step=step,
                packet=packet,
                observation=observation,
                checker_result=checker_result,
                turn_state=turn_state,
            )
            if result.status == "continue":
                turn_state.finish("completed")
                continue
            turn_state.finish(result.status if result.status in {"completed", "failed", "blocked", "waiting_user", "request_replan", "cancelled"} else "failed")
            return result

        return self._fail_step_without_observation(
            context,
            step,
            "Maximum step turns reached before the step completed.",
            "max_step_turns_reached",
        )

    def _apply_checker_decision(
        self,
        context: ReActExecutionContext,
        *,
        step: Any,
        packet: ActionPacket | None,
        observation: ObservationPacket,
        checker_result: Any,
        turn_state: ReActTurnState,
        decision_depth: int = 0,
    ) -> ReActStepLoopResult:
        step_id = str(getattr(step, "id", "") or observation.step_id or "")
        task_id = str(getattr(step, "task_id", "") or observation.task_id or "")
        status = checker_result.checker_status
        message = checker_result.reason or observation.message or observation.error or status
        self.execution_logger.write_record(
            context,
            record_type="checker_result",
            task_id=task_id or None,
            step_id=step_id or None,
            packet_id=observation.packet_id,
            action_type=observation.action_type,
            action_target=observation.action_target,
            attempt=turn_state.attempt,
            success=checker_result.success,
            code=checker_result.code,
            checker_result=checker_result.to_dict(),
            observation_id=observation.observation_id,
            metadata={"turn_id": turn_state.turn_id},
        )

        if decision_depth >= max(int(self.config.max_step_turns), 1):
            return self._fail_step_without_observation(
                context,
                step,
                "Checker transition depth exceeded maximum step turns.",
                "max_step_turns_reached",
            )

        terminal_action_type = (packet.action_type if packet is not None else observation.action_type) or ""
        if terminal_action_type in {"finish", "fail", "request_replan", "blocked", "cancel"}:
            return self._finalize_terminal_action(
                context,
                step=step,
                packet=packet,
                observation=observation,
                checker_result=checker_result,
                message=message,
            )

        if status == "continue":
            self._log_checker_transition(context, step=step, packet=packet, observation=observation, checker_result=checker_result, transition="continue")
            return ReActStepLoopResult(
                status="continue",
                success=False,
                message=message,
                packet=packet,
                observation=observation,
                checker_result=checker_result,
            )

        if status == "step_completed":
            self._set_step_status(context, step_id, checker_result.step_status or "completed", message=message, error_code=checker_result.code)
            self._sync_task_statuses_from_steps(context)
            context.output = observation.message or observation.error or message
            if observation.action_type == "finish" and packet is not None and packet.final_answer:
                context.output = packet.final_answer
            context.summary = context.output
            context.error_code = None
            context.event_stream.emit_event(
                "step_completed",
                message,
                payload={"checker_result": checker_result.to_dict(), "observation_id": observation.observation_id},
                task_id=task_id or None,
                step_id=step_id or None,
            )
            self._log_checker_transition(context, step=step, packet=packet, observation=observation, checker_result=checker_result, transition="completed")
            return ReActStepLoopResult(
                status="completed",
                success=True,
                message=context.output,
                packet=packet,
                observation=observation,
                checker_result=checker_result,
            )

        if status == "ask_user":
            context.requires_user_input = True
            context.user_input_request = context.user_input_request or message
            self._set_step_status(context, step_id, "waiting_user", message=message, error_code=checker_result.code)
            self._sync_task_statuses_from_steps(context)
            context.output = message
            context.summary = message
            context.error_code = checker_result.code or USER_INPUT_REQUIRED_CODE
            self._log_checker_transition(context, step=step, packet=packet, observation=observation, checker_result=checker_result, transition="waiting_user")
            return ReActStepLoopResult("waiting_user", False, message=message, error_code=context.error_code, packet=packet, observation=observation, checker_result=checker_result)

        if status == "request_replan":
            context.request_replan = True
            context.replan_reason = message
            self._set_step_status(context, step_id, "failed", message=message, error_code=checker_result.code or REQUEST_REPLAN_CODE)
            self._sync_task_statuses_from_steps(context)
            context.output = message
            context.summary = message
            context.error_code = checker_result.code or REQUEST_REPLAN_CODE
            context.failed_step_id = step_id or None
            context.event_stream.emit_event(
                "request_replan",
                message,
                payload={"checker_result": checker_result.to_dict(), "observation_id": observation.observation_id},
                task_id=task_id or None,
                step_id=step_id or None,
            )
            self._log_checker_transition(context, step=step, packet=packet, observation=observation, checker_result=checker_result, transition="request_replan")
            return ReActStepLoopResult("request_replan", False, message=message, error_code=context.error_code, packet=packet, observation=observation, checker_result=checker_result)

        if status == "retry":
            if not self._can_consume_retry_decision(observation):
                return self._finalize_checker_failure(
                    context,
                    step=step,
                    packet=packet,
                    observation=observation,
                    checker_result=checker_result,
                    turn_state=turn_state,
                    message=message,
                    forced_status="failed",
                )
            retry_packet = self._retry_action_packet_from_checker_decision(context, step, observation, checker_result)
            self._log_checker_transition(context, step=step, packet=retry_packet, observation=observation, checker_result=checker_result, transition="retry_step")
            return self._dispatch_checker_transition_action(
                context,
                step=step,
                packet=retry_packet,
                attempt=max(turn_state.attempt + 1, observation.attempt + 1),
                turn_state=turn_state,
                decision_depth=decision_depth + 1,
            )

        if status in {"fallback_to_model", "fallback_to_tool"}:
            if observation.fallback_used or observation.action_type in {"fallback_to_model", "fallback_to_tool"}:
                return self._finalize_checker_failure(
                    context,
                    step=step,
                    packet=packet,
                    observation=observation,
                    checker_result=checker_result,
                    turn_state=turn_state,
                    message=message,
                    forced_status="failed",
                )
            fallback_packet = self._fallback_action_packet_from_checker_decision(context, step, observation, checker_result)
            self._log_checker_transition(context, step=step, packet=fallback_packet, observation=observation, checker_result=checker_result, transition=status)
            return self._dispatch_checker_transition_action(
                context,
                step=step,
                packet=fallback_packet,
                attempt=max(turn_state.attempt + 1, observation.attempt + 1),
                turn_state=turn_state,
                decision_depth=decision_depth + 1,
            )

        return self._finalize_checker_failure(
            context,
            step=step,
            packet=packet,
            observation=observation,
            checker_result=checker_result,
            turn_state=turn_state,
            message=message,
        )

    def _finalize_terminal_action(
        self,
        context: ReActExecutionContext,
        *,
        step: Any,
        packet: ActionPacket | None,
        observation: ObservationPacket,
        checker_result: Any,
        message: str,
    ) -> ReActStepLoopResult:
        step_id = str(getattr(step, "id", "") or observation.step_id or "")
        task_id = str(getattr(step, "task_id", "") or observation.task_id or "")
        action_type = (packet.action_type if packet is not None else observation.action_type) or ""

        if action_type == "finish":
            final_answer = self._final_answer_from_terminal_action(packet, observation, message)
            self._set_step_status(context, step_id, "completed", message=final_answer, error_code=None)
            context.output = final_answer
            context.summary = final_answer
            context.error_code = None
            context.failed_step_id = None
            self._mark_remaining_steps_after_terminal_action(
                context,
                current_step_id=step_id,
                terminal_status="completed",
                message="Skipped because execution finished before this step.",
                error_code=None,
            )
            self._sync_task_statuses_from_steps(context)
            context.event_stream.emit_event(
                "step_completed",
                final_answer,
                payload={
                    "checker_result": checker_result.to_dict(),
                    "observation_id": observation.observation_id,
                    "terminal_action": action_type,
                },
                task_id=task_id or None,
                step_id=step_id or None,
            )
            self._log_checker_transition(context, step=step, packet=packet, observation=observation, checker_result=checker_result, transition="completed")
            return ReActStepLoopResult("completed", True, message=final_answer, packet=packet, observation=observation, checker_result=checker_result, terminal=True)

        if action_type == "request_replan":
            reason = self._replan_reason_from_terminal_action(packet, observation, message)
            context.request_replan = True
            context.replan_reason = reason
            context.output = reason
            context.summary = reason
            context.error_code = checker_result.code or observation.code or REQUEST_REPLAN_CODE
            context.failed_step_id = step_id or None
            self._set_step_status(context, step_id, "failed", message=reason, error_code=context.error_code)
            self._mark_remaining_steps_after_terminal_action(
                context,
                current_step_id=step_id,
                terminal_status="request_replan",
                message="Skipped because replan was requested before this step.",
                error_code=None,
            )
            self._sync_task_statuses_from_steps(context)
            context.event_stream.emit_event(
                "request_replan",
                reason,
                payload={
                    "checker_result": checker_result.to_dict(),
                    "observation_id": observation.observation_id,
                    "terminal_action": action_type,
                },
                task_id=task_id or None,
                step_id=step_id or None,
            )
            self._log_checker_transition(context, step=step, packet=packet, observation=observation, checker_result=checker_result, transition="request_replan")
            return ReActStepLoopResult("request_replan", False, message=reason, error_code=context.error_code, packet=packet, observation=observation, checker_result=checker_result, terminal=True)

        if action_type == "blocked":
            terminal_status = "blocked"
            step_status = "blocked"
            error_code = checker_result.code or observation.code or ACTION_BLOCKED_CODE
            event_type = "step_failed"
            skipped_message = "Skipped because execution was blocked before this step."
        elif action_type == "cancel":
            terminal_status = "cancelled"
            step_status = "cancelled"
            error_code = checker_result.code or observation.code or ACTION_CANCELLED_CODE
            event_type = "step_failed"
            skipped_message = "Skipped because execution was cancelled before this step."
        else:
            terminal_status = "failed"
            step_status = "failed"
            error_code = checker_result.code or observation.code or ACTION_FAILED_CODE
            event_type = "step_failed"
            skipped_message = "Skipped because execution failed before this step."

        reason = str(message or observation.error or observation.message or terminal_status)
        context.output = reason
        context.summary = reason
        context.error_code = error_code
        context.failed_step_id = step_id or None
        self._set_step_status(context, step_id, step_status, message=reason, error_code=error_code)
        self._mark_remaining_steps_after_terminal_action(
            context,
            current_step_id=step_id,
            terminal_status=terminal_status,
            message=skipped_message,
            error_code=None,
        )
        self._sync_task_statuses_from_steps(context)
        context.event_stream.emit_event(
            event_type,
            reason,
            payload={
                "checker_result": checker_result.to_dict(),
                "observation_id": observation.observation_id,
                "terminal_action": action_type,
                "error_code": error_code,
            },
            task_id=task_id or None,
            step_id=step_id or None,
        )
        self._log_checker_transition(context, step=step, packet=packet, observation=observation, checker_result=checker_result, transition=terminal_status)
        return ReActStepLoopResult(terminal_status, False, message=reason, error_code=error_code, packet=packet, observation=observation, checker_result=checker_result, terminal=True)

    def _final_answer_from_terminal_action(self, packet: ActionPacket | None, observation: ObservationPacket, fallback: str) -> str:
        if packet is not None and packet.final_answer:
            return str(packet.final_answer)
        data = observation.data if isinstance(observation.data, dict) else {}
        final_answer = data.get("final_answer")
        if final_answer:
            return str(final_answer)
        return str(observation.message or fallback or "Execution completed.")

    def _replan_reason_from_terminal_action(self, packet: ActionPacket | None, observation: ObservationPacket, fallback: str) -> str:
        if packet is not None and packet.request_replan_reason:
            return str(packet.request_replan_reason)
        data = observation.data if isinstance(observation.data, dict) else {}
        reason = data.get("reason")
        if reason:
            return str(reason)
        return str(observation.message or observation.error or fallback or "Replan requested.")

    def _mark_remaining_steps_after_terminal_action(
        self,
        context: ReActExecutionContext,
        *,
        current_step_id: str,
        terminal_status: str,
        message: str,
        error_code: str | None,
    ) -> None:
        if not current_step_id:
            return

        seen_current = False
        for task_unit in self._task_units_for_plan(context.plan):
            task_id = str(getattr(task_unit, "id", "") or "")
            for candidate_id in list(getattr(task_unit, "step_ids", []) or []):
                step_id = str(candidate_id)
                if step_id == current_step_id:
                    seen_current = True
                    continue
                if not seen_current:
                    continue

                state = context.step_states.get(step_id)
                if state is not None and state.status not in {"pending", "running"}:
                    continue

                step_message = f"{message} Terminal step: {current_step_id}."
                self._set_step_status(context, step_id, "skipped", message=step_message, error_code=error_code)
                context.event_stream.emit_event(
                    "step_completed",
                    step_message,
                    payload={
                        "status": "skipped",
                        "terminal_status": terminal_status,
                        "terminal_step_id": current_step_id,
                    },
                    task_id=task_id or None,
                    step_id=step_id,
                    visible_to_user=False,
                )

    def _apply_step_checker_result(
        self,
        context: ReActExecutionContext,
        *,
        step: Any,
        packet: ActionPacket | None,
        observation: ObservationPacket,
        checker_result: Any,
        turn_state: ReActTurnState,
    ) -> ReActStepLoopResult:
        return self._apply_checker_decision(
            context,
            step=step,
            packet=packet,
            observation=observation,
            checker_result=checker_result,
            turn_state=turn_state,
        )

    def _can_consume_retry_decision(self, observation: ObservationPacket) -> bool:
        return (
            not observation.success
            and not observation.fallback_used
            and observation.action_type in {"call_tool", "call_model"}
        )

    def _retry_action_packet_from_checker_decision(
        self,
        context: ReActExecutionContext,
        step: Any,
        observation: ObservationPacket,
        checker_result: Any,
    ) -> ActionPacket:
        step_id = str(getattr(step, "id", "") or observation.step_id or "")
        return ActionPacket(
            execution_id=context.execution_id,
            plan_id=context.plan_id,
            task_id=str(getattr(step, "task_id", "") or observation.task_id or "") or None,
            step_id=step_id or None,
            thought_summary="Retry the failed action selected by Checker.",
            user_visible_message="Retrying the failed action.",
            action_type="retry_step",
            action_args={
                "step_id": step_id,
                "observation_id": observation.observation_id,
                "packet_id": observation.packet_id,
                "retry_reason": checker_result.reason,
                "confirmed": observation.action_type in {"call_tool", "fallback_to_tool"} and str(observation.action_target or "") in COMMAND_TOOL_NAMES,
            },
            expected_observation=getattr(step, "expected_output", "") or "",
            confidence=1.0,
        )

    def _command_plan_step_defers_to_model(self, step: Any) -> bool:
        tool_name = str(getattr(step, "tool_name", "") or "")
        if tool_name not in COMMAND_TOOL_NAMES:
            return False
        args = dict(getattr(step, "args", {}) or {})
        return not bool(str(args.get("command", "") or "").strip())

    def _fallback_action_packet_from_checker_decision(
        self,
        context: ReActExecutionContext,
        step: Any,
        observation: ObservationPacket,
        checker_result: Any,
    ) -> ActionPacket:
        step_id = str(getattr(step, "id", "") or observation.step_id or "")
        action_type = checker_result.checker_status
        fallback_tool = checker_result.fallback_tool
        if action_type == "fallback_to_tool" and not fallback_tool:
            candidates = self._fallback_tool_names(step) or []
            fallback_tool = candidates[0] if candidates else None
        return ActionPacket(
            execution_id=context.execution_id,
            plan_id=context.plan_id,
            task_id=str(getattr(step, "task_id", "") or observation.task_id or "") or None,
            step_id=step_id or None,
            thought_summary="Use Checker-selected fallback for the failed action.",
            user_visible_message=(
                f"Using fallback tool: {fallback_tool}."
                if action_type == "fallback_to_tool" and fallback_tool
                else "Using model fallback."
            ),
            action_type=action_type,
            action_target=fallback_tool if action_type == "fallback_to_tool" else None,
            action_args={
                "step_id": step_id,
                "observation_id": observation.observation_id,
                "packet_id": observation.packet_id,
                "fallback_reason": checker_result.reason or observation.error or observation.message,
            },
            expected_observation=getattr(step, "expected_output", "") or "",
            confidence=1.0,
        )

    def _dispatch_checker_transition_action(
        self,
        context: ReActExecutionContext,
        *,
        step: Any,
        packet: ActionPacket,
        attempt: int,
        turn_state: ReActTurnState,
        decision_depth: int,
    ) -> ReActStepLoopResult:
        observation = self.dispatch_action(
            context,
            packet,
            step=step,
            attempt=attempt,
            output_key=getattr(step, "output_key", None),
        )
        context.loop_state.record_action(packet)
        context.loop_state.record_observation(observation)
        checker_result = self.check_observation(
            context,
            observation,
            step=step,
            packet=packet,
            current_step_turn=max(turn_state.step_turn, observation.attempt),
            current_execution_turn=context.loop_state.execution_turn,
        )
        if packet.action_type == "retry_step" and not observation.success and checker_result.checker_status != "retry":
            retry_decision = self.retry_policy.build_decision(
                observation,
                checker_result,
                step=step,
                step_state=context.step_states.get(str(getattr(step, "id", "") or observation.step_id or "")),
            )
            if retry_decision.code == RETRY_EXHAUSTED_CODE:
                context.event_stream.emit_event(
                    "retry_exhausted",
                    retry_decision.reason,
                    payload={
                        **retry_decision.to_dict(),
                        "after_retry_observation_id": observation.observation_id,
                        "checker_status": checker_result.checker_status,
                    },
                    task_id=observation.task_id,
                    step_id=observation.step_id,
                )
                self._log_retry_decision(
                    context,
                    packet=packet,
                    failed_observation=observation,
                    decision=retry_decision,
                    outcome="exhausted_after_retry",
                    success=False,
                    retry_observation=observation,
                )
        context.loop_state.record_checker_result(checker_result.to_dict())
        observation.checker_result = self._merge_observation_checker_result(observation, checker_result)
        return self._apply_checker_decision(
            context,
            step=step,
            packet=packet,
            observation=observation,
            checker_result=checker_result,
            turn_state=turn_state,
            decision_depth=decision_depth,
        )

    def _merge_observation_checker_result(self, observation: ObservationPacket, checker_result: Any) -> Dict[str, Any]:
        merged = dict(observation.checker_result or {})
        merged.update(checker_result.to_dict())
        if "retry" in observation.checker_result:
            merged["retry"] = observation.checker_result["retry"]
        if "fallback" in observation.checker_result:
            merged["fallback"] = observation.checker_result["fallback"]
        return merged

    def _finalize_checker_failure(
        self,
        context: ReActExecutionContext,
        *,
        step: Any,
        packet: ActionPacket | None,
        observation: ObservationPacket,
        checker_result: Any,
        turn_state: ReActTurnState,
        message: str,
        forced_status: str | None = None,
    ) -> ReActStepLoopResult:
        step_id = str(getattr(step, "id", "") or observation.step_id or "")
        task_id = str(getattr(step, "task_id", "") or observation.task_id or "")
        status = checker_result.checker_status
        final_step_status = "blocked" if status in {"retry", "fallback_to_model", "fallback_to_tool"} else checker_result.step_status or "failed"
        if forced_status is not None:
            final_step_status = forced_status
        if final_step_status not in {"failed", "blocked", "cancelled", "retrying", "fallback_used"}:
            final_step_status = "failed"
        self._set_step_status(context, step_id, final_step_status, message=message, error_code=checker_result.code or observation.code or ACTION_FAILED_CODE)
        self._sync_task_statuses_from_steps(context)
        context.output = message
        context.summary = message
        context.error_code = checker_result.code or observation.code or ACTION_FAILED_CODE
        context.failed_step_id = step_id or None
        event_status = "blocked" if final_step_status in {"blocked", "retrying", "fallback_used"} else "failed"
        context.event_stream.emit_event(
            "step_failed",
            message,
            payload={
                "checker_result": checker_result.to_dict(),
                "observation_id": observation.observation_id,
                "deferred_transition": status if status in {"retry", "fallback_to_model", "fallback_to_tool"} else None,
            },
            task_id=task_id or None,
            step_id=step_id or None,
        )
        self._log_checker_transition(context, step=step, packet=packet, observation=observation, checker_result=checker_result, transition=event_status)
        return ReActStepLoopResult(event_status, False, message=message, error_code=context.error_code, packet=packet, observation=observation, checker_result=checker_result)

    def _mark_step_running(self, context: ReActExecutionContext, task_id: str, step_id: str) -> None:
        state = context.step_states.get(step_id)
        if state is None:
            state = StepRuntimeState(step_id=step_id, status="running")
            context.step_states[step_id] = state
        state.status = "running"
        task_state = context.task_states.get(task_id)
        if task_state is None and task_id:
            task_state = TaskUnitRuntimeState(task_id=task_id, status="running", step_statuses={step_id: "running"})
            context.task_states[task_id] = task_state
        elif task_state is not None:
            task_state.status = "running"
            task_state.step_statuses[step_id] = "running"

    def _set_step_status(self, context: ReActExecutionContext, step_id: str, status: str, *, message: str, error_code: str | None) -> None:
        if not step_id:
            return
        state = context.step_states.get(step_id)
        if state is None:
            state = StepRuntimeState(step_id=step_id, status=status)
            context.step_states[step_id] = state
        state.status = status
        state.message = message
        state.error_code = error_code
        for task_state in context.task_states.values():
            if step_id in task_state.step_statuses:
                task_state.step_statuses[step_id] = status

    def _finalize_confirmation_preview_conflict(
        self,
        context: ReActExecutionContext,
        *,
        step: Any | None,
        packet: ActionPacket,
        observation: ObservationPacket,
        turn_state: ReActTurnState,
    ) -> ExecutionResult:
        step_id = str(getattr(step, "id", "") or observation.step_id or packet.step_id or "")
        task_id = str(getattr(step, "task_id", "") or observation.task_id or packet.task_id or "")
        message = (
            observation.error
            or observation.message
            or "The approved preview no longer matches the current resource state."
        )
        checker_payload = {
            "checker_status": "fail",
            "success": False,
            "code": PREVIEW_CONFLICT_CODE,
            "reason": message,
            "step_status": "failed",
            "execution_status": "failed",
            "metadata": {"preview_conflict": True},
        }
        observation.checker_result = checker_payload
        context.loop_state.record_checker_result(checker_payload)
        self._clear_pending_confirmation(context)
        self._set_step_status(context, step_id, "failed", message=message, error_code=PREVIEW_CONFLICT_CODE)
        self._sync_task_statuses_from_steps(context)
        context.output = message
        context.summary = message
        context.error_code = PREVIEW_CONFLICT_CODE
        context.failed_step_id = step_id or None
        context.event_stream.emit_event(
            "step_failed",
            message,
            payload={
                "error_code": PREVIEW_CONFLICT_CODE,
                "observation_id": observation.observation_id,
                "checker_result": checker_payload,
            },
            task_id=task_id or None,
            step_id=step_id or None,
        )
        context.event_stream.emit_event(
            "final_answer",
            message,
            payload={"status": "failed", "error_code": PREVIEW_CONFLICT_CODE},
            task_id=task_id or None,
            step_id=step_id or None,
        )
        turn_state.finish("failed")
        context.loop_state.finish("failed")
        return self._build_result(context, status="failed", success=False)

    def _fail_step_without_observation(self, context: ReActExecutionContext, step: Any, message: str, error_code: str) -> ReActStepLoopResult:
        step_id = str(getattr(step, "id", "") or "")
        task_id = str(getattr(step, "task_id", "") or "")
        self._set_step_status(context, step_id, "failed", message=message, error_code=error_code)
        self._sync_task_statuses_from_steps(context)
        context.output = message
        context.summary = message
        context.error_code = error_code
        context.failed_step_id = step_id or None
        context.event_stream.emit_event(
            "step_failed",
            message,
            payload={"error_code": error_code},
            task_id=task_id or None,
            step_id=step_id or None,
        )
        return ReActStepLoopResult("failed", False, message=message, error_code=error_code)

    def _log_checker_transition(
        self,
        context: ReActExecutionContext,
        *,
        step: Any,
        packet: ActionPacket | None,
        observation: ObservationPacket,
        checker_result: Any,
        transition: str,
    ) -> None:
        self.execution_logger.write_record(
            context,
            record_type="transition_decision",
            task_id=getattr(step, "task_id", None),
            step_id=getattr(step, "id", None),
            packet_id=packet.packet_id if packet else observation.packet_id,
            action_type=packet.action_type if packet else observation.action_type,
            action_target=packet.action_target if packet else observation.action_target,
            success=checker_result.success,
            code=checker_result.code,
            checker_result=checker_result.to_dict(),
            observation_id=observation.observation_id,
            metadata={"transition": transition},
        )

    def _step_dependency_issue(self, context: ReActExecutionContext, step: Any) -> Dict[str, Any] | None:
        refs = [str(ref) for ref in list(getattr(step, "depends_on", []) or [])]
        for ref in refs:
            state = context.step_states.get(ref)
            if state is None:
                return {"ref": ref, "kind": "depends_on", "reason": "missing_step_state"}
            if state.status not in {"completed", "skipped"}:
                return {"ref": ref, "kind": "depends_on", "reason": f"dependency_status_{state.status}"}

        input_refs = [str(ref) for ref in list(getattr(step, "input_from", []) or [])]
        for ref in input_refs:
            observation = context.observation_store.get_by_output_key(ref) or context.observation_store.get_latest_for_step(ref) or context.observation_store.get(ref)
            if observation is None:
                return {"ref": ref, "kind": "input_from", "reason": "missing_observation"}
            if not observation.success:
                return {"ref": ref, "kind": "input_from", "reason": "observation_failed", "observation_id": observation.observation_id}
        return None

    def _block_step_for_dependency(
        self,
        context: ReActExecutionContext,
        task_id: str,
        step: Any,
        issue: Dict[str, Any],
    ) -> ReActStepLoopResult:
        step_id = str(getattr(step, "id", "") or "")
        on_failure = str(getattr(step, "on_failure", "") or "").lower()
        status = "skipped" if on_failure == "continue" else "blocked"
        message = f"Step dependency is not satisfied: {issue.get('kind')} -> {issue.get('ref')} ({issue.get('reason')})."
        self._set_step_status(context, step_id, status, message=message, error_code=PLAN_REFERENCE_ERROR_CODE)
        if status == "blocked":
            context.failed_step_id = context.failed_step_id or step_id
            context.error_code = context.error_code or PLAN_REFERENCE_ERROR_CODE
            context.output = message
            context.summary = message
        self._sync_task_statuses_from_steps(context)
        event_type = "step_failed" if status == "blocked" else "step_completed"
        context.event_stream.emit_event(
            event_type,
            message,
            payload={"dependency_issue": issue, "status": status, "error_code": PLAN_REFERENCE_ERROR_CODE},
            task_id=task_id or None,
            step_id=step_id or None,
        )
        return ReActStepLoopResult(
            "blocked" if status == "blocked" else "completed",
            status == "skipped",
            message=message,
            error_code=PLAN_REFERENCE_ERROR_CODE if status == "blocked" else None,
        )

    def _block_dependent_remaining_steps(
        self,
        context: ReActExecutionContext,
        task_id: str,
        task_unit: Any,
        *,
        after_step_id: str,
    ) -> None:
        remaining = False
        blocked_by = {str(after_step_id)}
        for candidate_id in list(getattr(task_unit, "step_ids", []) or []):
            step_id = str(candidate_id)
            if step_id == str(after_step_id):
                remaining = True
                continue
            if not remaining:
                continue

            step = context.step_lookup.get(step_id)
            if step is None:
                continue
            state = context.step_states.get(step_id)
            if state is not None and state.status not in {"pending", "running"}:
                continue

            depends_on = {str(ref) for ref in list(getattr(step, "depends_on", []) or [])}
            input_from = {str(ref) for ref in list(getattr(step, "input_from", []) or [])}
            output_key = getattr(context.step_lookup.get(after_step_id), "output_key", None)
            refs = set(blocked_by)
            if output_key:
                refs.add(str(output_key))
            if refs.intersection(depends_on) or refs.intersection(input_from):
                message = f"Skipped because dependency failed before this step: {after_step_id}."
                self._set_step_status(
                    context,
                    step_id,
                    "blocked",
                    message=message,
                    error_code=PLAN_REFERENCE_ERROR_CODE,
                )
                blocked_by.add(step_id)
                context.event_stream.emit_event(
                    "step_failed",
                    message,
                    payload={
                        "blocked_by_step_id": after_step_id,
                        "status": "blocked",
                        "error_code": PLAN_REFERENCE_ERROR_CODE,
                    },
                    task_id=task_id or None,
                    step_id=step_id,
                )

    def _aggregate_execution_status(self, context: ReActExecutionContext) -> str:
        statuses = set(state.status for state in context.step_states.values())
        if not statuses or statuses.issubset({"completed", "skipped"}):
            return "completed"
        if "waiting_user" in statuses:
            return "waiting_user"
        if context.request_replan:
            return "request_replan"
        if "blocked" in statuses:
            return "blocked"
        if "failed" in statuses:
            if any(status == "completed" for status in statuses):
                return "partial_failed"
            return "failed"
        if "cancelled" in statuses:
            return "cancelled"
        if "running" in statuses:
            return "blocked"
        return "failed"

    def _can_continue_after_task_failure(self, context: ReActExecutionContext, task_unit: Any) -> bool:
        task_id = str(getattr(task_unit, "id", "") or "")
        task_state = context.task_states.get(task_id)
        if task_state is None:
            return False
        statuses = set(task_state.step_statuses.values())
        return bool(statuses and statuses.issubset({"completed", "failed", "skipped"}))

    def _build_action_decision_prompt(
        self,
        context: ReActExecutionContext,
        task_unit: Any | None,
        step: Any | None,
        turn_state: ReActTurnState,
    ) -> tuple[str, Dict[str, Any]]:
        input_from = [str(ref) for ref in list(getattr(step, "input_from", []) or [])] if step is not None else []
        turn_context = turn_state.to_model_context()
        model_observation_chars = self.config.max_model_observation_chars
        prompt_context = ReActPromptContext(
            user_input=context.user_input,
            analyzer_summary=self._analyzer_prompt_summary(context.task),
            task_plan=self._task_plan_prompt_summary(context),
            current_task_unit=self._task_unit_prompt_context(context, task_unit),
            current_step=self._step_prompt_context(context, step),
            available_tools=self._available_tool_prompt_specs(context),
            previous_action=turn_context.get("previous_action"),
            previous_observation=turn_context.get("previous_observation"),
            execution_progress={
                "input_observations": context.observation_store.to_model_context(
                    input_from,
                    max_value_chars=model_observation_chars,
                ),
                "recent_observations": context.observation_store.recent_model_context(
                    max_observations=self.config.max_recent_observations,
                    max_value_chars=model_observation_chars,
                ),
                "recent_events": context.event_stream.to_model_context(max_events=20),
                "task_statuses": {task_id: state.status for task_id, state in context.task_states.items()},
                "step_statuses": {step_id: state.status for step_id, state in context.step_states.items()},
                "loop": context.loop_state.to_model_context(),
            },
            history_summary=str(context.history or ""),
            extra_context={
                "turn": turn_context,
                "safety_constraints": self._safety_prompt_context(),
                "allowed_action_types": sorted(ACTION_TYPES),
                "tool_calls_allowed": str(getattr(context.plan, "mode", "") or "") != "chat",
                "required_response": "Return exactly one ActionPacket JSON object. Do not include Markdown or prose outside JSON.",
            },
        )
        prompt = build_react_executor_prompt(prompt_context, root=self.config.root)
        return prompt, prompt_context.to_dict()

    def _log_action_decision_prompt(
        self,
        context: ReActExecutionContext,
        *,
        task_id: str | None,
        step_id: str | None,
        turn_state: ReActTurnState,
        prompt: str,
        input_payload: Dict[str, Any],
    ) -> None:
        self.execution_logger.write_record(
            context,
            record_type="action_decision_prompt",
            task_id=task_id,
            step_id=step_id,
            attempt=turn_state.attempt,
            success=None,
            code=None,
            metadata={
                "turn_id": turn_state.turn_id,
                "prompt": build_prompt_log_summary(prompt),
                "input_summary": input_payload,
            },
        )

    def _request_action_packet(
        self,
        context: ReActExecutionContext,
        *,
        prompt: str,
        task_unit: Any | None,
        step: Any | None,
        turn_state: ReActTurnState,
    ) -> ActionPacketRequestResult:
        if self.model_manager is None or not hasattr(self.model_manager, "generate"):
            packet = self._failure_action_packet(
                context,
                step=step,
                reason="ModelManager is unavailable for ActionPacket decision.",
            )
            observation = self._record_observation(
                context,
                self._observation_from_packet(
                    context,
                    packet,
                    attempt=turn_state.attempt,
                    success=False,
                    message="ModelManager is unavailable for ActionPacket decision.",
                    error="ModelManager is unavailable for ActionPacket decision.",
                    code=ACTION_PACKET_MODEL_UNAVAILABLE_CODE,
                    model_consumable_observation={"success": False, "code": ACTION_PACKET_MODEL_UNAVAILABLE_CODE},
                ),
            )
            return ActionPacketRequestResult(packet=None, observation=observation, prompt=prompt)

        current_prompt = prompt
        max_repairs = min(max(int(self.config.max_action_packet_repair_attempts), 0), 5)
        available_tools = sorted(self._available_tool_names(context))
        fallback_tools = self._fallback_tool_names(step)
        current_step_id = str(getattr(step, "id", "") or "") or None
        retry_attempts = self._current_retry_attempts(context, step)
        max_retries = max(int(getattr(step, "max_retries", self.config.default_tool_max_retries) or 0), 0) if step is not None else self.config.default_tool_max_retries
        last_parse_result: ActionPacketParseResult | None = None
        last_raw_output: Any = None

        for repair_attempt in range(max_repairs + 1):
            model_call_id = f"{turn_state.turn_id}:action_decision:{repair_attempt}"
            context.event_stream.emit_event(
                "progress_message",
                "Requesting the next structured action from the model.",
                payload={
                    "turn_id": turn_state.turn_id,
                    "repair_attempt": repair_attempt,
                    "max_repair_attempts": max_repairs,
                    "available_tool_count": len(available_tools),
                },
                task_id=getattr(step, "task_id", None),
                step_id=getattr(step, "id", None),
            )
            context.event_stream.emit_event(
                "model_step_started",
                "Requesting the next structured action from the model.",
                payload={
                    "model_call_id": model_call_id,
                    "turn_id": turn_state.turn_id,
                    "repair_attempt": repair_attempt,
                    "max_repair_attempts": max_repairs,
                    "model_step": "action_decision",
                },
                task_id=getattr(step, "task_id", None),
                step_id=getattr(step, "id", None),
            )
            parse_result: ActionPacketParseResult | None = None
            try:
                model_output = self._action_packet_model_output(
                    current_prompt,
                    repair_attempt=repair_attempt,
                )
                if isinstance(model_output, StructuredModelResult):
                    if not model_output.success:
                        if model_output.code in STRUCTURED_JSON_FAILURE_CODES:
                            raw_output = model_output.content
                            errors = [model_output.error or model_output.code or "invalid structured JSON output"]
                            parse_result = ActionPacketParseResult(
                                success=False,
                                errors=errors,
                                needs_repair=True,
                                repair_prompt=self._build_action_packet_repair_prompt(
                                    errors,
                                    raw_output,
                                    step=step,
                                    available_tools=available_tools,
                                ),
                                raw_model_output=raw_output,
                            )
                        elif isinstance(model_output.model_result, ModelCallResult):
                            raise ModelCallFailure(model_output.model_result)
                        else:
                            raise RuntimeError(model_output.error or model_output.code or "model call failed")
                    else:
                        raw_output = model_output.data
                else:
                    raw_output = model_output
            except ModelCallFailure as failure:
                context.event_stream.emit_event(
                    "model_step_finished",
                    "Model ActionPacket decision call failed.",
                    payload={
                        "model_call_id": model_call_id,
                        "turn_id": turn_state.turn_id,
                        "repair_attempt": repair_attempt,
                        "model_step": "action_decision",
                        "success": False,
                        "code": ACTION_PACKET_MODEL_EXCEPTION_CODE,
                        "error_summary": self._truncate_summary_text(failure.result.error or str(failure), 300),
                    },
                    task_id=getattr(step, "task_id", None),
                    step_id=getattr(step, "id", None),
                )
                packet = self._failure_action_packet(context, step=step, reason=failure.result.error or str(failure))
                observation = self._record_observation(
                    context,
                    self._observation_from_packet(
                        context,
                        packet,
                        attempt=turn_state.attempt,
                        success=False,
                        message="Model ActionPacket decision call failed.",
                        error=failure.result.error or str(failure),
                        code=ACTION_PACKET_MODEL_EXCEPTION_CODE,
                        raw_observation=failure.result,
                        model_consumable_observation={
                            "success": False,
                            "code": ACTION_PACKET_MODEL_EXCEPTION_CODE,
                            "error": failure.result.error or str(failure),
                        },
                    ),
                )
                return ActionPacketRequestResult(
                    packet=None,
                    observation=observation,
                    raw_output=failure.result,
                    repair_attempts=repair_attempt,
                    prompt=current_prompt,
                )
            except Exception as exc:
                context.event_stream.emit_event(
                    "model_step_finished",
                    "Model ActionPacket decision call failed.",
                    payload={
                        "model_call_id": model_call_id,
                        "turn_id": turn_state.turn_id,
                        "repair_attempt": repair_attempt,
                        "model_step": "action_decision",
                        "success": False,
                        "code": ACTION_PACKET_MODEL_EXCEPTION_CODE,
                        "error_summary": self._truncate_summary_text(str(exc), 300),
                    },
                    task_id=getattr(step, "task_id", None),
                    step_id=getattr(step, "id", None),
                )
                packet = self._failure_action_packet(context, step=step, reason=str(exc))
                observation = self._record_observation(
                    context,
                    self._observation_from_packet(
                        context,
                        packet,
                        attempt=turn_state.attempt,
                        success=False,
                        message="Model ActionPacket decision call failed.",
                        error=str(exc),
                        code=ACTION_PACKET_MODEL_EXCEPTION_CODE,
                        raw_observation=exc,
                        model_consumable_observation={
                            "success": False,
                            "code": ACTION_PACKET_MODEL_EXCEPTION_CODE,
                            "error": str(exc),
                        },
                    ),
                )
                return ActionPacketRequestResult(
                    packet=None,
                    observation=observation,
                    raw_output=exc,
                    repair_attempts=repair_attempt,
                    prompt=current_prompt,
                )

            last_raw_output = raw_output
            if parse_result is None:
                strict_errors = self._strict_action_packet_output_errors(raw_output)
                if strict_errors:
                    parse_result = ActionPacketParseResult(
                        success=False,
                        errors=strict_errors,
                        needs_repair=True,
                        repair_prompt=self._build_action_packet_repair_prompt(
                            strict_errors,
                            raw_output,
                            step=step,
                            available_tools=available_tools,
                        ),
                        raw_model_output=raw_output,
                    )
                else:
                    parse_result = parse_action_packet(
                        raw_output,
                        execution_id=context.execution_id,
                        plan_id=context.plan_id,
                        task_id=getattr(task_unit, "id", None) or getattr(step, "task_id", None),
                        step_id=getattr(step, "id", None),
                        available_tools=available_tools,
                        fallback_tools=fallback_tools,
                        current_step_id=current_step_id,
                        recent_failed_action_ids=self._recent_failed_action_ids(context, current_step_id),
                        retry_attempts=retry_attempts,
                        max_retries=max_retries,
                    )
                    if not parse_result.success:
                        parse_result.repair_prompt = self._build_action_packet_repair_prompt(
                            parse_result.errors,
                            raw_output,
                            step=step,
                            available_tools=available_tools,
                        )
            last_parse_result = parse_result
            self._log_model_action_output(
                context,
                parse_result,
                raw_output=raw_output,
                repair_attempt=repair_attempt,
                turn_state=turn_state,
                step=step,
            )
            if parse_result.packet is not None:
                self.execution_logger.log_action_packet(
                    context,
                    parse_result.packet,
                    attempt=turn_state.attempt,
                    schema_valid=parse_result.success,
                    schema_errors=parse_result.errors,
                    repair_attempts=repair_attempt,
                )

            context.event_stream.emit_event(
                "model_step_finished",
                "Structured action received." if parse_result.success else "Structured action was invalid.",
                payload={
                    "model_call_id": model_call_id,
                    "turn_id": turn_state.turn_id,
                    "repair_attempt": repair_attempt,
                    "model_step": "action_decision",
                    "success": parse_result.success,
                    "schema_valid": parse_result.success,
                    "code": None if parse_result.success else ACTION_PACKET_INVALID_CODE,
                    "schema_errors": list(parse_result.errors),
                    "action_summary": self._action_packet_public_summary(parse_result.packet),
                    "raw_output_chars": len(str(raw_output or "")),
                },
                task_id=getattr(step, "task_id", None),
                step_id=getattr(step, "id", None),
            )

            if parse_result.success and parse_result.packet is not None:
                packet = parse_result.packet
                context.loop_state.record_action(packet)
                turn_state.thought_summary = packet.thought_summary
                turn_state.user_visible_message = packet.user_visible_message
                return ActionPacketRequestResult(
                    packet=packet,
                    observation=None,
                    parse_result=parse_result,
                    raw_output=raw_output,
                    repair_attempts=repair_attempt,
                    prompt=current_prompt,
                )

            if repair_attempt < max_repairs:
                self.execution_logger.write_record(
                    context,
                    record_type="action_packet_repair",
                    task_id=getattr(step, "task_id", None),
                    step_id=getattr(step, "id", None),
                    attempt=turn_state.attempt,
                    schema_valid=False,
                    repair_attempts=repair_attempt + 1,
                    success=None,
                    code=ACTION_PACKET_INVALID_CODE,
                    metadata={
                        "turn_id": turn_state.turn_id,
                        "schema_errors": list(parse_result.errors),
                        "raw_output_summary": self._raw_output_summary(raw_output),
                        "repair_prompt_summary": self._raw_output_summary(parse_result.repair_prompt),
                        "allowed_action_types": sorted(ACTION_TYPES),
                        "current_step_id": current_step_id,
                        "available_tools": available_tools,
                    },
                )
                current_prompt = parse_result.repair_prompt

        errors = list(last_parse_result.errors) if last_parse_result else ["ActionPacket parse failed."]
        failed_packet = (last_parse_result.packet if last_parse_result and last_parse_result.packet is not None else None) or self._failure_action_packet(
            context,
            step=step,
            reason="ActionPacket generation failed after repair attempts.",
        )
        observation = self._record_observation(
            context,
            self._observation_from_packet(
                context,
                failed_packet,
                attempt=turn_state.attempt,
                success=False,
                message="ActionPacket generation failed after repair attempts.",
                error="; ".join(errors),
                code=ACTION_PACKET_INVALID_CODE,
                data={"errors": errors, "repair_attempts": max_repairs, "raw_output_summary": self._raw_output_summary(last_raw_output)},
                raw_observation=last_raw_output,
                model_consumable_observation={
                    "success": False,
                    "code": ACTION_PACKET_INVALID_CODE,
                    "errors": errors,
                    "repair_attempts": max_repairs,
                },
                checker_result={"execution_status": "failed", "step_status": "failed"},
            ),
        )
        return ActionPacketRequestResult(
            packet=None,
            observation=observation,
            parse_result=last_parse_result,
            raw_output=last_raw_output,
            repair_attempts=max_repairs,
            prompt=current_prompt,
        )

    def _log_model_action_output(
        self,
        context: ReActExecutionContext,
        parse_result: ActionPacketParseResult,
        *,
        raw_output: Any,
        repair_attempt: int,
        turn_state: ReActTurnState,
        step: Any | None,
    ) -> None:
        self.execution_logger.write_record(
            context,
            record_type="model_action_output",
            task_id=getattr(step, "task_id", None),
            step_id=getattr(step, "id", None),
            packet_id=parse_result.packet.packet_id if parse_result.packet else None,
            action_type=parse_result.packet.action_type if parse_result.packet else None,
            action_target=parse_result.packet.action_target if parse_result.packet else None,
            attempt=turn_state.attempt,
            schema_valid=parse_result.success,
            repair_attempts=repair_attempt,
            success=parse_result.success,
            code=None if parse_result.success else ACTION_PACKET_INVALID_CODE,
            error=None if parse_result.success else "; ".join(parse_result.errors),
            metadata={
                "turn_id": turn_state.turn_id,
                "schema_errors": list(parse_result.errors),
                "raw_output_summary": self._raw_output_summary(raw_output),
                "needs_repair": parse_result.needs_repair,
            },
        )

    def _action_packet_model_output(
        self,
        prompt: str,
        *,
        repair_attempt: int,
    ) -> Any:
        generate_json = getattr(self.model_manager, "generate_json", None)
        if callable(generate_json):
            call_type = "react_action_repair" if repair_attempt > 0 else "react_action_decision"
            return generate_json(prompt, call_type=call_type, parse_mode="strict")
        return require_model_content(self.model_manager.generate(prompt))

    def _build_action_packet_repair_prompt(
        self,
        errors: List[str],
        raw_output: Any,
        *,
        step: Any | None,
        available_tools: List[str],
    ) -> str:
        error_lines = "\n".join(f"- {error}" for error in errors)
        return "\n".join(
            [
                "Your previous response was not a valid ActionPacket.",
                "Return exactly one strict JSON object. Do not include Markdown, prose, or explanations.",
                "Schema or contract errors:",
                error_lines,
                "Current step_id:",
                str(getattr(step, "id", None)),
                "Allowed action_type values:",
                json.dumps(sorted(ACTION_TYPES), ensure_ascii=False),
                "Available tool names:",
                json.dumps(available_tools, ensure_ascii=False),
                "Previous model output summary:",
                json.dumps(self._raw_output_summary(raw_output), ensure_ascii=False),
            ]
        )

    def _strict_action_packet_output_errors(self, raw_output: Any) -> List[str]:
        if isinstance(raw_output, dict):
            return []
        if not isinstance(raw_output, str):
            return ["model output must be a dict or strict JSON string"]
        text = raw_output.strip()
        if not text:
            return ["model output is empty"]
        try:
            payload = json.loads(text)
            return [] if isinstance(payload, dict) else ["top-level JSON must be an object"]
        except json.JSONDecodeError:
            pass
        if text.startswith("```") and text.endswith("```"):
            return []
        return ["model output must contain only one ActionPacket JSON object; mixed prose is not allowed"]

    def _failure_action_packet(self, context: ReActExecutionContext, *, step: Any | None, reason: str) -> ActionPacket:
        return ActionPacket(
            execution_id=context.execution_id,
            plan_id=context.plan_id,
            task_id=getattr(step, "task_id", None),
            step_id=getattr(step, "id", None),
            action_type="fail",
            action_args={"reason": reason},
            final_answer=reason,
            user_visible_message=reason,
        )

    def _current_retry_attempts(self, context: ReActExecutionContext, step: Any | None) -> int:
        step_id = str(getattr(step, "id", "") or "")
        state = context.step_states.get(step_id) if step_id else None
        return max(int(getattr(step, "attempts", 0) or 0), int(getattr(state, "attempts", 0) or 0), 0)

    def _recent_failed_action_ids(self, context: ReActExecutionContext, step_id: str | None) -> List[str]:
        failed: List[str] = []
        for observation in reversed(context.observation_store.observations):
            if step_id and observation.step_id != step_id:
                continue
            if not observation.success and observation.packet_id:
                failed.append(observation.packet_id)
            if len(failed) >= 5:
                break
        return failed

    def _raw_output_summary(self, value: Any, *, max_chars: int = 600) -> Any:
        sanitized = sanitize_sensitive(value)
        if isinstance(sanitized, (dict, list)):
            text = json.dumps(sanitized, ensure_ascii=False)
        else:
            text = str(sanitized)
        if len(text) <= max_chars:
            return sanitized if isinstance(sanitized, (dict, list)) else text
        return {"truncated": True, "original_chars": len(text), "preview": text[:max_chars]}

    def _analyzer_prompt_summary(self, task: Any) -> Dict[str, Any]:
        fields = [
            "trace_id",
            "mode",
            "mode_source",
            "intent_sequence",
            "parameters",
            "entities",
            "file_info",
            "missing_parameters",
            "clarification_questions",
            "task_type",
            "project_stage",
            "tech_stacks",
            "risk_level",
            "risk_flags",
            "action_policy",
            "requires_confirmation",
            "confirmation_reason",
            "recommended_tools",
            "available_tools",
            "missing_tools",
            "tool_strategy",
            "complexity_level",
            "execution_strategy",
            "confidence",
            "decision_summary",
            "user_visible_summary",
        ]
        return sanitize_sensitive({field: getattr(task, field, None) for field in fields if hasattr(task, field)})

    def _task_plan_prompt_summary(self, context: ReActExecutionContext) -> Dict[str, Any]:
        plan = context.plan
        steps = list(getattr(plan, "steps", []) or [])
        task_units = self._task_units_for_plan(plan)
        available_tool_names = sorted(self._available_tool_names(context))
        required_tool_names = [
            str(tool)
            for tool in list(getattr(plan, "required_tools", []) or [])
            if str(tool) in set(available_tool_names)
        ]
        return sanitize_sensitive(
            {
                "plan_id": getattr(plan, "plan_id", None),
                "source_trace_id": getattr(plan, "source_trace_id", None),
                "goal": getattr(plan, "goal", None),
                "mode": getattr(plan, "mode", None),
                "task_type": getattr(plan, "task_type", None),
                "execution_strategy": getattr(plan, "execution_strategy", None),
                "planning_strategy": getattr(plan, "planning_strategy", None),
                "can_execute": getattr(plan, "can_execute", None),
                "risk_policy": getattr(plan, "risk_policy", None),
                "required_tools": required_tool_names,
                "available_tools": available_tool_names,
                "missing_tools": list(getattr(plan, "missing_tools", []) or []),
                "plan_validation_status": getattr(plan, "plan_validation_status", None),
                "plan_validation_notes": list(getattr(plan, "plan_validation_notes", []) or []),
                "user_facing_summary": getattr(plan, "user_facing_summary", None),
                "step_count": len(steps),
                "task_unit_count": len(task_units),
                "steps": [self._plan_step_outline(step) for step in steps],
            }
        )

    def _plan_step_outline(self, step: Any) -> Dict[str, Any]:
        return sanitize_sensitive(
            {
                "id": getattr(step, "id", None),
                "task_id": getattr(step, "task_id", None),
                "description": getattr(step, "description", None),
                "step_type": getattr(step, "step_type", None),
                "tool_name": getattr(step, "tool_name", None),
                "depends_on": list(getattr(step, "depends_on", []) or []),
                "input_from": list(getattr(step, "input_from", []) or []),
                "output_key": getattr(step, "output_key", None),
                "requires_confirmation": bool(getattr(step, "requires_confirmation", False)),
                "on_failure": getattr(step, "on_failure", None),
            }
        )

    def _task_unit_prompt_context(self, context: ReActExecutionContext, task_unit: Any | None) -> Dict[str, Any] | None:
        if task_unit is None:
            return None
        task_id = str(getattr(task_unit, "id", "") or "")
        runtime_state = context.task_states.get(task_id)
        return sanitize_sensitive(
            {
                "id": getattr(task_unit, "id", None),
                "title": getattr(task_unit, "title", None),
                "description": getattr(task_unit, "description", None),
                "intent_refs": list(getattr(task_unit, "intent_refs", []) or []),
                "task_type": getattr(task_unit, "task_type", None),
                "depends_on": list(getattr(task_unit, "depends_on", []) or []),
                "step_ids": list(getattr(task_unit, "step_ids", []) or []),
                "expected_outcome": getattr(task_unit, "expected_outcome", None),
                "runtime_state": runtime_state.to_dict() if runtime_state else None,
            }
        )

    def _step_prompt_context(self, context: ReActExecutionContext | None, step: Any | None) -> Dict[str, Any] | None:
        if step is None:
            return None
        step_id = str(getattr(step, "id", "") or "")
        runtime_state = context.step_states.get(step_id) if context is not None else None
        tool_name = getattr(step, "tool_name", None)
        registered_tool = self.tool_registry.get(str(tool_name)) if tool_name else None
        registered_tool_spec = None
        if tool_name in COMMAND_TOOL_NAMES and registered_tool is not None:
            registered_tool_spec = registered_tool.to_model_spec()
        return sanitize_sensitive(
            {
                "id": getattr(step, "id", None),
                "task_id": getattr(step, "task_id", None),
                "description": getattr(step, "description", None),
                "step_type": getattr(step, "step_type", None),
                "tool_name": tool_name,
                "registered_tool_available": registered_tool is not None,
                "registered_tool_spec": registered_tool_spec,
                "args": dict(getattr(step, "args", {}) or {}),
                "expected_output": getattr(step, "expected_output", None),
                "input_from": list(getattr(step, "input_from", []) or []),
                "output_key": getattr(step, "output_key", None),
                "depends_on": list(getattr(step, "depends_on", []) or []),
                "on_failure": getattr(step, "on_failure", None),
                "requires_confirmation": bool(getattr(step, "requires_confirmation", False)),
                "confirmation_reason": getattr(step, "confirmation_reason", None),
                "retryable": bool(getattr(step, "retryable", False)),
                "max_retries": getattr(step, "max_retries", None),
                "fallback_tools": list(getattr(step, "fallback_tools", []) or []),
                "allow_model_reasoning": bool(getattr(step, "allow_model_reasoning", False)),
                "metadata": dict(getattr(step, "metadata", {}) or {}),
                "runtime_state": runtime_state.to_dict() if runtime_state else None,
            }
        )

    def _available_tool_prompt_specs(self, context: ReActExecutionContext) -> List[Dict[str, Any]]:
        allowed_tool_names = self._available_tool_names(context)
        specs = []
        for spec in self.tool_registry.list_specs():
            if spec.name not in allowed_tool_names:
                continue
            specs.append(spec.to_model_spec())
        return sanitize_sensitive(sorted(specs, key=lambda item: str(item.get("name", ""))))

    def _safety_prompt_context(self) -> Dict[str, Any]:
        return {
            "workspace_root": str(self.config.workspace_root),
            "command_confirmation_policy": self.config.command_confirmation_policy,
            "command_tool_enabled": self.config.enable_command_tool,
            "max_execution_turns": self.config.max_execution_turns,
            "max_step_turns": self.config.max_step_turns,
            "command_actions_must_use_tool_layer": True,
            "observations_are_executor_generated": True,
            "user_visible_events_separate_from_development_logs": True,
        }

    def _traverse_plan_skeleton(self, context: ReActExecutionContext) -> ExecutionResult:
        """Run the retired skeleton only when explicitly requested for diagnostics."""
        for task_unit in self._task_units_for_plan(context.plan):
            task_id = str(getattr(task_unit, "id", "task_1"))
            task_state = context.task_states.get(task_id)
            if task_state is None:
                task_state = TaskUnitRuntimeState(task_id=task_id, status="pending")
                context.task_states[task_id] = task_state
            task_state.status = "running"

            for step_id in list(getattr(task_unit, "step_ids", []) or []):
                self._traverse_step_skeleton(context, task_id, str(step_id))

            if any(status == "failed" for status in task_state.step_statuses.values()):
                task_state.status = "failed"
            elif any(status == "blocked" for status in task_state.step_statuses.values()):
                task_state.status = "blocked"
            elif task_state.step_statuses:
                task_state.status = "completed"
            else:
                task_state.status = "skipped"

        message = "Legacy diagnostic skeleton traversal bypassed the ActionPacket loop."
        context.output = message
        context.summary = message
        if context.error_code is None:
            context.error_code = ACTION_LOOP_NOT_IMPLEMENTED_CODE
        context.event_stream.emit_event(
            "system_notice",
            message,
            payload={"reason": context.error_code},
        )
        context.event_stream.emit_event("final_answer", message, payload={"status": "blocked"})
        status = "failed" if context.error_code == MISSING_STEP_CODE else "blocked"
        return self._build_result(context, status=status, success=False)

    def _traverse_step_skeleton(self, context: ReActExecutionContext, task_id: str, step_id: str) -> None:
        """Mark one step as blocked for the explicit legacy diagnostic traversal."""
        step = context.step_lookup.get(step_id)
        task_state = context.task_states[task_id]
        if step is None:
            state = StepRuntimeState(
                step_id=step_id,
                status="failed",
                error_code=MISSING_STEP_CODE,
                message=f"TaskUnit references missing step: {step_id}",
            )
            context.step_states[step_id] = state
            task_state.step_statuses[step_id] = "failed"
            context.failed_step_id = step_id
            context.error_code = MISSING_STEP_CODE
            context.event_stream.emit_event(
                "step_failed",
                state.message,
                payload={"step_id": step_id, "error_code": MISSING_STEP_CODE},
                task_id=task_id,
                step_id=step_id,
            )
            return

        state = context.step_states[step_id]
        state.status = "running"
        state.attempts = 0
        task_state.step_statuses[step_id] = "running"
        context.event_stream.emit_event(
            "step_started",
            str(getattr(step, "description", "")),
            payload={
                "step_id": step_id,
                "step_type": getattr(step, "step_type", None),
                "tool_name": getattr(step, "tool_name", None),
                "output_key": getattr(step, "output_key", None),
            },
            task_id=task_id,
            step_id=step_id,
        )

        state.status = "blocked"
        state.error_code = ACTION_LOOP_NOT_IMPLEMENTED_CODE
        state.message = "ActionPacket loop was intentionally bypassed by legacy diagnostic traversal."
        task_state.step_statuses[step_id] = "blocked"
        context.failed_step_id = context.failed_step_id or step_id
        context.event_stream.emit_event(
            "step_failed",
            state.message,
            payload={
                "step_id": step_id,
                "status": "blocked",
                "error_code": ACTION_LOOP_NOT_IMPLEMENTED_CODE,
            },
            task_id=task_id,
            step_id=step_id,
        )

    def _build_result(self, context: ReActExecutionContext, *, status: str, success: bool) -> ExecutionResult:
        result_summary = self.result_builder.build(context, status=status, success=success)
        context.output = result_summary.output
        context.summary = result_summary.summary
        context.request_replan = result_summary.request_replan
        context.replan_reason = result_summary.replan_reason
        return ExecutionResult(
            execution_id=context.execution_id,
            plan_id=context.plan_id,
            source_trace_id=context.source_trace_id,
            status=status,
            success=success,
            output=result_summary.output,
            summary=result_summary.summary,
            task_statuses={task_id: state.status for task_id, state in context.task_states.items()},
            step_statuses={step_id: state.status for step_id, state in context.step_states.items()},
            observations=list(context.observation_store.observations),
            events=list(context.event_stream.events),
            failed_step_id=context.failed_step_id,
            error_code=context.error_code,
            requires_user_input=context.requires_user_input,
            user_input_request=context.user_input_request,
            pending_confirmation=context.pending_confirmation,
            request_replan=result_summary.request_replan,
            replan_reason=result_summary.replan_reason,
        )

    def _mark_all_states(self, context: ReActExecutionContext, status: str, *, message: str = "", error_code: str | None = None) -> None:
        for step_state in context.step_states.values():
            step_state.status = status
            step_state.message = message
            step_state.error_code = error_code
        for task_state in context.task_states.values():
            task_state.status = status
            task_state.message = message
            for step_id in list(task_state.step_statuses):
                task_state.step_statuses[step_id] = status

    def _sync_task_statuses_from_steps(self, context: ReActExecutionContext) -> None:
        for task_state in context.task_states.values():
            for step_id in list(task_state.step_statuses):
                if step_id in context.step_states:
                    task_state.step_statuses[step_id] = context.step_states[step_id].status
            statuses = set(task_state.step_statuses.values())
            if "failed" in statuses:
                task_state.status = "failed"
            elif "blocked" in statuses:
                task_state.status = "blocked"
            elif "waiting_user" in statuses:
                task_state.status = "waiting_user"
            elif statuses and statuses.issubset({"completed", "skipped"}):
                task_state.status = "completed"
            elif "running" in statuses or "retrying" in statuses or "fallback_used" in statuses:
                task_state.status = "running"
            elif "cancelled" in statuses:
                task_state.status = "cancelled"
            elif statuses:
                task_state.status = "pending"

    def _mark_step_blocked(self, context: ReActExecutionContext, step_id: str | None, message: str, error_code: str) -> None:
        if not step_id:
            self._mark_all_states(context, "blocked", message=message, error_code=error_code)
            return
        state = context.step_states.get(step_id)
        if state is None:
            state = StepRuntimeState(step_id=step_id, status="blocked")
            context.step_states[step_id] = state
        state.status = "blocked"
        state.message = message
        state.error_code = error_code
        for task_state in context.task_states.values():
            if step_id in task_state.step_statuses:
                task_state.step_statuses[step_id] = "blocked"
                task_state.status = "blocked"
                task_state.message = message

    def _mark_step_waiting(self, context: ReActExecutionContext, step_id: str | None, message: str, error_code: str) -> None:
        if not step_id:
            return
        state = context.step_states.get(step_id)
        if state is None:
            state = StepRuntimeState(step_id=step_id, status="waiting_user")
            context.step_states[step_id] = state
        state.status = "waiting_user"
        state.message = message
        state.error_code = error_code
        for task_state in context.task_states.values():
            if step_id in task_state.step_statuses:
                task_state.step_statuses[step_id] = "waiting_user"
                task_state.status = "waiting_user"
                task_state.message = message

    def _mark_rejected_confirmation_states(self, context: ReActExecutionContext, step_id: str | None, message: str) -> None:
        if not step_id:
            return
        state = context.step_states.get(step_id)
        if state is None:
            state = StepRuntimeState(step_id=step_id, status="cancelled")
            context.step_states[step_id] = state
        state.status = "cancelled"
        state.message = message
        state.error_code = CONFIRMATION_REJECTED_CODE
        dependents = self._dependent_step_ids(context, step_id)
        for dependent_id in dependents:
            dep_state = context.step_states.get(dependent_id)
            if dep_state is None:
                dep_state = StepRuntimeState(step_id=dependent_id, status="skipped")
                context.step_states[dependent_id] = dep_state
            dep_state.status = "skipped"
            dep_state.message = f"Skipped because {step_id} was rejected."
            dep_state.error_code = CONFIRMATION_REJECTED_CODE
        self._sync_task_statuses_from_steps(context)

    def _dependent_step_ids(self, context: ReActExecutionContext, step_id: str) -> List[str]:
        dependents: List[str] = []
        target_output_key = getattr(context.step_lookup.get(step_id), "output_key", None)
        refs = {step_id}
        if target_output_key:
            refs.add(str(target_output_key))
        for candidate_id, step in context.step_lookup.items():
            depends_on = set(str(ref) for ref in list(getattr(step, "depends_on", []) or []))
            input_from = set(str(ref) for ref in list(getattr(step, "input_from", []) or []))
            if refs.intersection(depends_on) or refs.intersection(input_from):
                dependents.append(candidate_id)
        return dependents

    def _clear_pending_confirmation(self, context: ReActExecutionContext) -> None:
        context.pending_confirmation = None
        context.requires_user_input = False
        context.user_input_request = None

    def _confirmation_response_mismatch(
        self,
        context: ReActExecutionContext,
        pending: PendingConfirmation,
        *,
        confirmation_id: str | None,
        preview_hash: str | None,
    ) -> str | None:
        if pending.execution_id != context.execution_id:
            return "Confirmation execution_id does not match the active execution."
        if pending.plan_id != context.plan_id:
            return "Confirmation plan_id does not match the active plan."
        pending_session = pending.session_id
        current_session = self._task_value(context.task, "session_id")
        if pending_session and current_session and str(pending_session) != str(current_session):
            return "Confirmation session_id does not match the active session."
        if confirmation_id is not None and str(confirmation_id) != str(pending.confirmation_id or ""):
            return "Confirmation confirmation_id does not match the pending request."
        if preview_hash is not None and str(preview_hash) != str(pending.preview_hash or ""):
            return "Confirmation preview_hash does not match the pending preview."
        if pending.packet_id:
            action = pending.pending_action
            action_packet_id = getattr(action, "packet_id", None)
            if action_packet_id is None and isinstance(action, dict):
                action_packet_id = action.get("packet_id")
            if action_packet_id and str(action_packet_id) != str(pending.packet_id):
                return "Confirmation packet_id does not match the pending request."
        return None

    def _pending_confirmation_action_packet(
        self,
        context: ReActExecutionContext,
        pending: PendingConfirmation,
    ) -> ActionPacket | None:
        pending_action = pending.pending_action
        if isinstance(pending_action, ActionPacket):
            return pending_action
        if not isinstance(pending_action, dict):
            return None
        if str(pending_action.get("type", "")) != "plan_safety_confirmation":
            return None

        step_id = str(pending_action.get("step_id") or pending.step_id or "")
        step = context.step_lookup.get(step_id)
        tool_name = str(pending_action.get("tool_name") or getattr(step, "tool_name", "") or "")
        if step is None or not tool_name:
            return None

        return ActionPacket(
            execution_id=context.execution_id,
            plan_id=context.plan_id,
            task_id=str(getattr(step, "task_id", "") or pending.task_id or ""),
            step_id=step_id or None,
            action_type="call_tool",
            action_target=tool_name,
            action_args=dict(getattr(step, "args", {}) or {}),
            requires_confirmation=bool(getattr(step, "requires_confirmation", False)),
            confirmation_type="confirmation",
            user_visible_message=pending.confirmation_message,
        )

    def _clarification_request(self, context: ReActExecutionContext) -> str:
        questions: List[str] = []
        for step in list(getattr(context.plan, "steps", []) or []):
            args = getattr(step, "args", {}) or {}
            for question in list(args.get("questions", []) or []):
                if str(question).strip():
                    questions.append(str(question))
        if questions:
            return "\n".join(questions)
        summary = getattr(context.plan, "user_facing_summary", None)
        return str(summary).strip() if summary else ""

    def _confirmation_message(self, context: ReActExecutionContext) -> str:
        for step in list(getattr(context.plan, "steps", []) or []):
            reason = getattr(step, "confirmation_reason", None)
            if reason:
                return f"Confirmation required before execution: {reason}"
            args = getattr(step, "args", {}) or {}
            if args.get("reason"):
                return f"Confirmation required before execution: {args['reason']}"
        reason = getattr(context.task, "confirmation_reason", None)
        if reason:
            return f"Confirmation required before execution: {reason}"
        return "Confirmation required before execution."

    def _first_task_id(self, context: ReActExecutionContext) -> str | None:
        for task_id in context.task_states:
            return task_id
        return None

    def _first_step_id(self, context: ReActExecutionContext) -> str | None:
        for step_id in context.step_states:
            return step_id
        return None

    def _has_value(self, value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, (list, dict, tuple, set)):
            return bool(value)
        return True


@dataclass
class _SyntheticTaskUnit:
    step_ids: List[str]
    id: str = "task_1"
