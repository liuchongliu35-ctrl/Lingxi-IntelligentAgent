from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List

from src.agent.react_executor_observation import REDACTED_VALUE, sanitize_sensitive
from src.agent.react_executor_protocol import EVENT_TYPES, ExecutionEvent


INTERNAL_PAYLOAD_MARKERS = {
    "action_args",
    "full_prompt",
    "input_args",
    "raw_prompt",
    "prompt",
    "raw_input_args",
    "raw_observation",
    "raw_output",
    "raw_reasoning",
    "raw_result",
    "raw_tool_result",
    "reasoning",
    "thought_summary",
    "chain_of_thought",
    "stack_trace",
    "traceback",
    "exception",
    "env",
}

DEFAULT_MAX_MESSAGE_CHARS = 1200
DEFAULT_MAX_PAYLOAD_TEXT_CHARS = 1000


@dataclass
class EventStream:
    execution_id: str
    plan_id: str
    events: List[ExecutionEvent] = field(default_factory=list)
    enabled: bool = True
    max_message_chars: int = DEFAULT_MAX_MESSAGE_CHARS
    max_payload_text_chars: int = DEFAULT_MAX_PAYLOAD_TEXT_CHARS
    subscribers: List[Dict[str, Any]] = field(default_factory=list)

    def subscribe(self, callback: Callable[[ExecutionEvent], None], *, visible_only: bool = False) -> Callable[[], None]:
        if not callable(callback):
            raise TypeError("event subscriber callback must be callable")
        subscriber = {"callback": callback, "visible_only": bool(visible_only)}
        self.subscribers.append(subscriber)

        def unsubscribe() -> None:
            if subscriber in self.subscribers:
                self.subscribers.remove(subscriber)

        return unsubscribe

    def emit_event(
        self,
        type: str,
        message: str = "",
        payload: Dict[str, Any] | None = None,
        *,
        visible_to_user: bool = True,
        task_id: str | None = None,
        step_id: str | None = None,
    ) -> ExecutionEvent:
        if not self.enabled:
            event = ExecutionEvent(
                execution_id=self.execution_id,
                plan_id=self.plan_id,
                task_id=task_id,
                step_id=step_id,
                type=type,
                message="",
                visible_to_user=False,
                payload={"event_stream_disabled": True},
            )
            self.events.append(event)
            self._notify_subscribers(event)
            return event

        safe_payload = sanitize_event_payload(payload or {}, visible_to_user=visible_to_user, max_text_chars=self.max_payload_text_chars)
        event = ExecutionEvent(
            execution_id=self.execution_id,
            plan_id=self.plan_id,
            task_id=task_id,
            step_id=step_id,
            type=type,
            message=_truncate_text(str(message or ""), self.max_message_chars),
            visible_to_user=visible_to_user,
            payload=safe_payload,
        )
        self.events.append(event)
        self._notify_subscribers(event)
        return event

    def _notify_subscribers(self, event: ExecutionEvent) -> None:
        for subscriber in list(self.subscribers):
            if subscriber.get("visible_only") and not event.visible_to_user:
                continue
            subscriber["callback"](event)

    def visible_events(self) -> List[ExecutionEvent]:
        return [event for event in self.events if event.visible_to_user]

    def internal_events(self) -> List[ExecutionEvent]:
        return [event for event in self.events if not event.visible_to_user]

    def by_type(self, type: str) -> List[ExecutionEvent]:
        if type not in EVENT_TYPES:
            raise ValueError(f"Unsupported event type: {type}")
        return [event for event in self.events if event.type == type]

    def for_step(self, step_id: str) -> List[ExecutionEvent]:
        return [event for event in self.events if event.step_id == step_id]

    def count_by_type(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for event in self.events:
            counts[event.type] = counts.get(event.type, 0) + 1
        return counts

    def validate_step_timeline(self, step_ids: List[str]) -> List[str]:
        issues: List[str] = []
        for step_id in step_ids:
            step_events = self.for_step(step_id)
            if not any(event.type == "step_started" for event in step_events):
                issues.append(f"{step_id}: missing step_started")
            if not any(event.type in {"step_completed", "step_failed"} for event in step_events):
                issues.append(f"{step_id}: missing step_completed or step_failed")
        return issues

    def validate_timeline_integrity(self) -> List[str]:
        issues: List[str] = []
        visible = self.visible_events()
        final_indexes = [index for index, event in enumerate(visible) if event.type == "final_answer"]
        for final_index in final_indexes:
            for event in visible[final_index + 1 :]:
                if event.type in {
                    "thought_visible",
                    "action_selected",
                    "tool_started",
                    "tool_finished",
                    "tool_failed",
                    "command_started",
                    "command_finished",
                    "model_step_started",
                    "model_step_finished",
                    "message_delta",
                    "observation_created",
                    "retry_scheduled",
                    "retry_finished",
                    "retry_exhausted",
                    "fallback_started",
                    "fallback_finished",
                    "step_started",
                    "step_completed",
                    "step_failed",
                    "request_replan",
                }:
                    issues.append(f"final_answer appears before later {event.type}")
                    break

        grouped_pairs = {
            "command_started": "command_finished",
            "model_step_started": "model_step_finished",
            "tool_started": {"tool_finished", "tool_failed"},
        }
        for index, event in enumerate(visible):
            if event.type == "step_started":
                expected_set = {"step_completed", "step_failed"}
                step_id = event.step_id or event.payload.get("step_id")
                has_finish = any(
                    later.type in expected_set and (later.step_id == step_id or later.payload.get("step_id") == step_id)
                    for later in visible[index + 1 :]
                )
                if not has_finish:
                    issues.append(f"{event.type} missing matching finish for {step_id or event.event_id}")
                continue

            expected = grouped_pairs.get(event.type)
            if expected is None:
                continue
            expected_set = expected if isinstance(expected, set) else {expected}
            correlation_id = event_correlation_id(event)
            has_finish = any(
                later.type in expected_set and event_correlation_id(later) == correlation_id
                for later in visible[index + 1 :]
            )
            if not has_finish:
                issues.append(f"{event.type} missing matching finish for {correlation_id}")
        return issues

    def to_user_timeline(self, *, group_related: bool = True) -> List[Dict[str, Any]]:
        items = [timeline_item(event, sequence=index) for index, event in enumerate(self.visible_events())]
        if not group_related:
            return items
        return group_timeline_items(items)

    def to_model_context(self, max_events: int = 20) -> List[Dict[str, Any]]:
        start_index = max(len(self.events) - max(max_events, 1), 0)
        selected = self.events[start_index:]
        return [
            {
                "sequence": start_index + index,
                "type": event.type,
                "task_id": event.task_id,
                "step_id": event.step_id,
                "message": event.message,
                "visible_to_user": event.visible_to_user,
                "payload_summary": payload_summary(event.payload),
            }
            for index, event in enumerate(selected)
        ]

    def to_dict(self, *, include_internal: bool = True) -> Dict[str, Any]:
        events = self.events if include_internal else self.visible_events()
        return {
            "execution_id": self.execution_id,
            "plan_id": self.plan_id,
            "event_count": len(events),
            "events": [event.to_dict() for event in events],
            "counts": self.count_by_type(),
        }


def timeline_item(event: ExecutionEvent, sequence: int | None = None) -> Dict[str, Any]:
    mapping = {
        "progress_message": ("assistant_message", "Progress"),
        "message_delta": ("assistant_message", "Message"),
        "thought_visible": ("assistant_message", "Plan"),
        "command_started": ("ran_command", "Ran commands"),
        "command_finished": ("ran_command", "Ran commands"),
        "tool_started": ("tool_record", "Tool started"),
        "tool_finished": ("tool_record", "Tool finished"),
        "tool_failed": ("tool_record", "Tool failed"),
        "file_edited": ("file_edit", "Edited file"),
        "system_notice": ("system_notice", "System notice"),
        "final_answer": ("final_answer", "Final answer"),
        "model_step_started": ("model_step", "Model step started"),
        "model_step_finished": ("model_step", "Model step finished"),
        "step_started": ("step_record", "Step started"),
        "step_completed": ("step_record", "Step completed"),
        "step_failed": ("step_record", "Step failed"),
        "confirmation_requested": ("confirmation", "Confirmation requested"),
        "observation_created": ("observation", "Observation created"),
        "retry_scheduled": ("retry_record", "Retry scheduled"),
        "retry_finished": ("retry_record", "Retry finished"),
        "retry_exhausted": ("retry_record", "Retry exhausted"),
        "fallback_started": ("fallback_record", "Fallback started"),
        "fallback_finished": ("fallback_record", "Fallback finished"),
        "request_replan": ("request_replan", "Request replan"),
        "action_selected": ("action", "Action selected"),
    }
    render_as, title = mapping.get(event.type, ("event", event.type))
    item = {
        "event_id": event.event_id,
        "sequence": sequence,
        "type": event.type,
        "render_as": render_as,
        "title": title,
        "timestamp": event.timestamp,
        "task_id": event.task_id,
        "step_id": event.step_id,
        "correlation_id": event_correlation_id(event),
        "status": event_status(event),
        "message": event.message,
        "payload": sanitize_event_payload(event.payload, visible_to_user=True),
    }
    return item


def group_timeline_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: List[Dict[str, Any]] = []
    pending: Dict[tuple[str, str], Dict[str, Any]] = {}
    for item in items:
        if item["type"] in {"command_started", "tool_started", "model_step_started", "step_started"}:
            key = (item["render_as"], item.get("correlation_id") or item["event_id"])
            pending[key] = item
            grouped.append(item)
            continue
        if item["type"] in {"command_finished", "tool_finished", "tool_failed", "model_step_finished", "step_completed", "step_failed"}:
            key = (item["render_as"], item.get("correlation_id") or item["event_id"])
            started = pending.get(key)
            if started is not None:
                started["status"] = item["status"]
                started["message"] = item["message"] or started["message"]
                started["finished_event_id"] = item["event_id"]
                started["finished_at"] = item["timestamp"]
                started["payload"] = merge_timeline_payload(started["payload"], item["payload"])
                continue
        grouped.append(item)
    return grouped


def merge_timeline_payload(start_payload: Dict[str, Any], finish_payload: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(start_payload)
    for key, value in finish_payload.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            nested = dict(merged[key])
            nested.update(value)
            merged[key] = nested
        else:
            merged[key] = value
    return merged


def event_correlation_id(event: ExecutionEvent) -> str:
    payload = event.payload or {}
    for key in ("correlation_id", "model_call_id", "command_id", "tool_call_id", "action_id", "packet_id", "observation_id"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    if event.step_id:
        return event.step_id
    return event.event_id


def event_status(event: ExecutionEvent) -> str:
    if event.type.endswith("_started"):
        return "started"
    if event.type.endswith("_finished") or event.type.endswith("_completed"):
        return "completed"
    if event.type.endswith("_failed"):
        return "failed"
    if event.type == "final_answer":
        return "completed"
    if event.type == "confirmation_requested":
        return "waiting_user"
    if event.type == "request_replan":
        return "request_replan"
    return "recorded"


def payload_summary(payload: Dict[str, Any]) -> Dict[str, Any]:
    summary: Dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, str):
            summary[key] = _truncate_text(value, 200)
        elif isinstance(value, (int, float, bool)) or value is None:
            summary[key] = value
        elif isinstance(value, list):
            summary[key] = {"type": "list", "items": len(value)}
        elif isinstance(value, dict):
            summary[key] = {"type": "object", "keys": sorted(str(item) for item in value.keys())[:10]}
        else:
            summary[key] = str(value)
    return sanitize_sensitive(summary)


def sanitize_event_payload(payload: Dict[str, Any], *, visible_to_user: bool, max_text_chars: int = DEFAULT_MAX_PAYLOAD_TEXT_CHARS) -> Dict[str, Any]:
    sanitized = sanitize_sensitive(payload)
    if visible_to_user:
        sanitized = _drop_internal_payload(sanitized)
    return _truncate_payload_text(sanitized, max_text_chars)


def _drop_internal_payload(payload: Any) -> Any:
    if isinstance(payload, dict):
        result: Dict[str, Any] = {}
        for key, value in payload.items():
            key_text = str(key)
            if _is_internal_payload_key(key_text):
                result[key_text] = REDACTED_VALUE
            else:
                result[key_text] = _drop_internal_payload(value)
        return result
    if isinstance(payload, list):
        return [_drop_internal_payload(item) for item in payload]
    return payload


def _truncate_payload_text(payload: Any, max_text_chars: int) -> Any:
    if isinstance(payload, dict):
        return {key: _truncate_payload_text(value, max_text_chars) for key, value in payload.items()}
    if isinstance(payload, list):
        return [_truncate_payload_text(item, max_text_chars) for item in payload]
    if isinstance(payload, str):
        return _truncate_text(payload, max(max_text_chars, 1))
    return payload


def _is_internal_payload_key(key: str) -> bool:
    lowered = key.lower()
    if lowered in INTERNAL_PAYLOAD_MARKERS:
        return True
    return lowered.endswith("_full") or lowered.endswith("_trace")


def _truncate_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"... [truncated {len(text) - max_chars} chars]"
