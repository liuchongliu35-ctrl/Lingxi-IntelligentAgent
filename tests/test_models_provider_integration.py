from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from src.models import (
    ModelCallResult,
    ModelHealthStatus,
    ModelManager,
    ModelsConfig,
    ModelsRuntimeConfig,
    ProviderConf,
    ProviderCredential,
    default_provider_specs,
    default_route_configs,
)


TRUTHY = {"1", "true", "yes", "on"}


def _integration_enabled() -> bool:
    return str(os.getenv("RUN_MODEL_INTEGRATION_TESTS") or "").strip().lower() in TRUTHY


def _api_key_env_name() -> str:
    return str(os.getenv("MODEL_INTEGRATION_API_KEY_ENV") or "OPENAI_API_KEY").strip()


def _base_url() -> str:
    return str(os.getenv("MODEL_INTEGRATION_BASE_URL") or "https://api.openai.com/v1").strip()


def _chat_model() -> str:
    return str(os.getenv("MODEL_INTEGRATION_MODEL") or "gpt-4o-mini").strip()


def _embedding_model() -> str:
    return str(os.getenv("MODEL_INTEGRATION_EMBEDDING_MODEL") or "text-embedding-3-small").strip()


def _live_models_config(root: Path) -> ModelsConfig:
    conf = ProviderConf(
        id="conf_live_openai_compatible",
        name="Live OpenAI-Compatible",
        provider="openai",
        protocol="openai-compatible",
        enabled=True,
        base_url=_base_url(),
        default_model=_chat_model(),
        credentials=[
            ProviderCredential(
                slug="primary",
                api_key_env=_api_key_env_name(),
            )
        ],
        timeout_seconds=30,
        max_retries=0,
        tags=["integration", "openai-compatible"],
    )
    return ModelsConfig(
        workspace_root=root,
        config_dir=root,
        runtime=ModelsRuntimeConfig(
            logs_path=root / "logs" / "models.log",
            max_retries=0,
            retry_backoff_base_seconds=0.0,
            retry_backoff_max_seconds=0.0,
        ),
        provider_specs=default_provider_specs(),
        provider_confs={conf.id: conf},
        routes=default_route_configs(),
    )


class ModelsProviderIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not _integration_enabled():
            raise unittest.SkipTest(
                "set RUN_MODEL_INTEGRATION_TESTS=true to enable live model calls"
            )
        api_key_env = _api_key_env_name()
        if not os.getenv(api_key_env):
            raise unittest.SkipTest(f"{api_key_env} is not configured")
        if importlib.util.find_spec("openai") is None:
            raise unittest.SkipTest("openai package is not installed")

    def test_openai_compatible_live_generate_verify_and_embedding(self):
        with TemporaryDirectory() as temp_dir:
            config = _live_models_config(Path(temp_dir))
            manager = ModelManager(
                models_config=config,
                provider_conf_id="conf_live_openai_compatible",
            )

            health = manager.health_check()
            verify = manager.verify_provider_config("conf_live_openai_compatible")
            generated = manager.generate(
                "Reply with exactly one short word: pong",
                temperature=0,
                max_tokens=8,
                allow_retry=False,
            )
            embedding = manager.embed_text(
                "Models V1 live embedding smoke test.",
                model=_embedding_model(),
            )

        self.assertIsInstance(health, ModelHealthStatus)
        self.assertTrue(health.healthy, health.to_dict())
        self.assertTrue(verify.healthy, verify.to_dict())
        self.assertIsInstance(generated, ModelCallResult)
        self.assertTrue(generated.success, generated.to_dict())
        self.assertTrue(generated.content.strip())
        self.assertTrue(embedding.success, embedding.to_dict())
        self.assertGreater(embedding.dimensions or 0, 0)


if __name__ == "__main__":
    unittest.main()
