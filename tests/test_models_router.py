from __future__ import annotations

import os
import unittest

from src.models import (
    ModelCallOptions,
    ModelCallType,
    ModelErrorCode,
    ModelManager,
    ModelRouter,
    ModelsConfig,
    ModelsRuntimeConfig,
    ProviderSpec,
    ProviderConf,
    ProviderCredential,
    RouteCandidate,
    RouteConfig,
    default_provider_specs,
    default_route_configs,
)


def make_mock_conf(
    conf_id: str,
    *,
    enabled: bool = True,
    model: str = "mock-v1",
    max_context_chars: int | None = None,
) -> ProviderConf:
    return ProviderConf(
        id=conf_id,
        name=conf_id,
        provider="mock",
        protocol="mock",
        enabled=enabled,
        default_model=model,
        credentials=[ProviderCredential(slug="default")],
        max_context_chars=max_context_chars,
    )


def make_custom_conf(conf_id: str = "conf_custom") -> ProviderConf:
    return ProviderConf(
        id=conf_id,
        name="Custom",
        provider="custom_openai_compatible",
        protocol="openai-compatible",
        enabled=True,
        base_url="https://example.invalid/v1",
        default_model="custom-model",
        credentials=[ProviderCredential(slug="primary", api_key_env="CUSTOM_API_KEY")],
    )


def make_config(
    *,
    provider_confs: list[ProviderConf] | None = None,
    routes: dict[str, RouteConfig] | None = None,
) -> ModelsConfig:
    route_values = default_route_configs()
    route_values.update(routes or {})
    conf_values = {conf.id: conf for conf in provider_confs or []}
    return ModelsConfig(
        workspace_root=os.getcwd(),
        config_dir=os.getcwd(),
        runtime=ModelsRuntimeConfig(),
        provider_specs=default_provider_specs(),
        provider_confs=conf_values,
        routes=route_values,
    )


