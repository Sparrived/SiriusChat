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
    ) -> list[dict[str, str]]:
        """Build OpenAI messages array with history embedded in system prompt.

        Returns exactly two messages:
        1. system  -- enriched with diary summaries + XML conversation history
        2. user    -- the current turn (current_query)
        """
        # 1. Retrieve relevant diary entries
        diary_entries = self._diary.retrieve(
            query=search_query or current_query,
            top_k=diary_top_k,
            max_tokens_budget=diary_token_budget,
        )

        # 2. Build XML conversation history from recent basic memory
        history_xml = self._build_history_xml(group_id, n=recent_n)

        # 3. Compose enriched system prompt
        enriched_system = self._enrich_system_prompt(system_prompt, diary_entries, history_xml)

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

        lines: list[str] = ['<conversation_history>']
        for entry in recent:
            role = entry.role
            if role == "human":
                msg_role = "user"
            elif role == "assistant":
                msg_role = "assistant"
            else:
                msg_role = "system"

            speaker = entry.speaker_name or entry.user_id or "unknown"
            # Escape content to keep valid XML
            safe_content = html.escape(entry.content or "", quote=False)
            safe_speaker = html.escape(speaker, quote=True)
            safe_user_id = html.escape(entry.user_id or "", quote=True)
            safe_role = html.escape(msg_role, quote=True)

            lines.append(
                f'  <message speaker="{safe_speaker}" '
                f'user_id="{safe_user_id}" '
                f'role="{safe_role}">'
                f'{safe_content}'
                f'</message>'
            )
        lines.append('</conversation_history>')
        return "\n".join(lines)

    @staticmethod
    def _enrich_system_prompt(
        base_prompt: str,
        diary_entries: list[Any],
        history_xml: str = "",
    ) -> str:
        parts: list[str] = [base_prompt]

        if diary_entries:
            parts.extend(["", "【历史日记摘要】"])
            for i, entry in enumerate(diary_entries, 1):
                parts.append(f"{i}. {entry.summary}")
                if entry.content:
                    parts.append(f"   {entry.content[:120]}")
            parts.append("【日记摘要结束】")

        if history_xml:
            parts.extend([
                "",
                "【近期对话记录】",
                "以下是最新的几条消息，按时间顺序排列：",
                history_xml,
                "【近期对话记录结束】",
            ])

        return "\n".join(parts)
