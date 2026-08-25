from __future__ import annotations

import json
import unittest

from src.agent.output_feedback import OutputFeedbackProcessor
from src.agent.react_executor_observation import REDACTED_VALUE
from src.agent.react_executor_protocol import ExecutionEvent, ExecutionResult


class OutputFeedbackProcessorTest(unittest.TestCase):
    def test_build_consumes_visible_events_without_internal_logs(self):
        visible = ExecutionEvent(
            execution_id="exec_1",
            plan_id="plan_1",
            type="tool_failed",
            message="Command failed; stderr was truncated.",
            payload={
                "tool_call_id": "call_1",
                "tool_name": "command_tool",
                "raw_output": "full raw secret output",
                "safe": "summary",
            },
            step_id="step_1",
        )
        internal = ExecutionEvent(
            execution_id="exec_1",
            plan_id="plan_1",
            type="system_notice",
            message="logs/tools.log wrote audit record",
            visible_to_user=False,
            payload={"log_path": "logs/tools.log"},
        )
        result = ExecutionResult(
            execution_id="exec_1",
            plan_id="plan_1",
            status="failed",
            success=False,
            output="Status: failed.",
            summary="failed",
            events=[visible, internal],
        )

        feedback = OutputFeedbackProcessor().build(result)
        payload = json.dumps(feedback.to_dict(), ensure_ascii=False)

        self.assertEqual(len(feedback.items), 1)
        self.assertEqual(feedback.items[0].type, "tool_failed")
        self.assertIn("Command failed", payload)
        self.assertIn(REDACTED_VALUE, payload)
        self.assertNotIn("full raw secret output", payload)
        self.assertNotIn("logs/tools.log", payload)

    def test_waiting_user_feedback_preserves_pending_confirmation_summary(self):
        result = ExecutionResult(
            execution_id="exec_1",
            plan_id="plan_1",
            status="waiting_user",
            success=False,
            output="Waiting for user confirmation.",
            summary="waiting_user=true",
            requires_user_input=True,
            user_input_request="Approve write_file?",
            pending_confirmation={
                "confirmation_id": "confirm_1",
                "preview_summary": "overwrite out.txt",
                "api_key": "secret",
            },
            events=[
                ExecutionEvent(
                    execution_id="exec_1",
                    plan_id="plan_1",
                    type="confirmation_requested",
                    message="Approve write_file?",
                    payload={"preview": {"path": "out.txt"}},
                )
            ],
        )

        feedback = OutputFeedbackProcessor().build(result)

        self.assertTrue(feedback.requires_user_input)
        self.assertEqual(feedback.user_input_request, "Approve write_file?")
        self.assertEqual(feedback.pending_confirmation["api_key"], REDACTED_VALUE)
        self.assertEqual(feedback.timeline[0]["type"], "confirmation_requested")


if __name__ == "__main__":
    unittest.main()
