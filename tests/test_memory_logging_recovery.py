from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.memory.config import MemoryConfig
from src.memory.context_builder import ContextBuilder
from src.memory.ids import new_message_id, new_run_id, new_session_id
from src.memory.memory_logging import REDACTED_VALUE, build_memory_log_record
from src.memory.session_manager import SessionManager
from src.memory.storage import SQLiteSessionRepository


def _make_config(tmp_path: Path) -> MemoryConfig:
    return MemoryConfig(
        database_path=tmp_path / "memory.db",
        log_path=tmp_path / "memory.log",
        max_recent_messages=2,
        summary_trigger_messages=4,
        summary_batch_messages=2,
        summary_target_chars=2000,
        max_message_content_chars=12000,
        max_event_display_chars=1200,
        max_event_payload_chars=1000,
    )


def _read_log(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_memory_log_record_redacts_sensitive_fields_and_text() -> None:
    record = build_memory_log_record(
        "persistence_warning",
        api_key="secret-key",
        nested={"authorization": "Bearer abc123", "safe": "ok"},
        message="failed with token=abc123 and password=pw",
    )
    text = json.dumps(record, ensure_ascii=False)

    assert record["api_key"] == REDACTED_VALUE
    assert record["nested"]["authorization"] == REDACTED_VALUE
    assert record["nested"]["safe"] == "ok"
    assert "abc123" not in text
    assert "password=pw" not in text
    assert REDACTED_VALUE in text


def test_repository_logs_session_message_run_and_context_events(tmp_path: Path) -> None:
    manager = SessionManager(config=_make_config(tmp_path))
    message, run = manager.create_user_turn(None, "hello token=secret-token")
    ContextBuilder(session_manager=manager).build(message.session_id)

    records = _read_log(manager.config.log_path)
    event_types = [record["event_type"] for record in records]
    log_text = json.dumps(records, ensure_ascii=False)

    assert "session_created" in event_types
    assert "message_appended" in event_types
    assert "run_created" in event_types
    assert "context_built" in event_types
    assert message.message_id in log_text
    assert run.run_id in log_text
    assert "secret-token" not in log_text
    assert REDACTED_VALUE in log_text


def test_recover_interrupted_runs_marks_pending_running_and_waiting_user(tmp_path: Path) -> None:
    repo = SQLiteSessionRepository(_make_config(tmp_path))
    session_id = new_session_id()
    repo.create_session(session_id)
    statuses = ["pending", "running", "waiting_user", "completed"]
    run_ids: dict[str, str] = {}
    for status in statuses:
        run_id = new_run_id()
        run_ids[status] = run_id
        repo.insert_run(
            {
                "run_id": run_id,
                "session_id": session_id,
                "user_message_id": new_message_id(),
                "status": status,
                "started_at": "2026-08-20T00:00:00Z",
            }
        )
    manager = SessionManager(repo=repo)

    count = manager.recover_interrupted_runs()

    assert count == 3
    assert repo.load_run(run_ids["pending"]).status == "interrupted"
    assert repo.load_run(run_ids["running"]).status == "interrupted"
    assert repo.load_run(run_ids["waiting_user"]).status == "interrupted"
    assert repo.load_run(run_ids["completed"]).status == "completed"
    records = _read_log(manager.config.log_path)
    assert "runs_marked_interrupted" in [record["event_type"] for record in records]
    assert "recovery_completed" in [record["event_type"] for record in records]


def test_corrupt_database_error_is_readable_and_original_is_preserved(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    config.database_path.write_text("not a sqlite database", encoding="utf-8")

    with pytest.raises(RuntimeError) as exc_info:
        SQLiteSessionRepository(config)

    assert "could not be opened" in str(exc_info.value)
    assert config.database_path.read_text(encoding="utf-8") == "not a sqlite database"
    records = _read_log(config.log_path)
    warning = [record for record in records if record["event_type"] == "persistence_warning"][-1]
    assert warning["operation"] == "open_database"
    assert warning["original_preserved"] is True
