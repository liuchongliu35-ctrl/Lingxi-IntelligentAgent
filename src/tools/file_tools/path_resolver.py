from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ..errors import ToolErrorCode
from .common import (
    DEFAULT_IGNORED_DIRECTORY_PATTERNS,
    DEFAULT_SENSITIVE_PATH_PATTERNS,
    is_ignored_path,
    is_sensitive_path,
    normalize_path_text,
    path_is_within,
    path_relative_to,
)


@dataclass(frozen=True)
class ResolvedPath:
    """The single path classification object shared by file tools."""

    path_original: str
    path_resolved: str
    workspace_relative_path: str | None
    exists: bool
    resource_type: str
    is_inside_workspace: bool
    is_sensitive: bool
    is_ignored: bool
    is_symlink: bool
    valid: bool = True
    is_blocked: bool = False
    error_code: str = ToolErrorCode.OK.value
    reason: str = ""

    @property
    def path(self) -> str:
        return self.workspace_relative_path or self.path_resolved

    def to_dict(self) -> dict[str, object]:
        return {
            "path_original": self.path_original,
            "path_resolved": self.path_resolved,
            "workspace_relative_path": self.workspace_relative_path,
            "exists": self.exists,
            "resource_type": self.resource_type,
            "is_inside_workspace": self.is_inside_workspace,
            "is_sensitive": self.is_sensitive,
            "is_ignored": self.is_ignored,
            "is_symlink": self.is_symlink,
            "valid": self.valid,
            "is_blocked": self.is_blocked,
            "error_code": self.error_code,
            "reason": self.reason,
        }


class PathResolver:
    """Resolve and classify workspace paths without reading file contents."""

    def __init__(
        self,
        workspace_root: str | Path | None = None,
        *,
        sensitive_paths: Iterable[str | Path] | None = None,
        blocked_paths: Iterable[str | Path] | None = None,
        ignored_paths: Iterable[str | Path] | None = None,
        sensitive_patterns: Iterable[str] | None = None,
        ignored_patterns: Iterable[str] | None = None,
    ) -> None:
        self.workspace_root = _resolve_root(workspace_root or ".")
        self.sensitive_paths = tuple(_normalize_configured_path(item) for item in (sensitive_paths or ()))
        self.blocked_paths = tuple(_normalize_configured_path(item) for item in (blocked_paths or ()))
        self.ignored_paths = tuple(_normalize_configured_path(item) for item in (ignored_paths or ()))
        self.sensitive_patterns = tuple(
            str(item).strip()
            for item in (sensitive_patterns or DEFAULT_SENSITIVE_PATH_PATTERNS)
            if str(item).strip()
        )
        self.ignored_patterns = tuple(
            str(item).strip()
            for item in (ignored_patterns or DEFAULT_IGNORED_DIRECTORY_PATTERNS)
            if str(item).strip()
        )

    def resolve(
        self,
        path: str | Path,
        *,
        workspace_root: str | Path | None = None,
    ) -> ResolvedPath:
        root = _resolve_root(workspace_root or self.workspace_root)
        validation_error = _validate_path_input(path)
        if validation_error is not None:
            return ResolvedPath(
                path_original=str(path),
                path_resolved="",
                workspace_relative_path=None,
                exists=False,
                resource_type="invalid",
                is_inside_workspace=False,
                is_sensitive=False,
                is_ignored=False,
                is_symlink=False,
                valid=False,
                is_blocked=True,
                error_code=ToolErrorCode.INVALID_ARGS.value,
                reason=validation_error,
            )

        original = os.fspath(path)
        candidate = Path(original).expanduser()
        lexical_path = candidate if candidate.is_absolute() else root / candidate
        resolved = lexical_path.resolve(strict=False)
        inside = path_is_within(resolved, root)
        relative = path_relative_to(resolved, root)
        is_symlink = _contains_link(lexical_path, root)
        exists = resolved.exists()
        resource_type = _resource_type(resolved, lexical_path, exists)
        relative_for_match = relative or normalize_path_text(resolved)
        configured_sensitive = self._matches_configured(
            resolved,
            relative_for_match,
            self.sensitive_paths,
            root,
        )
        configured_blocked = self._matches_configured(
            resolved,
            relative_for_match,
            self.blocked_paths,
            root,
        )
        configured_ignored = self._matches_configured(
            resolved,
            relative_for_match,
            self.ignored_paths,
            root,
        )
        sensitive = configured_sensitive or is_sensitive_path(
            relative_for_match,
            patterns=self.sensitive_patterns,
        )
        ignored = configured_ignored or is_ignored_path(
            relative_for_match,
            patterns=self.ignored_patterns,
        )

        if not inside:
            return ResolvedPath(
                path_original=original,
                path_resolved=str(resolved),
                workspace_relative_path=None,
                exists=exists,
                resource_type=resource_type,
                is_inside_workspace=False,
                is_sensitive=sensitive,
                is_ignored=ignored,
                is_symlink=is_symlink,
                is_blocked=True,
                error_code=ToolErrorCode.WORKSPACE_OUT_OF_SCOPE.value,
                reason=f"path is outside workspace: {original}",
            )
        if configured_blocked or sensitive:
            return ResolvedPath(
                path_original=original,
                path_resolved=str(resolved),
                workspace_relative_path=relative,
                exists=exists,
                resource_type=resource_type,
                is_inside_workspace=True,
                is_sensitive=sensitive,
                is_ignored=ignored,
                is_symlink=is_symlink,
                is_blocked=True,
                error_code=ToolErrorCode.SENSITIVE_PATH_BLOCKED.value,
                reason=f"sensitive or blocked path: {original}",
            )
        return ResolvedPath(
            path_original=original,
            path_resolved=str(resolved),
            workspace_relative_path=relative,
            exists=exists,
            resource_type=resource_type,
            is_inside_workspace=True,
            is_sensitive=False,
            is_ignored=ignored,
            is_symlink=is_symlink,
            is_blocked=False,
        )

    def resolve_many(
        self,
        paths: Iterable[str | Path],
        *,
        workspace_root: str | Path | None = None,
    ) -> list[ResolvedPath]:
        return [self.resolve(path, workspace_root=workspace_root) for path in paths]

    def _matches_configured(
        self,
        resolved: Path,
        relative: str,
        configured: Iterable[str],
        workspace_root: Path,
    ) -> bool:
        for item in configured:
            item_text = normalize_path_text(item).strip()
            if not item_text:
                continue
            configured_path = Path(item_text).expanduser()
            if not configured_path.is_absolute():
                configured_path = workspace_root / configured_path
            configured_resolved = configured_path.resolve(strict=False)
            configured_text = normalize_path_text(configured_resolved)
            resolved_text = normalize_path_text(resolved)
            if _has_glob(configured_text) or _has_glob(item_text):
                if _fnmatch_platform(resolved_text, configured_text):
                    return True
                if _fnmatch_platform(relative, item_text):
                    return True
                continue
            if path_is_within(resolved, configured_resolved):
                return True
        return False


