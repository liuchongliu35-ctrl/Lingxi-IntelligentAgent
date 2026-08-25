from __future__ import annotations

import hashlib
import json
from collections import deque
from typing import Any, Deque, Generator, Iterable

from src.models.base_model import BaseModel
from src.models.errors import ModelErrorCode
from src.models.protocol import (
    ContextCompressionResult,
    EmbeddingBatchResult,
    EmbeddingResult,
    ModelCallResult,
    ModelCallType,
    ModelStreamChunk,
    normalize_model_call_type,
)


class MockModel(BaseModel):
    """Structured mock provider for explicit local development and tests."""

    def __init__(
        self,
        *,
        responses: Iterable[Any] | None = None,
        fixtures: dict[str, Any] | None = None,
        provider: str = "mock",
        model: str = "mock-v1",
        embedding_dimensions: int = 8,
    ):
        self.provider = provider
        self.model = model
        self.fixtures = dict(fixtures or {})
        self.responses: Deque[Any] = deque(responses or [])
        self.embedding_dimensions = max(int(embedding_dimensions), 1)
        self.generate_calls: list[dict[str, Any]] = []

    def generate(self, prompt: str, **kwargs: Any) -> ModelCallResult:
        call_type = self._call_type(kwargs)
        self.generate_calls.append({"prompt": prompt, "kwargs": dict(kwargs), "call_type": call_type})
        raw_response = self._next_response(call_type, prompt)
        return self._to_model_call_result(raw_response, call_type=call_type, kwargs=kwargs)

    def stream_generate(self, prompt: str, **kwargs: Any) -> Generator[ModelStreamChunk, None, None]:
        result = self.generate(prompt, **kwargs)
        if not result.success:
            yield ModelStreamChunk(
                success=False,
                code=result.code,
                error=result.error,
                request_id=result.request_id,
                provider=result.provider or self.provider,
                model=result.model or self.model,
                is_final=True,
            )
            return

        chunks = self._configured_stream_chunks(result.content, kwargs)
        for index, chunk in enumerate(chunks):
            yield ModelStreamChunk(
                success=True,
                content_delta=chunk,
                index=index,
                is_final=index == len(chunks) - 1,
                request_id=result.request_id,
                provider=result.provider or self.provider,
                model=result.model or self.model,
            )

    def embed_text(self, text: str, **kwargs: Any) -> EmbeddingResult:
        return EmbeddingResult(
            success=True,
            embedding=self._embedding_for_text(text),
            provider_conf_id=kwargs.get("provider_conf_id") or self.provider,
            model=kwargs.get("model") or self.model,
            metadata={"mock": True},
        )

    def embed_texts(self, texts: list[str], **kwargs: Any) -> EmbeddingBatchResult:
        item_results = [self.embed_text(text, **kwargs) for text in texts]
        return EmbeddingBatchResult(
            success=True,
            embeddings=[item.embedding or [] for item in item_results],
            item_results=item_results,
            metadata={"mock": True, "count": len(texts)},
        )

    def compress_context(
        self,
        text: str,
        *,
        target_chars: int | None = None,
        trigger_reason: str | None = None,
        **kwargs: Any,
    ) -> ContextCompressionResult:
        original = str(text or "")
        limit = max(int(target_chars or kwargs.get("target_chars") or 200), 1)
        compressed = original[:limit]
        warnings = ["mock_truncated_to_target_chars"] if len(original) > limit else []
        return ContextCompressionResult(
            success=True,
            short_summary="MockModel context summary.",
            compressed_text=compressed,
            source_refs=list(kwargs.get("source_refs") or []),
            original_length=len(original),
            compressed_length=len(compressed),
            compression_ratio=(len(compressed) / len(original)) if original else 0.0,
            trigger_reason=trigger_reason,
            loss_risk="low" if len(original) <= limit else "medium",
            key_points=["MockModel preserved deterministic context placeholder."],
            warnings=warnings,
            metadata={"mock": True},
        )

    def _call_type(self, kwargs: dict[str, Any]) -> str:
        try:
            return normalize_model_call_type(kwargs.get("call_type") or ModelCallType.CHAT)
        except ValueError:
            return ModelCallType.CHAT.value

    def _next_response(self, call_type: str, prompt: str) -> Any:
        if self.responses:
            return self.responses.popleft()
        if call_type in self.fixtures:
            return self.fixtures[call_type]
        return self._default_response(call_type, prompt)

    def _to_model_call_result(
        self,
        raw_response: Any,
        *,
        call_type: str,
        kwargs: dict[str, Any],
    ) -> ModelCallResult:
        metadata = {"mock": True, **dict(kwargs.get("metadata") or {})}
        common = {
            "provider": self.provider,
            "model": kwargs.get("model") or self.model,
            "call_type": call_type,
            "metadata": metadata,
        }
        if isinstance(raw_response, ModelCallResult):
            return raw_response
        if isinstance(raw_response, dict) and raw_response.get("success") is False:
            return ModelCallResult.fail(
                raw_response.get("code") or ModelErrorCode.MODEL_CALL_FAILED,
                str(raw_response.get("error") or "mock model failure"),
                **common,
            )
        if isinstance(raw_response, dict) and "content" in raw_response:
            return ModelCallResult.ok(str(raw_response.get("content") or ""), **common)
        if isinstance(raw_response, str):
            return ModelCallResult.ok(raw_response, **common)
        return ModelCallResult.ok(json.dumps(raw_response, ensure_ascii=False), **common)

    def _default_response(self, call_type: str, prompt: str) -> Any:
        if call_type == ModelCallType.ANALYZER_INTENT_FALLBACK.value:
            return {
                "intents": [
                    {
                        "name": "chat",
                        "confidence": 0.72,
                        "reason": "MockModel default analyzer intent.",
                    }
                ]
            }
        if call_type == ModelCallType.PLANNER_STRUCTURED_PLAN.value:
            return {
                "mode": "meso",
                "task_units": [{"id": "task_1", "title": "Mock task", "step_ids": ["step_1"]}],
                "steps": [
                    {
                        "id": "step_1",
                        "task_id": "task_1",
                        "step_type": "respond",
                        "description": "Return a deterministic MockModel response.",
                        "expected_output": "Mock response",
                    }
                ],
            }
        if call_type == ModelCallType.REACT_ACTION_DECISION.value:
            return {
                "action_type": "finish",
                "thought_summary": "MockModel selected a deterministic finish action.",
                "user_visible_message": "MockModel finished the mock action loop.",
                "action_args": {},
                "final_answer": "MockModel final answer.",
                "confidence": 0.8,
            }
        if call_type == ModelCallType.SUMMARY.value:
            return "MockModel summary."
        if call_type == ModelCallType.WEB_SEARCH.value:
            return {
                "query": "MockModel web search query",
                "summary": "MockModel web search summary.",
                "results": [],
                "evidence_level": "no_url_summary",
                "source_quality": "summary_only",
            }
        if call_type == ModelCallType.CONTEXT_COMPRESSION.value:
            return {
                "short_summary": "MockModel context summary.",
                "compressed_text": str(prompt or "")[:200],
                "loss_risk": "low",
            }
        return "MockModel structured placeholder response."

    def _configured_stream_chunks(self, content: str, kwargs: dict[str, Any]) -> list[str]:
        configured = kwargs.get("stream_chunks")
        if isinstance(configured, list) and all(isinstance(chunk, str) for chunk in configured):
            return configured or [""]
        return [content]

    def _embedding_for_text(self, text: str) -> list[float]:
        digest = hashlib.sha256(str(text or "").encode("utf-8")).digest()
        values: list[float] = []
        for index in range(self.embedding_dimensions):
            values.append(round(digest[index] / 255.0, 6))
        return values
