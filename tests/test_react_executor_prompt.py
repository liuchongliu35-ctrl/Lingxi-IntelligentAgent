from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.agent.planner import PlanStep, TaskPlan, TaskUnit
from src.agent.react_executor import ReActExecutor
from src.agent.react_executor_config import DEFAULT_REACT_EXECUTOR_CONFIG, ReActExecutorConfig
from src.agent.react_executor_logging import ReActExecutorLogger
from src.agent.react_executor_protocol import ActionPacket, ObservationPacket
from src.agent.react_executor_prompt import (
    ReActPromptContext,
    build_prompt_log_summary,
    build_react_executor_prompt,
    load_action_packet_schema,
    load_react_executor_model_prompts,
)
from src.models import ModelCallResult, MockModel
from src.tools.registry import ToolRegistry, ToolSpec


class ReActExecutorPromptTest(unittest.TestCase):
    def tearDown(self):
        load_react_executor_model_prompts.cache_clear()
        load_action_packet_schema.cache_clear()

    def test_model_prompt_config_file_is_valid_json(self):
        path = Path("config/react_executor/model_prompts.json")
        payload = json.loads(path.read_text(encoding="utf-8"))

        self.assertIn("system_instruction", payload)
        self.assertIn("output_contract", payload)
        self.assertIn("safety_rules", payload)
        self.assertIn("max_context_chars", payload)

    def test_prompt_contains_current_step_tools_schema_and_safety_rules(self):
        step = PlanStep(
            id="step_1",
            task_id="task_1",
            step_type="tool",
            description="Calculate expression.",
            tool_name="math_calculator",
            args={"expression": "2+3"},
            expected_output="number",
        )
        task_unit = TaskUnit(id="task_1", title="Calculate", step_ids=["step_1"])
        plan = TaskPlan(goal="calculate 2+3", mode="micro", steps=[step], task_units=[task_unit])
        context = ReActPromptContext(
            user_input="calculate 2+3",
            analyzer_summary={"intent_sequence": ["calculate"], "execution_strategy": "micro"},
            task_plan=plan,
            current_task_unit=task_unit,
            current_step=step,
            available_tools={"math_calculator": {"description": "Calculate expressions."}},
            execution_progress={"step_statuses": {"step_1": "running"}},
            history_summary="User asked for a calculation.",
        )

        prompt = build_react_executor_prompt(context)

        self.assertIn("# System Instruction", prompt)
        self.assertIn("# ActionPacket JSON Schema", prompt)
        self.assertIn("action_type", prompt)
        self.assertIn("call_tool", prompt)
        self.assertIn("finish", prompt)
        self.assertIn("Do not invent tool names", prompt)
        self.assertIn("Calculate expression.", prompt)
        self.assertIn("math_calculator", prompt)
        self.assertIn("calculate 2+3", prompt)

    def test_prompt_includes_previous_action_and_model_consumable_observation(self):
        previous_action = ActionPacket(
            execution_id="exec_1",
            plan_id="plan_1",
            task_id="task_1",
            step_id="step_1",
            action_type="call_tool",
            action_target="search_tool",
            action_args={"query": "FastAPI tests"},
        )
        previous_observation = ObservationPacket(
            execution_id="exec_1",
            plan_id="plan_1",
            task_id="task_1",
            step_id="step_1",
            action_type="call_tool",
            success=True,
            message="Search complete.",
            model_consumable_observation={"top_result": "Use unittest or pytest."},
        )
        context = ReActPromptContext(
            user_input="summarize testing advice",
            current_step={"id": "step_2", "description": "Summarize results."},
            previous_action=previous_action,
            previous_observation=previous_observation,
        )

        prompt = build_react_executor_prompt(context)

        self.assertIn("previous_action", prompt)
        self.assertIn("search_tool", prompt)
        self.assertIn("previous_observation", prompt)
        self.assertIn("Use unittest or pytest", prompt)

    def test_prompt_truncates_long_observation_and_history(self):
        long_text = "x" * 5000
        context = ReActPromptContext(
            user_input="continue",
            current_step={"id": "step_1"},
            previous_observation={"data": long_text},
            history_summary=long_text,
        )

        prompt = build_react_executor_prompt(
            context,
            prompt_config={
                "system_instruction": "Return JSON.",
                "output_contract": [],
                "safety_rules": [],
                "max_context_chars": 1200,
                "max_observation_chars": 100,
                "max_history_chars": 80,
            },
        )

        self.assertIn('"truncated": true', prompt)
        self.assertIn("original_chars", prompt)
        self.assertIn("[truncated", prompt)
        self.assertLess(len(prompt), 3500)

    def test_prompt_log_summary_does_not_store_full_prompt(self):
        prompt = "line\n" + ("x" * 1000)

        summary = build_prompt_log_summary(prompt)

        self.assertEqual(summary["prompt_length"], len(prompt))
        self.assertLessEqual(len(summary["prompt_preview"]), 330)
        self.assertNotIn("\n", summary["prompt_preview"])

    def test_missing_prompt_config_uses_defaults(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = load_react_executor_model_prompts(tmp_dir)

            self.assertIn("system_instruction", config)
            self.assertTrue(config["output_contract"])
            self.assertTrue(config["safety_rules"])

    def test_prompt_is_compatible_with_mock_model(self):
        context = ReActPromptContext(
            user_input="answer without tools",
            analyzer_summary={"mode": "chat"},
            current_step={"id": "step_1", "step_type": "respond"},
        )
        prompt = build_react_executor_prompt(context)

        response = MockModel().generate(prompt)

        self.assertIsInstance(response, ModelCallResult)
        self.assertTrue(response.success)
        self.assertIn("MockModel", response.content)

    def test_executor_builds_action_decision_prompt_from_runtime_context(self):
        executor = ReActExecutor(model_manager=RecordingModel(), tool_manager=None, tool_registry=_registry())
        step_1 = PlanStep(
            id="step_1",
            task_id="task_1",
            description="Calculate expression.",
            step_type="tool",
            tool_name="math_calculator",
            args={"expression": "2+3", "api_key": "secret"},
            output_key="calculation",
            requires_confirmation=False,
        )
        step_2 = PlanStep(
            id="step_2",
            task_id="task_1",
            description="Write result.",
            step_type="tool",
            tool_name="file_writer",
            args={"file_path": "out.txt", "content": "placeholder"},
            depends_on=["step_1"],
            input_from=["calculation"],
            on_failure="stop",
            requires_confirmation=True,
        )
        task_unit = TaskUnit(id="task_1", title="Calculate and write", step_ids=["step_1", "step_2"])
        plan = TaskPlan(
            goal="calculate and write",
            mode="micro",
            steps=[step_1, step_2],
            task_units=[task_unit],
            available_tools=["math_calculator", "file_writer", "unknown_planner_tool"],
            required_tools=["math_calculator", "file_writer"],
        )
        context = executor._create_context(
            plan,
            task=SimpleNamespace(
                trace_id="trace_1",
                intent_sequence=["calculate", "write_file"],
                parameters={"expression": "2+3", "api_key": "secret"},
                risk_flags=[],
                action_policy="allow",
                execution_strategy="micro",
            ),
            user_input="calculate 2+3 and write it",
            history="previous conversation",
        )
        action = ActionPacket(
            execution_id=context.execution_id,
            plan_id=context.plan_id,
            task_id="task_1",
            step_id="step_1",
            action_type="call_tool",
            action_target="math_calculator",
            action_args={"expression": "2+3", "api_key": "secret"},
            raw_model_output="raw model output",
        )
        observation = ObservationPacket(
            execution_id=context.execution_id,
            plan_id=context.plan_id,
            task_id="task_1",
            step_id="step_1",
            packet_id=action.packet_id,
            action_type="call_tool",
            action_target="math_calculator",
            success=True,
            message="5",
            raw_observation={"api_key": "secret", "result": 5},
            model_consumable_observation={"result": 5},
        )
        context.loop_state.record_action(action)
        context.loop_state.record_observation(observation)
        context.observation_store.add(observation, output_key="calculation")
        context.event_stream.emit_event("step_completed", "Calculated.", payload={"raw_observation": {"secret": "raw"}}, task_id="task_1", step_id="step_1")
        turn = context.loop_state.start_turn(task_id="task_1", step_id="step_2", attempt=1)

        prompt, payload = executor._build_action_decision_prompt(context, task_unit, step_2, turn)
        model_response = executor.model_manager.generate(prompt)

        self.assertEqual(model_response, "{}")
        self.assertEqual(len(executor.model_manager.generate_calls), 1)
        self.assertIn("Return exactly one JSON object matching the ActionPacket schema", prompt)
        self.assertIn("Write result.", prompt)
        self.assertIn("file_writer", prompt)
        self.assertIn("math_calculator", prompt)
        self.assertNotIn("unknown_planner_tool", json.dumps(payload["available_tools"], ensure_ascii=False))
        self.assertIn('"input_observations"', prompt)
        self.assertIn('"result": 5', prompt)
        self.assertNotIn("unknown_planner_tool", prompt)
        self.assertNotIn("raw_observation", prompt)
        self.assertNotIn("raw model output", prompt)
        self.assertNotIn('"api_key": "secret"', prompt)
        self.assertNotIn('"secret": "raw"', prompt)
        self.assertIn("***REDACTED***", prompt)
        self.assertEqual(payload["current_step"]["input_from"], ["calculation"])
        self.assertEqual(payload["current_step"]["output_key"], None)
        self.assertTrue(payload["current_step"]["requires_confirmation"])
        self.assertEqual(payload["execution_progress"]["recent_observations"][0]["observation_id"], observation.observation_id)

    def test_model_action_prompt_compacts_input_from_observations(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            values = dict(DEFAULT_REACT_EXECUTOR_CONFIG)
            values.update(
                {
                    "workspace_root": str(root),
                    "react_executor_log_path": str(root / "logs" / "react_executor_prompt.log"),
                    "max_model_observation_chars": 120,
                }
            )
            executor = ReActExecutor(
                model_manager=RecordingModel(),
                tool_manager=None,
                tool_registry=_registry(),
                config=ReActExecutorConfig(root=root, react_executor_config=values),
            )
            step_1 = PlanStep(id="step_1", task_id="task_1", description="Produce long output.", step_type="model", output_key="long_output")
            step_2 = PlanStep(id="step_2", task_id="task_1", description="Summarize.", step_type="model", input_from=["long_output"])
            task_unit = TaskUnit(id="task_1", title="Long", step_ids=["step_1", "step_2"])
            plan = TaskPlan(goal="long", mode="meso", steps=[step_1, step_2], task_units=[task_unit])
            context = executor._create_context(plan, task=SimpleNamespace(action_policy="allow"), user_input="summarize", history="")
            long_text = "x" * 500
            context.observation_store.add(
                ObservationPacket(
                    execution_id=context.execution_id,
                    plan_id=context.plan_id,
                    task_id="task_1",
                    step_id="step_1",
                    action_type="call_model",
                    success=True,
                    message=long_text,
                    data={"raw": long_text},
                    raw_observation={"raw": long_text},
                    model_consumable_observation={"content": long_text},
                ),
                output_key="long_output",
            )
            packet = ActionPacket(
                execution_id=context.execution_id,
                plan_id=context.plan_id,
                task_id="task_1",
                step_id="step_2",
                action_type="call_model",
                action_args={"goal": "summarize", "input_from": ["long_output"], "output_requirements": "short"},
            )

            prompt, input_payload, input_errors = executor._build_model_action_prompt(context, packet, step_2)

        self.assertEqual(input_errors, [])
        compact = input_payload["input_from"]["long_output"]
        self.assertTrue(compact["truncated"])
        self.assertNotIn(long_text, prompt)
        self.assertIn('"truncated": true', prompt)
        self.assertNotIn("raw_observation", prompt)

    def test_execute_logs_action_decision_prompt_summary_without_full_prompt(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config = _config(root)
            log_path = config.react_executor_log_path
            executor = ReActExecutor(
                model_manager=RecordingModel(),
                tool_manager=None,
                tool_registry=_registry(),
                config=config,
                execution_logger=ReActExecutorLogger(log_path),
            )
            step = PlanStep(
                id="step_1",
                task_id="task_1",
                description="Calculate expression.",
                step_type="tool",
                tool_name="math_calculator",
                args={"expression": "2+3"},
            )
            result = executor.execute(
                TaskPlan(
                    goal="calculate",
                    mode="micro",
                    steps=[step],
                    task_units=[TaskUnit(id="task_1", title="Calculate", step_ids=["step_1"])],
                    available_tools=["math_calculator"],
                    required_tools=["math_calculator"],
                ),
                task=SimpleNamespace(intent_sequence=["calculate"], action_policy="allow"),
                user_input="calculate 2+3",
            )
            records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]

        prompt_record = next(record for record in records if record["record_type"] == "action_decision_prompt")
        self.assertFalse(result.success)
        self.assertGreaterEqual(len(executor.model_manager.generate_calls), 1)
        self.assertIn("prompt_length", prompt_record["metadata"]["prompt"])
        self.assertIn("prompt_preview", prompt_record["metadata"]["prompt"])
        self.assertNotIn("full_prompt", prompt_record["metadata"]["prompt"])
        self.assertEqual(prompt_record["metadata"]["input_summary"]["current_step"]["id"], "step_1")


class RecordingModel:
    def __init__(self):
        self.generate_calls = []

    def generate(self, prompt: str, **kwargs):
        self.generate_calls.append((prompt, kwargs))
        return "{}"


def _registry() -> ToolRegistry:
    return ToolRegistry(
        [
            ToolSpec(
                name="math_calculator",
                description="Calculate expressions.",
                parameters_schema={"type": "object", "properties": {"expression": {"type": "string"}}},
                required_params=["expression"],
            ),
            ToolSpec(
                name="file_writer",
                description="Write workspace files.",
                parameters_schema={"type": "object", "properties": {"file_path": {"type": "string"}, "content": {"type": "string"}}},
                required_params=["file_path", "content"],
                risk_level="medium",
                requires_confirmation=True,
                workspace_scope="write_workspace",
            ),
        ]
    )


def _config(root: Path) -> ReActExecutorConfig:
    values = dict(DEFAULT_REACT_EXECUTOR_CONFIG)
    values.update(
        {
            "workspace_root": str(root),
            "react_executor_log_path": str(root / "logs" / "react_executor_prompt.log"),
            "log_full_prompt": False,
        }
    )
    return ReActExecutorConfig(root=root, react_executor_config=values)


if __name__ == "__main__":
    unittest.main()
