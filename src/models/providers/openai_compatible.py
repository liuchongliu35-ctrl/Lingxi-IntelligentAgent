from __future__ import annotations

import json
from typing import Any, Callable, Generator

from src.models.base_model import BaseModel
from src.models.config import ModelsConfig, ProviderConf, ProviderCredential, get_models_config
from src.models.credentials import CredentialResolution
from src.models.errors import ModelErrorCode
from src.models.protocol import (
    EmbeddingBatchResult,
    EmbeddingResult,
    ModelCallOptions,
    ModelCallResult,
    ModelErrorInfo,
    ModelMessage,
    ModelStreamChunk,
    ModelUsage,
    new_model_request_id,
)
from src.models.providers.base import BaseProvider


ClientFactory = Callable[..., Any]


def configured_builtin_provider_conf(
    provider: str,
    *,
    models_config: ModelsConfig | None = None,
) -> ProviderConf:
    """Resolve a built-in provider through Models configuration."""
    config = models_config or get_models_config()
    provider_name = str(provider or "").strip().lower()
    configured = config.get_provider_conf(f"conf_{provider_name}_default")
    if configured is not None:
        return configured

    spec = config.get_provider_spec(provider_name)
    if spec is None:
        raise ValueError(f"Unsupported model provider: {provider_name}")
    credential_env = {
        "openai": "OPENAI_API_KEY",
        "qianwen": "QIANWEN_API_KEY",
        "doubao": "DOUBAO_API_KEY",
    }.get(provider_name)
    return ProviderConf(
        id=f"conf_{provider_name}_legacy",
        name=spec.display_name,
        provider=spec.provider,
        protocol=spec.protocol,
        enabled=True,
        base_url=spec.default_base_url,
        default_model=spec.default_model,
        credentials=[ProviderCredential(slug="default", api_key_env=credential_env)],
        timeout_seconds=spec.default_timeout_seconds,
        max_retries=spec.default_max_retries,
    )


