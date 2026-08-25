from __future__ import annotations

from src.tools.base import ToolResult


class Translator:
    """Compatibility translation placeholder; not a real model-backed translator."""

    def run(
        self,
        text: str,
        source_language: str = "auto",
        target_language: str = "zh",
    ) -> ToolResult:
        data = {
            "text": text,
            "source_language": source_language,
            "target_language": target_language,
            "translated_text": None,
            "mock": True,
            "implemented": False,
            "provider": None,
        }
        return ToolResult.ok(
            data=data,
            message="translator is a compatibility placeholder and did not perform real translation.",
            metadata={"mock": True, "implemented": False},
        )


__all__ = ["Translator"]
