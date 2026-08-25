from __future__ import annotations

import os
import unittest

from src.tools import TavilySearchProvider, ToolErrorCode, WebSearchContext, WebSearchRequest


TRUTHY = {"1", "true", "yes", "on"}


def _enabled(name: str) -> bool:
    return str(os.getenv(name) or "").strip().lower() in TRUTHY


class TavilyWebSearchIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not _enabled("RUN_TOOL_INTEGRATION_TESTS"):
            raise unittest.SkipTest(
                "set RUN_TOOL_INTEGRATION_TESTS=true to enable live tool calls"
            )
        if not _enabled("RUN_WEB_SEARCH_INTEGRATION_TESTS"):
            raise unittest.SkipTest(
                "set RUN_WEB_SEARCH_INTEGRATION_TESTS=true to enable live web search"
            )
        if not os.getenv("TAVILY_API_KEY"):
            raise unittest.SkipTest("TAVILY_API_KEY is not configured")

    def test_live_tavily_returns_unified_web_search_data(self):
        provider = TavilySearchProvider(
            {
                "enabled": True,
                "api_key_env": "TAVILY_API_KEY",
                "endpoint": os.getenv(
                    "TAVILY_ENDPOINT",
                    "https://api.tavily.com/search",
                ),
                "timeout_seconds": int(os.getenv("TAVILY_TIMEOUT_SECONDS", "30")),
                "default_search_depth": os.getenv(
                    "TAVILY_SEARCH_DEPTH",
                    "basic",
                ),
                "default_topic": os.getenv("TAVILY_TOPIC", "general"),
            }
        )
        self.assertTrue(provider.is_configured())

        result = provider.search(
            WebSearchRequest(
                query=os.getenv(
                    "WEB_SEARCH_INTEGRATION_QUERY",
                    "latest developments in agent tool runtimes",
                ),
                provider="search_api",
                max_results=3,
                include_raw_content=False,
            ),
            WebSearchContext(
                allow_network=True,
                timeout_seconds=int(os.getenv("TAVILY_TIMEOUT_SECONDS", "30")),
            ),
        )

        self.assertTrue(result.success, result.to_dict())
        self.assertEqual(result.data.provider_type, "search_api")
        self.assertLessEqual(result.data.result_count, 3)
        self.assertFalse(result.data.raw_content_included)
        self.assertNotEqual(result.code, ToolErrorCode.PROVIDER_ERROR.value)


if __name__ == "__main__":
    unittest.main()
