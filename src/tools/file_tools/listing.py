from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.core.config import get_settings
from src.tools.base import ToolResult
from src.tools.errors import ToolErrorCode

from .common import is_hidden_name
from .path_resolver import PathResolver, ResolvedPath


DEFAULT_MAX_ENTRIES = 200
MAX_ENTRIES_HARD_LIMIT = 1000
FILE_INFO_SAMPLE_BYTES = 512 * 1024
FILE_INFO_HASH_MAX_BYTES = 8 * 1024 * 1024


class ListFilesTool:
    """List workspace files without reading file contents."""

    def run(
        self,
        path: str = ".",
        recursive: bool = False,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        include_hidden: bool = False,
        *,
        workspace_root: str | Path | None = None,
    ) -> ToolResult:
        root = _workspace_root(workspace_root)
        resolver = PathResolver(root)
        base = resolver.resolve(path)
        validation = _validate_directory(base)
        if validation is not None:
            return validation

        max_count = _entry_limit(max_entries)
        entries: list[dict[str, Any]] = []
        ignored_count = 0
        truncated = False

        stack = [Path(base.path_resolved)]
        while stack:
            current = stack.pop()
            children = _sorted_children(current)
            next_dirs: list[Path] = []
            for child in children:
                child_relative = _relative_path(child, root)
                child_resolved = resolver.resolve(child_relative)
                if not include_hidden and is_hidden_name(child.name):
                    continue
                if child_resolved.is_ignored:
                    ignored_count += 1
                    continue
                if len(entries) >= max_count:
                    truncated = True
                    break
                entries.append(_entry_data(child_resolved))
                if recursive and child_resolved.resource_type == "directory":
                    if child_resolved.is_sensitive:
                        continue
                    next_dirs.append(Path(child_resolved.path_resolved))
            if truncated or not recursive:
                break
            stack.extend(reversed(next_dirs))

        data = {
            "path": base.workspace_relative_path,
            "entries": entries,
            "entry_count": len(entries),
            "truncated": truncated,
            "ignored_count": ignored_count,
            "max_entries": max_count,
            "recursive": bool(recursive),
            "include_hidden": bool(include_hidden),
        }
        return ToolResult.ok(
            data=data,
            message=f"Listed {len(entries)} entries under {base.workspace_relative_path}.",
        )


class FileInfoTool:
    """Return bounded metadata for one workspace file or directory."""

    def run(
        self,
        path: str,
        include_hash: bool = False,
        *,
        workspace_root: str | Path | None = None,
    ) -> ToolResult:
        root = _workspace_root(workspace_root)
        resolver = PathResolver(root)
        resolved = resolver.resolve(path)
        if not resolved.valid or not resolved.is_inside_workspace:
            return _path_failure(resolved)
        if resolved.is_ignored and resolved.resource_type == "directory":
            return ToolResult.fail(
                f"Directory is ignored by policy: {resolved.workspace_relative_path}",
                code=ToolErrorCode.DIRECTORY_IGNORED.value,
                data={"path": resolved.workspace_relative_path, "is_ignored": True},
            )
        if not resolved.exists:
            return ToolResult.fail(
                f"Path not found: {resolved.workspace_relative_path}",
                code=ToolErrorCode.FILE_NOT_FOUND.value,
                data=_base_info(resolved),
            )

        data = _base_info(resolved)
        path_obj = Path(resolved.path_resolved)
        if resolved.resource_type == "file":
            stat = path_obj.stat()
            data.update(
                {
                    "size_bytes": stat.st_size,
                    "modified_at": _iso_from_timestamp(stat.st_mtime),
                    "hash": None,
                    "hash_algorithm": "sha256" if include_hash else None,
                    "hash_skipped_reason": None,
                    "encoding_guess": None,
                    "line_count": None,
                }
            )
            if resolved.is_sensitive:
                data["hash_skipped_reason"] = "sensitive_path"
                data["encoding_guess"] = "not_read_sensitive"
            else:
                data.update(_text_metadata(path_obj))
                if include_hash:
                    data.update(_hash_metadata(path_obj, stat.st_size))
        elif resolved.resource_type == "directory":
            stat = path_obj.stat()
            data.update(
                {
                    "size_bytes": None,
                    "modified_at": _iso_from_timestamp(stat.st_mtime),
                    "hash": None,
                    "hash_algorithm": None,
                    "hash_skipped_reason": "directory",
                    "encoding_guess": None,
                    "line_count": None,
                }
            )

        return ToolResult.ok(
            data=data,
            message=f"Collected metadata for {resolved.workspace_relative_path}.",
        )


