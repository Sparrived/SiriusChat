"""Chat context builder utilities extracted from AsyncRolePlayEngine.

Provides pure functions for:
- Multimodal input detection
- Chat model selection
- Memory metadata line filtering
- Chat request context construction
"""

from __future__ import annotations

import json
import logging
import re

from sirius_chat.config import SessionConfig
from sirius_chat.memory import SelfMemoryManager
from sirius_chat.models import Transcript
from sirius_chat.async_engine.prompts import build_system_prompt
from sirius_chat.core.markers import SKILL_RESULT_CHANNEL_MARKER

logger = logging.getLogger(__name__)

# ── Regex patterns for internal memory metadata line detection ──

_MEMORY_METADATA_LINE_PATTERNS = (
    re.compile(
        r"^\s*置信度\s*[：:]\s*\d+(?:\.\d+)?%\s*\|\s*类型\s*[：:]\s*[^|]+\|\s*来源\s*[：:]\s*[^|]+\|\s*时间\s*[：:]\s*[^|]+\|\s*内容\s*[：:]\s*.+$"
    ),
    re.compile(
        r"^\s*confidence\s*:\s*\d+(?:\.\d+)?%\s*\|\s*type\s*:\s*[^|]+\|\s*source\s*:\s*[^|]+\|\s*time\s*:\s*[^|]+\|\s*content\s*:\s*.+$",
        re.IGNORECASE,
    ),
)

_MEMORY_METADATA_CN_LABEL_PATTERNS = (
    re.compile(r"置信度\s*[：:]"),
    re.compile(r"类型\s*[：:]"),
    re.compile(r"来源\s*[：:]"),
    re.compile(r"时间\s*[：:]"),
    re.compile(r"内容\s*[：:]"),
)

_MEMORY_METADATA_EN_LABEL_PATTERNS = (
    re.compile(r"confidence\s*:", re.IGNORECASE),
    re.compile(r"type\s*:", re.IGNORECASE),
    re.compile(r"source\s*:", re.IGNORECASE),
    re.compile(r"time\s*:", re.IGNORECASE),
    re.compile(r"content\s*:", re.IGNORECASE),
)


def has_multimodal_inputs(transcript: Transcript) -> bool:
    """检测 transcript 中最后的用户消息是否包含多模态输入。

    Returns:
        True 如果最后的用户消息有多模态输入，否则 False
    """
    for message in reversed(transcript.messages):
        if message.role == "user":
            return bool(message.multimodal_inputs)
    return False


def get_model_for_chat(config: SessionConfig, transcript: Transcript) -> str:
    """根据是否有多模态输入，动态选择主模型。

    策略：
    - 如果最后用户消息有多模态输入，使用 multimodal_model（如果配置）
    - 否则使用默认的 agent.model

    Args:
        config: 会话配置
        transcript: 当前会话 transcript

    Returns:
        选定的模型名称
    """
    if has_multimodal_inputs(transcript):
        multimodal_model = config.agent.metadata.get("multimodal_model", "")
        if multimodal_model:
            return multimodal_model
    return config.agent.model


def is_internal_memory_metadata_line(line: str) -> bool:
    """判断一行文本是否是内部记忆元数据行，应在输出前过滤。"""
    stripped = line.strip()
    if not stripped:
        return False

    for pattern in _MEMORY_METADATA_LINE_PATTERNS:
        if pattern.match(stripped):
            return True

    if "|" not in stripped:
        return False

    cn_hits = sum(1 for p in _MEMORY_METADATA_CN_LABEL_PATTERNS if p.search(stripped))
    en_hits = sum(1 for p in _MEMORY_METADATA_EN_LABEL_PATTERNS if p.search(stripped))
    return cn_hits >= 2 or en_hits >= 2


def sanitize_assistant_content(content: str) -> str:
    """从 AI 回复中过滤掉内部记忆元数据行。"""
    if not content:
        return content

    cleaned_lines: list[str] = []
    for line in content.splitlines():
        if is_internal_memory_metadata_line(line):
            continue
        cleaned_lines.append(line)

    cleaned = "\n".join(cleaned_lines).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    if cleaned:
        return cleaned
    return "收到。"


