from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from src.tools.errors import ToolErrorCode
from src.tools.protocol import ToolCallOptions

from .file_tools.path_resolver import PathResolver


DELETE_COMMANDS = frozenset({"del", "erase", "rd", "remove-item", "rm", "rmdir"})
BLOCKED_COMMANDS = frozenset({"format", "reboot", "shutdown"})
SHELL_PROGRAMS = frozenset({"bash", "cmd", "cmd.exe", "powershell", "pwsh", "sh"})
SHELL_EVAL_FLAGS = frozenset({"/c", "-c", "-command", "-encodedcommand"})
NETWORK_COMMANDS = frozenset(
    {
        "curl",
        "ftp",
        "iwr",
        "irm",
        "invoke-restmethod",
        "invoke-webrequest",
        "nc",
        "ncat",
        "netcat",
        "nslookup",
        "ping",
        "scp",
        "sftp",
        "ssh",
        "telnet",
        "wget",
    }
)
NETWORK_SUBCOMMANDS: dict[str, set[str]] = {
    "git": {"clone", "fetch", "ls-remote", "pull", "push", "submodule"},
    "npm": {"add", "audit", "install", "publish", "update"},
    "npx": {"create", "install"},
    "pip": {"download", "install"},
    "pip3": {"download", "install"},
    "pnpm": {"add", "install", "update"},
    "yarn": {"add", "install", "upgrade"},
}
FORCE_FLAGS = frozenset({"--force", "-f", "-rf", "-fr", "/f", "/s", "-r"})
PERMISSION_BYPASS_COMMANDS = frozenset(
    {
        "chmod",
        "chown",
        "icacls",
        "runas",
        "set-acl",
        "su",
        "sudo",
        "takeown",
    }
)
SENSITIVE_TEXT_MARKERS = frozenset(
    {
        ".env",
        "api_key",
        "apikey",
        "authorization",
        "bearer ",
        "cookie",
        "id_rsa",
        "id_ed25519",
        "password",
        "private_key",
        "secret",
        "token",
    }
)
READ_SECRET_COMMANDS = frozenset({"cat", "get-content", "gc", "more", "type"})
DELETE_GUIDANCE = "Use the delete_file tool to delete files."
SHELL_ROUTE_GUIDANCE = "Use shell_command_tool for shell command evaluation."


@dataclass(frozen=True)
class CommandPolicyDecision:
    allowed: bool
    code: str
    message: str
    risk_matches: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)

    def to_result(self) -> dict[str, Any]:
        if self.allowed:
            return {
                "ok": True,
                "risk_matches": list(self.risk_matches),
                "data": dict(self.data),
            }
        return {
            "ok": False,
            "code": self.code,
            "message": self.message,
            "data": {
                **dict(self.data),
                "risk_matches": list(self.risk_matches),
            },
        }


