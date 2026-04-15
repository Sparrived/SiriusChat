"""Tests for the session event bus and real-time event delivery.

Covers:
- SessionEventBus subscribe / emit / close lifecycle
- Event emission during run_live_message (MESSAGE_ADDED, PROCESSING_*)
- Event emission during SKILL execution (SKILL_STARTED, SKILL_COMPLETED)
- SKILL completion events do not expose raw execution result text
- REPLY_SKIPPED event when engagement check decides not to reply
- Multiple concurrent subscribers
- Queue-full behavior
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest

from sirius_chat.api import (
    Agent,
    AgentPreset,
    AsyncRolePlayEngine,
    Message,
    OrchestrationPolicy,
    SessionConfig,
    SessionEvent,
    SessionEventBus,
    SessionEventType,
    asubscribe,
    create_async_engine,
)
from sirius_chat.providers.mock import MockProvider


# ------------------------------------------------------------------ helpers


def _make_config(
    work_path: Path,
    reply_mode: str = "always",
    *,
    enable_skills: bool = False,
) -> SessionConfig:
    return SessionConfig(
        preset=AgentPreset(
            agent=Agent(
                name="TestBot",
                persona="A test assistant.",
                model="mock-model",
            ),
            global_system_prompt="Test system prompt",
        ),
        orchestration=OrchestrationPolicy(
            unified_model="mock-model",
            session_reply_mode=reply_mode,
            enable_skills=enable_skills,
            task_enabled={
                "memory_extract": False,
                "event_extract": False,
                "memory_manager": False,
            },
        pending_message_threshold=0.0,
        ),
        work_path=work_path,
    )


# ----------------------------------------------------- SessionEventBus unit


class TestSessionEventBus:
    @pytest.mark.asyncio
    async def test_emit_and_subscribe(self):
        bus = SessionEventBus()
        received: list[SessionEvent] = []

        async def _reader():
            async for event in bus.subscribe():
                received.append(event)

        reader_task = asyncio.create_task(_reader())
        await asyncio.sleep(0)  # let reader start

        event = SessionEvent(type=SessionEventType.MESSAGE_ADDED, message=Message(role="user", content="hi"))
        await bus.emit(event)
        await asyncio.sleep(0)

        await bus.close()
        await asyncio.sleep(0)
        reader_task.cancel()
        try:
            await reader_task
        except asyncio.CancelledError:
            pass

        assert len(received) == 1
        assert received[0].type == SessionEventType.MESSAGE_ADDED
        assert received[0].message is not None
        assert received[0].message.content == "hi"

    @pytest.mark.asyncio
    async def test_close_terminates_subscribers(self):
        bus = SessionEventBus()
        finished = asyncio.Event()

        async def _reader():
            async for _ in bus.subscribe():
                pass
            finished.set()

        reader_task = asyncio.create_task(_reader())
        await asyncio.sleep(0)

        await bus.close()
        await asyncio.wait_for(finished.wait(), timeout=2.0)
        reader_task.cancel()
        try:
            await reader_task
        except asyncio.CancelledError:
            pass

        assert bus.closed

    @pytest.mark.asyncio
    async def test_multiple_subscribers(self):
        bus = SessionEventBus()
        received_a: list[SessionEvent] = []
        received_b: list[SessionEvent] = []

        async def _reader(target: list):
            async for event in bus.subscribe():
                target.append(event)

        task_a = asyncio.create_task(_reader(received_a))
        task_b = asyncio.create_task(_reader(received_b))
        await asyncio.sleep(0)

        event = SessionEvent(type=SessionEventType.PROCESSING_STARTED)
        await bus.emit(event)
        await asyncio.sleep(0)

        await bus.close()
        await asyncio.sleep(0)
        for t in (task_a, task_b):
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass

        assert len(received_a) == 1
        assert len(received_b) == 1

    @pytest.mark.asyncio
    async def test_emit_after_close_is_noop(self):
        bus = SessionEventBus()
        await bus.close()
        # Should not raise
        await bus.emit(SessionEvent(type=SessionEventType.ERROR))

    @pytest.mark.asyncio
    async def test_queue_full_drops_event(self):
        bus = SessionEventBus()
        received: list[SessionEvent] = []

        async def _reader():
            async for event in bus.subscribe(max_queue_size=1):
                received.append(event)

        task = asyncio.create_task(_reader())
        await asyncio.sleep(0)

        # Emit 3 events quickly without yielding — first lands, others may drop
        for i in range(3):
            await bus.emit(SessionEvent(type=SessionEventType.MESSAGE_ADDED, data={"i": i}))

        await asyncio.sleep(0.05)
        await bus.close()
        await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # At least 1 event received; some may be dropped by the full queue
        assert len(received) >= 1

    def test_subscriber_count(self):
        bus = SessionEventBus()
        assert bus.subscriber_count == 0


# --------------------------------------------- Integration with engine


@pytest.fixture
def work_dir(tmp_path: Path):
    wd = tmp_path / "event_test"
    wd.mkdir()
    yield wd
    shutil.rmtree(wd, ignore_errors=True)


class TestEngineEventIntegration:
    @pytest.mark.asyncio
    async def test_subscribe_emits_message_added(self, work_dir: Path):
        """run_live_message should emit MESSAGE_ADDED for both user turn and assistant reply."""
        provider = MockProvider(responses=["Hello from assistant!"])
        engine = create_async_engine(provider)
        config = _make_config(work_dir)
        transcript = await engine.run_live_session(config=config)

        received: list[SessionEvent] = []

        async def _collect():
            async for event in engine.subscribe(transcript):
                received.append(event)

        collector = asyncio.create_task(_collect())
        await asyncio.sleep(0)  # let subscriber register

        transcript = await engine.run_live_message(
            config=config,
            turn=Message(role="user", speaker="Alice", content="Hi"),
            transcript=transcript,
        )

        # Give events time to propagate
        await asyncio.sleep(0.05)

        # Find the context and close its event bus
        key = id(transcript)
        ctx = engine._live_session_contexts.get(key)
        if ctx:
            await ctx.subsystems.event_bus.close()
        await asyncio.sleep(0)
        collector.cancel()
        try:
            await collector
        except asyncio.CancelledError:
            pass

        type_names = [e.type for e in received]
        # Should have user MESSAGE_ADDED, PROCESSING_STARTED, assistant MESSAGE_ADDED, PROCESSING_COMPLETED
        assert SessionEventType.MESSAGE_ADDED in type_names
        assert SessionEventType.PROCESSING_STARTED in type_names
        assert SessionEventType.PROCESSING_COMPLETED in type_names

        # At least 2 MESSAGE_ADDED (user + assistant)
        message_events = [e for e in received if e.type == SessionEventType.MESSAGE_ADDED]
        assert len(message_events) >= 2
        roles = [e.message.role for e in message_events if e.message]
        assert "user" in roles
        assert "assistant" in roles

    @pytest.mark.asyncio
    async def test_subscribe_before_session_raises(self, work_dir: Path):
        """subscribe() should raise if no live session context exists."""
        provider = MockProvider(responses=["ok"])
        engine = create_async_engine(provider)
        from sirius_chat.models import Transcript
        transcript = Transcript()

        with pytest.raises(ValueError, match="未找到"):
            async for _ in engine.subscribe(transcript):
                break

    @pytest.mark.asyncio
    async def test_reply_skipped_event(self, work_dir: Path):
        """When reply_mode=never, a REPLY_SKIPPED event should be emitted."""
        provider = MockProvider(responses=["won't be used"])
        engine = create_async_engine(provider)
        config = _make_config(work_dir, reply_mode="never")
        transcript = await engine.run_live_session(config=config)

        received: list[SessionEvent] = []

        async def _collect():
            async for event in engine.subscribe(transcript):
                received.append(event)

        collector = asyncio.create_task(_collect())
        await asyncio.sleep(0)

        transcript = await engine.run_live_message(
            config=config,
            turn=Message(role="user", speaker="Bob", content="Ignored"),
            transcript=transcript,
        )
        await asyncio.sleep(0.05)

        key = id(transcript)
        ctx = engine._live_session_contexts.get(key)
        if ctx:
            await ctx.subsystems.event_bus.close()
        await asyncio.sleep(0)
        collector.cancel()
        try:
            await collector
        except asyncio.CancelledError:
            pass

        type_names = [e.type for e in received]
        assert SessionEventType.REPLY_SKIPPED in type_names

    @pytest.mark.asyncio
    async def test_skill_completed_event_does_not_expose_result_preview(self, work_dir: Path):
        """SKILL_COMPLETED should only expose status metadata, not raw result text."""
        skills_dir = work_dir / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)
        (skills_dir / "echo.py").write_text(
            """
