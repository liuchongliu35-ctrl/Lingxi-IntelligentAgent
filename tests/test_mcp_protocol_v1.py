from __future__ import annotations

import json
import unittest

from src.tools import (
    MCPCallRequest,
    MCPCallResult,
    MCPConnectionInfo,
    MCPProtocolError,
    MCPRemoteTool,
    MCPServerState,
    MCPToolData,
    MCP_ERROR_CODES,
    MCP_SERVER_STATES,
    ToolErrorCode,
    error_type_for_code,
    is_retryable_code,
    normalize_error_code,
    normalize_mcp_error_code,
    normalize_mcp_server_state,
)


REQUIRED_MCP_CODES = {
    "mcp_not_configured",
    "mcp_server_disabled",
    "mcp_server_not_found",
    "mcp_transport_not_supported",
    "mcp_command_not_found",
    "mcp_process_start_failed",
    "mcp_connection_failed",
    "mcp_initialization_failed",
    "mcp_tool_list_failed",
    "mcp_tool_not_found",
    "mcp_tool_not_allowed",
    "mcp_schema_invalid",
    "mcp_invalid_args",
    "mcp_timeout",
    "mcp_transport_error",
    "mcp_remote_error",
    "mcp_result_invalid",
    "mcp_output_too_large",
    "mcp_confirmation_required",
    "mcp_blocked",
}


class MCPProtocolV1Test(unittest.TestCase):
    def test_required_mcp_error_codes_are_registered_and_classified(self):
        self.assertTrue(REQUIRED_MCP_CODES.issubset(MCP_ERROR_CODES))

        for code in REQUIRED_MCP_CODES:
            with self.subTest(code=code):
                self.assertEqual(normalize_error_code(code), code)
                self.assertEqual(normalize_mcp_error_code(code), code)
                self.assertIsInstance(error_type_for_code(code), str)

        self.assertEqual(
            error_type_for_code(ToolErrorCode.MCP_REMOTE_ERROR.value),
            "provider",
        )
        self.assertEqual(
            error_type_for_code(ToolErrorCode.MCP_TOOL_NOT_ALLOWED.value),
            "permission",
        )
        self.assertTrue(is_retryable_code(ToolErrorCode.MCP_TIMEOUT.value))

    def test_server_state_enum_is_stable(self):
        self.assertEqual(
            MCP_SERVER_STATES,
            {
                "configured",
                "disabled",
                "starting",
                "ready",
                "failed",
                "stopped",
                "tool_discovery_failed",
            },
        )
        self.assertEqual(
            normalize_mcp_server_state(MCPServerState.READY),
            "ready",
        )
        with self.assertRaises(ValueError):
            normalize_mcp_server_state("unknown")

    def test_protocol_objects_are_json_serializable(self):
        connection = MCPConnectionInfo(
            server_id="github",
            state="ready",
            transport="stdio",
            pid=123,
            protocol_version="2025-03-26",
            server_name="fake-server",
            capabilities={"tools": {"listChanged": False}},
        )
        remote_tool = MCPRemoteTool(
            server_id="github",
            name="search_repositories",
            description="Search repositories.",
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        )
        call = MCPCallRequest(
            server_id="github",
            remote_tool_name="search_repositories",
            arguments={"query": "agent"},
            timeout_seconds=10,
            trace_id="trace_1",
        )

        payload = {
            "connection": connection.to_dict(),
            "remote_tool": remote_tool.to_dict(),
            "call": call.to_dict(),
        }
        serialized = json.dumps(payload, ensure_ascii=False)

        self.assertIn("schema_hash", payload["remote_tool"])
        self.assertEqual(
            call.to_json_rpc_params(),
            {"name": "search_repositories", "arguments": {"query": "agent"}},
        )
        self.assertIn("search_repositories", serialized)

    def test_remote_error_and_local_protocol_error_are_distinct(self):
        remote = MCPCallResult.fail(
            server_id="github",
            remote_tool_name="create_issue",
            code=ToolErrorCode.MCP_REMOTE_ERROR.value,
            error="Remote tool rejected the request.",
            content=[{"type": "text", "text": "validation failed"}],
        )
        local = MCPProtocolError(
            ToolErrorCode.MCP_TRANSPORT_NOT_SUPPORTED.value,
            "streamable_http is not supported by MCP V1 execution.",
            server_id="remote",
            details={"transport": "streamable_http"},
        )

        self.assertFalse(remote.success)
        self.assertTrue(remote.is_error)
        self.assertEqual(remote.code, "mcp_remote_error")
        self.assertEqual(remote.to_tool_data().is_error, True)

        self.assertEqual(local.code, "mcp_transport_not_supported")
        self.assertFalse(local.retryable)
        self.assertEqual(local.to_dict()["details"]["transport"], "streamable_http")

    def test_mcp_tool_data_shape_and_truncation(self):
        result = MCPCallResult.ok(
            server_id="docs",
            remote_tool_name="read_big",
            content=[
                {"type": "text", "text": "x" * 500},
                {"type": "text", "text": "y" * 500},
            ],
            structured_content={"items": list(range(100))},
            resource_links=[{"uri": "file:///tmp/a"}],
        )

        data = result.to_tool_data(max_content_chars=220)

        self.assertIsInstance(data, MCPToolData)
        self.assertEqual(data.source_type, "mcp")
        self.assertTrue(data.output_truncated)
        self.assertTrue(data.metadata["output_limit_applied"])
        self.assertLessEqual(len(json.dumps(data.to_dict(), ensure_ascii=False)), 900)

    def test_invalid_remote_tool_schema_is_rejected_early(self):
        with self.assertRaises(ValueError):
            MCPRemoteTool(
                server_id="bad",
                name="broken",
                input_schema={"type": "array"},
            )
        with self.assertRaises(ValueError):
            MCPRemoteTool(
                server_id="bad",
                name="broken",
                input_schema={"type": "object", "required": "query"},
            )


if __name__ == "__main__":
    unittest.main()
