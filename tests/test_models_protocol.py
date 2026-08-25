from __future__ import annotations

import json
import unittest

from src.models.errors import MODEL_ERROR_CODES, ModelErrorCode, normalize_model_error_code
from src.models.protocol import (
    CompressedChunkRef,
    ContextCompressionResult,
    EmbeddingBatchResult,
    EmbeddingResult,
    ModelCallOptions,
    ModelCallResult,
    ModelCallType,
    ModelCost,
    ModelErrorInfo,
    ModelHealthStatus,
    ModelMessage,
    ModelMessageRole,
    ModelStreamChunk,
    ModelStreamResult,
    ModelTraceContext,
    ModelUsage,
    StructuredModelResult,
    coerce_model_messages,
)


class ModelsProtocolTest(unittest.TestCase):
    def test_message_roles_and_prompt_conversion_are_stable(self):
        message = ModelMessage(
            role=ModelMessageRole.SYSTEM,
            content="Follow the protocol.",
            metadata={"priority": 1},
        )
        options = ModelCallOptions(
            call_type=ModelCallType.REACT_ACTION_DECISION,
            prompt="Return one JSON object.",
        )

        self.assertEqual(message.role, "system")
        self.assertEqual(options.call_type, "react_action_decision")
        self.assertEqual(options.to_messages()[0].to_dict()["role"], "user")
        self.assertEqual(options.to_messages()[0].content, "Return one JSON object.")

        messages = coerce_model_messages(
            [
                {"role": "system", "content": "Use JSON."},
                ModelMessage(role="user", content="Plan this task."),
            ]
        )
        self.assertEqual([item.role for item in messages], ["system", "user"])

        with self.assertRaises(ValueError):
            ModelMessage(role="developer", content="Unsupported in V1.")
        with self.assertRaises(TypeError):
            ModelMessage(role="user", content=None)  # type: ignore[arg-type]

    def test_options_preserve_messages_and_trace_context(self):
        trace = ModelTraceContext(
            source_trace_id="trace_1",
            plan_id="plan_1",
            execution_id="exec_1",
            task_id="task_1",
            step_id="step_1",
            packet_id="packet_1",
            caller="react_executor",
        )
        options = ModelCallOptions(
            call_type="react_action_decision",
            messages=[{"role": "system", "content": "Use ActionPacket."}],
            prompt="This prompt is only a legacy fallback.",
            max_retries=-2,
            max_tokens=-1,
            timeout_seconds=-0.5,
            trace_context=trace,
        )

        payload = options.to_dict()

        self.assertEqual(options.to_messages()[0].content, "Use ActionPacket.")
        self.assertEqual(options.max_retries, 0)
        self.assertEqual(options.max_tokens, 0)
        self.assertEqual(options.timeout_seconds, 0.0)
        self.assertEqual(payload["trace_context"]["execution_id"], "exec_1")
        json.dumps(payload, ensure_ascii=False)

        with self.assertRaises(ValueError):
            ModelCallOptions(call_type="free_form_reasoning")

    def test_success_and_failure_model_results_enforce_content_boundary(self):
        trace = ModelTraceContext(source_trace_id="trace_1", execution_id="exec_1")
        success = ModelCallResult.ok(
            "The task is complete.",
            provider="mock",
            model="mock-v1",
            call_type=ModelCallType.CHAT,
            trace_context=trace,
            usage=ModelUsage(prompt_tokens=3, completion_tokens=4, source="provider"),
            cost=ModelCost(total_cost=None),
            raw_response={"content": "The task is complete."},
        )
        failure = ModelCallResult.fail(
            ModelErrorCode.MISSING_API_KEY,
            "OPENAI_API_KEY is not configured",
            content="This must not be returned as model content.",
            provider="openai",
            retriable=True,
        )

        self.assertTrue(success.success)
        self.assertEqual(success.content, "The task is complete.")
        self.assertIsNone(success.code)
        self.assertEqual(success.source_trace_id, "trace_1")
        self.assertEqual(success.model_request_id, success.request_id)
        self.assertEqual(success.usage.total_tokens, None)

        self.assertFalse(failure.success)
        self.assertEqual(failure.content, "")
        self.assertEqual(failure.code, "missing_api_key")
        self.assertEqual(failure.error_info.code, "missing_api_key")
        self.assertTrue(failure.retriable)
        json.dumps(success.to_dict(), ensure_ascii=False)
        json.dumps(failure.to_dict(), ensure_ascii=False)

    def test_error_codes_are_fixed_and_unknown_values_normalize(self):
        self.assertIn("rate_limited", MODEL_ERROR_CODES)
        self.assertEqual(
            normalize_model_error_code(ModelErrorCode.NETWORK_ERROR),
            "network_error",
        )
        self.assertEqual(
            normalize_model_error_code("provider_specific_error"),
            "unknown_error",
        )

        error = ModelErrorInfo(
            code="rate_limited",
            message="Too many requests.",
            category="provider",
            retriable=True,
            fallback_allowed=True,
            http_status=429,
            provider_error_code="quota_temporary",
        )

        self.assertEqual(error.code, "rate_limited")
        self.assertEqual(error.http_status, 429)
        json.dumps(error.to_dict(), ensure_ascii=False)

    def test_stream_and_structured_results_serialize_without_bare_strings(self):
        stream_chunk = ModelStreamChunk(success=True, content_delta="Hello", index=-1)
        stream_failure = ModelStreamChunk(
            success=False,
            content_delta="must be cleared",
            code=ModelErrorCode.TIMEOUT,
        )
        stream_result = ModelStreamResult(
            success=False,
            content="must be cleared",
            code=ModelErrorCode.NETWORK_ERROR,
            chunks_count=-1,
            latency_ms=-3,
        )
        model_result = ModelCallResult.ok('{"intent":"chat"}')
        structured = StructuredModelResult(
            success=True,
            data={"intent": "chat"},
            content='{"intent":"chat"}',
            parse_mode="strict",
            schema_name="intent_fallback",
            schema_valid=True,
            model_result=model_result,
        )
        structured_failure = StructuredModelResult(
            success=False,
            content="not JSON",
            model_result=ModelCallResult.fail(
                ModelErrorCode.MODEL_CALL_FAILED,
                "provider unavailable",
            ),
        )

        self.assertEqual(stream_chunk.index, 0)
        self.assertEqual(stream_failure.content_delta, "")
        self.assertEqual(stream_failure.code, "timeout")
        self.assertEqual(stream_result.content, "")
        self.assertEqual(stream_result.code, "network_error")
        self.assertEqual(structured.parse_mode, "strict")
        self.assertEqual(structured_failure.code, "model_call_failed")
        json.dumps(structured.to_dict(), ensure_ascii=False)
        json.dumps(structured_failure.to_dict(), ensure_ascii=False)

    def test_health_embedding_and_compression_results_are_structured(self):
        health = ModelHealthStatus(
            healthy=False,
            provider_conf_id="conf_openai_default",
            provider="openai",
            protocol="openai-compatible",
            model="example-model",
            configured=False,
            missing_config=["api_key"],
            check_type="config_check",
            code=ModelErrorCode.MISSING_API_KEY,
        )
        embedding = EmbeddingResult(success=True, embedding=[1, 2.5, -3])
        embedding_failure = EmbeddingResult(
            success=False,
            embedding=[1.0],
            code=ModelErrorCode.MISSING_MODEL_CONFIG,
        )
        batch = EmbeddingBatchResult(
            success=True,
            embeddings=[[1, 2], [3, 4]],
            item_results=[embedding],
        )
        compression = ContextCompressionResult(
            success=True,
            short_summary="Completed steps are retained.",
            compressed_text="A concise execution summary.",
            compressed_chunks=[
                CompressedChunkRef(
                    source_ref="observation_1",
                    chunk_id="chunk_1",
                    original_length=100,
                    compressed_length=40,
                )
            ],
            source_refs=["observation_1"],
            original_length=100,
            compressed_length=40,
            compression_ratio=0.4,
            loss_risk="low",
            key_points=["step_1 completed"],
        )
        compression_failure = ContextCompressionResult(
            success=False,
            short_summary="must be cleared",
            compressed_text="must be cleared",
        )

        self.assertEqual(health.code, "missing_api_key")
        self.assertEqual(embedding.embedding, [1.0, 2.5, -3.0])
        self.assertEqual(embedding.dimensions, 3)
        self.assertIsNone(embedding_failure.embedding)
        self.assertEqual(embedding_failure.code, "missing_model_config")
        self.assertEqual(batch.embeddings, [[1.0, 2.0], [3.0, 4.0]])
        self.assertEqual(compression.compressed_chunks[0].chunk_id, "chunk_1")
        self.assertEqual(compression_failure.short_summary, "")
        self.assertEqual(compression_failure.code, "compression_failed")

        for value in (
            health,
            embedding,
            embedding_failure,
            batch,
            compression,
            compression_failure,
        ):
            json.dumps(value.to_dict(), ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
