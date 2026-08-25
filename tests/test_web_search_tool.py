from __future__ import annotations

import tempfile
import unittest

from src.tools import (
    ToolCallContext,
    ToolCallOptions,
    ToolCallRequest,
    ToolErrorCode,
    WebSearchData,
    default_tools_config,
)
from src.tools.tool_manager import ToolManager


class WebSearchToolTest(unittest.TestCase):
    def _manager(self, providers: dict | None = None) -> ToolManager:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        config = default_tools_config(temp_dir.name)
        config.providers = {
            "web_search": providers
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
    ) -> ToolCallRequest:
        return ToolCallRequest(
            tool_name=tool_name,
            args={"query": "agent architecture", "provider": provider},
            context=ToolCallContext(source="test"),
            options=ToolCallOptions(dry_run=dry_run, allow_network=allow_network),
        )

    def test_web_search_runs_through_tool_runtime(self):
        manager = self._manager()

        result = manager.execute(self._request())

        self.assertTrue(result.success)
        self.assertEqual(result.tool_name, "web_search")
        self.assertEqual(result.provider, "fake")
        self.assertIsInstance(result.data, WebSearchData)
        self.assertEqual(result.data.result_count, 1)
        self.assertEqual(result.data.provider, "fake")

    def test_search_tool_alias_resolves_to_web_search(self):
        manager = self._manager()

        result = manager.execute(self._request(tool_name="search_tool"))

        self.assertTrue(result.success)
        self.assertEqual(result.tool_name, "web_search")
        self.assertEqual(result.data.provider, "fake")

    def test_dry_run_returns_web_search_preview_without_network_access(self):
        manager = self._manager(
            {
                "provider": "auto",
                "auto_order": ["fake"],
                "fake": {"enabled": True, "scenario": "success"},
            }
        )

        result = manager.execute(self._request(dry_run=True, allow_network=False))

        self.assertTrue(result.success)
        self.assertEqual(result.code, ToolErrorCode.DRY_RUN_PREVIEW.value)
        self.assertIn("web_search", result.data["preview"])
        self.assertFalse(result.metadata["output_control"]["preview"]["web_search"]["allow_network"])

    def test_network_permission_is_enforced_for_real_execution(self):
        manager = self._manager()

        result = manager.execute(self._request(allow_network=False))

        self.assertFalse(result.success)
        self.assertEqual(result.code, ToolErrorCode.NETWORK_NOT_ALLOWED.value)

    def test_invalid_query_length_is_rejected(self):
        manager = self._manager()
        request = ToolCallRequest(
            tool_name="web_search",
            args={"query": "x" * 401},
            context=ToolCallContext(source="test"),
            options=ToolCallOptions(allow_network=True),
        )

        result = manager.execute(request)

        self.assertFalse(result.success)
        self.assertEqual(result.code, ToolErrorCode.INVALID_ARGS.value)


if __name__ == "__main__":
    unittest.main()
