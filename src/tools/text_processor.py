from __future__ import annotations

import re
from collections import Counter
from typing import Any

from src.tools.base import ToolResult
from src.tools.errors import ToolErrorCode


class TextProcessor:
    """Rule-based text formatting, keyword extraction, and simple statistics."""

    def run(
        self,
        text: str,
        operation: str = "summary",
        max_length: int = 200,
        top_n: int = 10,
    ) -> ToolResult:
        if not isinstance(text, str):
            return ToolResult.fail(
                "text must be a string.",
                code=ToolErrorCode.INVALID_ARGS.value,
                data={"text_type": type(text).__name__},
            )
        normalized_operation = str(operation or "summary").strip().lower()
        normalized_text = _format_text(text)

        if normalized_operation in {"format", "normalize"}:
            data = _base_data("format", text, normalized_text)
            data["text"] = normalized_text
            return ToolResult.ok(data=data, message=normalized_text)

        if normalized_operation == "keywords":
            limit = _positive_int(top_n, 10)
            words = re.findall(r"[\w\u4e00-\u9fff]{2,}", normalized_text.lower())
            keywords = [
                {"keyword": word, "count": count}
                for word, count in Counter(words).most_common(limit)
            ]
            data = _base_data("keywords", text, normalized_text)
            data.update({"keywords": keywords, "top_n": limit})
            return ToolResult.ok(
                data=data,
                message=", ".join(item["keyword"] for item in keywords),
            )

        if normalized_operation in {"stats", "statistics"}:
            words = re.findall(r"[\w\u4e00-\u9fff]+", normalized_text)
            lines = [] if not text else text.splitlines()
            data = _base_data("statistics", text, normalized_text)
            data["statistics"] = {
                "chars": len(text),
                "normalized_chars": len(normalized_text),
                "words": len(words),
                "lines": len(lines),
            }
            return ToolResult.ok(data=data, message=str(data["statistics"]))

        if normalized_operation in {"summary", "truncate"}:
            limit = _positive_int(max_length, 200)
            summary, truncated = _truncate(normalized_text, limit)
            data = _base_data("rule_summary", text, normalized_text)
            data.update(
                {
                    "summary": summary,
                    "max_length": limit,
                    "truncated": truncated,
                    "quality": "rule_based_truncation",
                }
            )
            return ToolResult.ok(data=data, message=summary)

        return ToolResult.fail(
            f"Unsupported text operation: {operation}",
            code=ToolErrorCode.INVALID_ARGS.value,
            data={"operation": operation},
        )


def _base_data(operation: str, original: str, normalized: str) -> dict[str, Any]:
    return {
        "operation": operation,
        "input_chars": len(original),
        "normalized_chars": len(normalized),
    }


def _format_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    if limit <= 3:
        return text[:limit], True
    return text[: limit - 3].rstrip() + "...", True


def _positive_int(value: Any, default: int) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return default
    return max(normalized, 1)


__all__ = ["TextProcessor"]
