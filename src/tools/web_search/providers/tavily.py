from __future__ import annotations

import json
import os
from dataclasses import dataclass
from numbers import Number
from typing import Any, Mapping
from urllib import error as urllib_error
from urllib import request as urllib_request

from ...base import ToolResult
from ...errors import ToolErrorCode
from ..protocol import (
    ProviderSearchResult,
    WebSearchContext,
    WebSearchRequest,
)
from .base import WebSearchProvider


DEFAULT_TAVILY_ENDPOINT = "https://api.tavily.com/search"
DEFAULT_TAVILY_TIMEOUT_SECONDS = 30
DEFAULT_TAVILY_SEARCH_DEPTH = "basic"
DEFAULT_TAVILY_TOPIC = "general"
_TAVILY_SEARCH_DEPTHS = {"basic", "advanced"}
_TAVILY_TOPICS = {"general", "news", "finance"}


@dataclass
class _ResponseLike:
    status_code: int
    body_text: str

    def json(self) -> object:
        return json.loads(self.body_text or "{}")


class TavilySearchProvider(WebSearchProvider):
    provider_id = "tavily"
    provider_type = "search_api"

    def __init__(
        self,
        config: Mapping[str, Any] | None = None,
        *,
        session: Any | None = None,
    ) -> None:
        self.config = dict(config or {})
        self.session = session
        self.enabled = _bool(self.config.get("enabled"), True)
        self.api_key_env = str(self.config.get("api_key_env") or "TAVILY_API_KEY").strip()
        self.endpoint = str(self.config.get("endpoint") or DEFAULT_TAVILY_ENDPOINT).strip()
        self.timeout_seconds = _positive_int(
            self.config.get("timeout_seconds"),
            DEFAULT_TAVILY_TIMEOUT_SECONDS,
        )
        self.default_search_depth = _choice(
            self.config.get("default_search_depth"),
            DEFAULT_TAVILY_SEARCH_DEPTH,
            _TAVILY_SEARCH_DEPTHS,
        )
        self.default_topic = _choice(
            self.config.get("default_topic"),
            DEFAULT_TAVILY_TOPIC,
            _TAVILY_TOPICS,
        )
        self.include_answer_default = _bool(self.config.get("include_answer"), False)
        self.include_raw_content_default = _bool(self.config.get("include_raw_content"), False)

    def is_configured(self) -> bool:
        return self.enabled and bool(self.endpoint) and bool(self._api_key())

    def supports(self, request: WebSearchRequest) -> bool:
        return request.provider in {"auto", "search_api"}

    def dry_run(
        self,
        request: WebSearchRequest,
        context: WebSearchContext,
    ) -> ToolResult:
        data = {
            "query": request.query,
            "provider": self.provider_id,
            "provider_type": self.provider_type,
            "endpoint": self.endpoint,
            "max_results": request.max_results,
            "search_depth": request.search_depth or self.default_search_depth,
            "topic": request.topic or self.default_topic,
            "include_answer": request.include_answer or self.include_answer_default,
            "include_raw_content": request.include_raw_content or self.include_raw_content_default,
            "include_domains": list(request.include_domains),
            "exclude_domains": list(request.exclude_domains),
            "allow_network": context.allow_network,
            "estimated_timeout": context.timeout_seconds or self.timeout_seconds,
        }
        return ToolResult.ok(
            data=data,
            message="Tavily dry-run prepared.",
            provider=self.provider_id,
        )

    def search(
        self,
        request: WebSearchRequest,
        context: WebSearchContext,
    ) -> ToolResult:
        if context.dry_run:
            return self.dry_run(request, context)
        if not self.is_configured():
            return ToolResult.fail(
                "Tavily search_api provider is not configured.",
                code=ToolErrorCode.PROVIDER_NOT_CONFIGURED.value,
                provider=self.provider_id,
            )
        validation_error = _validate_request(request)
        if validation_error is not None:
            return validation_error

        payload = self._build_payload(request)
        headers = {
            "Authorization": f"Bearer {self._api_key()}",
            "Content-Type": "application/json",
        }
        timeout = context.timeout_seconds or self.timeout_seconds
        try:
            response = self._send_request(payload, headers=headers, timeout=timeout)
        except Exception as exc:
            return _map_request_exception(exc, provider=self.provider_id)

        return self._parse_response(response, request=request)

    def _api_key(self) -> str | None:
        if not self.api_key_env:
            return None
        value = os.getenv(self.api_key_env)
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    def _build_payload(self, request: WebSearchRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "query": request.query,
            "max_results": request.max_results,
            "search_depth": request.search_depth or self.default_search_depth,
            "topic": request.topic or self.default_topic,
            "include_answer": bool(request.include_answer),
            "include_raw_content": bool(request.include_raw_content),
        }
        if request.time_range:
            payload["time_range"] = request.time_range
        if request.start_date:
            payload["start_date"] = request.start_date
        if request.end_date:
            payload["end_date"] = request.end_date
        if request.include_domains:
            payload["include_domains"] = list(request.include_domains)
        if request.exclude_domains:
            payload["exclude_domains"] = list(request.exclude_domains)
        return payload

    def _send_request(
        self,
        payload: Mapping[str, Any],
        *,
        headers: Mapping[str, str],
        timeout: int,
    ) -> Any:
        if self.session is not None:
            return self.session.post(
                self.endpoint,
                headers=dict(headers),
                json=dict(payload),
                timeout=timeout,
            )

        request = urllib_request.Request(
            self.endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=dict(headers),
            method="POST",
        )
        try:
            with urllib_request.urlopen(request, timeout=timeout) as response:
                status = int(getattr(response, "status", response.getcode()))
                body = response.read().decode("utf-8", errors="replace")
        except urllib_error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
            return _ResponseLike(status_code=int(getattr(exc, "code", 500) or 500), body_text=body)
        except urllib_error.URLError as exc:
            raise exc
        return _ResponseLike(status_code=status, body_text=body)

    def _parse_response(self, response: Any, *, request: WebSearchRequest) -> ToolResult:
        status_code = _status_code(response)
        if status_code in {401, 403}:
            return ToolResult.fail(
                "Tavily authentication failed.",
                code=ToolErrorCode.PROVIDER_AUTH_FAILED.value,
                provider=self.provider_id,
                retryable=False,
                metadata={"http_status": status_code},
            )
        if status_code == 429:
            return ToolResult.fail(
                "Tavily rate limit exceeded.",
                code=ToolErrorCode.PROVIDER_RATE_LIMITED.value,
                provider=self.provider_id,
                retryable=True,
                metadata={"http_status": status_code},
            )
        if status_code is not None and status_code >= 400:
            return ToolResult.fail(
                f"Tavily request failed with status {status_code}.",
                code=ToolErrorCode.PROVIDER_ERROR.value,
                provider=self.provider_id,
                metadata={"http_status": status_code},
            )

        try:
            raw = response.json()
        except Exception as exc:
            return ToolResult.fail(
                "Tavily response JSON is invalid.",
                code=ToolErrorCode.PROVIDER_RESPONSE_INVALID.value,
                provider=self.provider_id,
                metadata={"error_type": type(exc).__name__, "http_status": status_code},
            )

        if not isinstance(raw, Mapping):
            return ToolResult.fail(
                "Tavily response schema is invalid.",
                code=ToolErrorCode.PROVIDER_RESPONSE_INVALID.value,
                provider=self.provider_id,
            )

        parsed = self._parse_payload(raw, request=request)
        if isinstance(parsed, ToolResult):
            return parsed

        return ToolResult.ok(
            data=parsed.to_web_search_data(request=request),
            message=_success_message(parsed),
            provider=self.provider_id,
            metadata={
                "http_status": status_code,
                "request_id": parsed.provider_request_id,
            },
        )

    def _parse_payload(
        self,
        raw: Mapping[str, Any],
        *,
        request: WebSearchRequest,
    ) -> ProviderSearchResult | ToolResult:
        results_raw = raw.get("results")
        if results_raw is None or not isinstance(results_raw, list):
            return ToolResult.fail(
                "Tavily response schema is invalid.",
                code=ToolErrorCode.PROVIDER_RESPONSE_INVALID.value,
                provider=self.provider_id,
            )

        results: list[dict[str, Any]] = []
        for index, item in enumerate(results_raw, start=1):
            if not isinstance(item, Mapping):
                return ToolResult.fail(
                    "Tavily response schema is invalid.",
                    code=ToolErrorCode.PROVIDER_RESPONSE_INVALID.value,
                    provider=self.provider_id,
                )
            parsed_result = _parse_result_item(
                item,
                index=index,
                include_raw_content=request.include_raw_content,
            )
            if isinstance(parsed_result, ToolResult):
                return parsed_result
            results.append(parsed_result)

        summary = _optional_str(raw.get("answer")) or None
        response_time_ms = _response_time_ms(raw.get("response_time"))
        provider_request_id = _optional_str(raw.get("request_id"))
        usage = raw.get("usage")
        if usage is not None and not isinstance(usage, Mapping):
            return ToolResult.fail(
                "Tavily response schema is invalid.",
                code=ToolErrorCode.PROVIDER_RESPONSE_INVALID.value,
                provider=self.provider_id,
            )

        return ProviderSearchResult(
            query=_optional_str(raw.get("query")) or request.query,
            provider=self.provider_id,
            provider_type=self.provider_type,
            mode=request.mode,
            provider_request_id=provider_request_id,
            answer=summary,
            summary=summary,
            results=results,
            response_time_ms=response_time_ms,
            usage=dict(usage or {}),
            raw_content_included=bool(request.include_raw_content),
            metadata={
                "endpoint": self.endpoint,
                "provider": self.provider_id,
                "provider_type": self.provider_type,
            },
        )


