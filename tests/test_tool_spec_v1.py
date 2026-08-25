from __future__ import annotations

import json
import unittest
from dataclasses import fields

from src.tools.registry import ToolRegistry, ToolSpec, WORKSPACE_SCOPES


class ToolSpecV1Test(unittest.TestCase):
    def test_formal_fields_are_present_in_stable_order(self):
        self.assertEqual(
            [field.name for field in fields(ToolSpec)],
            [
                "name",
                "description",
                "category",
                "namespace",
                "parameters_schema",
                "required_params",
                "required_any_of",
                "returns_schema",
                "enabled",
                "risk_level",
                "requires_confirmation",
                "workspace_scope",
                "timeout_seconds",
                "max_output_chars",
                "default_observation_mode",
                "supports_dry_run",
                "fallback_tools",
                "aliases",
                "metadata",
            ],
        )

    def test_defaults_are_conservative_and_mutable_fields_are_independent(self):
        first = ToolSpec(name="one", description="One")
        second = ToolSpec(name="two", description="Two")

        self.assertEqual(first.category, "general")
        self.assertEqual(first.namespace, "builtin")
        self.assertTrue(first.enabled)
        self.assertEqual(first.risk_level, "low")
        self.assertEqual(first.workspace_scope, "none")
        self.assertEqual(first.timeout_seconds, 10)
        self.assertIsNone(first.max_output_chars)
        self.assertEqual(first.default_observation_mode, "standard")
        self.assertFalse(first.supports_dry_run)

        first.parameters_schema["properties"] = {}
        first.required_params.append("value")
        first.metadata["source_type"] = "builtin"
        self.assertEqual(second.parameters_schema, {})
        self.assertEqual(second.required_params, [])
        self.assertEqual(second.metadata, {})

    def test_timeout_seconds_is_formal_and_timeout_is_compatible(self):
        legacy = ToolSpec(name="legacy", description="Legacy", timeout=7)
        modern = ToolSpec(name="modern", description="Modern", timeout_seconds=8)

        self.assertEqual(legacy.timeout_seconds, 7)
        self.assertEqual(legacy.timeout, 7)
        legacy.timeout = 3
        self.assertEqual(legacy.timeout_seconds, 3)
        self.assertEqual(modern.timeout, 8)

        with self.assertRaises(ValueError):
            ToolSpec(name="conflict", description="Conflict", timeout=3, timeout_seconds=4)

    def test_invalid_risk_scope_and_observation_mode_are_normalized(self):
        spec = ToolSpec(
            name="normalized",
            description="Normalized",
            risk_level="unknown",
            workspace_scope="unknown",
            default_observation_mode="verbose",
            timeout_seconds=0,
            max_output_chars=-1,
        )

        self.assertEqual(spec.risk_level, "medium")
        self.assertEqual(spec.workspace_scope, "none")
        self.assertEqual(spec.default_observation_mode, "standard")
        self.assertEqual(spec.timeout_seconds, 1)
        self.assertEqual(spec.max_output_chars, 0)
        self.assertTrue({"shell_command", "mcp"}.issubset(WORKSPACE_SCOPES))

    def test_model_spec_uses_safe_formal_fields(self):
        spec = ToolSpec(
            name="search",
            description="Search",
            category="search",
            namespace="builtin",
            parameters_schema={"type": "object", "properties": {"query": {"type": "string"}}},
            required_params=["query"],
            required_any_of=[["query"]],
            returns_schema={"type": "object"},
            risk_level="medium",
            requires_confirmation=False,
            workspace_scope="network",
            timeout_seconds=30,
            max_output_chars=4000,
            default_observation_mode="minimal",
            supports_dry_run=True,
            fallback_tools=["fallback_to_model"],
            aliases=["search_tool"],
            metadata={
                "provider": "tavily",
                "source_type": "builtin",
                "internal_handler": "secret-handler",
            },
        )

        model_spec = spec.to_model_spec()

        self.assertEqual(model_spec["timeout_seconds"], 30)
        self.assertEqual(model_spec["returns_schema"], {"type": "object"})
        self.assertTrue(model_spec["supports_dry_run"])
        self.assertNotIn("timeout", model_spec)
        self.assertNotIn("metadata", model_spec)
        self.assertNotIn("fallback_tools", model_spec)
        self.assertNotIn("aliases", model_spec)
        json.dumps(model_spec, ensure_ascii=False)
        json.dumps(spec.to_dict(), ensure_ascii=False)

    def test_disabled_specs_are_not_model_visible_but_remain_queryable(self):
        enabled = ToolSpec(name="enabled", description="Enabled")
        disabled = ToolSpec(
            name="disabled",
            description="Disabled",
            enabled=False,
            metadata={"disabled_reason": "not configured"},
        )
        registry = ToolRegistry([enabled, disabled])

        self.assertIs(registry.get("disabled"), disabled)
        self.assertEqual([item["name"] for item in registry.to_model_specs()], ["enabled"])
        self.assertFalse(registry.get("disabled").enabled)

    def test_legacy_positional_constructor_remains_usable(self):
        spec = ToolSpec(
            "legacy",
            "Legacy",
            {"type": "object"},
            ["value"],
            {"type": "string"},
            "low",
            False,
            "none",
            4,
            [],
            "utility",
            [],
            {},
        )

        self.assertEqual(spec.name, "legacy")
        self.assertEqual(spec.category, "utility")
        self.assertEqual(spec.timeout_seconds, 4)
        self.assertEqual(spec.required_params, ["value"])


if __name__ == "__main__":
    unittest.main()
