from __future__ import annotations


class Translator:
    """Translation placeholder with explicit mock behavior."""

    def run(self, text: str, source_language: str = "auto", target_language: str = "zh") -> str:
        return (
            f"[mock translation: {source_language}->{target_language}] {text}"
        )