class ModelsRouterTest(unittest.TestCase):
    def test_call_type_uses_route_specific_parameters(self):
        manager = ModelManager(model_name="mock", models_config=make_config())

        manager.generate("hello", call_type=ModelCallType.REACT_ACTION_DECISION)

        kwargs = manager.model.generate_calls[-1]["kwargs"]
        self.assertEqual(kwargs["temperature"], 0.1)
        self.assertEqual(kwargs["max_tokens"], 1200)
        self.assertTrue(kwargs["json_mode"])

    def test_explicit_parameters_override_route_parameters(self):
        manager = ModelManager(model_name="mock", models_config=make_config())

        manager.generate(
            "hello",
            call_type=ModelCallType.REACT_ACTION_DECISION,
            temperature=0.7,
            max_tokens=33,
        )

        kwargs = manager.model.generate_calls[-1]["kwargs"]
        self.assertEqual(kwargs["temperature"], 0.7)
        self.assertEqual(kwargs["max_tokens"], 33)
        self.assertTrue(kwargs["json_mode"])

    def test_unsupported_top_k_is_filtered_before_provider_call(self):
        manager = ModelManager(model_name="mock", models_config=make_config())

        result = manager.generate("hello", top_k=20)

        kwargs = manager.model.generate_calls[-1]["kwargs"]
        self.assertNotIn("top_k", kwargs)
        self.assertIn("top_k", result.metadata["unsupported_params"])

    def test_unsupported_json_mode_is_filtered_by_provider_spec(self):
        config = make_config(provider_confs=[make_custom_conf()])
        config.provider_specs["custom_openai_compatible"] = ProviderSpec(
            provider="custom_openai_compatible",
            protocol="openai-compatible",
            display_name="Custom",
            supports_json_mode=False,
        )
        router = ModelRouter(config)

        resolution = router.resolve(
            ModelCallOptions(
                prompt="hello",
                provider_conf_id="conf_custom",
                json_mode=True,
            )
        )

        self.assertTrue(resolution.success)
        self.assertNotIn("json_mode", resolution.params)
        self.assertIn("json_mode", resolution.unsupported_params)

    def test_explicit_candidates_select_enabled_candidate_by_priority(self):
        disabled = make_mock_conf("conf_disabled", enabled=False, model="disabled-model")
        later = make_mock_conf("conf_later", model="later-model")
        selected = make_mock_conf("conf_selected", model="selected-model")
        config = make_config(
            provider_confs=[disabled, later, selected],
            routes={
                "summary": RouteConfig(
                    route="summary",
                    default_model_policy="explicit_candidates",
                    candidates=[
                        RouteCandidate(provider_conf_id=disabled.id, priority=0),
                        RouteCandidate(provider_conf_id=later.id, priority=5),
                        RouteCandidate(provider_conf_id=selected.id, priority=1),
                    ],
                )
            },
        )

        resolution = ModelRouter(config).resolve(
            ModelCallOptions(call_type=ModelCallType.SUMMARY, prompt="hello"),
            default_provider="mock",
        )

        self.assertTrue(resolution.success)
        self.assertEqual(resolution.provider_conf_id, "conf_selected")
        self.assertEqual(resolution.model, "selected-model")

    def test_disabled_candidates_and_providers_are_not_auto_selected(self):
        disabled_candidate = make_mock_conf("conf_disabled_candidate", model="a")
        disabled_provider = make_mock_conf("conf_disabled_provider", enabled=False, model="b")
        config = make_config(
            provider_confs=[disabled_candidate, disabled_provider],
            routes={
                "summary": RouteConfig(
                    route="summary",
                    default_model_policy="explicit_candidates",
                    candidates=[
                        RouteCandidate(provider_conf_id=disabled_candidate.id, enabled=False),
                        RouteCandidate(provider_conf_id=disabled_provider.id),
                    ],
                )
            },
        )

        resolution = ModelRouter(config).resolve(
            ModelCallOptions(call_type=ModelCallType.SUMMARY, prompt="hello"),
            default_provider="mock",
        )

        self.assertTrue(resolution.success)
        self.assertIsNone(resolution.provider_conf_id)
        self.assertEqual(resolution.provider, "mock")

    def test_explicit_provider_conf_id_and_model_take_priority(self):
        disabled = make_mock_conf("conf_disabled", enabled=False, model="disabled-model")
        config = make_config(provider_confs=[disabled])

        resolution = ModelRouter(config).resolve(
            ModelCallOptions(
                prompt="hello",
                provider_conf_id="conf_disabled",
                model="manual-model",
            ),
            default_provider="mock",
        )

        self.assertTrue(resolution.success)
        self.assertEqual(resolution.provider_conf_id, "conf_disabled")
        self.assertEqual(resolution.model, "manual-model")

    def test_context_limit_returns_structured_failure_by_default(self):
        conf = make_mock_conf("conf_ctx", max_context_chars=5)
        manager = ModelManager(model_name="mock", models_config=make_config(provider_confs=[conf]))

        result = manager.generate("0123456789", provider_conf_id=conf.id)

        self.assertFalse(result.success)
        self.assertEqual(result.code, ModelErrorCode.CONTEXT_LENGTH_EXCEEDED.value)
        self.assertEqual(result.metadata["context_limit_type"], "chars")

    def test_allow_truncation_records_metadata_and_sends_truncated_prompt(self):
        conf = make_mock_conf("conf_ctx", max_context_chars=5)
        manager = ModelManager(model_name="mock", models_config=make_config(provider_confs=[conf]))

        result = manager.generate("0123456789", provider_conf_id=conf.id, allow_truncation=True)
        route_model = manager._route_models[(conf.id, "default")]

        self.assertTrue(result.success)
        self.assertTrue(result.metadata["truncation_used"])
        self.assertEqual(result.metadata["dropped_chars"], 5)
        self.assertEqual(route_model.generate_calls[-1]["prompt"], "56789")


if __name__ == "__main__":
    unittest.main()
