from __future__ import annotations

import json
import unittest

from src.agent.planner import PlanStep, TaskPlan, TaskUnit
from src.agent.react_executor import ACTION_PACKET_INVALID_CODE, COMMAND_BLOCKED_CODE, CONFIRMATION_PENDING_CODE, ReActExecutor
from src.agent.react_executor_protocol import ActionPacket
from src.tools.base import ToolResult


class FakeCommandToolManager:
    def __init__(self, results=None):
        self.run_calls = []
        self.results = list(results or [])

    def list_tools(self):
        return {"command_tool": "Fake command tool."}

    def run_tool(self, tool_name: str, **kwargs):
        self.run_calls.append((tool_name, kwargs))
        if self.results:
            return self.results.pop(0)
        return ToolResult.ok(
            data={
                "command": kwargs["command"],
                "cwd": kwargs["cwd"],
                "purpose": kwargs["purpose"],
                "exit_code": 0,
                "stdout_summary": "ok",
                "stderr_summary": "",
            },
            message="ok",
        )

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


class ReActExecutorCommandActionTest(unittest.TestCase):
    def setUp(self):
        self.tool_manager = FakeCommandToolManager()
        self.executor = ReActExecutor(model_manager=None, tool_manager=self.tool_manager)
        self.plan, self.step = _plan()
        self.context = self.executor._create_context(self.plan, task={}, user_input="run command", history="")

    def test_command_requires_confirmation_before_tool_execution(self):
        packet = _command_packet(self.context)

        observation = self.executor.dispatch_action(self.context, packet, step=self.step)

        self.assertFalse(observation.success)
        self.assertEqual(observation.code, CONFIRMATION_PENDING_CODE)
        self.assertTrue(self.context.requires_user_input)
        self.assertIsNotNone(self.context.pending_confirmation)
        self.assertEqual(self.tool_manager.run_calls, [])
        self.assertIn("confirmation_requested", _event_types(self.context))
        self.assertNotIn("command_started", _event_types(self.context))

    def test_confirmed_command_enters_command_tool_and_emits_command_events(self):
        packet = _command_packet(self.context)

        observation = self.executor.dispatch_action(self.context, packet, step=self.step, confirmed=True)

        self.assertTrue(observation.success)
        self.assertEqual(self.tool_manager.run_calls[0][0], "command_tool")
        self.assertEqual(self.tool_manager.run_calls[0][1]["command"], "python --version")
        self.assertIn("command_started", _event_types(self.context))
        self.assertIn("command_finished", _event_types(self.context))
        command_finished = [event for event in self.context.event_stream.events if event.type == "command_finished"][0]
        self.assertEqual(command_finished.payload["command"], "python --version")
        self.assertEqual(command_finished.payload["exit_code"], 0)
        self.assertEqual(command_finished.payload["stdout_summary"], "ok")
        self.assertEqual(command_finished.payload["stderr_summary"], "")

    def test_command_action_requires_full_structured_arguments_before_confirmation(self):
        packet = _command_packet(self.context)
        del packet.action_args["expected_result"]

        observation = self.executor.dispatch_action(self.context, packet, step=self.step)

        self.assertFalse(observation.success)
        self.assertEqual(observation.code, ACTION_PACKET_INVALID_CODE)
        self.assertIn("expected_result", observation.error)
        self.assertEqual(self.tool_manager.run_calls, [])
        self.assertNotIn("confirmation_requested", _event_types(self.context))

    def test_dangerous_command_is_blocked_before_confirmation_or_tool_call(self):
        packet = _command_packet(self.context, command="rm -rf .", risk_level="blocked", destructive_risk=True)

        observation = self.executor.dispatch_action(self.context, packet, step=self.step, confirmed=True)

        self.assertFalse(observation.success)
        self.assertEqual(observation.code, COMMAND_BLOCKED_CODE)
        self.assertIn("destructive", observation.error)
        self.assertEqual(self.tool_manager.run_calls, [])
        self.assertNotIn("command_started", _event_types(self.context))

    def test_cwd_outside_workspace_is_blocked(self):
        packet = _command_packet(self.context, cwd="H:\\")

        observation = self.executor.dispatch_action(self.context, packet, step=self.step, confirmed=True)

        self.assertFalse(observation.success)
        self.assertEqual(observation.code, COMMAND_BLOCKED_CODE)
        self.assertIn("outside workspace", observation.error)
        self.assertEqual(self.tool_manager.run_calls, [])

    def test_shell_metachar_command_is_blocked_in_main_loop_before_tool_call(self):
        model = SequenceModel([_command_action_json(command="python --version | more")])
        executor = ReActExecutor(model_manager=model, tool_manager=self.tool_manager)
        plan, _step = _plan(requires_confirmation=False)
        context = executor._create_context(plan, task={}, user_input="run command", history="")

        self.assertIsNone(executor._run_plan_precheck(context))
        result = executor._execute_react_loop(context)

        self.assertFalse(result.success)
        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.error_code, COMMAND_BLOCKED_CODE)
        self.assertEqual(self.tool_manager.run_calls, [])
        self.assertNotIn("command_started", _event_types(context))

    def test_model_command_main_loop_pauses_and_resumes_through_command_tool(self):
        model = SequenceModel([_command_action_json()])
        executor = ReActExecutor(model_manager=model, tool_manager=self.tool_manager)
        plan, _step = _plan(requires_confirmation=False)
        context = executor._create_context(plan, task={}, user_input="run command", history="")

        self.assertIsNone(executor._run_plan_precheck(context))
        paused = executor._execute_react_loop(context)
        prompt = model.generate_calls[0][0]
        resumed = executor.resume_after_confirmation(context, approved=True)

        self.assertEqual(paused.status, "waiting_user")
        self.assertTrue(paused.requires_user_input)
        self.assertIsNotNone(paused.pending_confirmation)
        self.assertIn("command_tool", prompt)
        self.assertIn("expected_result", prompt)
        self.assertIn("timeout_seconds", prompt)
        self.assertEqual(self.tool_manager.run_calls, [("command_tool", _command_args())])
        self.assertTrue(resumed.success)
        self.assertEqual(resumed.status, "completed")
        self.assertEqual(resumed.step_statuses["step_1"], "completed")
        command_finished = [event for event in resumed.events if event.type == "command_finished"][0]
        self.assertEqual(command_finished.payload["command"], "python --version")
        self.assertEqual(command_finished.payload["cwd"], ".")
        self.assertEqual(command_finished.payload["exit_code"], 0)
        self.assertEqual(command_finished.payload["stdout_summary"], "ok")
        self.assertIn("duration_ms", command_finished.payload)

    def test_command_failure_can_retry_after_confirmation_without_second_prompt(self):
        tool_manager = FakeCommandToolManager(
            [
                ToolResult.fail(
                    "exit 1",
                    code="command_failed",
                    data={
                        "command": "python --version",
                        "cwd": ".",
                        "purpose": "diagnostic",
                        "exit_code": 1,
                        "stdout_summary": "",
                        "stderr_summary": "exit 1",
                    },
                ),
                ToolResult.ok(
                    data={
                        "command": "python --version",
                        "cwd": ".",
                        "purpose": "diagnostic",
                        "exit_code": 0,
                        "stdout_summary": "ok",
                        "stderr_summary": "",
                    },
                    message="ok",
                ),
            ]
        )
        model = SequenceModel([_command_action_json()])
        executor = ReActExecutor(model_manager=model, tool_manager=tool_manager)
        plan, _step = _plan(requires_confirmation=False, retryable=True, max_retries=1)
        context = executor._create_context(plan, task={}, user_input="run command", history="")

        self.assertIsNone(executor._run_plan_precheck(context))
        paused = executor._execute_react_loop(context)
        resumed = executor.resume_after_confirmation(context, approved=True)

        self.assertEqual(paused.status, "waiting_user")
        self.assertTrue(resumed.success)
        self.assertEqual(resumed.status, "completed")
        self.assertEqual(len(tool_manager.run_calls), 2)
        self.assertEqual(_event_types(context).count("confirmation_requested"), 1)
        self.assertIn("retry_scheduled", _event_types(context))
        self.assertIn("retry_finished", _event_types(context))
        self.assertEqual(_event_types(context).count("command_finished"), 2)

    def test_command_failure_can_request_replan_after_checker(self):
        tool_manager = FakeCommandToolManager(
            [
                ToolResult.fail(
                    "exit 1",
                    code="command_failed",
                    data={
                        "command": "python --version",
                        "cwd": ".",
                        "purpose": "diagnostic",
                        "exit_code": 1,
                        "stdout_summary": "",
                        "stderr_summary": "exit 1",
                    },
                )
            ]
        )
        model = SequenceModel([_command_action_json()])
        executor = ReActExecutor(model_manager=model, tool_manager=tool_manager)
        plan, _step = _plan(requires_confirmation=False, on_failure="request_replan", max_retries=0)
        context = executor._create_context(plan, task={}, user_input="run command", history="")

        self.assertIsNone(executor._run_plan_precheck(context))
        paused = executor._execute_react_loop(context)
        resumed = executor.resume_after_confirmation(context, approved=True)

        self.assertEqual(paused.status, "waiting_user")
        self.assertFalse(resumed.success)
        self.assertEqual(resumed.status, "request_replan")
        self.assertTrue(resumed.request_replan)
        self.assertEqual(len(tool_manager.run_calls), 1)
        self.assertIn("request_replan", _event_types(context))