def _validate_request(request: WebSearchRequest) -> ToolResult | None:
    if not isinstance(request.query, str) or not request.query.strip():
        return ToolResult.fail(
            "query must be a non-empty string.",
            code=ToolErrorCode.INVALID_ARGS.value,
            provider="tavily",
        )
    if request.search_depth not in _TAVILY_SEARCH_DEPTHS:
        return ToolResult.fail(
            "search_depth is invalid for Tavily.",
            code=ToolErrorCode.INVALID_ARGS.value,
            provider="tavily",
        )
    if request.topic not in _TAVILY_TOPICS:
        return ToolResult.fail(
            "topic is invalid for Tavily.",
            code=ToolErrorCode.INVALID_ARGS.value,
            provider="tavily",
        )
    if len(request.include_domains) > 10 or len(request.exclude_domains) > 10:
        return ToolResult.fail(
            "domain filters exceed the supported limit.",
            code=ToolErrorCode.INVALID_ARGS.value,
            provider="tavily",
        )
    if not _domains_valid(request.include_domains) or not _domains_valid(request.exclude_domains):
        return ToolResult.fail(
            "domain filters are invalid.",
            code=ToolErrorCode.INVALID_ARGS.value,
            provider="tavily",
        )
    if request.start_date and not _date_like(request.start_date):
        return ToolResult.fail(
            "start_date is invalid.",
            code=ToolErrorCode.INVALID_ARGS.value,
            provider="tavily",
        )
    if request.end_date and not _date_like(request.end_date):
        return ToolResult.fail(
            "end_date is invalid.",
            code=ToolErrorCode.INVALID_ARGS.value,
            provider="tavily",
        )
    if request.time_range and not request.time_range.strip():
        return ToolResult.fail(
            "time_range is invalid.",
            code=ToolErrorCode.INVALID_ARGS.value,
            provider="tavily",
        )
    return None


