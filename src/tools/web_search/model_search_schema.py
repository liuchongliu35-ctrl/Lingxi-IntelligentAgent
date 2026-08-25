from __future__ import annotations

from typing import Any


MODEL_SEARCH_SCHEMA_VERSION = "web_search.model.v1"
MODEL_SEARCH_PROMPT_VERSION = "web_search.prompt.v1"


MODEL_SEARCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["query", "summary", "results", "evidence_level", "source_quality"],
    "properties": {
        "query": {"type": "string"},
        "summary": {"type": "string"},
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["title"],
                "properties": {
                    "title": {"type": "string"},
                    # URL and optional source fields intentionally omit a
                    # scalar type so null is accepted by the small Models V1
                    # schema validator. The provider performs strict checks.
                    "url": {},
                    "snippet": {},
                    "source": {},
                    "published_at": {},
                },
            },
        },
        "evidence_level": {
            "type": "string",
            "enum": ["url_verified", "provider_reported", "model_reported", "no_url_summary"],
        },
        "source_quality": {
            "type": "string",
            "enum": ["verified_sources", "partial_sources", "summary_only", "empty"],
        },
    },
}


def build_model_search_prompt(
    *,
    query: str,
    max_results: int,
    search_depth: str,
    topic: str | None,
    include_answer: bool,
    time_range: str | None,
    start_date: str | None,
    end_date: str | None,
    include_domains: list[str],
    exclude_domains: list[str],
) -> str:
    """Build a bounded prompt without adding local files or hidden context."""
    return (
        "Perform web search for the user query using your configured web-search capability.\n"
        "Return only one JSON object matching the requested schema.\n"
        "Keep summary separate from results. For every result, provide title and, "
        "when available, url, snippet, source, and published_at.\n"
        "A model-reported URL is not independently verified. If no URL is available "
        "but a useful search summary exists, use evidence_level=no_url_summary and "
        "source_quality=summary_only. Never claim verified_sources without auditable URLs.\n\n"
        f"query: {query}\n"
        f"max_results: {max_results}\n"
        f"search_depth: {search_depth}\n"
        f"topic: {topic or 'general'}\n"
        f"include_answer: {bool(include_answer)}\n"
        f"time_range: {time_range or ''}\n"
        f"start_date: {start_date or ''}\n"
        f"end_date: {end_date or ''}\n"
        f"include_domains: {include_domains}\n"
        f"exclude_domains: {exclude_domains}\n"
    )


__all__ = [
    "MODEL_SEARCH_PROMPT_VERSION",
    "MODEL_SEARCH_SCHEMA",
    "MODEL_SEARCH_SCHEMA_VERSION",
    "build_model_search_prompt",
]
