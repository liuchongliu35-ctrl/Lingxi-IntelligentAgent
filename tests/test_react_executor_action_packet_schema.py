from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.agent.planner import PlanStep, TaskPlan, TaskUnit
from src.agent.react_executor import ACTION_PACKET_INVALID_CODE, ReActExecutor
from src.agent.react_executor_config import DEFAULT_REACT_EXECUTOR_CONFIG, ReActExecutorConfig
from src.agent.react_executor_logging import ReActExecutorLogger
from src.agent.react_executor_protocol import (
    ActionPacketParseResult,
    extract_action_packet_payload,
    parse_action_packet,
    validate_action_packet,
)
from src.tools.registry import ToolRegistry, ToolSpec


class ReActExecutorActionPacketSchemaTest(unittest.TestCase):
    def test_schema_file_is_valid_json_and_exposes_canonical_actions(self):
        path = Path("config/react_executor/action_packet_schema.json")
        payload = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(payload["title"], "ReActExecutor ActionPacket")
        self.assertIn("action_type", payload["required"])
        self.assertIn("retry_step", payload["properties"]["action_type"]["enum"])
        self.assertIn("finish", payload["properties"]["action_type"]["enum"])
        self.assertNotIn("stop_success", payload["properties"]["action_type"]["enum"])

    def test_parse_dict_response_into_action_packet(self):
        result = parse_action_packet(
            {
                "action_type": "call_tool",
                "action_target": "math_calculator",
                "action_args": {"expression": "2+3"},
                "confidence": 1.5,
            },
            execution_id="exec_1",
            plan_id="plan_1",
            task_id="task_1",
            step_id="step_1",
            available_tools=["math_calculator"],
        )

        self.assertTrue(result.success)
        self.assertIsNotNone(result.packet)
        self.assertEqual(result.packet.action_type, "call_tool")
        self.assertEqual(result.packet.confidence, 1.0)
        self.assertEqual(result.packet.execution_id, "exec_1")
        self.assertFalse(result.needs_repair)

    def test_parse_plain_json_string(self):
        raw = json.dumps(
            {
                "action_type": "finish",
                "final_answer": "Done.",
                "confidence": 0.8,
            }
        )

        result = parse_action_packet(raw)

        self.assertTrue(result.success)
        self.assertEqual(result.packet.action_type, "finish")
        self.assertEqual(result.packet.final_answer, "Done.")

    def test_parse_markdown_fenced_json(self):
        raw = """```json
{"action_type": "request_replan", "request_replan_reason": "Plan no longer matches observation."}
```"""

        result = parse_action_packet(raw)

        self.assertTrue(result.success)
        self.assertEqual(result.packet.action_type, "request_replan")
        self.assertIn("Plan no longer", result.packet.request_replan_reason)

    def test_extract_first_json_object_from_explanatory_text(self):
        raw = 'I will return JSON now: {"action_type": "finish", "final_answer": "ok"} thanks.'

        payload = extract_action_packet_payload(raw)
        result = parse_action_packet(raw)

        self.assertEqual(payload["action_type"], "finish")
        self.assertTrue(result.success)
        self.assertEqual(result.packet.final_answer, "ok")

    def test_non_json_output_requests_repair(self):
        result = parse_action_packet("I should call the calculator now.")

        self.assertFalse(result.success)
        self.assertTrue(result.needs_repair)
        self.assertIsNone(result.packet)
        self.assertIn("no JSON object found", result.errors[0])
        self.assertIn("Return only one strict JSON object", result.repair_prompt)

    def test_unknown_action_type_requests_repair_and_will_not_execute(self):
        result = parse_action_packet('{"action_type": "invent_tool", "action_args": {}}')

        self.assertFalse(result.success)
        self.assertTrue(result.needs_repair)
        self.assertIsNone(result.packet)
        self.assertIn("Unsupported action_type", result.errors[0])

    def test_missing_action_type_requests_repair(self):
        result = parse_action_packet('{"action_args": {}}')

        self.assertFalse(result.success)
        self.assertTrue(result.needs_repair)
        self.assertIn("ActionPacket requires action_type", result.errors)

    def test_action_args_must_be_object(self):
        result = parse_action_packet('{"action_type": "call_tool", "action_args": "bad"}')

        self.assertFalse(result.success)
        self.assertTrue(result.needs_repair)
        self.assertIn("action_args must be an object", result.errors)

    def test_final_answer_is_only_allowed_for_finish_or_fail(self):
        result = parse_action_packet(
            {
                "action_type": "call_tool",
                "action_target": "math_calculator",
                "action_args": {"expression": "1+1"},
                "final_answer": "2",
            },
            available_tools=["math_calculator"],
        )

        self.assertFalse(result.success)
        self.assertIsNotNone(result.packet)
        self.assertIn("final_answer is only allowed for finish or fail", result.errors)

    def test_call_tool_requires_available_tool_when_registry_context_exists(self):
        result = parse_action_packet(
            {
                "action_type": "call_tool",
                "action_target": "missing_tool",
                "action_args": {},
            },
            available_tools=["math_calculator"],
        )

        self.assertFalse(result.success)
        self.assertIn("call_tool target is not available: missing_tool", result.errors)

    def test_ask_user_requires_valid_ask_type_and_question(self):
        invalid = parse_action_packet({"action_type": "ask_user", "action_args": {"ask_type": "unknown"}})
        valid = parse_action_packet(
            {
                "action_type": "ask_user",
                "action_args": {"ask_type": "missing_info", "question": "Which file?"},
            }
        )

        self.assertFalse(invalid.success)
        self.assertIn("ask_user requires action_args.ask_type to be valid", invalid.errors)
        self.assertIn("ask_user requires action_args.question or action_args.message", invalid.errors)
        self.assertTrue(valid.success)

    def test_request_replan_requires_reason(self):
        result = parse_action_packet({"action_type": "request_replan"})

        self.assertFalse(result.success)
        self.assertIn("request_replan requires request_replan_reason", result.errors)

    def test_retry_step_validation_uses_current_step_and_retry_limit(self):
        wrong_step = parse_action_packet(
            {"action_type": "retry_step", "action_args": {"step_id": "step_2"}},
            current_step_id="step_1",
            retry_attempts=0,
            max_retries=3,
        )
        too_many = parse_action_packet(
            {"action_type": "retry_step", "action_args": {"step_id": "step_1"}},
            current_step_id="step_1",
            retry_attempts=3,
            max_retries=3,
        )

        self.assertFalse(wrong_step.success)
        self.assertIn("retry_step must target the current step", wrong_step.errors)
        self.assertFalse(too_many.success)
        self.assertIn("retry_step exceeds max_retries", too_many.errors)

    def test_fallback_to_tool_requires_known_fallback_tool_and_reason(self):
        result = parse_action_packet(
            {
                "action_type": "fallback_to_tool",
                "action_target": "shell_tool",
                "action_args": {},
            },
            fallback_tools=["search_tool"],
        )

        self.assertFalse(result.success)
        self.assertIn("fallback_to_tool target is not available: shell_tool", result.errors)
        self.assertIn("fallback_to_tool requires fallback_reason", result.errors)

    def test_parse_result_to_dict_is_json_serializable(self):
        result = parse_action_packet({"action_type": "finish", "final_answer": "Done."})

        self.assertIsInstance(result, ActionPacketParseResult)
        json.dumps(result.to_dict(), ensure_ascii=False)

    def test_validate_action_packet_can_be_used_directly(self):
        result = parse_action_packet({"action_type": "finish", "final_answer": "Done."})

        self.assertEqual(validate_action_packet(result.packet), [])

    def test_executor_repairs_invalid_model_output_then_executes_single_step(self):
        model = SequenceModel(
            [
                "I should call the calculator.",
                json.dumps(
                    {
                        "action_type": "call_tool",
                        "action_target": "math_calculator",
                        "action_args": {"expression": "2+3"},
                        "user_visible_message": "Calculating.",
                    }
                ),
            ]
        )
        tool_manager = RecordingToolManager()
        executor = ReActExecutor(model_manager=model, tool_manager=tool_manager, tool_registry=_registry())

        result = executor.execute(_plan(), task=_task(), user_input="calculate 2+3")

        self.assertTrue(result.success)
        self.assertEqual(result.status, "completed")
        self.assertIsNone(result.error_code)
        self.assertEqual(len(model.generate_calls), 2)
        self.assertIn("mixed prose", model.generate_calls[1])
        self.assertEqual(tool_manager.run_calls, [("math_calculator", {"expression": "2+3"})])
        self.assertEqual(len(result.observations), 1)
        self.assertIn("thought_visible", [event.type for event in result.events])

    def test_executor_repair_exhausted_returns_structured_failure_without_tool_execution(self):
        model = SequenceModel(["not json", "{}"])
        tool_manager = RecordingToolManager()
        with tempfile.TemporaryDirectory() as tmp_dir:
            executor = ReActExecutor(
                model_manager=model,
                tool_manager=tool_manager,
                tool_registry=_registry(),
                config=_config(Path(tmp_dir), max_repairs=1),
            )

            result = executor.execute(_plan(), task=_task(), user_input="calculate 2+3")

        self.assertFalse(result.success)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_code, ACTION_PACKET_INVALID_CODE)
        self.assertEqual(len(model.generate_calls), 2)
        self.assertEqual(tool_manager.run_calls, [])
        self.assertEqual(len(result.observations), 1)
        self.assertEqual(result.observations[0].code, ACTION_PACKET_INVALID_CODE)
        self.assertIn("action_packet_invalid", result.summary)

    def test_executor_does_not_accept_mixed_natural_language_as_action_without_repair(self):
        model = SequenceModel(
            [
                'I will do this: {"action_type": "call_tool", "action_target": "math_calculator", "action_args": {"expression": "2+3"}}',
                '{"action_type": "finish", "final_answer": "Done."}',
            ]
        )
        executor = ReActExecutor(model_manager=model, tool_manager=RecordingToolManager(), tool_registry=_registry())

        result = executor.execute(_plan(), task=_task(), user_input="calculate 2+3")

        self.assertTrue(result.success)
        self.assertEqual(result.status, "completed")
        self.assertIsNone(result.error_code)
        self.assertEqual(len(model.generate_calls), 2)
        self.assertIn("mixed prose is not allowed", model.generate_calls[1])

