"""SQLite-based persistent storage for cognition analysis events.

Tracks emotional and intent state over time for group atmosphere monitoring.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any


_SCHEMA_VERSION = 1

_CREATE_TABLE = """\
CREATE TABLE IF NOT EXISTS cognition_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       REAL    NOT NULL,
    group_id        TEXT    NOT NULL DEFAULT '',
    user_id         TEXT    NOT NULL DEFAULT '',
    valence         REAL    NOT NULL DEFAULT 0,
    arousal         REAL    NOT NULL DEFAULT 0.3,
    basic_emotion   TEXT    NOT NULL DEFAULT '',
    intensity       REAL    NOT NULL DEFAULT 0.5,
    social_intent   TEXT    NOT NULL DEFAULT '',
    urgency_score   REAL    NOT NULL DEFAULT 0,
    relevance_score REAL    NOT NULL DEFAULT 0.5,
    confidence      REAL    NOT NULL DEFAULT 0.8
);
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_ce_ts ON cognition_events(timestamp);",
    "CREATE INDEX IF NOT EXISTS idx_ce_group ON cognition_events(group_id);",
    "CREATE INDEX IF NOT EXISTS idx_ce_user ON cognition_events(user_id);",
]

_CREATE_META = """\
CREATE TABLE IF NOT EXISTS _meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class CognitionEventStore:
    """Append-only SQLite store for cognition analysis events."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._ensure_schema()

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
            "INSERT OR REPLACE INTO _meta(key, value) VALUES(?, ?)",
            ("schema_version", str(_SCHEMA_VERSION)),
        )
        conn.commit()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def add(
        self,
        *,
        group_id: str = "",
        user_id: str = "",
        valence: float = 0.0,
        arousal: float = 0.3,
        basic_emotion: str = "",
        intensity: float = 0.5,
        social_intent: str = "",
        urgency_score: float = 0.0,
        relevance_score: float = 0.5,
        confidence: float = 0.8,
        timestamp: float | None = None,
    ) -> None:
        """Persist a single cognition event."""
        ts = timestamp if timestamp is not None else time.time()
        conn = self._connect()
        conn.execute(
            """INSERT INTO cognition_events
               (timestamp, group_id, user_id, valence, arousal, basic_emotion,
                intensity, social_intent, urgency_score, relevance_score, confidence)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ts, group_id, user_id, valence, arousal, basic_emotion,
                intensity, social_intent, urgency_score, relevance_score, confidence,
            ),
        )
        conn.commit()

    def get_recent(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return recent cognition events ordered by timestamp desc."""
        conn = self._connect()
        rows = conn.execute(
            """SELECT * FROM cognition_events
            ORDER BY timestamp DESC
            LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_group_timeline(self, group_id: str, limit: int = 100) -> list[dict[str, Any]]:
        """Return cognition events for a specific group."""
        conn = self._connect()
        rows = conn.execute(
            """SELECT * FROM cognition_events
            WHERE group_id = ?
            ORDER BY timestamp DESC
            LIMIT ?""",
            (group_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_emotion_distribution(self, group_id: str | None = None) -> dict[str, int]:
        """Return count of each basic_emotion label."""
        conn = self._connect()
        where = "WHERE group_id = ?" if group_id else ""
        params = (group_id,) if group_id else ()
        rows = conn.execute(
            f"""SELECT basic_emotion, COUNT(*) as cnt
            FROM cognition_events
            {where}
            GROUP BY basic_emotion
            ORDER BY cnt DESC""",
            params,
        ).fetchall()
        return {row[0] or "unknown": row[1] for row in rows}

    @property
    def db_path(self) -> Path:
        return self._db_path
