from __future__ import annotations

import hashlib
import json
import re
import shlex
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol

from .base import ToolResult
from .policy import ToolPolicyDecision
from .protocol import ToolCallRequest

DEFAULT_TEXT_PREVIEW_CHARS = 240
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
_CONTENT_KEY_MARKERS = (
    "body",
    "content",
    "html",
    "output",
    "prompt",
    "response",
    "script",
    "stderr",
    "stdin",
    "stdout",
    "text",
)
_PATH_KEY_MARKERS = (
    "cwd",
    "directory",
    "dir",
    "file",
    "path",
    "target",
)
_SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|token|password|secret|authorization|cookie|set-cookie|"
    r"client[_-]?secret|access[_-]?key|refresh[_-]?token)\b(\s*[:=]\s*)([^\r\n,;]+)"
)
_BEARER_TOKEN_RE = re.compile(r"(?i)\bBearer\s+[^\s,;]+")


class ToolLogger(Protocol):
    """Best-effort developer audit hook for formal tool calls."""

    def log(
        self,
        request: ToolCallRequest,
        result: ToolResult,
        decision: ToolPolicyDecision | None = None,
    ) -> None:
        ...


class NullToolLogger:
    """No-op logger for callers that explicitly suppress developer audit logs."""

    def log(
        self,
        request: ToolCallRequest,
        result: ToolResult,
        decision: ToolPolicyDecision | None = None,
    ) -> None:
        del request, result, decision


class JsonlToolLogger:
    """Thread-safe JSONL audit logger that only persists safe tool summaries."""

    def __init__(
        self,
        logs_path: str | Path,
        *,
        preview_chars: int = DEFAULT_TEXT_PREVIEW_CHARS,
    ) -> None:
        self.logs_path = Path(logs_path)
        self.preview_chars = max(int(preview_chars), 1)
        self._write_lock = threading.Lock()
        self.records_written = 0
        self.write_error_count = 0
        self.last_write_error: str | None = None

    def log(
        self,
        request: ToolCallRequest | None,
        result: ToolResult,
        decision: ToolPolicyDecision | None = None,
    ) -> None:
        self.record(request, result, decision)

    def record(
        self,
        request: ToolCallRequest | None,
        result: ToolResult,
        decision: ToolPolicyDecision | None = None,
    ) -> bool:
        """Append one record and isolate file-system failures from ToolRuntime."""
        try:
            record = self.build_record(request, result, decision)
            serialized = json.dumps(record, ensure_ascii=False, sort_keys=True)
            with self._write_lock:
                self.logs_path.parent.mkdir(parents=True, exist_ok=True)
                with self.logs_path.open("a", encoding="utf-8") as file:
                    file.write(serialized + "\n")
                self.records_written += 1
            return True
        except Exception as exc:
            self.write_error_count += 1
            self.last_write_error = sanitize_tool_text(str(exc))
            return False

    def build_record(
        self,
        request: ToolCallRequest | None,
        result: ToolResult,
        decision: ToolPolicyDecision | None = None,
    ) -> dict[str, Any]:
        context = request.context if request is not None else None
        options = request.options if request is not None else None
        output_metadata = _mapping_at(result.metadata, "output_control")
        artifacts = _artifact_references(result.metadata, output_metadata)
        affected_resources = _affected_resources(
            decision,
            output_metadata,
            result.metadata,
        )
        raw_output_hash = output_metadata.get("raw_output_hash")
        if raw_output_hash is None and result.raw_output is not None:
            raw_output_hash = _hash_value(result.raw_output)

        metadata = {"artifacts": artifacts}
        mcp_metadata = _mcp_log_metadata(result)
        if mcp_metadata:
            metadata["mcp"] = mcp_metadata

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": "info" if result.success else "error",
            "record_type": "tool_call",
            "trace_id": getattr(context, "trace_id", None),
            "execution_id": getattr(context, "execution_id", None),
            "plan_id": getattr(context, "plan_id", None),
            "task_id": getattr(context, "task_id", None),
            "step_id": getattr(context, "step_id", None),
            "call_id": result.call_id,
            "tool_name": result.tool_name
            or (request.tool_name if request is not None else None),
            "tool_category": result.tool_category,
            "tool_namespace": result.tool_namespace,
            "provider": result.provider,
            "input_summary": summarize_tool_input(
                request.args if request is not None else {},
                tool_name=result.tool_name
                or (request.tool_name if request is not None else None),
            ),
            "options_summary": summarize_tool_options(options),
            "risk_level": decision.risk_level if decision is not None else None,
            "requires_confirmation": bool(
                decision.requires_confirmation
                if decision is not None
                else output_metadata.get("requires_confirmation", False)
            ),
            "confirmed": bool(getattr(options, "confirmed", False)),
            "confirmation_id": getattr(options, "confirmation_id", None),
            "preview_hash": getattr(options, "preview_hash", None)
            or output_metadata.get("preview_hash"),
            "dry_run": bool(getattr(options, "dry_run", False)),
            "success": bool(result.success),
            "code": result.code,
            "error_type": result.error_type,
            "retryable": bool(result.retryable),
            "duration_ms": max(int(result.duration_ms or 0), 0),
            "output_summary": summarize_tool_output(result),
            "raw_output_hash": raw_output_hash,
            "raw_output_truncated": bool(result.raw_output_truncated),
            "affected_resources": affected_resources,
            "metadata": metadata,
        }


