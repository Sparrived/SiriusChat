"""Tests for diary memory generator, indexer, and manager."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from sirius_chat.memory.diary import (
    DiaryEntry,
    DiaryGenerator,
    DiaryIndexer,
    DiaryRetriever,
    DiaryManager,
)
from sirius_chat.memory.basic import BasicMemoryEntry


class TestDiaryGenerator:
    @pytest.mark.asyncio
    async def test_generate_success(self) -> None:
        gen = DiaryGenerator()
        mock_provider = AsyncMock()
        mock_provider.generate_async.return_value = (
            '{"content": "大家聊了很多有趣的话题", '
            '"keywords": ["闲聊", "兴趣"], '
            '"summary": "群聊日常"}'
        )
        candidates = [
            BasicMemoryEntry("b1", "g1", "alice", "human", "你好", "2026-04-22T10:00:00+00:00"),
            BasicMemoryEntry("b2", "g1", "bob", "human", "你好呀", "2026-04-22T10:01:00+00:00"),
        ]
        entry = await gen.generate(
            group_id="g1",
            candidates=candidates,
            persona_name="小星",
            persona_description="一个温柔的AI助手",
            provider_async=mock_provider,
            model_name="gpt-4o-mini",
        )
        assert entry is not None
        assert entry.group_id == "g1"
        assert "大家聊了很多有趣的话题" in entry.content
        assert entry.source_ids == ["b1", "b2"]
        assert len(entry.keywords) == 2

    @pytest.mark.asyncio
    async def test_generate_empty_candidates(self) -> None:
        gen = DiaryGenerator()
        entry = await gen.generate(
            group_id="g1",
            candidates=[],
            persona_name="小星",
            persona_description="",
            provider_async=AsyncMock(),
            model_name="gpt-4o-mini",
        )
        assert entry is None

    @pytest.mark.asyncio
    async def test_generate_llm_failure(self) -> None:
        gen = DiaryGenerator()
        mock_provider = AsyncMock()
        mock_provider.generate_async.side_effect = RuntimeError("timeout")
        candidates = [
            BasicMemoryEntry("b1", "g1", "alice", "human", "hello", "2026-04-22T10:00:00+00:00"),
        ]
        entry = await gen.generate(
            group_id="g1",
            candidates=candidates,
            persona_name="小星",
            persona_description="",
            provider_async=mock_provider,
            model_name="gpt-4o-mini",
        )
        assert entry is None


class TestDiaryIndexer:
    def test_keyword_search(self) -> None:
        idx = DiaryIndexer()
        idx.add(DiaryEntry("d1", "g1", "2026-04-22T10:00:00+00:00", content="今天讨论了Python"))
        idx.add(DiaryEntry("d2", "g1", "2026-04-22T10:00:00+00:00", content="天气很好"))
        results = idx.search("Python", top_k=5)
        assert len(results) == 1
        assert results[0][0].entry_id == "d1"
        assert results[0][1] > 0

    def test_keyword_search_with_keywords_field(self) -> None:
        idx = DiaryIndexer()
        idx.add(DiaryEntry(
            "d1", "g1", "2026-04-22T10:00:00+00:00",
            content="内容", keywords=["编程", "Python"]
        ))
        results = idx.search("编程", top_k=5)
        assert len(results) == 1

    def test_empty_search(self) -> None:
        idx = DiaryIndexer()
        assert idx.search("anything") == []

    def test_cosine_sim(self) -> None:
        a = [1.0, 0.0]
        b = [1.0, 0.0]
        c = [0.0, 1.0]
        assert DiaryIndexer._cosine_sim(a, b) == pytest.approx(1.0)
        assert DiaryIndexer._cosine_sim(a, c) == pytest.approx(0.0)


class TestDiaryRetriever:
    def test_token_budget(self) -> None:
        idx = DiaryIndexer()
        for i in range(10):
            idx.add(DiaryEntry(
                f"d{i}", "g1", "2026-04-22T10:00:00+00:00",
                content="这是一段非常长的日记内容" * 3,  # ~90 chars
                summary=f"摘要{i}",
            ))
        retriever = DiaryRetriever(idx)
        results = retriever.retrieve("日记", top_k=10, max_tokens_budget=200)
        # 200 tokens * 1.5 ≈ 300 chars budget
        # Each entry ~48 chars, so should fit ~6 entries
        assert 1 <= len(results) <= 7


class TestDiaryManager:
    def test_is_source_diarized(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            mgr = DiaryManager(td)
            mgr._diarized_sources["g1"] = {"b1", "b2"}
            assert mgr.is_source_diarized("g1", "b1") is True
            assert mgr.is_source_diarized("g1", "b3") is False

    def test_retrieve_empty(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            mgr = DiaryManager(td)
            assert mgr.retrieve("hello") == []

    def test_add_and_load(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            mgr = DiaryManager(td)
            entry = DiaryEntry(
                "d1", "g1", datetime.now(timezone.utc).isoformat(),
                content="测试日记", summary="测试",
            )
            mgr.add_entry("g1", entry)
            loaded = mgr._store.load("g1")
            assert len(loaded) == 1
            assert loaded[0].content == "测试日记"
