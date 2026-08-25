from __future__ import annotations

import unittest

from src.tools import (
    ToolErrorCode,
    WebSearchContext,
    WebSearchData,
    WebSearchRequest,
    WebSearchRouter,
)


class WebSearchRoutingTest(unittest.TestCase):
    def test_auto_order_falls_back_from_unconfigured_provider_to_fake(self):
        router = WebSearchRouter(
            {
                "provider": "auto",
                "auto_order": ["search_api", "fake"],
                "search_api": {"enabled": False},
                "fake": {"enabled": True, "scenario": "success"},
            }
        )

        result = router.search(
            WebSearchRequest(query="agent"),
            WebSearchContext(allow_network=True),
        )

        self.assertTrue(result.success)
        self.assertIsInstance(result.data, WebSearchData)
        self.assertEqual(result.data.provider, "fake")
        self.assertEqual(result.metadata["attempted_providers"], ["search_api", "fake"])
        self.assertTrue(result.metadata["fallback_used"])
        self.assertEqual(result.metadata["final_provider"], "fake")

    def test_explicit_provider_does_not_fallback(self):
        router = WebSearchRouter(
            {
                "provider": "auto",
                "auto_order": ["search_api", "fake"],
                "search_api": {"enabled": False},
                "fake": {"enabled": True, "scenario": "success"},
            }
        )

        result = router.search(
            WebSearchRequest(query="agent", provider="search_api"),
            WebSearchContext(allow_network=True),
        )

        self.assertFalse(result.success)
        self.assertEqual(result.code, ToolErrorCode.SEARCH_NOT_CONFIGURED.value)
        self.assertEqual(result.metadata["attempted_providers"], ["search_api"])
        self.assertFalse(result.metadata["fallback_used"])

    def test_disabled_route_returns_search_not_configured(self):
        router = WebSearchRouter({"provider": "disabled"})

        result = router.search(
            WebSearchRequest(query="agent"),
            WebSearchContext(allow_network=True),
        )

        self.assertFalse(result.success)
        self.assertEqual(result.code, ToolErrorCode.SEARCH_NOT_CONFIGURED.value)
        self.assertEqual(result.metadata["final_provider"], "disabled")

    def test_fake_provider_scenarios_are_structured(self):
        empty = WebSearchRouter(
            {"provider": "fake", "fake": {"scenario": "empty"}}
        ).search(WebSearchRequest(query="nothing"), WebSearchContext())
        no_url = WebSearchRouter(
            {"provider": "fake", "fake": {"scenario": "no_url_summary"}}
        ).search(WebSearchRequest(query="summary"), WebSearchContext())
        timeout = WebSearchRouter(
            {"provider": "fake", "fake": {"scenario": "timeout"}}
        ).search(WebSearchRequest(query="slow"), WebSearchContext())
        schema_invalid = WebSearchRouter(
            {"provider": "fake", "fake": {"scenario": "schema_invalid"}}
        ).search(WebSearchRequest(query="bad"), WebSearchContext())

        self.assertTrue(empty.success)
        self.assertEqual(empty.data.result_count, 0)
        self.assertEqual(empty.data.source_quality, "empty")

        self.assertTrue(no_url.success)
        self.assertEqual(no_url.data.evidence_level, "no_url_summary")
        self.assertEqual(no_url.data.source_quality, "summary_only")

        self.assertFalse(timeout.success)
        self.assertEqual(timeout.code, ToolErrorCode.PROVIDER_TIMEOUT.value)

        self.assertFalse(schema_invalid.success)
        self.assertEqual(schema_invalid.code, ToolErrorCode.PROVIDER_RESPONSE_INVALID.value)

    def test_real_provider_without_network_is_not_fallbacked(self):
        router = WebSearchRouter(
            {
                "provider": "auto",
                "auto_order": ["search_api", "fake"],
                "search_api": {"enabled": True},
                "fake": {"enabled": True, "scenario": "success"},
            }
        )

        result = router.search(
            WebSearchRequest(query="agent"),
            WebSearchContext(allow_network=False),
        )

        self.assertFalse(result.success)
        self.assertEqual(result.code, ToolErrorCode.NETWORK_NOT_ALLOWED.value)
        self.assertEqual(result.metadata["attempted_providers"], ["search_api"])

    def test_dry_run_returns_route_without_provider_execution(self):
        router = WebSearchRouter(
            {"provider": "auto", "auto_order": ["search_api", "fake"]}
        )

        result = router.dry_run(
            WebSearchRequest(query="agent", include_raw_content=True),
            WebSearchContext(allow_network=False, dry_run=True, timeout_seconds=7),
        )

        self.assertTrue(result.success)
        self.assertEqual(result.data["provider_route"], "auto")
        self.assertEqual(result.data["auto_order"], ["search_api", "fake"])
        self.assertFalse(result.data["allow_network"])
        self.assertEqual(result.data["estimated_timeout"], 7)


if __name__ == "__main__":
    unittest.main()
