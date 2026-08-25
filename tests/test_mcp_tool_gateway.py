from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

from src.tools import (
    MCPRemoteTool,
    MCPResolvedServerConfig,
    MCPServerConfig,
    MCPStdioClient,
    MCPToolData,
    MCPToolDiscoveryResult,
    MCPToolGateway,
    ToolErrorCode,
    adapt_mcp_discovery_to_specs,
    register_mcp_tool_specs,
)
from src.tools.protocol import ToolCallContext, ToolCallOptions, ToolCallRequest, ToolCallSource
from src.tools.registry import ToolRegistry
from src.tools.tool_manager import ToolManager


FAKE_SERVER = Path(__file__).with_name("fixtures") / "fake_mcp_server.py"


def runtime_config(
    *,
    enabled: bool = True,
    timeout_seconds: int = 2,
) -> MCPResolvedServerConfig:
    config = MCPServerConfig.from_mapping(
        "fake",
        {
            "enabled": enabled,
            "transport": "stdio",
            "command": sys.executable,
            "args": [str(FAKE_SERVER)],
            "cwd": ".",
            "passEnv": False,
            "timeout_seconds": timeout_seconds,
        },
        workspace_root=Path.cwd(),
    )
    return config.resolve_runtime(environment=os.environ)


def request(tool_name: str, args: dict | None = None, **options) -> ToolCallRequest:
    return ToolCallRequest(
        tool_name=tool_name,
        args=dict(args or {}),
        context=ToolCallContext(
            source=ToolCallSource.TEST.value,
            trace_id="trace-gateway",
            workspace_root=Path.cwd(),
        ),
        options=ToolCallOptions(allow_mcp=True, **options),
    )


def remote_tool(name: str) -> MCPRemoteTool:
    return MCPRemoteTool(
        server_id="fake",
        name=name,
        description=f"{name} tool.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": True},
    )


