from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import datetime, timezone
import time
from typing import Any, Dict, Generator

from src.core.config import get_settings
from src.models.base_model import BaseModel
from src.models.config import (
    ModelProviderProtocol,
    ModelsConfig,
    ProviderConf,
    ProviderCredential,
    get_models_config,
)
from src.models.credentials import CredentialResolution, resolve_credential_secret
from src.models.errors import MODEL_ERROR_CODES, ModelErrorCode
from src.models.mock_model import MockModel
from src.models.compression import compress_context_with_model
from src.models.embeddings import (
    DEFAULT_EMBEDDING_MODEL,
    embedding_batch_failure,
    embedding_batch_from_items,
    embedding_failure,
    normalize_embedding_text,
    normalize_embedding_texts,
)
from src.models.observability import ModelCallLogger, estimate_model_cost
from src.models.protocol import (
    ContextCompressionResult,
    EmbeddingBatchResult,
    EmbeddingResult,
    ModelCallResult,
    ModelCallOptions,
    ModelErrorInfo,
    ModelCallType,
    ModelHealthStatus,
    ModelStreamChunk,
    ModelTraceContext,
    StructuredModelResult,
    new_model_request_id,
    normalize_model_call_type,
)
from src.models.retry import RetryPolicy, is_fallback_allowed, is_retryable_error
from src.models.router import (
    CandidateHealthRegistry,
    ModelRouter,
    ROUTE_PARAMETER_NAMES,
    RouteResolution,
    make_candidate_key,
)
from src.models.providers.openai_compatible import (
    OpenAICompatibleModel,
    OpenAICompatibleProvider,
    configured_builtin_provider_conf,
)
from src.models.structured_output import (
    build_json_repair_prompt,
    parse_json_output,
    validate_json_schema,
)


