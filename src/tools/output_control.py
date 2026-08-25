from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .base import _json_safe
from .file_tools.deletion import build_delete_preview
from .file_tools.mutation import build_file_mutation_preview
from .file_tools.patching import build_patch_preview
from .file_tools.path_resolver import PathResolver
from .policy import ToolPolicyDecision
from .protocol import ToolCallOptions, ToolCallRequest
from .registry import ToolSpec
from .shell_command_tool import build_shell_command_preview


DEFAULT_MAX_OUTPUT_CHARS = 12000
DEFAULT_MAX_RAW_OUTPUT_CHARS = 50000
DEFAULT_MAX_OBSERVATION_CHARS = 16000
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
_CONTENT_KEYS = {
    "body",
    "code",
    "content",
    "input",
    "prompt",
    "script",
    "stdin",
    "text",
}


@dataclass(frozen=True)
class OutputLimits:
    max_output_chars: int
    max_raw_output_chars: int
    max_observation_chars: int


@dataclass
class PreviewData:
    payload: dict[str, Any] = field(default_factory=dict)
    preview_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "payload": _json_safe(self.payload),
            "preview_hash": self.preview_hash,
        }


class OutputController:
    """Apply shared output limits and produce safe, repeatable previews."""

    def __init__(
        self,
        *,
        max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
        max_raw_output_chars: int = DEFAULT_MAX_RAW_OUTPUT_CHARS,
        max_observation_chars: int = DEFAULT_MAX_OBSERVATION_CHARS,
        default_observation_mode: str = "standard",
    ) -> None:
        self.defaults = OutputLimits(
            max_output_chars=_non_negative(max_output_chars),
            max_raw_output_chars=_non_negative(max_raw_output_chars),
            max_observation_chars=_non_negative(max_observation_chars),
        )
        self.default_observation_mode = (
            str(default_observation_mode or "standard").strip().lower()
        )
        if self.default_observation_mode not in {"minimal", "standard", "full"}:
            self.default_observation_mode = "standard"

    def resolve_limits(
        self,
        spec: ToolSpec,
        options: ToolCallOptions,
    ) -> OutputLimits:
        return OutputLimits(
            max_output_chars=_first_limit(
                options.max_output_chars,
                spec.max_output_chars,
                self.defaults.max_output_chars,
            ),
            max_raw_output_chars=_first_limit(
                options.max_raw_output_chars,
                self.defaults.max_raw_output_chars,
            ),
            max_observation_chars=_first_limit(
                options.max_observation_chars,
                self.defaults.max_observation_chars,
            ),
        )

    def build_preview(
        self,
        spec: ToolSpec,
        request: ToolCallRequest,
        decision: ToolPolicyDecision | None = None,
    ) -> PreviewData:
        limits = self.resolve_limits(spec, request.options)
        affected_resources = list(decision.affected_resources) if decision else []
        payload = {
            "tool_name": spec.name,
            "tool_category": spec.category,
            "tool_namespace": spec.namespace,
            "risk_level": decision.risk_level if decision else spec.risk_level,
            "requires_confirmation": (
                bool(
                    spec.requires_confirmation
                    or (decision.risk_level if decision else spec.risk_level) == "high"
                    or (decision.requires_confirmation if decision else False)
                    or request.options.require_confirmation is True
                )
            ),
            "affected_resources": affected_resources,
            "args": _summarize_args(request.args),
            "resource_snapshot": _snapshot_resources(
                request.context.workspace_root,
                affected_resources,
            ),
        }
        if spec.metadata.get("preview_kind") == "file_write":
            payload["write"] = _file_write_preview(
                request.context.workspace_root,
                request.args,
                payload["resource_snapshot"],
                payload["requires_confirmation"],
            )
        if spec.metadata.get("preview_kind") == "file_patch":
            payload["patch"] = _file_patch_preview(
                request.context.workspace_root,
                request.args,
                payload["requires_confirmation"],
            )
            if isinstance(payload["patch"], Mapping) and "preview_error" in payload["patch"]:
                payload["preview_error"] = payload["patch"]["preview_error"]
        if spec.metadata.get("preview_kind") == "file_mutation":
            payload["mutation"] = _file_mutation_preview(
                spec,
                request.context.workspace_root,
                request.args,
                payload["requires_confirmation"],
            )
            if (
                isinstance(payload["mutation"], Mapping)
                and "preview_error" in payload["mutation"]
            ):
                payload["preview_error"] = payload["mutation"]["preview_error"]
        if spec.metadata.get("preview_kind") == "file_delete":
            payload["delete"] = _file_delete_preview(
                request.context.workspace_root,
                request.args,
                payload["requires_confirmation"],
            )
            if (
                isinstance(payload["delete"], Mapping)
                and "preview_error" in payload["delete"]
            ):
                payload["preview_error"] = payload["delete"]["preview_error"]
        if spec.metadata.get("preview_kind") == "shell_command":
            payload["shell_command"] = _shell_command_preview(
                request.context.workspace_root,
                request.args,
                request.options,
                payload["requires_confirmation"],
            )
            if (
                isinstance(payload["shell_command"], Mapping)
                and "preview_error" in payload["shell_command"]
            ):
                payload["preview_error"] = payload["shell_command"]["preview_error"]
        if spec.metadata.get("preview_kind") == "web_search":
            payload["web_search"] = _web_search_preview(
                spec,
                request.args,
                request.options,
            )
        if spec.metadata.get("preview_kind") == "mcp":
            payload["mcp"] = _mcp_preview(
                spec,
                request.args,
                request.options,
                decision,
            )
        canonical = _canonical_json(payload)
        return PreviewData(payload=payload, preview_hash=_sha256(canonical))

    def apply(
        self,
        result: Any,
        spec: ToolSpec,
        request: ToolCallRequest,
        *,
        preview: PreviewData | None = None,
        decision: ToolPolicyDecision | None = None,
    ):
        controlled = result
        limits = self.resolve_limits(spec, request.options)
        output_metadata = dict(controlled.metadata.get("output_control", {}))

        controlled.data = _limit_data(controlled.data, limits.max_output_chars)
        controlled.message, message_truncated = _limit_with_flag(
            controlled.message,
            limits.max_output_chars,
        )
        if message_truncated:
            output_metadata["message_truncated"] = True

        raw_meta = _control_raw_output(
            controlled,
            limits.max_raw_output_chars,
        )
        output_metadata.update(raw_meta)

        summary = _safe_summary(controlled.data, limits.max_observation_chars)
        output_metadata["data_summary"] = summary
        output_metadata["observation_mode"] = (
            request.options.observation_mode
            or (
                self.default_observation_mode
                if spec.default_observation_mode == "standard"
                else spec.default_observation_mode
            )
        )

        if preview is not None:
            output_metadata["preview_hash"] = preview.preview_hash
            output_metadata["preview"] = preview.payload
            output_metadata["affected_resources"] = list(
                decision.affected_resources if decision else preview.payload.get("affected_resources", [])
            )
            output_metadata["requires_confirmation"] = bool(
                decision.requires_confirmation if decision else preview.payload.get("requires_confirmation")
            )

        controlled.metadata["output_control"] = output_metadata
        return controlled


