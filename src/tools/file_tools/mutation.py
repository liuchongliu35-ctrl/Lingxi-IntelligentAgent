from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.core.config import get_settings
from src.tools.base import ToolResult, _json_safe
from src.tools.errors import ToolErrorCode

from .path_resolver import PathResolver, ResolvedPath


FILE_MUTATION_OPERATIONS = frozenset({"copy", "move", "rename"})


@dataclass(frozen=True)
class FileMutationPlan:
    operation: str
    source_path: str
    target_path: str
    overwrite: bool
    source_size_bytes: int
    source_hash: str
    target_exists_before: bool
    target_size_before: int | None
    target_hash_before: str | None
    will_remove_source: bool
    will_overwrite: bool
    new_name: str | None = None

    def to_data(self) -> dict[str, Any]:
        moved = self.operation in {"move", "rename"}
        return {
            "operation": self.operation,
            "source_path": self.source_path,
            "target_path": self.target_path,
            "new_name": self.new_name,
            "overwrite": self.overwrite,
            "overwritten": self.will_overwrite,
            "bytes_copied": self.source_size_bytes if self.operation == "copy" else 0,
            "bytes_moved": self.source_size_bytes if moved else 0,
            "source_size_bytes": self.source_size_bytes,
            "target_size_before": self.target_size_before,
            "source_hash": self.source_hash,
            "target_hash_before": self.target_hash_before,
            "will_remove_source": self.will_remove_source,
            "will_overwrite": self.will_overwrite,
            "hash_algorithm": "sha256",
        }


class CopyFileTool:
    """Copy one explicit workspace file to another workspace file."""

    def run(
        self,
        source_path: str,
        target_path: str,
        overwrite: bool = False,
        *,
        workspace_root: str | Path | None = None,
    ) -> ToolResult:
        result = build_file_mutation_plan(
            operation="copy",
            source_path=source_path,
            target_path=target_path,
            overwrite=overwrite,
            workspace_root=workspace_root,
        )
        if not result["ok"]:
            return _tool_failure(result)
        plan: FileMutationPlan = result["plan"]
        source = Path(result["source_resolved"].path_resolved)
        target = Path(result["target_resolved"].path_resolved)
        try:
            if plan.will_overwrite:
                _atomic_copy_replace(source, target)
            else:
                shutil.copy2(source, target)
        except FileExistsError:
            return ToolResult.fail(
                f"File already exists: {plan.target_path}",
                code=ToolErrorCode.FILE_ALREADY_EXISTS.value,
                data=plan.to_data(),
            )
        except OSError as exc:
            return ToolResult.fail(
                f"Unable to copy file: {plan.source_path} -> {plan.target_path}",
                code=ToolErrorCode.FILE_WRITE_FAILED.value,
                data={**plan.to_data(), "mutation_error": str(exc)},
            )
        return _success_result(plan, source, target)


class MoveFileTool:
    """Move one explicit workspace file to another workspace file."""

    def run(
        self,
        source_path: str,
        target_path: str,
        overwrite: bool = False,
        *,
        workspace_root: str | Path | None = None,
    ) -> ToolResult:
        result = build_file_mutation_plan(
            operation="move",
            source_path=source_path,
            target_path=target_path,
            overwrite=overwrite,
            workspace_root=workspace_root,
        )
        if not result["ok"]:
            return _tool_failure(result)
        plan: FileMutationPlan = result["plan"]
        source = Path(result["source_resolved"].path_resolved)
        target = Path(result["target_resolved"].path_resolved)
        try:
            os.replace(str(source), str(target))
        except OSError as exc:
            return ToolResult.fail(
                f"Unable to move file: {plan.source_path} -> {plan.target_path}",
                code=ToolErrorCode.FILE_WRITE_FAILED.value,
                data={**plan.to_data(), "mutation_error": str(exc)},
            )
        return _success_result(plan, source, target)


