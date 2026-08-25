from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Generator

from src.models.protocol import ModelCallOptions, ModelCallResult, ModelStreamChunk


class BaseProvider(ABC):
    """Provider boundary that consumes the Models V1 call protocol."""

    @abstractmethod
    def generate(self, options: ModelCallOptions) -> ModelCallResult:
        raise NotImplementedError

    @abstractmethod
    def stream_generate(self, options: ModelCallOptions) -> Generator[ModelStreamChunk, None, None]:
        raise NotImplementedError
