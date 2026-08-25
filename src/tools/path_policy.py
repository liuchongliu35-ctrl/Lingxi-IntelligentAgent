from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from .errors import ToolErrorCode
from .file_tools.common import DEFAULT_SENSITIVE_PATH_PATTERNS
from .file_tools.path_resolver import PathResolver


DEFAULT_SENSITIVE_NAMES = frozenset(
    {
        ".env",
        ".git",
        ".ssh",
        "id_rsa",
        "id_ed25519",
        "credentials",
        "secrets",
    }
)


@dataclass(frozen=True)
class PathPolicyResult:
    original: str
    resolved: str
    relative: str | None
    within_workspace: bool
    sensitive: bool = False
    ignored: bool = False
    is_symlink: bool = False
    resource_type: str = "unknown"
    blocked: bool = False
    code: str = ToolErrorCode.OK.value
    reason: str = ""

    @property
    def affected_resource(self) -> str:
        return self.relative or self.resolved


class PathPolicy:
    """Resolve tool resources and enforce the shared workspace boundary.

    Business-specific file rules belong to the file tools. This class only
    provides the common root, configured block lists, and conservative generic
    sensitive-name checks needed by ToolPolicy.
    """

    def __init__(
        self,
        workspace_root: str | Path | None = None,
        *,
        sensitive_paths: Iterable[str | Path] | None = None,
        blocked_paths: Iterable[str | Path] | None = None,
        ignored_paths: Iterable[str | Path] | None = None,
        sensitive_names: Iterable[str] | None = None,
    ) -> None:
        self.workspace_root = _resolve_path(workspace_root or ".")
        self.sensitive_names = frozenset(
            str(item).strip().lower()
            for item in (sensitive_names or DEFAULT_SENSITIVE_NAMES)
            if str(item).strip()
        )
        self.resolver = PathResolver(
            self.workspace_root,
            sensitive_paths=sensitive_paths,
            blocked_paths=blocked_paths,
            ignored_paths=ignored_paths,
            sensitive_patterns=tuple(DEFAULT_SENSITIVE_PATH_PATTERNS)
            + tuple(self.sensitive_names),
        )

    def resolve(
        self,
        path: str | Path,
        *,
        workspace_root: str | Path | None = None,
    ) -> Path:
        result = self.resolver.resolve(path, workspace_root=workspace_root)
        return Path(result.path_resolved or ".")

    def check(
        self,
        path: str | Path,
        *,
        workspace_root: str | Path | None = None,
    ) -> PathPolicyResult:
        result = self.resolver.resolve(path, workspace_root=workspace_root)
        if not result.valid:
            return PathPolicyResult(
                original=result.path_original,
                resolved=result.path_resolved,
                relative=result.workspace_relative_path,
                within_workspace=result.is_inside_workspace,
                blocked=True,
                code=result.error_code,
                reason=result.reason,
            )
        if not result.is_inside_workspace:
            return PathPolicyResult(
                original=result.path_original,
                resolved=result.path_resolved,
                relative=result.workspace_relative_path,
                within_workspace=False,
                sensitive=result.is_sensitive,
                ignored=result.is_ignored,
                is_symlink=result.is_symlink,
                resource_type=result.resource_type,
                blocked=True,
                code=result.error_code,
                reason=result.reason,
            )
        if result.is_blocked or result.is_sensitive:
            return PathPolicyResult(
                original=result.path_original,
                resolved=result.path_resolved,
                relative=result.workspace_relative_path,
                within_workspace=True,
                sensitive=result.is_sensitive,
                ignored=result.is_ignored,
                is_symlink=result.is_symlink,
                resource_type=result.resource_type,
                blocked=True,
                code=result.error_code,
                reason=result.reason,
            )
        return PathPolicyResult(
            original=result.path_original,
            resolved=result.path_resolved,
            relative=result.workspace_relative_path,
            within_workspace=True,
            sensitive=result.is_sensitive,
            ignored=result.is_ignored,
            is_symlink=result.is_symlink,
            resource_type=result.resource_type,
        )

    def check_many(
        self,
        paths: Iterable[str | Path],
        *,
        workspace_root: str | Path | None = None,
    ) -> list[PathPolicyResult]:
        return [
            self.check(path, workspace_root=workspace_root)
            for path in paths
        ]
def extract_path_values(
    args: Mapping[str, object],
    *,
    include_cwd: bool = False,
) -> list[str]:
    """Extract only conventional resource arguments; never inspect command text."""
    keys = ["file_path", "path", "target_path", "directory", "target_paths", "paths"]
    if include_cwd:
        keys.append("cwd")

    values: list[str] = []
    for key in keys:
        value = args.get(key)
        if isinstance(value, (str, Path)):
            values.append(str(value))
        elif isinstance(value, (list, tuple, set, frozenset)):
            values.extend(str(item) for item in value if isinstance(item, (str, Path)))
    return _unique(values)


def _resolve_path(value: str | Path) -> Path:
    return Path(value).expanduser().resolve(strict=False)


def _unique(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


__all__ = [
    "DEFAULT_SENSITIVE_NAMES",
    "PathPolicy",
    "PathPolicyResult",
    "extract_path_values",
]
