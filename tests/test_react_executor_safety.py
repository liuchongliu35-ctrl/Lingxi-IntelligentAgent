from __future__ import annotations

import unittest
from types import SimpleNamespace

from src.agent.planner import PlanStep, TaskPlan, TaskUnit
from src.agent.react_executor import CONFIRMATION_PENDING_CODE, ReActExecutor, SAFETY_BLOCKED_CODE
from src.agent.react_executor_protocol import ActionPacket
from src.agent.react_executor_safety import SAFETY_CONFIRMATION_REQUIRED_CODE
from src.tools.base import ToolResult
from src.tools.registry import ToolRegistry, ToolSpec


class FakeSafetyToolManager:
    def __init__(self):
        self.run_calls = []

    def list_tools(self):
        return {
            "file_writer": "Fake writer.",
            "code_executor": "Fake code executor.",
            "blocked_tool": "Blocked fake tool.",
        }

    def run_tool(self, tool_name: str, **kwargs):
        self.run_calls.append((tool_name, kwargs))
        return ToolResult.ok(data={"tool": tool_name, "args": kwargs}, message="ok")


    def execute(self, request):
        return self.run_tool(request.tool_name, **request.args)
class ReActExecutorSafetyTest(unittest.TestCase):
    def setUp(self):
        self.tool_manager = FakeSafetyToolManager()
        self.executor = ReActExecutor(
            model_manager=None,
            tool_manager=self.tool_manager,
            tool_registry=_registry(),
        )

    def test_workspace_write_outside_workspace_is_blocked_before_tool_call(self):
        plan, step = _plan(_file_writer_step(file_path="H:\\outside.txt"))
        context = self.executor._create_context(plan, task=_task(), user_input="write outside", history="")
        packet = _packet(context, "file_writer", {"file_path": "H:\\outside.txt", "content": "data"})

        observation = self.executor.dispatch_action(context, packet, step=step, confirmed=True)

        self.assertFalse(observation.success)
        self.assertEqual(observation.code, SAFETY_BLOCKED_CODE)
        self.assertIn("outside workspace", observation.error)
        self.assertEqual(self.tool_manager.run_calls, [])
        self.assertIn("system_notice", _event_types(context))

    def test_sensitive_system_path_is_blocked(self):
        plan, step = _plan(_file_writer_step(file_path="C:\\Windows\\System32\\drivers\\etc\\hosts"))
        context = self.executor._create_context(plan, task=_task(), user_input="write system path", history="")
        packet = _packet(
            context,
            "file_writer",
            {"file_path": "C:\\Windows\\System32\\drivers\\etc\\hosts", "content": "data"},
        )

        observation = self.executor.dispatch_action(context, packet, step=step, confirmed=True)

        self.assertFalse(observation.success)
        self.assertEqual(observation.code, SAFETY_BLOCKED_CODE)
        self.assertIn("Sensitive system path", observation.error)
        self.assertEqual(self.tool_manager.run_calls, [])

    def test_explicitly_forbidden_path_is_blocked(self):
        plan, step = _plan(_file_writer_step(file_path="protected.txt"))
        context = self.executor._create_context(
            plan,
            task=_task(forbidden_paths=["protected.txt"]),
            user_input="write forbidden",
            history="",
        )
        packet = _packet(context, "file_writer", {"file_path": "protected.txt", "content": "data"})

        observation = self.executor.dispatch_action(context, packet, step=step, confirmed=True)

        self.assertFalse(observation.success)
        self.assertEqual(observation.code, SAFETY_BLOCKED_CODE)
        self.assertIn("explicitly forbidden", observation.error)
        self.assertEqual(self.tool_manager.run_calls, [])

    def test_high_risk_tool_requires_confirmation_before_tool_call(self):
        plan, step = _plan(_code_step())
        context = self.executor._create_context(plan, task=_task(), user_input="run code", history="")
        packet = _packet(context, "code_executor", {"code": "print('x')"})

        observation = self.executor.dispatch_action(context, packet, step=step)

        self.assertFalse(observation.success)
        self.assertEqual(observation.code, CONFIRMATION_PENDING_CODE)
        self.assertEqual(observation.checker_result["safety"]["code"], SAFETY_CONFIRMATION_REQUIRED_CODE)
        self.assertTrue(context.requires_user_input)
        self.assertIsNotNone(context.pending_confirmation)
        self.assertEqual(self.tool_manager.run_calls, [])

    def test_blocked_tool_risk_is_rejected_even_when_confirmed(self):
        plan, step = _plan(_blocked_step())
        context = self.executor._create_context(plan, task=_task(), user_input="blocked tool", history="")
        packet = _packet(context, "blocked_tool", {"value": "x"})

        observation = self.executor.dispatch_action(context, packet, step=step, confirmed=True)

        self.assertFalse(observation.success)
        self.assertEqual(observation.code, SAFETY_BLOCKED_CODE)
        self.assertIn("Tool risk is blocked", observation.error)
        self.assertEqual(self.tool_manager.run_calls, [])

    def test_plan_precheck_blocks_unsafe_step_before_skeleton_traversal(self):
        plan, _step = _plan(_file_writer_step(file_path="H:\\outside.txt"))

        result = self.executor.execute(plan, task=_task(), user_input="unsafe plan")

        self.assertFalse(result.success)
        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.error_code, SAFETY_BLOCKED_CODE)
        self.assertEqual(result.failed_step_id, "step_1")
        self.assertEqual(result.step_statuses["step_1"], "blocked")
        self.assertNotIn("step_started", [event.type for event in result.events])
        self.assertEqual(self.tool_manager.run_calls, [])

    def test_plan_precheck_requests_confirmation_for_confirm_step(self):
        plan, _step = _plan(_file_writer_step(file_path="out.txt", requires_confirmation=True))

        result = self.executor.execute(plan, task=_task(), user_input="confirm plan")

        self.assertFalse(result.success)
        self.assertEqual(result.status, "waiting_user")
        self.assertEqual(result.error_code, "confirmation_required")
        self.assertTrue(result.requires_user_input)
        self.assertIsNotNone(result.pending_confirmation)
        self.assertEqual(result.step_statuses["step_1"], "waiting_user")
        self.assertEqual(self.tool_manager.run_calls, [])


