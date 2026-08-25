from __future__ import annotations

from abc import ABC, abstractmethod

from ...base import ToolResult
from ..protocol import ProviderSearchResult, WebSearchContext, WebSearchData, WebSearchRequest


class WebSearchProvider(ABC):
    """Base contract for local web_search providers.

    Providers may return a ProviderSearchResult for normalization or a fully
    normalized WebSearchData when they already share the official schema.
    """

    provider_id: str
    provider_type: str

    @abstractmethod
    def is_configured(self) -> bool:
        """Return whether the provider has enough local configuration to run."""

    @abstractmethod
    def supports(self, request: WebSearchRequest) -> bool:
        """Return whether the provider can handle this normalized request."""

    @abstractmethod
    def dry_run(
        self,
        request: WebSearchRequest,
        context: WebSearchContext,
    ) -> ToolResult:
        """Return a structured preview without performing real network access."""

    @abstractmethod
    def search(
        self,
        request: WebSearchRequest,
        context: WebSearchContext,
    ) -> ToolResult:
        """Execute the provider and return a ToolResult containing web search data."""


ProviderSearchPayload = ProviderSearchResult | WebSearchData


__all__ = [
    "ProviderSearchPayload",
    "ProviderSearchResult",
    "WebSearchContext",
    "WebSearchData",
    "WebSearchProvider",
    "WebSearchRequest",
]
