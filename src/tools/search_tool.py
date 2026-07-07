from __future__ import annotations

import os

import requests

from src.tools.base import ToolResult


class SearchTool:
    """Search provider wrapper.

    Uses Bing Web Search when BING_API_KEY is configured. Without a key it returns
    an explicit configuration message instead of scraping search pages.
    """

    def run(self, query: str, max_results: int = 5) -> ToolResult:
        api_key = os.getenv("BING_API_KEY")
        if not api_key:
            return ToolResult.fail(
                "Search is not configured. Set BING_API_KEY to enable web search.",
                code="search_not_configured",
            )

        response = requests.get(
            "https://api.bing.microsoft.com/v7.0/search",
            headers={"Ocp-Apim-Subscription-Key": api_key},
            params={"q": query, "count": max_results},
            timeout=10,
        )
        response.raise_for_status()
        items = response.json().get("webPages", {}).get("value", [])
        if not items:
            return ToolResult.ok(data=[], message=f"No search results found for: {query}")
        text = "\n\n".join(
            f"{idx}. {item.get('name', '')}\n{item.get('snippet', '')}\n{item.get('url', '')}"
            for idx, item in enumerate(items, 1)
        )
        return ToolResult.ok(data=items, message=text)
