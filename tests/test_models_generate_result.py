from __future__ import annotations

import unittest

from src.models import ModelCallResult, ModelErrorCode, ModelManager, ModelStreamChunk
from src.models.compat import ModelCallFailure, require_model_content


class RaisingModel:
    def generate(self, prompt: str, **kwargs):
        raise RuntimeError("boom")

    def stream_generate(self, prompt: str, **kwargs):
        raise RuntimeError("boom")


class StreamingModel:
    def generate(self, prompt: str, **kwargs):
        return "streamed text"

    def stream_generate(self, prompt: str, **kwargs):
        yield "hello"
        yield " world"


class StructuredFailureModel:
    def generate(self, prompt: str, **kwargs):
        return ModelCallResult.fail(ModelErrorCode.MODEL_CALL_FAILED, "provider said no")

    def stream_generate(self, prompt: str, **kwargs):
        yield ModelStreamChunk(success=False, code=ModelErrorCode.MODEL_CALL_FAILED, error="stream failed")


class ModelsGenerateResultTest(unittest.TestCase):
    def test_generate_wraps_legacy_string_result(self):
        manager = ModelManager(model_name="mock")

        result = manager.generate("hello")

        self.assertTrue(result.success)
        self.assertIsInstance(result, ModelCallResult)
        self.assertEqual(result.content, require_model_content(result))

    def test_generate_returns_structured_failure_when_provider_raises(self):
        manager = ModelManager(model_name="mock")
        manager.model = RaisingModel()

        result = manager.generate("hello")

        self.assertFalse(result.success)
        self.assertEqual(result.content, "")
        self.assertEqual(result.code, ModelErrorCode.MODEL_CALL_FAILED.value)

    def test_stream_generate_wraps_chunks_structurally(self):
        manager = ModelManager(model_name="mock")
        manager.model = StreamingModel()

        chunks = list(manager.stream_generate("hello"))

        self.assertEqual([chunk.content_delta for chunk in chunks], ["hello", " world"])
        self.assertTrue(chunks[-1].is_final)
        self.assertTrue(all(isinstance(chunk, ModelStreamChunk) for chunk in chunks))

    def test_require_model_content_raises_on_structured_failure(self):
        result = StructuredFailureModel().generate("hello")

        with self.assertRaises(ModelCallFailure):
            require_model_content(result)


if __name__ == "__main__":
    unittest.main()
