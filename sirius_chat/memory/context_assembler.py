"""Context assembler: builds LLM messages from basic memory + diary RAG.

Short-term memory (recent basic memory entries) is embedded into the system
prompt as an XML block rather than traditional OpenAI message history.
This avoids role-confusion in multi-human group chat scenarios.
"""

from __future__ import annotations

import html
from typing import Any

from sirius_chat.memory.basic.manager import BasicMemoryManager
from sirius_chat.memory.diary.indexer import DiaryRetriever


class ContextAssembler:
    """Assembles conversation context for LLM generation.

    Combines recent basic memory (immediate context, embedded as XML in system
    prompt) with relevant diary entries (historical context) into standard
    OpenAI messages format.
    """

    def __init__(
        self,
        basic_mgr: BasicMemoryManager,
        diary_retriever: DiaryRetriever,
    ) -> None:
        self._basic = basic_mgr
        self._diary = diary_retriever

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_messages(
        self,
        group_id: str,
        current_query: str,
        system_prompt: str,
        *,
        search_query: str = "",
        recent_n: int = 5,
        diary_top_k: int = 5,
        diary_token_budget: int = 800,
        cross_group_user_id: str = "",
        cross_group_enabled: bool = False,
    ) -> list[dict[str, str]]:
        """Build OpenAI messages array with history embedded in system prompt.

        Returns exactly two messages:
        1. system  -- enriched with diary summaries + XML conversation history
        2. user    -- the current turn (current_query)

        When cross_group_enabled is True and cross_group_user_id is provided,
        recent messages from that user in other groups are also embedded
        (marked as cross-group to avoid confusion).
        """
        # 1. Retrieve relevant diary entries
        diary_entries = self._diary.retrieve(
            query=search_query or current_query,
            top_k=diary_top_k,
            max_tokens_budget=diary_token_budget,
        )

        # 2. Build XML conversation history from recent basic memory
        history_xml = self._build_history_xml(group_id, n=recent_n)

        # 2b. Cross-group history for the current user
        cross_group_xml = ""
        if cross_group_enabled and cross_group_user_id:
            cross_group_xml = self._build_cross_group_history_xml(
                cross_group_user_id, exclude_group_id=group_id, n=recent_n
            )

        # 3. Compose enriched system prompt
        enriched_system = self._enrich_system_prompt(
            system_prompt, diary_entries, history_xml, cross_group_xml
        )

        return [
            {"role": "system", "content": enriched_system},
            {"role": "user", "content": current_query},
        ]

    def build_history_xml(self, group_id: str, n: int = 10) -> str:
        """Build XML representation of recent conversation history.

        Exported for callers (e.g. proactive / delayed responses) that want
        to embed history into their own system prompts.
        """
        return self._build_history_xml(group_id, n=n)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_history_xml(self, group_id: str, n: int = 5) -> str:
        """Convert recent basic memory entries into an XML block."""
        recent = self._basic.get_context(group_id, n=n)
        if not recent:
            return ""
        return self._entries_to_xml(recent, tag="conversation_history")

    def _build_cross_group_history_xml(
        self, user_id: str, *, exclude_group_id: str, n: int = 5
    ) -> str:
        """Build XML of recent entries for a user across other groups."""
        entries = self._basic.get_entries_by_user(
            user_id, exclude_group_id=exclude_group_id, n=n
        )
        if not entries:
            return ""
        return self._entries_to_xml(entries, tag="cross_group_history", include_group=True)

    @staticmethod
    def _entries_to_xml(
        entries: list[Any],
        *,
        tag: str = "conversation_history",
        include_group: bool = False,
    ) -> str:
        """Convert basic memory entries into an XML block."""
        lines: list[str] = [f'<{tag}>']
        for entry in entries:
            role = entry.role
            if role == "human":
                msg_role = "user"
            elif role == "assistant":
                msg_role = "assistant"
            else:
                msg_role = "system"

            speaker = entry.speaker_name or entry.user_id or "unknown"
            safe_content = html.escape(entry.content or "", quote=False)
            safe_speaker = html.escape(speaker, quote=True)
            safe_user_id = html.escape(entry.user_id or "", quote=True)
            safe_role = html.escape(msg_role, quote=True)

            attrs = (
                f' speaker="{safe_speaker}"'
                f' user_id="{safe_user_id}"'
                f' role="{safe_role}"'
            )
            if include_group and getattr(entry, "group_id", None):
                safe_group = html.escape(entry.group_id, quote=True)
                attrs += f' group="{safe_group}"'

            lines.append(f'  <message{attrs}>{safe_content}</message>')
        lines.append(f'</{tag}>')
        return "\n".join(lines)

    @staticmethod
    def _enrich_system_prompt(
        base_prompt: str,
        diary_entries: list[Any],
        history_xml: str = "",
        cross_group_xml: str = "",
    ) -> str:
        parts: list[str] = [base_prompt]

        if diary_entries:
            parts.extend(["", "【历史日记摘要】"])
            for i, entry in enumerate(diary_entries, 1):
                parts.append(f"{i}. {entry.summary}")
                if entry.content:
                    parts.append(f"   {entry.content[:120]}")
            parts.append("【日记摘要结束】")

        if cross_group_xml:
            parts.extend([
                "",
                "【其他群近期记录】",
                "以下是你和这位用户在其它群中的近期互动（供参考，不要向当前群成员提及其它群的存在）：",
                cross_group_xml,
                "【其他群记录结束】",
            ])

        if history_xml:
            parts.extend([
                "",
                "【近期对话记录】",
                "以下是最新的几条消息，按时间顺序排列：",
                history_xml,
                "【近期对话记录结束】",
            ])

        return "\n".join(parts)