ToolCallLogger = JsonlToolLogger


def summarize_tool_input(
    args: Mapping[str, Any] | None,
    *,
    tool_name: str | None = None,
) -> dict[str, Any]:
    values = dict(args or {})
    summary: dict[str, Any] = {
        "tool_name": tool_name,
        "parameter_keys": sorted(
            str(key) for key in values if not _is_sensitive_key(str(key))
        ),
        "parameter_count": len(values),
    }
    paths = _path_summary(values)
    if paths:
        summary["paths"] = paths

    content = _content_summary(values)
    if content:
        summary["content"] = content

    command = values.get("command")
    if isinstance(command, (str, bytes)):
        summary["command"] = _command_summary(command)
    query = values.get("query")
    if isinstance(query, (str, bytes)):
        summary["search"] = {
            "query_chars": _text_length(query),
            "query_hash": _hash_value(query),
            "provider": _safe_scalar(values.get("provider")),
        }

    mcp_server = values.get("server") or values.get("server_name") or values.get("mcp_server")
    mcp_tool = values.get("mcp_tool") or values.get("tool")
    if mcp_server is not None or mcp_tool is not None:
        summary["mcp"] = {
            "server": _short_safe_text(mcp_server),
            "tool": _short_safe_text(mcp_tool),
            "parameter_keys": _nested_parameter_keys(values.get("parameters")),
        }
    return summary


def summarize_tool_options(options: Any) -> dict[str, Any]:
    if options is None:
        return {}
    capability_keys = (
        "allow_read_workspace",
        "allow_write_workspace",
        "allow_network",
        "allow_command",
        "allow_shell_command",
        "allow_mcp",
    )
    return {
        "timeout_seconds": getattr(options, "timeout_seconds", None),
        "dry_run": bool(getattr(options, "dry_run", False)),
        "require_confirmation": getattr(options, "require_confirmation", None),
        "approval_scope": getattr(options, "approval_scope", None),
        "observation_mode": getattr(options, "observation_mode", None),
        "max_output_chars": getattr(options, "max_output_chars", None),
        "max_raw_output_chars": getattr(options, "max_raw_output_chars", None),
        "max_observation_chars": getattr(options, "max_observation_chars", None),
        "capabilities": {
            key: bool(getattr(options, key, False))
            for key in capability_keys
        },
    }


def summarize_tool_output(result: ToolResult) -> dict[str, Any]:
    return {
        "data": _summarize_value(result.data),
        "message": _text_metadata(result.message),
        "error": _text_metadata(result.error),
        "raw_output_present": result.raw_output is not None,
        "raw_output_chars": _text_length(result.raw_output)
        if result.raw_output is not None
        else 0,
    }


def _mcp_log_metadata(result: ToolResult) -> dict[str, Any]:
    gateway = _mapping_at(result.metadata, "mcp_gateway")
    if not gateway:
        return {}
    command_summary = gateway.get("command_summary")
    if isinstance(command_summary, Mapping):
        safe_command_summary = {
            "command": _short_safe_text(command_summary.get("command")),
            "args_count": command_summary.get("args_count"),
            "cwd": _short_safe_text(command_summary.get("cwd")),
            "passEnv": bool(command_summary.get("passEnv", False)),
        }
    else:
        safe_command_summary = {}
    stderr_preview = gateway.get("stderr_preview")
    return {
        "server_id": _short_safe_text(gateway.get("server_id")),
        "remote_tool_name": _short_safe_text(gateway.get("remote_tool_name")),
        "transport": _short_safe_text(gateway.get("transport")),
        "command_summary": safe_command_summary,
        "argument_keys": [
            str(key)
            for key in gateway.get("argument_keys", [])
            if not _is_sensitive_key(str(key))
        ],
        "schema_hash": _short_safe_text(gateway.get("schema_hash")),
        "stderr_preview": _text_metadata(stderr_preview)
        if stderr_preview
        else None,
        "output_truncated": bool(gateway.get("output_truncated", False)),
        "fallback_performed": bool(gateway.get("fallback_performed", False)),
    }


def sanitize_tool_text(value: Any) -> str:
    text = str(value or "")
    text = _SENSITIVE_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}***",
        text,
    )
    return _BEARER_TOKEN_RE.sub("Bearer ***", text)


