from __future__ import annotations

import os
from dataclasses import dataclass, field as dataclass_field
from typing import Any, Mapping

from ..registry import ToolRegistry
from .adapter import (
    MCPToolAdapterResult,
    adapt_mcp_discovery_to_specs,
    mcp_dynamic_source,
    register_mcp_tool_specs,
    remove_mcp_tool_specs,
)
from .config import MCPServerConfig, MCPServersConfig
from .gateway import MCPToolGateway
from .protocol import (
    MCPConnectionInfo,
    MCPProtocolError,
    MCPResolvedServerConfig,
    MCPServerState,
    MCPToolDiscoveryResult,
    MCP_TRANSPORT_STDIO,
)
from .stdio_client import MCPStdioClient
from ..errors import ToolErrorCode


@dataclass
class MCPManagedServer:
    server_id: str
    config: MCPResolvedServerConfig
    client: MCPStdioClient
    discovery: MCPToolDiscoveryResult | None = None
    adapter_result: MCPToolAdapterResult | None = None
    registered_tools: list[str] = dataclass_field(default_factory=list)

    @property
    def connection_info(self) -> MCPConnectionInfo:
        return self.client.connection_info


class MCPManager:
    """Explicit MCP lifecycle coordinator for ToolRegistry and MCPToolGateway."""

    def __init__(
        self,
        servers_config: MCPServersConfig | Mapping[str, Any] | None = None,
        *,
        registry: ToolRegistry,
        gateway: MCPToolGateway,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self.servers_config = (
            servers_config
            if isinstance(servers_config, MCPServersConfig)
            else MCPServersConfig.from_mapping(servers_config or {})
        )
        self.registry = registry
        self.gateway = gateway
        self.environment = environment or os.environ
        self.managed: dict[str, MCPManagedServer] = {}

    def start_server(self, server_id: str) -> MCPManagedServer:
        configured = self._configured_server(server_id)
        runtime_config = configured.resolve_runtime(environment=self.environment)
        if runtime_config.transport != MCP_TRANSPORT_STDIO:
            raise MCPProtocolError(
                ToolErrorCode.MCP_TRANSPORT_NOT_SUPPORTED.value,
                "Only local stdio MCP transport is supported in V1.",
                server_id=runtime_config.server_id,
                details={"transport": runtime_config.transport},
            )
        client = MCPStdioClient(runtime_config)
        managed = MCPManagedServer(
            server_id=runtime_config.server_id,
            config=runtime_config,
            client=client,
        )
        try:
            discovery = client.list_tools()
            adapter_result = adapt_mcp_discovery_to_specs(discovery, runtime_config)
            registered_tools = register_mcp_tool_specs(self.registry, adapter_result)
            self.gateway.register_client(runtime_config.server_id, client)
        except Exception:
            try:
                client.stop()
            finally:
                self.gateway.remove_client(runtime_config.server_id)
                remove_mcp_tool_specs(self.registry, runtime_config.server_id)
            raise

        managed.discovery = discovery
        managed.adapter_result = adapter_result
        managed.registered_tools = registered_tools
        self.managed[runtime_config.server_id] = managed
        return managed

    def refresh_server(self, server_id: str) -> MCPManagedServer:
        managed = self._managed_server(server_id)
        discovery = managed.client.refresh_tools()
        adapter_result = adapt_mcp_discovery_to_specs(discovery, managed.config)
        registered_tools = register_mcp_tool_specs(self.registry, adapter_result)
        managed.discovery = discovery
        managed.adapter_result = adapter_result
        managed.registered_tools = registered_tools
        return managed

    def stop_server(self, server_id: str, *, remove_specs: bool = True) -> list[str]:
        normalized_id = str(server_id or "").strip()
        managed = self.managed.pop(normalized_id, None)
        removed: list[str] = []
        if remove_specs:
            removed = remove_mcp_tool_specs(self.registry, normalized_id)
        self.gateway.remove_client(normalized_id)
        if managed is not None:
            managed.client.stop()
        return removed

    def stop_all(self, *, remove_specs: bool = True) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for server_id in sorted(list(self.managed)):
            result[server_id] = self.stop_server(server_id, remove_specs=remove_specs)
        return result

    def connection_info(self, server_id: str) -> MCPConnectionInfo:
        managed = self.managed.get(str(server_id or "").strip())
        if managed is not None:
            return managed.connection_info
        configured = self.servers_config.get(str(server_id or "").strip())
        if configured is None:
            raise MCPProtocolError(
                ToolErrorCode.MCP_SERVER_NOT_FOUND.value,
                "MCP server is not configured.",
                server_id=server_id,
            )
        return MCPConnectionInfo(
            server_id=configured.server_id,
            state=(
                MCPServerState.DISABLED.value
                if not configured.enabled
                else MCPServerState.CONFIGURED.value
            ),
            transport=configured.transport,
        )

    def registered_tools(self, server_id: str) -> list[str]:
        return self.registry.list_dynamic_sources().get(
            mcp_dynamic_source(server_id),
            [],
        )

    def _configured_server(self, server_id: str) -> MCPServerConfig:
        configured = self.servers_config.get(str(server_id or "").strip())
        if configured is None:
            raise MCPProtocolError(
                ToolErrorCode.MCP_SERVER_NOT_FOUND.value,
                "MCP server is not configured.",
                server_id=server_id,
            )
        return configured

    def _managed_server(self, server_id: str) -> MCPManagedServer:
        managed = self.managed.get(str(server_id or "").strip())
        if managed is None:
            raise MCPProtocolError(
                ToolErrorCode.MCP_SERVER_NOT_FOUND.value,
                "MCP server is not started.",
                server_id=server_id,
            )
        return managed


__all__ = [
    "MCPManagedServer",
    "MCPManager",
]
