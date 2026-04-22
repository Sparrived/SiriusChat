"""Tests for context assembler."""

from __future__ import annotations

from sirius_chat.memory.basic import BasicMemoryManager
from sirius_chat.memory.diary import DiaryIndexer, DiaryRetriever, DiaryEntry
from sirius_chat.memory.context_assembler import ContextAssembler


class TestContextAssembler:
    def test_build_messages_with_diary(self) -> None:
        basic = BasicMemoryManager()
        basic.add_entry("g1", "alice", "human", "你好")
        basic.add_entry("g1", "assistant", "assistant", "你好呀")

        indexer = DiaryIndexer()
        indexer.add(DiaryEntry("d1", "g1", "2026-04-22T10:00:00+00:00", content="之前聊过问候", summary="问候日记"))
        retriever = DiaryRetriever(indexer)

        assembler = ContextAssembler(basic, retriever)
        messages = assembler.build_messages(
            "g1", "问候", "你是助手",
            recent_n=5,
            diary_top_k=5,
            diary_token_budget=800,
        )

        assert messages[0]["role"] == "system"
        assert "问候日记" in messages[0]["content"]
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == "你好"
        assert messages[2]["role"] == "assistant"
        assert messages[2]["content"] == "你好呀"
        assert messages[3]["role"] == "user"
        assert messages[3]["content"] == "问候"

    def test_build_messages_without_diary(self) -> None:
        basic = BasicMemoryManager()
        basic.add_entry("g1", "alice", "human", "hi")

        indexer = DiaryIndexer()
        retriever = DiaryRetriever(indexer)

        assembler = ContextAssembler(basic, retriever)
        messages = assembler.build_messages("g1", "hello", "sys")

        assert messages[0]["content"] == "sys"
        assert len(messages) == 3  # system + basic + current
