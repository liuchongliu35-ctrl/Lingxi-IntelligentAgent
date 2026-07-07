from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Generator


class BaseModel(ABC):
    """Common interface for chat-capable model providers."""

    @abstractmethod
    def generate(self, prompt: str, **kwargs: Any) -> str:
        raise NotImplementedError

    @abstractmethod
    def stream_generate(self, prompt: str, **kwargs: Any) -> Generator[str, None, None]:
        raise NotImplementedError

    def health_check(self) -> bool:
        return True
