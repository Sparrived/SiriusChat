from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from sirius_pulse.skills.builtin import github_monitor


class _Client:
    def __init__(self, **_: Any) -> None:
        pass

    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None


@pytest.mark.asyncio
async def test_poll_recovers_from_persisted_pre_restart_timestamp(monkeypatch):
    data: dict[str, Any] = {
        "poll_seconds": 30,
        "repos": [
            {
                "owner": "Sparrived",
                "repo": "SiriusPulse",
                "mode": "poll",
                "events": ["pushes"],
                "groups": ["1057020972"],
            }
        ],
        "_last_poll_at": {"Sparrived/SiriusPulse": 10_210_977.687},
    }
    store = Mock()
    store.get.side_effect = lambda key, default=None: data.get(key, default)
    store.set.side_effect = lambda key, value: data.__setitem__(key, value)
    ctx = Mock()
    ctx.get_data_store.return_value = store
    fetch_events = AsyncMock(return_value=[])

    monkeypatch.setattr(github_monitor, "GitHubClient", _Client)
    monkeypatch.setattr(github_monitor, "fetch_repo_events", fetch_events)
    monkeypatch.setattr(github_monitor.time, "time", lambda: 1_754_000_000.0)

    await github_monitor._poll_github_events(ctx)

    fetch_events.assert_awaited_once()
    assert data["_last_poll_at"]["Sparrived/SiriusPulse"] == 1_754_000_000.0