class ModelManager:
    """Manage chat model providers behind a stable interface."""

    def __init__(
        self,
        model_name: str | None = None,
        *,
        models_config: ModelsConfig | None = None,
        provider_conf_id: str | None = None,
        credential_slug: str | None = None,
        credential_lookup: Any | None = None,
        client_factory: Any | None = None,
        call_logger: ModelCallLogger | None = None,
    ):
        settings = get_settings()
        self.provider_conf_id = provider_conf_id
        self.credential_slug = credential_slug
        self.credential_lookup = credential_lookup
        self.client_factory = client_factory
        self.models_config: ModelsConfig | None = models_config
        self.model_name = (model_name or settings.model_name or "mock").lower()
        self.model: BaseModel | None = None
        self.init_error: Exception | None = None
        self.router: ModelRouter | None = None
        self.health_registry = CandidateHealthRegistry()
        self._route_models: dict[tuple[str | None, str | None], BaseModel] = {}
        self.call_logger = call_logger

        try:
            if self.models_config is None:
                self.models_config = get_models_config(settings.workspace_root)
            if self.call_logger is None and self.models_config is not None:
                runtime = self.models_config.runtime
                self.call_logger = ModelCallLogger(
                    runtime.logs_path,
                    log_full_prompt=runtime.log_full_prompt,
                    log_full_response=runtime.log_full_response,
                )
            if provider_conf_id and model_name is None:
                provider_conf = self.models_config.get_provider_conf(provider_conf_id)
                if provider_conf is not None:
                    self.model_name = provider_conf.provider
            self.router = ModelRouter(
                self.models_config,
                health_registry=self.health_registry,
            )
            self.model = self._create_model(self.model_name)
            self._route_models[(self.provider_conf_id, self.credential_slug)] = self.model
        except Exception as exc:
            self.init_error = exc

    def _create_model(self, model_name: str) -> BaseModel:
        if model_name == "mock":
            return MockModel()

        provider_conf = self._resolve_provider_conf(model_name)
        self.provider_conf_id = provider_conf.id
        return self._create_model_for_provider_conf(provider_conf)

    def _create_model_for_provider_conf(
        self,
        provider_conf: ProviderConf,
        *,
        credential_slug: str | None = None,
    ) -> BaseModel:
        if provider_conf.protocol == ModelProviderProtocol.MOCK.value:
            return MockModel(
                provider=provider_conf.provider,
                model=provider_conf.model_id or provider_conf.default_model or "mock-v1",
            )
        if provider_conf.protocol != ModelProviderProtocol.OPENAI_COMPATIBLE.value:
            raise ValueError(f"Unsupported model protocol: {provider_conf.protocol}")

        credential = self._resolve_credential(provider_conf, credential_slug=credential_slug)
        provider = OpenAICompatibleProvider(
            provider_conf,
            credential,
            client_factory=self.client_factory,
        )
        return OpenAICompatibleModel(provider)

    def generate(self, prompt: str, **kwargs: Any) -> ModelCallResult:
        call_type = self._call_type_from_kwargs(kwargs)
        request_id = new_model_request_id()
        base_metadata = self._metadata_from_kwargs(kwargs)
        model_id = kwargs.get("model")
        excluded_candidate_keys: set[tuple[str, str, str, str]] = set()
        fallback_history: list[dict[str, Any]] = []
        resolution, call_prompt, call_kwargs = self._prepare_call(
            prompt,
            kwargs,
            excluded_candidate_keys=excluded_candidate_keys,
        )

        def finish(
            result: ModelCallResult,
            *,
            effective_prompt: str = call_prompt,
            effective_kwargs: dict[str, Any] = call_kwargs,
        ) -> ModelCallResult:
            return self._finalize_generate_result(
                result,
                prompt=effective_prompt,
                call_kwargs=effective_kwargs,
                original_kwargs=kwargs,
            )

        while True:
            metadata = {**base_metadata, **resolution.metadata}
            if not resolution.success:
                result = ModelCallResult.fail(
                    resolution.code or ModelErrorCode.MODEL_CALL_FAILED.value,
                    resolution.error or "model route could not be resolved",
                    provider=resolution.provider or self.model_name,
                    protocol=resolution.protocol,
                    provider_conf_id=resolution.provider_conf_id,
                    credential_slug=resolution.credential_slug,
                    model=resolution.model or model_id,
                    route=resolution.route,
                    call_type=call_type,
                    request_id=request_id,
                    selected_candidate=resolution.selected_candidate,
                    metadata=metadata,
                )
                return finish(self._attach_fallback_metadata(result, fallback_history))

            model = self._model_for_resolution(resolution)
            if model is None:
                result = ModelCallResult.fail(
                    self._error_code_for_exception(self.init_error),
                    str(self.init_error or "model manager is not initialized"),
                    provider=resolution.provider or self.model_name,
                    protocol=resolution.protocol,
                    provider_conf_id=resolution.provider_conf_id,
                    credential_slug=resolution.credential_slug,
                    model=resolution.model or model_id,
                    route=resolution.route,
                    call_type=call_type,
                    request_id=request_id,
                    selected_candidate=resolution.selected_candidate,
                    metadata=metadata,
                )
            else:
                result = self._generate_with_retry(
                    model,
                    call_prompt,
                    call_kwargs,
                    resolution=resolution,
                    metadata=metadata,
                    request_id=request_id,
                    call_type=call_type,
                    model_id=model_id,
                    allow_retry=bool(kwargs.get("allow_retry", True)),
                )

            self._record_candidate_result(resolution, result)
            if result.success:
                return finish(self._finalize_fallback_success(result, fallback_history))
            if not self._should_fallback_to_next_candidate(resolution, result, kwargs):
                return finish(self._attach_fallback_metadata(result, fallback_history))

            candidate_key = self._candidate_key_for_resolution(resolution)
            if candidate_key is None or candidate_key in excluded_candidate_keys:
                return finish(self._attach_fallback_metadata(result, fallback_history))
            excluded_candidate_keys.add(candidate_key)
            fallback_history.append(
                {
                    "candidate_key": candidate_key,
                    "provider_conf_id": resolution.provider_conf_id,
                    "credential_slug": resolution.credential_slug,
                    "model": resolution.model,
                    "code": result.code,
                    "error": str(result.error or "")[:200],
                }
            )

            next_resolution, next_prompt, next_kwargs = self._prepare_call(
                prompt,
                kwargs,
                excluded_candidate_keys=excluded_candidate_keys,
            )
            if (
                not next_resolution.success
                or next_resolution.selected_candidate is None
            ):
                return finish(self._attach_fallback_metadata(result, fallback_history))
            resolution, call_prompt, call_kwargs = (
                next_resolution,
                next_prompt,
                next_kwargs,
            )

    def stream_generate(self, prompt: str, **kwargs: Any) -> Generator[ModelStreamChunk, None, None]:
        started_at = time.monotonic()
        request_id = new_model_request_id()
        call_type = self._call_type_from_kwargs(kwargs)
        model_id = kwargs.get("model")
        metadata = self._metadata_from_kwargs(kwargs)
        resolution, call_prompt, call_kwargs = self._prepare_call(prompt, kwargs)
        metadata.update(resolution.metadata)
        if not resolution.success:
            chunk = ModelStreamChunk(
                success=False,
                code=resolution.code or ModelErrorCode.MODEL_CALL_FAILED.value,
                error=resolution.error or "model route could not be resolved",
                request_id=request_id,
                provider=resolution.provider or self.model_name,
                model=resolution.model or model_id,
                metadata=metadata,
                is_final=True,
            )
            self._record_stream_result(
                success=False,
                content="",
                code=chunk.code,
                error=chunk.error,
                request_id=request_id,
                resolution=resolution,
                model_id=model_id,
                call_type=call_type,
                metadata=metadata,
                prompt=call_prompt,
                call_kwargs=call_kwargs,
                original_kwargs=kwargs,
                started_at=started_at,
                chunks_count=0,
            )
            yield chunk
            return
        model = self._model_for_resolution(resolution)
        if model is None:
            chunk = ModelStreamChunk(
                success=False,
                code=self._error_code_for_exception(self.init_error),
                error=str(self.init_error or "model manager is not initialized"),
                request_id=request_id,
                provider=resolution.provider or self.model_name,
                model=resolution.model or model_id,
                metadata=metadata,
                is_final=True,
            )
            self._record_stream_result(
                success=False,
                content="",
                code=chunk.code,
                error=chunk.error,
                request_id=request_id,
                resolution=resolution,
                model_id=model_id,
                call_type=call_type,
                metadata=metadata,
                prompt=call_prompt,
                call_kwargs=call_kwargs,
                original_kwargs=kwargs,
                started_at=started_at,
                chunks_count=0,
            )
            yield chunk
            return
        try:
            iterator = iter(model.stream_generate(call_prompt, **call_kwargs))
            previous: str | ModelStreamChunk | None = None
            index = 0
            content_parts: list[str] = []
            for raw_chunk in iterator:
                if previous is not None:
                    chunk = self._stream_chunk_from_provider_chunk(
                        previous,
                        request_id=request_id,
                        index=index,
                        is_final=False,
                        model_id=resolution.model or model_id,
                        metadata=metadata,
                    )
                    content_parts.append(chunk.content_delta)
                    yield chunk
                    index += 1
                previous = raw_chunk
            if previous is not None:
                chunk = self._stream_chunk_from_provider_chunk(
                    previous,
                    request_id=request_id,
                    index=index,
                    is_final=True,
                    model_id=resolution.model or model_id,
                    metadata=metadata,
                )
                content_parts.append(chunk.content_delta)
                yield chunk
                chunks_count = index + 1
            else:
                chunk = ModelStreamChunk(
                    success=True,
                    content_delta="",
                    index=0,
                    is_final=True,
                    request_id=request_id,
                    provider=resolution.provider or self.model_name,
                    model=resolution.model or model_id,
                    metadata=metadata,
                )
                yield chunk
                chunks_count = 0
            self._record_stream_result(
                success=True,
                content="".join(content_parts),
                code=None,
                error=None,
                request_id=request_id,
                resolution=resolution,
                model_id=model_id,
                call_type=call_type,
                metadata=metadata,
                prompt=call_prompt,
                call_kwargs=call_kwargs,
                original_kwargs=kwargs,
                started_at=started_at,
                chunks_count=chunks_count,
            )
        except Exception as exc:
            chunk = ModelStreamChunk(
                success=False,
                code=self._error_code_for_exception(exc),
                error=str(exc),
                request_id=request_id,
                provider=resolution.provider or self.model_name,
                model=resolution.model or model_id,
                metadata=metadata,
                is_final=True,
            )
            self._record_stream_result(
                success=False,
                content="",
                code=chunk.code,
                error=chunk.error,
                request_id=request_id,
                resolution=resolution,
                model_id=model_id,
                call_type=call_type,
                metadata=metadata,
                prompt=call_prompt,
                call_kwargs=call_kwargs,
                original_kwargs=kwargs,
                started_at=started_at,
                chunks_count=0,
            )
            yield chunk

    def generate_json(
        self,
        prompt: str,
        *,
        parse_mode: str | None = None,
        schema_name: str | None = None,
        schema: dict[str, Any] | None = None,
        max_repair_attempts: int | None = None,
        repair_enabled: bool | None = None,
        **kwargs: Any,
    ) -> StructuredModelResult:
        call_type = self._call_type_from_kwargs(kwargs)
        mode = self._structured_parse_mode(call_type, parse_mode)
        target_schema = schema or self._structured_schema(schema_name)
        repair_limit = self._structured_repair_attempts(max_repair_attempts)
        repair_allowed = self._structured_repair_enabled(repair_enabled)
        call_kwargs = dict(kwargs)
        call_kwargs.setdefault("json_mode", True)
        call_kwargs.setdefault("metadata", {})
        call_kwargs["metadata"] = {
            **dict(call_kwargs.get("metadata") or {}),
            "structured_output": True,
            "schema_name": schema_name,
            "parse_mode": mode,
        }

        model_result = self.generate(prompt, **call_kwargs)
        if not model_result.success:
            return StructuredModelResult(
                success=False,
                code=model_result.code or ModelErrorCode.MODEL_CALL_FAILED,
                error=model_result.error or "structured model call failed",
                parse_mode=mode,
                schema_name=schema_name,
                schema_valid=None,
                model_result=model_result,
                metadata={"stage": "model_call"},
            )

        current_prompt = prompt
        current_result = model_result
        last_error = ""
        repair_attempts_used = 0

        for attempt in range(repair_limit + 1):
            parsed = parse_json_output(current_result.content, parse_mode=mode)
            if parsed.success and parsed.data is not None:
                schema_check = validate_json_schema(parsed.data, target_schema)
                if schema_check.valid:
                    return StructuredModelResult(
                        success=True,
                        data=parsed.data,
                        content=current_result.content,
                        parse_mode=mode,
                        schema_name=schema_name,
                        schema_valid=True if target_schema else None,
                        repair_attempts=repair_attempts_used,
                        model_result=current_result,
                        raw_json_text=parsed.raw_json_text,
                        metadata={
                            **parsed.metadata,
                            "repair_used": repair_attempts_used > 0,
                        },
                    )
                last_error = "; ".join(schema_check.errors)
                failure_code = ModelErrorCode.SCHEMA_INVALID
            else:
                last_error = parsed.error or "invalid JSON"
                failure_code = ModelErrorCode.JSON_REPAIR_FAILED if repair_attempts_used else ModelErrorCode.INVALID_JSON

            if not repair_allowed or attempt >= repair_limit:
                final_code = (
                    ModelErrorCode.SCHEMA_INVALID
                    if failure_code == ModelErrorCode.SCHEMA_INVALID
                    else (
                        ModelErrorCode.JSON_REPAIR_FAILED
                        if repair_attempts_used or repair_allowed
                        else ModelErrorCode.INVALID_JSON
                    )
                )
                return StructuredModelResult(
                    success=False,
                    code=final_code,
                    error=last_error,
                    content=current_result.content,
                    parse_mode=mode,
                    schema_name=schema_name,
                    schema_valid=False if target_schema else None,
                    repair_attempts=repair_attempts_used,
                    model_result=current_result,
                    metadata={
                        "repair_enabled": repair_allowed,
                        "repair_exhausted": repair_allowed and repair_limit > 0,
                    },
                )

            repair_prompt = build_json_repair_prompt(
                original_prompt=prompt,
                raw_output=current_result.content,
                parse_error=last_error,
                parse_mode=mode,
                schema_name=schema_name,
                schema=target_schema,
            )
            repair_kwargs = dict(call_kwargs)
            repair_kwargs["metadata"] = {
                **dict(repair_kwargs.get("metadata") or {}),
                "structured_repair": True,
                "repair_attempt": attempt + 1,
                "repair_prompt_source": "models.generate_json",
            }
            current_prompt = repair_prompt
            current_result = self.generate(current_prompt, **repair_kwargs)
            repair_attempts_used += 1
            if not current_result.success:
                return StructuredModelResult(
                    success=False,
                    code=current_result.code or ModelErrorCode.JSON_REPAIR_FAILED,
                    error=current_result.error or "structured repair model call failed",
                    parse_mode=mode,
                    schema_name=schema_name,
                    schema_valid=None,
                    repair_attempts=repair_attempts_used,
                    model_result=current_result,
                    metadata={"stage": "repair_model_call"},
                )

    def health_check(self) -> ModelHealthStatus:
        if self.model_name == "mock":
            return ModelHealthStatus(
                healthy=self.model is not None,
                provider_conf_id=self.provider_conf_id,
                provider="mock",
                protocol=ModelProviderProtocol.MOCK.value,
                model="mock-v1",
                configured=self.model is not None,
                check_type="config_check",
                error=str(self.init_error) if self.init_error else None,
                code=self._error_code_for_exception(self.init_error) if self.init_error else None,
            )
        provider_conf = None
        if self.models_config is not None and self.provider_conf_id:
            provider_conf = self.models_config.get_provider_conf(self.provider_conf_id)
        if provider_conf is None and self.init_error is not None:
            return ModelHealthStatus(
                healthy=False,
                provider_conf_id=self.provider_conf_id,
                provider=self.model_name,
                configured=False,
                check_type="config_check",
                error=str(self.init_error),
                code=self._error_code_for_exception(self.init_error),
            )
        if provider_conf is None:
            try:
                provider_conf = self._resolve_provider_conf(self.model_name)
            except Exception as exc:
                return ModelHealthStatus(
                    healthy=False,
                    provider_conf_id=self.provider_conf_id,
                    provider=self.model_name,
                    configured=False,
                    check_type="config_check",
                    error=str(exc),
                    code=self._error_code_for_exception(exc),
                )
        return self._config_health_check(provider_conf)

    def verify_provider_config(self, provider_conf_id: str) -> ModelHealthStatus:
        if self.models_config is None:
            return ModelHealthStatus(
                healthy=False,
                provider_conf_id=provider_conf_id,
                configured=False,
                check_type="live_check",
                code=ModelErrorCode.MISSING_MODEL_CONFIG,
                error="models configuration is not available",
            )

        provider_conf = self.models_config.get_provider_conf(provider_conf_id)
        if provider_conf is None:
            return ModelHealthStatus(
                healthy=False,
                provider_conf_id=provider_conf_id,
                configured=False,
                check_type="live_check",
                code=ModelErrorCode.MISSING_MODEL_CONFIG,
                error=f"provider config not found: {provider_conf_id}",
            )

        config_status = self._config_health_check(provider_conf, check_type="live_check")
        if not config_status.healthy:
            self._mark_provider_verify_failure(provider_conf, config_status)
            return config_status

        credential = self._resolve_credential(provider_conf)
        provider = OpenAICompatibleProvider(
            provider_conf,
            credential,
            client_factory=self.client_factory,
        )
        started_at = datetime.now(timezone.utc)
        result = provider.generate(
            ModelCallOptions(
                call_type=ModelCallType.CHAT,
                provider_conf_id=provider_conf.id,
                credential_slug=credential.slug,
                model=provider_conf.model_id or provider_conf.default_model,
                prompt=str(provider_conf.verify.get("prompt") or "ping"),
                max_tokens=int(provider_conf.verify.get("max_tokens") or 8),
                temperature=0.0,
                metadata={"verify": True},
            )
        )
        latency_ms = result.latency_ms
        if latency_ms is None:
            latency_ms = int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000)

        if result.success:
            verified_at = datetime.now(timezone.utc).isoformat()
            provider_conf.status = "active"
            provider_conf.verified_at = verified_at
            return ModelHealthStatus(
                healthy=True,
                provider_conf_id=provider_conf.id,
                provider=provider_conf.provider,
                protocol=provider_conf.protocol,
                model=result.model or provider_conf.model_id or provider_conf.default_model,
                configured=True,
                check_type="live_check",
                latency_ms=latency_ms,
                verified_at=verified_at,
                metadata={"provider_status": provider_conf.status},
            )

        status = ModelHealthStatus(
            healthy=False,
            provider_conf_id=provider_conf.id,
            provider=provider_conf.provider,
            protocol=provider_conf.protocol,
            model=result.model or provider_conf.model_id or provider_conf.default_model,
            configured=True,
            check_type="live_check",
            latency_ms=latency_ms,
            code=result.code or ModelErrorCode.MODEL_CALL_FAILED,
            error=result.error or "provider verify failed",
            verified_at=provider_conf.verified_at,
            metadata={"provider_status": "error"},
        )
        self._mark_provider_verify_failure(provider_conf, status)
        return status

    def list_enabled_models(self) -> list[dict[str, Any]]:
        if self.models_config is None:
            return []
        return [
            self._provider_config_metadata(provider_conf)
            for provider_conf in self.models_config.list_provider_confs()
        ]

    def list_provider_configs(self, *, include_disabled: bool = False) -> list[dict[str, Any]]:
        if self.models_config is None:
            return []
        return [
            self._provider_config_metadata(provider_conf)
            for provider_conf in self.models_config.list_provider_confs(
                include_disabled=include_disabled,
            )
        ]

    def get_provider_config(self, provider_conf_id: str) -> dict[str, Any] | None:
        if self.models_config is None:
            return None
        provider_conf = self.models_config.get_provider_conf(provider_conf_id)
        if provider_conf is None:
            return None
        return self._provider_config_metadata(provider_conf)

    def enable_provider_config(self, provider_conf_id: str) -> dict[str, Any] | None:
        if self.models_config is None:
            return None
        provider_conf = self.models_config.get_provider_conf(provider_conf_id)
        if provider_conf is None:
            return None
        provider_conf.enabled = True
        if provider_conf.status == "disabled":
            provider_conf.status = "unverified"
        for key in list(self._route_models):
            if key[0] == provider_conf.id:
                self._route_models.pop(key, None)
        return self._provider_config_metadata(provider_conf)

    def disable_provider_config(self, provider_conf_id: str) -> dict[str, Any] | None:
        if self.models_config is None:
            return None
        provider_conf = self.models_config.get_provider_conf(provider_conf_id)
        if provider_conf is None:
            return None
        provider_conf.enabled = False
        if provider_conf.status == "active":
            provider_conf.status = "disabled"
        for key in list(self._route_models):
            if key[0] == provider_conf.id:
                self._route_models.pop(key, None)
        return self._provider_config_metadata(provider_conf)

    def get_default_routes(self) -> dict[str, Any]:
        if self.models_config is None:
            return {}
        return {
            "defaults": self.models_config.default_routes(),
            "routes": {
                route_name: route_config.to_dict()
                for route_name, route_config in sorted(self.models_config.routes.items())
            },
        }

    def compress_context(
        self,
        *,
        source_type: str = "text",
        text: str | None = None,
        chunks: list[Any] | None = None,
        target_tokens: int | None = None,
        target_chars: int | None = None,
        preserve_keys: list[str] | None = None,
        preserve_entities: list[str] | None = None,
        trigger_reason: str | None = None,
        metadata: dict[str, Any] | None = None,
        allow_rule_fallback: bool = False,
        max_chunk_chars: int | None = None,
    ) -> ContextCompressionResult:
        if self.model_name != "mock" and self.model is None and self.init_error is not None:
            return self._compression_failure_result(
                self._error_code_for_exception(self.init_error),
                str(self.init_error),
                trigger_reason=trigger_reason,
                metadata=metadata,
            )

        generate_json = getattr(self, "generate_json", None)
        if not callable(generate_json):
            return self._compression_failure_result(
                ModelErrorCode.COMPRESSION_FAILED,
                "model manager cannot produce structured compression output",
                trigger_reason=trigger_reason,
                metadata=metadata,
            )

        try:
            return compress_context_with_model(
                generate_json,
                source_type=source_type,
                text=text,
                chunks=chunks,
                target_tokens=target_tokens,
                target_chars=target_chars,
                preserve_keys=preserve_keys,
                preserve_entities=preserve_entities,
                trigger_reason=trigger_reason,
                metadata=metadata,
                allow_rule_fallback=allow_rule_fallback,
                max_chunk_chars=max_chunk_chars,
            )
        except Exception as exc:
            return self._compression_failure_result(
                ModelErrorCode.COMPRESSION_FAILED,
                str(exc),
                trigger_reason=trigger_reason,
                metadata=metadata,
        )

    def embed_text(
        self,
        text: str,
        *,
        model: str | None = None,
        route: str | None = None,
        provider_conf_id: str | None = None,
        credential_slug: str | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> EmbeddingResult:
        texts = [normalize_embedding_text(text)]
        batch = self.embed_texts(
            texts,
            model=model,
            route=route,
            provider_conf_id=provider_conf_id,
            credential_slug=credential_slug,
            metadata=metadata,
            **kwargs,
        )
        if not batch.success:
            return embedding_failure(
                batch.code or ModelErrorCode.EMBEDDING_FAILED,
                batch.error or "embedding call failed",
                provider_conf_id=provider_conf_id or self.provider_conf_id,
                model=model,
                metadata=batch.metadata,
            )
        if batch.item_results:
            result = batch.item_results[0]
            result.metadata = {**batch.metadata, **result.metadata}
            return result
        return embedding_failure(
            ModelErrorCode.EMBEDDING_FAILED,
            "embedding call returned no results",
            provider_conf_id=provider_conf_id or self.provider_conf_id,
            model=model,
            metadata=batch.metadata,
        )

    def embed_texts(
        self,
        texts: list[Any],
        *,
        model: str | None = None,
        route: str | None = None,
        provider_conf_id: str | None = None,
        credential_slug: str | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        normalized_texts = normalize_embedding_texts(texts)
        if not normalized_texts:
            return embedding_batch_from_items([], metadata={"empty_input": True, **dict(metadata or {})})

        call_kwargs = dict(kwargs)
        call_kwargs["call_type"] = ModelCallType.EMBEDDING.value
        call_kwargs["route"] = route or call_kwargs.get("route") or (self.models_config.runtime.default_embedding_route if self.models_config is not None else "embedding")
        if provider_conf_id is not None:
            call_kwargs["provider_conf_id"] = provider_conf_id
        elif self.provider_conf_id is not None:
            call_kwargs["provider_conf_id"] = self.provider_conf_id
        if credential_slug is not None:
            call_kwargs["credential_slug"] = credential_slug
        elif self.credential_slug is not None:
            call_kwargs["credential_slug"] = self.credential_slug
        if metadata is not None or call_kwargs.get("metadata") is None:
            call_kwargs["metadata"] = dict(metadata or call_kwargs.get("metadata") or {})
        joined_prompt = "\n".join(normalized_texts)
        if model is not None:
            call_kwargs["model"] = model

        resolution, call_prompt, prepared_kwargs = self._prepare_call(
            joined_prompt,
            call_kwargs,
        )
        if not resolution.success:
            return embedding_batch_failure(
                resolution.code or ModelErrorCode.EMBEDDING_FAILED,
                resolution.error or "embedding route could not be resolved",
                metadata={
                    **resolution.metadata,
                    "route": resolution.route,
                    "call_type": ModelCallType.EMBEDDING.value,
                },
            )
        if not self._provider_supports_embedding(resolution):
            return embedding_batch_failure(
                ModelErrorCode.UNSUPPORTED_PROVIDER,
                f"provider does not support embedding: {resolution.provider}",
                metadata={
                    **resolution.metadata,
                    "route": resolution.route,
                    "call_type": ModelCallType.EMBEDDING.value,
                },
            )

        embedding_model = prepared_kwargs.get("model") or resolution.model
        if resolution.provider != "mock" and model is None and resolution.selected_candidate is None:
            return embedding_batch_failure(
                ModelErrorCode.MISSING_MODEL_CONFIG,
                "embedding model is not configured; use an embedding route candidate or explicit model",
                metadata={
                    **resolution.metadata,
                    "route": resolution.route,
                    "call_type": ModelCallType.EMBEDDING.value,
                },
            )
        if resolution.provider == "mock" and model is None and resolution.selected_candidate is None:
            embedding_model = DEFAULT_EMBEDDING_MODEL
        elif resolution.provider == "mock" and not embedding_model:
            embedding_model = DEFAULT_EMBEDDING_MODEL
        prepared_kwargs["model"] = embedding_model
        prepared_kwargs["route"] = resolution.route
        prepared_kwargs["call_type"] = ModelCallType.EMBEDDING.value

        model_instance = self._model_for_resolution(resolution)
        if model_instance is None:
            return embedding_batch_failure(
                self._error_code_for_exception(self.init_error),
                str(self.init_error or "model manager is not initialized"),
                metadata={
                    **resolution.metadata,
                    "route": resolution.route,
                    "call_type": ModelCallType.EMBEDDING.value,
                },
            )

        item_results = self._invoke_embedding_call(
            model_instance,
            normalized_texts,
            resolution=resolution,
            call_kwargs=prepared_kwargs,
            model_name=embedding_model,
        )
        if isinstance(item_results, EmbeddingBatchResult):
            item_results.metadata = {
                **resolution.metadata,
                "route": resolution.route,
                "call_type": ModelCallType.EMBEDDING.value,
                **dict(metadata or {}),
                **item_results.metadata,
            }
            return item_results
        if isinstance(item_results, list):
            return embedding_batch_from_items(
                item_results,
                metadata={
                    **resolution.metadata,
                    "route": resolution.route,
                    "call_type": ModelCallType.EMBEDDING.value,
                    **dict(metadata or {}),
                },
            )
        return item_results

    def _provider_supports_embedding(self, resolution: RouteResolution) -> bool:
        if self.models_config is None:
            return resolution.provider == "mock"
        provider_spec = self.models_config.get_provider_spec(resolution.provider or "")
        return bool(provider_spec and provider_spec.supports_embedding)

    def get_model_info(self) -> Dict[str, Any]:
        health = self.health_check()
        return {
            "model_name": self.model_name,
            "provider_conf_id": self.provider_conf_id,
            "model": self.model.__class__.__name__ if self.model else None,
            "healthy": health.healthy,
            "health": health.to_dict(),
            "init_error": str(self.init_error) if self.init_error else None,
        }

    def _invoke_embedding_call(
        self,
        model: BaseModel,
        texts: list[str],
        *,
        resolution: RouteResolution,
        call_kwargs: dict[str, Any],
        model_name: str | None,
    ) -> list[EmbeddingResult] | EmbeddingBatchResult:
        try:
            if hasattr(model, "embed_texts") and callable(getattr(model, "embed_texts")):
                batch_result = model.embed_texts(texts, **call_kwargs)
                if isinstance(batch_result, EmbeddingBatchResult):
                    return batch_result
                if hasattr(batch_result, "item_results"):
                    return batch_result.item_results
                if hasattr(batch_result, "embeddings"):
                    item_results = [
                        EmbeddingResult(
                            success=True,
                            embedding=list(embedding),
                            provider_conf_id=resolution.provider_conf_id,
                            model=model_name or resolution.model,
                            metadata={"index": index},
                        )
                        for index, embedding in enumerate(getattr(batch_result, "embeddings", []) or [])
                    ]
                    return item_results
                return batch_result
            if hasattr(model, "embed_text") and callable(getattr(model, "embed_text")):
                from src.models.protocol import EmbeddingResult

                item_results = [
                    model.embed_text(
                        text,
                        **call_kwargs,
                    )
                    for text in texts
                ]
                if all(isinstance(item, EmbeddingResult) for item in item_results):
                    return item_results
                return item_results
        except Exception as exc:
            return embedding_batch_failure(
                self._error_code_for_exception(exc),
                str(exc),
                metadata={**resolution.metadata, "call_type": ModelCallType.EMBEDDING.value},
            )
        return embedding_batch_failure(
            ModelErrorCode.EMBEDDING_FAILED,
            "model does not support embedding operations",
            metadata={**resolution.metadata, "call_type": ModelCallType.EMBEDDING.value},
        )

    def _compression_failure_result(
        self,
        code: str | ModelErrorCode,
        error: str,
        *,
        trigger_reason: str | None,
        metadata: dict[str, Any] | None,
    ) -> Any:
        from src.models.protocol import ContextCompressionResult

        return ContextCompressionResult(
            success=False,
            code=code,
            error=error,
            trigger_reason=trigger_reason,
            metadata=dict(metadata or {}),
        )

    def _provider_config_metadata(self, provider_conf: ProviderConf) -> dict[str, Any]:
        provider_spec = (
            self.models_config.get_provider_spec(provider_conf.provider)
            if self.models_config is not None
            else None
        )
        safe_metadata = self._management_safe_metadata(provider_conf.metadata)
        credentials = list(provider_conf.credentials)
        active_credential_slug = self._active_credential_slug(provider_conf)
        is_builtin = "builtin" in provider_conf.tags or provider_conf.id == f"conf_{provider_conf.provider}_default"
        is_default = provider_conf.id == f"conf_{provider_conf.provider}_default"
        configured_model = provider_conf.model_id or provider_conf.default_model
        max_context_tokens = provider_conf.max_context_tokens
        if max_context_tokens is None and provider_spec is not None:
            max_context_tokens = provider_spec.max_context_tokens

        return {
            "id": provider_conf.id,
            "name": provider_conf.name,
            "display_name": safe_metadata.get("display_name")
            or (provider_spec.display_name if provider_spec else provider_conf.name),
            "alias": safe_metadata.get("alias"),
            "description": safe_metadata.get("description"),
            "provider": provider_conf.provider,
            "protocol": provider_conf.protocol,
            "base_url": provider_conf.base_url,
            "default_model": configured_model,
            "custom_models": list(provider_conf.custom_models),
            "enabled": provider_conf.enabled,
            "status": provider_conf.status,
            "verified_at": provider_conf.verified_at,
            "last_used_at": provider_conf.last_used_at,
            "last_error_code": safe_metadata.get("last_error_code")
            or safe_metadata.get("last_verify_code"),
            "last_error_at": safe_metadata.get("last_error_at"),
            "created_at": safe_metadata.get("created_at"),
            "updated_at": safe_metadata.get("updated_at"),
            "created_by": safe_metadata.get("created_by"),
            "supports_streaming": bool(provider_spec and provider_spec.supports_streaming),
            "supports_json_mode": bool(provider_spec and provider_spec.supports_json_mode),
            "supports_embedding": bool(provider_spec and provider_spec.supports_embedding),
            "supports_vision": bool(provider_spec and provider_spec.supports_vision),
            "supports_tool_calling": bool(provider_spec and provider_spec.supports_tool_calling),
            "supports_custom_headers": bool(provider_spec and provider_spec.supports_custom_headers),
            "max_context_tokens": max_context_tokens,
            "tags": list(provider_conf.tags),
            "labels": self._string_list_metadata(safe_metadata.get("labels")),
            "group": safe_metadata.get("group"),
            "sort_order": self._optional_int(safe_metadata.get("sort_order")),
            "is_builtin": is_builtin,
            "is_custom": not is_builtin,
            "is_default": is_default,
            "is_verified": bool(provider_conf.verified_at and provider_conf.status == "active"),
            "is_available_for_chat": bool(provider_conf.enabled and configured_model),
            "is_available_for_embedding": bool(
                provider_conf.enabled and provider_spec and provider_spec.supports_embedding
            ),
            "is_available_for_structured_output": bool(
                provider_conf.enabled and provider_spec and provider_spec.supports_json_mode
            ),
            "credential_count": len(credentials),
            "active_credential_slug": active_credential_slug,
            "metadata": {
                **safe_metadata,
                "credential_statuses": [
                    {
                        "slug": credential.slug,
                        "enabled": credential.enabled,
                        "status": credential.status,
                        "last_error_code": credential.last_error_code,
                        "last_error_at": credential.last_error_at,
                    }
                    for credential in credentials
                ],
            },
        }

    def _active_credential_slug(self, provider_conf: ProviderConf) -> str | None:
        if self.provider_conf_id == provider_conf.id and self.credential_slug:
            return self.credential_slug
        for credential in provider_conf.credentials:
            if credential.enabled:
                return credential.slug
        return None

    def _management_safe_metadata(self, metadata: dict[str, Any] | None) -> dict[str, Any]:
        from src.models.observability import sanitize_observability_value

        return dict(sanitize_observability_value(metadata or {}))

    def _string_list_metadata(self, value: Any) -> list[str]:
        if isinstance(value, (list, tuple, set)):
            return [str(item) for item in value if str(item).strip()]
        if value is None:
            return []
        return [str(value)]

    def _optional_int(self, value: Any) -> int | None:
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _resolve_provider_conf(self, model_name: str) -> ProviderConf:
        if self.models_config is None:
            raise ValueError("models configuration is not available")

        if self.provider_conf_id:
            provider_conf = self.models_config.get_provider_conf(self.provider_conf_id)
            if provider_conf is None:
                raise ValueError(f"provider config not found: {self.provider_conf_id}")
            return provider_conf

        default_conf = self.models_config.get_provider_conf(f"conf_{model_name}_default")
        if default_conf is not None:
            return default_conf
        return configured_builtin_provider_conf(model_name, models_config=self.models_config)

    def _resolve_credential(
        self,
        provider_conf: ProviderConf,
        *,
        credential_slug: str | None = None,
    ) -> CredentialResolution:
        credentials = list(provider_conf.credentials)
        requested_slug = credential_slug or self.credential_slug
        if requested_slug:
            credentials = [item for item in credentials if item.slug == requested_slug]
        if not credentials:
            credentials = [
                ProviderCredential(
                    slug=requested_slug or "default",
                    api_key_env={
                        "openai": "OPENAI_API_KEY",
                        "qianwen": "QIANWEN_API_KEY",
                        "doubao": "DOUBAO_API_KEY",
                    }.get(provider_conf.provider),
                )
            ]
        return resolve_credential_secret(
            credentials[0],
            credential_lookup=self.credential_lookup,
        )

    def _prepare_call(
        self,
        prompt: str,
        kwargs: dict[str, Any],
        *,
        excluded_candidate_keys: set[tuple[str, str, str, str]] | None = None,
    ) -> tuple[RouteResolution, str, dict[str, Any]]:
        if self.router is None:
            return (
                RouteResolution.fail(
                    ModelErrorCode.MISSING_MODEL_CONFIG.value,
                    "models router is not available",
                    provider=self.model_name,
                ),
                prompt,
                {},
            )

        try:
            options = ModelCallOptions(
                call_type=kwargs.get("call_type", ModelCallType.CHAT),
                route=kwargs.get("route"),
                provider_conf_id=kwargs.get("provider_conf_id"),
                credential_slug=kwargs.get("credential_slug") or self.credential_slug,
                model=kwargs.get("model"),
                messages=kwargs.get("messages"),
                prompt=prompt,
                temperature=kwargs.get("temperature"),
                top_p=kwargs.get("top_p"),
                top_k=kwargs.get("top_k"),
                max_tokens=kwargs.get("max_tokens"),
                timeout_seconds=kwargs.get("timeout_seconds"),
                max_retries=kwargs.get("max_retries"),
                json_mode=kwargs.get("json_mode"),
                response_format=kwargs.get("response_format"),
                allow_fallback=bool(kwargs.get("allow_fallback", True)),
                allow_retry=bool(kwargs.get("allow_retry", True)),
                allow_truncation=bool(kwargs.get("allow_truncation", False)),
                allow_external_provider=kwargs.get("allow_external_provider"),
                sensitive_content_policy=kwargs.get("sensitive_content_policy"),
                redact_before_send=kwargs.get("redact_before_send"),
                trace_context=kwargs.get("trace_context"),
                metadata=kwargs.get("metadata") or {},
            )
        except Exception as exc:
            return (
                RouteResolution.fail(
                    ModelErrorCode.INVALID_REQUEST.value,
                    str(exc),
                    provider=self.model_name,
                ),
                prompt,
                {},
            )

        resolution = self.router.resolve(
            options,
            default_provider_conf_id=self.provider_conf_id,
            default_provider=self.model_name,
            excluded_candidate_keys=excluded_candidate_keys,
        )
        call_prompt, messages = self._apply_truncation(prompt, options, resolution)

        call_kwargs = {
            key: value
            for key, value in kwargs.items()
            if key not in ROUTE_PARAMETER_NAMES
            and key
            not in {
                "allow_external_provider",
                "allow_fallback",
                "allow_retry",
                "allow_truncation",
                "credential_slug",
                "provider_conf_id",
                "redact_before_send",
                "route",
                "sensitive_content_policy",
            }
        }
        call_kwargs.update(resolution.params)
        call_kwargs["call_type"] = options.call_type
        if resolution.route is not None:
            call_kwargs["route"] = resolution.route
        if resolution.provider_conf_id is not None:
            call_kwargs["provider_conf_id"] = resolution.provider_conf_id
        if resolution.credential_slug is not None:
            call_kwargs["credential_slug"] = resolution.credential_slug
        if resolution.model is not None:
            call_kwargs["model"] = resolution.model
        if messages is not None:
            call_kwargs["messages"] = messages
        if options.response_format is not None:
            call_kwargs["response_format"] = options.response_format

        metadata = dict(kwargs.get("metadata") or {})
        metadata.update(resolution.metadata)
        call_kwargs["metadata"] = metadata
        return resolution, call_prompt, call_kwargs

    def _candidate_key_for_resolution(
        self,
        resolution: RouteResolution,
    ) -> tuple[str, str, str, str] | None:
        if (
            resolution.selected_candidate is None
            or resolution.route is None
            or resolution.provider_conf_id is None
        ):
            return None
        return make_candidate_key(
            resolution.route,
            resolution.provider_conf_id,
            resolution.credential_slug,
            resolution.model,
        )

    def _record_candidate_result(
        self,
        resolution: RouteResolution,
        result: ModelCallResult,
    ) -> None:
        candidate_key = self._candidate_key_for_resolution(resolution)
        if candidate_key is None:
            return
        _, provider_conf_id, credential_slug, model = candidate_key
        if result.success:
            self.health_registry.record_success(
                resolution.route or "",
                provider_conf_id,
                credential_slug,
                model,
            )
        elif is_fallback_allowed(result.code):
            self.health_registry.record_failure(
                resolution.route or "",
                provider_conf_id,
                credential_slug,
                model,
                result.code,
            )

    def _should_fallback_to_next_candidate(
        self,
        resolution: RouteResolution,
        result: ModelCallResult,
        kwargs: dict[str, Any],
    ) -> bool:
        if result.success or not bool(kwargs.get("allow_fallback", True)):
            return False
        if resolution.policy != "explicit_candidates":
            return False
        if resolution.selected_candidate is None:
            return False
        if any(
            kwargs.get(field_name) is not None
            for field_name in ("provider_conf_id", "credential_slug", "model")
        ):
            return False
        return is_fallback_allowed(result.code)

    def _attach_fallback_metadata(
        self,
        result: ModelCallResult,
        fallback_history: list[dict[str, Any]],
    ) -> ModelCallResult:
        if fallback_history:
            result.metadata = {
                **result.metadata,
                "fallback_attempted": True,
                "fallback_history": list(fallback_history),
                "fallback_attempts": len(fallback_history),
            }
        return result

    def _finalize_fallback_success(
        self,
        result: ModelCallResult,
        fallback_history: list[dict[str, Any]],
    ) -> ModelCallResult:
        if not fallback_history:
            return result
        result.fallback_used = True
        result.fallback_reason = str(fallback_history[0].get("code") or "candidate_failed")
        result.metadata = {
            **result.metadata,
            "fallback_attempted": True,
            "fallback_history": list(fallback_history),
            "fallback_attempts": len(fallback_history),
        }
        return result

    def _apply_truncation(
        self,
        prompt: str,
        options: ModelCallOptions,
        resolution: RouteResolution,
    ) -> tuple[str, list[dict[str, Any]] | None]:
        if not resolution.success or not resolution.metadata.get("truncation_requested"):
            return prompt, None

        max_chars = resolution.max_context_chars
        if max_chars is None and resolution.max_context_tokens is not None:
            max_chars = resolution.max_context_tokens * 4
        if max_chars is None or max_chars <= 0:
            return prompt, None

        messages = options.to_messages()
        original_chars = sum(len(message.content) for message in messages)
        if original_chars <= max_chars:
            resolution.metadata["truncation_used"] = False
            return prompt, None

        remaining = max_chars
        kept: list[dict[str, Any]] = []
        dropped_chars = 0
        for message in reversed(messages):
            content = message.content
            if remaining <= 0:
                dropped_chars += len(content)
                continue
            if len(content) > remaining:
                dropped_chars += len(content) - remaining
                content = content[-remaining:]
            remaining -= len(content)
            item = message.to_dict()
            item["content"] = content
            kept.append(item)
        kept.reverse()

        resolution.context_chars = sum(len(str(item.get("content") or "")) for item in kept)
        resolution.context_tokens = max(0, (resolution.context_chars + 3) // 4)
        resolution.metadata.update(
            {
                "truncation_used": True,
                "dropped_chars": dropped_chars,
                "dropped_tokens": max(0, (dropped_chars + 3) // 4),
                "context_chars": resolution.context_chars,
                "context_tokens": resolution.context_tokens,
            }
        )

        if options.messages:
            return prompt, kept
        truncated_prompt = kept[-1]["content"] if kept else ""
        return truncated_prompt, None

    def _model_for_resolution(self, resolution: RouteResolution) -> BaseModel | None:
        if not resolution.provider_conf_id:
            return self.model

        cache_key = (resolution.provider_conf_id, resolution.credential_slug)
        cached = self._route_models.get(cache_key)
        if cached is not None:
            return cached
        if self.models_config is None:
            self.init_error = ValueError("models configuration is not available")
            return None

        provider_conf = self.models_config.get_provider_conf(resolution.provider_conf_id)
        if provider_conf is None:
            self.init_error = ValueError(f"provider config not found: {resolution.provider_conf_id}")
            return None
        try:
            model = self._create_model_for_provider_conf(
                provider_conf,
                credential_slug=resolution.credential_slug,
            )
        except Exception as exc:
            self.init_error = exc
            return None
        self._route_models[cache_key] = model
        return model

    def _generate_with_retry(
        self,
        model: BaseModel,
        prompt: str,
        call_kwargs: dict[str, Any],
        *,
        resolution: RouteResolution,
        metadata: dict[str, Any],
        request_id: str,
        call_type: str,
        model_id: str | None,
        allow_retry: bool,
    ) -> ModelCallResult:
        policy = self._retry_policy(call_kwargs, allow_retry=allow_retry)
        started_at = time.monotonic()
        retry_history: list[dict[str, Any]] = []
        last_result: ModelCallResult | None = None

        for attempt in range(1, policy.max_attempts + 1):
            if attempt > 1:
                delay_seconds = policy.delay_seconds(attempt - 1)
                if delay_seconds > 0:
                    time.sleep(delay_seconds)

            raw_result, exc = self._invoke_generate_once(
                model,
                prompt,
                call_kwargs,
                timeout_seconds=policy.timeout_seconds,
            )
            if exc is not None:
                result = self._failure_result_from_exception(
                    exc,
                    resolution=resolution,
                    metadata=metadata,
                    request_id=request_id,
                    call_type=call_type,
                    model_id=model_id,
                )
            else:
                result = self._coerce_model_call_result(
                    raw_result,
                    resolution=resolution,
                    metadata=metadata,
                    request_id=request_id,
                    call_type=call_type,
                    model_id=model_id,
                )

            result.attempts = attempt
            result.latency_ms = int((time.monotonic() - started_at) * 1000)
            retry_used = attempt > 1
            result.metadata = {
                **result.metadata,
                "retry_used": retry_used,
                "retry_attempts": attempt - 1,
                "retry_max_retries": policy.max_retries,
            }
            if policy.timeout_seconds is not None:
                result.metadata["timeout_seconds"] = policy.timeout_seconds
            if retry_history:
                result.metadata["retry_history"] = list(retry_history)

            if result.success:
                return result

            should_retry = self._should_retry_result(
                result,
                attempt=attempt,
                policy=policy,
                allow_retry=allow_retry,
            )
            if not should_retry:
                return result

            retry_history.append(
                {
                    "attempt": attempt,
                    "code": result.code,
                    "error": str(result.error or "")[:200],
                }
            )
            last_result = result

        if last_result is not None:
            last_result.metadata["retry_exhausted"] = True
            return last_result
        return ModelCallResult.fail(
            ModelErrorCode.MODEL_CALL_FAILED,
            "model call failed before any attempt completed",
            provider=resolution.provider or self.model_name,
            protocol=resolution.protocol,
            provider_conf_id=resolution.provider_conf_id,
            credential_slug=resolution.credential_slug,
            model=resolution.model or model_id,
            route=resolution.route,
            call_type=call_type,
            request_id=request_id,
            selected_candidate=resolution.selected_candidate,
            metadata=metadata,
        )

    def _retry_policy(self, call_kwargs: dict[str, Any], *, allow_retry: bool) -> RetryPolicy:
        runtime = self.models_config.runtime if self.models_config is not None else None
        configured_retries = call_kwargs.get("max_retries")
        if configured_retries is None and runtime is not None:
            configured_retries = runtime.max_retries
        max_retries = 0 if not allow_retry else int(configured_retries or 0)
        base_delay = runtime.retry_backoff_base_seconds if runtime is not None else 0.5
        max_delay = runtime.retry_backoff_max_seconds if runtime is not None else 8.0
        timeout = self._optional_positive_float(call_kwargs.get("timeout_seconds"))
        return RetryPolicy(
            max_retries=max_retries,
            base_delay_seconds=base_delay,
            max_delay_seconds=max_delay,
            timeout_seconds=timeout,
        )

    def _invoke_generate_once(
        self,
        model: BaseModel,
        prompt: str,
        call_kwargs: dict[str, Any],
        *,
        timeout_seconds: float | None,
    ) -> tuple[Any | None, Exception | None]:
        if timeout_seconds is None:
            try:
                return model.generate(prompt, **call_kwargs), None
            except Exception as exc:
                return None, exc

        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(model.generate, prompt, **call_kwargs)
        try:
            return future.result(timeout=timeout_seconds), None
        except FutureTimeoutError:
            future.cancel()
            return None, TimeoutError(
                f"model call timed out after {timeout_seconds:g} seconds"
            )
        except Exception as exc:
            return None, exc
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    def _coerce_model_call_result(
        self,
        raw_result: Any,
        *,
        resolution: RouteResolution,
        metadata: dict[str, Any],
        request_id: str,
        call_type: str,
        model_id: str | None,
    ) -> ModelCallResult:
        if isinstance(raw_result, ModelCallResult):
            self._enrich_result(raw_result, resolution, metadata)
            return raw_result
        return ModelCallResult.ok(
            raw_result if isinstance(raw_result, str) else str(raw_result),
            provider=resolution.provider or self.model_name,
            protocol=resolution.protocol,
            provider_conf_id=resolution.provider_conf_id,
            credential_slug=resolution.credential_slug,
            model=resolution.model or model_id,
            route=resolution.route,
            call_type=call_type,
            request_id=request_id,
            selected_candidate=resolution.selected_candidate,
            metadata=metadata,
        )

    def _failure_result_from_exception(
        self,
        exc: Exception,
        *,
        resolution: RouteResolution,
        metadata: dict[str, Any],
        request_id: str,
        call_type: str,
        model_id: str | None,
    ) -> ModelCallResult:
        error_info = self._error_info_for_exception(exc)
        return ModelCallResult.fail(
            error_info.code,
            error_info.message,
            provider=resolution.provider or self.model_name,
            protocol=resolution.protocol,
            provider_conf_id=resolution.provider_conf_id,
            credential_slug=resolution.credential_slug,
            model=resolution.model or model_id,
            route=resolution.route,
            call_type=call_type,
            request_id=request_id,
            selected_candidate=resolution.selected_candidate,
            retriable=error_info.retriable,
            error_info=error_info,
            metadata=metadata,
        )

    def _should_retry_result(
        self,
        result: ModelCallResult,
        *,
        attempt: int,
        policy: RetryPolicy,
        allow_retry: bool,
    ) -> bool:
        if result.success or not allow_retry or attempt >= policy.max_attempts:
            return False
        retriable = result.error_info.retriable if result.error_info else result.retriable
        return is_retryable_error(result.code, retriable=retriable)

    def _optional_positive_float(self, value: Any) -> float | None:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    def _enrich_result(
        self,
        result: ModelCallResult,
        resolution: RouteResolution,
        metadata: dict[str, Any],
    ) -> None:
        result.provider = result.provider or resolution.provider or self.model_name
        result.protocol = result.protocol or resolution.protocol
        result.provider_conf_id = result.provider_conf_id or resolution.provider_conf_id
        result.credential_slug = result.credential_slug or resolution.credential_slug
        result.model = result.model or resolution.model
        result.route = result.route or resolution.route
        result.call_type = result.call_type or self._call_type_from_kwargs({"call_type": resolution.route})
        result.selected_candidate = result.selected_candidate or resolution.selected_candidate
        result.metadata = {**metadata, **result.metadata}

    def _config_health_check(
        self,
        provider_conf: ProviderConf,
        *,
        check_type: str = "config_check",
    ) -> ModelHealthStatus:
        missing_config: list[str] = []
        code: str | ModelErrorCode | None = None
        error: str | None = None

        if provider_conf.protocol != ModelProviderProtocol.OPENAI_COMPATIBLE.value:
            missing_config.append("protocol")
            code = ModelErrorCode.UNSUPPORTED_PROTOCOL
            error = f"unsupported model protocol: {provider_conf.protocol}"
        if not provider_conf.base_url:
            missing_config.append("base_url")
            code = code or ModelErrorCode.MISSING_MODEL_CONFIG
        if not (provider_conf.model_id or provider_conf.default_model):
            missing_config.append("model")
            code = code or ModelErrorCode.MISSING_MODEL_CONFIG

        credential = self._resolve_credential(provider_conf)
        if not credential.success:
            missing_config.extend(credential.missing_config)
            code = code or credential.code or ModelErrorCode.MISSING_API_KEY
            error = error or credential.error

        configured = not missing_config
        if not configured and error is None:
            error = "provider configuration is incomplete"

        return ModelHealthStatus(
            healthy=configured,
            provider_conf_id=provider_conf.id,
            provider=provider_conf.provider,
            protocol=provider_conf.protocol,
            model=provider_conf.model_id or provider_conf.default_model,
            configured=configured,
            missing_config=missing_config,
            check_type=check_type,
            code=None if configured else code,
            error=None if configured else error,
            verified_at=provider_conf.verified_at,
            metadata={
                "provider_status": provider_conf.status,
                "credential_slug": credential.slug,
                "credential_source": credential.source,
            },
        )

    def _mark_provider_verify_failure(
        self,
        provider_conf: ProviderConf,
        status: ModelHealthStatus,
    ) -> None:
        provider_conf.status = "error" if status.configured else "unverified"
        provider_conf.metadata["last_verify_code"] = str(status.code or "")
        provider_conf.metadata["last_verify_error"] = str(status.error or "")

    def _call_type_from_kwargs(self, kwargs: dict[str, Any]) -> str:
        try:
            return normalize_model_call_type(kwargs.get("call_type") or ModelCallType.CHAT)
        except ValueError:
            return ModelCallType.CHAT.value

    def _metadata_from_kwargs(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        metadata = dict(kwargs.get("metadata") or {})
        if "call_type" in kwargs:
            metadata.setdefault("requested_call_type", str(kwargs["call_type"]))
        return metadata

    def _finalize_generate_result(
        self,
        result: ModelCallResult,
        *,
        prompt: str,
        call_kwargs: dict[str, Any],
        original_kwargs: dict[str, Any],
    ) -> ModelCallResult:
        trace_context = self._coerce_trace_context(
            call_kwargs.get("trace_context") or original_kwargs.get("trace_context")
        )
        if result.trace_context is None and trace_context is not None:
            result.trace_context = trace_context
        if result.source_trace_id is None and result.trace_context is not None:
            result.source_trace_id = result.trace_context.source_trace_id
        if result.cost is None:
            pricing = self.models_config.pricing if self.models_config is not None else {}
            result.cost = estimate_model_cost(result, pricing)
        self._record_provider_activity(result)
        self._record_model_call(
            result,
            prompt=prompt,
            messages=call_kwargs.get("messages") or original_kwargs.get("messages"),
        )
        return result

    def _record_provider_activity(self, result: ModelCallResult) -> None:
        if self.models_config is None or not result.provider_conf_id:
            return
        provider_conf = self.models_config.get_provider_conf(result.provider_conf_id)
        if provider_conf is None:
            return
        now = datetime.now(timezone.utc).isoformat()
        if result.success:
            provider_conf.last_used_at = now
            return
        provider_conf.metadata["last_error_code"] = result.code
        provider_conf.metadata["last_error_at"] = now

    def _record_model_call(
        self,
        result: ModelCallResult,
        *,
        prompt: str,
        messages: Any,
    ) -> None:
        if self.call_logger is None:
            return
        try:
            self.call_logger.record_call(result, prompt=prompt, messages=messages)
        except Exception:
            # Developer logging is strictly best-effort and must not affect model calls.
            return

    def _record_stream_result(
        self,
        *,
        success: bool,
        content: str,
        code: str | ModelErrorCode | None,
        error: str | None,
        request_id: str,
        resolution: RouteResolution,
        model_id: str | None,
        call_type: str,
        metadata: dict[str, Any],
        prompt: str,
        call_kwargs: dict[str, Any],
        original_kwargs: dict[str, Any],
        started_at: float,
        chunks_count: int,
    ) -> None:
        stream_metadata = {
            **metadata,
            "streaming": True,
            "chunks_count": max(int(chunks_count), 0),
        }
        if success:
            result = ModelCallResult.ok(
                content,
                provider=resolution.provider or self.model_name,
                protocol=resolution.protocol,
                provider_conf_id=resolution.provider_conf_id,
                credential_slug=resolution.credential_slug,
                model=resolution.model or model_id,
                route=resolution.route,
                call_type=call_type,
                request_id=request_id,
                selected_candidate=resolution.selected_candidate,
                latency_ms=int((time.monotonic() - started_at) * 1000),
                metadata=stream_metadata,
            )
        else:
            result = ModelCallResult.fail(
                code or ModelErrorCode.MODEL_CALL_FAILED,
                error or "model stream call failed",
                provider=resolution.provider or self.model_name,
                protocol=resolution.protocol,
                provider_conf_id=resolution.provider_conf_id,
                credential_slug=resolution.credential_slug,
                model=resolution.model or model_id,
                route=resolution.route,
                call_type=call_type,
                request_id=request_id,
                selected_candidate=resolution.selected_candidate,
                latency_ms=int((time.monotonic() - started_at) * 1000),
                metadata=stream_metadata,
            )
        self._finalize_generate_result(
            result,
            prompt=prompt,
            call_kwargs=call_kwargs,
            original_kwargs=original_kwargs,
        )

    def _coerce_trace_context(self, value: Any) -> ModelTraceContext | None:
        if isinstance(value, ModelTraceContext):
            return value
        if isinstance(value, dict):
            try:
                return ModelTraceContext(**value)
            except (TypeError, ValueError):
                return None
        return None

    def _structured_parse_mode(self, call_type: str, explicit: str | None) -> str:
        if explicit:
            return str(explicit).strip().lower()
        config = self.models_config.structured_output if self.models_config is not None else {}
        parse_modes = config.get("parse_modes") if isinstance(config, dict) else {}
        if isinstance(parse_modes, dict):
            return str(parse_modes.get(call_type) or "lenient").strip().lower()
        return "lenient"

    def _structured_repair_attempts(self, explicit: int | None) -> int:
        if explicit is not None:
            return min(max(int(explicit), 0), 5)
        config = self.models_config.structured_output if self.models_config is not None else {}
        try:
            return min(max(int(config.get("default_repair_attempts", 1)), 0), 5)
        except (AttributeError, TypeError, ValueError):
            return 1

    def _structured_repair_enabled(self, explicit: bool | None) -> bool:
        if explicit is not None:
            return bool(explicit)
        config = self.models_config.structured_output if self.models_config is not None else {}
        try:
            return bool(config.get("repair_enabled", True))
        except AttributeError:
            return True

    def _structured_schema(self, schema_name: str | None) -> dict[str, Any] | None:
        if not schema_name or self.models_config is None:
            return None
        configured = self.models_config.structured_output
        schemas = configured.get("schemas") if isinstance(configured, dict) else None
        if isinstance(schemas, dict) and isinstance(schemas.get(schema_name), dict):
            return schemas[schema_name]
        return None

    def _stream_chunk_from_provider_chunk(
        self,
        raw_chunk: str | ModelStreamChunk,
        *,
        request_id: str,
        index: int,
        is_final: bool,
        model_id: str | None,
        metadata: dict[str, Any],
    ) -> ModelStreamChunk:
        if isinstance(raw_chunk, ModelStreamChunk):
            return ModelStreamChunk(
                success=raw_chunk.success,
                content_delta=raw_chunk.content_delta,
                index=index,
                is_final=is_final,
                code=raw_chunk.code,
                error=raw_chunk.error,
                request_id=request_id,
                provider=raw_chunk.provider or self.model_name,
                model=raw_chunk.model or model_id,
                metadata={**metadata, **raw_chunk.metadata},
            )
        return ModelStreamChunk(
            success=True,
            content_delta=raw_chunk if isinstance(raw_chunk, str) else str(raw_chunk),
            index=index,
            is_final=is_final,
            request_id=request_id,
            provider=self.model_name,
            model=model_id,
            metadata=metadata,
        )

    def _error_code_for_exception(self, exc: Exception | None) -> str:
        return self._error_info_for_exception(exc).code

    def _error_info_for_exception(self, exc: Exception | None) -> ModelErrorInfo:
        message = str(exc or "model call failed")
        status_code = self._exception_status_code(exc)
        provider_error_code = self._exception_provider_error_value(exc, "code")
        provider_error_message = self._exception_provider_error_value(exc, "message")
        provider_error_hint = self._exception_provider_error_value(exc, "hint")
        local_code = getattr(exc, "code", None)
        if local_code not in MODEL_ERROR_CODES:
            text = message.lower()
            if "unsupported model provider" in text:
                local_code = ModelErrorCode.UNSUPPORTED_PROVIDER.value
            elif "unsupported model protocol" in text:
                local_code = ModelErrorCode.UNSUPPORTED_PROTOCOL.value
            elif "api_key" in text or "api key" in text or "key" in text:
                local_code = ModelErrorCode.MISSING_API_KEY.value
            elif "provider config" in text or "model configuration" in text:
                local_code = ModelErrorCode.MISSING_MODEL_CONFIG.value
            else:
                local_code = ModelErrorCode.MODEL_CALL_FAILED.value
        return ModelErrorInfo.from_provider_error(
            code=local_code,
            message=message,
            http_status=status_code,
            provider_error_code=provider_error_code,
            provider_error_message=provider_error_message,
            provider_error_hint=provider_error_hint,
            raw_error_preview=message[:500],
        )

    def _exception_status_code(self, exc: Exception | None) -> int | None:
        value = getattr(exc, "status_code", None)
        if value is None:
            response = getattr(exc, "response", None)
            value = getattr(response, "status_code", None)
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _exception_provider_error_value(self, exc: Exception | None, key: str) -> Any:
        if exc is None:
            return None
        response = getattr(exc, "response", None)
        payload: Any = None
        if response is not None:
            json_method = getattr(response, "json", None)
            if callable(json_method):
                try:
                    payload = json_method()
                except Exception:
                    payload = None
        if isinstance(payload, dict):
            error = payload.get("error", payload)
            if isinstance(error, dict):
                return error.get(key)
        return getattr(exc, key, None)
