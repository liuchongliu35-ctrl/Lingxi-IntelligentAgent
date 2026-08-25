from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from ..base import ToolResult
from ..data_types import WebSearchData
from ..errors import ToolErrorCode
from .protocol import (
    ProviderSearchResult,
    WebSearchContext,
    WebSearchRequest,
    normalize_web_search_data,
)
from .normalization import normalize_web_search_evidence
from .providers import FakeWebSearchProvider, ModelBuiltinSearchProvider, TavilySearchProvider
from .providers.base import WebSearchProvider


WEB_SEARCH_PROVIDER_ORDER = ("search_api", "model_builtin")
WEB_SEARCH_PROVIDER_CHOICES = frozenset(
    {"auto", "search_api", "model_builtin", "fake", "disabled"}
)
_FALLBACK_CONFIG_CODES = {
    ToolErrorCode.PROVIDER_NOT_CONFIGURED.value,
    ToolErrorCode.SEARCH_NOT_CONFIGURED.value,
}
_FALLBACK_RETRYABLE_CODES = {
    ToolErrorCode.PROVIDER_TIMEOUT.value,
    ToolErrorCode.PROVIDER_RATE_LIMITED.value,
}
_NEVER_FALLBACK_CODES = {
    ToolErrorCode.NETWORK_NOT_ALLOWED.value,
    ToolErrorCode.BLOCKED_BY_POLICY.value,
    ToolErrorCode.PROVIDER_AUTH_FAILED.value,
    ToolErrorCode.PROVIDER_RESPONSE_INVALID.value,
}


@dataclass
class WebSearchRouterConfig:
    enabled: bool = True
    provider: str = "auto"
    auto_order: list[str] = field(default_factory=lambda: list(WEB_SEARCH_PROVIDER_ORDER))
    timeout_seconds: int = 30
    max_results: int = 5
    max_query_chars: int = 400
    allow_timeout_fallback: bool = True
    max_fallbacks: int = 1
    provider_configs: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None = None) -> "WebSearchRouterConfig":
        values = dict(raw or {})
        provider_configs = _provider_configs(values)
        provider = _provider_choice(
            values.get("provider", values.get("default_provider", "auto")),
            default="auto",
        )
        auto_order = _provider_list(
            values.get("auto_order"),
            default=list(WEB_SEARCH_PROVIDER_ORDER),
        )
        return cls(
            enabled=_bool(values.get("enabled"), True),
            provider=provider,
            auto_order=auto_order,
            timeout_seconds=_positive_int(values.get("timeout_seconds"), 30),
            max_results=_bounded_int(values.get("max_results"), 5, 1, 20),
            max_query_chars=_positive_int(values.get("max_query_chars"), 400),
            allow_timeout_fallback=_bool(values.get("allow_timeout_fallback"), True),
            max_fallbacks=max(_positive_int(values.get("max_fallbacks"), 1), 0),
            provider_configs=provider_configs,
        )

    def provider_config(self, provider: str) -> dict[str, Any]:
        return dict(self.provider_configs.get(provider, {}))