def _registry() -> ToolRegistry:
    return ToolRegistry(
        [
            ToolSpec(
                name="file_writer",
                description="Write file.",
                parameters_schema={"type": "object", "properties": {"file_path": {"type": "string"}, "content": {"type": "string"}}},
                required_params=["file_path", "content"],
                risk_level="medium",
                workspace_scope="write_workspace",
            ),
            ToolSpec(
                name="code_executor",
                description="Run code.",
                parameters_schema={"type": "object", "properties": {"code": {"type": "string"}}},
                required_params=["code"],
                risk_level="high",
                workspace_scope="code_execution",
            ),
            ToolSpec(
                name="blocked_tool",
                description="Blocked.",
                parameters_schema={"type": "object", "properties": {"value": {"type": "string"}}},
                required_params=["value"],
                risk_level="blocked",
                workspace_scope="none",
            ),
        ]
    )


def _plan(step: PlanStep) -> tuple[TaskPlan, PlanStep]:
    return (
        TaskPlan(
            goal="safety demo",
            mode="micro",
            steps=[step],
            task_units=[TaskUnit(id="task_1", title="Safety", step_ids=[step.id])],
            available_tools=["file_writer", "code_executor", "blocked_tool"],
            required_tools=[step.tool_name] if step.tool_name else [],
            can_execute=True,
            plan_validation_status="valid",
        ),
        step,
    )


def _file_writer_step(*, file_path: str, requires_confirmation: bool = False) -> PlanStep:
    return PlanStep(
        id="step_1",
        task_id="task_1",
        description="Write file.",
        step_type="tool",
        tool_name="file_writer",
        args={"file_path": file_path, "content": "data"},
        requires_confirmation=requires_confirmation,
    )


def _code_step() -> PlanStep:
    return PlanStep(
        id="step_1",
        task_id="task_1",
        description="Run code.",
        step_type="tool",
        tool_name="code_executor",
        args={"code": "print('x')"},
    )


def _blocked_step() -> PlanStep:
    return PlanStep(
        id="step_1",
        task_id="task_1",
        description="Use blocked tool.",
        step_type="tool",
        tool_name="blocked_tool",
        args={"value": "x"},
    )


def _packet(context, tool_name: str, action_args: dict) -> ActionPacket:
    return ActionPacket(
        execution_id=context.execution_id,
        plan_id=context.plan_id,
        task_id="task_1",
        step_id="step_1",
        action_type="call_tool",
        action_target=tool_name,
        action_args=action_args,
    )


def _task(**kwargs):
    values = {"action_policy": "allow", "requires_confirmation": False}
    values.update(kwargs)
    return SimpleNamespace(**values)


def _event_types(context) -> list[str]:
    return [event.type for event in context.event_stream.events]


if __name__ == "__main__":
    unittest.main()