def _extract_skill_result_channel(message_content: str) -> dict[str, object] | None:
    text = str(message_content or "").strip()
    marker_index = text.find(SKILL_RESULT_CHANNEL_MARKER)
    if marker_index == -1:
        return None

    payload_text = text[marker_index:]
    first_line, _, remainder = payload_text.partition("\n")
    if not first_line.startswith(SKILL_RESULT_CHANNEL_MARKER):
        return None
    closing = first_line.find("]", len(SKILL_RESULT_CHANNEL_MARKER))
    if closing == -1:
        return None
    skill_name = first_line[len(SKILL_RESULT_CHANNEL_MARKER):closing].strip()
    if not skill_name or not remainder.strip():
        return None
    try:
        parsed = json.loads(remainder)
    except (json.JSONDecodeError, ValueError):
        logger.warning("SKILL_RESULT_CHANNEL 解析失败: %s", skill_name)
        return None
    if not isinstance(parsed, dict):
        return None
    parsed.setdefault("skill_name", skill_name)
    return parsed


def _strip_skill_result_channel(message_content: str) -> str:
    text = str(message_content or "").strip()
    marker_index = text.find(SKILL_RESULT_CHANNEL_MARKER)
    if marker_index == -1:
        return text
    return text[:marker_index].strip()


def _build_skill_hidden_message(skill_channel: dict[str, object]) -> dict[str, object] | None:
    skill_name = str(skill_channel.get("skill_name", "SKILL")).strip() or "SKILL"
    display_text = str(skill_channel.get("display_text", "")).strip()
    text_blocks = skill_channel.get("text_blocks", [])
    multimodal_blocks = skill_channel.get("multimodal_blocks", [])

    instructions = [
        f"以下内容来自技能 {skill_name} 的内部结果，仅供你内部推理。",
        "最终回复中不要暴露内部 JSON 字段名、mime_type、label、internal_metadata、路径、URL 或其他工具元信息。",
        "只提炼对用户有帮助的结论、观察和结果。",
    ]
    text_fragments = ["\n".join(instructions)]

    if display_text:
        text_fragments.append(display_text)

    if isinstance(text_blocks, list):
        for item in text_blocks:
            if not isinstance(item, dict):
                continue
            value = str(item.get("value", "")).strip()
            if value and value not in text_fragments:
                text_fragments.append(value)

    content_parts: list[dict[str, object]] = [
        {"type": "text", "text": "\n\n".join(fragment for fragment in text_fragments if fragment.strip())}
    ]
    has_image = False
    if isinstance(multimodal_blocks, list):
        for item in multimodal_blocks:
            if not isinstance(item, dict):
                continue
            value = str(item.get("value", "")).strip()
            if not value:
                continue
            block_type = str(item.get("type", "")).strip().lower()
            mime_type = str(item.get("mime_type", "")).strip().lower()
            if block_type == "image" or mime_type.startswith("image/"):
                has_image = True
                content_parts.append(
                    {"type": "image_url", "image_url": {"url": value}}
                )

    if has_image:
        return {"role": "user", "content": content_parts}

    text_only = str(content_parts[0]["text"]).strip()
    if text_only:
        return {"role": "user", "content": text_only}
    return None


def collect_internal_system_notes(transcript: Transcript) -> str:
    """Collect all system-role messages from transcript as internal notes."""
    notes: list[str] = []
    for message in transcript.messages:
        if message.role != "system":
            continue
        text = _strip_skill_result_channel(message.content)
        if text:
            notes.append(text)
    if not notes:
        return ""
    return "\n".join(notes)


