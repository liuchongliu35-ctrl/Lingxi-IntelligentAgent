from __future__ import annotations

import shlex
import subprocess
import time
from pathlib import Path
from typing import Any, Iterable, Mapping

from src.core.config import get_settings
from src.tools.base import ToolResult
from src.tools.command_policy import evaluate_command_policy
from src.tools.errors import ToolErrorCode
from src.tools.protocol import ToolCallOptions

from .file_tools.path_resolver import PathResolver


DEFAULT_STREAM_CHARS = 12000
DEFAULT_PREVIEW_CHARS = 4000
SHELL_REQUIRED_MESSAGE = (
    "command_tool only supports argv with shell=False; use shell_command_tool for shell syntax."
)


class CommandTool:
    """Run simple workspace commands through argv and shell=False."""

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
        program: str | None = None,
        args: list[str] | None = None,
        command: str | None = None,
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
        cwd_result = _resolve_cwd(root, cwd)
        if not cwd_result["ok"]:
            return _failure(cwd_result)
        resolved_cwd: Path = cwd_result["path"]

        argv_result = _normalize_argv(program=program, args=args, command=command)
        if not argv_result["ok"]:
            return _failure(argv_result)
        argv: list[str] = argv_result["argv"]
        normalized_program = argv[0]
        normalized_args = argv[1:]
        command_text = str(command or _join_command(argv))

        policy_result = evaluate_command_policy(
            program=normalized_program,
            args=normalized_args,
            command_text=command_text,
            cwd=str(cwd_result["cwd"]),
            network_required=network_required,
            writes_files=writes_files,
            target_paths=target_paths or [],
            workspace_root=root,
            tool_call_options=tool_call_options,
        ).to_result()
        if not policy_result["ok"]:
            return _failure(policy_result)

        timeout = max(int(timeout_seconds), 1)
        started = time.monotonic()
        try:
            completed = subprocess.run(
                argv,
                cwd=str(resolved_cwd),
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
                program=normalized_program,
                args=normalized_args,
                cwd=str(cwd_result["cwd"]),
                purpose=purpose,
                timeout_seconds=timeout,
                duration_ms=duration_ms,
                exit_code=None,
                stdout=stdout,
                stderr=stderr,
                timed_out=True,
            )
            return ToolResult.fail(
                f"Command timed out after {timeout} seconds.",
                code=ToolErrorCode.COMMAND_TIMEOUT.value,
                data=data,
            )
        except OSError as exc:
            return ToolResult.fail(
                f"Command launch failed: {exc}",
                code=ToolErrorCode.COMMAND_LAUNCH_FAILED.value,
                data={
                    "command": command_text,
                    "program": normalized_program,
                    "args": normalized_args,
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
            program=normalized_program,
            args=normalized_args,
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
        if completed.returncode == 0:
            message = data["stdout_preview"] or "Command completed successfully."
            return ToolResult.ok(data=data, message=message)
        error = data["stderr_preview"] or f"Command failed with exit code {completed.returncode}."
        return ToolResult.fail(
            error,
            code=ToolErrorCode.COMMAND_NONZERO_EXIT.value,
            data=data,
        )


def _normalize_argv(
    *,
    program: str | None,
    args: list[str] | None,
    command: str | None,
) -> dict[str, Any]:
    has_program = isinstance(program, str) and bool(program.strip())
    has_command = isinstance(command, str) and bool(command.strip())
    if has_program:
        normalized_args = _normalize_args(args)
        if not normalized_args["ok"]:
            return normalized_args
        return {"ok": True, "argv": [str(program).strip(), *normalized_args["args"]]}
    if has_command:
        command_text = str(command or "")
        if _requires_shell(command_text):
            return _failure_dict(
                SHELL_REQUIRED_MESSAGE,
                ToolErrorCode.SHELL_REQUIRED.value,
                data={"command": command_text},
            )
        try:
            argv = shlex.split(command_text, posix=True)
        except ValueError as exc:
            return _failure_dict(
                f"Unable to parse command string as argv: {exc}",
                ToolErrorCode.INVALID_ARGS.value,
                data={"command": command_text},
            )
        if not argv:
            return _failure_dict(
                "command must not be empty.",
                ToolErrorCode.INVALID_ARGS.value,
                data={"command": command_text},
            )
        return {"ok": True, "argv": argv}
    return _failure_dict(
        "program or command is required.",
        ToolErrorCode.MISSING_REQUIRED_PARAM.value,
    )


def _normalize_args(args: list[str] | None) -> dict[str, Any]:
    if args is None:
        return {"ok": True, "args": []}
    if not isinstance(args, list):
        return _failure_dict(
            "args must be an array of strings.",
            ToolErrorCode.INVALID_ARGS.value,
            data={"args": args},
        )
    result: list[str] = []
    for index, value in enumerate(args):
        if not isinstance(value, str):
            return _failure_dict(
                "args must be an array of strings.",
                ToolErrorCode.INVALID_ARGS.value,
                data={"index": index, "value": value},
            )
        result.append(value)
    return {"ok": True, "args": result}



def _resolve_cwd(workspace_root: Path, cwd: str) -> dict[str, Any]:
    resolved = PathResolver(workspace_root).resolve(cwd or ".")
    if not resolved.valid or not resolved.is_inside_workspace or resolved.is_blocked:
        return _failure_dict(
            resolved.reason or "Invalid cwd.",
            resolved.error_code,
            data=resolved.to_dict(),
        )
    if not resolved.exists:
        return _failure_dict(
            f"cwd not found: {cwd}",
            ToolErrorCode.FILE_NOT_FOUND.value,
            data=resolved.to_dict(),
        )
    if resolved.resource_type != "directory":
        return _failure_dict(
            f"cwd is not a directory: {cwd}",
            ToolErrorCode.NOT_A_DIRECTORY.value,
            data=resolved.to_dict(),
        )
    return {
        "ok": True,
        "path": Path(resolved.path_resolved),
        "cwd": resolved.workspace_relative_path or ".",
    }


def _requires_shell(command: str) -> bool:
    quote: str | None = None
    escaped = False
    index = 0
    while index < len(command):
        char = command[index]
        if escaped:
            escaped = False
            index += 1
            continue
        if char == "\\":
            escaped = True
            index += 1
            continue
        if quote is not None:
            if char == quote:
                quote = None
            index += 1
            continue
        if char in {"'", '"'}:
            quote = char
            index += 1
            continue
        pair = command[index : index + 2]
        if char in {"|", ";", "<", ">", "`", "&"} or pair in {"&&", "||", "$(", "${"}:
            return True
        if char == "%" and "%" in command[index + 1 :]:
            return True
        index += 1
    return False


def _command_data(
    *,
    command: str,
    program: str,
    args: list[str],
    cwd: str,
    purpose: str,
    timeout_seconds: int,
    duration_ms: int,
    exit_code: int | None,
    stdout: str,
    stderr: str,
    timed_out: bool,
    max_stream_chars: int = DEFAULT_STREAM_CHARS,
    preview_chars: int = DEFAULT_PREVIEW_CHARS,
) -> dict[str, Any]:
    stdout_bytes = len(stdout.encode("utf-8", errors="replace"))
    stderr_bytes = len(stderr.encode("utf-8", errors="replace"))
    stdout_text, stdout_truncated = _truncate_with_flag(stdout, max_stream_chars)
    stderr_text, stderr_truncated = _truncate_with_flag(stderr, max_stream_chars)
    stdout_preview, _ = _truncate_with_flag(stdout, preview_chars)
    stderr_preview, _ = _truncate_with_flag(stderr, preview_chars)
    stdout_summary = stdout_preview.strip()
    stderr_summary = stderr_preview.strip()
    return {
        "command": command,
        "program": program,
        "args": list(args),
        "cwd": cwd,
        "purpose": purpose,
        "exit_code": exit_code,
        "stdout": stdout_text,
        "stderr": stderr_text,
        "stdout_preview": stdout_preview,
        "stderr_preview": stderr_preview,
        "stdout_summary": stdout_summary,
        "stderr_summary": stderr_summary,
        "stdout_bytes": stdout_bytes,
        "stderr_bytes": stderr_bytes,
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
        "timed_out": timed_out,
        "timeout_seconds": timeout_seconds,
        "duration_ms": duration_ms,
    }


def _decode_output(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _truncate_with_flag(value: str, limit: int) -> tuple[str, bool]:
    text = value or ""
    if len(text) <= limit:
        return text, False
    suffix = f"... [truncated {len(text) - limit} chars]"
    if len(suffix) >= limit:
        return suffix[:limit], True
    return text[: limit - len(suffix)] + suffix, True


def _join_command(argv: Iterable[str]) -> str:
    return " ".join(shlex.quote(str(item)) for item in argv)


def _failure(result: Mapping[str, Any]) -> ToolResult:
    return ToolResult.fail(
        str(result["message"]),
        code=str(result["code"]),
        data=result.get("data"),
    )


def _failure_dict(message: str, code: str, data: Any = None) -> dict[str, Any]:
    return {"ok": False, "message": message, "code": code, "data": data}


def _workspace_root(value: str | Path | None) -> Path:
    if value is not None:
        return Path(value).resolve()
    return get_settings().workspace_root.resolve()


__all__ = [
    "CommandTool",
    "SHELL_REQUIRED_MESSAGE",
]
