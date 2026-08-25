from __future__ import annotations

import codecs
import difflib
import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from src.core.config import get_settings
from src.tools.base import ToolResult, _json_safe
from src.tools.errors import ToolErrorCode

from .path_resolver import PathResolver, ResolvedPath


PATCH_OPERATIONS = frozenset(
    {"replace", "insert_before", "insert_after", "delete_block"}
)
DEFAULT_PATCH_DIFF_CHARS = 4000


@dataclass(frozen=True)
class LocatedPatch:
    index: int
    operation: str
    start: int
    end: int
    insert_at: int
    old_text: str
    new_text: str
    line_start: int
    line_end: int
    replacement: str


@dataclass(frozen=True)
class PatchPlan:
    path: str
    encoding: str
    original_text: str
    patched_text: str
    content_hash_before: str
    content_hash_after: str
    patch_results: list[dict[str, Any]]
    diff_preview: str
    changed_lines: int

    def to_data(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "patch_count": len(self.patch_results),
            "applied_count": len(self.patch_results),
            "changed_lines": self.changed_lines,
            "diff_preview": self.diff_preview,
            "content_hash_before": self.content_hash_before,
            "content_hash_after": self.content_hash_after,
            "patch_results": _json_safe(self.patch_results),
        }


class PatchFileTool:
    """Apply exact, bounded text patches to an existing workspace file."""

    def __init__(self, *, diff_preview_chars: int = DEFAULT_PATCH_DIFF_CHARS) -> None:
        self.diff_preview_chars = max(int(diff_preview_chars), 1)

    def run(
        self,
        path: str,
        patches: list[dict[str, Any]],
        encoding: str = "utf-8",
        *,
        workspace_root: str | Path | None = None,
    ) -> ToolResult:
        plan_result = build_patch_plan(
            path=path,
            patches=patches,
            encoding=encoding,
            workspace_root=workspace_root,
            diff_preview_chars=self.diff_preview_chars,
        )
        if not plan_result["ok"]:
            return ToolResult.fail(
                plan_result["message"],
                code=plan_result["code"],
                data=plan_result.get("data"),
            )

        plan: PatchPlan = plan_result["plan"]
        try:
            _atomic_replace(Path(plan_result["resolved_path"]), plan.patched_text, plan.encoding)
        except OSError as exc:
            return ToolResult.fail(
                f"Unable to patch file: {plan.path}",
                code=ToolErrorCode.FILE_WRITE_FAILED.value,
                data={**plan.to_data(), "write_error": str(exc)},
            )

        return ToolResult.ok(
            data=plan.to_data(),
            message=f"Applied {len(plan.patch_results)} patch(es) to {plan.path}.",
        )


def build_patch_preview(
    *,
    path: str,
    patches: list[dict[str, Any]],
    encoding: str = "utf-8",
    workspace_root: str | Path | None = None,
    requires_confirmation: bool = False,
    diff_preview_chars: int = DEFAULT_PATCH_DIFF_CHARS,
) -> dict[str, Any]:
    result = build_patch_plan(
        path=path,
        patches=patches,
        encoding=encoding,
        workspace_root=workspace_root,
        diff_preview_chars=diff_preview_chars,
    )
    if not result["ok"]:
        return {
            "path": path,
            "requires_confirmation": bool(requires_confirmation),
            "preview_error": {
                "code": result["code"],
                "message": result["message"],
                "data": result.get("data"),
            },
        }
    plan: PatchPlan = result["plan"]
    return {
        **plan.to_data(),
        "before_hash": plan.content_hash_before,
        "after_hash": plan.content_hash_after,
        "requires_confirmation": bool(requires_confirmation),
    }


