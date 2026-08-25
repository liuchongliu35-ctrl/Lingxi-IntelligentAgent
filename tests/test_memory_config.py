from __future__ import annotations

from pathlib import Path

import pytest

from src.memory.config import MemoryConfig


def test_default_config_uses_workspace_relative_paths_without_creating_directories(tmp_path: Path) -> None:
    config = MemoryConfig.default(tmp_path)

    assert config.database_path == (tmp_path / "storage" / "agent_memory.db").resolve()
    assert config.log_path == (tmp_path / "logs" / "memory.log").resolve()
    assert config.max_recent_messages == 10
    assert config.summary_trigger_messages == 14
    assert config.summary_batch_messages == 6
    assert not (tmp_path / "storage").exists()
    assert not (tmp_path / "logs").exists()


def test_config_validates_summary_window_and_positive_limits(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        MemoryConfig(
            database_path=tmp_path / "memory.db",
            log_path=tmp_path / "memory.log",
            max_recent_messages=10,
            summary_trigger_messages=9,
        )

    with pytest.raises(ValueError):
        MemoryConfig(
            database_path=tmp_path / "memory.db",
            log_path=tmp_path / "memory.log",
            max_event_payload_chars=0,
        )


def test_config_serializes_paths_as_strings(tmp_path: Path) -> None:
    config = MemoryConfig.default(tmp_path)
    serialized = config.to_dict()

    assert serialized["database_path"] == str(config.database_path)
    assert serialized["summary_allow_rule_fallback"] is True
