from __future__ import annotations

import inspect
import sys
import types
import unittest
from dataclasses import fields

from src.agent.react_executor import COMMAND_TOOL_NAMES, ReActExecutor
from src.tools.base import ToolResult
from src.tools.registry import ToolSpec, build_default_tool_registry

try:
    import requests  # noqa: F401
except ModuleNotFoundError:
    sys.modules["requests"] = types.SimpleNamespace()

from src.tools.tool_manager import ToolManager


class LegacyRawResultTool:
    def run(self, **kwargs):
        return {"received": kwargs}


class NoRunTool:
    pass


class ToolsCurrentBaselineTest(unittest.TestCase):
    def test_tool_result_current_shape_and_factories(self):
        field_names = [field.name for field in fields(ToolResult)]
        self.assertEqual(field_names[:5], ["success", "data", "message", "error", "code"])
        self.assertIn("call_id", field_names)
        self.assertIn("metadata", field_names)

        result = ToolResult(success=True, data={"value": 1}, message="ok", error=None, code="done")

        result_dict = result.to_dict()
        self.assertEqual(result_dict["success"], True)
        self.assertEqual(result_dict["data"], {"value": 1})
        self.assertEqual(result_dict["message"], "ok")
        self.assertIsNone(result_dict["error"])
        self.assertEqual(result_dict["code"], "done")
        self.assertEqual(result.to_text(), "ok")

        ok = ToolResult.ok(data=123)
        failed = ToolResult.fail("boom", code="tool_failed", data={"detail": "x"})

        self.assertTrue(ok.success)
        self.assertEqual(ok.message, "123")
        self.assertFalse(failed.success)
        self.assertEqual(failed.error, "boom")
        self.assertEqual(failed.message, "boom")
        self.assertEqual(failed.code, "tool_failed")

    def test_tool_manager_run_tool_signature_and_default_tools_are_current_baseline(self):
        signature = inspect.signature(ToolManager.run_tool)

        self.assertEqual(list(signature.parameters.keys()), ["self", "tool_name", "kwargs"])
        self.assertEqual(signature.parameters["tool_name"].kind, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        self.assertEqual(signature.parameters["kwargs"].kind, inspect.Parameter.VAR_KEYWORD)

        manager = ToolManager()
        expected_names = [
            "document_parser",
            "text_processor",
            "math_calculator",
            "translator",
            "time_query",
            "web_search",
            "list_files",
            "file_info",
            "find_files",
            "read_file",
            "read_file_chunk",
            "read_file_head",
            "read_file_tail",
            "code_executor",
            "write_file",
            "patch_file",
            "copy_file",
            "move_file",
            "rename_file",
            "delete_file",
            "command_tool",
            "shell_command_tool",
        ]

        self.assertEqual(list(manager.tools.keys()), expected_names)
        self.assertEqual(list(manager.list_tools().keys()), expected_names)

    def test_tool_manager_run_tool_current_compatibility_behavior(self):
        manager = ToolManager()
        manager.tools = {
            "legacy_raw": LegacyRawResultTool(),
            "no_run": NoRunTool(),
        }

        wrapped = manager.run_tool("legacy_raw", query="agent")
        missing = manager.run_tool("missing")
        no_run = manager.run_tool("no_run")

        self.assertTrue(wrapped.success)
        self.assertEqual(wrapped.data, {"received": {"query": "agent"}})
        self.assertEqual(wrapped.message, "{'received': {'query': 'agent'}}")
        self.assertFalse(missing.success)
        self.assertEqual(missing.error, "Tool not found: missing")
        self.assertFalse(no_run.success)
        self.assertEqual(no_run.error, "Tool has no run method: no_run")

    def test_tool_spec_and_registry_current_baseline(self):
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

        spec = ToolSpec(name="demo", description="Demo.", timeout=7)
        model_spec = spec.to_model_spec()

        self.assertEqual(spec.timeout_seconds, 7)
        self.assertEqual(spec.timeout, 7)
        self.assertIn("timeout_seconds", model_spec)
        self.assertNotIn("timeout", model_spec)

        default_registry = build_default_tool_registry()
        command_registry = build_default_tool_registry(include_command_tool=True)

        self.assertTrue(default_registry.has_tool("search_tool"))
        self.assertTrue(default_registry.has_tool("web_search"))
        self.assertFalse(default_registry.has_tool("command_tool"))
        self.assertTrue(command_registry.has_tool("command_tool"))

    def test_react_executor_tool_integration_current_baseline(self):
        self.assertEqual(COMMAND_TOOL_NAMES, {"command_tool", "shell_command_tool", "shell_tool"})

        executor = ReActExecutor(model_manager=None, tool_manager=None)

        raw_result = executor._coerce_tool_result("raw text")
        existing_result = ToolResult.ok(data="kept", message="kept")

        self.assertTrue(raw_result.success)
        self.assertEqual(raw_result.data, "raw text")
        self.assertEqual(raw_result.message, "raw text")
        self.assertIs(executor._coerce_tool_result(existing_result), existing_result)


if __name__ == "__main__":
    unittest.main()
