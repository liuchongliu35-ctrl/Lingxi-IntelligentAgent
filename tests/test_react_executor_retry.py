from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.agent.planner import PlanStep, TaskPlan, TaskUnit
from src.agent.react_executor import ReActExecutor
from src.agent.react_executor_checker import RuleChecker
from src.agent.react_executor_logging import ReActExecutorLogger
from src.agent.react_executor_protocol import ActionPacket
from src.agent.react_executor_retry import (
    RETRY_EXHAUSTED_CODE,
    RETRY_NOT_RETRYABLE_CODE,
    RETRY_SCHEDULED_CODE,
    RetryPolicy,
)
from src.tools.base import ToolResult


class FakeRetryToolManager:
    def __init__(self, results):
        self.results = list(results)
        self.run_calls = []

    def list_tools(self):
        return {"math_calculator": "Fake calculator."}

    def run_tool(self, tool_name: str, **kwargs):
        self.run_calls.append((tool_name, kwargs))
        if self.results:
            return self.results.pop(0)
        return ToolResult.ok(data="ok", message="ok")


    def execute(self, request):
        return self.run_tool(request.tool_name, **request.args)
class SequenceActionModelManager:
    def __init__(self, responses):
        self.responses = list(responses)
        self.generate_calls = []

    def generate(self, prompt: str, **kwargs):
        self.generate_calls.append((prompt, kwargs))
        if self.responses:
            return self.responses.pop(0)
        return "{}"


class ReActExecutorRetryPolicyTest(unittest.TestCase):
    def test_backoff_uses_exponential_formula_and_cap(self):
        policy = RetryPolicy(backoff_base_seconds=0.25, backoff_max_seconds=1.0, sleep_fn=None)

        self.assertEqual(policy.calculate_backoff_seconds(0), 0.25)
        self.assertEqual(policy.calculate_backoff_seconds(1), 0.5)
        self.assertEqual(policy.calculate_backoff_seconds(2), 1.0)
        self.assertEqual(policy.calculate_backoff_seconds(8), 1.0)

    def test_retryable_failure_builds_retry_decision(self):
        policy = RetryPolicy(default_max_retries=2, backoff_base_seconds=0.1, backoff_max_seconds=1.0, sleep_fn=None)
        checker = RuleChecker(default_max_retries=2)
        observation = _failed_observation(code="command_timeout", attempt=1)
        step = _step(max_retries=2, retryable=True)
        checker_result = checker.check_observation(observation, step=step)

        decision = policy.build_decision(observation, checker_result, step=step)

        self.assertTrue(decision.can_retry)
        self.assertEqual(decision.code, RETRY_SCHEDULED_CODE)
        self.assertEqual(decision.retry_count, 0)
        self.assertEqual(decision.next_attempt, 2)
        self.assertEqual(decision.backoff_seconds, 0.1)

    def test_non_retryable_failure_is_rejected(self):
        policy = RetryPolicy(default_max_retries=2, sleep_fn=None)
        checker = RuleChecker(default_max_retries=2)
        observation = _failed_observation(code="tool_argument_validation_failed", attempt=1)
        step = _step(max_retries=2, retryable=True)
        checker_result = checker.check_observation(observation, step=step)

        decision = policy.build_decision(observation, checker_result, step=step)

        self.assertFalse(decision.can_retry)
        self.assertEqual(decision.code, RETRY_NOT_RETRYABLE_CODE)

    def test_retry_exhaustion_uses_retry_count_not_action_attempt_count(self):
        policy = RetryPolicy(default_max_retries=2, sleep_fn=None)
        checker = RuleChecker(default_max_retries=2)
        step = _step(max_retries=2, retryable=True)
        second_failure = _failed_observation(code="command_timeout", attempt=3)
        checker_result = checker.check_observation(second_failure, step=step)

        decision = policy.build_decision(second_failure, checker_result, step=step)

        self.assertFalse(decision.can_retry)
        self.assertEqual(decision.code, RETRY_EXHAUSTED_CODE)
        self.assertEqual(decision.retry_count, 2)


