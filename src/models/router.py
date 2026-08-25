from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.models.config import ModelsConfig, ProviderConf, ProviderSpec, RouteCandidate
from src.models.errors import (
    RETRYABLE_MODEL_ERROR_CODES,
    ModelErrorCode,
    normalize_model_error_code,
)
from src.models.protocol import ModelCallOptions, ModelCallType, ModelMessage


ROUTE_PARAMETER_NAMES = frozenset(
    {
        "temperature",
        "top_p",
        "top_k",
        "max_tokens",
        "json_mode",
        "timeout_seconds",
        "max_retries",
    }
)


def make_candidate_key(
    route: str,
    provider_conf_id: str,
    credential_slug: str | None,
    model: str | None,
) -> tuple[str, str, str, str]:
    return (
        str(route or "").strip().lower(),
        str(provider_conf_id or "").strip().lower(),
        str(credential_slug or "default").strip().lower(),
        str(model or "").strip(),
    )


@dataclass
class CandidateHealthState:
    consecutive_failures: int = 0
    cooldown_until: float = 0.0
    circuit_open_until: float = 0.0
    last_error_code: str | None = None
    last_error_at: str | None = None
    success_count: int = 0

    def active(self, now: float) -> bool:
        return self.cooldown_until > now or self.circuit_open_until > now


