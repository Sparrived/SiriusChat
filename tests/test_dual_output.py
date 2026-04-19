"""Tests for dual-output <think> + <say> parsing."""

from __future__ import annotations

import pytest

from sirius_chat.core.response_assembler import ResponseAssembler
from sirius_chat.models.emotion import AssistantEmotionState, EmotionState, EmpathyStrategy
from sirius_chat.models.models import Message
from unittest.mock import Mock


class TestParseDualOutput:
    def test_both_tags_present(self):
        raw = "<think>这用户挺有意思的</think>\n<say>你好呀！</say>"
        think, say = ResponseAssembler.parse_dual_output(raw)
        assert think == "这用户挺有意思的"
        assert say == "你好呀！"

    def test_no_tags_fallback(self):
        raw = "你好呀！"
        think, say = ResponseAssembler.parse_dual_output(raw)
        assert think == ""
        assert say == "你好呀！"

    def test_only_think_tag(self):
        raw = "<think>只是想想</think>"
        think, say = ResponseAssembler.parse_dual_output(raw)
        assert think == "只是想想"
        # When only <think> is present, fallback to think content as say
        assert say == "只是想想"

    def test_multiline_content(self):
        raw = "<think>\n这用户挺有意思的\n让我想想怎么回\n</think>\n<say>\n你好呀！\n很高兴见到你\n</say>"
        think, say = ResponseAssembler.parse_dual_output(raw)
        assert "让我想想怎么回" in think
        assert "很高兴见到你" in say

    def test_whitespace_stripped(self):
        raw = "<think>  想法  </think><say>  回复  </say>"
        think, say = ResponseAssembler.parse_dual_output(raw)
        assert think == "想法"
        assert say == "回复"


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
        assert "<think>" in bundle.system_prompt
        assert "<say>" in bundle.system_prompt

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
        assert "<think>" not in bundle.system_prompt
