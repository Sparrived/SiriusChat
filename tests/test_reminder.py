from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from sirius_pulse.tools.builtin import reminder


def test_daily_reminder_uses_china_time_and_catches_up_after_target_minute():
    now = datetime(2026, 8, 12, 1, 5, tzinfo=timezone.utc)
    scheduled = {
        "mode": "daily",
        "time": "09:00",
        "created_at": "2026-08-11T14:00:00+00:00",
    }

    assert reminder._is_reminder_due(scheduled, now) is True


def test_daily_reminder_created_after_target_does_not_fire_immediately():
    now = datetime(2026, 8, 12, 2, 5, tzinfo=timezone.utc)
    scheduled = {
        "mode": "daily",
        "time": "09:00",
        "created_at": "2026-08-12T02:00:00+00:00",
    }

    assert reminder._is_reminder_due(scheduled, now) is False


@pytest.mark.asyncio
async def test_recurring_reminder_persists_fire_state(monkeypatch):
    class Store:
        def __init__(self):
            self.reminders = [
                {
                    "id": "rem_test",
                    "mode": "daily",
                    "time": "09:00",
                    "created_at": "2026-08-11T14:00:00+00:00",
                    "group_id": "group-1",
                    "content": "测试提醒",
                    "fire_count": 0,
                }
            ]
            self.saved = 0
            self.set_values = []

        def get(self, key, default=None):
            return self.reminders if key == "reminders" else default

        def set(self, key, value):
            self.set_values.append((key, value))
            self.reminders = value

        def save(self):
            self.saved += 1

    store = Store()
    queued = []
    ctx = SimpleNamespace(
        get_data_store=lambda name: store,
        get_persona=lambda: None,
        get_tool_descriptions=lambda caller_is_developer=False: "",
        queue_pending_message=lambda *args: queued.append(args),
        log_inner_thought=lambda *_args: None,
        emit_event=lambda *_args: None,
        tool_executor=None,
    )

    async def fake_generate(*_args, **_kwargs):
        return "到时间啦"

    async def fake_emit_event(*_args, **_kwargs):
        return None

    ctx.emit_event = fake_emit_event

    monkeypatch.setattr(reminder, "_is_reminder_due", lambda *_args: True)
    monkeypatch.setattr(reminder, "_generate_reminder_message", fake_generate)
    await reminder._check_and_fire_reminders(ctx)

    assert store.saved == 1
    assert store.set_values
    assert store.reminders[0]["fire_count"] == 1
    assert queued == [("group-1", "到时间啦", "")]
