from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.core.config import get_settings
from src.tools.base import ToolResult
from src.tools.command_policy import evaluate_shell_command_policy
from src.tools.command_tool import (
    DEFAULT_PREVIEW_CHARS,
    DEFAULT_STREAM_CHARS,
    _command_data,
    _decode_output,
    _failure,
    _failure_dict,
    _resolve_cwd,
)
from src.tools.errors import ToolErrorCode
from src.tools.protocol import ToolCallOptions


SUPPORTED_SHELLS = frozenset({"powershell", "cmd", "bash"})


@dataclass(frozen=True)
class ShellInvocation:
    shell: str
    argv: list[str]


class ShellCommandTool:
    """Run complex shell syntax through an explicit high-risk shell channel."""

    def __init__(
        self,
        *,
        max_stream_chars: int = DEFAULT_STREAM_CHARS,
        preview_chars: int = DEFAULT_PREVIEW_CHARS,
    ) -> None:
        self.max_stream_chars = max(int(max_stream_chars), 1)
        self.preview_chars = max(int(preview_chars), 1)

    def run(
        self,
        command: str,
        shell: str = "powershell",
        cwd: str = ".",
        purpose: str = "",
        timeout_seconds: int = 30,
        network_required: bool = False,
        writes_files: bool = False,
        target_paths: list[str] | None = None,
        *,
        workspace_root: str | Path | None = None,
        tool_call_options: ToolCallOptions | None = None,
        **kwargs: Any,
    ) -> ToolResult:
        del kwargs
        root = _workspace_root(workspace_root)
        command_text = str(command or "")
        if not command_text.strip():
            return ToolResult.fail(
                "command must not be empty.",
                code=ToolErrorCode.INVALID_ARGS.value,
                data={"command": command},
            )
        invocation_result = _shell_invocation(command_text, shell)
        if not invocation_result["ok"]:
            return _failure(invocation_result)
        cwd_result = _resolve_cwd(root, cwd)
        if not cwd_result["ok"]:
            return _failure(cwd_result)
        policy_result = evaluate_shell_command_policy(
            command_text=command_text,
            shell=invocation_result["invocation"].shell,
            cwd=str(cwd_result["cwd"]),
            target_paths=target_paths or [],
            network_required=network_required,
            writes_files=writes_files,
            workspace_root=root,
            tool_call_options=tool_call_options,
        ).to_result()
        if not policy_result["ok"]:
            return _failure(policy_result)

        invocation: ShellInvocation = invocation_result["invocation"]
        timeout = max(int(timeout_seconds), 1)
        started = time.monotonic()
        try:
            completed = subprocess.run(
                invocation.argv,
                cwd=str(cwd_result["path"]),
                capture_output=True,
                text=False,
                timeout=timeout,
                shell=False,
            )
            duration_ms = max(int((time.monotonic() - started) * 1000), 0)
        except subprocess.TimeoutExpired as exc:
            duration_ms = max(int((time.monotonic() - started) * 1000), 0)
            stdout = _decode_output(exc.stdout)
            stderr = _decode_output(exc.stderr)
            data = _command_data(
                command=command_text,
                program=invocation.shell,
                args=[command_text],
                cwd=str(cwd_result["cwd"]),
                purpose=purpose,
                timeout_seconds=timeout,
                duration_ms=duration_ms,
                exit_code=None,
                stdout=stdout,
                stderr=stderr,
                timed_out=True,
                max_stream_chars=self.max_stream_chars,
                preview_chars=self.preview_chars,
            )
            data["shell"] = invocation.shell
            return ToolResult.fail(
                f"Shell command timed out after {timeout} seconds.",
                code=ToolErrorCode.COMMAND_TIMEOUT.value,
                data=data,
            )
        except OSError as exc:
            return ToolResult.fail(
                f"Shell command launch failed: {exc}",
                code=ToolErrorCode.COMMAND_LAUNCH_FAILED.value,
                data={
                    "command": command_text,
                    "shell": invocation.shell,
                    "program": invocation.shell,
                    "args": [command_text],
                    "cwd": str(cwd_result["cwd"]),
                    "purpose": purpose,
                    "timeout_seconds": timeout,
                    "launch_error": str(exc),
                },
            )

        stdout = _decode_output(completed.stdout)
        stderr = _decode_output(completed.stderr)
        data = _command_data(
            command=command_text,
            program=invocation.shell,
            args=[command_text],
            cwd=str(cwd_result["cwd"]),
            purpose=purpose,
            timeout_seconds=timeout,
            duration_ms=duration_ms,
            exit_code=completed.returncode,
            stdout=stdout,
            stderr=stderr,
            timed_out=False,
            max_stream_chars=self.max_stream_chars,
            preview_chars=self.preview_chars,
        )
        data["shell"] = invocation.shell
        if completed.returncode == 0:
            message = data["stdout_preview"] or "Shell command completed successfully."
            return ToolResult.ok(data=data, message=message)
        error = data["stderr_preview"] or f"Shell command failed with exit code {completed.returncode}."
        return ToolResult.fail(
            error,
            code=ToolErrorCode.COMMAND_NONZERO_EXIT.value,
            data=data,
        )


