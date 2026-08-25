from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from src.models import (
    ContextCompressionResult,
    ModelCallResult,
    ModelErrorCode,
    ModelManager,
    ModelsConfig,
    ModelsRuntimeConfig,
    default_provider_specs,
    default_route_configs,
)


class SequenceModel:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def generate(self, prompt: str, **kwargs):
        self.calls.append({"prompt": prompt, "kwargs": dict(kwargs)})
        response = self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]
        if isinstance(response, ModelCallResult):
            return response
        return ModelCallResult.ok(
            response if isinstance(response, str) else json.dumps(response, ensure_ascii=False)
        )

    def stream_generate(self, prompt: str, **kwargs):
        yield ""


def make_config(root: Path) -> ModelsConfig:
    return ModelsConfig(
        workspace_root=root,
        config_dir=root,
        runtime=ModelsRuntimeConfig(
            logs_path=root / "logs" / "models.log",
            retry_backoff_base_seconds=0.0,
            retry_backoff_max_seconds=0.0,
        ),
        provider_specs=default_provider_specs(),
        provider_confs={},
        routes=default_route_configs(),
    )


def make_manager(root: Path, model: SequenceModel) -> ModelManager:
    manager = ModelManager(model_name="mock", models_config=make_config(root))
    manager.model = model
    return manager


class ModelsContextCompressionTest(unittest.TestCase):
    def test_short_text_is_compressed_by_model(self):
        with TemporaryDirectory() as temp_dir:
            model = SequenceModel(
                {
                    "short_summary": "Ada designed the API.",
                    "compressed_text": "Ada designed the API and left auth as unresolved.",
                    "key_points": ["API design", "auth unresolved"],
                    "preserved_entities": ["Ada"],
                    "loss_risk": "low",
                    "warnings": [],
                }
            )
            manager = make_manager(Path(temp_dir), model)

            result = manager.compress_context(
                text="Ada designed the API. Authentication remains unresolved.",
                target_chars=120,
                preserve_entities=["Ada"],
                trigger_reason="memory_budget",
                metadata={"caller": "test"},
            )

            self.assertIsInstance(result, ContextCompressionResult)
            self.assertTrue(result.success)
            self.assertEqual(result.short_summary, "Ada designed the API.")
            self.assertEqual(result.loss_risk, "low")
            self.assertEqual(result.preserved_entities, ["Ada"])
            self.assertEqual(result.metadata["compression_method"], "single_model_call")
            self.assertEqual(result.metadata["caller"], "test")
            self.assertEqual(result.source_refs, ["text"])
            self.assertEqual(model.calls[0]["kwargs"]["call_type"], "context_compression")

    def test_long_text_is_split_compressed_and_synthesized(self):
        with TemporaryDirectory() as temp_dir:
            model = SequenceModel(
                {
                    "short_summary": "Chunk one.",
                    "compressed_text": "Chunk one compressed.",
                    "key_points": ["one"],
                    "loss_risk": "medium",
                },
                {
                    "short_summary": "Chunk two.",
                    "compressed_text": "Chunk two compressed.",
                    "key_points": ["two"],
                    "loss_risk": "medium",
                },
                {
                    "short_summary": "Chunk three.",
                    "compressed_text": "Chunk three compressed.",
                    "key_points": ["three"],
                    "loss_risk": "medium",
                },
                {
                    "short_summary": "All chunks.",
                    "compressed_text": "Chunk one, two, and three compressed together.",
                    "key_points": ["one", "two", "three"],
                    "preserved_entities": ["REQ-7"],
                    "loss_risk": "medium",
                },
            )
            manager = make_manager(Path(temp_dir), model)
            text = "REQ-7 " + ("0123456789" * 250)

            result = manager.compress_context(
                text=text,
                target_chars=300,
                preserve_entities=["REQ-7"],
                trigger_reason="context_limit",
                max_chunk_chars=900,
            )

            self.assertTrue(result.success)
            self.assertEqual(len(model.calls), 4)
            self.assertEqual(result.metadata["compression_method"], "chunked_model_synthesis")
            self.assertEqual(result.metadata["chunk_count"], 3)
            self.assertEqual(result.metadata["partial_result_count"], 3)
            self.assertEqual(len(result.compressed_chunks), 3)
            self.assertIn("REQ-7", result.preserved_entities)

    def test_model_failure_returns_structured_failure_by_default(self):
        with TemporaryDirectory() as temp_dir:
            model = SequenceModel(
                ModelCallResult.fail(ModelErrorCode.MODEL_CALL_FAILED, "provider unavailable")
            )
            manager = make_manager(Path(temp_dir), model)

            result = manager.compress_context(
                text="important context",
                target_chars=20,
                trigger_reason="test_failure",
            )

            self.assertFalse(result.success)
            self.assertEqual(result.code, ModelErrorCode.MODEL_CALL_FAILED.value)
            self.assertEqual(result.error, "provider unavailable")
            self.assertEqual(result.compressed_text, "")

    def test_explicit_rule_fallback_truncates_without_hiding_status(self):
        with TemporaryDirectory() as temp_dir:
            model = SequenceModel(
                ModelCallResult.fail(ModelErrorCode.MODEL_CALL_FAILED, "provider unavailable")
            )
            manager = make_manager(Path(temp_dir), model)

            result = manager.compress_context(
                text="Ada " + ("abcdef" * 40),
                target_chars=60,
                preserve_entities=["Ada"],
                allow_rule_fallback=True,
            )

            self.assertTrue(result.success)
            self.assertIn("rule_fallback_truncation_used", result.warnings)
            self.assertEqual(result.metadata["compression_method"], "rule_fallback")
            self.assertEqual(result.metadata["fallback_reason"], "model_call_failed")
            self.assertLessEqual(len(result.compressed_text), 60 + len("Preserved entities: Ada\n"))

    def test_chunks_preserve_source_refs_and_selected_metadata(self):
        with TemporaryDirectory() as temp_dir:
            model = SequenceModel(
                {
                    "short_summary": "Chunk A.",
                    "compressed_text": "Chunk A compressed.",
                    "loss_risk": "low",
                },
                {
                    "short_summary": "Chunk B.",
                    "compressed_text": "Chunk B compressed.",
                    "loss_risk": "low",
                },
                {
                    "short_summary": "Merged chunks.",
                    "compressed_text": "Merged chunk compression.",
                    "loss_risk": "low",
                    "key_points": ["merged"],
                },
            )
            manager = make_manager(Path(temp_dir), model)

            result = manager.compress_context(
                source_type="observations",
                chunks=[
                    {
                        "id": "obs_1",
                        "source_ref": "observation_1",
                        "text": "first observation",
                        "task_id": "task_1",
                    },
                    {
                        "id": "obs_2",
                        "source_ref": "observation_2",
                        "text": "second observation",
                        "task_id": "task_2",
                    },
                ],
                preserve_keys=["task_id"],
                target_chars=80,
            )

            self.assertTrue(result.success)
            self.assertEqual(result.source_refs, ["observation_1", "observation_2"])
            self.assertEqual(result.compressed_chunks[0].metadata["preserved_fields"]["task_id"], "task_1")
            self.assertEqual(result.metadata["source_type"], "observations")


if __name__ == "__main__":
    unittest.main()
