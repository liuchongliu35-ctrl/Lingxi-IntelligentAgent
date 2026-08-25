"""Compatibility exports for the ReAct executor package."""

from importlib import import_module
from typing import Any

__all__ = ["ReActExecutor"]


def __getattr__(name: str) -> Any:
    module = import_module("src.agent.react_executor.react_executor")
    if name == "ReActExecutor" or name.isupper():
        try:
            return getattr(module, name)
        except AttributeError:
            pass
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
