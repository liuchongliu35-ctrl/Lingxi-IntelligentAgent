from __future__ import annotations

import json
import unittest

from src.agent.planner import PlanStep, TaskPlan, TaskUnit
from src.agent.react_executor import ReActExecutor
from src.agent.react_executor_checker import (
    EMPTY_OUTPUT_CODE,
    MAX_TURNS_REACHED_CODE,
    LLMChecker,
    RuleChecker,
    classify_tool_result_code,
)
from src.agent.react_executor_protocol import ObservationPacket


class FakeLLMCheckerModel:
    def __init__(self, response: str):
        self.response = response
        self.prompts = []

    def generate(self, prompt: str, **kwargs):
        self.prompts.append((prompt, kwargs))
        return self.response


class SequenceCheckerModelManager:
    def __init__(self, responses):
        self.responses = list(responses)
        self.generate_calls = []

    def generate(self, prompt: str, **kwargs):
        self.generate_calls.append((prompt, kwargs))
        if self.responses:
            return self.responses.pop(0)
        return "{}"


class ReActExecutorCheckerTest(unittest.TestCase):
    def setUp(self):
        self.checker = RuleChecker(default_max_retries=3, max_step_turns=5, max_execution_turns=20)

    def test_success_with_output_completes_step(self):
        step = _step()
        observation = _observation(success=True, data="5", message="5")

        result = self.checker.check_observation(observation, step=step)

        self.assertEqual(result.checker_status, "step_completed")
        self.assertTrue(result.success)
        self.assertEqual(result.step_status, "completed")

    def test_empty_success_does_not_blindly_continue(self):
        step = _step(retryable=False)
        observation = _observation(success=True, data=None, message="", model_consumable_observation=None)

        result = self.checker.check_observation(observation, step=step)

        self.assertEqual(result.checker_status, "fail")
        self.assertEqual(result.code, EMPTY_OUTPUT_CODE)

    def test_empty_success_can_retry_when_step_allows_retry(self):
        step = _step(retryable=True, max_retries=2)
        observation = _observation(success=True, data=None, message="", model_consumable_observation=None, attempt=1)

        result = self.checker.check_observation(observation, step=step)

        self.assertEqual(result.checker_status, "retry")
        self.assertTrue(result.retryable)
        self.assertEqual(result.step_status, "retrying")

    def test_retryable_tool_failure_triggers_retry(self):
        step = _step(retryable=True, max_retries=3)
        observation = _observation(success=False, error="timed out", code="command_timeout", attempt=1)

        result = self.checker.check_observation(observation, step=step)

        self.assertEqual(result.checker_status, "retry")
        self.assertEqual(result.metadata["failure_class"], "timeout")

    def test_retry_exhaustion_fails_at_max_step_turns(self):
        step = _step(retryable=True, max_retries=10)
        observation = _observation(success=False, error="still failing", code="command_timeout", attempt=5)

        result = self.checker.check_observation(observation, step=step, max_step_turns=5)

        self.assertEqual(result.checker_status, "fail")
        self.assertEqual(result.code, MAX_TURNS_REACHED_CODE)
        self.assertEqual(result.metadata["limit_type"], "step")

    def test_fallback_tool_is_selected_after_non_retryable_failure(self):
        step = _step(fallback_tools=["document_parser"])
        observation = _observation(success=False, error="already exists", code="file_exists")

        result = self.checker.check_observation(observation, step=step)

        self.assertEqual(result.checker_status, "fallback_to_tool")
        self.assertEqual(result.fallback_type, "tool")
        self.assertEqual(result.fallback_tool, "document_parser")

    def test_fallback_model_is_selected_when_allowed(self):
        step = _step(allow_model_reasoning=True)
        observation = _observation(success=False, error="tool unavailable", code="tool_manager_unavailable")

        result = self.checker.check_observation(observation, step=step)

        self.assertEqual(result.checker_status, "fallback_to_model")
        self.assertEqual(result.fallback_type, "model")

    def test_ask_user_is_returned_for_confirmation_required(self):
        observation = _observation(success=False, message="Approve?", code="confirmation_pending")

        result = self.checker.check_observation(observation, step=_step())

        self.assertEqual(result.checker_status, "ask_user")
        self.assertTrue(result.requires_user_input)
        self.assertEqual(result.step_status, "waiting_user")

    def test_request_replan_is_returned_from_observation(self):
        observation = _observation(
            success=False,
            message="plan stale",
            code="request_replan",
            data={"request_replan": True, "reason": "plan stale"},
        )

        result = self.checker.check_observation(observation, step=_step())

        self.assertEqual(result.checker_status, "request_replan")
        self.assertTrue(result.request_replan)
        self.assertEqual(result.execution_status, "request_replan")

    def test_safety_violation_fails_without_retry_or_fallback(self):
        step = _step(retryable=True, fallback_tools=["safe_tool"], allow_model_reasoning=True)
        observation = _observation(success=False, error="blocked", code="command_blocked")

        result = self.checker.check_observation(observation, step=step)

        self.assertEqual(result.checker_status, "fail")
        self.assertEqual(result.step_status, "blocked")
        self.assertEqual(result.metadata["failure_class"], "safety_violation")

    def test_dependency_failure_can_request_replan_by_policy(self):
        step = _step(on_failure="request_replan")
        observation = _observation(success=False, error="missing upstream", code="tool_input_ref_missing")

        result = self.checker.check_observation(observation, step=step)

        self.assertEqual(result.checker_status, "request_replan")
        self.assertEqual(result.metadata["failure_class"], "dependency_failure")

    def test_max_execution_turns_fails(self):
        observation = _observation(success=False, error="looping", code="tool_execution_failed")

        result = self.checker.check_observation(observation, step=_step(), current_execution_turn=20, max_execution_turns=20)

        self.assertEqual(result.checker_status, "fail")
        self.assertEqual(result.code, MAX_TURNS_REACHED_CODE)
        self.assertEqual(result.metadata["limit_type"], "execution")

    def test_success_at_execution_turn_limit_can_complete(self):
        observation = _observation(success=True, data="done", message="done")

        result = self.checker.check_observation(observation, step=_step(), current_execution_turn=20, max_execution_turns=20)

        self.assertEqual(result.checker_status, "step_completed")

    def test_tool_result_code_classification(self):
        self.assertEqual(classify_tool_result_code("command_timeout"), "timeout")
        self.assertEqual(classify_tool_result_code("tool_argument_validation_failed"), "validation_failure")
        self.assertEqual(classify_tool_result_code("command_blocked"), "safety_violation")
        self.assertEqual(classify_tool_result_code("temporary_network_error"), "retryable")

    def test_llm_checker_parses_structured_result(self):
        model = FakeLLMCheckerModel(
            '{"checker_status":"request_replan","success":false,"reason":"expected output cannot be met","request_replan":true}'
        )
        checker = LLMChecker(model)

        result = checker.check_observation(_observation(success=True, data="partial"), step=_step(expected_output="exact result"))

        self.assertEqual(result.checker_status, "request_replan")
        self.assertTrue(result.request_replan)
        self.assertEqual(len(model.prompts), 1)

    def test_executor_exposes_checker_without_mutating_dispatcher_behavior(self):
        executor = ReActExecutor(model_manager=None, tool_manager=None)
        plan, step = _plan()
        context = executor._create_context(plan, task={}, user_input="demo", history="")
        observation = _observation(
            execution_id=context.execution_id,
            plan_id=context.plan_id,
            task_id="task_1",
            step_id="step_1",
            success=True,
            data="done",
            message="done",
        )

        result = executor.check_observation(context, observation, step=step)

        self.assertEqual(result.checker_status, "step_completed")

    def test_main_loop_stops_on_checker_request_replan_decision(self):
        model_manager = SequenceCheckerModelManager(
            [
                json.dumps(
                    {
                        "action_type": "request_replan",
                        "request_replan_reason": "tool contract changed",
                        "user_visible_message": "Need a revised plan.",
                    }
                )
            ]
        )
        executor = ReActExecutor(model_manager=model_manager, tool_manager=None)
        plan, _step = _plan()

        result = executor.execute(plan, task={}, user_input="demo")

        self.assertFalse(result.success)
        self.assertEqual(result.status, "request_replan")
        self.assertTrue(result.request_replan)
        self.assertIn("tool contract changed", result.replan_reason or "")
        self.assertEqual(result.step_statuses["step_1"], "failed")
        self.assertEqual(len(model_manager.generate_calls), 1)


