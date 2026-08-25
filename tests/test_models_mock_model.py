from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from src.agent.react_executor_protocol import parse_action_packet
from src.models import (
    ContextCompressionResult,
    EmbeddingBatchResult,
    EmbeddingResult,
    ModelCallResult,
    ModelCallType,
    ModelManager,
    MockModel,
)


class MockModelTest(unittest.TestCase):
    def test_default_generate_returns_structured_text_result(self):
        model = MockModel()

        result = model.generate("hello", call_type=ModelCallType.CHAT)

        self.assertIsInstance(result, ModelCallResult)
        self.assertTrue(result.success)
        self.assertIn("MockModel", result.content)

    def test_call_type_driven_json_responses_are_parseable(self):
        model = MockModel()

        analyzer = json.loads(model.generate("analysis", call_type="analyzer_intent_fallback").content)
        planner = json.loads(model.generate("plan", call_type="planner_structured_plan").content)
        action = json.loads(model.generate("action", call_type="react_action_decision").content)
        web_search = json.loads(model.generate("search", call_type="web_search").content)

        self.assertIn("intents", analyzer)
        self.assertIn("steps", planner)
        self.assertEqual(action["action_type"], "finish")
        self.assertTrue(parse_action_packet(action).success)
        self.assertEqual(web_search["evidence_level"], "no_url_summary")

    def test_sequence_responses_can_mix_success_and_failure(self):
        model = MockModel(
            responses=[
                "first response",
                {"success": False, "code": "invalid_json", "error": "bad json"},
                {"content": "third response"},
            ]
        )

        first = model.generate("a")
        second = model.generate("b")
        third = model.generate("c")

        self.assertTrue(first.success)
        self.assertEqual(first.content, "first response")
        self.assertFalse(second.success)
        self.assertEqual(second.code, "invalid_json")
        self.assertTrue(third.success)
        self.assertEqual(third.content, "third response")

    def test_stream_generate_uses_configured_chunks(self):
        model = MockModel()

        chunks = list(model.stream_generate("hello", stream_chunks=["he", "llo"]))

        self.assertEqual([chunk.content_delta for chunk in chunks], ["he", "llo"])
        self.assertTrue(chunks[-1].is_final)
        self.assertTrue(all(chunk.success for chunk in chunks))

    def test_embedding_and_compression_are_structured(self):
        model = MockModel(embedding_dimensions=4)

        embedding = model.embed_text("hello")
        batch = model.embed_texts(["hello", "world"])
        compression = model.compress_context("abcdef", target_chars=3)

        self.assertIsInstance(embedding, EmbeddingResult)
        self.assertTrue(embedding.success)
        self.assertEqual(len(embedding.embedding or []), 4)
        self.assertIsInstance(batch, EmbeddingBatchResult)
        self.assertEqual(len(batch.embeddings), 2)
        self.assertIsInstance(compression, ContextCompressionResult)
        self.assertEqual(compression.compressed_text, "abc")

    def test_fixture_file_can_drive_mock_responses(self):
        fixture_path = Path("tests/fixtures/models/mock_model_fixtures.json")
        payload = json.loads(fixture_path.read_text(encoding="utf-8"))
        fixtures = {item["call_type"]: item["response"] for item in payload["responses"]}
        model = MockModel(fixtures=fixtures)

        analyzer = json.loads(model.generate("analysis", call_type="analyzer_intent_fallback").content)
        planner = json.loads(model.generate("plan", call_type="planner_structured_plan").content)

        self.assertEqual(analyzer["intents"][0]["name"], "search")
        self.assertEqual(planner["steps"][0]["id"], "step_1")

    def test_real_provider_initialization_failure_does_not_fallback_to_mock(self):
        with patch.object(ModelManager, "_create_model", side_effect=ValueError("API_KEY missing")):
            manager = ModelManager(model_name="openai")

        result = manager.generate("hello")

        self.assertIsNone(manager.model)
        self.assertFalse(result.success)
        self.assertEqual(result.code, "missing_api_key")


if __name__ == "__main__":
    unittest.main()
