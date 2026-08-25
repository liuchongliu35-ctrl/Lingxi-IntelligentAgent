from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.agent.planner import PlanStep, TaskPlan, TaskUnit
from src.agent.react_executor import ReActExecutor, SAFETY_BLOCKED_CODE
from src.agent.react_executor_config import ReActExecutorConfig, DEFAULT_REACT_EXECUTOR_CONFIG
from src.agent.react_executor_logging import ReActExecutorLogger
from src.agent.react_executor_protocol import ActionPacket
from src.tools.base import ToolResult
from src.tools.registry import ToolRegistry, ToolSpec


class FakeLoggingToolManager:
    def __init__(self):
        self.run_calls = []

    def list_tools(self):
        return {"math_calculator": "Fake calculator.", "file_writer": "Fake writer."}

    def run_tool(self, tool_name: str, **kwargs):
        self.run_calls.append((tool_name, kwargs))
        return ToolResult.ok(data={"value": 5, "token": "secret-token"}, message="5")


    def execute(self, request):
        return self.run_tool(request.tool_name, **request.args)
class FakeLoggingModelManager:
    def __init__(self):
        self.generate_calls = []

    def generate(self, prompt: str, **kwargs):
        self.generate_calls.append((prompt, kwargs))
        return "model output"


class SequenceLoggingModelManager:
    def __init__(self, responses):
        self.responses = list(responses)
        self.generate_calls = []

    def generate(self, prompt: str, **kwargs):
        self.generate_calls.append((prompt, kwargs))
        if self.responses:
            return self.responses.pop(0)
        return "model output"