class OpenAICompatibleProvider(BaseProvider):
    """OpenAI Chat Completions-compatible provider adapter."""

    def __init__(
        self,
        provider_conf: ProviderConf,
        credential: CredentialResolution | None,
        *,
        client_factory: ClientFactory | None = None,
        client: Any | None = None,
    ):
        self.provider_conf = provider_conf
        self.credential = credential
        self.client_factory = client_factory
        self.client = client

    def generate(self, options: ModelCallOptions) -> ModelCallResult:
        request_id = new_model_request_id()
        validation_error = self._validate_options(options, request_id=request_id)
        if validation_error is not None:
            return validation_error

        payload = self._build_payload(options, stream=False)
        try:
            response = self._client().chat.completions.create(**payload)
            return self._response_to_result(response, options=options, request_id=request_id)
        except Exception as exc:
            return self._failure_from_exception(exc, options=options, request_id=request_id)

    def stream_generate(self, options: ModelCallOptions) -> Generator[ModelStreamChunk, None, None]:
        request_id = new_model_request_id()
        validation_error = self._validate_options(options, request_id=request_id)
        if validation_error is not None:
            yield ModelStreamChunk(
                success=False,
                code=validation_error.code,
                error=validation_error.error,
                request_id=request_id,
                provider=self.provider_conf.provider,
                model=options.model or self._configured_model(),
                metadata={"provider_conf_id": self.provider_conf.id},
                is_final=True,
            )
            return

        payload = self._build_payload(options, stream=True)
        try:
            response = self._client().chat.completions.create(**payload)
            emitted = False
            index = 0
            for raw_chunk in response:
                content = self._stream_content(raw_chunk)
                if not content:
                    continue
                emitted = True
                yield ModelStreamChunk(
                    success=True,
                    content_delta=content,
                    index=index,
                    request_id=request_id,
                    provider=self.provider_conf.provider,
                    model=options.model or self._configured_model(),
                    metadata={"provider_conf_id": self.provider_conf.id},
                )
                index += 1
            if not emitted:
                yield ModelStreamChunk(
                    success=True,
                    content_delta="",
                    index=0,
                    request_id=request_id,
                    provider=self.provider_conf.provider,
                    model=options.model or self._configured_model(),
                    metadata={"provider_conf_id": self.provider_conf.id},
                    is_final=True,
                )
        except Exception as exc:
            failure = self._failure_from_exception(exc, options=options, request_id=request_id)
            yield ModelStreamChunk(
                success=False,
                code=failure.code,
                error=failure.error,
                request_id=request_id,
                provider=self.provider_conf.provider,
                model=options.model or self._configured_model(),
                metadata={"provider_conf_id": self.provider_conf.id},
                is_final=True,
            )

    def embed_text(self, text: str, options: ModelCallOptions) -> EmbeddingResult:
        batch = self.embed_texts([text], options)
        if not batch.success:
            return EmbeddingResult(
                success=False,
                code=batch.code,
                error=batch.error,
                provider_conf_id=options.provider_conf_id or self.provider_conf.id,
                model=options.model or self._configured_model(),
                metadata=batch.metadata,
            )
        if not batch.item_results:
            return EmbeddingResult(
                success=False,
                code=ModelErrorCode.EMBEDDING_FAILED,
                error="provider embedding response did not contain data[0].embedding",
                provider_conf_id=options.provider_conf_id or self.provider_conf.id,
                model=options.model or self._configured_model(),
                usage=None,
            )
        return batch.item_results[0]

    def embed_texts(self, texts: list[str], options: ModelCallOptions) -> EmbeddingBatchResult:
        request_id = new_model_request_id()
        validation_error = self._validate_embedding_options(options, request_id=request_id)
        if validation_error is not None:
            return EmbeddingBatchResult(
                success=False,
                code=validation_error.code,
                error=validation_error.error,
                metadata={"request_id": request_id},
            )
        if not texts:
            return EmbeddingBatchResult(
                success=True,
                embeddings=[],
                item_results=[],
                metadata={"request_id": request_id, "count": 0},
            )

        payload = {
            "model": options.model or self._configured_model(),
            "input": texts,
        }
        try:
            response = self._client().embeddings.create(**payload)
        except Exception as exc:
            failure = self._failure_from_exception(exc, options=options, request_id=request_id)
            return EmbeddingBatchResult(
                success=False,
                code=failure.code,
                error=failure.error,
                metadata={"request_id": request_id, "provider_error": failure.error_info.to_dict() if failure.error_info else None},
            )

        usage = self._usage(response)
        data = self._value(response, "data", []) or []
        item_results: list[EmbeddingResult] = []
        for index, item in enumerate(data):
            embedding = self._value(item, "embedding")
            if not isinstance(embedding, list):
                return EmbeddingBatchResult(
                    success=False,
                    code=ModelErrorCode.EMBEDDING_FAILED,
                    error=f"provider embedding response missing embedding at index {index}",
                    item_results=item_results,
                    metadata={"request_id": request_id, "provider_request_id": self._value(response, "id")},
                )
            item_results.append(
                EmbeddingResult(
                    success=True,
                    embedding=embedding,
                    provider_conf_id=options.provider_conf_id or self.provider_conf.id,
                    model=options.model or self._configured_model(),
                    usage=usage,
                    metadata={
                        "request_id": request_id,
                        "provider_request_id": self._value(response, "id"),
                        "index": index,
                    },
                )
            )
        if len(item_results) != len(texts):
            return EmbeddingBatchResult(
                success=False,
                code=ModelErrorCode.EMBEDDING_FAILED,
                error="provider embedding response item count does not match input count",
                item_results=item_results,
                metadata={
                    "request_id": request_id,
                    "provider_request_id": self._value(response, "id"),
                    "input_count": len(texts),
                    "output_count": len(item_results),
                },
            )
        return EmbeddingBatchResult(
            success=True,
            embeddings=[item.embedding or [] for item in item_results],
            item_results=item_results,
            metadata={
                "request_id": request_id,
                "provider_request_id": self._value(response, "id"),
                "count": len(item_results),
                "usage": usage.to_dict() if usage else None,
            },
        )

    def _validate_options(
        self,
        options: ModelCallOptions,
        *,
        request_id: str,
    ) -> ModelCallResult | None:
        if self.provider_conf.protocol != "openai-compatible":
            return ModelCallResult.fail(
                ModelErrorCode.UNSUPPORTED_PROTOCOL,
                f"provider protocol is not openai-compatible: {self.provider_conf.protocol}",
                provider=self.provider_conf.provider,
                protocol=self.provider_conf.protocol,
                provider_conf_id=self.provider_conf.id,
                credential_slug=self.credential.slug if self.credential else None,
                model=options.model or self._configured_model(),
                call_type=options.call_type,
                request_id=request_id,
            )
        if not self.provider_conf.base_url:
            return ModelCallResult.fail(
                ModelErrorCode.MISSING_MODEL_CONFIG,
                "provider base_url is not configured",
                provider=self.provider_conf.provider,
                protocol=self.provider_conf.protocol,
                provider_conf_id=self.provider_conf.id,
                credential_slug=self.credential.slug if self.credential else None,
                model=options.model or self._configured_model(),
                call_type=options.call_type,
                request_id=request_id,
            )
        if not self._configured_model() and not options.model:
            return ModelCallResult.fail(
                ModelErrorCode.MISSING_MODEL_CONFIG,
                "provider model is not configured",
                provider=self.provider_conf.provider,
                protocol=self.provider_conf.protocol,
                provider_conf_id=self.provider_conf.id,
                credential_slug=self.credential.slug if self.credential else None,
                call_type=options.call_type,
                request_id=request_id,
            )
        if self.credential is None or not self.credential.success or not self.credential.secret:
            return ModelCallResult.fail(
                self.credential.code if self.credential else ModelErrorCode.MISSING_API_KEY,
                self.credential.error if self.credential else "provider credential is not configured",
                provider=self.provider_conf.provider,
                protocol=self.provider_conf.protocol,
                provider_conf_id=self.provider_conf.id,
                credential_slug=self.credential.slug if self.credential else None,
                model=options.model or self._configured_model(),
                call_type=options.call_type,
                request_id=request_id,
                metadata={"missing_config": self.credential.missing_config if self.credential else []},
            )
        if not options.to_messages():
            return ModelCallResult.fail(
                ModelErrorCode.INVALID_PROMPT,
                "model call requires at least one message or prompt",
                provider=self.provider_conf.provider,
                protocol=self.provider_conf.protocol,
                provider_conf_id=self.provider_conf.id,
                credential_slug=self.credential.slug,
                model=options.model or self._configured_model(),
                call_type=options.call_type,
                request_id=request_id,
            )
        return None

    def _validate_embedding_options(
        self,
        options: ModelCallOptions,
        *,
        request_id: str,
    ) -> ModelCallResult | None:
        validation_error = self._validate_options(options, request_id=request_id)
        if validation_error is not None:
            if validation_error.code == ModelErrorCode.INVALID_PROMPT.value:
                return None
            return validation_error
        return None

    def _build_payload(self, options: ModelCallOptions, *, stream: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": options.model or self._configured_model(),
            "messages": [message.to_dict() for message in options.to_messages()],
        }
        temperature = options.temperature
        if temperature is None:
            temperature = self.provider_conf.temperature
        if temperature is not None:
            payload["temperature"] = temperature

        top_p = options.top_p if options.top_p is not None else self.provider_conf.top_p
        if top_p is not None:
            payload["top_p"] = top_p

        max_tokens = options.max_tokens if options.max_tokens is not None else self.provider_conf.max_tokens
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        if options.json_mode:
            payload["response_format"] = {"type": "json_object"}
        elif options.response_format:
            payload["response_format"] = {"type": options.response_format}
        if stream:
            payload["stream"] = True
        self._apply_web_search_capability(payload, options)
        return payload

    def _apply_web_search_capability(
        self,
        payload: dict[str, Any],
        options: ModelCallOptions,
    ) -> None:
        """Apply only explicitly configured provider web-search parameters."""
        metadata = dict(options.metadata or {})
        if options.call_type != "web_search" or not metadata.get("enable_web_search"):
            return
        configured = self.provider_conf.metadata.get("web_search")
        if not isinstance(configured, dict) or not configured.get("enabled", False):
            return
        extra_body = configured.get("extra_body")
        if isinstance(extra_body, dict):
            payload["extra_body"] = dict(extra_body)
        provider_tools = configured.get("tools")
        if isinstance(provider_tools, list):
            payload["tools"] = list(provider_tools)
        request_options = metadata.get("web_search_options")
        if isinstance(request_options, dict):
            payload.setdefault("extra_body", {}).update(dict(request_options))

    def _client(self) -> Any:
        if self.client is not None:
            return self.client
        if self.credential is None or not self.credential.secret:
            raise RuntimeError("provider credential is not configured")
        kwargs = {
            "api_key": self.credential.secret,
            "base_url": self.provider_conf.base_url,
            "default_headers": dict(self.provider_conf.headers or {}),
        }
        timeout = self.provider_conf.timeout_seconds
        if timeout is not None:
            kwargs["timeout"] = timeout
        if self.client_factory is not None:
            self.client = self.client_factory(**kwargs)
            return self.client
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("openai package is required for openai-compatible providers") from exc
        self.client = OpenAI(**kwargs)
        return self.client

    def _response_to_result(
        self,
        response: Any,
        *,
        options: ModelCallOptions,
        request_id: str,
    ) -> ModelCallResult:
        content = self._response_content(response)
        if content is None:
            return ModelCallResult.fail(
                ModelErrorCode.MODEL_CALL_FAILED,
                "provider response did not contain choices[0].message.content",
                provider=self.provider_conf.provider,
                protocol=self.provider_conf.protocol,
                provider_conf_id=self.provider_conf.id,
                credential_slug=self.credential.slug if self.credential else None,
                model=options.model or self._configured_model(),
                route=options.route,
                call_type=options.call_type,
                request_id=request_id,
                provider_request_id=self._value(response, "id"),
            )
        usage = self._usage(response)
        return ModelCallResult.ok(
            content,
            provider=self.provider_conf.provider,
            protocol=self.provider_conf.protocol,
            provider_conf_id=self.provider_conf.id,
            credential_slug=self.credential.slug if self.credential else None,
            model=options.model or self._configured_model(),
            route=options.route,
            call_type=options.call_type,
            request_id=request_id,
            provider_request_id=self._value(response, "id"),
            usage=usage,
            raw_response=response,
            metadata={
                "provider_conf_id": self.provider_conf.id,
                "finish_reason": self._finish_reason(response),
            },
        )

    def _failure_from_exception(
        self,
        exc: Exception,
        *,
        options: ModelCallOptions,
        request_id: str,
    ) -> ModelCallResult:
        status_code = self._status_code(exc)
        error_message = self._error_message(exc)
        error_info = ModelErrorInfo.from_provider_error(
            code=self._error_code(status_code),
            message=error_message,
            http_status=status_code,
            provider_error_code=self._provider_error_value(exc, "code"),
            provider_error_message=self._provider_error_value(exc, "message"),
            provider_error_hint=self._provider_error_value(exc, "hint"),
            raw_error_preview=error_message[:500],
        )
        retriable = error_info.retriable
        return ModelCallResult.fail(
            error_info.code,
            error_message,
            provider=self.provider_conf.provider,
            protocol=self.provider_conf.protocol,
            provider_conf_id=self.provider_conf.id,
            credential_slug=self.credential.slug if self.credential else None,
            model=options.model or self._configured_model(),
            route=options.route,
            call_type=options.call_type,
            request_id=request_id,
            retriable=retriable,
            error_info=error_info,
        )

    def _configured_model(self) -> str | None:
        return self.provider_conf.model_id or self.provider_conf.default_model

    def _value(self, value: Any, key: str, default: Any = None) -> Any:
        if isinstance(value, dict):
            return value.get(key, default)
        return getattr(value, key, default)

    def _response_content(self, response: Any) -> str | None:
        choices = self._value(response, "choices", []) or []
        if not choices:
            return None
        message = self._value(choices[0], "message", {}) or {}
        content = self._value(message, "content")
        if content is None:
            return None
        if isinstance(content, list):
            parts = []
            for item in content:
                part = self._value(item, "text")
                if part:
                    parts.append(str(part))
            return "".join(parts)
        return str(content)

    def _stream_content(self, chunk: Any) -> str:
        choices = self._value(chunk, "choices", []) or []
        if not choices:
            return ""
        delta = self._value(choices[0], "delta", {}) or {}
        content = self._value(delta, "content", "")
        return str(content or "")

    def _usage(self, response: Any) -> ModelUsage | None:
        usage = self._value(response, "usage")
        if usage is None:
            return None
        return ModelUsage(
            prompt_tokens=self._value(usage, "prompt_tokens"),
            completion_tokens=self._value(usage, "completion_tokens"),
            total_tokens=self._value(usage, "total_tokens"),
            source="provider",
        )

    def _finish_reason(self, response: Any) -> str | None:
        choices = self._value(response, "choices", []) or []
        return self._value(choices[0], "finish_reason") if choices else None

    def _status_code(self, exc: Exception) -> int | None:
        value = getattr(exc, "status_code", None)
        if value is None:
            response = getattr(exc, "response", None)
            value = getattr(response, "status_code", None)
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _error_code(self, status_code: int | None) -> str:
        if status_code in {401}:
            return ModelErrorCode.AUTHENTICATION_FAILED.value
        if status_code in {403}:
            return ModelErrorCode.PERMISSION_DENIED.value
        if status_code == 404:
            return ModelErrorCode.MODEL_NOT_FOUND.value
        if status_code == 429:
            return ModelErrorCode.RATE_LIMITED.value
        if status_code in {408, 504}:
            return ModelErrorCode.TIMEOUT.value
        if status_code == 503:
            return ModelErrorCode.TEMPORARY_UNAVAILABLE.value
        if status_code is not None and status_code >= 500:
            return ModelErrorCode.PROVIDER_SERVER_ERROR.value
        if status_code in {400, 422}:
            return ModelErrorCode.INVALID_REQUEST.value
        return ModelErrorCode.NETWORK_ERROR.value

    def _error_category(self, code: str) -> str:
        if code in {"authentication_failed", "permission_denied"}:
            return "auth"
        if code in {"rate_limited", "timeout", "network_error", "provider_server_error"}:
            return "transient"
        if code in {"invalid_request", "model_not_found"}:
            return "request"
        return "provider"

    def _error_message(self, exc: Exception) -> str:
        provider_message = self._provider_error_value(exc, "message")
        return str(provider_message or exc or "provider call failed")

    def _provider_error_value(self, exc: Exception, key: str) -> Any:
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


