from __future__ import annotations

from dataclasses import asdict, dataclass, field as dataclass_field, is_dataclass
from pathlib import Path
from typing import Any, Dict, List


SAFETY_ALLOWED_CODE = "safety_allowed"
SAFETY_CONFIRMATION_REQUIRED_CODE = "safety_confirmation_required"
SAFETY_BLOCKED_CODE = "safety_blocked"

SAFETY_ALLOW = "allow"
SAFETY_CONFIRM = "confirm"
SAFETY_BLOCK = "block"

DANGEROUS_FILE_OPERATIONS = {"delete", "remove", "move", "rename", "chmod", "chown", "permission", "permissions"}
PATH_ARG_KEYS = {
    "file_path",
    "path",
    "source_path",
    "target_path",
    "target_paths",
    "destination",
    "destination_path",
    "output_path",
    "write_path",
}
SENSITIVE_WINDOWS_ROOTS = (
    ("windows",),
    ("program files",),
    ("program files (x86)",),
    ("programdata",),
)
SENSITIVE_POSIX_ROOTS = {"/bin", "/boot", "/dev", "/etc", "/lib", "/lib64", "/proc", "/root", "/sbin", "/sys", "/usr"}
COMMAND_DANGEROUS_EXECUTABLES = {
    "rm",
    "del",
    "erase",
    "rmdir",
    "rd",
    "format",
    "shutdown",
    "reboot",
    "reg",
    "powershell",
    "pwsh",
    "cmd",
    "sudo",
    "chmod",
    "chown",
}
COMMAND_DOWNLOAD_EXECUTABLES = {"curl", "wget", "Invoke-WebRequest", "iwr"}
COMMAND_SHELL_METACHARS = {"|", "&", ";", "<", ">", "`"}


@dataclass
class SafetyIssue:
    code: str
    severity: str
    message: str
    field: str | None = None
    value: str | None = None
    metadata: Dict[str, Any] = dataclass_field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return _json_safe(self)


@dataclass
class SafetyDecision:
    status: str
    reason: str
    code: str
    issues: List[SafetyIssue] = dataclass_field(default_factory=list)
    requires_confirmation: bool = False
    confirmation_type: str = "confirmation"
    metadata: Dict[str, Any] = dataclass_field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.status == SAFETY_ALLOW

    @property
    def blocked(self) -> bool:
        return self.status == SAFETY_BLOCK

    @property
    def needs_confirmation(self) -> bool:
        return self.status == SAFETY_CONFIRM

    def to_dict(self) -> Dict[str, Any]:
        return _json_safe(self)