class CandidateHealthRegistry:
    """Small in-memory health registry used by route selection and fallback."""

    def __init__(
        self,
        *,
        credential_cooldown_seconds: float = 60.0,
        model_cooldown_seconds: float = 60.0,
        candidate_cooldown_seconds: float = 5.0,
        circuit_failure_threshold: int = 3,
        circuit_open_seconds: float = 30.0,
    ):
        self.credential_cooldown_seconds = max(float(credential_cooldown_seconds), 0.0)
        self.model_cooldown_seconds = max(float(model_cooldown_seconds), 0.0)
        self.candidate_cooldown_seconds = max(float(candidate_cooldown_seconds), 0.0)
        self.circuit_failure_threshold = max(int(circuit_failure_threshold), 1)
        self.circuit_open_seconds = max(float(circuit_open_seconds), 0.0)
        self._states: dict[tuple[str, str, str, str, str], CandidateHealthState] = {}

    def is_available(
        self,
        route: str,
        provider_conf_id: str,
        credential_slug: str | None,
        model: str | None,
        *,
        configured_cooldown_until: str | None = None,
    ) -> bool:
        if self._configured_cooldown_active(configured_cooldown_until):
            return False
        now = time.monotonic()
        keys = self._related_keys(route, provider_conf_id, credential_slug, model)
        return not any(self._states.get(key, CandidateHealthState()).active(now) for key in keys)

    def record_failure(
        self,
        route: str,
        provider_conf_id: str,
        credential_slug: str | None,
        model: str | None,
        code: str | ModelErrorCode | None,
    ) -> None:
        normalized_code = normalize_model_error_code(code)
        now = time.monotonic()
        timestamp = datetime.now(timezone.utc).isoformat()
        if normalized_code in {
            ModelErrorCode.AUTHENTICATION_FAILED.value,
            ModelErrorCode.PERMISSION_DENIED.value,
            ModelErrorCode.MISSING_API_KEY.value,
            ModelErrorCode.QUOTA_EXCEEDED.value,
        }:
            self._cooldown(
                self._key("credential", route, provider_conf_id, credential_slug, None),
                now + self.credential_cooldown_seconds,
                normalized_code,
                timestamp,
            )
        elif normalized_code == ModelErrorCode.MODEL_NOT_FOUND.value:
            self._cooldown(
                self._key("model", route, provider_conf_id, None, model),
                now + self.model_cooldown_seconds,
                normalized_code,
                timestamp,
            )
        else:
            candidate_key = self._key(
                "candidate",
                route,
                provider_conf_id,
                credential_slug,
                model,
            )
            candidate_state = self._states.setdefault(candidate_key, CandidateHealthState())
            candidate_state.consecutive_failures += 1
            candidate_state.last_error_code = normalized_code
            candidate_state.last_error_at = timestamp
            if normalized_code in RETRYABLE_MODEL_ERROR_CODES:
                candidate_state.cooldown_until = max(
                    candidate_state.cooldown_until,
                    now + self.candidate_cooldown_seconds,
                )
            if candidate_state.consecutive_failures >= self.circuit_failure_threshold:
                candidate_state.circuit_open_until = max(
                    candidate_state.circuit_open_until,
                    now + self.circuit_open_seconds,
                )

    def record_success(
        self,
        route: str,
        provider_conf_id: str,
        credential_slug: str | None,
        model: str | None,
    ) -> None:
        for key in self._related_keys(route, provider_conf_id, credential_slug, model):
            state = self._states.get(key)
            if state is None:
                continue
            state.consecutive_failures = 0
            state.cooldown_until = 0.0
            state.circuit_open_until = 0.0
            state.success_count += 1
            state.last_error_code = None
            state.last_error_at = None

    def snapshot(
        self,
        route: str,
        provider_conf_id: str,
        credential_slug: str | None,
        model: str | None,
    ) -> dict[str, Any]:
        now = time.monotonic()
        result: dict[str, Any] = {}
        for scope, key in (
            ("candidate", self._key("candidate", route, provider_conf_id, credential_slug, model)),
            ("credential", self._key("credential", route, provider_conf_id, credential_slug, None)),
            ("model", self._key("model", route, provider_conf_id, None, model)),
        ):
            state = self._states.get(key)
            if state is None:
                continue
            result[scope] = {
                "consecutive_failures": state.consecutive_failures,
                "cooldown_active": state.cooldown_until > now,
                "circuit_open": state.circuit_open_until > now,
                "last_error_code": state.last_error_code,
                "last_error_at": state.last_error_at,
                "success_count": state.success_count,
            }
        return result

    def _related_keys(
        self,
        route: str,
        provider_conf_id: str,
        credential_slug: str | None,
        model: str | None,
    ) -> list[tuple[str, str, str, str, str]]:
        return [
            self._key("candidate", route, provider_conf_id, credential_slug, model),
            self._key("credential", route, provider_conf_id, credential_slug, None),
            self._key("model", route, provider_conf_id, None, model),
        ]

    def _key(
        self,
        scope: str,
        route: str,
        provider_conf_id: str,
        credential_slug: str | None,
        model: str | None,
    ) -> tuple[str, str, str, str, str]:
        candidate = make_candidate_key(route, provider_conf_id, credential_slug, model)
        return (scope, *candidate)

    def _cooldown(
        self,
        key: tuple[str, str, str, str, str],
        until: float,
        code: str,
        timestamp: str,
    ) -> None:
        state = self._states.setdefault(key, CandidateHealthState())
        state.cooldown_until = max(state.cooldown_until, until)
        state.last_error_code = code
        state.last_error_at = timestamp

    def _configured_cooldown_active(self, value: str | None) -> bool:
        if not value:
            return False
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return False
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed > datetime.now(timezone.utc)


@dataclass
class RouteResolution:
    """The deterministic route and parameters selected for one model call."""

    success: bool
    route: str | None = None
    policy: str | None = None
    provider_conf_id: str | None = None
    credential_slug: str | None = None
    provider: str | None = None
    protocol: str | None = None
    model: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    selected_candidate: dict[str, Any] | None = None
    unsupported_params: list[str] = field(default_factory=list)
    max_context_tokens: int | None = None
    max_context_chars: int | None = None
    context_chars: int | None = None
    context_tokens: int | None = None
    code: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def fail(cls, code: str, error: str, **kwargs: Any) -> "RouteResolution":
        return cls(success=False, code=code, error=error, **kwargs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "route": self.route,
            "policy": self.policy,
            "provider_conf_id": self.provider_conf_id,
            "credential_slug": self.credential_slug,
            "provider": self.provider,
            "protocol": self.protocol,
            "model": self.model,
            "params": dict(self.params),
            "selected_candidate": self.selected_candidate,
            "unsupported_params": list(self.unsupported_params),
            "max_context_tokens": self.max_context_tokens,
            "max_context_chars": self.max_context_chars,
            "context_chars": self.context_chars,
            "context_tokens": self.context_tokens,
            "code": self.code,
            "error": self.error,
            "metadata": dict(self.metadata),
        }


