from __future__ import annotations

import os
import time
import unittest

from src.models import (
    ModelCallResult,
    ModelErrorCode,
    ModelErrorInfo,
    ModelManager,
    ModelsConfig,
    ModelsRuntimeConfig,
    RetryPolicy,
    default_provider_specs,
    default_route_configs,
)
from src.models.errors import classify_model_error_code, is_retryable_model_error_code


def make_config() -> ModelsConfig:
    return ModelsConfig(
        workspace_root=os.getcwd(),
        config_dir=os.getcwd(),
        runtime=ModelsRuntimeConfig(
            retry_backoff_base_seconds=0.0,
            retry_backoff_max_seconds=0.0,
        ),
        provider_specs=default_provider_specs(),
        provider_confs={},
        routes=default_route_configs(),
    )


class FlakyStructuredModel:
    def __init__(self, first_code: ModelErrorCode | str, *, final_content: str = "ok"):
        self.first_code = first_code
        self.final_content = final_content
        self.calls = 0

    def generate(self, prompt: str, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return ModelCallResult.fail(self.first_code, "temporary failure")
        return ModelCallResult.ok(self.final_content)

    def stream_generate(self, prompt: str, **kwargs):
        yield self.final_content


class SlowThenFastModel:
    def __init__(self):
        self.calls = 0

    def generate(self, prompt: str, **kwargs):
        self.calls += 1
        if self.calls == 1:
            time.sleep(0.05)
            return ModelCallResult.ok("late")
        return ModelCallResult.ok("fast")

    def stream_generate(self, prompt: str, **kwargs):
        yield "fast"


class StatusError(Exception):
    def __init__(self, status_code: int, message: str = "provider error"):
        self.status_code = status_code
        super().__init__(message)


class ModelsRetryErrorsTest(unittest.TestCase):
    def test_error_classification_priority_is_stable(self):
        self.assertEqual(
            classify_model_error_code(
                ModelErrorCode.INVALID_PROMPT,
                http_status=429,
                provider_error_code="bad_request",
            ),
            ModelErrorCode.RATE_LIMITED.value,
        )
        self.assertEqual(
            classify_model_error_code(
                ModelErrorCode.MODEL_CALL_FAILED,
                provider_error_code="service_unavailable",
            ),
            ModelErrorCode.TEMPORARY_UNAVAILABLE.value,
        )
        self.assertEqual(
            classify_model_error_code(
                ModelErrorCode.MODEL_CALL_FAILED,
                provider_error_message="The service is temporarily unavailable.",
            ),
            ModelErrorCode.TEMPORARY_UNAVAILABLE.value,
        )
        self.assertEqual(
            classify_model_error_code(ModelErrorCode.INVALID_PROMPT),
            ModelErrorCode.INVALID_PROMPT.value,
        )

    def test_model_error_info_infers_category_and_retriable(self):
        error = ModelErrorInfo.from_provider_error(
            message="Too many requests.",
            http_status=429,
            provider_error_code="ignored_by_http_status",
        )

        self.assertEqual(error.code, ModelErrorCode.RATE_LIMITED.value)
        self.assertEqual(error.category, "transient")
        self.assertTrue(error.retriable)
        self.assertTrue(is_retryable_model_error_code(error.code))

    def test_retry_policy_caps_retries_and_uses_exponential_delay(self):
        policy = RetryPolicy(max_retries=20, base_delay_seconds=0.5, max_delay_seconds=2.0)

        self.assertEqual(policy.max_retries, 5)
        self.assertEqual(policy.max_attempts, 6)
        self.assertEqual(policy.delay_seconds(1), 0.5)
        self.assertEqual(policy.delay_seconds(3), 2.0)
        self.assertEqual(policy.delay_seconds(5), 2.0)

    def test_rate_limited_result_retries_and_records_attempts(self):
        manager = ModelManager(model_name="mock", models_config=make_config())
        model = FlakyStructuredModel(ModelErrorCode.RATE_LIMITED)
        manager.model = model

        result = manager.generate("hello", max_retries=1)

        self.assertTrue(result.success)
        self.assertEqual(result.content, "ok")
        self.assertEqual(model.calls, 2)
        self.assertEqual(result.attempts, 2)
        self.assertTrue(result.metadata["retry_used"])
        self.assertEqual(result.metadata["retry_history"][0]["code"], "rate_limited")

    def test_timeout_retries_and_uses_fast_second_attempt(self):
        manager = ModelManager(model_name="mock", models_config=make_config())
        model = SlowThenFastModel()
        manager.model = model

        result = manager.generate("hello", timeout_seconds=0.01, max_retries=1)

        self.assertTrue(result.success)
        self.assertEqual(result.content, "fast")
        self.assertGreaterEqual(model.calls, 2)
        self.assertEqual(result.attempts, 2)
        self.assertEqual(result.metadata["retry_history"][0]["code"], "timeout")

    def test_missing_api_key_does_not_retry(self):
        manager = ModelManager(model_name="mock", models_config=make_config())
        model = FlakyStructuredModel(ModelErrorCode.MISSING_API_KEY)
        manager.model = model

        result = manager.generate("hello", max_retries=3)

        self.assertFalse(result.success)
        self.assertEqual(result.code, ModelErrorCode.MISSING_API_KEY.value)
        self.assertEqual(model.calls, 1)
        self.assertEqual(result.attempts, 1)
        self.assertFalse(result.metadata["retry_used"])

    def test_http_status_exception_is_classified_and_retried(self):
        class RaiseOnceModel:
            def __init__(self):
                self.calls = 0

            def generate(self, prompt: str, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise StatusError(500, "server error")
                return "ok"

            def stream_generate(self, prompt: str, **kwargs):
                yield "ok"

        manager = ModelManager(model_name="mock", models_config=make_config())
        model = RaiseOnceModel()
        manager.model = model

        result = manager.generate("hello", max_retries=1)

        self.assertTrue(result.success)
        self.assertEqual(result.content, "ok")
        self.assertEqual(model.calls, 2)
        self.assertEqual(result.metadata["retry_history"][0]["code"], "provider_server_error")


if __name__ == "__main__":
    unittest.main()
