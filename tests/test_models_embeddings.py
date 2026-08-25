from __future__ import annotations

import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.models import (
    DEFAULT_EMBEDDING_MODEL,
    EmbeddingBatchResult,
    EmbeddingResult,
    ModelCallType,
    ModelManager,
)
from src.models.config import (
    ModelsConfig,
    ModelsRuntimeConfig,
    ProviderConf,
    ProviderCredential,
    RouteCandidate,
    RouteConfig,
    default_provider_specs,
)
from src.models.credentials import resolve_credential_secret
from src.models.errors import ModelErrorCode
from src.models.protocol import ModelCallOptions
from src.models.providers.openai_compatible import OpenAICompatibleProvider


class FakeEmbeddings:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def create(self, **payload):
        self.calls.append(payload)
        if self.error is not None:
            raise self.error
        return self.response


class FakeChatCompletions:
    def __init__(self):
        self.calls = []

    def create(self, **payload):
        self.calls.append(payload)
        raise AssertionError("chat completions must not be used for embedding calls")


class FakeClient:
    def __init__(self, embeddings: FakeEmbeddings):
        self.embeddings = embeddings
        self.chat = SimpleNamespace(completions=FakeChatCompletions())


def _mock_provider_conf(
    provider_conf_id: str,
    *,
    model: str,
    provider_name: str = "mock",
) -> ProviderConf:
    return ProviderConf(
        id=provider_conf_id,
        name=provider_conf_id,
        provider=provider_name,
        protocol="mock",
        enabled=True,
        default_model=model,
        credentials=[ProviderCredential(slug="default")],
    )


def _mock_models_config() -> ModelsConfig:
    provider_specs = default_provider_specs()
    provider_confs = {
        "conf_chat_mock": _mock_provider_conf(
            "conf_chat_mock",
            model="mock-chat-v1",
        ),
        "conf_embed_mock": _mock_provider_conf(
            "conf_embed_mock",
            model="mock-embedding-v1",
        ),
    }
    routes = {
        "chat": RouteConfig(
            route="chat",
            default_model_policy="explicit_candidates",
            candidates=[
                RouteCandidate(
                    provider_conf_id="conf_chat_mock",
                    credential_slug="default",
                    model="mock-chat-v1",
                )
            ],
        ),
        "embedding": RouteConfig(
            route="embedding",
            default_model_policy="explicit_candidates",
            candidates=[
                RouteCandidate(
                    provider_conf_id="conf_embed_mock",
                    credential_slug="default",
                    model="mock-embedding-v1",
                )
            ],
        ),
    }
    return ModelsConfig(
        workspace_root=Path.cwd(),
        config_dir=Path.cwd() / "config" / "models",
        runtime=ModelsRuntimeConfig(),
        provider_specs=provider_specs,
        provider_confs=provider_confs,
        routes=routes,
        pricing={},
        structured_output={},
        loaded_files=[],
    )


def _openai_provider_conf() -> ProviderConf:
    return ProviderConf(
        id="conf_openai_embedding",
        name="OpenAI",
        provider="openai",
        protocol="openai-compatible",
        enabled=True,
        base_url="https://api.openai.com/v1",
        default_model="text-embedding-3-small",
        credentials=[ProviderCredential(slug="primary", api_key_env="OPENAI_API_KEY")],
    )


def _unsupported_embedding_models_config() -> ModelsConfig:
    return ModelsConfig(
        workspace_root=Path.cwd(),
        config_dir=Path.cwd() / "config" / "models",
        runtime=ModelsRuntimeConfig(),
        provider_specs=default_provider_specs(),
        provider_confs={
            "conf_custom_chat": ProviderConf(
                id="conf_custom_chat",
                name="Custom Chat",
                provider="custom_openai_compatible",
                protocol="openai-compatible",
                enabled=True,
                base_url="https://example.invalid/v1",
                default_model="custom-chat-model",
                credentials=[
                    ProviderCredential(
                        slug="default",
                        api_key_env="CUSTOM_MODEL_API_KEY",
                    )
                ],
            )
        },
        routes={
            "embedding": RouteConfig(
                route="embedding",
                default_model_policy="explicit_candidates",
                candidates=[
                    RouteCandidate(
                        provider_conf_id="conf_custom_chat",
                        credential_slug="default",
                        model="custom-chat-model",
                    )
                ],
            )
        },
        pricing={},
        structured_output={},
        loaded_files=[],
    )


