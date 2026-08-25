from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.agent.planner import PLAN_MODES, PLAN_VALIDATION_STATUSES, PLANNING_STRATEGIES, TASK_UNIT_STATUSES, Planner
from src.agent.planner_config import load_planner_config


def make_task(**overrides):
    defaults = {
        "trace_id": "trace_123",
        "mode": "solo",
        "task_type": "tool_operation",
        "execution_strategy": "micro",
        "action_policy": "allow",
        "requires_clarification": False,
        "clarification_questions": [],
        "requires_confirmation": False,
        "confirmation_reason": None,
        "tool_strategy": "tool",
        "available_tools": ["math_calculator"],
        "missing_tools": [],
        "intent": ["calculate"],
        "intent_sequence": ["calculate"],
        "parameters": {"expression": "2+3"},
        "complexity_level": "simple",
        "risk_flags": [],
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class PlannerV1StructureTest(unittest.TestCase):
    def setUp(self):
        self.repo_root = Path(__file__).resolve().parents[1]
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

    def test_planner_config_loads_defaults_from_json(self):
        self.assertEqual(self.config.max_plan_steps, 20)
        self.assertEqual(self.config.max_task_units, 6)
        self.assertEqual(self.config.max_llm_repair_attempts, 3)
        self.assertEqual(self.config.default_step_max_retries, 3)
        self.assertTrue(self.config.enable_llm_planner)
        self.assertFalse(self.config.enable_shell_fallback_plan)
        self.assertEqual(self.config.planner_log_path, (self.root / "logs" / "planner.log").resolve())

    def test_enums_cover_v1_protocol_values(self):
        self.assertTrue({"micro", "meso", "meso_advanced", "macro", "blocked", "clarify", "confirm", "missing_tools", "chat"}.issubset(PLAN_MODES))
        self.assertTrue({"policy_rule", "rule_template", "llm_planner", "llm_repaired", "fallback_rule", "fallback_model_only", "invalid"}.issubset(PLANNING_STRATEGIES))
        self.assertTrue({"pending", "running", "completed", "failed", "skipped", "blocked", "waiting_user"}.issubset(TASK_UNIT_STATUSES))
        self.assertTrue({"valid", "repaired", "invalid", "not_required"}.issubset(PLAN_VALIDATION_STATUSES))

    def test_micro_plan_has_task_units_flat_steps_and_trace_fields(self):
        planner = Planner(planner_config=self.config)
        plan = planner.create_plan("计算2+3", make_task())

        self.assertTrue(plan.plan_id.startswith("plan_"))
        self.assertEqual(plan.source_trace_id, "trace_123")
        self.assertEqual(plan.mode, "micro")
        self.assertEqual(plan.task_type, "tool_operation")
        self.assertEqual(plan.execution_strategy, "micro")
        self.assertEqual(plan.planning_strategy, "rule_template")
        self.assertTrue(plan.can_execute)
        self.assertEqual(plan.risk_policy, "allow")
        self.assertEqual(plan.required_tools, ["math_calculator"])
        self.assertEqual(plan.available_tools, ["math_calculator"])
        self.assertEqual(plan.plan_validation_status, "valid")

        self.assertEqual(len(plan.task_units), 1)
        self.assertEqual(plan.task_units[0].id, "task_1")
        self.assertEqual(plan.task_units[0].intent_refs, ["calculate"])
        self.assertEqual(plan.task_units[0].step_ids, ["step_1"])

        self.assertEqual(len(plan.steps), 1)
        self.assertEqual(plan.steps[0].id, "step_1")
        self.assertEqual(plan.steps[0].task_id, "task_1")
        self.assertEqual(plan.steps[0].step_type, "tool")
        self.assertEqual(plan.steps[0].output_key, "calculate_result")
        self.assertEqual(plan.steps[0].max_retries, 3)

    def test_policy_plan_has_non_executable_task_unit(self):
        planner = Planner(planner_config=self.config)
        task = make_task(action_policy="block", risk_flags=["dangerous_command"], execution_strategy="meso")
        plan = planner.create_plan("执行命令 rm -rf /", task)

        self.assertEqual(plan.mode, "blocked")
        self.assertFalse(plan.can_execute)
        self.assertEqual(plan.planning_strategy, "policy_rule")
        self.assertEqual(plan.plan_validation_status, "not_required")
        self.assertEqual(plan.task_units[0].status, "blocked")
        self.assertEqual(plan.steps[0].step_type, "block")
        self.assertEqual(plan.steps[0].task_id, plan.task_units[0].id)

    def test_plan_objects_support_to_dict(self):
        planner = Planner(planner_config=self.config)
        plan = planner.create_plan("计算2+3", make_task())
        payload = plan.to_dict()

        self.assertEqual(payload["task_units"][0]["id"], "task_1")
        self.assertEqual(payload["steps"][0]["task_id"], "task_1")
        json.dumps(payload, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
