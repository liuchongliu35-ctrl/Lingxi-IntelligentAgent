from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.core.config import get_settings
from src.tools.base import ToolResult
from src.tools.errors import ToolErrorCode
from src.tools.protocol import ToolCallOptions

from .path_resolver import PathResolver, ResolvedPath


DEFAULT_READ_FILE_SMALL_BYTES = 64 * 1024
DEFAULT_READ_FILE_MEDIUM_BYTES = 512 * 1024
DEFAULT_READ_FILE_HARD_BYTES = 8 * 1024 * 1024
DEFAULT_READ_FILE_PREVIEW_CHARS = 4000
DEFAULT_READ_FILE_RANGE_MAX_LINES = 400
BINARY_SAMPLE_BYTES = 8192
READ_CHUNK_BYTES = 1024 * 1024
FALLBACK_ENCODINGS = ("utf-8-sig", "gb18030", "cp1252")


@dataclass(frozen=True)
class ReadFileLimits:
    small_bytes: int = DEFAULT_READ_FILE_SMALL_BYTES
    medium_bytes: int = DEFAULT_READ_FILE_MEDIUM_BYTES
    hard_bytes: int = DEFAULT_READ_FILE_HARD_BYTES
    preview_chars: int = DEFAULT_READ_FILE_PREVIEW_CHARS
    range_max_lines: int = DEFAULT_READ_FILE_RANGE_MAX_LINES

    def __post_init__(self) -> None:
        object.__setattr__(self, "small_bytes", max(int(self.small_bytes), 1))
        object.__setattr__(self, "medium_bytes", max(int(self.medium_bytes), 1))
        object.__setattr__(self, "hard_bytes", max(int(self.hard_bytes), 1))
        object.__setattr__(self, "preview_chars", max(int(self.preview_chars), 1))
        object.__setattr__(
            self,
            "range_max_lines",
            max(int(self.range_max_lines), 1),
        )
        if self.small_bytes > self.medium_bytes:
            object.__setattr__(self, "small_bytes", self.medium_bytes)
        if self.medium_bytes > self.hard_bytes:
            object.__setattr__(self, "medium_bytes", self.hard_bytes)


