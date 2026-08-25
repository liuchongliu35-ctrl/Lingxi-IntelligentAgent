from __future__ import annotations

import json
import unittest

from src.agent.planner import PlanStep, TaskPlan, TaskUnit
from src.agent.react_executor import (
    CONFIRMATION_PENDING_CODE,
    CONFIRMATION_REJECTED_CODE,
    USER_INPUT_REQUIRED_CODE,
    ReActExecutor,
)
from src.agent.react_executor_protocol import ActionPacket
from src.tools.base import ToolResult
from src.tools.registry import ToolRegistry, ToolSpec


class FakeToolManager:
    def __init__(self):
        self.run_calls = []

    def list_tools(self):
        return {"math_calculator": "Fake calculator."}

    def run_tool(self, tool_name: str, **kwargs):
        self.run_calls.append((tool_name, kwargs))
        return ToolResult.ok(data="5", message="5")


    def execute(self, request):
        return self.run_tool(request.tool_name, **request.args)
class SequenceModel:
    def __init__(self, responses):
        self.responses = list(responses)
        self.generate_calls = []

    def generate(self, prompt: str, **kwargs):
        self.generate_calls.append((prompt, kwargs))
        if self.responses:
            return self.responses.pop(0)
        return "{}"


class ReActExecutorConfirmationTest(unittest.TestCase):
    def setUp(self):
        self.tool_manager = FakeToolManager()
        self.executor = ReActExecutor(model_manager=None, tool_manager=self.tool_manager)

    def test_ask_user_returns_waiting_observation_and_pending_request(self):
        plan, step = _plan_with_steps([_tool_step("step_1")])
        context = self.executor._create_context(plan, task={}, user_input="demo", history="")
        packet = ActionPacket(
            execution_id=context.execution_id,
            plan_id=context.plan_id,
            task_id="task_1",
            step_id="step_1",
            action_type="ask_user",
            action_args={"ask_type": "clarification", "question": "Which file?"},
        )

        observation = self.executor.dispatch_action(context, packet, step=step)

        self.assertFalse(observation.success)
        self.assertEqual(observation.code, USER_INPUT_REQUIRED_CODE)
        self.assertTrue(context.requires_user_input)
        self.assertEqual(context.user_input_request, "Which file?")
        self.assertIsNotNone(context.pending_confirmation)
        self.assertEqual(context.pending_confirmation.confirmation_type, "clarification")
        self.assertEqual(context.step_states["step_1"].status, "waiting_user")
        self.assertIn("confirmation_requested", _event_types(context))

    def test_action_requiring_confirmation_does_not_execute_tool(self):
        plan, step = _plan_with_steps([_tool_step("step_1", requires_confirmation=True)])
        context = self.executor._create_context(plan, task={}, user_input="demo", history="")
        packet = _call_tool_packet(context, requires_confirmation=True)

        observation = self.executor.dispatch_action(context, packet, step=step)

        self.assertFalse(observation.success)
        self.assertEqual(observation.code, CONFIRMATION_PENDING_CODE)
        self.assertTrue(context.requires_user_input)
        self.assertIsNotNone(context.pending_confirmation)
        self.assertEqual(context.step_states["step_1"].status, "waiting_user")
        self.assertEqual(self.tool_manager.run_calls, [])

    def test_confirmed_action_executes_current_action(self):
        plan, step = _plan_with_steps([_tool_step("step_1", requires_confirmation=True)])
        context = self.executor._create_context(plan, task={}, user_input="demo", history="")
        packet = _call_tool_packet(context, requires_confirmation=True)
        self.executor.dispatch_action(context, packet, step=step)

        observation = self.executor.dispatch_action(context, packet, step=step, confirmed=True)

        self.assertTrue(observation.success)
        self.assertEqual(observation.data, "5")
        self.assertEqual(self.tool_manager.run_calls, [("math_calculator", {"expression": "2+3"})])

    def test_handle_confirmation_response_approved_executes_pending_action(self):
        plan, step = _plan_with_steps([_tool_step("step_1", requires_confirmation=True)])
        context = self.executor._create_context(plan, task={}, user_input="demo", history="")
        packet = _call_tool_packet(context, requires_confirmation=True)
        self.executor.dispatch_action(context, packet, step=step)

        observation = self.executor.handle_confirmation_response(context, approved=True)

        self.assertTrue(observation.success)
        self.assertFalse(context.requires_user_input)
        self.assertIsNone(context.pending_confirmation)
        self.assertEqual(self.tool_manager.run_calls, [("math_calculator", {"expression": "2+3"})])

    def test_handle_confirmation_response_rejected_cancels_step_and_skips_dependents(self):
        step_1 = _tool_step("step_1", requires_confirmation=True, output_key="calculation")
        step_2 = _tool_step("step_2", depends_on=["step_1"], input_from=["calculation"])
        plan, step = _plan_with_steps([step_1, step_2])
        context = self.executor._create_context(plan, task={}, user_input="demo", history="")
        packet = _call_tool_packet(context, requires_confirmation=True)
        self.executor.dispatch_action(context, packet, step=step)

        observation = self.executor.handle_confirmation_response(context, approved=False, reason="No")

        self.assertFalse(observation.success)
        self.assertEqual(observation.code, CONFIRMATION_REJECTED_CODE)
        self.assertEqual(context.step_states["step_1"].status, "cancelled")
        self.assertEqual(context.step_states["step_2"].status, "skipped")
        self.assertEqual(self.tool_manager.run_calls, [])
        self.assertFalse(context.requires_user_input)
        self.assertIsNone(context.pending_confirmation)

    def test_resume_after_confirmation_approved_executes_pending_action_and_continues_loop(self):
        model = SequenceModel(
            [
                json.dumps(
                    {
                        "action_type": "call_tool",
                        "action_target": "math_calculator",
                        "action_args": {"expression": "2+3"},
                        "requires_confirmation": True,
                        "confirmation_type": "confirmation",
                        "user_visible_message": "Run calculator?",
                    }
                ),
                json.dumps(
                    {
                        "action_type": "call_tool",
                        "action_target": "math_calculator",
                        "action_args": {"expression": "7+8"},
                    }
                ),
            ]
        )
        executor = ReActExecutor(model_manager=model, tool_manager=self.tool_manager)
        step_1 = _tool_step("step_1", output_key="first")
        step_2 = _tool_step("step_2", output_key="second")
        plan, _step = _plan_with_steps([step_1, step_2])
        context = executor._create_context(plan, task={}, user_input="demo", history="")

        paused = executor._execute_react_loop(context)
        resumed = executor.resume_after_confirmation(context, approved=True)

        self.assertEqual(paused.status, "waiting_user")
        self.assertIsNone(resumed.pending_confirmation)
        self.assertTrue(resumed.success)
        self.assertEqual(resumed.status, "completed")
        self.assertEqual(resumed.step_statuses["step_1"], "completed")
        self.assertEqual(resumed.step_statuses["step_2"], "completed")
        self.assertEqual(
            self.tool_manager.run_calls,
            [
                ("math_calculator", {"expression": "2+3"}),
                ("math_calculator", {"expression": "7+8"}),
            ],
        )
        self.assertEqual(len(model.generate_calls), 2)

    def test_resume_after_confirmation_rejected_skips_dependents_and_runs_independent_steps(self):
        model = SequenceModel(
            [
                json.dumps(
                    {
                        "action_type": "call_tool",
                        "action_target": "math_calculator",
                        "action_args": {"expression": "2+3"},
                        "requires_confirmation": True,
                        "confirmation_type": "confirmation",
                    }
                ),
                json.dumps(
                    {
                        "action_type": "call_tool",
                        "action_target": "math_calculator",
                        "action_args": {"expression": "11+12"},
                    }
                ),
            ]
        )
        executor = ReActExecutor(model_manager=model, tool_manager=self.tool_manager)
        step_1 = _tool_step("step_1", output_key="first")
        step_2 = _tool_step("step_2", depends_on=["step_1"], input_from=["first"])
        step_3 = _tool_step("step_3")
        plan, _step = _plan_with_steps([step_1, step_2, step_3])
        context = executor._create_context(plan, task={}, user_input="demo", history="")

        paused = executor._execute_react_loop(context)
        resumed = executor.resume_after_confirmation(context, approved=False, reason="No")

        self.assertEqual(paused.status, "waiting_user")
        self.assertEqual(resumed.step_statuses["step_1"], "cancelled")
        self.assertEqual(resumed.step_statuses["step_2"], "skipped")
        self.assertEqual(resumed.step_statuses["step_3"], "completed")
        self.assertEqual(self.tool_manager.run_calls, [("math_calculator", {"expression": "11+12"})])
        self.assertFalse(resumed.requires_user_input)
        self.assertIsNone(resumed.pending_confirmation)

    def test_resume_after_plan_safety_confirmation_executes_planned_step_without_model(self):
        tool_manager = FileToolManager()
        executor = ReActExecutor(
            model_manager=None,
            tool_manager=tool_manager,
            tool_registry=_file_registry(),
        )
        step = PlanStep(
            id="step_1",
            task_id="task_1",
            description="Write file.",
            step_type="tool",
            tool_name="file_writer",
            args={"file_path": "out.txt", "content": "data"},
            requires_confirmation=True,
            output_key="written",
        )
        plan, _step = _plan_with_steps([step], available_tools=["file_writer"])
        context = executor._create_context(plan, task={}, user_input="write", history="")

        paused = executor._run_plan_precheck(context)
        resumed = executor.resume_after_confirmation(context, approved=True)

        self.assertIsNotNone(paused)
        self.assertEqual(paused.status, "waiting_user")
        self.assertTrue(resumed.success)
        self.assertEqual(resumed.status, "completed")
        self.assertEqual(resumed.step_statuses["step_1"], "completed")
        self.assertEqual(tool_manager.run_calls, [("file_writer", {"file_path": "out.txt", "content": "data"})])
        self.assertIsNone(resumed.pending_confirmation)

    def test_resume_after_plan_confirmation_enters_react_loop(self):
        model = SequenceModel(
            [
                json.dumps(
                    {
                        "action_type": "call_tool",
                        "action_target": "math_calculator",
                        "action_args": {"expression": "2+3"},
                    }
                )
            ]
        )
        executor = ReActExecutor(model_manager=model, tool_manager=self.tool_manager)
        plan, _step = _plan_with_steps([_tool_step("step_1")])
        plan.mode = "confirm"
        context = executor._create_context(plan, task={}, user_input="demo", history="")

        paused = executor._run_plan_precheck(context)
        resumed = executor.resume_after_confirmation(context, approved=True)

        self.assertIsNotNone(paused)
        self.assertEqual(paused.status, "waiting_user")
        self.assertTrue(resumed.success)
        self.assertEqual(resumed.status, "completed")
        self.assertEqual(resumed.step_statuses["step_1"], "completed")
        self.assertEqual(self.tool_manager.run_calls, [("math_calculator", {"expression": "2+3"})])
        self.assertEqual(len(model.generate_calls), 1)


