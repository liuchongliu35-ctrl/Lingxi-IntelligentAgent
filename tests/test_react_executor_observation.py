from __future__ import annotations

import json
import unittest

from src.agent.react_executor_observation import (
    REDACTED_VALUE,
    ObservationStore,
    observation_to_text,
    sanitize_sensitive,
)
from src.agent.react_executor_protocol import ObservationPacket


def make_observation(**overrides):
    defaults = {
        "execution_id": "exec_1",
        "plan_id": "plan_1",
        "task_id": "task_1",
        "step_id": "step_1",
        "action_type": "call_tool",
        "action_target": "tool",
        "tool_name": "tool",
        "success": True,
        "message": "ok",
        "data": {"value": "data"},
        "model_consumable_observation": {"value": "model data"},
    }
    defaults.update(overrides)
    return ObservationPacket(**defaults)


class ReActExecutorObservationStoreTest(unittest.TestCase):
    def test_add_and_get_by_observation_id(self):
        store = ObservationStore()
        observation = make_observation(observation_id="observation_1")

        store.add(observation)

        self.assertIs(store.get("observation_1"), observation)
        self.assertIsNone(store.get("missing"))

    def test_get_by_step_and_latest_for_step(self):
        store = ObservationStore()
        first = make_observation(observation_id="observation_1", step_id="step_1", message="first")
        second = make_observation(observation_id="observation_2", step_id="step_1", message="second")
        other = make_observation(observation_id="observation_3", step_id="step_2")

        store.add(first)
        store.add(second)
        store.add(other)

        self.assertEqual([item.observation_id for item in store.get_by_step("step_1")], ["observation_1", "observation_2"])
        self.assertIs(store.get_latest_for_step("step_1"), second)
        self.assertIs(store.get_latest_for_step("step_2"), other)

    def test_output_key_index_supports_explicit_and_inferred_keys(self):
        store = ObservationStore()
        explicit = make_observation(observation_id="observation_1", step_id="step_1")
        inferred = make_observation(
            observation_id="observation_2",
            step_id="step_2",
            checker_result={"output_key": "checker_key"},
        )

        store.add(explicit, output_key="summary")
        store.add(inferred)

        self.assertIs(store.get_by_output_key("summary"), explicit)
        self.assertIs(store.get_by_output_key("checker_key"), inferred)

    def test_resolve_input_refs_supports_step_output_key_and_observation_id(self):
        store = ObservationStore()
        step_observation = make_observation(
            observation_id="observation_step",
            step_id="step_1",
            model_consumable_observation={"from": "step"},
        )
        key_observation = make_observation(
            observation_id="observation_key",
            step_id="step_2",
            model_consumable_observation={"from": "key"},
        )
        id_observation = make_observation(
            observation_id="observation_id",
            step_id="step_3",
            model_consumable_observation={"from": "id"},
        )

        store.add(step_observation)
        store.add(key_observation, output_key="summary")
        store.add(id_observation)

        resolved = store.resolve_input_refs(["step_1", "summary", "observation_id", "missing"])

        self.assertEqual(resolved["step_1"], {"from": "step"})
        self.assertEqual(resolved["summary"], {"from": "key"})
        self.assertEqual(resolved["observation_id"], {"from": "id"})
        self.assertEqual(resolved["missing"], {"missing": True, "ref": "missing"})

    def test_to_model_context_redacts_sensitive_values(self):
        store = ObservationStore()
        observation = make_observation(
            observation_id="observation_1",
            step_id="step_1",
            model_consumable_observation={
                "content": "safe",
                "api_key": "secret-key",
                "nested": {"authorization": "Bearer token"},
            },
        )

        store.add(observation, output_key="result")
        context = store.to_model_context(["result"])

        self.assertEqual(context[0]["model_consumable_observation"]["content"], "safe")
        self.assertEqual(context[0]["model_consumable_observation"]["api_key"], REDACTED_VALUE)
        self.assertEqual(context[0]["model_consumable_observation"]["nested"]["authorization"], REDACTED_VALUE)

    def test_to_model_context_truncates_long_model_consumable_values(self):
        store = ObservationStore()
        long_text = "x" * 500
        observation = make_observation(
            observation_id="observation_1",
            step_id="step_1",
            message=long_text,
            model_consumable_observation={"content": long_text},
        )

        store.add(observation, output_key="long_output")
        context = store.to_model_context(["long_output"], max_value_chars=120)

        self.assertIn("[truncated", context[0]["message"])
        self.assertTrue(context[0]["model_consumable_observation"]["truncated"])
        self.assertEqual(context[0]["model_consumable_observation"]["original_chars"], len(json.dumps({"content": long_text}, ensure_ascii=False)))
        self.assertNotIn(long_text, json.dumps(context, ensure_ascii=False))

    def test_recent_model_context_uses_latest_observations_without_raw_payloads(self):
        store = ObservationStore()
        first = make_observation(observation_id="observation_1", step_id="step_1", raw_observation={"password": "pw"})
        second = make_observation(observation_id="observation_2", step_id="step_2", model_consumable_observation={"content": "second"})
        third = make_observation(observation_id="observation_3", step_id="step_3", model_consumable_observation={"content": "third"})

        store.add(first)
        store.add(second)
        store.add(third)
        context = store.recent_model_context(max_observations=2, max_value_chars=120)

        self.assertEqual([item["observation_id"] for item in context], ["observation_2", "observation_3"])
        self.assertEqual(context[0]["model_consumable_observation"]["content"], "second")
        self.assertNotIn("raw_observation", json.dumps(context, ensure_ascii=False))
        self.assertNotIn("password", json.dumps(context, ensure_ascii=False))

    def test_resolve_input_refs_can_return_compact_model_values(self):
        store = ObservationStore()
        long_text = "x" * 500
        observation = make_observation(
            observation_id="observation_1",
            step_id="step_1",
            model_consumable_observation={"content": long_text},
        )

        store.add(observation, output_key="long_output")
        full = store.resolve_input_refs(["long_output"])
        compact = store.resolve_input_refs(["long_output"], compact=True, max_value_chars=120)

        self.assertEqual(full["long_output"]["content"], long_text)
        self.assertTrue(compact["long_output"]["truncated"])
        self.assertNotIn(long_text, json.dumps(compact, ensure_ascii=False))

    def test_to_dict_redacts_sensitive_input_and_raw_observation(self):
        store = ObservationStore()
        observation = make_observation(
            observation_id="observation_1",
            input_args={"token": "tool-token", "query": "safe"},
            raw_observation={"password": "pw", "data": "safe"},
            model_consumable_observation={"secret": "hidden", "data": "safe"},
        )

        store.add(observation, output_key="safe_output")
        payload = store.to_dict()

        stored = payload["observations"][0]
        self.assertEqual(stored["input_args"]["token"], REDACTED_VALUE)
        self.assertEqual(stored["raw_observation"]["password"], REDACTED_VALUE)
        self.assertEqual(stored["model_consumable_observation"]["secret"], REDACTED_VALUE)
        self.assertEqual(stored["input_args"]["query"], "safe")
        json.dumps(payload, ensure_ascii=False)

    def test_store_preserves_raw_observation_in_memory(self):
        store = ObservationStore()
        observation = make_observation(raw_observation={"password": "pw"})

        store.add(observation)

        self.assertEqual(store.observations[0].raw_observation["password"], "pw")
        self.assertEqual(store.to_dict()["observations"][0]["raw_observation"]["password"], REDACTED_VALUE)

    def test_observation_to_text_prefers_model_consumable_then_data_then_message(self):
        with_model = make_observation(model_consumable_observation={"answer": 42}, data={"data": 1}, message="msg")
        with_data = make_observation(model_consumable_observation=None, data={"data": 1}, message="msg")
        with_message = make_observation(model_consumable_observation=None, data=None, message="msg")

        self.assertEqual(observation_to_text(with_model), '{"answer": 42}')
        self.assertEqual(observation_to_text(with_data), '{"data": 1}')
        self.assertEqual(observation_to_text(with_message), "msg")

    def test_sanitize_sensitive_handles_nested_collections(self):
        payload = {
            "items": [
                {"password": "pw"},
                {"safe": "ok", "client_secret": "secret"},
            ]
        }

        sanitized = sanitize_sensitive(payload)

        self.assertEqual(sanitized["items"][0]["password"], REDACTED_VALUE)
        self.assertEqual(sanitized["items"][1]["client_secret"], REDACTED_VALUE)
        self.assertEqual(sanitized["items"][1]["safe"], "ok")


if __name__ == "__main__":
    unittest.main()
