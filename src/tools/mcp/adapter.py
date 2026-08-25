from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field as dataclass_field
from typing import Any

from ..registry import ToolRegistry, ToolSpec
from .protocol import (
    MCPRemoteTool,
    MCPResolvedServerConfig,
    MCPToolDiscoveryResult,
)


MCP_TOOL_NAME_PREFIX = "mcp"
MCP_TOOL_SOURCE_PREFIX = "mcp:"
MCP_TOOL_NAMESPACE_PREFIX = "mcp"
MCP_TOOL_CATEGORY = "mcp"
MCP_TOOL_WORKSPACE_SCOPE = "mcp"
MCP_TOOL_DEFAULT_TIMEOUT_SECONDS = 30
MCP_TOOL_DEFAULT_DESCRIPTION = "MCP remote tool."
_LOCAL_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_LOCAL_NAME_CHARS_RE = re.compile(r"[^A-Za-z0-9_]+")
_HIGH_RISK_KEYWORDS = (
    "create",
    "delete",
    "drop",
    "execute",
    "pay",
    "post",
    "publish",
    "remove",
    "send",
    "shell",
    "transfer",
    "update",
    "write",
)
_LOW_RISK_KEYWORDS = (
    "get",
    "list",
    "query",
    "read",
    "search",
)
_BLOCKED_RISK_KEYWORDS = (
    "drop",
    "pay",
    "shell",
    "transfer",
)
_RISK_LEVELS = {"low", "medium", "high", "blocked"}


@dataclass
class MCPToolAdapterResult:
    server_id: str
    source: str
    specs: list[ToolSpec] = dataclass_field(default_factory=list)
    skipped_tools: list[dict[str, Any]] = dataclass_field(default_factory=list)
    metadata: dict[str, Any] = dataclass_field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "server_id": self.server_id,
            "source": self.source,
            "specs": [spec.to_dict() for spec in self.specs],
            "skipped_tools": [dict(item) for item in self.skipped_tools],
            "metadata": dict(self.metadata),
        }


def mcp_dynamic_source(server_id: str) -> str:
    return f"{MCP_TOOL_SOURCE_PREFIX}{str(server_id).strip()}"


def mcp_tool_namespace(server_id: str) -> str:
    return f"{MCP_TOOL_NAMESPACE_PREFIX}.{str(server_id).strip()}"


def normalize_mcp_local_tool_segment(remote_tool_name: str) -> str:
    raw = str(remote_tool_name or "").strip()
    if _LOCAL_NAME_RE.match(raw):
        return raw
    normalized = _LOCAL_NAME_CHARS_RE.sub("_", raw).strip("_")
    if not normalized:
        normalized = "tool"
    if normalized[0].isdigit():
        normalized = f"tool_{normalized}"
    suffix = hashlib.sha1(raw.encode("utf-8", errors="replace")).hexdigest()[:8]
    return f"{normalized}_{suffix}"


def mcp_local_tool_name(server_id: str, remote_tool_name: str) -> str:
    return (
        f"{MCP_TOOL_NAME_PREFIX}.{str(server_id).strip()}."
        f"{normalize_mcp_local_tool_segment(remote_tool_name)}"
    )


def infer_mcp_tool_risk(remote_tool: MCPRemoteTool) -> str:
    text = f"{remote_tool.name} {remote_tool.description}".lower()
    tokens = set(re.split(r"[^a-z0-9]+", text))
    if tokens.intersection(_BLOCKED_RISK_KEYWORDS):
        return "blocked"
    if tokens.intersection(_HIGH_RISK_KEYWORDS):
        return "high"
    if tokens.intersection(_LOW_RISK_KEYWORDS):
        return "low"
    return "medium"


def adapt_mcp_remote_tool(
    remote_tool: MCPRemoteTool,
    config: MCPResolvedServerConfig,
) -> ToolSpec:
    local_name = mcp_local_tool_name(config.server_id, remote_tool.name)
    policy = _tool_policy_for(config, remote_tool.name)
    inferred_risk = infer_mcp_tool_risk(remote_tool)
    risk_level = _risk_level(policy.get("risk_level"), default=inferred_risk)
    requires_confirmation = bool(
        policy.get("requires_confirmation", risk_level in {"high", "blocked"})
    )
    timeout_seconds = _positive_int(
        policy.get("timeout_seconds"),
        default=config.timeout_seconds or MCP_TOOL_DEFAULT_TIMEOUT_SECONDS,
    )
    return ToolSpec(
        name=local_name,
        description=remote_tool.description or MCP_TOOL_DEFAULT_DESCRIPTION,
        category=MCP_TOOL_CATEGORY,
        namespace=mcp_tool_namespace(config.server_id),
        parameters_schema=dict(remote_tool.input_schema),
        required_params=_required_params(remote_tool.input_schema),
        returns_schema={"type": "object"},
        risk_level=risk_level,
        requires_confirmation=requires_confirmation,
        workspace_scope=MCP_TOOL_WORKSPACE_SCOPE,
        timeout_seconds=timeout_seconds,
        supports_dry_run=True,
        metadata={
            "implemented": True,
            "preview_kind": "mcp",
            "source_type": "mcp",
            "source": mcp_dynamic_source(config.server_id),
            "server_id": config.server_id,
            "remote_tool_name": remote_tool.name,
            "transport": config.transport,
            "remote_schema_hash": remote_tool.schema_hash,
            "remote_tool_title": remote_tool.title,
            "remote_tool_annotations": dict(remote_tool.annotations),
            "risk_inferred": inferred_risk,
            "risk_configured": policy.get("risk_level"),
            "local_name_segment": normalize_mcp_local_tool_segment(remote_tool.name),
        },
    )


