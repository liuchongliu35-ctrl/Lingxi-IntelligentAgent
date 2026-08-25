from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from src.agent.planner import PlanStep, TaskPlan, TaskUnit
from src.agent.react_executor import (
    ACTION_LOOP_NOT_IMPLEMENTED_CODE,
    EMPTY_PLAN_CODE,
    MISSING_STEP_CODE,
    PLAN_NOT_EXECUTABLE_CODE,
    ReActExecutor,
)
from src.agent.react_executor_protocol import ExecutionResult


class FakeModelManager:
    def __init__(self):
        self.generate_calls = 0

    def generate(self, prompt: str, **kwargs):
        self.generate_calls += 1
        if self.generate_calls == 2:
            return json.dumps(
                {
                    "action_type": "call_tool",
                    "action_target": "file_writer",
                    "action_args": {"file_path": "out.txt", "content": "5"},
                }
            )
        return json.dumps(
            {
                "action_type": "call_tool",
                "action_target": "math_calculator",
                "action_args": {"expression": "2+3"},
            }
        )


class FakeToolManager:
    def __init__(self):
        self.run_calls = []

    def list_tools(self):
        return {
            "math_calculator": "Fake calculator.",
            "file_writer": "Fake writer.",
        }

    def run_tool(self, tool_name: str, **kwargs):
        self.run_calls.append((tool_name, kwargs))
        return "5"


    def execute(self, request):
        return self.run_tool(request.tool_name, **request.args)
