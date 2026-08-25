from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field as dataclass_field
from pathlib import Path
from typing import Any, Mapping

from .protocol import (
    MCP_RISK_LEVELS,
    MCP_TRANSPORT_STDIO,
    MCP_TRANSPORT_STREAMABLE_HTTP,
    MCP_TRANSPORTS,
    MCPResolvedServerConfig,
)


MCP_SERVERS_CONFIG_FILENAME = "mcp_servers.json"
MCP_SERVERS_CONFIG_KEY = "mcpServers"
SERVER_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
ENV_REF_RE = re.compile(r"^\$\{env:([A-Za-z_][A-Za-z0-9_]*)\}$")
ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_SENSITIVE_KEY_MARKERS = (
    "api_key",
    "apikey",
    "authorization",
    "auth_header",
    "cookie",
    "credential",
    "password",
    "private_key",
    "secret",
    "token",
)


@dataclass
class MCPConfigError(Exception):
    message: str
    field: str | None = None
    server_id: str | None = None
    details: dict[str, Any] = dataclass_field(default_factory=dict)

    def __post_init__(self) -> None:
        self.details = dict(self.details or {})
        if self.field is not None:
            self.details.setdefault("field", self.field)
        if self.server_id is not None:
            self.details.setdefault("server_id", self.server_id)
        Exception.__init__(self, self.message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "message": self.message,
            "field": self.field,
            "server_id": self.server_id,
            "details": dict(self.details),
        }


