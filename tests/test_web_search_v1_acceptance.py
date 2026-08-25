from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.agent.react_executor_observation import ObservationStore
from src.agent.react_executor_protocol import ObservationPacket
from src.models import ModelCallResult, ModelUsage, StructuredModelResult
from src.tools import (
    ModelBuiltinSearchProvider,
    ToolCallContext,
    ToolCallOptions,
    ToolCallRequest,
    ToolErrorCode,
    WebSearchContext,
    WebSearchData,
    WebSearchRequest,
    WebSearchRouter,
    WebSearchResult,
    build_web_search_observation_views,
    default_tools_config,
    normalize_web_search_evidence,
)
from src.tools.tool_manager import ToolManager


class _FakeModelManager:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[dict] = []

    def generate_json(self, prompt: str, **kwargs):
        self.calls.append({"prompt": prompt, "kwargs": kwargs})
        model_result = ModelCallResult.ok(
            json.dumps(self.payload, ensure_ascii=False),
            provider="fake-model",
            route="web_search",
            call_type="web_search",
            provider_request_id="fake-provider-request",
            source_trace_id="fake-trace",
            usage=ModelUsage(
                prompt_tokens=10,
                completion_tokens=20,
                total_tokens=30,
                source="fake",
            ),
        )
        return StructuredModelResult(
            success=True,
            data=self.payload,
            content=model_result.content,
            parse_mode="strict",
            schema_name="web_search",
            schema_valid=True,
            model_result=model_result,
        )


