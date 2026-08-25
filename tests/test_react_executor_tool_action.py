from __future__ import annotations

import json
import unittest

from src.agent.planner import PlanStep, TaskPlan, TaskUnit
from src.agent.react_executor import (
    TOOL_ARGUMENT_VALIDATION_FAILED_CODE,
    TOOL_EXECUTION_EXCEPTION_CODE,
    TOOL_INPUT_REF_MISSING_CODE,
    ReActExecutor,
)
from src.agent.react_executor_protocol import ActionPacket, ObservationPacket
from src.tools.base import ToolResult


class FakeModelManager:
    def __init__(self):
        self.generate_calls = 0

    def generate(self, prompt: str, **kwargs):
        self.generate_calls += 1
        return "{}"


class SequenceModelManager:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.generate_calls = []

    def generate(self, prompt: str, **kwargs):
        self.generate_calls.append((prompt, kwargs))
        if self.outputs:
            return self.outputs.pop(0)
        return "{}"


class FakeToolManager:
    def __init__(self):
        self.run_calls = []

    def list_tools(self):
        return {
            "math_calculator": "Fake calculator.",
            "document_parser": "Fake parser.",
            "text_processor": "Fake text processor.",
            "file_writer": "Fake writer.",
        }

    def run_tool(self, tool_name: str, **kwargs):
        self.run_calls.append((tool_name, kwargs))
        if kwargs.get("expression") == "raise":
            raise RuntimeError("tool exploded")
        if tool_name == "math_calculator":
            return ToolResult.ok(data="5", message="5")
        if tool_name == "text_processor":
            return ToolResult.ok(data=f"{kwargs.get('operation')}:{kwargs.get('text')}", message="processed")
        if tool_name == "file_writer":
            return ToolResult.fail("File already exists.", code="file_exists")
        return ToolResult.ok(data=kwargs, message="ok")

    def execute(self, request):
        return self.run_tool(request.tool_name, **request.args)


