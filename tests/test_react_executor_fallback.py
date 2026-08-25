from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.agent.planner import PlanStep, TaskPlan, TaskUnit
from src.agent.react_executor import CONFIRMATION_PENDING_CODE, ReActExecutor
from src.agent.react_executor_fallback import (
    FALLBACK_SCHEDULED_CODE,
    FALLBACK_TOOL_NOT_AVAILABLE_CODE,
    FallbackPolicy,
)
from src.agent.react_executor_logging import ReActExecutorLogger
from src.agent.react_executor_protocol import ActionPacket
from src.tools.base import ToolResult
from src.tools.registry import ToolRegistry, ToolSpec


class FakeFallbackToolManager:
    def __init__(self, results=None):
        self.results = list(results or [])
        self.run_calls = []

    def list_tools(self):
        return {
            "primary_tool": "Primary fake tool.",
            "fallback_tool": "Fallback fake tool.",
            "command_tool": "Command fake tool.",
        }

    def run_tool(self, tool_name: str, **kwargs):
        self.run_calls.append((tool_name, kwargs))
        if self.results:
            return self.results.pop(0)
        return ToolResult.ok(data={"tool": tool_name, "args": kwargs}, message=f"{tool_name} ok")


    def execute(self, request):
        return self.run_tool(request.tool_name, **request.args)
class FakeFallbackModelManager:
    def __init__(self, response="model fallback result"):
        self.response = response
        self.generate_calls = []

    def generate(self, prompt: str, **kwargs):
        self.generate_calls.append((prompt, kwargs))
        return self.response


class SequenceFallbackModelManager:
    def __init__(self, responses):
        self.responses = list(responses)
        self.generate_calls = []

    def generate(self, prompt: str, **kwargs):
        self.generate_calls.append((prompt, kwargs))
        if self.responses:
            return self.responses.pop(0)
        return "{}"


class ReActExecutorFallbackPolicyTest(unittest.TestCase):
    def test_policy_selects_plan_fallback_tool_when_available(self):
        policy = FallbackPolicy()
        step = _step(fallback_tools=["fallback_tool"])
        observation = _failed_observation()
        checker_result = _checker_result("fallback_to_tool")

        decision = policy.build_decision(
            observation,
            checker_result,
            step=step,
            requested_type="tool",
            available_tools={"primary_tool", "fallback_tool"},
        )

        self.assertTrue(decision.can_fallback)
        self.assertEqual(decision.fallback_type, "tool")
        self.assertEqual(decision.fallback_tool, "fallback_tool")

    def test_policy_rejects_unavailable_tool_without_model_fallback(self):
        policy = FallbackPolicy()
        step = _step(fallback_tools=["missing_tool"], allow_model_reasoning=False)

        decision = policy.build_decision(
            _failed_observation(),
            _checker_result("fallback_to_tool"),
            step=step,
            requested_type="tool",
            available_tools={"primary_tool"},
        )

        self.assertFalse(decision.can_fallback)
        self.assertEqual(decision.code, FALLBACK_TOOL_NOT_AVAILABLE_CODE)


