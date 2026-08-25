from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..base import ToolResult
from ..errors import ToolErrorCode
from ..protocol import ToolCallContext, ToolCallOptions
from ..registry import ToolSpec
from .protocol import MCPCallRequest, MCPCallResult, MCPProtocolError
from .stdio_client import MCPStdioClient


MCP_GATEWAY_METADATA_VERSION = "mcp.gateway.v1"


@dataclass
class MCPGatewayHandler:
    gateway: "MCPToolGateway"
    spec: ToolSpec

    def run(
        self,
        timeout_seconds: int | None = None,
        tool_call_context: ToolCallContext | None = None,
        tool_call_options: ToolCallOptions | None = None,
        **arguments: Any,
    ) -> ToolResult:
        return self.gateway.run_spec(
            self.spec,
            arguments,
            timeout_seconds=timeout_seconds,
            tool_call_context=tool_call_context,
            tool_call_options=tool_call_options,
        )


class MCPToolGateway:
    """Shared ToolRuntime handler for dynamically registered MCP ToolSpecs."""

    def __init__(
        self,
        clients: Mapping[str, MCPStdioClient] | None = None,
        *,
        max_content_chars: int | None = None,
    ) -> None:
        self.clients: dict[str, MCPStdioClient] = dict(clients or {})
        self.max_content_chars = max_content_chars

    def register_client(self, server_id: str, client: MCPStdioClient) -> None:
        if not isinstance(client, MCPStdioClient):
            raise TypeError("client must be MCPStdioClient")
        self.clients[str(server_id).strip()] = client

    def remove_client(self, server_id: str) -> MCPStdioClient | None:
        return self.clients.pop(str(server_id).strip(), None)

    def get_client(self, server_id: str) -> MCPStdioClient | None:
        return self.clients.get(str(server_id).strip())

    def handler_for(self, spec: ToolSpec) -> MCPGatewayHandler:
        return MCPGatewayHandler(self, spec)

    def run_spec(
        self,
        spec: ToolSpec,
        arguments: dict[str, Any],
        *,
        timeout_seconds: int | None = None,
        tool_call_context: ToolCallContext | None = None,
        tool_call_options: ToolCallOptions | None = None,
    ) -> ToolResult:
        try:
            server_id, remote_tool_name = _metadata_identity(spec)
            client = self._ready_client(server_id, remote_tool_name)
            call_request = MCPCallRequest(
                server_id=server_id,
                remote_tool_name=remote_tool_name,
                arguments=dict(arguments or {}),
                timeout_seconds=timeout_seconds,
                trace_id=None if tool_call_context is None else tool_call_context.trace_id,
            )
            call_result = client.call_tool(
                call_request,
                timeout_seconds=timeout_seconds,
            )
            return self._to_tool_result(
                call_result,
                spec=spec,
                client=client,
                arguments=arguments,
                max_content_chars=_max_content_chars(
                    self.max_content_chars,
                    spec,
                    tool_call_options,
                ),
            )
        except MCPProtocolError as exc:
            return _protocol_error_result(exc, spec)
        except Exception as exc:
            return ToolResult.fail(
                f"MCP gateway failed: {exc}",
                code=ToolErrorCode.MCP_TRANSPORT_ERROR.value,
                metadata={"mcp_gateway": {"version": MCP_GATEWAY_METADATA_VERSION}},
            )

    def _ready_client(
        self,
        server_id: str,
        remote_tool_name: str,
    ) -> MCPStdioClient:
        client = self.get_client(server_id)
        if client is None:
            raise MCPProtocolError(
                ToolErrorCode.MCP_SERVER_NOT_FOUND.value,
                "MCP server client is not registered.",
                server_id=server_id,
                remote_tool_name=remote_tool_name,
            )
        if not client.config.enabled:
            raise MCPProtocolError(
                ToolErrorCode.MCP_SERVER_DISABLED.value,
                "MCP server is disabled.",
                server_id=server_id,
                remote_tool_name=remote_tool_name,
            )
        if not client.initialized or client.discovery_result is None:
            raise MCPProtocolError(
                ToolErrorCode.MCP_CONNECTION_FAILED.value,
                "MCP server is not ready; initialize and tools/list must complete before tools/call.",
                server_id=server_id,
                remote_tool_name=remote_tool_name,
            )
        if not any(tool.name == remote_tool_name for tool in client.discovery_result.tools):
            raise MCPProtocolError(
                ToolErrorCode.MCP_TOOL_NOT_FOUND.value,
                "MCP remote tool was not found in the latest discovery result.",
                server_id=server_id,
                remote_tool_name=remote_tool_name,
            )
        return client

    def _to_tool_result(
        self,
        call_result: MCPCallResult,
        *,
        spec: ToolSpec,
        client: MCPStdioClient,
        arguments: dict[str, Any],
        max_content_chars: int | None,
    ) -> ToolResult:
        data = call_result.to_tool_data(max_content_chars=max_content_chars)
        process_info = client.process.connection_info()
        stderr_preview = client.process.stderr_preview()
        metadata = {
            "mcp_gateway": {
                "version": MCP_GATEWAY_METADATA_VERSION,
                "server_id": call_result.server_id,
                "remote_tool_name": call_result.remote_tool_name,
                "transport": client.config.transport,
                "command_summary": process_info.metadata.get("command_summary"),
                "argument_keys": sorted(str(key) for key in arguments),
                "schema_hash": spec.metadata.get("remote_schema_hash"),
                "stderr_preview": stderr_preview,
                "output_truncated": data.output_truncated,
                "fallback_performed": False,
            }
        }
        if call_result.success:
            return ToolResult.ok(
                data=data,
                message=_success_message(call_result),
                code=ToolErrorCode.OK.value,
                raw_output=call_result.to_dict(),
                metadata=metadata,
            )
        return ToolResult.fail(
            call_result.error or "MCP remote tool failed.",
            code=call_result.code,
            data=data,
            raw_output=call_result.to_dict(),
            metadata=metadata,
        )