class RenameFileTool:
    """Rename one workspace file within its current directory."""

    def run(
        self,
        source_path: str,
        new_name: str,
        overwrite: bool = False,
        *,
        workspace_root: str | Path | None = None,
    ) -> ToolResult:
        target_result = _rename_target_path(
            source_path=source_path,
            new_name=new_name,
            workspace_root=workspace_root,
        )
        if not target_result["ok"]:
            return ToolResult.fail(
                target_result["message"],
                code=target_result["code"],
                data=target_result.get("data"),
            )
        result = build_file_mutation_plan(
            operation="rename",
            source_path=source_path,
            target_path=target_result["target_path"],
            overwrite=overwrite,
            workspace_root=workspace_root,
            new_name=str(new_name),
        )
        if not result["ok"]:
            return _tool_failure(result)
        plan: FileMutationPlan = result["plan"]
        source = Path(result["source_resolved"].path_resolved)
        target = Path(result["target_resolved"].path_resolved)
        try:
            os.replace(str(source), str(target))
        except OSError as exc:
            return ToolResult.fail(
                f"Unable to rename file: {plan.source_path} -> {plan.target_path}",
                code=ToolErrorCode.FILE_WRITE_FAILED.value,
                data={**plan.to_data(), "mutation_error": str(exc)},
            )
        return _success_result(plan, source, target)


def build_file_mutation_preview(
    *,
    operation: str,
    args: dict[str, Any],
    workspace_root: str | Path | None = None,
    requires_confirmation: bool = False,
) -> dict[str, Any]:
    plan_result = _plan_from_args(
        operation=operation,
        args=args,
        workspace_root=workspace_root,
    )
    if not plan_result["ok"]:
        return {
            "operation": operation,
            "source_path": args.get("source_path"),
            "target_path": args.get("target_path"),
            "new_name": args.get("new_name"),
            "requires_confirmation": bool(requires_confirmation),
            "preview_error": {
                "code": plan_result["code"],
                "message": plan_result["message"],
                "data": plan_result.get("data"),
            },
        }
    plan: FileMutationPlan = plan_result["plan"]
    return {
        **plan.to_data(),
        "requires_confirmation": bool(requires_confirmation),
        "source_exists_before": True,
        "target_exists_before": plan.target_exists_before,
        "source_snapshot": _snapshot(Path(plan_result["source_resolved"].path_resolved)),
        "target_snapshot": _snapshot(Path(plan_result["target_resolved"].path_resolved)),
    }


def build_file_mutation_plan(
    *,
    operation: str,
    source_path: str,
    target_path: str,
    overwrite: bool = False,
    workspace_root: str | Path | None = None,
    new_name: str | None = None,
) -> dict[str, Any]:
    normalized_operation = str(operation or "").strip().lower()
    if normalized_operation not in FILE_MUTATION_OPERATIONS:
        return _failure(
            f"Unsupported file mutation operation: {operation}",
            ToolErrorCode.INVALID_ARGS.value,
            data={"operation": operation},
        )
    root = _workspace_root(workspace_root)
    resolver = PathResolver(root)
    source = resolver.resolve(source_path)
    target = resolver.resolve(target_path)
    source_failure = _validate_source(source)
    if source_failure is not None:
        return source_failure
    if Path(source.path_resolved) == Path(target.path_resolved):
        return _failure(
            "source_path and target_path must refer to different files.",
            ToolErrorCode.FILE_CONFLICT.value,
            data={
                "source_path": source.workspace_relative_path,
                "target_path": target.workspace_relative_path,
            },
        )
    target_failure = _validate_target(target, overwrite=bool(overwrite))
    if target_failure is not None:
        return target_failure

    source_file = Path(source.path_resolved)
    target_file = Path(target.path_resolved)
    try:
        source_bytes = source_file.read_bytes()
        target_bytes = target_file.read_bytes() if target_file.exists() else None
    except OSError as exc:
        return _failure(
            "Unable to read source or target metadata.",
            ToolErrorCode.PERMISSION_DENIED.value,
            data={
                "source_path": source.workspace_relative_path,
                "target_path": target.workspace_relative_path,
                "read_error": str(exc),
            },
        )

    plan = FileMutationPlan(
        operation=normalized_operation,
        source_path=str(source.workspace_relative_path),
        target_path=str(target.workspace_relative_path),
        overwrite=bool(overwrite),
        source_size_bytes=len(source_bytes),
        source_hash=_sha256(source_bytes),
        target_exists_before=target.exists,
        target_size_before=len(target_bytes) if target_bytes is not None else None,
        target_hash_before=_sha256(target_bytes) if target_bytes is not None else None,
        will_remove_source=normalized_operation in {"move", "rename"},
        will_overwrite=bool(target.exists and overwrite),
        new_name=new_name,
    )
    return {
        "ok": True,
        "plan": plan,
        "source_resolved": source,
        "target_resolved": target,
    }


