from __future__ import annotations

from typing import Any, Dict, Generator

from src.core.config import get_settings
from src.models.base_model import BaseModel
from src.models.mock_model import MockModel


class ModelManager:
    """Manage chat model providers behind a stable interface."""

    def __init__(self, model_name: str | None = None):
        settings = get_settings()
        self.model_name = (model_name or settings.model_name or "mock").lower()
        self.model = self._create_model(self.model_name)

    def _create_model(self, model_name: str) -> BaseModel:
        if model_name == "mock":
            return MockModel()

        if model_name == "doubao":
            from src.models.doubao_model import DoubaoModel

            return DoubaoModel()

        if model_name == "qianwen":
            from src.models.qianwen_model import QianwenModel

            return QianwenModel()

        if model_name == "openai":
            from src.models.openai_model import OpenAIModel

            return OpenAIModel()

        raise ValueError(f"Unsupported model provider: {model_name}")

    def generate(self, prompt: str, **kwargs: Any) -> str:
        try:
            return self.model.generate(prompt, **kwargs)
        except Exception as exc:
            return f"Model call failed: {exc}"

    def stream_generate(self, prompt: str, **kwargs: Any) -> Generator[str, None, None]:
        try:
            yield from self.model.stream_generate(prompt, **kwargs)
        except Exception as exc:
            yield f"Model stream call failed: {exc}"

    def health_check(self) -> bool:
        return self.model.health_check()

    def get_model_info(self) -> Dict[str, Any]:
        return {
            "model_name": self.model_name,
            "model": self.model.__class__.__name__,
            "healthy": self.health_check(),
        }
