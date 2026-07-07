from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


def _to_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    workspace_root: Path
    model_name: str
    vector_store_path: Path
    enable_code_execution: bool
    enable_file_write: bool
    default_model: str


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    workspace_root = Path(
        os.getenv("AGENT_WORKSPACE_ROOT") or Path.cwd()
    ).resolve()
    vector_store_path = Path(
        os.getenv("VECTOR_STORE_PATH") or workspace_root / "storage" / "vector_store.pkl"
    ).resolve()

    return Settings(
        workspace_root=workspace_root,
        model_name=os.getenv("MODEL_NAME", "mock").strip().lower(),
        vector_store_path=vector_store_path,
        enable_code_execution=_to_bool(os.getenv("ENABLE_CODE_EXECUTION"), default=False),
        enable_file_write=_to_bool(os.getenv("ENABLE_FILE_WRITE"), default=True),
        default_model=os.getenv("DEFAULT_CHAT_MODEL", "mock"),
    )
