"""Provider interfaces for web_search."""

from .base import ProviderSearchPayload, WebSearchProvider
from .fake import FAKE_WEB_SEARCH_SCENARIOS, FakeWebSearchProvider
from .model_builtin import ModelBuiltinSearchProvider
from .tavily import TavilySearchProvider

__all__ = [
    "FAKE_WEB_SEARCH_SCENARIOS",
    "FakeWebSearchProvider",
    "ModelBuiltinSearchProvider",
    "ProviderSearchPayload",
    "TavilySearchProvider",
    "WebSearchProvider",
]