class ReadFileTool:
    """Read ordinary workspace text files within configured limits."""

    def __init__(self, limits: ReadFileLimits | None = None) -> None:
        self.limits = limits or ReadFileLimits()

    def run(
        self,
        path: str,
        encoding: str = "utf-8",
        max_bytes: int | None = None,
        observation_mode: str | None = None,
        *,
        workspace_root: str | Path | None = None,
        tool_call_options: ToolCallOptions | None = None,
    ) -> ToolResult:
        del observation_mode
        root = _workspace_root(workspace_root)
        resolver = PathResolver(root)
        resolved = resolver.resolve(path)
        allow_sensitive = bool(
            resolved.is_sensitive
            and tool_call_options is not None
            and tool_call_options.has_confirmation_ticket
        )
        validation = _validate_read_target(resolved, allow_sensitive=allow_sensitive)
        if validation is not None:
            return validation

        file_path = Path(resolved.path_resolved)
        metadata = _base_data(resolved, self.limits)
        size_bytes = int(metadata["size_bytes"])
        try:
            sample = _read_sample(file_path)
        except OSError as exc:
            return ToolResult.fail(
                f"Unable to read file metadata: {resolved.workspace_relative_path}",
                code=ToolErrorCode.PERMISSION_DENIED.value,
                data={**metadata, "read_error": str(exc)},
            )
        if _is_binary_sample(sample):
            return ToolResult.fail(
                f"Binary file is not supported by read_file: {resolved.workspace_relative_path}",
                code=ToolErrorCode.BINARY_FILE_NOT_SUPPORTED.value,
                data={
                    **metadata,
                    "encoding": None,
                    "line_count": None,
                    "content": None,
                    "content_preview": None,
                    "content_truncated": False,
                    "content_hash": None,
                    "recommended_tools": [],
                },
            )

        if size_bytes > self.limits.hard_bytes:
            return _file_too_large(
                metadata,
                reason="hard_limit_exceeded",
                line_count=None,
            )
        if size_bytes > self.limits.medium_bytes:
            line_count = _count_lines_bytes(file_path)
            return _file_too_large(
                metadata,
                reason="medium_limit_exceeded",
                line_count=line_count,
            )

        try:
            raw_content = file_path.read_bytes()
        except OSError as exc:
            return ToolResult.fail(
                f"Unable to read file: {resolved.workspace_relative_path}",
                code=ToolErrorCode.PERMISSION_DENIED.value,
                data={**metadata, "read_error": str(exc)},
            )

        decoded = _decode_text(raw_content, encoding)
        if decoded["error"] is not None:
            return ToolResult.fail(
                f"Unable to decode file as text: {resolved.workspace_relative_path}",
                code=ToolErrorCode.ENCODING_ERROR.value,
                data={
                    **metadata,
                    "encoding": None,
                    "line_count": None,
                    "content": None,
                    "content_preview": None,
                    "content_truncated": False,
                    "content_hash": None,
                    "decode_error": decoded["error"],
                    "attempted_encodings": decoded["attempted_encodings"],
                },
            )

        text = str(decoded["text"])
        returned_text = text
        content_truncated = False
        requested_max = _normalize_max_bytes(max_bytes)
        if requested_max is not None and len(raw_content) > requested_max:
            returned_text = _truncate_by_encoded_bytes(
                text,
                requested_max,
                str(decoded["encoding"]),
            )
            content_truncated = True

        data = {
            **metadata,
            "encoding": decoded["encoding"],
            "line_count": _line_count(text),
            "content": returned_text,
            "content_preview": _preview(text, self.limits.preview_chars),
            "content_truncated": content_truncated,
            "content_hash": hashlib.sha256(raw_content).hexdigest(),
            "hash_algorithm": "sha256",
            "read_strategy": _read_strategy(size_bytes, self.limits),
            "requested_max_bytes": requested_max,
        }
        return ToolResult.ok(
            data=data,
            message=f"Read file {resolved.workspace_relative_path}.",
        )


class ReadFileChunkTool:
    """Read a bounded 1-based line range from a workspace text file."""

    def __init__(self, limits: ReadFileLimits | None = None) -> None:
        self.limits = limits or ReadFileLimits()

    def run(
        self,
        path: str,
        start_line: int,
        line_count: int,
        encoding: str = "utf-8",
        *,
        workspace_root: str | Path | None = None,
        tool_call_options: ToolCallOptions | None = None,
    ) -> ToolResult:
        return _read_range(
            path=path,
            start_line=start_line,
            line_count=line_count,
            encoding=encoding,
            mode="chunk",
            limits=self.limits,
            workspace_root=workspace_root,
            tool_call_options=tool_call_options,
        )


class ReadFileHeadTool:
    """Read the first bounded number of lines from a workspace text file."""

    def __init__(self, limits: ReadFileLimits | None = None) -> None:
        self.limits = limits or ReadFileLimits()

    def run(
        self,
        path: str,
        line_count: int,
        encoding: str = "utf-8",
        *,
        workspace_root: str | Path | None = None,
        tool_call_options: ToolCallOptions | None = None,
    ) -> ToolResult:
        return _read_range(
            path=path,
            start_line=1,
            line_count=line_count,
            encoding=encoding,
            mode="head",
            limits=self.limits,
            workspace_root=workspace_root,
            tool_call_options=tool_call_options,
        )


class ReadFileTailTool:
    """Read the last bounded number of lines from a workspace text file."""

    def __init__(self, limits: ReadFileLimits | None = None) -> None:
        self.limits = limits or ReadFileLimits()

    def run(
        self,
        path: str,
        line_count: int,
        encoding: str = "utf-8",
        *,
        workspace_root: str | Path | None = None,
        tool_call_options: ToolCallOptions | None = None,
    ) -> ToolResult:
        return _read_range(
            path=path,
            start_line=1,
            line_count=line_count,
            encoding=encoding,
            mode="tail",
            limits=self.limits,
            workspace_root=workspace_root,
            tool_call_options=tool_call_options,
        )


