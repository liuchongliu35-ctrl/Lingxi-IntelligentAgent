from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from src.agent.analyzer_config import load_analyzer_config
from src.agent.complexity_analyzer import ComplexityAnalyzer


V1_REQUIRED_FIELDS = {
    "raw_input",
    "cleaned_input",
    "mode",
    "mode_source",
    "task_type",
    "intents",
    "intent_sequence",
    "entities",
    "parameters",
    "missing_parameters",
    "clarification_questions",
    "file_info",
    "edit_mode",
    "project_stage",
    "tech_stacks",
    "risk_level",
    "risk_flags",
    "action_policy",
    "requires_confirmation",
    "confirmation_reason",
    "dimension_scores",
    "complexity_score",
    "complexity_level",
    "execution_strategy",
    "recommended_tools",
    "available_tools",
    "missing_tools",
    "tool_strategy",
    "confidence_score",
    "confidence_level",
    "raw_analysis_trace",
    "trace_id",
    "decision_summary",
    "pending_intents_recorded",
    "user_facing_summary",
}


class ArchiveIntentModel:
    def generate(self, prompt: str, **kwargs):
        return {
            "intents": [
                {
                    "name": "archive_files",
                    "confidence": 0.8,
                    "reason": "The user asks to archive files.",
                }
            ]
        }


class EmptyToolManager:
    def list_tools(self):
        return {}


class AnalyzerV1AcceptanceTest(unittest.TestCase):
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

    def _analyzer(self, **kwargs):
        return ComplexityAnalyzer(analyzer_config=self.config, **kwargs)

    def test_all_fixture_cases_emit_v1_required_fields_and_logs(self):
        cases_path = self.repo_root / "tests" / "fixtures" / "analyzer_cases.json"
        cases = json.loads(cases_path.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(cases), 50)

        analyzer = self._analyzer()
        for case in cases:
            with self.subTest(case=case["name"]):
                result = analyzer.analyze(case["text"]).to_dict()
                self.assertTrue(V1_REQUIRED_FIELDS.issubset(result.keys()))
                self.assertEqual(result["raw_input"], case["text"])
                self.assertEqual(result["cleaned_input"], case["text"].strip())
                self.assertTrue(result["trace_id"])
                self.assertTrue(result["intent_sequence"])
                self.assertEqual(result["intent"], result["intent_sequence"])
                self.assertIn(result["mode"], {"solo", "chat"})
                self.assertIn(result["action_policy"], {"allow", "confirm", "block"})
                self.assertIn(result["tool_strategy"], {"tool", "model_only", "blocked_missing_tools"})
                self.assertIsInstance(result["dimension_scores"], dict)
                self.assertGreaterEqual(len(result["dimension_scores"]), 7)
                self.assertGreaterEqual(len(result["decision_summary"]), 7)
                self.assertTrue(result["raw_analysis_trace"])
                self.assertTrue(result["user_facing_summary"])

        log_entries = self._read_log_entries()
        self.assertGreaterEqual(len(log_entries), len(cases))
        last_entry = log_entries[-1]
        self.assertTrue(V1_REQUIRED_FIELDS.intersection(last_entry.keys()))
        self.assertIn("trace_id", last_entry)
        self.assertIn("decision_summary", last_entry)
        self.assertIn("raw_analysis_trace", last_entry)

    def test_v1_clarification_risk_tool_classifier_pending_acceptance_paths(self):
        analyzer = self._analyzer()

        clarification = analyzer.analyze("翻译：hello world")
        self.assertTrue(clarification.requires_clarification)
        self.assertIn("target_language", clarification.missing_parameters)
        self.assertTrue(clarification.clarification_questions)

        risk = analyzer.analyze("执行命令 rm -rf /")
        self.assertEqual(risk.action_policy, "block")
        self.assertEqual(risk.risk_level, "high")
        self.assertIn("dangerous_command", risk.risk_flags)

        no_tools = self._analyzer(tool_manager=EmptyToolManager()).analyze("搜索关于Python测试框架的资料")
        self.assertEqual(no_tools.tool_strategy, "blocked_missing_tools")
        self.assertIn("search_tool", no_tools.missing_tools)

        classifier_fallback = analyzer.analyze("alpha beta gamma")
        self.assertEqual(classifier_fallback.intent_sequence, ["chat"])
        self.assertEqual(classifier_fallback.intent_source, "fallback")
        self.assertEqual(classifier_fallback.uncertainty_reason, "classifier_not_ready")

        pending = self._analyzer(model_manager=ArchiveIntentModel()).analyze("alpha beta gamma")
        self.assertEqual(pending.intent_sequence, ["archive_files"])
        self.assertEqual(pending.pending_intents_recorded, ["archive_files"])
        self.assertTrue(self.config.pending_intents_path.exists())

        log_entries = self._read_log_entries()
        self.assertTrue(any(entry.get("action_policy") == "block" for entry in log_entries))
        self.assertTrue(any(entry.get("tool_strategy") == "blocked_missing_tools" for entry in log_entries))
        self.assertTrue(any(entry.get("pending_intents_recorded") == ["archive_files"] for entry in log_entries))

    def _read_log_entries(self):
        self.assertTrue(self.config.log_path.exists())
        return [
            json.loads(line)
            for line in self.config.log_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]


if __name__ == "__main__":
    unittest.main()