def build_patch_plan(
    *,
    path: str,
    patches: list[dict[str, Any]],
    encoding: str = "utf-8",
    workspace_root: str | Path | None = None,
    diff_preview_chars: int = DEFAULT_PATCH_DIFF_CHARS,
) -> dict[str, Any]:
    if not isinstance(patches, list) or not patches:
        return _failure(
            "patches must be a non-empty array.",
            ToolErrorCode.INVALID_ARGS.value,
            data={"path": path, "patches": patches},
        )
    encoding_result = _lookup_encoding(encoding)
    if not encoding_result["ok"]:
        return _failure(
            f"Invalid or unsupported encoding: {encoding}",
            ToolErrorCode.INVALID_ENCODING.value,
            data={"path": path, "encoding": encoding, "encoding_error": encoding_result["error"]},
        )
    normalized_encoding = encoding_result["encoding"]

    root = _workspace_root(workspace_root)
    resolved = PathResolver(root).resolve(path)
    validation = _validate_patch_target(resolved)
    if validation is not None:
        return validation

    target = Path(resolved.path_resolved)
    try:
        raw_content = target.read_bytes()
    except OSError as exc:
        return _failure(
            f"Unable to read file: {resolved.workspace_relative_path}",
            ToolErrorCode.PERMISSION_DENIED.value,
            data={**_base_data(resolved), "read_error": str(exc)},
        )
    if b"\x00" in raw_content[:8192]:
        return _failure(
            f"Binary file is not supported by patch_file: {resolved.workspace_relative_path}",
            ToolErrorCode.BINARY_FILE_NOT_SUPPORTED.value,
            data=_base_data(resolved),
        )
    try:
        original_text = raw_content.decode(normalized_encoding)
    except UnicodeDecodeError as exc:
        return _failure(
            f"Unable to decode file as text: {resolved.workspace_relative_path}",
            ToolErrorCode.ENCODING_ERROR.value,
            data={**_base_data(resolved), "encoding": normalized_encoding, "decode_error": str(exc)},
        )

    located_result = _locate_patches(original_text, patches)
    if not located_result["ok"]:
        return _failure(
            located_result["message"],
            located_result["code"],
            data={**_base_data(resolved), **located_result.get("data", {})},
        )
    located: list[LocatedPatch] = located_result["located"]
    conflict = _overlap_conflict(located)
    if conflict is not None:
        return _failure(
            "patches conflict or overlap",
            ToolErrorCode.PATCH_CONFLICT.value,
            data={**_base_data(resolved), **conflict},
        )

    patched_text = _apply_located_patches(original_text, located)
    diff_preview = _diff_preview(
        original_text,
        patched_text,
        str(resolved.workspace_relative_path),
        max(int(diff_preview_chars), 1),
    )
    patch_results = [_patch_result(item) for item in located]
    plan = PatchPlan(
        path=str(resolved.workspace_relative_path),
        encoding=normalized_encoding,
        original_text=original_text,
        patched_text=patched_text,
        content_hash_before=_sha256(raw_content),
        content_hash_after=_sha256(patched_text.encode(normalized_encoding)),
        patch_results=patch_results,
        diff_preview=diff_preview,
        changed_lines=sum(int(item["changed_lines"]) for item in patch_results),
    )
    return {
        "ok": True,
        "plan": plan,
        "resolved_path": str(target),
    }


def _validate_patch_target(resolved: ResolvedPath) -> dict[str, Any] | None:
    if not resolved.valid or not resolved.is_inside_workspace or resolved.is_blocked:
        return _failure(
            resolved.reason or "Invalid workspace path.",
            resolved.error_code,
            data=resolved.to_dict(),
        )
    if not resolved.exists:
        return _failure(
            f"Path not found: {resolved.workspace_relative_path}",
            ToolErrorCode.FILE_NOT_FOUND.value,
            data=_base_data(resolved),
        )
    if resolved.resource_type != "file":
        return _failure(
            f"Path is not a file: {resolved.workspace_relative_path}",
            ToolErrorCode.NOT_A_FILE.value,
            data=_base_data(resolved),
        )
    if resolved.is_symlink:
        return _failure(
            f"Symlink patching is not supported: {resolved.workspace_relative_path}",
            ToolErrorCode.SENSITIVE_PATH_BLOCKED.value,
            data=_base_data(resolved),
        )
    return None


def _locate_patches(text: str, patches: list[dict[str, Any]]) -> dict[str, Any]:
    located: list[LocatedPatch] = []
    for index, patch in enumerate(patches):
        if not isinstance(patch, Mapping):
            return _patch_failure(index, "patch must be an object", ToolErrorCode.INVALID_ARGS.value)
        operation = str(patch.get("operation") or "").strip()
        if operation not in PATCH_OPERATIONS:
            return _patch_failure(
                index,
                f"Unsupported patch operation: {operation}",
                ToolErrorCode.INVALID_ARGS.value,
            )
        old_text = patch.get("old_text")
        if not isinstance(old_text, str) or old_text == "":
            return _patch_failure(
                index,
                "old_text must be a non-empty string.",
                ToolErrorCode.INVALID_ARGS.value,
            )
        new_text = patch.get("new_text", "")
        if operation != "delete_block" and not isinstance(new_text, str):
            return _patch_failure(
                index,
                "new_text must be a string.",
                ToolErrorCode.INVALID_ARGS.value,
            )
        if operation == "delete_block":
            new_text = ""

        anchor_result = _anchor_bounds(text, patch)
        if not anchor_result["ok"]:
            return _patch_failure(index, anchor_result["message"], anchor_result["code"])
        line_result = _line_range_match(text, patch, old_text)
        if line_result is not None:
            if not line_result["ok"]:
                return _patch_failure(index, line_result["message"], line_result["code"])
            start, end = int(line_result["start"]), int(line_result["end"])
        else:
            match_result = _old_text_match(
                text,
                old_text,
                int(anchor_result["start"]),
                int(anchor_result["end"]),
                patch.get("occurrence"),
                anchored=bool(anchor_result["anchored"]),
            )
            if not match_result["ok"]:
                return _patch_failure(index, match_result["message"], match_result["code"])
            start, end = int(match_result["start"]), int(match_result["end"])

        if operation == "replace":
            insert_at = start
            replacement = str(new_text)
        elif operation == "delete_block":
            insert_at = start
            replacement = ""
        elif operation == "insert_before":
            insert_at = start
            replacement = str(new_text)
        else:
            insert_at = end
            replacement = str(new_text)

        line_start, line_end = _span_lines(text, start, end)
        located.append(
            LocatedPatch(
                index=index,
                operation=operation,
                start=start,
                end=end,
                insert_at=insert_at,
                old_text=old_text,
                new_text=str(new_text),
                line_start=line_start,
                line_end=line_end,
                replacement=replacement,
            )
        )
    return {"ok": True, "located": located}


