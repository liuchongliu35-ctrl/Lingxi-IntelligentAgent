from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping

from .base import _json_safe
from .errors import ToolErrorCode
from .path_policy import PathPolicy, extract_path_values
from .protocol import ToolCallOptions, ToolCallRequest
from .registry import ToolSpec


DEFAULT_PERMISSIONS: dict[str, bool] = {
    "allow_read_workspace": True,
    "allow_write_workspace": False,
    "allow_network": False,
    "allow_command": False,
    "allow_shell_command": False,
    "allow_mcp": False,
}

_RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "blocked": 3}
_RISK_POLICY_ACTIONS = {"allow", "confirm", "block"}
_WORKSPACE_SCOPES = {"read_workspace", "write_workspace"}
_PATH_ARGUMENT_SCOPES = _WORKSPACE_SCOPES | {"command", "shell_command", "code_execution"}
_PERMISSION_BY_SCOPE = {
    "read_workspace": "allow_read_workspace",
    "write_workspace": "allow_write_workspace",
    "network": "allow_network",
    "command": "allow_command",
    "shell_command": "allow_shell_command",
    "code_execution": "allow_command",
    "mcp": "allow_mcp",
}


@dataclass
class SessionCapabilities:
    allow_read_workspace: bool = True
    allow_write_workspace: bool = False
    allow_network: bool = False
    allow_command: bool = False
    allow_shell_command: bool = False
    allow_mcp: bool = False

    @classmethod
    def from_mapping(
        cls,
        values: Mapping[str, Any] | "SessionCapabilities" | None,
        *,
        defaults: Mapping[str, bool] | None = None,
    ) -> "SessionCapabilities":
        if isinstance(values, cls):
            return cls(**values.to_dict())
        merged = dict(DEFAULT_PERMISSIONS)
        if defaults:
            merged.update(
                {
                    key: bool(value)
                    for key, value in defaults.items()
                    if key in DEFAULT_PERMISSIONS
                }
            )
        if values:
            merged.update(
                {
                    key: bool(value)
                    for key, value in values.items()
                    if key in DEFAULT_PERMISSIONS
                }
            )
        return cls(**merged)

    @classmethod
    def from_options(
        cls,
        options: ToolCallOptions,
        *,
        defaults: Mapping[str, bool] | None = None,
    ) -> "SessionCapabilities":
        values = {
            key: getattr(options, key, None)
            for key in DEFAULT_PERMISSIONS
            if getattr(options, key, None) is not None
        }
        return cls.from_mapping(values, defaults=defaults)

    def to_dict(self) -> dict[str, bool]:
        return {
            key: bool(getattr(self, key))
            for key in DEFAULT_PERMISSIONS
        }


@dataclass
class ToolPolicyDecision:
    allowed: bool
    blocked: bool
    requires_confirmation: bool
    risk_level: str
    reason: str
    code: str
    preview_required: bool = False
    affected_resources: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.affected_resources = list(self.affected_resources)

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(self)


