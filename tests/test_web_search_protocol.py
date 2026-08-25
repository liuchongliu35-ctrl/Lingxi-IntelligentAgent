from __future__ import annotations

import json
import unittest

from src.tools import (
    ProviderSearchResult,
    ToolResult,
    WebSearchContext,
    WebSearchData,
    WebSearchProvider,
    WebSearchRequest,
    WebSearchResult,
    normalize_web_search_data,
    normalize_web_search_evidence_level,
    normalize_web_search_source_quality,
)


class FakeProtocolProvider(WebSearchProvider):
    provider_id = "fake"
    provider_type = "fake"

    def is_configured(self) -> bool:
        return True

    def supports(self, request: WebSearchRequest) -> bool:
        return request.provider in {"auto", "fake"}

    def dry_run(self, request: WebSearchRequest, context: WebSearchContext) -> ToolResult:
        return ToolResult.ok(
            data={
                "query": request.query,
                "provider": self.provider_id,
                "allow_network": context.allow_network,
                "estimated_timeout": context.timeout_seconds,
            },
            tool_name="web_search",
            provider=self.provider_id,
        )

    def search(self, request: WebSearchRequest, context: WebSearchContext) -> ToolResult:
        payload = ProviderSearchResult(
            query=request.query,
            provider=self.provider_id,
            provider_type=self.provider_type,
            search_depth=request.search_depth,
            topic=request.topic,
            results=[
                {
                    "title": "Example",
                    "url": "https://example.test/search",
                    "snippet": "Structured search result.",
                }
            ],
        )
        return ToolResult.ok(
            data=payload.to_web_search_data(request=request),
            tool_name="web_search",
            provider=self.provider_id,
        )


class WebSearchProtocolTest(unittest.TestCase):
    def test_data_object_serialization_and_result_count_are_stable(self):
        data = WebSearchData(
            query="agent",
            provider="fake",
            provider_type="fake",
            results=[
                WebSearchResult(title="A", url="https://example.test/a"),
                {"title": "B", "url": "https://example.test/b", "extra": "raw"},
            ],
            result_count=99,
        )

        payload = data.to_dict()
        encoded = json.dumps(payload, ensure_ascii=False)

        self.assertEqual(data.result_count, 2)
        self.assertEqual(json.loads(encoded)["result_count"], 2)
        self.assertEqual(payload["results"][1]["title"], "B")
        self.assertNotIn("extra", payload["results"][1])

    def test_evidence_and_source_quality_normalizers_accept_only_known_values(self):
        self.assertEqual(
            normalize_web_search_evidence_level("MODEL_REPORTED"),
            "model_reported",
        )
        self.assertEqual(
            normalize_web_search_evidence_level("unknown", default="no_url_summary"),
            "no_url_summary",
        )
        self.assertEqual(
            normalize_web_search_source_quality("PARTIAL_SOURCES"),
            "partial_sources",
        )
        self.assertEqual(
            normalize_web_search_source_quality("unknown", default="summary_only"),
            "summary_only",
        )

    def test_no_url_summary_is_success_compatible_and_weak_evidence(self):
        data = normalize_web_search_data(
            request=WebSearchRequest(query="today's context", provider="model_builtin"),
            provider="configured-model",
            provider_type="model_builtin",
            summary="The provider returned a summary without auditable URLs.",
            evidence_level="url_verified",
            source_quality="verified_sources",
        )

        self.assertEqual(data.result_count, 0)
        self.assertEqual(data.evidence_level, "no_url_summary")
        self.assertEqual(data.source_quality, "summary_only")

    def test_empty_results_remain_empty_without_provider_error(self):
        data = normalize_web_search_data(
            request=WebSearchRequest(query="rare query", provider="search_api"),
            provider="tavily",
            provider_type="search_api",
            results=[],
        )

        self.assertEqual(data.result_count, 0)
        self.assertEqual(data.source_quality, "empty")
        self.assertEqual(data.evidence_level, "provider_reported")

    def test_provider_raw_fields_are_confined_to_metadata(self):
        data = normalize_web_search_data(
            {
                "query": "agent",
                "provider": "tavily",
                "provider_type": "search_api",
                "results": [
                    {
                        "title": "A",
                        "url": "https://example.test/a",
                        "provider_extra": "not official",
                    }
                ],
                "unexpected_top_level": {"request_units": 1},
            },
            raw_provider_response={"opaque": True},
        )
        payload = data.to_dict()

        self.assertNotIn("unexpected_top_level", payload)
        self.assertNotIn("provider_extra", payload["results"][0])
        self.assertEqual(
            payload["metadata"]["provider_raw"]["unexpected_top_level"],
            {"request_units": 1},
        )
        self.assertEqual(
            payload["metadata"]["provider_raw_results"][0]["fields"]["provider_extra"],
            "not official",
        )
        self.assertEqual(payload["metadata"]["provider_raw_response"], {"opaque": True})

    def test_provider_interface_can_return_normalized_web_search_data(self):
        provider = FakeProtocolProvider()
        request = WebSearchRequest(query="agent architecture", provider="fake")
        context = WebSearchContext(allow_network=False, dry_run=True, timeout_seconds=9)

        preview = provider.dry_run(request, context)
        result = provider.search(request, context)

        self.assertTrue(provider.is_configured())
        self.assertTrue(provider.supports(request))
        self.assertTrue(preview.success)
        self.assertEqual(preview.data["estimated_timeout"], 9)
        self.assertTrue(result.success)
        self.assertIsInstance(result.data, WebSearchData)
        self.assertEqual(result.data.provider, "fake")
        self.assertEqual(result.data.result_count, 1)
        self.assertEqual(result.data.evidence_level, "url_verified")


if __name__ == "__main__":
    unittest.main()