class MCPToolGatewayTest(unittest.TestCase):
    def tearDown(self) -> None:
        client = getattr(self, "_client", None)
        if client is not None:
            try:
                client.stop()
            except Exception:
                pass

    def _ready_manager(self, *, max_content_chars: int | None = None) -> ToolManager:
        self._client = MCPStdioClient(runtime_config())
        discovery = self._client.list_tools()
        registry = ToolRegistry()
        adapter_result = adapt_mcp_discovery_to_specs(discovery, self._client.config)
        register_mcp_tool_specs(registry, adapter_result)
        gateway = MCPToolGateway({"fake": self._client}, max_content_chars=max_content_chars)
        return ToolManager(tools={}, registry=registry, mcp_gateway=gateway)

    def test_tool_manager_executes_mcp_tool_call_successfully(self):
        manager = self._ready_manager()

        result = manager.execute(request("mcp.fake.search", {"query": "agent"}))

        self.assertTrue(result.success)
        self.assertEqual(result.code, ToolErrorCode.OK.value)
        self.assertEqual(result.tool_name, "mcp.fake.search")
        self.assertEqual(result.tool_namespace, "mcp.fake")
        self.assertIsInstance(result.data, MCPToolData)
        self.assertEqual(result.data.server_id, "fake")
        self.assertEqual(result.data.remote_tool_name, "search")
        self.assertEqual(result.data.structured_content["arguments"], {"query": "agent"})
        self.assertNotIn("trace-gateway", str(result.data.structured_content["arguments"]))
        self.assertEqual(result.metadata["mcp_gateway"]["server_id"], "fake")
        self.assertIn("output_control", result.metadata)

    def test_structured_content_and_resource_links_are_preserved_without_auto_reading(self):
        manager = self._ready_manager()

        structured = manager.execute(request("mcp.fake.structured", {"value": "ok"}))
        resource = manager.execute(request("mcp.fake.resource"))

        self.assertTrue(structured.success)
        self.assertEqual(structured.data.structured_content, {"items": [{"value": "ok"}]})
        self.assertTrue(resource.success)
        self.assertEqual(resource.data.resource_links[0]["uri"], "file:///tmp/fake.txt")
        self.assertIn("resource ok", resource.message)

    def test_remote_is_error_becomes_failed_tool_result_with_mcp_data(self):
        manager = self._ready_manager()

        result = manager.execute(request("mcp.fake.remote_error_tool"))

        self.assertFalse(result.success)
        self.assertEqual(result.code, ToolErrorCode.MCP_REMOTE_ERROR.value)
        self.assertIsInstance(result.data, MCPToolData)
        self.assertTrue(result.data.is_error)
        self.assertIn("remote tool failed", result.error)

    def test_invalid_result_and_timeout_are_structured_failures(self):
        manager = self._ready_manager()

        invalid = manager.execute(request("mcp.fake.invalid_result"))
        timed_out = manager.execute(
            request("mcp.fake.sleep", {"seconds": 2}, timeout_seconds=1)
        )

        self.assertFalse(invalid.success)
        self.assertEqual(invalid.code, ToolErrorCode.MCP_RESULT_INVALID.value)
        self.assertFalse(timed_out.success)
        self.assertEqual(timed_out.code, ToolErrorCode.MCP_TIMEOUT.value)

    def test_output_limit_truncates_mcp_tool_data(self):
        manager = self._ready_manager(max_content_chars=220)

        result = manager.execute(request("mcp.fake.big_text", max_output_chars=300))

        self.assertTrue(result.success)
        self.assertTrue(result.metadata["mcp_gateway"]["output_truncated"])
        self.assertTrue(result.data["metadata"]["output_limit_applied"])

    def test_server_not_registered_disabled_not_ready_and_tool_missing(self):
        registry = ToolRegistry()
        config = runtime_config()
        adapter_result = adapt_mcp_discovery_to_specs(
            MCPToolDiscoveryResult(server_id="fake", tools=[remote_tool("search")]),
            config,
        )
        register_mcp_tool_specs(registry, adapter_result)

        missing_server = ToolManager(
            tools={},
            registry=registry,
            mcp_gateway=MCPToolGateway(),
        ).execute(request("mcp.fake.search"))

        disabled_client = MCPStdioClient(runtime_config(enabled=False))
        disabled = ToolManager(
            tools={},
            registry=registry,
            mcp_gateway=MCPToolGateway({"fake": disabled_client}),
        ).execute(request("mcp.fake.search"))

        not_ready_client = MCPStdioClient(runtime_config())
        self.addCleanup(not_ready_client.stop)
        not_ready = ToolManager(
            tools={},
            registry=registry,
            mcp_gateway=MCPToolGateway({"fake": not_ready_client}),
        ).execute(request("mcp.fake.search"))

        ready_manager = self._ready_manager()
        missing_tool = ready_manager.execute(request("mcp.fake.search", {"unknown": "x"}))

        self.assertEqual(missing_server.code, ToolErrorCode.MCP_SERVER_NOT_FOUND.value)
        self.assertEqual(disabled.code, ToolErrorCode.MCP_SERVER_DISABLED.value)
        self.assertEqual(not_ready.code, ToolErrorCode.MCP_CONNECTION_FAILED.value)
        self.assertEqual(missing_tool.code, ToolErrorCode.MISSING_REQUIRED_PARAM.value)

    def test_allow_mcp_false_is_rejected_before_gateway_call(self):
        manager = self._ready_manager()

        result = manager.execute(
            ToolCallRequest(
                tool_name="mcp.fake.search",
                args={"query": "agent"},
                context=ToolCallContext(source=ToolCallSource.TEST.value),
                options=ToolCallOptions(allow_mcp=False),
            )
        )

        self.assertFalse(result.success)
        self.assertEqual(result.code, ToolErrorCode.PERMISSION_DENIED.value)


if __name__ == "__main__":
    unittest.main()
