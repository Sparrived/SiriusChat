"""多模型协作编排配置工具。

提供简易的 API 用于配置多模型任务路由、预算、模型参数等。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sirius_chat.config import OrchestrationPolicy, SessionConfig


@dataclass(slots=True)
class MultiModelConfig:
    """多模型协作配置对象。"""

    task_models: dict[str, str]  # {"memory_extract": "model_name", ...}
    task_budgets: dict[str, int]  # token 预算限制
    task_temperatures: dict[str, float] = None  # type: ignore
    task_max_tokens: dict[str, int] = None  # type: ignore
    task_retries: dict[str, int] = None  # type: ignore
    max_multimodal_inputs_per_turn: int = 4
    max_multimodal_value_length: int = 4096

    def __post_init__(self) -> None:
        if self.task_temperatures is None:
            self.task_temperatures = {}
        if self.task_max_tokens is None:
            self.task_max_tokens = {}
        if self.task_retries is None:
            self.task_retries = {}

    def to_orchestration_policy(self) -> OrchestrationPolicy:
        """转换为 OrchestrationPolicy 对象。"""
        return OrchestrationPolicy(
            unified_model="",  # 使用按任务配置模式
            task_models=self.task_models,
            task_budgets=self.task_budgets,
            task_temperatures=self.task_temperatures or {},
            task_max_tokens=self.task_max_tokens or {},
            task_retries=self.task_retries or {},
            max_multimodal_inputs_per_turn=self.max_multimodal_inputs_per_turn,
            max_multimodal_value_length=self.max_multimodal_value_length,
        )


def setup_multimodel_config(
    *,
    session_config: SessionConfig,
    task_models: dict[str, str],
    task_budgets: dict[str, int] | None = None,
    task_temperatures: dict[str, float] | None = None,
    task_max_tokens: dict[str, int] | None = None,
    task_retries: dict[str, int] | None = None,
    max_multimodal_inputs_per_turn: int = 4,
    max_multimodal_value_length: int = 4096,
) -> SessionConfig:
    """在现有会话配置中设置多模型编排。

    Args:
        session_config: 现有的 SessionConfig 对象
        task_models: 任务模型映射，例如 {"memory_extract": "model-1", "event_extract": "model-2"}
        task_budgets: 各任务的 token 预算，例如 {"memory_extract": 1200, "event_extract": 1000}
        task_temperatures: 各任务的采样温度，例如 {"memory_extract": 0.1}
        task_max_tokens: 各任务的最大 token 数，例如 {"memory_extract": 128}
        task_retries: 各任务的重试次数，例如 {"memory_extract": 1}
        max_multimodal_inputs_per_turn: 每轮最多多模态输入数（默认 4）
        max_multimodal_value_length: 多模态值最大长度（默认 4096）

    Returns:
        配置完成的 SessionConfig 对象（原对象已修改）

    Example:
        >>> from sirius_chat.api import SessionConfig, setup_multimodel_config
        >>> session = SessionConfig(...)
        >>> setup_multimodel_config(
        ...     session_config=session,
        ...     task_models={
        ...         "memory_extract": "doubao-seed-2-0-lite-260215",
        ...         "event_extract": "doubao-seed-2-0-lite-260215",
        ...     },
        ...     task_budgets={
        ...         "memory_extract": 1200,
        ...         "event_extract": 1000,
        ...     },
        ...     task_temperatures={
        ...         "memory_extract": 0.1,
        ...         "event_extract": 0.1,
        ...     },
        ... )
    """
    config = MultiModelConfig(
        task_models=task_models,
        task_budgets=task_budgets or {},
        task_temperatures=task_temperatures or {},
        task_max_tokens=task_max_tokens or {},
        task_retries=task_retries or {},
        max_multimodal_inputs_per_turn=max_multimodal_inputs_per_turn,
        max_multimodal_value_length=max_multimodal_value_length,
    )
    session_config.orchestration = config.to_orchestration_policy()
    return session_config


def create_multimodel_config(
    *,
    task_models: dict[str, str],
    task_budgets: dict[str, int] | None = None,
    task_temperatures: dict[str, float] | None = None,
    task_max_tokens: dict[str, int] | None = None,
    task_retries: dict[str, int] | None = None,
    max_multimodal_inputs_per_turn: int = 4,
    max_multimodal_value_length: int = 4096,
) -> MultiModelConfig:
    """创建多模型配置对象。

    返回 MultiModelConfig 对象，可以用于后续设置或转换为 OrchestrationPolicy。

    Args:
        task_models: 任务模型映射
        task_budgets: 任务预算限制
        task_temperatures: 任务采样温度
        task_max_tokens: 任务最大 token 数
        task_retries: 任务重试次数
        max_multimodal_inputs_per_turn: 最多多模态输入数
        max_multimodal_value_length: 多模态值最大长度

    Returns:
        MultiModelConfig 对象

    Example:
        >>> from sirius_chat.api import create_multimodel_config
        >>> mm_config = create_multimodel_config(
        ...     task_models={"memory_extract": "model-1"},
        ...     task_budgets={"memory_extract": 1200},
        ... )
        >>> orchestration = mm_config.to_orchestration_policy()
    """
    return MultiModelConfig(
        task_models=task_models,
        task_budgets=task_budgets or {},
        task_temperatures=task_temperatures or {},
        task_max_tokens=task_max_tokens or {},
        task_retries=task_retries or {},
        max_multimodal_inputs_per_turn=max_multimodal_inputs_per_turn,
        max_multimodal_value_length=max_multimodal_value_length,
    )


__all__ = [
    "MultiModelConfig",
    "setup_multimodel_config",
    "create_multimodel_config",
]