def _resolve_root(value: str | Path) -> Path:
    return Path(value).expanduser().resolve(strict=False)


def _validate_path_input(value: object) -> str | None:
    if not isinstance(value, (str, Path)):
        return "path must be a string or Path"
    text = os.fspath(value)
    if not isinstance(text, str):
        return "path must be a string or Path"
    if not text.strip():
        return "path must not be empty"
    if "\x00" in text:
        return "path must not contain NUL"
    return None


def _resource_type(resolved: Path, lexical_path: Path, exists: bool) -> str:
    if exists and resolved.is_file():
        return "file"
    if exists and resolved.is_dir():
        return "directory"
    if exists:
        return "other"
    if _lexists(lexical_path):
        return "symlink"
    return "missing"


def _contains_link(path: Path, root: Path) -> bool:
    current = path if path.is_absolute() else root / path
    start = len(root.parts) if path_is_within(current, root) else 0
    parts = current.parts
    for index in range(start, len(parts)):
        component = Path(*parts[: index + 1])
        if component.is_symlink() or _is_junction(component):
            return True
    return False


def _is_junction(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", None)
    if callable(is_junction):
        try:
            return bool(is_junction())
        except OSError:
            return False
    os_is_junction = getattr(os.path, "isjunction", None)
    if callable(os_is_junction):
        try:
            return bool(os_is_junction(path))
        except OSError:
            return False
    return False


def _lexists(path: Path) -> bool:
    try:
        return os.path.lexists(path)
    except OSError:
        return False


def _normalize_configured_path(value: str | Path) -> str:
    return normalize_path_text(value).strip()


def _has_glob(value: str) -> bool:
    return any(char in value for char in "*?[]")


def _fnmatch_platform(value: str, pattern: str) -> bool:
    return fnmatch.fnmatchcase(value.lower(), pattern.lower())


__all__ = ["PathResolver", "ResolvedPath"]
