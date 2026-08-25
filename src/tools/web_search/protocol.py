from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from typing import Any, Mapping

from ..base import _json_safe
from ..data_types import (
    WEB_SEARCH_EVIDENCE_LEVELS,
    WEB_SEARCH_SOURCE_QUALITIES,
    WebSearchData,
    WebSearchResult,
    normalize_web_search_evidence_level,
    normalize_web_search_source_quality,
)
from ..protocol import ToolCallContext, ToolCallOptions, ToolCallRequest


WEB_SEARCH_PROVIDER_TYPES = frozenset(
    {
        "search_api",
        "model_builtin",
        "fake",
        "disabled",
    }
)
WEB_SEARCH_ROUTE_PROVIDERS = frozenset(WEB_SEARCH_PROVIDER_TYPES | {"auto"})
WEB_SEARCH_DEPTHS = frozenset({"basic", "advanced"})
WEB_SEARCH_TOPICS = frozenset({"general", "news", "finance"})

_WEB_SEARCH_DATA_FIELDS = {field_info.name for field_info in fields(WebSearchData)}
_WEB_SEARCH_RESULT_FIELDS = {field_info.name for field_info in fields(WebSearchResult)}


@dataclass
class WebSearchRequest:
    query: str
    max_results: int = 5
    search_depth: str = "basic"
    topic: str | None = "general"
    include_answer: bool = False
    include_raw_content: bool = False
    provider: str = "auto"
    mode: str = "search"
    time_range: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    include_domains: list[str] = field(default_factory=list)
    exclude_domains: list[str] = field(default_factory=list)
    observation_mode: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.query = _required_text(self.query, "query")
        self.max_results = _int_between(self.max_results, "max_results", minimum=1, maximum=20)
        self.search_depth = _choice(
            self.search_depth,
            "search_depth",
            choices=WEB_SEARCH_DEPTHS,
            default="basic",
        )
        self.topic = _optional_choice(
            self.topic,
            "topic",
            choices=WEB_SEARCH_TOPICS,
            default="general",
        )
        self.provider = _choice(
            self.provider,
            "provider",
            choices=WEB_SEARCH_ROUTE_PROVIDERS,
            default="auto",
        )
        self.mode = _required_text(self.mode, "mode")
        self.time_range = _optional_text(self.time_range)
        self.start_date = _optional_text(self.start_date)
        self.end_date = _optional_text(self.end_date)
        self.include_domains = _string_list(self.include_domains)
        self.exclude_domains = _string_list(self.exclude_domains)
        self.observation_mode = _optional_text(self.observation_mode)
        self.include_answer = bool(self.include_answer)
        self.include_raw_content = bool(self.include_raw_content)
        self.metadata = dict(self.metadata or {})

    @classmethod
    def from_args(cls, args: Mapping[str, Any] | None = None) -> "WebSearchRequest":
        return cls(**dict(args or {}))

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))


@dataclass
class WebSearchContext:
    trace_id: str | None = None
    execution_id: str | None = None
    step_id: str | None = None
    workspace_root: str | None = None
    allow_network: bool = False
    dry_run: bool = False
    timeout_seconds: int | None = 30
    observation_mode: str | None = None
    max_output_chars: int | None = None
    max_observation_chars: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.trace_id = _optional_text(self.trace_id)
        self.execution_id = _optional_text(self.execution_id)
        self.step_id = _optional_text(self.step_id)
        self.workspace_root = _optional_text(self.workspace_root)
        self.allow_network = bool(self.allow_network)
        self.dry_run = bool(self.dry_run)
        if self.timeout_seconds is not None:
            self.timeout_seconds = _int_between(
                self.timeout_seconds,
                "timeout_seconds",
                minimum=1,
                maximum=3600,
            )
        self.observation_mode = _optional_text(self.observation_mode)
        if self.max_output_chars is not None:
            self.max_output_chars = max(int(self.max_output_chars), 0)
        if self.max_observation_chars is not None:
            self.max_observation_chars = max(int(self.max_observation_chars), 0)
        self.metadata = dict(self.metadata or {})

    @classmethod
    def from_tool_call(
        cls,
        request: ToolCallRequest,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> "WebSearchContext":
        context: ToolCallContext = request.context
        options: ToolCallOptions = request.options
        merged_metadata = dict(metadata or {})
        return cls(
            trace_id=context.trace_id,
            execution_id=context.execution_id,
            step_id=context.step_id,
            workspace_root=context.workspace_root,
            allow_network=options.allow_network,
            dry_run=options.dry_run,
            timeout_seconds=options.timeout_seconds or 30,
            observation_mode=options.observation_mode,
            max_output_chars=options.max_output_chars,
            max_observation_chars=options.max_observation_chars,
            metadata=merged_metadata,
        )

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))


