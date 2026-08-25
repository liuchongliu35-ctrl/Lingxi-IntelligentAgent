from __future__ import annotations

import copy
import json
import os
from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping

from src.models.credentials import contains_sensitive_key

from .mcp.config import MCPConfigError, MCPServersConfig
from .policy import DEFAULT_PERMISSIONS
from .protocol import OBSERVATION_MODES


DEFAULT_TOOLS_CONFIG_DIR = ("config", "tools")
TOOLS_CONFIG_FILE_ORDER = (
    "defaults.json",
    "policies.json",
    "providers.json",
    "mcp_servers.json",
)
WORKSPACE_ROOT_POLICIES = frozenset({"workspace_only"})
RISK_POLICY_ACTIONS = frozenset({"allow", "confirm", "block"})


class ToolsConfigErrorCode(str, Enum):
    INVALID_JSON = "invalid_json"
    INVALID_VALUE = "invalid_value"
    PLAIN_SECRET_IN_CONFIG = "plain_secret_in_config"
    UNKNOWN_ERROR = "unknown_error"


TOOLS_CONFIG_ERROR_CODES = frozenset(item.value for item in ToolsConfigErrorCode)


def _json_safe(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _safe_config_value(value: Any, *, key: str | None = None) -> Any:
    if key is not None and _is_sensitive_key(key):
        return "***"
    if isinstance(value, Mapping):
        return {
            str(item_key): _safe_config_value(item, key=str(item_key))
            for item_key, item in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_safe_config_value(item) for item in value]
    return _json_safe(value)


def _is_sensitive_key(key: str) -> bool:
    normalized = str(key or "").strip().lower().replace("-", "_")
    if normalized in {"api_key_env", "credential_ref"}:
        return False
    return contains_sensitive_key(normalized) or any(
        marker in normalized
        for marker in (
            "api_key",
            "access_token",
            "auth_header",
            "private_key",
        )
    )


def _normalize_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _normalize_positive_int(value: Any, default: int, field_name: str) -> int:
    if value is None:
        return default
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise ToolsConfigError(
            ToolsConfigErrorCode.INVALID_VALUE,
            f"{field_name} must be a positive integer",
            details={"field": field_name},
        ) from exc
    if normalized < 1:
        raise ToolsConfigError(
            ToolsConfigErrorCode.INVALID_VALUE,
            f"{field_name} must be a positive integer",
            details={"field": field_name},
        )
    return normalized


def _normalize_string_list(value: Iterable[Any] | None, field_name: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        raise ToolsConfigError(
            ToolsConfigErrorCode.INVALID_VALUE,
            f"{field_name} must be a list",
            details={"field": field_name},
        )
    return [str(item).strip() for item in value if str(item).strip()]


def normalize_tools_config_error_code(
    value: str | ToolsConfigErrorCode | None,
) -> str:
    if isinstance(value, ToolsConfigErrorCode):
        return value.value
    normalized = str(value or "").strip().lower()
    if normalized in TOOLS_CONFIG_ERROR_CODES:
        return normalized
    return ToolsConfigErrorCode.UNKNOWN_ERROR.value


@dataclass
class ToolsConfigError(Exception):
    code: str | ToolsConfigErrorCode
    message: str
    path: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.code = normalize_tools_config_error_code(self.code)
        self.details = dict(self.details or {})
        Exception.__init__(self, self.message)

    def to_dict(self) -> dict[str, Any]:
        return _safe_config_value(self)


@dataclass
class ToolsRuntimeConfig:
    enabled: bool = True
    default_timeout_seconds: int = 30
    max_output_chars: int = 12000
    max_raw_output_chars: int = 50000
    max_observation_chars: int = 16000
    read_file_small_bytes: int = 64 * 1024
    read_file_medium_bytes: int = 512 * 1024
    read_file_hard_bytes: int = 8 * 1024 * 1024
    read_file_preview_chars: int = 4000
    read_file_range_max_lines: int = 400
    default_observation_mode: str = "standard"
    workspace_root_policy: str = "workspace_only"
    logs_path: Path | str = "logs/tools.log"

    def __post_init__(self) -> None:
        self.enabled = bool(self.enabled)
        self.default_timeout_seconds = _normalize_positive_int(
            self.default_timeout_seconds,
            30,
            "default_timeout_seconds",
        )
        self.max_output_chars = _normalize_positive_int(
            self.max_output_chars,
            12000,
            "max_output_chars",
        )
        self.max_raw_output_chars = _normalize_positive_int(
            self.max_raw_output_chars,
            50000,
            "max_raw_output_chars",
        )
        self.max_observation_chars = _normalize_positive_int(
            self.max_observation_chars,
            16000,
            "max_observation_chars",
        )
        self.read_file_small_bytes = _normalize_positive_int(
            self.read_file_small_bytes,
            64 * 1024,
            "read_file_small_bytes",
        )
        self.read_file_medium_bytes = _normalize_positive_int(
            self.read_file_medium_bytes,
            512 * 1024,
            "read_file_medium_bytes",
        )
        self.read_file_hard_bytes = _normalize_positive_int(
            self.read_file_hard_bytes,
            8 * 1024 * 1024,
            "read_file_hard_bytes",
        )
        self.read_file_preview_chars = _normalize_positive_int(
            self.read_file_preview_chars,
            4000,
            "read_file_preview_chars",
        )
        self.read_file_range_max_lines = _normalize_positive_int(
            self.read_file_range_max_lines,
            400,
            "read_file_range_max_lines",
        )
        if self.read_file_small_bytes > self.read_file_medium_bytes:
            raise ToolsConfigError(
                ToolsConfigErrorCode.INVALID_VALUE,
                "read_file_small_bytes must be <= read_file_medium_bytes",
                details={"field": "read_file_small_bytes"},
            )
        if self.read_file_medium_bytes > self.read_file_hard_bytes:
            raise ToolsConfigError(
                ToolsConfigErrorCode.INVALID_VALUE,
                "read_file_medium_bytes must be <= read_file_hard_bytes",
                details={"field": "read_file_medium_bytes"},
            )
        self.default_observation_mode = str(
            self.default_observation_mode or "standard"
        ).strip().lower()
        if self.default_observation_mode not in OBSERVATION_MODES:
            raise ToolsConfigError(
                ToolsConfigErrorCode.INVALID_VALUE,
                "default_observation_mode is unsupported",
                details={"field": "default_observation_mode"},
            )
        self.workspace_root_policy = str(
            self.workspace_root_policy or "workspace_only"
        ).strip().lower()
        if self.workspace_root_policy not in WORKSPACE_ROOT_POLICIES:
            raise ToolsConfigError(
                ToolsConfigErrorCode.INVALID_VALUE,
                "workspace_root_policy is unsupported",
                details={"field": "workspace_root_policy"},
            )

    def with_resolved_paths(self, root: Path) -> "ToolsRuntimeConfig":
        clone = copy.deepcopy(self)
        logs_path = Path(clone.logs_path)
        if not logs_path.is_absolute():
            logs_path = root / logs_path
        clone.logs_path = logs_path.resolve()
        return clone

    def to_dict(self) -> dict[str, Any]:
        return _safe_config_value(self)


@dataclass
class ToolsPolicyConfig:
    default_permissions: dict[str, bool] = field(
        default_factory=lambda: dict(DEFAULT_PERMISSIONS)
    )
    risk_policy: dict[str, str] = field(
        default_factory=lambda: {
            "low": "allow",
            "medium": "allow",
            "high": "confirm",
            "blocked": "block",
        }
    )
    sensitive_paths: list[str] = field(default_factory=list)
    blocked_paths: list[str] = field(default_factory=list)
    ignored_directories: list[str] = field(default_factory=list)
    blocked_tools: list[str] = field(default_factory=list)
    blocked_scopes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        values = dict(DEFAULT_PERMISSIONS)
        if not isinstance(self.default_permissions, Mapping):
            raise ToolsConfigError(
                ToolsConfigErrorCode.INVALID_VALUE,
                "default_permissions must be an object",
                details={"field": "default_permissions"},
            )
        values.update(
            {
                key: _normalize_bool(value, values[key])
                for key, value in self.default_permissions.items()
                if key in values
            }
        )
        self.default_permissions = values

        if not isinstance(self.risk_policy, Mapping):
            raise ToolsConfigError(
                ToolsConfigErrorCode.INVALID_VALUE,
                "risk_policy must be an object",
                details={"field": "risk_policy"},
            )
        policy = {
            "low": "allow",
            "medium": "allow",
            "high": "confirm",
            "blocked": "block",
        }
        for risk, action in self.risk_policy.items():
            normalized_risk = str(risk).strip().lower()
            normalized_action = str(action).strip().lower()
            if normalized_risk not in policy or normalized_action not in RISK_POLICY_ACTIONS:
                raise ToolsConfigError(
                    ToolsConfigErrorCode.INVALID_VALUE,
                    "risk_policy contains an unsupported value",
                    details={"field": "risk_policy", "risk": str(risk)},
                )
            policy[normalized_risk] = normalized_action
        if policy["blocked"] != "block":
            raise ToolsConfigError(
                ToolsConfigErrorCode.INVALID_VALUE,
                "blocked risk must remain blocked",
                details={"field": "risk_policy.blocked"},
            )
        self.risk_policy = policy
        self.sensitive_paths = _normalize_string_list(
            self.sensitive_paths,
            "sensitive_paths",
        )
        self.blocked_paths = _normalize_string_list(
            self.blocked_paths,
            "blocked_paths",
        )
        self.ignored_directories = _normalize_string_list(
            self.ignored_directories,
            "ignored_directories",
        )
        self.blocked_tools = _normalize_string_list(self.blocked_tools, "blocked_tools")
        self.blocked_scopes = _normalize_string_list(
            self.blocked_scopes,
            "blocked_scopes",
        )

    def to_dict(self) -> dict[str, Any]:
        return _safe_config_value(self)


@dataclass
class ToolsConfig:
    workspace_root: Path
    config_dir: Path
    runtime: ToolsRuntimeConfig = field(default_factory=ToolsRuntimeConfig)
    policy: ToolsPolicyConfig = field(default_factory=ToolsPolicyConfig)
    providers: dict[str, Any] = field(default_factory=dict)
    mcp_servers: dict[str, Any] = field(default_factory=dict)
    loaded_files: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.workspace_root = Path(self.workspace_root).resolve()
        self.config_dir = Path(self.config_dir).resolve()
        self.runtime = self.runtime.with_resolved_paths(self.workspace_root)
        self.providers = dict(self.providers or {})
        if not isinstance(self.mcp_servers, MCPServersConfig):
            self.mcp_servers = MCPServersConfig.from_mapping(
                self.mcp_servers or {},
                workspace_root=self.workspace_root,
            )
        self.loaded_files = [str(item) for item in self.loaded_files]

    def to_dict(self) -> dict[str, Any]:
        value = _safe_config_value(self)
        if isinstance(value, dict):
            value["mcp_servers"] = self.mcp_servers.to_safe_dict()
        return value


def default_tools_config(workspace_root: str | Path | None = None) -> ToolsConfig:
    root = _resolve_workspace_root(workspace_root)
    return ToolsConfig(
        workspace_root=root,
        config_dir=_resolve_config_dir(root),
    )


def _resolve_workspace_root(workspace_root: str | Path | None) -> Path:
    return Path(
        workspace_root or os.getenv("AGENT_WORKSPACE_ROOT") or Path.cwd()
    ).resolve()


def _resolve_config_dir(workspace_root: Path) -> Path:
    override = os.getenv("TOOLS_CONFIG_DIR")
    if not override:
        return (workspace_root / Path(*DEFAULT_TOOLS_CONFIG_DIR)).resolve()
    path = Path(override)
    if not path.is_absolute():
        path = workspace_root / path
    return path.resolve()


def _read_json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ToolsConfigError(
            ToolsConfigErrorCode.INVALID_JSON,
            f"invalid JSON in {path.name}: {exc.msg}",
            path=str(path),
            details={"line": exc.lineno, "column": exc.colno},
        ) from exc


def _load_json_if_present(path: Path, loaded_files: list[str]) -> Any | None:
    if not path.exists():
        return None
    loaded_files.append(str(path))
    return _read_json_file(path)


def _require_object(value: Any | None, path: Path) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ToolsConfigError(
            ToolsConfigErrorCode.INVALID_VALUE,
            f"{path.name} must contain a JSON object",
            path=str(path),
        )
    return dict(value)


def _find_plain_secret_keys(value: Any, *, path: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key)
            item_path = f"{path}.{key_text}" if path else key_text
            if _is_sensitive_key(key_text):
                found.append(item_path)
            found.extend(_find_plain_secret_keys(item, path=item_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_find_plain_secret_keys(item, path=f"{path}[{index}]"))
    return found


def _reject_plain_secrets(value: Any, path: Path) -> None:
    keys = _find_plain_secret_keys(value)
    if keys:
        raise ToolsConfigError(
            ToolsConfigErrorCode.PLAIN_SECRET_IN_CONFIG,
            "tools configuration must not contain plaintext secrets",
            path=str(path),
            details={"keys": keys},
        )


def _load_runtime_config(raw: Any | None, path: Path) -> ToolsRuntimeConfig:
    values = _require_object(raw, path)
    _reject_plain_secrets(values, path)
    _apply_runtime_env_overrides(values)
    return ToolsRuntimeConfig(**values)


def _apply_runtime_env_overrides(values: dict[str, Any]) -> None:
    env_map: dict[str, tuple[str, Any]] = {
        "TOOLS_ENABLED": ("enabled", lambda value: _normalize_bool(value, True)),
        "TOOLS_DEFAULT_TIMEOUT_SECONDS": ("default_timeout_seconds", int),
        "TOOLS_MAX_OUTPUT_CHARS": ("max_output_chars", int),
        "TOOLS_MAX_RAW_OUTPUT_CHARS": ("max_raw_output_chars", int),
        "TOOLS_MAX_OBSERVATION_CHARS": ("max_observation_chars", int),
        "TOOLS_READ_FILE_SMALL_BYTES": ("read_file_small_bytes", int),
        "TOOLS_READ_FILE_MEDIUM_BYTES": ("read_file_medium_bytes", int),
        "TOOLS_READ_FILE_HARD_BYTES": ("read_file_hard_bytes", int),
        "TOOLS_READ_FILE_PREVIEW_CHARS": ("read_file_preview_chars", int),
        "TOOLS_READ_FILE_RANGE_MAX_LINES": ("read_file_range_max_lines", int),
        "TOOLS_DEFAULT_OBSERVATION_MODE": ("default_observation_mode", str),
        "TOOLS_LOGS_PATH": ("logs_path", str),
    }
    for env_name, (field_name, parser) in env_map.items():
        raw = os.getenv(env_name)
        if raw is None:
            continue
        try:
            values[field_name] = parser(raw)
        except (TypeError, ValueError):
            values[field_name] = raw


def _load_policy_config(raw: Any | None, path: Path) -> ToolsPolicyConfig:
    values = _require_object(raw, path)
    _reject_plain_secrets(values, path)
    return ToolsPolicyConfig(**values)


def _load_providers_config(raw: Any | None, path: Path) -> dict[str, Any]:
    values = _require_object(raw, path)
    _reject_plain_secrets(values, path)
    return values


def _load_mcp_servers_config(
    raw: Any | None,
    path: Path,
    *,
    workspace_root: Path,
) -> MCPServersConfig:
    values = _require_object(raw, path)
    try:
        return MCPServersConfig.from_mapping(values, workspace_root=workspace_root)
    except MCPConfigError as exc:
        raise ToolsConfigError(
            ToolsConfigErrorCode.INVALID_VALUE,
            exc.message,
            path=str(path),
            details=exc.details,
        ) from exc


@lru_cache(maxsize=8)
def load_tools_config(workspace_root: str | Path | None = None) -> ToolsConfig:
    root = _resolve_workspace_root(workspace_root)
    config_dir = _resolve_config_dir(root)
    loaded_files: list[str] = []

    paths = {
        name: config_dir / name
        for name in TOOLS_CONFIG_FILE_ORDER
    }
    raw_defaults = _load_json_if_present(paths["defaults.json"], loaded_files)
    raw_policies = _load_json_if_present(paths["policies.json"], loaded_files)
    raw_providers = _load_json_if_present(paths["providers.json"], loaded_files)
    raw_mcp_servers = _load_json_if_present(paths["mcp_servers.json"], loaded_files)

    return ToolsConfig(
        workspace_root=root,
        config_dir=config_dir,
        runtime=_load_runtime_config(raw_defaults, paths["defaults.json"]),
        policy=_load_policy_config(raw_policies, paths["policies.json"]),
        providers=_load_providers_config(raw_providers, paths["providers.json"]),
        mcp_servers=_load_mcp_servers_config(
            raw_mcp_servers,
            paths["mcp_servers.json"],
            workspace_root=root,
        ),
        loaded_files=loaded_files,
    )


def clear_tools_config_cache() -> None:
    load_tools_config.cache_clear()


def get_tools_config(workspace_root: str | Path | None = None) -> ToolsConfig:
    return load_tools_config(workspace_root)


__all__ = [
    "DEFAULT_TOOLS_CONFIG_DIR",
    "TOOLS_CONFIG_FILE_ORDER",
    "ToolsConfig",
    "ToolsConfigError",
    "ToolsConfigErrorCode",
    "ToolsPolicyConfig",
    "ToolsRuntimeConfig",
    "clear_tools_config_cache",
    "default_tools_config",
    "get_tools_config",
    "load_tools_config",
    "normalize_tools_config_error_code",
]
