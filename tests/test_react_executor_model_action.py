from __future__ import annotations

import json
import unittest

from src.agent.planner import PlanStep, TaskPlan, TaskUnit
from src.agent.react_executor import (
    MODEL_CALL_EXCEPTION_CODE,
    MODEL_INPUT_REF_MISSING_CODE,
    MODEL_MANAGER_UNAVAILABLE_CODE,
    ReActExecutor,
)
from src.agent.react_executor_protocol import ActionPacket, ObservationPacket


class FakeModelManager:
    def __init__(self, response="generated summary", *, raises: bool = False):
        self.response = response
        self.raises = raises
        self.generate_calls = []

    def generate(self, prompt: str, **kwargs):
        self.generate_calls.append((prompt, kwargs))
        if self.raises:
            raise RuntimeError("model failed")
        return self.response


class FakeToolManager:
    def __init__(self):
        self.run_calls = []

    def list_tools(self):
        return {"math_calculator": "Fake calculator."}

    def run_tool(self, tool_name: str, **kwargs):
        self.run_calls.append((tool_name, kwargs))
        return "tool result"


    def execute(self, request):
        return self.run_tool(request.tool_name, **request.args)
class ReActExecutorModelActionTest(unittest.TestCase):
    def test_call_model_generates_intermediate_observation(self):
        model_manager = FakeModelManager("summary text")
        executor = ReActExecutor(model_manager=model_manager, tool_manager=FakeToolManager())
        plan, step = _plan_with_step(
            PlanStep(
                id="step_1",
                task_id="task_1",
                description="Summarize.",
                step_type="model",
                expected_output="Short summary",
            )
        )
        context = executor._create_context(plan, task={}, user_input="summarize this", history="history")
        packet = _packet(
            context,
            "call_model",
            {
                "goal": "summarize",
                "input": "source text",
                "output_requirements": "short",
            },
        )

        observation = executor.dispatch_action(context, packet, step=step, output_key="summary")

        self.assertTrue(observation.success)
        self.assertEqual(observation.data, "summary text")
        self.assertEqual(observation.model_consumable_observation["content"], "summary text")
        self.assertIs(context.observation_store.get_by_output_key("summary"), observation)
        self.assertEqual(len(model_manager.generate_calls), 1)
        prompt = model_manager.generate_calls[0][0]
        self.assertIn("Do not return an ActionPacket", prompt)
        self.assertIn("source text", prompt)
        self.assertIn("message_delta", _event_types(context))
        self.assertIn("model_step_started", _event_types(context))
        self.assertIn("model_step_finished", _event_types(context))
        event_payload = json.dumps([event.payload for event in context.event_stream.events], ensure_ascii=False)
        self.assertNotIn('"prompt"', event_payload)
        self.assertNotIn("Do not return an ActionPacket", event_payload)

    def test_call_model_can_consume_input_from_observation_store(self):
        model_manager = FakeModelManager("summary from observation")
        executor = ReActExecutor(model_manager=model_manager, tool_manager=FakeToolManager())
        plan, step = _plan_with_step(
            PlanStep(
                id="step_2",
                task_id="task_1",
                description="Summarize.",
                step_type="model",
                input_from=["file_content"],
                expected_output="Summary",
            )
        )
        context = executor._create_context(plan, task={}, user_input="summarize", history="")
        context.observation_store.add(
            ObservationPacket(
                execution_id=context.execution_id,
                plan_id=context.plan_id,
                task_id="task_1",
                step_id="step_1",
                action_type="call_tool",
                success=True,
                data="file body",
                message="file body",
                model_consumable_observation="file body",
            ),
            output_key="file_content",
        )
        packet = _packet(
            context,
            "call_model",
            {
                "goal": "summarize",
                "input_from": ["file_content"],
                "output_requirements": "bullets",
            },
        )

        observation = executor.dispatch_action(context, packet, step=step)

        self.assertTrue(observation.success)
        self.assertIn("file body", model_manager.generate_calls[0][0])

    def test_missing_input_from_does_not_call_model(self):
        model_manager = FakeModelManager()
        executor = ReActExecutor(model_manager=model_manager, tool_manager=FakeToolManager())
        plan, step = _plan_with_step(
            PlanStep(id="step_1", task_id="task_1", description="Summarize.", step_type="model", input_from=["missing"])
        )
        context = executor._create_context(plan, task={}, user_input="summarize", history="")
        packet = _packet(
            context,
            "call_model",
            {
                "goal": "summarize",
                "input_from": ["missing"],
                "output_requirements": "short",
            },
        )

        observation = executor.dispatch_action(context, packet, step=step)

        self.assertFalse(observation.success)
        self.assertEqual(observation.code, MODEL_INPUT_REF_MISSING_CODE)
        self.assertIn("missing", observation.error)
        self.assertEqual(model_manager.generate_calls, [])

    def test_model_exception_returns_structured_failure(self):
        model_manager = FakeModelManager(raises=True)
        executor = ReActExecutor(model_manager=model_manager, tool_manager=FakeToolManager())
        plan, step = _plan_with_step(PlanStep(id="step_1", task_id="task_1", description="Generate.", step_type="model"))
        context = executor._create_context(plan, task={}, user_input="generate", history="")
        packet = _packet(
            context,
            "call_model",
            {
                "goal": "generate",
                "input": "input",
                "output_requirements": "text",
            },
        )

        observation = executor.dispatch_action(context, packet, step=step)

        self.assertFalse(observation.success)
        self.assertEqual(observation.code, MODEL_CALL_EXCEPTION_CODE)
        self.assertEqual(observation.error, "model failed")
        self.assertIn("model_step_started", _event_types(context))
        self.assertIn("model_step_finished", _event_types(context))

    def test_missing_model_manager_returns_structured_failure(self):
        executor = ReActExecutor(model_manager=None, tool_manager=FakeToolManager())
        plan, step = _plan_with_step(PlanStep(id="step_1", task_id="task_1", description="Generate.", step_type="model"))
        context = executor._create_context(plan, task={}, user_input="generate", history="")
        packet = _packet(
            context,
            "call_model",
            {
                "goal": "generate",
                "input": "input",
                "output_requirements": "text",
            },
        )

        observation = executor.dispatch_action(context, packet, step=step)

        self.assertFalse(observation.success)
        self.assertEqual(observation.code, MODEL_MANAGER_UNAVAILABLE_CODE)

    def test_finish_emits_final_answer_with_summary(self):
        executor = ReActExecutor(model_manager=FakeModelManager(), tool_manager=FakeToolManager())
        plan, step = _plan_with_step(PlanStep(id="step_1", task_id="task_1", description="Finish.", step_type="respond"))
        context = executor._create_context(plan, task={}, user_input="finish", history="")
        context.step_states["step_1"].status = "completed"
        packet = ActionPacket(
            execution_id=context.execution_id,
            plan_id=context.plan_id,
            task_id="task_1",
            step_id="step_1",
            action_type="finish",
            final_answer="final text",
        )

        observation = executor.dispatch_action(context, packet, step=step)

        self.assertTrue(observation.success)
        self.assertEqual(observation.data["final_answer"], "final text")
        self.assertEqual(observation.data["summary"]["completed_steps"], ["step_1"])
        self.assertIn("final_answer", _event_types(context))

    def test_chat_mode_plan_finishes_through_structured_action_without_tools(self):
        model_manager = FakeModelManager(
            json.dumps(
                {
                    "action_type": "finish",
                    "final_answer": "chat answer",
                }
            )
        )
        tool_manager = FakeToolManager()
        executor = ReActExecutor(model_manager=model_manager, tool_manager=tool_manager)
        step = PlanStep(id="step_1", task_id="task_1", description="Respond.", step_type="respond", allow_model_reasoning=True)
        plan = TaskPlan(
            goal="chat",
            mode="chat",
            steps=[step],
            task_units=[TaskUnit(id="task_1", title="Chat", step_ids=["step_1"])],
            available_tools=["math_calculator"],
            can_execute=True,
            plan_validation_status="not_required",
        )

        result = executor.execute(plan, task={}, user_input="chat")

        self.assertTrue(result.success)
        self.assertEqual(result.status, "completed")
        self.assertIsNone(result.error_code)
        self.assertEqual(result.step_statuses["step_1"], "completed")
        self.assertEqual(tool_manager.run_calls, [])
        self.assertEqual(len(model_manager.generate_calls), 1)
        self.assertIn("chat answer", result.output)


def _plan_with_step(step: PlanStep) -> tuple[TaskPlan, PlanStep]:
    return (
        TaskPlan(
            goal="demo",
            mode="micro",
            steps=[step],
            task_units=[TaskUnit(id="task_1", title="Demo", step_ids=[step.id])],
            available_tools=[],
            can_execute=True,
            plan_validation_status="valid",
        ),
        step,
    )


def _packet(context, action_type: str, action_args: dict) -> ActionPacket:
    return ActionPacket(
        execution_id=context.execution_id,
        plan_id=context.plan_id,
        task_id="task_1",
        step_id="step_1",
        action_type=action_type,
        action_args=action_args,
    )


def _event_types(context) -> list[str]:
    return [event.type for event in context.event_stream.events]


if __name__ == "__main__":
    unittest.main()
