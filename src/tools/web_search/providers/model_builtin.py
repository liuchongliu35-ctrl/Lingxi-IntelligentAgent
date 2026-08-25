from __future__ import annotations

from typing import Any, Mapping

from ...base import ToolResult
from ...errors import ToolErrorCode
from ..protocol import (
    ProviderSearchResult,
    WebSearchContext,
    WebSearchRequest,
    normalize_web_search_data,
)
from ..model_search_schema import (
    MODEL_SEARCH_PROMPT_VERSION,
    MODEL_SEARCH_SCHEMA,
    MODEL_SEARCH_SCHEMA_VERSION,
    build_model_search_prompt,
)
from .base import WebSearchProvider


class ModelBuiltinSearchProvider(WebSearchProvider):
    """Search provider backed exclusively by the project Models facade."""

    provider_id = "model_builtin"
    provider_type = "model_builtin"

    def __init__(
        self,
        config: Mapping[str, Any] | None = None,
        *,
        model_manager: Any | None = None,
    ) -> None:
        self.config = dict(config or {})
        self.enabled = _bool(self.config.get("enabled"), False)
        self.provider_conf_id = _optional_text(self.config.get("provider_conf_id"))
        self.model_route = _optional_text(self.config.get("model_route")) or "web_search"
        self.model = _optional_text(self.config.get("model"))
        self.enable_web_search = _bool(self.config.get("enable_web_search"), True)
        self.timeout_seconds = _positive_int(self.config.get("timeout_seconds"), 30)
        self._model_manager = model_manager
        self._manager_error: str | None = None

    def is_configured(self) -> bool:
        # ModelManager is intentionally lazy so importing Tools never creates
        # a real provider client or reads a provider secret.
        return self.enabled and self.enable_web_search

    def supports(self, request: WebSearchRequest) -> bool:
        return request.provider in {"auto", "model_builtin"}

    def dry_run(
        self,
        request: WebSearchRequest,
        context: WebSearchContext,
    ) -> ToolResult:
        return ToolResult.ok(
            data={
                "query": request.query,
                "provider": self.provider_id,
                "provider_type": self.provider_type,
                "provider_conf_id": self.provider_conf_id,
                "model_route": self.model_route,
                "model": self.model,
                "max_results": request.max_results,
                "search_depth": request.search_depth,
                "topic": request.topic,
                "include_answer": request.include_answer,
                "include_raw_content": request.include_raw_content,
                "enable_web_search": self.enable_web_search,
                "allow_network": context.allow_network,
                "estimated_timeout": context.timeout_seconds or self.timeout_seconds,
            },
            message="model_builtin web_search dry-run prepared.",
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
                "model_builtin web_search provider is not configured.",
                code=ToolErrorCode.PROVIDER_NOT_CONFIGURED.value,
                provider=self.provider_id,
            )
        manager = self._get_model_manager()
        if manager is None:
            return ToolResult.fail(
                self._manager_error or "Models service is not configured.",
                code=ToolErrorCode.PROVIDER_NOT_CONFIGURED.value,
                provider=self.provider_id,
            )

        prompt = build_model_search_prompt(
            query=request.query,
            max_results=request.max_results,
            search_depth=request.search_depth,
            topic=request.topic,
            include_answer=request.include_answer,
            time_range=request.time_range,
            start_date=request.start_date,
            end_date=request.end_date,
            include_domains=list(request.include_domains),
            exclude_domains=list(request.exclude_domains),
        )
        metadata = {
            "enable_web_search": self.enable_web_search,
            "web_search_options": {
                "search_depth": request.search_depth,
                "topic": request.topic or "general",
            },
            "schema_version": MODEL_SEARCH_SCHEMA_VERSION,
            "prompt_version": MODEL_SEARCH_PROMPT_VERSION,
            "tools_provider": self.provider_id,
        }
        try:
            structured = manager.generate_json(
                prompt,
                call_type="web_search",
                route=self.model_route,
                provider_conf_id=self.provider_conf_id,
                model=self.model,
                timeout_seconds=context.timeout_seconds or self.timeout_seconds,
                schema_name="web_search",
                schema=MODEL_SEARCH_SCHEMA,
                parse_mode="strict",
                metadata=metadata,
            )
        except Exception as exc:
            return ToolResult.fail(
                "Models web_search call failed.",
                code=ToolErrorCode.PROVIDER_ERROR.value,
                provider=self.provider_id,
                metadata={"error_type": type(exc).__name__},
            )

        if not getattr(structured, "success", False):
            return self._structured_failure(structured)
        data = getattr(structured, "data", None)
        validation_error = _validate_model_payload(data)
        if validation_error is not None:
            return ToolResult.fail(
                validation_error,
                code=ToolErrorCode.MODEL_SEARCH_SCHEMA_INVALID.value,
                provider=self.provider_id,
                metadata=_model_metadata(structured, metadata),
            )
        if not data.get("results") and not str(data.get("summary") or "").strip():
            return ToolResult.fail(
                "Model web_search returned neither sources nor a summary.",
                code=ToolErrorCode.MODEL_SEARCH_NO_SOURCES.value,
                provider=self.provider_id,
                metadata=_model_metadata(structured, metadata),
            )

        result = ProviderSearchResult(
            query=request.query,
            provider=self.provider_id,
            provider_type=self.provider_type,
            mode=request.mode,
            search_depth=request.search_depth,
            topic=request.topic,
            summary=str(data.get("summary") or "").strip() or None,
            results=list(data.get("results") or []),
            evidence_level=str(data.get("evidence_level") or ""),
            source_quality=str(data.get("source_quality") or ""),
            usage=_usage_dict(structured),
            metadata=_model_metadata(structured, metadata),
        )
        normalized = normalize_web_search_data(result, request=request)
        return ToolResult.ok(
            data=normalized,
            message=(
                "Model builtin search returned a summary without auditable URLs."
                if normalized.evidence_level == "no_url_summary"
                else f"Model builtin search returned {normalized.result_count} result(s)."
            ),
            provider=self.provider_id,
            metadata=_model_metadata(structured, metadata),
        )

    def _get_model_manager(self) -> Any | None:
        if self._model_manager is not None:
            return self._model_manager
        try:
            from src.models import ModelManager

            self._model_manager = ModelManager(provider_conf_id=self.provider_conf_id)
            return self._model_manager
        except Exception as exc:
            self._manager_error = f"Models service initialization failed: {type(exc).__name__}"
            return None

    def _structured_failure(self, structured: Any) -> ToolResult:
        model_result = getattr(structured, "model_result", None)
        model_code = str(getattr(structured, "code", None) or getattr(model_result, "code", "") or "")
        if model_code in {"invalid_json", "json_repair_failed"}:
            code = ToolErrorCode.MODEL_SEARCH_PARSE_FAILED.value
        elif model_code == "schema_invalid":
            code = ToolErrorCode.MODEL_SEARCH_SCHEMA_INVALID.value
        elif model_code == "missing_model_config":
            code = ToolErrorCode.PROVIDER_NOT_CONFIGURED.value
        elif model_code == "timeout":
            code = ToolErrorCode.PROVIDER_TIMEOUT.value
        elif model_code == "rate_limited":
            code = ToolErrorCode.PROVIDER_RATE_LIMITED.value
        else:
            code = ToolErrorCode.PROVIDER_ERROR.value
        return ToolResult.fail(
            "Models web_search call failed.",
            code=code,
            provider=self.provider_id,
            retryable=bool(getattr(model_result, "retriable", False)),
            metadata={
                "model_error_code": model_code,
                "model_error": str(getattr(structured, "error", None) or getattr(model_result, "error", "")),
                "model_request_id": getattr(model_result, "model_request_id", None),
            },
        )


