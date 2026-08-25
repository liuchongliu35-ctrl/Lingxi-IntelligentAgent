from __future__ import annotations

import unittest

from src.agent.planner import PlanStep, TaskPlan, TaskUnit
from src.agent.react_executor import (
    ACTION_BLOCKED_CODE,
    ACTION_CANCELLED_CODE,
    ACTION_FAILED_CODE,
    ACTION_PACKET_INVALID_CODE,
    FALLBACK_TO_MODEL_NOT_IMPLEMENTED_CODE,
    FALLBACK_TO_TOOL_NOT_IMPLEMENTED_CODE,
    REQUEST_REPLAN_CODE,
    RETRY_NOT_IMPLEMENTED_CODE,
    STEP_SKIPPED_CODE,
    USER_INPUT_REQUIRED_CODE,
    ReActExecutor,
)
from src.agent.react_executor_protocol import ACTION_TYPES, ActionPacket
from src.tools.base import ToolResult


class FakeModelManager:
    def __init__(self):
        self.generate_calls = 0

    def generate(self, prompt: str, **kwargs):
        self.generate_calls += 1
        return "model output"


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
        return ToolResult.ok(data="5", message="5")


    def execute(self, request):
        return self.run_tool(request.tool_name, **request.args)
class ReActExecutorActionDispatcherTest(unittest.TestCase):
    def setUp(self):
        self.model_manager = FakeModelManager()
        self.tool_manager = FakeToolManager()
        self.executor = ReActExecutor(model_manager=self.model_manager, tool_manager=self.tool_manager)
        self.plan = _plan()
        self.context = self.executor._create_context(self.plan, task={}, user_input="demo", history="")
        self.step = self.plan.steps[0]

    def test_dispatcher_has_handler_for_every_action_type(self):
        self.assertEqual(set(self.executor._action_handlers()), ACTION_TYPES)

    def test_call_tool_is_routed_through_tool_manager(self):
        packet = self._packet("call_tool", action_target="math_calculator", action_args={"expression": "2+3"})

        observation = self.executor.dispatch_action(self.context, packet, step=self.step)

        self.assertTrue(observation.success)
        self.assertIsNone(observation.code)
        self.assertEqual(observation.tool_name, "math_calculator")
        self.assertEqual(self.tool_manager.run_calls, [("math_calculator", {"expression": "2+3"})])
        self.assertEqual(self.context.observation_store.observations, [observation])
        self.assertIn("action_selected", self.event_types())
        self.assertIn("tool_started", self.event_types())
        self.assertIn("tool_finished", self.event_types())
        self.assertIn("observation_created", self.event_types())
        self.assertEqual(self.model_manager.generate_calls, 0)

    def test_call_model_is_routed_through_model_manager(self):
        packet = self._packet(
            "call_model",
            action_args={
                "goal": "summarize",
                "input": "content",
                "output_requirements": "short answer",
            },
        )

        observation = self.executor.dispatch_action(self.context, packet, step=self.step)

        self.assertTrue(observation.success)
        self.assertIsNone(observation.code)
        self.assertEqual(observation.data, "model output")
        self.assertEqual(self.model_manager.generate_calls, 1)
        self.assertEqual(self.tool_manager.run_calls, [])

    def test_invalid_action_packet_returns_structured_observation(self):
        packet = self._packet("call_model", action_args={"goal": "missing required fields"})

        observation = self.executor.dispatch_action(self.context, packet, step=self.step)

        self.assertFalse(observation.success)
        self.assertEqual(observation.code, ACTION_PACKET_INVALID_CODE)
        self.assertIn("call_model requires", observation.error)
        self.assertEqual(len(self.context.observation_store.observations), 1)
        self.assert_no_model_or_tool_calls()

    def test_unknown_action_type_returns_structured_failure_without_throwing(self):
        packet = self._packet("fail", action_args={"reason": "placeholder"})
        packet.action_type = "unknown_action"

        observation = self.executor.dispatch_action(self.context, packet, step=self.step)

        self.assertFalse(observation.success)
        self.assertEqual(observation.action_type, "fail")
        self.assertEqual(observation.code, ACTION_PACKET_INVALID_CODE)
        self.assertIn("Unsupported action_type", observation.error)
        self.assert_no_model_or_tool_calls()

    def test_finish_action_returns_success_observation(self):
        packet = self._packet("finish", final_answer="done")

        observation = self.executor.dispatch_action(self.context, packet, step=self.step)

        self.assertTrue(observation.success)
        self.assertIsNone(observation.code)
        self.assertEqual(observation.data["final_answer"], "done")
        self.assertEqual(observation.checker_result["execution_status"], "completed")

    def test_request_replan_action_emits_request_replan_event(self):
        packet = self._packet("request_replan", request_replan_reason="plan is stale")

        observation = self.executor.dispatch_action(self.context, packet, step=self.step)

        self.assertFalse(observation.success)
        self.assertEqual(observation.code, REQUEST_REPLAN_CODE)
        self.assertIn("request_replan", self.event_types())

    def test_control_actions_return_explicit_structured_results(self):
        cases = [
            ("ask_user", USER_INPUT_REQUIRED_CODE, {"ask_type": "clarification", "question": "Which file?"}, False),
            ("retry_step", RETRY_NOT_IMPLEMENTED_CODE, {"step_id": "step_1"}, False),
            ("fallback_to_model", FALLBACK_TO_MODEL_NOT_IMPLEMENTED_CODE, {"fallback_reason": "tool failed"}, False),
            ("fallback_to_tool", FALLBACK_TO_TOOL_NOT_IMPLEMENTED_CODE, {"fallback_reason": "primary failed"}, False),
            ("skip_step", STEP_SKIPPED_CODE, {"reason": "not needed"}, True),
            ("fail", ACTION_FAILED_CODE, {"reason": "failed by model"}, False),
            ("blocked", ACTION_BLOCKED_CODE, {"reason": "blocked by policy"}, False),
            ("cancel", ACTION_CANCELLED_CODE, {"reason": "cancelled by user"}, False),
        ]
        for action_type, expected_code, action_args, expected_success in cases:
            with self.subTest(action_type=action_type):
                action_target = "file_writer" if action_type == "fallback_to_tool" else None
                packet = self._packet(action_type, action_target=action_target, action_args=action_args)

                observation = self.executor.dispatch_action(self.context, packet, step=self.step)

                self.assertEqual(observation.success, expected_success)
                self.assertEqual(observation.code, expected_code)

        self.assertEqual(len(self.context.observation_store.observations), len(cases))
        self.assert_no_model_or_tool_calls()

    def test_observation_can_be_indexed_by_output_key(self):
        packet = self._packet("finish", final_answer="stored")

        observation = self.executor.dispatch_action(self.context, packet, step=self.step, output_key="final")

        self.assertIs(self.context.observation_store.get_by_output_key("final"), observation)

    def _packet(self, action_type: str, **kwargs) -> ActionPacket:
        defaults = {
            "execution_id": self.context.execution_id,
            "plan_id": self.context.plan_id,
            "task_id": "task_1",
            "step_id": "step_1",
            "action_args": {},
        }
        defaults.update(kwargs)
        return ActionPacket(action_type=action_type, **defaults)

    def event_types(self):
        return [event.type for event in self.context.event_stream.events]

    def assert_no_model_or_tool_calls(self):
        self.assertEqual(self.model_manager.generate_calls, 0)
        self.assertEqual(self.tool_manager.run_calls, [])


def _plan() -> TaskPlan:
    step = PlanStep(
        id="step_1",
        task_id="task_1",
        description="Calculate.",
        step_type="tool",
        tool_name="math_calculator",
        args={"expression": "2+3"},
        fallback_tools=["file_writer"],
        output_key="calculation",
    )
    return TaskPlan(
        goal="demo",
        mode="micro",
        steps=[step],
        task_units=[TaskUnit(id="task_1", title="Demo", step_ids=["step_1"])],
        available_tools=["math_calculator", "file_writer"],
        required_tools=["math_calculator"],
        can_execute=True,
        plan_validation_status="valid",
    )


if __name__ == "__main__":
    unittest.main()
