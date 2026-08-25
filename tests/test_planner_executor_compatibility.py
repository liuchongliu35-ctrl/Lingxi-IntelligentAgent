from __future__ import annotations

import unittest
from types import SimpleNamespace

from src.agent.executor import Executor
from src.agent.planner import Planner
from src.tools.base import ToolResult


def make_task(**overrides):
    defaults = {
        "trace_id": "trace_executor_compat",
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
        "available_tools": ["math_calculator", "search_tool", "text_processor", "file_writer"],
        "missing_tools": [],
        "intent": ["calculate"],
        "intent_sequence": ["calculate"],
        "parameters": {"expression": "2+3"},
        "file_info": {},
        "edit_mode": None,
        "project_stage": None,
        "tech_stacks": [],
        "complexity_level": "simple",
        "risk_flags": [],
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class FakeModelManager:
    def __init__(self, response="model response"):
        self.response = response
        self.calls = []

    def generate(self, prompt: str, **kwargs):
        self.calls.append(prompt)
        return self.response


class FakeToolManager:
    def __init__(self):
        self.calls = []

    def run_tool(self, tool_name: str, **kwargs):
        self.calls.append((tool_name, kwargs))
        if tool_name == "search_tool":
            return ToolResult.ok(data="search data", message="search output")
        if tool_name == "text_processor":
            return ToolResult.ok(data="summary data", message=f"summary of {kwargs['text']}")
        if tool_name == "file_writer":
            return ToolResult.ok(data={"path": kwargs["file_path"]}, message=f"wrote {kwargs['content']}")
        return ToolResult.ok(data="tool data", message="tool response")


class PlannerExecutorCompatibilityTest(unittest.TestCase):
    def setUp(self):
        self.model_manager = FakeModelManager()
        self.tool_manager = FakeToolManager()
        self.executor = Executor(self.model_manager, self.tool_manager)
        self.planner = Planner()

    def test_current_executor_consumes_new_micro_plan_structure(self):
        task = make_task()
        plan = self.planner.create_plan("calculate 2+3", task)

        result = self.executor.execute(plan, task, "calculate 2+3")

        self.assertTrue(result.success)
        self.assertEqual(result.output, "tool response")
        self.assertEqual(len(plan.task_units), 1)
        self.assertEqual(plan.steps[0].task_id, plan.task_units[0].id)
        self.assertEqual(self.tool_manager.calls, [("math_calculator", {"expression": "2+3"})])

    def test_current_executor_still_short_circuits_special_policy_plans(self):
        cases = [
            (
                make_task(action_policy="block", risk_flags=["dangerous_command"], execution_strategy="meso"),
                "delete system files",
                "blocked",
            ),
            (
                make_task(
                    requires_clarification=True,
                    clarification_questions=["Which file?"],
                    missing_parameters=["file_path"],
                    execution_strategy="macro",
                ),
                "read file",
                "clarify",
            ),
            (
                make_task(
                    action_policy="confirm",
                    requires_confirmation=True,
                    confirmation_reason="write_file",
                    execution_strategy="meso",
                ),
                "overwrite file",
                "confirm",
            ),
            (
                make_task(tool_strategy="blocked_missing_tools", missing_tools=["excel_parser"], execution_strategy="meso"),
                "analyze excel",
                "missing_tools",
            ),
        ]

        for task, user_input, expected_mode in cases:
            with self.subTest(mode=expected_mode):
                self.tool_manager.calls.clear()
                plan = self.planner.create_plan(user_input, task)
                result = self.executor.execute(plan, task, user_input)

                self.assertEqual(plan.mode, expected_mode)
                self.assertFalse(result.success)
                self.assertEqual(self.tool_manager.calls, [])

    def test_current_executor_executes_chat_plan_as_model_only(self):
        task = make_task(mode="chat", intent=["chat"], intent_sequence=["chat"], execution_strategy="meso")
        plan = self.planner.create_plan("explain how to do it", task)

        result = self.executor.execute(plan, task, "explain how to do it")

        self.assertTrue(result.success)
        self.assertEqual(result.output, "model response")
        self.assertEqual(self.tool_manager.calls, [])
        self.assertEqual(len(self.model_manager.calls), 1)

    def test_current_executor_rejects_invalid_new_plan_before_tool_execution(self):
        task = make_task(
            task_type="document_understanding",
            execution_strategy="meso",
            intent=["read_file"],
            intent_sequence=["read_file"],
            parameters={},
            available_tools=["document_parser"],
        )
        plan = self.planner.create_plan("read a file", task)

        result = self.executor.execute(plan, task, "read a file")

        self.assertEqual(plan.plan_validation_status, "invalid")
        self.assertFalse(result.success)
        self.assertIn("document_parser requires file_path", result.output)
        self.assertEqual(self.tool_manager.calls, [])

    def test_current_executor_resolves_new_input_from_dependencies(self):
        task = make_task(
            execution_strategy="meso",
            task_type="research",
            intent=["search", "summarize", "write_file"],
            intent_sequence=["search", "summarize", "write_file"],
            parameters={"topic": "planner", "target_path": "out/planner.md"},
        )
        plan = self.planner.create_plan("search planner and write notes", task)

        result = self.executor.execute(plan, task, "search planner and write notes")

        self.assertTrue(result.success)
        self.assertEqual(
            self.tool_manager.calls,
            [
                ("search_tool", {"query": "planner", "max_results": 5}),
                ("text_processor", {"operation": "summary", "text": "search output"}),
                ("file_writer", {"file_path": "out/planner.md", "content": "summary of search output", "overwrite": False}),
            ],
        )

    def test_current_executor_runs_llm_model_only_plan(self):
        llm_response = {
            "mode": "meso",
            "task_units": [{"id": "task_1", "title": "Answer", "step_ids": ["step_1"]}],
            "steps": [
                {
                    "id": "step_1",
                    "task_id": "task_1",
                    "step_type": "respond",
                    "description": "Answer without tools.",
                    "expected_output": "Answer",
                }
            ],
        }
        planner = Planner(model_manager=FakeModelManager(response=llm_response))
        task = make_task(
            execution_strategy="meso_advanced",
            task_type="qa",
            tool_strategy="model_only",
            intent=["unknown_operation"],
            intent_sequence=["unknown_operation"],
            parameters={},
            available_tools=[],
        )

        plan = planner.create_plan("handle unusual request", task)
        result = self.executor.execute(plan, task, "handle unusual request")

        self.assertEqual(plan.planning_strategy, "llm_planner")
        self.assertTrue(result.success)
        self.assertEqual(result.output, "model response")
        self.assertEqual(self.tool_manager.calls, [])


if __name__ == "__main__":
    unittest.main()
