from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any, Dict, List

from src.agent.react_executor_observation import sanitize_sensitive
from src.agent.react_executor_protocol import ActionPacket, ExecutionResult, ObservationPacket, utc_now_iso


DEFAULT_MAX_SUMMARY_CHARS = 500


@dataclass
class LogWriteResult:
    success: bool
    error: str | None = None


@dataclass
class ReActExecutorLogger:
    log_path: Path
    enabled: bool = True
    log_full_prompt: bool = False
    max_summary_chars: int = DEFAULT_MAX_SUMMARY_CHARS
    write_error_count: int = 0
    last_write_error: str | None = None
    records_written: int = 0
    in_memory_records: List[Dict[str, Any]] = field(default_factory=list)
    keep_in_memory: bool = False

    def log_execution_started(self, context: Any) -> LogWriteResult:
        return self.write_record(
            context,
            record_type="execution_started",
            success=None,
            code=None,
            metadata={
                "mode": getattr(context.plan, "mode", None),
                "step_count": len(getattr(context.plan, "steps", []) or []),
                "task_unit_count": len(getattr(context.plan, "task_units", []) or []),
            },
        )

    def log_execution_finished(self, context: Any, result: ExecutionResult) -> LogWriteResult:
        return self.write_record(
            context,
            record_type="execution_finished",
            success=result.success,
            code=result.error_code,
            error=None if result.success else result.output or result.summary,
            request_replan=result.request_replan,
            metadata={
                "status": result.status,
                "failed_step_id": result.failed_step_id,
                "requires_user_input": result.requires_user_input,
                "observation_count": len(result.observations),
            },
        )

    def log_execution_exception(self, context: Any, exc: Exception) -> LogWriteResult:
        return self.write_record(
            context,
            record_type="execution_exception",
            success=False,
            code="execution_exception",
            error=str(exc),
        )

    def log_action_packet(
        self,
        context: Any,
        packet: ActionPacket,
        *,
        attempt: int,
        schema_valid: bool,
        schema_errors: List[str] | None = None,
        repair_attempts: int = 0,
    ) -> LogWriteResult:
        return self.write_record(
            context,
            record_type="action_packet",
            task_id=packet.task_id,
            step_id=packet.step_id,
            packet_id=packet.packet_id,
            action_type=packet.action_type,
            action_target=packet.action_target,
            attempt=attempt,
            schema_valid=schema_valid,
            repair_attempts=repair_attempts,
            success=None,
            code=None,
            metadata={
                "schema_errors": list(schema_errors or []),
                "confidence": packet.confidence,
                "requires_confirmation": packet.requires_confirmation,
                "action_args_summary": summarize_payload(packet.action_args, max_chars=self.max_summary_chars),
                "safety_notes": list(packet.safety_notes or []),
            },
        )

    def log_safety_decision(self, context: Any, packet: ActionPacket, decision: Any, *, attempt: int) -> LogWriteResult:
        return self.write_record(
            context,
            record_type="safety_decision",
            task_id=packet.task_id,
            step_id=packet.step_id,
            packet_id=packet.packet_id,
            action_type=packet.action_type,
            action_target=packet.action_target,
            attempt=attempt,
            success=not bool(getattr(decision, "blocked", False)),
            code=getattr(decision, "code", None),
            error=getattr(decision, "reason", None) if bool(getattr(decision, "blocked", False)) else None,
            metadata={"safety": _to_dict(decision)},
        )

    def log_model_prompt(self, context: Any, packet: ActionPacket, prompt: str, input_payload: Dict[str, Any]) -> LogWriteResult:
        prompt_text = str(prompt or "")
        metadata: Dict[str, Any] = {
            "prompt_length": len(prompt_text),
            "prompt_summary": summarize_text(prompt_text, max_chars=self.max_summary_chars),
            "input_summary": summarize_payload(input_payload, max_chars=self.max_summary_chars),
        }
        if self.log_full_prompt:
            metadata["full_prompt"] = prompt_text
        return self.write_record(
            context,
            record_type="model_prompt",
            task_id=packet.task_id,
            step_id=packet.step_id,
            packet_id=packet.packet_id,
            action_type=packet.action_type,
            action_target=packet.action_target,
            attempt=None,
            success=None,
            code=None,
            metadata=metadata,
        )

    def log_observation(self, context: Any, observation: ObservationPacket) -> LogWriteResult:
        return self.write_record(
            context,
            record_type="observation",
            task_id=observation.task_id,
            step_id=observation.step_id,
            packet_id=observation.packet_id,
            action_type=observation.action_type,
            action_target=observation.action_target,
            tool_name=observation.tool_name,
            attempt=observation.attempt,
            success=observation.success,
            error=observation.error,
            code=observation.code,
            duration_ms=observation.duration_ms,
            checker_result=observation.checker_result,
            fallback_used=observation.fallback_used,
            observation_id=observation.observation_id,
            metadata={
                "message": summarize_text(observation.message, max_chars=self.max_summary_chars),
                "data_summary": summarize_payload(observation.data, max_chars=self.max_summary_chars),
                "model_observation_summary": summarize_payload(observation.model_consumable_observation, max_chars=self.max_summary_chars),
            },
        )

    def write_record(
        self,
        context: Any,
        *,
        record_type: str,
        turn_id: str | None = None,
        task_id: str | None = None,
        step_id: str | None = None,
        packet_id: str | None = None,
        action_type: str | None = None,
        action_target: str | None = None,
        tool_name: str | None = None,
        attempt: int | None = None,
        schema_valid: bool | None = None,
        repair_attempts: int = 0,
        success: bool | None = None,
        error: str | None = None,
        code: str | None = None,
        duration_ms: int = 0,
        checker_result: Dict[str, Any] | None = None,
        fallback_used: bool = False,
        request_replan: bool = False,
        observation_id: str | None = None,
        metadata: Dict[str, Any] | None = None,
    ) -> LogWriteResult:
        if not self.enabled:
            return LogWriteResult(success=True)
        resolved_turn_id = turn_id
        if resolved_turn_id is None:
            loop_state = getattr(context, "loop_state", None)
            resolved_turn_id = getattr(loop_state, "current_turn_id", None)
        record = {
            "timestamp": utc_now_iso(),
            "record_type": record_type,
            "execution_id": getattr(context, "execution_id", ""),
            "source_trace_id": getattr(context, "source_trace_id", None),
            "plan_id": getattr(context, "plan_id", ""),
            "turn_id": resolved_turn_id,
            "task_id": task_id,
            "step_id": step_id,
            "packet_id": packet_id,
            "action_type": action_type,
            "action_target": action_target,
            "tool_name": tool_name,
            "attempt": attempt,
            "schema_valid": schema_valid,
            "repair_attempts": repair_attempts,
            "success": success,
            "error": summarize_text(error or "", max_chars=self.max_summary_chars) if error else None,
            "code": code,
            "duration_ms": max(int(duration_ms or 0), 0),
            "checker_result": sanitize_sensitive(checker_result or {}),
            "fallback_used": fallback_used,
            "request_replan": request_replan,
            "event_count": len(getattr(getattr(context, "event_stream", None), "events", []) or []),
            "observation_id": observation_id,
            "metadata": sanitize_sensitive(metadata or {}),
        }
        return self._write_json_line(record)

    def _write_json_line(self, record: Dict[str, Any]) -> LogWriteResult:
        safe_record = _json_safe(record)
        if self.keep_in_memory:
            self.in_memory_records.append(safe_record)
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(safe_record, ensure_ascii=False, sort_keys=True) + "\n")
            self.records_written += 1
            return LogWriteResult(success=True)
        except Exception as exc:
            self.write_error_count += 1
            self.last_write_error = str(exc)
            return LogWriteResult(success=False, error=str(exc))


