from __future__ import annotations

import json
import unittest

from src.tools.registry import ToolRegistry, ToolSpec, build_default_tool_registry


class FakeToolManager:
    def list_tools(self):
        return {
            "math_calculator": "Fake math.",
            "file_writer": "Fake writer.",
        }


class FakeFullToolManager:
    def list_tools(self):
        return {
            "document_parser": "Read files.",
            "text_processor": "Process text.",
            "math_calculator": "Calculate.",
            "translator": "Translate.",
            "time_query": "Time.",
            "web_search": "Search.",
            "code_executor": "Code.",
            "write_file": "Write.",
        }


class ToolRegistryV1Test(unittest.TestCase):
    def test_default_registry_lists_existing_tool_specs(self):
        registry = build_default_tool_registry()

        self.assertTrue(registry.has_tool("math_calculator"))
        self.assertTrue(registry.has_tool("document_parser"))
        self.assertTrue(registry.has_tool("file_writer"))
        self.assertTrue(registry.has_tool("write_file"))
        self.assertEqual(registry.resolve_name("file_writer"), "write_file")
        self.assertFalse(registry.has_tool("command_tool"))
        self.assertIn("math_calculator", registry.list_tools())

    def test_get_spec_contains_risk_and_confirmation_policy(self):
        registry = build_default_tool_registry()

        file_writer = registry.get("write_file")
        code_executor = registry.get("code_executor")

        self.assertEqual(file_writer.risk_level, "medium")
        self.assertFalse(file_writer.requires_confirmation)
        self.assertEqual(file_writer.workspace_scope, "write_workspace")
        self.assertIn("file_writer", file_writer.aliases)
        self.assertEqual(code_executor.risk_level, "high")
        self.assertTrue(code_executor.requires_confirmation)
        self.assertEqual(code_executor.workspace_scope, "code_execution")

    def test_validate_required_params(self):
        registry = build_default_tool_registry()

        missing = registry.validate_tool_args("document_parser", {})
        valid = registry.validate_tool_args("document_parser", {"path": "README.md"})
        legacy_valid = registry.validate_tool_args("document_parser", {"file_path": "README.md"})

        self.assertFalse(missing.success)
        self.assertEqual(missing.missing_params, [])
        self.assertIn("one of path, file_path is required", missing.errors)
        self.assertTrue(valid.success)
        self.assertTrue(legacy_valid.success)

    def test_disabled_tool_is_queryable_but_not_callable_or_model_visible(self):
        registry = ToolRegistry(
            [
                ToolSpec(name="disabled", description="Disabled.", enabled=False),
                ToolSpec(name="enabled", description="Enabled."),
            ]
        )

        result = registry.validate_tool_args("disabled", {"value": 1})

        self.assertFalse(result.success)
        self.assertEqual(result.code, "tool_disabled")
        self.assertEqual(result.canonical_tool_name, "disabled")
        self.assertNotIn("disabled", {item["name"] for item in registry.to_model_specs()})
        self.assertIsNotNone(registry.get("disabled"))

    def test_validate_required_any_of_for_math_calculator(self):
        registry = build_default_tool_registry()

        missing = registry.validate_tool_args("math_calculator", {})
        with_expression = registry.validate_tool_args("math_calculator", {"expression": "2+3"})
        with_data = registry.validate_tool_args("math_calculator", {"data": [1, 2, 3], "operation": "statistics"})

        self.assertFalse(missing.success)
        self.assertIn("one of expression, data is required", missing.errors)
        self.assertTrue(with_expression.success)
        self.assertTrue(with_data.success)

    def test_validate_basic_json_types(self):
        registry = build_default_tool_registry()

        invalid = registry.validate_tool_args("search_tool", {"query": "agent", "max_results": "5"})
        valid = registry.validate_tool_args("search_tool", {"query": "agent", "max_results": 5})

        self.assertFalse(invalid.success)
        self.assertIn("max_results must be integer", invalid.errors)
        self.assertTrue(valid.success)

    def test_validate_rejects_non_object_args_without_throwing(self):
        registry = build_default_tool_registry()

        result = registry.validate_tool_args("search_tool", "query=agent")

        self.assertFalse(result.success)
        self.assertEqual(result.errors, ["tool args must be object"])

    def test_unknown_tool_returns_structured_failure(self):
        registry = build_default_tool_registry()

        result = registry.validate_tool_args("missing_tool", {})

        self.assertFalse(result.success)
        self.assertEqual(result.tool_name, "missing_tool")
        self.assertEqual(result.errors, ["tool not found: missing_tool"])

    def test_registry_can_register_custom_tool(self):
        registry = ToolRegistry()
        spec = ToolSpec(
            name="custom_tool",
            description="Custom.",
            parameters_schema={"type": "object", "properties": {"name": {"type": "string"}}},
            required_params=["name"],
            risk_level="low",
        )

        registry.register(spec)

        self.assertTrue(registry.has_tool("custom_tool"))
        self.assertTrue(registry.validate_tool_args("custom_tool", {"name": "demo"}).success)
        self.assertFalse(registry.validate_tool_args("custom_tool", {}).success)

    def test_alias_resolves_to_canonical_name_for_lookup_and_validation(self):
        registry = ToolRegistry()
        registry.register(
            ToolSpec(
                name="web_search",
                description="Search.",
                aliases=["search_tool"],
                parameters_schema={"properties": {"query": {"type": "string"}}},
                required_params=["query"],
            )
        )

        self.assertEqual(registry.resolve_name("search_tool"), "web_search")
        self.assertTrue(registry.has_tool("search_tool"))
        self.assertIs(registry.get("search_tool"), registry.get("web_search"))
        result = registry.validate_tool_args("search_tool", {"query": "agent"})
        self.assertTrue(result.success)
        self.assertEqual(result.tool_name, "search_tool")
        self.assertEqual(result.canonical_tool_name, "web_search")

    def test_alias_conflicts_are_rejected(self):
        registry = ToolRegistry([ToolSpec(name="first", description="First.")])
        registry.register_alias("legacy", "first")

        with self.assertRaises(ValueError):
            registry.register_alias("legacy", "first")
        with self.assertRaises(ValueError):
            registry.register_alias("first", "first")
        with self.assertRaises(ValueError):
            registry.register(ToolSpec(name="second", description="Second.", aliases=["legacy"]))
        with self.assertRaises(ValueError):
            registry.register(ToolSpec(name="legacy", description="Legacy."))

    def test_to_model_specs_are_json_serializable_and_contain_core_fields(self):
        registry = build_default_tool_registry()

        specs = registry.to_model_specs()

        write_spec = next(spec for spec in specs if spec["name"] == "write_file")
        document_spec = next(spec for spec in specs if spec["name"] == "document_parser")
        self.assertIn("write_mode", write_spec["parameters_schema"]["properties"])
        self.assertNotIn("file_path", write_spec["parameters_schema"]["properties"])
        self.assertEqual(write_spec["required_params"], ["path", "content", "write_mode"])
        self.assertEqual(write_spec["required_any_of"], [])
        self.assertIn("path", document_spec["parameters_schema"]["properties"])
        self.assertNotIn("file_path", document_spec["parameters_schema"]["properties"])
        self.assertEqual(document_spec["required_params"], ["path"])
        self.assertTrue(all("parameters_schema" in spec for spec in specs))
        self.assertTrue(all("risk_level" in spec for spec in specs))
        json.dumps(specs, ensure_ascii=False)
        json.dumps(registry.to_dict(), ensure_ascii=False)

    def test_build_from_tool_manager_filters_to_runnable_tools_and_keeps_descriptions(self):
        registry = build_default_tool_registry(FakeToolManager())

        self.assertEqual(registry.tool_names(), ["math_calculator", "write_file"])
        self.assertEqual(
            registry.get("math_calculator").description,
            "Calculate expressions and simple statistics.",
        )
        self.assertFalse(registry.has_tool("document_parser"))

    def test_build_from_tool_manager_matches_list_tools_names(self):
        tool_manager = FakeFullToolManager()
        registry = build_default_tool_registry(tool_manager)

        self.assertEqual(set(registry.tool_names()), set(tool_manager.list_tools().keys()))

    def test_command_tool_is_optional_and_marked_implemented(self):
        without_command = build_default_tool_registry()
        with_command = build_default_tool_registry(include_command_tool=True)

        self.assertFalse(without_command.has_tool("command_tool"))
        self.assertFalse(without_command.has_tool("shell_command_tool"))
        self.assertTrue(with_command.has_tool("command_tool"))
        self.assertTrue(with_command.has_tool("shell_command_tool"))
        self.assertEqual(with_command.resolve_name("shell_tool"), "shell_command_tool")
        command_spec = with_command.get("command_tool")
        shell_spec = with_command.get("shell_command_tool")
        self.assertEqual(command_spec.risk_level, "high")
        self.assertTrue(command_spec.requires_confirmation)
        self.assertTrue(command_spec.metadata["implemented"])
        self.assertEqual(shell_spec.workspace_scope, "shell_command")
        self.assertTrue(shell_spec.supports_dry_run)
        self.assertIn("shell_tool", shell_spec.aliases)
        self.assertNotIn("shell_tool", {item["name"] for item in with_command.to_model_specs()})

    def test_invalid_spec_risk_and_scope_are_normalized(self):
        spec = ToolSpec(name="bad", description="Bad.", risk_level="unknown", workspace_scope="bad", timeout=0)

        self.assertEqual(spec.risk_level, "medium")
        self.assertEqual(spec.workspace_scope, "none")
        self.assertEqual(spec.timeout, 1)


if __name__ == "__main__":
    unittest.main()
