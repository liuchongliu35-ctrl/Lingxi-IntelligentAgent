from __future__ import annotations

from typing import Any, Generator

from src.models.base_model import BaseModel


class MockModel(BaseModel):
    """Fallback model used when no external provider is configured."""

    def generate(self, prompt: str, **kwargs: Any) -> str:
        return (
            "当前未配置真实大模型，已使用 MockModel 返回占位响应。"
            "这说明基础链路已跑通，后续配置模型密钥后即可替换为真实生成结果。"
        )

    def stream_generate(self, prompt: str, **kwargs: Any) -> Generator[str, None, None]:
        yield self.generate(prompt, **kwargs)