def _parse_result_item(
    item: Mapping[str, Any],
    *,
    index: int,
    include_raw_content: bool,
) -> dict[str, Any] | ToolResult:
    title = _optional_str(item.get("title"))
    if title is None:
        return ToolResult.fail(
            "Tavily response schema is invalid.",
            code=ToolErrorCode.PROVIDER_RESPONSE_INVALID.value,
            provider="tavily",
        )
    images = item.get("images")
    if images is not None and not isinstance(images, list):
        return ToolResult.fail(
            "Tavily response schema is invalid.",
            code=ToolErrorCode.PROVIDER_RESPONSE_INVALID.value,
            provider="tavily",
        )
    return {
        "title": title,
        "url": _optional_str(item.get("url")),
        "snippet": _optional_str(item.get("snippet")) or _optional_str(item.get("content")),
        "content": _optional_str(item.get("content")),
        "score": _optional_float(item.get("score")),
        "rank": _optional_int(item.get("rank"), default=index),
        "source": _optional_str(item.get("source")),
        "published_at": _optional_str(item.get("published_at")),
        "favicon": _optional_str(item.get("favicon")),
        "images": list(images or []),
        "raw_content": _optional_str(item.get("raw_content")) if include_raw_content else None,
    }


def _map_request_exception(exc: Exception, *, provider: str) -> ToolResult:
    if isinstance(exc, urllib_error.HTTPError):
        status = _status_from_exception(exc)
        if status in {401, 403}:
            return ToolResult.fail(
                "Tavily authentication failed.",
                code=ToolErrorCode.PROVIDER_AUTH_FAILED.value,
                provider=provider,
            )
        if status == 429:
            return ToolResult.fail(
                "Tavily rate limit exceeded.",
                code=ToolErrorCode.PROVIDER_RATE_LIMITED.value,
                provider=provider,
                retryable=True,
            )
        return ToolResult.fail(
            "Tavily request failed.",
            code=ToolErrorCode.PROVIDER_ERROR.value,
            provider=provider,
            metadata={"http_status": status},
        )
    if isinstance(exc, urllib_error.URLError):
        reason = getattr(exc, "reason", None)
        if isinstance(reason, TimeoutError) or "timed out" in str(reason).lower():
            return ToolResult.fail(
                "Tavily request timed out.",
                code=ToolErrorCode.PROVIDER_TIMEOUT.value,
                provider=provider,
                retryable=True,
            )
        return ToolResult.fail(
            "Tavily request failed.",
            code=ToolErrorCode.PROVIDER_ERROR.value,
            provider=provider,
        )
    if isinstance(exc, TimeoutError):
        return ToolResult.fail(
            "Tavily request timed out.",
            code=ToolErrorCode.PROVIDER_TIMEOUT.value,
            provider=provider,
            retryable=True,
        )
    return ToolResult.fail(
        "Tavily request failed.",
        code=ToolErrorCode.PROVIDER_ERROR.value,
        provider=provider,
        metadata={"error_type": type(exc).__name__},
    )


