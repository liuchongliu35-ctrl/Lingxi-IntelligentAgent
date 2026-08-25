from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.agent.planner import PlanStep, TaskPlan, TaskUnit
from src.agent.react_executor import (
    ACTION_PACKET_INVALID_CODE,
    COMMAND_BLOCKED_CODE,
    CONFIRMATION_PENDING_CODE,
    ReActExecutor,
)
from src.agent.react_executor_checker import MAX_TURNS_REACHED_CODE
from src.agent.react_executor_config import DEFAULT_REACT_EXECUTOR_CONFIG, ReActExecutorConfig
from src.tools.base import ToolResult
from src.tools.registry import ToolRegistry, ToolSpec


class InvariantModelManager:
    def __init__(self, responses):
        self.responses = list(responses)
        self.generate_calls = []

    def generate(self, prompt: str, **kwargs):
        self.generate_calls.append((prompt, kwargs))
        if self.responses:
            return self.responses.pop(0)
        return "{}"


class InvariantToolManager:
    def __init__(self, results=None):
        self.results = list(results or [])
        self.run_calls = []

    def list_tools(self):
        return {
            "math_calculator": "Calculate expressions.",
            "command_tool": "Run approved commands through the Tool layer.",
        }

    def run_tool(self, tool_name: str, **kwargs):
        self.run_calls.append((tool_name, kwargs))
        if self.results:
            return self.results.pop(0)
        if tool_name == "command_tool":
            return ToolResult.ok(
                data={
                    "command": kwargs.get("command"),
                    "cwd": kwargs.get("cwd"),
                    "purpose": kwargs.get("purpose"),
                    "exit_code": 0,
                    "stdout_summary": "ok",
                    "stderr_summary": "",
                },
                message="command ok",
            )
        return ToolResult.ok(data="5", message="5")


    def execute(self, request):
        return self.run_tool(request.tool_name, **request.args)
