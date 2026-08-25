from __future__ import annotations

from typing import Any

from src.models.errors import ModelErrorCode
from src.models.protocol import EmbeddingBatchResult, EmbeddingResult, ModelUsage


DEFAULT_EMBEDDING_MODEL = "mock-embedding-v1"


def normalize_embedding_text(value: Any) -> str:
    return str(value or "")


def normalize_embedding_texts(values: list[Any] | tuple[Any, ...] | None) -> list[str]:
    return [normalize_embedding_text(item) for item in values or []]


def embedding_failure(
    code: str | ModelErrorCode,
    error: str,
    *,
    provider_conf_id: str | None = None,
    model: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> EmbeddingResult:
    return EmbeddingResult(
        success=False,
        code=code,
        error=error,
        provider_conf_id=provider_conf_id,
        model=model,
        metadata=dict(metadata or {}),
    )


def embedding_batch_failure(
    code: str | ModelErrorCode,
    error: str,
    *,
    item_results: list[EmbeddingResult] | None = None,
    metadata: dict[str, Any] | None = None,
) -> EmbeddingBatchResult:
    return EmbeddingBatchResult(
        success=False,
        code=code,
        error=error,
        item_results=list(item_results or []),
        metadata=dict(metadata or {}),
    )


def embedding_batch_from_items(
    item_results: list[EmbeddingResult],
    *,
    usage: ModelUsage | None = None,
    metadata: dict[str, Any] | None = None,
) -> EmbeddingBatchResult:
    failures = [item for item in item_results if not item.success]
    if failures:
        first = failures[0]
        return embedding_batch_failure(
            first.code or ModelErrorCode.EMBEDDING_FAILED,
            first.error or "embedding batch item failed",
            item_results=item_results,
            metadata={**dict(metadata or {}), "failed_items": len(failures)},
        )
    return EmbeddingBatchResult(
        success=True,
        embeddings=[item.embedding or [] for item in item_results],
        item_results=item_results,
        metadata={
            **dict(metadata or {}),
            "count": len(item_results),
            "usage": usage.to_dict() if usage else None,
        },
    )


__all__ = [
    "DEFAULT_EMBEDDING_MODEL",
    "embedding_batch_failure",
    "embedding_batch_from_items",
    "embedding_failure",
    "normalize_embedding_text",
    "normalize_embedding_texts",
]
