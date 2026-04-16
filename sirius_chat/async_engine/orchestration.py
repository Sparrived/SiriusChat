"""Task orchestration configuration and helpers for async engine.

This module provides task definitions, configuration management, and 
orchestration utilities for the async engine's multi-task coordination.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sirius_chat.config import SessionConfig


# Task identifiers
TASK_MEMORY_EXTRACT = "memory_extract"
TASK_EVENT_EXTRACT = "event_extract"
TASK_INTENT_ANALYSIS = "intent_analysis"
TASK_MEMORY_MANAGER = "memory_manager"

# System prompts for task execution
TASK_MEMORY_EXTRACT_SYSTEM_PROMPT = (
    "你是用户画像提取器。只提取当前说话者自己的稳定信息，并严格输出 JSON 对象，"
    "字段仅包含 inferred_persona(string)、inferred_traits(array[string])、"
    "inferred_aliases(array[string])、preference_tags(array[string])、summary_note(string)。"
    "规则：1) inferred_aliases 必须极度保守，只有当前说话者明确自称且不与 strong_identity/trusted_labels 冲突时才可填写；"
    "2) 不得把第三方称呼、引用、群聊玩笑、临时昵称或他人冒充内容写成别名；"
    "3) 若不确定，inferred_aliases 返回空数组；"
    "4) summary_note 只保留对当前说话者长期有参考价值的信息，不要写其他人的事实。"
)

TASK_EVENT_EXTRACT_SYSTEM_PROMPT = (
    "你是用户画像分析器。请分析参与者的对话消息并提取有长期参考价值的观察信息，"
    "严格输出 JSON 数组，每个元素包含 category(string: preference|trait|relationship|"
    "experience|emotion|goal)、content(string, 不超过50字)、"
    "confidence(float: 0.0-1.0)。如无有价值信息，返回 []。"
)

TASK_INTENT_ANALYSIS_SYSTEM_PROMPT = (
    "你是一个对话意图分析器。你的任务是分析群聊中的每条消息，判断说话者在跟谁对话、意图是什么。\n"
    "严格输出 JSON 对象：\n"
    "{\n"
    '  "intent_type": "question|request|chat|reaction|information_share|command",\n'
    '  "target": "ai|others|everyone|unknown",\n'
    '  "target_scope": "self_ai|other_ai|human|everyone|unknown",\n'
    '  "importance": float(0-1),\n'
    '  "needs_memory": bool,\n'
    '  "needs_summary": bool,\n'
    '  "reason": "一句话解释你的判断依据",\n'
    '  "evidence_span": "从原消息中摘取的关键短语"\n'
    "}\n\n"
    "判断指南：\n"
    "- target=ai 且 target_scope=self_ai：只有在消息明确点名当前助手名字/别名，或有非常强的上下文承接证据表明用户正在直接回应当前助手上一轮发言时，才可使用 self_ai\n"
    "- target=ai 且 target_scope=other_ai：消息明确指向群内其他 AI，或点名了名字上带明显 AI 线索的其他对象\n"
    "- target=others 且 target_scope=human：消息明确指向群内其他参与者\n"
    "- target=everyone：消息面向全体（公告、一般感叹、分享信息）\n"
    "- target=unknown：无法确定指向\n\n"
    "重要规则：\n"
    "- 仅凭\"你\"字不能判定指向AI，必须结合上下文确认\n"
    "- 判断 self_ai 时，当前助手名字/别名命中是最强依据；不要把泛指 AI/机器人/助手 的话自动算成 self_ai\n"
    "- 如果只知道消息指向某个 AI，但不能可靠区分是当前助手还是其他 AI，target_scope 应优先返回 other_ai 或 unknown，不要勉强写 self_ai\n"
    "- 如果上一条消息是某个人说的，当前消息可能在回复那个人而非AI\n"
    "- 当群聊中有多人对话时，要根据话题连续性判断说话对象\n"
    "- 当证据不足时，宁可给出 other_ai 或 unknown，也不要轻易判成 self_ai\n"
    "- 不要输出任何额外文字\n"
)

TASK_MEMORY_MANAGER_SYSTEM_PROMPT = (
    "你是记忆管理器。请管理用户记忆，输出 JSON 对象，"
    "字段仅包含 action(string: 'add'/'update'/'remove')、"
    "target_id(string)、memory_content(string)。"
)

SUPPORTED_MULTIMODAL_TYPES = {"image", "video", "audio", "text"}


@dataclass(slots=True)
class TaskConfig:
    """Configuration for a single orchestration task."""
    
    enabled: bool
    model: str
    temperature: float
    max_tokens: int
    retries: int
    system_prompt: str


def get_task_config(config: SessionConfig, task_name: str) -> TaskConfig:
    """Extract task configuration from session config.
    
    Args:
        config: Session configuration
        task_name: Name of the task
        
    Returns:
        TaskConfig with merged defaults
    """
    default_max_tokens = 192 if task_name == TASK_INTENT_ANALYSIS else 128
    return TaskConfig(
        enabled=config.orchestration.is_task_enabled(task_name),
        model=config.orchestration.resolve_model_for_task(
            task_name,
            default_model=config.agent.model if task_name == TASK_INTENT_ANALYSIS else "",
        ),
        temperature=float(config.orchestration.task_temperatures.get(task_name, 0.1)),
        max_tokens=int(config.orchestration.task_max_tokens.get(task_name, default_max_tokens)),
        retries=int(config.orchestration.task_retries.get(task_name, 0)),
        system_prompt="",  # Set by caller based on task type
    )


def get_system_prompt_for_task(task_name: str) -> str:
    """Get the default system prompt for a task."""
    prompts = {
        TASK_MEMORY_EXTRACT: TASK_MEMORY_EXTRACT_SYSTEM_PROMPT,
        TASK_EVENT_EXTRACT: TASK_EVENT_EXTRACT_SYSTEM_PROMPT,
        TASK_INTENT_ANALYSIS: TASK_INTENT_ANALYSIS_SYSTEM_PROMPT,
        TASK_MEMORY_MANAGER: TASK_MEMORY_MANAGER_SYSTEM_PROMPT,
    }
    return prompts.get(task_name, "")
