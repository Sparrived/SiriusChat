from __future__ import annotations

import asyncio
from pathlib import Path

from sirius_pulse.core.group_dispatcher import GroupDispatcher


class _Clock:
    def __init__(self, value: float = 100.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def _dispatcher(
    path: Path,
    clock: _Clock,
    worker_id: str,
    account_id: str,
    peer_cooldown_seconds: float = 60,
    **kwargs,
) -> GroupDispatcher:
    return GroupDispatcher(
        path,
        worker_id=worker_id,
        account_id=account_id,
        clock=clock,
        min_reply_interval_seconds=3,
        peer_cooldown_seconds=peer_cooldown_seconds,
        **kwargs,
    )


def test_group_dispatcher_selects_one_worker_and_releases_turn(tmp_path: Path):
    clock = _Clock()
    db_path = tmp_path / "dispatcher.db"
    first = _dispatcher(db_path, clock, "alpha", "100")
    second = _dispatcher(db_path, clock, "beta", "200")

    granted = first.admit(event_id="m1", group_id="g1")
    observed = second.admit(event_id="m1", group_id="g1")

    assert granted.granted
    assert observed.action == "observe"
    assert observed.reason == "event_granted"

    assert first.finish(granted.lease_id, sent=True)
    clock.value += 4
    next_turn = second.admit(event_id="m2", group_id="g1")
    assert next_turn.granted
    assert next_turn.worker_id == "beta"


def test_group_dispatcher_targeted_account_wins_and_bypasses_cooldown(tmp_path: Path):
    clock = _Clock()
    db_path = tmp_path / "dispatcher.db"
    first = _dispatcher(db_path, clock, "alpha", "100")
    second = _dispatcher(db_path, clock, "beta", "200")

    first_turn = first.admit(event_id="m1", group_id="g1")
    assert first_turn.granted
    assert first.finish(first_turn.lease_id, sent=True)

    blocked = first.admit(event_id="m2", group_id="g1")
    assert blocked.action == "observe"
    assert blocked.reason == "reply_cooldown"

    targeted = second.admit(
        event_id="m3",
        group_id="g1",
        target_account_ids=("200",),
    )
    assert targeted.granted
    assert targeted.worker_id == "beta"


def test_group_dispatcher_limits_peer_turns_until_humans_return(tmp_path: Path):
    clock = _Clock()
    db_path = tmp_path / "dispatcher.db"
    first = _dispatcher(db_path, clock, "alpha", "100", max_peer_turns=1)
    second = _dispatcher(db_path, clock, "beta", "200", max_peer_turns=1)

    human = first.admit(event_id="m1", group_id="g1")
    assert human.granted
    assert first.finish(human.lease_id, sent=True)

    clock.value += 61
    peer = second.admit(
        event_id="m2",
        group_id="g1",
        sender_type="other_ai",
        sender_account_id="100",
    )
    assert peer.granted
    assert second.finish(peer.lease_id, sent=True)

    clock.value += 1
    another_peer = first.admit(
        event_id="m3",
        group_id="g1",
        sender_type="other_ai",
        sender_account_id="200",
    )
    assert another_peer.action == "observe"
    assert another_peer.reason == "peer_budget_exhausted"


def test_group_dispatcher_defers_peer_event_until_active_turn_releases(tmp_path: Path):
    async def run() -> None:
        clock = _Clock()
        db_path = tmp_path / "dispatcher.db"
        first = _dispatcher(db_path, clock, "alpha", "100", peer_cooldown_seconds=0)
        second = _dispatcher(db_path, clock, "beta", "200", peer_cooldown_seconds=0)

        human = first.admit(event_id="m1", group_id="g1")
        assert human.granted

        pending = await second.coordinate(
            event_id="m2",
            group_id="g1",
            base_score=1.0,
            should_reply=True,
            sender_type="other_ai",
            sender_account_id="100",
        )
        assert pending.deferred
        assert pending.reason == "group_busy"

        assert first.finish(human.lease_id, sent=True)
        resumed = await second.coordinate(
            event_id="m2",
            group_id="g1",
            base_score=1.0,
            should_reply=True,
            sender_type="other_ai",
            sender_account_id="100",
        )
        assert resumed.granted

    asyncio.run(run())


def test_group_dispatcher_expired_lease_does_not_block_group(tmp_path: Path):
    clock = _Clock()
    db_path = tmp_path / "dispatcher.db"
    first = _dispatcher(db_path, clock, "alpha", "100", lease_seconds=5)
    second = _dispatcher(db_path, clock, "beta", "200", lease_seconds=5)

    turn = first.admit(event_id="m1", group_id="g1")
    assert turn.granted
    clock.value += 6

    replacement = second.admit(event_id="m2", group_id="g1")
    assert replacement.action == "observe"
    assert first.admit(event_id="m2", group_id="g1").granted


def test_group_dispatcher_read_snapshot_does_not_register_a_webui_worker(tmp_path: Path):
    clock = _Clock()
    db_path = tmp_path / "dispatcher.db"
    first = _dispatcher(db_path, clock, "alpha", "100")
    second = _dispatcher(db_path, clock, "beta", "200")

    turn = first.admit(event_id="m1", group_id="g1")
    assert turn.granted
    assert first.finish(turn.lease_id, sent=True)

    snapshot = GroupDispatcher.read_snapshot(db_path, now=clock.value)

    assert snapshot["available"] is True
    assert {worker["worker_id"] for worker in snapshot["workers"]} == {"alpha", "beta"}
    assert snapshot["summary"]["sent_24h"] == 1
    assert snapshot["events"][0]["reason"] == "selected"
    assert all(worker["worker_id"] != "webui" for worker in snapshot["workers"])
    second.close()


def test_group_dispatcher_final_score_selects_highest_candidate(tmp_path: Path):
    async def run() -> None:
        clock = _Clock()
        db_path = tmp_path / "dispatcher.db"
        first = _dispatcher(db_path, clock, "alpha", "100")
        second = _dispatcher(db_path, clock, "beta", "200")

        alpha, beta = await asyncio.gather(
            first.coordinate(
                event_id="scored-1",
                group_id="g1",
                base_score=0.55,
                should_reply=True,
            ),
            second.coordinate(
                event_id="scored-1",
                group_id="g1",
                base_score=0.90,
                should_reply=True,
            ),
        )
        assert not alpha.granted
        assert beta.granted
        assert beta.base_score == 0.9
        assert beta.final_score == 0.9

    asyncio.run(run())


def test_group_dispatcher_penalizes_recently_active_worker(tmp_path: Path):
    async def run() -> None:
        clock = _Clock()
        db_path = tmp_path / "dispatcher.db"
        first = _dispatcher(db_path, clock, "alpha", "100")
        second = _dispatcher(db_path, clock, "beta", "200")

        alpha, beta = await asyncio.gather(
            first.coordinate(
                event_id="active-1",
                group_id="g1",
                base_score=0.90,
                should_reply=True,
            ),
            second.coordinate(
                event_id="active-1",
                group_id="g1",
                base_score=0.20,
                should_reply=True,
            ),
        )
        assert alpha.granted
        assert first.finish(alpha.lease_id, sent=True)

        clock.value += 4
        next_alpha, next_beta = await asyncio.gather(
            first.coordinate(
                event_id="active-2",
                group_id="g1",
                base_score=0.80,
                should_reply=True,
            ),
            second.coordinate(
                event_id="active-2",
                group_id="g1",
                base_score=0.75,
                should_reply=True,
            ),
        )
        assert not next_alpha.granted
        assert next_beta.granted
        assert next_beta.activity_penalty == 0.0

    asyncio.run(run())