def evaluate_command_policy(
    *,
    program: str,
    args: Sequence[str] | None = None,
    command_text: str = "",
    cwd: str = ".",
    target_paths: Sequence[str] | None = None,
    network_required: bool = False,
    writes_files: bool = False,
    workspace_root: str | Path | None = None,
    tool_call_options: ToolCallOptions | None = None,
) -> CommandPolicyDecision:
    argv = [str(program or ""), *[str(item) for item in (args or [])]]
    base = _base_data(
        channel="command",
        command_text=command_text,
        program=argv[0],
        args=argv[1:],
        cwd=cwd,
        target_paths=target_paths or [],
        network_required=network_required,
        writes_files=writes_files,
    )
    risk_matches: list[str] = []
    executable = command_name(argv[0])
    lowered_args = [item.lower() for item in argv[1:]]

    if executable in SHELL_PROGRAMS and any(item in SHELL_EVAL_FLAGS for item in lowered_args):
        return _blocked(
            ToolErrorCode.SHELL_REQUIRED.value,
            SHELL_ROUTE_GUIDANCE,
            "shell_evaluation_in_command_tool",
            base,
            risk_matches,
        )
    deletion = _delete_match_from_argv(executable, lowered_args)
    if deletion:
        return _blocked(
            ToolErrorCode.COMMAND_DELETE_NOT_ALLOWED.value,
            DELETE_GUIDANCE,
            deletion,
            base,
            risk_matches,
        )
    blocked = _blocked_executable(executable, lowered_args)
    if blocked:
        return _blocked(
            ToolErrorCode.COMMAND_BLOCKED.value,
            blocked,
            "blocked_command",
            base,
            risk_matches,
        )
    if _permission_bypass(executable):
        return _blocked(
            ToolErrorCode.COMMAND_BLOCKED.value,
            f"Permission-changing command is blocked: {executable}",
            "permission_bypass",
            base,
            risk_matches,
        )
    secret_risk = _secret_risk(executable, argv[1:], command_text)
    if secret_risk:
        return _blocked(
            ToolErrorCode.COMMAND_BLOCKED.value,
            "Command appears to read or expose sensitive material.",
            secret_risk,
            base,
            risk_matches,
        )
    network_risk = _network_risk(executable, lowered_args, command_text, network_required)
    if network_risk:
        risk_matches.append(network_risk)
        if not bool(getattr(tool_call_options, "allow_network", False)):
            return _blocked(
                ToolErrorCode.NETWORK_NOT_ALLOWED.value,
                "Network command requires allow_network capability.",
                network_risk,
                base,
                risk_matches,
            )
    if writes_files and not bool(getattr(tool_call_options, "allow_write_workspace", False)):
        return _blocked(
            ToolErrorCode.PERMISSION_DENIED.value,
            "writes_files=true requires allow_write_workspace capability.",
            "writes_files_requires_workspace_permission",
            base,
            risk_matches,
        )
    path_decision = _check_target_paths(
        target_paths or [],
        workspace_root=workspace_root,
        base=base,
        risk_matches=risk_matches,
    )
    if path_decision is not None:
        return path_decision
    return CommandPolicyDecision(True, ToolErrorCode.OK.value, "allowed", risk_matches, base)


def evaluate_shell_command_policy(
    *,
    command_text: str,
    shell: str,
    cwd: str = ".",
    target_paths: Sequence[str] | None = None,
    network_required: bool = False,
    writes_files: bool = False,
    workspace_root: str | Path | None = None,
    tool_call_options: ToolCallOptions | None = None,
) -> CommandPolicyDecision:
    tokens = shell_tokens(command_text)
    base = _base_data(
        channel="shell_command",
        command_text=command_text,
        program=str(shell or ""),
        args=[],
        cwd=cwd,
        target_paths=target_paths or [],
        network_required=network_required,
        writes_files=writes_files,
    )
    base["shell"] = shell
    risk_matches: list[str] = []

    deletion = _delete_match_from_tokens(tokens)
    if deletion:
        return _blocked(
            ToolErrorCode.COMMAND_DELETE_NOT_ALLOWED.value,
            DELETE_GUIDANCE,
            deletion,
            base,
            risk_matches,
        )
    blocked = _blocked_from_tokens(tokens)
    if blocked:
        return _blocked(
            ToolErrorCode.COMMAND_BLOCKED.value,
            "Blocked dangerous shell command.",
            blocked,
            base,
            risk_matches,
        )
    secret_risk = _secret_risk("", tokens, command_text)
    if secret_risk:
        return _blocked(
            ToolErrorCode.COMMAND_BLOCKED.value,
            "Shell command appears to read or expose sensitive material.",
            secret_risk,
            base,
            risk_matches,
        )
    network_risk = _network_risk_from_tokens(tokens, command_text, network_required)
    if network_risk:
        risk_matches.append(network_risk)
        if not bool(getattr(tool_call_options, "allow_network", False)):
            return _blocked(
                ToolErrorCode.NETWORK_NOT_ALLOWED.value,
                "Network shell command requires allow_network capability.",
                network_risk,
                base,
                risk_matches,
            )
    redirection = _redirection_target_decision(
        command_text,
        workspace_root=workspace_root,
        base=base,
        risk_matches=risk_matches,
    )
    if redirection is not None:
        return redirection
    if writes_files and not bool(getattr(tool_call_options, "allow_write_workspace", False)):
        return _blocked(
            ToolErrorCode.PERMISSION_DENIED.value,
            "writes_files=true requires allow_write_workspace capability.",
            "writes_files_requires_workspace_permission",
            base,
            risk_matches,
        )
    path_decision = _check_target_paths(
        target_paths or [],
        workspace_root=workspace_root,
        base=base,
        risk_matches=risk_matches,
    )
    if path_decision is not None:
        return path_decision
    return CommandPolicyDecision(True, ToolErrorCode.OK.value, "allowed", risk_matches, base)