def _plan_from_args(
    *,
    operation: str,
    args: dict[str, Any],
    workspace_root: str | Path | None,
) -> dict[str, Any]:
    if operation == "rename":
        source_path = args.get("source_path")
        new_name = args.get("new_name")
        rename_target = _rename_target_path(
            source_path=str(source_path or ""),
            new_name=str(new_name or ""),
            workspace_root=workspace_root,
        )
        if not rename_target["ok"]:
            return rename_target
        return build_file_mutation_plan(
            operation=operation,
            source_path=str(source_path or ""),
            target_path=rename_target["target_path"],
            overwrite=bool(args.get("overwrite", False)),
            workspace_root=workspace_root,
            new_name=str(new_name or ""),
        )
    return build_file_mutation_plan(
        operation=operation,
        source_path=str(args.get("source_path") or ""),
        target_path=str(args.get("target_path") or ""),
        overwrite=bool(args.get("overwrite", False)),
        workspace_root=workspace_root,
    )


def _rename_target_path(
    *,
    source_path: str,
    new_name: str,
    workspace_root: str | Path | None,
) -> dict[str, Any]:
    name = str(new_name or "")
    if not name.strip():
        return _failure(
            "new_name must not be empty.",
            ToolErrorCode.INVALID_ARGS.value,
            data={"source_path": source_path, "new_name": new_name},
        )
    if name in {".", ".."} or "/" in name or "\\" in name or "\x00" in name:
        return _failure(
            "new_name must be a plain file name without path separators.",
            ToolErrorCode.INVALID_ARGS.value,
            data={"source_path": source_path, "new_name": new_name},
        )
    if Path(name).is_absolute() or Path(name).name != name:
        return _failure(
            "new_name must be a plain file name.",
            ToolErrorCode.INVALID_ARGS.value,
            data={"source_path": source_path, "new_name": new_name},
        )
    root = _workspace_root(workspace_root)
    source = PathResolver(root).resolve(source_path)
    if not source.valid or not source.is_inside_workspace:
        return _failure(
            source.reason or "Invalid source path.",
            source.error_code,
            data=source.to_dict(),
        )
    source_resolved = Path(source.path_resolved)
    target = source_resolved.parent / name
    try:
        return {"ok": True, "target_path": str(target.relative_to(root))}
    except ValueError:
        return _failure(
            "rename target is outside workspace.",
            ToolErrorCode.WORKSPACE_OUT_OF_SCOPE.value,
            data={"source_path": source_path, "new_name": new_name},
        )


def _validate_source(resolved: ResolvedPath) -> dict[str, Any] | None:
    if not resolved.valid or not resolved.is_inside_workspace or resolved.is_blocked:
        return _failure(
            resolved.reason or "Invalid source path.",
            resolved.error_code,
            data=resolved.to_dict(),
        )
    if not resolved.exists:
        return _failure(
            f"Source file not found: {resolved.workspace_relative_path}",
            ToolErrorCode.FILE_NOT_FOUND.value,
            data=_path_data("source", resolved),
        )
    if resolved.resource_type != "file":
        return _failure(
            f"Source path is not a file: {resolved.workspace_relative_path}",
            ToolErrorCode.NOT_A_FILE.value,
            data=_path_data("source", resolved),
        )
    if resolved.is_symlink:
        return _failure(
            f"Symlink source is not supported: {resolved.workspace_relative_path}",
            ToolErrorCode.SENSITIVE_PATH_BLOCKED.value,
            data=_path_data("source", resolved),
        )
    return None


