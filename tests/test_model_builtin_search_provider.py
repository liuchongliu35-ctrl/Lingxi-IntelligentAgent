from __future__ import annotations

import json
import unittest

from src.models import ModelCallResult, ModelUsage, StructuredModelResult
from src.tools import (
    ModelBuiltinSearchProvider,
    ToolErrorCode,
    WebSearchContext,
    WebSearchRequest,
    WebSearchRouter,
)


class FakeModelManager:
    def __init__(self, result: StructuredModelResult):
        self.result = result
        self.calls: list[dict] = []

    def generate_json(self, prompt: str, **kwargs):
        self.calls.append({"prompt": prompt, "kwargs": kwargs})
        return self.result


def structured_result(payload: dict, *, provider: str = "openai") -> StructuredModelResult:
    model_result = ModelCallResult.ok(
        json.dumps(payload, ensure_ascii=False),
        provider=provider,
        route="web_search",
        call_type="web_search",
        provider_request_id="provider_req_1",
        source_trace_id="trace_1",
        usage=ModelUsage(prompt_tokens=12, completion_tokens=34, total_tokens=46, source="provider"),
    )
    return StructuredModelResult(
        success=True,
        data=payload,
        content=model_result.content,
        parse_mode="strict",
        schema_name="web_search",
        schema_valid=True,
        model_result=model_result,
    )


class ModelBuiltinSearchProviderTest(unittest.TestCase):
    def _provider(self, result: StructuredModelResult) -> tuple[ModelBuiltinSearchProvider, FakeModelManager]:
        manager = FakeModelManager(result)
        provider = ModelBuiltinSearchProvider(
            {
                "enabled": True,
                "provider_conf_id": "conf_openai_default",
                "model_route": "web_search",
                "enable_web_search": True,
            },
            model_manager=manager,
        )
        return provider, manager

    def test_success_uses_models_web_search_call_type_and_normalizes_evidence(self):
        payload = {
            "query": "latest agent architecture",
            "summary": "A bounded model search summary.",
            "results": [
                {
                    "title": "Agent architecture",
                    "url": "https://example.test/agent",
                    "snippet": "A source snippet.",
                    "source": "example.test",
                    "published_at": "2026-08-16",
                }
            ],
            "evidence_level": "url_verified",
            "source_quality": "verified_sources",
        }
        provider, manager = self._provider(structured_result(payload))

        result = provider.search(
            WebSearchRequest(query="latest agent architecture", provider="model_builtin"),
            WebSearchContext(allow_network=True),
        )

        self.assertTrue(result.success)
        self.assertEqual(result.data.provider_type, "model_builtin")
        self.assertEqual(result.data.evidence_level, "model_reported")
        self.assertEqual(result.data.source_quality, "partial_sources")
        self.assertEqual(result.data.usage["total_tokens"], 46)
        self.assertEqual(manager.calls[0]["kwargs"]["call_type"], "web_search")
        self.assertTrue(manager.calls[0]["kwargs"]["metadata"]["enable_web_search"])
        self.assertNotIn("prompt", manager.calls[0]["kwargs"]["metadata"])
        self.assertTrue(result.data.metadata["model_request_id"].startswith("modelreq_"))

    def test_summary_without_urls_is_success_with_weak_evidence(self):
        payload = {
            "query": "offline summary",
            "summary": "The model returned a summary but no auditable links.",
            "results": [],
            "evidence_level": "no_url_summary",
            "source_quality": "summary_only",
        }
        provider, _ = self._provider(structured_result(payload))

        result = provider.search(
            WebSearchRequest(query="offline summary", provider="model_builtin"),
            WebSearchContext(allow_network=True),
        )

        self.assertTrue(result.success)
        self.assertEqual(result.data.evidence_level, "no_url_summary")
        self.assertEqual(result.data.source_quality, "summary_only")
        self.assertIn("without auditable URLs", result.message)

    def test_invalid_business_schema_is_not_wrapped_as_success(self):
        payload = {
            "query": "bad",
            "summary": "bad",
            "results": [{"title": "bad", "url": 123}],
            "evidence_level": "model_reported",
            "source_quality": "partial_sources",
        }
        provider, _ = self._provider(structured_result(payload))

        result = provider.search(
            WebSearchRequest(query="bad", provider="model_builtin"),
            WebSearchContext(allow_network=True),
        )

        self.assertFalse(result.success)
        self.assertEqual(result.code, ToolErrorCode.MODEL_SEARCH_SCHEMA_INVALID.value)

    def test_models_structured_failure_maps_to_search_error(self):
        model_result = ModelCallResult.fail("invalid_json", "bad JSON")
        structured = StructuredModelResult(
            success=False,
            code="invalid_json",
            error="bad JSON",
            parse_mode="strict",
            model_result=model_result,
        )
        provider, _ = self._provider(structured)

        result = provider.search(
            WebSearchRequest(query="bad", provider="model_builtin"),
            WebSearchContext(allow_network=True),
        )

        self.assertFalse(result.success)
        self.assertEqual(result.code, ToolErrorCode.MODEL_SEARCH_PARSE_FAILED.value)

    def test_router_can_route_to_injected_model_builtin_provider(self):
        payload = {
            "query": "route",
            "summary": "summary",
            "results": [],
            "evidence_level": "no_url_summary",
            "source_quality": "summary_only",
        }
        provider, manager = self._provider(structured_result(payload))
        router = WebSearchRouter(
            {
                "enabled": True,
                "provider": "model_builtin",
                "model_builtin": {"enabled": True},
            },
            providers={"model_builtin": provider},
        )

        result = router.search(
            WebSearchRequest(query="route", provider="model_builtin"),
            WebSearchContext(allow_network=True),
        )

        self.assertTrue(result.success)
        self.assertEqual(result.metadata["final_provider"], "model_builtin")
        self.assertEqual(len(manager.calls), 1)


if __name__ == "__main__":
    unittest.main()
