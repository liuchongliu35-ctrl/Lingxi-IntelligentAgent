from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any

from src.agent.analyzer_config import load_analyzer_config
from src.agent.complexity_analyzer import ComplexityAnalyzer


class FakeModelManager:
    def generate(self, prompt: str, **kwargs):
        return json.dumps(
            {
                "intents": [
                    {
                        "name": "archive_files",
                        "confidence": 0.72,
                        "reason": "User asks for an unsupported archive operation.",
                    }
                ]
            }
        )


class AnalyzerV1Test(unittest.TestCase):
    def setUp(self):
        self.repo_root = Path(__file__).resolve().parents[1]
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self._copy_analyzer_config()
        load_analyzer_config.cache_clear()
        self.config = load_analyzer_config(self.root)

    def tearDown(self):
        self.temp_dir.cleanup()
        load_analyzer_config.cache_clear()

    def _copy_analyzer_config(self):
        source_dir = self.repo_root / "config" / "analyzer"
        target_dir = self.root / "config" / "analyzer"
        target_dir.mkdir(parents=True)
        for source_path in source_dir.glob("*.json"):
            shutil.copyfile(source_path, target_dir / source_path.name)

        analyzer_config_path = target_dir / "analyzer_config.json"
        analyzer_config = json.loads(analyzer_config_path.read_text(encoding="utf-8"))
        analyzer_config["log_path"] = "logs/analyzer.log"
        analyzer_config["pending_intents_path"] = "storage/analyzer/pending_intents.json"
        analyzer_config_path.write_text(json.dumps(analyzer_config, ensure_ascii=False, indent=2), encoding="utf-8")

    def _analyzer(self, model_manager=None):
        return ComplexityAnalyzer(analyzer_config=self.config, model_manager=model_manager)

    def test_search_extracts_topic_count_and_time_range(self):
        result = self._analyzer().analyze("搜索关于大语言模型的最新5篇论文并总结重点")

        self.assertEqual(result.intent_sequence, ["search", "summarize"])
        self.assertEqual(result.parameters["topic"], "大语言模型")
        self.assertEqual(result.parameters["count"], 5)
        self.assertEqual(result.parameters["time_range"], "latest")
        self.assertNotIn("topic", result.missing_parameters)

    def test_translate_missing_target_language_generates_clarification(self):
        result = self._analyzer().analyze("翻译：hello world")

        self.assertIn("target_language", result.missing_parameters)
        self.assertIn("请说明要翻译成哪种语言。", result.clarification_questions)
        self.assertTrue(result.requires_clarification)

    def test_log_contains_decision_summary_and_trace_fields(self):
        result = self._analyzer().analyze("翻译：hello world")

        log_entry = self._read_last_log_entry()
        self.assertEqual(log_entry["trace_id"], result.trace_id)
        self.assertEqual(log_entry["requires_clarification"], True)
        self.assertIn("target_language", log_entry["clarification_decision"])
        self.assertIn("模式判定", log_entry["mode_decision"])
        self.assertIn("工具策略", log_entry["tool_decision"])
        self.assertIn("本轮没有写入 pending intents", log_entry["pending_intent_decision"])
        self.assertGreaterEqual(len(log_entry["decision_summary"]), 5)
        self.assertEqual(log_entry["user_facing_summary"], result.user_facing_summary)

    def test_translate_extracts_target_language_and_content(self):
        result = self._analyzer().analyze("把 hello world 翻译成中文")

        self.assertEqual(result.parameters["target_language"], "zh")
        self.assertNotIn("target_language", result.missing_parameters)

    def test_file_operation_extracts_source_and_target_paths(self):
        result = self._analyzer().analyze("移动文件 data/a.txt 到 archive/a.txt")

        self.assertEqual(result.parameters["source_path"], "data/a.txt")
        self.assertEqual(result.parameters["target_path"], "archive/a.txt")
        self.assertNotIn("source_path", result.missing_parameters)
        self.assertNotIn("target_path", result.missing_parameters)

    def test_move_file_missing_target_path_generates_clarification(self):
        result = self._analyzer().analyze("移动文件 data/a.txt")

        self.assertIn("target_path", result.missing_parameters)
        self.assertIn("请补充目标文件路径或新文件名。", result.clarification_questions)

    def test_pending_intent_records_llm_custom_intent(self):
        result = self._analyzer(model_manager=FakeModelManager()).analyze("帮我把这个目录打包归档")

        self.assertEqual(result.intent_sequence, ["archive_files"])
        pending_path = self.config.pending_intents_path
        self.assertTrue(pending_path.exists())
        pending = json.loads(pending_path.read_text(encoding="utf-8"))
        self.assertEqual(pending[0]["raw_name"], "archive_files")
        self.assertEqual(pending[0]["normalized_name"], "archive_files")
        self.assertEqual(pending[0]["status"], "pending")
        self.assertEqual(pending[0]["occurrence_count"], 1)
        log_entry = self._read_last_log_entry()
        self.assertEqual(log_entry["pending_intents_recorded"], ["archive_files"])
        self.assertIn("archive_files", log_entry["pending_intent_decision"])

    def test_fixture_cases(self):
        cases_path = self.repo_root / "tests" / "fixtures" / "analyzer_cases.json"
        cases = json.loads(cases_path.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(cases), 30)

        analyzer = self._analyzer()
        for case in cases:
            with self.subTest(case=case["name"]):
                result = analyzer.analyze(case["text"])
                self._assert_expected(result.to_dict(), case["expected"])

    def _assert_expected(self, result: dict[str, Any], expected: dict[str, Any]):
        direct_fields = [
            "mode",
            "mode_source",
            "task_type",
            "complexity_level",
            "execution_strategy",
            "action_policy",
            "risk_level",
            "requires_confirmation",
            "confirmation_reason",
            "tool_strategy",
            "project_stage",
        ]
        for field in direct_fields:
            if field in expected:
                self.assertEqual(result.get(field), expected[field], field)

        if "intent_sequence" in expected:
            self.assertEqual(result["intent_sequence"], expected["intent_sequence"])
        if "intent_sequence_contains" in expected:
            self._assert_contains_all(result["intent_sequence"], expected["intent_sequence_contains"], "intent_sequence")
        if "missing_parameters_contains" in expected:
            self._assert_contains_all(result["missing_parameters"], expected["missing_parameters_contains"], "missing_parameters")
        if "missing_parameters_absent" in expected:
            self._assert_absent_all(result["missing_parameters"], expected["missing_parameters_absent"], "missing_parameters")
        if "risk_flags_contains" in expected:
            self._assert_contains_all(result["risk_flags"], expected["risk_flags_contains"], "risk_flags")
        if "tech_stacks_contains" in expected:
            self._assert_contains_all(result["tech_stacks"], expected["tech_stacks_contains"], "tech_stacks")
        if "clarification_questions_contains" in expected:
            self._assert_contains_all(
                result["clarification_questions"],
                expected["clarification_questions_contains"],
                "clarification_questions",
            )
        if "parameters" in expected:
            self._assert_mapping_subset(result["parameters"], expected["parameters"], "parameters")
        if "file_info" in expected:
            self._assert_mapping_subset(result["file_info"], expected["file_info"], "file_info")

    def _assert_contains_all(self, actual: list[Any], expected_items: list[Any], field_name: str):
        for item in expected_items:
            self.assertIn(item, actual, field_name)

    def _assert_absent_all(self, actual: list[Any], expected_items: list[Any], field_name: str):
        for item in expected_items:
            self.assertNotIn(item, actual, field_name)

    def _assert_mapping_subset(self, actual: dict[str, Any], expected: dict[str, Any], field_name: str):
        for key, value in expected.items():
            self.assertEqual(actual.get(key), value, f"{field_name}.{key}")

    def _read_last_log_entry(self) -> dict[str, Any]:
        log_path = self.config.log_path
        self.assertTrue(log_path.exists())
        lines = [line for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertTrue(lines)
        return json.loads(lines[-1])


if __name__ == "__main__":
    unittest.main()
