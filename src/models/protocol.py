from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
from typing import Any, Iterable
from uuid import uuid4

from src.models.errors import (
    ModelErrorCode,
    classify_model_error_code,
    normalize_model_error_category,
    normalize_model_error_code,
)
from src.models.retry import is_retryable_error


class ModelMessageRole(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ModelCallType(str, Enum):
    CHAT = "chat"
    ANALYZER_INTENT_FALLBACK = "analyzer_intent_fallback"
    PLANNER_STRUCTURED_PLAN = "planner_structured_plan"
    REACT_ACTION_DECISION = "react_action_decision"
    REACT_ACTION_REPAIR = "react_action_repair"
    REACT_CALL_MODEL = "react_call_model"
    CHECKER_SEMANTIC = "checker_semantic"
    SUMMARY = "summary"
    MEMORY_SUMMARY = "memory_summary"
    CONTEXT_COMPRESSION = "context_compression"
    RAG_ANSWER = "rag_answer"
    WEB_SEARCH = "web_search"
    EMBEDDING = "embedding"


MODEL_MESSAGE_ROLES = frozenset(role.value for role in ModelMessageRole)
MODEL_CALL_TYPES = frozenset(call_type.value for call_type in ModelCallType)
STRUCTURED_PARSE_MODES = frozenset({"strict", "lenient"})
HEALTH_CHECK_TYPES = frozenset({"config_check", "live_check"})
COMPRESSION_LOSS_RISKS = frozenset({"low", "medium", "high"})


def new_model_request_id() -> str:
    return f"modelreq_{uuid4().hex[:12]}"


def normalize_model_call_type(value: str | ModelCallType) -> str:
    if isinstance(value, ModelCallType):
        return value.value
    normalized = str(value or "").strip().lower()
    if normalized not in MODEL_CALL_TYPES:
        raise ValueError(f"Unsupported model call type: {value}")
    return normalized


def _json_safe(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _copy_metadata(value: dict[str, Any] | None) -> dict[str, Any]:
    return dict(value or {})


@dataclass
class ModelMessage:
    role: str | ModelMessageRole
    content: str
    name: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.role, ModelMessageRole):
            self.role = self.role.value
        else:
            self.role = str(self.role or "").strip().lower()
        if self.role not in MODEL_MESSAGE_ROLES:
            raise ValueError(f"Unsupported model message role: {self.role}")
        if not isinstance(self.content, str):
            raise TypeError("ModelMessage.content must be a string")
        self.metadata = _copy_metadata(self.metadata)

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(self)


def coerce_model_messages(
    messages: Iterable[ModelMessage | dict[str, Any]] | None,
) -> list[ModelMessage]:
    normalized: list[ModelMessage] = []
    for message in messages or []:
        if isinstance(message, ModelMessage):
            normalized.append(message)
        elif isinstance(message, dict):
            normalized.append(ModelMessage(**message))
        else:
            raise TypeError("Model messages must be ModelMessage objects or dictionaries")
    return normalized


@dataclass
class ModelTraceContext:
    source_trace_id: str | None = None
    conversation_id: str | None = None
    session_id: str | None = None
    plan_id: str | None = None
    execution_id: str | None = None
    task_id: str | None = None
    step_id: str | None = None
    packet_id: str | None = None
    parent_request_id: str | None = None
    caller: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.metadata = _copy_metadata(self.metadata)

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(self)


@dataclass
class ModelCallOptions:
    call_type: str | ModelCallType = ModelCallType.CHAT
    route: str | None = None
    provider_conf_id: str | None = None
    credential_slug: str | None = None
    model: str | None = None
    messages: list[ModelMessage] | list[dict[str, Any]] | None = None
    prompt: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    max_tokens: int | None = None
    timeout_seconds: float | None = None
    max_retries: int | None = None
    json_mode: bool | None = None
    response_format: str | None = None
    allow_fallback: bool = True
    allow_retry: bool = True
    allow_truncation: bool = False
    allow_external_provider: bool | None = None
    sensitive_content_policy: str | None = None
    redact_before_send: bool | None = None
    trace_context: ModelTraceContext | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.call_type = normalize_model_call_type(self.call_type)
        self.messages = coerce_model_messages(self.messages)
        if self.prompt is not None and not isinstance(self.prompt, str):
            raise TypeError("ModelCallOptions.prompt must be a string or None")
        if self.max_retries is not None:
            self.max_retries = max(int(self.max_retries), 0)
        if self.max_tokens is not None:
            self.max_tokens = max(int(self.max_tokens), 0)
        if self.timeout_seconds is not None:
            self.timeout_seconds = max(float(self.timeout_seconds), 0.0)
        self.metadata = _copy_metadata(self.metadata)

    def to_messages(self) -> list[ModelMessage]:
        if self.messages:
            return list(self.messages)
        if self.prompt is None:
            return []
        return [ModelMessage(role=ModelMessageRole.USER, content=self.prompt)]

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(self)


@dataclass
class ModelUsage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    reasoning_tokens: int | None = None
    cached_tokens: int | None = None
    source: str = "none"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in (
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "reasoning_tokens",
            "cached_tokens",
        ):
            value = getattr(self, field_name)
            if value is not None:
                setattr(self, field_name, max(int(value), 0))
        self.metadata = _copy_metadata(self.metadata)

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(self)


@dataclass
class ModelCost:
    input_cost: float | None = None
    output_cost: float | None = None
    total_cost: float | None = None
    currency: str | None = None
    pricing_source: str = "none"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in ("input_cost", "output_cost", "total_cost"):
            value = getattr(self, field_name)
            if value is not None:
                setattr(self, field_name, float(value))
        self.metadata = _copy_metadata(self.metadata)

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(self)


@dataclass
class ModelErrorInfo:
    code: str | ModelErrorCode = ModelErrorCode.UNKNOWN_ERROR
    message: str = ""
    category: str = "unknown"
    retriable: bool = False
    fallback_allowed: bool = False
    cooldown_scope: str | None = None
    http_status: int | None = None
    provider_error_code: str | None = None
    provider_error_message: str | None = None
    provider_error_hint: str | None = None
    raw_error_preview: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.code = normalize_model_error_code(self.code)
        self.category = str(self.category or "").strip().lower() or "unknown"
        if self.category == "unknown":
            self.category = normalize_model_error_category(self.code)
        if self.http_status is not None:
            self.http_status = int(self.http_status)
        self.metadata = _copy_metadata(self.metadata)

    @classmethod
    def from_provider_error(
        cls,
        *,
        code: str | ModelErrorCode | None = None,
        message: str = "",
        http_status: int | None = None,
        provider_error_code: str | None = None,
        provider_error_message: str | None = None,
        provider_error_hint: str | None = None,
        raw_error_preview: str | None = None,
        retriable: bool | None = None,
        fallback_allowed: bool | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "ModelErrorInfo":
        classification_message = provider_error_message or message
        normalized_code = classify_model_error_code(
            code,
            http_status=http_status,
            provider_error_code=provider_error_code,
            provider_error_message=classification_message,
            provider_error_hint=provider_error_hint,
        )
        inferred_retriable = is_retryable_error(normalized_code)
        if retriable is None:
            retriable = inferred_retriable
        if fallback_allowed is None:
            fallback_allowed = bool(retriable)
        return cls(
            code=normalized_code,
            message=message,
            category=normalize_model_error_category(normalized_code),
            retriable=bool(retriable),
            fallback_allowed=bool(fallback_allowed),
            http_status=http_status,
            provider_error_code=provider_error_code,
            provider_error_message=provider_error_message,
            provider_error_hint=provider_error_hint,
            raw_error_preview=raw_error_preview,
            metadata=metadata or {},
        )

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(self)


@dataclass
class ModelCallResult:
    success: bool
    content: str = ""
    code: str | ModelErrorCode | None = None
    error: str | None = None
    provider: str | None = None
    protocol: str | None = None
    provider_conf_id: str | None = None
    credential_slug: str | None = None
    model: str | None = None
    route: str | None = None
    call_type: str | ModelCallType | None = None
    request_id: str = field(default_factory=new_model_request_id)
    source_trace_id: str | None = None
    trace_context: ModelTraceContext | None = None
    model_request_id: str = ""
    provider_request_id: str | None = None
    latency_ms: int | None = None
    usage: ModelUsage | None = None
    cost: ModelCost | None = None
    attempts: int = 1
    retriable: bool = False
    fallback_used: bool = False
    fallback_reason: str | None = None
    error_info: ModelErrorInfo | None = None
    selected_candidate: dict[str, Any] | None = None
    raw_response: Any = None
    raw_response_preview: str | None = None
    raw_response_hash: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.call_type is not None:
            self.call_type = normalize_model_call_type(self.call_type)
        self.model_request_id = self.model_request_id or self.request_id
        self.attempts = max(int(self.attempts), 1)
        if self.latency_ms is not None:
            self.latency_ms = max(int(self.latency_ms), 0)
        self.metadata = _copy_metadata(self.metadata)
        self.selected_candidate = (
            _json_safe(self.selected_candidate) if self.selected_candidate is not None else None
        )

        if self.trace_context is not None and self.source_trace_id is None:
            self.source_trace_id = self.trace_context.source_trace_id

        if self.success:
            self.code = None
            self.error = None
            self.error_info = None
            return

        self.content = ""
        if self.error_info is None:
            normalized_code = normalize_model_error_code(self.code)
            message = self.error or "model call failed"
            self.error_info = ModelErrorInfo(
                code=normalized_code,
                message=message,
                category=normalize_model_error_category(normalized_code),
                retriable=self.retriable or is_retryable_error(normalized_code),
                fallback_allowed=self.fallback_used,
            )
        self.code = self.error_info.code
        self.error = self.error or self.error_info.message or "model call failed"
        self.retriable = bool(self.error_info.retriable)

    @classmethod
    def ok(cls, content: str, **kwargs: Any) -> "ModelCallResult":
        return cls(success=True, content=content, **kwargs)

    @classmethod
    def fail(
        cls,
        code: str | ModelErrorCode,
        error: str,
        **kwargs: Any,
    ) -> "ModelCallResult":
        # Failures never expose text as model-generated content.
        kwargs.pop("content", None)
        return cls(success=False, content="", code=code, error=error, **kwargs)

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(self)


@dataclass
class ModelStreamChunk:
    success: bool
    content_delta: str = ""
    index: int = 0
    is_final: bool = False
    code: str | ModelErrorCode | None = None
    error: str | None = None
    request_id: str = field(default_factory=new_model_request_id)
    provider: str | None = None
    model: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.index = max(int(self.index), 0)
        self.metadata = _copy_metadata(self.metadata)
        if self.success:
            self.code = None
            self.error = None
        else:
            self.content_delta = ""
            self.code = normalize_model_error_code(self.code)
            self.error = self.error or "model stream call failed"

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(self)


@dataclass
class ModelStreamResult:
    success: bool
    content: str = ""
    code: str | ModelErrorCode | None = None
    error: str | None = None
    request_id: str = field(default_factory=new_model_request_id)
    provider: str | None = None
    model: str | None = None
    chunks_count: int = 0
    latency_ms: int | None = None
    usage: ModelUsage | None = None
    cost: ModelCost | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.chunks_count = max(int(self.chunks_count), 0)
        if self.latency_ms is not None:
            self.latency_ms = max(int(self.latency_ms), 0)
        self.metadata = _copy_metadata(self.metadata)
        if self.success:
            self.code = None
            self.error = None
        else:
            self.content = ""
            self.code = normalize_model_error_code(self.code)
            self.error = self.error or "model stream call failed"

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(self)


@dataclass
class StructuredModelResult:
    success: bool
    data: dict[str, Any] | list[Any] | None = None
    content: str = ""
    code: str | ModelErrorCode | None = None
    error: str | None = None
    parse_mode: str = "lenient"
    schema_name: str | None = None
    schema_valid: bool | None = None
    repair_attempts: int = 0
    model_result: ModelCallResult | None = None
    raw_json_text: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.parse_mode = str(self.parse_mode or "").strip().lower()
        if self.parse_mode not in STRUCTURED_PARSE_MODES:
            raise ValueError(f"Unsupported structured parse mode: {self.parse_mode}")
        self.repair_attempts = max(int(self.repair_attempts), 0)
        self.metadata = _copy_metadata(self.metadata)
        if self.success:
            self.code = None
            self.error = None
        else:
            self.code = normalize_model_error_code(
                self.code or (self.model_result.code if self.model_result else None),
                default=ModelErrorCode.INVALID_JSON,
            )
            self.error = self.error or (
                self.model_result.error if self.model_result else "structured model output failed"
            )

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(self)


@dataclass
class ModelHealthStatus:
    healthy: bool
    provider_conf_id: str | None = None
    provider: str | None = None
    protocol: str | None = None
    model: str | None = None
    configured: bool = False
    missing_config: list[str] = field(default_factory=list)
    check_type: str = "config_check"
    latency_ms: int | None = None
    error: str | None = None
    code: str | ModelErrorCode | None = None
    verified_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.check_type = str(self.check_type or "").strip().lower()
        if self.check_type not in HEALTH_CHECK_TYPES:
            raise ValueError(f"Unsupported model health check type: {self.check_type}")
        self.missing_config = [str(item) for item in self.missing_config]
        if self.latency_ms is not None:
            self.latency_ms = max(int(self.latency_ms), 0)
        self.metadata = _copy_metadata(self.metadata)
        if self.healthy:
            self.code = None
            self.error = None
        else:
            self.code = normalize_model_error_code(self.code)

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(self)


@dataclass
class EmbeddingResult:
    success: bool
    embedding: list[float] | None = None
    dimensions: int | None = None
    code: str | ModelErrorCode | None = None
    error: str | None = None
    provider_conf_id: str | None = None
    model: str | None = None
    usage: ModelUsage | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.metadata = _copy_metadata(self.metadata)
        if self.success:
            if self.embedding is not None:
                self.embedding = [float(value) for value in self.embedding]
                self.dimensions = len(self.embedding)
            elif self.dimensions is not None:
                self.dimensions = max(int(self.dimensions), 0)
            self.code = None
            self.error = None
        else:
            self.embedding = None
            self.dimensions = None
            self.code = normalize_model_error_code(
                self.code,
                default=ModelErrorCode.EMBEDDING_FAILED,
            )
            self.error = self.error or "embedding call failed"

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(self)


@dataclass
class EmbeddingBatchResult:
    success: bool
    embeddings: list[list[float]] = field(default_factory=list)
    item_results: list[EmbeddingResult] = field(default_factory=list)
    code: str | ModelErrorCode | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.metadata = _copy_metadata(self.metadata)
        self.item_results = list(self.item_results)
        if self.success:
            self.embeddings = [[float(value) for value in item] for item in self.embeddings]
            self.code = None
            self.error = None
        else:
            self.embeddings = []
            self.code = normalize_model_error_code(
                self.code,
                default=ModelErrorCode.EMBEDDING_FAILED,
            )
            self.error = self.error or "embedding batch call failed"

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(self)


@dataclass
class CompressedChunkRef:
    source_ref: str
    chunk_id: str | None = None
    original_length: int | None = None
    compressed_length: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.original_length is not None:
            self.original_length = max(int(self.original_length), 0)
        if self.compressed_length is not None:
            self.compressed_length = max(int(self.compressed_length), 0)
        self.metadata = _copy_metadata(self.metadata)

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(self)


@dataclass
class ContextCompressionResult:
    success: bool
    short_summary: str = ""
    compressed_text: str = ""
    compressed_chunks: list[CompressedChunkRef] = field(default_factory=list)
    source_refs: list[str] = field(default_factory=list)
    original_length: int | None = None
    compressed_length: int | None = None
    original_token_count: int | None = None
    compressed_token_count: int | None = None
    compression_ratio: float | None = None
    trigger_reason: str | None = None
    round_index: int | None = None
    loss_risk: str | None = None
    key_points: list[str] = field(default_factory=list)
    preserved_entities: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    code: str | ModelErrorCode | None = None
    error: str | None = None
    model_result: ModelCallResult | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.compressed_chunks = [
            item if isinstance(item, CompressedChunkRef) else CompressedChunkRef(**item)
            for item in self.compressed_chunks
        ]
        for field_name in (
            "original_length",
            "compressed_length",
            "original_token_count",
            "compressed_token_count",
            "round_index",
        ):
            value = getattr(self, field_name)
            if value is not None:
                setattr(self, field_name, max(int(value), 0))
        if self.compression_ratio is not None:
            self.compression_ratio = max(float(self.compression_ratio), 0.0)
        if self.loss_risk is not None:
            self.loss_risk = str(self.loss_risk).strip().lower()
            if self.loss_risk not in COMPRESSION_LOSS_RISKS:
                raise ValueError(f"Unsupported compression loss risk: {self.loss_risk}")
        self.metadata = _copy_metadata(self.metadata)
        if self.success:
            self.code = None
            self.error = None
        else:
            self.short_summary = ""
            self.compressed_text = ""
            self.code = normalize_model_error_code(
                self.code,
                default=ModelErrorCode.COMPRESSION_FAILED,
            )
            self.error = self.error or "context compression failed"

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(self)
