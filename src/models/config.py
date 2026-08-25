from __future__ import annotations

import copy
import json
import os
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping

from src.models.credentials import find_plain_secret_keys
from src.models.protocol import MODEL_CALL_TYPES


class ModelProviderProtocol(str, Enum):
    OPENAI_COMPATIBLE = "openai-compatible"
    ANTHROPIC_COMPATIBLE = "anthropic-compatible"
    GEMINI_COMPATIBLE = "gemini-compatible"
    CUSTOM_MAPPING = "custom-mapping"
    MOCK = "mock"


SUPPORTED_MODEL_PROTOCOLS = frozenset(protocol.value for protocol in ModelProviderProtocol)
SUPPORTED_ROUTE_POLICIES = frozenset({"user_selected", "explicit_candidates"})
CONFIG_FILE_ORDER = (
    "provider_specs.json",
    "provider_confs.json",
    "routes.json",
    "models_config.json",
    "pricing.json",
    "structured_output.json",
)


class ModelsConfigErrorCode(str, Enum):
    INVALID_JSON = "invalid_json"
    INVALID_VALUE = "invalid_value"
    UNSUPPORTED_PROVIDER = "unsupported_provider"
    UNSUPPORTED_PROTOCOL = "unsupported_protocol"
    DUPLICATE_PROVIDER_CONF_ID = "duplicate_provider_conf_id"
    DUPLICATE_CREDENTIAL_SLUG = "duplicate_credential_slug"
    DUPLICATE_PROVIDER_SPEC = "duplicate_provider_spec"
    INVALID_ROUTE_CANDIDATE = "invalid_route_candidate"
    PLAIN_SECRET_IN_CONFIG = "plain_secret_in_config"
    UNKNOWN_ERROR = "unknown_error"


MODELS_CONFIG_ERROR_CODES = frozenset(error.value for error in ModelsConfigErrorCode)


def _json_safe(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _copy_metadata(value: dict[str, Any] | None) -> dict[str, Any]:
    return dict(value or {})


def _normalize_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, Enum):
        value = value.value
    text = str(value).strip()
    return text or None


def _normalize_required_text(value: Any, field_name: str) -> str:
    text = _normalize_optional_text(value)
    if text is None:
        raise ModelsConfigError(
            ModelsConfigErrorCode.INVALID_VALUE,
            f"{field_name} is required",
            details={"field": field_name},
        )
    return text


def _normalize_identifier(value: Any, field_name: str) -> str:
    return _normalize_required_text(value, field_name).strip().lower()


def _normalize_protocol(value: Any) -> str:
    protocol = _normalize_identifier(value, "protocol")
    if protocol not in SUPPORTED_MODEL_PROTOCOLS:
        raise ModelsConfigError(
            ModelsConfigErrorCode.UNSUPPORTED_PROTOCOL,
            f"unsupported model protocol: {protocol}",
            details={"protocol": protocol},
        )
    return protocol


def _normalize_int(value: Any, default: int, minimum: int = 0) -> int:
    if value is None:
        return default
    try:
        return max(int(value), minimum)
    except (TypeError, ValueError):
        return default


