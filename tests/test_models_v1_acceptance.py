from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from src.models import (
    ContextCompressionResult,
    EmbeddingBatchResult,
    EmbeddingResult,
    ModelCallResult,
    ModelCallType,
    ModelHealthStatus,
    ModelManager,
    ModelStreamChunk,
    ModelsConfig,
    ModelsRuntimeConfig,
    MockModel,
    ProviderConf,
    ProviderCredential,
    StructuredModelResult,
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
        return ModelCallResult.ok(str(response))

    def stream_generate(self, prompt: str, **kwargs):
        yield "acceptance"


class FakeCompletions:
    def __init__(self):
        self.calls = []
        self.stream_response = [
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="hel"))]),
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="lo"))]),
        ]

    def create(self, **payload):
        self.calls.append(payload)
        if payload.get("stream"):
            return self.stream_response
        return SimpleNamespace(
            id="acceptance-chat-request",
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="pong"),
                    finish_reason="stop",
                )
            ],
            usage=SimpleNamespace(prompt_tokens=2, completion_tokens=1, total_tokens=3),
        )


class FakeEmbeddings:
    def __init__(self):
        self.calls = []

    def create(self, **payload):
        self.calls.append(payload)
        inputs = list(payload.get("input") or [])
        return SimpleNamespace(
            id="acceptance-embedding-request",
            data=[
                SimpleNamespace(embedding=[float(index), float(index) + 0.5])
                for index, _ in enumerate(inputs)
            ],
            usage=SimpleNamespace(prompt_tokens=len(inputs), total_tokens=len(inputs)),
        )


class FakeOpenAIClient:
    def __init__(self, completions: FakeCompletions, embeddings: FakeEmbeddings):
        self.chat = SimpleNamespace(completions=completions)
        self.embeddings = embeddings


def _runtime(root: Path) -> ModelsRuntimeConfig:
    return ModelsRuntimeConfig(
        logs_path=root / "logs" / "models.log",
        retry_backoff_base_seconds=0.0,
        retry_backoff_max_seconds=0.0,
    )


def _models_config(
    root: Path,
    *,
    provider_confs: dict[str, ProviderConf] | None = None,
    structured_output: dict | None = None,
) -> ModelsConfig:
    return ModelsConfig(
        workspace_root=root,
        config_dir=root,
        runtime=_runtime(root),
        provider_specs=default_provider_specs(),
        provider_confs=provider_confs or {},
        routes=default_route_configs(),
        structured_output=structured_output or {
            "repair_enabled": True,
            "default_repair_attempts": 1,
        },
    )


def _openai_provider_conf() -> ProviderConf:
    return ProviderConf(
        id="conf_acceptance_openai",
        name="Acceptance OpenAI",
        provider="openai",
        protocol="openai-compatible",
        enabled=True,
        base_url="https://api.openai.com/v1",
        default_model="gpt-4o-mini",
        credentials=[ProviderCredential(slug="primary", api_key_env="OPENAI_API_KEY")],
        metadata={
            "authorization": "Bearer should-not-leak",
            "labels": ["acceptance"],
        },
    )


class ModelsV1AcceptanceTest(unittest.TestCase):
    def test_mock_surface_returns_structured_results_across_v1_capabilities(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = ModelManager(
                model_name="mock",
                models_config=_models_config(root),
            )

            generated = manager.generate("hello")
            chunks = list(manager.stream_generate("hello"))
            compressed = manager.compress_context(
                text="Ada built the model layer.",
                target_chars=80,
                trigger_reason="acceptance",
            )
            embedding = manager.embed_text("hello")
            batch = manager.embed_texts(["hello", "world"])
            health = manager.health_check()
            routes = manager.get_default_routes()

        self.assertIsInstance(generated, ModelCallResult)
        self.assertTrue(generated.success)
        self.assertTrue(all(isinstance(chunk, ModelStreamChunk) for chunk in chunks))
        self.assertTrue(chunks[-1].is_final)
        self.assertIsInstance(compressed, ContextCompressionResult)
        self.assertTrue(compressed.success)
        self.assertIsInstance(embedding, EmbeddingResult)
        self.assertTrue(embedding.success)
        self.assertEqual(embedding.metadata["route"], "embedding")
        self.assertIsInstance(batch, EmbeddingBatchResult)
        self.assertEqual(len(batch.embeddings), 2)
        self.assertIsInstance(health, ModelHealthStatus)
        self.assertTrue(health.healthy)
        self.assertEqual(routes["defaults"]["chat"], "chat")
        self.assertEqual(routes["defaults"]["embedding"], "embedding")

    def test_generate_json_repair_is_part_of_v1_contract(self):
        with TemporaryDirectory() as temp_dir:
            manager = ModelManager(
                model_name="mock",
                models_config=_models_config(Path(temp_dir)),
            )
            manager.model = SequenceModel("not JSON", '{"ok": true}')

            result = manager.generate_json("return JSON", parse_mode="strict")

        self.assertIsInstance(result, StructuredModelResult)
        self.assertTrue(result.success)
        self.assertEqual(result.data, {"ok": True})
        self.assertEqual(result.repair_attempts, 1)
        self.assertEqual(manager.model.calls[0]["kwargs"]["call_type"], ModelCallType.CHAT.value)

    def test_openai_compatible_fake_provider_acceptance_without_network(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            completions = FakeCompletions()
            embeddings = FakeEmbeddings()
            conf = _openai_provider_conf()
            config = _models_config(root, provider_confs={conf.id: conf})

            def client_factory(**kwargs):
                self.assertEqual(kwargs["api_key"], "sk-acceptance")
                self.assertEqual(kwargs["base_url"], "https://api.openai.com/v1")
                return FakeOpenAIClient(completions, embeddings)

            with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-acceptance"}, clear=False):
                manager = ModelManager(
                    models_config=config,
                    provider_conf_id=conf.id,
                    client_factory=client_factory,
                )
                health = manager.health_check()
                verify = manager.verify_provider_config(conf.id)
                generated = manager.generate(
                    "Say pong.",
                    metadata={"authorization": "Bearer request-secret"},
                )
                chunks = list(manager.stream_generate("Say hello."))
                embedding = manager.embed_texts(
                    ["alpha", "beta"],
                    model="text-embedding-3-small",
                )
                provider_info = manager.get_provider_config(conf.id)

            log_text = (root / "logs" / "models.log").read_text(encoding="utf-8")

        self.assertTrue(health.healthy)
        self.assertTrue(verify.healthy)
        self.assertEqual(conf.status, "active")
        self.assertTrue(generated.success)
        self.assertEqual(generated.content, "pong")
        self.assertEqual("".join(chunk.content_delta for chunk in chunks), "hello")
        self.assertTrue(chunks[-1].is_final)
        self.assertTrue(embedding.success)
        self.assertEqual(embedding.embeddings, [[0.0, 0.5], [1.0, 1.5]])
        self.assertTrue(provider_info["supports_embedding"])
        self.assertTrue(provider_info["is_available_for_chat"])
        self.assertTrue(provider_info["is_available_for_embedding"])
        self.assertEqual(provider_info["metadata"]["authorization"], "***")
        self.assertNotIn("request-secret", log_text)
        self.assertNotIn("should-not-leak", json.dumps(provider_info, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
