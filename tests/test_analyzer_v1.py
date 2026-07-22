from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

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
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        config_dir = self.root / "config" / "analyzer"
        config_dir.mkdir(parents=True)
        self._write_json(
            config_dir / "analyzer_config.json",
            {
                "agent_mode": "solo",
                "max_intents": 4,
                "intent_score_threshold": 50,
                "pending_intent_threshold": 0.65,
                "allow_auto_pending_intents": False,
                "log_path": "logs/analyzer.log",
                "pending_intents_path": "storage/analyzer/pending_intents.json",
                "supported_file_types": ["txt", "md", "pdf", "docx", "xlsx", "csv", "json"],
                "confidence_thresholds": {"high": 0.85, "medium": 0.6},
            },
        )
        self._write_json(
            config_dir / "intents.json",
            [
                "calculate",
                "search",
                "summarize",
                "translate",
                "write",
                "read_file",
                "write_file",
                "move_file",
                "copy_file",
                "rename_file",
                "delete_file",
                "chat",
            ],
        )
        self._write_json(
            config_dir / "intent_keywords.json",
            {
                "calculate": ["计算", "+", "-", "*", "/", "×"],
                "search": ["搜索", "查询", "找资料"],
                "summarize": ["总结"],
                "translate": ["翻译", "译成", "翻成"],
                "write": ["写", "生成"],
                "read_file": ["读取"],
                "write_file": ["写入文件", "保存到", "输出到"],
                "move_file": ["移动文件"],
                "copy_file": ["复制文件"],
                "rename_file": ["重命名", "改名"],
                "delete_file": ["删除"],
                "chat": ["告诉我", "怎么做"],
            },
        )
        self._write_json(
            config_dir / "risk_rules.json",
            {
                "domain_risks": {},
                "confirm_intents": ["delete_file"],
                "block_keywords": ["system32"],
                "sensitive_paths": ["C:\\Windows"],
            },
        )
        self._write_json(
            config_dir / "complexity_weights.json",
            {
                "weights": {
                    "uncertainty": 3.0,
                    "steps": 2.0,
                    "domain_risk": 1.8,
                    "tools": 1.5,
                    "information": 1.5,
                    "data_processing": 1.2,
                    "creativity": 1.0,
                },
                "thresholds": {"simple_max": 10, "medium_max": 30},
                "risk_bonus": 10,
            },
        )
        self._write_json(config_dir / "tech_stacks.json", {})
        self._write_json(
            config_dir / "tool_mapping.json",
            {
                "calculate": ["math_calculator"],
                "search": ["search_tool"],
                "summarize": ["text_processor"],
                "translate": ["translator"],
                "read_file": ["document_parser"],
                "write_file": ["file_writer"],
            },
        )
        self.config = load_analyzer_config(self.root)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _write_json(self, path: Path, data):
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

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


if __name__ == "__main__":
    unittest.main()
