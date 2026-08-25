from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.agent.analyzer_config import load_analyzer_config
from src.agent.complexity_analyzer import ComplexityAnalyzer
from src.agent.planner import Planner
from src.agent.planner_config import load_planner_config
from src.agent.react_agent import ReactAgent


class FakeToolManager:
    def list_tools(self):
        return {
            "document_parser": "Read files.",
            "text_processor": "Process text.",
            "math_calculator": "Calculate expressions.",
            "translator": "Translate text.",
            "search_tool": "Search information.",
            "file_writer": "Write files.",
        }


class FakeModelManager:
    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []

    def generate(self, prompt: str, **kwargs):
        self.prompts.append(prompt)
        if len(self.responses) > 1:
            return self.responses.pop(0)
        return self.responses[0]


class FakeShortTermMemory:
    def __init__(self):
        self.messages = []

    def add_message(self, role: str, content: str) -> None:
        self.messages.append((role, content))

    def get_history_text(self) -> str:
        return "\n".join(f"{role}: {content}" for role, content in self.messages)


class AnalyzerPlannerPipelineTest(unittest.TestCase):
    def setUp(self):
        self.repo_root = Path(__file__).resolve().parents[1]
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self._copy_config("analyzer")
        self._copy_config("planner")
        load_analyzer_config.cache_clear()
        load_planner_config.cache_clear()
        self.analyzer_config = load_analyzer_config(self.root)
        self.planner_config = load_planner_config(self.root)

    def tearDown(self):
        self.temp_dir.cleanup()
        load_analyzer_config.cache_clear()
        load_planner_config.cache_clear()

    def _copy_config(self, name: str) -> None:
        source_dir = self.repo_root / "config" / name
        target_dir = self.root / "config" / name
        target_dir.mkdir(parents=True)
        for source_path in source_dir.glob("*.json"):
            shutil.copyfile(source_path, target_dir / source_path.name)

        if name == "analyzer":
            config_path = target_dir / "analyzer_config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["log_path"] = "logs/analyzer_pipeline.log"
            config["pending_intents_path"] = "storage/analyzer/pending_intents_pipeline.json"
            config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        elif name == "planner":
            config_path = target_dir / "planner_config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["planner_log_path"] = "logs/planner_pipeline.log"
            config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

    def _analyze_and_plan(self, user_input: str):
        analyzer = ComplexityAnalyzer(
            analyzer_config=self.analyzer_config,
            tool_manager=FakeToolManager(),
        )
        planner = Planner(planner_config=self.planner_config)
        task = analyzer.analyze(user_input)
        plan = planner.create_plan(user_input, task)
        return task, plan

    def test_analyzer_search_summarize_output_becomes_rule_template_plan(self):
        task, plan = self._analyze_and_plan("搜索关于Python测试框架的资料并总结")

        self.assertIn("search", task.intent_sequence)
        self.assertIn("summarize", task.intent_sequence)
        self.assertEqual(plan.mode, "meso")
        self.assertEqual(plan.planning_strategy, "rule_template")
        self.assertEqual([step.tool_name for step in plan.steps], ["search_tool", "text_processor"])
        self.assertEqual(plan.steps[1].input_from, ["step_1"])
        self.assertTrue(plan.can_execute)
        self.assertEqual(plan.plan_validation_status, "valid")

    def test_analyzer_special_policies_short_circuit_planner(self):
        clarify_task, clarify_plan = self._analyze_and_plan("翻译：hello world")
        self.assertTrue(clarify_task.requires_clarification)
        self.assertEqual(clarify_plan.mode, "clarify")
        self.assertFalse(clarify_plan.can_execute)

        blocked_task, blocked_plan = self._analyze_and_plan("执行命令 rm -rf /")
        self.assertEqual(blocked_task.action_policy, "block")
        self.assertEqual(blocked_plan.mode, "blocked")
        self.assertFalse(blocked_plan.can_execute)

    def test_react_agent_default_planner_receives_model_manager_for_llm_planning(self):
        llm_plan = {
            "mode": "meso",
            "task_units": [{"id": "task_1", "title": "Answer", "step_ids": ["step_1"]}],
            "steps": [
                {
                    "id": "step_1",
                    "task_id": "task_1",
                    "step_type": "respond",
                    "description": "Answer the unusual request.",
                    "expected_output": "Answer",
                }
            ],
        }
        model_manager = FakeModelManager([llm_plan, "final response"])
        task = SimpleNamespace(
            trace_id="trace_agent_pipeline",
            mode="solo",
            task_type="qa",
            execution_strategy="meso_advanced",
            action_policy="allow",
            requires_clarification=False,
            clarification_questions=[],
            missing_parameters=[],
            requires_confirmation=False,
            confirmation_reason=None,
            tool_strategy="model_only",
            available_tools=[],
            missing_tools=[],
            intent=["unknown_operation"],
            intent_sequence=["unknown_operation"],
            parameters={},
            file_info={},
            edit_mode=None,
            project_stage=None,
            tech_stacks=[],
            complexity_level="complex",
            risk_flags=[],
        )
        analyzer = SimpleNamespace(analyze=lambda user_input: task)
        agent = ReactAgent(
            model_manager=model_manager,
            short_term_memory=FakeShortTermMemory(),
            long_term_memory=SimpleNamespace(),
            tool_manager=FakeToolManager(),
            rag_system=SimpleNamespace(),
            complexity_analyzer=analyzer,
            executor_type="legacy",
        )

        response = agent.run("handle unusual request")

        self.assertEqual(response, "final response")
        self.assertEqual(len(model_manager.prompts), 2)
        self.assertIn("Plan JSON schema", model_manager.prompts[0])
        self.assertIn("Step: Answer the unusual request.", model_manager.prompts[1])

    def test_llm_plan_exceeding_config_limits_is_invalid(self):
        oversized_plan = {
            "mode": "meso",
            "task_units": [
                {
                    "id": "task_1",
                    "title": "Oversized",
                    "step_ids": [f"step_{index}" for index in range(1, 22)],
                }
            ],
            "steps": [
                {
                    "id": f"step_{index}",
                    "task_id": "task_1",
                    "step_type": "respond",
                    "description": f"Step {index}",
                    "expected_output": "Response",
                }
                for index in range(1, 22)
            ],
        }
        model_manager = FakeModelManager([oversized_plan])
        task = SimpleNamespace(
            trace_id="trace_oversized_plan",
            mode="solo",
            task_type="qa",
            execution_strategy="meso_advanced",
            action_policy="allow",
            requires_clarification=False,
            clarification_questions=[],
            missing_parameters=[],
            requires_confirmation=False,
            confirmation_reason=None,
            tool_strategy="model_only",
            available_tools=[],
            missing_tools=[],
            intent=["unknown_operation"],
            intent_sequence=["unknown_operation"],
            parameters={},
            file_info={},
            edit_mode=None,
            project_stage=None,
            tech_stacks=[],
            complexity_level="complex",
            risk_flags=[],
        )
        planner = Planner(planner_config=self.planner_config, model_manager=model_manager)

        plan = planner.create_plan("handle oversized request", task)

        self.assertEqual(plan.plan_validation_status, "invalid")
        self.assertFalse(plan.can_execute)
        self.assertTrue(
            any("step count exceeds max_plan_steps" in note for note in plan.plan_validation_notes),
            plan.plan_validation_notes,
        )


if __name__ == "__main__":
    unittest.main()