class ReActExecutorFallbackActionTest(unittest.TestCase):
    def test_fallback_to_tool_replays_failed_action_with_fallback_tool(self):
        tool_manager = FakeFallbackToolManager(
            [
                ToolResult.fail("primary failed", code="tool_execution_failed"),
                ToolResult.ok(data="fallback data", message="fallback ok"),
            ]
        )
        executor = ReActExecutor(model_manager=FakeFallbackModelManager(), tool_manager=tool_manager, tool_registry=_registry())
        plan, step = _plan(fallback_tools=["fallback_tool"])
        context = executor._create_context(plan, task={}, user_input="run fallback", history="")

        first = executor.dispatch_action(context, _primary_packet(context), step=step)
        fallback = executor.dispatch_action(context, _fallback_tool_packet(context), step=step)

        self.assertFalse(first.success)
        self.assertTrue(fallback.success)
        self.assertEqual(fallback.action_type, "call_tool")
        self.assertEqual(fallback.tool_name, "fallback_tool")
        self.assertTrue(fallback.fallback_used)
        self.assertEqual(fallback.fallback_type, "tool")
        self.assertEqual(fallback.checker_result["fallback"]["code"], FALLBACK_SCHEDULED_CODE)
        self.assertEqual(tool_manager.run_calls[0][0], "primary_tool")
        self.assertEqual(tool_manager.run_calls[1][0], "fallback_tool")
        self.assertIn("fallback_started", _event_types(context))
        self.assertIn("fallback_finished", _event_types(context))
        self.assertIs(context.observation_store.get_by_output_key("result"), fallback)

    def test_fallback_to_model_uses_model_manager_and_marks_observation(self):
        tool_manager = FakeFallbackToolManager([ToolResult.fail("primary failed", code="tool_execution_failed")])
        model_manager = FakeFallbackModelManager("model recovered")
        executor = ReActExecutor(model_manager=model_manager, tool_manager=tool_manager, tool_registry=_registry())
        plan, step = _plan(allow_model_reasoning=True)
        context = executor._create_context(plan, task={}, user_input="run fallback", history="")

        first = executor.dispatch_action(context, _primary_packet(context), step=step)
        fallback = executor.dispatch_action(context, _fallback_model_packet(context), step=step)

        self.assertFalse(first.success)
        self.assertTrue(fallback.success)
        self.assertEqual(fallback.action_type, "call_model")
        self.assertEqual(fallback.data, "model recovered")
        self.assertTrue(fallback.fallback_used)
        self.assertEqual(fallback.fallback_type, "model")
        self.assertEqual(len(model_manager.generate_calls), 1)
        self.assertIn("primary failed", model_manager.generate_calls[0][0])
        self.assertIn("fallback_started", _event_types(context))
        self.assertIn("fallback_finished", _event_types(context))

    def test_unavailable_fallback_tool_can_degrade_to_model_when_allowed(self):
        tool_manager = FakeFallbackToolManager([ToolResult.fail("primary failed", code="tool_execution_failed")])
        model_manager = FakeFallbackModelManager("model fallback")
        executor = ReActExecutor(model_manager=model_manager, tool_manager=tool_manager, tool_registry=_registry())
        plan, step = _plan(fallback_tools=["missing_tool"], allow_model_reasoning=True)
        context = executor._create_context(plan, task={}, user_input="run fallback", history="")

        executor.dispatch_action(context, _primary_packet(context), step=step)
        fallback = executor.dispatch_action(
            context,
            _fallback_tool_packet(context, action_target="missing_tool"),
            step=step,
        )

        self.assertTrue(fallback.success)
        self.assertEqual(fallback.action_type, "call_model")
        self.assertEqual(fallback.fallback_type, "model")
        self.assertEqual(len(model_manager.generate_calls), 1)

    def test_unavailable_fallback_tool_without_model_returns_structured_failure(self):
        tool_manager = FakeFallbackToolManager([ToolResult.fail("primary failed", code="tool_execution_failed")])
        executor = ReActExecutor(model_manager=FakeFallbackModelManager(), tool_manager=tool_manager, tool_registry=_registry())
        plan, step = _plan(fallback_tools=["missing_tool"], allow_model_reasoning=False)
        context = executor._create_context(plan, task={}, user_input="run fallback", history="")

        executor.dispatch_action(context, _primary_packet(context), step=step)
        fallback = executor.dispatch_action(
            context,
            _fallback_tool_packet(context, action_target="missing_tool"),
            step=step,
        )

        self.assertFalse(fallback.success)
        self.assertEqual(fallback.code, FALLBACK_TOOL_NOT_AVAILABLE_CODE)
        self.assertEqual(len(tool_manager.run_calls), 1)
        self.assertIn("fallback_finished", _event_types(context))

    def test_command_fallback_requires_confirmation_and_does_not_execute_tool(self):
        tool_manager = FakeFallbackToolManager([ToolResult.fail("primary failed", code="tool_execution_failed")])
        executor = ReActExecutor(model_manager=FakeFallbackModelManager(), tool_manager=tool_manager, tool_registry=_registry())
        plan, step = _plan(fallback_tools=["command_tool"])
        context = executor._create_context(plan, task={}, user_input="run command fallback", history="")

        executor.dispatch_action(context, _primary_packet(context), step=step)
        fallback = executor.dispatch_action(
            context,
            _fallback_tool_packet(
                context,
                action_target="command_tool",
                action_args={
                    "step_id": "step_1",
                    "fallback_reason": "primary failed",
                    "command": "python --version",
                    "cwd": ".",
                    "purpose": "diagnostic",
                    "risk_level": "low",
                    "requires_confirmation": True,
                    "expected_result": "command completes successfully",
                    "timeout_seconds": 5,
                },
            ),
            step=step,
        )

        self.assertFalse(fallback.success)
        self.assertEqual(fallback.code, CONFIRMATION_PENDING_CODE)
        self.assertTrue(fallback.fallback_used)
        self.assertEqual(fallback.fallback_type, "tool")
        self.assertIsNotNone(context.pending_confirmation)
        self.assertEqual([call[0] for call in tool_manager.run_calls], ["primary_tool"])

    def test_main_loop_consumes_checker_fallback_to_tool_decision(self):
        tool_manager = FakeFallbackToolManager(
            [
                ToolResult.fail("primary failed", code="file_exists"),
                ToolResult.ok(data="fallback data", message="fallback ok"),
            ]
        )
        model_manager = SequenceFallbackModelManager(
            [
                json.dumps(
                    {
                        "action_type": "call_tool",
                        "action_target": "primary_tool",
                        "action_args": {"query": "topic"},
                    }
                )
            ]
        )
        executor = ReActExecutor(model_manager=model_manager, tool_manager=tool_manager, tool_registry=_registry())
        plan, _step = _plan(fallback_tools=["fallback_tool"])

        result = executor.execute(plan, task={}, user_input="run fallback")

        self.assertTrue(result.success)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.step_statuses["step_1"], "completed")
        self.assertEqual([call[0] for call in tool_manager.run_calls], ["primary_tool", "fallback_tool"])
        self.assertEqual(len(model_manager.generate_calls), 1)
        self.assertEqual(sum(1 for observation in result.observations if observation.fallback_used), 1)
        event_types = [event.type for event in result.events]
        self.assertIn("fallback_started", event_types)
        self.assertIn("fallback_finished", event_types)

    def test_main_loop_consumes_checker_fallback_to_model_decision(self):
        tool_manager = FakeFallbackToolManager([ToolResult.fail("primary failed", code="file_exists")])
        model_manager = SequenceFallbackModelManager(
            [
                json.dumps(
                    {
                        "action_type": "call_tool",
                        "action_target": "primary_tool",
                        "action_args": {"query": "topic"},
                    }
                ),
                "model recovered",
            ]
        )
        executor = ReActExecutor(model_manager=model_manager, tool_manager=tool_manager, tool_registry=_registry())
        plan, _step = _plan(allow_model_reasoning=True)

        result = executor.execute(plan, task={}, user_input="run model fallback")

        self.assertTrue(result.success)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.step_statuses["step_1"], "completed")
        self.assertEqual([call[0] for call in tool_manager.run_calls], ["primary_tool"])
        self.assertEqual(len(model_manager.generate_calls), 2)
        self.assertEqual(sum(1 for observation in result.observations if observation.fallback_type == "model"), 1)

    def test_main_loop_falls_back_to_tool_after_retry_exhausted(self):
        tool_manager = FakeFallbackToolManager(
            [
                ToolResult.fail("timeout", code="command_timeout"),
                ToolResult.fail("still timeout", code="command_timeout"),
                ToolResult.ok(data="fallback data", message="fallback ok"),
            ]
        )
        model_manager = SequenceFallbackModelManager(
            [
                json.dumps(
                    {
                        "action_type": "call_tool",
                        "action_target": "primary_tool",
                        "action_args": {"query": "topic"},
                    }
                )
            ]
        )
        executor = ReActExecutor(
            model_manager=model_manager,
            tool_manager=tool_manager,
            tool_registry=_registry(),
            retry_sleep_fn=lambda _seconds: None,
        )
        plan, _step = _plan(fallback_tools=["fallback_tool"], retryable=True, max_retries=1)

        result = executor.execute(plan, task={}, user_input="retry then fallback")

        self.assertTrue(result.success)
        self.assertEqual(result.status, "completed")
        self.assertEqual([call[0] for call in tool_manager.run_calls], ["primary_tool", "primary_tool", "fallback_tool"])
        self.assertEqual(sum(1 for observation in result.observations if observation.fallback_used), 1)
        event_types = [event.type for event in result.events]
        self.assertIn("retry_exhausted", event_types)
        self.assertIn("fallback_started", event_types)
        self.assertIn("fallback_finished", event_types)

    def test_main_loop_unavailable_fallback_tool_fails_without_looping(self):
        tool_manager = FakeFallbackToolManager([ToolResult.fail("primary failed", code="file_exists")])
        model_manager = SequenceFallbackModelManager(
            [
                json.dumps(
                    {
                        "action_type": "call_tool",
                        "action_target": "primary_tool",
                        "action_args": {"query": "topic"},
                    }
                )
            ]
        )
        executor = ReActExecutor(model_manager=model_manager, tool_manager=tool_manager, tool_registry=_registry())
        plan, _step = _plan(fallback_tools=["missing_tool"], allow_model_reasoning=False)

        result = executor.execute(plan, task={}, user_input="fallback unavailable")

        self.assertFalse(result.success)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_code, FALLBACK_TOOL_NOT_AVAILABLE_CODE)
        self.assertEqual([call[0] for call in tool_manager.run_calls], ["primary_tool"])
        event_types = [event.type for event in result.events]
        self.assertEqual(event_types.count("fallback_finished"), 1)

    def test_main_loop_logs_fallback_decision_metadata(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            logger = ReActExecutorLogger(Path(tmp_dir) / "react_executor.log", keep_in_memory=True)
            tool_manager = FakeFallbackToolManager(
                [
                    ToolResult.fail("primary failed", code="file_exists"),
                    ToolResult.ok(data="fallback data", message="fallback ok"),
                ]
            )
            model_manager = SequenceFallbackModelManager(
                [
                    json.dumps(
                        {
                            "action_type": "call_tool",
                            "action_target": "primary_tool",
                            "action_args": {"query": "topic"},
                        }
                    )
                ]
            )
            executor = ReActExecutor(
                model_manager=model_manager,
                tool_manager=tool_manager,
                tool_registry=_registry(),
                execution_logger=logger,
            )
            plan, _step = _plan(fallback_tools=["fallback_tool"])

            result = executor.execute(plan, task={}, user_input="fallback with logs")

        fallback_records = [record for record in logger.in_memory_records if record["record_type"] == "fallback_decision"]
        self.assertTrue(result.success)
        self.assertGreaterEqual(len(fallback_records), 2)
        self.assertIn("scheduled", [record["metadata"]["outcome"] for record in fallback_records])
        self.assertIn("finished", [record["metadata"]["outcome"] for record in fallback_records])
        self.assertTrue(any(record["metadata"]["fallback"]["code"] == FALLBACK_SCHEDULED_CODE for record in fallback_records))


def _registry() -> ToolRegistry:
    return ToolRegistry(
        [
            ToolSpec(
                name="primary_tool",
                description="Primary fake tool.",
                parameters_schema={"type": "object", "properties": {"query": {"type": "string"}}},
                required_params=["query"],
                fallback_tools=["fallback_tool"],
            ),
            ToolSpec(
                name="fallback_tool",
                description="Fallback fake tool.",
                parameters_schema={"type": "object", "properties": {"query": {"type": "string"}, "fallback_reason": {"type": "string"}}},
                required_params=["query"],
            ),
            ToolSpec(
                name="command_tool",
                description="Command fallback tool.",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                        "cwd": {"type": "string"},
                        "purpose": {"type": "string"},
                        "risk_level": {"type": "string"},
                        "timeout_seconds": {"type": "integer"},
                    },
                },
                required_params=["command", "cwd", "purpose"],
                risk_level="high",
                requires_confirmation=True,
                workspace_scope="command",
            ),
        ]
    )


