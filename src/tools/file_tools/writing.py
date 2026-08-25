from __future__ import annotations

import codecs
import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any

from src.core.config import get_settings
from src.tools.base import ToolResult
from src.tools.errors import ToolErrorCode

from .path_resolver import PathResolver, ResolvedPath


WRITE_MODES = frozenset({"create", "overwrite", "append", "create_or_overwrite"})
DEFAULT_WRITE_FILE_PREVIEW_CHARS = 1000


class WriteFileTool:
    """Write complete workspace files through explicit create/overwrite modes."""

    def __init__(self, *, preview_chars: int = DEFAULT_WRITE_FILE_PREVIEW_CHARS) -> None:
        self.preview_chars = max(int(preview_chars), 1)

    def run(
        self,
        content: str,
        path: str | None = None,
        write_mode: str | None = None,
        encoding: str = "utf-8",
        file_path: str | None = None,
        overwrite: bool | None = None,
        *,
        workspace_root: str | Path | None = None,
    ) -> ToolResult:
        normalized_path = path if path is not None else file_path
        if not normalized_path:
            return ToolResult.fail(
                "path is required.",
                code=ToolErrorCode.MISSING_REQUIRED_PARAM.value,
            )
        mode = _normalize_write_mode(write_mode, overwrite)
        if mode not in WRITE_MODES:
            return ToolResult.fail(
                f"Unsupported write_mode: {write_mode}",
                code=ToolErrorCode.INVALID_ARGS.value,
                data={"write_mode": write_mode, "allowed_write_modes": sorted(WRITE_MODES)},
            )
        if not isinstance(content, str):
            return ToolResult.fail(
                "content must be a string.",
                code=ToolErrorCode.INVALID_ARGS.value,
            )

        encoding_result = _encode_content(content, encoding)
        if not encoding_result["ok"]:
            return ToolResult.fail(
                f"Invalid or unsupported encoding: {encoding}",
                code=ToolErrorCode.INVALID_ENCODING.value,
                data={
                    "path": normalized_path,
                    "encoding": encoding,
                    "encoding_error": encoding_result["error"],
                },
            )
        content_bytes = encoding_result["content_bytes"]
        normalized_encoding = encoding_result["encoding"]

        root = _workspace_root(workspace_root)
        resolved = PathResolver(root).resolve(normalized_path)
        validation = _validate_write_target(resolved, mode)
        if validation is not None:
            return validation

        target = Path(resolved.path_resolved)
        before_bytes = _read_existing_bytes(target) if target.exists() else None
        before_hash = _sha256(before_bytes) if before_bytes is not None else None
        old_size = len(before_bytes) if before_bytes is not None else 0

        try:
            if mode == "append":
                with target.open("ab") as handle:
                    handle.write(content_bytes)
                created = False
                overwritten = False
                appended = True
            elif mode == "create":
                with target.open("xb") as handle:
                    handle.write(content_bytes)
                created = True
                overwritten = False
                appended = False
            elif mode == "overwrite":
                _atomic_replace(target, content_bytes)
                created = False
                overwritten = True
                appended = False
            else:
                existed_before = target.exists()
                if existed_before:
                    _atomic_replace(target, content_bytes)
                    created = False
                    overwritten = True
                else:
                    with target.open("xb") as handle:
                        handle.write(content_bytes)
                    created = True
                    overwritten = False
                appended = False
        except FileExistsError:
            return ToolResult.fail(
                f"File already exists: {resolved.workspace_relative_path}",
                code=ToolErrorCode.FILE_ALREADY_EXISTS.value,
                data=_base_data(resolved, mode),
            )
        except FileNotFoundError:
            return ToolResult.fail(
                f"Parent directory not found: {resolved.workspace_relative_path}",
                code=ToolErrorCode.PARENT_DIRECTORY_NOT_FOUND.value,
                data=_base_data(resolved, mode),
            )
        except OSError as exc:
            return ToolResult.fail(
                f"Unable to write file: {resolved.workspace_relative_path}",
                code=ToolErrorCode.FILE_WRITE_FAILED.value,
                data={**_base_data(resolved, mode), "write_error": str(exc)},
            )

        after_bytes = _read_existing_bytes(target)
        data = {
            **_base_data(resolved, mode),
            "encoding": normalized_encoding,
            "created": created,
            "overwritten": overwritten,
            "appended": appended,
            "bytes_written": len(content_bytes),
            "old_size_bytes": old_size,
            "new_size_bytes": len(after_bytes),
            "content_hash_before": before_hash,
            "content_hash_after": _sha256(after_bytes),
            "content_hash": _sha256(content_bytes),
            "hash_algorithm": "sha256",
            "content_preview": _preview(content, self.preview_chars),
            "content_truncated": len(content) > self.preview_chars,
        }
        return ToolResult.ok(
            data=data,
            message=f"Wrote file {resolved.workspace_relative_path} using {mode}.",
        )


