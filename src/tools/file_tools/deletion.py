from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from src.core.config import get_settings
from src.tools.base import ToolResult, _json_safe
from src.tools.errors import ToolErrorCode

from .path_resolver import PathResolver, ResolvedPath


@dataclass(frozen=True)
class DeleteTarget:
    path: str
    resolved_path: str
    size_bytes: int
    mtime_ns: int
    content_hash: str
    existed: bool = True
    is_sensitive: bool = False

    def to_data(self) -> dict[str, Any]:
        return _json_safe(self)


@dataclass(frozen=True)
class DeletePlan:
    targets: list[DeleteTarget]
    total_size_bytes: int

    def to_preview(self, *, requires_confirmation: bool) -> dict[str, Any]:
        return {
            "paths": [target.path for target in self.targets],
            "targets": [target.to_data() for target in self.targets],
            "total_count": len(self.targets),
            "total_size_bytes": self.total_size_bytes,
            "requires_confirmation": bool(requires_confirmation),
        }


class DeleteFileTool:
    """Delete one or more explicit workspace files."""

    def run(
        self,
        path: str | None = None,
        file_paths: list[str] | None = None,
        *,
        workspace_root: str | Path | None = None,
    ) -> ToolResult:
        plan_result = build_delete_plan(
            {"path": path, "file_paths": file_paths},
            workspace_root=workspace_root,
        )
        if not plan_result["ok"]:
            return _tool_failure(plan_result)

        plan: DeletePlan = plan_result["plan"]
        deleted: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        pending = [target.to_data() for target in plan.targets]

        for target in plan.targets:
            target_path = Path(target.resolved_path)
            try:
                target_path.unlink()
            except OSError as exc:
                failed.append({**target.to_data(), "delete_error": str(exc)})
                break
            deleted.append(target.to_data())
            pending = pending[1:]

        data = {
            "paths": [target.path for target in plan.targets],
            "deleted_files": deleted,
            "deleted_paths": [item["path"] for item in deleted],
            "deleted_count": len(deleted),
            "failed_files": failed,
            "failed_paths": [item["path"] for item in failed],
            "failed_count": len(failed),
            "pending_files": pending,
            "skipped": pending,
            "total_count": len(plan.targets),
            "total_size_bytes": plan.total_size_bytes,
        }
        if failed:
            return ToolResult.fail(
                "delete_file failed during execution; some files may already be deleted.",
                code=ToolErrorCode.FILE_DELETE_FAILED.value,
                data=data,
            )
        return ToolResult.ok(
            data=data,
            message=f"Deleted {len(deleted)} file(s).",
        )


def build_delete_preview(
    *,
    args: Mapping[str, Any],
    workspace_root: str | Path | None = None,
    requires_confirmation: bool = False,
) -> dict[str, Any]:
    result = build_delete_plan(args, workspace_root=workspace_root)
    if not result["ok"]:
        return {
            "paths": _raw_paths(args),
            "requires_confirmation": bool(requires_confirmation),
            "preview_error": {
                "code": result["code"],
                "message": result["message"],
                "data": result.get("data"),
            },
        }
    plan: DeletePlan = result["plan"]
    return plan.to_preview(requires_confirmation=requires_confirmation)


def build_delete_plan(
    args: Mapping[str, Any],
    *,
    workspace_root: str | Path | None = None,
) -> dict[str, Any]:
    normalized = _normalize_delete_targets(args)
    if not normalized["ok"]:
        return normalized

    raw_paths: list[str] = normalized["paths"]
    root = _workspace_root(workspace_root)
    resolver = PathResolver(root)
    targets: list[DeleteTarget] = []
    seen: set[str] = set()

    for raw_path in raw_paths:
        if _has_glob(raw_path):
            return _failure(
                "glob delete is not supported; pass explicit file paths.",
                ToolErrorCode.GLOB_DELETE_NOT_ALLOWED.value,
                data={"path": raw_path},
            )
        resolved = resolver.resolve(raw_path)
        validation = _validate_delete_target(resolved)
        if validation is not None:
            return validation
        resolved_key = str(Path(resolved.path_resolved))
        if resolved_key in seen:
            return _failure(
                "delete_file target paths must be unique.",
                ToolErrorCode.FILE_CONFLICT.value,
                data={"path": resolved.workspace_relative_path},
            )
        seen.add(resolved_key)
        target = Path(resolved.path_resolved)
        try:
            stat = target.stat()
            content = target.read_bytes()
        except OSError as exc:
            return _failure(
                f"Unable to read delete target metadata: {resolved.workspace_relative_path}",
                ToolErrorCode.PERMISSION_DENIED.value,
                data={**_path_data(resolved), "read_error": str(exc)},
            )
        targets.append(
            DeleteTarget(
                path=str(resolved.workspace_relative_path),
                resolved_path=str(target),
                size_bytes=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                content_hash=_sha256(content),
                is_sensitive=resolved.is_sensitive,
            )
        )

    return {
        "ok": True,
        "plan": DeletePlan(
            targets=targets,
            total_size_bytes=sum(target.size_bytes for target in targets),
        ),
    }


