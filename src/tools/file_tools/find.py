from __future__ import annotations

import fnmatch
import os
from pathlib import Path
from typing import Any

from src.core.config import get_settings
from src.tools.base import ToolResult
from src.tools.errors import ToolErrorCode

from .common import is_hidden_name
from .path_resolver import PathResolver, ResolvedPath


DEFAULT_MAX_RESULTS = 200
MAX_RESULTS_HARD_LIMIT = 1000
MAX_TEXT_SCAN_BYTES = 8 * 1024 * 1024
MAX_LINE_PREVIEW_CHARS = 240
SAMPLE_BYTES = 8192


class FindFilesTool:
    """Find workspace files by name and/or bounded text content."""

    def run(
        self,
        path: str = ".",
        name_pattern: str | None = None,
        text_pattern: str | None = None,
        case_sensitive: bool = False,
        max_results: int = DEFAULT_MAX_RESULTS,
        *,
        workspace_root: str | Path | None = None,
    ) -> ToolResult:
        name_pattern = _optional_pattern(name_pattern)
        text_pattern = _optional_pattern(text_pattern)
        if not name_pattern and not text_pattern:
            return ToolResult.fail(
                "name_pattern or text_pattern is required.",
                code=ToolErrorCode.INVALID_ARGS.value,
            )

        root = _workspace_root(workspace_root)
        resolver = PathResolver(root)
        base = resolver.resolve(path)
        validation = _validate_search_root(base)
        if validation is not None:
            return validation

        limit = _result_limit(max_results)
        matches: list[dict[str, Any]] = []
        skipped_count = 0
        truncated = False
        stack = [Path(base.path_resolved)]

        while stack:
            current = stack.pop()
            try:
                children = _sorted_children(current)
            except OSError:
                skipped_count += 1
                continue

            next_dirs: list[Path] = []
            for child in children:
                relative = _relative_path(child, root)
                if is_hidden_name(child.name) and child.is_dir():
                    # Hidden project metadata is handled by the ignored/sensitive
                    # policy when explicitly targeted; do not recurse by default.
                    if resolver.resolve(relative).is_ignored:
                        skipped_count += 1
                        continue

                resolved = resolver.resolve(relative)
                if resolved.is_ignored or resolved.is_sensitive:
                    skipped_count += 1
                    continue
                if resolved.is_symlink:
                    skipped_count += 1
                    continue
                if resolved.resource_type == "directory":
                    next_dirs.append(Path(resolved.path_resolved))
                    continue
                if resolved.resource_type != "file":
                    skipped_count += 1
                    continue

                if name_pattern and not _matches_name(
                    child.name,
                    name_pattern,
                    case_sensitive,
                ):
                    continue
                if text_pattern:
                    matched, line_number, line_preview, reason = _matches_text(
                        Path(resolved.path_resolved),
                        text_pattern,
                        case_sensitive,
                    )
                    if reason is not None:
                        skipped_count += 1
                    if not matched:
                        continue
                else:
                    line_number = None
                    line_preview = None

                if len(matches) >= limit:
                    truncated = True
                    break
                matches.append(
                    {
                        "path": resolved.workspace_relative_path,
                        "type": "file",
                        "line_number": line_number,
                        "line_preview": line_preview,
                    }
                )
            if truncated:
                break
            stack.extend(reversed(next_dirs))

        data = {
            "path": base.workspace_relative_path,
            "matches": matches,
            "match_count": len(matches),
            "truncated": truncated,
            "skipped_count": skipped_count,
            "max_results": limit,
            "name_pattern": name_pattern,
            "text_pattern": text_pattern,
            "case_sensitive": bool(case_sensitive),
        }
        return ToolResult.ok(
            data=data,
            message=f"Found {len(matches)} matching files.",
        )


def _validate_search_root(resolved: ResolvedPath) -> ToolResult | None:
    if not resolved.valid or not resolved.is_inside_workspace or resolved.is_blocked:
        return ToolResult.fail(
            resolved.reason or "Invalid workspace path.",
            code=resolved.error_code,
            data=resolved.to_dict(),
        )
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
        )
    if resolved.resource_type != "directory":
        return ToolResult.fail(
            f"Path is not a directory: {resolved.workspace_relative_path}",
            code=ToolErrorCode.NOT_A_DIRECTORY.value,
        )
    return None


def _matches_name(name: str, pattern: str, case_sensitive: bool) -> bool:
    left = name if case_sensitive else name.lower()
    right = pattern if case_sensitive else pattern.lower()
    return fnmatch.fnmatchcase(left, right)


def _matches_text(
    path: Path,
    pattern: str,
    case_sensitive: bool,
) -> tuple[bool, int | None, str | None, str | None]:
    try:
        stat = path.stat()
        if stat.st_size > MAX_TEXT_SCAN_BYTES:
            return False, None, None, "file_too_large"
        with path.open("rb") as handle:
            sample = handle.read(SAMPLE_BYTES)
            if b"\x00" in sample:
                return False, None, None, "binary_file"
            handle.seek(0)
            text_handle = _open_text_stream(handle)
            if text_handle is None:
                return False, None, None, "decode_error"
            with text_handle:
                for line_number, line in enumerate(text_handle, start=1):
                    haystack = line if case_sensitive else line.lower()
                    needle = pattern if case_sensitive else pattern.lower()
                    if needle in haystack:
                        return (
                            True,
                            line_number,
                            _line_preview(line),
                            None,
                        )
    except (OSError, UnicodeError):
        return False, None, None, "read_error"
    return False, None, None, None


def _open_text_stream(binary_handle: Any):
    import io

    return io.TextIOWrapper(
        binary_handle,
        encoding="utf-8",
        errors="strict",
        newline="",
    )


def _line_preview(line: str) -> str:
    text = line.rstrip("\r\n")
    if len(text) <= MAX_LINE_PREVIEW_CHARS:
        return text
    return text[:MAX_LINE_PREVIEW_CHARS] + "..."


def _sorted_children(path: Path) -> list[Path]:
    return sorted(
        path.iterdir(),
        key=lambda item: (item.name.lower(), item.name),
    )


def _result_limit(value: int) -> int:
    try:
        requested = int(value)
    except (TypeError, ValueError):
        requested = DEFAULT_MAX_RESULTS
    return max(min(requested, MAX_RESULTS_HARD_LIMIT), 1)


def _optional_pattern(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _relative_path(path: Path, root: Path) -> str:
    relative = path.relative_to(root)
    return "." if str(relative) == "." else str(relative).replace("\\", "/")


def _workspace_root(value: str | Path | None) -> Path:
    if value is not None:
        return Path(value).resolve()
    return get_settings().workspace_root.resolve()


__all__ = [
    "DEFAULT_MAX_RESULTS",
    "MAX_TEXT_SCAN_BYTES",
    "FindFilesTool",
]
