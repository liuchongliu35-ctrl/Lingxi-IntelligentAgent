from __future__ import annotations

import json
import unittest

from src.agent.planner import PlanStep, TaskPlan, TaskUnit
from src.agent.react_executor import ReActExecutor
from src.tools.base import ToolResult
from src.tools.registry import ToolRegistry, ToolSpec


class SequenceModelManager:
    def __init__(self, responses):
        self.responses = list(responses)
        self.generate_calls = []

    def generate(self, prompt: str, **kwargs):
        self.generate_calls.append((prompt, kwargs))
        if self.responses:
            return self.responses.pop(0)
        return "{}"


class RecordingToolManager:
    def __init__(self):
        self.run_calls = []

    def list_tools(self):
        return {"math_calculator": "Fake calculator."}

    def run_tool(self, tool_name: str, **kwargs):
        self.run_calls.append((tool_name, kwargs))
        return ToolResult.ok(data="5", message="5")


    def execute(self, request):
        return self.run_tool(request.tool_name, **request.args)
class ReActExecutorTerminationTest(unittest.TestCase):
    def test_model_finish_stops_later_steps_and_marks_them_skipped(self):
        model = SequenceModelManager(
            [
                json.dumps(
                    {
                        "action_type": "finish",
                        "final_answer": "All required work is already done.",
                    }
                )
            ]
        )
        tools = RecordingToolManager()
        executor = ReActExecutor(model_manager=model, tool_manager=tools, tool_registry=_registry())

        result = executor.execute(_plan([_respond_step("step_1"), _tool_step("step_2")]), task={}, user_input="answer")

        self.assertTrue(result.success)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.step_statuses["step_1"], "completed")
        self.assertEqual(result.step_statuses["step_2"], "skipped")
        self.assertEqual(tools.run_calls, [])
        self.assertEqual(len(model.generate_calls), 1)
        self.assertIn("All required work is already done.", result.output)
        self.assertIn("Skipped: step_2", result.output)

    def test_model_request_replan_stops_later_steps_and_preserves_reason(self):
        model = SequenceModelManager(
            [
                json.dumps(
                    {
                        "action_type": "request_replan",
                        "request_replan_reason": "The selected tool contract no longer matches the task.",
                    }
                )
            ]
        )
        tools = RecordingToolManager()
        executor = ReActExecutor(model_manager=model, tool_manager=tools, tool_registry=_registry())

        result = executor.execute(_plan([_tool_step("step_1"), _tool_step("step_2")]), task={}, user_input="calculate")

        self.assertFalse(result.success)
        self.assertEqual(result.status, "request_replan")
        self.assertTrue(result.request_replan)
        self.assertEqual(result.replan_reason, "The selected tool contract no longer matches the task.")
        self.assertEqual(result.step_statuses["step_1"], "failed")
        self.assertEqual(result.step_statuses["step_2"], "skipped")
        self.assertEqual(tools.run_calls, [])
        self.assertEqual(len(model.generate_calls), 1)
        self.assertIn("ask Planner for a revised TaskPlan", result.output)

    def test_partial_success_then_model_fail_keeps_success_and_failure_in_result(self):
        model = SequenceModelManager(
            [
                json.dumps(
                    {
                        "action_type": "call_tool",
                        "action_target": "math_calculator",
                        "action_args": {"expression": "2+3"},
                    }
                ),
                json.dumps(
                    {
                        "action_type": "fail",
                        "action_args": {"reason": "The second step cannot satisfy the required output."},
                    }
                ),
            ]
        )
        tools = RecordingToolManager()
        executor = ReActExecutor(model_manager=model, tool_manager=tools, tool_registry=_registry())

        result = executor.execute(_plan([_tool_step("step_1"), _respond_step("step_2")]), task={}, user_input="calculate then decide")

        self.assertFalse(result.success)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.step_statuses["step_1"], "completed")
        self.assertEqual(result.step_statuses["step_2"], "failed")
        self.assertEqual(tools.run_calls, [("math_calculator", {"expression": "2+3"})])
        self.assertEqual(len(model.generate_calls), 2)
        self.assertIn("Succeeded:", result.output)
        self.assertIn("step_1/math_calculator", result.output)
        self.assertIn("Failed:", result.output)
        self.assertIn("The second step cannot satisfy the required output.", result.output)


def _plan(steps: list[PlanStep]) -> TaskPlan:
    return TaskPlan(
        goal="termination demo",
        mode="meso",
        steps=steps,
        task_units=[TaskUnit(id="task_1", title="Termination", step_ids=[step.id for step in steps])],
        available_tools=["math_calculator"],
        required_tools=[step.tool_name for step in steps if step.tool_name],
        can_execute=True,
        plan_validation_status="valid",
    )


def _tool_step(step_id: str) -> PlanStep:
    return PlanStep(
        id=step_id,
        task_id="task_1",
        description="Calculate.",
        step_type="tool",
        tool_name="math_calculator",
        args={"expression": "2+3"},
    )


def _respond_step(step_id: str) -> PlanStep:
    return PlanStep(
        id=step_id,
        task_id="task_1",
        description="Respond.",
        step_type="respond",
        tool_name=None,
    )


def _registry() -> ToolRegistry:
    return ToolRegistry(
        [
            ToolSpec(
                name="math_calculator",
                description="Calculate.",
                parameters_schema={"type": "object", "properties": {"expression": {"type": "string"}}},
                required_params=["expression"],
                risk_level="low",
                workspace_scope="none",
            ),
        ]
    )


if __name__ == "__main__":
    unittest.main()
