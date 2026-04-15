"""
意图分析与记忆归纳测试

覆盖范围：
- IntentAnalyzer.fallback_analysis: 关键词匹配、意图分类、target 判定
- IntentAnalyzer._parse_response: LLM JSON 解析、错误容忍
- EventMemoryManager.consolidate_entries: 归纳合并、阈值跳过
- UserMemoryManager.consolidate_summary_notes: 摘要归纳
- UserMemoryManager.consolidate_memory_facts: 事实归纳
- BackgroundTaskManager: 归纳循环生命周期
- build_system_prompt skip_sections: 段落跳过
"""

import asyncio
import json
import pytest
from datetime import datetime
from pathlib import Path

from sirius_chat.core.intent_v2 import IntentAnalysis, IntentAnalyzer, INTENT_TYPES
from sirius_chat.memory.event.manager import EventMemoryManager
from sirius_chat.memory.event.models import EventMemoryEntry
from sirius_chat.memory.user.manager import UserMemoryManager
from sirius_chat.memory.user.models import MemoryFact, UserProfile
from sirius_chat.background_tasks import BackgroundTaskConfig, BackgroundTaskManager
from sirius_chat.async_engine.prompts import build_system_prompt
from sirius_chat.config import OrchestrationPolicy
from sirius_chat.config.models import Agent, AgentPreset, SessionConfig
from sirius_chat.models.models import Transcript
from sirius_chat.providers.mock import MockProvider
from sirius_chat.providers.base import GenerationRequest


class AsyncMockProvider:
    """MockProvider wrapper that exposes generate_async for consolidation tests."""

    def __init__(self, responses: list[str]):
        self._queue = list(responses)
        self._index = 0

    async def generate_async(self, request: GenerationRequest) -> str:
        if self._index < len(self._queue):
            resp = self._queue[self._index]
            self._index += 1
            return resp
        return ""


# ── IntentAnalyzer.fallback_analysis tests ──────────────────────────


class TestFallbackAnalysis:
    """IntentAnalyzer.fallback_analysis 关键词匹配逻辑."""

    @pytest.mark.parametrize("content,expected_type,is_actionable", [
        ("今天天气怎么样？", "question", True),
        ("What's going on?", "question", True),
        ("请帮我查一下资料", "request", True),
        ("帮忙看看这个", "request", True),
        ("can you do it please", "request", True),
        ("好", "reaction", False),
        ("嗯", "reaction", False),
        ("ok", "reaction", False),
        ("今天在公司遇到了很多事情呢", "chat", False),
    ])
    def test_intent_type_classification(self, content: str, expected_type: str, is_actionable: bool):
        result = IntentAnalyzer.fallback_analysis(content, "助手", "")
        assert result.intent_type == expected_type
        # Actionable intents (question/request) should have importance > 0
        if is_actionable:
            assert result.importance > 0
        # Reaction type should not drive engagement
        if expected_type == "reaction":
            assert result.importance <= 0.5

    def test_directed_at_ai_by_name(self):
        result = IntentAnalyzer.fallback_analysis("小助手你好", "小助手", "")
        assert result.directed_at_ai is True
        assert result.target == "ai"

    def test_directed_at_ai_by_alias(self):
        result = IntentAnalyzer.fallback_analysis("阿助你好", "助手", "阿助")
        assert result.directed_at_ai is True
        assert result.target == "ai"

    def test_pronoun_maps_to_unknown_not_ai(self):
        """核心修复：裸代词「你」不再直接指向 AI，而是 unknown。"""
        result = IntentAnalyzer.fallback_analysis("你觉得呢", "助手", "")
        assert result.target == "unknown"
        assert result.directed_at_ai is False

    def test_target_others_when_mentioning_participant(self):
        """提及其他参与者时 target 应为 others。"""
        result = IntentAnalyzer.fallback_analysis("小王你觉得呢", "助手", "", ["小王", "小李"])
        assert result.target == "others"
        assert result.directed_at_ai is False

    def test_directed_at_ai_engagement_higher_than_ambient(self):
        """直接提及 AI 的消息 importance 应高于普通消息。"""
        directed = IntentAnalyzer.fallback_analysis("助手你好吗？", "助手", "")
        not_directed = IntentAnalyzer.fallback_analysis("明天有空吗？", "NoMatch", "")
        assert directed.directed_at_ai is True
        assert not_directed.directed_at_ai is False

    def test_fallback_has_reason_and_evidence(self):
        result = IntentAnalyzer.fallback_analysis("请帮我看看这个", "助手", "")
        assert result.reason
        assert result.evidence_span