class SafetyPolicy:
    """Final engineering safety gate before ReActExecutor executes an action."""

    def evaluate_action(
        self,
        *,
        packet: Any,
        step: Any | None,
        plan: Any,
        task: Any,
        tool_spec: Any | None,
        input_args: Dict[str, Any] | None,
        workspace_root: Path,
        command_confirmation_policy: str = "ask",
    ) -> SafetyDecision:
        action_args = input_args if input_args is not None else dict(getattr(packet, "action_args", {}) or {})
        issues: List[SafetyIssue] = []
        action_type = str(getattr(packet, "action_type", "") or "")
        action_target = str(getattr(packet, "action_target", "") or "")
        workspace = workspace_root.resolve()

        task_policy = str(getattr(task, "action_policy", getattr(plan, "risk_policy", "allow")) or "allow")
        if task_policy == "block":
            issues.append(self._block_issue("task_policy_block", "Task policy blocks this action."))
        if action_type in {"skip_step", "finish", "fail", "request_replan", "blocked", "cancel", "ask_user", "retry_step", "fallback_to_model", "fallback_to_tool"}:
            return self._decision(issues, reason="Control action passed safety policy.")

        if tool_spec is not None:
            self._evaluate_tool_spec(issues, tool_spec)
            self._evaluate_paths(
                issues,
                input_args=action_args,
                workspace_root=workspace,
                workspace_scope=str(getattr(tool_spec, "workspace_scope", "none") or "none"),
                forbidden_paths=self._forbidden_paths(plan, task, step),
            )
            if str(getattr(tool_spec, "workspace_scope", "") or "") == "command" or action_target in {"command_tool", "shell_tool"}:
                self._evaluate_command(issues, action_args=action_args, workspace_root=workspace, command_confirmation_policy=command_confirmation_policy)
        elif action_type == "call_tool":
            issues.append(self._block_issue("tool_spec_missing", f"Tool spec is missing for action target: {action_target}"))

        if bool(getattr(packet, "requires_confirmation", False)):
            issues.append(self._confirm_issue("action_requires_confirmation", "ActionPacket requires user confirmation."))
        if bool(getattr(step, "requires_confirmation", False)):
            reason = str(getattr(step, "confirmation_reason", "") or "Planner step requires user confirmation.")
            issues.append(self._confirm_issue("step_requires_confirmation", reason))
        if bool(getattr(task, "requires_confirmation", False)):
            issues.append(self._confirm_issue("task_requires_confirmation", "Task requires user confirmation."))
        if task_policy == "confirm":
            issues.append(self._confirm_issue("task_policy_confirm", "Task policy requires user confirmation."))

        operation = str(action_args.get("operation") or action_args.get("op") or "").lower()
        if operation in DANGEROUS_FILE_OPERATIONS or bool(action_args.get("destructive_risk", False)):
            issues.append(self._confirm_issue("dangerous_file_operation", f"Dangerous file operation requires confirmation: {operation or 'destructive_risk'}"))

        return self._decision(issues, reason="Action passed safety policy.")

    def _evaluate_tool_spec(self, issues: List[SafetyIssue], tool_spec: Any) -> None:
        risk_level = str(getattr(tool_spec, "risk_level", "low") or "low")
        workspace_scope = str(getattr(tool_spec, "workspace_scope", "none") or "none")
        if risk_level == "blocked":
            issues.append(self._block_issue("tool_risk_blocked", f"Tool risk is blocked: {getattr(tool_spec, 'name', '')}"))
        elif risk_level == "high" or workspace_scope in {"code_execution", "command"}:
            issues.append(self._confirm_issue("dangerous_tool_requires_confirmation", f"Tool requires confirmation before execution: {getattr(tool_spec, 'name', '')}"))
        if bool(getattr(tool_spec, "requires_confirmation", False)):
            issues.append(self._confirm_issue("tool_requires_confirmation", f"ToolSpec requires confirmation: {getattr(tool_spec, 'name', '')}"))

    def _evaluate_paths(
        self,
        issues: List[SafetyIssue],
        *,
        input_args: Dict[str, Any],
        workspace_root: Path,
        workspace_scope: str,
        forbidden_paths: List[str],
    ) -> None:
        path_values = self._path_values(input_args)
        if not path_values:
            return
        forbidden_resolved = [self._resolve_path(workspace_root, value) for value in forbidden_paths if str(value).strip()]
        for field_name, path_text in path_values:
            resolved = self._resolve_path(workspace_root, path_text)
            if self._is_sensitive_path(resolved, workspace_root):
                issues.append(self._block_issue("sensitive_path_blocked", f"Sensitive system path is blocked: {path_text}", field=field_name, value=path_text))
                continue
            if any(resolved == forbidden or forbidden in resolved.parents for forbidden in forbidden_resolved):
                issues.append(self._block_issue("forbidden_path_blocked", f"Path is explicitly forbidden: {path_text}", field=field_name, value=path_text))
                continue
            if workspace_scope in {"write_workspace", "command", "code_execution"} and not self._is_inside(resolved, workspace_root):
                issues.append(self._block_issue("write_outside_workspace", f"Workspace write target is outside workspace: {path_text}", field=field_name, value=path_text))

    def _evaluate_command(
        self,
        issues: List[SafetyIssue],
        *,
        action_args: Dict[str, Any],
        workspace_root: Path,
        command_confirmation_policy: str,
    ) -> None:
        command = str(action_args.get("command", "") or "")
        cwd = str(action_args.get("cwd", ".") or ".")
        if not command.strip():
            issues.append(self._block_issue("command_required", "command is required"))
        if self._resolve_path(workspace_root, cwd) and not self._is_inside(self._resolve_path(workspace_root, cwd), workspace_root):
            issues.append(self._block_issue("command_cwd_outside_workspace", f"cwd is outside workspace: {cwd}", field="cwd", value=cwd))
        if any(marker in command for marker in COMMAND_SHELL_METACHARS):
            issues.append(self._block_issue("command_shell_metacharacters", "Shell metacharacters are not allowed."))
        if action_args.get("shell"):
            issues.append(self._block_issue("command_shell_selection", "Direct shell selection is not allowed."))
        if bool(action_args.get("network_required", False)):
            issues.append(self._block_issue("command_network_blocked", "Network command execution is blocked."))
        risk_level = str(action_args.get("risk_level", "unknown") or "unknown")
        if risk_level == "blocked" or bool(action_args.get("destructive_risk", False)):
            issues.append(self._block_issue("command_destructive_blocked", "destructive command risk is blocked."))
        executable = self._command_executable(command)
        if executable in {item.lower() for item in COMMAND_DANGEROUS_EXECUTABLES}:
            issues.append(self._block_issue("command_executable_blocked", f"Dangerous command executable is blocked: {executable}"))
        if executable in {item.lower() for item in COMMAND_DOWNLOAD_EXECUTABLES}:
            issues.append(self._block_issue("command_download_blocked", f"Network download command is blocked: {executable}"))
        if command_confirmation_policy == "low_risk_auto":
            if risk_level != "low" or bool(action_args.get("writes_files", False)):
                issues.append(self._confirm_issue("command_requires_confirmation", "Command requires user confirmation."))
        else:
            issues.append(self._confirm_issue("command_requires_confirmation", "Command requires user confirmation."))

    def _decision(self, issues: List[SafetyIssue], *, reason: str) -> SafetyDecision:
        blocking = [issue for issue in issues if issue.severity == SAFETY_BLOCK]
        if blocking:
            return SafetyDecision(
                status=SAFETY_BLOCK,
                code=SAFETY_BLOCKED_CODE,
                reason=blocking[0].message,
                issues=issues,
                metadata={"blocked_issue_count": len(blocking)},
            )
        confirming = [issue for issue in issues if issue.severity == SAFETY_CONFIRM]
        if confirming:
            return SafetyDecision(
                status=SAFETY_CONFIRM,
                code=SAFETY_CONFIRMATION_REQUIRED_CODE,
                reason=confirming[0].message,
                issues=issues,
                requires_confirmation=True,
                metadata={"confirmation_issue_count": len(confirming)},
            )
        return SafetyDecision(status=SAFETY_ALLOW, code=SAFETY_ALLOWED_CODE, reason=reason, issues=issues)

    def _path_values(self, input_args: Dict[str, Any]) -> List[tuple[str, str]]:
        paths: List[tuple[str, str]] = []
        for key, value in input_args.items():
            if key not in PATH_ARG_KEYS:
                continue
            if isinstance(value, list):
                paths.extend((key, str(item)) for item in value if str(item).strip())
            elif str(value).strip():
                paths.append((key, str(value)))
        return paths

    def _forbidden_paths(self, plan: Any, task: Any, step: Any | None) -> List[str]:
        values: List[str] = []
        for source in (task, plan, step):
            if source is None:
                continue
            for attr in ("forbidden_paths", "disallowed_paths", "protected_paths"):
                values.extend(str(item) for item in list(getattr(source, attr, []) or []) if str(item).strip())
            metadata = getattr(source, "metadata", None)
            if isinstance(metadata, dict):
                for key in ("forbidden_paths", "disallowed_paths", "protected_paths"):
                    values.extend(str(item) for item in list(metadata.get(key, []) or []) if str(item).strip())
        return values

    def _resolve_path(self, workspace_root: Path, path_text: str) -> Path:
        path = Path(path_text)
        return (workspace_root / path).resolve() if not path.is_absolute() else path.resolve()

    def _is_inside(self, path: Path, root: Path) -> bool:
        return root == path or root in path.parents

    def _is_sensitive_path(self, path: Path, workspace_root: Path) -> bool:
        if self._is_inside(path, workspace_root):
            return False
        anchor = path.anchor.lower()
        parts = tuple(part.lower() for part in path.parts)
        if anchor and len(parts) >= 2:
            root_name = parts[1]
            if any(root_name == marker[0] for marker in SENSITIVE_WINDOWS_ROOTS):
                return True
        text = path.as_posix().lower()
        return any(text == root or text.startswith(f"{root}/") for root in SENSITIVE_POSIX_ROOTS)

    def _command_executable(self, command: str) -> str:
        parts = command.strip().split()
        if not parts:
            return ""
        return Path(parts[0].strip("\"'")).name.lower()

    def _block_issue(self, code: str, message: str, *, field: str | None = None, value: str | None = None) -> SafetyIssue:
        return SafetyIssue(code=code, severity=SAFETY_BLOCK, message=message, field=field, value=value)

    def _confirm_issue(self, code: str, message: str) -> SafetyIssue:
        return SafetyIssue(code=code, severity=SAFETY_CONFIRM, message=message)


def _json_safe(value: Any) -> Any:
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
