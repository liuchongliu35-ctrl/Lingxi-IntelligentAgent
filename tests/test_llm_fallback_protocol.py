from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from src.agent.analyzer_config import load_analyzer_config
from src.agent.complexity_analyzer import ComplexityAnalyzer


class CapturingModelManager:
    def __init__(self, response):
        self.response = response
        self.prompts: list[str] = []

    def generate(self, prompt: str, **kwargs):
        self.prompts.append(prompt)
        return self.response


class LLMFallbackProtocolTest(unittest.TestCase):
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

    def _analyzer(self, model_manager):
        return ComplexityAnalyzer(analyzer_config=self.config, model_manager=model_manager)

    def test_llm_fallback_prompt_and_fenced_json_known_intent_priority(self):
        model = CapturingModelManager(
            """```json
{"intents":[
  {"name":"archive files","confidence":0.95,"reason":"unsupported archive action"},
  {"name":"search","confidence":0.70,"reason":"known intent also fits"}
]}
```"""
        )
        result = self._analyzer(model).analyze("alpha beta gamma")

        self.assertEqual(result.intent_sequence, ["search", "archive_files"])
        self.assertEqual(result.llm_fallback_status, "parsed")
        self.assertEqual(result.pending_intents_recorded, ["archive_files"])
        self.assertIn("Return strict JSON only", model.prompts[0])
        self.assertIn("Return at most 4 intents", model.prompts[0])
        self.assertIn("unknown", model.prompts[0])

    def test_llm_fallback_parse_failure_becomes_unknown_clarification(self):
        model = CapturingModelManager("当前未配置真实大模型，已使用 MockModel 返回占位响应。")
        result = self._analyzer(model).analyze("alpha beta gamma")

        self.assertEqual(result.intent_sequence, ["unknown"])
        self.assertEqual(result.intent_source, "llm")
        self.assertEqual(result.llm_fallback_status, "parse_failed")
        self.assertEqual(result.llm_fallback_error, "no_parseable_json_intents")
        self.assertTrue(result.requires_clarification)
        self.assertIn("我还不能确定你希望我执行的具体任务，请补充目标、对象或期望输出。", result.clarification_questions)
        self.assertNotEqual(result.intent_sequence, ["chat"])

    def test_llm_fallback_explicit_unknown_is_allowed(self):
        model = CapturingModelManager({"intents": [{"name": "unknown", "confidence": 0.82, "reason": "unclear"}]})
        result = self._analyzer(model).analyze("alpha beta gamma")

        self.assertEqual(result.intent_sequence, ["unknown"])
        self.assertEqual(result.llm_fallback_status, "parsed")
        self.assertTrue(result.requires_clarification)
        self.assertEqual(result.pending_intents_recorded, [])

    def test_llm_fallback_low_confidence_custom_intent_becomes_unknown(self):
        model = CapturingModelManager({"intents": [{"name": "archive_files", "confidence": 0.40, "reason": "weak guess"}]})
        result = self._analyzer(model).analyze("alpha beta gamma")

        self.assertEqual(result.intent_sequence, ["unknown"])
        self.assertEqual(result.llm_fallback_status, "unknown")
        self.assertEqual(result.llm_fallback_error, "all_candidates_below_threshold")
        self.assertFalse(self.config.pending_intents_path.exists())


if __name__ == "__main__":
    unittest.main()