def _validate_directory(resolved: ResolvedPath) -> ToolResult | None:
    if not resolved.valid or not resolved.is_inside_workspace or resolved.is_blocked:
        return _path_failure(resolved)
    if resolved.is_ignored:
        return ToolResult.fail(
            f"Directory is ignored by policy: {resolved.workspace_relative_path}",
            code=ToolErrorCode.DIRECTORY_IGNORED.value,
            data={"path": resolved.workspace_relative_path, "is_ignored": True},
        )
    if not resolved.exists:
        return ToolResult.fail(
            f"Path not found: {resolved.workspace_relative_path}",
            code=ToolErrorCode.FILE_NOT_FOUND.value,
            data=_base_info(resolved),
        )
    if resolved.resource_type != "directory":
        return ToolResult.fail(
            f"Path is not a directory: {resolved.workspace_relative_path}",
            code=ToolErrorCode.NOT_A_DIRECTORY.value,
            data=_base_info(resolved),
        )
    return None


def _path_failure(resolved: ResolvedPath) -> ToolResult:
    return ToolResult.fail(
        resolved.reason or "Invalid workspace path.",
        code=resolved.error_code,
        data=resolved.to_dict(),
    )


def _entry_limit(value: int) -> int:
    try:
        requested = int(value)
    except (TypeError, ValueError):
        requested = DEFAULT_MAX_ENTRIES
    return max(min(requested, MAX_ENTRIES_HARD_LIMIT), 1)


def _sorted_children(path: Path) -> list[Path]:
    children = list(path.iterdir())
    return sorted(
        children,
        key=lambda item: (
            0 if item.is_dir() else 1,
            item.name.lower(),
            item.name,
        ),
    )


def _entry_data(resolved: ResolvedPath) -> dict[str, Any]:
    data = _base_info(resolved)
    path = Path(resolved.path_resolved)
    if resolved.exists and resolved.resource_type == "file":
        stat = path.stat()
        data["size_bytes"] = stat.st_size
        data["modified_at"] = _iso_from_timestamp(stat.st_mtime)
    elif resolved.exists and resolved.resource_type == "directory":
        stat = path.stat()
        data["size_bytes"] = None
        data["modified_at"] = _iso_from_timestamp(stat.st_mtime)
    else:
        data["size_bytes"] = None
        data["modified_at"] = None
    return data


def _base_info(resolved: ResolvedPath) -> dict[str, Any]:
    path = resolved.workspace_relative_path
    name = "" if path in {None, "."} else Path(str(path)).name
    return {
        "name": name,
        "path": path,
        "type": resolved.resource_type,
        "exists": resolved.exists,
        "is_sensitive": resolved.is_sensitive,
        "is_ignored": resolved.is_ignored,
        "is_symlink": resolved.is_symlink,
    }


def _text_metadata(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            sample = handle.read(FILE_INFO_SAMPLE_BYTES)
    except OSError as exc:
        return {
            "encoding_guess": "unreadable",
            "line_count": None,
            "read_error": str(exc),
        }
    if b"\x00" in sample:
        return {"encoding_guess": "binary", "line_count": None}
    try:
        text = sample.decode("utf-8")
    except UnicodeDecodeError:
        return {"encoding_guess": "unknown", "line_count": None}
    truncated = path.stat().st_size > len(sample)
    return {
        "encoding_guess": "utf-8",
        "line_count": text.count("\n") + (1 if text and not text.endswith("\n") else 0),
        "line_count_truncated": truncated,
    }


def _hash_metadata(path: Path, size_bytes: int) -> dict[str, Any]:
    if size_bytes > FILE_INFO_HASH_MAX_BYTES:
        return {"hash": None, "hash_skipped_reason": "file_too_large"}
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {
        "hash": digest.hexdigest(),
        "hash_algorithm": "sha256",
        "hash_skipped_reason": None,
    }


def _relative_path(path: Path, root: Path) -> str:
    relative = path.relative_to(root)
    return "." if str(relative) == "." else str(relative).replace("\\", "/")


def _workspace_root(value: str | Path | None) -> Path:
    if value is not None:
        return Path(value).resolve()
    return get_settings().workspace_root.resolve()


def _iso_from_timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


__all__ = [
    "FILE_INFO_HASH_MAX_BYTES",
    "FILE_INFO_SAMPLE_BYTES",
    "ListFilesTool",
    "FileInfoTool",
]
