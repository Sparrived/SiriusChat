"""Diary generator: converts basic memory archive candidates into diary entries via LLM."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sirius_chat.memory.basic.models import BasicMemoryEntry
from sirius_chat.memory.diary.models import DiaryEntry

logger = logging.getLogger(__name__)

_DIARY_SYSTEM_PROMPT = (
    "你是日记整理助手。请根据提供的对话记录，以第一人称口吻整理成一段日记。\n"
    "要求：\n"
    "- 保留关键信息、用户观点、重要约定\n"
    "- 去除日常寒暄和重复内容\n"
    "- 口吻自然，像AI本人在回顾群聊经历\n"
    "- 正文不超过300字\n"
    "严格输出 JSON，包含以下字段：\n"
    '{"content": "日记正文", "keywords": ["关键词1", "关键词2"], "summary": "一句话摘要（不超过50字）"}'
)


def _build_diary_user_prompt(
    persona_name: str,
    persona_description: str,
    candidates: list[BasicMemoryEntry],
) -> str:
    lines: list[str] = []
    for e in candidates:
        speaker = e.user_id
        lines.append(f"[{speaker}] {e.content}")
    conversation = "\n".join(lines)
    return (
        f"人格设定：{persona_name}，{persona_description}\n\n"
        f"以下是对话记录：\n{conversation}\n\n"
        "请整理成日记。"
    )


class DiaryGenerator:
    """Generates diary entries from archive candidate messages."""

    async def generate(
        self,
        *,
        group_id: str,
        candidates: list[BasicMemoryEntry],
        persona_name: str,
        persona_description: str,
        provider_async: Any,
        model_name: str,
        temperature: float = 0.5,
        max_tokens: int = 512,
    ) -> DiaryEntry | None:
        """Generate a diary entry from candidate messages.

        Returns None if generation fails or candidates are empty.
        """
        if not candidates:
            return None

        from sirius_chat.providers.base import GenerationRequest

        request = GenerationRequest(
            model=model_name,
            system_prompt=_DIARY_SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": _build_diary_user_prompt(
                    persona_name, persona_description, candidates
                ),
            }],
            temperature=temperature,
            max_tokens=max_tokens,
            purpose="diary_generate",
        )

        try:
            raw = await provider_async.generate_async(request)
        except Exception as exc:
            logger.warning("日记生成 LLM 调用失败 (group=%s): %s", group_id, exc)
            return None

        parsed = self._parse_response(raw)
        if not parsed:
            return None

        now_iso = datetime.now(timezone.utc).isoformat()
        return DiaryEntry(
            entry_id=f"dgy_{uuid.uuid4().hex[:12]}",
            group_id=group_id,
            created_at=now_iso,
            source_ids=[e.entry_id for e in candidates],
            content=parsed.get("content", "")[:300],
            keywords=[str(k).strip() for k in parsed.get("keywords", []) if str(k).strip()][:10],
            summary=parsed.get("summary", "")[:50],
        )

    @staticmethod
    def _parse_response(raw: str) -> dict[str, Any] | None:
        text = raw.strip()
        if "```" in text:
            parts = text.split("```")
            if len(parts) >= 3:
                text = parts[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip()
        try:
            result = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            logger.warning("日记生成响应 JSON 解析失败")
            return None
        if isinstance(result, dict):
            return result
        return None
