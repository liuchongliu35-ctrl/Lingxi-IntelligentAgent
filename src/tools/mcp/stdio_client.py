from __future__ import annotations

import json
import time
from itertools import count
from threading import Lock
from typing import Any

from ..base import _json_safe
from ..errors import ToolErrorCode
from .process import MCPStdioProcess
from .protocol import (
    DEFAULT_MCP_TOOL_DESCRIPTION_CHARS,
    MCP_CLIENT_NAME,
    MCP_CLIENT_VERSION,
    MCP_DEFAULT_WIRE_PROTOCOL_VERSION,
    MCP_SUPPORTED_WIRE_PROTOCOL_VERSIONS,
    MCPConnectionInfo,
    MCPCallRequest,
    MCPCallResult,
    MCPInitializeResult,
    MCPProtocolError,
    MCPRemoteTool,
    MCPResolvedServerConfig,
    MCPServerState,
    MCPToolDiscoveryResult,
)


JSONRPC_VERSION = "2.0"


class MCPStdioClient:
    """Synchronous serial JSON-RPC client for local MCP STDIO servers."""

    def __init__(
        self,
        config: MCPResolvedServerConfig,
        *,
        process: MCPStdioProcess | None = None,
    ) -> None:
        self.config = config
        self.process = process or MCPStdioProcess(config)
        self._ids = count(1)
        self._request_lock = Lock()
        self._initialize_result: MCPInitializeResult | None = None
        self._discovery_result: MCPToolDiscoveryResult | None = None
        self._last_info = MCPConnectionInfo(
            server_id=config.server_id,
            state=(
                MCPServerState.DISABLED.value
                if not config.enabled
                else MCPServerState.CONFIGURED.value
            ),
            transport=config.transport,
        )

    @property
    def connection_info(self) -> MCPConnectionInfo:
        return self._last_info

    def start(self) -> MCPConnectionInfo:
        self._last_info = self.process.start()
        return self._last_info

    def stop(self) -> MCPConnectionInfo:
        self._last_info = self.process.stop()
        self._initialize_result = None
        self._discovery_result = None
        return self._last_info

    @property
    def initialized(self) -> bool:
        return self._initialize_result is not None and self.process.running

    @property
    def initialize_result(self) -> MCPInitializeResult | None:
        return self._initialize_result

    @property
    def discovery_result(self) -> MCPToolDiscoveryResult | None:
        return self._discovery_result

    def initialize(self, *, timeout_seconds: float | None = None) -> MCPInitializeResult:
        if self.initialized:
            assert self._initialize_result is not None
            return self._initialize_result

        params = {
            "protocolVersion": MCP_DEFAULT_WIRE_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {
                "name": MCP_CLIENT_NAME,
                "version": MCP_CLIENT_VERSION,
            },
        }
        try:
            result = self.request(
                "initialize",
                params,
                timeout_seconds=timeout_seconds,
            )
            initialized = self._parse_initialize_result(result)
            self.notify("notifications/initialized")
        except MCPProtocolError as exc:
            error = self._phase_error(
                exc,
                ToolErrorCode.MCP_INITIALIZATION_FAILED.value,
                "MCP initialize failed.",
            )
            self._last_info = self._connection_error_info(error, MCPServerState.FAILED.value)
            raise error from exc

        process_info = self.process.connection_info(state=MCPServerState.READY.value)
        self._initialize_result = initialized
        self._last_info = initialized.to_connection_info(
            state=MCPServerState.READY.value,
            transport=self.config.transport,
            pid=process_info.pid,
            started_at=process_info.started_at,
            stopped_at=process_info.stopped_at,
            metadata=process_info.metadata,
        )
        return initialized

    def list_tools(
        self,
        *,
        timeout_seconds: float | None = None,
    ) -> MCPToolDiscoveryResult:
        if not self.initialized:
            self.initialize(timeout_seconds=timeout_seconds)
        try:
            result = self.request("tools/list", {}, timeout_seconds=timeout_seconds)
            discovery = self._parse_tools_list_result(result, refreshed=False)
        except MCPProtocolError as exc:
            error = self._phase_error(
                exc,
                ToolErrorCode.MCP_TOOL_LIST_FAILED.value,
                "MCP tools/list failed.",
            )
            self._last_info = self._connection_error_info(
                error,
                MCPServerState.TOOL_DISCOVERY_FAILED.value,
            )
            raise error from exc

        self._discovery_result = discovery
        self._last_info = self._ready_info(discovery)
        return discovery

    def refresh_tools(
        self,
        *,
        timeout_seconds: float | None = None,
    ) -> MCPToolDiscoveryResult:
        previous = self._discovery_result
        discovery = self.list_tools(timeout_seconds=timeout_seconds)
        discovery.refreshed = True
        previous_hashes = previous.schema_hashes if previous is not None else {}
        current_hashes = discovery.schema_hashes
        discovery.metadata.update(
            {
                "previous_schema_hashes": previous_hashes,
                "added_tools": sorted(set(current_hashes) - set(previous_hashes)),
                "removed_tools": sorted(set(previous_hashes) - set(current_hashes)),
                "changed_tools": sorted(
                    name
                    for name, schema_hash in current_hashes.items()
                    if name in previous_hashes and previous_hashes[name] != schema_hash
                ),
            }
        )
        self._discovery_result = discovery
        self._last_info = self._ready_info(discovery)
        return discovery

    def call_tool(
        self,
        request: MCPCallRequest,
        *,
        timeout_seconds: float | None = None,
    ) -> MCPCallResult:
        if request.server_id != self.config.server_id:
            raise MCPProtocolError(
                ToolErrorCode.MCP_INVALID_ARGS.value,
                "MCP call server_id does not match client config.",
                server_id=self.config.server_id,
                remote_tool_name=request.remote_tool_name,
            )
        if not self.initialized:
            self.initialize(timeout_seconds=timeout_seconds)
        started = time.monotonic()
        try:
            result = self.request(
                "tools/call",
                request.to_json_rpc_params(),
                timeout_seconds=timeout_seconds or request.timeout_seconds,
            )
        except MCPProtocolError:
            raise
        duration_ms = max(int((time.monotonic() - started) * 1000), 0)
        return self._parse_tools_call_result(
            result,
            remote_tool_name=request.remote_tool_name,
            duration_ms=duration_ms,
            trace_id=request.trace_id,
        )

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> Any:
        method_name = str(method or "").strip()
        if not method_name:
            raise MCPProtocolError(
                ToolErrorCode.MCP_INVALID_ARGS.value,
                "MCP JSON-RPC method must be non-empty.",
                server_id=self.config.server_id,
            )
        timeout = max(float(timeout_seconds or self.config.timeout_seconds or 30), 0.001)
        with self._request_lock:
            if not self.process.running:
                self.start()
            request_id = next(self._ids)
            payload = {
                "jsonrpc": JSONRPC_VERSION,
                "id": request_id,
                "method": method_name,
                "params": _json_safe(dict(params or {})),
            }
            self.process.write_line(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
            return self._wait_for_response(request_id, timeout_seconds=timeout)

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        method_name = str(method or "").strip()
        if not method_name:
            raise MCPProtocolError(
                ToolErrorCode.MCP_INVALID_ARGS.value,
                "MCP JSON-RPC notification method must be non-empty.",
                server_id=self.config.server_id,
            )
        if not self.process.running:
            self.start()
        payload = {
            "jsonrpc": JSONRPC_VERSION,
            "method": method_name,
            "params": _json_safe(dict(params or {})),
        }
        self.process.write_line(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))

    def _wait_for_response(self, request_id: int, *, timeout_seconds: float) -> Any:
        deadline = time.monotonic() + timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MCPProtocolError(
                    ToolErrorCode.MCP_TIMEOUT.value,
                    "MCP stdio request timed out.",
                    server_id=self.config.server_id,
                    details={"request_id": request_id},
                )
            line = self.process.read_stdout_line(remaining)
            if line is None:
                if self.process.running:
                    continue
                self._last_info = self.process.connection_info(
                    state=MCPServerState.STOPPED.value
                )
                raise MCPProtocolError(
                    ToolErrorCode.MCP_PROCESS_EXITED.value,
                    "MCP stdio process exited before responding.",
                    server_id=self.config.server_id,
                    details={
                        "request_id": request_id,
                        "returncode": None
                        if self.process.process is None
                        else self.process.process.poll(),
                    },
                )
            message = self._parse_message(line, request_id=request_id)
            if message is None:
                continue
            message_id = message.get("id")
            if message_id != request_id:
                # V1 serial mode deliberately discards stale or mismatched
                # responses so they cannot satisfy a later request.
                continue
            if "error" in message:
                error = message.get("error")
                raise MCPProtocolError(
                    ToolErrorCode.MCP_REMOTE_ERROR.value,
                    _remote_error_message(error),
                    server_id=self.config.server_id,
                    details={"request_id": request_id, "remote_error": error},
                )
            if "result" not in message:
                raise MCPProtocolError(
                    ToolErrorCode.MCP_RESULT_INVALID.value,
                    "MCP JSON-RPC response did not include result.",
                    server_id=self.config.server_id,
                    details={"request_id": request_id},
                )
            return message["result"]

    def _parse_message(self, line: str, *, request_id: int) -> dict[str, Any] | None:
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            raise MCPProtocolError(
                ToolErrorCode.MCP_STDOUT_INVALID_JSON.value,
                "MCP stdio stdout contained invalid JSON.",
                server_id=self.config.server_id,
                details={
                    "request_id": request_id,
                    "line_preview": line[:200],
                    "line": exc.lineno,
                    "column": exc.colno,
                },
            ) from exc
        if not isinstance(message, dict):
            raise MCPProtocolError(
                ToolErrorCode.MCP_RESULT_INVALID.value,
                "MCP JSON-RPC message must be an object.",
                server_id=self.config.server_id,
                details={"request_id": request_id},
            )
        if message.get("jsonrpc") != JSONRPC_VERSION:
            raise MCPProtocolError(
                ToolErrorCode.MCP_RESULT_INVALID.value,
                "MCP JSON-RPC version is invalid.",
                server_id=self.config.server_id,
                details={"request_id": request_id},
            )
        if "id" not in message:
            # Notification or server-side request; Step 32 client waits only
            # for direct responses.
            return None
        return message

    def _parse_initialize_result(self, result: Any) -> MCPInitializeResult:
        if not isinstance(result, dict):
            raise MCPProtocolError(
                ToolErrorCode.MCP_INITIALIZATION_FAILED.value,
                "MCP initialize result must be an object.",
                server_id=self.config.server_id,
            )
        protocol_version = str(result.get("protocolVersion") or "").strip()
        if not protocol_version:
            raise MCPProtocolError(
                ToolErrorCode.MCP_INITIALIZATION_FAILED.value,
                "MCP initialize result missing protocolVersion.",
                server_id=self.config.server_id,
            )
        if protocol_version not in MCP_SUPPORTED_WIRE_PROTOCOL_VERSIONS:
            raise MCPProtocolError(
                ToolErrorCode.MCP_INITIALIZATION_FAILED.value,
                "MCP protocol version is not supported.",
                server_id=self.config.server_id,
                details={
                    "protocol_version": protocol_version,
                    "supported_protocol_versions": sorted(MCP_SUPPORTED_WIRE_PROTOCOL_VERSIONS),
                },
            )
        capabilities = result.get("capabilities")
        if not isinstance(capabilities, dict):
            raise MCPProtocolError(
                ToolErrorCode.MCP_INITIALIZATION_FAILED.value,
                "MCP initialize result capabilities must be an object.",
                server_id=self.config.server_id,
            )
        tools_capability = capabilities.get("tools")
        if not isinstance(tools_capability, dict):
            raise MCPProtocolError(
                ToolErrorCode.MCP_INITIALIZATION_FAILED.value,
                "MCP server does not advertise tools capability.",
                server_id=self.config.server_id,
            )
        server_info = result.get("serverInfo") or {}
        if not isinstance(server_info, dict):
            server_info = {}
        return MCPInitializeResult(
            server_id=self.config.server_id,
            protocol_version=protocol_version,
            capabilities=capabilities,
            server_name=server_info.get("name"),
            server_version=server_info.get("version"),
            metadata={
                "client_protocol_version": MCP_DEFAULT_WIRE_PROTOCOL_VERSION,
            },
        )

    def _parse_tools_list_result(
        self,
        result: Any,
        *,
        refreshed: bool,
    ) -> MCPToolDiscoveryResult:
        if not isinstance(result, dict):
            raise MCPProtocolError(
                ToolErrorCode.MCP_TOOL_LIST_FAILED.value,
                "MCP tools/list result must be an object.",
                server_id=self.config.server_id,
            )
        raw_tools = result.get("tools")
        if not isinstance(raw_tools, list):
            raise MCPProtocolError(
                ToolErrorCode.MCP_TOOL_LIST_FAILED.value,
                "MCP tools/list result.tools must be an array.",
                server_id=self.config.server_id,
            )
        allowed = {name for name in self.config.allowed_tools}
        tools: list[MCPRemoteTool] = []
        skipped: list[dict[str, Any]] = []
        for index, raw_tool in enumerate(raw_tools):
            tool, skip = self._parse_remote_tool(raw_tool, index=index, allowed=allowed)
            if tool is not None:
                tools.append(tool)
            else:
                skipped.append(skip)
        return MCPToolDiscoveryResult(
            server_id=self.config.server_id,
            tools=tools,
            skipped_tools=skipped,
            raw_tool_count=len(raw_tools),
            allowed_tools=sorted(allowed),
            refreshed=refreshed,
            metadata={
                "next_cursor_present": bool(result.get("nextCursor")),
                "skipped_count": len(skipped),
            },
        )

    def _parse_tools_call_result(
        self,
        result: Any,
        *,
        remote_tool_name: str,
        duration_ms: int,
        trace_id: str | None,
    ) -> MCPCallResult:
        if not isinstance(result, dict):
            raise MCPProtocolError(
                ToolErrorCode.MCP_RESULT_INVALID.value,
                "MCP tools/call result must be an object.",
                server_id=self.config.server_id,
                remote_tool_name=remote_tool_name,
            )
        content = result.get("content", [])
        if content is None:
            content = []
        if not isinstance(content, list):
            raise MCPProtocolError(
                ToolErrorCode.MCP_RESULT_INVALID.value,
                "MCP tools/call result.content must be an array.",
                server_id=self.config.server_id,
                remote_tool_name=remote_tool_name,
            )
        structured_content = result.get("structuredContent")
        resource_links = _extract_resource_links(content, result)
        is_error = bool(result.get("isError", False))
        metadata = {
            "trace_id": trace_id,
            "remote_result_keys": sorted(str(key) for key in result),
        }
        if is_error:
            return MCPCallResult.fail(
                server_id=self.config.server_id,
                remote_tool_name=remote_tool_name,
                code=ToolErrorCode.MCP_REMOTE_ERROR.value,
                error=_content_error_message(content) or "MCP remote tool returned isError=true.",
                content=content,
                structured_content=structured_content,
                resource_links=resource_links,
                stderr_preview=self.process.stderr_preview(),
                duration_ms=duration_ms,
                metadata=metadata,
            )
        return MCPCallResult.ok(
            server_id=self.config.server_id,
            remote_tool_name=remote_tool_name,
            content=content,
            structured_content=structured_content,
            resource_links=resource_links,
            stderr_preview=self.process.stderr_preview(),
            duration_ms=duration_ms,
            metadata=metadata,
        )

    def _parse_remote_tool(
        self,
        raw_tool: Any,
        *,
        index: int,
        allowed: set[str],
    ) -> tuple[MCPRemoteTool | None, dict[str, Any]]:
        if not isinstance(raw_tool, dict):
            return None, _skipped_tool(index, None, ToolErrorCode.MCP_SCHEMA_INVALID.value, "tool must be an object")
        name = str(raw_tool.get("name") or "").strip()
        if not name:
            return None, _skipped_tool(index, None, ToolErrorCode.MCP_SCHEMA_INVALID.value, "tool.name must be non-empty")
        if allowed and name not in allowed:
            return None, _skipped_tool(index, name, ToolErrorCode.MCP_TOOL_NOT_ALLOWED.value, "tool is not in allowed_tools")
        schema = raw_tool.get("inputSchema")
        if schema is None:
            schema = raw_tool.get("input_schema")
        if not isinstance(schema, dict):
            return None, _skipped_tool(index, name, ToolErrorCode.MCP_SCHEMA_INVALID.value, "tool.inputSchema must be an object schema")
        description = str(raw_tool.get("description") or "")
        if len(description) > DEFAULT_MCP_TOOL_DESCRIPTION_CHARS:
            description = description[:DEFAULT_MCP_TOOL_DESCRIPTION_CHARS]
        try:
            tool = MCPRemoteTool(
                server_id=self.config.server_id,
                name=name,
                title=raw_tool.get("title"),
                description=description,
                input_schema=dict(schema),
                annotations=raw_tool.get("annotations") if isinstance(raw_tool.get("annotations"), dict) else {},
                metadata={
                    "transport": self.config.transport,
                    "raw_index": index,
                },
            )
        except ValueError as exc:
            return None, _skipped_tool(index, name, ToolErrorCode.MCP_SCHEMA_INVALID.value, str(exc))
        return tool, {}

    def _ready_info(self, discovery: MCPToolDiscoveryResult) -> MCPConnectionInfo:
        process_info = self.process.connection_info(state=MCPServerState.READY.value)
        initialized = self._initialize_result
        return MCPConnectionInfo(
            server_id=self.config.server_id,
            state=MCPServerState.READY.value,
            transport=self.config.transport,
            pid=process_info.pid,
            started_at=process_info.started_at,
            stopped_at=process_info.stopped_at,
            protocol_version=None if initialized is None else initialized.protocol_version,
            server_name=None if initialized is None else initialized.server_name,
            server_version=None if initialized is None else initialized.server_version,
            capabilities={} if initialized is None else dict(initialized.capabilities),
            metadata={
                **process_info.metadata,
                "tool_count": len(discovery.tools),
                "raw_tool_count": discovery.raw_tool_count,
                "skipped_tools": list(discovery.skipped_tools),
                "schema_hashes": discovery.schema_hashes,
            },
        )

    def _connection_error_info(
        self,
        error: MCPProtocolError,
        state: str,
    ) -> MCPConnectionInfo:
        process_info = self.process.connection_info(state=state)
        return MCPConnectionInfo(
            server_id=self.config.server_id,
            state=state,
            transport=self.config.transport,
            pid=process_info.pid,
            started_at=process_info.started_at,
            stopped_at=process_info.stopped_at,
            last_error_code=error.code,
            last_error_message=error.message,
            metadata=process_info.metadata,
        )

    def _phase_error(
        self,
        error: MCPProtocolError,
        code: str,
        message: str,
    ) -> MCPProtocolError:
        if error.code in {
            ToolErrorCode.MCP_TIMEOUT.value,
            ToolErrorCode.MCP_PROCESS_EXITED.value,
            ToolErrorCode.MCP_COMMAND_NOT_FOUND.value,
            ToolErrorCode.MCP_SERVER_DISABLED.value,
            ToolErrorCode.MCP_TRANSPORT_NOT_SUPPORTED.value,
        }:
            return error
        if error.code == code:
            return error
        return MCPProtocolError(
            code,
            message,
            server_id=self.config.server_id,
            details={"cause": error.to_dict()},
        )


def _remote_error_message(error: Any) -> str:
    if isinstance(error, dict):
        message = error.get("message")
        if message:
            return str(message)
    return "MCP remote JSON-RPC error."


def _skipped_tool(index: int, name: str | None, code: str, reason: str) -> dict[str, Any]:
    return {
        "index": index,
        "name": name,
        "code": code,
        "reason": reason,
    }


def _extract_resource_links(content: list[Any], result: dict[str, Any]) -> list[Any]:
    links: list[Any] = []
    result_links = result.get("resourceLinks")
    if isinstance(result_links, list):
        links.extend(result_links)
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") in {"resource_link", "resource"}:
            resource = item.get("resource") if isinstance(item.get("resource"), dict) else item
            links.append(resource)
    return links


def _content_error_message(content: list[Any]) -> str | None:
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text" and item.get("text"):
            return str(item.get("text"))
    return None


__all__ = [
    "JSONRPC_VERSION",
    "MCPStdioClient",
]
