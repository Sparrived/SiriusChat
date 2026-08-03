import pytest

from sirius_pulse.platforms.onebot_v11.napcat.adapter import NapCatAdapter


def _group_event(group_id: str) -> dict:
    return {
        "post_type": "message",
        "message_type": "group",
        "group_id": group_id,
        "user_id": "300",
        "self_id": "100",
        "message_id": f"message-{group_id}",
        "message": [{"type": "text", "data": {"text": "你好"}}],
        "sender": {"nickname": "Alice", "card": ""},
    }


@pytest.mark.asyncio
async def test_group_whitelist_blocks_events_before_the_engine(tmp_path):
    adapter = NapCatAdapter(
        "ws://example.invalid",
        work_path=tmp_path,
        config={"allowed_group_ids": ["100"]},
    )

    await adapter._on_event(_group_event("200"))

    assert adapter._event_queue.empty()
    assert await adapter.parse_event(_group_event("200")) is None
    assert await adapter.parse_event(_group_event("100")) is not None


@pytest.mark.asyncio
async def test_group_whitelist_blocks_direct_send_api(tmp_path):
    adapter = NapCatAdapter(
        "ws://example.invalid",
        work_path=tmp_path,
        config={"allowed_group_ids": ["100"]},
    )

    with pytest.raises(PermissionError, match="不在允许列表"):
        await adapter.send_group_msg("200", "不应发送")
