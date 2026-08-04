from __future__ import annotations

import asyncio

import pytest

from sirius_pulse.adapters.models import ParsedEvent
from sirius_pulse.platforms.onebot_v11.napcat.adapter import NapCatAdapter


class _Engine:
    def __init__(self) -> None:
        self._bot_platform_uids = {}
        self.processed: list[str] = []
        self.processed_messages = []
        self.processed_participants = []
        self.observed: list[str] = []

    async def process_message(self, *, message, participants, group_id):
        self.processed.append(message.speaker or "")
        self.processed_messages.append(message)
        self.processed_participants.append(participants)
        return {"strategy": "silent", "reply": None}

    def observe_message(self, message, participants, group_id):
        self.observed.append(message.speaker or "")


class _PreviewEngine(_Engine):
    def __init__(self, candidate: dict[str, object]) -> None:
        super().__init__()
        self.candidate = candidate

    def preview_dispatch(self, message, participants, group_id):
        return self.candidate


class _EventBus:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.closed = asyncio.Event()

    async def subscribe(self):
        self.started.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.closed.set()
        if False:
            yield None


class _EventEngine(_Engine):
    def __init__(self) -> None:
        super().__init__()
        self.event_bus = _EventBus()


def _adapter(tmp_path, persona: str, account: str, engine: _Engine) -> NapCatAdapter:
    adapter = NapCatAdapter(
        "ws://example.invalid",
        work_path=tmp_path / persona,
        config={
            "persona_name": persona,
            "qq_number": account,
            "dispatch_db_path": str(tmp_path / "dispatcher.db"),
            "dispatch_min_reply_interval_seconds": 30,
        },
    )
    adapter._engine = engine
    adapter._publish_group_metadata = lambda group_id, self_id: _async_noop()  # type: ignore[method-assign]
    adapter._get_dispatcher()
    return adapter


async def _async_noop():
    return None


@pytest.mark.asyncio
async def test_rebind_engine_switches_event_bus_listener(tmp_path):
    first_engine = _EventEngine()
    second_engine = _EventEngine()
    adapter = _adapter(tmp_path, "alpha", "100", first_engine)
    adapter._running = True
    adapter._event_bus_task = asyncio.create_task(adapter._event_bus_listener())

    await first_engine.event_bus.started.wait()
    await adapter.rebind_engine(second_engine)
    await second_engine.event_bus.started.wait()

    assert adapter._engine is second_engine
    assert first_engine.event_bus.closed.is_set()

    await adapter.stop_handling()


def _set_parsed(adapter: NapCatAdapter, parsed: ParsedEvent) -> None:
    async def fake_parse(event):
        return parsed

    adapter.parse_event = fake_parse  # type: ignore[method-assign]


def _parsed(
    account: str,
    message_id: str,
    *,
    at: tuple[str, ...] = (),
    user_id: str = "300",
    prompt: str = "群里说一句",
    event_time: int = 0,
) -> ParsedEvent:
    return ParsedEvent(
        group_id="g1",
        user_id=user_id,
        self_id=account,
        message_type="group",
        prompt=prompt,
        nickname="Alice",
        message_id=message_id,
        event_time=event_time,
        at_user_ids=list(at),
    )


@pytest.mark.asyncio
async def test_parse_poke_notice_as_model_message(tmp_path):
    adapter = NapCatAdapter(
        "ws://example.invalid",
        work_path=tmp_path,
        config={"persona_name": "alpha", "qq_number": "100", "allowed_group_ids": ["g1"]},
    )
    adapter.set_persona_name("alpha")

    parsed = await adapter.parse_event(
        {
            "time": 1720000000,
            "post_type": "notice",
            "notice_type": "notify",
            "sub_type": "poke",
            "group_id": "g1",
            "user_id": "300",
            "self_id": "100",
            "target_id": "100",
            "sender": {"nickname": "Alice", "card": ""},
        }
    )

    assert parsed is not None
    assert parsed.message_type == "group"
    assert parsed.poke_target_id == "100"
    assert parsed.message_id == "poke-1720000000-300-100"
    assert parsed.event_time == 1720000000
    assert "戳了一下 alpha" in parsed.prompt


@pytest.mark.asyncio
async def test_only_one_persona_processes_a_group_event(tmp_path):
    first_engine = _Engine()
    second_engine = _Engine()
    first = _adapter(tmp_path, "alpha", "100", first_engine)
    second = _adapter(tmp_path, "beta", "200", second_engine)
    _set_parsed(first, _parsed("100", "m1", event_time=1720000000))
    _set_parsed(second, _parsed("200", "m2", event_time=1720000000))

    await asyncio.gather(
        first._process_event_impl({"self_id": "100"}),
        second._process_event_impl({"self_id": "200"}),
    )

    assert first_engine.processed == ["Alice"]
    assert second_engine.processed == []
    assert second_engine.observed == ["Alice"]


