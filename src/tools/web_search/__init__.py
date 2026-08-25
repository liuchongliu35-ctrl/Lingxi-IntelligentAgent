"""Web search protocol package for Tools V1."""

from .protocol import (
    WEB_SEARCH_DEPTHS,
    WEB_SEARCH_EVIDENCE_LEVELS,
    WEB_SEARCH_PROVIDER_TYPES,
    WEB_SEARCH_ROUTE_PROVIDERS,
    WEB_SEARCH_SOURCE_QUALITIES,
    WEB_SEARCH_TOPICS,
    ProviderSearchResult,
    WebSearchContext,
    WebSearchData,
    WebSearchRequest,
    WebSearchResult,
    normalize_web_search_data,
)
from .providers import (
    FAKE_WEB_SEARCH_SCENARIOS,
    FakeWebSearchProvider,
    ModelBuiltinSearchProvider,
    TavilySearchProvider,
)
from .model_search_schema import (
    MODEL_SEARCH_PROMPT_VERSION,
    MODEL_SEARCH_SCHEMA,
    MODEL_SEARCH_SCHEMA_VERSION,
    build_model_search_prompt,
)
from .normalization import (
    DEFAULT_OBSERVATION_RESULT_LIMIT,
    WEB_SEARCH_NORMALIZATION_VERSION,
    build_web_search_observation_views,
    normalize_url_for_dedup,
    normalize_web_search_evidence,
)
from .providers.base import ProviderSearchPayload, WebSearchProvider
from .router import WEB_SEARCH_PROVIDER_CHOICES, WEB_SEARCH_PROVIDER_ORDER, WebSearchRouter, WebSearchRouterConfig
from .tool import WebSearchTool

__all__ = [
    "FAKE_WEB_SEARCH_SCENARIOS",
    "WEB_SEARCH_DEPTHS",
    "WEB_SEARCH_EVIDENCE_LEVELS",
    "WEB_SEARCH_PROVIDER_CHOICES",
    "WEB_SEARCH_PROVIDER_ORDER",
    "WEB_SEARCH_PROVIDER_TYPES",
    "WEB_SEARCH_ROUTE_PROVIDERS",
    "WEB_SEARCH_SOURCE_QUALITIES",
    "WEB_SEARCH_TOPICS",
    "FakeWebSearchProvider",
    "ModelBuiltinSearchProvider",
    "TavilySearchProvider",
    "MODEL_SEARCH_PROMPT_VERSION",
    "MODEL_SEARCH_SCHEMA",
    "MODEL_SEARCH_SCHEMA_VERSION",
    "build_model_search_prompt",
    "DEFAULT_OBSERVATION_RESULT_LIMIT",
    "WEB_SEARCH_NORMALIZATION_VERSION",
    "build_web_search_observation_views",
    "normalize_url_for_dedup",
    "normalize_web_search_evidence",
    "ProviderSearchPayload",
    "ProviderSearchResult",
    "WebSearchContext",
    "WebSearchData",
    "WebSearchProvider",
    "WebSearchRequest",
    "WebSearchResult",
    "WebSearchRouter",
    "WebSearchRouterConfig",
    "WebSearchTool",
    "normalize_web_search_data",
]