def truncate_text(value: Any, limit: int) -> str:
    text = "" if value is None else str(value)
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    suffix = f"... [truncated {len(text) - limit} chars]"
    if len(suffix) >= limit:
        return suffix[:limit]
    return text[: limit - len(suffix)] + suffix


def _control_raw_output(result: Any, limit: int) -> dict[str, Any]:
    if result.raw_output is None:
        return {}
    if isinstance(result.raw_output, bytes):
        serialized = result.raw_output.decode("utf-8", errors="replace")
    elif isinstance(result.raw_output, str):
        serialized = result.raw_output
    else:
        serialized = _canonical_json(_json_safe(result.raw_output))
    output_hash = _sha256(serialized)
    output_chars = len(serialized)
    output_bytes = len(serialized.encode("utf-8"))
    metadata = {
        "raw_output_chars": output_chars,
        "raw_output_bytes": output_bytes,
        "raw_output_hash": output_hash,
    }
    if output_chars > limit:
        result.raw_output = truncate_text(serialized, limit)
        result.raw_output_truncated = True
        artifact_ref = f"artifact://tool-output/{output_hash}"
        metadata["artifact_ref"] = artifact_ref
        metadata["raw_ref"] = artifact_ref
    else:
        result.raw_output_truncated = bool(result.raw_output_truncated)
    return metadata


def _limit_data(value: Any, limit: int) -> Any:
    if limit <= 0:
        return None
    serialized = _canonical_json(_json_safe(value))
    if len(serialized) <= limit:
        return value
    return _limit_value(_json_safe(value), limit)


def _limit_value(value: Any, limit: int) -> Any:
    if isinstance(value, str):
        return truncate_text(value, limit)
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        item_limit = max(limit // max(len(value), 1), 1)
        for key, item in value.items():
            result[str(key)] = _limit_value(item, item_limit)
        return result
    if isinstance(value, list):
        result: list[Any] = []
        for item in value:
            candidate = _limit_value(item, limit)
            result.append(candidate)
            if len(_canonical_json(result)) > limit:
                result.pop()
                result.append(f"<truncated {len(value) - len(result)} items>")
                break
        return result
    return value


def _summarize_args(args: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): _summarize_argument(str(key), value)
        for key, value in args.items()
    }


