import asyncio
from types import SimpleNamespace

import pytest

from sirius_pulse.core.events import SessionEvent, SessionEventBus, SessionEventType
from sirius_pulse.core.helpers import Helpers
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


def _private_event(user_id: str) -> dict:
    return {
        "post_type": "message",
        "message_type": "private",
        "user_id": user_id,
        "self_id": "100",
        "message_id": f"private-{user_id}",
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


@pytest.mark.asyncio
async def test_private_user_whitelist_applies_to_incoming_events(tmp_path):
    adapter = NapCatAdapter(
        "ws://example.invalid",
        work_path=tmp_path,
        config={"allowed_private_user_ids": ["123"]},
    )

    assert await adapter.parse_event(_private_event("456")) is None
    assert await adapter.parse_event(_private_event("123")) is not None


@pytest.mark.asyncio
async def test_retiring_engine_does_not_process_new_raw_group_events(tmp_path):
    adapter = NapCatAdapter(
        "ws://example.invalid",
        work_path=tmp_path,
        config={"allowed_group_ids": ["100"]},
    )
    processed = []

    async def fake_process_event(event):
        processed.append(event)
        return True

    adapter._engine = SimpleNamespace(_runtime_retiring=True, is_ready=lambda: True)
    adapter._process_event = fake_process_event  # type: ignore[method-assign]

    await adapter._on_event(_group_event("100"))

    assert processed == []
    assert adapter._event_queue.empty()


@pytest.mark.asyncio
async def test_delayed_event_for_another_adapter_does_not_consume_queue(tmp_path):
    adapter = NapCatAdapter(
        "ws://example.invalid",
        work_path=tmp_path,
        config={"allowed_group_ids": ["100"]},
    )
    consumed = []

    async def fake_tick_delayed_queue(group_id, on_partial_reply=None):
        consumed.append(group_id)
        return []

    adapter._engine = SimpleNamespace(tick_delayed_queue=fake_tick_delayed_queue)
    await adapter._handle_event(
        SessionEvent(
            type=SessionEventType.DELAYED_RESPONSE_TRIGGERED,
            data={"group_id": "100", "item_id": "item-1", "adapter_type": "discord"},
        )
    )

    assert consumed == []


@pytest.mark.asyncio
async def test_delayed_event_uses_stable_same_type_adapter_route(tmp_path):
    first = NapCatAdapter(
        "ws://first.example.invalid",
        work_path=tmp_path / "first",
        config={"qq_number": "100", "allowed_group_ids": ["1000"]},
    )
    second = NapCatAdapter(
        "ws://second.example.invalid",
        work_path=tmp_path / "second",
        config={"qq_number": "200", "allowed_group_ids": ["1000"]},
    )
    consumed: list[tuple[str, str | None]] = []

    async def fake_tick_delayed_queue(
        group_id, on_partial_reply=None, *, adapter_type=None, adapter_route_id=None
    ):
        consumed.append((str(group_id), adapter_route_id))
        return []

    engine = SimpleNamespace(tick_delayed_queue=fake_tick_delayed_queue)
    first._engine = engine
    second._engine = engine
    first._get_dispatcher = lambda: None  # type: ignore[method-assign]
    second._get_dispatcher = lambda: None  # type: ignore[method-assign]
    event = SessionEvent(
        type=SessionEventType.DELAYED_RESPONSE_TRIGGERED,
        data={
            "group_id": "1000",
            "item_id": "item-1",
            "adapter_type": "napcat",
            "adapter_route_id": "napcat:200",
        },
    )

    await first._handle_event(event)
    await second._handle_event(event)

    assert consumed == [("1000", "napcat:200")]


def test_delivery_ack_allows_another_same_type_instance_to_succeed(tmp_path):
    first = NapCatAdapter(
        "ws://first.example.invalid",
        work_path=tmp_path / "first",
        config={"qq_number": "100"},
    )
    second = NapCatAdapter(
        "ws://second.example.invalid",
        work_path=tmp_path / "second",
        config={"qq_number": "200"},
    )
    loop = asyncio.new_event_loop()
    try:
        future = loop.create_future()
        event = SessionEvent(
            type=SessionEventType.REMINDER_TRIGGERED,
            data={
                "_delivery_ack": {
                    "expected": ["napcat"],
                    "expected_counts": {"napcat": 2},
                    "results": {},
                    "future": future,
                }
            },
        )

        first._ack_proactive_delivery(event, False)
        assert not future.done()
        second._ack_proactive_delivery(event, True)
        assert future.result() is True
    finally:
        loop.close()


@pytest.mark.asyncio
async def test_delayed_dispatch_admission_error_releases_group_guard(tmp_path):
    adapter = NapCatAdapter(
        "ws://example.invalid",
        work_path=tmp_path,
        config={"allowed_group_ids": ["100"]},
    )
    admissions = []

    def fail_admit(**_kwargs):
        admissions.append(True)
        raise RuntimeError("dispatcher unavailable")

    adapter._engine = SimpleNamespace()
    adapter._get_dispatcher = lambda: SimpleNamespace(
        active_lease=lambda _group_id: "",
        worker_id="worker-1",
        admit=fail_admit,
    )  # type: ignore[method-assign]
    event = SessionEvent(
        type=SessionEventType.DELAYED_RESPONSE_TRIGGERED,
        data={"group_id": "100", "item_id": "item-1", "adapter_type": "napcat"},
    )

    await adapter._handle_event(event)
    await adapter._handle_event(event)

    assert admissions == [True, True]
    assert adapter._dispatch_delivery_active == set()


@pytest.mark.asyncio
async def test_delayed_event_for_retiring_engine_does_not_consume_queue(tmp_path):
    adapter = NapCatAdapter(
        "ws://example.invalid",
        work_path=tmp_path,
        config={"allowed_group_ids": ["100"]},
    )
    consumed = []

    async def fake_tick_delayed_queue(group_id, on_partial_reply=None):
        consumed.append(group_id)
        return []

    adapter._engine = SimpleNamespace(
        _runtime_retiring=True,
        tick_delayed_queue=fake_tick_delayed_queue,
    )
    await adapter._handle_event(
        SessionEvent(
            type=SessionEventType.DELAYED_RESPONSE_TRIGGERED,
            data={"group_id": "100", "item_id": "item-1", "adapter_type": "napcat"},
        )
    )

    assert consumed == []


@pytest.mark.asyncio
async def test_proactive_event_with_blank_adapter_type_uses_group_configuration(tmp_path):
    adapter = NapCatAdapter(
        "ws://example.invalid",
        work_path=tmp_path,
        config={"allowed_group_ids": ["100"]},
    )
    sent: list[tuple[str, str]] = []

    async def fake_send_group_text(group_id, text, reply_refs=None):
        sent.append((str(group_id), str(text)))
        return True

    adapter._send_group_text = fake_send_group_text  # type: ignore[method-assign]
    adapter._engine = SimpleNamespace()

    await adapter._handle_event(
        SessionEvent(
            type=SessionEventType.REMINDER_TRIGGERED,
            data={"group_id": "100", "reply": "按群配置发送", "adapter_type": ""},
        )
    )
    await adapter._handle_event(
        SessionEvent(
            type=SessionEventType.REMINDER_TRIGGERED,
            data={"group_id": "200", "reply": "不应发送", "adapter_type": ""},
        )
    )

    assert sent == [("100", "按群配置发送")]


@pytest.mark.asyncio
async def test_proactive_private_event_uses_private_user_whitelist(tmp_path):
    adapter = NapCatAdapter(
        "ws://example.invalid",
        work_path=tmp_path,
        config={"allowed_private_user_ids": ["123"]},
    )
    sent: list[tuple[str, str]] = []

    async def fake_send_private_text(user_id, text, reply_refs=None):
        sent.append((str(user_id), str(text)))
        return True

    adapter._send_private_text = fake_send_private_text  # type: ignore[method-assign]
    adapter._engine = SimpleNamespace()

    await adapter._handle_event(
        SessionEvent(
            type=SessionEventType.REMINDER_TRIGGERED,
            data={"group_id": "private_123", "reply": "发送", "adapter_type": ""},
        )
    )
    await adapter._handle_event(
        SessionEvent(
            type=SessionEventType.REMINDER_TRIGGERED,
            data={"group_id": "private_456", "reply": "拦截", "adapter_type": ""},
        )
    )

    assert sent == [("123", "发送")]


@pytest.mark.asyncio
async def test_proactive_event_with_resolved_adapter_types_respects_target(tmp_path):
    adapter = NapCatAdapter(
        "ws://example.invalid",
        work_path=tmp_path,
        config={"allowed_group_ids": ["100"]},
    )
    sent: list[str] = []

    async def fake_send_group_text(group_id, text, reply_refs=None):
        sent.append(str(text))
        return True

    adapter._send_group_text = fake_send_group_text  # type: ignore[method-assign]
    adapter._engine = SimpleNamespace()

    await adapter._handle_event(
        SessionEvent(
            type=SessionEventType.REMINDER_TRIGGERED,
            data={
                "group_id": "100",
                "reply": "不匹配",
                "adapter_type": "",
                "adapter_types": ["discord"],
            },
        )
    )
    await adapter._handle_event(
        SessionEvent(
            type=SessionEventType.REMINDER_TRIGGERED,
            data={
                "group_id": "100",
                "reply": "匹配",
                "adapter_type": "",
                "adapter_types": ["napcat", "discord"],
            },
        )
    )

    assert sent == ["匹配"]


async def _wait_for_bus_subscriber(bus: SessionEventBus) -> None:
    for _ in range(20):
        if bus.subscriber_count:
            return
        await asyncio.sleep(0)
    raise AssertionError("event-bus consumer did not subscribe")


@pytest.mark.asyncio
@pytest.mark.parametrize("send_result", [True, False])
async def test_proactive_dispatch_waits_for_actual_napcat_send_result(tmp_path, send_result):
    """A queued event is not an acknowledgement until the adapter sends it."""
    bus = SessionEventBus()
    adapter = NapCatAdapter(
        "ws://example.invalid",
        work_path=tmp_path,
        config={"allowed_group_ids": ["100"]},
    )
    sent: list[tuple[str, str]] = []

    async def fake_send_group_text(group_id, text, reply_refs=None):
        sent.append((str(group_id), str(text)))
        return send_result

    engine = SimpleNamespace(
        _current_adapter_type="",
        _active_private_groups=set(),
        _group_last_message_at={},
        event_bus=bus,
        resolve_adapter_types=lambda _group_id: ["napcat"],
        has_registered_adapters=lambda: True,
    )
    adapter._engine = engine
    adapter._send_group_text = fake_send_group_text  # type: ignore[method-assign]

    async def consume_events() -> None:
        async for event in bus.subscribe():
            await adapter._handle_event(event)

    consumer = asyncio.create_task(consume_events())
    await _wait_for_bus_subscriber(bus)
    try:
        accepted = await Helpers(engine).dispatch_proactive_message(
            group_id="100",
            text="来自插件的通知",
            adapter_type="napcat",
            event_id="sub2api:test",
        )
    finally:
        await bus.close()
        await consumer

    assert accepted is send_result
    assert sent == [("100", "来自插件的通知")]


@pytest.mark.asyncio
async def test_proactive_dispatch_rejects_unsubscribed_delivery_bus():
    bus = SessionEventBus()
    engine = SimpleNamespace(
        _current_adapter_type="",
        _active_private_groups=set(),
        event_bus=bus,
        resolve_adapter_types=lambda _group_id: ["napcat"],
        has_registered_adapters=lambda: True,
    )

    assert (
        await Helpers(engine).dispatch_proactive_message(
            group_id="100", text="不能确认", adapter_type="napcat"
        )
        is False
    )