class ReActExecutorInvariantTest(unittest.TestCase):
    def test_invalid_action_packet_never_reaches_tool_manager(self):
        model = InvariantModelManager(["not json", "{}"])
        tools = InvariantToolManager()

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            executor = ReActExecutor(
                model_manager=model,
                tool_manager=tools,
                tool_registry=_registry(),
                config=_config(root, max_action_packet_repair_attempts=1),
            )
            result = executor.execute(_plan(_math_step()), task=_task(), user_input="calculate")

        self.assertFalse(result.success)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_code, ACTION_PACKET_INVALID_CODE)
        self.assertEqual(tools.run_calls, [])
        self.assertEqual(len(model.generate_calls), 2)

    def test_unknown_tool_repair_never_reaches_tool_manager(self):
        model = InvariantModelManager(
            [
                json.dumps(
                    {
                        "action_type": "call_tool",
                        "action_target": "missing_tool",
                        "action_args": {},
                    }
                ),
                json.dumps(
                    {
                        "action_type": "request_replan",
                        "request_replan_reason": "tool is not available",
                    }
                ),
            ]
        )
        tools = InvariantToolManager()
        executor = ReActExecutor(model_manager=model, tool_manager=tools, tool_registry=_registry())

        result = executor.execute(_plan(_math_step()), task=_task(), user_input="use missing tool")

        self.assertFalse(result.success)
        self.assertEqual(result.status, "request_replan")
        self.assertEqual(tools.run_calls, [])
        self.assertEqual(len(model.generate_calls), 2)

    def test_command_execution_uses_tool_layer_after_confirmation(self):
        model = InvariantModelManager([_command_action_json()])
        tools = InvariantToolManager()
        executor = ReActExecutor(model_manager=model, tool_manager=tools, tool_registry=_registry())
        plan = _plan(_command_step())
        context = executor._create_context(plan, task=_task(), user_input="run command", history="")

        paused = executor._execute_react_loop(context)

        self.assertEqual(paused.status, "waiting_user")
        self.assertEqual(paused.error_code, CONFIRMATION_PENDING_CODE)
        self.assertEqual(tools.run_calls, [])
        self.assertNotIn("command_started", _event_types(context))

        resumed = executor.resume_after_confirmation(context, approved=True)

        self.assertTrue(resumed.success)
        self.assertEqual(resumed.status, "completed")
        self.assertEqual([name for name, _kwargs in tools.run_calls], ["command_tool"])
        self.assertIn("command_started", _event_types(context))
        self.assertIn("command_finished", _event_types(context))

    def test_safety_block_stops_later_steps_and_does_not_execute_action(self):
        model = InvariantModelManager([_command_action_json(command="python --version | more")])
        tools = InvariantToolManager()
        executor = ReActExecutor(model_manager=model, tool_manager=tools, tool_registry=_registry())
        plan = _plan(_command_step(), _math_step(step_id="step_2"))

        result = executor.execute(plan, task=_task(), user_input="run blocked command then calculate")

        self.assertFalse(result.success)
        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.error_code, COMMAND_BLOCKED_CODE)
        self.assertEqual(tools.run_calls, [])
        self.assertEqual(len(model.generate_calls), 1)
        self.assertEqual(result.step_statuses["step_1"], "blocked")
        self.assertEqual(result.step_statuses["step_2"], "pending")
        self.assertNotIn(
            "step_2",
            [event.step_id for event in result.events if event.step_id == "step_2"],
        )
        self.assertNotIn("command_started", _event_types(result))

    def test_confirmation_pending_stops_later_step_until_resume(self):
        model = InvariantModelManager(
            [
                _command_action_json(),
                json.dumps(
                    {
                        "action_type": "call_tool",
                        "action_target": "math_calculator",
                        "action_args": {"expression": "2+3"},
                    }
                ),
            ]
        )
        tools = InvariantToolManager()
        executor = ReActExecutor(model_manager=model, tool_manager=tools, tool_registry=_registry())
        plan = _plan(_command_step(), _math_step(step_id="step_2"))
        context = executor._create_context(plan, task=_task(), user_input="run two steps", history="")

        paused = executor._execute_react_loop(context)

        self.assertEqual(paused.status, "waiting_user")
        self.assertEqual(len(model.generate_calls), 1)
        self.assertEqual(tools.run_calls, [])
        self.assertEqual(context.step_states["step_2"].status, "pending")

        resumed = executor.resume_after_confirmation(context, approved=True)

        self.assertTrue(resumed.success)
        self.assertEqual(resumed.status, "completed")
        self.assertEqual([name for name, _kwargs in tools.run_calls], ["command_tool", "math_calculator"])
        self.assertEqual(len(model.generate_calls), 2)

    def test_network_download_command_is_blocked_before_confirmation_and_tool_call(self):
        model = InvariantModelManager([_command_action_json(command="curl https://example.com")])
        tools = InvariantToolManager()
        executor = ReActExecutor(model_manager=model, tool_manager=tools, tool_registry=_registry())
        plan = _plan(_command_step())

        result = executor.execute(plan, task=_task(), user_input="download content")

        self.assertFalse(result.success)
        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.error_code, COMMAND_BLOCKED_CODE)
        self.assertEqual(tools.run_calls, [])
        self.assertNotIn("confirmation_requested", _event_types(result))
        self.assertNotIn("command_started", _event_types(result))

    def test_max_execution_turns_stops_before_another_model_or_tool_turn(self):
        model = InvariantModelManager(
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
        tools = InvariantToolManager([ToolResult.fail("timeout", code="command_timeout")])

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            executor = ReActExecutor(
                model_manager=model,
                tool_manager=tools,
                tool_registry=_registry(),
                config=_config(root, max_execution_turns=1),
            )
            result = executor.execute(_plan(_math_step()), task=_task(), user_input="loop")

        self.assertFalse(result.success)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_code, MAX_TURNS_REACHED_CODE)
        self.assertEqual(len(model.generate_calls), 1)
        self.assertEqual(len(tools.run_calls), 1)

    def test_request_replan_stops_later_steps_without_tool_execution(self):
        model = InvariantModelManager(
            [
                json.dumps(
                    {
                        "action_type": "request_replan",
                        "request_replan_reason": "planner route is no longer valid",
                    }
                )
            ]
        )
        tools = InvariantToolManager()
        executor = ReActExecutor(model_manager=model, tool_manager=tools, tool_registry=_registry())

        result = executor.execute(
            _plan(_math_step(), _math_step(step_id="step_2")),
            task=_task(),
            user_input="replan",
        )

        self.assertFalse(result.success)
        self.assertEqual(result.status, "request_replan")
        self.assertTrue(result.request_replan)
        self.assertEqual(len(model.generate_calls), 1)
        self.assertEqual(tools.run_calls, [])
        self.assertEqual(result.step_statuses["step_1"], "failed")
        self.assertEqual(result.step_statuses["step_2"], "skipped")