class ReActExecutorRetryActionTest(unittest.TestCase):
    def test_retry_step_replays_failed_tool_action_and_records_retry_metadata(self):
        tool_manager = FakeRetryToolManager(
            [
                ToolResult.fail("timeout", code="command_timeout"),
                ToolResult.ok(data="5", message="5"),
            ]
        )
        sleeps = []
        executor = ReActExecutor(model_manager=None, tool_manager=tool_manager, retry_sleep_fn=sleeps.append)
        plan, step = _plan(max_retries=2, retryable=True)
        context = executor._create_context(plan, task={}, user_input="calculate", history="")
        first_packet = _tool_packet(context)

        first = executor.dispatch_action(context, first_packet, step=step)
        retry_packet = ActionPacket(
            execution_id=context.execution_id,
            plan_id=context.plan_id,
            task_id="task_1",
            step_id="step_1",
            action_type="retry_step",
            action_args={"step_id": "step_1"},
        )
        retry = executor.dispatch_action(context, retry_packet, step=step)

        self.assertFalse(first.success)
        self.assertTrue(retry.success)
        self.assertEqual(retry.attempt, 2)
        self.assertEqual(tool_manager.run_calls, [("math_calculator", {"expression": "2+3"}), ("math_calculator", {"expression": "2+3"})])
        self.assertEqual(len(sleeps), 1)
        self.assertIn("retry", retry.checker_result)
        self.assertEqual(retry.checker_result["retry"]["code"], RETRY_SCHEDULED_CODE)
        self.assertEqual(retry.checker_result["retry"]["retried_from_observation_id"], first.observation_id)
        self.assertIn("retry_scheduled", _event_types(context))
        self.assertIn("retry_finished", _event_types(context))
        self.assertIs(context.observation_store.get_by_output_key("calculation"), retry)

    def test_non_retryable_failure_does_not_call_tool_again(self):
        tool_manager = FakeRetryToolManager([ToolResult.fail("bad args", code="tool_argument_validation_failed")])
        executor = ReActExecutor(model_manager=None, tool_manager=tool_manager, retry_sleep_fn=None)
        plan, step = _plan(max_retries=2, retryable=True)
        context = executor._create_context(plan, task={}, user_input="calculate", history="")
        first = executor.dispatch_action(context, _tool_packet(context), step=step)

        retry = executor.dispatch_action(
            context,
            ActionPacket(
                execution_id=context.execution_id,
                plan_id=context.plan_id,
                task_id="task_1",
                step_id="step_1",
                action_type="retry_step",
                action_args={"step_id": "step_1"},
            ),
            step=step,
        )

        self.assertFalse(first.success)
        self.assertFalse(retry.success)
        self.assertEqual(retry.code, RETRY_NOT_RETRYABLE_CODE)
        self.assertEqual(len(tool_manager.run_calls), 1)
        self.assertIn("retry_scheduled", _event_types(context))
        self.assertNotIn("retry_finished", _event_types(context))

    def test_retry_step_can_target_failed_packet_id(self):
        tool_manager = FakeRetryToolManager([ToolResult.fail("timeout", code="command_timeout"), ToolResult.ok(data="5", message="5")])
        executor = ReActExecutor(model_manager=None, tool_manager=tool_manager, retry_sleep_fn=None)
        plan, step = _plan(max_retries=2, retryable=True)
        context = executor._create_context(plan, task={}, user_input="calculate", history="")
        first = executor.dispatch_action(context, _tool_packet(context), step=step)

        retry = executor.dispatch_action(
            context,
            ActionPacket(
                execution_id=context.execution_id,
                plan_id=context.plan_id,
                task_id="task_1",
                step_id="step_1",
                action_type="retry_step",
                action_args={"packet_id": first.packet_id},
            ),
            step=step,
        )

        self.assertTrue(retry.success)
        self.assertEqual(retry.checker_result["retry"]["source_packet_id"], first.packet_id)

    def test_main_loop_consumes_checker_retry_decision(self):
        tool_manager = FakeRetryToolManager(
            [
                ToolResult.fail("timeout", code="command_timeout"),
                ToolResult.ok(data="5", message="5"),
            ]
        )
        model_manager = SequenceActionModelManager(
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
        executor = ReActExecutor(
            model_manager=model_manager,
            tool_manager=tool_manager,
            retry_sleep_fn=lambda _seconds: None,
        )
        plan, _step = _plan(max_retries=2, retryable=True)

        result = executor.execute(plan, task={}, user_input="calculate")

        self.assertTrue(result.success)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.step_statuses["step_1"], "completed")
        self.assertEqual(len(model_manager.generate_calls), 1)
        self.assertEqual(
            tool_manager.run_calls,
            [("math_calculator", {"expression": "2+3"}), ("math_calculator", {"expression": "2+3"})],
        )
        event_types = [event.type for event in result.events]
        self.assertIn("retry_scheduled", event_types)
        self.assertIn("retry_finished", event_types)

    def test_main_loop_retry_updates_step_attempts_and_logs_metadata(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            logger = ReActExecutorLogger(Path(tmp_dir) / "react_executor.log", keep_in_memory=True)
            tool_manager = FakeRetryToolManager(
                [
                    ToolResult.fail("timeout", code="command_timeout"),
                    ToolResult.ok(data="5", message="5"),
                ]
            )
            model_manager = SequenceActionModelManager(
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
            executor = ReActExecutor(
                model_manager=model_manager,
                tool_manager=tool_manager,
                retry_sleep_fn=lambda _seconds: None,
                execution_logger=logger,
            )
            plan, _step = _plan(max_retries=2, retryable=True)
            context = executor._create_context(plan, task={}, user_input="calculate", history="")

            result = executor._execute_react_loop(context)

        retry_records = [record for record in logger.in_memory_records if record["record_type"] == "retry_decision"]
        self.assertTrue(result.success)
        self.assertEqual(context.step_states["step_1"].attempts, 2)
        self.assertGreaterEqual(len(retry_records), 2)
        self.assertIn("scheduled", [record["metadata"]["outcome"] for record in retry_records])
        self.assertIn("finished", [record["metadata"]["outcome"] for record in retry_records])
        self.assertTrue(any(record["metadata"]["retry"]["code"] == RETRY_SCHEDULED_CODE for record in retry_records))
        retry_observations = [observation for observation in result.observations if "retry" in observation.checker_result]
        self.assertEqual(len(retry_observations), 1)
        self.assertEqual(retry_observations[0].checker_result["retry"]["code"], RETRY_SCHEDULED_CODE)

    def test_main_loop_emits_retry_exhausted_after_failed_retry(self):
        tool_manager = FakeRetryToolManager(
            [
                ToolResult.fail("timeout", code="command_timeout"),
                ToolResult.fail("still timeout", code="command_timeout"),
            ]
        )
        model_manager = SequenceActionModelManager(
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
        executor = ReActExecutor(
            model_manager=model_manager,
            tool_manager=tool_manager,
            retry_sleep_fn=lambda _seconds: None,
        )
        plan, _step = _plan(max_retries=1, retryable=True)
        context = executor._create_context(plan, task={}, user_input="calculate", history="")

        result = executor._execute_react_loop(context)

        self.assertFalse(result.success)
        self.assertEqual(result.status, "failed")
        self.assertEqual(context.step_states["step_1"].attempts, 2)
        self.assertEqual(len(tool_manager.run_calls), 2)
        event_types = [event.type for event in result.events]
        self.assertIn("retry_scheduled", event_types)
        self.assertIn("retry_finished", event_types)
        self.assertIn("retry_exhausted", event_types)


def _plan(*, max_retries: int, retryable: bool) -> tuple[TaskPlan, PlanStep]:
    step = _step(max_retries=max_retries, retryable=retryable)
    plan = TaskPlan(
        goal="calculate",
        mode="micro",
        steps=[step],
        task_units=[TaskUnit(id="task_1", title="Calculate", step_ids=["step_1"])],
        available_tools=["math_calculator"],
        required_tools=["math_calculator"],
        can_execute=True,
        plan_validation_status="valid",
    )
    return plan, step


def _step(*, max_retries: int = 2, retryable: bool = True) -> PlanStep:
    return PlanStep(
        id="step_1",
        task_id="task_1",
        description="Calculate.",
        step_type="tool",
        tool_name="math_calculator",
        args={"expression": "2+3"},
        output_key="calculation",
        retryable=retryable,
        max_retries=max_retries,
    )


def _tool_packet(context) -> ActionPacket:
    return ActionPacket(
        execution_id=context.execution_id,
        plan_id=context.plan_id,
        task_id="task_1",
        step_id="step_1",
        action_type="call_tool",
        action_target="math_calculator",
        action_args={"expression": "2+3"},
    )


def _failed_observation(*, code: str, attempt: int):
    from src.agent.react_executor_protocol import ObservationPacket

    return ObservationPacket(
        execution_id="execution_1",
        plan_id="plan_1",
        task_id="task_1",
        step_id="step_1",
        packet_id="action_1",
        action_type="call_tool",
        action_target="math_calculator",
        tool_name="math_calculator",
        input_args={"expression": "2+3"},
        success=False,
        message="failed",
        error="failed",
        code=code,
        attempt=attempt,
    )


def _event_types(context) -> list[str]:
    return [event.type for event in context.event_stream.events]


if __name__ == "__main__":
    unittest.main()
