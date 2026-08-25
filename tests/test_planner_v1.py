from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.agent.planner import Planner
from src.agent.planner_config import load_planner_config


DEFAULT_TASK = {
    "trace_id": "trace_fixture",
    "mode": "solo",
    "task_type": "qa",
    "execution_strategy": "meso",
    "action_policy": "allow",
    "requires_clarification": False,
    "clarification_questions": [],
    "missing_parameters": [],
    "requires_confirmation": False,
    "confirmation_reason": None,
    "tool_strategy": "model_only",
    "available_tools": [],
    "missing_tools": [],
    "intent": ["chat"],
    "intent_sequence": ["chat"],
    "parameters": {},
    "file_info": {},
    "edit_mode": None,
    "project_stage": None,
    "tech_stacks": [],
    "complexity_level": "medium",
    "risk_flags": [],
}


class FixtureModelManager:
    def __init__(self, case):
        self.prompts = []
        if "model_response_sequence" in case:
            self.responses = list(case["model_response_sequence"])
        elif "model_response" in case:
            self.responses = [case["model_response"]]
        else:
            self.responses = []

    def generate(self, prompt: str, **kwargs):
        self.prompts.append(prompt)
        if not self.responses:
            return "not configured"
        if len(self.responses) > 1:
            return self.responses.pop(0)
        return self.responses[0]


class PlannerV1FixtureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo_root = Path(__file__).resolve().parents[1]
        fixture_path = cls.repo_root / "tests" / "fixtures" / "planner_cases.json"
        cls.cases = json.loads(fixture_path.read_text(encoding="utf-8"))

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self._copy_planner_config()
        load_planner_config.cache_clear()
        self.config = load_planner_config(self.root)

    def tearDown(self):
        self.temp_dir.cleanup()
        load_planner_config.cache_clear()

    def _copy_planner_config(self):
        source_dir = self.repo_root / "config" / "planner"
        target_dir = self.root / "config" / "planner"
        target_dir.mkdir(parents=True)
        for source_path in source_dir.glob("*.json"):
            shutil.copyfile(source_path, target_dir / source_path.name)

        config_path = target_dir / "planner_config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["planner_log_path"] = "logs/planner_fixture.log"
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

    def test_planner_fixture_cases(self):
        self.assertGreaterEqual(len(self.cases), 20)
        self.assertLessEqual(len(self.cases), 40)

        for case in self.cases:
            with self.subTest(case=case["id"]):
                plan, model = self._run_case(case)
                self._assert_expected(case, plan, model)

    def _run_case(self, case):
        task_data = dict(DEFAULT_TASK)
        task_data.update(case.get("task", {}))
        task = SimpleNamespace(**task_data)
        model = FixtureModelManager(case) if "model_response" in case or "model_response_sequence" in case else None
        planner = Planner(planner_config=self.config, model_manager=model)
        return planner.create_plan(case["user_input"], task), model

    def _assert_expected(self, case, plan, model):
        expected = case["expected"]
        if "mode" in expected:
            self.assertEqual(plan.mode, expected["mode"])
        if "planning_strategy" in expected:
            self.assertEqual(plan.planning_strategy, expected["planning_strategy"])
        if "plan_validation_status" in expected:
            self.assertEqual(plan.plan_validation_status, expected["plan_validation_status"])
        if "can_execute" in expected:
            self.assertEqual(plan.can_execute, expected["can_execute"])
        if "task_units_count" in expected:
            self.assertEqual(len(plan.task_units), expected["task_units_count"])
        if "step_count" in expected:
            self.assertEqual(len(plan.steps), expected["step_count"])
        if "step_types" in expected:
            self.assertEqual([step.step_type for step in plan.steps], expected["step_types"])
        if "tool_names" in expected:
            self.assertEqual([step.tool_name for step in plan.steps], expected["tool_names"])
        if "required_tools" in expected:
            self.assertEqual(plan.required_tools, expected["required_tools"])
        if "missing_tools" in expected:
            self.assertEqual(plan.missing_tools, expected["missing_tools"])
        for text in expected.get("notes_contains", []):
            self.assertTrue(
                any(text in note for note in plan.plan_validation_notes),
                f"{case['id']} expected note containing {text!r}, got {plan.plan_validation_notes!r}",
            )
        for text in expected.get("added_steps_contains", []):
            self.assertTrue(
                any(text in note for note in plan.added_steps_reason),
                f"{case['id']} expected added reason containing {text!r}, got {plan.added_steps_reason!r}",
            )
        if model is None:
            return
        if plan.planning_strategy in {"llm_planner", "llm_repaired", "fallback_model_only", "invalid"}:
            self.assertGreaterEqual(len(model.prompts), 1)


if __name__ == "__main__":
    unittest.main()