def test_dispatch_event_id_aligns_per_account_message_ids_within_time_bucket():
    first = _parsed("100", "m1", event_time=1720000000)
    second = _parsed("200", "m2", event_time=1720000002)
    later = _parsed("200", "m3", event_time=1720000006)

    assert NapCatAdapter._dispatch_event_id(first) == NapCatAdapter._dispatch_event_id(second)
    assert NapCatAdapter._dispatch_event_id(first) != NapCatAdapter._dispatch_event_id(later)


@pytest.mark.asyncio
async def test_targeted_persona_gets_the_next_group_event(tmp_path):
    first_engine = _Engine()
    second_engine = _Engine()
    first = _adapter(tmp_path, "alpha", "100", first_engine)
    second = _adapter(tmp_path, "beta", "200", second_engine)

    _set_parsed(first, _parsed("100", "m1"))
    _set_parsed(second, _parsed("200", "m1"))
    await asyncio.gather(
        first._process_event_impl({"self_id": "100"}),
        second._process_event_impl({"self_id": "200"}),
    )

    _set_parsed(first, _parsed("100", "m2", at=("200",)))
    _set_parsed(second, _parsed("200", "m2", at=("200",)))
    await asyncio.gather(
        first._process_event_impl({"self_id": "100"}),
        second._process_event_impl({"self_id": "200"}),
    )

    assert first_engine.processed == ["Alice"]
    assert second_engine.processed == ["Alice"]


@pytest.mark.asyncio
async def test_text_named_persona_gets_a_soft_dispatch_bonus(tmp_path):
    first_engine = _PreviewEngine(
        {
            "should_reply": True,
            "score": 1.2,
            "reason": "reply_needed",
            "is_mentioned": False,
            "strategy": "delayed",
            "delay_seconds": 12.0,
        }
    )
    second_engine = _PreviewEngine(
        {
            "should_reply": True,
            "score": 1.0,
            "reason": "addressed",
            "is_mentioned": True,
            "strategy": "delayed",
            "delay_seconds": 12.0,
        }
    )
    first = _adapter(tmp_path, "alpha", "100", first_engine)
    second = _adapter(tmp_path, "beta", "200", second_engine)
    _set_parsed(first, _parsed("100", "named-1", prompt="beta，帮我看一下"))
    _set_parsed(second, _parsed("200", "named-1", prompt="beta，帮我看一下"))

    await asyncio.gather(
        first._process_event_impl({"self_id": "100"}),
        second._process_event_impl({"self_id": "200"}),
    )

    assert first_engine.processed == []
    assert first_engine.observed == ["Alice"]
    assert second_engine.processed == ["Alice"]
    assert second_engine.processed_messages[0].dispatch_response_strategy == "delayed"
    assert second_engine.processed_messages[0].dispatch_response_delay_seconds == 12.0


@pytest.mark.asyncio
async def test_registered_peer_account_is_marked_as_other_ai(tmp_path):
    first_engine = _Engine()
    second_engine = _Engine()
    first = _adapter(tmp_path, "alpha", "100", first_engine)
    _adapter(tmp_path, "beta", "200", second_engine)

    _set_parsed(first, _parsed("100", "peer-1", user_id="200", at=("100",)))
    await first._process_event_impl({"self_id": "100"})

    assert first_engine.processed_messages[0].sender_type == "other_ai"
    assert first_engine.processed_participants[0][0].metadata["is_ai"] is True


@pytest.mark.asyncio
async def test_poke_targeted_persona_receives_group_event(tmp_path):
    first_engine = _Engine()
    second_engine = _Engine()
    first = _adapter(tmp_path, "alpha", "100", first_engine)
    second = _adapter(tmp_path, "beta", "200", second_engine)

    _set_parsed(
        first,
        ParsedEvent(
            group_id="g1",
            user_id="300",
            self_id="100",
            message_type="group",
            prompt="戳了一下 beta",
            nickname="Alice",
            message_id="poke-1",
            poke_target_id="200",
        ),
    )
    _set_parsed(
        second,
        ParsedEvent(
            group_id="g1",
            user_id="300",
            self_id="200",
            message_type="group",
            prompt="戳了一下 beta",
            nickname="Alice",
            message_id="poke-1",
            poke_target_id="200",
        ),
    )

    await asyncio.gather(
        first._process_event_impl({"self_id": "100"}),
        second._process_event_impl({"self_id": "200"}),
    )

    assert first_engine.processed == []
    assert second_engine.processed == ["Alice"]
    assert second_engine.processed_messages[0].mentions_current_bot is True