def _path_summary(values: Mapping[str, Any]) -> dict[str, Any]:
    paths: dict[str, Any] = {}
    for key, value in values.items():
        normalized = str(key).lower().replace("-", "_")
        if _is_sensitive_key(normalized) or not any(
            marker in normalized for marker in _PATH_KEY_MARKERS
        ):
            continue
        if isinstance(value, (str, bytes)):
            paths[str(key)] = _short_safe_text(value)
        elif isinstance(value, (list, tuple, set, frozenset)):
            paths[str(key)] = [
                _short_safe_text(item)
                for item in list(value)[:50]
                if isinstance(item, (str, bytes))
            ]
    return paths


def _content_summary(values: Mapping[str, Any]) -> dict[str, Any]:
    content: dict[str, Any] = {}
    for key, value in values.items():
        normalized = str(key).lower().replace("-", "_")
        if _is_sensitive_key(normalized) or not any(
            marker in normalized for marker in _CONTENT_KEY_MARKERS
        ):
            continue
        content[str(key)] = _text_metadata(value)
    return content


def _command_summary(value: str | bytes) -> dict[str, Any]:
    text = _to_text(value)
    try:
        parts = shlex.split(text, posix=False)
    except ValueError:
        parts = text.split()
    return {
        "program": _short_safe_text(parts[0]) if parts else None,
        "args_count": max(len(parts) - 1, 0),
        "chars": len(text),
        "sha256": _hash_value(text),
    }


def _summarize_value(value: Any, *, key: str = "") -> Any:
    if _is_sensitive_key(key):
        return "<redacted>"
    normalized = key.lower().replace("-", "_")
    if any(marker in normalized for marker in _CONTENT_KEY_MARKERS):
        return _text_metadata(value)
    if isinstance(value, Mapping):
        return {
            str(item_key): _summarize_value(item, key=str(item_key))
            for item_key, item in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return {
            "type": "list",
            "items": len(value),
            "item_summaries": [
                _summarize_value(item, key=key)
                for item in list(value)[:10]
            ],
        }
    if isinstance(value, str):
        return _text_metadata(value)
    if isinstance(value, bytes):
        return _text_metadata(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _text_metadata(str(value))


def _artifact_references(
    metadata: Mapping[str, Any] | None,
    output_metadata: Mapping[str, Any],
) -> list[str]:
    refs: list[str] = []
    for key in ("artifact_ref", "raw_ref"):
        value = output_metadata.get(key)
        if isinstance(value, str) and value:
            refs.append(_short_safe_text(value))
    artifacts = _mapping_at(metadata or {}, "artifacts")
    for value in artifacts.values():
        if isinstance(value, str):
            refs.append(_short_safe_text(value))
    if isinstance((metadata or {}).get("artifacts"), (list, tuple, set, frozenset)):
        for value in (metadata or {}).get("artifacts", []):
            if isinstance(value, str):
                refs.append(_short_safe_text(value))
    return list(dict.fromkeys(refs))


def _affected_resources(
    decision: ToolPolicyDecision | None,
    output_metadata: Mapping[str, Any],
    metadata: Mapping[str, Any] | None,
) -> list[str]:
    values = (
        decision.affected_resources
        if decision is not None
        else output_metadata.get("affected_resources")
        or (metadata or {}).get("affected_resources")
        or []
    )
    if not isinstance(values, (list, tuple, set, frozenset)):
        return []
    return [_short_safe_text(value) for value in values if isinstance(value, (str, bytes))]


def _mapping_at(value: Mapping[str, Any] | Any, key: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    nested = value.get(key)
    return dict(nested) if isinstance(nested, Mapping) else {}


def _nested_parameter_keys(value: Any) -> list[str]:
    if not isinstance(value, Mapping):
        return []
    return sorted(
        str(key) for key in value if not _is_sensitive_key(str(key))
    )


def _text_metadata(value: Any) -> dict[str, Any]:
    text = _to_text(value)
    return {
        "chars": len(text),
        "sha256": _hash_value(text),
    }


def _text_length(value: Any) -> int:
    return len(_to_text(value))


def _hash_value(value: Any) -> str:
    return hashlib.sha256(_to_text(value).encode("utf-8")).hexdigest()


def _to_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return str(value)


def _short_safe_text(value: Any) -> str | None:
    if value is None:
        return None
    text = sanitize_tool_text(_to_text(value))
    if len(text) <= DEFAULT_TEXT_PREVIEW_CHARS:
        return text
    return text[:DEFAULT_TEXT_PREVIEW_CHARS] + "..."


def _safe_scalar(value: Any) -> Any:
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _short_safe_text(value)


def _is_sensitive_key(key: str) -> bool:
    normalized = str(key or "").lower().replace("-", "_")
    return any(marker in normalized for marker in _SENSITIVE_KEY_MARKERS)


__all__ = [
    "DEFAULT_TEXT_PREVIEW_CHARS",
    "JsonlToolLogger",
    "NullToolLogger",
    "ToolCallLogger",
    "ToolLogger",
    "sanitize_tool_text",
    "summarize_tool_input",
    "summarize_tool_options",
    "summarize_tool_output",
]
