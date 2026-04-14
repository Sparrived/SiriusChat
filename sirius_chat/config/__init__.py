"""Configuration package for Sirius Chat.

Provides centralized configuration management for sessions, agents, and orchestration.
Exports all configuration-related classes and utilities.
"""

from __future__ import annotations

# Configuration data models
from sirius_chat.config.models import (
    Agent,
    AgentPreset,
    MemoryPolicy,
    OrchestrationPolicy,
    ProviderPolicy,
    SessionConfig,
    SessionDefaults,
    TokenUsageRecord,
    WorkspaceBootstrap,
    WorkspaceConfig,
)

# Configuration management
from sirius_chat.config.manager import ConfigManager

# Orchestration configuration utilities
from sirius_chat.config.helpers import (
    configure_full_orchestration,
    configure_orchestration_budgets,
    configure_orchestration_models,
    configure_orchestration_retries,
    configure_orchestration_temperatures,
    auto_configure_multimodal_agent,
    create_agent_with_multimodal,
)

__all__ = [
    # Models
    "Agent",
    "AgentPreset",
    "MemoryPolicy",
    "OrchestrationPolicy",
    "ProviderPolicy",
    "SessionConfig",
    "SessionDefaults",
    "TokenUsageRecord",
    "WorkspaceBootstrap",
    "WorkspaceConfig",
    # Management
    "ConfigManager",
    # Helpers
    "configure_full_orchestration",
    "configure_orchestration_budgets",
    "configure_orchestration_models",
    "configure_orchestration_retries",
    "configure_orchestration_temperatures",
    "auto_configure_multimodal_agent",
    "create_agent_with_multimodal",
]