class WebSearchV1AcceptanceTest(unittest.TestCase):
    def _manager(self, provider_config: dict | None = None) -> ToolManager:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        config = default_tools_config(temp_dir.name)
        config.providers = {
            "web_search": provider_config
            or {
                "provider": "fake",
                "fake": {"enabled": True, "scenario": "success"},
            }
        }
        return ToolManager(workspace_root=temp_dir.name, tools_config=config)

    def _request(
        self,
        *,
        tool_name: str = "web_search",
        provider: str = "fake",
        dry_run: bool = False,
        allow_network: bool = True,
        query: str = "agent architecture",
        **args,
    ) -> ToolCallRequest:
        request_args = {"query": query, "provider": provider}
        request_args.update(args)
        return ToolCallRequest(
            tool_name=tool_name,
            args=request_args,
            context=ToolCallContext(source="test"),
            options=ToolCallOptions(
                dry_run=dry_run,
                allow_network=allow_network,
                observation_mode="standard",
            ),
        )

    def test_registry_spec_schema_and_legacy_alias_are_formal(self):
        manager = self._manager()
        registry = manager.get_registry()
        spec = registry.get("web_search")

        self.assertIsNotNone(spec)
        assert spec is not None
        self.assertEqual(spec.name, "web_search")
        self.assertEqual(spec.category, "search")
        self.assertEqual(spec.namespace, "builtin")
        self.assertIn("query", spec.parameters_schema["properties"])
        self.assertIn("search_tool", spec.aliases)
        self.assertEqual(registry.resolve_name("search_tool"), "web_search")

    def test_network_policy_blocks_real_route_without_fallback(self):
        manager = self._manager(
            {
                "provider": "auto",
                "auto_order": ["search_api", "fake"],
                "search_api": {"enabled": True},
                "fake": {"enabled": True, "scenario": "success"},
            }
        )

        result = manager.execute(
            self._request(provider="auto", allow_network=False)
        )

        self.assertFalse(result.success)
        self.assertEqual(result.code, ToolErrorCode.NETWORK_NOT_ALLOWED.value)
        self.assertIn("policy", result.metadata)
        self.assertNotIn("attempted_providers", result.metadata)

    def test_dry_run_returns_route_and_does_not_execute_provider(self):
        manager = self._manager(
            {
                "provider": "auto",
                "auto_order": ["fake"],
                "fake": {"enabled": True, "scenario": "success"},
            }
        )

        result = manager.execute(
            self._request(provider="auto", dry_run=True, allow_network=False)
        )

        self.assertTrue(result.success)
        self.assertEqual(result.code, ToolErrorCode.DRY_RUN_PREVIEW.value)
        self.assertEqual(result.data["preview"]["web_search"]["provider_route"], "auto")
        self.assertFalse(result.data["preview"]["web_search"]["allow_network"])

    def test_fake_provider_covers_success_empty_timeout_invalid_and_summary(self):
        scenarios = {
            "success": (True, None),
            "empty": (True, "empty"),
            "timeout": (False, ToolErrorCode.PROVIDER_TIMEOUT.value),
            "schema_invalid": (
                False,
                ToolErrorCode.PROVIDER_RESPONSE_INVALID.value,
            ),
            "no_url_summary": (True, "no_url_summary"),
            "not_configured": (
                False,
                ToolErrorCode.SEARCH_NOT_CONFIGURED.value,
            ),
        }
        for scenario, (success, marker) in scenarios.items():
            with self.subTest(scenario=scenario):
                router = WebSearchRouter(
                    {
                        "provider": "fake",
                        "fake": {"enabled": True, "scenario": scenario},
                    }
                )
                result = router.search(
                    WebSearchRequest(query=scenario, provider="fake"),
                    WebSearchContext(allow_network=False),
                )

                self.assertEqual(result.success, success)
                if not success:
                    self.assertEqual(result.code, marker)
                elif marker == "empty":
                    self.assertEqual(result.data.source_quality, "empty")
                elif marker == "no_url_summary":
                    self.assertEqual(result.data.evidence_level, "no_url_summary")
                    self.assertEqual(result.data.source_quality, "summary_only")

    def test_auto_route_records_attempts_and_explicit_provider_does_not_fallback(self):
        router = WebSearchRouter(
            {
                "provider": "auto",
                "auto_order": ["search_api", "fake"],
                "search_api": {"enabled": False},
                "fake": {"enabled": True, "scenario": "success"},
            }
        )
        auto_result = router.search(
            WebSearchRequest(query="fallback"),
            WebSearchContext(allow_network=True),
        )

        self.assertTrue(auto_result.success)
        self.assertEqual(auto_result.data.provider, "fake")
        self.assertEqual(
            auto_result.metadata["attempted_providers"],
            ["search_api", "fake"],
        )
        self.assertTrue(auto_result.metadata["fallback_used"])
        self.assertEqual(auto_result.metadata["final_provider"], "fake")

        explicit_result = router.search(
            WebSearchRequest(query="explicit", provider="search_api"),
            WebSearchContext(allow_network=True),
        )
        self.assertFalse(explicit_result.success)
        self.assertEqual(
            explicit_result.metadata["attempted_providers"],
            ["search_api"],
        )
        self.assertFalse(explicit_result.metadata["fallback_used"])

    def test_model_builtin_uses_models_call_type_and_weak_evidence(self):
        payload = {
            "query": "model search",
            "summary": "A summary without auditable URLs.",
            "results": [],
            "evidence_level": "no_url_summary",
            "source_quality": "summary_only",
        }
        model_manager = _FakeModelManager(payload)
        provider = ModelBuiltinSearchProvider(
            {
                "enabled": True,
                "provider_conf_id": "conf_fake",
                "enable_web_search": True,
            },
            model_manager=model_manager,
        )

        result = provider.search(
            WebSearchRequest(query="model search", provider="model_builtin"),
            WebSearchContext(allow_network=True),
        )

        self.assertTrue(result.success)
        self.assertIsInstance(result.data, WebSearchData)
        self.assertEqual(result.data.evidence_level, "no_url_summary")
        self.assertEqual(result.data.source_quality, "summary_only")
        self.assertEqual(model_manager.calls[0]["kwargs"]["call_type"], "web_search")
        self.assertTrue(
            model_manager.calls[0]["kwargs"]["metadata"]["enable_web_search"]
        )
        self.assertNotIn("prompt", model_manager.calls[0]["kwargs"]["metadata"])

    def test_observation_views_bound_output_and_remain_consumable_by_store(self):
        data = WebSearchData(
            query="bounded",
            provider="fake",
            provider_type="fake",
            results=[
                WebSearchResult(
                    title="Result",
                    url="https://example.test/result",
                    snippet="A bounded snippet.",
                    raw_content="must not enter observation",
                )
            ],
        )
        data = normalize_web_search_evidence(
            data,
            max_output_chars=2000,
            max_observation_chars=1000,
        )
        views = build_web_search_observation_views(
            data,
            max_chars=1000,
            result_limit=1,
            code="ok",
        )
        self.assertNotIn("raw_content", json.dumps(views, ensure_ascii=False))
        self.assertEqual(views["standard_data"]["result_count"], 1)

        observation = ObservationPacket(
            execution_id="exec_29",
            plan_id="plan_29",
            task_id="task_29",
            step_id="step_29",
            action_type="call_tool",
            action_target="tool",
            tool_name="web_search",
            success=True,
            message="search completed",
            data=data.to_dict(),
            model_consumable_observation=views["standard_data"],
        )
        store = ObservationStore()
        store.add(observation, output_key="search_results")
        resolved = store.resolve_input_refs(["search_results"])

        self.assertEqual(
            resolved["search_results"]["results"][0]["url"],
            "https://example.test/result",
        )
        self.assertNotIn("raw_content", json.dumps(resolved, ensure_ascii=False))

    def test_tools_log_is_jsonl_and_does_not_write_search_query_or_secret(self):
        manager = self._manager()
        result = manager.execute(
            self._request(query="find token=super-secret architecture")
        )

        self.assertTrue(result.success)
        log_path = Path(manager.tools_config.runtime.logs_path)
        lines = log_path.read_text(encoding="utf-8").splitlines()
        self.assertTrue(lines)
        record = json.loads(lines[-1])
        self.assertEqual(record["tool_name"], "web_search")
        self.assertEqual(record["tool_category"], "search")
        self.assertIn("search", record["input_summary"])
        self.assertNotIn("super-secret", log_path.read_text(encoding="utf-8"))
        self.assertNotIn("find token=", log_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