class ReActExecutorCoreTest(unittest.TestCase):
    def test_can_instantiate_with_default_registry(self):
        model_manager = FakeModelManager()
        tool_manager = FakeToolManager()

        executor = ReActExecutor(model_manager=model_manager, tool_manager=tool_manager)

        self.assertIs(executor.model_manager, model_manager)
        self.assertIs(executor.tool_manager, tool_manager)
        self.assertTrue(executor.tool_registry.has_tool("math_calculator"))
        self.assertTrue(executor.tool_registry.has_tool("file_writer"))

    def test_rejects_plan_can_execute_false_without_tool_or_model_calls(self):
        model_manager = FakeModelManager()
        tool_manager = FakeToolManager()
        executor = ReActExecutor(model_manager=model_manager, tool_manager=tool_manager)
        plan = _plan(can_execute=False, mode="blocked")

        result = executor.execute(plan, task={}, user_input="blocked request")

        self.assertIsInstance(result, ExecutionResult)
        self.assertFalse(result.success)
        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.error_code, PLAN_NOT_EXECUTABLE_CODE)
        self.assertEqual(model_manager.generate_calls, 0)
        self.assertEqual(tool_manager.run_calls, [])
        self.assertNotIn("step_started", _event_types(result))
        self.assertIn("final_answer", _event_types(result))

    def test_executable_plan_enters_react_loop_entry_without_skeleton_traversal(self):
        model_manager = FakeModelManager()
        tool_manager = FakeToolManager()
        executor = ReActExecutor(model_manager=model_manager, tool_manager=tool_manager)
        plan = _plan(
            steps=[
                PlanStep(
                    id="step_1",
                    task_id="task_1",
                    description="Calculate.",
                    step_type="tool",
                    tool_name="math_calculator",
                    args={"expression": "2+3"},
                    output_key="calculation",
                ),
                PlanStep(
                    id="step_2",
                    task_id="task_1",
                    description="Write result.",
                    step_type="tool",
                    tool_name="file_writer",
                    args={"file_path": "out.txt", "content": "placeholder"},
                    depends_on=["step_1"],
                    input_from=["calculation"],
                ),
            ],
            task_units=[TaskUnit(id="task_1", title="Demo task", step_ids=["step_1", "step_2"])],
        )

        with patch.object(
            executor,
            "_traverse_plan_skeleton",
            side_effect=AssertionError("default execute must not use legacy skeleton traversal"),
        ):
            result = executor.execute(plan, task={}, user_input="calculate and write")

        self.assertTrue(result.success)
        self.assertEqual(result.status, "completed")
        self.assertIsNone(result.error_code)
        self.assertEqual(result.task_statuses, {"task_1": "completed"})
        self.assertEqual(result.step_statuses["step_1"], "completed")
        self.assertEqual(result.step_statuses["step_2"], "completed")
        self.assertEqual(_event_types(result).count("step_started"), 2)
        self.assertEqual(_event_types(result).count("step_failed"), 0)
        self.assertIn("progress_message", _event_types(result))
        self.assertEqual(model_manager.generate_calls, 2)
        self.assertEqual(
            tool_manager.run_calls,
            [
                ("math_calculator", {"expression": "2+3"}),
                ("file_writer", {"file_path": "out.txt", "content": "5"}),
            ],
        )

    def test_legacy_skeleton_traversal_remains_available_only_for_explicit_diagnostics(self):
        executor = ReActExecutor(model_manager=FakeModelManager(), tool_manager=FakeToolManager())
        plan = _plan()
        context = executor._create_context(plan, task={}, user_input="calculate", history="")

        result = executor._traverse_plan_skeleton(context)

        self.assertFalse(result.success)
        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.error_code, ACTION_LOOP_NOT_IMPLEMENTED_CODE)
        self.assertEqual(result.step_statuses["step_1"], "blocked")
        self.assertIn("Legacy diagnostic skeleton traversal", result.output)
        self.assertEqual(_event_types(result).count("step_started"), 1)
        self.assertEqual(_event_types(result).count("step_failed"), 1)

    def test_missing_step_reference_returns_structured_failure(self):
        executor = ReActExecutor(model_manager=FakeModelManager(), tool_manager=FakeToolManager())
        plan = _plan(task_units=[TaskUnit(id="task_1", title="Bad refs", step_ids=["step_1", "missing_step"])])

        result = executor.execute(plan, task={}, user_input="bad plan")

        self.assertFalse(result.success)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_code, MISSING_STEP_CODE)
        self.assertEqual(result.failed_step_id, "missing_step")
        self.assertEqual(result.step_statuses["missing_step"], "failed")
        self.assertIn("step_failed", _event_types(result))

    def test_empty_plan_returns_basic_failed_result(self):
        executor = ReActExecutor(model_manager=FakeModelManager(), tool_manager=FakeToolManager())
        plan = TaskPlan(goal="empty", mode="micro", can_execute=True, steps=[], task_units=[])

        result = executor.execute(plan, task={}, user_input="empty")

        self.assertFalse(result.success)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_code, EMPTY_PLAN_CODE)
        self.assertEqual(result.observations, [])

    def test_execution_result_is_json_serializable(self):
        executor = ReActExecutor(model_manager=FakeModelManager(), tool_manager=FakeToolManager())
        result = executor.execute(_plan(), task={}, user_input="calculate")

        payload = result.to_dict()

        self.assertEqual(payload["status"], "completed")
        self.assertIsNone(payload["error_code"])
        json.dumps(payload, ensure_ascii=False)


def _plan(
    *,
    can_execute: bool = True,
    mode: str = "micro",
    steps: list[PlanStep] | None = None,
    task_units: list[TaskUnit] | None = None,
) -> TaskPlan:
    steps = steps or [
        PlanStep(
            id="step_1",
            task_id="task_1",
            description="Calculate.",
            step_type="tool",
            tool_name="math_calculator",
            args={"expression": "2+3"},
            output_key="calculation",
        )
    ]
    task_units = task_units or [TaskUnit(id="task_1", title="Demo task", step_ids=[step.id for step in steps])]
    return TaskPlan(
        goal="demo",
        mode=mode,
        can_execute=can_execute,
        steps=steps,
        task_units=task_units,
        available_tools=["math_calculator", "file_writer"],
        required_tools=["math_calculator"],
        plan_validation_status="valid",
    )


def _event_types(result: ExecutionResult) -> list[str]:
    return [event.type for event in result.events]


if __name__ == "__main__":
    unittest.main()