def _read_range(
    *,
    path: str,
    start_line: int,
    line_count: int,
    encoding: str,
    mode: str,
    limits: ReadFileLimits,
    workspace_root: str | Path | None,
    tool_call_options: ToolCallOptions | None,
) -> ToolResult:
    if mode not in {"chunk", "head", "tail"}:
        return ToolResult.fail(
            f"Unsupported range mode: {mode}",
            code=ToolErrorCode.INVALID_ARGS.value,
        )
    normalized_start = _positive_int(start_line)
    requested_count = _positive_int(line_count)
    if normalized_start is None or requested_count is None:
        return ToolResult.fail(
            "start_line and line_count must be positive integers.",
            code=ToolErrorCode.INVALID_ARGS.value,
        )

    root = _workspace_root(workspace_root)
    resolver = PathResolver(root)
    resolved = resolver.resolve(path)
    allow_sensitive = bool(
        resolved.is_sensitive
        and tool_call_options is not None
        and tool_call_options.has_confirmation_ticket
    )
    validation = _validate_read_target(resolved, allow_sensitive=allow_sensitive)
    if validation is not None:
        return validation

    file_path = Path(resolved.path_resolved)
    metadata = _base_data(resolved, limits)
    try:
        sample = _read_sample(file_path)
    except OSError as exc:
        return ToolResult.fail(
            f"Unable to read file metadata: {resolved.workspace_relative_path}",
            code=ToolErrorCode.PERMISSION_DENIED.value,
            data={**metadata, "read_error": str(exc)},
        )
    if _is_binary_sample(sample):
        return ToolResult.fail(
            f"Binary file is not supported by line reads: {resolved.workspace_relative_path}",
            code=ToolErrorCode.BINARY_FILE_NOT_SUPPORTED.value,
            data=_range_failure_data(
                metadata,
                encoding=None,
                requested_start=normalized_start,
                requested_count=requested_count,
            ),
        )

    decoded_sample = _decode_text(sample, encoding)
    if decoded_sample["error"] is not None and sample:
        return ToolResult.fail(
            f"Unable to decode file as text: {resolved.workspace_relative_path}",
            code=ToolErrorCode.ENCODING_ERROR.value,
            data={
                **_range_failure_data(
                    metadata,
                    encoding=None,
                    requested_start=normalized_start,
                    requested_count=requested_count,
                ),
                "decode_error": decoded_sample["error"],
                "attempted_encodings": decoded_sample["attempted_encodings"],
            },
        )

    selected_encoding = str(decoded_sample["encoding"] or encoding or "utf-8")
    try:
        if mode == "tail":
            lines, total_lines = _read_tail_lines(
                file_path,
                selected_encoding,
                requested_count,
                limits.range_max_lines,
            )
            actual_start = total_lines - len(lines) + 1 if lines else 1
            actual_end = total_lines if lines else 0
            has_more_before = bool(total_lines > len(lines))
            has_more_after = False
        else:
            effective_count, capped = _bounded_line_count(
                requested_count,
                limits.range_max_lines,
            )
            lines, has_more_after = _read_forward_lines(
                file_path,
                selected_encoding,
                normalized_start,
                effective_count,
            )
            actual_start = normalized_start
            actual_end = (
                normalized_start + len(lines) - 1
                if lines
                else max(normalized_start - 1, 0)
            )
            has_more_before = bool(normalized_start > 1 and metadata["size_bytes"] > 0)
    except (OSError, UnicodeDecodeError, LookupError) as exc:
        return ToolResult.fail(
            f"Unable to read line range: {resolved.workspace_relative_path}",
            code=ToolErrorCode.ENCODING_ERROR.value
            if isinstance(exc, (UnicodeDecodeError, LookupError))
            else ToolErrorCode.PERMISSION_DENIED.value,
            data={
                **_range_failure_data(
                    metadata,
                    encoding=selected_encoding,
                    requested_start=normalized_start,
                    requested_count=requested_count,
                ),
                "read_error": str(exc),
            },
        )

    effective_count, capped = _bounded_line_count(
        requested_count,
        limits.range_max_lines,
    )
    content = "".join(lines)
    data = {
        **metadata,
        "encoding": selected_encoding,
        "start_line": actual_start,
        "end_line": actual_end,
        "line_count": len(lines),
        "requested_start_line": normalized_start,
        "requested_line_count": requested_count,
        "effective_line_count": effective_count,
        "line_count_limit": limits.range_max_lines,
        "line_count_capped": capped,
        "content": content,
        "has_more_before": has_more_before,
        "has_more_after": has_more_after,
        "mode": mode,
    }
    return ToolResult.ok(
        data=data,
        message=(
            f"Read {mode} lines {actual_start}-{actual_end} "
            f"from {resolved.workspace_relative_path}."
        ),
    )


