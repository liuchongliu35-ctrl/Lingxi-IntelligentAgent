from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

from src.tools import (
    MCP_DEFAULT_WIRE_PROTOCOL_VERSION,
    MCPProtocolError,
    MCPServerConfig,
    MCPStdioClient,
    MCPToolDiscoveryResult,
    ToolErrorCode,
)


FAKE_SERVER = Path(__file__).with_name("fixtures") / "fake_mcp_server.py"


def runtime_config(
    *,
    env: dict[str, str] | None = None,
    allowed_tools: list[str] | None = None,
    timeout_seconds: int = 2,
):
    config = MCPServerConfig.from_mapping(
        "fake",
        {
            "enabled": True,
            "transport": "stdio",
            "command": sys.executable,
            "args": [str(FAKE_SERVER)],
            "env": env or {},
            "cwd": ".",
            "passEnv": False,
            "allowed_tools": allowed_tools or [],
            "timeout_seconds": timeout_seconds,
        },
        workspace_root=Path.cwd(),
    )
    return config.resolve_runtime(environment=os.environ)


class MCPDiscoveryTest(unittest.TestCase):
    def tearDown(self) -> None:
        client = getattr(self, "_client", None)
        if client is not None:
            try:
                client.stop()
            except Exception:
                pass

    def _client_for(self, **kwargs) -> MCPStdioClient:
        self._client = MCPStdioClient(runtime_config(**kwargs))
        return self._client

    def test_initialize_sends_initialized_and_discovers_tools(self):
        client = self._client_for()

        initialized = client.initialize()
        discovery = client.list_tools()
        info = client.connection_info

        self.assertEqual(initialized.protocol_version, MCP_DEFAULT_WIRE_PROTOCOL_VERSION)
        self.assertEqual(initialized.server_name, "fake-mcp")
        self.assertEqual(info.state, "ready")
        self.assertEqual(info.protocol_version, MCP_DEFAULT_WIRE_PROTOCOL_VERSION)
        discovered_names = {tool.name for tool in discovery.tools}
        self.assertTrue({"echo", "search"}.issubset(discovered_names))
        self.assertEqual(discovery.raw_tool_count, 9)
        self.assertEqual(discovery.skipped_tools, [])
        self.assertIn("search", discovery.schema_hashes)

    def test_initialize_rejects_unsupported_version_and_missing_tools_capability(self):
        cases = [
            ("unsupported_protocol", "MCP protocol version is not supported."),
            ("no_tools_capability", "tools capability"),
            ("bad_capabilities", "capabilities must be an object"),
        ]
        for mode, expected_message in cases:
            with self.subTest(mode=mode):
                client = self._client_for(env={"FAKE_MCP_INIT_MODE": mode})
                with self.assertRaises(MCPProtocolError) as caught:
                    client.initialize()
                client.stop()

                self.assertEqual(
                    caught.exception.code,
                    ToolErrorCode.MCP_INITIALIZATION_FAILED.value,
                )
                self.assertIn(expected_message, caught.exception.message)

    def test_tools_list_requires_initialized_notification_order(self):
        client = self._client_for()

        raw_init = client.request(
            "initialize",
            {
                "protocolVersion": MCP_DEFAULT_WIRE_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        )
        with self.assertRaises(MCPProtocolError) as caught:
            client.request("tools/list", {})

        self.assertEqual(raw_init["serverInfo"]["name"], "fake-mcp")
        self.assertEqual(caught.exception.code, ToolErrorCode.MCP_REMOTE_ERROR.value)
        self.assertIn("initialized notification required", caught.exception.message)

    def test_tools_list_failure_maps_to_tool_list_error(self):
        client = self._client_for(env={"FAKE_MCP_TOOLS_MODE": "remote_error"})

        with self.assertRaises(MCPProtocolError) as caught:
            client.list_tools()

        self.assertEqual(caught.exception.code, ToolErrorCode.MCP_TOOL_LIST_FAILED.value)
        self.assertEqual(client.connection_info.state, "tool_discovery_failed")
        self.assertEqual(
            client.connection_info.last_error_code,
            ToolErrorCode.MCP_TOOL_LIST_FAILED.value,
        )

    def test_invalid_tool_schemas_are_skipped_without_losing_valid_tools(self):
        client = self._client_for(env={"FAKE_MCP_TOOLS_MODE": "invalid_schema"})

        discovery = client.list_tools()

        self.assertIsInstance(discovery, MCPToolDiscoveryResult)
        discovered_names = {tool.name for tool in discovery.tools}
        self.assertTrue({"echo", "search"}.issubset(discovered_names))
        self.assertEqual(discovery.raw_tool_count, 12)
        self.assertEqual(len(discovery.skipped_tools), 3)
        self.assertTrue(
            all(
                skipped["code"] == ToolErrorCode.MCP_SCHEMA_INVALID.value
                for skipped in discovery.skipped_tools
            )
        )
        self.assertEqual(client.connection_info.metadata["tool_count"], 9)
        self.assertEqual(len(client.connection_info.metadata["skipped_tools"]), 3)

    def test_allowed_tools_filters_discovered_tools(self):
        client = self._client_for(allowed_tools=["search"])

        discovery = client.list_tools()

        self.assertEqual([tool.name for tool in discovery.tools], ["search"])
        self.assertEqual(discovery.allowed_tools, ["search"])
        self.assertEqual(len(discovery.skipped_tools), 8)
        self.assertEqual(
            discovery.skipped_tools[0]["code"],
            ToolErrorCode.MCP_TOOL_NOT_ALLOWED.value,
        )
        self.assertEqual(discovery.skipped_tools[0]["name"], "echo")

    def test_refresh_records_schema_changes(self):
        client = self._client_for(env={"FAKE_MCP_REFRESH_CHANGES": "1"})

        first = client.list_tools()
        refreshed = client.refresh_tools()

        self.assertTrue(refreshed.refreshed)
        self.assertNotEqual(first.schema_hashes["search"], refreshed.schema_hashes["search"])
        self.assertEqual(refreshed.metadata["changed_tools"], ["search"])
        self.assertEqual(refreshed.metadata["added_tools"], [])
        self.assertEqual(refreshed.metadata["removed_tools"], [])


if __name__ == "__main__":
    unittest.main()
