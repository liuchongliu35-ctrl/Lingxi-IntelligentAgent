from __future__ import annotations

from pathlib import Path
import sqlite3
from types import SimpleNamespace

from src.memory.config import MemoryConfig
from src.memory.context_builder import ContextBuilder
from src.memory.event_mapper import REDACTED_VALUE
from src.memory.models import AgentRunStatus
from src.memory.runtime_adapter import RuntimeMemoryAdapter
from src.memory.session_manager import SessionManager
from src.memory.storage import SCHEMA_VERSION


class CompressionModel:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def compress_context(self, **kwargs):
        self.calls.append(dict(kwargs))
        return SimpleNamespace(
            success=True,
            compressed_text="The user is building a SQLite-backed Memory MVP; token=summary-secret.",
            source_refs=["message:early"],
            metadata={"compression_method": "single_model_call"},
            model_result=SimpleNamespace(model="acceptance-summary-model", provider="mock"),
        )


def _make_config(root: Path) -> MemoryConfig:
    return MemoryConfig(
        database_path=root / "storage" / "agent_memory.db",
        log_path=root / "logs" / "memory.log",
        max_recent_messages=4,
        summary_trigger_messages=4,
        summary_batch_messages=2,
        summary_target_chars=2000,
        max_message_content_chars=12000,
        max_event_display_chars=1200,
        max_event_payload_chars=1000,
    )


def _event(
    *,
    event_id: str,
    event_type: str,
    message: str,
    visible_to_user: bool = True,
    payload: dict[str, object] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        execution_id=f"execution_{event_id}",
        plan_id="plan_acceptance",
        type=event_type,
        message=message,
        event_id=event_id,
        task_id="task_acceptance",
        step_id="step_acceptance",
        timestamp="2026-08-21T12:00:00Z",
        visible_to_user=visible_to_user,
        payload=payload or {},
    )