def _read_forward_lines(
    path: Path,
    encoding: str,
    start_line: int,
    line_count: int,
) -> tuple[list[str], bool]:
    lines: list[str] = []
    has_more_after = False
    with path.open("r", encoding=encoding, errors="strict", newline="") as handle:
        for current_line, line in enumerate(handle, start=1):
            if current_line < start_line:
                continue
            if len(lines) < line_count:
                lines.append(line)
                continue
            has_more_after = True
            break
    return lines, has_more_after


def _read_tail_lines(
    path: Path,
    encoding: str,
    requested_count: int,
    max_lines: int,
) -> tuple[list[str], int]:
    from collections import deque

    effective_count, _ = _bounded_line_count(requested_count, max_lines)
    lines: deque[str] = deque(maxlen=effective_count)
    total_lines = 0
    with path.open("r", encoding=encoding, errors="strict", newline="") as handle:
        for line in handle:
            total_lines += 1
            lines.append(line)
    return list(lines), total_lines


def _range_failure_data(
    metadata: dict[str, Any],
    *,
    encoding: str | None,
    requested_start: int,
    requested_count: int,
) -> dict[str, Any]:
    return {
        **metadata,
        "encoding": encoding,
        "start_line": requested_start,
        "end_line": max(requested_start - 1, 0),
        "line_count": 0,
        "requested_start_line": requested_start,
        "requested_line_count": requested_count,
        "content": None,
        "has_more_before": False,
        "has_more_after": False,
    }


def _bounded_line_count(value: int, maximum: int) -> tuple[int, bool]:
    effective = min(max(int(value), 1), max(int(maximum), 1))
    return effective, effective != int(value)


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    return normalized if normalized >= 1 else None


def _validate_read_target(
    resolved: ResolvedPath,
    *,
    allow_sensitive: bool,
) -> ToolResult | None:
    if not resolved.valid or not resolved.is_inside_workspace:
        return _path_failure(resolved)
    if resolved.is_blocked and not allow_sensitive:
        return _path_failure(resolved)
    if resolved.is_ignored and resolved.resource_type == "directory":
        return ToolResult.fail(
            f"Directory is ignored by policy: {resolved.workspace_relative_path}",
            code=ToolErrorCode.DIRECTORY_IGNORED.value,
            data=_base_info(resolved),
        )
    if not resolved.exists:
        return ToolResult.fail(
            f"Path not found: {resolved.workspace_relative_path}",
            code=ToolErrorCode.FILE_NOT_FOUND.value,
            data=_base_info(resolved),
        )
    if resolved.resource_type != "file":
        return ToolResult.fail(
            f"Path is not a file: {resolved.workspace_relative_path}",
            code=ToolErrorCode.NOT_A_FILE.value,
            data=_base_info(resolved),
        )
    if resolved.is_symlink:
        return ToolResult.fail(
            f"Symlink reads are not supported: {resolved.workspace_relative_path}",
            code=ToolErrorCode.SENSITIVE_PATH_BLOCKED.value,
            data=_base_info(resolved),
        )
    return None


def _path_failure(resolved: ResolvedPath) -> ToolResult:
    return ToolResult.fail(
        resolved.reason or "Invalid workspace path.",
        code=resolved.error_code,
        data=resolved.to_dict(),
    )


