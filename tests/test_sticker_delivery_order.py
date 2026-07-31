from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from sirius_pulse.core.events import SessionEvent, SessionEventType
from sirius_pulse.platforms.onebot_v11.napcat.adapter import NapCatAdapter


@pytest.mark.asyncio
async def test_napcat_delayed_delivery_sends_text_before_sticker():
    adapter = NapCatAdapter("ws://example.invalid")
    order: list[str] = []

    async def fake_send_group_msg(group_id, message):
        order.append("text")
        return {"ok": True}

    async def fake_send_stickers(group_id, names):
        order.append("sticker")
        return {"ok": True}

    async def fake_send_poke(user_id, group_id):
        order.append("poke")
        return {"ok": True}

    adapter.send_group_msg = fake_send_group_msg  # type: ignore[method-assign]
    adapter.send_poke = fake_send_poke  # type: ignore[method-assign]
    adapter._engine = SimpleNamespace(
        tick_delayed_queue=AsyncMock(
            return_value=[
                {
                    "reply": "先说正文",
                    "reply_references": [],
                    "sticker_names": ["开心"],
                    "poke_user_ids": ["1001"],
                }
            ]
        ),
        _send_stickers_by_names=fake_send_stickers,
    )
    adapter._get_allowed_group_ids = lambda: ["100"]  # type: ignore[method-assign]

    await adapter._handle_event(
        SessionEvent(
            type=SessionEventType.DELAYED_RESPONSE_TRIGGERED,
            data={"group_id": "100"},
        )
    )

    assert order == ["text", "sticker", "poke"]
