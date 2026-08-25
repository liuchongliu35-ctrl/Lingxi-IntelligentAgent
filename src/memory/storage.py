from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from src.memory.config import MemoryConfig
from src.memory.event_mapper import sanitize_payload
from src.memory.ids import (
    new_message_id,
    new_run_id,
    new_session_id,
    validate_generated_id,
    validate_session_id,
)
from src.memory.memory_logging import log_text_preview, write_memory_log
from src.memory.models import (
    AgentRunStatus,
    ContentFormat,
    AgentRun,
    DisplayType,
    ExecutionEventRecord,
    Message,
    MessageRole,
    MessageStatus,
    SessionInfo,
    SessionState,
    SessionStatus,
    SessionSummary,
    TimelineItem,
)

SCHEMA_VERSION = 1

_LOGGER = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _json_dumps(value: dict[str, Any] | None) -> str:
    return json.dumps(value or {}, ensure_ascii=False, sort_keys=True)


def _json_loads(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _sanitize_metadata(
    value: dict[str, Any] | None,
    *,
    max_text_chars: int,
) -> dict[str, Any]:
    sanitized = sanitize_payload(value or {}, max_text_chars=max_text_chars)
    return sanitized if isinstance(sanitized, dict) else {"value": sanitized}


def _as_bool(value: Any) -> bool:
    return bool(int(value))


def _as_str(value: Any) -> str:
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)


def _row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


@dataclass(slots=True)
class SessionRow:
    session_id: str
    title: str | None
    status: str
    created_at: str
    updated_at: str
    last_activity_at: str
    current_summary_id: str | None
    last_run_id: str | None
    next_timeline_seq: int
    metadata: dict[str, Any]
    schema_version: int