def _base_data(resolved: ResolvedPath, limits: ReadFileLimits) -> dict[str, Any]:
    data = _base_info(resolved)
    path = Path(resolved.path_resolved)
    stat = path.stat()
    data.update(
        {
            "size_bytes": stat.st_size,
            "small_limit_bytes": limits.small_bytes,
            "medium_limit_bytes": limits.medium_bytes,
            "hard_limit_bytes": limits.hard_bytes,
        }
    )
    return data


def _base_info(resolved: ResolvedPath) -> dict[str, Any]:
    return {
        "path": resolved.workspace_relative_path,
        "type": resolved.resource_type,
        "exists": resolved.exists,
        "is_sensitive": resolved.is_sensitive,
        "is_ignored": resolved.is_ignored,
        "is_symlink": resolved.is_symlink,
    }


def _read_sample(path: Path) -> bytes:
    with path.open("rb") as handle:
        return handle.read(BINARY_SAMPLE_BYTES)


def _is_binary_sample(sample: bytes) -> bool:
    return b"\x00" in sample


def _count_lines_bytes(path: Path) -> int:
    count = 0
    last_byte = b""
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(READ_CHUNK_BYTES), b""):
            count += chunk.count(b"\n")
            if chunk:
                last_byte = chunk[-1:]
    if path.stat().st_size > 0 and last_byte != b"\n":
        count += 1
    return count


def _file_too_large(
    metadata: dict[str, Any],
    *,
    reason: str,
    line_count: int | None,
) -> ToolResult:
    return ToolResult.fail(
        f"File is too large for read_file: {metadata.get('path')}",
        code=ToolErrorCode.FILE_TOO_LARGE.value,
        data={
            **metadata,
            "encoding": None,
            "line_count": line_count,
            "content": None,
            "content_preview": None,
            "content_truncated": False,
            "content_hash": None,
            "too_large_reason": reason,
            "recommended_tools": [
                "read_file_chunk",
                "read_file_head",
                "read_file_tail",
            ],
        },
    )


def _decode_text(raw_content: bytes, requested_encoding: str | None) -> dict[str, Any]:
    encodings = _candidate_encodings(requested_encoding)
    errors: list[str] = []
    for encoding in encodings:
        try:
            return {
                "text": raw_content.decode(encoding),
                "encoding": encoding,
                "error": None,
                "attempted_encodings": encodings,
            }
        except (LookupError, UnicodeDecodeError) as exc:
            errors.append(f"{encoding}: {exc}")
    return {
        "text": None,
        "encoding": None,
        "error": "; ".join(errors),
        "attempted_encodings": encodings,
    }


def _candidate_encodings(requested_encoding: str | None) -> list[str]:
    first = str(requested_encoding or "utf-8").strip() or "utf-8"
    result = [first]
    for encoding in FALLBACK_ENCODINGS:
        if encoding.lower() not in {item.lower() for item in result}:
            result.append(encoding)
    return result


def _line_count(text: str) -> int:
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def _preview(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(limit - 3, 0)] + "..."


def _read_strategy(size_bytes: int, limits: ReadFileLimits) -> str:
    if size_bytes <= limits.small_bytes:
        return "small_full"
    return "medium_full"


def _normalize_max_bytes(value: int | None) -> int | None:
    if value is None:
        return None
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    return max(normalized, 1)


def _truncate_by_encoded_bytes(text: str, max_bytes: int, encoding: str) -> str:
    result: list[str] = []
    total = 0
    for char in text:
        encoded = char.encode(encoding, errors="strict")
        if total + len(encoded) > max_bytes:
            break
        result.append(char)
        total += len(encoded)
    return "".join(result)


def _workspace_root(value: str | Path | None) -> Path:
    if value is not None:
        return Path(value).resolve()
    return get_settings().workspace_root.resolve()


__all__ = [
    "DEFAULT_READ_FILE_HARD_BYTES",
    "DEFAULT_READ_FILE_MEDIUM_BYTES",
    "DEFAULT_READ_FILE_PREVIEW_CHARS",
    "DEFAULT_READ_FILE_RANGE_MAX_LINES",
    "DEFAULT_READ_FILE_SMALL_BYTES",
    "ReadFileChunkTool",
    "ReadFileHeadTool",
    "ReadFileLimits",
    "ReadFileTailTool",
    "ReadFileTool",
]
