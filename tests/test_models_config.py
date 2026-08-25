from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.models.config import (
    ModelsConfigError,
    ProviderCredential,
    load_models_config,
)
from src.models.credentials import resolve_credential_secret


class ModelsConfigTest(unittest.TestCase):
    def tearDown(self) -> None:
        load_models_config.cache_clear()

    def _write_config_file(self, root: Path, name: str, payload: object) -> None:
        config_dir = root / "config" / "models"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / name).write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )

    def test_missing_config_dir_uses_stable_defaults(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config = load_models_config(root)

            self.assertEqual(config.config_dir, (root / "config" / "models").resolve())
            self.assertIn("mock", config.provider_specs)
            self.assertIn("openai", config.provider_specs)
            self.assertIn("qianwen", config.provider_specs)
            self.assertIn("doubao", config.provider_specs)
            self.assertIn("custom_openai_compatible", config.provider_specs)
            self.assertEqual(config.provider_confs, {})
            self.assertEqual(config.runtime.default_chat_route, "chat")
            self.assertEqual(config.runtime.logs_path, (root / "logs" / "models.log").resolve())
            self.assertEqual(
                config.get_route("react_action_decision").params["temperature"],
                0.1,
            )
            self.assertEqual(
                config.get_route("embedding").default_model_policy,
                "explicit_candidates",
            )
            json.dumps(config.to_dict(), ensure_ascii=False)

    def test_loads_user_provider_routes_and_runtime_env_overrides(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            self._write_config_file(
                root,
                "models_config.json",
                {"max_retries": 9, "logs_path": "tmp/models.log"},
            )
            self._write_config_file(
                root,
                "provider_confs.json",
                {
                    "conf_custom": {
                        "name": "Custom Endpoint",
                        "provider": "custom_openai_compatible",
                        "protocol": "openai-compatible",
                        "enabled": True,
                        "base_url": "https://example.invalid/v1",
                        "model_id": "custom-chat-model",
                        "credentials": [
                            {
                                "slug": "primary",
                                "api_key_env": "CUSTOM_MODEL_API_KEY",
                                "status": "active",
                            }
                        ],
                        "headers": {"X-Test": "models-config"},
                        "verify": {"prompt": "hi", "expected_non_empty": True},
                        "tags": ["custom"],
                    },
                    "conf_disabled": {
                        "name": "Disabled OpenAI",
                        "provider": "openai",
                        "protocol": "openai-compatible",
                        "enabled": False,
                        "base_url": "https://api.openai.com/v1",
                        "default_model": "gpt-4o-mini",
                        "credentials": [{"slug": "default", "api_key_env": "OPENAI_API_KEY"}],
                    },
                },
            )
            self._write_config_file(
                root,
                "routes.json",
                {
                    "react_call_model": {
                        "default_model_policy": "explicit_candidates",
                        "params": {"temperature": 0.4, "max_tokens": 512},
                        "candidates": [
                            {
                                "provider_conf_id": "conf_custom",
                                "credential_slug": "primary",
                                "model_id": "custom-chat-model",
                            }
                        ],
                    },
                    "summary": {
                        "default_model_policy": "explicit_candidates",
                        "params": {},
                        "candidates": [
                            {
                                "provider_conf_id": "conf_disabled",
                                "credential_slug": "default",
                                "model": "gpt-4o-mini",
                            }
                        ],
                    },
                },
            )

            with patch.dict(
                "os.environ",
                {
                    "MODELS_MAX_RETRIES": "2",
                    "MODELS_LOG_FULL_PROMPT": "true",
                    "CUSTOM_MODEL_API_KEY": "sk-test-1234567890",
                },
                clear=False,
            ):
                config = load_models_config(root)

            custom = config.get_provider_conf("CONF_CUSTOM")
            self.assertIsNotNone(custom)
            self.assertEqual(custom.default_model, "custom-chat-model")
            self.assertEqual(custom.model_id, "custom-chat-model")
            self.assertEqual(custom.verify["prompt"], "hi")
            self.assertEqual(config.runtime.max_retries, 2)
            self.assertTrue(config.runtime.log_full_prompt)
            self.assertEqual(config.route_candidates("react_call_model")[0].model, "custom-chat-model")
            self.assertEqual(config.route_candidates("summary"), [])

            resolution = resolve_credential_secret(
                custom.credentials[0],
                environ={"CUSTOM_MODEL_API_KEY": "sk-test-1234567890"},
            )
            self.assertTrue(resolution.success)
            self.assertNotIn(
                "sk-test-1234567890",
                json.dumps(resolution.to_dict(), ensure_ascii=False),
            )

    def test_duplicate_provider_conf_ids_are_structured_errors(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            self._write_config_file(
                root,
                "provider_confs.json",
                [
                    {
                        "id": "conf_duplicate",
                        "name": "A",
                        "provider": "openai",
                        "protocol": "openai-compatible",
                    },
                    {
                        "id": "conf_duplicate",
                        "name": "B",
                        "provider": "qianwen",
                        "protocol": "openai-compatible",
                    },
                ],
            )

            with self.assertRaises(ModelsConfigError) as caught:
                load_models_config(root)
            self.assertEqual(caught.exception.code, "duplicate_provider_conf_id")
            json.dumps(caught.exception.to_dict(), ensure_ascii=False)

    def test_duplicate_credential_slugs_are_structured_errors(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            self._write_config_file(
                root,
                "provider_confs.json",
                {
                    "conf_openai": {
                        "name": "OpenAI",
                        "provider": "openai",
                        "protocol": "openai-compatible",
                        "credentials": [
                            {"slug": "default", "api_key_env": "OPENAI_API_KEY"},
                            {"slug": "default", "api_key_env": "OPENAI_API_KEY_BACKUP"},
                        ],
                    }
                },
            )

            with self.assertRaises(ModelsConfigError) as caught:
                load_models_config(root)
            self.assertEqual(caught.exception.code, "duplicate_credential_slug")

    def test_unsupported_provider_and_protocol_are_structured_errors(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            self._write_config_file(
                root,
                "provider_confs.json",
                {
                    "conf_unknown": {
                        "name": "Unknown",
                        "provider": "not_supported",
                        "protocol": "openai-compatible",
                    }
                },
            )

            with self.assertRaises(ModelsConfigError) as caught:
                load_models_config(root)
            self.assertEqual(caught.exception.code, "unsupported_provider")

        load_models_config.cache_clear()

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            self._write_config_file(
                root,
                "provider_confs.json",
                {
                    "conf_bad_protocol": {
                        "name": "Bad Protocol",
                        "provider": "openai",
                        "protocol": "not-a-protocol",
                    }
                },
            )

            with self.assertRaises(ModelsConfigError) as caught:
                load_models_config(root)
            self.assertEqual(caught.exception.code, "unsupported_protocol")

    def test_plain_api_key_is_rejected_but_api_key_env_is_allowed(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            self._write_config_file(
                root,
                "provider_confs.json",
                {
                    "conf_openai": {
                        "name": "OpenAI",
                        "provider": "openai",
                        "protocol": "openai-compatible",
                        "credentials": [
                            {
                                "slug": "default",
                                "api_key_env": "OPENAI_API_KEY",
                                "api_key": "must-not-be-here",
                            }
                        ],
                    }
                },
            )

            with self.assertRaises(ModelsConfigError) as caught:
                load_models_config(root)
            self.assertEqual(caught.exception.code, "plain_secret_in_config")
            self.assertIn("api_key", caught.exception.to_dict()["details"]["keys"][0])

    def test_missing_credential_ref_returns_structured_resolution_failure(self):
        credential = ProviderCredential(slug="default", credential_ref="local://missing")

        resolution = resolve_credential_secret(credential, environ={})

        self.assertFalse(resolution.success)
        self.assertEqual(resolution.code, "missing_api_key")
        self.assertIn("credential_ref", resolution.missing_config)
        json.dumps(resolution.to_dict(), ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