def _metadata_identity(spec: ToolSpec) -> tuple[str, str]:
    metadata = dict(spec.metadata or {})
    if metadata.get("source_type") != "mcp":
        raise MCPProtocolError(
            ToolErrorCode.MCP_INVALID_ARGS.value,
            "ToolSpec is not an MCP tool.",
            details={"tool_name": spec.name},
        )
    server_id = str(metadata.get("server_id") or "").strip()
    remote_tool_name = str(metadata.get("remote_tool_name") or "").strip()
    if not server_id or not remote_tool_name:
        raise MCPProtocolError(
            ToolErrorCode.MCP_INVALID_ARGS.value,
            "MCP ToolSpec metadata missing server_id or remote_tool_name.",
            details={"tool_name": spec.name},
        )
    return server_id, remote_tool_name


def _protocol_error_result(error: MCPProtocolError, spec: ToolSpec | None = None) -> ToolResult:
    return ToolResult.fail(
        error.message,
        code=error.code,
        data={"mcp_error": error.to_dict()},
        metadata={
            "mcp_gateway": {
                "version": MCP_GATEWAY_METADATA_VERSION,
                "server_id": error.server_id,
                "remote_tool_name": error.remote_tool_name,
                "tool_name": None if spec is None else spec.name,
                "fallback_performed": False,
            }
        },
    )


def _max_content_chars(
    gateway_default: int | None,
    spec: ToolSpec,
    options: ToolCallOptions | None,
) -> int | None:
    values = [
        value
        for value in (
            gateway_default,
            spec.max_output_chars,
            None if options is None else options.max_output_chars,
        )
        if value is not None
    ]
    if not values:
        return None
    return max(min(int(value) for value in values), 0)


def _success_message(call_result: MCPCallResult) -> str:
    for item in call_result.content:
        if isinstance(item, dict) and item.get("type") == "text" and item.get("text"):
            text = str(item.get("text"))
            return text if len(text) <= 200 else text[:197] + "..."
    if call_result.structured_content is not None:
        return "MCP remote tool returned structured content."
    if call_result.resource_links:
        return "MCP remote tool returned resource links."
    return "MCP remote tool completed."


__all__ = [
    "MCP_GATEWAY_METADATA_VERSION",
    "MCPGatewayHandler",
    "MCPToolGateway",
]