def _anchor_bounds(text: str, patch: Mapping[str, Any]) -> dict[str, Any]:
    start = 0
    end = len(text)
    anchored = False
    before = patch.get("anchor_before")
    after = patch.get("anchor_after")
    if isinstance(before, str) and before:
        before_index = text.find(before)
        if before_index < 0:
            return {
                "ok": False,
                "code": ToolErrorCode.PATCH_ANCHOR_NOT_FOUND.value,
                "message": "anchor_before was not found",
            }
        start = before_index + len(before)
        anchored = True
    if isinstance(after, str) and after:
        after_index = text.find(after, start)
        if after_index < 0:
            return {
                "ok": False,
                "code": ToolErrorCode.PATCH_ANCHOR_NOT_FOUND.value,
                "message": "anchor_after was not found",
            }
        end = after_index
        anchored = True
    if start > end:
        return {
            "ok": False,
            "code": ToolErrorCode.PATCH_ANCHOR_NOT_FOUND.value,
            "message": "anchor range is invalid",
        }
    return {"ok": True, "start": start, "end": end, "anchored": anchored}


def _line_range_match(
    text: str,
    patch: Mapping[str, Any],
    old_text: str,
) -> dict[str, Any] | None:
    line_start = _positive_int_or_none(patch.get("line_start"))
    line_end = _positive_int_or_none(patch.get("line_end"))
    if line_start is None and line_end is None:
        return None
    if line_start is None or line_end is None or line_end < line_start:
        return {
            "ok": False,
            "code": ToolErrorCode.PATCH_LINE_MISMATCH.value,
            "message": "line_start and line_end must be a valid 1-based range",
        }
    spans = _line_spans(text)
    if line_start > len(spans) or line_end > len(spans):
        return {
            "ok": False,
            "code": ToolErrorCode.PATCH_LINE_MISMATCH.value,
            "message": "line range is outside the file",
        }
    start = spans[line_start - 1][0]
    end = spans[line_end - 1][1]
    if text[start:end] != old_text:
        return {
            "ok": False,
            "code": ToolErrorCode.PATCH_LINE_MISMATCH.value,
            "message": "line range content does not match old_text",
        }
    return {"ok": True, "start": start, "end": end}


def _old_text_match(
    text: str,
    old_text: str,
    start: int,
    end: int,
    occurrence: Any,
    *,
    anchored: bool,
) -> dict[str, Any]:
    matches = _find_occurrences(text, old_text, start, end)
    if not matches:
        return {
            "ok": False,
            "code": ToolErrorCode.PATCH_OLD_TEXT_NOT_FOUND.value,
            "message": "old_text was not found",
        }
    occurrence_value = _positive_int_or_none(occurrence)
    if occurrence is not None and occurrence_value is None:
        return {
            "ok": False,
            "code": ToolErrorCode.INVALID_ARGS.value,
            "message": "occurrence must be a positive integer",
        }
    if occurrence_value is not None:
        if occurrence_value > len(matches):
            return {
                "ok": False,
                "code": ToolErrorCode.PATCH_OLD_TEXT_NOT_FOUND.value,
                "message": "old_text occurrence was not found",
            }
        match_start, match_end = matches[occurrence_value - 1]
        return {"ok": True, "start": match_start, "end": match_end}
    if len(matches) > 1:
        return {
            "ok": False,
            "code": ToolErrorCode.PATCH_AMBIGUOUS_MATCH.value,
            "message": "old_text matched multiple locations; specify occurrence or anchors",
        }
    match_start, match_end = matches[0]
    return {"ok": True, "start": match_start, "end": match_end}


def _find_occurrences(text: str, needle: str, start: int, end: int) -> list[tuple[int, int]]:
    matches: list[tuple[int, int]] = []
    position = start
    while position <= end:
        found = text.find(needle, position, end)
        if found < 0:
            break
        matches.append((found, found + len(needle)))
        position = found + max(len(needle), 1)
    return matches