def _validate_write_target(resolved: ResolvedPath, mode: str) -> ToolResult | None:
    if not resolved.valid or not resolved.is_inside_workspace or resolved.is_blocked:
        return _path_failure(resolved)
    target = Path(resolved.path_resolved)
    parent = target.parent
    if not parent.exists():
        return ToolResult.fail(
            f"Parent directory not found: {resolved.workspace_relative_path}",
            code=ToolErrorCode.PARENT_DIRECTORY_NOT_FOUND.value,
            data=_base_data(resolved, mode),
        )
    if not parent.is_dir():
        return ToolResult.fail(
            f"Parent path is not a directory: {resolved.workspace_relative_path}",
            code=ToolErrorCode.NOT_A_DIRECTORY.value,
            data=_base_data(resolved, mode),
        )
    if resolved.is_symlink:
        return ToolResult.fail(
            f"Symlink writes are not supported: {resolved.workspace_relative_path}",
            code=ToolErrorCode.SENSITIVE_PATH_BLOCKED.value,
            data=_base_data(resolved, mode),
        )
    if resolved.exists and resolved.resource_type != "file":
        return ToolResult.fail(
            f"Path is not a file: {resolved.workspace_relative_path}",
            code=ToolErrorCode.NOT_A_FILE.value,
            data=_base_data(resolved, mode),
        )
    if mode == "create" and resolved.exists:
        return ToolResult.fail(
            f"File already exists: {resolved.workspace_relative_path}",
            code=ToolErrorCode.FILE_ALREADY_EXISTS.value,
            data=_base_data(resolved, mode),
        )
    if mode in {"overwrite", "append"} and not resolved.exists:
        return ToolResult.fail(
            f"File not found: {resolved.workspace_relative_path}",
            code=ToolErrorCode.FILE_NOT_FOUND.value,
            data=_base_data(resolved, mode),
        )
    return None


def _normalize_write_mode(write_mode: str | None, overwrite: bool | None) -> str:
    if write_mode is not None and str(write_mode).strip():
        return str(write_mode).strip().lower()
    if overwrite is True:
        return "overwrite"
    return "create"


def _path_failure(resolved: ResolvedPath) -> ToolResult:
    return ToolResult.fail(
        resolved.reason or "Invalid workspace path.",
        code=resolved.error_code,
        data=resolved.to_dict(),
    )


def _base_data(resolved: ResolvedPath, write_mode: str) -> dict[str, Any]:
    return {
        "path": resolved.workspace_relative_path,
        "write_mode": write_mode,
        "exists": resolved.exists,
        "type": resolved.resource_type,
        "is_sensitive": resolved.is_sensitive,
        "is_ignored": resolved.is_ignored,
        "is_symlink": resolved.is_symlink,
    }


def _atomic_replace(target: Path, content: bytes) -> None:
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
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temp_path), str(target))
    finally:
        if temp_path is not None:
            try:
                if temp_path.exists():
                    temp_path.unlink()
            except OSError:
                pass


def _encode_content(content: str, encoding: str | None) -> dict[str, Any]:
    requested = str(encoding or "utf-8").strip() or "utf-8"
    try:
        codec = codecs.lookup(requested)
        return {
            "ok": True,
            "encoding": codec.name,
            "content_bytes": content.encode(codec.name, errors="strict"),
            "error": None,
        }
    except (LookupError, UnicodeEncodeError) as exc:
        return {
            "ok": False,
            "encoding": requested,
            "content_bytes": b"",
            "error": str(exc),
        }


def _read_existing_bytes(path: Path) -> bytes:
    return path.read_bytes()


def _preview(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(limit - 3, 0)] + "..."


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _workspace_root(value: str | Path | None) -> Path:
    if value is not None:
        return Path(value).resolve()
    return get_settings().workspace_root.resolve()


__all__ = [
    "DEFAULT_WRITE_FILE_PREVIEW_CHARS",
    "WRITE_MODES",
    "WriteFileTool",
]