class ModelsEmbeddingTest(unittest.TestCase):
    def test_mock_embedding_is_deterministic_and_structured(self):
        manager = ModelManager(model_name="mock")

        first = manager.embed_text("hello")
        second = manager.embed_text("hello")
        different = manager.embed_text("world")

        self.assertIsInstance(first, EmbeddingResult)
        self.assertTrue(first.success)
        self.assertEqual(first.embedding, second.embedding)
        self.assertNotEqual(first.embedding, different.embedding)
        self.assertEqual(first.model, DEFAULT_EMBEDDING_MODEL)
        self.assertEqual(first.metadata["route"], "embedding")
        self.assertEqual(first.metadata["call_type"], ModelCallType.EMBEDDING.value)

    def test_mock_embedding_route_is_separate_from_chat_route(self):
        manager = ModelManager(model_name="mock", models_config=_mock_models_config())

        chat_result = manager.generate("hello")
        embed_result = manager.embed_text("hello")
        embed_batch = manager.embed_texts(["hello", "world"])

        self.assertTrue(chat_result.success)
        self.assertEqual(chat_result.model, "mock-chat-v1")
        self.assertTrue(embed_result.success)
        self.assertEqual(embed_result.model, "mock-embedding-v1")
        self.assertEqual(embed_result.metadata["route"], "embedding")
        self.assertEqual(embed_batch.metadata["route"], "embedding")
        self.assertEqual(embed_batch.item_results[0].model, "mock-embedding-v1")

    def test_real_provider_embedding_requires_embedding_route_or_explicit_model(self):
        manager = ModelManager(model_name="openai")

        result = manager.embed_texts(["hello"])

        self.assertFalse(result.success)
        self.assertEqual(result.code, ModelErrorCode.MISSING_MODEL_CONFIG.value)
        self.assertEqual(result.metadata["call_type"], ModelCallType.EMBEDDING.value)
        self.assertEqual(result.metadata["route"], "embedding")

    def test_real_provider_embedding_failure_is_preserved(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False):
            manager = ModelManager(model_name="openai")
            result = manager.embed_texts(["hello"], model="text-embedding-3-small")

        self.assertFalse(result.success)
        self.assertEqual(result.code, ModelErrorCode.MISSING_API_KEY.value)
        self.assertEqual(result.error, "credential 'default' has no available secret")

    def test_provider_without_embedding_support_is_rejected_before_call(self):
        manager = ModelManager(
            model_name="mock",
            models_config=_unsupported_embedding_models_config(),
        )

        result = manager.embed_texts(["hello"])

        self.assertFalse(result.success)
        self.assertEqual(result.code, ModelErrorCode.UNSUPPORTED_PROVIDER.value)
        self.assertEqual(result.metadata["route"], "embedding")

    def test_openai_compatible_provider_uses_embeddings_endpoint(self):
        embeddings = FakeEmbeddings(
            response=SimpleNamespace(
                id="embed_req_1",
                data=[
                    SimpleNamespace(embedding=[0.1, 0.2, 0.3]),
                    SimpleNamespace(embedding=[0.4, 0.5, 0.6]),
                ],
                usage=SimpleNamespace(prompt_tokens=2, total_tokens=2),
            )
        )
        conf = _openai_provider_conf()
        provider = OpenAICompatibleProvider(
            conf,
            resolve_credential_secret(conf.credentials[0], environ={"OPENAI_API_KEY": "sk-test"}),
            client=FakeClient(embeddings),
        )
        options = ModelCallOptions(
            call_type=ModelCallType.EMBEDDING,
            provider_conf_id=conf.id,
            credential_slug="primary",
        )

        result = provider.embed_texts(["alpha", "beta"], options)

        self.assertTrue(result.success)
        self.assertEqual(result.embeddings, [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]])
        self.assertEqual(result.item_results[0].dimensions, 3)
        self.assertEqual(result.metadata["count"], 2)
        self.assertEqual(result.metadata["usage"]["total_tokens"], 2)
        self.assertEqual(embeddings.calls[0]["model"], "text-embedding-3-small")
        self.assertEqual(embeddings.calls[0]["input"], ["alpha", "beta"])


if __name__ == "__main__":
    unittest.main()
