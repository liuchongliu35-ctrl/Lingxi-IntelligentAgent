from __future__ import annotations

from typing import Any, Dict

from src.tools.base import ToolResult
from src.tools.code_executor import CodeExecutor
from src.tools.document_parser import DocumentParser
from src.tools.file_writer import FileWriter
from src.tools.math_calculator import MathCalculator
from src.tools.search_tool import SearchTool
from src.tools.text_processor import TextProcessor
from src.tools.time_query import TimeQuery
from src.tools.translator import Translator


class ToolManager:
    """Register and execute tools through a stable result contract."""

    def __init__(self):
        self.tools: Dict[str, Any] = {
            "document_parser": DocumentParser(),
            "text_processor": TextProcessor(),
            "math_calculator": MathCalculator(),
            "translator": Translator(),
            "time_query": TimeQuery(),
            "search_tool": SearchTool(),
            "code_executor": CodeExecutor(),
            "file_writer": FileWriter(),
        }

    def get_tool(self, tool_name: str) -> Any | None:
        return self.tools.get(tool_name)

    def list_tools(self) -> Dict[str, str]:
        return {
            "document_parser": "Read txt, md, and pdf files.",
            "text_processor": "Summarize, extract keywords, and format text.",
            "math_calculator": "Calculate expressions and simple statistics.",
            "translator": "Mock translation placeholder.",
            "time_query": "Current time and date conversion.",
            "search_tool": "Bing web search when configured.",
            "code_executor": "Controlled Python execution, disabled by default.",
            "file_writer": "Write files inside the workspace.",
        }

    def run_tool(self, tool_name: str, **kwargs: Any) -> ToolResult:
        tool = self.get_tool(tool_name)
        if tool is None:
            return ToolResult(success=False, error=f"Tool not found: {tool_name}")
        if not hasattr(tool, "run"):
            return ToolResult(success=False, error=f"Tool has no run method: {tool_name}")

        try:
            data = tool.run(**kwargs)
            if isinstance(data, ToolResult):
                return data
            return ToolResult(success=True, data=data, message=str(data))
        except Exception as exc:
            return ToolResult(success=False, error=f"Tool {tool_name} failed: {exc}")
