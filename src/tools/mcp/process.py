from __future__ import annotations

import os
import queue
import shutil
import subprocess
import threading
from dataclasses import dataclass, field as dataclass_field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..errors import ToolErrorCode
from .protocol import (
    MCPConnectionInfo,
    MCPProtocolError,
    MCPResolvedServerConfig,
    MCPServerState,
    MCP_TRANSPORT_STDIO,
)


DEFAULT_STDERR_PREVIEW_CHARS = 4000
MINIMAL_ENV_KEYS = (
    "APPDATA",
    "PATH",
    "PATHEXT",
    "PROGRAMDATA",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "TEMP",
    "TMP",
    "HOME",
    "LOCALAPPDATA",
    "USERPROFILE",
)


@dataclass
class MCPStdioProcess:
    config: MCPResolvedServerConfig
    stderr_preview_chars: int = DEFAULT_STDERR_PREVIEW_CHARS
    process: subprocess.Popen | None = None
    stdout_queue: queue.Queue[str | None] = dataclass_field(default_factory=queue.Queue)
    stderr_queue: queue.Queue[str | None] = dataclass_field(default_factory=queue.Queue)
    _stdout_thread: threading.Thread | None = None
    _stderr_thread: threading.Thread | None = None
    _stderr_buffer: list[str] = dataclass_field(default_factory=list)
    _started_at: str | None = None
    _stopped_at: str | None = None

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(self) -> MCPConnectionInfo:
        if self.config.transport != MCP_TRANSPORT_STDIO:
            raise MCPProtocolError(
                ToolErrorCode.MCP_TRANSPORT_NOT_SUPPORTED.value,
                "Only local stdio MCP transport is supported in V1.",
                server_id=self.config.server_id,
                details={"transport": self.config.transport},
            )
        if not self.config.enabled:
            raise MCPProtocolError(
                ToolErrorCode.MCP_SERVER_DISABLED.value,
                "MCP server is disabled.",
                server_id=self.config.server_id,
            )
        if not self.config.command:
            raise MCPProtocolError(
                ToolErrorCode.MCP_COMMAND_NOT_FOUND.value,
                "MCP stdio command is not configured.",
                server_id=self.config.server_id,
            )
        if self.running:
            return self.connection_info(state=MCPServerState.STARTING.value)

        env = build_stdio_environment(self.config)
        command_path = resolve_command(self.config.command, env=env)
        if command_path is None:
            raise MCPProtocolError(
                ToolErrorCode.MCP_COMMAND_NOT_FOUND.value,
                f"MCP command not found: {self.config.command}",
                server_id=self.config.server_id,
                details={"command": self.config.command},
            )
        cwd = self.config.cwd or os.getcwd()
        try:
            self.process = subprocess.Popen(
                [command_path, *self.config.args],
                cwd=cwd,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=False,
                bufsize=1,
            )
        except FileNotFoundError as exc:
            raise MCPProtocolError(
                ToolErrorCode.MCP_COMMAND_NOT_FOUND.value,
                f"MCP command not found: {self.config.command}",
                server_id=self.config.server_id,
                details={"command": self.config.command},
            ) from exc
        except Exception as exc:
            raise MCPProtocolError(
                ToolErrorCode.MCP_PROCESS_START_FAILED.value,
                "Failed to start MCP stdio process.",
                server_id=self.config.server_id,
                details={"error_type": type(exc).__name__},
            ) from exc

        self._started_at = _utc_now()
        self._stdout_thread = threading.Thread(
            target=_pump_lines,
            args=(self.process.stdout, self.stdout_queue),
            name=f"mcp-stdout-{self.config.server_id}",
            daemon=True,
        )
        self._stderr_thread = threading.Thread(
            target=self._pump_stderr,
            name=f"mcp-stderr-{self.config.server_id}",
            daemon=True,
        )
        self._stdout_thread.start()
        self._stderr_thread.start()
        return self.connection_info(state=MCPServerState.STARTING.value)

    def write_line(self, line: str) -> None:
        if self.process is None or self.process.stdin is None or self.process.poll() is not None:
            raise MCPProtocolError(
                ToolErrorCode.MCP_PROCESS_EXITED.value,
                "MCP stdio process is not running.",
                server_id=self.config.server_id,
                details={"returncode": None if self.process is None else self.process.poll()},
            )
        try:
            self.process.stdin.write(line.rstrip("\r\n") + "\n")
            self.process.stdin.flush()
        except Exception as exc:
            raise MCPProtocolError(
                ToolErrorCode.MCP_TRANSPORT_ERROR.value,
                "Failed to write MCP stdio request.",
                server_id=self.config.server_id,
                details={"error_type": type(exc).__name__},
            ) from exc

    def read_stdout_line(self, timeout_seconds: float) -> str | None:
        try:
            return self.stdout_queue.get(timeout=max(float(timeout_seconds), 0.0))
        except queue.Empty:
            return None

    def stderr_preview(self) -> str | None:
        text = "".join(self._stderr_buffer)
        if not text:
            return None
        if len(text) <= self.stderr_preview_chars:
            return text
        return text[-self.stderr_preview_chars :]

    def stop(self, *, timeout_seconds: float = 2.0) -> MCPConnectionInfo:
        process = self.process
        if process is None:
            self._stopped_at = _utc_now()
            return self.connection_info(state=MCPServerState.STOPPED.value)
        try:
            if process.stdin is not None:
                try:
                    process.stdin.close()
                except Exception:
                    pass
            if process.poll() is None:
                try:
                    process.wait(timeout=max(float(timeout_seconds), 0.0))
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        process.wait(timeout=1.0)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=1.0)
        finally:
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:
                        pass
            self._stopped_at = _utc_now()
        return self.connection_info(state=MCPServerState.STOPPED.value)

    def connection_info(self, *, state: str | None = None) -> MCPConnectionInfo:
        return MCPConnectionInfo(
            server_id=self.config.server_id,
            state=state or (
                MCPServerState.STARTING.value if self.running else MCPServerState.STOPPED.value
            ),
            transport=self.config.transport,
            pid=getattr(self.process, "pid", None),
            started_at=self._started_at,
            stopped_at=self._stopped_at,
            metadata={
                "command_summary": command_summary(self.config),
                "stderr_preview": self.stderr_preview(),
            },
        )

    def _pump_stderr(self) -> None:
        stream = self.process.stderr if self.process is not None else None
        if stream is None:
            self.stderr_queue.put(None)
            return
        for line in stream:
            self._stderr_buffer.append(line)
            self._trim_stderr_buffer()
            self.stderr_queue.put(line)
        self.stderr_queue.put(None)

    def _trim_stderr_buffer(self) -> None:
        text = "".join(self._stderr_buffer)
        if len(text) <= self.stderr_preview_chars:
            return
        self._stderr_buffer = [text[-self.stderr_preview_chars :]]


