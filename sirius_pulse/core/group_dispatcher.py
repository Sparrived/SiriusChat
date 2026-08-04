"""Cross-persona group turn arbitration.

The persona workers are separate OS processes, so an in-memory asyncio lock
cannot prevent two accounts from replying to the same group.  This small
SQLite-backed coordinator owns one active turn per group and gives workers a
short-lived lease before they enter the response pipeline.
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from uuid import uuid4


_OPEN_LOOP_RE = re.compile(
    r"(?:[?？]|你(?:怎么看|觉得|认为|说|来|接着|继续|补充)|"
    r"(?:轮到你|请你|想听你|能不能|要不要|是不是|对吧))"
)
_CONTINUATION_RE = re.compile(r"(?:不过|但是|不太同意|不同意|补充|反而|确实|有道理|我也觉得)")


def _normalized_topic(text: str) -> str:
    return "".join(
        char.lower()
        for char in str(text or "")
        if char.isalnum() or "\u4e00" <= char <= "\u9fff"
    )[:240]


def _topic_signature(text: str) -> str:
    normalized = _normalized_topic(text)
    if not normalized:
        return ""
    if len(normalized) < 2:
        tokens = {normalized}
    else:
        tokens = {normalized[index : index + 2] for index in range(len(normalized) - 1)}
    return json.dumps(sorted(tokens), ensure_ascii=False, separators=(",", ":"))


def _topic_similarity(left: str, right: str) -> float:
    try:
        left_tokens = set(json.loads(left or "[]"))
        right_tokens = set(json.loads(right or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return 0.0
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


@dataclass(frozen=True, slots=True)
class DispatchDecision:
    action: str
    event_id: str
    worker_id: str = ""
    lease_id: str = ""
    reason: str = ""
    base_score: float = 0.0
    final_score: float = 0.0
    activity_penalty: float = 0.0

    @property
    def granted(self) -> bool:
        return self.action == "grant" and bool(self.lease_id)

    @property
    def deferred(self) -> bool:
        return self.action == "defer"


class GroupDispatcher:
    """Coordinate one public persona turn per group across worker processes."""

    # ponytail: SQLite is sufficient for same-host workers; use a service if
    # persona workers move to different hosts.

    def __init__(
        self,
        db_path: str | Path,
        *,
        worker_id: str,
        account_id: str = "",
        priority: float = 0.0,
        min_reply_interval_seconds: float = 3.0,
        lease_seconds: float = 120.0,
        peer_cooldown_seconds: float = 5.0,
        max_peer_turns: int = 3,
        registry_ttl_seconds: float = 180.0,
        score_collection_seconds: float = 0.15,
        activity_window_seconds: float = 300.0,
        activity_penalty_per_reply: float = 0.12,
        max_activity_penalty: float = 0.6,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.worker_id = str(worker_id).strip()
        self.account_id = str(account_id).strip()
        self.priority = float(priority)
        self.min_reply_interval_seconds = max(0.0, float(min_reply_interval_seconds))
        self.lease_seconds = max(5.0, float(lease_seconds))
        self.peer_cooldown_seconds = max(0.0, float(peer_cooldown_seconds))
        self.max_peer_turns = max(0, int(max_peer_turns))
        self.registry_ttl_seconds = max(30.0, float(registry_ttl_seconds))
        self.score_collection_seconds = max(0.05, min(1.0, float(score_collection_seconds)))
        self.activity_window_seconds = max(30.0, float(activity_window_seconds))
        self.activity_penalty_per_reply = max(0.0, float(activity_penalty_per_reply))
        self.max_activity_penalty = max(0.0, float(max_activity_penalty))
        self._clock = clock or time.time
        if not self.worker_id:
            raise ValueError("GroupDispatcher requires a worker_id")
        self._initialize()
        self.register()

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS dispatcher_workers (
                    worker_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL DEFAULT '',
                    priority REAL NOT NULL DEFAULT 0,
                    last_seen REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS dispatcher_groups (
                    group_id TEXT PRIMARY KEY,
                    last_reply_at REAL NOT NULL DEFAULT 0,
                    last_human_at REAL NOT NULL DEFAULT 0,
                    peer_turns INTEGER NOT NULL DEFAULT 0,
                    peer_window_started_at REAL NOT NULL DEFAULT 0,
                    open_loop_target_worker_id TEXT NOT NULL DEFAULT '',
                    open_loop_topic TEXT NOT NULL DEFAULT '',
                    open_loop_expires_at REAL NOT NULL DEFAULT 0,
                    active_lease_id TEXT NOT NULL DEFAULT '',
                    active_worker_id TEXT NOT NULL DEFAULT '',
                    active_event_id TEXT NOT NULL DEFAULT '',
                    active_expires_at REAL NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS dispatcher_worker_stats (
                    group_id TEXT NOT NULL,
                    worker_id TEXT NOT NULL,
                    last_reply_at REAL NOT NULL DEFAULT 0,
                    reply_count INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (group_id, worker_id)
                );
                CREATE TABLE IF NOT EXISTS dispatcher_events (
                    event_id TEXT PRIMARY KEY,
                    group_id TEXT NOT NULL,
                    worker_id TEXT NOT NULL DEFAULT '',
                    lease_id TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS dispatcher_candidates (
                    event_id TEXT NOT NULL,
                    group_id TEXT NOT NULL,
                    worker_id TEXT NOT NULL,
                    account_id TEXT NOT NULL DEFAULT '',
                    base_score REAL NOT NULL DEFAULT 0,
                    eligible INTEGER NOT NULL DEFAULT 0,
                    reason TEXT NOT NULL DEFAULT '',
                    sender_type TEXT NOT NULL DEFAULT 'human',
                    sender_account_id TEXT NOT NULL DEFAULT '',
                    topic_signature TEXT NOT NULL DEFAULT '',
                    target_account_ids TEXT NOT NULL DEFAULT '[]',
                    preferred_worker_id TEXT NOT NULL DEFAULT '',
                    submitted_at REAL NOT NULL,
                    PRIMARY KEY(event_id, worker_id)
                );
                CREATE INDEX IF NOT EXISTS dispatcher_events_updated_idx
                    ON dispatcher_events(updated_at);
                CREATE INDEX IF NOT EXISTS dispatcher_candidates_event_idx
                    ON dispatcher_candidates(event_id);
                """
            )
            # Older dispatcher databases predate the explanation field. Keep
            # them readable while allowing the WebUI to explain decisions.
            columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(dispatcher_events)").fetchall()
            }
            if "reason" not in columns:
                conn.execute(
                    "ALTER TABLE dispatcher_events ADD COLUMN reason TEXT NOT NULL DEFAULT ''"
                )
            group_columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(dispatcher_groups)").fetchall()
            }
            for name, definition in (
                ("open_loop_target_worker_id", "TEXT NOT NULL DEFAULT ''"),
                ("open_loop_topic", "TEXT NOT NULL DEFAULT ''"),
                ("open_loop_expires_at", "REAL NOT NULL DEFAULT 0"),
            ):
                if name not in group_columns:
                    conn.execute(f"ALTER TABLE dispatcher_groups ADD COLUMN {name} {definition}")
            candidate_columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(dispatcher_candidates)").fetchall()
            }
            if "topic_signature" not in candidate_columns:
                conn.execute(
                    "ALTER TABLE dispatcher_candidates ADD COLUMN topic_signature TEXT NOT NULL DEFAULT ''"
                )
            for name, definition in (
                ("base_score", "REAL NOT NULL DEFAULT 0"),
                ("final_score", "REAL NOT NULL DEFAULT 0"),
                ("activity_penalty", "REAL NOT NULL DEFAULT 0"),
            ):
                if name not in columns:
                    conn.execute(f"ALTER TABLE dispatcher_events ADD COLUMN {name} {definition}")

    def register(self) -> None:
        now = self._clock()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO dispatcher_workers(worker_id, account_id, priority, last_seen)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(worker_id) DO UPDATE SET
                    account_id=excluded.account_id,
                    priority=excluded.priority,
                    last_seen=excluded.last_seen
                """,
                (self.worker_id, self.account_id, self.priority, now),
            )

    def set_account_id(self, account_id: str) -> None:
        account_id = str(account_id or "").strip()
        if not account_id or account_id == self.account_id:
            return
        self.account_id = account_id
        self.register()

    def is_peer_account(self, account_id: str) -> bool:
        """Return whether a live registered worker owns this other account."""
        account_id = str(account_id or "").strip()
        if not account_id or account_id == self.account_id:
            return False
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM dispatcher_workers
                WHERE account_id=? AND last_seen>?
                LIMIT 1
                """,
                (account_id, self._clock() - self.registry_ttl_seconds),
            ).fetchone()
        return row is not None

    def close(self) -> None:
        """Expire this worker and release a lease it owns."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE dispatcher_workers SET last_seen = 0 WHERE worker_id = ?",
                (self.worker_id,),
            )
            conn.execute(
                """
                UPDATE dispatcher_groups
                SET active_lease_id='', active_worker_id='', active_event_id='', active_expires_at=0
                WHERE active_worker_id=?
                """,
                (self.worker_id,),
            )

    async def coordinate(
        self,
        *,
        event_id: str,
        group_id: str,
        base_score: float,
        should_reply: bool,
        sender_type: str = "human",
        sender_account_id: str = "",
        target_account_ids: tuple[str, ...] | list[str] = (),
        preferred_worker_id: str = "",
        reason: str = "",
        message_text: str = "",
    ) -> DispatchDecision:
        """Collect local candidates, then grant the highest final score."""
        self._submit_candidate(
            event_id=event_id,
            group_id=group_id,
            base_score=base_score,
            should_reply=should_reply,
            sender_type=sender_type,
            sender_account_id=sender_account_id,
            target_account_ids=target_account_ids,
            preferred_worker_id=preferred_worker_id,
            reason=reason,
            message_text=message_text,
        )
        deadline = time.monotonic() + self.score_collection_seconds
        while True:
            decision = self._finalize_candidate(event_id, group_id, force=False)
            if decision is not None:
                return decision
            if time.monotonic() >= deadline:
                decision = self._finalize_candidate(event_id, group_id, force=True)
                return decision or DispatchDecision("observe", event_id, reason="collection_timeout")
            await asyncio.sleep(0.01)

    def _submit_candidate(
        self,
        *,
        event_id: str,
        group_id: str,
        base_score: float,
        should_reply: bool,
        sender_type: str,
        sender_account_id: str,
        target_account_ids: tuple[str, ...] | list[str],
        preferred_worker_id: str,
        reason: str,
        message_text: str,
    ) -> None:
        event_id = str(event_id or "").strip()
        group_id = str(group_id or "").strip()
        if not event_id or not group_id:
            return
        now = self._clock()
        targets = sorted({str(value).strip() for value in target_account_ids if str(value).strip()})
        topic_signature = _topic_signature(message_text)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE dispatcher_workers SET last_seen=? WHERE worker_id=?",
                (now, self.worker_id),
            )
            existing = conn.execute(
                "SELECT status FROM dispatcher_events WHERE event_id=?", (event_id,)
            ).fetchone()
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO dispatcher_events(event_id, group_id, status, created_at, updated_at)
                    VALUES (?, ?, 'collecting', ?, ?)
                    """,
                    (event_id, group_id, now, now),
                )
            elif str(existing["status"]) != "collecting":
                return
            conn.execute(
                """
                INSERT INTO dispatcher_candidates(
                    event_id, group_id, worker_id, account_id, base_score, eligible,
                    reason, sender_type, sender_account_id, topic_signature, target_account_ids,
                    preferred_worker_id, submitted_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id, worker_id) DO UPDATE SET
                    account_id=excluded.account_id,
                    base_score=excluded.base_score,
                    eligible=excluded.eligible,
                    reason=excluded.reason,
                    sender_type=excluded.sender_type,
                    sender_account_id=excluded.sender_account_id,
                    topic_signature=excluded.topic_signature,
                    target_account_ids=excluded.target_account_ids,
                    preferred_worker_id=excluded.preferred_worker_id,
                    submitted_at=excluded.submitted_at
                """,
                (
                    event_id,
                    group_id,
                    self.worker_id,
                    self.account_id,
                    max(0.0, float(base_score)),
                    1 if should_reply else 0,
                    str(reason or ""),
                    str(sender_type or "human"),
                    str(sender_account_id or ""),
                    topic_signature,
                    json.dumps(targets, ensure_ascii=True),
                    str(preferred_worker_id or ""),
                    now,
                ),
            )

    @staticmethod
    def _clear_open_loop(conn: sqlite3.Connection, group_id: str) -> None:
        conn.execute(
            """
            UPDATE dispatcher_groups
            SET open_loop_target_worker_id='', open_loop_topic='', open_loop_expires_at=0
            WHERE group_id=?
            """,
            (group_id,),
        )

    def _open_loop_state(
        self,
        conn: sqlite3.Connection,
        group_id: str,
        state: sqlite3.Row,
        now: float,
    ) -> tuple[str, str]:
        expires_at = float(state["open_loop_expires_at"] or 0)
        if expires_at <= now:
            if expires_at:
                self._clear_open_loop(conn, group_id)
            return "", ""
        return (
            str(state["open_loop_target_worker_id"] or ""),
            str(state["open_loop_topic"] or ""),
        )

    def _peer_gate(
        self,
        state: sqlite3.Row,
        now: float,
        *,
        interaction_expected: bool,
    ) -> str:
        if self.max_peer_turns <= 0:
            return "peer_disabled"

        last_human_at = float(state["last_human_at"] or 0)
        if last_human_at and now - last_human_at < self.peer_cooldown_seconds:
            return "peer_waiting_for_humans"
        if int(state["peer_turns"] or 0) >= self.max_peer_turns:
            return "peer_budget_exhausted"
        if not interaction_expected:
            return "peer_topic_closed"
        return ""

    @staticmethod
    def _response_opens_peer_loop(
        text: str,
        *,
        target_worker_id: str,
        source_sender_type: str,
    ) -> bool:
        if target_worker_id:
            return True
        if _OPEN_LOOP_RE.search(text):
            return True
        return source_sender_type == "other_ai" and bool(_CONTINUATION_RE.search(text))

    def _find_peer_target(self, conn: sqlite3.Connection, text: str, now: float) -> str:
        normalized = _normalized_topic(text)
        if not normalized:
            return ""
        workers = conn.execute(
            """
            SELECT worker_id, account_id
            FROM dispatcher_workers
            WHERE last_seen > ? AND worker_id<>?
            ORDER BY LENGTH(worker_id) DESC
            """,
            (now - self.registry_ttl_seconds, self.worker_id),
        ).fetchall()
        for worker in workers:
            for candidate in (str(worker["worker_id"] or ""), str(worker["account_id"] or "")):
                if candidate and _normalized_topic(candidate) in normalized:
                    return str(worker["worker_id"] or "")
        return ""

    def _finalize_candidate(
        self,
        event_id: str,
        group_id: str,
        *,
        force: bool,
    ) -> DispatchDecision | None:
        now = self._clock()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            event = conn.execute(
                "SELECT * FROM dispatcher_events WHERE event_id=? AND group_id=?",
                (event_id, group_id),
            ).fetchone()
            if event is None:
                return DispatchDecision("observe", event_id, reason="missing_event")
            if str(event["status"]) != "collecting":
                return self._decision_from_event(event)

            candidates = conn.execute(
                "SELECT * FROM dispatcher_candidates WHERE event_id=? ORDER BY worker_id",
                (event_id,),
            ).fetchall()
            expected = conn.execute(
                "SELECT COUNT(*) FROM dispatcher_workers WHERE last_seen > ?",
                (now - self.registry_ttl_seconds,),
            ).fetchone()[0]
            if not force and len(candidates) < max(1, int(expected)) and now < float(event["created_at"]) + self.score_collection_seconds:
                return None

            first = candidates[0] if candidates else None
            sender_type = str(first["sender_type"] or "human") if first else "human"
            sender_account_id = str(first["sender_account_id"] or "") if first else ""
            message_topic = str(first["topic_signature"] or "") if first else ""
            target_accounts: set[str] = set()
            text_target_worker_ids = {
                str(row["preferred_worker_id"] or "").strip()
                for row in candidates
                if str(row["preferred_worker_id"] or "").strip()
            }
            text_target_worker_id = (
                next(iter(text_target_worker_ids)) if len(text_target_worker_ids) == 1 else ""
            )
            if first:
                try:
                    target_accounts = set(json.loads(str(first["target_account_ids"] or "[]")))
                except (TypeError, ValueError):
                    target_accounts = set()

            workers = conn.execute(
                """
                SELECT w.worker_id, w.account_id, w.priority
                FROM dispatcher_workers AS w
                WHERE w.last_seen > ?
                """,
                (now - self.registry_ttl_seconds,),
            ).fetchall()
            known_accounts = {str(row["account_id"] or "") for row in workers}
            known_targets = target_accounts & known_accounts
            eligible = [
                row
                for row in candidates
                if bool(row["eligible"])
                and (
                    not known_targets
                    or str(row["account_id"] or "") in known_targets
                )
            ]
            is_peer = sender_type == "other_ai" or (
                sender_account_id
                and sender_account_id in known_accounts
                and sender_account_id != self.account_id
            )
            self._expire_active_lease(conn, group_id, now)
            state = conn.execute(
                "SELECT * FROM dispatcher_groups WHERE group_id=?", (group_id,)
            ).fetchone()
            if state is None:
                conn.execute("INSERT OR IGNORE INTO dispatcher_groups(group_id) VALUES (?)", (group_id,))
                state = conn.execute(
                    "SELECT * FROM dispatcher_groups WHERE group_id=?", (group_id,)
                ).fetchone()
            assert state is not None
            open_target, open_topic = self._open_loop_state(conn, group_id, state, now)
            if is_peer:
                if known_targets:
                    directed = [
                        row
                        for row in candidates
                        if str(row["account_id"] or "") in known_targets
                    ]
                    if directed:
                        eligible = directed
                elif open_target:
                    eligible = [row for row in candidates if str(row["worker_id"]) == open_target]
                elif open_topic and _topic_similarity(open_topic, message_topic) >= 0.18:
                    # A non-directed question keeps the topic open. The dispatcher
                    # selects a peer even when its local preview was initially silent.
                    eligible = list(candidates)
            active_worker = str(state["active_worker_id"] or "")
            if active_worker:
                if is_peer and eligible:
                    return self._mark_candidate_deferred(
                        conn, event_id, now, active_worker, "group_busy"
                    )
                return self._mark_candidate_observed(conn, event_id, now, active_worker, "group_busy")

            is_human = sender_type not in {"other_ai", "system"}
            if not eligible:
                reason = (
                    "peer_target_unavailable"
                    if is_peer and (known_targets or open_target)
                    else "all_candidates_silent"
                )
                return self._mark_candidate_observed(conn, event_id, now, "", reason)
            if is_peer:
                interaction_expected = bool(known_targets or open_target)
                if not interaction_expected and open_topic:
                    interaction_expected = _topic_similarity(open_topic, message_topic) >= 0.18
                peer_reason = self._peer_gate(
                    state,
                    now,
                    interaction_expected=interaction_expected,
                )
                if peer_reason == "peer_waiting_for_humans":
                    return self._mark_candidate_deferred(
                        conn, event_id, now, "", "peer_waiting_for_humans"
                    )
                if peer_reason:
                    return self._mark_candidate_observed(conn, event_id, now, "", peer_reason)
            elif is_human:
                conn.execute(
                    """
                    UPDATE dispatcher_groups
                    SET last_human_at=?, peer_turns=0, peer_window_started_at=0,
                        open_loop_target_worker_id='', open_loop_topic='', open_loop_expires_at=0
                    WHERE group_id=?
                    """,
                    (now, group_id),
                )
                if (
                    not known_targets
                    and float(state["last_reply_at"] or 0)
                    and now - float(state["last_reply_at"]) < self.min_reply_interval_seconds
                ):
                    return self._mark_candidate_observed(conn, event_id, now, "", "reply_cooldown")

            recent_counts = self._recent_reply_counts(conn, group_id, now)
            ranked: list[dict[str, object]] = []
            for candidate in eligible:
                worker_id = str(candidate["worker_id"])
                worker = next((row for row in workers if row["worker_id"] == worker_id), None)
                priority = float(worker["priority"] or 0) if worker else 0.0
                recent_replies = recent_counts.get(worker_id, 0)
                activity_penalty = min(
                    self.max_activity_penalty,
                    recent_replies * self.activity_penalty_per_reply,
                )
                priority_bonus = max(-0.2, min(0.2, priority * 0.1))
                base_score = float(candidate["base_score"] or 0)
                text_target_adjustment = 0.0
                if text_target_worker_id:
                    text_target_adjustment = (
                        0.55 if worker_id == text_target_worker_id else -0.35
                    )
                final_score = (
                    base_score + priority_bonus - activity_penalty + text_target_adjustment
                )
                ranked.append(
                    {
                        "worker_id": worker_id,
                        "base_score": base_score,
                        "final_score": final_score,
                        "activity_penalty": activity_penalty,
                        "priority": priority,
                        "recent_replies": recent_replies,
                    }
                )
            ranked.sort(
                key=lambda item: (
                    -float(item["final_score"]),
                    -float(item["base_score"]),
                    int(item["recent_replies"]),
                    -float(item["priority"]),
                    str(item["worker_id"]),
                )
            )
            selected = ranked[0]
            lease_id = f"dl_{uuid4().hex}"
            expires_at = now + self.lease_seconds
            conn.execute(
                """
                UPDATE dispatcher_events
                SET worker_id=?, lease_id=?, status='granted', reason='selected',
                    base_score=?, final_score=?, activity_penalty=?, updated_at=?
                WHERE event_id=?
                """,
                (
                    selected["worker_id"],
                    lease_id,
                    selected["base_score"],
                    selected["final_score"],
                    selected["activity_penalty"],
                    now,
                    event_id,
                ),
            )
            conn.execute(
                """
                UPDATE dispatcher_groups
                SET active_lease_id=?, active_worker_id=?, active_event_id=?, active_expires_at=?
                WHERE group_id=?
                """,
                (lease_id, selected["worker_id"], event_id, expires_at, group_id),
            )
            if is_peer:
                conn.execute(
                    """
                    UPDATE dispatcher_groups
                    SET peer_turns=peer_turns+1,
                        peer_window_started_at=CASE WHEN peer_window_started_at=0 THEN ? ELSE peer_window_started_at END,
                        open_loop_target_worker_id='', open_loop_topic='', open_loop_expires_at=0
                    WHERE group_id=?
                    """,
                    (now, group_id),
                )
            return self._decision_from_event(
                conn.execute(
                    "SELECT * FROM dispatcher_events WHERE event_id=?", (event_id,)
                ).fetchone()
            )

    def _decision_from_event(self, event: sqlite3.Row) -> DispatchDecision:
        worker_id = str(event["worker_id"] or "")
        status = str(event["status"] or "")
        base_score = float(event["base_score"] or 0)
        final_score = float(event["final_score"] or 0)
        activity_penalty = float(event["activity_penalty"] or 0)
        if status == "granted" and worker_id == self.worker_id and event["lease_id"]:
            return DispatchDecision(
                "grant",
                str(event["event_id"]),
                worker_id=worker_id,
                lease_id=str(event["lease_id"]),
                reason=str(event["reason"] or "selected"),
                base_score=base_score,
                final_score=final_score,
                activity_penalty=activity_penalty,
            )
        return DispatchDecision(
            "observe",
            str(event["event_id"]),
            worker_id=worker_id,
            reason=(
                "another_worker_selected"
                if status == "granted" and worker_id
                else str(event["reason"] or f"event_{status}")
            ),
            base_score=base_score,
            final_score=final_score,
            activity_penalty=activity_penalty,
        )

    @staticmethod
    def _mark_candidate_observed(
        conn: sqlite3.Connection,
        event_id: str,
        now: float,
        worker_id: str,
        reason: str,
    ) -> DispatchDecision:
        conn.execute(
            "UPDATE dispatcher_events SET status='observed', worker_id=?, reason=?, updated_at=? WHERE event_id=?",
            (worker_id, reason, now, event_id),
        )
        return DispatchDecision("observe", event_id, worker_id=worker_id, reason=reason)

    @staticmethod
    def _mark_candidate_deferred(
        conn: sqlite3.Connection,
        event_id: str,
        now: float,
        worker_id: str,
        reason: str,
    ) -> DispatchDecision:
        conn.execute(
            "UPDATE dispatcher_events SET worker_id=?, reason=?, updated_at=? WHERE event_id=? AND status='collecting'",
            (worker_id, reason, now, event_id),
        )
        return DispatchDecision("defer", event_id, worker_id=worker_id, reason=reason)

    def _recent_reply_counts(
        self, conn: sqlite3.Connection, group_id: str, now: float
    ) -> dict[str, int]:
        rows = conn.execute(
            """
            SELECT worker_id, COUNT(*) AS reply_count
            FROM dispatcher_events
            WHERE group_id=? AND status='sent' AND updated_at>=?
            GROUP BY worker_id
            """,
            (group_id, now - self.activity_window_seconds),
        ).fetchall()
        return {str(row["worker_id"]): int(row["reply_count"] or 0) for row in rows}

    def admit(
        self,
        *,
        event_id: str,
        group_id: str,
        sender_type: str = "human",
        sender_account_id: str = "",
        target_account_ids: tuple[str, ...] | list[str] = (),
        preferred_worker_id: str = "",
        message_text: str = "",
    ) -> DispatchDecision:
        """Return a grant for the selected worker, or observe for everyone else."""
        event_id = str(event_id or "").strip()
        group_id = str(group_id or "").strip()
        if not event_id or not group_id:
            return DispatchDecision("observe", event_id, reason="missing_identity")

        now = self._clock()
        target_accounts = {str(value).strip() for value in target_account_ids if str(value).strip()}
        sender_account_id = str(sender_account_id or "").strip()
        message_topic = _topic_signature(message_text)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE dispatcher_workers SET last_seen=? WHERE worker_id=?",
                (now, self.worker_id),
            )
            conn.execute(
                "INSERT OR IGNORE INTO dispatcher_groups(group_id) VALUES (?)", (group_id,)
            )
            self._expire_active_lease(conn, group_id, now)
            self._prune_events(conn, now)

            existing = conn.execute(
                "SELECT worker_id, lease_id, status FROM dispatcher_events WHERE event_id=?",
                (event_id,),
            ).fetchone()
            if existing is not None:
                if existing["status"] == "collecting":
                    pass
                elif (
                    existing["worker_id"] == self.worker_id
                    and existing["status"] == "granted"
                    and existing["lease_id"]
                ):
                    return DispatchDecision(
                        "grant",
                        event_id,
                        worker_id=self.worker_id,
                        lease_id=existing["lease_id"],
                        reason="idempotent_claim",
                    )
                return DispatchDecision(
                    "observe",
                    event_id,
                    worker_id=existing["worker_id"],
                    reason=f"event_{existing['status']}",
                )

            state = conn.execute(
                "SELECT * FROM dispatcher_groups WHERE group_id=?", (group_id,)
            ).fetchone()
            assert state is not None
            open_target, open_topic = self._open_loop_state(conn, group_id, state, now)
            active_worker = str(state["active_worker_id"] or "")
            if active_worker:
                self._record_event(
                    conn, event_id, group_id, "", "observed", now, reason="group_busy"
                )
                return DispatchDecision("observe", event_id, worker_id=active_worker, reason="group_busy")

            workers = conn.execute(
                """
                SELECT w.worker_id, w.account_id, w.priority,
                       COALESCE(s.last_reply_at, 0) AS last_worker_reply_at
                FROM dispatcher_workers AS w
                LEFT JOIN dispatcher_worker_stats AS s
                  ON s.worker_id=w.worker_id AND s.group_id=?
                WHERE w.last_seen > ?
                ORDER BY last_worker_reply_at ASC, w.priority DESC, w.worker_id ASC
                """,
                (group_id, now - self.registry_ttl_seconds),
            ).fetchall()
            if not workers:
                self._record_event(
                    conn, event_id, group_id, "", "observed", now, reason="no_workers"
                )
                return DispatchDecision("observe", event_id, reason="no_workers")

            known_accounts = {str(row["account_id"] or "") for row in workers}
            known_targets = target_accounts & known_accounts
            eligible = [
                row
                for row in workers
                if not known_targets or str(row["account_id"] or "") in known_targets
            ]
            if preferred_worker_id:
                preferred = [row for row in eligible if row["worker_id"] == preferred_worker_id]
                if preferred:
                    eligible = preferred + [row for row in eligible if row not in preferred]

            is_peer = sender_type == "other_ai" or (
                sender_account_id
                and sender_account_id in known_accounts
                and sender_account_id != self.account_id
            )
            is_human = sender_type not in {"other_ai", "system"}
            if is_peer:
                if open_target:
                    eligible = [row for row in eligible if str(row["worker_id"]) == open_target]
                    preferred_worker_id = open_target
                interaction_expected = bool(known_targets or open_target)
                if not interaction_expected and open_topic:
                    interaction_expected = _topic_similarity(open_topic, message_topic) >= 0.18
                peer_reason = self._peer_gate(
                    state,
                    now,
                    interaction_expected=interaction_expected,
                )
                if peer_reason:
                    self._record_event(
                        conn,
                        event_id,
                        group_id,
                        "",
                        "observed",
                        now,
                        reason=peer_reason,
                    )
                    return DispatchDecision("observe", event_id, reason=peer_reason)
            elif is_human:
                conn.execute(
                    """
                    UPDATE dispatcher_groups
                    SET last_human_at=?, peer_turns=0, peer_window_started_at=0,
                        open_loop_target_worker_id='', open_loop_topic='', open_loop_expires_at=0
                    WHERE group_id=?
                    """,
                    (now, group_id),
                )
                if (
                    not known_targets
                    and float(state["last_reply_at"] or 0)
                    and now - float(state["last_reply_at"]) < self.min_reply_interval_seconds
                ):
                    self._record_event(
                        conn, event_id, group_id, "", "observed", now, reason="reply_cooldown"
                    )
                    return DispatchDecision("observe", event_id, reason="reply_cooldown")

            if not eligible:
                self._record_event(
                    conn,
                    event_id,
                    group_id,
                    "",
                    "observed",
                    now,
                    reason="peer_target_unavailable" if is_peer and open_target else "target_unavailable",
                )
                return DispatchDecision(
                    "observe",
                    event_id,
                    reason="peer_target_unavailable" if is_peer and open_target else "target_unavailable",
                )

            selected = eligible[0]
            selected_worker = str(selected["worker_id"])
            lease_id = f"dl_{uuid4().hex}"
            expires_at = now + self.lease_seconds
            self._record_event(
                conn,
                event_id,
                group_id,
                selected_worker,
                "granted",
                now,
                lease_id,
                reason="selected",
            )
            conn.execute(
                """
                UPDATE dispatcher_groups
                SET active_lease_id=?, active_worker_id=?, active_event_id=?, active_expires_at=?
                WHERE group_id=?
                """,
                (lease_id, selected_worker, event_id, expires_at, group_id),
            )
            if is_peer:
                conn.execute(
                    """
                    UPDATE dispatcher_groups
                    SET peer_turns=peer_turns+1,
                        peer_window_started_at=CASE WHEN peer_window_started_at=0 THEN ? ELSE peer_window_started_at END,
                        open_loop_target_worker_id='', open_loop_topic='', open_loop_expires_at=0
                    WHERE group_id=?
                    """,
                    (now, group_id),
                )
            action = "grant" if selected_worker == self.worker_id else "observe"
            return DispatchDecision(
                action,
                event_id,
                worker_id=selected_worker,
                lease_id=lease_id if action == "grant" else "",
                reason="selected" if action == "grant" else "another_worker_selected",
            )

    def _record_open_loop(
        self,
        conn: sqlite3.Connection,
        group_id: str,
        event_id: str,
        response_text: str,
        now: float,
    ) -> None:
        source = conn.execute(
            """
            SELECT sender_type
            FROM dispatcher_candidates
            WHERE event_id=?
            ORDER BY submitted_at ASC
            LIMIT 1
            """,
            (event_id,),
        ).fetchone()
        source_sender_type = str(source["sender_type"] or "human") if source else "human"
        target_worker_id = self._find_peer_target(conn, response_text, now)
        if not self._response_opens_peer_loop(
            response_text,
            target_worker_id=target_worker_id,
            source_sender_type=source_sender_type,
        ):
            self._clear_open_loop(conn, group_id)
            return
        topic = _topic_signature(response_text)
        if not topic:
            self._clear_open_loop(conn, group_id)
            return
        conn.execute(
            """
            UPDATE dispatcher_groups
            SET open_loop_target_worker_id=?, open_loop_topic=?, open_loop_expires_at=?
            WHERE group_id=?
            """,
            (target_worker_id, topic, now + 60.0, group_id),
        )

    def finish(self, lease_id: str, *, sent: bool, response_text: str = "") -> bool:
        """Release a lease and remember whether the reply leaves a peer loop open."""
        lease_id = str(lease_id or "").strip()
        if not lease_id:
            return False
        now = self._clock()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT group_id, active_worker_id, active_event_id
                FROM dispatcher_groups
                WHERE active_lease_id=?
                """,
                (lease_id,),
            ).fetchone()
            if row is None or row["active_worker_id"] != self.worker_id:
                return False
            group_id = str(row["group_id"])
            event_id = str(row["active_event_id"] or "")
            status = "sent" if sent else "silent"
            conn.execute(
                "UPDATE dispatcher_events SET status=?, updated_at=? WHERE lease_id=?",
                (status, now, lease_id),
            )
            conn.execute(
                """
                UPDATE dispatcher_groups
                SET last_reply_at=CASE WHEN ? THEN ? ELSE last_reply_at END,
                    active_lease_id='', active_worker_id='', active_event_id='', active_expires_at=0
                WHERE group_id=? AND active_lease_id=?
                """,
                (1 if sent else 0, now, group_id, lease_id),
            )
            if sent:
                self._record_open_loop(conn, group_id, event_id, response_text, now)
                conn.execute(
                    """
                    INSERT INTO dispatcher_worker_stats(group_id, worker_id, last_reply_at, reply_count)
                    VALUES (?, ?, ?, 1)
                    ON CONFLICT(group_id, worker_id) DO UPDATE SET
                        last_reply_at=excluded.last_reply_at,
                        reply_count=reply_count+1
                    """,
                    (group_id, self.worker_id, now),
                )
            return True

    def active_lease(self, group_id: str) -> str:
        now = self._clock()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT active_lease_id FROM dispatcher_groups
                WHERE group_id=? AND active_worker_id=? AND active_expires_at>?
                """,
                (str(group_id), self.worker_id, now),
            ).fetchone()
            return str(row["active_lease_id"] or "") if row else ""

    @classmethod
    def read_snapshot(
        cls,
        db_path: str | Path,
        *,
        now: float | None = None,
        event_limit: int = 80,
    ) -> dict[str, object]:
        """Read coordinator state without registering a WebUI worker."""
        path = Path(db_path)
        if not path.exists():
            return {"available": False, "db_path": str(path)}

        current = time.time() if now is None else float(now)
        try:
            conn = sqlite3.connect(str(path), timeout=1.0)
            conn.row_factory = sqlite3.Row
            tables = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            required = {
                "dispatcher_workers",
                "dispatcher_groups",
                "dispatcher_worker_stats",
                "dispatcher_events",
            }
            if not required.issubset(tables):
                conn.close()
                return {"available": False, "db_path": str(path), "reason": "schema_missing"}

            event_columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(dispatcher_events)").fetchall()
            }
            group_columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(dispatcher_groups)").fetchall()
            }
            reason_sql = "reason" if "reason" in event_columns else "'' AS reason"
            base_score_sql = "base_score" if "base_score" in event_columns else "0 AS base_score"
            final_score_sql = "final_score" if "final_score" in event_columns else "0 AS final_score"
            activity_penalty_sql = (
                "activity_penalty" if "activity_penalty" in event_columns else "0 AS activity_penalty"
            )
            workers = [
                {
                    "worker_id": str(row["worker_id"] or ""),
                    "account_id": str(row["account_id"] or ""),
                    "priority": float(row["priority"] or 0),
                    "last_seen": float(row["last_seen"] or 0),
                    "online": float(row["last_seen"] or 0) > current - 180.0,
                    "last_reply_at": float(row["last_reply_at"] or 0),
                    "reply_count": int(row["reply_count"] or 0),
                    "recent_reply_count": int(row["recent_reply_count"] or 0),
                }
                for row in conn.execute(
                    """
                    SELECT w.worker_id, w.account_id, w.priority, w.last_seen,
                           COALESCE(MAX(s.last_reply_at), 0) AS last_reply_at,
                           COALESCE(SUM(s.reply_count), 0) AS reply_count,
                           COALESCE((
                               SELECT COUNT(*)
                               FROM dispatcher_events AS recent_events
                               WHERE recent_events.worker_id=w.worker_id
                                 AND recent_events.status='sent'
                                 AND recent_events.updated_at>=?
                           ), 0) AS recent_reply_count
                    FROM dispatcher_workers AS w
                    LEFT JOIN dispatcher_worker_stats AS s ON s.worker_id=w.worker_id
                    GROUP BY w.worker_id, w.account_id, w.priority, w.last_seen
                    ORDER BY w.last_seen DESC, w.priority DESC, w.worker_id ASC
                    """,
                    (current - 300.0,),
                ).fetchall()
            ]
            groups = []
            for row in conn.execute(
                """
                SELECT * FROM dispatcher_groups
                ORDER BY CASE WHEN active_worker_id <> '' THEN 0 ELSE 1 END,
                         COALESCE(last_reply_at, 0) DESC, group_id ASC
                LIMIT 200
                """
            ).fetchall():
                expires_at = float(row["active_expires_at"] or 0)
                active = bool(row["active_worker_id"] and expires_at > current)
                groups.append(
                    {
                        "group_id": str(row["group_id"] or ""),
                        "last_reply_at": float(row["last_reply_at"] or 0),
                        "last_human_at": float(row["last_human_at"] or 0),
                        "peer_turns": int(row["peer_turns"] or 0),
                        "peer_window_started_at": float(row["peer_window_started_at"] or 0),
                        "active": active,
                        "active_worker_id": str(row["active_worker_id"] or "") if active else "",
                        "active_event_id": str(row["active_event_id"] or "") if active else "",
                        "active_lease_id": str(row["active_lease_id"] or "") if active else "",
                        "active_expires_at": expires_at if active else 0,
                        "active_remaining_seconds": max(0.0, expires_at - current) if active else 0,
                    }
                )

            limit = max(1, min(int(event_limit), 200))
            events = [
                {
                    "event_id": str(row["event_id"] or ""),
                    "group_id": str(row["group_id"] or ""),
                    "worker_id": str(row["worker_id"] or ""),
                    "lease_id": str(row["lease_id"] or ""),
                    "status": str(row["status"] or ""),
                    "reason": str(row["reason"] or ""),
                    "base_score": float(row["base_score"] or 0),
                    "final_score": float(row["final_score"] or 0),
                    "activity_penalty": float(row["activity_penalty"] or 0),
                    "created_at": float(row["created_at"] or 0),
                    "updated_at": float(row["updated_at"] or 0),
                }
                for row in conn.execute(
                    f"""
                    SELECT event_id, group_id, worker_id, lease_id, status,
                           {reason_sql}, {base_score_sql}, {final_score_sql},
                           {activity_penalty_sql}, created_at, updated_at
                    FROM dispatcher_events
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            ]
            since = current - 86400.0
            counts = conn.execute(
                """
                SELECT
                    COUNT(*) AS decisions,
                    SUM(CASE WHEN status IN ('granted', 'sent', 'silent', 'expired') THEN 1 ELSE 0 END) AS granted,
                    SUM(CASE WHEN status='sent' THEN 1 ELSE 0 END) AS sent,
                    SUM(CASE WHEN status='observed' THEN 1 ELSE 0 END) AS observed
                FROM dispatcher_events
                WHERE updated_at >= ?
                """,
                (since,),
            ).fetchone()
            conn.close()
            return {
                "available": True,
                "db_path": str(path),
                "updated_at": current,
                "workers": workers,
                "groups": groups,
                "events": events,
                "summary": {
                    "workers_total": len(workers),
                    "workers_online": sum(1 for item in workers if item["online"]),
                    "groups_total": len(groups),
                    "active_turns": sum(1 for item in groups if item["active"]),
                    "decisions_24h": int(counts["decisions"] or 0),
                    "granted_24h": int(counts["granted"] or 0),
                    "sent_24h": int(counts["sent"] or 0),
                    "observed_24h": int(counts["observed"] or 0),
                },
            }
        except (OSError, sqlite3.Error) as exc:
            return {"available": False, "db_path": str(path), "reason": str(exc)}

    def _expire_active_lease(self, conn: sqlite3.Connection, group_id: str, now: float) -> None:
        row = conn.execute(
            """
            SELECT active_lease_id FROM dispatcher_groups
            WHERE group_id=? AND active_lease_id<>'' AND active_expires_at<=?
            """,
            (group_id, now),
        ).fetchone()
        if row is None:
            return
        conn.execute(
            "UPDATE dispatcher_events SET status='expired', updated_at=? WHERE lease_id=?",
            (now, row["active_lease_id"]),
        )
        conn.execute(
            """
            UPDATE dispatcher_groups
            SET active_lease_id='', active_worker_id='', active_event_id='', active_expires_at=0
            WHERE group_id=?
            """,
            (group_id,),
        )

    @staticmethod
    def _record_event(
        conn: sqlite3.Connection,
        event_id: str,
        group_id: str,
        worker_id: str,
        status: str,
        now: float,
        lease_id: str = "",
        reason: str = "",
    ) -> None:
        conn.execute(
            """
            INSERT INTO dispatcher_events(
                event_id, group_id, worker_id, lease_id, status, reason, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (event_id, group_id, worker_id, lease_id, status, reason, now, now),
        )

    @staticmethod
    def _prune_events(conn: sqlite3.Connection, now: float) -> None:
        conn.execute(
            "DELETE FROM dispatcher_events WHERE updated_at < ?",
            (now - 86400.0,),
        )
        conn.execute(
            "DELETE FROM dispatcher_candidates WHERE submitted_at < ?",
            (now - 86400.0,),
        )


__all__ = ["DispatchDecision", "GroupDispatcher"]