def _registry() -> ToolRegistry:
    return ToolRegistry(
        [
            ToolSpec(
                name="math_calculator",
                description="Calculate expressions.",
                parameters_schema={"type": "object", "properties": {"expression": {"type": "string"}}},
                required_params=["expression"],
                risk_level="low",
                workspace_scope="none",
            ),
            ToolSpec(
                name="command_tool",
                description="Run approved commands.",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                        "cwd": {"type": "string"},
                        "purpose": {"type": "string"},
                        "risk_level": {"type": "string"},
                        "requires_confirmation": {"type": "boolean"},
                        "expected_result": {"type": "string"},
                        "timeout_seconds": {"type": "integer"},
                        "network_required": {"type": "boolean"},
                        "writes_files": {"type": "boolean"},
                        "destructive_risk": {"type": "boolean"},
                    },
                },
                required_params=["command", "cwd", "purpose"],
                risk_level="high",
                requires_confirmation=True,
                workspace_scope="command",
            ),
        ]
    )


def _plan(*steps: PlanStep) -> TaskPlan:
    return TaskPlan(
        goal="safety invariants",
        mode="meso",
        steps=list(steps),
        task_units=[TaskUnit(id="task_1", title="Invariant checks", step_ids=[step.id for step in steps])],
        available_tools=["math_calculator", "command_tool"],
        required_tools=[step.tool_name for step in steps if step.tool_name],
        can_execute=True,
        plan_validation_status="valid",
    )


def _math_step(*, step_id: str = "step_1") -> PlanStep:
    return PlanStep(
        id=step_id,
        task_id="task_1",
        description="Calculate.",
        step_type="tool",
        tool_name="math_calculator",
        args={"expression": "2+3"},
    )


def _command_step() -> PlanStep:
    return PlanStep(
        id="step_1",
        task_id="task_1",
        description="Run command.",
        step_type="tool",
        tool_name="command_tool",
        args={},
    )


def _command_action_json(
    *,
    command: str = "python --version",
    cwd: str = ".",
    risk_level: str = "low",
) -> str:
    return json.dumps(
        {
            "action_type": "call_tool",
            "action_target": "command_tool",
            "action_args": {
                "command": command,
                "cwd": cwd,
                "purpose": "invariant test",
                "risk_level": risk_level,
                "requires_confirmation": True,
                "expected_result": "command completes successfully",
                "timeout_seconds": 5,
                "network_required": False,
                "writes_files": False,
                "destructive_risk": False,
            },
        }
    )


def _config(root: Path, **overrides) -> ReActExecutorConfig:
    values = dict(DEFAULT_REACT_EXECUTOR_CONFIG)
    values.update(
        {
            "workspace_root": str(root),
            "react_executor_log_path": str(root / "logs" / "react_executor.log"),
            "command_confirmation_policy": "ask",
        }
    )
    values.update(overrides)
    return ReActExecutorConfig(root=root, react_executor_config=values)


def _task():
    return SimpleNamespace(action_policy="allow", requires_confirmation=False)


def _event_types(value) -> list[str]:
    events = value.events if hasattr(value, "events") else value.event_stream.events
    return [event.type for event in events]


if __name__ == "__main__":
    unittest.main()