def _tool_step(
    step_id: str,
    *,
    requires_confirmation: bool = False,
    depends_on: list[str] | None = None,
    input_from: list[str] | None = None,
    output_key: str | None = None,
) -> PlanStep:
    return PlanStep(
        id=step_id,
        task_id="task_1",
        description="Calculate.",
        step_type="tool",
        tool_name="math_calculator",
        args={"expression": "2+3"},
        requires_confirmation=requires_confirmation,
        depends_on=depends_on or [],
        input_from=input_from or [],
        output_key=output_key,
    )


def _plan_with_steps(steps: list[PlanStep], *, available_tools: list[str] | None = None) -> tuple[TaskPlan, PlanStep]:
    return (
        TaskPlan(
            goal="demo",
            mode="micro",
            steps=steps,
            task_units=[TaskUnit(id="task_1", title="Demo", step_ids=[step.id for step in steps])],
            available_tools=available_tools or ["math_calculator"],
            required_tools=available_tools or ["math_calculator"],
            can_execute=True,
            plan_validation_status="valid",
        ),
        steps[0],
    )


def _call_tool_packet(context, *, requires_confirmation: bool) -> ActionPacket:
    return ActionPacket(
        execution_id=context.execution_id,
        plan_id=context.plan_id,
        task_id="task_1",
        step_id="step_1",
        action_type="call_tool",
        action_target="math_calculator",
        action_args={"expression": "2+3"},
        requires_confirmation=requires_confirmation,
        confirmation_type="confirmation",
        user_visible_message="Run calculator?",
    )


def _event_types(context) -> list[str]:
    return [event.type for event in context.event_stream.events]


class FileToolManager:
    def __init__(self):
        self.run_calls = []

    def list_tools(self):
        return {"file_writer": "Fake file writer."}

    def run_tool(self, tool_name: str, **kwargs):
        self.run_calls.append((tool_name, kwargs))
        return ToolResult.ok(data={"written": True, "path": kwargs.get("file_path")}, message="written")

    def execute(self, request):
        return self.run_tool(request.tool_name, **request.args)


def _file_registry() -> ToolRegistry:
    return ToolRegistry(
        [
            ToolSpec(
                name="file_writer",
                description="Write file.",
                parameters_schema={
                    "type": "object",
                    "properties": {"file_path": {"type": "string"}, "content": {"type": "string"}},
                },
                required_params=["file_path", "content"],
                risk_level="medium",
                workspace_scope="write_workspace",
            )
        ]
    )


if __name__ == "__main__":
    unittest.main()