def build_chat_main_request_context(
    *,
    config: SessionConfig,
    transcript: Transcript,
    skill_descriptions: str = "",
    environment_context: str = "",
    skip_sections: list[str] | None = None,
    self_memory: SelfMemoryManager | None = None,
) -> tuple[str, list[dict[str, object]]]:
    """构建主聊天请求所需的系统提示词与消息历史。

    Args:
        config: 会话配置
        transcript: 当前会话 transcript
        skill_descriptions: 技能描述文本（注入系统提示词）
        environment_context: 环境上下文（注入系统提示词）
        skip_sections: 要跳过的系统提示词章节列表
        self_memory: 自我记忆管理器（可选）

    Returns:
        (system_prompt, chat_history) 元组
    """
    # Build self-memory prompt sections
    diary_section = ""
    glossary_section = ""
    if self_memory is not None and config.orchestration.enable_self_memory:
        recent_keywords: list[str] = []
        for msg in transcript.messages[-6:]:
            if msg.content.strip():
                recent_keywords.extend(msg.content[:100].split())
        diary_section = self_memory.build_diary_prompt_section(
            keywords=recent_keywords,
            max_entries=config.orchestration.self_memory_max_diary_prompt_entries,
        )
        recent_text = " ".join(
            msg.content[:200] for msg in transcript.messages[-6:] if msg.content.strip()
        )
        glossary_section = self_memory.build_glossary_prompt_section(
            text=recent_text,
            max_terms=config.orchestration.self_memory_max_glossary_prompt_terms,
        )

    system_prompt = build_system_prompt(
        config, transcript,
        skill_descriptions=skill_descriptions,
        environment_context=environment_context,
        skip_sections=skip_sections or [],
        diary_section=diary_section,
        glossary_section=glossary_section,
    )
    internal_notes = collect_internal_system_notes(transcript)
    if internal_notes:
        system_prompt = (
            f"{system_prompt}\n\n"
            "[会话内部系统补充]\n"
            "以下为引擎内部记录的系统上下文，用于辅助推理；"
            "请勿在最终回复中逐字复述。\n"
            f"{internal_notes}"
        )

    chat_history: list[dict[str, object]] = []

    # Narrow check: only look at the LAST user message group
    # (messages after the last assistant reply).
    _last_assistant_idx = -1
    for _idx, _msg in enumerate(transcript.messages):
        if _msg.role == "assistant":
            _last_assistant_idx = _idx
    current_batch_has_images = False
    for _msg in transcript.messages[_last_assistant_idx + 1:]:
        if _msg.role == "user":
            for _item in _msg.multimodal_inputs:
                if _item.get("type") == "image" and _item.get("value"):
                    current_batch_has_images = True
                    break
        if current_batch_has_images:
            break

    for index, message in enumerate(transcript.messages):
        role = str(message.role or "").strip().lower()
        if role == "system":
            if index > _last_assistant_idx:
                skill_channel = _extract_skill_result_channel(message.content)
                if skill_channel is not None:
                    hidden_message = _build_skill_hidden_message(skill_channel)
                    if hidden_message is not None:
                        chat_history.append(hidden_message)
            continue
        speaker_prefix = f"[{message.speaker}] " if message.speaker else ""
        text_content = f"{speaker_prefix}{message.content}"
        image_inputs = [
            item for item in message.multimodal_inputs
            if item.get("type") == "image" and item.get("value")
        ]
        if image_inputs and role == "user" and current_batch_has_images:
            content_parts: list[dict[str, object]] = [{"type": "text", "text": text_content}]
            for image in image_inputs:
                content_parts.append(
                    {"type": "image_url", "image_url": {"url": image["value"]}}
                )
            chat_history.append({"role": message.role, "content": content_parts})
        elif image_inputs and role == "user":
            desc_parts = [f"[图片: {img['value'][:60]}...]" for img in image_inputs]
            chat_history.append({
                "role": message.role,
                "content": f"{text_content}\n{'  '.join(desc_parts)}",
            })
        else:
            chat_history.append({"role": message.role, "content": text_content})

    # Safety guard: after compression, the current user message may have been
    # evicted from the transcript (e.g. by a very large skill-result system
    # message that inflated the char budget before the root-cause fix landed).
    # Ensure chat_history always contains at least one user message so that
    # API providers that enforce this constraint (e.g. Qwen-VL) don't reject the
    # request with "do not contain elements with the role of user".
    if chat_history and not any(d.get("role") == "user" for d in chat_history):
        for msg in reversed(transcript.messages):
            if str(msg.role or "").strip().lower() == "user" and msg.content.strip():
                chat_history.insert(0, {"role": "user", "content": msg.content})
                logger.warning(
                    "chat_history 不含 user 消息，已回填最近一条 user 消息以防 API 拒绝。"
                    " 请检查 history_max_chars 配置或技能返回内容是否过大。"
                )
                break

    return system_prompt, chat_history
