from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from src.models.errors import ModelErrorCode
from src.models.protocol import (
    CompressedChunkRef,
    ContextCompressionResult,
    ModelCallType,
    StructuredModelResult,
)


DEFAULT_TARGET_CHARS = 2000
DEFAULT_MIN_CHUNK_CHARS = 800
DEFAULT_MAX_CHUNK_CHARS = 6000
LOSS_RISK_VALUES = {"low", "medium", "high"}
COMPRESSION_SCHEMA = {
    "type": "object",
    "required": ["compressed_text"],
    "properties": {
        "short_summary": {"type": "string"},
        "compressed_text": {"type": "string"},
        "loss_risk": {"type": "string"},
        "key_points": {"type": "array", "items": {"type": "string"}},
        "preserved_entities": {"type": "array", "items": {"type": "string"}},
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
}


GenerateJsonCallable = Callable[..., StructuredModelResult]


@dataclass
class CompressionSourceChunk:
    source_ref: str
    text: str
    chunk_id: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def original_length(self) -> int:
        return len(self.text)


def compress_context_with_model(
    generate_json: GenerateJsonCallable,
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
    round_index: int = 0,
    allow_rule_fallback: bool = False,
    max_chunk_chars: int | None = None,
) -> ContextCompressionResult:
    source_chunks = normalize_compression_chunks(
        text=text,
        chunks=chunks,
        source_type=source_type,
        preserve_keys=preserve_keys,
        max_chunk_chars=max_chunk_chars or _chunk_size(target_chars, target_tokens),
    )
    original_text = "\n\n".join(chunk.text for chunk in source_chunks)
    if not original_text.strip():
        return ContextCompressionResult(
            success=False,
            code=ModelErrorCode.INVALID_PROMPT,
            error="context compression requires non-empty text or chunks",
            trigger_reason=trigger_reason,
            round_index=round_index,
            metadata={"source_type": source_type, **dict(metadata or {})},
        )

    target_chars_value = _target_chars(target_chars, target_tokens)
    common_metadata = {
        "source_type": source_type,
        "chunk_count": len(source_chunks),
        "target_chars": target_chars_value,
        "target_tokens": target_tokens,
        "preserve_keys": list(preserve_keys or []),
        **dict(metadata or {}),
    }

    partial_results: list[ContextCompressionResult] = []
    for index, chunk in enumerate(source_chunks):
        result = _compress_chunk(
            generate_json,
            chunk=chunk,
            source_type=source_type,
            target_chars=max(target_chars_value // max(len(source_chunks), 1), 1),
            preserve_entities=preserve_entities or [],
            trigger_reason=trigger_reason,
            round_index=round_index,
            metadata={**common_metadata, "chunk_index": index},
        )
        if not result.success:
            if allow_rule_fallback:
                return _rule_fallback_result(
                    source_chunks,
                    target_chars=target_chars_value,
                    preserve_entities=preserve_entities or [],
                    trigger_reason=trigger_reason,
                    round_index=round_index,
                    metadata={
                        **common_metadata,
                        "compression_method": "rule_fallback",
                        "fallback_reason": result.code,
                    },
                )
            return _failure_from_structured_result(
                result,
                trigger_reason=trigger_reason,
                round_index=round_index,
                metadata=common_metadata,
            )
        partial_results.append(result)

    if len(partial_results) == 1:
        return _finalize_compression_result(
            partial_results[0],
            source_chunks,
            original_text=original_text,
            trigger_reason=trigger_reason,
            round_index=round_index,
            metadata={**common_metadata, "compression_method": "single_model_call"},
        )

    synthesis = _synthesize_chunks(
        generate_json,
        partial_results=partial_results,
        source_chunks=source_chunks,
        source_type=source_type,
        target_chars=target_chars_value,
        preserve_entities=preserve_entities or [],
        trigger_reason=trigger_reason,
        round_index=round_index,
        metadata=common_metadata,
    )
    if synthesis.success:
        return _finalize_compression_result(
            synthesis,
            source_chunks,
            original_text=original_text,
            trigger_reason=trigger_reason,
            round_index=round_index,
            metadata={**common_metadata, "compression_method": "chunked_model_synthesis"},
        )
    if allow_rule_fallback:
        return _rule_fallback_result(
            source_chunks,
            target_chars=target_chars_value,
            preserve_entities=preserve_entities or [],
            trigger_reason=trigger_reason,
            round_index=round_index,
            metadata={
                **common_metadata,
                "compression_method": "rule_fallback_after_synthesis_failure",
                "fallback_reason": synthesis.code,
            },
        )
    return _failure_from_structured_result(
        synthesis,
        trigger_reason=trigger_reason,
        round_index=round_index,
        metadata=common_metadata,
    )


def normalize_compression_chunks(
    *,
    text: str | None,
    chunks: list[Any] | None,
    source_type: str,
    preserve_keys: list[str] | None,
    max_chunk_chars: int,
) -> list[CompressionSourceChunk]:
    raw_chunks: list[CompressionSourceChunk] = []
    if chunks:
        for index, item in enumerate(chunks):
            raw_chunks.append(_chunk_from_input(item, index=index, preserve_keys=preserve_keys))
    elif text is not None:
        raw_chunks.append(
            CompressionSourceChunk(
                source_ref=str(source_type or "text"),
                text=str(text or ""),
                chunk_id="chunk_1",
            )
        )
    return _split_large_chunks(raw_chunks, max_chunk_chars=max_chunk_chars)


def build_context_compression_prompt(
    *,
    source_type: str,
    chunk: CompressionSourceChunk,
    target_chars: int,
    preserve_entities: list[str],
    trigger_reason: str | None,
) -> str:
    payload = {
        "source_type": source_type,
        "source_ref": chunk.source_ref,
        "chunk_id": chunk.chunk_id,
        "target_chars": target_chars,
        "preserve_entities": list(preserve_entities),
        "trigger_reason": trigger_reason,
        "chunk_metadata": chunk.metadata,
        "text": chunk.text,
    }
    return "\n\n".join(
        [
            "Compress the provided context for later Memory or RAG consumption.",
            "Return JSON only. Do not include Markdown or explanatory prose.",
            "Schema: {\"short_summary\":\"string\",\"compressed_text\":\"string\",\"key_points\":[\"string\"],\"preserved_entities\":[\"string\"],\"loss_risk\":\"low|medium|high\",\"warnings\":[\"string\"]}",
            "Rules:",
            "- Preserve explicit entities, identifiers, decisions, file paths, code symbols, and unresolved questions.",
            "- Keep compressed_text concise but semantically useful.",
            "- Do not invent facts not present in the source context.",
            "Compression input:",
            json.dumps(payload, ensure_ascii=False, indent=2),
        ]
    )


def build_context_synthesis_prompt(
    *,
    source_type: str,
    partial_results: list[ContextCompressionResult],
    target_chars: int,
    preserve_entities: list[str],
    trigger_reason: str | None,
) -> str:
    payload = {
        "source_type": source_type,
        "target_chars": target_chars,
        "preserve_entities": list(preserve_entities),
        "trigger_reason": trigger_reason,
        "partial_compressions": [
            {
                "source_refs": result.source_refs,
                "short_summary": result.short_summary,
                "compressed_text": result.compressed_text,
                "key_points": result.key_points,
                "preserved_entities": result.preserved_entities,
                "loss_risk": result.loss_risk,
            }
            for result in partial_results
        ],
    }
    return "\n\n".join(
        [
            "Synthesize the partial context compressions into one coherent compressed context.",
            "Return JSON only. Do not include Markdown or explanatory prose.",
            "Schema: {\"short_summary\":\"string\",\"compressed_text\":\"string\",\"key_points\":[\"string\"],\"preserved_entities\":[\"string\"],\"loss_risk\":\"low|medium|high\",\"warnings\":[\"string\"]}",
            "Rules:",
            "- Preserve cross-chunk dependencies, decisions, identifiers, and unresolved questions.",
            "- Do not invent facts not present in partial_compressions.",
            "Synthesis input:",
            json.dumps(payload, ensure_ascii=False, indent=2),
        ]
    )


def _compress_chunk(
    generate_json: GenerateJsonCallable,
    *,
    chunk: CompressionSourceChunk,
    source_type: str,
    target_chars: int,
    preserve_entities: list[str],
    trigger_reason: str | None,
    round_index: int,
    metadata: dict[str, Any],
) -> ContextCompressionResult:
    prompt = build_context_compression_prompt(
        source_type=source_type,
        chunk=chunk,
        target_chars=target_chars,
        preserve_entities=preserve_entities,
        trigger_reason=trigger_reason,
    )
    try:
        structured = generate_json(
            prompt,
            call_type=ModelCallType.CONTEXT_COMPRESSION.value,
            schema=COMPRESSION_SCHEMA,
            metadata={
                "context_compression": True,
                "source_ref": chunk.source_ref,
                "chunk_id": chunk.chunk_id,
                **metadata,
            },
        )
    except Exception as exc:
        return ContextCompressionResult(
            success=False,
            code=ModelErrorCode.COMPRESSION_FAILED,
            error=str(exc),
            trigger_reason=trigger_reason,
            round_index=round_index,
            metadata=metadata,
        )
    return _result_from_structured_payload(
        structured,
        [chunk],
        trigger_reason=trigger_reason,
        round_index=round_index,
        metadata=metadata,
    )


def _synthesize_chunks(
    generate_json: GenerateJsonCallable,
    *,
    partial_results: list[ContextCompressionResult],
    source_chunks: list[CompressionSourceChunk],
    source_type: str,
    target_chars: int,
    preserve_entities: list[str],
    trigger_reason: str | None,
    round_index: int,
    metadata: dict[str, Any],
) -> ContextCompressionResult:
    prompt = build_context_synthesis_prompt(
        source_type=source_type,
        partial_results=partial_results,
        target_chars=target_chars,
        preserve_entities=preserve_entities,
        trigger_reason=trigger_reason,
    )
    try:
        structured = generate_json(
            prompt,
            call_type=ModelCallType.CONTEXT_COMPRESSION.value,
            schema=COMPRESSION_SCHEMA,
            metadata={
                "context_compression": True,
                "synthesis": True,
                **metadata,
            },
        )
    except Exception as exc:
        return ContextCompressionResult(
            success=False,
            code=ModelErrorCode.COMPRESSION_FAILED,
            error=str(exc),
            trigger_reason=trigger_reason,
            round_index=round_index,
            metadata=metadata,
        )
    result = _result_from_structured_payload(
        structured,
        source_chunks,
        trigger_reason=trigger_reason,
        round_index=round_index,
        metadata=metadata,
    )
    if result.success:
        result.metadata["partial_result_count"] = len(partial_results)
    return result


def _result_from_structured_payload(
    structured: StructuredModelResult,
    source_chunks: list[CompressionSourceChunk],
    *,
    trigger_reason: str | None,
    round_index: int,
    metadata: dict[str, Any],
) -> ContextCompressionResult:
    if not structured.success or not isinstance(structured.data, dict):
        return ContextCompressionResult(
            success=False,
            code=structured.code or ModelErrorCode.COMPRESSION_FAILED,
            error=structured.error or "context compression model output failed",
            trigger_reason=trigger_reason,
            round_index=round_index,
            model_result=structured.model_result,
            metadata={**metadata, "structured_result": structured.to_dict()},
        )

    payload = structured.data
    compressed_text = str(payload.get("compressed_text") or "").strip()
    if not compressed_text:
        return ContextCompressionResult(
            success=False,
            code=ModelErrorCode.COMPRESSION_FAILED,
            error="context compression output missing compressed_text",
            trigger_reason=trigger_reason,
            round_index=round_index,
            model_result=structured.model_result,
            metadata={**metadata, "structured_result": structured.to_dict()},
        )

    original_length = sum(chunk.original_length for chunk in source_chunks)
    compressed_length = len(compressed_text)
    warnings = _string_list(payload.get("warnings"))
    loss_risk = _loss_risk(payload.get("loss_risk"), warnings)
    preserved_entities = _string_list(payload.get("preserved_entities"))
    return ContextCompressionResult(
        success=True,
        short_summary=str(payload.get("short_summary") or compressed_text[:160]),
        compressed_text=compressed_text,
        compressed_chunks=[
            CompressedChunkRef(
                source_ref=chunk.source_ref,
                chunk_id=chunk.chunk_id,
                original_length=chunk.original_length,
                compressed_length=compressed_length if len(source_chunks) == 1 else None,
                metadata=dict(chunk.metadata),
            )
            for chunk in source_chunks
        ],
        source_refs=[chunk.source_ref for chunk in source_chunks],
        original_length=original_length,
        compressed_length=compressed_length,
        original_token_count=_estimate_tokens(original_length),
        compressed_token_count=_estimate_tokens(compressed_length),
        compression_ratio=(compressed_length / original_length) if original_length else 0.0,
        trigger_reason=trigger_reason,
        round_index=round_index,
        loss_risk=loss_risk,
        key_points=_string_list(payload.get("key_points")),
        preserved_entities=preserved_entities,
        warnings=warnings,
        model_result=structured.model_result,
        metadata={
            **metadata,
            "compression_method": metadata.get("compression_method", "model"),
            "raw_json_text": structured.raw_json_text,
            "repair_attempts": structured.repair_attempts,
        },
    )


def _finalize_compression_result(
    result: ContextCompressionResult,
    source_chunks: list[CompressionSourceChunk],
    *,
    original_text: str,
    trigger_reason: str | None,
    round_index: int,
    metadata: dict[str, Any],
) -> ContextCompressionResult:
    original_length = len(original_text)
    compressed_length = len(result.compressed_text)
    result.compressed_chunks = [
        CompressedChunkRef(
            source_ref=chunk.source_ref,
            chunk_id=chunk.chunk_id,
            original_length=chunk.original_length,
            compressed_length=(
                compressed_length if len(source_chunks) == 1 else None
            ),
            metadata=dict(chunk.metadata),
        )
        for chunk in source_chunks
    ]
    result.source_refs = [chunk.source_ref for chunk in source_chunks]
    result.original_length = original_length
    result.compressed_length = compressed_length
    result.original_token_count = _estimate_tokens(original_length)
    result.compressed_token_count = _estimate_tokens(compressed_length)
    result.compression_ratio = (compressed_length / original_length) if original_length else 0.0
    result.trigger_reason = trigger_reason
    result.round_index = round_index
    result.metadata = {**result.metadata, **metadata}
    return result


def _failure_from_structured_result(
    result: ContextCompressionResult,
    *,
    trigger_reason: str | None,
    round_index: int,
    metadata: dict[str, Any],
) -> ContextCompressionResult:
    return ContextCompressionResult(
        success=False,
        code=result.code or ModelErrorCode.COMPRESSION_FAILED,
        error=result.error or "context compression failed",
        trigger_reason=trigger_reason,
        round_index=round_index,
        model_result=result.model_result,
        metadata={**metadata, **result.metadata},
    )


def _rule_fallback_result(
    source_chunks: list[CompressionSourceChunk],
    *,
    target_chars: int,
    preserve_entities: list[str],
    trigger_reason: str | None,
    round_index: int,
    metadata: dict[str, Any],
) -> ContextCompressionResult:
    original_text = "\n\n".join(chunk.text for chunk in source_chunks)
    prefix = ""
    if preserve_entities:
        prefix = "Preserved entities: " + ", ".join(preserve_entities) + "\n"
    limit = max(target_chars - len(prefix), 1)
    compressed_text = prefix + original_text[:limit]
    original_length = len(original_text)
    compressed_length = len(compressed_text)
    warnings = ["rule_fallback_truncation_used"]
    if original_length > compressed_length:
        warnings.append("compressed_text_truncated_to_target_chars")
    return ContextCompressionResult(
        success=True,
        short_summary="Rule fallback context compression.",
        compressed_text=compressed_text,
        compressed_chunks=[
            CompressedChunkRef(
                source_ref=chunk.source_ref,
                chunk_id=chunk.chunk_id,
                original_length=chunk.original_length,
                compressed_length=None,
                metadata=dict(chunk.metadata),
            )
            for chunk in source_chunks
        ],
        source_refs=[chunk.source_ref for chunk in source_chunks],
        original_length=original_length,
        compressed_length=compressed_length,
        original_token_count=_estimate_tokens(original_length),
        compressed_token_count=_estimate_tokens(compressed_length),
        compression_ratio=(compressed_length / original_length) if original_length else 0.0,
        trigger_reason=trigger_reason,
        round_index=round_index,
        loss_risk="high" if original_length > compressed_length else "medium",
        preserved_entities=list(preserve_entities),
        warnings=warnings,
        metadata=metadata,
    )


def _chunk_from_input(
    item: Any,
    *,
    index: int,
    preserve_keys: list[str] | None,
) -> CompressionSourceChunk:
    if isinstance(item, dict):
        chunk_id = str(item.get("chunk_id") or item.get("id") or f"chunk_{index + 1}")
        source_ref = str(item.get("source_ref") or item.get("ref") or chunk_id)
        text = item.get("text", item.get("content", ""))
        preserved = {
            key: item.get(key)
            for key in (preserve_keys or [])
            if key in item and key not in {"text", "content"}
        }
        metadata = dict(item.get("metadata") or {})
        if preserved:
            metadata["preserved_fields"] = preserved
        return CompressionSourceChunk(
            source_ref=source_ref,
            text=str(text or ""),
            chunk_id=chunk_id,
            metadata=metadata,
        )
    return CompressionSourceChunk(
        source_ref=f"chunk_{index + 1}",
        text=str(item or ""),
        chunk_id=f"chunk_{index + 1}",
    )


def _split_large_chunks(
    chunks: list[CompressionSourceChunk],
    *,
    max_chunk_chars: int,
) -> list[CompressionSourceChunk]:
    limit = max(int(max_chunk_chars), 1)
    split_chunks: list[CompressionSourceChunk] = []
    for chunk in chunks:
        if len(chunk.text) <= limit:
            split_chunks.append(chunk)
            continue
        start = 0
        part_index = 1
        while start < len(chunk.text):
            part = chunk.text[start : start + limit]
            split_chunks.append(
                CompressionSourceChunk(
                    source_ref=chunk.source_ref,
                    text=part,
                    chunk_id=f"{chunk.chunk_id}_part_{part_index}",
                    metadata={**chunk.metadata, "split_from": chunk.chunk_id},
                )
            )
            start += limit
            part_index += 1
    return split_chunks


def _target_chars(target_chars: int | None, target_tokens: int | None) -> int:
    if target_chars is not None:
        return max(int(target_chars), 1)
    if target_tokens is not None:
        return max(int(target_tokens) * 4, 1)
    return DEFAULT_TARGET_CHARS


def _chunk_size(target_chars: int | None, target_tokens: int | None) -> int:
    target = _target_chars(target_chars, target_tokens)
    return max(DEFAULT_MIN_CHUNK_CHARS, min(DEFAULT_MAX_CHUNK_CHARS, target * 3))


def _estimate_tokens(char_count: int | None) -> int | None:
    if char_count is None:
        return None
    return max(0, (int(char_count) + 3) // 4)


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if value is None:
        return []
    return [str(value)]


def _loss_risk(value: Any, warnings: list[str]) -> str:
    risk = str(value or "medium").strip().lower()
    if risk not in LOSS_RISK_VALUES:
        warnings.append(f"unsupported_loss_risk:{risk}")
        return "medium"
    return risk


__all__ = [
    "COMPRESSION_SCHEMA",
    "CompressionSourceChunk",
    "build_context_compression_prompt",
    "build_context_synthesis_prompt",
    "compress_context_with_model",
    "normalize_compression_chunks",
]