# ── IntentAnalyzer._parse_response tests ────────────────────────────


class TestParseResponse:
    """IntentAnalyzer._parse_response JSON 解析."""

    def test_valid_json(self):
        raw = json.dumps({
            "intent_type": "question",
            "target": "ai",
            "importance": 0.8,
            "needs_memory": False,
            "needs_summary": True,
            "reason": "用户在询问天气",
            "evidence_span": "天气怎么样",
        })
        result = IntentAnalyzer._parse_response(raw)
        assert result is not None
        assert result.intent_type == "question"
        assert result.directed_at_ai is True
        assert result.target == "ai"
        assert "participant_memory" in result.skip_sections
        assert "session_summary" not in result.skip_sections
        assert result.reason == "用户在询问天气"
        assert result.evidence_span == "天气怎么样"

    def test_markdown_fenced_json(self):
        raw = '```json\n{"intent_type":"request","target":"ai","importance":0.6,"needs_memory":true,"needs_summary":false}\n```'
        result = IntentAnalyzer._parse_response(raw)
        assert result is not None
        assert result.intent_type == "request"
        assert "session_summary" in result.skip_sections

    def test_invalid_json_returns_none(self):
        result = IntentAnalyzer._parse_response("not valid json at all")
        assert result is None

    def test_invalid_json_logs_warning(self, caplog):
        caplog.set_level("WARNING")
        _ = IntentAnalyzer._parse_response("not valid json at all")
        assert any("意图分析响应解析失败" in rec.message for rec in caplog.records)

    def test_reason_and_evidence_span_are_truncated(self):
        raw = json.dumps({
            "intent_type": "request",
            "directed_at_ai": True,
            "importance": 0.9,
            "needs_memory": True,
            "needs_summary": True,
            "reason": "r" * 300,
            "evidence_span": "e" * 200,
        })
        result = IntentAnalyzer._parse_response(raw)
        assert result is not None
        assert len(result.reason) == 200
        assert len(result.evidence_span) == 120

    def test_unknown_intent_type_defaults_to_chat(self):
        raw = json.dumps({"intent_type": "unknown_type", "target": "ai", "importance": 0.5})
        result = IntentAnalyzer._parse_response(raw)
        assert result is not None
        assert result.intent_type == "chat"

    def test_importance_clamped(self):
        raw = json.dumps({"intent_type": "question", "target": "ai", "importance": 5.0})
        result = IntentAnalyzer._parse_response(raw)
        assert result is not None
        assert result.confidence <= 1.0

    def test_reaction_low_importance(self):
        raw = json.dumps({
            "intent_type": "reaction",
            "target": "ai",
            "importance": 0.2,
            "needs_memory": True,
            "needs_summary": True,
        })
        result = IntentAnalyzer._parse_response(raw)
        assert result is not None
        assert result.importance <= 0.3

    def test_not_directed_at_ai_target(self):
        raw = json.dumps({
            "intent_type": "question",
            "target": "others",
            "importance": 0.5,
        })
        result = IntentAnalyzer._parse_response(raw)
        assert result is not None
        assert result.directed_at_ai is False
        assert result.target == "others"
        directed_raw = json.dumps({
            "intent_type": "question",
            "target": "ai",
            "importance": 0.5,
        })
        directed_result = IntentAnalyzer._parse_response(directed_raw)
        assert directed_result is not None
        assert directed_result.directed_at_ai is True
        assert directed_result.target == "ai"


# ── IntentAnalyzer.analyze LLM integration ──────────────────────────


