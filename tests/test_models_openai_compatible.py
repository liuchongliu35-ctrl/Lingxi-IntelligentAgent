from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.models.config import ProviderConf, ProviderCredential
from src.models.credentials import resolve_credential_secret
from src.models.errors import ModelErrorCode
from src.models.model_manager import ModelManager
from src.models.protocol import ModelCallOptions, ModelCallType
from src.models.providers.openai_compatible import (
    OpenAICompatibleModel,
    OpenAICompatibleProvider,
)


class FakeHTTPError(Exception):
    def __init__(self, status_code: int, message: str = "provider error"):
        self.status_code = status_code
        super().__init__(message)


class FakeCompletions:
    def __init__(self, response=None, error=None, stream_response=None):
        self.response = response
        self.error = error
        self.stream_response = stream_response
        self.calls = []

    def create(self, **payload):
        self.calls.append(payload)
        if self.error is not None:
            raise self.error
        if payload.get("stream"):
            return self.stream_response or []
        return self.response


class FakeClient:
    def __init__(self, completions: FakeCompletions):
        self.chat = SimpleNamespace(completions=completions)


def provider_conf(**overrides):
    values = {
        "id": "conf_custom",
        "name": "Custom",
        "provider": "custom_openai_compatible",
        "protocol": "openai-compatible",
        "enabled": True,
        "base_url": "https://example.invalid/v1",
        "default_model": "custom-model",
        "credentials": [
            ProviderCredential(slug="primary", api_key_env="CUSTOM_MODEL_API_KEY")
        ],
        "headers": {"X-Test": "adapter"},
        "temperature": 0.4,
        "top_p": 0.8,
        "max_tokens": 512,
    }
    values.update(overrides)
    return ProviderConf(**values)


class OpenAICompatibleProviderTest(unittest.TestCase):
    def _credential(self, conf):
        return resolve_credential_secret(
            conf.credentials[0],
            environ={"CUSTOM_MODEL_API_KEY": "sk-test"},
        )

    def test_generate_builds_openai_compatible_request_and_parses_response(self):
        completions = FakeCompletions(
            response=SimpleNamespace(
                id="provider_req_1",
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="hello from provider"),
                        finish_reason="stop",
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=3, completion_tokens=4, total_tokens=7),
            )
        )
        factory_calls = []

        def factory(**kwargs):
            factory_calls.append(kwargs)
            return FakeClient(completions)

        conf = provider_conf()
        provider = OpenAICompatibleProvider(
            conf,
            self._credential(conf),
            client_factory=factory,
        )
        result = provider.generate(
            ModelCallOptions(
                call_type=ModelCallType.CHAT,
                prompt="hello",
                temperature=0.1,
                top_p=0.9,
                max_tokens=120,
                json_mode=True,
            )
        )

        self.assertTrue(result.success)
        self.assertEqual(result.content, "hello from provider")
        self.assertEqual(result.provider_request_id, "provider_req_1")
        self.assertEqual(result.usage.total_tokens, 7)
        self.assertEqual(factory_calls[0]["base_url"], "https://example.invalid/v1")
        self.assertEqual(factory_calls[0]["default_headers"], {"X-Test": "adapter"})
        self.assertEqual(factory_calls[0]["api_key"], "sk-test")
        payload = completions.calls[0]
        self.assertEqual(payload["model"], "custom-model")
        self.assertEqual(payload["messages"][0]["role"], "user")
        self.assertEqual(payload["temperature"], 0.1)
        self.assertEqual(payload["top_p"], 0.9)
        self.assertEqual(payload["max_tokens"], 120)
        self.assertEqual(payload["response_format"], {"type": "json_object"})

    def test_custom_base_url_and_model_id_are_used(self):
        completions = FakeCompletions(
            response={"id": "req", "choices": [{"message": {"content": "ok"}}]}
        )
        conf = provider_conf(base_url="https://custom.invalid/api/v1", model_id="model-from-id", default_model=None)
        provider = OpenAICompatibleProvider(
            conf,
            self._credential(conf),
            client=FakeClient(completions),
        )

        result = provider.generate(ModelCallOptions(prompt="hello"))

        self.assertTrue(result.success)
        self.assertEqual(completions.calls[0]["model"], "model-from-id")
        self.assertEqual(result.model, "model-from-id")

    def test_http_statuses_are_normalized(self):
        expected = {
            401: ModelErrorCode.AUTHENTICATION_FAILED.value,
            403: ModelErrorCode.PERMISSION_DENIED.value,
            404: ModelErrorCode.MODEL_NOT_FOUND.value,
            429: ModelErrorCode.RATE_LIMITED.value,
            500: ModelErrorCode.PROVIDER_SERVER_ERROR.value,
        }
        for status_code, expected_code in expected.items():
            with self.subTest(status_code=status_code):
                conf = provider_conf()
                provider = OpenAICompatibleProvider(
                    conf,
                    self._credential(conf),
                    client=FakeClient(FakeCompletions(error=FakeHTTPError(status_code))),
                )

                result = provider.generate(ModelCallOptions(prompt="hello"))

                self.assertFalse(result.success)
                self.assertEqual(result.code, expected_code)
                self.assertEqual(result.error_info.http_status, status_code)

    def test_missing_api_key_returns_structured_failure_without_client_call(self):
        conf = provider_conf()
        credential = resolve_credential_secret(conf.credentials[0], environ={})
        factory_calls = []
        provider = OpenAICompatibleProvider(
            conf,
            credential,
            client_factory=lambda **kwargs: factory_calls.append(kwargs),
        )

        result = provider.generate(ModelCallOptions(prompt="hello"))

        self.assertFalse(result.success)
        self.assertEqual(result.code, ModelErrorCode.MISSING_API_KEY.value)
        self.assertEqual(factory_calls, [])

    def test_stream_generate_parses_delta_content(self):
        stream = [
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="hel"))]),
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="lo"))]),
        ]
        completions = FakeCompletions(stream_response=stream)
        conf = provider_conf()
        provider = OpenAICompatibleProvider(
            conf,
            self._credential(conf),
            client=FakeClient(completions),
        )

        chunks = list(provider.stream_generate(ModelCallOptions(prompt="hello")))

        self.assertEqual([chunk.content_delta for chunk in chunks], ["hel", "lo"])
        self.assertTrue(all(chunk.success for chunk in chunks))
        self.assertTrue(completions.calls[0]["stream"])

    def test_model_manager_creates_configured_builtin_adapter(self):
        with patch.dict(os.environ, {"QIANWEN_API_KEY": ""}, clear=False):
            manager = ModelManager(model_name="qianwen")

        self.assertIsInstance(manager.model, OpenAICompatibleModel)
        result = manager.generate("hello")

        self.assertFalse(result.success)
        self.assertEqual(result.code, ModelErrorCode.MISSING_API_KEY.value)
        self.assertEqual(manager.get_model_info()["provider_conf_id"], "conf_qianwen_default")


if __name__ == "__main__":
    unittest.main()