def _summarize_argument(key: str, value: Any) -> Any:
    normalized = key.lower().replace("-", "_")
    if any(marker in normalized for marker in _SENSITIVE_KEY_MARKERS):
        return "<redacted>"
    if normalized in _CONTENT_KEYS and isinstance(value, (str, bytes)):
        text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
        return {
            "chars": len(text),
            "sha256": _sha256(text),
        }
    if isinstance(value, str):
        return truncate_text(value, 512)
    if isinstance(value, Mapping):
        return {
            str(item_key): _summarize_argument(str(item_key), item_value)
            for item_key, item_value in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_summarize_argument(key, item) for item in list(value)[:50]]
    return _json_safe(value)


def _snapshot_resources(workspace_root: str, resources: list[str]) -> list[dict[str, Any]]:
    root = Path(workspace_root).resolve(strict=False)
    snapshots: list[dict[str, Any]] = []
    for resource in resources:
        path = Path(resource)
        if not path.is_absolute():
            path = root / path
        path = path.resolve(strict=False)
        item: dict[str, Any] = {
            "path": resource,
            "exists": path.exists(),
        }
        if path.exists():
            stat = path.stat()
            item.update(
                {
                    "is_file": path.is_file(),
                    "is_dir": path.is_dir(),
                }
            )
            if path.is_file():
                item["size_bytes"] = stat.st_size
                item["mtime_ns"] = stat.st_mtime_ns
                if stat.st_size <= 1024 * 1024:
                    item["sha256"] = _sha256(path.read_bytes())
            else:
                item["size_bytes"] = None
        snapshots.append(item)
    return snapshots


def _file_write_preview(
    workspace_root: str,
    args: Mapping[str, Any],
    resource_snapshot: list[dict[str, Any]],
    requires_confirmation: bool,
) -> dict[str, Any]:
    content = args.get("content", "")
    content_text = content if isinstance(content, str) else str(content)
    encoding = str(args.get("encoding") or "utf-8")
    try:
        content_bytes = content_text.encode(encoding, errors="replace")
    except LookupError:
        content_bytes = content_text.encode("utf-8", errors="replace")
    target_arg = args.get("path") or args.get("file_path") or ""
    mode = args.get("write_mode")
    if not mode:
        mode = "overwrite" if args.get("overwrite") is True else "create"
    resolved = PathResolver(workspace_root).resolve(str(target_arg)) if target_arg else None
    snapshot = resource_snapshot[0] if resource_snapshot else {}
    old_size = snapshot.get("size_bytes") if snapshot.get("exists") else 0
    new_size = len(content_bytes)
    if str(mode) == "append":
        try:
            new_size += int(old_size or 0)
        except (TypeError, ValueError):
            pass
    return {
        "path": resolved.workspace_relative_path if resolved is not None else None,
        "exists": bool(snapshot.get("exists", False)),
        "write_mode": str(mode),
        "old_size_bytes": old_size,
        "new_size_bytes": new_size,
        "content_hash": _sha256(content_bytes),
        "content_size_bytes": len(content_bytes),
        "before_hash": snapshot.get("sha256"),
        "content_preview": truncate_text(content_text, 300),
        "content_truncated": len(content_text) > 300,
        "requires_confirmation": bool(requires_confirmation),
    }


def _file_patch_preview(
    workspace_root: str,
    args: Mapping[str, Any],
    requires_confirmation: bool,
) -> dict[str, Any]:
    patches = args.get("patches")
    if not isinstance(patches, list):
        return {
            "path": args.get("path"),
            "requires_confirmation": bool(requires_confirmation),
            "preview_error": {
                "code": "invalid_args",
                "message": "patches must be an array",
                "data": {"patches": patches},
            },
        }
    return build_patch_preview(
        path=str(args.get("path") or ""),
        patches=patches,
        encoding=str(args.get("encoding") or "utf-8"),
        workspace_root=workspace_root,
        requires_confirmation=requires_confirmation,
    )


def _file_mutation_preview(
    spec: ToolSpec,
    workspace_root: str,
    args: Mapping[str, Any],
    requires_confirmation: bool,
) -> dict[str, Any]:
    operation = str(spec.metadata.get("mutation_operation") or "").strip()
    return build_file_mutation_preview(
        operation=operation,
        args=dict(args),
        workspace_root=workspace_root,
        requires_confirmation=requires_confirmation,
    )


