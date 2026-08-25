from __future__ import annotations

from typing import Any, Mapping

from src.tools.web_search.tool import WebSearchTool


class SearchTool(WebSearchTool):
    """Legacy compatibility wrapper for the canonical web_search tool."""

    def __init__(
        self,
        providers_config: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(providers_config)


__all__ = ["SearchTool"]
