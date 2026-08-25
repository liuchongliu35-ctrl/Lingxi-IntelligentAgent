from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from src.models import (
    ModelManager,
    ModelsConfig,
    ModelsRuntimeConfig,
    ProviderConf,
    ProviderCredential,
    default_provider_specs,
    default_route_configs,
)


class FakeCompletions:
    def __init__(self):
        self.calls = []

    def create(self, **payload):
        self.calls.append(payload)
        return SimpleNamespace(
            id="verify-request",
            choices=[SimpleNamespace(message=SimpleNamespace(content="pong"))],
        )


def fake_client_factory(completions: FakeCompletions):
    return lambda **kwargs: SimpleNamespace(chat=SimpleNamespace(completions=completions))


def make_config(root: Path) -> ModelsConfig:
    enabled = ProviderConf(
        id="conf_enabled",
        name="Enabled Custom",
        provider="custom_openai_compatible",
        protocol="openai-compatible",
        enabled=True,
        base_url="https://example.invalid/v1",
        default_model="enabled-model",
        custom_models=["enabled-model-alt"],
        credentials=[
            ProviderCredential(
                slug="primary",
                api_key_env="MANAGEMENT_API_KEY",
                status="active",
            )
        ],
        status="unverified",
        tags=["custom", "team-a"],
        metadata={
            "alias": "team-model",
            "description": "Management fixture",
            "labels": ["fast", "test"],
            "group": "internal",
            "sort_order": 3,
            "created_by": "test",
            "authorization": "Bearer should-not-leak",
        },
    )
    disabled = ProviderConf(
        id="conf_disabled",
        name="Disabled OpenAI",
        provider="openai",
        protocol="openai-compatible",
        enabled=False,
        base_url="https://api.openai.com/v1",
        default_model="disabled-model",
        credentials=[ProviderCredential(slug="default", api_key_env="DISABLED_API_KEY")],
        status="active",
        tags=["builtin"],
    )
    return ModelsConfig(
        workspace_root=root,
        config_dir=root,
        runtime=ModelsRuntimeConfig(logs_path=root / "logs" / "models.log"),
        provider_specs=default_provider_specs(),
        provider_confs={enabled.id: enabled, disabled.id: disabled},
        routes=default_route_configs(),
    )


class ModelsManagementApiTest(unittest.TestCase):
    def test_lists_enabled_configs_with_safe_management_metadata(self):
        with TemporaryDirectory() as temp_dir:
            manager = ModelManager(model_name="mock", models_config=make_config(Path(temp_dir)))

            models = manager.list_enabled_models()
            all_configs = manager.list_provider_configs(include_disabled=True)
            metadata = models[0]

            self.assertEqual([item["id"] for item in models], ["conf_enabled"])
            self.assertEqual([item["id"] for item in all_configs], ["conf_disabled", "conf_enabled"])
            self.assertEqual(metadata["display_name"], "Custom OpenAI-Compatible")
            self.assertEqual(metadata["alias"], "team-model")
            self.assertEqual(metadata["custom_models"], ["enabled-model-alt"])
            self.assertTrue(metadata["supports_streaming"])
            self.assertTrue(metadata["supports_json_mode"])
            self.assertEqual(metadata["credential_count"], 1)
            self.assertEqual(metadata["active_credential_slug"], "primary")
            self.assertEqual(metadata["metadata"]["authorization"], "***")
            serialized = str(all_configs)
            self.assertNotIn("should-not-leak", serialized)
            self.assertNotIn("MANAGEMENT_API_KEY=", serialized)
            self.assertNotIn("api_key", metadata)

    def test_enable_disable_is_in_memory_and_changes_enabled_listing(self):
        with TemporaryDirectory() as temp_dir:
            config = make_config(Path(temp_dir))
            manager = ModelManager(model_name="mock", models_config=config)

            disabled = manager.disable_provider_config("conf_enabled")
            self.assertFalse(disabled["enabled"])
            self.assertEqual(disabled["status"], "unverified")
            self.assertEqual(manager.list_enabled_models(), [])

            enabled = manager.enable_provider_config("conf_disabled")
            self.assertTrue(enabled["enabled"])
            self.assertEqual(enabled["status"], "active")
            self.assertEqual([item["id"] for item in manager.list_enabled_models()], ["conf_disabled"])
            self.assertTrue(config.get_provider_conf("conf_disabled").enabled)

    def test_get_provider_config_and_default_routes_are_queryable(self):
        with TemporaryDirectory() as temp_dir:
            manager = ModelManager(model_name="mock", models_config=make_config(Path(temp_dir)))

            provider = manager.get_provider_config("CONF_ENABLED")
            routes = manager.get_default_routes()

            self.assertEqual(provider["id"], "conf_enabled")
            self.assertIsNone(manager.get_provider_config("missing"))
            self.assertEqual(routes["defaults"]["chat"], "chat")
            self.assertIn("react_action_decision", routes["routes"])
            self.assertEqual(
                routes["routes"]["react_action_decision"]["params"]["temperature"],
                0.1,
            )

    def test_verify_result_is_reflected_in_management_metadata(self):
        with TemporaryDirectory() as temp_dir:
            completions = FakeCompletions()
            config = make_config(Path(temp_dir))
            with patch.dict(os.environ, {"MANAGEMENT_API_KEY": "sk-management-test"}, clear=False):
                manager = ModelManager(
                    models_config=config,
                    provider_conf_id="conf_enabled",
                    client_factory=fake_client_factory(completions),
                )
                status = manager.verify_provider_config("conf_enabled")

            provider = manager.get_provider_config("conf_enabled")
            self.assertTrue(status.healthy)
            self.assertEqual(provider["status"], "active")
            self.assertTrue(provider["is_verified"])
            self.assertIsNotNone(provider["verified_at"])
            self.assertEqual(completions.calls[0]["model"], "enabled-model")


if __name__ == "__main__":
    unittest.main()
