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
TASK_MEMORY_MANAGER = "memory_manager"

# System prompts for task execution
TASK_MEMORY_EXTRACT_SYSTEM_PROMPT = (
    "你是用户画像提取器。请从输入中提取 JSON，并严格输出 JSON 对象，"
    "字段仅包含 inferred_persona(string)、inferred_traits(array[string])、"
    "inferred_aliases(array[string])、preference_tags(array[string])、summary_note(string)。"
)

TASK_EVENT_EXTRACT_SYSTEM_PROMPT = (
    "你是用户画像分析器。请分析参与者的对话消息并提取有长期参考价值的观察信息，"
    "严格输出 JSON 数组，每个元素包含 category(string: preference|trait|relationship|"
    "experience|emotion|goal)、content(string, 不超过50字)、"
    "confidence(float: 0.0-1.0)。如无有价值信息，返回 []。"
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
    budget: int
    system_prompt: str


def get_task_config(config: SessionConfig, task_name: str) -> TaskConfig:
    """Extract task configuration from session config.
    
    Args:
        config: Session configuration
        task_name: Name of the task
        
    Returns:
        TaskConfig with merged defaults
    """
    budget = int(config.orchestration.task_budgets.get(task_name, 0))
    return TaskConfig(
        enabled=config.orchestration.task_enabled.get(task_name, True),  # Use task_enabled dict
        model=config.orchestration.task_models.get(task_name, "").strip(),
        temperature=float(config.orchestration.task_temperatures.get(task_name, 0.1)),
        max_tokens=int(config.orchestration.task_max_tokens.get(task_name, 128)),
        retries=int(config.orchestration.task_retries.get(task_name, 0)),
        budget=budget,
        system_prompt="",  # Set by caller based on task type
    )


def get_system_prompt_for_task(task_name: str) -> str:
    """Get the default system prompt for a task."""
    prompts = {
        TASK_MEMORY_EXTRACT: TASK_MEMORY_EXTRACT_SYSTEM_PROMPT,
        TASK_EVENT_EXTRACT: TASK_EVENT_EXTRACT_SYSTEM_PROMPT,
        TASK_MEMORY_MANAGER: TASK_MEMORY_MANAGER_SYSTEM_PROMPT,
    }
    return prompts.get(task_name, "")
