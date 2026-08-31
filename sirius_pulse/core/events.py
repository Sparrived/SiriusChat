"""Session-level event bus for real-time message delivery.

Provides a pub/sub mechanism so external consumers can subscribe to session
events (new messages, TOOL execution status, errors) without being blocked
by the request-response cycle of ``run_live_message``.
"""

from __future__ import annotations

import asyncio
import enum
import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from sirius_pulse.models import Message

logger = logging.getLogger(__name__)


class SessionEventType(enum.Enum):
    """Categories of events emitted during session processing."""

    PERCEPTION_COMPLETED = "perception_completed"
    COGNITION_COMPLETED = "cognition_completed"
    DECISION_COMPLETED = "decision_completed"
    EXECUTION_COMPLETED = "execution_completed"
    AGENT_TURN_UPDATED = "agent_turn_updated"
    DELAYED_RESPONSE_TRIGGERED = "delayed_response_triggered"
    REMINDER_TRIGGERED = "reminder_triggered"
    CUSTOM = "custom"


@dataclass(slots=True)
class SessionEvent:
    """A single event emitted by the engine during session processing.

    Attributes:
        type: The category of the event.
        message: The ``Message`` object, present for message-related events.
        data: Arbitrary metadata (e.g. tool name, error details).
        timestamp: Unix timestamp when the event was created.
    """

    type: SessionEventType
    message: Message | None = None
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


@dataclass(slots=True)
class _EventSubscriber:
    queue: asyncio.Queue[SessionEvent]
    closed: asyncio.Event = field(default_factory=asyncio.Event)


class SessionEventBus:
    """Per-session event bus supporting multiple concurrent subscribers.

    Proactive message producers may attach an in-memory delivery receipt to an
    event.  The bus never serializes or persists that receipt; adapters resolve
    it after routing and attempting the send.

    Usage::

        bus = SessionEventBus()
        # Subscribe
        async for event in bus.subscribe():
            handle(event)

        # Publish (from engine internals)
        await bus.emit(
            SessionEvent(type=SessionEventType.PERCEPTION_COMPLETED, data={"message": msg})
        )

        # Close when the session ends
        await bus.close()
    """

    supports_delivery_ack = True

    def __init__(self) -> None:
        self._subscribers: list[_EventSubscriber] = []
        self._closed = False

    async def emit(self, event: SessionEvent) -> bool:
        """Publish an event and report whether it was accepted by the bus.

        ``True`` means the event bus was open and at least one subscriber (or
        an internal consumer that wraps the bus) accepted the event.  A closed
        or completely unsubscribed bus is not a successful delivery target;
        producers must be able to retain their cursor and retry in that case.
        A full subscriber queue is not counted as an acknowledgement.
        """
        if self._closed:
            return False
        if not self._subscribers:
            return False
        accepted = False
        for subscriber in tuple(self._subscribers):
            if subscriber.closed.is_set():
                continue
            try:
                subscriber.queue.put_nowait(event)
                accepted = True
            except asyncio.QueueFull:
                logger.warning("事件总线订阅者队列已满，丢弃事件: %s", event.type.value)
        return accepted

    async def subscribe(self, *, max_queue_size: int = 256) -> AsyncIterator[SessionEvent]:
        """Return an async iterator that yields events as they arrive.

        The iterator terminates when :meth:`close` is called.  Closing does not
        rely on inserting a sentinel into a bounded queue: a slow consumer may
        drain events already accepted before it exits, while a consumer waiting
        on an empty queue is woken by a per-subscriber close event.
        """
        if isinstance(max_queue_size, bool) or not isinstance(max_queue_size, int):
            raise ValueError("max_queue_size must be a positive integer")
        if max_queue_size <= 0:
            raise ValueError("max_queue_size must be a positive integer")
        if self._closed:
            return

        subscriber = _EventSubscriber(asyncio.Queue(maxsize=max_queue_size))
        self._subscribers.append(subscriber)
        try:
            while True:
                # Drain events which were accepted before close() first.  Once
                # the queue is empty, the close flag is terminal.
                if subscriber.closed.is_set() and subscriber.queue.empty():
                    break
                get_task = asyncio.create_task(subscriber.queue.get())
                close_task = asyncio.create_task(subscriber.closed.wait())
                try:
                    done, _pending = await asyncio.wait(
                        {get_task, close_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if get_task in done:
                        close_task.cancel()
                        await asyncio.gather(close_task, return_exceptions=True)
                        yield get_task.result()
                    else:
                        get_task.cancel()
                        await asyncio.gather(get_task, return_exceptions=True)
                        if subscriber.queue.empty():
                            break
                finally:
                    # If the consumer itself is cancelled while waiting, do not
                    # leak either helper task into the retiring event loop.
                    for task in (get_task, close_task):
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(get_task, close_task, return_exceptions=True)
        finally:
            try:
                self._subscribers.remove(subscriber)
            except ValueError:
                pass  # Already removed by close()

    async def close(self) -> None:
        """Signal all subscribers to stop and clear the subscriber list."""
        self._closed = True
        subscribers = tuple(self._subscribers)
        # Set a separate close event rather than enqueueing a sentinel, because
        # every bounded queue may already be full.  This operation is
        # cancellation-free and leaves no late subscribers behind.
        for subscriber in subscribers:
            subscriber.closed.set()
        self._subscribers.clear()

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    @property
    def closed(self) -> bool:
        return self._closed
