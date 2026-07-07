from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class ToolResult:
    success: bool
    data: Any = None
    message: str = ""
    error: str | None = None
    code: str | None = None

    def to_text(self) -> str:
        if self.success:
            if self.message:
                return self.message
            return str(self.data) if self.data is not None else ""
        return self.error or self.message or "Tool execution failed"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "data": self.data,
            "message": self.message,
            "error": self.error,
            "code": self.code,
        }

    @classmethod
    def ok(cls, data: Any = None, message: str = "") -> "ToolResult":
        return cls(success=True, data=data, message=message or (str(data) if data is not None else ""))

    @classmethod
    def fail(cls, error: str, code: str | None = None, data: Any = None) -> "ToolResult":
        return cls(success=False, data=data, error=error, message=error, code=code)