def _overlap_conflict(located: list[LocatedPatch]) -> dict[str, Any] | None:
    sorted_items = sorted(located, key=lambda item: (item.start, item.end, item.index))
    occupied: list[LocatedPatch] = []
    insert_positions: dict[int, int] = {}
    for item in sorted_items:
        if item.operation.startswith("insert_"):
            count = insert_positions.get(item.insert_at, 0)
            if count:
                return {
                    "first_patch_index": count - 1,
                    "second_patch_index": item.index,
                    "conflict": "multiple inserts at the same position",
                }
            insert_positions[item.insert_at] = item.index + 1
            continue
        for existing in occupied:
            if item.start < existing.end and item.end > existing.start:
                return {
                    "first_patch_index": existing.index,
                    "second_patch_index": item.index,
                    "conflict": "overlapping text ranges",
                }
        occupied.append(item)
    return None


def _apply_located_patches(text: str, located: list[LocatedPatch]) -> str:
    result = text
    for item in sorted(located, key=lambda patch: (patch.insert_at, patch.start), reverse=True):
        if item.operation == "insert_before" or item.operation == "insert_after":
            result = result[: item.insert_at] + item.replacement + result[item.insert_at :]
        else:
            result = result[: item.start] + item.replacement + result[item.end :]
    return result


def _patch_result(item: LocatedPatch) -> dict[str, Any]:
    removed_lines = _count_text_lines(item.old_text)
    added_lines = _count_text_lines(item.replacement)
    if item.operation.startswith("insert_"):
        affected = added_lines
    elif item.operation == "delete_block":
        affected = removed_lines
    else:
        affected = max(removed_lines, added_lines)
    return {
        "index": item.index,
        "operation": item.operation,
        "success": True,
        "line_start": item.line_start,
        "line_end": item.line_end,
        "affected_lines": affected,
        "changed_lines": affected,
    }


def _line_spans(text: str) -> list[tuple[int, int]]:
    if text == "":
        return []
    spans: list[tuple[int, int]] = []
    start = 0
    for line in text.splitlines(keepends=True):
        end = start + len(line)
        spans.append((start, end))
        start = end
    if start < len(text):
        spans.append((start, len(text)))
    return spans


def _span_lines(text: str, start: int, end: int) -> tuple[int, int]:
    spans = _line_spans(text)
    if not spans:
        return 1, 1
    start_line = 1
    end_line = len(spans)
    for index, (line_start, line_end) in enumerate(spans, start=1):
        if line_start <= start < line_end or start == line_end:
            start_line = index
            break
    target_end = max(end - 1, start)
    for index, (line_start, line_end) in enumerate(spans, start=1):
        if line_start <= target_end < line_end or target_end == line_end:
            end_line = index
            break
    return start_line, end_line


def _diff_preview(before: str, after: str, path: str, limit: int) -> str:
    diff = "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            lineterm="",
        )
    )
    if len(diff) <= limit:
        return diff
    return diff[: max(limit - 3, 0)] + "..."


def _count_text_lines(text: str) -> int:
    if text == "":
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def _atomic_replace(target: Path, content: str, encoding: str) -> None:
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding=encoding,
            newline="",
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


def _lookup_encoding(encoding: str | None) -> dict[str, Any]:
    requested = str(encoding or "utf-8").strip() or "utf-8"
    try:
        codec = codecs.lookup(requested)
        return {"ok": True, "encoding": codec.name, "error": None}
    except LookupError as exc:
        return {"ok": False, "encoding": requested, "error": str(exc)}


def _positive_int_or_none(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    return normalized if normalized >= 1 else None


def _base_data(resolved: ResolvedPath) -> dict[str, Any]:
    return {
        "path": resolved.workspace_relative_path,
        "exists": resolved.exists,
        "type": resolved.resource_type,
        "is_sensitive": resolved.is_sensitive,
        "is_ignored": resolved.is_ignored,
        "is_symlink": resolved.is_symlink,
    }


def _patch_failure(index: int, message: str, code: str) -> dict[str, Any]:
    return {
        "ok": False,
        "message": f"patch[{index}]: {message}",
        "code": code,
        "data": {"patch_index": index},
    }


def _failure(message: str, code: str, data: Any = None) -> dict[str, Any]:
    return {"ok": False, "message": message, "code": code, "data": data}


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _workspace_root(value: str | Path | None) -> Path:
    if value is not None:
        return Path(value).resolve()
    return get_settings().workspace_root.resolve()


__all__ = [
    "DEFAULT_PATCH_DIFF_CHARS",
    "PATCH_OPERATIONS",
    "PatchFileTool",
    "PatchPlan",
    "build_patch_plan",
    "build_patch_preview",
]
