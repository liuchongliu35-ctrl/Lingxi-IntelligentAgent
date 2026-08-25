from __future__ import annotations

import sys
import tempfile
import types
import unittest

if "requests" not in sys.modules:
    sys.modules["requests"] = types.SimpleNamespace()

from src.tools.code_executor import CodeExecutor
from src.tools.errors import ToolErrorCode
from src.tools.math_calculator import MathCalculator
from src.tools.protocol import ToolCallContext, ToolCallOptions, ToolCallRequest
from src.tools.registry import build_default_tool_registry
from src.tools.text_processor import TextProcessor
from src.tools.time_query import TimeQuery
from src.tools.tool_manager import ToolManager
from src.tools.translator import Translator


class UtilityToolsV1Test(unittest.TestCase):
    def test_math_calculator_returns_structured_expression_and_blocks_python(self):
        ok = MathCalculator().run(expression="sqrt(9) + 2")
        blocked = MathCalculator().run(expression="__import__('os').system('echo bad')")

        self.assertTrue(ok.success)
        self.assertEqual(ok.data["operation"], "expression")
        self.assertEqual(ok.data["result"], 5.0)
        self.assertFalse(blocked.success)
        self.assertEqual(blocked.code, ToolErrorCode.INVALID_ARGS.value)

    def test_math_statistics_returns_structured_result(self):
        result = MathCalculator().run(data=[1, 2, 3], operation="statistics")

        self.assertTrue(result.success)
        self.assertEqual(result.data["operation"], "statistics")
        self.assertEqual(result.data["result"]["mean"], 2)

    def test_time_query_uses_zoneinfo_and_rejects_invalid_timezone(self):
        current = TimeQuery().run(operation="current", timezone="Asia/Shanghai")
        invalid = TimeQuery().run(operation="current", timezone="No/Such_Zone")
        date_info = TimeQuery().run(operation="date_info", date="2026-08-16", timezone="UTC")

        self.assertTrue(current.success)
        self.assertEqual(current.data["timezone"], "Asia/Shanghai")
        self.assertIn("iso", current.data)
        self.assertFalse(invalid.success)
        self.assertEqual(invalid.code, ToolErrorCode.INVALID_ARGS.value)
        self.assertTrue(date_info.success)
        self.assertEqual(date_info.data["weekday"], "Sunday")

    def test_text_processor_is_rule_based(self):
        formatted = TextProcessor().run("alpha   beta\nalpha", operation="format")
        keywords = TextProcessor().run("alpha beta alpha gamma", operation="keywords", top_n=2)
        summary = TextProcessor().run("abcdef", operation="summary", max_length=4)
        stats = TextProcessor().run("one two\nthree", operation="statistics")

        self.assertTrue(formatted.success)
        self.assertEqual(formatted.data["text"], "alpha beta alpha")
        self.assertEqual(keywords.data["keywords"][0], {"keyword": "alpha", "count": 2})
        self.assertEqual(summary.data["quality"], "rule_based_truncation")
        self.assertTrue(summary.data["truncated"])
        self.assertEqual(stats.data["statistics"]["words"], 3)

    def test_translator_is_explicit_mock_placeholder(self):
        result = Translator().run("hello", target_language="zh")

        self.assertTrue(result.success)
        self.assertIsNone(result.data["translated_text"])
        self.assertTrue(result.data["mock"])
        self.assertFalse(result.data["implemented"])
        self.assertTrue(result.metadata["mock"])

    def test_code_executor_is_disabled_shell(self):
        direct = CodeExecutor().run("print('should not run')")

        self.assertFalse(direct.success)
        self.assertEqual(direct.code, ToolErrorCode.TOOL_DISABLED.value)
        self.assertFalse(direct.data["enabled"])

    def test_tool_manager_runtime_utility_tools_return_tool_result_v1(self):
        with tempfile.TemporaryDirectory() as workspace:
            manager = ToolManager(workspace_root=workspace)
            math = manager.execute(
                ToolCallRequest(
                    tool_name="math_calculator",
                    args={"expression": "2+3"},
                    context=ToolCallContext(workspace_root=workspace, source="test"),
                    options=ToolCallOptions(),
                )
            )
            time = manager.execute(
                ToolCallRequest(
                    tool_name="time_query",
                    args={"operation": "current", "timezone": "UTC"},
                    context=ToolCallContext(workspace_root=workspace, source="test"),
                    options=ToolCallOptions(),
                )
            )
            code = manager.execute(
                ToolCallRequest(
                    tool_name="code_executor",
                    args={"code": "print('x')"},
                    context=ToolCallContext(workspace_root=workspace, source="test"),
                    options=ToolCallOptions(allow_command=True, confirmed=True),
                )
            )

            self.assertTrue(math.success)
            self.assertEqual(math.data["result"], 5.0)
            self.assertTrue(time.success)
            self.assertFalse(code.success)
            self.assertEqual(code.code, ToolErrorCode.TOOL_DISABLED.value)

    def test_translator_and_code_executor_are_not_model_visible(self):
        registry = build_default_tool_registry()
        model_names = {item["name"] for item in registry.to_model_specs()}

        self.assertNotIn("translator", model_names)
        self.assertNotIn("code_executor", model_names)
        self.assertIn("math_calculator", model_names)
        self.assertIn("time_query", model_names)
        self.assertIn("text_processor", model_names)
        self.assertTrue(registry.has_tool("translator"))
        self.assertTrue(registry.has_tool("code_executor"))


if __name__ == "__main__":
    unittest.main()
