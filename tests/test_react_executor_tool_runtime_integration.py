from __future__ import annotations

import unittest

from src.agent.planner import PlanStep, TaskPlan, TaskUnit
from src.agent.react_executor import (
    CONFIRMATION_PENDING_CODE,
    TOOL_ARGUMENT_VALIDATION_FAILED_CODE,
    ReActExecutor,
)
from src.agent.react_executor_protocol import ActionPacket
from src.tools.base import ToolResult
from src.tools.protocol import ToolCallRequest
from src.tools.registry import ToolRegistry, ToolSpec


class FormalToolManager:
    def __init__(self, result: ToolResult | None = None):
        self.requests: list[ToolCallRequest] = []
        self.result = result or ToolResult.ok(data={"value": "ok"}, message="ok")

    def execute(self, request: ToolCallRequest) -> ToolResult:
        self.requests.append(request)
        return self.result


class ReActExecutorToolRuntimeIntegrationTest(unittest.TestCase):
    def test_action_packet_is_mapped_to_formal_request_with_context_and_clean_args(self):
        manager = FormalToolManager(ToolResult.ok(data={"value": 5}, message="five"))
        registry = _registry(
            ToolSpec(
                name="demo_tool",
                description="Demo.",
                parameters_schema={
                    "type": "object",
                    "properties": {"value": {"type": "integer"}},
                    "additionalProperties": False,
                },
                required_params=["value"],
            )
        )
        executor = ReActExecutor(tool_manager=manager, tool_registry=registry)
        plan, step = _plan("demo_tool", {"value": 5})
        plan.source_trace_id = "trace-1"
        context = executor._create_context(
            plan,
            task={
                "session_id": "session-1",
                "user_id": "user-1",
                "session_capabilities": {"allow_read_workspace": True},
            },
            user_input="run demo",
            history="",
        )
        packet = ActionPacket(
            execution_id=context.execution_id,
            plan_id=context.plan_id,
            task_id="task-1",
            step_id="step-1",
            thought_summary="private reasoning must stay in the executor",
            user_visible_message="Run the demo tool.",
            action_type="call_tool",
            action_target="demo_tool",
            action_args={
                "value": 5,
                "input_from": [],
                "output_key": "result",
                "fallback_reason": "internal",
                "packet_id": "forged-packet",
                "observation_id": "forged-observation",
                "action_id": "forged-action",
                "confirmed": True,
            },
        )

        observation = executor.dispatch_action(context, packet, step=step)

        self.assertTrue(observation.success)
        self.assertEqual(len(manager.requests), 1)
        request = manager.requests[0]
        self.assertIsInstance(request, ToolCallRequest)
        self.assertEqual(request.tool_name, "demo_tool")
        self.assertEqual(request.args, {"value": 5})
        self.assertEqual(request.context.trace_id, "trace-1")
        self.assertEqual(request.context.execution_id, context.execution_id)
        self.assertEqual(request.context.plan_id, plan.plan_id)
        self.assertEqual(request.context.task_id, "task-1")
        self.assertEqual(request.context.step_id, "step-1")
        self.assertEqual(request.context.packet_id, packet.packet_id)
        self.assertEqual(request.context.session_id, "session-1")
        self.assertEqual(request.context.user_id, "user-1")
        self.assertEqual(request.context.source, "react_executor")
        self.assertEqual(request.context.initiated_by, "model")
        self.assertFalse(request.options.confirmed)

    def test_invalid_args_are_rejected_before_formal_manager_execution(self):
        manager = FormalToolManager()
        registry = _registry(
            ToolSpec(
                name="required_tool",
                description="Requires value.",
                parameters_schema={
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "additionalProperties": False,
                },
                required_params=["value"],
            )
        )
        executor = ReActExecutor(tool_manager=manager, tool_registry=registry)
        plan, step = _plan("required_tool", {})
        context = executor._create_context(plan, task={}, user_input="invalid", history="")
        packet = _packet(context, "required_tool", {})

        observation = executor.dispatch_action(context, packet, step=step)

        self.assertFalse(observation.success)
        self.assertEqual(observation.code, TOOL_ARGUMENT_VALIDATION_FAILED_CODE)
        self.assertEqual(manager.requests, [])

    def test_model_confirmed_true_cannot_bypass_executor_confirmation(self):
        manager = FormalToolManager()
        registry = _registry(
            ToolSpec(
                name="high_risk_tool",
                description="High risk.",
                parameters_schema={
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "additionalProperties": False,
                },
                required_params=["value"],
                risk_level="high",
                requires_confirmation=True,
            )
        )
        executor = ReActExecutor(tool_manager=manager, tool_registry=registry)
        plan, step = _plan("high_risk_tool", {"value": "x"})
        context = executor._create_context(plan, task={}, user_input="high risk", history="")
        packet = _packet(
            context,
            "high_risk_tool",
            {"value": "x", "confirmed": True},
        )

        observation = executor.dispatch_action(context, packet, step=step)

        self.assertFalse(observation.success)
        self.assertEqual(observation.code, CONFIRMATION_PENDING_CODE)
        self.assertEqual(manager.requests, [])

    def test_alias_is_preserved_in_request_and_resolved_by_formal_manager_boundary(self):
        manager = FormalToolManager(ToolResult.ok(data="shell", message="shell"))
        registry = _registry(
            ToolSpec(
                name="shell_command_tool",
                description="Shell.",
                parameters_schema={
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                    "additionalProperties": False,
                },
                required_params=["command"],
                aliases=["shell_tool"],
            )
        )
        executor = ReActExecutor(tool_manager=manager, tool_registry=registry)
        plan, step = _plan("shell_command_tool", {"command": "echo ok"})
        context = executor._create_context(plan, task={}, user_input="shell", history="")
        packet = _packet(context, "shell_tool", {"command": "echo ok"})

        observation = executor.dispatch_action(context, packet, step=step, confirmed=True)

        self.assertTrue(observation.success)
        self.assertEqual(manager.requests[0].tool_name, "shell_tool")
        self.assertIs(registry.get(manager.requests[0].tool_name), registry.get("shell_command_tool"))

    def test_tool_result_error_code_reaches_observation_unchanged(self):
        manager = FormalToolManager(
            ToolResult.fail("remote failed", code="provider_error", data={"retry": False})
        )
        registry = _registry(
            ToolSpec(
                name="provider_tool",
                description="Provider.",
                parameters_schema={"type": "object", "additionalProperties": True},
            )
        )
        executor = ReActExecutor(tool_manager=manager, tool_registry=registry)
        plan, step = _plan("provider_tool", {})
        context = executor._create_context(plan, task={}, user_input="provider", history="")
        packet = _packet(context, "provider_tool", {})

        observation = executor.dispatch_action(context, packet, step=step)

        self.assertFalse(observation.success)
        self.assertEqual(observation.code, "provider_error")
        self.assertEqual(observation.error, "remote failed")
        self.assertEqual(observation.data, {"retry": False})


def _registry(spec: ToolSpec) -> ToolRegistry:
    return ToolRegistry([spec])


def _plan(tool_name: str, args: dict) -> tuple[TaskPlan, PlanStep]:
    step = PlanStep(
        id="step-1",
        task_id="task-1",
        description="Execute tool.",
        step_type="tool",
        tool_name=tool_name,
        args=dict(args),
    )
    plan = TaskPlan(
        goal="integration",
        mode="micro",
        steps=[step],
        task_units=[TaskUnit(id="task-1", title="Integration", step_ids=["step-1"])],
        available_tools=[tool_name],
        required_tools=[tool_name],
        can_execute=True,
        plan_validation_status="valid",
    )
    return plan, step


def _packet(context, tool_name: str, args: dict) -> ActionPacket:
    return ActionPacket(
        execution_id=context.execution_id,
        plan_id=context.plan_id,
        task_id="task-1",
        step_id="step-1",
        action_type="call_tool",
        action_target=tool_name,
        action_args=dict(args),
    )


if __name__ == "__main__":
    unittest.main()
