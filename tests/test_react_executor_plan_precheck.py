from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from src.agent.planner import PlanStep, TaskPlan, TaskUnit
from src.agent.react_executor import (
    CLARIFICATION_REQUIRED_CODE,
    CONFIRMATION_REQUIRED_CODE,
    INVALID_PLAN_CODE,
    MISSING_STEP_CODE,
    MISSING_TOOLS_CODE,
    PLAN_REFERENCE_ERROR_CODE,
    TASK_POLICY_BLOCKED_CODE,
    TOOL_NOT_AVAILABLE_CODE,
    ReActExecutor,
)


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


class ChatModelManager:
    def __init__(self):
        self.generate_calls = 0

    def generate(self, prompt: str, **kwargs):
        self.generate_calls += 1
        return json.dumps({"action_type": "finish", "final_answer": "chat answer"})


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
class ReActExecutorPlanPrecheckTest(unittest.TestCase):
    def setUp(self):
        self.model_manager = FakeModelManager()
        self.tool_manager = FakeToolManager()
        self.executor = ReActExecutor(model_manager=self.model_manager, tool_manager=self.tool_manager)

    def test_invalid_plan_fails_before_step_traversal(self):
        plan = _plan(plan_validation_status="invalid", plan_validation_notes=["bad dependency"])

        result = self.executor.execute(plan, task=_task(), user_input="invalid")

        self.assertFalse(result.success)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_code, INVALID_PLAN_CODE)
        self.assertEqual(result.step_statuses["step_1"], "failed")
        self.assertNotIn("step_started", _event_types(result))
        self.assert_no_model_or_tool_calls()

    def test_block_task_policy_blocks_before_execution(self):
        plan = _plan()

        result = self.executor.execute(plan, task=_task(action_policy="block"), user_input="blocked")

        self.assertFalse(result.success)
        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.error_code, TASK_POLICY_BLOCKED_CODE)
        self.assertEqual(result.step_statuses["step_1"], "blocked")
        self.assert_no_model_or_tool_calls()

    def test_clarify_mode_returns_user_input_request(self):
        plan = _plan(
            mode="clarify",
            can_execute=False,
            steps=[
                PlanStep(
                    id="step_1",
                    task_id="task_1",
                    description="Ask for details.",
                    step_type="clarify",
                    args={"questions": ["Which file should I read?"]},
                )
            ],
        )

        result = self.executor.execute(plan, task=_task(), user_input="clarify")

        self.assertFalse(result.success)
        self.assertEqual(result.status, "waiting_user")
        self.assertEqual(result.error_code, CLARIFICATION_REQUIRED_CODE)
        self.assertTrue(result.requires_user_input)
        self.assertEqual(result.user_input_request, "Which file should I read?")
        self.assertEqual(result.step_statuses["step_1"], "waiting_user")
        self.assert_no_model_or_tool_calls()

    def test_confirm_mode_returns_pending_confirmation(self):
        plan = _plan(
            mode="confirm",
            can_execute=False,
            steps=[
                PlanStep(
                    id="step_1",
                    task_id="task_1",
                    description="Confirm write.",
                    step_type="confirm",
                    requires_confirmation=True,
                    confirmation_reason="file overwrite",
                )
            ],
        )

        result = self.executor.execute(plan, task=_task(), user_input="confirm")

        self.assertFalse(result.success)
        self.assertEqual(result.status, "waiting_user")
        self.assertEqual(result.error_code, CONFIRMATION_REQUIRED_CODE)
        self.assertTrue(result.requires_user_input)
        self.assertIsNotNone(result.pending_confirmation)
        self.assertIn("confirmation_requested", _event_types(result))
        self.assert_no_model_or_tool_calls()

    def test_missing_tools_mode_blocks_with_missing_tool_list(self):
        plan = _plan(mode="missing_tools", can_execute=False, missing_tools=["browser_tool"])

        result = self.executor.execute(plan, task=_task(), user_input="missing tools")

        self.assertFalse(result.success)
        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.error_code, MISSING_TOOLS_CODE)
        self.assertIn("browser_tool", result.output)
        self.assert_no_model_or_tool_calls()

    def test_chat_mode_does_not_call_execution_tools_before_model_action_step(self):
        model_manager = ChatModelManager()
        executor = ReActExecutor(model_manager=model_manager, tool_manager=self.tool_manager)
        plan = _plan(mode="chat", steps=[PlanStep(id="step_1", task_id="task_1", description="Respond.", step_type="respond")])

        result = executor.execute(plan, task=_task(), user_input="chat")

        self.assertTrue(result.success)
        self.assertEqual(result.status, "completed")
        self.assertIsNone(result.error_code)
        self.assertEqual(result.step_statuses["step_1"], "completed")
        self.assertNotIn("tool_started", _event_types(result))
        self.assertEqual(model_manager.generate_calls, 1)
        self.assertEqual(self.tool_manager.run_calls, [])

    def test_tool_not_available_fails_precheck(self):
        plan = _plan(
            steps=[
                PlanStep(
                    id="step_1",
                    task_id="task_1",
                    description="Search.",
                    step_type="tool",
                    tool_name="search_tool",
                    args={"query": "agent"},
                )
            ],
            available_tools=["search_tool"],
        )

        result = self.executor.execute(plan, task=_task(), user_input="search")

        self.assertFalse(result.success)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_code, TOOL_NOT_AVAILABLE_CODE)
        self.assertEqual(result.failed_step_id, "step_1")
        self.assert_no_model_or_tool_calls()

    def test_task_unit_missing_step_fails_structurally(self):
        plan = _plan(task_units=[TaskUnit(id="task_1", title="Bad refs", step_ids=["missing_step"])])

        result = self.executor.execute(plan, task=_task(), user_input="bad refs")

        self.assertFalse(result.success)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_code, MISSING_STEP_CODE)
        self.assertEqual(result.failed_step_id, "missing_step")
        self.assertEqual(result.step_statuses["missing_step"], "failed")
        self.assertNotIn("step_started", _event_types(result))
        self.assert_no_model_or_tool_calls()

    def test_depends_on_or_input_from_missing_ref_fails_structurally(self):
        plan = _plan(
            steps=[
                PlanStep(
                    id="step_1",
                    task_id="task_1",
                    description="Summarize.",
                    step_type="tool",
                    tool_name="math_calculator",
                    args={"expression": "1+1"},
                    depends_on=["missing_dep"],
                    input_from=["missing_output"],
                )
            ]
        )

        result = self.executor.execute(plan, task=_task(), user_input="bad refs")

        self.assertFalse(result.success)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_code, PLAN_REFERENCE_ERROR_CODE)
        self.assertEqual(result.failed_step_id, "step_1")
        self.assert_no_model_or_tool_calls()

    def test_input_from_can_reference_output_key(self):
        plan = _plan(
            steps=[
                PlanStep(
                    id="step_1",
                    task_id="task_1",
                    description="Calculate.",
                    step_type="tool",
                    tool_name="math_calculator",
                    args={"expression": "1+1"},
                    output_key="calculation",
                ),
                PlanStep(
                    id="step_2",
                    task_id="task_1",
                    description="Write.",
                    step_type="tool",
                    tool_name="file_writer",
                    args={"file_path": "out.txt", "content": "placeholder"},
                    input_from=["calculation"],
                ),
            ],
            task_units=[TaskUnit(id="task_1", title="Refs", step_ids=["step_1", "step_2"])],
        )

        result = self.executor.execute(plan, task=_task(), user_input="valid refs")

        self.assertTrue(result.success)
        self.assertEqual(result.status, "completed")
        self.assertIsNone(result.error_code)
        self.assertEqual(result.step_statuses["step_1"], "completed")
        self.assertEqual(result.step_statuses["step_2"], "completed")
        self.assertEqual(self.model_manager.generate_calls, 2)
        self.assertEqual(
            self.tool_manager.run_calls,
            [
                ("math_calculator", {"expression": "2+3"}),
                ("file_writer", {"file_path": "out.txt", "content": "5"}),
            ],
        )

    def assert_no_model_or_tool_calls(self):
        self.assertEqual(self.model_manager.generate_calls, 0)
        self.assertEqual(self.tool_manager.run_calls, [])


def _plan(
    *,
    mode: str = "micro",
    can_execute: bool = True,
    steps: list[PlanStep] | None = None,
    task_units: list[TaskUnit] | None = None,
    available_tools: list[str] | None = None,
    missing_tools: list[str] | None = None,
    plan_validation_status: str = "valid",
    plan_validation_notes: list[str] | None = None,
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
    task_units = task_units or [TaskUnit(id="task_1", title="Demo", step_ids=[step.id for step in steps])]
    return TaskPlan(
        goal="demo",
        mode=mode,
        can_execute=can_execute,
        steps=steps,
        task_units=task_units,
        available_tools=available_tools if available_tools is not None else ["math_calculator", "file_writer"],
        required_tools=[step.tool_name for step in steps if step.tool_name],
        missing_tools=missing_tools or [],
        plan_validation_status=plan_validation_status,
        plan_validation_notes=plan_validation_notes or [],
    )


def _task(**kwargs):
    values = {"action_policy": "allow", "requires_confirmation": False}
    values.update(kwargs)
    return SimpleNamespace(**values)


def _event_types(result) -> list[str]:
    return [event.type for event in result.events]


if __name__ == "__main__":
    unittest.main()