class TestAnalyzeLLM:
    """IntentAnalyzer.analyze 通过 MockProvider 测试."""

    def test_analyze_returns_parsed_result(self):
        async def _run():
            response = json.dumps({
                "intent_type": "question",
                "target": "ai",
                "importance": 0.7,
                "needs_memory": True,
                "needs_summary": False,
            })
            provider = AsyncMockProvider(responses=[response])

            async def call_provider(req):
                return await provider.generate_async(req)

            result = await IntentAnalyzer.analyze(
                content="你知道明天什么天气吗？",
                agent_name="助手",
                agent_alias="",
                participant_names=["小王"],
                recent_messages=[{"role": "user", "content": "hello"}],
                call_provider=call_provider,
                model="mock-model",
            )
            assert result.intent_type == "question"
            assert result.directed_at_ai is True
            assert result.target == "ai"
            assert "session_summary" in result.skip_sections

        asyncio.run(_run())

    def test_analyze_fallback_on_error(self):
        async def _run():
            async def failing_provider(req):
                raise RuntimeError("provider exploded")

            result = await IntentAnalyzer.analyze(
                content="请帮忙看一下",
                agent_name="助手",
                agent_alias="",
                participant_names=[],
                recent_messages=[],
                call_provider=failing_provider,
                model="mock-model",
            )
            # Should fallback gracefully
            assert result.intent_type in INTENT_TYPES

        asyncio.run(_run())


# ── EventMemoryManager.consolidate_entries tests ────────────────────


class TestEventConsolidation:
    """EventMemoryManager.consolidate_entries 事件归纳."""

    def _make_entries(self, user_id: str, category: str, count: int) -> list[EventMemoryEntry]:
        return [
            EventMemoryEntry(
                event_id=f"evt-{user_id}-{category}-{i}",
                user_id=user_id,
                category=category,
                summary=f"观察 {i}: 用户表现出{category}行为",
                confidence=0.7,
                evidence_samples=[],
                created_at=datetime.now().isoformat(),
                mention_count=1,
            )
            for i in range(count)
        ]

    def test_skip_when_below_min_entries(self):
        async def _run():
            mgr = EventMemoryManager()
            mgr.entries = self._make_entries("u1", "personality", 3)
            provider = AsyncMockProvider(responses=["should not be called"])
            result = await mgr.consolidate_entries(
                user_id="u1", provider_async=provider, model_name="mock", min_entries=6
            )
            assert result == 0
            assert len(mgr.entries) == 3

        asyncio.run(_run())

    def test_consolidates_large_category(self):
        async def _run():
            mgr = EventMemoryManager()
            mgr.entries = self._make_entries("u1", "personality", 8)
            consolidated_response = json.dumps([
                {"summary": "归纳1: 性格特征", "confidence": 0.9, "mention_count": 5},
                {"summary": "归纳2: 行为模式", "confidence": 0.8, "mention_count": 3},
            ])
            provider = AsyncMockProvider(responses=[consolidated_response])
            result = await mgr.consolidate_entries(
                user_id="u1", provider_async=provider, model_name="mock", min_entries=6
            )
            assert result == 6  # 8 - 2
            assert len(mgr.entries) == 2
            assert mgr.entries[0].verified is True

        asyncio.run(_run())

    def test_leaves_other_users_untouched(self):
        async def _run():
            mgr = EventMemoryManager()
            mgr.entries = self._make_entries("u1", "habit", 6) + self._make_entries("u2", "habit", 2)
            response = json.dumps([
                {"summary": "归纳习惯", "confidence": 0.9, "mention_count": 6}
            ])
            provider = AsyncMockProvider(responses=[response])
            await mgr.consolidate_entries(
                user_id="u1", provider_async=provider, model_name="mock", min_entries=6
            )
            u2_entries = [e for e in mgr.entries if e.user_id == "u2"]
            assert len(u2_entries) == 2

        asyncio.run(_run())

    def test_get_all_user_ids(self):
        mgr = EventMemoryManager()
        mgr.entries = self._make_entries("u1", "habit", 2) + self._make_entries("u2", "mood", 1)
        assert mgr.get_all_user_ids() == {"u1", "u2"}


