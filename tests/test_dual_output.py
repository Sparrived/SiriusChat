"""Tests for response parsing (dual-output disabled by default)."""

from __future__ import annotations

import pytest

from sirius_chat.core.response_assembler import ResponseAssembler
from sirius_chat.models.emotion import AssistantEmotionState, EmotionState, EmpathyStrategy
from sirius_chat.models.models import Message
from unittest.mock import Mock


class TestParseDualOutput:
    def test_returns_raw_as_say(self):
        raw = "你好呀！"
        think, say = ResponseAssembler.parse_dual_output(raw)
        assert think == ""
        assert say == "你好呀！"

    def test_strips_whitespace(self):
        raw = "  你好呀！  "
        think, say = ResponseAssembler.parse_dual_output(raw)
        assert think == ""
        assert say == "你好呀！"

    def test_ignores_legacy_think_say_tags(self):
        raw = "<think>这用户挺有意思的</think>\n<say>你好呀！</say>"
        think, say = ResponseAssembler.parse_dual_output(raw)
        assert think == ""
        assert say == raw.strip()


class TestAssemblerDualOutputFlag:
    def test_prompt_contains_format_when_enabled(self):
        assembler = ResponseAssembler(enable_dual_output=True)
        bundle = assembler.assemble(
            message=Message(role="human", content="你好"),
            intent=Mock(),
            emotion=EmotionState(),
            empathy_strategy=EmpathyStrategy(strategy_type="presence", priority=3, depth_level=1),
            memories=[],
            group_profile=None,
            user_profile=None,
            assistant_emotion=AssistantEmotionState(),
        )
        assert "输出格式" in bundle.system_prompt
        assert "直接输出" in bundle.system_prompt

    def test_prompt_omits_format_when_disabled(self):
        assembler = ResponseAssembler(enable_dual_output=False)
        bundle = assembler.assemble(
            message=Message(role="human", content="你好"),
            intent=Mock(),
            emotion=EmotionState(),
            empathy_strategy=EmpathyStrategy(strategy_type="presence", priority=3, depth_level=1),
            memories=[],
            group_profile=None,
            user_profile=None,
            assistant_emotion=AssistantEmotionState(),
        )
        assert "输出格式" not in bundle.system_prompt
