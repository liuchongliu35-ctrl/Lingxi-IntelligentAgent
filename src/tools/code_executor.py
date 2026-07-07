from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from src.core.config import get_settings
from src.tools.base import ToolResult


class CodeExecutor:
    """Controlled Python execution tool.

    Disabled by default. Enable with ENABLE_CODE_EXECUTION=true only in trusted local
    development environments.
    """

    def run(self, code: str, timeout: int = 10) -> ToolResult:
        settings = get_settings()
        if not settings.enable_code_execution:
            return ToolResult.fail(
                "Code execution is disabled. Set ENABLE_CODE_EXECUTION=true to enable it in a trusted environment.",
                code="code_execution_disabled",
            )

        temp_file: str | None = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as handle:
                handle.write(code)
                temp_file = handle.name

            result = subprocess.run(
                ["python", temp_file],
                capture_output=True,
                text=True,
                timeout=min(int(timeout), 30),
                cwd=str(settings.workspace_root),
            )
            if result.returncode == 0:
                output = result.stdout.strip() or "Code executed successfully."
                return ToolResult.ok(data=output, message=output)
            error = result.stderr.strip() or f"Code failed with exit code {result.returncode}."
            return ToolResult.fail(error, code="code_execution_failed")
        except subprocess.TimeoutExpired:
            return ToolResult.fail("Code execution timed out.", code="code_execution_timeout")
        finally:
            if temp_file:
                try:
                    Path(temp_file).unlink(missing_ok=True)
                except OSError:
                    pass