def _status_code(response: Any) -> int | None:
    if response is None:
        return None
    value = getattr(response, "status_code", None)
    if value is None:
        value = getattr(response, "status", None)
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def _status_from_exception(exc: BaseException) -> int | None:
    value = getattr(exc, "code", None)
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def _success_message(result: ProviderSearchResult) -> str:
    count = len(result.results)
    if result.summary:
        return f"Tavily search returned {count} result(s) with summary."
    return f"Tavily search returned {count} result(s)."


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _positive_int(value: Any, default: int) -> int:
    normalized = _optional_int(value, default=default)
    return normalized if normalized >= 1 else default


def _bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _choice(value: Any, default: str, allowed: set[str]) -> str:
    normalized = _optional_str(value)
    if normalized is None:
        return default
    lowered = normalized.lower()
    return lowered if lowered in allowed else default


def _domains_valid(values: list[str]) -> bool:
    for value in values:
        candidate = str(value).strip().lower()
        if not candidate:
            return False
        if "://" in candidate or "/" in candidate or candidate.startswith("."):
            return False
    return True


def _date_like(value: str) -> bool:
    text = str(value).strip()
    if not text:
        return False
    parts = text.split("-")
    return len(parts) == 3 and all(part.isdigit() for part in parts)


def _response_time_ms(value: Any) -> int | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric < 0:
        return None
    if isinstance(value, Number) and isinstance(value, int) and float(value) >= 1000:
        return int(round(float(value)))
    return int(round(numeric * 1000))


__all__ = [
    "DEFAULT_TAVILY_ENDPOINT",
    "DEFAULT_TAVILY_SEARCH_DEPTH",
    "DEFAULT_TAVILY_TIMEOUT_SECONDS",
    "DEFAULT_TAVILY_TOPIC",
    "TavilySearchProvider",
]