def test_memory_v1_full_session_lifecycle_survives_restart(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    model = CompressionModel()
    manager = SessionManager(config=config, model_manager=model)
    adapter = RuntimeMemoryAdapter(session_manager=manager)

    first = adapter.begin_turn(
        None,
        "We are building a Memory MVP.",
        user_metadata={"entrypoint": "acceptance", "api_key": "metadata-secret"},
        session_metadata={"channel": "test", "authorization": "Bearer metadata-secret"},
    )
    adapter.record_event(
        first,
        _event(
            event_id="event_20260821_120000_aabbcc",
            event_type="tool_finished",
            message="SQLite inspection token=secret-token",
            payload={
                "tool_name": "sqlite",
                "summary": "schema is valid",
                "api_key": "do-not-store",
                "raw_observation": "do-not-store",
            },
        ),
    )
    adapter.record_event(
        first,
        _event(
            event_id="event_20260821_120001_ddeeff",
            event_type="model_step_started",
            message="hidden reasoning",
            visible_to_user=False,
            payload={"raw_model_output": "do-not-store"},
        ),
    )
    adapter.complete_turn(first, "Use SQLite as the only formal persistence source.")

    second = adapter.begin_turn(first.session_id, "Keep recent messages and summaries.")
    assert "Use SQLite as the only formal persistence source." in second.context_text
    assert "[Current User Input]" in second.context_text
    adapter.complete_turn(second, "Keep the recent window and summarize early history.")

    third = adapter.begin_turn(first.session_id, "Runtime will call the Memory facade later.")
    third_result = adapter.complete_turn(third, "The Runtime adapter is reserved for the future.")

    assert model.calls
    assert third_result.summary is not None
    assert third_result.summary.content == (
        "The user is building a SQLite-backed Memory MVP; token=***REDACTED***."
    )
    assert third_result.summary.covered_from_timeline_seq == 1
    assert third_result.summary.covered_to_timeline_seq > 1
    assert third_result.run.status == AgentRunStatus.COMPLETED.value

    other = adapter.begin_turn("other_session", "This session must be isolated.")
    assert "SQLite as the only formal persistence source." not in other.context_text
    adapter.complete_turn(other, "Isolation confirmed.", maybe_summarize=False)

    reopened = SessionManager(config=config, model_manager=model)
    reopened_adapter = RuntimeMemoryAdapter(session_manager=reopened)
    restored = reopened.load_session(first.session_id)
    restored_context = ContextBuilder(session_manager=reopened).build(first.session_id)
    timeline = reopened_adapter.get_timeline(first.session_id)
    event_items = [item for item in timeline if item.item_kind == "execution_event"]
    message_items = [item for item in timeline if item.item_kind == "message"]
    log_text = config.log_path.read_text(encoding="utf-8")

    assert restored.summary == (
        "The user is building a SQLite-backed Memory MVP; token=***REDACTED***."
    )
    assert restored.message_count == 6
    assert restored_context.summary == restored.summary
    assert len(restored_context.recent_messages) == 4
    assert restored.metadata["channel"] == "test"
    assert restored.metadata["authorization"] == REDACTED_VALUE
    assert restored.messages[0].metadata["api_key"] == REDACTED_VALUE
    assert [item.timeline_seq for item in timeline] == sorted(item.timeline_seq for item in timeline)
    assert len(event_items) == 1
    assert len(message_items) == 6
    assert event_items[0].metadata["sanitized_payload"]["api_key"] == REDACTED_VALUE
    assert event_items[0].metadata["sanitized_payload"]["raw_observation"] == REDACTED_VALUE
    assert REDACTED_VALUE in event_items[0].content
    assert "secret-token" not in event_items[0].content
    assert "hidden reasoning" not in "\n".join(item.content for item in timeline)
    assert "do-not-store" not in log_text
    assert "secret-token" not in log_text
    assert "summary-secret" not in log_text


def test_memory_v1_recovery_preserves_visible_events_and_marks_run_interrupted(
    tmp_path: Path,
) -> None:
    config = _make_config(tmp_path)
    manager = SessionManager(config=config)
    adapter = RuntimeMemoryAdapter(session_manager=manager)

    turn = adapter.begin_turn("recovery_session", "This run will be interrupted.")
    adapter.record_event(
        turn,
        _event(
            event_id="event_20260821_120002_aabbcc",
            event_type="progress_message",
            message="Progress was visible before interruption.",
        ),
    )

    restarted = SessionManager(config=config)
    assert restarted.load_session("recovery_session").message_count == 1
    assert restarted.recover_interrupted_runs() == 1

    run = restarted.repo.load_run(turn.run_id)
    timeline = restarted.get_session_timeline("recovery_session")

    assert run is not None
    assert run.status == AgentRunStatus.INTERRUPTED.value
    assert [item.item_kind for item in timeline] == ["message", "execution_event"]
    assert timeline[1].content == "Progress was visible before interruption."


def test_memory_v1_health_reports_database_and_schema_state(tmp_path: Path) -> None:
    adapter = RuntimeMemoryAdapter(
        session_manager=SessionManager(config=_make_config(tmp_path))
    )

    health = adapter.health()

    assert health.ok is True
    assert health.schema_version == 1
    assert health.session_count == 0
    assert health.database_path.endswith("agent_memory.db")


def test_memory_v1_schema_mismatch_is_readable_and_preserves_database(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    config.database_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(config.database_path) as connection:
        connection.execute(
            """
            CREATE TABLE schema_migrations (
              version INTEGER PRIMARY KEY,
              name TEXT NOT NULL,
              applied_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
            (99, "future_schema", "2026-08-21T12:00:00Z"),
        )
        connection.commit()

    original_bytes = config.database_path.read_bytes()

    try:
        SessionManager(config=config)
    except RuntimeError as exc:
        assert "schema version is incompatible" in str(exc)
    else:
        raise AssertionError("schema mismatch should be rejected")

    assert config.database_path.read_bytes() == original_bytes
    log_text = config.log_path.read_text(encoding="utf-8")
    assert "SchemaVersionMismatch" in log_text
    assert "original_preserved" in log_text


def test_memory_v1_context_boundary_and_sqlite_schema_are_stable(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    manager = SessionManager(config=config)
    session = manager.create_session("boundary_session")
    manager.append_message(session.session_id, "user", "user context")
    manager.append_message(session.session_id, "assistant", "assistant context")
    manager.append_message(session.session_id, "system", "system context must stay out")
    manager.append_message(session.session_id, "tool", "tool context must stay out")
    manager.append_message(
        session.session_id,
        "system",
        "hidden system note",
        visible_to_user=False,
    )

    context = ContextBuilder(session_manager=manager).build(session.session_id)
    memory = manager.get_short_term_memory(session.session_id)
    memory.clear()

    with sqlite3.connect(config.database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        schema_version = connection.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()[0]

    timeline = manager.get_session_timeline(session.session_id)

    assert context.included_message_ids
    assert [message.role for message in context.recent_messages] == ["user", "assistant"]
    assert "system context must stay out" not in context.context_text
    assert "tool context must stay out" not in context.context_text
    assert "user context" in context.context_text
    assert "assistant context" in context.context_text
    assert memory.session_id == session.session_id
    assert [item.role for item in timeline] == ["user", "assistant", "system", "tool"]
    assert tables == {
        "sessions",
        "messages",
        "agent_runs",
        "execution_events",
        "session_summaries",
        "schema_migrations",
    }
    assert schema_version == SCHEMA_VERSION == 1


def test_memory_v1_persistence_failure_returns_ephemeral_result_with_warning(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    failing_manager = SimpleNamespace(
        config=config,
        repo=SimpleNamespace(record_memory_event=lambda *args, **kwargs: None),
        create_user_turn=lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("database write failed token=do-not-leak")
        ),
    )
    adapter = RuntimeMemoryAdapter(session_manager=failing_manager)

    turn = adapter.begin_turn("degraded_session", "continue without persistence")
    result = adapter.complete_turn(turn, "The agent can still answer.", maybe_summarize=False)

    assert turn.persistence_available is False
    assert turn.persistence_warning
    assert result.success is True
    assert result.persistence_available is False
    assert result.persistence_warning == turn.persistence_warning
    assert result.assistant_message is not None
    assert result.assistant_message.content == "The agent can still answer."
    assert result.run.status == AgentRunStatus.COMPLETED.value