class OpenAICompatibleModel(BaseModel):
    """Legacy prompt-shaped facade over the Models V1 provider adapter."""

    def __init__(self, provider: OpenAICompatibleProvider):
        self.provider = provider

    def generate(self, prompt: str, **kwargs: Any) -> ModelCallResult:
        return self.provider.generate(self._options(prompt, kwargs))

    def stream_generate(self, prompt: str, **kwargs: Any) -> Generator[ModelStreamChunk, None, None]:
        yield from self.provider.stream_generate(self._options(prompt, kwargs))

    def embed_text(self, text: str, **kwargs: Any) -> EmbeddingResult:
        return self.provider.embed_text(text, self._options("", kwargs))

    def embed_texts(self, texts: list[str], **kwargs: Any) -> EmbeddingBatchResult:
        return self.provider.embed_texts(texts, self._options("", kwargs))

    def health_check(self) -> bool:
        credential = self.provider.credential
        return bool(
            self.provider.provider_conf.base_url
            and (self.provider.provider_conf.model_id or self.provider.provider_conf.default_model)
            and credential
            and credential.success
        )

    def _options(self, prompt: str, kwargs: dict[str, Any]) -> ModelCallOptions:
        return ModelCallOptions(
            call_type=kwargs.get("call_type", "chat"),
            route=kwargs.get("route"),
            provider_conf_id=self.provider.provider_conf.id,
            credential_slug=self.provider.credential.slug if self.provider.credential else None,
            model=kwargs.get("model"),
            messages=kwargs.get("messages"),
            prompt=prompt,
            temperature=kwargs.get("temperature"),
            top_p=kwargs.get("top_p"),
            top_k=kwargs.get("top_k"),
            max_tokens=kwargs.get("max_tokens"),
            timeout_seconds=kwargs.get("timeout_seconds"),
            json_mode=kwargs.get("json_mode"),
            response_format=kwargs.get("response_format"),
            trace_context=kwargs.get("trace_context"),
            metadata=kwargs.get("metadata"),
        )
