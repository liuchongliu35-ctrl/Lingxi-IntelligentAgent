from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.agent.planner import Planner
from src.agent.planner_config import load_planner_config


def make_task(**overrides):
    defaults = {
        "trace_id": "trace_log",
        "mode": "solo",
        "task_type": "tool_operation",
        "execution_strategy": "micro",
        "action_policy": "allow",
        "requires_clarification": False,
        "clarification_questions": [],
        "missing_parameters": [],
        "requires_confirmation": False,
        "confirmation_reason": None,
        "tool_strategy": "tool",
        "available_tools": ["math_calculator"],
        "missing_tools": [],
        "intent": ["calculate"],
        "intent_sequence": ["calculate"],
        "parameters": {"expression": "2+3"},
        "file_info": {},
        "edit_mode": None,
        "project_stage": None,
        "tech_stacks": [],
        "complexity_level": "simple",
        "risk_flags": [],
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class PlannerLoggingTest(unittest.TestCase):
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

        config_path = target_dir / "planner_config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["planner_log_path"] = "logs/test_planner.log"
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

    def test_create_plan_writes_jsonl_log_entry(self):
        planner = Planner(planner_config=self.config)
        task = make_task()

        plan = planner.create_plan("calculate 2+3", task)

        log_path = self.root / "logs" / "test_planner.log"
        self.assertTrue(log_path.exists())
        lines = log_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)

        entry = json.loads(lines[0])
        self.assertEqual(entry["plan_id"], plan.plan_id)
        self.assertEqual(entry["source_trace_id"], "trace_log")
        self.assertEqual(entry["raw_input"], "calculate 2+3")
        self.assertEqual(entry["intent_sequence"], ["calculate"])
        self.assertEqual(entry["planning_strategy"], "rule_template")
        self.assertEqual(entry["plan_validation_status"], "valid")
        self.assertEqual(entry["required_tools"], ["math_calculator"])
        self.assertEqual(entry["tool_args"]["step_1"]["args"], {"expression": "2+3"})
        self.assertEqual(entry["task_units"][0]["id"], "task_1")
        self.assertEqual(entry["steps"][0]["id"], "step_1")

    def test_special_policy_log_entry_records_policy_and_non_executable_state(self):
        planner = Planner(planner_config=self.config)
        task = make_task(action_policy="block", risk_flags=["dangerous_command"], execution_strategy="meso")

        plan = planner.create_plan("delete system files", task)

        entry = json.loads(self.config.planner_log_path.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(entry["plan_id"], plan.plan_id)
        self.assertEqual(entry["special_policy"], "blocked")
        self.assertFalse(entry["can_execute"])
        self.assertEqual(entry["risk_policy"], "block")
        self.assertEqual(entry["risk_flags"], ["dangerous_command"])
        self.assertEqual(entry["steps"][0]["step_type"], "block")


if __name__ == "__main__":
    unittest.main()
