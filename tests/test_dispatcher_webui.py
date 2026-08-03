from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from sirius_pulse.core.group_dispatcher import GroupDispatcher
from sirius_pulse.webui.dispatcher_api import api_dispatcher_overview


def _write_adapters(path: Path, **overrides: object) -> None:
    config = {
        "adapters": [
            {
                "type": "napcat",
                "enabled": True,
                "group_dispatch_enabled": True,
                "dispatch_db_path": "",
                "dispatch_min_reply_interval_seconds": 3,
                "dispatch_lease_seconds": 120,
                "dispatch_peer_cooldown_seconds": 60,
                "dispatch_max_peer_turns": 1,
                **overrides,
            }
        ]
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config), encoding="utf-8")


@pytest.mark.asyncio
async def test_dispatcher_overview_when_workers_are_running_then_returns_live_state(tmp_path: Path):
    db_path = tmp_path / "dispatcher" / "dispatcher.db"
    persona_dir = tmp_path / "personas" / "alpha"
    _write_adapters(persona_dir / "adapters.json")
    dispatcher = GroupDispatcher(db_path, worker_id="alpha", account_id="100")
    turn = dispatcher.admit(event_id="m1", group_id="g1")
    assert turn.granted
    dispatcher.finish(turn.lease_id, sent=True)

    response = await api_dispatcher_overview(SimpleNamespace(), tmp_path)
    payload = json.loads(response.text)

    assert payload["available"] is True
    assert payload["summary"]["workers_online"] == 1
    assert payload["summary"]["sent_24h"] == 1
    assert payload["policy"]["dispatch_enabled"] is True
    assert payload["policy"]["values"]["dispatch_lease_seconds"] == 120


@pytest.mark.asyncio
async def test_dispatcher_overview_when_database_is_missing_then_returns_configured_empty_state(tmp_path: Path):
    _write_adapters(tmp_path / "personas" / "alpha" / "adapters.json")

    response = await api_dispatcher_overview(SimpleNamespace(), tmp_path)
    payload = json.loads(response.text)

    assert payload["available"] is False
    assert payload["policy"]["adapters_total"] == 1
    assert payload["policy"]["dispatch_enabled"] is True