class ReActExecutorToolActionTest(unittest.TestCase):
    def setUp(self):
        self.model_manager = FakeModelManager()
        self.tool_manager = FakeToolManager()
        self.executor = ReActExecutor(model_manager=self.model_manager, tool_manager=self.tool_manager)

    def test_tool_success_returns_success_observation_and_events(self):
        plan, step = _plan_with_step(
            PlanStep(
                id="step_1",
                task_id="task_1",
                description="Calculate.",
                step_type="tool",
                tool_name="math_calculator",
                args={"expression": "2+3"},
                output_key="calculation",
            )
        )
        context = self.executor._create_context(plan, task={}, user_input="calculate", history="")
        packet = _packet(context, "call_tool", "math_calculator", {"expression": "2+3"})

        observation = self.executor.dispatch_action(context, packet, step=step)

        self.assertTrue(observation.success)
        self.assertIsNone(observation.code)
        self.assertEqual(observation.data, "5")
        self.assertEqual(observation.message, "5")
        self.assertEqual(observation.tool_name, "math_calculator")
        self.assertEqual(self.tool_manager.run_calls, [("math_calculator", {"expression": "2+3"})])
        self.assertIs(context.observation_store.get_by_output_key("calculation"), observation)
        self.assertIn("tool_started", _event_types(context))
        self.assertIn("tool_finished", _event_types(context))
        self.assertIn("observation_created", _event_types(context))

    def test_tool_failure_preserves_tool_result_code(self):
        plan, step = _plan_with_step(
            PlanStep(
                id="step_1",
                task_id="task_1",
                description="Write.",
                step_type="tool",
                tool_name="file_writer",
                args={"file_path": "out.txt", "content": "data"},
            )
        )
        context = self.executor._create_context(plan, task={}, user_input="write", history="")
        packet = _packet(context, "call_tool", "file_writer", {"file_path": "out.txt", "content": "data"})

        observation = self.executor.dispatch_action(context, packet, step=step)

        self.assertFalse(observation.success)
        self.assertEqual(observation.code, "file_exists")
        self.assertEqual(observation.error, "File already exists.")
        self.assertEqual(self.tool_manager.run_calls, [("file_writer", {"file_path": "out.txt", "content": "data"})])
        self.assertIn("tool_failed", _event_types(context))
        self.assertNotIn("tool_finished", _event_types(context))

    def test_missing_required_args_do_not_call_tool(self):
        plan, step = _plan_with_step(
            PlanStep(
                id="step_1",
                task_id="task_1",
                description="Read.",
                step_type="tool",
                tool_name="document_parser",
                args={},
            )
        )
        context = self.executor._create_context(plan, task={}, user_input="read", history="")
        packet = _packet(context, "call_tool", "document_parser", {})

        observation = self.executor.dispatch_action(context, packet, step=step)

        self.assertFalse(observation.success)
        self.assertEqual(observation.code, TOOL_ARGUMENT_VALIDATION_FAILED_CODE)
        self.assertIn("file_path is required", observation.error)
        self.assertEqual(self.tool_manager.run_calls, [])
        self.assertNotIn("tool_started", _event_types(context))

    def test_input_from_injects_previous_observation_into_tool_args(self):
        plan, step = _plan_with_step(
            PlanStep(
                id="step_2",
                task_id="task_1",
                description="Summarize.",
                step_type="tool",
                tool_name="text_processor",
                args={"operation": "summary"},
                input_from=["file_content"],
            )
        )
        context = self.executor._create_context(plan, task={}, user_input="summarize", history="")
        context.observation_store.add(
            ObservationPacket(
                execution_id=context.execution_id,
                plan_id=context.plan_id,
                task_id="task_1",
                step_id="step_1",
                action_type="call_tool",
                action_target="document_parser",
                tool_name="document_parser",
                success=True,
                data="hello world",
                message="hello world",
                model_consumable_observation="hello world",
            ),
            output_key="file_content",
        )
        packet = _packet(context, "call_tool", "text_processor", {"operation": "summary"})

        observation = self.executor.dispatch_action(context, packet, step=step)

        self.assertTrue(observation.success)
        self.assertEqual(
            self.tool_manager.run_calls,
            [("text_processor", {"operation": "summary", "text": "hello world"})],
        )
        self.assertEqual(observation.input_args["text"], "hello world")

    def test_missing_input_from_reference_does_not_call_tool(self):
        plan, step = _plan_with_step(
            PlanStep(
                id="step_2",
                task_id="task_1",
                description="Summarize.",
                step_type="tool",
                tool_name="text_processor",
                args={"operation": "summary"},
                input_from=["missing_output"],
            )
        )
        context = self.executor._create_context(plan, task={}, user_input="summarize", history="")
        packet = _packet(context, "call_tool", "text_processor", {"operation": "summary"})

        observation = self.executor.dispatch_action(context, packet, step=step)

        self.assertFalse(observation.success)
        self.assertEqual(observation.code, TOOL_INPUT_REF_MISSING_CODE)
        self.assertIn("missing_output", observation.error)
        self.assertEqual(self.tool_manager.run_calls, [])

    def test_tool_exception_returns_structured_failure(self):
        plan, step = _plan_with_step(
            PlanStep(
                id="step_1",
                task_id="task_1",
                description="Calculate.",
                step_type="tool",
                tool_name="math_calculator",
                args={"expression": "raise"},
            )
        )
        context = self.executor._create_context(plan, task={}, user_input="raise", history="")
        packet = _packet(context, "call_tool", "math_calculator", {"expression": "raise"})

        observation = self.executor.dispatch_action(context, packet, step=step)

        self.assertFalse(observation.success)
        self.assertEqual(observation.code, TOOL_EXECUTION_EXCEPTION_CODE)
        self.assertEqual(observation.error, "tool exploded")
        self.assertIn("tool_failed", _event_types(context))

    def test_action_args_override_planner_step_args(self):
        plan, step = _plan_with_step(
            PlanStep(
                id="step_1",
                task_id="task_1",
                description="Calculate.",
                step_type="tool",
                tool_name="math_calculator",
                args={"expression": "1+1"},
            )
        )
        context = self.executor._create_context(plan, task={}, user_input="calculate", history="")
        packet = _packet(context, "call_tool", "math_calculator", {"expression": "2+3"})

        self.executor.dispatch_action(context, packet, step=step)

        self.assertEqual(self.tool_manager.run_calls, [("math_calculator", {"expression": "2+3"})])

    def test_execute_single_step_tool_plan_completes_through_react_loop(self):
        model_manager = SequenceModelManager(
            [
                json.dumps(
                    {
                        "action_type": "call_tool",
                        "action_target": "math_calculator",
                        "action_args": {"expression": "2+3"},
                        "user_visible_message": "Calculating.",
                    }
                )
            ]
        )
        tool_manager = FakeToolManager()
        executor = ReActExecutor(model_manager=model_manager, tool_manager=tool_manager)
        plan, _step = _plan_with_step(
            PlanStep(
                id="step_1",
                task_id="task_1",
                description="Calculate.",
                step_type="tool",
                tool_name="math_calculator",
                args={"expression": "2+3"},
                output_key="calculation",
            )
        )

        result = executor.execute(plan, task={}, user_input="calculate")

        self.assertTrue(result.success)
        self.assertEqual(result.status, "completed")
        self.assertIsNone(result.error_code)
        self.assertEqual(result.step_statuses, {"step_1": "completed"})
        self.assertEqual(result.task_statuses, {"task_1": "completed"})
        self.assertEqual(tool_manager.run_calls, [("math_calculator", {"expression": "2+3"})])
        self.assertEqual(len(model_manager.generate_calls), 1)
        event_types = [event.type for event in result.events]
        self.assertIn("thought_visible", event_types)
        self.assertIn("action_selected", event_types)
        self.assertIn("tool_started", event_types)
        self.assertIn("observation_created", event_types)
        self.assertIn("step_completed", event_types)
        self.assertNotIn("react_action_loop_not_implemented", result.output)

    def test_execute_finish_action_completes_without_tool_call(self):
        model_manager = SequenceModelManager(
            [
                json.dumps(
                    {
                        "action_type": "finish",
                        "final_answer": "Done.",
                        "user_visible_message": "Finishing.",
                    }
                )
            ]
        )
        tool_manager = FakeToolManager()
        executor = ReActExecutor(model_manager=model_manager, tool_manager=tool_manager)
        plan, _step = _plan_with_step(
            PlanStep(
                id="step_1",
                task_id="task_1",
                description="Answer.",
                step_type="respond",
                tool_name=None,
            )
        )

        result = executor.execute(plan, task={}, user_input="answer")

        self.assertTrue(result.success)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.output.splitlines()[0], "Status: completed.")
        self.assertIn("Done.", result.output)
        self.assertEqual(tool_manager.run_calls, [])


def _plan_with_step(step: PlanStep) -> tuple[TaskPlan, PlanStep]:
    plan = TaskPlan(
        goal="demo",
        mode="micro",
        steps=[step],
        task_units=[TaskUnit(id="task_1", title="Demo", step_ids=[step.id])],
        available_tools=["math_calculator", "document_parser", "text_processor", "file_writer"],
        required_tools=[step.tool_name] if step.tool_name else [],
        can_execute=True,
        plan_validation_status="valid",
    )
    return plan, step


def _packet(context, action_type: str, action_target: str, action_args: dict) -> ActionPacket:
    return ActionPacket(
        execution_id=context.execution_id,
        plan_id=context.plan_id,
        task_id="task_1",
        step_id="step_1",
        action_type=action_type,
        action_target=action_target,
        action_args=action_args,
    )


def _event_types(context) -> list[str]:
    return [event.type for event in context.event_stream.events]


if __name__ == "__main__":
    unittest.main()