class SequenceModel:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.generate_calls = []

    def generate(self, prompt: str, **kwargs):
        self.generate_calls.append(prompt)
        if self.outputs:
            return self.outputs.pop(0)
        return "{}"


class RecordingToolManager:
    def __init__(self):
        self.run_calls = []

    def list_tools(self):
        return {"math_calculator": "Calculate expressions."}

    def run_tool(self, tool_name: str, **kwargs):
        self.run_calls.append((tool_name, kwargs))
        return None

    def execute(self, request):
        return self.run_tool(request.tool_name, **request.args)


def _registry() -> ToolRegistry:
    return ToolRegistry(
        [
            ToolSpec(
                name="math_calculator",
                description="Calculate expressions.",
                parameters_schema={"type": "object", "properties": {"expression": {"type": "string"}}},
                required_params=["expression"],
            )
        ]
    )


def _plan() -> TaskPlan:
    step = PlanStep(
        id="step_1",
        task_id="task_1",
        description="Calculate.",
        step_type="tool",
        tool_name="math_calculator",
        args={"expression": "2+3"},
    )
    return TaskPlan(
        goal="calculate",
        mode="micro",
        steps=[step],
        task_units=[TaskUnit(id="task_1", title="Calculate", step_ids=["step_1"])],
        available_tools=["math_calculator"],
        required_tools=["math_calculator"],
    )


def _task():
    return {"action_policy": "allow"}


def _config(root: Path, *, max_repairs: int) -> ReActExecutorConfig:
    values = dict(DEFAULT_REACT_EXECUTOR_CONFIG)
    values.update(
        {
            "workspace_root": str(root),
            "react_executor_log_path": str(root / "logs" / "react_executor_action_packet.log"),
            "max_action_packet_repair_attempts": max_repairs,
        }
    )
    return ReActExecutorConfig(root=root, react_executor_config=values)


if __name__ == "__main__":
    unittest.main()
