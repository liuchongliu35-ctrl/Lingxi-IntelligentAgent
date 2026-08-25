from __future__ import annotations

import os
import unittest

from src.models import (
    ModelCallResult,
    ModelErrorCode,
    ModelManager,
    ModelsConfig,
    ModelsRuntimeConfig,
    StructuredModelResult,
    default_provider_specs,
    default_route_configs,
    parse_json_output,
    validate_json_schema,
)


class SequenceModel:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = 0

    def generate(self, prompt: str, **kwargs):
        self.calls += 1
        response = self.responses[min(self.calls - 1, len(self.responses) - 1)]
        if isinstance(response, ModelCallResult):
            return response
        return ModelCallResult.ok(str(response))

    def stream_generate(self, prompt: str, **kwargs):
        yield "stream"


def make_config() -> ModelsConfig:
    return ModelsConfig(
        workspace_root=os.getcwd(),
        config_dir=os.getcwd(),
        runtime=ModelsRuntimeConfig(
            retry_backoff_base_seconds=0.0,
            retry_backoff_max_seconds=0.0,
        ),
        provider_specs=default_provider_specs(),
        provider_confs={},
        routes=default_route_configs(),
        structured_output={
            "repair_enabled": True,
            "default_repair_attempts": 1,
            "parse_modes": {
                "chat": "lenient",
                "react_action_decision": "strict",
            },
            "schemas": {
                "simple_object": {
                    "type": "object",
                    "required": ["name"],
                    "properties": {"name": {"type": "string"}},
                }
            },
        },
    )


class ModelsStructuredOutputTest(unittest.TestCase):
    def test_parse_json_output_accepts_pure_json(self):
        parsed = parse_json_output('{"ok": true}', parse_mode="strict")

        self.assertTrue(parsed.success)
        self.assertEqual(parsed.data, {"ok": True})

    def test_lenient_parse_accepts_fenced_and_embedded_json(self):
        fenced = parse_json_output('```json\n{"ok": true}\n```', parse_mode="lenient")
        embedded = parse_json_output('Here is the result: {"items": [1, 2]} thanks.', parse_mode="lenient")

        self.assertTrue(fenced.success)
        self.assertEqual(fenced.metadata["json_source"], "fenced")
        self.assertTrue(embedded.success)
        self.assertEqual(embedded.data, {"items": [1, 2]})

    def test_strict_parse_rejects_mixed_prose_without_repair(self):
        manager = ModelManager(model_name="mock", models_config=make_config())
        manager.model = SequenceModel('Here is JSON: {"ok": true}')

        result = manager.generate_json(
            "return JSON",
            parse_mode="strict",
            repair_enabled=False,
        )

        self.assertFalse(result.success)
        self.assertEqual(result.code, ModelErrorCode.INVALID_JSON.value)

    def test_generate_json_repairs_invalid_json(self):
        manager = ModelManager(model_name="mock", models_config=make_config())
        manager.model = SequenceModel("not JSON", '{"ok": true}')

        result = manager.generate_json("return JSON", parse_mode="strict")

        self.assertIsInstance(result, StructuredModelResult)
        self.assertTrue(result.success)
        self.assertEqual(result.data, {"ok": True})
        self.assertEqual(result.repair_attempts, 1)
        self.assertEqual(manager.model.calls, 2)
        self.assertTrue(result.metadata["repair_used"])

    def test_generate_json_returns_repair_failure_when_exhausted(self):
        manager = ModelManager(model_name="mock", models_config=make_config())
        manager.model = SequenceModel("not JSON", "still not JSON")

        result = manager.generate_json("return JSON", parse_mode="strict", max_repair_attempts=1)

        self.assertFalse(result.success)
        self.assertEqual(result.code, ModelErrorCode.JSON_REPAIR_FAILED.value)
        self.assertEqual(result.repair_attempts, 1)

    def test_schema_validation_failure_is_structured(self):
        manager = ModelManager(model_name="mock", models_config=make_config())
        manager.model = SequenceModel('{"title": "missing name"}')

        result = manager.generate_json(
            "return JSON",
            schema_name="simple_object",
            max_repair_attempts=0,
        )

        self.assertFalse(result.success)
        self.assertEqual(result.code, ModelErrorCode.SCHEMA_INVALID.value)
        self.assertFalse(result.schema_valid)

    def test_schema_validation_subset_accepts_valid_payload(self):
        schema = {
            "type": "object",
            "required": ["name"],
            "properties": {"name": {"type": "string"}},
        }

        result = validate_json_schema({"name": "Ada"}, schema)

        self.assertTrue(result.valid)

    def test_model_failure_propagates_as_structured_result(self):
        manager = ModelManager(model_name="mock", models_config=make_config())
        manager.model = SequenceModel(
            ModelCallResult.fail(ModelErrorCode.MODEL_CALL_FAILED, "provider failed")
        )

        result = manager.generate_json("return JSON")

        self.assertFalse(result.success)
        self.assertEqual(result.code, ModelErrorCode.MODEL_CALL_FAILED.value)
        self.assertEqual(result.error, "provider failed")

    def test_action_packet_business_fields_are_not_validated_by_models_layer(self):
        manager = ModelManager(model_name="mock", models_config=make_config())
        manager.model = SequenceModel(
            '{"action_type":"teleport","action_args":{},"confidence":0.2}'
        )
        generic_schema = {
            "type": "object",
            "required": ["action_type", "action_args"],
            "properties": {
                "action_type": {"type": "string"},
                "action_args": {"type": "object"},
            },
        }

        result = manager.generate_json("return ActionPacket", schema=generic_schema)

        self.assertTrue(result.success)
        self.assertEqual(result.data["action_type"], "teleport")


if __name__ == "__main__":
    unittest.main()
