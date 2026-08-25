from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from src.models import (
    ModelCallLogger,
    ModelCallResult,
    ModelErrorCode,
    ModelManager,
    ModelTraceContext,
    ModelUsage,
    ModelsConfig,
    ModelsRuntimeConfig,
    default_provider_specs,
    default_route_configs,
)


class StaticResultModel:
    def __init__(self, result: ModelCallResult):
        self.result = result

    def generate(self, prompt: str, **kwargs):
        return self.result

    def stream_generate(self, prompt: str, **kwargs):
        yield ""


class StaticStreamModel:
    def generate(self, prompt: str, **kwargs):
        return ModelCallResult.ok("unused")

    def stream_generate(self, prompt: str, **kwargs):
        yield "hello "
        yield "world"


class ExplodingLogger:
    def record_call(self, result, *, prompt, messages):
        raise OSError("logs directory is unavailable")


def make_config(root: Path, *, pricing: dict | None = None) -> ModelsConfig:
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
        pricing=pricing or {},
    )


class ModelsObservabilityTest(unittest.TestCase):
    def test_success_log_captures_trace_usage_cost_and_redacts_metadata(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = ModelManager(
                model_name="mock",
                models_config=make_config(
                    root,
                    pricing={
                        "default": {
                            "input_per_1k": 0.1,
                            "output_per_1k": 0.2,
                            "currency": "USD",
                        }
                    },
                ),
            )
            response = "response-" + ("y" * 300)
            manager.model = StaticResultModel(
                ModelCallResult.ok(
                    response,
                    usage=ModelUsage(
                        prompt_tokens=1000,
                        completion_tokens=500,
                        total_tokens=1500,
                        source="provider",
                    ),
                    raw_response={"unlogged": True},
                )
            )
            prompt = "prompt-" + ("x" * 300)

            result = manager.generate(
                prompt,
                messages=[{"role": "user", "content": prompt}],
                trace_context=ModelTraceContext(
                    source_trace_id="trace_123",
                    plan_id="plan_123",
                    execution_id="execution_123",
                    caller="test",
                ),
                metadata={
                    "authorization": "Bearer very-secret-token",
                    "nested": {"token": "nested-secret"},
                    "safe": "visible",
                },
            )

            self.assertTrue(result.success)
            self.assertIsNotNone(result.cost)
            self.assertAlmostEqual(result.cost.total_cost, 0.2)
            log_path = root / "logs" / "models.log"
            record = json.loads(log_path.read_text(encoding="utf-8").strip())

            self.assertTrue(record["success"])
            self.assertEqual(record["source_trace_id"], "trace_123")
            self.assertEqual(record["trace_context"]["plan_id"], "plan_123")
            self.assertEqual(record["messages_count"], 1)
            self.assertEqual(record["prompt_tokens"], 1000)
            self.assertEqual(record["completion_tokens"], 500)
            self.assertAlmostEqual(record["cost"]["total_cost"], 0.2)
            self.assertNotEqual(record["prompt_preview"], prompt)
            self.assertNotEqual(record["response_preview"], response)
            self.assertEqual(record["metadata"]["authorization"], "***")
            self.assertEqual(record["metadata"]["nested"]["token"], "***")
            self.assertNotIn("raw_response", record)

    def test_failure_log_is_structured_and_sensitive_error_text_is_redacted(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = ModelManager(model_name="mock", models_config=make_config(root))
            manager.model = StaticResultModel(
                ModelCallResult.fail(
                    ModelErrorCode.MODEL_CALL_FAILED,
                    "provider rejected request: api_key=plain-secret",
                )
            )

            result = manager.generate("hello")

            self.assertFalse(result.success)
            record = json.loads((root / "logs" / "models.log").read_text(encoding="utf-8").strip())
            self.assertFalse(record["success"])
            self.assertEqual(record["code"], ModelErrorCode.MODEL_CALL_FAILED.value)
            self.assertIn("api_key=***", record["error_summary"])
            self.assertNotIn("plain-secret", record["error_summary"])
            self.assertIsNone(record["prompt_tokens"])
            self.assertIsNone(record["cost"])

    def test_logging_failure_does_not_change_model_result(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = ModelManager(
                model_name="mock",
                models_config=make_config(root),
                call_logger=ExplodingLogger(),
            )
            manager.model = StaticResultModel(ModelCallResult.ok("still succeeds"))

            result = manager.generate("hello")

            self.assertTrue(result.success)
            self.assertEqual(result.content, "still succeeds")

    def test_logger_can_build_record_without_usage_or_cost(self):
        with TemporaryDirectory() as temp_dir:
            logger = ModelCallLogger(Path(temp_dir) / "models.log")
            result = ModelCallResult.ok("ok")

            record = logger.build_record(result, prompt="hello", messages=None)

            self.assertIsNone(record["prompt_tokens"])
            self.assertIsNone(record["cost"])
            self.assertEqual(record["messages_count"], 0)

    def test_stream_generate_writes_one_summary_log_record(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = ModelManager(model_name="mock", models_config=make_config(root))
            manager.model = StaticStreamModel()

            chunks = list(manager.stream_generate("hello"))

            self.assertEqual("".join(chunk.content_delta for chunk in chunks), "hello world")
            records = [
                json.loads(line)
                for line in (root / "logs" / "models.log").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(len(records), 1)
            self.assertTrue(records[0]["success"])
            self.assertTrue(records[0]["metadata"]["streaming"])
            self.assertEqual(records[0]["metadata"]["chunks_count"], 2)
            self.assertEqual(records[0]["response_length"], len("hello world"))


if __name__ == "__main__":
    unittest.main()