class WebSearchRouter:
    def __init__(
        self,
        config: Mapping[str, Any] | WebSearchRouterConfig | None = None,
        *,
        providers: Mapping[str, WebSearchProvider] | None = None,
        model_manager: Any | None = None,
    ) -> None:
        self.config = (
            config
            if isinstance(config, WebSearchRouterConfig)
            else WebSearchRouterConfig.from_mapping(config)
        )
        configured_providers = dict(providers or {})
        configured_providers.setdefault(
            "search_api",
            TavilySearchProvider(self.config.provider_config("search_api")),
        )
        configured_providers.setdefault(
            "fake",
            FakeWebSearchProvider(self.config.provider_config("fake")),
        )
        configured_providers.setdefault(
            "model_builtin",
            ModelBuiltinSearchProvider(
                self.config.provider_config("model_builtin"),
                model_manager=model_manager,
            ),
        )
        self.providers = configured_providers

    def dry_run(self, request: WebSearchRequest, context: WebSearchContext) -> ToolResult:
        route = self._route_provider(request)
        data = {
            "query": request.query,
            "provider_route": route,
            "auto_order": self._auto_order(),
            "max_results": request.max_results,
            "search_depth": request.search_depth,
            "include_answer": request.include_answer,
            "include_raw_content": request.include_raw_content,
            "allow_network": context.allow_network,
            "estimated_timeout": context.timeout_seconds or self.config.timeout_seconds,
        }
        return ToolResult.ok(
            data=data,
            message="web_search dry-run route prepared.",
            provider=route,
        )

    def search(self, request: WebSearchRequest, context: WebSearchContext) -> ToolResult:
        route = self._route_provider(request)
        if not self.config.enabled or route == "disabled":
            return self._route_failure(
                "web_search is disabled by provider configuration.",
                code=ToolErrorCode.SEARCH_NOT_CONFIGURED.value,
                attempted_providers=[],
                final_provider=route,
            )

        if route == "auto":
            return self._search_auto(request, context)
        return self._search_one(
            route,
            request,
            context,
            attempted=[],
            explicit=True,
        )

    def _search_auto(self, request: WebSearchRequest, context: WebSearchContext) -> ToolResult:
        attempted: list[str] = []
        last_result: ToolResult | None = None
        fallback_count = 0

        for provider_name in self._auto_order():
            result = self._search_one(
                provider_name,
                request,
                context,
                attempted=attempted,
                explicit=False,
            )
            last_result = result
            if result.success:
                return result

            code = str(result.code or "")
            if code in _NEVER_FALLBACK_CODES:
                return result
            if code in _FALLBACK_CONFIG_CODES:
                continue
            if code in _FALLBACK_RETRYABLE_CODES:
                if not self.config.allow_timeout_fallback:
                    return result
                fallback_count += 1
                if fallback_count > self.config.max_fallbacks:
                    return result
                continue
            return result

        if last_result is not None:
            return last_result
        return self._route_failure(
            "No configured web_search providers are available.",
            code=ToolErrorCode.SEARCH_NOT_CONFIGURED.value,
            attempted_providers=attempted,
            final_provider=None,
        )

    def _search_one(
        self,
        provider_name: str,
        request: WebSearchRequest,
        context: WebSearchContext,
        *,
        attempted: list[str],
        explicit: bool,
    ) -> ToolResult:
        attempted.append(provider_name)
        if provider_name not in WEB_SEARCH_PROVIDER_CHOICES or provider_name in {"auto", "disabled"}:
            return self._route_failure(
                f"Unsupported web_search provider: {provider_name}",
                code=ToolErrorCode.SEARCH_NOT_CONFIGURED.value,
                attempted_providers=attempted,
                final_provider=provider_name,
            )

        provider_config = self.config.provider_config(provider_name)
        if provider_config and not _bool(provider_config.get("enabled"), True):
            return self._route_failure(
                f"web_search provider is disabled: {provider_name}",
                code=ToolErrorCode.SEARCH_NOT_CONFIGURED.value,
                attempted_providers=attempted,
                final_provider=provider_name,
            )
        if provider_name != "fake" and not context.allow_network:
            return self._route_failure(
                "Network access is not allowed for web_search.",
                code=ToolErrorCode.NETWORK_NOT_ALLOWED.value,
                attempted_providers=attempted,
                final_provider=provider_name,
            )

        provider = self.providers.get(provider_name)
        if provider is None:
            return self._route_failure(
                f"web_search provider is not configured: {provider_name}",
                code=ToolErrorCode.SEARCH_NOT_CONFIGURED.value,
                attempted_providers=attempted,
                final_provider=provider_name,
            )
        if not provider.is_configured():
            return self._route_failure(
                f"web_search provider is not configured: {provider_name}",
                code=ToolErrorCode.SEARCH_NOT_CONFIGURED.value,
                attempted_providers=attempted,
                final_provider=provider_name,
            )
        if not provider.supports(request):
            return self._route_failure(
                f"web_search provider does not support this request: {provider_name}",
                code=ToolErrorCode.PROVIDER_NOT_CONFIGURED.value,
                attempted_providers=attempted,
                final_provider=provider_name,
            )

        result = provider.search(request, context)
        return self._finalize_result(
            result,
            request=request,
            context=context,
            attempted_providers=attempted,
            final_provider=provider_name,
            fallback_used=(not explicit and len(attempted) > 1),
        )

    def _finalize_result(
        self,
        result: ToolResult,
        *,
        request: WebSearchRequest,
        context: WebSearchContext,
        attempted_providers: list[str],
        final_provider: str | None,
        fallback_used: bool,
    ) -> ToolResult:
        if result.success and isinstance(result.data, ProviderSearchResult):
            result.data = normalize_web_search_data(result.data, request=request)
        if result.success and isinstance(result.data, WebSearchData):
            result.data = normalize_web_search_evidence(
                result.data,
                max_results=request.max_results,
                max_output_chars=context.max_output_chars,
                max_observation_chars=context.max_observation_chars,
                observation_result_limit=_observation_result_limit(context),
                code=result.code or "ok",
            )
        route_metadata = _route_metadata(
            attempted_providers=attempted_providers,
            final_provider=final_provider,
            fallback_used=fallback_used,
            fallback_reason=None if not fallback_used else "previous_provider_failed",
        )
        result.metadata.update(route_metadata)
        if isinstance(result.data, WebSearchData):
            result.data.metadata.setdefault("route", route_metadata)
        return result

    def _route_failure(
        self,
        message: str,
        *,
        code: str,
        attempted_providers: list[str],
        final_provider: str | None,
    ) -> ToolResult:
        return ToolResult.fail(
            message,
            code=code,
            provider=final_provider,
            metadata=_route_metadata(
                attempted_providers=attempted_providers,
                final_provider=final_provider,
                fallback_used=len(attempted_providers) > 1,
                fallback_reason=code if len(attempted_providers) > 1 else None,
            ),
        )

    def _route_provider(self, request: WebSearchRequest) -> str:
        if request.provider != "auto":
            return request.provider
        return self.config.provider

    def _auto_order(self) -> list[str]:
        return [provider for provider in self.config.auto_order if provider != "auto"]


