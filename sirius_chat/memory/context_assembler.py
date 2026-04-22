"""Context assembler: builds LLM messages from basic memory + diary RAG."""

from __future__ import annotations

from typing import Any

from sirius_chat.memory.basic.manager import BasicMemoryManager
from sirius_chat.memory.diary.indexer import DiaryRetriever


class ContextAssembler:
    """Assembles conversation context for LLM generation.

    Combines recent basic memory (immediate context) with relevant diary
    entries (historical context) into standard OpenAI messages format.
    """

    def __init__(
        self,
        basic_mgr: BasicMemoryManager,
        diary_retriever: DiaryRetriever,
    ) -> None:
        self._basic = basic_mgr
        self._diary = diary_retriever

    def build_messages(
        self,
        group_id: str,
        current_query: str,
        system_prompt: str,
        *,
        recent_n: int = 5,
        diary_top_k: int = 5,
        diary_token_budget: int = 800,
    ) -> list[dict[str, str]]:
        """Build standard OpenAI messages array.

        Returns messages in chronological order:
        [system(with diary context), ...recent_basic_entries..., user(current_query)]
        """
        # 1. Retrieve relevant diary entries
        diary_entries = self._diary.retrieve(
            query=current_query,
            top_k=diary_top_k,
            max_tokens_budget=diary_token_budget,
        )

        # 2. Build enriched system prompt
        enriched_system = self._enrich_system_prompt(system_prompt, diary_entries)

        # 3. Build message list
        messages: list[dict[str, str]] = [{"role": "system", "content": enriched_system}]

        # 4. Add recent basic memory entries as context
        recent = self._basic.get_context(group_id, n=recent_n)
        for entry in recent:
            role = entry.role
            # Map internal roles to OpenAI roles
            if role == "human":
                msg_role = "user"
            elif role == "assistant":
                msg_role = "assistant"
            else:
                msg_role = "system"

            content = entry.content
            if entry.system_prompt and role == "assistant":
                # Optionally note the system prompt used for this turn
                # (kept minimal to avoid token bloat)
                pass

            messages.append({"role": msg_role, "content": content})

        # 5. Append current user query
        messages.append({"role": "user", "content": current_query})

        return messages

    @staticmethod
    def _enrich_system_prompt(
        base_prompt: str,
        diary_entries: list[Any],
    ) -> str:
        if not diary_entries:
            return base_prompt

        lines: list[str] = [base_prompt, "", "【历史日记摘要】"]
        for i, entry in enumerate(diary_entries, 1):
            lines.append(f"{i}. {entry.summary}")
            if entry.content:
                lines.append(f"   {entry.content[:120]}")
        lines.append("【日记摘要结束】")

        return "\n".join(lines)
