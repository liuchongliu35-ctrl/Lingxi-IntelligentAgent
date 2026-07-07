from __future__ import annotations

from pathlib import Path

from src.core.config import get_settings
from src.tools.base import ToolResult


class FileWriter:
    """Write files inside the configured workspace only."""

    def run(self, content: str, file_path: str, overwrite: bool = False) -> ToolResult:
        settings = get_settings()
        if not settings.enable_file_write:
            return ToolResult.fail("File writing is disabled by configuration.", code="file_write_disabled")

        root = settings.workspace_root.resolve()
        target = (root / file_path).resolve() if not Path(file_path).is_absolute() else Path(file_path).resolve()

        if root not in [target, *target.parents]:
            return ToolResult.fail(f"Refusing to write outside workspace: {target}", code="outside_workspace")

        if target.exists() and not overwrite:
            return ToolResult.fail(
                f"File already exists: {target}. Pass overwrite=True to replace it.",
                code="file_exists",
            )

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return ToolResult.ok(data={"path": str(target)}, message=f"File written: {target}")