def _normalize_float(value: Any, default: float, minimum: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return max(float(value), minimum)
    except (TypeError, ValueError):
        return default


def _normalize_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _normalize_string_list(value: Iterable[Any] | None) -> list[str]:
    return [str(item).strip() for item in value or [] if str(item).strip()]


def _dataclass_kwargs(model_type: type, item: dict[str, Any]) -> dict[str, Any]:
    allowed = {field.name for field in fields(model_type)}
    values = dict(item)
    metadata = _copy_metadata(values.get("metadata"))
    for key in list(values):
        if key in allowed:
            continue
        metadata.setdefault(key, values.pop(key))
    if metadata and "metadata" in allowed:
        values["metadata"] = metadata
    return values


def normalize_models_config_error_code(value: str | ModelsConfigErrorCode | None) -> str:
    if isinstance(value, ModelsConfigErrorCode):
        return value.value
    normalized = str(value or "").strip().lower()
    if normalized in MODELS_CONFIG_ERROR_CODES:
        return normalized
    return ModelsConfigErrorCode.UNKNOWN_ERROR.value


@dataclass
class ModelsConfigError(Exception):
    code: str | ModelsConfigErrorCode
    message: str
    path: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.code = normalize_models_config_error_code(self.code)
        self.details = _copy_metadata(self.details)
        Exception.__init__(self, self.message)

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(self)


@dataclass
class ProviderSpec:
    provider: str
    protocol: str | ModelProviderProtocol
    display_name: str
    default_base_url: str | None = None
    default_model: str | None = None
    supports_streaming: bool = False
    supports_json_mode: bool = False
    supports_tool_calling: bool = False
    supports_embedding: bool = False
    supports_vision: bool = False
    supports_custom_headers: bool = False
    supports_top_p: bool = False
    supports_top_k: bool = False
    default_timeout_seconds: float = 60.0
    default_max_retries: int = 5
    max_context_tokens: int | None = None
    max_context_chars: int | None = None
    request_adapter: str | None = None
    response_adapter: str | None = None
    known_model_prefixes: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.provider = _normalize_identifier(self.provider, "provider")
        self.protocol = _normalize_protocol(self.protocol)
        self.display_name = _normalize_required_text(self.display_name, "display_name")
        self.default_base_url = _normalize_optional_text(self.default_base_url)
        self.default_model = _normalize_optional_text(self.default_model)
        self.supports_streaming = bool(self.supports_streaming)
        self.supports_json_mode = bool(self.supports_json_mode)
        self.supports_tool_calling = bool(self.supports_tool_calling)
        self.supports_embedding = bool(self.supports_embedding)
        self.supports_vision = bool(self.supports_vision)
        self.supports_custom_headers = bool(self.supports_custom_headers)
        self.supports_top_p = bool(self.supports_top_p)
        self.supports_top_k = bool(self.supports_top_k)
        self.default_timeout_seconds = _normalize_float(self.default_timeout_seconds, 60.0)
        self.default_max_retries = _normalize_int(self.default_max_retries, 5)
        self.max_context_tokens = (
            None if self.max_context_tokens is None else _normalize_int(self.max_context_tokens, 0)
        )
        self.max_context_chars = (
            None if self.max_context_chars is None else _normalize_int(self.max_context_chars, 0)
        )
        self.request_adapter = _normalize_optional_text(self.request_adapter)
        self.response_adapter = _normalize_optional_text(self.response_adapter)
        self.known_model_prefixes = _normalize_string_list(self.known_model_prefixes)
        self.tags = _normalize_string_list(self.tags)
        self.metadata = _copy_metadata(self.metadata)

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(self)


@dataclass
class ProviderCredential:
    slug: str = "default"
    api_key_env: str | None = None
    credential_ref: str | None = None
    enabled: bool = True
    status: str = "unverified"
    last_error_code: str | None = None
    last_error_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.slug = _normalize_identifier(self.slug, "credential_slug")
        self.api_key_env = _normalize_optional_text(self.api_key_env)
        self.credential_ref = _normalize_optional_text(self.credential_ref)
        self.enabled = bool(self.enabled)
        self.status = _normalize_identifier(self.status, "credential_status")
        self.last_error_code = _normalize_optional_text(self.last_error_code)
        self.last_error_at = _normalize_optional_text(self.last_error_at)
        self.metadata = _copy_metadata(self.metadata)

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(self)


@dataclass
class ProviderConf:
    id: str
    name: str
    provider: str
    protocol: str | ModelProviderProtocol
    enabled: bool = False
    base_url: str | None = None
    default_model: str | None = None
    model_id: str | None = None
    custom_models: list[str] = field(default_factory=list)
    credentials: list[ProviderCredential | dict[str, Any]] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)
    timeout_seconds: float | None = None
    max_retries: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    max_context_tokens: int | None = None
    max_context_chars: int | None = None
    status: str = "unverified"
    verified_at: str | None = None
    last_used_at: str | None = None
    verify: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.id = _normalize_identifier(self.id, "provider_conf_id")
        self.name = _normalize_required_text(self.name, "name")
        self.provider = _normalize_identifier(self.provider, "provider")
        self.protocol = _normalize_protocol(self.protocol)
        self.enabled = bool(self.enabled)
        self.base_url = _normalize_optional_text(self.base_url)
        self.default_model = _normalize_optional_text(self.default_model)
        self.model_id = _normalize_optional_text(self.model_id)
        if self.default_model is None:
            self.default_model = self.model_id
        if self.model_id is None:
            self.model_id = self.default_model
        self.custom_models = _normalize_string_list(self.custom_models)
        self.credentials = [
            item
            if isinstance(item, ProviderCredential)
            else ProviderCredential(**_dataclass_kwargs(ProviderCredential, item))
            for item in self.credentials
        ]
        self.headers = {str(key): str(value) for key, value in dict(self.headers or {}).items()}
        self.timeout_seconds = (
            None if self.timeout_seconds is None else _normalize_float(self.timeout_seconds, 0.0)
        )
        self.max_retries = None if self.max_retries is None else _normalize_int(self.max_retries, 0)
        self.temperature = None if self.temperature is None else float(self.temperature)
        self.top_p = None if self.top_p is None else float(self.top_p)
        self.max_tokens = None if self.max_tokens is None else _normalize_int(self.max_tokens, 0)
        self.max_context_tokens = (
            None if self.max_context_tokens is None else _normalize_int(self.max_context_tokens, 0)
        )
        self.max_context_chars = (
            None if self.max_context_chars is None else _normalize_int(self.max_context_chars, 0)
        )
        self.status = _normalize_identifier(self.status, "provider_status")
        self.verified_at = _normalize_optional_text(self.verified_at)
        self.last_used_at = _normalize_optional_text(self.last_used_at)
        self.verify = _copy_metadata(self.verify)
        self.tags = _normalize_string_list(self.tags)
        self.metadata = _copy_metadata(self.metadata)
        _ensure_unique_credential_slugs(self)

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(self)


