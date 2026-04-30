"""Sirius Chat — 支持多人格启用的异步角色扮演程序.

公开 API（直接从顶层导入）:
    from sirius_chat import EmotionalGroupChatEngine, Message, SessionConfig

本包不再提供 `sirius_chat.api` 子模块；所有公开符号均已平铺到顶层。
"""

from __future__ import annotations

# ── Core engine ──
from sirius_chat.core.emotional_engine import (
    EmotionalGroupChatEngine,
    create_emotional_engine,
)
from sirius_chat.core.events import SessionEvent, SessionEventBus, SessionEventType
from sirius_chat.core.identity_resolver import IdentityContext, IdentityResolver
from sirius_chat.core.model_router import ModelRouter, TaskConfig
from sirius_chat.core.proactive_trigger import ProactiveTrigger
from sirius_chat.core.response_assembler import ResponseAssembler, StyleAdapter, StyleParams
from sirius_chat.core.response_strategy import ResponseStrategyEngine
from sirius_chat.core.delayed_response_queue import DelayedResponseQueue
from sirius_chat.core.rhythm import RhythmAnalysis, RhythmAnalyzer
from sirius_chat.core.threshold_engine import ThresholdEngine

# ── Config / Models ──
from sirius_chat.config import (
    Agent,
    AgentPreset,
    MemoryPolicy,
    MultiModelConfig,
    OrchestrationPolicy,
    ProviderPolicy,
    SessionConfig,
    SessionDefaults,
    TokenUsageRecord,
    WorkspaceBootstrap,
    WorkspaceConfig,
)
from sirius_chat.config.helpers import (
    configure_full_orchestration,
    configure_orchestration_models,
    configure_orchestration_retries,
    configure_orchestration_temperatures,
    auto_configure_multimodal_agent,
    create_agent_with_multimodal,
    create_multimodel_config,
    setup_multimodel_config,
)
from sirius_chat.config.manager import ConfigManager

# ── Models ──
from sirius_chat.models import Message, Participant, Transcript, User
from sirius_chat.models.emotion import (
    AssistantEmotionState,
    EmotionState,
    EmpathyStrategy,
)
from sirius_chat.models.intent_v3 import IntentAnalysisV3, SocialIntent
from sirius_chat.models.response_strategy import ResponseStrategy, StrategyDecision

# ── Memory ──
from sirius_chat.memory.user.simple import UserProfile

# ── Providers ──
from sirius_chat.providers.base import AsyncLLMProvider, GenerationRequest, LLMProvider
from sirius_chat.providers import (
    AliyunBailianProvider,
    AutoRoutingProvider,
    BigModelProvider,
    MockProvider,
    OpenAICompatibleProvider,
    ProviderConfig,
    ProviderRegistry,
    SiliconFlowProvider,
    VolcengineArkProvider,
    WorkspaceProviderManager,
    ensure_provider_platform_supported,
    get_supported_provider_platforms,
    merge_provider_sources,
    normalize_provider_type,
    probe_provider_availability,
    register_provider_with_validation,
    run_provider_detection_flow,
)
from sirius_chat.providers.middleware import (
    CircuitBreakerMiddleware,
    CostMetricsMiddleware,
    Middleware,
    MiddlewareChain,
    MiddlewareContext,
    RateLimiterMiddleware,
    RetryMiddleware,
    TokenBucketRateLimiter,
)

# ── Session / Workspace ──
from sirius_chat.session.store import JsonSessionStore, SessionStoreFactory, SqliteSessionStore
from sirius_chat.workspace.layout import WorkspaceLayout
from sirius_chat.workspace.roleplay_manager import RoleplayWorkspaceManager
from sirius_chat.workspace.runtime import WorkspaceRuntime

# ── Skills ──
from sirius_chat.skills import (
    SkillDataStore,
    SkillDefinition,
    SkillExecutor,
    SkillInvocationContext,
    SkillParameter,
    SkillRegistry,
    SkillResult,
)

