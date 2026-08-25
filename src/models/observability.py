from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from src.models.credentials import contains_sensitive_key
from src.models.protocol import ModelCallResult, ModelCost


DEFAULT_PREVIEW_CHARS = 240
DEFAULT_ERROR_CHARS = 500
SENSITIVE_LOG_KEYS = frozenset(
    {
        "api_key",
        "token",
        "password",
        "secret",
        "authorization",
        "cookie",
        "set_cookie",
        "client_secret",
        "access_key",
        "refresh_token",
    }
)
_SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|token|password|secret|authorization|cookie|set-cookie|"
    r"client[_-]?secret|access[_-]?key|refresh[_-]?token)\b(\s*[:=]\s*)([^\r\n,;]+)"
)
_BEARER_TOKEN_RE = re.compile(r"(?i)\bBearer\s+[^\s,;]+")


def sanitize_observability_value(value: Any, *, key: str | None = None) -> Any:
    """Recursively redact credential-like fields before they reach developer logs."""
    if key is not None and _is_sensitive_key(key):
        return "***"
    if isinstance(value, Mapping):
        return {
            str(item_key): sanitize_observability_value(item, key=str(item_key))
            for item_key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [sanitize_observability_value(item) for item in value]
    if hasattr(value, "to_dict") and callable(value.to_dict):
        try:
            return sanitize_observability_value(value.to_dict(), key=key)
        except Exception:
            return sanitize_observability_text(str(value))
    if isinstance(value, str):
        return sanitize_observability_text(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return sanitize_observability_text(str(value))


def sanitize_observability_text(value: str) -> str:
    text = str(value or "")
    text = _SENSITIVE_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}***", text)
    return _BEARER_TOKEN_RE.sub("Bearer ***", text)


def estimate_model_cost(
    result: ModelCallResult,
    pricing: Mapping[str, Any] | None,
) -> ModelCost | None:
    """Estimate cost only when provider usage and a matching pricing entry are available."""
    if result.cost is not None or result.usage is None:
        return result.cost

    pricing_entry, pricing_key = _find_pricing_entry(pricing or {}, result)
    if pricing_entry is None:
        return None

    input_rate = _rate_per_thousand(
        pricing_entry,
        per_thousand_keys=(
            "input_per_1k",
            "input_cost_per_1k",
            "input_per_1k_tokens",
            "input_cost_per_1k_tokens",
        ),
        per_million_keys=(
            "input_per_million",
            "input_cost_per_million",
            "input_per_million_tokens",
            "input_cost_per_million_tokens",
        ),
    )
    output_rate = _rate_per_thousand(
        pricing_entry,
        per_thousand_keys=(
            "output_per_1k",
            "output_cost_per_1k",
            "output_per_1k_tokens",
            "output_cost_per_1k_tokens",
        ),
        per_million_keys=(
            "output_per_million",
            "output_cost_per_million",
            "output_per_million_tokens",
            "output_cost_per_million_tokens",
        ),
    )
    if input_rate is None and output_rate is None:
        return None

    input_cost = _token_cost(result.usage.prompt_tokens, input_rate)
    output_cost = _token_cost(result.usage.completion_tokens, output_rate)
    available_costs = [value for value in (input_cost, output_cost) if value is not None]
    if not available_costs:
        return None

    return ModelCost(
        input_cost=input_cost,
        output_cost=output_cost,
        total_cost=sum(available_costs),
        currency=str(pricing_entry.get("currency") or "USD"),
        pricing_source="config",
        metadata={"pricing_key": pricing_key},
    )


class ModelCallLogger:
    """Best-effort JSONL developer logger for Models V1 calls."""

    def __init__(
        self,
        logs_path: str | Path,
        *,
        log_full_prompt: bool = False,
        log_full_response: bool = False,
        preview_chars: int = DEFAULT_PREVIEW_CHARS,
    ):
        self.logs_path = Path(logs_path)
        self.log_full_prompt = bool(log_full_prompt)
        self.log_full_response = bool(log_full_response)
        self.preview_chars = max(int(preview_chars), 1)

    def record_call(
        self,
        result: ModelCallResult,
        *,
        prompt: str,
        messages: Any = None,
    ) -> bool:
        """Write one JSONL record and never leak a logging error to the caller."""
        try:
            record = self.build_record(result, prompt=prompt, messages=messages)
            self.logs_path.parent.mkdir(parents=True, exist_ok=True)
            with self.logs_path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            return True
        except Exception:
            return False

    def build_record(
        self,
        result: ModelCallResult,
        *,
        prompt: str,
        messages: Any = None,
    ) -> dict[str, Any]:
        trace_context = sanitize_observability_value(result.trace_context)
        usage = result.usage
        cost = result.cost
        prompt_fields = self._text_fields(
            prompt,
            include_full=self.log_full_prompt,
            include_preview=self.log_full_prompt,
        )
        response_fields = self._text_fields(
            result.content,
            include_full=self.log_full_response,
            include_preview=True,
        )
        metadata = sanitize_observability_value(result.metadata)

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": "info" if result.success else "error",
            "record_type": "model_call",
            "source_trace_id": result.source_trace_id
            or _trace_field(trace_context, "source_trace_id"),
            "model_request_id": result.model_request_id or result.request_id,
            "provider_request_id": result.provider_request_id,
            "call_type": result.call_type,
            "route": result.route,
            "provider_conf_id": result.provider_conf_id,
            "provider": result.provider,
            "protocol": result.protocol,
            "model": result.model,
            "credential_slug": result.credential_slug,
            "success": result.success,
            "code": result.code,
            "error_summary": _short_text(result.error, DEFAULT_ERROR_CHARS),
            "latency_ms": result.latency_ms,
            "attempts": result.attempts,
            "retry_used": bool(metadata.get("retry_used", False)),
            "fallback_used": bool(result.fallback_used),
            "fallback_reason": result.fallback_reason,
            "prompt_length": prompt_fields["length"],
            "prompt_preview": prompt_fields["preview"],
            "prompt_hash": prompt_fields["hash"],
            "messages_count": _messages_count(messages),
            "response_length": response_fields["length"],
            "response_preview": response_fields["preview"],
            "response_hash": response_fields["hash"],
            "prompt_tokens": usage.prompt_tokens if usage else None,
            "completion_tokens": usage.completion_tokens if usage else None,
            "total_tokens": usage.total_tokens if usage else None,
            "cost": sanitize_observability_value(cost),
            "trace_context": trace_context,
            "metadata": metadata,
        }

    def _text_fields(
        self,
        value: Any,
        *,
        include_full: bool,
        include_preview: bool,
    ) -> dict[str, Any]:
        text = sanitize_observability_text(str(value or ""))
        return {
            "length": len(text),
            "preview": text if include_full else (_short_text(text, self.preview_chars) if include_preview else None),
            "hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }


def _is_sensitive_key(key: str) -> bool:
    normalized = str(key or "").strip().lower().replace("-", "_")
    if normalized in {"api_key_env", "credential_ref"}:
        return False
    return normalized in SENSITIVE_LOG_KEYS or contains_sensitive_key(str(key))


def _short_text(value: Any, max_chars: int) -> str | None:
    if value is None:
        return None
    text = sanitize_observability_text(str(value))
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}..."


