"""SQLite-based persistent storage for token usage records.

Provides :class:`TokenUsageStore` which writes every
:class:`~sirius_chat.config.TokenUsageRecord` into a local SQLite database
so that cross-session and multi-dimensional analytics become possible.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from sirius_chat.config import TokenUsageRecord

_SCHEMA_VERSION = 1

_CREATE_TABLE = """\
CREATE TABLE IF NOT EXISTS token_usage (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT    NOT NULL,
    timestamp       REAL    NOT NULL,
    actor_id        TEXT    NOT NULL,
    task_name       TEXT    NOT NULL,
    model           TEXT    NOT NULL DEFAULT '',
    prompt_tokens   INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens    INTEGER NOT NULL DEFAULT 0,
    input_chars     INTEGER NOT NULL DEFAULT 0,
    output_chars    INTEGER NOT NULL DEFAULT 0,
    estimation_method TEXT  NOT NULL DEFAULT 'char_div4',
    retries_used    INTEGER NOT NULL DEFAULT 0
);
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_tu_session ON token_usage(session_id);",
    "CREATE INDEX IF NOT EXISTS idx_tu_actor   ON token_usage(actor_id);",
    "CREATE INDEX IF NOT EXISTS idx_tu_task    ON token_usage(task_name);",
    "CREATE INDEX IF NOT EXISTS idx_tu_model   ON token_usage(model);",
    "CREATE INDEX IF NOT EXISTS idx_tu_ts      ON token_usage(timestamp);",
]

_CREATE_META = """\
CREATE TABLE IF NOT EXISTS _meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class TokenUsageStore:
    """Append-only SQLite store for :class:`TokenUsageRecord` instances.

    Parameters
    ----------
    db_path:
        Path to the SQLite database file.  Created automatically if absent.
    session_id:
        Logical session identifier written alongside every record so that
        per-session queries are possible.
    """

    def __init__(self, db_path: str | Path, *, session_id: str = "default") -> None:
        self._db_path = Path(db_path)
        self._session_id = session_id
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._ensure_schema()

    # ------------------------------------------------------------------
    # Connection helpers
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self._db_path), timeout=10)
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA synchronous=NORMAL;")
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _ensure_schema(self) -> None:
        conn = self._connect()
        conn.execute(_CREATE_META)
        conn.execute(_CREATE_TABLE)
        for idx_sql in _CREATE_INDEXES:
            conn.execute(idx_sql)
        conn.execute(
            "INSERT OR IGNORE INTO _meta(key, value) VALUES(?, ?)",
            ("schema_version", str(_SCHEMA_VERSION)),
        )
        conn.commit()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def add(self, record: TokenUsageRecord, *, timestamp: float | None = None) -> None:
        """Persist a single :class:`TokenUsageRecord`."""
        ts = timestamp if timestamp is not None else time.time()
        conn = self._connect()
        conn.execute(
            """INSERT INTO token_usage
               (session_id, timestamp, actor_id, task_name, model,
                prompt_tokens, completion_tokens, total_tokens,
                input_chars, output_chars, estimation_method, retries_used)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                self._session_id,
                ts,
                record.actor_id,
                record.task_name,
                record.model,
                record.prompt_tokens,
                record.completion_tokens,
                record.total_tokens,
                record.input_chars,
                record.output_chars,
                record.estimation_method,
                record.retries_used,
            ),
        )
        conn.commit()

    def add_many(self, records: list[TokenUsageRecord], *, timestamp: float | None = None) -> None:
        """Persist multiple records in a single transaction."""
        if not records:
            return
        ts = timestamp if timestamp is not None else time.time()
        conn = self._connect()
        conn.executemany(
            """INSERT INTO token_usage
               (session_id, timestamp, actor_id, task_name, model,
                prompt_tokens, completion_tokens, total_tokens,
                input_chars, output_chars, estimation_method, retries_used)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    self._session_id,
                    ts,
                    r.actor_id,
                    r.task_name,
                    r.model,
                    r.prompt_tokens,
                    r.completion_tokens,
                    r.total_tokens,
                    r.input_chars,
                    r.output_chars,
                    r.estimation_method,
                    r.retries_used,
                )
                for r in records
            ],
        )
        conn.commit()

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------

    def count(self, *, session_id: str | None = None) -> int:
        conn = self._connect()
        if session_id is not None:
            row = conn.execute(
                "SELECT COUNT(*) FROM token_usage WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        else:
            row = conn.execute("SELECT COUNT(*) FROM token_usage").fetchone()
        return int(row[0])

    def list_sessions(self) -> list[str]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT DISTINCT session_id FROM token_usage ORDER BY session_id"
        ).fetchall()
        return [row[0] for row in rows]

    def fetch_records(
        self,
        *,
        session_id: str | None = None,
        actor_id: str | None = None,
        task_name: str | None = None,
        model: str | None = None,
    ) -> list[dict[str, object]]:
        """Return raw rows matching the given filters."""
        clauses: list[str] = []
        params: list[object] = []
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)
        if actor_id is not None:
            clauses.append("actor_id = ?")
            params.append(actor_id)
        if task_name is not None:
            clauses.append("task_name = ?")
            params.append(task_name)
        if model is not None:
            clauses.append("model = ?")
            params.append(model)

        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        conn = self._connect()
        rows = conn.execute(
            f"SELECT * FROM token_usage{where} ORDER BY timestamp",
            params,
        ).fetchall()
        return [dict(row) for row in rows]

    @property
    def db_path(self) -> Path:
        return self._db_path

    @property
    def session_id(self) -> str:
        return self._session_id
