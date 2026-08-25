from __future__ import annotations

from typing import Any

from src.tools.base import ToolResult
from src.tools.errors import ToolErrorCode


class CodeExecutor:
    """Protocol shell for future controlled code execution.

    Tools V1 deliberately does not provide a Python sandbox. ReActExecutor should
    use command_tool for approved local test commands instead.
    """

    def run(self, code: str, timeout: int = 10, **kwargs: Any) -> ToolResult:
        del kwargs
        return ToolResult.fail(
            "code_executor is disabled in Tools V1; use command_tool for approved commands.",
            code=ToolErrorCode.TOOL_DISABLED.value,
            data={
                "enabled": False,
                "implemented": False,
                "code_chars": len(str(code or "")),
                "timeout": timeout,
            },
        )


__all__ = ["CodeExecutor"]
