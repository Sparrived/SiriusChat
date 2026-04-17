"""Compact memory prompt assembly helpers.

Keeps identity anchors and memory hints separate so the main prompt can stay
focused, cheaper, and less vulnerable to nickname-driven memory pollution.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from sirius_chat.memory.user.models import MemoryFact, UserMemoryEntry

if TYPE_CHECKING:
    from sirius_chat.config import SessionConfig
    from sirius_chat.models import Message, Transcript


_MAX_SESSION_SUMMARY_SEGMENTS = 3
_MAX_SESSION_SUMMARY_CHARS = 320
_MAX_PROMPT_PARTICIPANTS = 3
_MAX_STABLE_FACTS = 4
_MAX_TENTATIVE_ITEMS = 2
_MAX_LABELS = 4
_MAX_IDENTITIES = 2


@dataclass(slots=True)
class MemoryPromptSections:
    session_summary: str = ""
    participant_memory: str = ""


def build_memory_prompt_sections(
    *,
    config: SessionConfig,
    transcript: Transcript,
) -> MemoryPromptSections:
    """Build compact session-summary and participant-memory sections."""
    focus_message = _latest_user_message(transcript)
    focus_user_id, match_kind = _resolve_focus_user_id(transcript=transcript, message=focus_message)
    selected_user_ids = _select_prompt_user_ids(
        transcript=transcript,
        focus_message=focus_message,
        focus_user_id=focus_user_id,
    )

    participant_memory = _build_participant_memory_section(
        config=config,
        transcript=transcript,
        selected_user_ids=selected_user_ids,
        focus_user_id=focus_user_id,
        focus_match_kind=match_kind,
    )
    session_summary = _compact_session_summary(transcript.session_summary)
    return MemoryPromptSections(
        session_summary=session_summary,
        participant_memory=participant_memory,
    )


def _latest_user_message(transcript: Transcript) -> Message | None:
    for message in reversed(transcript.messages):
        if str(message.role or "").strip().lower() == "user":
            return message
    return None


def _resolve_focus_user_id(*, transcript: Transcript, message: Message | None) -> tuple[str | None, str]:
    if message is None:
        return None, ""
    manager = transcript.user_memory
    if message.channel and message.channel_user_id:
        user_id = manager.resolve_user_id(
            channel=message.channel,
            external_user_id=message.channel_user_id,
        )
        if user_id:
            return user_id, "channel_identity"

    speaker = str(message.speaker or "").strip()
    if not speaker:
        return None, ""
    user_id = manager.resolve_user_id(speaker=speaker)
    if not user_id:
        return None, ""
    entry = manager.entries.get(user_id)
    if entry is None:
        return user_id, "trusted_label"
    normalized = speaker.lower()
    if normalized == entry.profile.name.strip().lower():
        return user_id, "profile_name"
    if any(alias.strip().lower() == normalized for alias in entry.profile.aliases):
        return user_id, "trusted_alias"
    return user_id, "trusted_label"


def _select_prompt_user_ids(
    *,
    transcript: Transcript,
    focus_message: Message | None,
    focus_user_id: str | None,
) -> list[str]:
    selected: list[str] = []
    seen: set[str] = set()

    def _append(user_id: str) -> None:
        if user_id in seen:
            return
        if transcript.user_memory.get_user_by_id(user_id) is None:
            return
        selected.append(user_id)
        seen.add(user_id)

    if focus_user_id:
        _append(focus_user_id)

    if focus_message is not None and focus_message.content.strip():
        for user_id in _find_mentioned_user_ids(
            transcript=transcript,
            text=focus_message.content,
            exclude={focus_user_id} if focus_user_id else set(),
        ):
            _append(user_id)
            if len(selected) >= _MAX_PROMPT_PARTICIPANTS:
                return selected

    if selected:
        return selected[:_MAX_PROMPT_PARTICIPANTS]

    # Flatten group-isolated entries for ranking
    all_entries: list[tuple[str, UserMemoryEntry]] = []
    for _gid, group_entries in transcript.user_memory.entries.items():
        for user_id, entry in group_entries.items():
            all_entries.append((user_id, entry))

    ranked = sorted(
        all_entries,
        key=lambda item: _memory_richness(item[1]),
        reverse=True,
    )
    for user_id, _entry in ranked[:_MAX_PROMPT_PARTICIPANTS]:
        _append(user_id)
    return selected


def _find_mentioned_user_ids(
    *,
    transcript: Transcript,
    text: str,
    exclude: set[str],
) -> list[str]:
    lowered = text.lower()
    scored: list[tuple[int, str]] = []
    seen: set[str] = set()
    for group_entries in transcript.user_memory.entries.values():
        for user_id, entry in group_entries.items():
            if user_id in exclude or user_id in seen:
                continue
            seen.add(user_id)
            labels = [entry.profile.name, *entry.profile.aliases]
            best = 0
            for label in labels:
                value = label.strip()
                if len(value) < 2:
                    continue
                if value.lower() in lowered:
                    best = max(best, len(value))
            if best > 0:
                scored.append((best, user_id))
    scored.sort(reverse=True)
    return [user_id for _score, user_id in scored]


def _build_participant_memory_section(
    *,
    config: SessionConfig,
    transcript: Transcript,
    selected_user_ids: list[str],
    focus_user_id: str | None,
    focus_match_kind: str,
) -> str:
    if not selected_user_ids:
        return ""

    lines = [
        "以下为本轮相关的识人记忆，仅保留当前发言者和当前消息直接相关的人。",
        "身份判断优先级：渠道身份/外部 UID > 外部显式档案中的 name 与 aliases > 当前消息与近期上下文 > 弱线索称呼。",
        "弱线索称呼来自历史推断或临时昵称，不能单独改写人物归属；若与当前消息冲突，以当前消息和强绑定为准。",
    ]

    for user_id in selected_user_ids:
        entry = transcript.user_memory.get_user_by_id(user_id)
        if entry is None:
            continue
        role = "current_speaker" if user_id == focus_user_id else "related_participant"
        match_kind = focus_match_kind if user_id == focus_user_id else "mentioned_or_relevant"
        lines.extend(_build_participant_block(entry=entry, role=role, match_kind=match_kind))

    if len(lines) <= 3:
        return ""
    return "<participant_memory>\n" + "\n".join(lines) + "\n</participant_memory>"


def _build_participant_block(*, entry: UserMemoryEntry, role: str, match_kind: str) -> list[str]:
    header = (
        f'<participant id="{_escape_attr(entry.profile.user_id)}" '
        f'role="{_escape_attr(role)}" '
        f'name="{_escape_attr(entry.profile.name)}" '
        f'matched_by="{_escape_attr(match_kind or "memory")}">'
    )
    lines = [header]

    identities = [
        f"{channel}={external_uid}"
        for channel, external_uid in list(entry.profile.identities.items())[:_MAX_IDENTITIES]
        if channel.strip() and external_uid.strip()
    ]
    if identities:
        lines.append("  强绑定: " + " | ".join(identities))

    trusted_labels = _dedupe_non_empty([entry.profile.name, *entry.profile.aliases])[:_MAX_LABELS]
    if trusted_labels:
        lines.append("  可信称呼: " + " / ".join(trusted_labels))

    weak_labels = [
        alias
        for alias in _dedupe_non_empty(entry.runtime.inferred_aliases)
        if alias.lower() not in {label.lower() for label in trusted_labels}
    ][:3]
    if weak_labels:
        lines.append("  弱线索称呼: " + " / ".join(weak_labels))

    stable_memory = _select_stable_memory(entry)
    if stable_memory:
        lines.append("  稳定记忆: " + "；".join(stable_memory))

    tentative_memory = _select_tentative_memory(entry, stable_memory)
    if tentative_memory:
        lines.append("  暂存线索: " + "；".join(tentative_memory))

    lines.append("</participant>")
    return lines


def _select_stable_memory(entry: UserMemoryEntry) -> list[str]:
    stable_facts = [fact for fact in entry.runtime.memory_facts if _is_stable_fact(fact)]
    stable_facts.sort(key=_fact_sort_key, reverse=True)
    rendered: list[str] = []
    seen: set[str] = set()
    for fact in stable_facts:
        text = _render_fact(fact, tentative=False)
        normalized = text.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        rendered.append(text)
        if len(rendered) >= _MAX_STABLE_FACTS:
            break

    if entry.runtime.inferred_persona.strip():
        persona_text = f"画像={entry.runtime.inferred_persona.strip()}"
        if persona_text.lower() not in seen:
            rendered.insert(0, persona_text)

    if entry.runtime.preference_tags:
        interest_text = "偏好=" + ",".join(_dedupe_non_empty(entry.runtime.preference_tags)[:3])
        if interest_text.lower() not in seen:
            rendered.append(interest_text)

    if entry.runtime.inferred_traits:
        traits_text = "特质=" + ",".join(_dedupe_non_empty(entry.runtime.inferred_traits)[:3])
        if traits_text.lower() not in seen:
            rendered.append(traits_text)

    return rendered[:_MAX_STABLE_FACTS]


def _select_tentative_memory(entry: UserMemoryEntry, stable_memory: list[str]) -> list[str]:
    rendered: list[str] = []
    seen = {item.lower() for item in stable_memory}

    for note in reversed(entry.runtime.summary_notes[-_MAX_TENTATIVE_ITEMS:]):
        text = _truncate_piece(note, 48)
        if not text:
            continue
        normalized = text.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        rendered.append(text)

    if len(rendered) >= _MAX_TENTATIVE_ITEMS:
        return rendered[:_MAX_TENTATIVE_ITEMS]

    weak_facts = [fact for fact in entry.runtime.memory_facts if not _is_stable_fact(fact)]
    weak_facts.sort(key=_fact_sort_key, reverse=True)
    for fact in weak_facts:
        text = _render_fact(fact, tentative=True)
        normalized = text.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        rendered.append(text)
        if len(rendered) >= _MAX_TENTATIVE_ITEMS:
            break

    return rendered[:_MAX_TENTATIVE_ITEMS]


def _is_stable_fact(fact: MemoryFact) -> bool:
    if fact.validated:
        return True
    if fact.mention_count > 0:
        return True
    if fact.confidence >= 0.78:
        return True
    if fact.source in {"manual", "event_observation", "consolidation"} and fact.confidence >= 0.65:
        return True
    return False


def _fact_sort_key(fact: MemoryFact) -> tuple[int, int, float, str]:
    return (
        1 if fact.validated else 0,
        fact.mention_count,
        fact.confidence,
        fact.observed_at,
    )


def _render_fact(fact: MemoryFact, *, tentative: bool) -> str:
    category_map = {
        "identity": "身份",
        "preference": "偏好",
        "emotion": "情绪",
        "event": "事件",
        "custom": "线索",
    }
    label = category_map.get(fact.memory_category, "线索")
    value = _truncate_piece(fact.value, 44)
    time_tag = _relative_time_zh(fact.observed_at)
    suffix = f"({time_tag})" if time_tag else ""
    prefix = f"{label}=" if not value.startswith(f"{label}=") else ""
    marker = "~" if tentative else ""
    return f"{prefix}{value}{suffix}{marker}"


def _compact_session_summary(summary: str) -> str:
    text = str(summary or "").strip()
    if not text:
        return ""
    parts = [segment.strip() for segment in text.split("||") if segment.strip()]
    if parts:
        text = " | ".join(_truncate_piece(segment, 96) for segment in parts[-_MAX_SESSION_SUMMARY_SEGMENTS:])
    if len(text) > _MAX_SESSION_SUMMARY_CHARS:
        text = text[-_MAX_SESSION_SUMMARY_CHARS:]
    return text.strip()


def _memory_richness(entry: UserMemoryEntry) -> tuple[int, int, int]:
    return (
        len(entry.runtime.memory_facts),
        len(entry.runtime.summary_notes),
        len(entry.runtime.preference_tags) + len(entry.runtime.inferred_traits),
    )


def _dedupe_non_empty(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for raw in values:
        value = str(raw or "").strip()
        if not value:
            continue
        normalized = value.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(value)
    return result


def _truncate_piece(value: str, limit: int) -> str:
    text = str(value or "").strip().replace("\n", " ")
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _escape_attr(value: str) -> str:
    return str(value or "").replace('"', "").strip()


def _relative_time_zh(iso_str: str) -> str:
    if not iso_str:
        return ""
    try:
        observed = datetime.fromisoformat(iso_str)
        delta = datetime.now() - observed
        seconds = delta.total_seconds()
        if seconds < 0:
            return ""
        if seconds < 120:
            return "刚才"
        if seconds < 3600:
            return f"{int(seconds / 60)}分钟前"
        if seconds < 86400:
            return f"{int(seconds / 3600)}小时前"
        if seconds < 86400 * 7:
            return f"{int(seconds / 86400)}天前"
        if seconds < 86400 * 30:
            return f"{int(seconds / (86400 * 7))}周前"
        if seconds < 86400 * 365:
            return f"{int(seconds / (86400 * 30))}个月前"
        return f"{int(seconds / (86400 * 365))}年前"
    except (ValueError, TypeError):
        return ""