def _file_delete_preview(
    workspace_root: str,
    args: Mapping[str, Any],
    requires_confirmation: bool,
) -> dict[str, Any]:
    return build_delete_preview(
        args=args,
        workspace_root=workspace_root,
        requires_confirmation=requires_confirmation,
    )


def _shell_command_preview(
    workspace_root: str,
    args: Mapping[str, Any],
    options: ToolCallOptions,
    requires_confirmation: bool,
) -> dict[str, Any]:
    return build_shell_command_preview(
        command=str(args.get("command") or ""),
        shell=str(args.get("shell") or "powershell"),
        cwd=str(args.get("cwd") or "."),
        purpose=str(args.get("purpose") or ""),
        timeout_seconds=int(args.get("timeout_seconds") or 30),
        network_required=bool(args.get("network_required", False)),
        writes_files=bool(args.get("writes_files", False)),
        target_paths=_string_list(args.get("target_paths")),
        workspace_root=workspace_root,
        tool_call_options=options,
        requires_confirmation=requires_confirmation,
    )


def _web_search_preview(
    spec: ToolSpec,
    args: Mapping[str, Any],
    options: ToolCallOptions,
) -> dict[str, Any]:
    provider = str(args.get("provider") or spec.metadata.get("provider") or "auto")
    auto_order = spec.metadata.get("auto_order")
    if not isinstance(auto_order, list):
        auto_order = ["search_api", "model_builtin"]
    return {
        "query": str(args.get("query") or ""),
        "provider_route": provider,
        "auto_order": [str(item) for item in auto_order],
        "max_results": args.get("max_results", 5),
        "search_depth": str(args.get("search_depth") or "basic"),
        "include_answer": bool(args.get("include_answer", False)),
        "include_raw_content": bool(args.get("include_raw_content", False)),
        "allow_network": bool(options.allow_network),
        "estimated_timeout": options.timeout_seconds or spec.timeout_seconds,
    }


def _mcp_preview(
    spec: ToolSpec,
    args: Mapping[str, Any],
    options: ToolCallOptions,
    decision: ToolPolicyDecision | None,
) -> dict[str, Any]:
    metadata = dict(spec.metadata or {})
    return {
        "server_id": metadata.get("server_id"),
        "remote_tool_name": metadata.get("remote_tool_name"),
        "transport": metadata.get("transport", "stdio"),
        "arguments_summary": _summarize_args(args),
        "argument_keys": sorted(str(key) for key in args),
        "risk_level": decision.risk_level if decision is not None else spec.risk_level,
        "requires_confirmation": bool(
            spec.requires_confirmation
            or (decision.risk_level if decision is not None else spec.risk_level) == "high"
            or (decision.requires_confirmation if decision is not None else False)
            or options.require_confirmation is True
        ),
        "timeout": options.timeout_seconds or spec.timeout_seconds,
        "remote_simulation_performed": False,
        "dry_run_scope": "local_precheck_only",
    }


def _safe_summary(value: Any, limit: int) -> str:
    sanitized = _sanitize_preview(value)
    return truncate_text(_canonical_json(sanitized), limit)


def _sanitize_preview(value: Any, key: str = "") -> Any:
    normalized = key.lower().replace("-", "_")
    if any(marker in normalized for marker in _SENSITIVE_KEY_MARKERS):
        return "<redacted>"
    if normalized in _CONTENT_KEYS and isinstance(value, (str, bytes)):
        text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
        return {"chars": len(text), "sha256": _sha256(text)}
    if isinstance(value, Mapping):
        return {
            str(item_key): _sanitize_preview(item_value, str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_sanitize_preview(item, key) for item in value]
    if isinstance(value, str):
        return truncate_text(value, 512)
    return _json_safe(value)


def _limit_with_flag(value: Any, limit: int) -> tuple[str, bool]:
    text = "" if value is None else str(value)
    truncated = len(text) > limit
    return truncate_text(text, limit), truncated


def _first_limit(*values: int | None) -> int:
    for value in values:
        if value is not None:
            return _non_negative(value)
    return 0


def _string_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set, frozenset)):
        return [str(item) for item in value]
    if isinstance(value, str) and value.strip():
        return [value]
    return []


def _non_negative(value: int) -> int:
    return max(int(value), 0)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _json_safe(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _sha256(value: str | bytes) -> str:
    payload = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(payload).hexdigest()


__all__ = [
    "DEFAULT_MAX_OBSERVATION_CHARS",
    "DEFAULT_MAX_OUTPUT_CHARS",
    "DEFAULT_MAX_RAW_OUTPUT_CHARS",
    "OutputController",
    "OutputLimits",
    "PreviewData",
    "truncate_text",
]