def _plan(*, requires_confirmation: bool = True, retryable: bool = False, max_retries: int = 3, on_failure: str = "stop"):
    step = PlanStep(
        id="step_1",
        task_id="task_1",
        description="Run command.",
        step_type="tool",
        tool_name="command_tool",
        args={},
        requires_confirmation=requires_confirmation,
        retryable=retryable,
        max_retries=max_retries,
        on_failure=on_failure,
    )
    plan = TaskPlan(
        goal="run command",
        mode="micro",
        steps=[step],
        task_units=[TaskUnit(id="task_1", title="Command", step_ids=["step_1"])],
        available_tools=["command_tool"],
        required_tools=["command_tool"],
        can_execute=True,
        plan_validation_status="valid",
    )
    return plan, step


def _command_args(
    *,
    command: str = "python --version",
    cwd: str = ".",
    risk_level: str = "low",
    destructive_risk: bool = False,
):
    return {
        "command": command,
        "cwd": cwd,
        "purpose": "diagnostic",
        "risk_level": risk_level,
        "requires_confirmation": True,
        "expected_result": "command completes successfully",
        "timeout_seconds": 5,
        "destructive_risk": destructive_risk,
    }


def _command_action_json(**kwargs):
    return json.dumps(
        {
            "action_type": "call_tool",
            "action_target": "command_tool",
            "action_args": _command_args(**kwargs),
            "user_visible_message": "Run command?",
        }
    )


def _command_packet(
    context,
    *,
    command: str = "python --version",
    cwd: str = ".",
    risk_level: str = "low",
    destructive_risk: bool = False,
) -> ActionPacket:
    return ActionPacket(
        execution_id=context.execution_id,
        plan_id=context.plan_id,
        task_id="task_1",
        step_id="step_1",
        action_type="call_tool",
        action_target="command_tool",
        action_args=_command_args(command=command, cwd=cwd, risk_level=risk_level, destructive_risk=destructive_risk),
    )


def _event_types(context):
    return [event.type for event in context.event_stream.events]


if __name__ == "__main__":
    unittest.main()