def _validate_target(resolved: ResolvedPath, *, overwrite: bool) -> dict[str, Any] | None:
    if not resolved.valid or not resolved.is_inside_workspace or resolved.is_blocked:
        return _failure(
            resolved.reason or "Invalid target path.",
            resolved.error_code,
            data=resolved.to_dict(),
        )
    target = Path(resolved.path_resolved)
    parent = target.parent
    if not parent.exists():
        return _failure(
            f"Parent directory not found: {resolved.workspace_relative_path}",
            ToolErrorCode.PARENT_DIRECTORY_NOT_FOUND.value,
            data=_path_data("target", resolved),
        )
    if not parent.is_dir():
        return _failure(
            f"Parent path is not a directory: {resolved.workspace_relative_path}",
            ToolErrorCode.NOT_A_DIRECTORY.value,
            data=_path_data("target", resolved),
        )
    if resolved.is_symlink:
        return _failure(
            f"Symlink target is not supported: {resolved.workspace_relative_path}",
            ToolErrorCode.SENSITIVE_PATH_BLOCKED.value,
            data=_path_data("target", resolved),
        )
    if resolved.exists and resolved.resource_type != "file":
        return _failure(
            f"Target path is not a file: {resolved.workspace_relative_path}",
            ToolErrorCode.NOT_A_FILE.value,
            data=_path_data("target", resolved),
        )
    if resolved.exists and not overwrite:
        return _failure(
            f"File already exists: {resolved.workspace_relative_path}",
            ToolErrorCode.FILE_ALREADY_EXISTS.value,
            data=_path_data("target", resolved),
        )
    return None


def _success_result(plan: FileMutationPlan, source: Path, target: Path) -> ToolResult:
    target_bytes = target.read_bytes()
    data = {
        **plan.to_data(),
        "target_hash_after": _sha256(target_bytes),
        "target_size_after": len(target_bytes),
        "source_exists_after": source.exists(),
        "target_exists_after": target.exists(),
    }
    return ToolResult.ok(
        data=data,
        message=(
            f"{plan.operation}_file completed: "
            f"{plan.source_path} -> {plan.target_path}."
        ),
    )


def _atomic_copy_replace(source: Path, target: Path) -> None:
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=str(target.parent),
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            with source.open("rb") as source_handle:
                shutil.copyfileobj(source_handle, handle)
            handle.flush()
            os.fsync(handle.fileno())
        shutil.copystat(source, temp_path)
        os.replace(str(temp_path), str(target))
    finally:
        if temp_path is not None:
            try:
                if temp_path.exists():
                    temp_path.unlink()
            except OSError:
                pass


def _snapshot(path: Path) -> dict[str, Any]:
    exists = path.exists()
    item: dict[str, Any] = {"exists": exists}
    if exists:
        stat = path.stat()
        item.update(
            {
                "size_bytes": stat.st_size if path.is_file() else None,
                "mtime_ns": stat.st_mtime_ns,
                "is_file": path.is_file(),
                "is_dir": path.is_dir(),
            }
        )
        if path.is_file():
            item["sha256"] = _sha256(path.read_bytes())
    return item


def _path_data(role: str, resolved: ResolvedPath) -> dict[str, Any]:
    return {
        f"{role}_path": resolved.workspace_relative_path,
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


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _workspace_root(value: str | Path | None) -> Path:
    if value is not None:
        return Path(value).resolve()
    return get_settings().workspace_root.resolve()


__all__ = [
    "CopyFileTool",
    "FILE_MUTATION_OPERATIONS",
    "FileMutationPlan",
    "MoveFileTool",
    "RenameFileTool",
    "build_file_mutation_plan",
    "build_file_mutation_preview",
]