class ToolPolicy:
    """Single tool-level risk, scope, and confirmation decision point."""

    def __init__(
        self,
        *,
        default_permissions: Mapping[str, bool] | None = None,
        path_policy: PathPolicy | None = None,
        sensitive_paths: Iterable[str] | None = None,
        blocked_paths: Iterable[str] | None = None,
        ignored_paths: Iterable[str] | None = None,
        blocked_tools: Iterable[str] | None = None,
        blocked_scopes: Iterable[str] | None = None,
        risk_policy: Mapping[str, str] | None = None,
    ) -> None:
        self.default_permissions = dict(DEFAULT_PERMISSIONS)
        if default_permissions:
            self.default_permissions.update(
                {
                    key: bool(value)
                    for key, value in default_permissions.items()
                    if key in DEFAULT_PERMISSIONS
                }
            )
        self.path_policy = path_policy or PathPolicy(
            sensitive_paths=sensitive_paths,
            blocked_paths=blocked_paths,
            ignored_paths=ignored_paths,
        )
        self.blocked_tools = {str(item).strip() for item in (blocked_tools or ()) if str(item).strip()}
        self.blocked_scopes = {str(item).strip() for item in (blocked_scopes or ()) if str(item).strip()}
        self.risk_policy = {
            "low": "allow",
            "medium": "allow",
            "high": "confirm",
            "blocked": "block",
        }
        if risk_policy:
            for risk, action in risk_policy.items():
                normalized_risk = str(risk).strip().lower()
                normalized_action = str(action).strip().lower()
                if (
                    normalized_risk in self.risk_policy
                    and normalized_action in _RISK_POLICY_ACTIONS
                ):
                    self.risk_policy[normalized_risk] = normalized_action
        self.risk_policy["blocked"] = "block"

    def decide(
        self,
        spec: ToolSpec,
        request: ToolCallRequest,
        *,
        resolved_paths: Iterable[str] | None = None,
        session_permissions: Mapping[str, Any] | SessionCapabilities | None = None,
        requires_admin: bool | None = None,
        requested_risk_level: str | None = None,
        requested_requires_confirmation: bool | None = None,
        expected_preview_hash: str | None = None,
    ) -> ToolPolicyDecision:
        if not isinstance(spec, ToolSpec):
            raise TypeError("spec must be ToolSpec")
        if not isinstance(request, ToolCallRequest):
            raise TypeError("request must be ToolCallRequest")

        metadata_risk = _risk_from_metadata(spec, request.args)
        effective_risk = _max_risk(
            spec.risk_level,
            _max_risk(requested_risk_level or "low", metadata_risk),
        )
        resources = _normalize_resources(
            resolved_paths
            if resolved_paths is not None
            else extract_path_values(
                request.args,
                include_cwd=spec.workspace_scope in {"command", "shell_command", "code_execution"},
            )
        )
        path_results = self.path_policy.check_many(
            resources,
            workspace_root=request.context.workspace_root,
        )
        affected_resources = [
            result.affected_resource
            for result in path_results
        ]

        # Highest-priority hard blocks never enter confirmation.
        if not spec.enabled:
            return self._blocked(
                risk_level=effective_risk,
                reason=f"tool is disabled: {spec.name}",
                code=ToolErrorCode.TOOL_DISABLED.value,
                affected_resources=affected_resources,
            )
        if spec.name in self.blocked_tools or bool(spec.metadata.get("blocked")):
            return self._blocked(
                risk_level=effective_risk,
                reason=f"tool is blocked by policy: {spec.name}",
                code=ToolErrorCode.BLOCKED_BY_POLICY.value,
                affected_resources=affected_resources,
            )
        if spec.workspace_scope in self.blocked_scopes:
            return self._blocked(
                risk_level=effective_risk,
                reason=f"workspace scope is blocked by policy: {spec.workspace_scope}",
                code=ToolErrorCode.BLOCKED_BY_POLICY.value,
                affected_resources=affected_resources,
            )
        if effective_risk == "blocked":
            return self._blocked(
                risk_level=effective_risk,
                reason=f"tool risk is blocked: {spec.name}",
                code=ToolErrorCode.BLOCKED_BY_POLICY.value,
                affected_resources=affected_resources,
            )
        configured_risk_action = self.risk_policy.get(effective_risk, "confirm")
        if configured_risk_action == "block":
            return self._blocked(
                risk_level=effective_risk,
                reason=f"tool risk is blocked by policy: {effective_risk}",
                code=ToolErrorCode.BLOCKED_BY_POLICY.value,
                affected_resources=affected_resources,
            )

        sensitive_read_requires_confirmation = False
        for path_result in path_results:
            if path_result.blocked:
                if (
                    path_result.sensitive
                    and bool(spec.metadata.get("allow_sensitive_metadata"))
                ):
                    continue
                if (
                    path_result.sensitive
                    and path_result.within_workspace
                    and bool(spec.metadata.get("allow_sensitive_read_with_confirmation"))
                ):
                    sensitive_read_requires_confirmation = True
                    effective_risk = _max_risk(effective_risk, "high")
                    continue
                return self._blocked(
                    risk_level=effective_risk,
                    reason=path_result.reason,
                    code=path_result.code,
                    affected_resources=affected_resources,
                )

        configured_risk_action = self.risk_policy.get(effective_risk, "confirm")
        if configured_risk_action == "block":
            return self._blocked(
                risk_level=effective_risk,
                reason=f"tool risk is blocked by policy: {effective_risk}",
                code=ToolErrorCode.BLOCKED_BY_POLICY.value,
                affected_resources=affected_resources,
            )

        admin_required = (
            bool(spec.metadata.get("requires_admin"))
            if requires_admin is None
            else bool(requires_admin)
        )
        if admin_required:
            return self._blocked(
                risk_level=effective_risk,
                reason="administrator permission is not supported in Tools V1",
                code=ToolErrorCode.ADMIN_PERMISSION_REQUIRED.value,
                affected_resources=affected_resources,
            )

        capabilities = self._capabilities(request.options, session_permissions)
        scope = spec.workspace_scope
        permission_key = _PERMISSION_BY_SCOPE.get(scope)
        if permission_key and not getattr(capabilities, permission_key):
            if request.options.dry_run and _allows_confirmation_preview(spec):
                return ToolPolicyDecision(
                    allowed=True,
                    blocked=False,
                    requires_confirmation=True,
                    risk_level=effective_risk,
                    reason="dry-run preview is allowed before one-call authorization",
                    code=ToolErrorCode.OK.value,
                    preview_required=True,
                    affected_resources=affected_resources,
                )
            if request.options.has_confirmation_ticket:
                # A valid ticket is a bounded one-call authorization. Hard blocks,
                # path scope checks, and admin restrictions have already run above.
                pass
            else:
                return self._blocked(
                    risk_level=effective_risk,
                    reason=f"session capability is not enabled: {permission_key}",
                    code=_permission_code(scope),
                    affected_resources=affected_resources,
                )

        if (
            expected_preview_hash
            and request.options.confirmed
            and request.options.preview_hash != expected_preview_hash
        ):
            return ToolPolicyDecision(
                allowed=False,
                blocked=True,
                requires_confirmation=False,
                risk_level=effective_risk,
                reason="the preview no longer matches the current tool call",
                code=ToolErrorCode.PREVIEW_CONFLICT.value,
                preview_required=True,
                affected_resources=affected_resources,
            )

        requested_confirmation = (
            requested_requires_confirmation is True
            or request.options.require_confirmation is True
            or sensitive_read_requires_confirmation
            or configured_risk_action == "confirm"
        )
        needs_confirmation = (
            effective_risk == "high"
            or bool(spec.requires_confirmation)
            or requested_confirmation
        )
        if not needs_confirmation:
            return ToolPolicyDecision(
                allowed=True,
                blocked=False,
                requires_confirmation=False,
                risk_level=effective_risk,
                reason="tool call is allowed within the active policy",
                code=ToolErrorCode.OK.value,
                affected_resources=affected_resources,
            )

        if not request.options.has_confirmation_ticket:
            if request.options.dry_run:
                return ToolPolicyDecision(
                    allowed=True,
                    blocked=False,
                    requires_confirmation=True,
                    risk_level=effective_risk,
                    reason="dry-run preview is allowed before confirmation",
                    code=ToolErrorCode.OK.value,
                    preview_required=True,
                    affected_resources=affected_resources,
                )
            return ToolPolicyDecision(
                allowed=False,
                blocked=False,
                requires_confirmation=True,
                risk_level=effective_risk,
                reason="valid confirmation_id and preview_hash are required",
                code=ToolErrorCode.CONFIRMATION_REQUIRED.value,
                preview_required=True,
                affected_resources=affected_resources,
            )

        if expected_preview_hash and request.options.preview_hash != expected_preview_hash:
            return ToolPolicyDecision(
                allowed=False,
                blocked=True,
                requires_confirmation=False,
                risk_level=effective_risk,
                reason="the preview no longer matches the current tool call",
                code=ToolErrorCode.PREVIEW_CONFLICT.value,
                preview_required=True,
                affected_resources=affected_resources,
            )

        return ToolPolicyDecision(
            allowed=True,
            blocked=False,
            requires_confirmation=False,
            risk_level=effective_risk,
            reason="tool call is allowed by a valid confirmation ticket",
            code=ToolErrorCode.OK.value,
            affected_resources=affected_resources,
        )

    evaluate = decide

    def _capabilities(
        self,
        options: ToolCallOptions,
        session_permissions: Mapping[str, Any] | SessionCapabilities | None,
    ) -> SessionCapabilities:
        capabilities = SessionCapabilities.from_options(
            options,
            defaults=self.default_permissions,
        )
        if session_permissions is not None:
            capabilities = SessionCapabilities.from_mapping(
                session_permissions,
                defaults=capabilities.to_dict(),
            )
        return capabilities

    @staticmethod
    def _blocked(
        *,
        risk_level: str,
        reason: str,
        code: str,
        affected_resources: list[str],
    ) -> ToolPolicyDecision:
        return ToolPolicyDecision(
            allowed=False,
            blocked=True,
            requires_confirmation=False,
            risk_level=risk_level,
            reason=reason,
            code=code,
            affected_resources=affected_resources,
        )