def _normalize_delete_targets(args: Mapping[str, Any]) -> dict[str, Any]:
    has_path = _has_value(args.get("path"))
    has_file_paths = _has_value(args.get("file_paths"))
    if has_path == has_file_paths:
        return _failure(
            "exactly one of path or file_paths is required.",
            ToolErrorCode.INVALID_ARGS.value,
            data={"path": args.get("path"), "file_paths": args.get("file_paths")},
        )
    if has_path:
        value = args.get("path")
        if not isinstance(value, str):
            return _failure(
                "path must be a string.",
                ToolErrorCode.INVALID_ARGS.value,
                data={"path": value},
            )
        return {"ok": True, "paths": [value]}

    values = args.get("file_paths")
    if not isinstance(values, list) or not values:
        return _failure(
            "file_paths must be a non-empty array.",
            ToolErrorCode.INVALID_ARGS.value,
            data={"file_paths": values},
        )
    paths: list[str] = []
    for index, value in enumerate(values):
        if not isinstance(value, str) or not value.strip():
            return _failure(
                "file_paths must contain non-empty strings.",
                ToolErrorCode.INVALID_ARGS.value,
                data={"index": index, "value": value},
            )
        paths.append(value)
    return {"ok": True, "paths": paths}


def _validate_delete_target(resolved: ResolvedPath) -> dict[str, Any] | None:
    if not resolved.valid or not resolved.is_inside_workspace or resolved.is_blocked:
        return _failure(
            resolved.reason or "Invalid delete target path.",
            resolved.error_code,
            data=resolved.to_dict(),
        )
    if not resolved.exists:
        return _failure(
            f"File not found: {resolved.workspace_relative_path}",
            ToolErrorCode.FILE_NOT_FOUND.value,
            data=_path_data(resolved),
        )
    if resolved.resource_type == "directory":
        return _failure(
            f"Directory deletion is not supported: {resolved.workspace_relative_path}",
            ToolErrorCode.DELETE_DIRECTORY_NOT_ALLOWED.value,
            data=_path_data(resolved),
        )
    if resolved.resource_type != "file":
        return _failure(
            f"Path is not a file: {resolved.workspace_relative_path}",
            ToolErrorCode.NOT_A_FILE.value,
            data=_path_data(resolved),
        )
    if resolved.is_symlink:
        return _failure(
            f"Symlink deletion is not supported: {resolved.workspace_relative_path}",
            ToolErrorCode.SENSITIVE_PATH_BLOCKED.value,
            data=_path_data(resolved),
        )
    return None


def _raw_paths(args: Mapping[str, Any]) -> list[str]:
    path = args.get("path")
    if isinstance(path, str):
        return [path]
    values = args.get("file_paths")
    if isinstance(values, list):
        return [str(item) for item in values]
    return []


def _path_data(resolved: ResolvedPath) -> dict[str, Any]:
    return {
        "path": resolved.workspace_relative_path,
        "exists": resolved.exists,
        "type": resolved.resource_type,
        "is_sensitive": resolved.is_sensitive,
        "is_ignored": resolved.is_ignored,
        "is_symlink": resolved.is_symlink,
    }


def _tool_failure(result: dict[str, Any]) -> ToolResult:
    return ToolResult.fail(
        result["message"],
        code=result["code"],
        data=result.get("data"),
    )


def _failure(message: str, code: str, data: Any = None) -> dict[str, Any]:
    return {"ok": False, "message": message, "code": code, "data": _json_safe(data)}


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, frozenset, dict)):
        return bool(value)
    return True


def _has_glob(value: str) -> bool:
    return any(char in value for char in "*?[]")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _workspace_root(value: str | Path | None) -> Path:
    if value is not None:
        return Path(value).resolve()
    return get_settings().workspace_root.resolve()


__all__ = [
    "DeleteFileTool",
    "DeletePlan",
    "DeleteTarget",
    "build_delete_plan",
    "build_delete_preview",
]