# ── UserMemoryManager consolidation tests ──────────────────────────


class TestUserMemoryConsolidation:
    """UserMemoryManager 摘要与事实归纳."""

    def _make_manager_with_notes(self, user_id: str, notes: list[str]) -> UserMemoryManager:
        mgr = UserMemoryManager()
        mgr.register_user(UserProfile(user_id=user_id, name=user_id))
        entry = mgr.entries[user_id]
        entry.runtime.summary_notes = list(notes)
        return mgr

    def _make_manager_with_facts(self, user_id: str, count: int) -> UserMemoryManager:
        mgr = UserMemoryManager()
        mgr.register_user(UserProfile(user_id=user_id, name=user_id))
        entry = mgr.entries[user_id]
        entry.runtime.memory_facts = [
            MemoryFact(
                fact_type="preference",
                value=f"喜欢事物{i}",
                source="chat",
                confidence=0.6 + i * 0.02,
                mention_count=i + 1,
            )
            for i in range(count)
        ]
        return mgr

    def test_summary_notes_skip_below_threshold(self):
        async def _run():
            mgr = self._make_manager_with_notes("u1", ["note1", "note2"])
            provider = AsyncMockProvider(responses=["not called"])
            result = await mgr.consolidate_summary_notes(
                "u1", provider, "mock", min_notes=4
            )
            assert result == 0

        asyncio.run(_run())

    def test_summary_notes_consolidation(self):
        async def _run():
            notes = [f"摘要{i}" for i in range(6)]
            mgr = self._make_manager_with_notes("u1", notes)
            response = json.dumps(["综合摘要A", "综合摘要B"])
            provider = AsyncMockProvider(responses=[response])
            result = await mgr.consolidate_summary_notes(
                "u1", provider, "mock", min_notes=4
            )
            assert result == 4  # 6 - 2
            assert len(mgr.entries["u1"].runtime.summary_notes) == 2

        asyncio.run(_run())

    def test_memory_facts_skip_below_threshold(self):
        async def _run():
            mgr = self._make_manager_with_facts("u1", 5)
            provider = AsyncMockProvider(responses=["not called"])
            result = await mgr.consolidate_memory_facts(
                "u1", provider, "mock", min_facts=15
            )
            assert result == 0

        asyncio.run(_run())

    def test_memory_facts_consolidation(self):
        async def _run():
            mgr = self._make_manager_with_facts("u1", 16)
            response = json.dumps([
                {"fact_type": "preference", "value": "归纳偏好A", "confidence": 0.9,
                 "category": "custom", "mention_count": 20},
                {"fact_type": "preference", "value": "归纳偏好B", "confidence": 0.8,
                 "category": "custom", "mention_count": 15},
            ])
            provider = AsyncMockProvider(responses=[response])
            result = await mgr.consolidate_memory_facts(
                "u1", provider, "mock", min_facts=15
            )
            assert result == 14  # 16 - 2
            facts = mgr.entries["u1"].runtime.memory_facts
            assert len(facts) == 2
            assert facts[0].source == "consolidation"
            assert facts[0].validated is True

        asyncio.run(_run())

    def test_memory_facts_llm_failure_returns_zero(self):
        async def _run():
            mgr = self._make_manager_with_facts("u1", 16)

            class FailProvider:
                async def generate_async(self, req):
                    raise RuntimeError("boom")

            result = await mgr.consolidate_memory_facts(
                "u1", FailProvider(), "mock", min_facts=15
            )
            assert result == 0
            # Original facts should be preserved
            assert len(mgr.entries["u1"].runtime.memory_facts) == 16

        asyncio.run(_run())


# ── BackgroundTaskManager lifecycle tests ───────────────────────────


