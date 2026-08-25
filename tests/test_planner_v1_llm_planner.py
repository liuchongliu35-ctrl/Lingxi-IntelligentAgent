from __future__ import annotations

import unittest
from types import SimpleNamespace

from src.agent.planner import Planner


def make_task(**overrides):
    defaults = {
        "trace_id": "trace_llm_planner",
        "mode": "solo",
        "task_type": "software_engineering",
        "execution_strategy": "meso_advanced",
        "action_policy": "allow",
        "requires_clarification": False,
        "clarification_questions": [],
        "missing_parameters": [],
        "requires_confirmation": False,
        "confirmation_reason": None,
        "tool_strategy": "model_only",
        "available_tools": ["document_parser", "text_processor", "file_writer"],
        "missing_tools": [],
        "intent": ["create_project"],
        "intent_sequence": ["create_project"],
        "parameters": {"topic": "todo app", "target_path": "docs/plan.md"},
        "file_info": {},
        "edit_mode": None,
        "project_stage": "design",
        "tech_stacks": ["python"],
        "complexity_level": "complex",
        "risk_flags": [],
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class CapturingModelManager:
    def __init__(self, response):
        self.response = response
        self.prompts = []

    def generate(self, prompt: str, **kwargs):
        self.prompts.append(prompt)
        return self.response


class SequenceModelManager:
    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []

    def generate(self, prompt: str, **kwargs):
        self.prompts.append(prompt)
        if len(self.responses) > 1:
            return self.responses.pop(0)
        return self.responses[0]


class PlannerLLMPlannerTest(unittest.TestCase):
    def test_llm_planner_prompt_and_fenced_json_plan(self):
        model = CapturingModelManager(
            """```json
{
  "mode": "meso_advanced",
  "task_type": "software_engineering",
  "execution_strategy": "meso_advanced",
  "can_execute": true,
  "user_facing_summary": "LLM plan ready",
  "plan_validation_notes": ["LLM kept Analyzer intent order"],
  "added_steps_reason": ["Added architecture step before implementation"],
  "task_units": [
    {
      "id": "task_1",
      "title": "Design todo app",
      "description": "Create a design plan",
      "intent_refs": ["create_project"],
      "task_type": "software_engineering",
      "status": "pending",
      "depends_on": [],
      "step_ids": ["step_1", "step_2"],
      "expected_outcome": "Project design"
    }
  ],
  "steps": [
    {
      "id": "step_1",
      "task_id": "task_1",
      "step_type": "model",
      "description": "Analyze product scope and architecture.",
      "args": {},
      "depends_on": [],
      "input_from": [],
      "output_key": "architecture_notes",
      "expected_output": "Architecture notes",
      "allow_model_reasoning": true
    },
    {
      "id": "step_2",
      "task_id": "task_1",
      "step_type": "respond",
      "description": "Return the implementation plan.",
      "args": {},
      "depends_on": ["step_1"],
      "input_from": ["step_1"],
      "output_key": "final_plan",
      "expected_output": "Project design",
      "allow_model_reasoning": true
    }
  ]
}
```"""
        )
        task = make_task()

        plan = Planner(model_manager=model).create_plan("design a todo app", task)

        self.assertEqual(plan.planning_strategy, "llm_planner")
        self.assertEqual(plan.mode, "meso_advanced")
        self.assertTrue(plan.can_execute)
        self.assertEqual(plan.user_facing_summary, "LLM plan ready")
        self.assertEqual(plan.task_units[0].step_ids, ["step_1", "step_2"])
        self.assertEqual([step.step_type for step in plan.steps], ["model", "respond"])
        self.assertEqual(plan.steps[1].depends_on, ["step_1"])
        self.assertIn("Added architecture step before implementation", plan.added_steps_reason)
        self.assertIn("llm_planner parsed structured JSON plan", plan.plan_validation_notes)

        prompt = model.prompts[0]
        self.assertIn("Return strict JSON only", prompt)
        self.assertIn("Analyzer result", prompt)
        self.assertIn("Available tools", prompt)
        self.assertIn("Plan JSON schema", prompt)
        self.assertIn("Do not bypass Analyzer block decisions", prompt)
        self.assertIn("create_project", prompt)

    def test_llm_planner_accepts_dict_response(self):
        model = CapturingModelManager(
            {
                "mode": "meso",
                "task_units": [{"id": "task_1", "title": "Answer", "step_ids": ["step_1"]}],
                "steps": [
                    {
                        "id": "step_1",
                        "task_id": "task_1",
                        "step_type": "respond",
                        "description": "Answer from model only.",
                        "expected_output": "Answer",
                    }
                ],
            }
        )

        plan = Planner(model_manager=model).create_plan("plan an unusual workflow", make_task())

        self.assertEqual(plan.planning_strategy, "llm_planner")
        self.assertEqual(plan.steps[0].step_type, "respond")
        self.assertIsNone(plan.steps[0].tool_name)

    def test_llm_planner_parse_failure_falls_back_without_fake_success(self):
        model = CapturingModelManager("I cannot return JSON today.")

        plan = Planner(model_manager=model).create_plan("plan an unusual workflow", make_task())

        self.assertEqual(plan.planning_strategy, "fallback_model_only")
        self.assertEqual(plan.mode, "meso_advanced")
        self.assertEqual([step.step_type for step in plan.steps], ["model", "respond"])
        self.assertTrue(any("no_parseable_json_plan" in note for note in plan.plan_validation_notes))
        self.assertTrue(any("llm_planner_status=failed" in item for item in plan.raw_planner_trace))

    def test_llm_planner_missing_steps_falls_back_without_fake_success(self):
        model = CapturingModelManager({"mode": "meso", "task_units": [{"id": "task_1"}]})

        plan = Planner(model_manager=model).create_plan("plan an unusual workflow", make_task())

        self.assertEqual(plan.planning_strategy, "fallback_model_only")
        self.assertTrue(any("llm_plan_missing_steps" in note for note in plan.plan_validation_notes))

    def test_rule_template_match_does_not_call_llm_planner(self):
        model = CapturingModelManager({"steps": []})
        task = make_task(
            task_type="tool_operation",
            execution_strategy="micro",
            intent=["calculate"],
            intent_sequence=["calculate"],
            parameters={"expression": "2+3"},
        )

        plan = Planner(model_manager=model).create_plan("calculate 2+3", task)

        self.assertEqual(plan.planning_strategy, "rule_template")
        self.assertEqual(model.prompts, [])

    def test_invalid_llm_references_are_repaired(self):
        model = SequenceModelManager(
            [
                {
                    "mode": "meso",
                    "steps": [
                        {
                            "id": "step_1",
                            "task_id": "task_1",
                            "step_type": "respond",
                            "description": "Answer with a missing dependency.",
                            "depends_on": ["missing_step"],
                            "input_from": ["missing_step"],
                            "expected_output": "Answer",
                        }
                    ],
                },
                {
                    "mode": "meso",
                    "task_units": [{"id": "task_1", "title": "Answer", "step_ids": ["step_1"]}],
                    "steps": [
                        {
                            "id": "step_1",
                            "task_id": "task_1",
                            "step_type": "respond",
                            "description": "Answer directly.",
                            "expected_output": "Answer",
                        }
                    ],
                },
            ]
        )

        plan = Planner(model_manager=model).create_plan("plan an unusual workflow", make_task())

        self.assertEqual(plan.planning_strategy, "llm_repaired")
        self.assertEqual(plan.plan_validation_status, "repaired")
        self.assertTrue(plan.can_execute)
        self.assertEqual(len(model.prompts), 2)
        self.assertIn("missing_step", model.prompts[1])
        self.assertTrue(any("repaired after 1 attempt" in note for note in plan.plan_validation_notes))

    def test_invalid_llm_plan_after_retries_remains_invalid(self):
        model = CapturingModelManager(
            {
                "mode": "meso",
                "steps": [
                    {
                        "id": "step_1",
                        "task_id": "task_1",
                        "step_type": "tool",
                        "tool_name": "missing_tool",
                        "description": "Use an unavailable tool.",
                        "args": {},
                        "expected_output": "Result",
                    }
                ],
            }
        )

        plan = Planner(model_manager=model).create_plan("plan an unusual workflow", make_task())

        self.assertEqual(plan.planning_strategy, "invalid")
        self.assertEqual(plan.plan_validation_status, "invalid")
        self.assertFalse(plan.can_execute)
        self.assertEqual(len(model.prompts), 4)
        self.assertTrue(any("missing_tool is not in available_tools" in note for note in plan.plan_validation_notes))
        self.assertTrue(any("llm_planner_status=invalid_after_repair" in item for item in plan.raw_planner_trace))

    def test_llm_shell_step_is_invalid_without_shell_policy(self):
        model = CapturingModelManager(
            {
                "mode": "meso",
                "steps": [
                    {
                        "id": "step_1",
                        "task_id": "task_1",
                        "step_type": "shell",
                        "description": "Run a shell command.",
                        "args": {"command": "echo hi"},
                        "expected_output": "Shell output",
                    }
                ],
            }
        )

        plan = Planner(model_manager=model).create_plan("run a shell workflow", make_task())

        self.assertEqual(plan.plan_validation_status, "invalid")
        self.assertFalse(plan.can_execute)
        self.assertTrue(any("step_type=shell is not executable" in note for note in plan.plan_validation_notes))

    def test_llm_step_referencing_missing_task_unit_is_invalid(self):
        model = CapturingModelManager(
            {
                "mode": "meso",
                "task_units": [{"id": "task_1", "title": "Task", "step_ids": ["step_1"]}],
                "steps": [
                    {
                        "id": "step_1",
                        "task_id": "task_missing",
                        "step_type": "respond",
                        "description": "Answer.",
                        "expected_output": "Answer",
                    }
                ],
            }
        )

        plan = Planner(model_manager=model).create_plan("plan an unusual workflow", make_task())

        self.assertEqual(plan.plan_validation_status, "invalid")
        self.assertFalse(plan.can_execute)
        self.assertTrue(any("task_id references missing TaskUnit task_missing" in note for note in plan.plan_validation_notes))

    def test_confirm_policy_cannot_be_bypassed_by_llm_tool_step(self):
        planner = Planner(model_manager=CapturingModelManager({}))
        task = make_task(
            action_policy="confirm",
            requires_confirmation=True,
            confirmation_reason="write_file",
            available_tools=["file_writer"],
        )
        payload = {
            "mode": "meso",
            "task_units": [{"id": "task_1", "title": "Write", "step_ids": ["step_1"]}],
            "steps": [
                {
                    "id": "step_1",
                    "task_id": "task_1",
                    "step_type": "tool",
                    "tool_name": "file_writer",
                    "description": "Write without confirmation.",
                    "args": {"file_path": "out.txt", "content": "hello"},
                    "expected_output": "Written file",
                }
            ],
        }

        plan = planner._task_plan_from_llm_payload("write file", task, payload, raw_response=payload)

        self.assertEqual(plan.plan_validation_status, "invalid")
        self.assertFalse(plan.can_execute)
        self.assertTrue(any("confirm policy cannot contain unconfirmed tool steps" in note for note in plan.plan_validation_notes))


if __name__ == "__main__":
    unittest.main()
