from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from src.agent.react_executor_observation import observation_to_text
from src.agent.react_executor_protocol import ObservationPacket


MAX_RESULT_LINE_CHARS = 300
MAX_LIST_ITEMS = 8


@dataclass
class ResultSummary:
    output: str
    summary: str
    request_replan: bool = False
    replan_reason: str | None = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class ExecutionResultBuilder:
    """Build user-facing ExecutionResult text from runtime state."""

    def build(self, context: Any, *, status: str, success: bool) -> ResultSummary:
        observations = list(getattr(context.observation_store, "observations", []) or [])
        step_statuses = {step_id: state.status for step_id, state in getattr(context, "step_states", {}).items()}
        task_statuses = {task_id: state.status for task_id, state in getattr(context, "task_states", {}).items()}
        request_replan, replan_reason = self._replan_state(context, observations)
        counts = self._status_counts(step_statuses)
        fallback_observations = [observation for observation in observations if getattr(observation, "fallback_used", False)]
        latest_failure = self._latest_failure(observations)

        output = self._build_output(
            context,
            status=status,
            success=success,
            observations=observations,
            step_statuses=step_statuses,
            task_statuses=task_statuses,
            request_replan=request_replan,
            replan_reason=replan_reason,
            fallback_observations=fallback_observations,
            latest_failure=latest_failure,
        )
        summary = self._build_summary(
            context,
            status=status,
            success=success,
            counts=counts,
            observations=observations,
            request_replan=request_replan,
            replan_reason=replan_reason,
            fallback_count=len(fallback_observations),
        )
        return ResultSummary(
            output=output,
            summary=summary,
            request_replan=request_replan,
            replan_reason=replan_reason,
            metadata={
                "status_counts": counts,
                "observation_count": len(observations),
                "fallback_count": len(fallback_observations),
                "task_statuses": task_statuses,
                "step_statuses": step_statuses,
            },
        )

    def _build_output(
        self,
        context: Any,
        *,
        status: str,
        success: bool,
        observations: List[ObservationPacket],
        step_statuses: Dict[str, str],
        task_statuses: Dict[str, str],
        request_replan: bool,
        replan_reason: str | None,
        fallback_observations: List[ObservationPacket],
        latest_failure: ObservationPacket | None,
    ) -> str:
        existing = _clean_text(getattr(context, "output", "") or getattr(context, "summary", ""))
        lines = [
            f"Status: {status}.",
            f"Goal: {_clean_text(getattr(context.plan, 'goal', '') or 'not specified')}.",
        ]
        if existing:
            lines.append(f"Current result: {_truncate(existing)}")

        progress = self._progress_line(step_statuses, task_statuses, len(observations))
        if progress:
            lines.append(f"Progress: {progress}")

        successes = self._successful_observation_lines(observations)
        if successes:
            lines.append("Succeeded: " + "; ".join(successes[:MAX_LIST_ITEMS]) + ".")
        elif success:
            lines.append("Succeeded: execution completed.")

        failed_lines = self._failed_observation_lines(observations)
        if failed_lines:
            lines.append("Failed: " + "; ".join(failed_lines[:MAX_LIST_ITEMS]) + ".")
        elif latest_failure is not None:
            lines.append(f"Failed: {_observation_label(latest_failure)}: {_truncate(latest_failure.error or latest_failure.message or latest_failure.code or 'failed')}.")
        else:
            failed_state_lines = self._failed_state_lines(context)
            if failed_state_lines:
                lines.append("Failed: " + "; ".join(failed_state_lines[:MAX_LIST_ITEMS]) + ".")

        skipped = [step_id for step_id, item_status in step_statuses.items() if item_status == "skipped"]
        if skipped:
            lines.append("Skipped: " + ", ".join(skipped[:MAX_LIST_ITEMS]) + ".")

        blocked = [step_id for step_id, item_status in step_statuses.items() if item_status == "blocked"]
        if blocked:
            lines.append("Blocked: " + ", ".join(blocked[:MAX_LIST_ITEMS]) + ".")

        waiting = [step_id for step_id, item_status in step_statuses.items() if item_status == "waiting_user"]
        if waiting or getattr(context, "requires_user_input", False):
            question = _clean_text(getattr(context, "user_input_request", "") or "")
            lines.append(f"Waiting for user: {_truncate(question or 'confirmation or additional input is required')}.")

        if fallback_observations:
            fallback_items = [
                f"{_observation_label(observation)} via {observation.fallback_type or 'fallback'}"
                for observation in fallback_observations[:MAX_LIST_ITEMS]
            ]
            lines.append("Fallback used: " + "; ".join(fallback_items) + ".")

        if request_replan:
            lines.append(f"Replan requested: {_truncate(replan_reason or 'replan requested')}.")

        lines.append("Next: " + self._next_step(status, success, context, request_replan=request_replan, latest_failure=latest_failure))
        return "\n".join(line for line in lines if line.strip())

    def _build_summary(
        self,
        context: Any,
        *,
        status: str,
        success: bool,
        counts: Dict[str, int],
        observations: List[ObservationPacket],
        request_replan: bool,
        replan_reason: str | None,
        fallback_count: int,
    ) -> str:
        parts = [f"status={status}", f"success={success}"]
        if counts:
            parts.append(
                "steps="
                + ",".join(f"{key}:{value}" for key, value in sorted(counts.items()) if value)
            )
        parts.append(f"observations={len(observations)}")
        if fallback_count:
            parts.append(f"fallback={fallback_count}")
        if request_replan:
            parts.append(f"request_replan={_truncate(replan_reason or 'true', 120)}")
        if getattr(context, "requires_user_input", False):
            parts.append("waiting_user=true")
        if getattr(context, "error_code", None):
            parts.append(f"error_code={context.error_code}")
        return "; ".join(parts)

    def _progress_line(self, step_statuses: Dict[str, str], task_statuses: Dict[str, str], observation_count: int) -> str:
        if not step_statuses and not task_statuses:
            return f"{observation_count} observations recorded"
        counts = self._status_counts(step_statuses)
        fragments = []
        for status in ("completed", "failed", "blocked", "waiting_user", "skipped", "pending", "running"):
            value = counts.get(status, 0)
            if value:
                fragments.append(f"{value} {status}")
        if observation_count:
            fragments.append(f"{observation_count} observations")
        return ", ".join(fragments)

    def _successful_observation_lines(self, observations: List[ObservationPacket]) -> List[str]:
        lines: List[str] = []
        for observation in observations:
            if not observation.success:
                continue
            label = _observation_label(observation)
            text = self._observation_result_text(observation)
            lines.append(f"{label}: {_truncate(text)}" if text else label)
        return lines

    def _failed_observation_lines(self, observations: List[ObservationPacket]) -> List[str]:
        lines: List[str] = []
        for observation in observations:
            if observation.success:
                continue
            reason = observation.error or observation.message or observation.code or "failed"
            lines.append(f"{_observation_label(observation)}: {_truncate(reason)}")
        return lines

    def _failed_state_lines(self, context: Any) -> List[str]:
        lines: List[str] = []
        for step_id, state in getattr(context, "step_states", {}).items():
            if state.status not in {"failed", "blocked", "cancelled"}:
                continue
            reason = state.message or state.error_code or state.status
            lines.append(f"{step_id}: {_truncate(reason)}")
        return lines

    def _latest_failure(self, observations: List[ObservationPacket]) -> ObservationPacket | None:
        for observation in reversed(observations):
            if not observation.success:
                return observation
        return None

    def _observation_result_text(self, observation: ObservationPacket) -> str:
        if observation.action_type == "finish":
            data = observation.data if isinstance(observation.data, dict) else {}
            final_answer = data.get("final_answer")
            if isinstance(final_answer, str) and final_answer.strip():
                return final_answer
        try:
            return observation_to_text(observation)
        except Exception:
            return observation.message or ""

    def _status_counts(self, statuses: Dict[str, str]) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for status in statuses.values():
            counts[status] = counts.get(status, 0) + 1
        return counts

    def _replan_state(self, context: Any, observations: List[ObservationPacket]) -> tuple[bool, str | None]:
        if bool(getattr(context, "request_replan", False)):
            return True, getattr(context, "replan_reason", None)
        for observation in reversed(observations):
            if observation.code == "request_replan":
                data = observation.data if isinstance(observation.data, dict) else {}
                return True, str(data.get("reason") or observation.message or observation.error or "Replan requested.")
            checker_result = observation.checker_result or {}
            if checker_result.get("execution_status") == "request_replan":
                return True, observation.message or observation.error or "Replan requested."
        return False, None

    def _next_step(self, status: str, success: bool, context: Any, *, request_replan: bool, latest_failure: ObservationPacket | None) -> str:
        if success or status == "completed":
            return "no further action is required."
        if request_replan:
            return "ask Planner for a revised TaskPlan before continuing."
        if getattr(context, "requires_user_input", False):
            return "wait for the user response, then resume the pending action."
        if status == "blocked":
            return "resolve the blocking policy or safety issue before retrying."
        if latest_failure is not None:
            return "inspect the failed observation, then retry, fallback, or request replan."
        return "continue once the next ReActExecutor step is implemented."


def _observation_label(observation: ObservationPacket) -> str:
    target = observation.tool_name or observation.action_target or observation.action_type
    if observation.step_id:
        return f"{observation.step_id}/{target}"
    return str(target)


def _clean_text(text: str) -> str:
    return " ".join(str(text or "").split())


def _truncate(text: str, max_chars: int = MAX_RESULT_LINE_CHARS) -> str:
    value = _clean_text(text)
    if len(value) <= max_chars:
        return value
    return value[:max_chars] + f"... [truncated {len(value) - max_chars} chars]"
