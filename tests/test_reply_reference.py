from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from sirius_pulse.core.engine_core import _EmotionalGroupChatEngineBase
from sirius_pulse.memory.basic import BasicMemoryManager


class RecordingBrain:
    def __init__(self) -> None:
        self.post_hooks: list[tuple[Any, int, set[str] | None]] = []

    def register_post_hook(
        self,
        hook: Any,
        priority: int = 100,
        task_filter: set[str] | None = None,
    ) -> None:
        self.post_hooks.append((hook, priority, task_filter))


def _reply_reference_hook() -> tuple[Any, Any]:
    engine = SimpleNamespace(
        brain=RecordingBrain(),
        basic_memory=BasicMemoryManager(),
    )
    _EmotionalGroupChatEngineBase._register_engine_hooks(engine)
    for hook, priority, _task_filter in engine.brain.post_hooks:
        if priority == 10:
            return hook, engine
    raise AssertionError("reply reference hook was not registered")


def _interaction_marker_hook() -> tuple[Any, Any]:
    engine = SimpleNamespace(
        brain=RecordingBrain(),
        basic_memory=BasicMemoryManager(),
        _sticker_names=["开心", "疑惑"],
        get_qq_group_members_for_prompt=lambda group_id: [{"user_id": "1001"}],
    )
    _EmotionalGroupChatEngineBase._register_engine_hooks(engine)
    for hook, priority, _task_filter in engine.brain.post_hooks:
        if priority == 20:
            return hook, engine
    raise AssertionError("interaction marker hook was not registered")


@pytest.mark.parametrize(
    "marker",
    [
        "[REPLY:1]",
        '[REPLY:msg_id="abc-123"]',
    ],
)
def test_reply_reference_when_marker_is_present_then_extracts_ref_and_strips_marker(
    marker: str,
) -> None:
    hook, engine = _reply_reference_hook()
    engine.basic_memory.add_entry(
        "group_a",
        "u1",
        "human",
        "older message",
        speaker_name="Alice",
        platform_message_id="old-456",
    )
    engine.basic_memory.add_entry(
        "group_a",
        "u2",
        "human",
        "target message",
        speaker_name="Bob",
        platform_message_id="abc-123",
    )
    req = SimpleNamespace(group_id="group_a", user_id="caller")
    result = SimpleNamespace(
        raw_text=f"{marker} reply body",
        clean_text=f"{marker} reply body",
        reply_references=[],
    )

    hook(None, req, result, {})

    assert result.reply_references == [
        {
            "msg_id": "abc-123",
            "speaker": "Bob",
            "content": "target message",
        }
    ]
    assert "[REPLY:" not in result.raw_text
    assert "[REPLY:" not in result.clean_text
    assert result.clean_text.strip() == "reply body"


def test_interaction_markers_when_valid_then_extract_actions_and_strip_controls() -> None:
    hook, _engine = _interaction_marker_hook()
    req = SimpleNamespace(group_id="group_a", user_id="caller")
    result = SimpleNamespace(
        raw_text="[POKE:1001] [STICKER:开心] 正文",
        clean_text="[POKE:1001] [STICKER:开心] 正文",
        sticker_names=[],
        poke_user_ids=[],
    )

    hook(None, req, result, {})

    assert result.clean_text == "正文"
    assert result.raw_text == "正文"
    assert result.sticker_names == ["开心"]
    assert result.poke_user_ids == ["1001"]


def test_interaction_markers_when_unknown_values_then_strip_without_sending() -> None:
    hook, _engine = _interaction_marker_hook()
    req = SimpleNamespace(group_id="group_a", user_id="caller")
    result = SimpleNamespace(
        raw_text="[POKE:9999][STICKER:不存在] 正文",
        clean_text="[POKE:9999][STICKER:不存在] 正文",
        sticker_names=[],
        poke_user_ids=[],
    )

    hook(None, req, result, {})

    assert result.clean_text == "正文"
    assert result.sticker_names == []
    assert result.poke_user_ids == []