@dataclass
class MCPServerConfig:
    server_id: str
    display_name: str | None = None
    enabled: bool = False
    transport: str = MCP_TRANSPORT_STDIO
    command: str | None = None
    args: list[str] = dataclass_field(default_factory=list)
    env: dict[str, str] = dataclass_field(default_factory=dict)
    cwd: str | None = None
    pass_env: bool = False
    allowed_tools: list[str] = dataclass_field(default_factory=list)
    tool_policies: dict[str, Any] = dataclass_field(default_factory=dict)
    default_risk_level: str = "medium"
    timeout_seconds: int = 30
    endpoint_url: str | None = None
    headers: dict[str, str] = dataclass_field(default_factory=dict)
    credential_ref: str | None = None
    cwd_resolved: str | None = None

    def __post_init__(self) -> None:
        self.server_id = normalize_mcp_server_id(self.server_id)
        self.display_name = _optional_text(self.display_name) or self.server_id
        self.enabled = bool(self.enabled)
        self.transport = _normalize_transport(self.transport)
        self.command = _optional_text(self.command)
        if self.transport == MCP_TRANSPORT_STDIO and not self.command:
            raise MCPConfigError(
                "stdio MCP server command is required",
                field="command",
                server_id=self.server_id,
            )
        self.args = _string_list(self.args, "args", self.server_id)
        self.env = _string_mapping(self.env, "env", self.server_id, env_keys=True)
        _validate_sensitive_mapping_references(self.env, "env", self.server_id)
        self.cwd = _optional_text(self.cwd)
        self.pass_env = bool(self.pass_env)
        self.allowed_tools = _string_list(
            self.allowed_tools,
            "allowed_tools",
            self.server_id,
        )
        self.tool_policies = dict(self.tool_policies or {})
        if not isinstance(self.tool_policies, dict):
            raise MCPConfigError(
                "tool_policies must be an object",
                field="tool_policies",
                server_id=self.server_id,
            )
        self.default_risk_level = str(self.default_risk_level or "medium").strip().lower()
        if self.default_risk_level not in MCP_RISK_LEVELS:
            raise MCPConfigError(
                "default_risk_level is unsupported",
                field="default_risk_level",
                server_id=self.server_id,
            )
        self.timeout_seconds = _positive_int(
            self.timeout_seconds,
            "timeout_seconds",
            self.server_id,
        )
        self.endpoint_url = _optional_text(self.endpoint_url)
        self.headers = _string_mapping(self.headers, "headers", self.server_id)
        _validate_sensitive_mapping_references(self.headers, "headers", self.server_id)
        self.credential_ref = _optional_text(self.credential_ref)
        self.cwd_resolved = _optional_text(self.cwd_resolved)

    @property
    def stdio_execution_enabled(self) -> bool:
        return self.enabled and self.transport == MCP_TRANSPORT_STDIO

    @property
    def transport_supported_for_execution(self) -> bool:
        return self.transport == MCP_TRANSPORT_STDIO

    @classmethod
    def from_mapping(
        cls,
        server_id: str,
        values: Mapping[str, Any],
        *,
        workspace_root: str | Path | None = None,
    ) -> "MCPServerConfig":
        if not isinstance(values, Mapping):
            raise MCPConfigError(
                "MCP server config must be an object",
                field=server_id,
                server_id=server_id,
            )
        raw = dict(values)
        pass_env = raw.pop("passEnv", raw.pop("pass_env", False))
        endpoint_url = raw.pop("endpoint_url", raw.pop("url", None))
        config = cls(
            server_id=server_id,
            display_name=raw.get("display_name"),
            enabled=raw.get("enabled", False),
            transport=raw.get("transport", MCP_TRANSPORT_STDIO),
            command=raw.get("command"),
            args=raw.get("args", []),
            env=raw.get("env", {}),
            cwd=raw.get("cwd"),
            pass_env=pass_env,
            allowed_tools=raw.get("allowed_tools", []),
            tool_policies=raw.get("tool_policies", {}),
            default_risk_level=raw.get("default_risk_level", "medium"),
            timeout_seconds=raw.get("timeout_seconds", 30),
            endpoint_url=endpoint_url,
            headers=raw.get("headers", {}),
            credential_ref=raw.get("credential_ref"),
        )
        config.cwd_resolved = resolve_mcp_cwd(
            config.cwd,
            workspace_root=workspace_root,
            server_id=config.server_id,
        )
        return config

    def resolve_runtime(
        self,
        *,
        environment: Mapping[str, str] | None = None,
    ) -> MCPResolvedServerConfig:
        env_source = environment or os.environ
        return MCPResolvedServerConfig(
            server_id=self.server_id,
            display_name=self.display_name or self.server_id,
            enabled=self.enabled,
            transport=self.transport,
            command=self.command,
            args=list(self.args),
            env=resolve_env_mapping(self.env, env_source, server_id=self.server_id),
            cwd=self.cwd_resolved,
            pass_env=self.pass_env,
            allowed_tools=list(self.allowed_tools),
            tool_policies=dict(self.tool_policies),
            default_risk_level=self.default_risk_level,
            timeout_seconds=self.timeout_seconds,
            endpoint_url=self.endpoint_url,
            headers=resolve_env_mapping(
                self.headers,
                env_source,
                server_id=self.server_id,
                field="headers",
            ),
            credential_ref=self.credential_ref,
        )

    def to_config_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "display_name": self.display_name,
            "enabled": self.enabled,
            "transport": self.transport,
            "default_risk_level": self.default_risk_level,
            "timeout_seconds": self.timeout_seconds,
        }
        if self.command is not None:
            value["command"] = self.command
        value["args"] = list(self.args)
        if self.env:
            value["env"] = dict(self.env)
        if self.cwd is not None:
            value["cwd"] = self.cwd
        value["passEnv"] = self.pass_env
        if self.allowed_tools:
            value["allowed_tools"] = list(self.allowed_tools)
        if self.tool_policies:
            value["tool_policies"] = dict(self.tool_policies)
        if self.endpoint_url is not None:
            value["endpoint_url"] = self.endpoint_url
        if self.headers:
            value["headers"] = dict(self.headers)
        if self.credential_ref is not None:
            value["credential_ref"] = self.credential_ref
        return sanitize_mcp_config(value)

    def to_safe_dict(self) -> dict[str, Any]:
        value = self.to_config_dict()
        value["server_id"] = self.server_id
        value["cwd_resolved"] = self.cwd_resolved
        value["transport_supported_for_execution"] = self.transport_supported_for_execution
        return sanitize_mcp_config(value)