class ReActExecutorLoggingTest(unittest.TestCase):
    def test_execute_writes_start_and_finish_jsonl_records(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            executor, log_path = _executor(tmp_dir, model_manager=FakeLoggingModelManager())
            result = executor.execute(_plan(_math_step()), task={}, user_input="calculate")

            records = _read_records(log_path)

        self.assertFalse(result.success)
        self.assertGreaterEqual(len(records), 2)
        self.assertEqual(records[0]["record_type"], "execution_started")
        self.assertEqual(records[-1]["record_type"], "execution_finished")
        self.assertEqual(records[-1]["execution_id"], result.execution_id)
        self.assertEqual(records[-1]["plan_id"], result.plan_id)
        self.assertIn("event_count", records[-1])
        loop_record = _first_record(records, "react_loop_started")
        self.assertEqual(loop_record["task_id"], "task_1")
        self.assertEqual(loop_record["step_id"], "step_1")
        self.assertIsNotNone(_first_record(records, "action_decision_prompt"))
        self.assertIsNotNone(_first_record(records, "model_action_output"))
        self.assertIsNotNone(_first_record(records, "checker_result"))
        self.assertIsNotNone(_first_record(records, "transition_decision"))

    def test_main_loop_logs_trace_chain_for_repair_and_replan(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            log_path = root / "logs" / "react_executor.log"
            logger = ReActExecutorLogger(log_path, keep_in_memory=True)
            executor = ReActExecutor(
                model_manager=SequenceLoggingModelManager(
                    [
                        "not json",
                        json.dumps(
                            {
                                "action_type": "request_replan",
                                "request_replan_reason": "tool contract changed",
                                "user_visible_message": "Need a revised plan.",
                            }
                        ),
                    ]
                ),
                tool_manager=FakeLoggingToolManager(),
                tool_registry=_registry(),
                config=_config(root, log_path),
                execution_logger=logger,
            )

            result = executor.execute(_plan(_math_step()), task={}, user_input="replan please")
            records = list(logger.in_memory_records)

        record_types = [record["record_type"] for record in records]
        self.assertTrue(result.request_replan)
        self.assertIn("action_packet_repair", record_types)
        self.assertIn("model_action_output", record_types)
        self.assertIn("action_packet", record_types)
        self.assertIn("observation", record_types)
        self.assertIn("checker_result", record_types)
        self.assertIn("transition_decision", record_types)
        self.assertEqual(records[0]["record_type"], "execution_started")
        self.assertEqual(records[-1]["record_type"], "execution_finished")
        trace_records = [
            record
            for record in records
            if record["record_type"] in {"model_prompt", "model_action_output", "action_packet_repair", "action_packet", "observation", "checker_result", "transition_decision", "execution_finished"}
        ]
        self.assertTrue(all(record["turn_id"] for record in trace_records))
        self.assertTrue(all(record["execution_id"] == result.execution_id for record in trace_records))
        self.assertTrue(all(record["plan_id"] == result.plan_id for record in trace_records))
        packet_record = _first_record(records, "action_packet")
        observation_record = _first_record(records, "observation")
        self.assertEqual(packet_record["packet_id"], observation_record["packet_id"])
        self.assertEqual(observation_record["observation_id"], result.observations[-1].observation_id)

    def test_dispatch_action_logs_action_packet_and_observation(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            executor, log_path = _executor(tmp_dir)
            plan = _plan(_math_step())
            context = executor._create_context(plan, task={}, user_input="calculate", history="")
            packet = ActionPacket(
                execution_id=context.execution_id,
                plan_id=context.plan_id,
                task_id="task_1",
                step_id="step_1",
                action_type="call_tool",
                action_target="math_calculator",
                action_args={"expression": "2+3", "api_key": "secret"},
            )

            observation = executor.dispatch_action(context, packet, step=plan.steps[0])
            records = _read_records(log_path)

        action_record = _first_record(records, "action_packet")
        observation_record = _first_record(records, "observation")
        self.assertTrue(observation.success)
        self.assertTrue(action_record["schema_valid"])
        self.assertEqual(action_record["packet_id"], packet.packet_id)
        self.assertEqual(action_record["metadata"]["action_args_summary"]["api_key"], "***REDACTED***")
        self.assertEqual(observation_record["observation_id"], observation.observation_id)
        self.assertEqual(observation_record["metadata"]["data_summary"]["token"], "***REDACTED***")

    def test_invalid_action_packet_logs_schema_errors(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            executor, log_path = _executor(tmp_dir)
            plan = _plan(_math_step())
            context = executor._create_context(plan, task={}, user_input="bad action", history="")
            packet = ActionPacket(
                execution_id=context.execution_id,
                plan_id=context.plan_id,
                task_id="task_1",
                step_id="step_1",
                action_type="call_model",
                action_args={"goal": "missing input fields"},
            )

            observation = executor.dispatch_action(context, packet, step=plan.steps[0])
            records = _read_records(log_path)

        action_record = _first_record(records, "action_packet")
        self.assertFalse(observation.success)
        self.assertFalse(action_record["schema_valid"])
        self.assertTrue(action_record["metadata"]["schema_errors"])

    def test_model_prompt_log_uses_summary_without_full_prompt_by_default(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            executor, log_path = _executor(tmp_dir, model_manager=FakeLoggingModelManager())
            plan = _plan(_math_step())
            context = executor._create_context(plan, task={}, user_input="summarize", history="history")
            packet = ActionPacket(
                execution_id=context.execution_id,
                plan_id=context.plan_id,
                task_id="task_1",
                step_id="step_1",
                action_type="call_model",
                action_args={
                    "goal": "summarize",
                    "input": "content",
                    "output_requirements": "short",
                    "prompt": "do not log full prompt",
                },
            )

            observation = executor.dispatch_action(context, packet, step=plan.steps[0])
            records = _read_records(log_path)

        prompt_record = _first_record(records, "model_prompt")
        self.assertTrue(observation.success)
        self.assertIn("prompt_length", prompt_record["metadata"])
        self.assertIn("prompt_summary", prompt_record["metadata"])
        self.assertNotIn("full_prompt", prompt_record["metadata"])

    def test_safety_block_logs_policy_record_and_does_not_call_tool(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            executor, log_path = _executor(tmp_dir)
            plan = _plan(_file_step("H:\\outside.txt"))
            context = executor._create_context(plan, task={}, user_input="unsafe", history="")
            packet = ActionPacket(
                execution_id=context.execution_id,
                plan_id=context.plan_id,
                task_id="task_1",
                step_id="step_1",
                action_type="call_tool",
                action_target="file_writer",
                action_args={"file_path": "H:\\outside.txt", "content": "data"},
            )

            observation = executor.dispatch_action(context, packet, step=plan.steps[0], confirmed=True)
            records = _read_records(log_path)

        safety_record = _first_record(records, "safety_decision")
        self.assertFalse(observation.success)
        self.assertEqual(observation.code, SAFETY_BLOCKED_CODE)
        self.assertEqual(safety_record["code"], SAFETY_BLOCKED_CODE)
        self.assertEqual(executor.tool_manager.run_calls, [])

    def test_log_write_failure_does_not_break_execution(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            blocked_path = root / "as_directory"
            blocked_path.mkdir()
            config = _config(root, blocked_path)
            logger = ReActExecutorLogger(blocked_path)
            executor = ReActExecutor(
                model_manager=None,
                tool_manager=FakeLoggingToolManager(),
                tool_registry=_registry(),
                config=config,
                execution_logger=logger,
            )
            plan = _plan(_math_step())
            context = executor._create_context(plan, task={}, user_input="calculate", history="")
            packet = ActionPacket(
                execution_id=context.execution_id,
                plan_id=context.plan_id,
                task_id="task_1",
                step_id="step_1",
                action_type="call_tool",
                action_target="math_calculator",
                action_args={"expression": "2+3"},
            )

            observation = executor.dispatch_action(context, packet, step=plan.steps[0])

        self.assertTrue(observation.success)
        self.assertGreater(logger.write_error_count, 0)


def _executor(tmp_dir: str, *, model_manager=None) -> tuple[ReActExecutor, Path]:
    root = Path(tmp_dir)
    log_path = root / "logs" / "react_executor.log"
    config = _config(root, log_path)
    executor = ReActExecutor(
        model_manager=model_manager,
        tool_manager=FakeLoggingToolManager(),
        tool_registry=_registry(),
        config=config,
        execution_logger=ReActExecutorLogger(log_path),
    )
    return executor, log_path


def _config(root: Path, log_path: Path) -> ReActExecutorConfig:
    values = dict(DEFAULT_REACT_EXECUTOR_CONFIG)
    values.update(
        {
            "workspace_root": str(root),
            "react_executor_log_path": str(log_path),
            "command_confirmation_policy": "low_risk_auto",
        }
    )
    return ReActExecutorConfig(root=root, react_executor_config=values)


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
            ToolSpec(
                name="file_writer",
                description="Write.",
                parameters_schema={"type": "object", "properties": {"file_path": {"type": "string"}, "content": {"type": "string"}}},
                required_params=["file_path", "content"],
                risk_level="medium",
                workspace_scope="write_workspace",
            ),
        ]
    )


def _plan(step: PlanStep) -> TaskPlan:
    return TaskPlan(
        goal="logging demo",
        mode="micro",
        steps=[step],
        task_units=[TaskUnit(id="task_1", title="Logging", step_ids=[step.id])],
        available_tools=["math_calculator", "file_writer"],
        required_tools=[step.tool_name] if step.tool_name else [],
        can_execute=True,
        plan_validation_status="valid",
    )


def _math_step() -> PlanStep:
    return PlanStep(
        id="step_1",
        task_id="task_1",
        description="Calculate.",
        step_type="tool",
        tool_name="math_calculator",
        args={"expression": "2+3"},
    )


def _file_step(file_path: str) -> PlanStep:
    return PlanStep(
        id="step_1",
        task_id="task_1",
        description="Write file.",
        step_type="tool",
        tool_name="file_writer",
        args={"file_path": file_path, "content": "data"},
    )


def _read_records(log_path: Path) -> list[dict]:
    return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _first_record(records: list[dict], record_type: str) -> dict:
    for record in records:
        if record["record_type"] == record_type:
            return record
    raise AssertionError(f"missing record_type={record_type}")


if __name__ == "__main__":
    unittest.main()