# ── Roleplay / Prompting ──
from sirius_chat.roleplay_prompting import (
    GENERATED_AGENTS_FILE_NAME,
    GENERATED_AGENT_TRACE_DIR_NAME,
    DependencyFileSnapshot,
    GeneratedSessionPreset,
    PersonaGenerationTrace,
    PersonaSpec,
    RolePlayAnswer,
    RolePlayQuestion,
    abuild_roleplay_prompt_from_answers_and_apply,
    agenerate_agent_prompts_from_answers,
    agenerate_from_persona_spec,
    aregenerate_agent_prompt_from_dependencies,
    aupdate_agent_prompt,
    create_session_config_from_selected_agent,
    generate_humanized_roleplay_questions,
    list_roleplay_question_templates,
    load_generated_agent_library,
    load_persona_generation_traces,
    load_persona_spec,
    persist_generated_agent_profile,
    select_generated_agent_profile,
)

# ── Token usage ──
from sirius_chat.token.store import TokenUsageStore
from sirius_chat.token.usage import (
    TokenUsageBaseline,
    build_token_usage_baseline,
    summarize_token_usage,
)
from sirius_chat.token.analytics import (
    AnalyticsReport,
    BaselineDict,
    BucketDict,
    TimeSliceDict,
    compute_baseline,
    full_report,
    group_by_actor,
    group_by_model,
    group_by_session,
    group_by_task,
    time_series,
)

# ── Background tasks ──
from sirius_chat.background_tasks import BackgroundTaskConfig, BackgroundTaskManager

# ── Exceptions ──
from sirius_chat.exceptions import (
    ConfigError,
    ConflictingMemoryError,
    ContentValidationError,
    InvalidConfigError,
    JSONParseError,
    MemoryError,
    MissingConfigError,
    ParseError,
    ProviderAuthError,
    ProviderConnectionError,
    ProviderError,
    ProviderResponseError,
    SiriusException,
    TokenBudgetExceededError,
    TokenError,
    TokenEstimationError,
    UserNotFoundError,
)

# ── Logging ──
from sirius_chat.logging_config import (
    LogFormat,
    LogLevel,
    configure_logging,
    get_logger,
)