def command_name(value: str) -> str:
    name = os.path.basename(str(value or "")).lower()
    for suffix in (".exe", ".cmd", ".bat", ".ps1"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def shell_tokens(command_text: str) -> list[str]:
    try:
        return shlex.split(str(command_text or ""), posix=False)
    except ValueError:
        return str(command_text or "").split()


def _base_data(
    *,
    channel: str,
    command_text: str,
    program: str,
    args: Sequence[str],
    cwd: str,
    target_paths: Sequence[str],
    network_required: bool,
    writes_files: bool,
) -> dict[str, Any]:
    return {
        "channel": channel,
        "command": command_text,
        "program": program,
        "args": list(args),
        "cwd": cwd,
        "target_paths": list(target_paths),
        "network_required": bool(network_required),
        "writes_files": bool(writes_files),
    }


def _delete_match_from_argv(executable: str, lowered_args: Sequence[str]) -> str | None:
    if executable in DELETE_COMMANDS:
        if any(item in FORCE_FLAGS for item in lowered_args):
            return "recursive_or_forced_delete"
        return "delete_command"
    return None


def _delete_match_from_tokens(tokens: Sequence[str]) -> str | None:
    lowered = [command_name(token) for token in tokens]
    for index, token in enumerate(lowered):
        if token in DELETE_COMMANDS:
            remaining = {item.lower() for item in tokens[index + 1 : index + 4]}
            if remaining & FORCE_FLAGS:
                return "recursive_or_forced_delete"
            return "delete_command"
    return None


def _blocked_executable(executable: str, lowered_args: Sequence[str]) -> str | None:
    if executable in BLOCKED_COMMANDS:
        return f"Blocked dangerous command executable: {executable}"
    if executable == "reg" and lowered_args and lowered_args[0] == "delete":
        return "Registry delete commands are blocked."
    if executable in {"robocopy", "xcopy"} and any(item in {"/mir", "/purge"} for item in lowered_args):
        return "Mirroring or purge file operations are blocked."
    return None


def _blocked_from_tokens(tokens: Sequence[str]) -> str | None:
    lowered = [command_name(token) for token in tokens]
    for index, token in enumerate(lowered):
        if token in BLOCKED_COMMANDS:
            return "blocked_command"
        if token == "reg" and index + 1 < len(lowered) and lowered[index + 1] == "delete":
            return "reg_delete"
        if token in {"robocopy", "xcopy"}:
            window = {item.lower() for item in tokens[index + 1 : index + 5]}
            if window & {"/mir", "/purge"}:
                return "destructive_mirror"
        if token in PERMISSION_BYPASS_COMMANDS:
            return "permission_bypass"
    return None


def _permission_bypass(executable: str) -> bool:
    return executable in PERMISSION_BYPASS_COMMANDS


def _secret_risk(executable: str, args: Sequence[str], command_text: str) -> str | None:
    lowered_command = command_text.lower()
    contains_secret_marker = any(marker in lowered_command for marker in SENSITIVE_TEXT_MARKERS)
    if not contains_secret_marker:
        return None
    if executable in READ_SECRET_COMMANDS:
        return "sensitive_read"
    if _network_risk(executable, [str(item).lower() for item in args], command_text, False):
        return "sensitive_exfiltration"
    if any(command_name(item) in READ_SECRET_COMMANDS for item in args):
        return "sensitive_read"
    return "sensitive_material_reference"


def _network_risk(
    executable: str,
    lowered_args: Sequence[str],
    command_text: str,
    network_required: bool,
) -> str | None:
    if network_required:
        return "network_required"
    if executable in NETWORK_COMMANDS:
        return "network_program"
    subcommands = NETWORK_SUBCOMMANDS.get(executable)
    if subcommands:
        first_arg = next((item for item in lowered_args if item and not item.startswith("-")), "")
        if first_arg in subcommands:
            return "network_subcommand"
    if re.search(r"https?://|ftp://|ssh://", command_text.lower()):
        return "network_url"
    return None


def _network_risk_from_tokens(
    tokens: Sequence[str],
    command_text: str,
    network_required: bool,
) -> str | None:
    if network_required:
        return "network_required"
    lowered = [command_name(token) for token in tokens]
    for index, token in enumerate(lowered):
        if token in NETWORK_COMMANDS:
            return "network_program"
        subcommands = NETWORK_SUBCOMMANDS.get(token)
        if subcommands:
            next_arg = _next_non_option(tokens[index + 1 :])
            if next_arg in subcommands:
                return "network_subcommand"
    if re.search(r"https?://|ftp://|ssh://", command_text.lower()):
        return "network_url"
    return None


def _next_non_option(values: Sequence[str]) -> str:
    for value in values:
        text = str(value).lower()
        if text and not text.startswith("-"):
            return text
    return ""


def _check_target_paths(
    target_paths: Sequence[str],
    *,
    workspace_root: str | Path | None,
    base: dict[str, Any],
    risk_matches: list[str],
) -> CommandPolicyDecision | None:
    root = Path(workspace_root or ".").resolve()
    resolver = PathResolver(root)
    for target in target_paths:
        resolved = resolver.resolve(str(target))
        if not resolved.valid or not resolved.is_inside_workspace or resolved.is_blocked:
            return _blocked(
                resolved.error_code,
                resolved.reason or "Invalid target path.",
                "target_path_blocked",
                {**base, "target_path": target, "path_result": resolved.to_dict()},
                risk_matches,
            )
    return None


def _redirection_target_decision(
    command_text: str,
    *,
    workspace_root: str | Path | None,
    base: dict[str, Any],
    risk_matches: list[str],
) -> CommandPolicyDecision | None:
    for target in redirection_targets(command_text):
        resolved = PathResolver(Path(workspace_root or ".").resolve()).resolve(target)
        if not resolved.valid or not resolved.is_inside_workspace or resolved.is_blocked:
            return _blocked(
                resolved.error_code,
                resolved.reason or "Invalid shell redirection target.",
                "redirection_target_blocked",
                {**base, "target_path": target, "path_result": resolved.to_dict()},
                risk_matches,
            )
    return None


def redirection_targets(command_text: str) -> list[str]:
    targets: list[str] = []
    pattern = re.compile(r"(?<![2&])(?:>>|>)(?!&)\s*(\"[^\"]+\"|'[^']+'|[^\s;&|]+)")
    for match in pattern.finditer(str(command_text or "")):
        raw = match.group(1).strip()
        if not raw or raw.startswith("&"):
            continue
        targets.append(raw.strip("\"'"))
    return targets


def _blocked(
    code: str,
    message: str,
    risk: str,
    data: dict[str, Any],
    risk_matches: list[str],
) -> CommandPolicyDecision:
    combined = [*risk_matches, risk]
    return CommandPolicyDecision(
        allowed=False,
        code=code,
        message=message,
        risk_matches=list(dict.fromkeys(combined)),
        data=data,
    )


__all__ = [
    "BLOCKED_COMMANDS",
    "CommandPolicyDecision",
    "DELETE_COMMANDS",
    "DELETE_GUIDANCE",
    "NETWORK_COMMANDS",
    "SHELL_PROGRAMS",
    "evaluate_command_policy",
    "evaluate_shell_command_policy",
    "redirection_targets",
    "shell_tokens",
]