def _step(**kwargs) -> PlanStep:
    defaults = {
        "id": "step_1",
        "task_id": "task_1",
        "description": "Run step.",
        "step_type": "tool",
        "tool_name": "math_calculator",
        "args": {"expression": "2+3"},
        "expected_output": "result",
    }
    defaults.update(kwargs)
    return PlanStep(**defaults)


def _plan() -> tuple[TaskPlan, PlanStep]:
    step = _step()
    return (
        TaskPlan(
            goal="demo",
            mode="micro",
            steps=[step],
            task_units=[TaskUnit(id="task_1", title="Demo", step_ids=["step_1"])],
            available_tools=["math_calculator"],
            required_tools=["math_calculator"],
            can_execute=True,
            plan_validation_status="valid",
        ),
        step,
    )


def _observation(**kwargs) -> ObservationPacket:
    defaults = {
        "execution_id": "execution_1",
        "plan_id": "plan_1",
        "task_id": "task_1",
        "step_id": "step_1",
        "action_type": "call_tool",
        "action_target": "math_calculator",
        "tool_name": "math_calculator",
        "success": False,
        "message": "",
        "error": None,
        "code": None,
        "data": None,
        "model_consumable_observation": None,
        "attempt": 1,
    }
    defaults.update(kwargs)
    return ObservationPacket(**defaults)


if __name__ == "__main__":
    unittest.main()
