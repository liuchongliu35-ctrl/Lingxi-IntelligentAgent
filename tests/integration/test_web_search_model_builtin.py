from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from src.models import (
    ModelManager,
    ModelsConfig,
    ModelsRuntimeConfig,
    ProviderConf,
    ProviderCredential,
    default_provider_specs,
    default_route_configs,
)
from src.tools import (
    ModelBuiltinSearchProvider,
    WebSearchContext,
    WebSearchRequest,
)


TRUTHY = {"1", "true", "yes", "on"}


def _enabled(name: str) -> bool:
    return str(os.getenv(name) or "").strip().lower() in TRUTHY


def _api_key_env() -> str:
    return str(
        os.getenv("MODEL_INTEGRATION_API_KEY_ENV") or "OPENAI_API_KEY"
    ).strip()


def _live_models_config(root: Path) -> ModelsConfig:
    provider_conf = ProviderConf(
        id="conf_live_web_search",
        name="Live web search model",
        provider=os.getenv("MODEL_INTEGRATION_PROVIDER", "openai"),
        protocol=os.getenv("MODEL_INTEGRATION_PROTOCOL", "openai-compatible"),
        enabled=True,
        base_url=os.getenv(
            "MODEL_INTEGRATION_BASE_URL",
            "https://api.openai.com/v1",
        ),
        default_model=os.getenv(
            "MODEL_INTEGRATION_MODEL",
            "gpt-4o-mini",
        ),
        credentials=[
            ProviderCredential(
                slug="primary",
                api_key_env=_api_key_env(),
            )
        ],
        timeout_seconds=float(os.getenv("MODEL_INTEGRATION_TIMEOUT_SECONDS", "60")),
        max_retries=0,
        metadata={
            "web_search": {
                "enabled": True,
                **_web_search_capability_mapping(),
            }
        },
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
        provider_confs={provider_conf.id: provider_conf},
        routes=default_route_configs(),
    )


def _web_search_capability_mapping() -> dict:
    raw = os.getenv("MODEL_INTEGRATION_WEB_SEARCH_MAPPING")
    if not raw:
        return {}
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("MODEL_INTEGRATION_WEB_SEARCH_MAPPING must be a JSON object")
    return value


class ModelBuiltinWebSearchIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not _enabled("RUN_TOOL_INTEGRATION_TESTS"):
            raise unittest.SkipTest(
                "set RUN_TOOL_INTEGRATION_TESTS=true to enable live tool calls"
            )
        if not _enabled("RUN_MODEL_BUILTIN_SEARCH_TESTS"):
            raise unittest.SkipTest(
                "set RUN_MODEL_BUILTIN_SEARCH_TESTS=true to enable live model search"
            )
        if not os.getenv(_api_key_env()):
            raise unittest.SkipTest(f"{_api_key_env()} is not configured")
        if importlib.util.find_spec("openai") is None:
            raise unittest.SkipTest("openai package is not installed")

    def test_live_model_builtin_returns_unified_web_search_data(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            models_config = _live_models_config(root)
            manager = ModelManager(
                models_config=models_config,
                provider_conf_id="conf_live_web_search",
            )
            provider = ModelBuiltinSearchProvider(
                {
                    "enabled": True,
                    "provider_conf_id": "conf_live_web_search",
                    "model_route": "web_search",
                    "enable_web_search": True,
                    "timeout_seconds": int(
                        os.getenv("MODEL_INTEGRATION_TIMEOUT_SECONDS", "60")
                    ),
                },
                model_manager=manager,
            )
            result = provider.search(
                WebSearchRequest(
                    query=os.getenv(
                        "WEB_SEARCH_INTEGRATION_QUERY",
                        "latest developments in agent tool runtimes",
                    ),
                    provider="model_builtin",
                    max_results=3,
                    include_answer=True,
                ),
                WebSearchContext(
                    allow_network=True,
                    timeout_seconds=int(
                        os.getenv("MODEL_INTEGRATION_TIMEOUT_SECONDS", "60")
                    ),
                ),
            )

        self.assertTrue(result.success, result.to_dict())
        self.assertEqual(result.data.provider_type, "model_builtin")
        self.assertLessEqual(result.data.result_count, 3)
        if result.data.results:
            self.assertEqual(result.data.evidence_level, "model_reported")
        else:
            self.assertEqual(result.data.evidence_level, "no_url_summary")
            self.assertEqual(result.data.source_quality, "summary_only")


if __name__ == "__main__":
    unittest.main()
