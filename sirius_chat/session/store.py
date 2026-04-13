from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Protocol

from sirius_chat.models import Transcript


class SessionStore(Protocol):
    @property
    def path(self) -> Path:
        ...

    def exists(self) -> bool:
        ...

    def load(self) -> Transcript:
        ...

    def save(self, transcript: Transcript) -> None:
        ...


class JsonSessionStore:
    def __init__(self, work_path: str | Path, filename: str = "session_state.json") -> None:
        self._work_path = Path(work_path)
        self._path = self._work_path / filename

    @property
    def path(self) -> Path:
        return self._path

    def exists(self) -> bool:
        return self._path.exists()

    def load(self) -> Transcript:
        payload = json.loads(self._path.read_text(encoding="utf-8"))
        transcript = Transcript.from_dict(payload)
        # Schema write-back: immediately persist any new default fields so the
        # file stays in sync with the current model definition.
        self.save(transcript)
        return transcript

    def save(self, transcript: Transcript) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(transcript.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


class SqliteSessionStore:
    def __init__(self, work_path: str | Path, filename: str = "session_state.db") -> None:
        self._work_path = Path(work_path)
        self._path = self._work_path / filename
        self._ensure_schema()

    @property
    def path(self) -> Path:
        return self._path

    def _connect(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(self._path)

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS session_state (
                    id INTEGER PRIMARY KEY CHECK(id = 1),
                    payload TEXT NOT NULL
                )
                """
            )

    def exists(self) -> bool:
        if not self._path.exists():
            return False
        with self._connect() as conn:
            row = conn.execute("SELECT 1 FROM session_state WHERE id = 1").fetchone()
        return row is not None

    def load(self) -> Transcript:
        with self._connect() as conn:
            row = conn.execute("SELECT payload FROM session_state WHERE id = 1").fetchone()
        if row is None:
            raise FileNotFoundError(f"session state not found in sqlite store: {self._path}")
        payload = json.loads(str(row[0]))
        transcript = Transcript.from_dict(payload)
        # Schema write-back: immediately persist any new default fields.
        self.save(transcript)
        return transcript

    def save(self, transcript: Transcript) -> None:
        payload = json.dumps(transcript.to_dict(), ensure_ascii=False)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO session_state(id, payload) VALUES(1, ?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                (payload,),
            )
