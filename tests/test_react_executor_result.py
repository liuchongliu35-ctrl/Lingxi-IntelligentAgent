from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.agent.planner import PlanStep, TaskPlan, TaskUnit
from src.agent.react_executor import ReActExecutor, REQUEST_REPLAN_CODE, SAFETY_BLOCKED_CODE
from src.agent.react_executor_config import DEFAULT_REACT_EXECUTOR_CONFIG, ReActExecutorConfig
from src.agent.react_executor_logging import ReActExecutorLogger
from src.agent.react_executor_protocol import ActionPacket, ObservationPacket
from src.tools.base import ToolResult
from src.tools.registry import ToolRegistry, ToolSpec


class FakeResultToolManager:
    def __init__(self):
        self.run_calls = []

    def list_tools(self):
        return {"math_calculator": "Fake calculator.", "file_writer": "Fake writer."}

    def run_tool(self, tool_name: str, **kwargs):
        self.run_calls.append((tool_name, kwargs))
        if tool_name == "file_writer":
            return ToolResult.fail("write failed", code="file_exists")
        return ToolResult.ok(data="5", message="5")


    def execute(self, request):
        return self.run_tool(request.tool_name, **request.args)
class ReActExecutorResultTest(unittest.TestCase):
    def test_completed_result_summarizes_successful_observation(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            executor = _executor(tmp_dir)
            plan = _plan([_math_step("step_1")])
            context = executor._create_context(plan, task={}, user_input="calculate", history="")
            observation = executor.dispatch_action(context, _packet(context, "math_calculator", {"expression": "2+3"}), step=plan.steps[0])
            context.step_states["step_1"].status = "completed"
            context.task_states["task_1"].status = "completed"
            context.task_states["task_1"].step_statuses["step_1"] = "completed"

            result = executor._build_result(context, status="completed", success=True)

        self.assertTrue(observation.success)
        self.assertTrue(result.success)
        self.assertEqual(result.status, "completed")
        self.assertIn("Succeeded:", result.output)
        self.assertIn("step_1/math_calculator", result.output)
        self.assertIn("Next: no further action is required", result.output)
        self.assertIn("success=True", result.summary)

    def test_partial_failure_result_lists_success_and_failure(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            executor = _executor(tmp_dir)
            plan = _plan([_math_step("step_1"), _file_step("step_2")])
            context = executor._create_context(plan, task={}, user_input="partial", history="")
            executor.dispatch_action(context, _packet(context, "math_calculator", {"expression": "2+3"}, step_id="step_1"), step=plan.steps[0])
            executor.dispatch_action(context, _packet(context, "file_writer", {"file_path": "out.txt", "content": "x"}, step_id="step_2"), step=plan.steps[1])
            context.step_states["step_1"].status = "completed"
            context.step_states["step_2"].status = "failed"
            context.failed_step_id = "step_2"
            context.error_code = "file_exists"

            result = executor._build_result(context, status="failed", success=False)

        self.assertFalse(result.success)
        self.assertIn("Succeeded:", result.output)
        self.assertIn("Failed:", result.output)
        self.assertIn("write failed", result.output)
        self.assertIn("inspect the failed observation", result.output)
        self.assertEqual(result.failed_step_id, "step_2")

    def test_waiting_user_result_includes_pending_question(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            executor = _executor(tmp_dir)
            plan = _plan([_math_step("step_1")])
            context = executor._create_context(plan, task={}, user_input="ask", history="")
            packet = ActionPacket(
                execution_id=context.execution_id,
                plan_id=context.plan_id,
                task_id="task_1",
                step_id="step_1",
                action_type="ask_user",
                action_args={"ask_type": "clarification", "question": "Which file?"},
            )
            executor.dispatch_action(context, packet, step=plan.steps[0])

            result = executor._build_result(context, status="waiting_user", success=False)

        self.assertTrue(result.requires_user_input)
        self.assertEqual(result.user_input_request, "Which file?")
        self.assertIn("Waiting for user: Which file?", result.output)
        self.assertIn("wait for the user response", result.output)

    def test_request_replan_result_sets_replan_fields(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            executor = _executor(tmp_dir)
            plan = _plan([_math_step("step_1")])
            context = executor._create_context(plan, task={}, user_input="replan", history="")
            packet = ActionPacket(
                execution_id=context.execution_id,
                plan_id=context.plan_id,
                task_id="task_1",
                step_id="step_1",
                action_type="request_replan",
                request_replan_reason="tool contract changed",
            )
            observation = executor.dispatch_action(context, packet, step=plan.steps[0])

            result = executor._build_result(context, status="request_replan", success=False)

        self.assertEqual(observation.code, REQUEST_REPLAN_CODE)
        self.assertTrue(result.request_replan)
        self.assertEqual(result.replan_reason, "tool contract changed")
        self.assertIn("Replan requested: tool contract changed", result.output)
        self.assertIn("ask Planner for a revised TaskPlan", result.output)

    def test_fallback_result_mentions_fallback_usage(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            executor = _executor(tmp_dir)
            plan = _plan([_math_step("step_1")])
            context = executor._create_context(plan, task={}, user_input="fallback", history="")
            context.observation_store.add(
                ObservationPacket(
                    execution_id=context.execution_id,
                    plan_id=context.plan_id,
                    task_id="task_1",
                    step_id="step_1",
                    action_type="call_model",
                    action_target="model",
                    success=True,
                    data="model fallback result",
                    message="model fallback result",
                    fallback_used=True,
                    fallback_type="model",
                )
            )
            context.step_states["step_1"].status = "completed"

            result = executor._build_result(context, status="completed", success=True)

        self.assertIn("Fallback used:", result.output)
        self.assertIn("via model", result.output)
        self.assertIn("fallback=1", result.summary)

    def test_blocked_result_explains_blocking_reason(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            executor = _executor(tmp_dir)
            plan = _plan([_file_step("step_1", file_path="H:\\outside.txt")])

            result = executor.execute(plan, task={}, user_input="unsafe")

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, SAFETY_BLOCKED_CODE)
        self.assertIn("Blocked:", result.output)
        self.assertIn("outside workspace", result.output)
        self.assertIn("resolve the blocking policy", result.output)


def _executor(tmp_dir: str) -> ReActExecutor:
    root = Path(tmp_dir)
    values = dict(DEFAULT_REACT_EXECUTOR_CONFIG)
    values.update(
        {
            "workspace_root": str(root),
            "react_executor_log_path": str(root / "logs" / "react_executor.log"),
            "command_confirmation_policy": "low_risk_auto",
        }
    )
    config = ReActExecutorConfig(root=root, react_executor_config=values)
    return ReActExecutor(
        model_manager=None,
        tool_manager=FakeResultToolManager(),
        tool_registry=_registry(),
        config=config,
        execution_logger=ReActExecutorLogger(root / "logs" / "react_executor.log"),
    )


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


def _plan(steps: list[PlanStep]) -> TaskPlan:
    return TaskPlan(
        goal="result demo",
        mode="micro",
        steps=steps,
        task_units=[TaskUnit(id="task_1", title="Result", step_ids=[step.id for step in steps])],
        available_tools=["math_calculator", "file_writer"],
        required_tools=[step.tool_name for step in steps if step.tool_name],
        can_execute=True,
        plan_validation_status="valid",
    )


def _math_step(step_id: str) -> PlanStep:
    return PlanStep(
        id=step_id,
        task_id="task_1",
        description="Calculate.",
        step_type="tool",
        tool_name="math_calculator",
        args={"expression": "2+3"},
    )


def _file_step(step_id: str, *, file_path: str = "out.txt") -> PlanStep:
    return PlanStep(
        id=step_id,
        task_id="task_1",
        description="Write.",
        step_type="tool",
        tool_name="file_writer",
        args={"file_path": file_path, "content": "x"},
    )


def _packet(context, tool_name: str, action_args: dict, *, step_id: str = "step_1") -> ActionPacket:
    return ActionPacket(
        execution_id=context.execution_id,
        plan_id=context.plan_id,
        task_id="task_1",
        step_id=step_id,
        action_type="call_tool",
        action_target=tool_name,
        action_args=action_args,
    )


if __name__ == "__main__":
    unittest.main()
