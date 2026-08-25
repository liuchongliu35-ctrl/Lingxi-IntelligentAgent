from __future__ import annotations

import unittest
from types import SimpleNamespace

from src.agent.planner import Planner


def make_task(**overrides):
    defaults = {
        "trace_id": "trace_policy",
        "mode": "solo",
        "task_type": "tool_operation",
        "execution_strategy": "micro",
        "action_policy": "allow",
        "requires_clarification": False,
        "clarification_questions": [],
        "missing_parameters": [],
        "requires_confirmation": False,
        "confirmation_reason": None,
        "tool_strategy": "tool",
        "available_tools": ["math_calculator", "document_parser"],
        "missing_tools": [],
        "intent": ["calculate"],
        "intent_sequence": ["calculate"],
        "parameters": {"expression": "2+3"},
        "complexity_level": "simple",
        "risk_flags": [],
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class PlannerPolicyConvergenceTest(unittest.TestCase):
    def setUp(self):
        self.planner = Planner()

    def assert_no_tool_steps(self, plan):
        self.assertEqual(plan.required_tools, [])
        for step in plan.steps:
            self.assertIsNone(step.tool_name)
            self.assertNotEqual(step.step_type, "tool")

    def test_block_policy_dominates_normal_micro_and_chat_planning(self):
        task = make_task(
            mode="chat",
            action_policy="block",
            intent=["calculate", "chat"],
            intent_sequence=["calculate", "chat"],
            risk_flags=["dangerous_command"],
        )

        plan = self.planner.create_plan("do a dangerous action, but only explain it", task)

        self.assertEqual(plan.mode, "blocked")
        self.assertFalse(plan.can_execute)
        self.assertEqual(plan.task_units[0].status, "blocked")
        self.assertEqual(len(plan.steps), 1)
        self.assertEqual(plan.steps[0].step_type, "block")
        self.assertEqual(plan.steps[0].metadata["policy"], "block")
        self.assertIn("dangerous_command", plan.steps[0].args["risk_flags"])
        self.assert_no_tool_steps(plan)

    def test_clarify_plan_is_single_waiting_user_step(self):
        task = make_task(
            requires_clarification=True,
            clarification_questions=["Which file should I read?"],
            missing_parameters=["file_path"],
            intent=["read_file"],
            intent_sequence=["read_file"],
        )

        plan = self.planner.create_plan("read the file", task)

        self.assertEqual(plan.mode, "clarify")
        self.assertFalse(plan.can_execute)
        self.assertEqual(plan.task_units[0].status, "waiting_user")
        self.assertEqual(len(plan.steps), 1)
        self.assertEqual(plan.steps[0].step_type, "clarify")
        self.assertEqual(plan.steps[0].args["questions"], ["Which file should I read?"])
        self.assertEqual(plan.steps[0].args["missing_parameters"], ["file_path"])
        self.assert_no_tool_steps(plan)

    def test_confirm_plan_is_single_confirmation_step(self):
        task = make_task(
            action_policy="confirm",
            requires_confirmation=True,
            confirmation_reason="write_file",
            intent=["write_file"],
            intent_sequence=["write_file"],
            risk_flags=["filesystem_write"],
        )

        plan = self.planner.create_plan("overwrite a report", task)

        self.assertEqual(plan.mode, "confirm")
        self.assertFalse(plan.can_execute)
        self.assertEqual(plan.task_units[0].status, "waiting_user")
        self.assertEqual(len(plan.steps), 1)
        self.assertEqual(plan.steps[0].step_type, "confirm")
        self.assertTrue(plan.steps[0].requires_confirmation)
        self.assertEqual(plan.steps[0].confirmation_reason, "write_file")
        self.assertEqual(plan.steps[0].args["reason"], "write_file")
        self.assert_no_tool_steps(plan)

    def test_missing_tools_plan_carries_missing_tools_without_tool_steps(self):
        task = make_task(
            tool_strategy="blocked_missing_tools",
            missing_tools=["excel_parser", "web_search"],
            intent=["read_file", "summarize"],
            intent_sequence=["read_file", "summarize"],
        )

        plan = self.planner.create_plan("summarize a remote excel file", task)

        self.assertEqual(plan.mode, "missing_tools")
        self.assertFalse(plan.can_execute)
        self.assertEqual(plan.task_units[0].status, "blocked")
        self.assertEqual(plan.missing_tools, ["excel_parser", "web_search"])
        self.assertEqual(len(plan.steps), 1)
        self.assertEqual(plan.steps[0].step_type, "respond")
        self.assertTrue(plan.steps[0].allow_model_reasoning)
        self.assertEqual(plan.steps[0].args["missing_tools"], ["excel_parser", "web_search"])
        self.assert_no_tool_steps(plan)

    def test_chat_plan_is_model_only_even_when_intent_mentions_tools(self):
        task = make_task(
            mode="chat",
            intent=["read_file", "delete_file", "chat"],
            intent_sequence=["read_file", "delete_file", "chat"],
            parameters={"file": "data/report.xlsx"},
        )

        plan = self.planner.create_plan("tell me how to delete the file, do not execute", task)

        self.assertEqual(plan.mode, "chat")
        self.assertTrue(plan.can_execute)
        self.assertEqual(plan.task_units[0].status, "pending")
        self.assertEqual(len(plan.steps), 1)
        self.assertEqual(plan.steps[0].step_type, "respond")
        self.assertTrue(plan.steps[0].allow_model_reasoning)
        self.assertEqual(plan.steps[0].metadata["policy"], "chat")
        self.assert_no_tool_steps(plan)


if __name__ == "__main__":
    unittest.main()