def _validate_model_payload(data: Any) -> str | None:
    if not isinstance(data, dict):
        return "model web_search output must be a JSON object"
    if not isinstance(data.get("query"), str) or not data["query"].strip():
        return "model web_search output query must be a non-empty string"
    if not isinstance(data.get("summary"), str):
        return "model web_search output summary must be a string"
    if not isinstance(data.get("results"), list):
        return "model web_search output results must be an array"
    for index, item in enumerate(data["results"]):
        if not isinstance(item, dict):
            return f"model web_search result {index} must be an object"
        if not isinstance(item.get("title"), str) or not item["title"].strip():
            return f"model web_search result {index} title must be a non-empty string"
        for field_name in ("url", "snippet", "source", "published_at"):
            value = item.get(field_name)
            if value is not None and not isinstance(value, str):
                return f"model web_search result {index} {field_name} must be a string or null"
    return None


def _model_metadata(structured: Any, base: Mapping[str, Any]) -> dict[str, Any]:
    model_result = getattr(structured, "model_result", None)
    metadata = dict(base)
    metadata.update(
        {
            "model_request_id": getattr(model_result, "model_request_id", None),
            "model_provider_request_id": getattr(model_result, "provider_request_id", None),
            "model_provider": getattr(model_result, "provider", None),
            "model": getattr(model_result, "model", None),
            "model_route": getattr(model_result, "route", None),
            "model_trace_id": getattr(model_result, "source_trace_id", None),
        }
    )
    return {key: value for key, value in metadata.items() if value is not None}


def _usage_dict(structured: Any) -> dict[str, Any]:
    model_result = getattr(structured, "model_result", None)
    usage = getattr(model_result, "usage", None)
    if usage is None:
        return {}
    if hasattr(usage, "to_dict"):
        return dict(usage.to_dict())
    return dict(usage) if isinstance(usage, dict) else {}


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


__all__ = ["ModelBuiltinSearchProvider"]