def _messages_count(messages: Any) -> int:
    if isinstance(messages, (list, tuple)):
        return len(messages)
    return 0


def _trace_field(trace_context: Any, field_name: str) -> Any:
    if isinstance(trace_context, Mapping):
        return trace_context.get(field_name)
    return None


def _find_pricing_entry(
    pricing: Mapping[str, Any],
    result: ModelCallResult,
) -> tuple[dict[str, Any] | None, str | None]:
    keys = [
        f"{result.provider_conf_id}:{result.model}"
        if result.provider_conf_id and result.model
        else None,
        f"{result.provider}:{result.model}" if result.provider and result.model else None,
        result.model,
        result.provider_conf_id,
        result.provider,
        "default",
    ]
    containers: list[Mapping[str, Any]] = []
    for container_key in ("models", "provider_confs", "providers"):
        container = pricing.get(container_key)
        if isinstance(container, Mapping):
            containers.append(container)
    containers.append(pricing)

    for key in (key for key in keys if key):
        for container in containers:
            entry = container.get(key)
            if isinstance(entry, Mapping):
                return dict(entry), str(key)
    return None, None


def _rate_per_thousand(
    pricing_entry: Mapping[str, Any],
    *,
    per_thousand_keys: tuple[str, ...],
    per_million_keys: tuple[str, ...],
) -> float | None:
    for key in per_thousand_keys:
        value = _optional_float(pricing_entry.get(key))
        if value is not None:
            return value
    for key in per_million_keys:
        value = _optional_float(pricing_entry.get(key))
        if value is not None:
            return value / 1000
    return None


def _token_cost(tokens: int | None, rate_per_thousand: float | None) -> float | None:
    if tokens is None or rate_per_thousand is None:
        return None
    return (float(tokens) / 1000) * rate_per_thousand


def _optional_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "DEFAULT_ERROR_CHARS",
    "DEFAULT_PREVIEW_CHARS",
    "ModelCallLogger",
    "SENSITIVE_LOG_KEYS",
    "estimate_model_cost",
    "sanitize_observability_text",
    "sanitize_observability_value",
]