@dataclass
class MCPServersConfig:
    servers: dict[str, MCPServerConfig] = dataclass_field(default_factory=dict)

    @classmethod
    def from_mapping(
        cls,
        raw: Mapping[str, Any] | None,
        *,
        workspace_root: str | Path | None = None,
    ) -> "MCPServersConfig":
        values = dict(raw or {})
        raw_servers = _extract_servers(values)
        servers: dict[str, MCPServerConfig] = {}
        for server_id, server_values in raw_servers.items():
            normalized_id = normalize_mcp_server_id(server_id)
            if normalized_id in servers:
                raise MCPConfigError(
                    "duplicate MCP server_id",
                    field=MCP_SERVERS_CONFIG_KEY,
                    server_id=normalized_id,
                )
            servers[normalized_id] = MCPServerConfig.from_mapping(
                normalized_id,
                server_values,
                workspace_root=workspace_root,
            )
        return cls(servers=servers)

    def to_config_dict(self) -> dict[str, Any]:
        return {
            MCP_SERVERS_CONFIG_KEY: {
                server_id: config.to_config_dict()
                for server_id, config in sorted(self.servers.items())
            }
        }

    def to_safe_dict(self) -> dict[str, Any]:
        return self.to_config_dict()

    def get(self, server_id: str) -> MCPServerConfig | None:
        return self.servers.get(str(server_id or "").strip())

    def enabled_servers(self) -> list[MCPServerConfig]:
        return [config for config in self.servers.values() if config.enabled]


def load_mcp_servers_config_file(
    path: str | Path,
    *,
    workspace_root: str | Path | None = None,
) -> MCPServersConfig:
    config_path = Path(path)
    if not config_path.exists():
        return MCPServersConfig()
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise MCPConfigError(
            f"invalid JSON in {config_path.name}: {exc.msg}",
            field=str(config_path),
            details={"line": exc.lineno, "column": exc.colno},
        ) from exc
    if not isinstance(raw, Mapping):
        raise MCPConfigError("mcp_servers.json must contain an object")
    return MCPServersConfig.from_mapping(raw, workspace_root=workspace_root)


