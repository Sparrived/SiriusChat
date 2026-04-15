"""Engagement and reply-decision pipeline functions.

Extracted from ``AsyncRolePlayEngine`` to reduce the god-class size.
All dependencies are passed explicitly via keyword arguments.
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable

from sirius_chat.config import SessionConfig
from sirius_chat.models import Message, Participant, Transcript
from sirius_chat.providers.base import GenerationRequest
from sirius_chat.async_engine.utils import estimate_tokens, record_task_stat
from sirius_chat.async_engine.orchestration import TASK_INTENT_ANALYSIS
from sirius_chat.core.heat import HeatAnalysis, HeatAnalyzer
from sirius_chat.core.intent_v2 import IntentAnalysis, IntentAnalyzer

logger = logging.getLogger(__name__)

_CallWithRetry = Callable[..., Awaitable[str]]
_GetModel = Callable[[SessionConfig, str], str]


def build_heat_analysis(
    *,
    transcript: Transcript,
    config: SessionConfig,
    group_recent_count: int,
) -> HeatAnalysis:
    """构建热度分析所需的数据并执行分析。"""
    window = float(config.orchestration.heat_window_seconds)

    active_ids: set[str] = set()
    assistant_count = 0
    for msg in transcript.messages[-(group_recent_count + 10):]:
        if msg.role == "assistant":
            assistant_count += 1
        if msg.role == "user" and msg.speaker:
            active_ids.add(msg.speaker)

    return HeatAnalyzer.analyze(
        group_recent_count=group_recent_count,
        window_seconds=window,
        active_participant_ids=active_ids,
        assistant_reply_count_in_window=min(
            assistant_count,
            len(transcript.reply_runtime.assistant_reply_timestamps),
        ),
    )


async def run_engagement_intent_analysis(
    *,
    config: SessionConfig,
    transcript: Transcript,
    participant: Participant,
    content: str,
    task_token_usage: dict[str, int],
    call_with_retry: _CallWithRetry,
    get_model: _GetModel,
) -> IntentAnalysis | None:
    """执行新版意图分析（携带参与者上下文）。"""
    agent_alias = str(config.agent.metadata.get("alias", "")).strip()
    task_name = TASK_INTENT_ANALYSIS

    participant_names: list[str] = []
    seen: set[str] = set()
    for msg in reversed(transcript.messages[-20:]):
        if msg.role == "user" and msg.speaker and msg.speaker not in seen:
            seen.add(msg.speaker)
            participant_names.append(msg.speaker)

    if not config.orchestration.is_task_enabled(task_name):
        return IntentAnalyzer.fallback_analysis(
            content, config.agent.name, agent_alias, participant_names,
        )

    model = get_model(config, task_name)

    recent_messages: list[dict[str, str]] = []
    for msg in transcript.messages[-8:]:
        if msg.role in ("user", "assistant"):
            entry: dict[str, str] = {"role": msg.role, "content": msg.content}
            if msg.speaker:
                entry["speaker"] = msg.speaker
            recent_messages.append(entry)

    request_payload = IntentAnalyzer.build_request(
        content=content,
        agent_name=config.agent.name,
        agent_alias=agent_alias,
        participant_names=participant_names,
        recent_messages=recent_messages,
        model=model,
        temperature=float(config.orchestration.task_temperatures.get(task_name, 0.1)),
        max_tokens=int(config.orchestration.task_max_tokens.get(task_name, 192)),
    )

    prompt_text = request_payload.system_prompt + "\n" + "\n".join(
        str(item.get("content", "")) for item in request_payload.messages
    )
    estimated_cost = estimate_tokens(prompt_text)
    used = task_token_usage.get(task_name, 0)

    record_task_stat(transcript, task_name, "attempted")
    retry_times = int(config.orchestration.task_retries.get(task_name, 0))
    try:
        raw = await call_with_retry(
            request_payload=request_payload,
            retry_times=retry_times,
            transcript=transcript,
            task_name=task_name,
            actor_id=participant.user_id,
        )
    except RuntimeError as exc:
        record_task_stat(transcript, task_name, "failed_provider")
        logger.warning("意图分析任务调用失败，放弃本轮意图推断: %s", exc)
        return None

    if retry_times > 0:
        record_task_stat(transcript, task_name, "retry_enabled")
    parsed = IntentAnalyzer._parse_response(raw)
    if parsed is None:
        record_task_stat(transcript, task_name, "failed_parse")
        logger.warning("意图分析任务响应无法解析，放弃本轮意图推断。")
        return None
    task_token_usage[task_name] = used + estimated_cost
    record_task_stat(transcript, task_name, "succeeded")
    return parsed


def should_reply_for_turn(turn: Message) -> bool:
    """Check reply_mode: never → False, otherwise → True."""
    mode = str(getattr(turn, "reply_mode", "always") or "always").strip().lower()
    return mode not in {"never", "silent", "none", "no_reply"}