@dataclass
class RouteCandidate:
    provider_conf_id: str
    credential_slug: str = "default"
    model: str | None = None
    model_id: str | None = None
    weight: int | None = None
    priority: int | None = None
    enabled: bool = True
    cooldown_until: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.provider_conf_id = _normalize_identifier(self.provider_conf_id, "provider_conf_id")
        self.credential_slug = _normalize_identifier(self.credential_slug, "credential_slug")
        self.model = _normalize_optional_text(self.model)
        self.model_id = _normalize_optional_text(self.model_id)
        if self.model is None:
            self.model = self.model_id
        if self.model_id is None:
            self.model_id = self.model
        self.weight = None if self.weight is None else _normalize_int(self.weight, 0)
        self.priority = None if self.priority is None else _normalize_int(self.priority, 0)
        self.enabled = bool(self.enabled)
        self.cooldown_until = _normalize_optional_text(self.cooldown_until)
        self.metadata = _copy_metadata(self.metadata)

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(self)


@dataclass
class RouteConfig:
    route: str
    default_model_policy: str = "user_selected"
    params: dict[str, Any] = field(default_factory=dict)
    candidates: list[RouteCandidate | dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.route = _normalize_identifier(self.route, "route")
        self.default_model_policy = _normalize_identifier(
            self.default_model_policy,
            "default_model_policy",
        )
        if self.default_model_policy not in SUPPORTED_ROUTE_POLICIES:
            raise ModelsConfigError(
                ModelsConfigErrorCode.INVALID_VALUE,
                f"unsupported default_model_policy: {self.default_model_policy}",
                details={"route": self.route, "default_model_policy": self.default_model_policy},
            )
        self.params = _copy_metadata(self.params)
        self.candidates = [
            item if isinstance(item, RouteCandidate) else RouteCandidate(**_dataclass_kwargs(RouteCandidate, item))
            for item in self.candidates
        ]
        self.metadata = _copy_metadata(self.metadata)

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(self)


@dataclass
class ModelsRuntimeConfig:
    default_chat_route: str = "chat"
    default_structured_route: str = "structured"
    default_embedding_route: str = "embedding"
    default_compression_route: str = "context_compression"
    default_mock_enabled: bool = True
    real_provider_enabled_by_default: bool = False
    max_retries: int = 5
    retry_backoff_base_seconds: float = 0.5
    retry_backoff_max_seconds: float = 8.0
    logs_path: Path | str = "logs/models.log"
    log_full_prompt: bool = False
    log_full_response: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.default_chat_route = _normalize_identifier(self.default_chat_route, "default_chat_route")
        self.default_structured_route = _normalize_identifier(
            self.default_structured_route,
            "default_structured_route",
        )
        self.default_embedding_route = _normalize_identifier(
            self.default_embedding_route,
            "default_embedding_route",
        )
        self.default_compression_route = _normalize_identifier(
            self.default_compression_route,
            "default_compression_route",
        )
        self.default_mock_enabled = bool(self.default_mock_enabled)
        self.real_provider_enabled_by_default = bool(self.real_provider_enabled_by_default)
        self.max_retries = _normalize_int(self.max_retries, 5)
        self.retry_backoff_base_seconds = _normalize_float(self.retry_backoff_base_seconds, 0.5)
        self.retry_backoff_max_seconds = _normalize_float(self.retry_backoff_max_seconds, 8.0)
        self.log_full_prompt = bool(self.log_full_prompt)
        self.log_full_response = bool(self.log_full_response)
        self.metadata = _copy_metadata(self.metadata)

    def with_resolved_paths(self, root: Path) -> "ModelsRuntimeConfig":
        clone = copy.deepcopy(self)
        logs_path = Path(clone.logs_path)
        if not logs_path.is_absolute():
            logs_path = root / logs_path
        clone.logs_path = logs_path.resolve()
        return clone

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(self)


@dataclass
class ModelsConfig:
    workspace_root: Path
    config_dir: Path
    runtime: ModelsRuntimeConfig
    provider_specs: dict[str, ProviderSpec] = field(default_factory=dict)
    provider_confs: dict[str, ProviderConf] = field(default_factory=dict)
    routes: dict[str, RouteConfig] = field(default_factory=dict)
    pricing: dict[str, Any] = field(default_factory=dict)
    structured_output: dict[str, Any] = field(default_factory=dict)
    loaded_files: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.workspace_root = Path(self.workspace_root).resolve()
        self.config_dir = Path(self.config_dir).resolve()
        self.runtime = self.runtime.with_resolved_paths(self.workspace_root)
        self.provider_specs = dict(self.provider_specs)
        self.provider_confs = dict(self.provider_confs)
        self.routes = dict(self.routes)
        self.pricing = _copy_metadata(self.pricing)
        self.structured_output = _copy_metadata(self.structured_output)
        self.loaded_files = [str(item) for item in self.loaded_files]

    def get_provider_spec(self, provider: str) -> ProviderSpec | None:
        return self.provider_specs.get(str(provider or "").strip().lower())

    def get_provider_conf(self, provider_conf_id: str) -> ProviderConf | None:
        return self.provider_confs.get(str(provider_conf_id or "").strip().lower())

    def get_route(self, route: str) -> RouteConfig | None:
        return self.routes.get(str(route or "").strip().lower())

    def list_provider_confs(self, *, include_disabled: bool = False) -> list[ProviderConf]:
        values = sorted(self.provider_confs.values(), key=lambda item: item.id)
        if include_disabled:
            return values
        return [item for item in values if item.enabled]

    def route_candidates(
        self,
        route: str,
        *,
        include_disabled: bool = False,
    ) -> list[RouteCandidate]:
        route_config = self.get_route(route)
        if route_config is None:
            return []
        if include_disabled:
            return list(route_config.candidates)
        enabled: list[RouteCandidate] = []
        for candidate in route_config.candidates:
            provider_conf = self.get_provider_conf(candidate.provider_conf_id)
            if candidate.enabled and provider_conf is not None and provider_conf.enabled:
                enabled.append(candidate)
        return enabled

    def default_routes(self) -> dict[str, str]:
        return {
            "chat": self.runtime.default_chat_route,
            "structured": self.runtime.default_structured_route,
            "embedding": self.runtime.default_embedding_route,
            "context_compression": self.runtime.default_compression_route,
        }

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(self)


def default_provider_specs() -> dict[str, ProviderSpec]:
    specs = {
        "mock": {
            "provider": "mock",
            "protocol": "mock",
            "display_name": "Mock",
            "default_base_url": None,
            "default_model": "mock-v1",
            "supports_streaming": True,
            "supports_json_mode": True,
            "supports_tool_calling": False,
            "supports_embedding": True,
            "supports_vision": False,
            "supports_custom_headers": False,
            "supports_top_p": True,
            "supports_top_k": False,
            "default_timeout_seconds": 5,
            "default_max_retries": 0,
            "request_adapter": "mock",
            "response_adapter": "mock",
            "known_model_prefixes": ["mock"],
            "tags": ["builtin", "mock"],
        },
        "openai": {
            "provider": "openai",
            "protocol": "openai-compatible",
            "display_name": "OpenAI",
            "default_base_url": "https://api.openai.com/v1",
            "default_model": "gpt-4o-mini",
            "supports_streaming": True,
            "supports_json_mode": True,
            "supports_tool_calling": True,
            "supports_embedding": True,
            "supports_vision": True,
            "supports_custom_headers": True,
            "supports_top_p": True,
            "supports_top_k": False,
            "default_timeout_seconds": 60,
            "default_max_retries": 5,
            "request_adapter": "openai_compatible",
            "response_adapter": "openai_compatible",
            "known_model_prefixes": ["gpt-", "o"],
            "tags": ["builtin", "openai-compatible"],
        },
        "qianwen": {
            "provider": "qianwen",
            "protocol": "openai-compatible",
            "display_name": "Qianwen",
            "default_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "default_model": "qwen-plus",
            "supports_streaming": True,
            "supports_json_mode": True,
            "supports_tool_calling": True,
            "supports_embedding": True,
            "supports_vision": True,
            "supports_custom_headers": True,
            "supports_top_p": True,
            "supports_top_k": False,
            "default_timeout_seconds": 60,
            "default_max_retries": 5,
            "request_adapter": "openai_compatible",
            "response_adapter": "openai_compatible",
            "known_model_prefixes": ["qwen-"],
            "tags": ["builtin", "openai-compatible"],
        },
        "doubao": {
            "provider": "doubao",
            "protocol": "openai-compatible",
            "display_name": "Doubao",
            "default_base_url": "https://ark.cn-beijing.volces.com/api/v3",
            "default_model": None,
            "supports_streaming": True,
            "supports_json_mode": True,
            "supports_tool_calling": True,
            "supports_embedding": False,
            "supports_vision": True,
            "supports_custom_headers": True,
            "supports_top_p": True,
            "supports_top_k": False,
            "default_timeout_seconds": 60,
            "default_max_retries": 5,
            "request_adapter": "openai_compatible",
            "response_adapter": "openai_compatible",
            "known_model_prefixes": ["ep-"],
            "tags": ["builtin", "openai-compatible"],
        },
        "custom_openai_compatible": {
            "provider": "custom_openai_compatible",
            "protocol": "openai-compatible",
            "display_name": "Custom OpenAI-Compatible",
            "default_base_url": None,
            "default_model": None,
            "supports_streaming": True,
            "supports_json_mode": True,
            "supports_tool_calling": False,
            "supports_embedding": False,
            "supports_vision": False,
            "supports_custom_headers": True,
            "supports_top_p": True,
            "supports_top_k": False,
            "default_timeout_seconds": 60,
            "default_max_retries": 5,
            "request_adapter": "openai_compatible",
            "response_adapter": "openai_compatible",
            "known_model_prefixes": [],
            "tags": ["custom", "openai-compatible"],
        },
    }
    return {key: ProviderSpec(**value) for key, value in specs.items()}


def default_route_configs() -> dict[str, RouteConfig]:
    defaults: dict[str, dict[str, Any]] = {
        "chat": {
            "default_model_policy": "user_selected",
            "params": {"temperature": 0.5, "max_tokens": 2000},
        },
        "analyzer_intent_fallback": {
            "default_model_policy": "user_selected",
            "params": {"temperature": 0.1, "max_tokens": 1200, "json_mode": True},
        },
        "planner_structured_plan": {
            "default_model_policy": "user_selected",
            "params": {"temperature": 0.1, "max_tokens": 2500, "json_mode": True},
        },
        "react_action_decision": {
            "default_model_policy": "user_selected",
            "params": {
                "temperature": 0.1,
                "top_p": 0.9,
                "max_tokens": 1200,
                "json_mode": True,
                "timeout_seconds": 60,
            },
        },
        "react_action_repair": {
            "default_model_policy": "user_selected",
            "params": {"temperature": 0.0, "max_tokens": 1200, "json_mode": True},
        },
        "react_call_model": {
            "default_model_policy": "user_selected",
            "params": {"temperature": 0.5, "max_tokens": 2000},
        },
        "checker_semantic": {
            "default_model_policy": "user_selected",
            "params": {"temperature": 0.1, "max_tokens": 1200},
        },
        "summary": {
            "default_model_policy": "user_selected",
            "params": {"temperature": 0.3, "max_tokens": 2000},
        },
        "memory_summary": {
            "default_model_policy": "user_selected",
            "params": {"temperature": 0.3, "max_tokens": 2000},
        },
        "context_compression": {
            "default_model_policy": "user_selected",
            "params": {"temperature": 0.2, "max_tokens": 2000},
        },
        "rag_answer": {
            "default_model_policy": "user_selected",
            "params": {"temperature": 0.4, "max_tokens": 2000},
        },
        "web_search": {
            "default_model_policy": "user_selected",
            "params": {
                "temperature": 0.0,
                "max_tokens": 1800,
                "json_mode": True,
                "timeout_seconds": 30,
            },
        },
        "embedding": {
            "default_model_policy": "explicit_candidates",
            "params": {},
        },
    }
    return {
        route: RouteConfig(route=route, candidates=[], **payload)
        for route, payload in defaults.items()
    }


def _resolve_workspace_root(workspace_root: str | Path | None) -> Path:
    return Path(workspace_root or os.getenv("AGENT_WORKSPACE_ROOT") or Path.cwd()).resolve()


def _resolve_config_dir(workspace_root: Path) -> Path:
    override = os.getenv("MODELS_CONFIG_DIR")
    if not override:
        return (workspace_root / "config" / "models").resolve()
    path = Path(override)
    if not path.is_absolute():
        path = workspace_root / path
    return path.resolve()


def _read_json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ModelsConfigError(
            ModelsConfigErrorCode.INVALID_JSON,
            f"invalid JSON in {path.name}: {exc.msg}",
            path=str(path),
            details={"line": exc.lineno, "column": exc.colno},
        ) from exc


def _load_json_if_present(path: Path, loaded_files: list[str]) -> Any | None:
    if not path.exists():
        return None
    loaded_files.append(str(path))
    return _read_json_file(path)


def _as_item_list(
    data: Any,
    *,
    key_field: str,
    path: str,
) -> list[dict[str, Any]]:
    if data is None:
        return []
    if isinstance(data, list):
        return [dict(item) for item in data]
    if isinstance(data, dict):
        items = data.get("items") if "items" in data and isinstance(data.get("items"), list) else None
        if items is not None:
            return [dict(item) for item in items]
        normalized: list[dict[str, Any]] = []
        for key, value in data.items():
            if not isinstance(value, dict):
                raise ModelsConfigError(
                    ModelsConfigErrorCode.INVALID_VALUE,
                    f"invalid object in {path}",
                    path=path,
                    details={"key": key},
                )
            item = dict(value)
            item.setdefault(key_field, key)
            normalized.append(item)
        return normalized
    raise ModelsConfigError(
        ModelsConfigErrorCode.INVALID_VALUE,
        f"{path} must contain a JSON object or list",
        path=path,
    )


def _as_route_list(data: Any, *, path: str) -> list[dict[str, Any]]:
    items = _as_item_list(data, key_field="route", path=path)
    for item in items:
        if "route" not in item and "call_type" in item:
            item["route"] = item["call_type"]
    return items


def _ensure_unique_credential_slugs(provider_conf: ProviderConf) -> None:
    seen: set[str] = set()
    for credential in provider_conf.credentials:
        if credential.slug in seen:
            raise ModelsConfigError(
                ModelsConfigErrorCode.DUPLICATE_CREDENTIAL_SLUG,
                f"duplicate credential_slug '{credential.slug}' in {provider_conf.id}",
                details={
                    "provider_conf_id": provider_conf.id,
                    "credential_slug": credential.slug,
                },
            )
        seen.add(credential.slug)


def _reject_plain_secrets(raw_data: Any, *, path: Path) -> None:
    secret_keys = find_plain_secret_keys(raw_data)
    if secret_keys:
        raise ModelsConfigError(
            ModelsConfigErrorCode.PLAIN_SECRET_IN_CONFIG,
            "provider configuration must not contain plaintext secrets",
            path=str(path),
            details={"keys": secret_keys},
        )


def _load_provider_specs(raw_specs: Any | None, *, path: Path) -> dict[str, ProviderSpec]:
    specs = default_provider_specs()
    seen_file_specs: set[str] = set()
    for item in _as_item_list(raw_specs, key_field="provider", path=str(path)):
        spec = ProviderSpec(**_dataclass_kwargs(ProviderSpec, item))
        if spec.provider in seen_file_specs:
            raise ModelsConfigError(
                ModelsConfigErrorCode.DUPLICATE_PROVIDER_SPEC,
                f"duplicate provider spec '{spec.provider}'",
                path=str(path),
                details={"provider": spec.provider},
            )
        seen_file_specs.add(spec.provider)
        specs[spec.provider] = spec
    return specs


def _load_provider_confs(raw_confs: Any | None, *, path: Path) -> dict[str, ProviderConf]:
    _reject_plain_secrets(raw_confs, path=path)
    conns: dict[str, ProviderConf] = {}
    for item in _as_item_list(raw_confs, key_field="id", path=str(path)):
        provider_conf = ProviderConf(**_dataclass_kwargs(ProviderConf, item))
        if provider_conf.id in conns:
            raise ModelsConfigError(
                ModelsConfigErrorCode.DUPLICATE_PROVIDER_CONF_ID,
                f"duplicate provider_conf_id '{provider_conf.id}'",
                path=str(path),
                details={"provider_conf_id": provider_conf.id},
            )
        conns[provider_conf.id] = provider_conf
    return conns


def _load_routes(raw_routes: Any | None, *, path: Path) -> dict[str, RouteConfig]:
    routes = default_route_configs()
    seen_file_routes: set[str] = set()
    for item in _as_route_list(raw_routes, path=str(path)):
        route_config = RouteConfig(**_dataclass_kwargs(RouteConfig, item))
        if route_config.route in seen_file_routes:
            raise ModelsConfigError(
                ModelsConfigErrorCode.INVALID_VALUE,
                f"duplicate route '{route_config.route}'",
                path=str(path),
                details={"route": route_config.route},
            )
        seen_file_routes.add(route_config.route)
        routes[route_config.route] = route_config
    return routes


def _load_runtime_config(raw_runtime: Any | None, *, root: Path, path: Path) -> ModelsRuntimeConfig:
    if raw_runtime is not None and not isinstance(raw_runtime, dict):
        raise ModelsConfigError(
            ModelsConfigErrorCode.INVALID_VALUE,
            "models_config.json must contain a JSON object",
            path=str(path),
        )
    values = dict(raw_runtime or {})
    _apply_runtime_env_overrides(values)
    return ModelsRuntimeConfig(**values).with_resolved_paths(root)


def _apply_runtime_env_overrides(values: dict[str, Any]) -> None:
    env_map: dict[str, tuple[str, Any]] = {
        "MODELS_DEFAULT_CHAT_ROUTE": ("default_chat_route", str),
        "MODELS_DEFAULT_STRUCTURED_ROUTE": ("default_structured_route", str),
        "MODELS_DEFAULT_EMBEDDING_ROUTE": ("default_embedding_route", str),
        "MODELS_DEFAULT_COMPRESSION_ROUTE": ("default_compression_route", str),
        "MODELS_DEFAULT_MOCK_ENABLED": ("default_mock_enabled", _normalize_bool),
        "MODELS_REAL_PROVIDER_ENABLED_BY_DEFAULT": (
            "real_provider_enabled_by_default",
            _normalize_bool,
        ),
        "MODELS_MAX_RETRIES": ("max_retries", int),
        "MODELS_RETRY_BACKOFF_BASE_SECONDS": ("retry_backoff_base_seconds", float),
        "MODELS_RETRY_BACKOFF_MAX_SECONDS": ("retry_backoff_max_seconds", float),
        "MODELS_LOGS_PATH": ("logs_path", str),
        "MODELS_LOG_FULL_PROMPT": ("log_full_prompt", _normalize_bool),
        "MODELS_LOG_FULL_RESPONSE": ("log_full_response", _normalize_bool),
    }
    for env_name, (field_name, parser) in env_map.items():
        raw = os.getenv(env_name)
        if raw is None:
            continue
        try:
            values[field_name] = parser(raw)
        except (TypeError, ValueError):
            values[field_name] = raw


def _validate_provider_confs(
    provider_specs: Mapping[str, ProviderSpec],
    provider_confs: Mapping[str, ProviderConf],
) -> None:
    for provider_conf in provider_confs.values():
        spec = provider_specs.get(provider_conf.provider)
        if spec is None:
            raise ModelsConfigError(
                ModelsConfigErrorCode.UNSUPPORTED_PROVIDER,
                f"unsupported provider: {provider_conf.provider}",
                details={
                    "provider_conf_id": provider_conf.id,
                    "provider": provider_conf.provider,
                },
            )
        if provider_conf.protocol != spec.protocol:
            raise ModelsConfigError(
                ModelsConfigErrorCode.UNSUPPORTED_PROTOCOL,
                "provider_conf protocol does not match provider spec",
                details={
                    "provider_conf_id": provider_conf.id,
                    "provider": provider_conf.provider,
                    "provider_conf_protocol": provider_conf.protocol,
                    "provider_spec_protocol": spec.protocol,
                },
            )


def _validate_routes(
    routes: Mapping[str, RouteConfig],
    provider_confs: Mapping[str, ProviderConf],
) -> None:
    for route_config in routes.values():
        if route_config.route not in MODEL_CALL_TYPES and route_config.route != "structured":
            raise ModelsConfigError(
                ModelsConfigErrorCode.INVALID_VALUE,
                f"unsupported route name: {route_config.route}",
                details={"route": route_config.route},
            )
        for candidate in route_config.candidates:
            provider_conf = provider_confs.get(candidate.provider_conf_id)
            if provider_conf is None:
                raise ModelsConfigError(
                    ModelsConfigErrorCode.INVALID_ROUTE_CANDIDATE,
                    "route candidate references unknown provider_conf_id",
                    details={
                        "route": route_config.route,
                        "provider_conf_id": candidate.provider_conf_id,
                    },
                )
            if candidate.credential_slug not in {item.slug for item in provider_conf.credentials}:
                raise ModelsConfigError(
                    ModelsConfigErrorCode.INVALID_ROUTE_CANDIDATE,
                    "route candidate references unknown credential_slug",
                    details={
                        "route": route_config.route,
                        "provider_conf_id": candidate.provider_conf_id,
                        "credential_slug": candidate.credential_slug,
                    },
                )


@lru_cache(maxsize=8)
def load_models_config(workspace_root: str | Path | None = None) -> ModelsConfig:
    root = _resolve_workspace_root(workspace_root)
    config_dir = _resolve_config_dir(root)
    loaded_files: list[str] = []

    provider_specs_path = config_dir / "provider_specs.json"
    provider_confs_path = config_dir / "provider_confs.json"
    routes_path = config_dir / "routes.json"
    runtime_path = config_dir / "models_config.json"
    pricing_path = config_dir / "pricing.json"
    structured_output_path = config_dir / "structured_output.json"

    raw_provider_specs = _load_json_if_present(provider_specs_path, loaded_files)
    raw_provider_confs = _load_json_if_present(provider_confs_path, loaded_files)
    raw_routes = _load_json_if_present(routes_path, loaded_files)
    raw_runtime = _load_json_if_present(runtime_path, loaded_files)
    raw_pricing = _load_json_if_present(pricing_path, loaded_files)
    raw_structured_output = _load_json_if_present(structured_output_path, loaded_files)

    provider_specs = _load_provider_specs(raw_provider_specs, path=provider_specs_path)
    provider_confs = _load_provider_confs(raw_provider_confs, path=provider_confs_path)
    routes = _load_routes(raw_routes, path=routes_path)
    runtime = _load_runtime_config(raw_runtime, root=root, path=runtime_path)
    pricing = _copy_metadata(raw_pricing) if isinstance(raw_pricing, dict) else {}
    structured_output = (
        _copy_metadata(raw_structured_output) if isinstance(raw_structured_output, dict) else {}
    )

    _validate_provider_confs(provider_specs, provider_confs)
    _validate_routes(routes, provider_confs)

    return ModelsConfig(
        workspace_root=root,
        config_dir=config_dir,
        runtime=runtime,
        provider_specs=provider_specs,
        provider_confs=provider_confs,
        routes=routes,
        pricing=pricing,
        structured_output=structured_output,
        loaded_files=loaded_files,
    )


def get_models_config(workspace_root: str | Path | None = None) -> ModelsConfig:
    return load_models_config(workspace_root)