def _max_risk(spec_risk: str, requested_risk: str | None) -> str:
    normalized_spec = spec_risk if spec_risk in _RISK_ORDER else "medium"
    if requested_risk not in _RISK_ORDER:
        return normalized_spec
    return max((normalized_spec, requested_risk), key=_RISK_ORDER.get)


def _risk_from_metadata(spec: ToolSpec, args: Mapping[str, Any]) -> str | None:
    risk_by_arg = spec.metadata.get("risk_by_arg")
    if not isinstance(risk_by_arg, Mapping):
        return None
    effective: str | None = None
    for arg_name, value_map in risk_by_arg.items():
        if not isinstance(value_map, Mapping):
            continue
        value = args.get(str(arg_name))
        mapped = value_map.get(value)
        if mapped is None:
            mapped = value_map.get(str(value))
        if isinstance(mapped, str) and mapped in _RISK_ORDER:
            effective = _max_risk(effective or "low", mapped)
    return effective


def _permission_code(scope: str) -> str:
    if scope == "network":
        return ToolErrorCode.NETWORK_NOT_ALLOWED.value
    if scope in {"command", "shell_command", "code_execution"}:
        return ToolErrorCode.COMMAND_BLOCKED.value
    return ToolErrorCode.PERMISSION_DENIED.value


def _allows_confirmation_preview(spec: ToolSpec) -> bool:
    return bool(
        spec.supports_dry_run
        or spec.risk_level in {"high", "blocked"}
        or spec.workspace_scope != "none"
    )


def _normalize_resources(values: Iterable[str] | None) -> list[str]:
    if values is None:
        return []
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if not text or _is_non_filesystem_resource(text) or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _is_non_filesystem_resource(value: str) -> bool:
    lowered = value.lower()
    return lowered.startswith(("http://", "https://", "mcp://", "urn:"))


__all__ = [
    "DEFAULT_PERMISSIONS",
    "SessionCapabilities",
    "ToolPolicy",
    "ToolPolicyDecision",
]
