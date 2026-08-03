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
        self.observed: list[str] = []

    async def process_message(self, *, message, participants, group_id):
        self.processed.append(message.speaker or "")
        self.processed_messages.append(message)
        return {"strategy": "silent", "reply": None}

    def observe_message(self, message, participants, group_id):
        self.observed.append(message.speaker or "")


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


def _set_parsed(adapter: NapCatAdapter, parsed: ParsedEvent) -> None:
    async def fake_parse(event):
        return parsed

    adapter.parse_event = fake_parse  # type: ignore[method-assign]


def _parsed(account: str, message_id: str, *, at: tuple[str, ...] = ()) -> ParsedEvent:
    return ParsedEvent(
        group_id="g1",
        user_id="300",
        self_id=account,
        message_type="group",
        prompt="群里说一句",
        nickname="Alice",
        message_id=message_id,
        at_user_ids=list(at),
    )


@pytest.mark.asyncio
async def test_parse_poke_notice_as_model_message(tmp_path):
    adapter = NapCatAdapter(
        "ws://example.invalid",
        work_path=tmp_path,
        config={"persona_name": "alpha", "qq_number": "100"},
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
    assert "戳了一下 alpha" in parsed.prompt


@pytest.mark.asyncio
async def test_only_one_persona_processes_a_group_event(tmp_path):
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

    assert first_engine.processed == ["Alice"]
    assert second_engine.processed == []
    assert second_engine.observed == ["Alice"]


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