@dataclass
class ProviderSearchResult:
    query: str = ""
    provider: str = ""
    provider_type: str = ""
    mode: str = "search"
    provider_request_id: str | None = None
    retrieved_at: str | None = None
    search_depth: str | None = None
    topic: str | None = None
    answer: str | None = None
    summary: str | None = None
    results: list[Any] = field(default_factory=list)
    evidence_level: str | None = None
    source_quality: str | None = None
    response_time_ms: int | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    raw_content_included: bool = False
    truncated: bool = False
    cache_key: str | None = None
    cache_hit: bool | None = None
    cache_age_seconds: float | None = None
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    raw_provider_response: Any = None

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))

    def to_web_search_data(
        self,
        *,
        request: WebSearchRequest | None = None,
    ) -> WebSearchData:
        return normalize_web_search_data(self, request=request)


def normalize_web_search_data(
    provider_result: ProviderSearchResult | WebSearchData | Mapping[str, Any] | None = None,
    *,
    request: WebSearchRequest | None = None,
    provider: str | None = None,
    provider_type: str | None = None,
    mode: str | None = None,
    query: str | None = None,
    provider_request_id: str | None = None,
    retrieved_at: str | None = None,
    search_depth: str | None = None,
    topic: str | None = None,
    answer: str | None = None,
    summary: str | None = None,
    results: list[Any] | None = None,
    evidence_level: str | None = None,
    source_quality: str | None = None,
    response_time_ms: int | None = None,
    usage: Mapping[str, Any] | None = None,
    raw_content_included: bool | None = None,
    truncated: bool | None = None,
    cache_key: str | None = None,
    cache_hit: bool | None = None,
    cache_age_seconds: float | None = None,
    warnings: list[Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
    raw_provider_response: Any = None,
) -> WebSearchData:
    if isinstance(provider_result, WebSearchData):
        base = provider_result.to_dict()
        raw_extras: dict[str, Any] = {}
    elif isinstance(provider_result, ProviderSearchResult):
        base = provider_result.to_dict()
        raw_extras = {}
    elif isinstance(provider_result, Mapping):
        base = dict(provider_result)
        raw_extras = {
            key: value
            for key, value in base.items()
            if key not in _WEB_SEARCH_DATA_FIELDS and key != "raw_provider_response"
        }
    else:
        base = {}
        raw_extras = {}

    overrides = {
        "query": query,
        "provider": provider,
        "provider_type": provider_type,
        "mode": mode,
        "provider_request_id": provider_request_id,
        "retrieved_at": retrieved_at,
        "search_depth": search_depth,
        "topic": topic,
        "answer": answer,
        "summary": summary,
        "results": results,
        "evidence_level": evidence_level,
        "source_quality": source_quality,
        "response_time_ms": response_time_ms,
        "usage": dict(usage) if usage is not None else None,
        "raw_content_included": raw_content_included,
        "truncated": truncated,
        "cache_key": cache_key,
        "cache_hit": cache_hit,
        "cache_age_seconds": cache_age_seconds,
        "warnings": warnings,
        "metadata": dict(metadata) if metadata is not None else None,
    }
    for key, value in overrides.items():
        if value is not None:
            base[key] = value

    if request is not None:
        base.setdefault("query", request.query)
        base.setdefault("mode", request.mode)
        base.setdefault("search_depth", request.search_depth)
        base.setdefault("topic", request.topic)
        if not base.get("provider_type") and request.provider and request.provider != "auto":
            base["provider_type"] = request.provider
        base.setdefault("raw_content_included", request.include_raw_content)

    base.setdefault("query", "")
    base.setdefault("provider", "")
    base.setdefault("provider_type", "")
    base.setdefault("mode", "search")
    base.setdefault("retrieved_at", _utc_now_iso())

    normalized_results, raw_result_extras = _normalize_result_list(base.get("results"))
    base["results"] = normalized_results

    normalized_metadata = dict(base.get("metadata") or {})
    raw_from_result = base.get("raw_provider_response", None)
    if raw_provider_response is not None:
        raw_from_result = raw_provider_response
    if raw_extras:
        normalized_metadata.setdefault("provider_raw", {}).update(raw_extras)
    if raw_result_extras:
        normalized_metadata.setdefault("provider_raw_results", raw_result_extras)
    if raw_from_result is not None:
        normalized_metadata.setdefault("provider_raw_response", raw_from_result)
    base["metadata"] = normalized_metadata

    filtered = {
        key: value
        for key, value in base.items()
        if key in _WEB_SEARCH_DATA_FIELDS and key != "result_count"
    }
    data = WebSearchData(**filtered)
    data.evidence_level = _validated_evidence_level(data)
    data.source_quality = _validated_source_quality(data)
    return data


def _normalize_result_list(value: Any) -> tuple[list[WebSearchResult], list[dict[str, Any]]]:
    if value is None:
        return [], []
    if not isinstance(value, list):
        value = [value]

    results: list[WebSearchResult] = []
    raw_extras: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if isinstance(item, WebSearchResult):
            results.append(item)
            continue
        if isinstance(item, Mapping):
            item_dict = dict(item)
            filtered = {
                key: field_value
                for key, field_value in item_dict.items()
                if key in _WEB_SEARCH_RESULT_FIELDS
            }
            extras = {
                key: field_value
                for key, field_value in item_dict.items()
                if key not in _WEB_SEARCH_RESULT_FIELDS
            }
            if extras:
                raw_extras.append({"index": index, "fields": extras})
            results.append(WebSearchResult(**filtered))
            continue
        results.append(WebSearchResult(title=str(item or "")))
    return results, raw_extras


def _validated_evidence_level(data: WebSearchData) -> str:
    if data.evidence_level not in WEB_SEARCH_EVIDENCE_LEVELS:
        return normalize_web_search_evidence_level(data.evidence_level)
    if not data.results and (data.summary or data.answer):
        return "no_url_summary"
    if data.provider_type == "model_builtin" and any(result.url for result in data.results):
        return "model_reported"
    return data.evidence_level


def _validated_source_quality(data: WebSearchData) -> str:
    if data.source_quality not in WEB_SEARCH_SOURCE_QUALITIES:
        return normalize_web_search_source_quality(data.source_quality)
    if not data.results and (data.summary or data.answer):
        return "summary_only"
    if not data.results:
        return "empty"
    if data.provider_type == "model_builtin":
        return "partial_sources"
    if all(result.url for result in data.results):
        return "verified_sources"
    return "partial_sources"


def _required_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} must be a non-empty string")
    return text


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _choice(value: Any, field_name: str, *, choices: frozenset[str], default: str) -> str:
    normalized = str(value or default).strip().lower()
    if normalized not in choices:
        raise ValueError(f"Unsupported {field_name}: {value}")
    return normalized


def _optional_choice(
    value: Any,
    field_name: str,
    *,
    choices: frozenset[str],
    default: str,
) -> str | None:
    if value is None:
        return None
    return _choice(value, field_name, choices=choices, default=default)


def _int_between(value: Any, field_name: str, *, minimum: int, maximum: int) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer") from exc
    if normalized < minimum or normalized > maximum:
        raise ValueError(f"{field_name} must be between {minimum} and {maximum}")
    return normalized


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        candidates = [value]
    else:
        candidates = list(value)
    return [str(item).strip() for item in candidates if str(item).strip()]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "WEB_SEARCH_DEPTHS",
    "WEB_SEARCH_EVIDENCE_LEVELS",
    "WEB_SEARCH_PROVIDER_TYPES",
    "WEB_SEARCH_ROUTE_PROVIDERS",
    "WEB_SEARCH_SOURCE_QUALITIES",
    "WEB_SEARCH_TOPICS",
    "ProviderSearchResult",
    "WebSearchContext",
    "WebSearchData",
    "WebSearchRequest",
    "WebSearchResult",
    "normalize_web_search_data",
]