def _route_metadata(
    *,
    attempted_providers: list[str],
    final_provider: str | None,
    fallback_used: bool,
    fallback_reason: str | None,
) -> dict[str, Any]:
    return {
        "attempted_providers": list(attempted_providers),
        "fallback_used": bool(fallback_used),
        "fallback_reason": fallback_reason,
        "final_provider": final_provider,
    }


def _provider_configs(values: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    raw_nested = values.get("providers")
    result: dict[str, dict[str, Any]] = {}
    if isinstance(raw_nested, Mapping):
        for key, value in raw_nested.items():
            if str(key) in WEB_SEARCH_PROVIDER_CHOICES and isinstance(value, Mapping):
                result[str(key)] = dict(value)
    for key in WEB_SEARCH_PROVIDER_CHOICES:
        value = values.get(key)
        if isinstance(value, Mapping):
            merged = dict(result.get(key, {}))
            merged.update(dict(value))
            result[key] = merged
    return result


def _provider_choice(value: Any, *, default: str) -> str:
    normalized = str(value or default).strip().lower()
    return normalized if normalized in WEB_SEARCH_PROVIDER_CHOICES else default


def _provider_list(value: Any, *, default: list[str]) -> list[str]:
    if not isinstance(value, list):
        return list(default)
    result: list[str] = []
    for item in value:
        provider = _provider_choice(item, default="")
        if provider and provider not in {"auto", "disabled"} and provider not in result:
            result.append(provider)
    return result or list(default)


def _bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _positive_int(value: Any, default: int) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return default
    return normalized if normalized >= 1 else default


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    return max(min(_positive_int(value, default), maximum), minimum)


def _observation_result_limit(context: WebSearchContext) -> int:
    value = context.metadata.get("observation_result_limit")
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 5


__all__ = [
    "WEB_SEARCH_PROVIDER_CHOICES",
    "WEB_SEARCH_PROVIDER_ORDER",
    "WebSearchRouter",
    "WebSearchRouterConfig",
]
