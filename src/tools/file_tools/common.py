from __future__ import annotations

import fnmatch
import os
from pathlib import Path
from typing import Iterable


DEFAULT_SENSITIVE_PATH_PATTERNS = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "id_rsa",
    "id_dsa",
    "id_ed25519",
    "credentials*",
    "secrets*",
    ".git",
)

DEFAULT_IGNORED_DIRECTORY_PATTERNS = (
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
)


def normalize_path_text(value: str | Path) -> str:
    """Normalize separators without resolving or changing path semantics."""
    return str(value).replace("\\", "/")


def is_hidden_name(name: str) -> bool:
    """Use the cross-platform hidden-name convention for listing tools."""
    text = str(name or "")
    return text.startswith(".") and text not in {".", ".."}


def is_sensitive_path(
    path: str | Path,
    *,
    patterns: Iterable[str] = DEFAULT_SENSITIVE_PATH_PATTERNS,
) -> bool:
    return _matches_path_patterns(path, patterns)


def is_ignored_path(
    path: str | Path,
    *,
    patterns: Iterable[str] = DEFAULT_IGNORED_DIRECTORY_PATTERNS,
) -> bool:
    return _matches_path_patterns(path, patterns)


def path_is_within(path: Path, root: Path) -> bool:
    """Compare paths using the platform's case, drive, and UNC rules."""
    try:
        return os.path.commonpath(
            [os.path.normcase(str(path)), os.path.normcase(str(root))]
        ) == os.path.normcase(str(root))
    except ValueError:
        return False


def path_relative_to(path: Path, root: Path) -> str | None:
    if not path_is_within(path, root):
        return None
    relative = os.path.relpath(str(path), str(root))
    return "." if relative == "." else normalize_path_text(relative)


def _matches_path_patterns(path: str | Path, patterns: Iterable[str]) -> bool:
    normalized_path = normalize_path_text(path).strip("/")
    if not normalized_path:
        return False
    parts = [part for part in normalized_path.split("/") if part]
    for raw_pattern in patterns:
        pattern = normalize_path_text(str(raw_pattern)).strip("/")
        if not pattern:
            continue
        if "/" in pattern:
            if fnmatch.fnmatchcase(normalized_path.lower(), pattern.lower()):
                return True
            if any(
                fnmatch.fnmatchcase(
                    "/".join(parts[index:]).lower(),
                    pattern.lower(),
                )
                for index in range(len(parts))
            ):
                return True
            continue
        if any(fnmatch.fnmatchcase(part.lower(), pattern.lower()) for part in parts):
            return True
    return False


__all__ = [
    "DEFAULT_IGNORED_DIRECTORY_PATTERNS",
    "DEFAULT_SENSITIVE_PATH_PATTERNS",
    "is_hidden_name",
    "is_ignored_path",
    "is_sensitive_path",
    "normalize_path_text",
    "path_is_within",
    "path_relative_to",
]