def adapt_mcp_discovery_to_specs(
    discovery: MCPToolDiscoveryResult,
    config: MCPResolvedServerConfig,
) -> MCPToolAdapterResult:
    specs: list[ToolSpec] = []
    skipped = [dict(item) for item in discovery.skipped_tools]
    seen: set[str] = set()
    for remote_tool in discovery.tools:
        spec = adapt_mcp_remote_tool(remote_tool, config)
        if spec.name in seen:
            skipped.append(
                {
                    "name": remote_tool.name,
                    "code": "mcp_schema_invalid",
                    "reason": f"local tool name collision: {spec.name}",
                }
            )
            continue
        seen.add(spec.name)
        specs.append(spec)
    return MCPToolAdapterResult(
        server_id=config.server_id,
        source=mcp_dynamic_source(config.server_id),
        specs=specs,
        skipped_tools=skipped,
        metadata={
            "raw_tool_count": discovery.raw_tool_count,
            "adapted_tool_count": len(specs),
            "skipped_count": len(skipped),
            "schema_hashes": discovery.schema_hashes,
        },
    )


def register_mcp_tool_specs(
    registry: ToolRegistry,
    adapter_result: MCPToolAdapterResult,
    *,
    replace_existing_source: bool = True,
) -> list[str]:
    existing_source_names = set(
        registry.list_dynamic_sources().get(adapter_result.source, [])
    )
    _validate_registry_conflicts(registry, adapter_result.specs, existing_source_names)
    if replace_existing_source:
        registry.remove_dynamic_source(adapter_result.source)
    registered: list[str] = []
    try:
        for spec in adapter_result.specs:
            registry.register(spec, source=adapter_result.source)
            registered.append(spec.name)
    except Exception:
        for name in registered:
            registry.unregister(name)
        raise
    return registered


def remove_mcp_tool_specs(registry: ToolRegistry, server_id: str) -> list[str]:
    return registry.remove_dynamic_source(mcp_dynamic_source(server_id))


def _validate_registry_conflicts(
    registry: ToolRegistry,
    specs: list[ToolSpec],
    existing_source_names: set[str],
) -> None:
    names: set[str] = set()
    for spec in specs:
        if spec.name in names:
            raise ValueError(f"duplicate MCP tool name: {spec.name}")
        names.add(spec.name)
        resolved = registry.resolve_name(spec.name)
        if resolved is not None and resolved not in existing_source_names:
            raise ValueError(f"MCP tool conflicts with existing registry entry: {spec.name}")


def _tool_policy_for(config: MCPResolvedServerConfig, remote_tool_name: str) -> dict[str, Any]:
    policies = dict(config.tool_policies or {})
    value = policies.get(remote_tool_name) or policies.get(
        normalize_mcp_local_tool_segment(remote_tool_name)
    )
    return dict(value) if isinstance(value, dict) else {}


def _risk_level(value: Any, *, default: str) -> str:
    candidate = str(value or default or "medium").strip().lower()
    return candidate if candidate in _RISK_LEVELS else "medium"


def _positive_int(value: Any, *, default: int) -> int:
    try:
        return max(int(value), 1)
    except (TypeError, ValueError):
        return max(int(default), 1)


def _required_params(schema: dict[str, Any]) -> list[str]:
    required = schema.get("required")
    if not isinstance(required, list):
        return []
    return [str(item).strip() for item in required if str(item).strip()]


__all__ = [
    "MCP_TOOL_CATEGORY",
    "MCP_TOOL_DEFAULT_DESCRIPTION",
    "MCP_TOOL_DEFAULT_TIMEOUT_SECONDS",
    "MCP_TOOL_NAME_PREFIX",
    "MCP_TOOL_NAMESPACE_PREFIX",
    "MCP_TOOL_SOURCE_PREFIX",
    "MCP_TOOL_WORKSPACE_SCOPE",
    "MCPToolAdapterResult",
    "adapt_mcp_discovery_to_specs",
    "adapt_mcp_remote_tool",
    "infer_mcp_tool_risk",
    "mcp_dynamic_source",
    "mcp_local_tool_name",
    "mcp_tool_namespace",
    "normalize_mcp_local_tool_segment",
    "register_mcp_tool_specs",
    "remove_mcp_tool_specs",
]
