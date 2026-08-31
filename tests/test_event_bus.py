"""会话事件总线的业务行为测试。"""

from __future__ import annotations

import asyncio

import pytest

from sirius_pulse.core.events import SessionEvent, SessionEventBus, SessionEventType


async def _wait_for_subscriber(bus: SessionEventBus, expected: int = 1) -> None:
    for _ in range(20):
        if bus.subscriber_count >= expected:
            return
        await asyncio.sleep(0)
    raise AssertionError("subscriber did not attach in time")


@pytest.mark.asyncio
async def test_event_bus_when_monitor_subscribes_then_it_receives_engine_events():
    bus = SessionEventBus()
    events: list[SessionEvent] = []

    async def monitor() -> None:
        async for event in bus.subscribe():
            events.append(event)

    task = asyncio.create_task(monitor())
    await _wait_for_subscriber(bus)

    await bus.emit(
        SessionEvent(type=SessionEventType.PERCEPTION_COMPLETED, data={"group_id": "group_a"})
    )
    await bus.close()
    await task

    assert [event.type for event in events] == [SessionEventType.PERCEPTION_COMPLETED]
    assert events[0].data["group_id"] == "group_a"
    assert events[0].timestamp > 0


@pytest.mark.asyncio
async def test_event_bus_when_two_clients_watch_session_then_both_see_same_decision():
    bus = SessionEventBus()
    received: list[list[SessionEventType]] = [[], []]

    async def monitor(index: int) -> None:
        async for event in bus.subscribe():
            received[index].append(event.type)

    task_a = asyncio.create_task(monitor(0))
    task_b = asyncio.create_task(monitor(1))
    await _wait_for_subscriber(bus, expected=2)

    await bus.emit(SessionEvent(type=SessionEventType.DECISION_COMPLETED))
    await bus.close()
    await asyncio.gather(task_a, task_b)

    assert received == [
        [SessionEventType.DECISION_COMPLETED],
        [SessionEventType.DECISION_COMPLETED],
    ]


@pytest.mark.asyncio
async def test_event_bus_when_session_closes_then_subscribers_finish_cleanly():
    bus = SessionEventBus()
    finished = False

    async def monitor() -> None:
        nonlocal finished
        async for _event in bus.subscribe():
            pass
        finished = True

    task = asyncio.create_task(monitor())
    await _wait_for_subscriber(bus)

    await bus.close()
    await task

    assert finished is True
    assert bus.closed is True
    assert bus.subscriber_count == 0


@pytest.mark.asyncio
async def test_event_bus_when_closed_then_late_engine_events_are_ignored():
    bus = SessionEventBus()
    await bus.close()

    await bus.emit(SessionEvent(type=SessionEventType.EXECUTION_COMPLETED))

    assert bus.closed is True
    assert bus.subscriber_count == 0


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_event_bus_when_queue_is_full_then_close_still_finishes_consumer():
    bus = SessionEventBus()
    received: list[int] = []
    first_received = asyncio.Event()
    allow_finish = asyncio.Event()

    async def slow_monitor() -> None:
        async for event in bus.subscribe(max_queue_size=1):
            received.append(int(event.data["index"]))
            first_received.set()
            await allow_finish.wait()

    task = asyncio.create_task(slow_monitor())
    await _wait_for_subscriber(bus)
    assert await bus.emit(SessionEvent(type=SessionEventType.CUSTOM, data={"index": 1}))
    await first_received.wait()
    assert await bus.emit(SessionEvent(type=SessionEventType.CUSTOM, data={"index": 2}))

    await bus.close()
    allow_finish.set()
    await asyncio.wait_for(task, timeout=1.0)
    assert received == [1, 2]


@pytest.mark.asyncio
async def test_event_bus_when_closed_before_first_iteration_then_subscription_finishes():
    bus = SessionEventBus()
    iterator = bus.subscribe()
    await bus.close()

    with pytest.raises(StopAsyncIteration):
        await iterator.__anext__()


@pytest.mark.asyncio
async def test_event_bus_rejects_nonpositive_queue_capacity():
    bus = SessionEventBus()

    with pytest.raises(ValueError, match="max_queue_size"):
        await bus.subscribe(max_queue_size=0).__anext__()


@pytest.mark.asyncio
async def test_event_bus_when_monitor_is_slow_then_session_keeps_running():
    bus = SessionEventBus()
    first_event_received = asyncio.Event()

    async def slow_monitor() -> None:
        async for event in bus.subscribe(max_queue_size=1):
            first_event_received.set()
            if event.data.get("index") == 0:
                await asyncio.Event().wait()

    task = asyncio.create_task(slow_monitor())
    await _wait_for_subscriber(bus)

    for index in range(5):
        await bus.emit(SessionEvent(type=SessionEventType.CUSTOM, data={"index": index}))

    await asyncio.wait_for(first_event_received.wait(), timeout=1.0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert bus.closed is False
