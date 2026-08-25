from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.models.config import ModelsConfig, ProviderConf, ProviderCredential, default_provider_specs, default_route_configs, ModelsRuntimeConfig
from src.models.errors import ModelErrorCode
from src.models.model_manager import ModelManager
from src.models.providers.openai_compatible import OpenAICompatibleProvider


class FakeHTTPError(Exception):
    def __init__(self, status_code: int):
        self.status_code = status_code
        super().__init__("provider error")


class FakeCompletions:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def create(self, **payload):
        self.calls.append(payload)
        if self.error:
            raise self.error
        return self.response


def fake_client_factory(completions):
    return lambda **kwargs: SimpleNamespace(
        chat=SimpleNamespace(completions=completions)
    )


def make_config(conf: ProviderConf) -> ModelsConfig:
    return ModelsConfig(
        workspace_root=os.getcwd(),
        config_dir=os.getcwd(),
        runtime=ModelsRuntimeConfig(),
        provider_specs=default_provider_specs(),
        provider_confs={conf.id: conf},
        routes=default_route_configs(),
    )


def make_conf(**overrides) -> ProviderConf:
    values = {
        "id": "conf_custom_verify",
        "name": "Custom Verify",
        "provider": "custom_openai_compatible",
        "protocol": "openai-compatible",
        "enabled": True,
        "base_url": "https://example.invalid/v1",
        "default_model": "verify-model",
        "credentials": [ProviderCredential(slug="primary", api_key_env="VERIFY_API_KEY")],
    }
    values.update(overrides)
    return ProviderConf(**values)


class ModelsHealthVerifyTest(unittest.TestCase):
    def test_health_check_is_config_only_and_reports_missing_key(self):
        completions = FakeCompletions()
        with patch.dict(os.environ, {"VERIFY_API_KEY": ""}, clear=False):
            manager = ModelManager(
                models_config=make_config(make_conf()),
                provider_conf_id="conf_custom_verify",
                client_factory=fake_client_factory(completions),
            )
            status = manager.health_check()

        self.assertFalse(status.healthy)
        self.assertEqual(status.check_type, "config_check")
        self.assertEqual(status.code, ModelErrorCode.MISSING_API_KEY.value)
        self.assertEqual(completions.calls, [])

    def test_health_check_reports_missing_base_url_and_model(self):
        conf = make_conf(base_url=None, default_model=None, model_id=None)
        with patch.dict(os.environ, {"VERIFY_API_KEY": "sk-test"}, clear=False):
            status = ModelManager(
                models_config=make_config(conf),
                provider_conf_id=conf.id,
            ).health_check()

        self.assertFalse(status.healthy)
        self.assertIn("base_url", status.missing_config)
        self.assertIn("model", status.missing_config)

    def test_verify_success_updates_provider_status(self):
        completions = FakeCompletions(
            response=SimpleNamespace(
                id="verify-request",
                choices=[SimpleNamespace(message=SimpleNamespace(content="pong"))],
            )
        )
        conf = make_conf()
        with patch.dict(os.environ, {"VERIFY_API_KEY": "sk-test"}, clear=False):
            manager = ModelManager(
                models_config=make_config(conf),
                provider_conf_id=conf.id,
                client_factory=fake_client_factory(completions),
            )
            status = manager.verify_provider_config(conf.id)

        self.assertTrue(status.healthy)
        self.assertEqual(status.check_type, "live_check")
        self.assertIsNotNone(status.verified_at)
        self.assertEqual(conf.status, "active")
        self.assertEqual(completions.calls[0]["model"], "verify-model")
        self.assertEqual(completions.calls[0]["messages"][0]["content"], "ping")

    def test_verify_failure_records_structured_error_without_active_status(self):
        completions = FakeCompletions(error=FakeHTTPError(401))
        conf = make_conf()
        with patch.dict(os.environ, {"VERIFY_API_KEY": "sk-test"}, clear=False):
            manager = ModelManager(
                models_config=make_config(conf),
                provider_conf_id=conf.id,
                client_factory=fake_client_factory(completions),
            )
            status = manager.verify_provider_config(conf.id)

        self.assertFalse(status.healthy)
        self.assertEqual(status.code, ModelErrorCode.AUTHENTICATION_FAILED.value)
        self.assertEqual(conf.status, "error")
        self.assertEqual(conf.metadata["last_verify_code"], "authentication_failed")

    def test_verify_unknown_provider_config_returns_structured_failure(self):
        manager = ModelManager(models_config=make_config(make_conf()))
        status = manager.verify_provider_config("missing-config")

        self.assertFalse(status.healthy)
        self.assertEqual(status.code, ModelErrorCode.MISSING_MODEL_CONFIG.value)
        self.assertEqual(status.check_type, "live_check")


if __name__ == "__main__":
    unittest.main()