SKILL_META = {
    "name": "echo",
    "description": "Return the given text",
    "parameters": {
        "text": {"type": "str", "description": "text", "required": True}
    },
}

def run(text: str, **kwargs):
    return {"echo": text}
""".strip(),
            encoding="utf-8",
        )

        provider = MockProvider(
            responses=[
                '[SKILL_CALL: echo | {"text": "苹果"}]\n\n正在处理...',
                "处理完成。",
            ]
        )
        engine = create_async_engine(provider)
        config = _make_config(work_dir, enable_skills=True)
        transcript = await engine.run_live_session(config=config)

        received: list[SessionEvent] = []

        async def _collect():
            async for event in engine.subscribe(transcript):
                received.append(event)

        collector = asyncio.create_task(_collect())
        await asyncio.sleep(0)

        transcript = await engine.run_live_message(
            config=config,
            turn=Message(role="user", speaker="Alice", content="帮我调用技能"),
            transcript=transcript,
        )
        await asyncio.sleep(0.05)

        key = id(transcript)
        ctx = engine._live_session_contexts.get(key)
        if ctx:
            await ctx.subsystems.event_bus.close()
        await asyncio.sleep(0)
        collector.cancel()
        try:
            await collector
        except asyncio.CancelledError:
            pass

        skill_completed = [e for e in received if e.type == SessionEventType.SKILL_COMPLETED]
        assert len(skill_completed) == 1
        assert skill_completed[0].data == {"skill_name": "echo", "success": True}
        assert "result_preview" not in skill_completed[0].data

    @pytest.mark.asyncio
    async def test_unknown_skill_completed_event_does_not_expose_error_preview(self, work_dir: Path):
        """Unknown SKILL completion should still avoid exposing internal result previews."""
        skills_dir = work_dir / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)

        provider = MockProvider(
            responses=[
                "[SKILL_CALL: missing_skill]",
                "我暂时没有这个技能。",
            ]
        )
        engine = create_async_engine(provider)
        config = _make_config(work_dir, enable_skills=True)
        transcript = await engine.run_live_session(config=config)

        received: list[SessionEvent] = []

        async def _collect():
            async for event in engine.subscribe(transcript):
                received.append(event)

        collector = asyncio.create_task(_collect())
        await asyncio.sleep(0)

        transcript = await engine.run_live_message(
            config=config,
            turn=Message(role="user", speaker="Alice", content="调用一个不存在的技能"),
            transcript=transcript,
        )
        await asyncio.sleep(0.05)

        key = id(transcript)
        ctx = engine._live_session_contexts.get(key)
        if ctx:
            await ctx.subsystems.event_bus.close()
        await asyncio.sleep(0)
        collector.cancel()
        try:
            await collector
        except asyncio.CancelledError:
            pass

        skill_completed = [e for e in received if e.type == SessionEventType.SKILL_COMPLETED]
        assert len(skill_completed) == 1
        assert skill_completed[0].data == {"skill_name": "missing_skill", "success": False}
        assert "result_preview" not in skill_completed[0].data
        assert SessionEventType.SKILL_STARTED not in [e.type for e in received]

    @pytest.mark.asyncio
    async def test_asubscribe_facade(self, work_dir: Path):
        """The asubscribe() facade should work the same as engine.subscribe()."""
        provider = MockProvider(responses=["facade test"])
        engine = create_async_engine(provider)
        config = _make_config(work_dir)
        transcript = await engine.run_live_session(config=config)

        received: list[SessionEvent] = []

        async def _collect():
            async for event in asubscribe(engine, transcript):
                received.append(event)

        collector = asyncio.create_task(_collect())
        await asyncio.sleep(0)

        transcript = await engine.run_live_message(
            config=config,
            turn=Message(role="user", speaker="Charlie", content="Hello"),
            transcript=transcript,
        )
        await asyncio.sleep(0.05)

        key = id(transcript)
        ctx = engine._live_session_contexts.get(key)
        if ctx:
            await ctx.subsystems.event_bus.close()
        await asyncio.sleep(0)
        collector.cancel()
        try:
            await collector
        except asyncio.CancelledError:
            pass

        assert len(received) >= 2  # at least user + assistant MESSAGE_ADDED
