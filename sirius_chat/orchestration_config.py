"""多模型协同配置工具。

提供便捷的配置函数，用于在运行时配置多模型协同参数。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from sirius_chat.models import SessionConfig
from sirius_chat.exceptions import OrchestrationConfigError


def configure_orchestration_models(
    config: SessionConfig,
    **task_models: str,
) -> SessionConfig:
    """为会话配置多模型协同的任务模型。
    
    这个函数允许外部代码在收到 OrchestrationConfigError 后动态添加模型配置。
    使用此函数时，会自动切换到按任务配置模式（task_models）。
    
    Args:
        config: 会话配置对象
        **task_models: 任务名称到模型名称的映射。
            支持的任务名：
            - memory_extract: 用户记忆提取
            - multimodal_parse: 多模态内容解析
            - event_extract: 事件提取
            - memory_manager: 记忆管理器
            
    Returns:
        更新后的 SessionConfig 对象（原对象被修改并返回）
        
    Example:
        >>> config = SessionConfig(...)
        >>> from sirius_chat.orchestration_config import configure_orchestration_models
        >>> config = configure_orchestration_models(
        ...     config,
        ...     memory_extract="gpt-4-mini",
        ...     event_extract="gpt-4-mini",
        ... )
    """
    if not config.orchestration:
        raise ValueError("config.orchestration 为 None，无法配置")
    
    # 合并新的任务模型配置
    updated_models = dict(config.orchestration.task_models)
    updated_models.update(task_models)
    
    # 当使用 task_models 时，清除 unified_model（两种模式互斥）
    # 创建新的 OrchestrationPolicy 对象
    updated_orchestration = replace(
        config.orchestration,
        unified_model="",  # 清除统一模型，切换到按任务配置模式
        task_models=updated_models,
    )
    
    # 创建并返回新的 SessionConfig
    updated_config = replace(
        config,
        orchestration=updated_orchestration,
    )
    
    return updated_config


def configure_orchestration_budgets(
    config: SessionConfig,
    **task_budgets: int,
) -> SessionConfig:
    """配置多模型协同任务的 token 预算。
    
    Args:
        config: 会话配置对象
        **task_budgets: 任务名称到 token 预算的映射
        
    Returns:
        更新后的 SessionConfig 对象
        
    Example:
        >>> config = configure_orchestration_budgets(
        ...     config,
        ...     memory_extract=1000,
        ...     event_extract=500,
        ... )
    """
    if not config.orchestration:
        raise ValueError("config.orchestration 为 None，无法配置")
    
    updated_budgets = dict(config.orchestration.task_budgets)
    updated_budgets.update(task_budgets)
    
    updated_orchestration = replace(
        config.orchestration,
        task_budgets=updated_budgets,
    )
    
    updated_config = replace(
        config,
        orchestration=updated_orchestration,
    )
    
    return updated_config


def configure_orchestration_temperatures(
    config: SessionConfig,
    **task_temperatures: float,
) -> SessionConfig:
    """配置多模型协同任务的采样温度。
    
    Args:
        config: 会话配置对象
        **task_temperatures: 任务名称到温度值（0.0-2.0）的映射
        
    Returns:
        更新后的 SessionConfig 对象
    """
    if not config.orchestration:
        raise ValueError("config.orchestration 为 None，无法配置")
    
    updated_temps = dict(config.orchestration.task_temperatures)
    updated_temps.update(task_temperatures)
    
    updated_orchestration = replace(
        config.orchestration,
        task_temperatures=updated_temps,
    )
    
    updated_config = replace(
        config,
        orchestration=updated_orchestration,
    )
    
    return updated_config


def configure_orchestration_retries(
    config: SessionConfig,
    **task_retries: int,
) -> SessionConfig:
    """配置多模型协同任务的重试次数。
    
    Args:
        config: 会话配置对象
        **task_retries: 任务名称到重试次数的映射
        
    Returns:
        更新后的 SessionConfig 对象
    """
    if not config.orchestration:
        raise ValueError("config.orchestration 为 None，无法配置")
    
    updated_retries = dict(config.orchestration.task_retries)
    updated_retries.update(task_retries)
    
    updated_orchestration = replace(
        config.orchestration,
        task_retries=updated_retries,
    )
    
    updated_config = replace(
        config,
        orchestration=updated_orchestration,
    )
    
    return updated_config


def configure_full_orchestration(
    config: SessionConfig,
    task_models: dict[str, str] | None = None,
    task_budgets: dict[str, int] | None = None,
    task_temperatures: dict[str, float] | None = None,
    task_retries: dict[str, int] | None = None,
    **extra_fields: Any,
) -> SessionConfig:
    """一次性配置多模型协同的所有参数。
    
    这是一个便捷方法，可以一次性设置多个配置字段。
    如果指定了 task_models，会自动切换到按任务配置模式（task_models）。
    
    Args:
        config: 会话配置对象
        task_models: 任务模型映射
        task_budgets: 任务预算映射
        task_temperatures: 任务温度映射
        task_retries: 任务重试次数映射
        **extra_fields: 其他 OrchestrationPolicy 字段（如 memory_manager_model）
        
    Returns:
        更新后的 SessionConfig 对象
        
    Example:
        >>> config = configure_full_orchestration(
        ...     config,
        ...     task_models={
        ...         "memory_extract": "gpt-4-mini",
        ...         "event_extract": "gpt-4-mini",
        ...     },
        ...     task_budgets={
        ...         "memory_extract": 1000,
        ...         "event_extract": 500,
        ...     },
        ...     task_temperatures={
        ...         "memory_extract": 0.1,
        ...     },
        ...     memory_manager_model="gpt-4",
        ... )
    """
    if not config.orchestration:
        raise ValueError("config.orchestration 为 None，无法配置")
    
    # 准备更新字段
    update_fields: dict[str, Any] = {}
    
    # 如果指定了 task_models，清除 unified_model（切换到按任务配置模式）
    if task_models is not None:
        merged_models = dict(config.orchestration.task_models)
        merged_models.update(task_models)
        update_fields["task_models"] = merged_models
        update_fields["unified_model"] = ""  # 清除统一模型
    
    if task_budgets is not None:
        merged_budgets = dict(config.orchestration.task_budgets)
        merged_budgets.update(task_budgets)
        update_fields["task_budgets"] = merged_budgets
    
    if task_temperatures is not None:
        merged_temps = dict(config.orchestration.task_temperatures)
        merged_temps.update(task_temperatures)
        update_fields["task_temperatures"] = merged_temps
    
    if task_retries is not None:
        merged_retries = dict(config.orchestration.task_retries)
        merged_retries.update(task_retries)
        update_fields["task_retries"] = merged_retries
    
    # 合并其他字段
    update_fields.update(extra_fields)
    
    # 创建新的 OrchestrationPolicy
    updated_orchestration = replace(
        config.orchestration,
        **update_fields,
    )
    
    # 创建并返回新的 SessionConfig
    updated_config = replace(
        config,
        orchestration=updated_orchestration,
    )
    
    return updated_config