class SQLiteSessionRepository:
    def __init__(self, config: MemoryConfig):
        self.config = config
        self.database_path = Path(config.database_path)
        self.log_path = Path(config.log_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._configure_logger()
        self._initialize()

    def _configure_logger(self) -> None:
        for handler in list(_LOGGER.handlers):
            if isinstance(handler, logging.FileHandler):
                _LOGGER.removeHandler(handler)
                handler.close()
        if not any(isinstance(handler, logging.NullHandler) for handler in _LOGGER.handlers):
            _LOGGER.addHandler(logging.NullHandler())
        _LOGGER.setLevel(logging.INFO)

    def _connect(self) -> sqlite3.Connection:
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(self.database_path)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA synchronous = NORMAL")
        except sqlite3.DatabaseError as exc:
            try:
                if conn is not None:
                    conn.close()
            except Exception:
                pass
            self.record_memory_event(
                "persistence_warning",
                operation="open_database",
                database_path=str(self.database_path),
                error_code=exc.__class__.__name__,
                error_message=str(exc),
                original_preserved=True,
            )
            _LOGGER.exception("Failed to open memory database: %s", self.database_path)
            raise RuntimeError(
                "Memory SQLite database could not be opened. "
                f"The original database was not overwritten: {self.database_path}"
            ) from exc
        return conn

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _initialize(self) -> None:
        with self._connect() as conn:
            existing_migrations = conn.execute(
                """
                SELECT version
                FROM schema_migrations
                ORDER BY version ASC
                """
            ).fetchall() if self._table_exists(conn, "schema_migrations") else []
            existing_versions = [int(row["version"]) for row in existing_migrations]
            if existing_versions and max(existing_versions) != SCHEMA_VERSION:
                error_message = (
                    f"Unsupported Memory schema version {max(existing_versions)}; "
                    f"expected {SCHEMA_VERSION}"
                )
                self.record_memory_event(
                    "persistence_warning",
                    operation="schema_check",
                    database_path=str(self.database_path),
                    error_code="SchemaVersionMismatch",
                    error_message=error_message,
                    original_preserved=True,
                )
                raise RuntimeError(
                    "Memory SQLite schema version is incompatible. "
                    f"The original database was not overwritten: {self.database_path}"
                )
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                  session_id TEXT PRIMARY KEY,
                  title TEXT,
                  status TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  last_activity_at TEXT NOT NULL,
                  current_summary_id TEXT,
                  last_run_id TEXT,
                  next_timeline_seq INTEGER NOT NULL DEFAULT 1,
                  metadata_json TEXT NOT NULL DEFAULT '{}',
                  schema_version INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS messages (
                  message_id TEXT PRIMARY KEY,
                  session_id TEXT NOT NULL,
                  run_id TEXT,
                  timeline_seq INTEGER NOT NULL,
                  role TEXT NOT NULL,
                  content TEXT NOT NULL,
                  content_format TEXT NOT NULL,
                  display_type TEXT NOT NULL,
                  visible_to_user INTEGER NOT NULL,
                  status TEXT NOT NULL,
                  parent_message_id TEXT,
                  created_at TEXT NOT NULL,
                  updated_at TEXT,
                  metadata_json TEXT NOT NULL DEFAULT '{}',
                  UNIQUE(session_id, timeline_seq),
                  FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS agent_runs (
                  run_id TEXT PRIMARY KEY,
                  session_id TEXT NOT NULL,
                  user_message_id TEXT NOT NULL,
                  status TEXT NOT NULL,
                  started_at TEXT NOT NULL,
                  finished_at TEXT,
                  final_message_id TEXT,
                  error_code TEXT,
                  error_message TEXT,
                  agent_version TEXT,
                  model_profile TEXT,
                  metadata_json TEXT NOT NULL DEFAULT '{}',
                  FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS execution_events (
                  event_id TEXT PRIMARY KEY,
                  session_id TEXT NOT NULL,
                  run_id TEXT NOT NULL,
                  timeline_seq INTEGER NOT NULL,
                  event_type TEXT NOT NULL,
                  display_type TEXT NOT NULL,
                  display_content TEXT NOT NULL,
                  visible_to_user INTEGER NOT NULL,
                  status TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  completed_at TEXT,
                  parent_event_id TEXT,
                  sanitized_payload_json TEXT NOT NULL DEFAULT '{}',
                  metadata_json TEXT NOT NULL DEFAULT '{}',
                  UNIQUE(session_id, timeline_seq),
                  FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE,
                  FOREIGN KEY(run_id) REFERENCES agent_runs(run_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS session_summaries (
                  summary_id TEXT PRIMARY KEY,
                  session_id TEXT NOT NULL,
                  content TEXT NOT NULL,
                  covered_from_timeline_seq INTEGER NOT NULL,
                  covered_to_timeline_seq INTEGER NOT NULL,
                  created_at TEXT NOT NULL,
                  source TEXT NOT NULL,
                  model_profile TEXT,
                  metadata_json TEXT NOT NULL DEFAULT '{}',
                  FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS schema_migrations (
                  version INTEGER PRIMARY KEY,
                  name TEXT NOT NULL,
                  applied_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_messages_session_seq
                  ON messages(session_id, timeline_seq);
                CREATE INDEX IF NOT EXISTS idx_messages_session_role_seq
                  ON messages(session_id, role, timeline_seq);
                CREATE INDEX IF NOT EXISTS idx_runs_session_started
                  ON agent_runs(session_id, started_at);
                CREATE INDEX IF NOT EXISTS idx_events_session_seq
                  ON execution_events(session_id, timeline_seq);
                CREATE INDEX IF NOT EXISTS idx_events_run_seq
                  ON execution_events(run_id, timeline_seq);
                CREATE INDEX IF NOT EXISTS idx_summaries_session_created
                  ON session_summaries(session_id, created_at);
                """
            )
            versions = [
                int(row["version"])
                for row in conn.execute(
                    "SELECT version FROM schema_migrations ORDER BY version ASC"
                ).fetchall()
            ]
            if not versions:
                conn.execute(
                    "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
                    (SCHEMA_VERSION, "initial_memory_schema", _now_iso()),
                )

    def _table_exists(self, conn: sqlite3.Connection, table_name: str) -> bool:
        row = conn.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table' AND name = ?
            """,
            (table_name,),
        ).fetchone()
        return row is not None

    def _reserve_next_timeline_seq(self, conn: sqlite3.Connection, session_id: str, seq: int | None) -> int:
        row = conn.execute(
            "SELECT next_timeline_seq FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Session not found: {session_id}")
        current_next = int(row["next_timeline_seq"])
        if seq is None or seq <= 0:
            seq = current_next
        conn.execute(
            """
            UPDATE sessions
            SET next_timeline_seq = CASE
                WHEN next_timeline_seq <= ? THEN ? + 1
                ELSE next_timeline_seq
            END,
                updated_at = ?,
                last_activity_at = ?
            WHERE session_id = ?
            """,
            (seq, seq, _now_iso(), _now_iso(), session_id),
        )
        return seq

    def _touch_session(self, conn: sqlite3.Connection, session_id: str, *, last_run_id: str | None = None, summary_id: str | None = None) -> None:
        conn.execute(
            """
            UPDATE sessions
            SET updated_at = ?, last_activity_at = ?,
                last_run_id = COALESCE(?, last_run_id),
                current_summary_id = COALESCE(?, current_summary_id)
            WHERE session_id = ?
            """,
            (_now_iso(), _now_iso(), last_run_id, summary_id, session_id),
        )

    def _ensure_session_row(
        self,
        conn: sqlite3.Connection,
        session_id: str,
        *,
        title: str | None = None,
        status: str | SessionStatus = SessionStatus.ACTIVE,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if conn.execute("SELECT 1 FROM sessions WHERE session_id = ?", (session_id,)).fetchone():
            return
        created_at = _now_iso()
        conn.execute(
            """
            INSERT INTO sessions (
              session_id, title, status, created_at, updated_at, last_activity_at,
              current_summary_id, last_run_id, next_timeline_seq, metadata_json, schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                title,
                _as_str(status),
                created_at,
                created_at,
                created_at,
                None,
                None,
                1,
                _json_dumps(
                    _sanitize_metadata(
                        metadata,
                        max_text_chars=self.config.max_event_payload_chars,
                    )
                ),
                SCHEMA_VERSION,
            ),
        )

    def create_session(
        self,
        session_id: str,
        *,
        title: str | None = None,
        status: str | SessionStatus = SessionStatus.ACTIVE,
        metadata: dict[str, Any] | None = None,
    ) -> SessionState:
        session_id = validate_session_id(session_id)
        existing = self.load_session(session_id)
        if existing is not None:
            return existing
        with self._transaction() as conn:
            self._ensure_session_row(conn, session_id, title=title, status=status, metadata=metadata)
        session = self.load_session(session_id)
        self.record_memory_event(
            "session_created",
            session_id=session_id,
            status=getattr(session, "status", _as_str(status)),
            metadata_keys=sorted(str(key) for key in (metadata or {}).keys())[:20],
        )
        return session

    def get_or_create_session(
        self,
        session_id: str,
        *,
        title: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SessionState:
        session_id = validate_session_id(session_id)
        session = self.load_session(session_id)
        if session is not None:
            self.record_memory_event(
                "session_loaded",
                session_id=session_id,
                message_count=session.message_count,
                current_summary_id=session.current_summary_id,
            )
            return session
        return self.create_session(session_id, title=title, metadata=metadata)

    def create_user_turn(
        self,
        session_id: str | None,
        *,
        content: str,
        metadata: dict[str, Any] | None = None,
        title: str | None = None,
        session_metadata: dict[str, Any] | None = None,
        role: str | MessageRole = MessageRole.USER,
        content_format: str | ContentFormat = ContentFormat.TEXT,
        display_type: str | DisplayType = DisplayType.CHAT,
        visible_to_user: bool = True,
        status: str | MessageStatus = MessageStatus.COMPLETED,
        run_status: str | AgentRunStatus = AgentRunStatus.RUNNING,
        agent_version: str | None = None,
        model_profile: str | None = None,
    ) -> tuple[Message, AgentRun]:
        if session_id is None:
            session_id = new_session_id()
        session_id = validate_session_id(session_id)
        message_id = new_message_id()
        run_id = new_run_id()
        created_at = _now_iso()
        session_created = False
        with self._transaction() as conn:
            session_created = not bool(conn.execute("SELECT 1 FROM sessions WHERE session_id = ?", (session_id,)).fetchone())
            self._ensure_session_row(
                conn,
                session_id,
                title=title,
                status=SessionStatus.ACTIVE,
                metadata=session_metadata,
            )
            seq = self._reserve_next_timeline_seq(conn, session_id, None)
            message = Message(
                message_id=message_id,
                session_id=session_id,
                timeline_seq=seq,
                role=role,
                content=content,
                content_format=content_format,
                display_type=display_type,
                visible_to_user=visible_to_user,
                status=status,
                run_id=run_id,
                created_at=created_at,
                metadata=_sanitize_metadata(
                    metadata,
                    max_text_chars=self.config.max_event_payload_chars,
                ),
            )
            conn.execute(
                """
                INSERT INTO messages (
                  message_id, session_id, run_id, timeline_seq, role, content,
                  content_format, display_type, visible_to_user, status,
                  parent_message_id, created_at, updated_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message.message_id,
                    message.session_id,
                    run_id,
                    message.timeline_seq,
                    message.role,
                    message.content,
                    message.content_format,
                    message.display_type,
                    1 if message.visible_to_user else 0,
                    message.status,
                    message.parent_message_id,
                    message.created_at,
                    message.updated_at,
                    _json_dumps(
                        _sanitize_metadata(
                            message.metadata,
                            max_text_chars=self.config.max_event_payload_chars,
                        )
                    ),
                ),
            )
            run = AgentRun(
                run_id=run_id,
                session_id=session_id,
                user_message_id=message_id,
                status=run_status,
                started_at=created_at,
                agent_version=agent_version,
                model_profile=model_profile,
            )
            conn.execute(
                """
                INSERT INTO agent_runs (
                  run_id, session_id, user_message_id, status, started_at,
                  finished_at, final_message_id, error_code, error_message,
                  agent_version, model_profile, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.run_id,
                    run.session_id,
                    run.user_message_id,
                    run.status,
                    run.started_at,
                    run.finished_at,
                    run.final_message_id,
                    run.error_code,
                    run.error_message,
                    run.agent_version,
                    run.model_profile,
                    _json_dumps(
                        _sanitize_metadata(
                            run.metadata,
                            max_text_chars=self.config.max_event_payload_chars,
                        )
                    ),
                ),
            )
            self._touch_session(conn, session_id, last_run_id=run_id)
        message = self.load_message(message_id)
        run = self.load_run(run_id)
        if session_created:
            self.record_memory_event(
                "session_created",
                session_id=session_id,
                status=SessionStatus.ACTIVE.value,
                metadata_keys=sorted(str(key) for key in (session_metadata or {}).keys())[:20],
            )
        self.record_memory_event(
            "message_appended",
            session_id=session_id,
            run_id=run_id,
            message_id=message_id,
            role=_as_str(role),
            status=_as_str(status),
            content_length=len(content or ""),
            content_preview=log_text_preview(content),
            persisted=True,
        )
        self.record_memory_event(
            "run_created",
            session_id=session_id,
            run_id=run_id,
            user_message_id=message_id,
            status=_as_str(run_status),
            agent_version=agent_version,
            model_profile=model_profile,
        )
        return message, run

    def load_session_row(self, session_id: str) -> SessionRow | None:
        session_id = validate_session_id(session_id)
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
        if row is None:
            return None
        data = _row_dict(row)
        return SessionRow(
            session_id=data["session_id"],
            title=data["title"],
            status=data["status"],
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            last_activity_at=data["last_activity_at"],
            current_summary_id=data["current_summary_id"],
            last_run_id=data["last_run_id"],
            next_timeline_seq=int(data["next_timeline_seq"]),
            metadata=_json_loads(data["metadata_json"]),
            schema_version=int(data["schema_version"]),
        )

    def load_session(self, session_id: str) -> SessionState | None:
        session_id = validate_session_id(session_id)
        with self._connect() as conn:
            session_row = conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if session_row is None:
                return None
            message_rows = conn.execute(
                "SELECT * FROM messages WHERE session_id = ? ORDER BY timeline_seq ASC",
                (session_id,),
            ).fetchall()
            summary_row = None
            if session_row["current_summary_id"]:
                summary_row = conn.execute(
                    "SELECT * FROM session_summaries WHERE summary_id = ?",
                    (session_row["current_summary_id"],),
                ).fetchone()
        messages = [self._message_from_row(row) for row in message_rows]
        session = SessionState(
            session_id=session_row["session_id"],
            messages=messages,
            summary=summary_row["content"] if summary_row else "",
            current_summary_id=session_row["current_summary_id"],
            created_at=session_row["created_at"],
            updated_at=session_row["updated_at"],
            last_activity_at=session_row["last_activity_at"],
            status=session_row["status"],
            metadata=_json_loads(session_row["metadata_json"]),
        )
        self.record_memory_event(
            "session_loaded",
            session_id=session.session_id,
            message_count=session.message_count,
            current_summary_id=session.current_summary_id,
            has_summary=bool(session.summary),
        )
        return session

    def list_sessions(self) -> list[SessionInfo]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                  s.session_id,
                  s.title,
                  s.status,
                  s.created_at,
                  s.updated_at,
                  s.last_activity_at,
                  s.current_summary_id,
                  s.metadata_json,
                  COUNT(m.message_id) AS message_count
                FROM sessions s
                LEFT JOIN messages m ON m.session_id = s.session_id
                GROUP BY s.session_id
                ORDER BY s.last_activity_at DESC
                """
            ).fetchall()
        return [
            SessionInfo(
                session_id=row["session_id"],
                title=row["title"],
                status=row["status"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                last_activity_at=row["last_activity_at"],
                message_count=int(row["message_count"]),
                current_summary_id=row["current_summary_id"],
                metadata=_json_loads(row["metadata_json"]),
            )
            for row in rows
        ]

    def delete_session(self, session_id: str) -> bool:
        session_id = validate_session_id(session_id)
        with self._transaction() as conn:
            deleted = conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,)).rowcount
        return bool(deleted)

    def insert_message(self, message: Message | dict[str, Any]) -> Message:
        if isinstance(message, Message):
            session_id = validate_session_id(message.session_id)
            message_id = validate_generated_id(message.message_id, prefix="msg")
            payload = message.to_dict()
        else:
            payload = dict(message)
            session_id = validate_session_id(payload["session_id"])
            message_id = validate_generated_id(payload["message_id"], prefix="msg")
        with self._transaction() as conn:
            if conn.execute("SELECT 1 FROM messages WHERE message_id = ?", (message_id,)).fetchone():
                return self.load_message(message_id)
            seq = self._reserve_next_timeline_seq(
                conn,
                session_id,
                int(payload.get("timeline_seq") or 0),
            )
            stored = Message(
                message_id=message_id,
                session_id=session_id,
                timeline_seq=seq,
                role=payload["role"],
                content=payload["content"],
                content_format=payload.get("content_format", "text"),
                display_type=payload.get("display_type", "chat"),
                visible_to_user=payload.get("visible_to_user", True),
                status=payload.get("status", "completed"),
                run_id=payload.get("run_id"),
                parent_message_id=payload.get("parent_message_id"),
                created_at=payload.get("created_at") or _now_iso(),
                updated_at=payload.get("updated_at"),
                metadata=_sanitize_metadata(
                    payload.get("metadata"),
                    max_text_chars=self.config.max_event_payload_chars,
                ),
            )
            payload = stored.to_dict()
            conn.execute(
                """
                INSERT INTO messages (
                  message_id, session_id, run_id, timeline_seq, role, content,
                  content_format, display_type, visible_to_user, status,
                  parent_message_id, created_at, updated_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["message_id"],
                    payload["session_id"],
                    payload["run_id"],
                    payload["timeline_seq"],
                    payload["role"],
                    payload["content"],
                    payload["content_format"],
                    payload["display_type"],
                    1 if payload["visible_to_user"] else 0,
                    payload["status"],
                    payload["parent_message_id"],
                    payload["created_at"],
                    payload["updated_at"],
                    _json_dumps(
                        _sanitize_metadata(
                            payload.get("metadata"),
                            max_text_chars=self.config.max_event_payload_chars,
                        )
                    ),
                ),
            )
            self._touch_session(conn, session_id)
        message = self.load_message(message_id)
        self.record_memory_event(
            "message_appended",
            session_id=session_id,
            run_id=payload.get("run_id"),
            message_id=message_id,
            role=payload.get("role"),
            status=payload.get("status"),
            content_length=len(str(payload.get("content") or "")),
            content_preview=log_text_preview(payload.get("content")),
            persisted=True,
        )
        return message

    def insert_run(self, run: AgentRun | dict[str, Any]) -> AgentRun:
        if not isinstance(run, AgentRun):
            run = AgentRun(**run)
        validate_session_id(run.session_id)
        validate_generated_id(run.run_id, prefix="run")
        with self._transaction() as conn:
            existing = conn.execute("SELECT 1 FROM agent_runs WHERE run_id = ?", (run.run_id,)).fetchone()
            if existing:
                return self.load_run(run.run_id)
            conn.execute(
                """
                INSERT INTO agent_runs (
                  run_id, session_id, user_message_id, status, started_at,
                  finished_at, final_message_id, error_code, error_message,
                  agent_version, model_profile, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.run_id,
                    run.session_id,
                    run.user_message_id,
                    _as_str(run.status),
                    run.started_at,
                    run.finished_at,
                    run.final_message_id,
                    run.error_code,
                    run.error_message,
                    run.agent_version,
                    run.model_profile,
                    _json_dumps(
                        _sanitize_metadata(
                            run.metadata,
                            max_text_chars=self.config.max_event_payload_chars,
                        )
                    ),
                ),
            )
            self._touch_session(conn, run.session_id, last_run_id=run.run_id)
        stored = self.load_run(run.run_id)
        self.record_memory_event(
            "run_created",
            session_id=run.session_id,
            run_id=run.run_id,
            user_message_id=run.user_message_id,
            status=_as_str(run.status),
            agent_version=run.agent_version,
            model_profile=run.model_profile,
        )
        return stored

    def update_run(self, run: AgentRun | dict[str, Any]) -> AgentRun:
        if not isinstance(run, AgentRun):
            run = AgentRun(**run)
        validate_session_id(run.session_id)
        validate_generated_id(run.run_id, prefix="run")
        with self._transaction() as conn:
            conn.execute(
                """
                UPDATE agent_runs
                SET session_id = ?, user_message_id = ?, status = ?, started_at = ?,
                    finished_at = ?, final_message_id = ?, error_code = ?, error_message = ?,
                    agent_version = ?, model_profile = ?, metadata_json = ?
                WHERE run_id = ?
                """,
                (
                    run.session_id,
                    run.user_message_id,
                    _as_str(run.status),
                    run.started_at,
                    run.finished_at,
                    run.final_message_id,
                    run.error_code,
                    run.error_message,
                    run.agent_version,
                    run.model_profile,
                    _json_dumps(
                        _sanitize_metadata(
                            run.metadata,
                            max_text_chars=self.config.max_event_payload_chars,
                        )
                    ),
                    run.run_id,
                ),
            )
            self._touch_session(conn, run.session_id, last_run_id=run.run_id)
        return self.load_run(run.run_id)

    def complete_run(self, run_id: str, final_message_id: str | None = None) -> AgentRun | None:
        run = self.load_run(run_id)
        if run is None:
            return None
        run.status = "completed"
        run.final_message_id = final_message_id
        run.finished_at = _now_iso()
        restored = self.update_run(run)
        self.record_memory_event(
            "run_completed",
            session_id=restored.session_id if restored else None,
            run_id=run_id,
            final_message_id=final_message_id,
            status=getattr(restored, "status", None),
        )
        return restored

    def fail_run(
        self,
        run_id: str,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
        status: str = "failed",
    ) -> AgentRun | None:
        run = self.load_run(run_id)
        if run is None:
            return None
        run.status = status
        run.error_code = error_code
        run.error_message = error_message
        run.finished_at = _now_iso()
        restored = self.update_run(run)
        self.record_memory_event(
            "run_failed",
            session_id=restored.session_id if restored else None,
            run_id=run_id,
            status=getattr(restored, "status", status),
            error_code=error_code,
            error_message=error_message,
        )
        return restored

    def insert_execution_event(self, event: ExecutionEventRecord | dict[str, Any]) -> ExecutionEventRecord | None:
        if isinstance(event, ExecutionEventRecord):
            payload = event.to_dict()
        else:
            payload = dict(event)
        if not _as_bool(payload.get("visible_to_user", False)):
            self.record_memory_event(
                "event_skipped_internal",
                session_id=payload.get("session_id"),
                run_id=payload.get("run_id"),
                event_id=payload.get("event_id"),
                source_event_type=payload.get("event_type"),
                status=payload.get("status"),
            )
            return None
        session_id = validate_session_id(payload["session_id"])
        run_id = validate_generated_id(payload["run_id"], prefix="run")
        event_id = validate_generated_id(payload["event_id"], prefix="event")
        with self._transaction() as conn:
            if conn.execute("SELECT 1 FROM execution_events WHERE event_id = ?", (event_id,)).fetchone():
                restored = self.load_execution_event(event_id)
                self.record_memory_event(
                    "event_persisted",
                    session_id=session_id,
                    run_id=run_id,
                    event_id=event_id,
                    persisted_event_type=getattr(restored, "event_type", payload.get("event_type")),
                    status=getattr(restored, "status", payload.get("status")),
                    duplicate=True,
                )
                return restored
            seq = self._reserve_next_timeline_seq(conn, session_id, int(payload.get("timeline_seq") or 0))
            stored = ExecutionEventRecord(
                event_id=event_id,
                session_id=session_id,
                run_id=run_id,
                timeline_seq=seq,
                event_type=payload["event_type"],
                display_type=payload.get("display_type", "tool_progress"),
                display_content=payload.get("display_content", ""),
                visible_to_user=True,
                status=payload.get("status", "recorded"),
                created_at=payload.get("created_at") or _now_iso(),
                completed_at=payload.get("completed_at"),
                parent_event_id=payload.get("parent_event_id"),
                sanitized_payload=payload.get("sanitized_payload"),
                metadata=payload.get("metadata"),
            )
            payload = stored.to_dict()
            conn.execute(
                """
                INSERT INTO execution_events (
                  event_id, session_id, run_id, timeline_seq, event_type,
                  display_type, display_content, visible_to_user, status,
                  created_at, completed_at, parent_event_id,
                  sanitized_payload_json, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["event_id"],
                    payload["session_id"],
                    payload["run_id"],
                    payload["timeline_seq"],
                    payload["event_type"],
                    payload["display_type"],
                    payload["display_content"],
                    1,
                    payload["status"],
                    payload["created_at"],
                    payload["completed_at"],
                    payload["parent_event_id"],
                    _json_dumps(payload["sanitized_payload"]),
                    _json_dumps(
                        _sanitize_metadata(
                            payload.get("metadata"),
                            max_text_chars=self.config.max_event_payload_chars,
                        )
                    ),
                ),
            )
            self._touch_session(conn, session_id)
        stored = self.load_execution_event(event_id)
        self.record_memory_event(
            "event_persisted",
            session_id=session_id,
            run_id=run_id,
            event_id=event_id,
            persisted_event_type=stored.event_type if stored else payload.get("event_type"),
            status=stored.status if stored else payload.get("status"),
            content_length=len(stored.display_content if stored else payload.get("display_content", "")),
            payload_keys=sorted(str(key) for key in (stored.sanitized_payload if stored else {}).keys())[:20],
            duplicate=False,
        )
        return stored

    def insert_summary(self, summary: SessionSummary | dict[str, Any]) -> SessionSummary:
        if not isinstance(summary, SessionSummary):
            summary = SessionSummary(**summary)
        validate_session_id(summary.session_id)
        validate_generated_id(summary.summary_id, prefix="summary")
        with self._transaction() as conn:
            if conn.execute("SELECT 1 FROM session_summaries WHERE summary_id = ?", (summary.summary_id,)).fetchone():
                return self.load_summary(summary.summary_id)
            conn.execute(
                """
                INSERT INTO session_summaries (
                  summary_id, session_id, content, covered_from_timeline_seq,
                  covered_to_timeline_seq, created_at, source, model_profile,
                  metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    summary.summary_id,
                    summary.session_id,
                    summary.content,
                    summary.covered_from_timeline_seq,
                    summary.covered_to_timeline_seq,
                    summary.created_at,
                    _as_str(summary.source),
                    summary.model_profile,
                    _json_dumps(
                        _sanitize_metadata(
                            summary.metadata,
                            max_text_chars=self.config.max_event_payload_chars,
                        )
                    ),
                ),
            )
            self._touch_session(conn, summary.session_id, summary_id=summary.summary_id)
        stored = self.load_summary(summary.summary_id)
        self.record_memory_event(
            "summary_completed",
            session_id=summary.session_id,
            summary_id=summary.summary_id,
            source=_as_str(summary.source),
            covered_from_timeline_seq=summary.covered_from_timeline_seq,
            covered_to_timeline_seq=summary.covered_to_timeline_seq,
            content_length=len(summary.content),
        )
        return stored

    def record_memory_event(self, event_type: str, **payload: Any) -> None:
        try:
            write_memory_log(self.log_path, event_type, **payload)
        except Exception:
            _LOGGER.exception("Failed to record memory event: %s", event_type)

    def load_summary(self, summary_id: str) -> SessionSummary | None:
        validate_generated_id(summary_id, prefix="summary")
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM session_summaries WHERE summary_id = ?", (summary_id,)).fetchone()
        return self._summary_from_row(row) if row else None

    def load_current_summary(self, session_id: str) -> SessionSummary | None:
        session_id = validate_session_id(session_id)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT ss.*
                FROM sessions s
                JOIN session_summaries ss ON ss.summary_id = s.current_summary_id
                WHERE s.session_id = ?
                """,
                (session_id,),
            ).fetchone()
        return self._summary_from_row(row) if row else None

    def load_message(self, message_id: str) -> Message | None:
        validate_generated_id(message_id, prefix="msg")
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM messages WHERE message_id = ?", (message_id,)).fetchone()
        return self._message_from_row(row) if row else None

    def load_run(self, run_id: str) -> AgentRun | None:
        validate_generated_id(run_id, prefix="run")
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM agent_runs WHERE run_id = ?", (run_id,)).fetchone()
        return self._run_from_row(row) if row else None

    def load_execution_event(self, event_id: str) -> ExecutionEventRecord | None:
        validate_generated_id(event_id, prefix="event")
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM execution_events WHERE event_id = ?", (event_id,)).fetchone()
        return self._event_from_row(row) if row else None

    def load_recent_messages(
        self,
        session_id: str,
        limit: int,
        *,
        roles: tuple[str, ...] | list[str] | None = None,
        statuses: tuple[str, ...] | list[str] | None = None,
    ) -> list[Message]:
        session_id = validate_session_id(session_id)
        if int(limit) <= 0:
            raise ValueError("limit must be > 0")
        normalized_roles = tuple(str(role.value if hasattr(role, "value") else role).lower() for role in (roles or ()))
        where_clause = "session_id = ?"
        parameters: list[Any] = [session_id]
        if normalized_roles:
            placeholders = ", ".join("?" for _ in normalized_roles)
            where_clause += f" AND role IN ({placeholders})"
            parameters.extend(normalized_roles)
        normalized_statuses = tuple(str(status.value if hasattr(status, "value") else status).lower() for status in (statuses or ()))
        if normalized_statuses:
            placeholders = ", ".join("?" for _ in normalized_statuses)
            where_clause += f" AND status IN ({placeholders})"
            parameters.extend(normalized_statuses)
        parameters.append(int(limit))
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM messages
                WHERE {where_clause}
                ORDER BY timeline_seq DESC
                LIMIT ?
                """,
                parameters,
            ).fetchall()
        return [self._message_from_row(row) for row in reversed(rows)]

    def load_messages_before(
        self,
        session_id: str,
        timeline_seq: int,
        *,
        roles: tuple[str, ...] | list[str] | None = None,
        statuses: tuple[str, ...] | list[str] | None = None,
    ) -> list[Message]:
        session_id = validate_session_id(session_id)
        boundary = int(timeline_seq)
        if boundary <= 1:
            return []
        where_clause = "session_id = ? AND timeline_seq < ?"
        parameters: list[Any] = [session_id, boundary]
        normalized_roles = tuple(str(role.value if hasattr(role, "value") else role).lower() for role in (roles or ()))
        if normalized_roles:
            placeholders = ", ".join("?" for _ in normalized_roles)
            where_clause += f" AND role IN ({placeholders})"
            parameters.extend(normalized_roles)
        normalized_statuses = tuple(str(status.value if hasattr(status, "value") else status).lower() for status in (statuses or ()))
        if normalized_statuses:
            placeholders = ", ".join("?" for _ in normalized_statuses)
            where_clause += f" AND status IN ({placeholders})"
            parameters.extend(normalized_statuses)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM messages
                WHERE {where_clause}
                ORDER BY timeline_seq ASC
                """,
                parameters,
            ).fetchall()
        return [self._message_from_row(row) for row in rows]

    def count_messages(
        self,
        session_id: str,
        *,
        roles: tuple[str, ...] | list[str] | None = None,
        statuses: tuple[str, ...] | list[str] | None = None,
    ) -> int:
        session_id = validate_session_id(session_id)
        where_clause = "session_id = ?"
        parameters: list[Any] = [session_id]
        normalized_roles = tuple(str(role.value if hasattr(role, "value") else role).lower() for role in (roles or ()))
        if normalized_roles:
            placeholders = ", ".join("?" for _ in normalized_roles)
            where_clause += f" AND role IN ({placeholders})"
            parameters.extend(normalized_roles)
        normalized_statuses = tuple(str(status.value if hasattr(status, "value") else status).lower() for status in (statuses or ()))
        if normalized_statuses:
            placeholders = ", ".join("?" for _ in normalized_statuses)
            where_clause += f" AND status IN ({placeholders})"
            parameters.extend(normalized_statuses)
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) AS message_count FROM messages WHERE {where_clause}",
                parameters,
            ).fetchone()
        return int(row["message_count"]) if row else 0

    def load_session_timeline(self, session_id: str) -> list[TimelineItem]:
        session_id = validate_session_id(session_id)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                  message_id AS item_id,
                  'message' AS item_kind,
                  session_id,
                  run_id,
                  timeline_seq,
                  display_type,
                  role,
                  content,
                  status,
                  created_at,
                  metadata_json,
                  NULL AS event_type,
                  '{}' AS sanitized_payload_json
                FROM messages
                WHERE session_id = ? AND visible_to_user = 1
                UNION ALL
                SELECT
                  event_id AS item_id,
                  'execution_event' AS item_kind,
                  session_id,
                  run_id,
                  timeline_seq,
                  display_type,
                  NULL AS role,
                  display_content AS content,
                  status,
                  created_at,
                  metadata_json,
                  event_type,
                  sanitized_payload_json
                FROM execution_events
                WHERE session_id = ? AND visible_to_user = 1
                ORDER BY timeline_seq ASC
                """,
                (session_id, session_id),
            ).fetchall()
        items: list[TimelineItem] = []
        for row in rows:
            metadata = _json_loads(row["metadata_json"])
            if row["item_kind"] == "execution_event":
                metadata = {
                    **metadata,
                    "event_type": row["event_type"],
                    "sanitized_payload": _json_loads(row["sanitized_payload_json"]),
                }
            items.append(TimelineItem(
                item_id=row["item_id"],
                item_kind=row["item_kind"],
                session_id=row["session_id"],
                run_id=row["run_id"],
                timeline_seq=row["timeline_seq"],
                display_type=row["display_type"],
                role=row["role"],
                content=row["content"],
                status=row["status"],
                created_at=row["created_at"],
                metadata=metadata,
            ))
        return items

    def mark_interrupted_runs(self) -> int:
        with self._transaction() as conn:
            result = conn.execute(
                """
                UPDATE agent_runs
                SET status = 'interrupted'
                WHERE status IN ('pending', 'running', 'waiting_user')
                """
            )
            count = int(result.rowcount)
        self.record_memory_event(
            "runs_marked_interrupted",
            count=count,
            from_statuses=["pending", "running", "waiting_user"],
        )
        return count

    def _message_from_row(self, row: sqlite3.Row | None) -> Message | None:
        if row is None:
            return None
        return Message(
            message_id=row["message_id"],
            session_id=row["session_id"],
            timeline_seq=row["timeline_seq"],
            role=row["role"],
            content=row["content"],
            content_format=row["content_format"],
            display_type=row["display_type"],
            visible_to_user=_as_bool(row["visible_to_user"]),
            status=row["status"],
            run_id=row["run_id"],
            parent_message_id=row["parent_message_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            metadata=_json_loads(row["metadata_json"]),
        )

    def _run_from_row(self, row: sqlite3.Row | None) -> AgentRun | None:
        if row is None:
            return None
        return AgentRun(
            run_id=row["run_id"],
            session_id=row["session_id"],
            user_message_id=row["user_message_id"],
            status=row["status"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            final_message_id=row["final_message_id"],
            error_code=row["error_code"],
            error_message=row["error_message"],
            agent_version=row["agent_version"],
            model_profile=row["model_profile"],
            metadata=_json_loads(row["metadata_json"]),
        )

    def _event_from_row(self, row: sqlite3.Row | None) -> ExecutionEventRecord | None:
        if row is None:
            return None
        return ExecutionEventRecord(
            event_id=row["event_id"],
            session_id=row["session_id"],
            run_id=row["run_id"],
            timeline_seq=row["timeline_seq"],
            event_type=row["event_type"],
            display_type=row["display_type"],
            display_content=row["display_content"],
            visible_to_user=_as_bool(row["visible_to_user"]),
            status=row["status"],
            created_at=row["created_at"],
            completed_at=row["completed_at"],
            parent_event_id=row["parent_event_id"],
            sanitized_payload=_json_loads(row["sanitized_payload_json"]),
            metadata=_json_loads(row["metadata_json"]),
        )

    def _summary_from_row(self, row: sqlite3.Row | None) -> SessionSummary | None:
        if row is None:
            return None
        return SessionSummary(
            summary_id=row["summary_id"],
            session_id=row["session_id"],
            content=row["content"],
            covered_from_timeline_seq=row["covered_from_timeline_seq"],
            covered_to_timeline_seq=row["covered_to_timeline_seq"],
            created_at=row["created_at"],
            source=row["source"],
            model_profile=row["model_profile"],
            metadata=_json_loads(row["metadata_json"]),
        )


__all__ = ["SCHEMA_VERSION", "SessionRow", "SQLiteSessionRepository"]