def _plan(
    *,
    fallback_tools=None,
    allow_model_reasoning=False,
    retryable: bool = False,
    max_retries: int = 3,
) -> tuple[TaskPlan, PlanStep]:
    step = _step(
        fallback_tools=fallback_tools or [],
        allow_model_reasoning=allow_model_reasoning,
        retryable=retryable,
        max_retries=max_retries,
    )
    plan = TaskPlan(
        goal="fallback demo",
        mode="micro",
        steps=[step],
        task_units=[TaskUnit(id="task_1", title="Fallback", step_ids=["step_1"])],
        available_tools=["primary_tool", "fallback_tool", "command_tool"],
        required_tools=["primary_tool"],
        can_execute=True,
        plan_validation_status="valid",
    )
    return plan, step


def _step(*, fallback_tools=None, allow_model_reasoning=False, retryable: bool = False, max_retries: int = 3) -> PlanStep:
    return PlanStep(
        id="step_1",
        task_id="task_1",
        description="Run primary tool.",
        step_type="tool",
        tool_name="primary_tool",
        args={"query": "topic"},
        output_key="result",
        fallback_tools=list(fallback_tools or []),
        allow_model_reasoning=allow_model_reasoning,
        retryable=retryable,
        max_retries=max_retries,
    )


def _primary_packet(context) -> ActionPacket:
    return ActionPacket(
        execution_id=context.execution_id,
        plan_id=context.plan_id,
        task_id="task_1",
        step_id="step_1",
        action_type="call_tool",
        action_target="primary_tool",
        action_args={"query": "topic"},
    )


