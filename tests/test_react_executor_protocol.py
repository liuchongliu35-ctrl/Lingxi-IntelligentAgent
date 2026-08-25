from __future__ import annotations

import json
import unittest

from src.agent.react_executor_protocol import (
    ACTION_TYPES,
    ActionPacket,
    CommandAction,
    ExecutionEvent,
    ExecutionResult,
    ObservationPacket,
    PendingConfirmation,
    ReActLoopState,
    ReActTurnState,
    StepRuntimeState,
    TaskUnitRuntimeState,
    normalize_action_type,
)


class ReActExecutorProtocolTest(unittest.TestCase):
    def test_action_type_aliases_are_normalized(self):
        self.assertEqual(normalize_action_type("retry"), "retry_step")
        self.assertEqual(normalize_action_type("stop_success"), "finish")
        self.assertEqual(normalize_action_type("stop_failed"), "fail")

        packet = ActionPacket(action_type="retry", confidence=1.7)

        self.assertEqual(packet.action_type, "retry_step")
        self.assertEqual(packet.confidence, 1.0)
        self.assertIn(packet.to_dict()["action_type"], ACTION_TYPES)

    def test_unknown_action_type_is_rejected(self):
        with self.assertRaises(ValueError):
            ActionPacket(action_type="invent_tool")

    def test_action_packet_has_stable_defaults_and_json_serializes(self):
        packet = ActionPacket(
            execution_id="exec_1",
            plan_id="plan_1",
            task_id="task_1",
            step_id="step_1",
            action_type="call_tool",
            action_target="math_calculator",
            action_args={"expression": "2+3"},
            thought_summary="Use calculator.",
            user_visible_message="I will calculate the expression.",
            expected_observation="Calculation result",
            confidence=-1,
            raw_model_output=object(),
        )

        payload = packet.to_dict()
        encoded = json.dumps(payload, ensure_ascii=False)

        self.assertEqual(payload["confidence"], 0.0)
        self.assertEqual(payload["action_args"], {"expression": "2+3"})
        self.assertIn("raw_model_output", payload)
        self.assertIsInstance(encoded, str)

    def test_observation_packet_json_serializes_and_normalizes_attempts(self):
        observation = ObservationPacket(
            execution_id="exec_1",
            plan_id="plan_1",
            task_id="task_1",
            step_id="step_1",
            packet_id="action_1",
            attempt=0,
            action_type="call_tool",
            action_target="math_calculator",
            tool_name="math_calculator",
            input_args={"expression": "2+3"},
            success=True,
            data={"result": 5},
            message="5",
            raw_observation={"success": True, "data": {"result": 5}},
            model_consumable_observation={"result": 5},
            duration_ms=-10,
        )

        payload = observation.to_dict()

        self.assertEqual(payload["attempt"], 1)
        self.assertEqual(payload["duration_ms"], 0)
        self.assertTrue(payload["visible_to_user"])
        json.dumps(payload, ensure_ascii=False)

    def test_execution_event_validates_type(self):
        event = ExecutionEvent(
            execution_id="exec_1",
            plan_id="plan_1",
            task_id="task_1",
            step_id="step_1",
            type="step_started",
            message="Starting step.",
            payload={"description": "Read file"},
        )

        self.assertEqual(event.to_dict()["type"], "step_started")
        json.dumps(event.to_dict(), ensure_ascii=False)

        with self.assertRaises(ValueError):
            ExecutionEvent(execution_id="exec_1", plan_id="plan_1", type="debug_dump")

    def test_pending_confirmation_serializes_nested_action(self):
        action = ActionPacket(
            execution_id="exec_1",
            plan_id="plan_1",
            step_id="step_1",
            action_type="call_tool",
            action_target="file_writer",
            requires_confirmation=True,
            confirmation_type="confirmation",
        )
        pending = PendingConfirmation(
            execution_id="exec_1",
            plan_id="plan_1",
            task_id="task_1",
            step_id="step_1",
            confirmation_type="confirmation",
            confirmation_message="Confirm file write?",
            pending_action=action,
        )

        payload = pending.to_dict()

        self.assertEqual(payload["pending_action"]["action_type"], "call_tool")
        self.assertTrue(payload["pending_action"]["requires_confirmation"])
        json.dumps(payload, ensure_ascii=False)

    def test_runtime_state_status_validation(self):
        step_state = StepRuntimeState(step_id="step_1", status="completed", attempts=2)
        task_state = TaskUnitRuntimeState(
            task_id="task_1",
            status="completed",
            step_statuses={"step_1": "completed"},
        )

        self.assertEqual(step_state.to_dict()["status"], "completed")
        self.assertEqual(task_state.to_dict()["step_statuses"]["step_1"], "completed")

        with self.assertRaises(ValueError):
            StepRuntimeState(step_id="step_2", status="done")
        with self.assertRaises(ValueError):
            TaskUnitRuntimeState(task_id="task_1", step_statuses={"step_1": "done"})

    def test_react_turn_and_loop_state_serialize_for_model_context(self):
        action = ActionPacket(
            execution_id="exec_1",
            plan_id="plan_1",
            task_id="task_1",
            step_id="step_1",
            action_type="call_tool",
            action_target="math_calculator",
            action_args={"expression": "2+3", "api_key": "secret"},
            raw_model_output="raw output should not enter model context",
        )
        observation = ObservationPacket(
            execution_id="exec_1",
            plan_id="plan_1",
            task_id="task_1",
            step_id="step_1",
            packet_id=action.packet_id,
            action_type="call_tool",
            action_target="math_calculator",
            success=True,
            message="5",
            raw_observation={"secret": "raw"},
            model_consumable_observation={"result": 5},
        )
        turn = ReActTurnState(
            execution_turn=1,
            step_turn=1,
            task_id="task_1",
            step_id="step_1",
            previous_action=action,
            previous_observation=observation,
            last_checker_result={"step_status": "completed"},
            status="running",
            thought_summary="Use calculator.",
            user_visible_message="Calculating.",
        )
        turn.finish("completed")
        loop = ReActLoopState(execution_id="exec_1", plan_id="plan_1", max_execution_turns=5, max_step_turns=3)
        loop.record_action(action)
        loop.record_observation(observation)
        loop.record_checker_result({"step_status": "completed"})
        created_turn = loop.start_turn(task_id="task_1", step_id="step_1", attempt=2)
        created_turn.finish("completed")
        loop.finish("completed")

        turn_context = turn.to_model_context()
        loop_context = loop.to_model_context()

        self.assertEqual(turn_context["previous_action"]["action_type"], "call_tool")
        self.assertNotIn("action_args", turn_context["previous_action"])
        self.assertEqual(turn_context["previous_observation"]["model_consumable_observation"], {"result": 5})
        self.assertNotIn("raw_observation", turn_context["previous_observation"])
        self.assertEqual(loop_context["execution_turn"], 1)
        self.assertEqual(loop_context["step_turns"], {"step_1": 1})
        self.assertEqual(loop_context["recent_turns"][0]["attempt"], 2)
        json.dumps(turn.to_dict(), ensure_ascii=False)
        json.dumps(loop.to_dict(), ensure_ascii=False)

        with self.assertRaises(ValueError):
            ReActTurnState(status="done")
        with self.assertRaises(ValueError):
            ReActLoopState(execution_id="exec_1", plan_id="plan_1", status="done")

    def test_command_action_normalizes_risk_and_timeout(self):
        command = CommandAction(command="python -B -m unittest", risk_level="custom", timeout_seconds=0)

        payload = command.to_dict()

        self.assertEqual(payload["risk_level"], "unknown")
        self.assertEqual(payload["timeout_seconds"], 1)
        self.assertTrue(payload["requires_confirmation"])
        json.dumps(payload, ensure_ascii=False)

    def test_execution_result_serializes_nested_protocol_objects(self):
        observation = ObservationPacket(
            execution_id="exec_1",
            plan_id="plan_1",
            action_type="call_model",
            success=True,
            message="intermediate result",
        )
        event = ExecutionEvent(
            execution_id="exec_1",
            plan_id="plan_1",
            type="final_answer",
            message="Done.",
        )
        result = ExecutionResult(
            execution_id="exec_1",
            plan_id="plan_1",
            source_trace_id="trace_1",
            status="completed",
            success=True,
            output="Done.",
            summary="Completed one model step.",
            task_statuses={"task_1": "completed"},
            step_statuses={"step_1": "completed"},
            observations=[observation],
            events=[event],
        )

        payload = result.to_dict()

        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["observations"][0]["message"], "intermediate result")
        self.assertEqual(payload["events"][0]["type"], "final_answer")
        json.dumps(payload, ensure_ascii=False)

        with self.assertRaises(ValueError):
            ExecutionResult(execution_id="exec_2", plan_id="plan_2", status="done", success=True)


if __name__ == "__main__":
    unittest.main()