def build_shell_command_preview(
    *,
    command: str,
    shell: str = "powershell",
    cwd: str = ".",
    purpose: str = "",
    timeout_seconds: int = 30,
    network_required: bool = False,
    writes_files: bool = False,
    target_paths: list[str] | None = None,
    workspace_root: str | Path | None = None,
    tool_call_options: ToolCallOptions | None = None,
    requires_confirmation: bool = False,
) -> dict[str, Any]:
    root = _workspace_root(workspace_root)
    command_text = str(command or "")
    invocation_result = _shell_invocation(command_text, shell)
    if not invocation_result["ok"]:
        return _preview_error(
            command=command_text,
            shell=shell,
            requires_confirmation=requires_confirmation,
            result=invocation_result,
        )
    cwd_result = _resolve_cwd(root, cwd)
    if not cwd_result["ok"]:
        return _preview_error(
            command=command_text,
            shell=shell,
            requires_confirmation=requires_confirmation,
            result=cwd_result,
        )
    policy_result = evaluate_shell_command_policy(
        command_text=command_text,
        shell=invocation_result["invocation"].shell,
        cwd=str(cwd_result["cwd"]),
        target_paths=target_paths or [],
        network_required=network_required,
        writes_files=writes_files,
        workspace_root=root,
        tool_call_options=tool_call_options,
    ).to_result()
    if not policy_result["ok"]:
        return _preview_error(
            command=command_text,
            shell=shell,
            requires_confirmation=requires_confirmation,
            result=policy_result,
        )
    invocation: ShellInvocation = invocation_result["invocation"]
    return {
        "command": command_text,
        "shell": invocation.shell,
        "cwd": str(cwd_result["cwd"]),
        "purpose": str(purpose or ""),
        "timeout_seconds": max(int(timeout_seconds), 1),
        "requires_confirmation": bool(requires_confirmation),
        "will_execute": False,
        "shell_argv_preview": invocation.argv[:2] + ["<command>"],
        "risk_matches": policy_result.get("risk_matches", []),
    }


def _shell_invocation(command: str, shell: str) -> dict[str, Any]:
    normalized = str(shell or "powershell").strip().lower()
    if normalized not in SUPPORTED_SHELLS:
        return _failure_dict(
            "shell must be one of: powershell, cmd, bash.",
            ToolErrorCode.INVALID_ARGS.value,
            data={"shell": shell},
        )
    if normalized == "powershell":
        return {
            "ok": True,
            "invocation": ShellInvocation(
                shell=normalized,
                argv=[
                    "powershell",
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    command,
                ],
            ),
        }
    if normalized == "cmd":
        return {
            "ok": True,
            "invocation": ShellInvocation(
                shell=normalized,
                argv=["cmd.exe", "/d", "/c", command],
            ),
        }
    return {
        "ok": True,
        "invocation": ShellInvocation(
            shell=normalized,
            argv=["bash", "-lc", command],
        ),
    }


def _preview_error(
    *,
    command: str,
    shell: str,
    requires_confirmation: bool,
    result: dict[str, Any],
) -> dict[str, Any]:
    return {
        "command": command,
        "shell": shell,
        "requires_confirmation": bool(requires_confirmation),
        "will_execute": False,
        "preview_error": {
            "code": result["code"],
            "message": result["message"],
            "data": result.get("data"),
        },
    }


def _workspace_root(value: str | Path | None) -> Path:
    if value is not None:
        return Path(value).resolve()
    return get_settings().workspace_root.resolve()


__all__ = [
    "SUPPORTED_SHELLS",
    "ShellCommandTool",
    "build_shell_command_preview",
]
