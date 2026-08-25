from __future__ import annotations

from types import SimpleNamespace
import unittest
from pathlib import Path

from src.agent.complexity_analyzer import AnalysisResult, ComplexityAnalyzer
from src.agent.executor import Executor
from src.agent.planner import PlanStep, Planner, TaskPlan, TaskUnit
from src.agent.planner_config import load_planner_config
from src.agent.react_executor import (
    ACTION_PACKET_MODEL_EXCEPTION_CODE,
    MODEL_CALL_EXCEPTION_CODE,
    ReActExecutor,
)
from src.agent.react_executor_protocol import ActionPacket
from src.agent.analyzer_config import load_analyzer_config
from src.models import ModelCallResult, ModelErrorCode, StructuredModelResult


class StructuredFailureModelManager:
    def __init__(self):
        self.generate_calls = []

    def generate(self, prompt: str, **kwargs):
        self.generate_calls.append(prompt)
        return ModelCallResult.fail(ModelErrorCode.MODEL_CALL_FAILED, "model failure")


class StructuredJsonOnlyModelManager:
    def __init__(self, result: StructuredModelResult):
        self.result = result
        self.generate_calls = []
        self.generate_json_calls = []

    def generate(self, prompt: str, **kwargs):
        self.generate_calls.append((prompt, kwargs))
        raise AssertionError("generate should not be used when generate_json is available")

    def generate_json(self, prompt: str, **kwargs):
        self.generate_json_calls.append((prompt, kwargs))
        return self.result


class FakeToolManager:
    def run_tool(self, tool_name: str, **kwargs):
        return "tool result"


def _task_namespace():
    return SimpleNamespace(
        action_policy="allow",
        requires_clarification=False,
        requires_confirmation=False,
        tool_strategy="model_only",
        mode="solo",
        execution_strategy="meso_advanced",
        intent=["create_project"],
        complexity_level="complex",
        parameters={},
        risk_flags=[],
        available_tools=[],
        missing_tools=[],
    )