def build_stdio_environment(config: MCPResolvedServerConfig) -> dict[str, str]:
    if config.pass_env:
        values = dict(os.environ)
    else:
        allowed = {key.upper() for key in MINIMAL_ENV_KEYS}
        values = {
            key: value
            for key, value in os.environ.items()
            if key.upper() in allowed
        }
    values.update({str(key): str(value) for key, value in config.env.items()})
    return values


def resolve_command(command: str, *, env: dict[str, str]) -> str | None:
    candidate = str(command or "").strip()
    if not candidate:
        return None
    path = Path(candidate)
    if path.is_absolute() or any(separator in candidate for separator in ("/", "\\")):
        return str(path) if path.exists() else None
    found = shutil.which(candidate, path=env.get("PATH"))
    return found


def command_summary(config: MCPResolvedServerConfig) -> dict[str, Any]:
    return {
        "command": Path(str(config.command or "")).name or None,
        "args_count": len(config.args),
        "cwd": config.cwd,
        "passEnv": config.pass_env,
        "env_keys": sorted(config.env),
    }


def _pump_lines(stream: Any, output: queue.Queue[str | None]) -> None:
    if stream is None:
        output.put(None)
        return
    for line in stream:
        output.put(line.rstrip("\r\n"))
    output.put(None)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "DEFAULT_STDERR_PREVIEW_CHARS",
    "MCPStdioProcess",
    "MINIMAL_ENV_KEYS",
    "build_stdio_environment",
    "command_summary",
    "resolve_command",
]
