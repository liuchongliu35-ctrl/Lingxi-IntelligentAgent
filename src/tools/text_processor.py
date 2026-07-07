from __future__ import annotations

import re
from collections import Counter


class TextProcessor:
    """Small deterministic text processing helper."""

    def run(self, text: str, operation: str = "summary") -> str:
        if operation == "summary":
            return self._summary(text)
        if operation == "keywords":
            return self._keywords(text)
        if operation == "format":
            return self._format(text)
        return f"Unsupported text operation: {operation}"

    def _summary(self, text: str, max_length: int = 200) -> str:
        clean = self._format(text)
        if len(clean) <= max_length:
            return clean
        return clean[:max_length].rstrip() + "..."

    def _keywords(self, text: str, top_n: int = 10) -> str:
        words = re.findall(r"[\w\u4e00-\u9fff]{2,}", text.lower())
        common = [word for word, _ in Counter(words).most_common(top_n)]
        return ", ".join(common)

    def _format(self, text: str) -> str:
        return re.sub(r"\s+", " ", text or "").strip()
