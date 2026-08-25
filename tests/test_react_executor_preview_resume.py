from __future__ import annotations

import json
import unittest

from src.agent.planner import PlanStep, TaskPlan, TaskUnit
from src.agent.react_executor import ReActExecutor
from src.agent.react_executor_protocol import ActionPacket
from src.tools.base import ToolResult
from src.tools.registry import ToolRegistry, ToolSpec


class PreviewAwareToolManager:
    def __init__(self):
        self.runtime = object()
        self.requests = []
        self.preview_hash = "preview-1"

    def execute(self, request):
        self.requests.append(request)
        if request.options.dry_run:
            return ToolResult.ok(
                data={
                    "preview": {
                        "tool_name": request.tool_name,
                        "affected_resources": ["notes.txt"],
                        "operation": "overwrite",
                    },
                    "affected_resources": ["notes.txt"],
                },
                message="Dry-run preview prepared.",
                code="dry_run_preview",
                metadata={
                    "output_control": {
                        "preview_hash": self.preview_hash,
                        "preview": {
                            "tool_name": request.tool_name,
                            "affected_resources": ["notes.txt"],
                            "operation": "overwrite",
                        },
                        "affected_resources": ["notes.txt"],
                    }
                },
            )
        if request.options.preview_hash != self.preview_hash:
            return ToolResult.fail(
                "The preview no longer matches the current tool call.",
                code="preview_conflict",
            )
        return ToolResult.ok(data={"written": True}, message="Write completed.")


class ReActExecutorPreviewResumeTest(unittest.TestCase):
    def test_confirmation_runs_dry_run_and_persists_verifiable_ticket(self):
        manager = PreviewAwareToolManager()
        executor, plan, step = _executor(manager)
        context = executor._create_context(
            plan,
            task={"session_id": "session-1"},
            user_input="write",
            history="",
        )

        observation = executor.dispatch_action(context, _packet(context), step=step)
        pending = context.pending_confirmation

        self.assertFalse(observation.success)
        self.assertEqual(observation.code, "confirmation_pending")
        self.assertIsNotNone(pending)
        self.assertEqual(len(manager.requests), 1)
        self.assertTrue(manager.requests[0].options.dry_run)
        self.assertEqual(pending.session_id, "session-1")
        self.assertEqual(pending.packet_id, "packet-1")
        self.assertTrue(pending.confirmation_id)
        self.assertTrue(pending.call_id)
        self.assertEqual(pending.preview_hash, "preview-1")
        self.assertEqual(pending.affected_resources, ["notes.txt"])
        confirmation_event = [event for event in context.event_stream.events if event.type == "confirmation_requested"][0]
        self.assertEqual(confirmation_event.payload["preview_hash"], "preview-1")
        self.assertEqual(confirmation_event.payload["affected_resources"], ["notes.txt"])
        pending_json = json.dumps(confirmation_event.payload["pending_confirmation"], ensure_ascii=False)
        self.assertNotIn("action_args", pending_json)
        self.assertNotIn("updated", pending_json)

    def test_approved_resume_reuses_ticket_and_executes_only_after_approval(self):
        manager = PreviewAwareToolManager()
        executor, plan, step = _executor(manager)
        context = executor._create_context(
            plan,
            task={"session_id": "session-1"},
            user_input="write",
            history="",
        )

        executor.dispatch_action(context, _packet(context), step=step)
        pending = context.pending_confirmation
        result = executor.resume_after_confirmation(
            context,
            approved=True,
            confirmation_id=pending.confirmation_id,
            preview_hash=pending.preview_hash,
        )

        self.assertTrue(result.success)
        self.assertEqual(len(manager.requests), 2)
        actual_request = manager.requests[1]
        self.assertFalse(actual_request.options.dry_run)
        self.assertTrue(actual_request.options.confirmed)
        self.assertEqual(actual_request.options.confirmation_id, pending.confirmation_id)
        self.assertEqual(actual_request.options.preview_hash, pending.preview_hash)
        self.assertEqual(actual_request.options.approval_source, "user")
        self.assertIsNotNone(actual_request.options.approved_at)

    def test_wrong_confirmation_ticket_is_rejected_without_real_execution(self):
        manager = PreviewAwareToolManager()
        executor, plan, step = _executor(manager)
        context = executor._create_context(
            plan,
            task={"session_id": "session-1"},
            user_input="write",
            history="",
        )

        executor.dispatch_action(context, _packet(context), step=step)
        result = executor.resume_after_confirmation(
            context,
            approved=True,
            confirmation_id="wrong-confirmation",
            preview_hash="preview-1",
        )

        self.assertFalse(result.success)
        self.assertEqual(len(manager.requests), 1)
        self.assertIsNotNone(context.pending_confirmation)

    def test_changed_preview_hash_returns_conflict_after_approval(self):
        manager = PreviewAwareToolManager()
        executor, plan, step = _executor(manager)
        context = executor._create_context(
            plan,
            task={"session_id": "session-1"},
            user_input="write",
            history="",
        )

        executor.dispatch_action(context, _packet(context), step=step)
        pending = context.pending_confirmation
        manager.preview_hash = "preview-2"

        result = executor.resume_after_confirmation(
            context,
            approved=True,
            confirmation_id=pending.confirmation_id,
            preview_hash=pending.preview_hash,
        )

        self.assertFalse(result.success)
        self.assertEqual(result.status, "failed")
        self.assertEqual(len(manager.requests), 2)
        self.assertFalse(result.requires_user_input)
        self.assertIsNone(result.pending_confirmation)
        self.assertEqual(result.step_statuses["step-1"], "failed")
        self.assertEqual(context.observation_store.observations[-1].code, "preview_conflict")


def _executor(manager: PreviewAwareToolManager):
    spec = ToolSpec(
        name="write_preview_tool",
        description="Test write tool.",
        parameters_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "additionalProperties": False,
        },
        required_params=["path", "content"],
        risk_level="high",
        requires_confirmation=True,
        workspace_scope="write_workspace",
        supports_dry_run=True,
    )
    registry = ToolRegistry([spec])
    step = PlanStep(
        id="step-1",
        task_id="task-1",
        description="Write file.",
        step_type="tool",
        tool_name="write_preview_tool",
        args={"path": "notes.txt", "content": "updated"},
    )
    plan = TaskPlan(
        goal="preview resume",
        mode="micro",
        steps=[step],
        task_units=[TaskUnit(id="task-1", title="Write", step_ids=["step-1"])],
        available_tools=["write_preview_tool"],
        required_tools=["write_preview_tool"],
        can_execute=True,
        plan_validation_status="valid",
    )
    return ReActExecutor(tool_manager=manager, tool_registry=registry), plan, step


def _packet(context) -> ActionPacket:
    return ActionPacket(
        packet_id="packet-1",
        execution_id=context.execution_id,
        plan_id=context.plan_id,
        task_id="task-1",
        step_id="step-1",
        action_type="call_tool",
        action_target="write_preview_tool",
        action_args={"path": "notes.txt", "content": "updated"},
        requires_confirmation=True,
        confirmation_type="confirmation",
        user_visible_message="Overwrite notes.txt?",
    )


if __name__ == "__main__":
    unittest.main()