class ModelRouter:
    """Resolve one explicit route without doing retry or fallback."""

    def __init__(
        self,
        models_config: ModelsConfig,
        *,
        health_registry: CandidateHealthRegistry | None = None,
    ):
        self.models_config = models_config
        self.health_registry = health_registry

    def resolve(
        self,
        options: ModelCallOptions,
        *,
        default_provider_conf_id: str | None = None,
        default_provider: str | None = None,
        excluded_candidate_keys: set[tuple[str, str, str, str]] | None = None,
    ) -> RouteResolution:
        route = self._resolve_route(options)
        route_config = self.models_config.get_route(route)
        if route_config is None:
            return RouteResolution.fail(
                ModelErrorCode.MISSING_MODEL_CONFIG.value,
                f"model route is not configured: {route}",
                route=route,
            )

        explicit_provider_conf_id = options.provider_conf_id
        provider_conf_id = explicit_provider_conf_id or default_provider_conf_id
        candidate: RouteCandidate | None = None

        if explicit_provider_conf_id:
            provider_conf = self.models_config.get_provider_conf(explicit_provider_conf_id)
            if provider_conf is None:
                return RouteResolution.fail(
                    ModelErrorCode.MISSING_MODEL_CONFIG.value,
                    f"provider config not found: {explicit_provider_conf_id}",
                    route=route,
                )
        elif route_config.default_model_policy == "explicit_candidates":
            candidate = self._first_enabled_candidate(
                route,
                route_config.candidates,
                excluded_candidate_keys=excluded_candidate_keys,
            )
            if candidate is not None:
                provider_conf_id = candidate.provider_conf_id

        provider_conf = (
            self.models_config.get_provider_conf(provider_conf_id)
            if provider_conf_id
            else None
        )
        provider = provider_conf.provider if provider_conf else (default_provider or "mock")
        spec = self.models_config.get_provider_spec(provider)

        if provider_conf_id and provider_conf is None:
            return RouteResolution.fail(
                ModelErrorCode.MISSING_MODEL_CONFIG.value,
                f"provider config not found: {provider_conf_id}",
                route=route,
                provider_conf_id=provider_conf_id,
            )
        if spec is None:
            return RouteResolution.fail(
                ModelErrorCode.UNSUPPORTED_PROVIDER.value,
                f"unsupported model provider: {provider}",
                route=route,
                provider_conf_id=provider_conf_id,
                provider=provider,
            )

        credential_slug = options.credential_slug
        if credential_slug is None and candidate is not None:
            credential_slug = candidate.credential_slug
        if credential_slug is None and provider_conf is not None:
            credential_slug = self._default_credential_slug(provider_conf)

        model = options.model
        if model is None and candidate is not None:
            model = candidate.model
        if model is None and provider_conf is not None:
            model = provider_conf.model_id or provider_conf.default_model
        if model is None:
            model = spec.default_model

        params, unsupported = self._merge_params(
            options,
            route_config.params,
            provider_conf,
            spec,
        )
        context_limits = self._context_limits(provider_conf, spec, model)
        context_chars, context_tokens = self._context_size(options)
        metadata = {
            "route_policy": route_config.default_model_policy,
            "unsupported_params": list(unsupported),
            "context_chars": context_chars,
            "context_tokens": context_tokens,
        }
        if candidate is not None:
            metadata["candidate_key"] = make_candidate_key(
                route,
                candidate.provider_conf_id,
                credential_slug,
                model,
            )
        if context_limits[0] is not None and context_chars > context_limits[0]:
            metadata.update(
                {
                    "context_limit_type": "chars",
                    "context_limit": context_limits[0],
                    "truncation_used": False,
                }
            )
            if not options.allow_truncation:
                return RouteResolution.fail(
                    ModelErrorCode.CONTEXT_LENGTH_EXCEEDED.value,
                    "model prompt exceeds configured context character limit",
                    route=route,
                    policy=route_config.default_model_policy,
                    provider_conf_id=provider_conf_id,
                    credential_slug=credential_slug,
                    provider=provider,
                    protocol=spec.protocol,
                    model=model,
                    params=params,
                    selected_candidate=candidate.to_dict() if candidate else None,
                    unsupported_params=unsupported,
                    max_context_chars=context_limits[0],
                    max_context_tokens=context_limits[1],
                    context_chars=context_chars,
                    context_tokens=context_tokens,
                    metadata=metadata,
                )
        if context_limits[1] is not None and context_tokens > context_limits[1]:
            metadata.update(
                {
                    "context_limit_type": "tokens",
                    "context_limit": context_limits[1],
                    "truncation_used": False,
                }
            )
            if not options.allow_truncation:
                return RouteResolution.fail(
                    ModelErrorCode.CONTEXT_LENGTH_EXCEEDED.value,
                    "model prompt exceeds configured context token limit",
                    route=route,
                    policy=route_config.default_model_policy,
                    provider_conf_id=provider_conf_id,
                    credential_slug=credential_slug,
                    provider=provider,
                    protocol=spec.protocol,
                    model=model,
                    params=params,
                    selected_candidate=candidate.to_dict() if candidate else None,
                    unsupported_params=unsupported,
                    max_context_chars=context_limits[0],
                    max_context_tokens=context_limits[1],
                    context_chars=context_chars,
                    context_tokens=context_tokens,
                    metadata=metadata,
                )
        if options.allow_truncation and (
            (context_limits[0] is not None and context_chars > context_limits[0])
            or (context_limits[1] is not None and context_tokens > context_limits[1])
        ):
            metadata["truncation_requested"] = True

        return RouteResolution(
            success=True,
            route=route,
            policy=route_config.default_model_policy,
            provider_conf_id=provider_conf_id,
            credential_slug=credential_slug,
            provider=provider,
            protocol=spec.protocol,
            model=model,
            params=params,
            selected_candidate=candidate.to_dict() if candidate else None,
            unsupported_params=unsupported,
            max_context_tokens=context_limits[1],
            max_context_chars=context_limits[0],
            context_chars=context_chars,
            context_tokens=context_tokens,
            metadata=metadata,
        )

    def _resolve_route(self, options: ModelCallOptions) -> str:
        if options.route:
            return str(options.route).strip().lower()
        call_type = str(options.call_type)
        if self.models_config.get_route(call_type) is not None:
            return call_type
        defaults = self.models_config.default_routes()
        if call_type == ModelCallType.EMBEDDING.value:
            return defaults["embedding"]
        if call_type == ModelCallType.CONTEXT_COMPRESSION.value:
            return defaults["context_compression"]
        return defaults["chat"]

    def _first_enabled_candidate(
        self,
        route: str,
        candidates: list[RouteCandidate],
        *,
        excluded_candidate_keys: set[tuple[str, str, str, str]] | None = None,
    ) -> RouteCandidate | None:
        excluded = excluded_candidate_keys or set()
        enabled = [
            item
            for item in candidates
            if item.enabled
            and self.models_config.get_provider_conf(item.provider_conf_id) is not None
            and self.models_config.get_provider_conf(item.provider_conf_id).enabled
            and make_candidate_key(
                route,
                item.provider_conf_id,
                item.credential_slug,
                item.model
                or (
                    self.models_config.get_provider_conf(item.provider_conf_id).model_id
                    or self.models_config.get_provider_conf(item.provider_conf_id).default_model
                ),
            )
            not in excluded
        ]
        if self.health_registry is not None:
            enabled = [
                item
                for item in enabled
                if self.health_registry.is_available(
                    route,
                    item.provider_conf_id,
                    item.credential_slug,
                    item.model
                    or (
                        self.models_config.get_provider_conf(item.provider_conf_id).model_id
                        or self.models_config.get_provider_conf(item.provider_conf_id).default_model
                    ),
                    configured_cooldown_until=item.cooldown_until,
                )
            ]
        if not enabled:
            return None
        return sorted(
            enabled,
            key=lambda item: (
                item.priority if item.priority is not None else 0,
                -(item.weight if item.weight is not None else 0),
                item.provider_conf_id,
            ),
        )[0]

    def _merge_params(
        self,
        options: ModelCallOptions,
        route_params: dict[str, Any],
        provider_conf: ProviderConf | None,
        spec: ProviderSpec,
    ) -> tuple[dict[str, Any], list[str]]:
        params = dict(route_params or {})
        provider_defaults = {
            "temperature": provider_conf.temperature if provider_conf else None,
            "top_p": provider_conf.top_p if provider_conf else None,
            "max_tokens": provider_conf.max_tokens if provider_conf else None,
            "timeout_seconds": provider_conf.timeout_seconds if provider_conf else None,
            "max_retries": provider_conf.max_retries if provider_conf else None,
        }
        for key, value in provider_defaults.items():
            if value is not None and key not in params:
                params[key] = value
        if "timeout_seconds" not in params:
            params["timeout_seconds"] = spec.default_timeout_seconds
        if "max_retries" not in params:
            params["max_retries"] = spec.default_max_retries

        explicit = {
            key: getattr(options, key)
            for key in ROUTE_PARAMETER_NAMES
            if getattr(options, key, None) is not None
        }
        params.update(explicit)

        unsupported: list[str] = []
        if "top_p" in params and not spec.supports_top_p:
            unsupported.append("top_p")
            params.pop("top_p", None)
        if "top_k" in params and not spec.supports_top_k:
            unsupported.append("top_k")
            params.pop("top_k", None)
        if params.get("json_mode") and not spec.supports_json_mode:
            unsupported.append("json_mode")
            params.pop("json_mode", None)
        return {key: params[key] for key in ROUTE_PARAMETER_NAMES if key in params}, unsupported

    def _context_limits(
        self,
        provider_conf: ProviderConf | None,
        spec: ProviderSpec,
        model: str | None,
    ) -> tuple[int | None, int | None]:
        values: list[dict[str, Any]] = []
        if provider_conf is not None:
            values.append(provider_conf.metadata)
        values.append(spec.metadata)
        chars = provider_conf.max_context_chars if provider_conf else None
        tokens = provider_conf.max_context_tokens if provider_conf else None
        chars = chars or spec.max_context_chars
        tokens = tokens or spec.max_context_tokens
        for metadata in values:
            model_limits = metadata.get("model_limits") if isinstance(metadata, dict) else None
            if isinstance(model_limits, dict) and model and isinstance(model_limits.get(model), dict):
                model_limit = model_limits[model]
                chars = chars or self._positive_int(model_limit.get("max_context_chars"))
                tokens = tokens or self._positive_int(model_limit.get("max_context_tokens"))
            chars = chars or self._positive_int(metadata.get("max_context_chars"))
            tokens = tokens or self._positive_int(metadata.get("max_context_tokens"))
        return chars, tokens

    def _context_size(self, options: ModelCallOptions) -> tuple[int, int]:
        messages = options.to_messages()
        chars = sum(len(str(message.content or "")) for message in messages)
        return chars, max(0, math.ceil(chars / 4))

    def _default_credential_slug(self, provider_conf: ProviderConf) -> str | None:
        for credential in provider_conf.credentials:
            if credential.enabled:
                return credential.slug
        return provider_conf.credentials[0].slug if provider_conf.credentials else None

    def _positive_int(self, value: Any) -> int | None:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None


__all__ = [
    "CandidateHealthRegistry",
    "CandidateHealthState",
    "ModelRouter",
    "RouteResolution",
    "ROUTE_PARAMETER_NAMES",
    "make_candidate_key",
]
