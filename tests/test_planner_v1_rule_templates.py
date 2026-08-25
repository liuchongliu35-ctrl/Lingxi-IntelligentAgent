from __future__ import annotations

import unittest
from types import SimpleNamespace

from src.agent.planner import Planner


def make_task(**overrides):
    defaults = {
        "trace_id": "trace_rules",
        "mode": "solo",
        "task_type": "tool_operation",
        "execution_strategy": "meso",
        "action_policy": "allow",
        "requires_clarification": False,
        "clarification_questions": [],
        "missing_parameters": [],
        "requires_confirmation": False,
        "confirmation_reason": None,
        "tool_strategy": "tool",
        "available_tools": ["math_calculator", "search_tool", "text_processor", "document_parser", "file_writer", "translator"],
        "missing_tools": [],
        "intent": ["calculate"],
        "intent_sequence": ["calculate"],
        "parameters": {"expression": "2+3"},
        "file_info": {},
        "edit_mode": None,
        "project_stage": None,
        "tech_stacks": [],
        "complexity_level": "medium",
        "risk_flags": [],
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class PlannerRuleTemplateTest(unittest.TestCase):
    def setUp(self):
        self.planner = Planner()

    def assert_single_task_with_steps(self, plan, expected_step_ids):
        self.assertEqual(len(plan.task_units), 1)
        self.assertEqual(plan.task_units[0].id, "task_1")
        self.assertEqual(plan.task_units[0].step_ids, expected_step_ids)
        self.assertEqual([step.id for step in plan.steps], expected_step_ids)
        self.assertTrue(all(step.task_id == "task_1" for step in plan.steps))

    def test_calculate_generates_one_task_unit_and_one_tool_step(self):
        task = make_task(execution_strategy="micro", intent=["calculate"], intent_sequence=["calculate"])

        plan = self.planner.create_plan("calculate 2+3", task)

        self.assertEqual(plan.mode, "micro")
        self.assertEqual(plan.planning_strategy, "rule_template")
        self.assert_single_task_with_steps(plan, ["step_1"])
        self.assertEqual(plan.steps[0].tool_name, "math_calculator")
        self.assertEqual(plan.steps[0].args, {"expression": "2+3"})

    def test_search_summarize_write_file_generates_one_pipeline_task(self):
        task = make_task(
            task_type="research",
            intent=["search", "summarize", "write_file"],
            intent_sequence=["search", "summarize", "write_file"],
            parameters={"topic": "planner architecture", "target_path": "notes/planner.md"},
        )

        plan = self.planner.create_plan("search planner architecture and write notes", task)

        self.assertEqual(plan.mode, "meso")
        self.assert_single_task_with_steps(plan, ["step_1", "step_2", "step_3"])
        self.assertEqual([step.tool_name for step in plan.steps], ["search_tool", "text_processor", "file_writer"])
        self.assertEqual(plan.steps[0].args["query"], "planner architecture")
        self.assertEqual(plan.steps[1].depends_on, ["step_1"])
        self.assertEqual(plan.steps[1].input_from, ["step_1"])
        self.assertEqual(plan.steps[2].depends_on, ["step_2"])
        self.assertEqual(plan.steps[2].input_from, ["step_2"])
        self.assertEqual(plan.steps[2].args["file_path"], "notes/planner.md")
        self.assertIn("rule_template:search_summarize_write_file", plan.added_steps_reason)

    def test_read_extract_write_file_generates_read_process_write_pipeline(self):
        task = make_task(
            task_type="document_understanding",
            intent=["read_file", "extract", "write_file"],
            intent_sequence=["read_file", "extract", "write_file"],
            parameters={"file_path": "data/source.md", "target_path": "out/extract.md"},
        )

        plan = self.planner.create_plan("extract key facts from data/source.md into out/extract.md", task)

        self.assert_single_task_with_steps(plan, ["step_1", "step_2", "step_3"])
        self.assertEqual([step.tool_name for step in plan.steps], ["document_parser", "text_processor", "file_writer"])
        self.assertEqual(plan.steps[0].args["file_path"], "data/source.md")
        self.assertEqual(plan.steps[1].args["operation"], "keywords")
        self.assertEqual(plan.steps[2].args["file_path"], "out/extract.md")

    def test_translate_write_file_generates_translate_then_write_pipeline(self):
        task = make_task(
            task_type="file_operation",
            intent=["translate", "write_file"],
            intent_sequence=["translate", "write_file"],
            parameters={"content": "hello", "target_language": "fr", "target_path": "out/fr.txt"},
        )

        plan = self.planner.create_plan("translate hello to French and save it", task)

        self.assert_single_task_with_steps(plan, ["step_1", "step_2"])
        self.assertEqual([step.tool_name for step in plan.steps], ["translator", "file_writer"])
        self.assertEqual(plan.steps[0].args["text"], "hello")
        self.assertEqual(plan.steps[0].args["target_language"], "fr")
        self.assertEqual(plan.steps[1].depends_on, ["step_1"])
        self.assertEqual(plan.steps[1].input_from, ["step_1"])
        self.assertEqual(plan.steps[1].args["file_path"], "out/fr.txt")

    def test_convert_format_generates_read_model_convert_and_optional_write(self):
        task = make_task(
            task_type="file_operation",
            intent=["convert_format"],
            intent_sequence=["convert_format"],
            parameters={
                "file_path": "docs/source.md",
                "file_type": "md",
                "target_format": "txt",
                "target_path": "out/source.txt",
            },
        )

        plan = self.planner.create_plan("convert docs/source.md to out/source.txt", task)

        self.assert_single_task_with_steps(plan, ["step_1", "step_2", "step_3"])
        self.assertEqual([step.tool_name for step in plan.steps], ["document_parser", None, "file_writer"])
        self.assertEqual([step.step_type for step in plan.steps], ["tool", "model", "tool"])
        self.assertEqual(plan.steps[1].depends_on, ["step_1"])
        self.assertEqual(plan.steps[1].args["target_format"], "txt")
        self.assertEqual(plan.steps[2].depends_on, ["step_2"])
        self.assertEqual(plan.steps[2].args["file_path"], "out/source.txt")

    def test_multiple_files_split_into_multiple_task_units(self):
        task = make_task(
            task_type="document_understanding",
            intent=["read_file", "summarize"],
            intent_sequence=["read_file", "summarize"],
            parameters={"file_paths": ["docs/a.md", "docs/b.md"]},
        )

        plan = self.planner.create_plan("summarize docs/a.md and docs/b.md", task)

        self.assertEqual(len(plan.task_units), 2)
        self.assertEqual([unit.id for unit in plan.task_units], ["task_1", "task_2"])
        self.assertEqual(plan.task_units[0].step_ids, ["step_1", "step_2"])
        self.assertEqual(plan.task_units[1].step_ids, ["step_3", "step_4"])
        self.assertEqual([step.task_id for step in plan.steps], ["task_1", "task_1", "task_2", "task_2"])
        self.assertEqual(plan.steps[0].args["file_path"], "docs/a.md")
        self.assertEqual(plan.steps[2].args["file_path"], "docs/b.md")

    def test_software_engineering_intent_generates_basic_multi_step_model_plan(self):
        task = make_task(
            task_type="software_engineering",
            intent=["debug_code"],
            intent_sequence=["debug_code"],
            parameters={"file_path": "src/app.py"},
            project_stage="debug",
            tech_stacks=["python"],
        )

        plan = self.planner.create_plan("debug src/app.py", task)

        self.assert_single_task_with_steps(plan, ["step_1", "step_2"])
        self.assertEqual([step.step_type for step in plan.steps], ["model", "respond"])
        self.assertTrue(all(step.tool_name is None for step in plan.steps))
        self.assertEqual(plan.steps[1].depends_on, ["step_1"])
        self.assertIn("rule_template:software_engineering_basic", plan.added_steps_reason)

    def test_unknown_intent_falls_back_to_meso_plan(self):
        task = make_task(
            intent=["unknown_operation"],
            intent_sequence=["unknown_operation"],
            parameters={},
        )

        plan = self.planner.create_plan("do an unknown operation", task)

        self.assertEqual(plan.mode, "meso")
        self.assertEqual([step.step_type for step in plan.steps], ["model", "respond"])
        self.assertEqual(plan.added_steps_reason, [])


if __name__ == "__main__":
    unittest.main()
