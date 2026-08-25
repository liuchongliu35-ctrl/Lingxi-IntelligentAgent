from __future__ import annotations

import os
import unittest

from src.models import (
    CandidateHealthRegistry,
    ModelCallOptions,
    ModelCallResult,
    ModelCallType,
    ModelErrorCode,
    ModelManager,
    ModelRouter,
    ModelsConfig,
    ModelsRuntimeConfig,
    ProviderConf,
    ProviderCredential,
    RouteCandidate,
    RouteConfig,
    default_provider_specs,
    default_route_configs,
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


def make_conf(
    conf_id: str,
    *,
    model: str = "model-a",
    credentials: list[str] | None = None,
) -> ProviderConf:
    return ProviderConf(
        id=conf_id,
        name=conf_id,
        provider="mock",
        protocol="mock",
        enabled=True,
        default_model=model,
        credentials=[
            ProviderCredential(slug=slug)
            for slug in (credentials or ["default"])
        ],
    )


def make_config(
    provider_confs: list[ProviderConf],
    candidates: list[RouteCandidate],
) -> ModelsConfig:
    routes = default_route_configs()
    routes["summary"] = RouteConfig(
        route="summary",
        default_model_policy="explicit_candidates",
        params={"max_retries": 0},
        candidates=candidates,
    )
    return ModelsConfig(
        workspace_root=os.getcwd(),
        config_dir=os.getcwd(),
        runtime=ModelsRuntimeConfig(
            retry_backoff_base_seconds=0.0,
            retry_backoff_max_seconds=0.0,
        ),
        provider_specs=default_provider_specs(),
        provider_confs={item.id: item for item in provider_confs},
        routes=routes,
    )


def make_manager(
    config: ModelsConfig,
    models: dict[tuple[str, str], SequenceModel],
) -> ModelManager:
    manager = ModelManager(model_name="mock", models_config=config)
    for key, model in models.items():
        manager._route_models[key] = model
    return manager


class ModelsFallbackCircuitTest(unittest.TestCase):
    def test_transient_failure_falls_back_to_next_candidate(self):
        first = make_conf("conf_first", model="model-a")
        second = make_conf("conf_second", model="model-b")
        config = make_config(
            [first, second],
            [
                RouteCandidate(provider_conf_id=first.id, model="model-a", priority=0),
                RouteCandidate(provider_conf_id=second.id, model="model-b", priority=1),
            ],
        )
        manager = make_manager(
            config,
            {
                ("conf_first", "default"): SequenceModel(
                    ModelCallResult.fail(ModelErrorCode.TIMEOUT, "first timed out")
                ),
                ("conf_second", "default"): SequenceModel("second response"),
            },
        )

        result = manager.generate("hello", call_type=ModelCallType.SUMMARY)

        self.assertTrue(result.success)
        self.assertEqual(result.content, "second response")
        self.assertTrue(result.fallback_used)
        self.assertEqual(result.fallback_reason, "timeout")
        self.assertEqual(result.metadata["fallback_attempts"], 1)
        self.assertEqual(result.provider_conf_id, "conf_second")

    def test_authentication_failure_cools_only_the_credential(self):
        conf = make_conf("conf_shared", credentials=["primary", "backup"])
        config = make_config(
            [conf],
            [
                RouteCandidate(
                    provider_conf_id=conf.id,
                    credential_slug="primary",
                    model="model-a",
                    priority=0,
                ),
                RouteCandidate(
                    provider_conf_id=conf.id,
                    credential_slug="backup",
                    model="model-a",
                    priority=1,
                ),
            ],
        )
        manager = make_manager(
            config,
            {
                ("conf_shared", "primary"): SequenceModel(
                    ModelCallResult.fail(
                        ModelErrorCode.AUTHENTICATION_FAILED,
                        "bad primary credential",
                    )
                ),
                ("conf_shared", "backup"): SequenceModel("backup response"),
            },
        )

        result = manager.generate("hello", call_type=ModelCallType.SUMMARY)

        self.assertTrue(result.success)
        self.assertEqual(result.credential_slug, "backup")
        snapshot = manager.health_registry.snapshot(
            "summary",
            "conf_shared",
            "primary",
            "model-a",
        )
        self.assertTrue(snapshot["credential"]["cooldown_active"])
        self.assertNotIn("candidate", snapshot)

    def test_model_not_found_cools_only_provider_and_model(self):
        conf = make_conf("conf_models", model="model-a")
        config = make_config(
            [conf],
            [
                RouteCandidate(
                    provider_conf_id=conf.id,
                    credential_slug="default",
                    model="model-a",
                    priority=0,
                ),
                RouteCandidate(
                    provider_conf_id=conf.id,
                    credential_slug="default",
                    model="model-b",
                    priority=1,
                ),
            ],
        )
        manager = make_manager(
            config,
            {
                ("conf_models", "default"): SequenceModel(
                    ModelCallResult.fail(ModelErrorCode.MODEL_NOT_FOUND, "missing model"),
                    "model b response",
                ),
            },
        )

        result = manager.generate("hello", call_type=ModelCallType.SUMMARY)

        self.assertTrue(result.success)
        self.assertEqual(result.model, "model-b")
        snapshot = manager.health_registry.snapshot(
            "summary",
            "conf_models",
            "default",
            "model-a",
        )
        self.assertTrue(snapshot["model"]["cooldown_active"])
        self.assertNotIn("credential", snapshot)

    def test_blocked_by_policy_does_not_fallback(self):
        first = make_conf("conf_blocked_first")
        second = make_conf("conf_blocked_second", model="model-b")
        second_model = SequenceModel("must not be called")
        config = make_config(
            [first, second],
            [
                RouteCandidate(provider_conf_id=first.id, model="model-a", priority=0),
                RouteCandidate(provider_conf_id=second.id, model="model-b", priority=1),
            ],
        )
        manager = make_manager(
            config,
            {
                ("conf_blocked_first", "default"): SequenceModel(
                    ModelCallResult.fail(
                        ModelErrorCode.BLOCKED_BY_POLICY,
                        "blocked",
                    )
                ),
                ("conf_blocked_second", "default"): second_model,
            },
        )

        result = manager.generate("hello", call_type=ModelCallType.SUMMARY)

        self.assertFalse(result.success)
        self.assertEqual(result.code, ModelErrorCode.BLOCKED_BY_POLICY.value)
        self.assertFalse(result.fallback_used)
        self.assertEqual(second_model.calls, 0)

    def test_circuit_open_candidate_is_skipped_and_success_restores_it(self):
        first = make_conf("conf_circuit_first")
        second = make_conf("conf_circuit_second", model="model-b")
        config = make_config(
            [first, second],
            [
                RouteCandidate(provider_conf_id=first.id, model="model-a", priority=0),
                RouteCandidate(provider_conf_id=second.id, model="model-b", priority=1),
            ],
        )
        registry = CandidateHealthRegistry(
            candidate_cooldown_seconds=0,
            circuit_failure_threshold=2,
            circuit_open_seconds=60,
        )
        router = ModelRouter(config, health_registry=registry)

        for _ in range(2):
            registry.record_failure(
                "summary",
                "conf_circuit_first",
                "default",
                "model-a",
                ModelErrorCode.TIMEOUT,
            )

        selected = router.resolve(
            ModelCallOptions(call_type=ModelCallType.SUMMARY, prompt="hello"),
            default_provider="mock",
        )
        self.assertEqual(selected.provider_conf_id, "conf_circuit_second")
        self.assertTrue(
            registry.snapshot(
                "summary",
                "conf_circuit_first",
                "default",
                "model-a",
            )["candidate"]["circuit_open"]
        )

        registry.record_success(
            "summary",
            "conf_circuit_first",
            "default",
            "model-a",
        )
        restored = router.resolve(
            ModelCallOptions(call_type=ModelCallType.SUMMARY, prompt="hello"),
            default_provider="mock",
        )
        self.assertEqual(restored.provider_conf_id, "conf_circuit_first")

    def test_explicit_provider_does_not_fallback_to_route_candidates(self):
        first = make_conf("conf_explicit_first")
        second = make_conf("conf_explicit_second", model="model-b")
        second_model = SequenceModel("must not be called")
        config = make_config(
            [first, second],
            [
                RouteCandidate(provider_conf_id=first.id, model="model-a", priority=0),
                RouteCandidate(provider_conf_id=second.id, model="model-b", priority=1),
            ],
        )
        manager = make_manager(
            config,
            {
                ("conf_explicit_first", "default"): SequenceModel(
                    ModelCallResult.fail(ModelErrorCode.TIMEOUT, "explicit timeout")
                ),
                ("conf_explicit_second", "default"): second_model,
            },
        )

        result = manager.generate(
            "hello",
            call_type=ModelCallType.SUMMARY,
            provider_conf_id=first.id,
        )

        self.assertFalse(result.success)
        self.assertFalse(result.fallback_used)
        self.assertEqual(second_model.calls, 0)


if __name__ == "__main__":
    unittest.main()
