from __future__ import annotations

import json
from dataclasses import replace
from typing import Any
from urllib.parse import SplitResult, urlsplit, urlunsplit

from ..base import _json_safe
from ..data_types import (
    WebSearchData,
    WebSearchResult,
    normalize_web_search_evidence_level,
    normalize_web_search_source_quality,
)
from ..output_control import DEFAULT_MAX_OBSERVATION_CHARS, DEFAULT_MAX_OUTPUT_CHARS, truncate_text


WEB_SEARCH_NORMALIZATION_VERSION = "web_search.normalization.v1"
DEFAULT_OBSERVATION_RESULT_LIMIT = 5
DEFAULT_RESULT_TITLE_CHARS = 512
DEFAULT_RESULT_SNIPPET_CHARS = 2400
DEFAULT_RESULT_CONTENT_CHARS = 6000
DEFAULT_RESULT_SOURCE_CHARS = 256
DEFAULT_RESULT_DATE_CHARS = 128
_EVIDENCE_LEVELS = ("url_verified", "provider_reported", "model_reported", "no_url_summary")


def normalize_url_for_dedup(value: Any) -> str | None:
    """Return a stable HTTP(S) URL form without fetching or verifying it."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = urlsplit(text)
    except ValueError:
        return text
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return text
    try:
        hostname = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError:
        return text
    if not hostname:
        return text
    netloc = hostname
    if port is not None and not (
        (parsed.scheme.lower() == "http" and port == 80)
        or (parsed.scheme.lower() == "https" and port == 443)
    ):
        netloc = f"{netloc}:{port}"
    path = parsed.path or "/"
    return urlunsplit(
        SplitResult(
            parsed.scheme.lower(),
            netloc,
            path,
            parsed.query,
            "",
        )
    )


def normalize_web_search_evidence(
    data: WebSearchData,
    *,
    max_results: int | None = None,
    max_output_chars: int | None = DEFAULT_MAX_OUTPUT_CHARS,
    max_observation_chars: int | None = DEFAULT_MAX_OBSERVATION_CHARS,
    observation_result_limit: int = DEFAULT_OBSERVATION_RESULT_LIMIT,
    code: str | None = "ok",
) -> WebSearchData:
    """Deduplicate, bound and annotate one normalized search response.

    This function is deliberately local and deterministic. It never performs
    URL requests, model summarization, reranking, or cache lookup.
    """
    original_count = len(data.results)
    bounded_results = _limit_result_count(data.results, max_results)
    normalized_results, duplicates_removed = _deduplicate_results(
        bounded_results,
        provider_type=data.provider_type,
    )
    normalized_results, field_truncated = _truncate_results(normalized_results)
    data.results = normalized_results
    data.result_count = len(normalized_results)
    data.evidence_level = _normalized_evidence_level(data)
    data.source_quality = _normalized_source_quality(data)

    warnings = list(data.warnings)
    if duplicates_removed:
        warnings.append("duplicate_urls_removed")
    if len(bounded_results) < original_count:
        warnings.append("max_results_applied")
    if field_truncated:
        warnings.append("result_fields_truncated")
    data.warnings = _unique_strings(warnings)

    normalization_metadata = dict(data.metadata.get("normalization") or {})
    normalization_metadata.update(
        {
            "version": WEB_SEARCH_NORMALIZATION_VERSION,
            "original_result_count": original_count,
            "normalized_result_count": len(normalized_results),
            "duplicates_removed": duplicates_removed,
            "observation_result_limit": max(int(observation_result_limit), 0),
        }
    )
    data.metadata["normalization"] = normalization_metadata

    if max_output_chars is not None:
        _fit_data_to_output_limit(data, max(int(max_output_chars), 0))
    data.metadata["observation_views"] = build_web_search_observation_views(
        data,
        max_chars=max_observation_chars,
        result_limit=observation_result_limit,
        code=code,
    )
    return data


def build_web_search_observation_views(
    data: WebSearchData,
    *,
    max_chars: int | None = DEFAULT_MAX_OBSERVATION_CHARS,
    result_limit: int = DEFAULT_OBSERVATION_RESULT_LIMIT,
    code: str | None = "ok",
) -> dict[str, dict[str, Any]]:
    """Build candidate views; ReActExecutor still owns final ObservationPacket."""
    limit = max(int(result_limit), 0)
    minimal = {
        "provider": data.provider,
        "provider_type": data.provider_type,
        "query": data.query,
        "result_count": data.result_count,
        "evidence_level": data.evidence_level,
        "code": code,
    }
    if data.truncated:
        minimal["truncated"] = True
    standard = {
        **minimal,
        "summary": data.summary,
        "answer": data.answer,
        "source_quality": data.source_quality,
        "results": [
            _result_view(result, include_content=False)
            for result in data.results[:limit]
        ],
    }
    full = {
        **standard,
        "results": [
            _result_view(result, include_content=True)
            for result in data.results
        ],
    }
    return {
        "minimal_data": _fit_view(minimal, max_chars),
        "standard_data": _fit_view(standard, max_chars),
        "full_data": _fit_view(full, max_chars),
    }


def _deduplicate_results(
    results: list[WebSearchResult],
    *,
    provider_type: str,
) -> tuple[list[WebSearchResult], int]:
    positions: dict[str, int] = {}
    combined: list[WebSearchResult] = []
    duplicates_removed = 0
    for result in results:
        normalized_url = normalize_url_for_dedup(result.url)
        normalized = replace(
            result,
            url=normalized_url,
            evidence_level=_result_evidence(result, provider_type=provider_type, url=normalized_url),
        )
        if normalized_url is None:
            combined.append(normalized)
            continue
        key = normalized_url.casefold()
        position = positions.get(key)
        if position is None:
            positions[key] = len(combined)
            combined.append(normalized)
            continue
        combined[position] = _merge_results(
            combined[position],
            normalized,
            provider_type=provider_type,
        )
        duplicates_removed += 1

    ranked = [
        replace(result, rank=index)
        for index, result in enumerate(combined, start=1)
    ]
    return ranked, duplicates_removed


def _merge_results(
    first: WebSearchResult,
    second: WebSearchResult,
    *,
    provider_type: str,
) -> WebSearchResult:
    return WebSearchResult(
        title=first.title or second.title,
        url=first.url or second.url,
        snippet=first.snippet or second.snippet,
        content=first.content or second.content,
        score=_max_number(first.score, second.score),
        rank=_min_number(first.rank, second.rank),
        source=first.source or second.source,
        published_at=first.published_at or second.published_at,
        favicon=first.favicon or second.favicon,
        images=first.images or second.images,
        raw_content=first.raw_content or second.raw_content,
        evidence_level=_result_evidence(
            first,
            provider_type=provider_type,
            url=first.url or second.url,
            other=second,
        ),
    )


def _result_evidence(
    result: WebSearchResult,
    *,
    provider_type: str,
    url: str | None,
    other: WebSearchResult | None = None,
) -> str:
    if not url:
        return "provider_reported"
    candidates = [result.evidence_level]
    if other is not None:
        candidates.append(other.evidence_level)
    if "model_reported" in candidates or provider_type == "model_builtin":
        return "model_reported"
    if "provider_reported" in candidates:
        return "provider_reported"
    if "no_url_summary" in candidates:
        return "provider_reported"
    if provider_type in {"search_api", "fake"}:
        return "url_verified"
    return normalize_web_search_evidence_level(result.evidence_level, default="provider_reported")


def _normalized_evidence_level(data: WebSearchData) -> str:
    if not data.results and (data.summary or data.answer):
        return "no_url_summary"
    if not data.results:
        return normalize_web_search_evidence_level(data.evidence_level)
    if data.provider_type == "model_builtin" and any(item.url for item in data.results):
        return "model_reported"
    if any(item.url for item in data.results):
        return normalize_web_search_evidence_level(data.evidence_level, default="url_verified")
    return "provider_reported"


def _normalized_source_quality(data: WebSearchData) -> str:
    if not data.results:
        return "summary_only" if (data.summary or data.answer) else "empty"
    if data.provider_type == "model_builtin":
        return "partial_sources"
    if all(item.url for item in data.results):
        return "verified_sources"
    return "partial_sources"


def _truncate_results(results: list[WebSearchResult]) -> tuple[list[WebSearchResult], bool]:
    truncated = False
    normalized: list[WebSearchResult] = []
    for result in results:
        fields = {
            "title": (result.title, DEFAULT_RESULT_TITLE_CHARS),
            "snippet": (result.snippet, DEFAULT_RESULT_SNIPPET_CHARS),
            "content": (result.content, DEFAULT_RESULT_CONTENT_CHARS),
            "raw_content": (result.raw_content, DEFAULT_RESULT_CONTENT_CHARS),
            "source": (result.source, DEFAULT_RESULT_SOURCE_CHARS),
            "published_at": (result.published_at, DEFAULT_RESULT_DATE_CHARS),
            "favicon": (result.favicon, DEFAULT_RESULT_SOURCE_CHARS),
        }
        values: dict[str, Any] = {}
        for field_name, (value, limit) in fields.items():
            shortened = truncate_text(value, limit) if value is not None else None
            if value is not None and shortened != value:
                truncated = True
            values[field_name] = shortened
        normalized.append(replace(result, **values))
    return normalized, truncated


def _fit_data_to_output_limit(data: WebSearchData, limit: int) -> None:
    if limit <= 0:
        data.results = []
        data.result_count = 0
        data.summary = None
        data.answer = None
        data.truncated = True
        data.warnings = _unique_strings([*data.warnings, "max_output_chars_applied"])
        return
    while data.results and _serialized_chars(data) > limit:
        data.results.pop()
        data.result_count = len(data.results)
        data.truncated = True
    if _serialized_chars(data) > limit:
        for field_name in ("raw_content", "content", "snippet"):
            for index, result in enumerate(data.results):
                value = getattr(result, field_name)
                if value:
                    shortened = truncate_text(value, max(len(value) // 2, 1))
                    data.results[index] = replace(result, **{field_name: shortened})
                    data.truncated = True
                    if _serialized_chars(data) <= limit:
                        break
            if _serialized_chars(data) <= limit:
                break
    if data.truncated:
        data.evidence_level = _normalized_evidence_level(data)
        data.source_quality = _normalized_source_quality(data)
        data.warnings = _unique_strings([*data.warnings, "max_output_chars_applied"])
        data.metadata["normalization"]["output_limit_chars"] = limit
        data.metadata["normalization"]["output_limit_used"] = True


def _fit_view(value: dict[str, Any], max_chars: int | None) -> dict[str, Any]:
    if max_chars is None or max_chars <= 0:
        return value
    if _json_chars(value) <= max_chars:
        return value
    compact = dict(value)
    results = list(compact.get("results") or [])
    compact["truncated"] = True
    while results and _json_chars({**compact, "results": results}) > max_chars:
        results.pop()
    compact["results"] = results
    for field_name in ("summary", "answer"):
        if _json_chars(compact) <= max_chars:
            break
        if compact.get(field_name):
            compact[field_name] = truncate_text(compact[field_name], max(max_chars // 4, 1))
    if _json_chars(compact) > max_chars:
        compact = {
            key: value
            for key, value in compact.items()
            if key not in {"summary", "answer", "results"}
        }
        compact["truncated"] = True
    return compact


def _result_view(result: WebSearchResult, *, include_content: bool) -> dict[str, Any]:
    value = {
        "title": result.title,
        "url": result.url,
        "snippet": result.snippet,
        "rank": result.rank,
        "source": result.source,
        "published_at": result.published_at,
        "evidence_level": result.evidence_level,
    }
    if include_content:
        value.update(
            {
                "content": result.content,
                "score": result.score,
                "favicon": result.favicon,
                "images": result.images,
            }
        )
    # raw_content is intentionally excluded from every Observation candidate.
    return value


def _limit_result_count(results: list[WebSearchResult], max_results: int | None) -> list[WebSearchResult]:
    if max_results is None:
        return list(results)
    return list(results[: max(int(max_results), 0)])


def _serialized_chars(data: WebSearchData) -> int:
    return _json_chars(data.to_dict())


def _json_chars(value: Any) -> int:
    return len(json.dumps(_json_safe(value), ensure_ascii=False, separators=(",", ":"), default=str))


def _max_number(first: float | None, second: float | None) -> float | None:
    if first is None:
        return second
    if second is None:
        return first
    return max(first, second)


def _min_number(first: int | None, second: int | None) -> int | None:
    if first is None:
        return second
    if second is None:
        return first
    return min(first, second)


def _unique_strings(values: list[Any]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value)
        if text and text not in result:
            result.append(text)
    return result


__all__ = [
    "DEFAULT_OBSERVATION_RESULT_LIMIT",
    "WEB_SEARCH_NORMALIZATION_VERSION",
    "build_web_search_observation_views",
    "normalize_url_for_dedup",
    "normalize_web_search_evidence",
]