def _fallback_tool_packet(context, *, action_target="fallback_tool", action_args=None) -> ActionPacket:
    return ActionPacket(
        execution_id=context.execution_id,
        plan_id=context.plan_id,
        task_id="task_1",
        step_id="step_1",
        action_type="fallback_to_tool",
        action_target=action_target,
        action_args=action_args or {"step_id": "step_1", "fallback_reason": "primary failed"},
    )


def _fallback_model_packet(context) -> ActionPacket:
    return ActionPacket(
        execution_id=context.execution_id,
        plan_id=context.plan_id,
        task_id="task_1",
        step_id="step_1",
        action_type="fallback_to_model",
        action_args={"step_id": "step_1", "fallback_reason": "primary failed"},
    )


def _failed_observation():
    from src.agent.react_executor_protocol import ObservationPacket

    return ObservationPacket(
        execution_id="execution_1",
        plan_id="plan_1",
        task_id="task_1",
        step_id="step_1",
        packet_id="action_1",
        action_type="call_tool",
        action_target="primary_tool",
        tool_name="primary_tool",
        input_args={"query": "topic"},
        success=False,
        message="primary failed",
        error="primary failed",
        code="tool_execution_failed",
    )


def _checker_result(status: str):
    from src.agent.react_executor_checker import CheckerResult

    return CheckerResult(
        checker_status=status,
        success=False,
        reason="primary failed",
        code="tool_execution_failed",
        fallback_type="tool" if status == "fallback_to_tool" else "model",
        fallback_tool="fallback_tool" if status == "fallback_to_tool" else None,
        metadata={"failure_class": "unknown_failure"},
    )


def _event_types(context) -> list[str]:
    return [event.type for event in context.event_stream.events]


if __name__ == "__main__":
    unittest.main()
