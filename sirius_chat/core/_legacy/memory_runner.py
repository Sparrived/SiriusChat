"""Memory and event extraction task functions.

These are pure async functions extracted from ``AsyncRolePlayEngine`` to reduce
the size of the god-class.  They have no engine-level state — all dependencies
(provider callable, model resolver, …) are passed explicitly via keyword args.
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Awaitable, Callable

from sirius_chat.config import SessionConfig
from sirius_chat.models import Message, Participant, Transcript
from sirius_chat.providers.base import GenerationRequest
from sirius_chat.async_engine.orchestration import (
    TASK_MEMORY_EXTRACT,
    TASK_EVENT_EXTRACT,
    TASK_MEMORY_MANAGER,
    get_system_prompt_for_task,
)
from sirius_chat.async_engine.utils import (
    estimate_tokens,
    extract_json_payload,
    record_task_stat,
)
from sirius_chat.memory import EventMemoryManager, SelfMemoryManager
from sirius_chat.memory.self.models import DiaryEntry, GlossaryTerm

if TYPE_CHECKING:
    from sirius_chat.core._legacy.engine import LiveSessionContext

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

_CallWithRetry = Callable[..., Awaitable[str]]
_GetModel = Callable[[SessionConfig, str], str]


# ---------------------------------------------------------------------------
# build_memory_extract_task_input
# ---------------------------------------------------------------------------

def build_memory_extract_task_input(
    *,
    transcript: Transcript,
    participant: Participant,
    content: str,
    max_context_messages: int = 8,
    max_context_chars: int = 1200,
) -> str:
    """Build the user-content string fed to the memory-extract LLM task."""
    # Group-isolated lookup: search across all groups for this user
    entry = None
    for group_entries in transcript.user_memory.entries.values():
        entry = group_entries.get(participant.user_id)
        if entry is not None:
            break
    trusted_labels = [participant.name, *participant.aliases]
    weak_labels = entry.runtime.inferred_aliases if entry is not None else []
    identity_lines: list[str] = []
    for channel, external_uid in list(participant.identities.items())[:2]:
        if channel and external_uid:
            identity_lines.append(f"{channel}={external_uid}")

    context_lines: list[str] = []
    for message in transcript.messages:
        role = str(message.role or "").strip().lower()
        if role not in {"user", "assistant"}:
            continue
        text = str(message.content or "").strip()
        if not text:
            continue
        speaker = str(message.speaker or role).strip()
        context_lines.append(f"[{role}][{speaker}] {text}")

    if max_context_messages > 0:
        context_lines = context_lines[-max_context_messages:]

    context_text = "\n".join(context_lines)
    if max_context_chars > 0 and len(context_text) > max_context_chars:
        context_text = context_text[-max_context_chars:]

    return (
        f"user_id={participant.user_id}\n"
        f"speaker={participant.name}\n"
        f"strong_identity={' | '.join(identity_lines) if identity_lines else 'none'}\n"
        f"trusted_labels={', '.join(label for label in trusted_labels if label.strip()) or 'none'}\n"
        f"weak_labels={', '.join(label for label in weak_labels if label.strip()) or 'none'}\n"
        "alias_guardrails=仅当当前说话者在本轮上下文中明确自称某个名字，且不与 strong_identity 或 trusted_labels 冲突时，才可输出 inferred_aliases；"
        "第三方称呼、玩笑、模仿、引用、群内他人代称一律不要当作该用户别名。\n"
        f"latest_user_content={content}\n"
        "conversation_context=\n"
        f"{context_text}"
    )


# ---------------------------------------------------------------------------
# run_memory_extract_task
# ---------------------------------------------------------------------------

async def run_memory_extract_task(
    *,
    config: SessionConfig,
    transcript: Transcript,
    participant: Participant,
    content: str,
    task_token_usage: dict[str, int],
    call_with_retry: _CallWithRetry,
    get_model: _GetModel,
) -> None:
    """Extract and persist user memory facts from the latest turn."""
    task_name = TASK_MEMORY_EXTRACT
    if not config.orchestration.task_enabled.get(task_name, True):
        return

    model = get_model(config, task_name)
    record_task_stat(transcript, task_name, "attempted")

    system_prompt = get_system_prompt_for_task(task_name)
    task_input = build_memory_extract_task_input(
        transcript=transcript,
        participant=participant,
        content=content,
    )
    estimated_cost = estimate_tokens(system_prompt + task_input)

    used = task_token_usage.get(task_name, 0)

    request_payload = GenerationRequest(
        model=model,
        system_prompt=system_prompt,
        messages=[{"role": "user", "content": task_input}],
        temperature=float(config.orchestration.task_temperatures.get(task_name, 0.1)),
        max_tokens=int(config.orchestration.task_max_tokens.get(task_name, 128)),
        purpose=task_name,
    )

    retry_times = int(config.orchestration.task_retries.get(task_name, 0))
    try:
        raw = await call_with_retry(
            request_payload=request_payload,
            retry_times=retry_times,
            transcript=transcript,
            task_name=task_name,
            actor_id=participant.user_id,
        )
    except RuntimeError:
        record_task_stat(transcript, task_name, "failed_provider")
        return

    if retry_times > 0:
        record_task_stat(transcript, task_name, "retry_enabled")

    task_token_usage[task_name] = used + estimated_cost
    parsed = extract_json_payload(raw)
    if parsed is None:
        record_task_stat(transcript, task_name, "failed_parse")
        return

    inferred_persona = parsed.get("inferred_persona")
    inferred_aliases = parsed.get("inferred_aliases")
    inferred_traits = parsed.get("inferred_traits")
    preference_tags = parsed.get("preference_tags")
    summary_note = parsed.get("summary_note")

    transcript.user_memory.apply_ai_runtime_update(
        user_id=participant.user_id,
        inferred_persona=str(inferred_persona).strip() if isinstance(inferred_persona, str) else None,
        inferred_aliases=[str(item).strip() for item in inferred_aliases if str(item).strip()]
        if isinstance(inferred_aliases, list)
        else None,
        inferred_traits=[str(item).strip() for item in inferred_traits if str(item).strip()]
        if isinstance(inferred_traits, list)
        else None,
        preference_tags=[str(item).strip() for item in preference_tags if str(item).strip()]
        if isinstance(preference_tags, list)
        else None,
        summary_note=str(summary_note).strip() if isinstance(summary_note, str) else None,
        source="memory_extract",
        confidence=0.8,
    )
    record_task_stat(transcript, task_name, "succeeded")


# ---------------------------------------------------------------------------
# run_self_memory_extract_task
# ---------------------------------------------------------------------------

async def run_self_memory_extract_task(
    *,
    config: SessionConfig,
    transcript: Transcript,
    context: LiveSessionContext,
    assistant_content: str,
    call_with_retry: _CallWithRetry,
    get_model: _GetModel,
) -> None:
    """Extract diary entries and glossary terms from the conversation."""
    if not config.orchestration.enable_self_memory:
        return

    task_name = "self_memory_extract"
    if config.orchestration.task_models.get(task_name):
        model = get_model(config, task_name)
    elif config.orchestration.unified_model:
        model = config.orchestration.unified_model
    else:
        model = config.orchestration.resolve_model_for_task(
            TASK_MEMORY_MANAGER,
            default_model=config.agent.model,
        )

    recent_msgs: list[str] = []
    for msg in transcript.messages[-8:]:
        role = msg.role
        speaker = msg.speaker or role
        text = msg.content[:200].replace("\n", " ")
        if text.strip():
            recent_msgs.append(f"[{speaker}] {text}")
    context_text = "\n".join(recent_msgs)

    system_prompt = (
        "你是AI的自省记忆提取器。基于以下对话片段，提取两类记忆：\n"
        "1. diary: AI值得记住的事情（有趣的事、重要决定、情感印象、里程碑）。"
        "每条包含 content(string), importance(0-1), keywords(array), category(reflection|observation|decision|emotion|milestone)。\n"
        "2. glossary: 对话中出现的AI可能不熟悉或值得记录的专有名词/术语。"
        "每条包含 term(string), definition(string), domain(tech|daily|culture|game|custom), confidence(0-1)。\n"
        "严格输出JSON: {\"diary\": [...], \"glossary\": [...]}\n"
        "若无值得记录的内容，返回空数组。保持简洁，每次最多3条diary和5条glossary。"
    )

    request_payload = GenerationRequest(
        model=model,
        system_prompt=system_prompt,
        messages=[{"role": "user", "content": context_text}],
        temperature=0.2,
        max_tokens=int(config.orchestration.task_max_tokens.get(task_name, 256)),
        purpose=task_name,
    )

    try:
        raw = await call_with_retry(
            request_payload=request_payload,
            retry_times=0,
            transcript=transcript,
            task_name=task_name,
            actor_id=config.agent.name,
        )
    except RuntimeError:
        logger.debug("自我记忆提取失败，跳过")
        return

    parsed = extract_json_payload(raw)
    if parsed is None:
        return

    diary_items = parsed.get("diary")
    if isinstance(diary_items, list):
        for item in diary_items[:3]:
            if not isinstance(item, dict):
                continue
            content_text = str(item.get("content", "")).strip()
            if not content_text:
                continue
            entry = DiaryEntry(
                content=content_text,
                importance=float(item.get("importance", 0.5)),
                keywords=[str(k) for k in item.get("keywords", []) if str(k).strip()],
                category=str(item.get("category", "observation")),
                related_user_ids=[],
            )
            context.subsystems.self_memory.add_diary_entry(entry)

    glossary_items = parsed.get("glossary")
    if isinstance(glossary_items, list):
        for item in glossary_items[:5]:
            if not isinstance(item, dict):
                continue
            term_text = str(item.get("term", "")).strip()
            defn = str(item.get("definition", "")).strip()
            if not term_text or not defn:
                continue
            term = GlossaryTerm(
                term=term_text,
                definition=defn,
                source="conversation",
                confidence=float(item.get("confidence", 0.6)),
                domain=str(item.get("domain", "custom")),
                context_examples=[assistant_content[:80]] if assistant_content.strip() else [],
            )
            context.subsystems.self_memory.add_or_update_term(term)

    logger.debug(
        "自我记忆提取完成 | diary=%d glossary=%d",
        len(diary_items) if isinstance(diary_items, list) else 0,
        len(glossary_items) if isinstance(glossary_items, list) else 0,
    )


# ---------------------------------------------------------------------------
# run_batch_event_extract
# ---------------------------------------------------------------------------

async def run_batch_event_extract(
    *,
    config: SessionConfig,
    transcript: Transcript,
    participant: Participant,
    task_token_usage: dict[str, int],
    event_store: EventMemoryManager,
    make_adapter: Callable[[], object],
    get_model: _GetModel,
) -> list[object]:
    """Batch-extract user observations from buffered messages."""
    task_name = TASK_EVENT_EXTRACT
    if not config.orchestration.task_enabled.get(task_name, True):
        return []

    model = get_model(config, task_name)
    record_task_stat(transcript, task_name, "attempted")

    estimated_cost = 512
    used = task_token_usage.get(task_name, 0)

    try:
        new_observations = await event_store.extract_observations(
            user_id=participant.user_id,
            user_name=participant.name,
            provider_async=make_adapter(),
            model_name=model,
            temperature=float(config.orchestration.task_temperatures.get(task_name, 0.3)),
            max_tokens=int(config.orchestration.task_max_tokens.get(task_name, 512)),
        )
    except Exception as exc:
        logger.warning("批量事件提取失败 (user=%s): %s", participant.user_id, exc)
        record_task_stat(transcript, task_name, "failed_provider")
        return []

    task_token_usage[task_name] = used + estimated_cost

    if not new_observations:
        record_task_stat(transcript, task_name, "no_observations")
        return []

    category_to_memory = {
        "preference": ("preference_tag", "preference"),
        "trait": ("inferred_trait", "identity"),
        "relationship": ("social_context", "event"),
        "experience": ("summary", "event"),
        "emotion": ("emotional_pattern", "emotion"),
        "goal": ("summary", "event"),
        "custom": ("summary", "custom"),
    }
    for obs in new_observations:
        fact_type, mem_cat = category_to_memory.get(obs.category, ("summary", "custom"))
        transcript.user_memory.add_memory_fact(
            user_id=participant.user_id,
            fact_type=fact_type,
            value=obs.summary,
            source="event_observation",
            confidence=obs.confidence,
            memory_category=mem_cat,
            source_event_id=obs.event_id,
        )

    record_task_stat(transcript, task_name, "succeeded")
    logger.info(
        "留意到 %s 的 %d 处细节，已悄悄记下",
        participant.name or participant.user_id, len(new_observations),
    )
    return list(new_observations)


# ---------------------------------------------------------------------------
# run_memory_manager_task
# ---------------------------------------------------------------------------

async def run_memory_manager_task(
    *,
    config: SessionConfig,
    transcript: Transcript,
    participant: Participant,
    task_token_usage: dict[str, int],
    call_with_retry: _CallWithRetry,
) -> None:
    """汇聚、去重、标注、验证用户的记忆事实。"""
    task_name = TASK_MEMORY_MANAGER
    if not config.orchestration.is_task_enabled(task_name):
        return

    model = config.orchestration.resolve_model_for_task(task_name)
    if not model:
        return

    record_task_stat(transcript, task_name, "attempted")

    entry = transcript.user_memory.entries.get(participant.user_id)
    if entry is None or not entry.runtime.memory_facts:
        return

    facts_json = [
        {
            "id": i,
            "fact_type": fact.fact_type,
            "value": fact.value,
            "source": fact.source,
            "confidence": fact.confidence,
            "category": fact.memory_category,
        }
        for i, fact in enumerate(entry.runtime.memory_facts)
    ]

    system_prompt = (
        "你是记忆管理器。基于输入的记忆事实列表，执行以下操作：\n"
        "1. 检测重复/相似的事实并合并\n"
        "2. 为每个事实分配类别：identity（身份）、preference（偏好）、emotion（情绪）、event（事件）或 custom（自定义）\n"
        "3. 检测相互冲突的记忆（如：喜欢稳定 vs 喜欢创新）\n"
        "4. 输出结构化的汇聚结果为 JSON 数组，每个元素包含："
        "value、memory_category、is_duplicate、conflict_ids(冲突的id列表)、reason(说明)\n"
        "严格输出 JSON 数组，不要额外文本。"
    )

    task_input = f"记忆事实列表：{json.dumps(facts_json, ensure_ascii=False, indent=2)}"
    estimated_cost = estimate_tokens(system_prompt + task_input)

    used = task_token_usage.get(task_name, 0)

    request_payload = GenerationRequest(
        model=model,
        system_prompt=system_prompt,
        messages=[{"role": "user", "content": task_input}],
        temperature=float(config.orchestration.task_temperatures.get(task_name, 0.3)),
        max_tokens=int(config.orchestration.task_max_tokens.get(task_name, 512)),
        purpose=task_name,
    )

    retry_times = int(config.orchestration.task_retries.get(task_name, 0))
    try:
        raw = await call_with_retry(
            request_payload=request_payload,
            retry_times=retry_times,
            transcript=transcript,
            task_name=task_name,
            actor_id=participant.user_id,
        )
    except RuntimeError:
        record_task_stat(transcript, task_name, "failed_provider")
        return

    if retry_times > 0:
        record_task_stat(transcript, task_name, "retry_enabled")

    task_token_usage[task_name] = used + estimated_cost

    try:
        parsed_list = json.loads(raw)
        if not isinstance(parsed_list, list):
            record_task_stat(transcript, task_name, "failed_parse")
            return
    except (json.JSONDecodeError, ValueError):
        record_task_stat(transcript, task_name, "failed_parse")
        return

    duplicate_indices: set[int] = set()
    for result in parsed_list:
        if not isinstance(result, dict):
            continue
        if result.get("is_duplicate", False):
            value = str(result.get("value", "")).strip()
            for idx, fact in enumerate(entry.runtime.memory_facts):
                if fact.value == value and idx not in duplicate_indices:
                    duplicate_indices.add(idx)
                    break

    for i, fact in enumerate(entry.runtime.memory_facts):
        if i in duplicate_indices:
            continue
        for result in parsed_list:
            if str(result.get("value", "")).strip() == fact.value:
                fact.memory_category = str(result.get("memory_category", "custom")).strip() or "custom"
                fact.validated = True
                conflict_ids = result.get("conflict_ids", [])
                if isinstance(conflict_ids, list):
                    fact.conflict_with = [str(cid) for cid in conflict_ids]
                break

    entry.runtime.memory_facts = [
        fact for i, fact in enumerate(entry.runtime.memory_facts)
        if i not in duplicate_indices
    ]

    record_task_stat(transcript, task_name, "succeeded")
