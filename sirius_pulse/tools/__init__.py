"""Tool system for Sirius Chat.

Provides a mechanism for AI agents to invoke external code through
structured tool definitions. Tools are loaded from the work_path/tools/
directory and presented to the AI as callable tools.

Key components:
- ToolDefinition / ToolParameter / ToolResult: Data models
- ToolChainContext: Per-round chain context for multi-tool pipelines
- ToolRegistry: Discovers, loads, and manages tools
- ToolExecutor: Safely executes tools with parameter validation
- ToolDataStore: Persistent key-value storage for tools
"""

# Re-export config builder utilities for tool developers
from sirius_pulse.config.config_builder import (
    ConfigBuilder,
    ParamDefinition,
    build_parameters_from_class,
    config_param,
    secret,
)
from sirius_pulse.tools.data_store import ToolDataStore
from sirius_pulse.tools.dependency_resolver import resolve_tool_dependencies
from sirius_pulse.tools.executor import ToolExecutor
from sirius_pulse.tools.models import (
    BackgroundTaskSpec,
    ToolChainContext,
    ToolDefinition,
    ToolEngineContext,
    ToolInvocationContext,
    ToolParameter,
    ToolPassiveType,
    ToolResult,
    ToolSideEffect,
    TriggerSpec,
)
from sirius_pulse.tools.registry import ToolRegistry

__all__ = [
    "BackgroundTaskSpec",
    "ToolDefinition",
    "ToolEngineContext",
    "ToolInvocationContext",
    "ToolParameter",
    "ToolPassiveType",
    "ToolResult",
    "ToolSideEffect",
    "ToolChainContext",
    "TriggerSpec",
    "ToolRegistry",
    "ToolExecutor",
    "ToolDataStore",
    "resolve_tool_dependencies",
    # Config builder utilities
    "ConfigBuilder",
    "ParamDefinition",
    "config_param",
    "secret",
    "build_parameters_from_class",
]
