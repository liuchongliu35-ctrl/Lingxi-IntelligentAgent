from __future__ import annotations

from typing import Any, Mapping

from ...base import ToolResult
from ...errors import ToolErrorCode
from ..protocol import (
    ProviderSearchResult,
    WebSearchContext,
    WebSearchRequest,
)
from .base import WebSearchProvider


FAKE_WEB_SEARCH_SCENARIOS = frozenset(
    {
        "success",
        "empty",
        "timeout",
        "schema_invalid",
        "no_url_summary",
        "not_configured",
        "network_not_allowed",
    }
)


class FakeWebSearchProvider(WebSearchProvider):
    provider_id = "fake"
    provider_type = "fake"

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        self.config = dict(config or {})
        self.enabled = bool(self.config.get("enabled", True))
        scenario = str(self.config.get("scenario") or "success").strip().lower()
        self.scenario = scenario if scenario in FAKE_WEB_SEARCH_SCENARIOS else "success"

    def is_configured(self) -> bool:
        return self.enabled and self.scenario != "not_configured"

    def supports(self, request: WebSearchRequest) -> bool:
        return request.provider in {"auto", "fake"}

    def dry_run(
        self,
        request: WebSearchRequest,
        context: WebSearchContext,
    ) -> ToolResult:
        data = {
            "query": request.query,
            "provider": self.provider_id,
            "provider_type": self.provider_type,
            "scenario": self.scenario,
            "max_results": request.max_results,
            "search_depth": request.search_depth,
            "include_answer": request.include_answer,
            "include_raw_content": request.include_raw_content,
            "allow_network": context.allow_network,
            "estimated_timeout": context.timeout_seconds,
        }
        return ToolResult.ok(
            data=data,
            message="Fake web_search dry-run prepared.",
            provider=self.provider_id,
        )

    def search(
        self,
        request: WebSearchRequest,
        context: WebSearchContext,
    ) -> ToolResult:
        del context
        if not self.is_configured():
            return ToolResult.fail(
                "Fake web search provider is not configured.",
                code=ToolErrorCode.SEARCH_NOT_CONFIGURED.value,
                provider=self.provider_id,
                metadata={"provider": self.provider_id, "scenario": self.scenario},
            )
        if self.scenario == "network_not_allowed":
            return ToolResult.fail(
                "Network access is not allowed for web_search.",
                code=ToolErrorCode.NETWORK_NOT_ALLOWED.value,
                provider=self.provider_id,
                metadata={"provider": self.provider_id, "scenario": self.scenario},
            )
        if self.scenario == "timeout":
            return ToolResult.fail(
                "Fake web search provider timed out.",
                code=ToolErrorCode.PROVIDER_TIMEOUT.value,
                provider=self.provider_id,
                metadata={"provider": self.provider_id, "scenario": self.scenario},
            )
        if self.scenario == "schema_invalid":
            return ToolResult.fail(
                "Fake web search provider returned an invalid response schema.",
                code=ToolErrorCode.PROVIDER_RESPONSE_INVALID.value,
                provider=self.provider_id,
                metadata={"provider": self.provider_id, "scenario": self.scenario},
            )

        if self.scenario == "empty":
            payload = ProviderSearchResult(
                query=request.query,
                provider=self.provider_id,
                provider_type=self.provider_type,
                mode=request.mode,
                search_depth=request.search_depth,
                topic=request.topic,
                results=[],
                metadata={"scenario": self.scenario},
            )
            return ToolResult.ok(
                data=payload.to_web_search_data(request=request),
                message=f"No fake search results found for: {request.query}",
                provider=self.provider_id,
            )

        if self.scenario == "no_url_summary":
            payload = ProviderSearchResult(
                query=request.query,
                provider=self.provider_id,
                provider_type=self.provider_type,
                mode=request.mode,
                search_depth=request.search_depth,
                topic=request.topic,
                summary=f"Fake provider summary for {request.query}.",
                evidence_level="no_url_summary",
                source_quality="summary_only",
                results=[],
                metadata={"scenario": self.scenario},
            )
            return ToolResult.ok(
                data=payload.to_web_search_data(request=request),
                message="Fake provider returned a summary without URL evidence.",
                provider=self.provider_id,
            )

        payload = ProviderSearchResult(
            query=request.query,
            provider=self.provider_id,
            provider_type=self.provider_type,
            mode=request.mode,
            search_depth=request.search_depth,
            topic=request.topic,
            results=[
                {
                    "title": f"Fake result for {request.query}",
                    "url": "https://example.test/web-search",
                    "snippet": "Deterministic fake web_search result for offline tests.",
                    "source": "example.test",
                    "rank": 1,
                    "score": 0.99,
                }
            ][: request.max_results],
            metadata={"scenario": self.scenario},
        )
        return ToolResult.ok(
            data=payload.to_web_search_data(request=request),
            message=f"Fake web search returned {len(payload.results)} result(s).",
            provider=self.provider_id,
        )


__all__ = [
    "FAKE_WEB_SEARCH_SCENARIOS",
    "FakeWebSearchProvider",
]