def summarize_payload(value: Any, *, max_chars: int = DEFAULT_MAX_SUMMARY_CHARS) -> Any:
    sanitized = sanitize_sensitive(value)
    if isinstance(sanitized, str):
        return summarize_text(sanitized, max_chars=max_chars)
    if isinstance(sanitized, dict):
        result: Dict[str, Any] = {}
        for key, item in sanitized.items():
            if isinstance(item, str):
                result[str(key)] = summarize_text(item, max_chars=max_chars)
            elif isinstance(item, (int, float, bool)) or item is None:
                result[str(key)] = item
            elif isinstance(item, list):
                result[str(key)] = {"type": "list", "items": len(item)}
            elif isinstance(item, dict):
                result[str(key)] = {"type": "object", "keys": sorted(str(inner_key) for inner_key in item.keys())[:10]}
            else:
                result[str(key)] = summarize_text(str(item), max_chars=max_chars)
        return result
    if isinstance(sanitized, list):
        return {"type": "list", "items": len(sanitized)}
    if isinstance(sanitized, (int, float, bool)) or sanitized is None:
        return sanitized
    return summarize_text(str(sanitized), max_chars=max_chars)


def summarize_text(text: str, *, max_chars: int = DEFAULT_MAX_SUMMARY_CHARS) -> str:
    value = str(text or "")
    if len(value) <= max_chars:
        return value
    return value[:max_chars] + f"... [truncated {len(value) - max_chars} chars]"


def _to_dict(value: Any) -> Any:
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
