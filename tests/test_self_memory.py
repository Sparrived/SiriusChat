"""Tests for AI self-memory system (diary + glossary) and reply frequency limiter."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sirius_chat.memory.self.models import DiaryEntry, GlossaryTerm, SelfMemoryState
from sirius_chat.memory.self.manager import (
    DIARY_DECAY_SCHEDULE,
    MAX_DIARY_ENTRIES,
    MAX_GLOSSARY_TERMS,
    SelfMemoryManager,
)
from sirius_chat.memory.self.store import SelfMemoryFileStore
from sirius_chat.async_engine.prompts import build_system_prompt
from sirius_chat.config.models import (
    Agent,
    AgentPreset,
    OrchestrationPolicy,
    SessionConfig,
)
from sirius_chat.models.models import Message, ReplyRuntimeState, Transcript
from sirius_chat.core.engine import AsyncRolePlayEngine


# ── Helpers ──


def _past_iso(days: float) -> str:
    """ISO 8601 timestamp `days` days in the past."""
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _make_entry(
    content: str = "test",
    importance: float = 0.5,
    confidence: float = 1.0,
    days_old: float = 0.0,
    keywords: list[str] | None = None,
    category: str = "observation",
    mention_count: int = 0,
) -> DiaryEntry:
    return DiaryEntry(
        content=content,
        recorded_at=_past_iso(days_old),
        importance=importance,
        keywords=keywords or [],
        category=category,
        confidence=confidence,
        mention_count=mention_count,
    )


def _make_term(
    term: str = "API",
    definition: str = "Application Programming Interface",
    confidence: float = 0.8,
    usage_count: int = 1,
    source: str = "inferred",
    domain: str = "tech",
    context_examples: list[str] | None = None,
) -> GlossaryTerm:
    return GlossaryTerm(
        term=term,
        definition=definition,
        confidence=confidence,
        usage_count=usage_count,
        source=source,
        domain=domain,
        context_examples=context_examples or [],
    )


# ==============================================================================
# DiaryEntry model tests
# ==============================================================================


class TestDiaryEntryModel:

    def test_auto_generated_fields(self):
        entry = DiaryEntry(content="hello")
        assert entry.entry_id  # non-empty hash
        assert entry.recorded_at  # auto-set to now

    def test_importance_clamped(self):
        assert DiaryEntry(importance=1.5).importance == 1.0
        assert DiaryEntry(importance=-0.3).importance == 0.0

    def test_confidence_clamped(self):
        assert DiaryEntry(confidence=2.0).confidence == 1.0
        assert DiaryEntry(confidence=-1.0).confidence == 0.0

    def test_age_days_recent(self):
        entry = _make_entry(days_old=0)
        assert entry.age_days() < 0.01

    def test_age_days_old(self):
        entry = _make_entry(days_old=10)
        assert 9.9 < entry.age_days() < 10.1

    def test_round_trip_serialization(self):
        entry = _make_entry(content="round trip", importance=0.9, keywords=["kw1", "kw2"])
        restored = DiaryEntry.from_dict(entry.to_dict())
        assert restored.content == entry.content
        assert restored.importance == entry.importance
        assert restored.keywords == entry.keywords
        assert restored.entry_id == entry.entry_id


# ==============================================================================
# GlossaryTerm model tests
# ==============================================================================


class TestGlossaryTermModel:

    def test_auto_timestamps(self):
        term = GlossaryTerm(term="foo", definition="bar")
        assert term.first_seen_at
        assert term.last_updated_at

    def test_confidence_clamped(self):
        assert GlossaryTerm(confidence=5.0).confidence == 1.0
        assert GlossaryTerm(confidence=-2.0).confidence == 0.0

    def test_round_trip_serialization(self):
        term = _make_term(context_examples=["ex1", "ex2"], usage_count=3)
        restored = GlossaryTerm.from_dict(term.to_dict())
        assert restored.term == term.term
        assert restored.definition == term.definition
        assert restored.usage_count == term.usage_count
        assert restored.context_examples == term.context_examples


# ==============================================================================
# SelfMemoryState serialization
# ==============================================================================


class TestSelfMemoryState:

    def test_round_trip(self):
        state = SelfMemoryState(
            diary_entries=[_make_entry(content="d1"), _make_entry(content="d2")],
            glossary_terms={"api": _make_term()},
        )
        data = state.to_dict()
        restored = SelfMemoryState.from_dict(data)
        assert len(restored.diary_entries) == 2
        assert "api" in restored.glossary_terms
        assert restored.diary_entries[0].content == "d1"


# ==============================================================================
# SelfMemoryManager — Diary subsystem
# ==============================================================================


class TestDiarySubsystem:

    def test_add_entry(self):
        mgr = SelfMemoryManager()
        mgr.add_diary_entry(_make_entry(content="first"))
        assert len(mgr.diary_entries) == 1
        assert mgr.diary_entries[0].content == "first"

    def test_eviction_when_over_capacity(self):
        mgr = SelfMemoryManager()
        for i in range(MAX_DIARY_ENTRIES + 5):
            mgr.add_diary_entry(
                _make_entry(content=f"entry_{i}", importance=i / (MAX_DIARY_ENTRIES + 5))
            )
        assert len(mgr.diary_entries) == MAX_DIARY_ENTRIES

    def test_eviction_removes_weakest(self):
        mgr = SelfMemoryManager()
        weak = _make_entry(content="weak", importance=0.0, confidence=0.1)
        strong = _make_entry(content="strong", importance=1.0, confidence=1.0)
        # Fill to limit
        for i in range(MAX_DIARY_ENTRIES - 1):
            mgr.add_diary_entry(_make_entry(content=f"filler_{i}", importance=0.5))
        mgr.add_diary_entry(weak)
        mgr.add_diary_entry(strong)  # triggers eviction
        assert len(mgr.diary_entries) == MAX_DIARY_ENTRIES
        contents = {e.content for e in mgr.diary_entries}
        assert "strong" in contents
        assert "weak" not in contents

    def test_decay_removes_very_old_entries(self):
        mgr = SelfMemoryManager()
        mgr.add_diary_entry(_make_entry(content="ancient", days_old=200, confidence=0.1))
        mgr.add_diary_entry(_make_entry(content="recent", days_old=1, confidence=1.0))
        removed = mgr.apply_diary_decay()
        assert removed >= 1
        contents = {e.content for e in mgr.diary_entries}
        assert "recent" in contents
        assert "ancient" not in contents

    def test_decay_importance_slows_decay(self):
        mgr = SelfMemoryManager()
        low_imp = _make_entry(content="low", days_old=30, importance=0.0, confidence=1.0)
        high_imp = _make_entry(content="high", days_old=30, importance=1.0, confidence=1.0)
        mgr.add_diary_entry(low_imp)
        mgr.add_diary_entry(high_imp)
        mgr.apply_diary_decay()
        entries = {e.content: e.confidence for e in mgr.diary_entries}
        # High importance should retain more confidence
        if "low" in entries and "high" in entries:
            assert entries["high"] > entries["low"]
        elif "high" in entries:
            # Low importance was removed entirely
            pass
        else:
            pytest.fail("High-importance entry should not be removed at 30 days")

    def test_decay_mention_boost(self):
        mgr = SelfMemoryManager()
        no_mention = _make_entry(content="no_mention", days_old=30, mention_count=0, confidence=1.0)
        mentioned = _make_entry(content="mentioned", days_old=30, mention_count=5, confidence=1.0)
        mgr.add_diary_entry(no_mention)
        mgr.add_diary_entry(mentioned)
        mgr.apply_diary_decay()
        entries = {e.content: e.confidence for e in mgr.diary_entries}
        if "no_mention" in entries and "mentioned" in entries:
            assert entries["mentioned"] > entries["no_mention"]

    def test_reinforce_entry(self):
        mgr = SelfMemoryManager()
        entry = _make_entry(content="reinforce me", confidence=0.7)
        mgr.add_diary_entry(entry)
        eid = entry.entry_id
        assert mgr.reinforce_diary_entry(eid) is True
        assert mgr.diary_entries[0].mention_count == 1
        assert mgr.diary_entries[0].confidence == pytest.approx(0.8, abs=0.01)

    def test_reinforce_nonexistent_returns_false(self):
        mgr = SelfMemoryManager()
        assert mgr.reinforce_diary_entry("nonexistent") is False

    def test_get_relevant_entries_sorted_by_score(self):
        mgr = SelfMemoryManager()
        mgr.add_diary_entry(_make_entry(content="low", importance=0.1, confidence=0.3))
        mgr.add_diary_entry(_make_entry(content="high", importance=0.9, confidence=0.9))
        mgr.add_diary_entry(_make_entry(content="mid", importance=0.5, confidence=0.5))
        results = mgr.get_relevant_diary_entries(max_entries=2)
        assert len(results) == 2
        assert results[0].content == "high"

    def test_get_relevant_entries_keyword_boost(self):
        mgr = SelfMemoryManager()
        mgr.add_diary_entry(_make_entry(content="A", importance=0.5, confidence=0.5, keywords=["target"]))
        mgr.add_diary_entry(_make_entry(content="B", importance=0.5, confidence=0.5, keywords=["other"]))
        results = mgr.get_relevant_diary_entries(keywords=["target"], max_entries=2)
        assert results[0].content == "A"


# ==============================================================================
# SelfMemoryManager — Glossary subsystem
# ==============================================================================


class TestGlossarySubsystem:

    def test_add_new_term(self):
        mgr = SelfMemoryManager()
        mgr.add_or_update_term(_make_term(term="REST", definition="Representational State Transfer"))
        assert mgr.get_term("REST") is not None
        assert mgr.get_term("rest") is not None  # case-insensitive

    def test_update_existing_term_merges(self):
        mgr = SelfMemoryManager()
        mgr.add_or_update_term(_make_term(term="API", confidence=0.5, context_examples=["ex1"]))
        mgr.add_or_update_term(
            _make_term(
                term="API",
                definition="Better definition",
                confidence=0.9,
                context_examples=["ex2"],
            )
        )
        term = mgr.get_term("api")
        assert term is not None
        assert term.definition == "Better definition"
        assert term.usage_count == 2
        assert "ex1" in term.context_examples
        assert "ex2" in term.context_examples

    def test_update_lower_confidence_keeps_original_definition(self):
        mgr = SelfMemoryManager()
        mgr.add_or_update_term(_make_term(term="API", definition="original", confidence=0.9))
        mgr.add_or_update_term(_make_term(term="API", definition="weaker", confidence=0.3))
        term = mgr.get_term("api")
        assert term.definition == "original"

    def test_empty_term_ignored(self):
        mgr = SelfMemoryManager()
        mgr.add_or_update_term(GlossaryTerm(term="", definition="empty"))
        assert len(mgr.glossary_terms) == 0

    def test_eviction_when_over_capacity(self):
        mgr = SelfMemoryManager()
        for i in range(MAX_GLOSSARY_TERMS + 5):
            mgr.add_or_update_term(
                _make_term(term=f"term_{i}", confidence=i / (MAX_GLOSSARY_TERMS + 5))
            )
        assert len(mgr.glossary_terms) == MAX_GLOSSARY_TERMS

    def test_get_relevant_terms_matches_text(self):
        mgr = SelfMemoryManager()
        mgr.add_or_update_term(_make_term(term="API"))
        mgr.add_or_update_term(_make_term(term="REST"))
        results = mgr.get_relevant_terms("This API is fast")
        assert len(results) == 1
        assert results[0].term == "API"

    def test_get_relevant_terms_empty_text(self):
        mgr = SelfMemoryManager()
        mgr.add_or_update_term(_make_term(term="API"))
        results = mgr.get_relevant_terms("")
        assert len(results) == 0

    def test_domain_update_from_non_custom(self):
        mgr = SelfMemoryManager()
        mgr.add_or_update_term(_make_term(term="Docker", domain="custom"))
        mgr.add_or_update_term(_make_term(term="Docker", domain="tech"))
        assert mgr.get_term("docker").domain == "tech"


# ==============================================================================
# Prompt section builders
# ==============================================================================


class TestPromptSectionBuilders:

    def test_diary_prompt_section_empty_when_no_entries(self):
        mgr = SelfMemoryManager()
        assert mgr.build_diary_prompt_section() == ""

    def test_diary_prompt_section_includes_content(self):
        mgr = SelfMemoryManager()
        mgr.add_diary_entry(_make_entry(content="Important insight", importance=0.8))
        section = mgr.build_diary_prompt_section()
        assert "Important insight" in section
        assert "!" in section  # importance >= 0.7

    def test_diary_prompt_section_low_confidence_marker(self):
        mgr = SelfMemoryManager()
        mgr.add_diary_entry(_make_entry(content="Uncertain note", confidence=0.3))
        section = mgr.build_diary_prompt_section()
        assert "?" in section  # confidence < 0.5

    def test_diary_prompt_section_keyword_tags(self):
        mgr = SelfMemoryManager()
        mgr.add_diary_entry(_make_entry(content="note", keywords=["kw1", "kw2"]))
        section = mgr.build_diary_prompt_section()
        assert "#kw1,kw2" in section

    def test_diary_prompt_section_category_prefix(self):
        mgr = SelfMemoryManager()
        mgr.add_diary_entry(_make_entry(content="note", category="decision"))
        section = mgr.build_diary_prompt_section()
        assert "[decision]" in section

    def test_diary_prompt_section_observation_no_prefix(self):
        mgr = SelfMemoryManager()
        mgr.add_diary_entry(_make_entry(content="ob note", category="observation"))
        section = mgr.build_diary_prompt_section()
        assert "[observation]" not in section

    def test_glossary_prompt_section_empty_when_no_terms(self):
        mgr = SelfMemoryManager()
        assert mgr.build_glossary_prompt_section() == ""

    def test_glossary_prompt_section_includes_definition(self):
        mgr = SelfMemoryManager()
        mgr.add_or_update_term(_make_term(term="API", definition="Interface for apps"))
        section = mgr.build_glossary_prompt_section(text="the API endpoint")
        assert "API" in section
        assert "Interface for apps" in section

    def test_glossary_prompt_section_low_confidence_marker(self):
        mgr = SelfMemoryManager()
        mgr.add_or_update_term(_make_term(term="CRUD", definition="Create Read Update Delete", confidence=0.4))
        section = mgr.build_glossary_prompt_section(text="use CRUD operations")
        assert "CRUD?" in section  # confidence < 0.6

    def test_glossary_prompt_section_no_text_returns_top_terms(self):
        mgr = SelfMemoryManager()
        mgr.add_or_update_term(_make_term(term="A", confidence=0.9, usage_count=10))
        mgr.add_or_update_term(_make_term(term="B", confidence=0.1, usage_count=1))
        section = mgr.build_glossary_prompt_section(text="")
        assert "A" in section


# ==============================================================================
# SelfMemoryFileStore
# ==============================================================================


class TestSelfMemoryFileStore:

    def test_save_and_load_round_trip(self, tmp_path: Path):
        store = SelfMemoryFileStore(tmp_path)
        mgr = SelfMemoryManager()
        mgr.add_diary_entry(_make_entry(content="persisted entry"))
        mgr.add_or_update_term(_make_term(term="persisted term"))
        store.save(mgr)

        loaded = store.load()
        assert len(loaded.diary_entries) == 1
        assert loaded.diary_entries[0].content == "persisted entry"
        assert loaded.get_term("persisted term") is not None

    def test_load_missing_file_returns_empty(self, tmp_path: Path):
        store = SelfMemoryFileStore(tmp_path)
        mgr = store.load()
        assert len(mgr.diary_entries) == 0
        assert len(mgr.glossary_terms) == 0

    def test_load_corrupt_file_returns_empty(self, tmp_path: Path):
        store = SelfMemoryFileStore(tmp_path)
        (tmp_path / "self_memory.json").write_text("NOT JSON", encoding="utf-8")
        mgr = store.load()
        assert len(mgr.diary_entries) == 0


# ==============================================================================
# Prompt integration — diary and glossary in system prompt
# ==============================================================================


class TestPromptIntegration:

    def test_diary_section_in_system_prompt(self):
        config = SessionConfig(
            preset=AgentPreset(
                agent=Agent(name="Bot", persona="helpful", model="m"),
                global_system_prompt="Global prompt.",
            ),
            work_path="./data",
        )
        transcript = Transcript()
        prompt = build_system_prompt(
            config, transcript, diary_section="[decision]! Study Python #code"
        )
        assert "<self_diary>" in prompt
        assert "Study Python" in prompt
        assert "</self_diary>" in prompt

    def test_glossary_section_in_system_prompt(self):
        config = SessionConfig(
            preset=AgentPreset(
                agent=Agent(name="Bot", persona="helpful", model="m"),
                global_system_prompt="Global prompt.",
            ),
            work_path="./data",
        )
        transcript = Transcript()
        prompt = build_system_prompt(
            config, transcript, glossary_section="API: Application Interface"
        )
        assert "<glossary>" in prompt
        assert "API: Application Interface" in prompt
        assert "</glossary>" in prompt

    def test_empty_sections_omitted(self):
        config = SessionConfig(
            preset=AgentPreset(
                agent=Agent(name="Bot", persona="helpful", model="m"),
                global_system_prompt="Global prompt.",
            ),
            work_path="./data",
        )
        transcript = Transcript()
        prompt = build_system_prompt(config, transcript, diary_section="", glossary_section="")
        assert "<self_diary>" not in prompt
        assert "<glossary>" not in prompt


# ==============================================================================
# Reply frequency limiter
# ==============================================================================


class TestReplyFrequencyLimiter:

    @staticmethod
    def _make_config(
        window: float = 60.0,
        max_replies: int = 3,
        exempt_on_mention: bool = True,
    ) -> SessionConfig:
        return SessionConfig(
            preset=AgentPreset(
                agent=Agent(name="TestBot", persona="p", model="m"),
                global_system_prompt="test",
            ),
            work_path="./data",
            orchestration=OrchestrationPolicy(
                unified_model="mock-model",
                reply_frequency_window_seconds=window,
                reply_frequency_max_replies=max_replies,
                reply_frequency_exempt_on_mention=exempt_on_mention,
            ),
        )

    @staticmethod
    def _make_transcript(timestamps: list[str]) -> Transcript:
        t = Transcript()
        t.reply_runtime.assistant_reply_timestamps = list(timestamps)
        return t

    def test_under_limit_not_blocked(self):
        now = datetime.now(timezone.utc)
        recent = (now - timedelta(seconds=10)).isoformat()
        config = self._make_config(max_replies=3)
        transcript = self._make_transcript([recent, recent])
        turn = Message(role="user", speaker="User", content="hello")
        result = AsyncRolePlayEngine._check_reply_frequency_limit(
            transcript=transcript, config=config, turn=turn, now=now,
        )
        assert result is False  # not blocked

    def test_at_limit_blocked(self):
        now = datetime.now(timezone.utc)
        recent = (now - timedelta(seconds=10)).isoformat()
        config = self._make_config(max_replies=3)
        transcript = self._make_transcript([recent, recent, recent])
        turn = Message(role="user", speaker="User", content="hello")
        result = AsyncRolePlayEngine._check_reply_frequency_limit(
            transcript=transcript, config=config, turn=turn, now=now,
        )
        assert result is True  # blocked

    def test_old_timestamps_outside_window_not_counted(self):
        now = datetime.now(timezone.utc)
        old = (now - timedelta(seconds=120)).isoformat()  # outside 60s window
        recent = (now - timedelta(seconds=5)).isoformat()
        config = self._make_config(window=60.0, max_replies=2)
        transcript = self._make_transcript([old, old, old, recent])
        turn = Message(role="user", speaker="User", content="hello")
        result = AsyncRolePlayEngine._check_reply_frequency_limit(
            transcript=transcript, config=config, turn=turn, now=now,
        )
        assert result is False  # only 1 recent, limit is 2

    def test_mention_exemption_bypasses_limit(self):
        now = datetime.now(timezone.utc)
        recent = (now - timedelta(seconds=5)).isoformat()
        config = self._make_config(max_replies=2, exempt_on_mention=True)
        transcript = self._make_transcript([recent, recent, recent])
        turn = Message(role="user", speaker="User", content="Hey TestBot what do you think?")
        result = AsyncRolePlayEngine._check_reply_frequency_limit(
            transcript=transcript, config=config, turn=turn, now=now,
        )
        assert result is False  # not blocked because of mention

    def test_mention_exemption_disabled(self):
        now = datetime.now(timezone.utc)
        recent = (now - timedelta(seconds=5)).isoformat()
        config = self._make_config(max_replies=2, exempt_on_mention=False)
        transcript = self._make_transcript([recent, recent, recent])
        turn = Message(role="user", speaker="User", content="Hey TestBot")
        result = AsyncRolePlayEngine._check_reply_frequency_limit(
            transcript=transcript, config=config, turn=turn, now=now,
        )
        assert result is True  # blocked even with mention

    def test_zero_max_replies_disables_limiter(self):
        now = datetime.now(timezone.utc)
        config = self._make_config(max_replies=0)
        transcript = self._make_transcript(["2025-01-01T00:00:00+00:00"] * 100)
        turn = Message(role="user", speaker="User", content="hello")
        result = AsyncRolePlayEngine._check_reply_frequency_limit(
            transcript=transcript, config=config, turn=turn, now=now,
        )
        assert result is False  # disabled

    def test_zero_window_disables_limiter(self):
        now = datetime.now(timezone.utc)
        config = self._make_config(window=0.0)
        transcript = self._make_transcript([(now - timedelta(seconds=1)).isoformat()] * 100)
        turn = Message(role="user", speaker="User", content="hello")
        result = AsyncRolePlayEngine._check_reply_frequency_limit(
            transcript=transcript, config=config, turn=turn, now=now,
        )
        assert result is False  # disabled

    def test_invalid_timestamps_ignored(self):
        now = datetime.now(timezone.utc)
        recent = (now - timedelta(seconds=5)).isoformat()
        config = self._make_config(max_replies=3)
        transcript = self._make_transcript(["bad", "invalid", recent, recent])
        turn = Message(role="user", speaker="User", content="hello")
        result = AsyncRolePlayEngine._check_reply_frequency_limit(
            transcript=transcript, config=config, turn=turn, now=now,
        )
        assert result is False  # only 2 valid recent


# ==============================================================================
# SelfMemoryManager serialization round-trip
# ==============================================================================


class TestManagerSerialization:

    def test_to_dict_from_dict_round_trip(self):
        mgr = SelfMemoryManager()
        mgr.add_diary_entry(_make_entry(content="diary 1"))
        mgr.add_diary_entry(_make_entry(content="diary 2", keywords=["kw"]))
        mgr.add_or_update_term(_make_term(term="REST", definition="RESTful"))
        mgr.add_or_update_term(_make_term(term="gRPC", definition="Google RPC"))

        data = mgr.to_dict()
        restored = SelfMemoryManager.from_dict(data)

        assert len(restored.diary_entries) == 2
        assert restored.diary_entries[1].keywords == ["kw"]
        assert len(restored.glossary_terms) == 2
        assert restored.get_term("rest").definition == "RESTful"


# ==============================================================================
# Config fields existence
# ==============================================================================


class TestConfigFields:

    def test_self_memory_config_defaults(self):
        policy = OrchestrationPolicy()
        assert policy.enable_self_memory is True
        assert policy.self_memory_extract_batch_size == 3
        assert policy.self_memory_max_diary_prompt_entries == 6
        assert policy.self_memory_max_glossary_prompt_terms == 15

    def test_reply_frequency_config_defaults(self):
        policy = OrchestrationPolicy()
        assert policy.reply_frequency_window_seconds == 60.0
        assert policy.reply_frequency_max_replies == 8
        assert policy.reply_frequency_exempt_on_mention is True

    def test_reply_runtime_state_timestamps(self):
        state = ReplyRuntimeState()
        assert state.assistant_reply_timestamps == []
        state.assistant_reply_timestamps.append("2025-01-01T00:00:00+00:00")
        assert len(state.assistant_reply_timestamps) == 1