def save_mcp_servers_config_file(
    config: MCPServersConfig | Mapping[str, Any],
    path: str | Path,
    *,
    workspace_root: str | Path | None = None,
) -> MCPServersConfig:
    normalized = (
        config
        if isinstance(config, MCPServersConfig)
        else MCPServersConfig.from_mapping(config, workspace_root=workspace_root)
    )
    config_path = Path(path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = normalized.to_config_dict()
    config_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return normalized


def normalize_mcp_server_id(value: Any) -> str:
    server_id = str(value or "").strip()
    if not server_id or not SERVER_ID_RE.fullmatch(server_id):
        raise MCPConfigError(
            "server_id must contain only letters, digits, underscores or hyphens",
            field="server_id",
            server_id=server_id or None,
        )
    return server_id


def resolve_mcp_cwd(
    cwd: str | Path | None,
    *,
    workspace_root: str | Path | None,
    server_id: str,
) -> str:
    root = Path(workspace_root or ".").expanduser().resolve(strict=False)
    target = root if cwd is None else Path(cwd).expanduser()
    if not target.is_absolute():
        target = root / target
    resolved = target.resolve(strict=False)
    if resolved != root and root not in resolved.parents:
        raise MCPConfigError(
            "cwd must stay within the workspace root",
            field="cwd",
            server_id=server_id,
            details={"cwd": str(cwd), "workspace_root": str(root)},
        )
    return str(resolved)


def resolve_env_mapping(
    values: Mapping[str, str],
    environment: Mapping[str, str],
    *,
    server_id: str,
    field: str = "env",
) -> dict[str, str]:
    resolved: dict[str, str] = {}
    for key, value in values.items():
        ref_name = env_ref_name(value)
        if ref_name is None:
            resolved[key] = value
            continue
        env_value = environment.get(ref_name)
        if env_value is None:
            raise MCPConfigError(
                "referenced environment variable is not configured",
                field=field,
                server_id=server_id,
                details={"env_ref": ref_name, "key": key},
            )
        resolved[key] = str(env_value)
    return resolved


def env_ref_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    match = ENV_REF_RE.fullmatch(value.strip())
    if match is None:
        return None
    return match.group(1)


def sanitize_mcp_config(value: Any) -> Any:
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_sensitive_key(key_text) and env_ref_name(item) is None:
                sanitized[key_text] = "<redacted>"
            else:
                sanitized[key_text] = sanitize_mcp_config(item)
        return sanitized
    if isinstance(value, (list, tuple, set, frozenset)):
        return [sanitize_mcp_config(item) for item in value]
    return value


def _extract_servers(values: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    if MCP_SERVERS_CONFIG_KEY in values:
        raw = values.get(MCP_SERVERS_CONFIG_KEY)
        if not isinstance(raw, Mapping):
            raise MCPConfigError(
                "mcpServers must be an object keyed by server_id",
                field=MCP_SERVERS_CONFIG_KEY,
            )
        return {str(key): _require_server_object(key, item) for key, item in raw.items()}

    legacy = values.get("servers", [])
    if legacy in (None, []):
        return {}
    if not isinstance(legacy, list):
        raise MCPConfigError(
            "legacy servers must be a list",
            field="servers",
        )
    result: dict[str, Mapping[str, Any]] = {}
    for item in legacy:
        if not isinstance(item, Mapping):
            raise MCPConfigError("legacy server item must be an object", field="servers")
        server_id = item.get("server_id") or item.get("id")
        if server_id is None:
            raise MCPConfigError("legacy server item requires server_id", field="server_id")
        item_copy = dict(item)
        item_copy.pop("server_id", None)
        item_copy.pop("id", None)
        result[str(server_id)] = item_copy
    return result


def _require_server_object(key: Any, value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MCPConfigError(
            "MCP server config must be an object",
            field=str(key),
            server_id=str(key),
        )
    return value


def _normalize_transport(value: Any) -> str:
    transport = str(value or MCP_TRANSPORT_STDIO).strip().lower()
    if transport not in MCP_TRANSPORTS:
        raise MCPConfigError(
            "transport is unsupported",
            field="transport",
            details={"transport": transport},
        )
    return transport


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _string_list(value: Any, field_name: str, server_id: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raise MCPConfigError(
            f"{field_name} must be an array, not a shell string",
            field=field_name,
            server_id=server_id,
        )
    if not isinstance(value, list):
        raise MCPConfigError(
            f"{field_name} must be an array",
            field=field_name,
            server_id=server_id,
        )
    return [str(item) for item in value]


def _string_mapping(
    value: Any,
    field_name: str,
    server_id: str,
    *,
    env_keys: bool = False,
) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise MCPConfigError(
            f"{field_name} must be an object",
            field=field_name,
            server_id=server_id,
        )
    result: dict[str, str] = {}
    for key, item in value.items():
        key_text = str(key).strip()
        if not key_text:
            raise MCPConfigError(
                f"{field_name} keys must be non-empty",
                field=field_name,
                server_id=server_id,
            )
        if env_keys and not ENV_KEY_RE.fullmatch(key_text):
            raise MCPConfigError(
                "env keys must be valid environment variable names",
                field=field_name,
                server_id=server_id,
                details={"key": key_text},
            )
        if not isinstance(item, str):
            raise MCPConfigError(
                f"{field_name} values must be strings",
                field=field_name,
                server_id=server_id,
                details={"key": key_text},
            )
        result[key_text] = item
    return result


def _validate_sensitive_mapping_references(
    values: Mapping[str, str],
    field_name: str,
    server_id: str,
) -> None:
    for key, value in values.items():
        if _is_sensitive_key(key) and env_ref_name(value) is None:
            raise MCPConfigError(
                f"sensitive {field_name} values must use ${{env:NAME}} references",
                field=field_name,
                server_id=server_id,
                details={"key": key},
            )


def _positive_int(value: Any, field_name: str, server_id: str) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise MCPConfigError(
            f"{field_name} must be a positive integer",
            field=field_name,
            server_id=server_id,
        ) from exc
    if normalized < 1:
        raise MCPConfigError(
            f"{field_name} must be a positive integer",
            field=field_name,
            server_id=server_id,
        )
    return normalized


def _is_sensitive_key(key: str) -> bool:
    normalized = str(key or "").strip().lower().replace("-", "_")
    return any(marker in normalized for marker in _SENSITIVE_KEY_MARKERS)


__all__ = [
    "ENV_REF_RE",
    "MCPConfigError",
    "MCPServerConfig",
    "MCPServersConfig",
    "MCP_SERVERS_CONFIG_FILENAME",
    "MCP_SERVERS_CONFIG_KEY",
    "SERVER_ID_RE",
    "env_ref_name",
    "load_mcp_servers_config_file",
    "normalize_mcp_server_id",
    "resolve_env_mapping",
    "resolve_mcp_cwd",
    "sanitize_mcp_config",
    "save_mcp_servers_config_file",
]
