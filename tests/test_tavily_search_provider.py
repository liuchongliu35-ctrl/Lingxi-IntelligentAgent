from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch
import unittest

from src.tools import ToolErrorCode, WebSearchContext, WebSearchRequest
from src.tools.web_search.providers import TavilySearchProvider
from src.tools.web_search.router import WebSearchRouter


class FakeResponse:
    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> object:
        if isinstance(self._payload, BaseException):
            raise self._payload
        return self._payload


class FakeSession:
    def __init__(self, response: object | None = None, error: BaseException | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict[str, object]] = []

    def post(self, url: str, **kwargs: object) -> object:
        self.calls.append({"url": url, **kwargs})
        if self.error is not None:
            raise self.error
        return self.response


class TavilySearchProviderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        fixture_path = Path(__file__).with_name("fixtures") / "tavily_search_responses.json"
        cls.fixture = json.loads(fixture_path.read_text(encoding="utf-8"))

    def _provider(self, session: object | None = None, **config: object) -> TavilySearchProvider:
        merged = {
            "enabled": True,
            "api_key_env": "TAVILY_API_KEY",
            "endpoint": "https://api.tavily.com/search",
            "timeout_seconds": 12,
            "default_search_depth": "basic",
            "default_topic": "general",
            "include_answer": False,
            "include_raw_content": False,
        }
        merged.update(config)
        return TavilySearchProvider(merged, session=session)

    def test_success_response_is_normalized_and_raw_content_is_closed_by_default(self):
        session = FakeSession(FakeResponse(200, self.fixture["success"]))
        provider = self._provider(session=session)
        request = WebSearchRequest(
            query="agent architecture",
            provider="search_api",
            include_raw_content=False,
        )

        with patch.dict(os.environ, {"TAVILY_API_KEY": "secret-key"}, clear=False):
            result = provider.search(request, WebSearchContext(allow_network=True, timeout_seconds=9))

        self.assertTrue(result.success)
        self.assertEqual(result.provider, "tavily")
        self.assertEqual(result.metadata["request_id"], "req-123")
        self.assertEqual(result.data.query, "agent architecture")
        self.assertEqual(result.data.provider, "tavily")
        self.assertEqual(result.data.provider_type, "search_api")
        self.assertEqual(result.data.result_count, 1)
        self.assertEqual(result.data.answer, "A concise summary from Tavily.")
        self.assertEqual(result.data.summary, "A concise summary from Tavily.")
        self.assertEqual(result.data.response_time_ms, 420)
        self.assertEqual(result.data.evidence_level, "url_verified")
        self.assertEqual(result.data.source_quality, "verified_sources")
        self.assertIsNone(result.data.results[0].raw_content)
        self.assertEqual(result.data.results[0].favicon, "https://example.test/favicon.ico")
        self.assertEqual(result.data.results[0].images[0]["url"], "https://example.test/image.png")

    def test_include_raw_content_can_be_enabled(self):
        session = FakeSession(FakeResponse(200, self.fixture["success"]))
        provider = self._provider(session=session)
        request = WebSearchRequest(
            query="agent architecture",
            provider="search_api",
            include_raw_content=True,
        )

        with patch.dict(os.environ, {"TAVILY_API_KEY": "secret-key"}, clear=False):
            result = provider.search(request, WebSearchContext(allow_network=True))

        self.assertTrue(result.success)
        self.assertEqual(
            result.data.results[0].raw_content,
            "Full raw page text that should be hidden by default.",
        )

    def test_missing_api_key_is_not_configured(self):
        provider = self._provider()

        with patch.dict(os.environ, {}, clear=True):
            result = provider.search(
                WebSearchRequest(query="agent", provider="search_api"),
                WebSearchContext(allow_network=True),
            )

        self.assertFalse(provider.is_configured())
        self.assertFalse(result.success)
        self.assertEqual(result.code, ToolErrorCode.PROVIDER_NOT_CONFIGURED.value)

    def test_error_mapping_and_invalid_schema_are_structured(self):
        request = WebSearchRequest(query="agent", provider="search_api")

        cases = [
            (
                FakeSession(FakeResponse(401, {"error": "nope"})),
                ToolErrorCode.PROVIDER_AUTH_FAILED.value,
                False,
            ),
            (
                FakeSession(FakeResponse(429, {"error": "slow down"})),
                ToolErrorCode.PROVIDER_RATE_LIMITED.value,
                True,
            ),
            (
                FakeSession(error=TimeoutError("timeout")),
                ToolErrorCode.PROVIDER_TIMEOUT.value,
                True,
            ),
            (
                FakeSession(FakeResponse(200, self.fixture["missing_results"])),
                ToolErrorCode.PROVIDER_RESPONSE_INVALID.value,
                False,
            ),
            (
                FakeSession(FakeResponse(200, ["not", "an", "object"])),
                ToolErrorCode.PROVIDER_RESPONSE_INVALID.value,
                False,
            ),
        ]

        with patch.dict(os.environ, {"TAVILY_API_KEY": "secret-key"}, clear=False):
            for session, expected_code, retryable in cases:
                with self.subTest(expected_code=expected_code):
                    provider = self._provider(session=session)
                    result = provider.search(request, WebSearchContext(allow_network=True))
                    self.assertFalse(result.success)
                    self.assertEqual(result.code, expected_code)
                    self.assertEqual(result.retryable, retryable)

    def test_router_can_use_tavily_search_api_provider(self):
        session = FakeSession(FakeResponse(200, self.fixture["success"]))
        provider = self._provider(session=session)
        router = WebSearchRouter(
            {
                "provider": "search_api",
                "search_api": {"enabled": True},
                "fake": {"enabled": True, "scenario": "success"},
            },
            providers={"search_api": provider},
        )

        with patch.dict(os.environ, {"TAVILY_API_KEY": "secret-key"}, clear=False):
            result = router.search(
                WebSearchRequest(query="agent architecture", provider="search_api"),
                WebSearchContext(allow_network=True),
            )

        self.assertTrue(result.success)
        self.assertEqual(result.provider, "tavily")
        self.assertEqual(result.metadata["final_provider"], "search_api")
        self.assertEqual(result.data.provider, "tavily")
        self.assertEqual(result.data.result_count, 1)


if __name__ == "__main__":
    unittest.main()