class ModelsCallersAdaptationTest(unittest.TestCase):
    def test_analyzer_llm_fallback_stops_on_structured_failure(self):
        analyzer = ComplexityAnalyzer(
            analyzer_config=load_analyzer_config(Path.cwd()),
            model_manager=StructuredFailureModelManager(),
        )
        result = AnalysisResult(raw_input="hello", cleaned_input="hello")

        fallback = analyzer._llm_fallback_intents("hello", result)

        self.assertEqual([item.name for item in fallback], ["unknown"])
        self.assertEqual(result.llm_fallback_status, "call_failed")
        self.assertEqual(result.llm_fallback_error, "model failure")

    def test_analyzer_llm_fallback_prefers_generate_json(self):
        manager = StructuredJsonOnlyModelManager(
            StructuredModelResult(
                success=True,
                data={"intents": [{"name": "chat", "confidence": 0.9, "reason": "direct chat"}]},
            )
        )
        analyzer = ComplexityAnalyzer(
            analyzer_config=load_analyzer_config(Path.cwd()),
            model_manager=manager,
        )
        result = AnalysisResult(raw_input="hello", cleaned_input="hello")

        fallback = analyzer._llm_fallback_intents("hello", result)

        self.assertEqual([item.name for item in fallback], ["chat"])
        self.assertEqual(manager.generate_calls, [])
        self.assertEqual(manager.generate_json_calls[0][1]["call_type"], "analyzer_intent_fallback")

    def test_planner_llm_fallbacks_on_structured_failure(self):
        planner = Planner(
            planner_config=load_planner_config(Path.cwd()),
            model_manager=StructuredFailureModelManager(),
        )

        plan = planner._llm_planner_plan("design a todo app", _task_namespace())

        self.assertIsNotNone(plan)
        self.assertEqual(plan.planning_strategy, "fallback_model_only")

    def test_planner_llm_plan_prefers_generate_json(self):
        manager = StructuredJsonOnlyModelManager(
            StructuredModelResult(
                success=True,
                data={
                    "mode": "meso",
                    "task_type": "qa",
                    "execution_strategy": "meso",
                    "can_execute": True,
                    "steps": [
                        {
                            "id": "step_1",
                            "task_id": "task_1",
                            "description": "Answer the user.",
                            "step_type": "model",
                            "expected_output": "Answer",
                        }
                    ],
                    "task_units": [
                        {
                            "id": "task_1",
                            "title": "Answer",
                            "description": "design a todo app",
                            "intent_refs": ["create_project"],
                            "task_type": "qa",
                            "status": "pending",
                            "step_ids": ["step_1"],
                            "expected_outcome": "Answer",
                        }
                    ],
                },
            )
        )
        planner = Planner(
            planner_config=load_planner_config(Path.cwd()),
            model_manager=manager,
        )

        plan = planner._llm_planner_plan("design a todo app", _task_namespace())

        self.assertIsNotNone(plan)
        self.assertEqual(plan.planning_strategy, "llm_planner")
        self.assertEqual(manager.generate_calls, [])
        self.assertEqual(manager.generate_json_calls[0][1]["call_type"], "planner_structured_plan")

    def test_executor_returns_failed_tool_result_on_structured_failure(self):
        executor = Executor(model_manager=StructuredFailureModelManager(), tool_manager=FakeToolManager())
        plan = TaskPlan(
            goal="summarize",
            mode="micro",
            steps=[PlanStep(id="step_1", description="Summarize text.", step_type="model")],
        )

        result = executor.execute(plan, _task_namespace(), user_input="summarize", history="")

        self.assertFalse(result.success)
        self.assertEqual(result.steps[0].result.code, "model_call_failed")

    def test_react_executor_model_action_uses_execution_error_code(self):
        executor = ReActExecutor(model_manager=StructuredFailureModelManager(), tool_manager=FakeToolManager())
        plan = TaskPlan(
            goal="summarize",
            mode="micro",
            steps=[PlanStep(id="step_1", task_id="task_1", description="Summarize text.", step_type="model")],
            task_units=[TaskUnit(id="task_1", title="Task")],
        )
        task = _task_namespace()
        context = executor._create_context(plan, task, user_input="summarize", history="")
        packet = ActionPacket(
            action_type="call_model",
            execution_id=context.execution_id,
            plan_id=context.plan_id,
            task_id="task_1",
            step_id="step_1",
            action_args={"goal": "summarize", "input": "text", "output_requirements": "short"},
        )

        observation = executor._handle_call_model(context, packet, step=plan.steps[0], attempt=1)

        self.assertFalse(observation.success)
        self.assertEqual(observation.code, MODEL_CALL_EXCEPTION_CODE)

    def test_react_executor_action_packet_decision_uses_execution_error_code(self):
        executor = ReActExecutor(model_manager=StructuredFailureModelManager(), tool_manager=FakeToolManager())
        plan = TaskPlan(
            goal="summarize",
            mode="micro",
            steps=[PlanStep(id="step_1", task_id="task_1", description="Summarize text.", step_type="model")],
            task_units=[TaskUnit(id="task_1", title="Task")],
        )
        task = _task_namespace()
        context = executor._create_context(plan, task, user_input="summarize", history="")
        turn_state = context.loop_state.start_turn(task_id="task_1", step_id="step_1", attempt=1)

        result = executor._request_action_packet(
            context,
            prompt="{ }",
            task_unit=plan.task_units[0],
            step=plan.steps[0],
            turn_state=turn_state,
        )

        self.assertIsNotNone(result.observation)
        self.assertEqual(result.observation.code, ACTION_PACKET_MODEL_EXCEPTION_CODE)

    def test_react_executor_action_packet_decision_prefers_generate_json(self):
        manager = StructuredJsonOnlyModelManager(
            StructuredModelResult(
                success=True,
                data={
                    "action_type": "call_model",
                    "action_args": {
                        "goal": "summarize",
                        "input": "text",
                        "output_requirements": "short",
                    },
                    "thought_summary": "Use model reasoning.",
                },
            )
        )
        executor = ReActExecutor(model_manager=manager, tool_manager=FakeToolManager())
        plan = TaskPlan(
            goal="summarize",
            mode="micro",
            steps=[PlanStep(id="step_1", task_id="task_1", description="Summarize text.", step_type="model")],
            task_units=[TaskUnit(id="task_1", title="Task")],
        )
        task = _task_namespace()
        context = executor._create_context(plan, task, user_input="summarize", history="")
        turn_state = context.loop_state.start_turn(task_id="task_1", step_id="step_1", attempt=1)

        result = executor._request_action_packet(
            context,
            prompt="{ }",
            task_unit=plan.task_units[0],
            step=plan.steps[0],
            turn_state=turn_state,
        )

        self.assertTrue(result.success)
        self.assertEqual(result.packet.action_type, "call_model")
        self.assertEqual(manager.generate_calls, [])
        self.assertEqual(manager.generate_json_calls[0][1]["call_type"], "react_action_decision")
        self.assertEqual(manager.generate_json_calls[0][1]["parse_mode"], "strict")


if __name__ == "__main__":
    unittest.main()