class TestBackgroundTaskManager:
    """BackgroundTaskManager 归纳循环生命周期."""

    def test_config_defaults(self):
        cfg = BackgroundTaskConfig()
        assert cfg.consolidation_interval_seconds == 900

    def test_start_and_stop(self):
        async def _run():
            call_count = 0
            async def callback():
                nonlocal call_count
                call_count += 1

            cfg = BackgroundTaskConfig(
                compression_enabled=False,
                cleanup_enabled=False,
                consolidation_interval_seconds=1,
            )
            mgr = BackgroundTaskManager(config=cfg)
            mgr.set_consolidation_callback(callback)
            await mgr.start()
            assert mgr.is_running() is True

            # Allow background loop to make at least one call
            await asyncio.sleep(1.5)
            await mgr.stop()
            assert mgr.is_running() is False

        asyncio.run(_run())

    def test_trigger_now(self):
        async def _run():
            triggered = False
            async def callback():
                nonlocal triggered
                triggered = True

            cfg = BackgroundTaskConfig(
                compression_enabled=False,
                cleanup_enabled=False,
                consolidation_interval_seconds=3600,  # long interval
            )
            mgr = BackgroundTaskManager(config=cfg)
            mgr.set_consolidation_callback(callback)
            await mgr.start()
            await mgr.trigger_consolidation_now()
            await mgr.stop()
            assert triggered is True

        asyncio.run(_run())


# ── build_system_prompt skip_sections tests ─────────────────────────


class TestSkipSections:
    """build_system_prompt 的 skip_sections 段落跳过."""

    @staticmethod
    def _build(
        participants_info: str = "",
        session_summary: str = "",
        skip_sections: list[str] | None = None,
    ) -> str:
        config = SessionConfig(
            work_path=Path("data/tests/skip_sections"),
            preset=AgentPreset(
                agent=Agent(name="助手", persona="测试人设", model="mock-model"),
                global_system_prompt="系统提示",
            ),
        )
        transcript = Transcript(session_summary=session_summary)
        # If participants_info is provided, set up user memory
        if participants_info:
            uid = "test_uid"
            transcript.user_memory.register_user(UserProfile(user_id=uid, name=uid))
            entry = transcript.user_memory.entries[uid]
            entry.runtime.summary_notes = [participants_info]
        return build_system_prompt(config=config, transcript=transcript, skip_sections=skip_sections)

    def test_skip_participant_memory(self):
        prompt = self._build(
            participants_info="用户A的记忆信息",
            session_summary="会话摘要",
            skip_sections=["participant_memory"],
        )
        assert "用户A的记忆信息" not in prompt
        assert "会话摘要" in prompt

    def test_skip_session_summary(self):
        prompt = self._build(
            participants_info="用户A的记忆",
            session_summary="会话摘要内容",
            skip_sections=["session_summary"],
        )
        assert "会话摘要内容" not in prompt
        # participant memory should still be present
        assert "participant_memory" not in (["session_summary"])

    def test_skip_multiple_sections(self):
        prompt = self._build(
            participants_info="记忆段落",
            session_summary="摘要段落",
            skip_sections=["participant_memory", "session_summary"],
        )
        assert "记忆段落" not in prompt
        assert "摘要段落" not in prompt

    def test_no_skip_includes_all(self):
        prompt = self._build(
            session_summary="摘要信息",
        )
        assert "摘要信息" in prompt


# ── parse helper tests ──────────────────────────────────────────────


class TestParseHelpers:
    """UserMemoryManager 解析辅助方法."""

    def test_parse_string_array_valid(self):
        raw = '["a", "b", "c"]'
        result = UserMemoryManager._parse_string_array(raw)
        assert result == ["a", "b", "c"]

    def test_parse_string_array_fenced(self):
        raw = '```json\n["x", "y"]\n```'
        result = UserMemoryManager._parse_string_array(raw)
        assert result == ["x", "y"]

    def test_parse_string_array_invalid(self):
        result = UserMemoryManager._parse_string_array("not json")
        assert result == []

    def test_parse_dict_array_valid(self):
        raw = json.dumps([{"key": "val"}])
        result = UserMemoryManager._parse_dict_array(raw)
        assert len(result) == 1
        assert result[0]["key"] == "val"

    def test_parse_dict_array_invalid(self):
        result = UserMemoryManager._parse_dict_array("{broken")
        assert result == []
