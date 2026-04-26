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

        # Should return exactly 2 messages: system (with XML history + diary) + user (current)
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert "问候日记" in messages[0]["content"]
        # History is embedded in system prompt as XML
        assert "<conversation_history>" in messages[0]["content"]
        assert 'speaker="alice"' in messages[0]["content"]
        assert 'speaker="assistant"' in messages[0]["content"]
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == "问候"

    def test_build_messages_without_diary(self) -> None:
        basic = BasicMemoryManager()
        basic.add_entry("g1", "alice", "human", "hi")

        indexer = DiaryIndexer()
        retriever = DiaryRetriever(indexer)

        assembler = ContextAssembler(basic, retriever)
        messages = assembler.build_messages("g1", "hello", "sys")

        # Should return exactly 2 messages: system (with XML history) + user (current)
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert "<conversation_history>" in messages[0]["content"]
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == "hello"

    def test_build_history_xml(self) -> None:
        basic = BasicMemoryManager()
        basic.add_entry("g1", "alice", "human", "你好", speaker_name="Alice")
        basic.add_entry("g1", "assistant", "assistant", "你好呀", speaker_name="小星")

        indexer = DiaryIndexer()
        retriever = DiaryRetriever(indexer)

        assembler = ContextAssembler(basic, retriever)
        xml = assembler.build_history_xml("g1", n=5)

        assert "<conversation_history>" in xml
        assert "</conversation_history>" in xml
        assert 'speaker="Alice"' in xml
        assert 'speaker="小星"' in xml
        assert 'role="user"' in xml
        assert 'role="assistant"' in xml
        assert "你好" in xml
        assert "你好呀" in xml

    def test_xml_escaping(self) -> None:
        basic = BasicMemoryManager()
        basic.add_entry("g1", "alice", "human", "<script>alert('xss')</script>", speaker_name="Alice")

        indexer = DiaryIndexer()
        retriever = DiaryRetriever(indexer)

        assembler = ContextAssembler(basic, retriever)
        xml = assembler.build_history_xml("g1", n=5)

        # Content should be escaped, raw HTML tags should not appear
        assert "<script>" not in xml
        assert "&lt;script&gt;" in xml