__all__ = [
    # Core engine
    "EmotionalGroupChatEngine",
    "create_emotional_engine",
    "SessionEvent",
    "SessionEventBus",
    "SessionEventType",
    "IdentityResolver",
    "IdentityContext",
    "ModelRouter",
    "TaskConfig",
    "ProactiveTrigger",
    "ResponseAssembler",
    "StyleAdapter",
    "StyleParams",
    "ResponseStrategyEngine",
    "DelayedResponseQueue",
    "RhythmAnalysis",
    "RhythmAnalyzer",
    "ThresholdEngine",
    # Config
    "Agent",
    "AgentPreset",
    "MemoryPolicy",
    "MultiModelConfig",
    "OrchestrationPolicy",
    "ProviderPolicy",
    "SessionConfig",
    "SessionDefaults",
    "TokenUsageRecord",
    "WorkspaceBootstrap",
    "WorkspaceConfig",
    "ConfigManager",
    "configure_full_orchestration",
    "configure_orchestration_models",
    "configure_orchestration_retries",
    "configure_orchestration_temperatures",
    "auto_configure_multimodal_agent",
    "create_agent_with_multimodal",
    "create_multimodel_config",
    "setup_multimodel_config",
    # Models
    "Message",
    "Participant",
    "Transcript",
    "User",
    "EmotionState",
    "AssistantEmotionState",
    "EmpathyStrategy",
    "IntentAnalysisV3",
    "SocialIntent",
    "ResponseStrategy",
    "StrategyDecision",
    # Memory
    "UserProfile",
    # Providers
    "AliyunBailianProvider",
    "AsyncLLMProvider",
    "AutoRoutingProvider",
    "BigModelProvider",
    "LLMProvider",
    "MockProvider",
    "OpenAICompatibleProvider",
    "ProviderConfig",
    "ProviderRegistry",
    "SiliconFlowProvider",
    "VolcengineArkProvider",
    "WorkspaceProviderManager",
    "ensure_provider_platform_supported",
    "get_supported_provider_platforms",
    "merge_provider_sources",
    "normalize_provider_type",
    "probe_provider_availability",
    "register_provider_with_validation",
    "run_provider_detection_flow",
    # Middleware
    "Middleware",
    "MiddlewareChain",
    "MiddlewareContext",
    "RateLimiterMiddleware",
    "TokenBucketRateLimiter",
    "RetryMiddleware",
    "CircuitBreakerMiddleware",
    "CostMetricsMiddleware",
    # Session / Workspace
    "JsonSessionStore",
    "SessionStoreFactory",
    "SqliteSessionStore",
    "RoleplayWorkspaceManager",
    "WorkspaceLayout",
    "WorkspaceRuntime",
    # Skills
    "SkillDataStore",
    "SkillDefinition",
    "SkillExecutor",
    "SkillInvocationContext",
    "SkillParameter",
    "SkillRegistry",
    "SkillResult",
    # Roleplay
    "GENERATED_AGENTS_FILE_NAME",
    "GENERATED_AGENT_TRACE_DIR_NAME",
    "DependencyFileSnapshot",
    "GeneratedSessionPreset",
    "PersonaGenerationTrace",
    "PersonaSpec",
    "RolePlayAnswer",
    "RolePlayQuestion",
    "abuild_roleplay_prompt_from_answers_and_apply",
    "agenerate_agent_prompts_from_answers",
    "agenerate_from_persona_spec",
    "aregenerate_agent_prompt_from_dependencies",
    "aupdate_agent_prompt",
    "create_session_config_from_selected_agent",
    "generate_humanized_roleplay_questions",
    "list_roleplay_question_templates",
    "load_generated_agent_library",
    "load_persona_generation_traces",
    "load_persona_spec",
    "persist_generated_agent_profile",
    "select_generated_agent_profile",
    # Token
    "TokenUsageStore",
    "AnalyticsReport",
    "BaselineDict",
    "BucketDict",
    "TimeSliceDict",
    "TokenUsageBaseline",
    "build_token_usage_baseline",
    "compute_baseline",
    "full_report",
    "group_by_actor",
    "group_by_model",
    "group_by_session",
    "group_by_task",
    "summarize_token_usage",
    "time_series",
    # Background tasks
    "BackgroundTaskConfig",
    "BackgroundTaskManager",
    # Logging
    "LogFormat",
    "LogLevel",
    "configure_logging",
    "get_logger",
    # Exceptions
    "SiriusException",
    "ProviderError",
    "ProviderConnectionError",
    "ProviderAuthError",
    "ProviderResponseError",
    "TokenError",
    "TokenBudgetExceededError",
    "TokenEstimationError",
    "ParseError",
    "JSONParseError",
    "ContentValidationError",
    "ConfigError",
    "InvalidConfigError",
    "MissingConfigError",
    "MemoryError",
    "UserNotFoundError",
    "ConflictingMemoryError",
]

# 为一些缺少文档的导入项添加 docstring
AsyncLLMProvider.__doc__ = "Base class for LLM providers."
JsonSessionStore.__doc__ = "JSON-based session store for persisting sessions."
SqliteSessionStore.__doc__ = "SQLite-based session store for persisting sessions."
Middleware.__doc__ = "Base class for middleware components."
MiddlewareChain.__doc__ = "Chain of middleware components for request processing."
MiddlewareContext.__doc__ = "Context information for middleware execution."
abuild_roleplay_prompt_from_answers_and_apply.__doc__ = "Build roleplay prompt from user answers and apply it."
agenerate_agent_prompts_from_answers.__doc__ = "Generate agent prompts from user answers."
build_token_usage_baseline.__doc__ = "Build a baseline for token usage metrics."
create_session_config_from_selected_agent.__doc__ = "Create session configuration from a selected agent preset."
generate_humanized_roleplay_questions.__doc__ = "Generate humanized roleplay questions for user interaction."
list_roleplay_question_templates.__doc__ = "List available roleplay questionnaire templates."
load_generated_agent_library.__doc__ = "Load the generated agent profile library."
persist_generated_agent_profile.__doc__ = "Persist a generated agent profile to storage."
select_generated_agent_profile.__doc__ = "Select a generated agent profile from the library."
summarize_token_usage.__doc__ = "Summarize token usage statistics."
