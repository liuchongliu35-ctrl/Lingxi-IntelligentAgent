from __future__ import annotations

import unittest
from types import SimpleNamespace

from src.agent.executor import Executor
from src.agent.planner import Planner
from src.tools.base import ToolResult


class FakeModelManager:
    def __init__(self):
        self.calls = []

    def generate(self, prompt: str, **kwargs):
        self.calls.append(prompt)
        return "model response"


class FakeToolManager:
    def __init__(self):
        self.calls = []

    def run_tool(self, tool_name: str, **kwargs):
        self.calls.append((tool_name, kwargs))
        return ToolResult.ok(data="tool response", message="tool response")


def make_task(**overrides):
    defaults = {
        "mode": "solo",
        "execution_strategy": "meso",
        "action_policy": "allow",
        "requires_clarification": False,
        "clarification_questions": [],
        "requires_confirmation": False,
        "confirmation_reason": None,
        "tool_strategy": "model_only",
        "missing_tools": [],
        "intent": ["chat"],
        "intent_sequence": ["chat"],
        "parameters": {},
        "complexity_level": "medium",
        "risk_flags": [],
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class PlannerExecutorPolicyTest(unittest.TestCase):
    def setUp(self):
        self.planner = Planner()
        self.model_manager = FakeModelManager()
        self.tool_manager = FakeToolManager()
        self.executor = Executor(self.model_manager, self.tool_manager)

    def test_block_policy_returns_blocked_plan_and_does_not_call_tools(self):
        task = make_task(action_policy="block", risk_flags=["dangerous_command"])
        plan = self.planner.create_plan("删除 C:\\Windows\\System32", task)
        result = self.executor.execute(plan, task, "删除 C:\\Windows\\System32")

        self.assertEqual(plan.mode, "blocked")
        self.assertFalse(result.success)
        self.assertIn("高风险操作", result.output)
        self.assertEqual(self.tool_manager.calls, [])

    def test_clarification_uses_analyzer_questions_and_does_not_call_tools(self):
        task = make_task(
            requires_clarification=True,
            clarification_questions=["请说明要翻译成哪种语言。"],
            missing_parameters=["target_language"],
        )
        plan = self.planner.create_plan("翻译：hello", task)
        result = self.executor.execute(plan, task, "翻译：hello")

        self.assertEqual(plan.mode, "clarify")
        self.assertFalse(result.success)
        self.assertEqual(result.output, "请说明要翻译成哪种语言。")
        self.assertEqual(self.tool_manager.calls, [])

    def test_confirm_policy_pauses_before_tool_execution(self):
        task = make_task(
            action_policy="confirm",
            requires_confirmation=True,
            confirmation_reason="delete_file",
            intent=["delete_file"],
            intent_sequence=["delete_file"],
        )
        plan = self.planner.create_plan("删除 data/report.xlsx", task)
        result = self.executor.execute(plan, task, "删除 data/report.xlsx")

        self.assertEqual(plan.mode, "confirm")
        self.assertFalse(result.success)
        self.assertIn("delete_file", result.output)
        self.assertEqual(self.tool_manager.calls, [])

    def test_chat_mode_generates_guidance_without_tool_execution(self):
        task = make_task(mode="chat", intent=["delete_file", "chat"], intent_sequence=["delete_file", "chat"])
        plan = self.planner.create_plan("只告诉我怎么删除 data/report.xlsx，不要执行", task)
        result = self.executor.execute(plan, task, "只告诉我怎么删除 data/report.xlsx，不要执行")

        self.assertEqual(plan.mode, "chat")
        self.assertTrue(result.success)
        self.assertEqual(result.output, "model response")
        self.assertEqual(self.tool_manager.calls, [])
        self.assertEqual(len(self.model_manager.calls), 1)

    def test_missing_tools_stops_execution(self):
        task = make_task(tool_strategy="blocked_missing_tools", missing_tools=["excel_parser"])
        plan = self.planner.create_plan("分析 Excel", task)
        result = self.executor.execute(plan, task, "分析 Excel")

        self.assertEqual(plan.mode, "missing_tools")
        self.assertFalse(result.success)
        self.assertIn("excel_parser", result.output)
        self.assertEqual(self.tool_manager.calls, [])

    def test_allowed_micro_task_still_calls_tool(self):
        task = make_task(
            execution_strategy="micro",
            tool_strategy="tool",
            intent=["calculate"],
            intent_sequence=["calculate"],
            parameters={"expression": "2+3"},
            complexity_level="simple",
        )
        plan = self.planner.create_plan("计算2+3", task)
        result = self.executor.execute(plan, task, "计算2+3")

        self.assertEqual(plan.mode, "micro")
        self.assertTrue(result.success)
        self.assertEqual(result.output, "tool response")
        self.assertEqual(self.tool_manager.calls, [("math_calculator", {"expression": "2+3"})])


if __name__ == "__main__":
    unittest.main()
