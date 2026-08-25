from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, List

from src.agent.react_executor_events import EventStream, sanitize_event_payload, timeline_item
from src.agent.react_executor_observation import sanitize_sensitive
from src.agent.react_executor_protocol import ExecutionEvent, ExecutionResult


@dataclass
class FeedbackItem:
    source: str
    type: str
    message: str = ""
    status: str = "recorded"
    event_id: str | None = None
    task_id: str | None = None
    step_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "type": self.type,
            "message": self.message,
            "status": self.status,
            "event_id": self.event_id,
            "task_id": self.task_id,
            "step_id": self.step_id,
            "payload": sanitize_sensitive(self.payload),
        }


@dataclass
class OutputFeedback:
    execution_id: str
    plan_id: str
    status: str
    success: bool
    final_output: str = ""
    summary: str = ""
    requires_user_input: bool = False
    user_input_request: str | None = None
    request_replan: bool = False
    replan_reason: str | None = None
    pending_confirmation: dict[str, Any] | None = None
    timeline: List[dict[str, Any]] = field(default_factory=list)
    items: List[FeedbackItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "plan_id": self.plan_id,
            "status": self.status,
            "success": self.success,
            "final_output": self.final_output,
            "summary": self.summary,
            "requires_user_input": self.requires_user_input,
            "user_input_request": self.user_input_request,
            "request_replan": self.request_replan,
            "replan_reason": self.replan_reason,
            "pending_confirmation": sanitize_sensitive(self.pending_confirmation),
            "timeline": sanitize_sensitive(self.timeline),
            "items": [item.to_dict() for item in self.items],
        }


class OutputFeedbackProcessor:
    """Read-only adapter from executor outputs to user feedback payloads."""

    def build(
        self,
        result: ExecutionResult,
        *,
        include_internal: bool = False,
        group_related: bool = True,
    ) -> OutputFeedback:
        events = self._result_events(result)
        visible_events = events if include_internal else [event for event in events if event.visible_to_user]
        timeline = EventStream(
            execution_id=result.execution_id,
            plan_id=result.plan_id,
            events=list(visible_events),
        ).to_user_timeline(group_related=group_related)
        return OutputFeedback(
            execution_id=result.execution_id,
            plan_id=result.plan_id,
            status=result.status,
            success=result.success,
            final_output=result.output,
            summary=result.summary,
            requires_user_input=result.requires_user_input,
            user_input_request=result.user_input_request,
            request_replan=result.request_replan,
            replan_reason=result.replan_reason,
            pending_confirmation=self._pending_confirmation_payload(result),
            timeline=timeline,
            items=[self.from_event(event) for event in visible_events],
        )

    def from_event(self, event: ExecutionEvent) -> FeedbackItem:
        item = timeline_item(event)
        return FeedbackItem(
            source="execution_event",
            type=event.type,
            message=item.get("message") or "",
            status=item.get("status") or "recorded",
            event_id=event.event_id,
            task_id=event.task_id,
            step_id=event.step_id,
            payload=sanitize_event_payload(item.get("payload") or {}, visible_to_user=True),
        )

    def from_events(self, events: Iterable[ExecutionEvent]) -> list[FeedbackItem]:
        return [self.from_event(event) for event in events if event.visible_to_user]

    def final_item(self, result: ExecutionResult) -> FeedbackItem:
        payload = {
            "status": result.status,
            "success": result.success,
            "requires_user_input": result.requires_user_input,
            "request_replan": result.request_replan,
            "error_code": result.error_code,
        }
        return FeedbackItem(
            source="execution_result",
            type="execution_result",
            message=result.output,
            status=result.status,
            payload=sanitize_event_payload(payload, visible_to_user=True),
        )

    def _result_events(self, result: ExecutionResult) -> list[ExecutionEvent]:
        events: list[ExecutionEvent] = []
        for event in result.events:
            if isinstance(event, ExecutionEvent):
                events.append(event)
        return events

    def _pending_confirmation_payload(self, result: ExecutionResult) -> dict[str, Any] | None:
        pending = result.pending_confirmation
        if pending is None:
            return None
        if hasattr(pending, "to_dict") and callable(pending.to_dict):
            return sanitize_sensitive(pending.to_dict())
        if isinstance(pending, dict):
            return sanitize_sensitive(dict(pending))
        return {"summary": str(pending)}
