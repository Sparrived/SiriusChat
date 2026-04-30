# Sirius Chat API 文档

自动生成的 Python API 参考文档（基于 `sirius_chat` 顶层公开导出）。

## 模块索引

- [background_tasks](#background_tasks)
- [config.helpers](#config-helpers)
- [config.manager](#config-manager)
- [config.models](#config-models)
- [core.delayed_response_queue](#core-delayed_response_queue)
- [core.emotional_engine](#core-emotional_engine)
- [core.events](#core-events)
- [core.identity_resolver](#core-identity_resolver)
- [core.model_router](#core-model_router)
- [core.proactive_trigger](#core-proactive_trigger)
- [core.response_assembler](#core-response_assembler)
- [core.response_strategy](#core-response_strategy)
- [core.rhythm](#core-rhythm)
- [core.threshold_engine](#core-threshold_engine)
- [exceptions](#exceptions)
- [logging_config](#logging_config)
- [memory.user.models](#memory-user-models)
- [models.emotion](#models-emotion)
- [models.intent_v3](#models-intent_v3)
- [models.models](#models-models)
- [models.response_strategy](#models-response_strategy)
- [providers.aliyun_bailian](#providers-aliyun_bailian)
- [providers.base](#providers-base)
- [providers.bigmodel](#providers-bigmodel)
- [providers.middleware.base](#providers-middleware-base)
- [providers.middleware.cost_metrics](#providers-middleware-cost_metrics)
- [providers.middleware.rate_limiter](#providers-middleware-rate_limiter)
- [providers.middleware.retry](#providers-middleware-retry)
- [providers.mock](#providers-mock)
- [providers.openai_compatible](#providers-openai_compatible)
- [providers.routing](#providers-routing)
- [providers.siliconflow](#providers-siliconflow)
- [providers.volcengine_ark](#providers-volcengine_ark)
- [public](#public)
- [roleplay_prompting](#roleplay_prompting)
- [session.store](#session-store)
- [skills.data_store](#skills-data_store)
- [skills.executor](#skills-executor)
- [skills.models](#skills-models)
- [skills.registry](#skills-registry)
- [token.analytics](#token-analytics)
- [token.store](#token-store)
- [token.usage](#token-usage)
- [typing](#typing)
- [workspace.layout](#workspace-layout)
- [workspace.roleplay_manager](#workspace-roleplay_manager)
- [workspace.runtime](#workspace-runtime)

---

## background_tasks

### Classes

#### `BackgroundTaskConfig`

后台任务配置

#### `BackgroundTaskManager`

轻量级后台任务管理器

用于运行异步定时任务，如内存压缩、数据清理、记忆归纳等。
基于asyncio.create_task，不引入额外依赖。

**方法：**

- `is_running(self) -> bool` - 检查后台任务是否在运行
- `set_consolidation_callback(self, callback: Callable[[], Awaitable[None]]) -> None` - 设置记忆归纳回调函数（异步）。
- `set_memory_compressor_callback(self, callback: Callable[[str], None]) -> None` - 设置内存压缩回调函数。
- `set_self_memory_callback(self, callback: Callable[[], Awaitable[None]]) -> None` - 设置 AI 自身记忆提取回调函数（异步）。
- `set_transient_cleanup_callback(self, callback: Callable[[str], None]) -> None` - 设置临时数据清理回调函数。
- `async start(self) -> None` - 启动所有启用的后台任务
- `async stop(self) -> None` - 停止所有后台任务
- `async trigger_cleanup_now(self, user_id: str) -> None` - 立即触发一次临时数据清理
- `async trigger_compression_now(self, user_id: str) -> None` - 立即触发一次内存压缩
- `async trigger_consolidation_now(self) -> None` - 立即触发一次记忆归纳
- `async trigger_self_memory_now(self) -> None` - 立即触发一次 AI 自身记忆提取


---

## config.helpers

### Functions

#### `configure_full_orchestration(config: SessionConfig, task_models: dict[str, str] | None, task_temperatures: dict[str, float] | None, task_retries: dict[str, int] | None, extra_fields: Any) -> SessionConfig`

一次性配置多模型协同的所有参数。

这是一个便捷方法，可以一次性设置多个配置字段。
如果指定了 task_models，会自动切换到按任务配置模式（task_models）。

Args:
    config: 会话配置对象
    task_models: 任务模型映射
    task_temperatures: 任务温度映射
    task_retries: 任务重试次数映射
    **extra_fields: 其他 OrchestrationPolicy 字段（如 pending_message_threshold）
    
Returns:
    更新后的 SessionConfig 对象
    
Example:
    >>> config = configure_full_orchestration(
    ...     config,
    ...     task_models={
    ...         "memory_extract": "gpt-4-mini",
    ...         "event_extract": "gpt-4-mini",
    ...     },
    ...     task_temperatures={
    ...         "memory_extract": 0.1,
    ...     },
    ...     pending_message_threshold=0,
    ... )

#### `configure_orchestration_models(config: SessionConfig, task_models: str) -> SessionConfig`

为会话配置多模型协同的任务模型。

这个函数允许外部代码在收到 OrchestrationConfigError 后动态添加模型配置。
使用此函数时，会自动切换到按任务配置模式（task_models）。

Args:
    config: 会话配置对象
    **task_models: 任务名称到模型名称的映射。
        支持的任务名：
        - memory_extract: 用户记忆提取
        - event_extract: 事件提取
        - intent_analysis: 意图分析
        - memory_manager: 记忆整理与后台归纳
        
Returns:
    更新后的 SessionConfig 对象（原对象被修改并返回）
    
Example:
    >>> config = SessionConfig(...)
    >>> from sirius_chat.config import configure_orchestration_models
    >>> config = configure_orchestration_models(
    ...     config,
    ...     memory_extract="gpt-4-mini",
    ...     event_extract="gpt-4-mini",
    ... )

#### `configure_orchestration_retries(config: SessionConfig, task_retries: int) -> SessionConfig`

配置多模型协同任务的重试次数。

Args:
    config: 会话配置对象
    **task_retries: 任务名称到重试次数的映射
    
Returns:
    更新后的 SessionConfig 对象

#### `configure_orchestration_temperatures(config: SessionConfig, task_temperatures: float) -> SessionConfig`

配置多模型协同任务的采样温度。

Args:
    config: 会话配置对象
    **task_temperatures: 任务名称到温度值（0.0-2.0）的映射
    
Returns:
    更新后的 SessionConfig 对象

#### `auto_configure_multimodal_agent(agent: Agent, multimodal_model: str | None) -> Agent`

为 Agent 配置多模态模型（如果有图片输入时使用）。

不进行自动推断，而是要求用户显式指定或在 Agent.metadata 中设置。
这样可以兼容各种平台（有些平台可能没有 vision 版本）。

Args:
    agent: AI Agent 配置对象
    multimodal_model: 多模态模型名称（可选）。如果提供，将覆盖 agent.metadata 中的设置。
                     如果不提供，将检查 agent.metadata 中是否已有配置。
    
Returns:
    更新后的 Agent 对象（原对象被修改）
    
Example:
    >>> agent = Agent(name="Assistant", persona="helpful", model="gpt-4o-mini")
    >>> agent = auto_configure_multimodal_agent(agent, multimodal_model="gpt-4o")
    >>> agent.metadata["multimodal_model"]
    'gpt-4o'

#### `create_agent_with_multimodal(name: str, persona: str, model: str, multimodal_model: str, temperature: float, max_tokens: int, metadata: Any) -> Agent`

便捷函数：一次性创建带有多模态模型的 Agent。

Args:
    name: Agent 名称
    persona: Agent 人设
    model: 主模型名称
    multimodal_model: 多模态模型名称（当有图片输入时使用）
    temperature: 温度参数
    max_tokens: 最大输出 token 数
    **metadata: 其他元数据
    
Returns:
    已配置多模态模型的 Agent 对象
    
Example:
    >>> agent = create_agent_with_multimodal(
    ...     name="Assistant",
    ...     persona="helpful",
    ...     model="gpt-4o-mini",
    ...     multimodal_model="gpt-4o",
    ... )

#### `create_multimodel_config(task_models: dict[str, str], task_temperatures: dict[str, float] | None, task_max_tokens: dict[str, int] | None, task_retries: dict[str, int] | None, max_multimodal_inputs_per_turn: int, max_multimodal_value_length: int) -> MultiModelConfig`

创建多模型配置对象。

#### `setup_multimodel_config(session_config: SessionConfig, task_models: dict[str, str], task_temperatures: dict[str, float] | None, task_max_tokens: dict[str, int] | None, task_retries: dict[str, int] | None, max_multimodal_inputs_per_turn: int, max_multimodal_value_length: int) -> SessionConfig`

在现有会话配置中设置多模型编排。


---

## config.manager

### Classes

#### `ConfigManager`

Manages configuration loading, validation, and merging.

**方法：**

- `bootstrap_workspace_from_legacy_session_json(self, config_path: Path | str, work_path: Path | str, data_path: Path | str | None) -> tuple[WorkspaceConfig, list[dict[str, Any]]]` - Bootstrap workspace config from a legacy session.json file.
- `build_session_config(self, work_path: Path | str, data_path: Path | str | None, session_id: str, overrides: dict[str, Any] | None) -> SessionConfig` - Build a runtime SessionConfig from workspace config + roleplay assets.
- `load_from_env(self, env: str) -> SessionConfig` - Load configuration for a specific environment.
- `load_from_json(self, path: Path | str) -> SessionConfig` - Load configuration from JSON file.
- `load_workspace_config(self, work_path: Path | str, data_path: Path | str | None) -> WorkspaceConfig` - Load workspace-level config, creating defaults when missing.
- `merge_configs(self, base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]` - Merge two configuration dictionaries.
- `save_workspace_config(self, work_path: Path | str, config: WorkspaceConfig, data_path: Path | str | None) -> None` - Persist workspace-level config and a human-readable session snapshot.


---

## config.models

### Classes

#### `Agent`

AI agent definition with model and parameters.

#### `AgentPreset`

Pre-configured agent with system prompt.

#### `MemoryPolicy`

Centralized memory system configuration.

Controls memory fact limits, confidence thresholds, decay behaviour,
observed-set caps and prompt-injection budget.

#### `MultiModelConfig`

多模型协作配置对象。

**方法：**

- `to_dict(self) -> dict[str, Any]`
- `to_orchestration_policy(self) -> OrchestrationPolicy` - 转换为 OrchestrationPolicy 对象。

#### `OrchestrationPolicy`

Multi-model orchestration strategy (required).

Supports two configuration approaches:

Approach 1 - Unified Model: all tasks use the same model
    - Set unified_model: model name
    - Simplifies configuration, suitable for small task volumes
    
Approach 2 - Per-Task Configuration: specify model for each task
    - Set task_models: {"memory_extract": "model-a", "event_extract": "model-b", ...}
    - Supports fine-grained task-level control

Task Enablement:
    - All tasks (memory_extract, event_extract, intent_analysis) enabled by default
    - Use task_enabled dict to enable/disable specific tasks
    - Example: task_enabled={"memory_extract": False} disables memory extraction tasks

**方法：**

- `is_task_enabled(self, task_name: str) -> bool`
- `resolve_model_for_task(self, task_name: str, default_model: str) -> str`
- `validate(self) -> None` - Validate configuration legitimacy.

#### `ProviderPolicy`

Workspace-level provider bootstrap policy.

#### `SessionConfig`

Session configuration including agent, paths, and orchestration policy.

#### `SessionDefaults`

Workspace-level defaults used to build SessionConfig instances.

#### `TokenUsageRecord`

Record of token usage for a task execution.

**方法：**

- `to_dict(self) -> dict[str, Any]` - Serialise to a plain dict (recursively via ``dataclasses.asdict``).

#### `WorkspaceBootstrap`

Host-provided defaults injected at workspace open time.

The host (plugin / CLI) fills in the fields it cares about; the runtime
decides how to merge them into the workspace and whether to persist.

#### `WorkspaceConfig`

Persisted workspace-level configuration source.

**方法：**

- `to_dict(self) -> dict[str, Any]`

#### `GeneratedSessionPreset`

Pre-configured agent with system prompt.


---

## core.delayed_response_queue

### Classes

#### `DelayedResponseQueue`

Queue for DELAYED and IMMEDIATE strategy responses.

**方法：**

- `cancel_all_for_user(self, group_id: str, user_id: str) -> int` - Cancel all pending items for a user in a group.
- `clear_group(self, group_id: str) -> None` - Clear all items for a group.
- `enqueue(self, group_id: str, user_id: str, message_content: str, strategy_decision: StrategyDecision, emotion_state: dict[str, Any] | None, candidate_memories: list[str] | None, channel: str | None, channel_user_id: str | None, multimodal_inputs: list[dict[str, str]] | None, adapter_type: str | None, heat_level: str, pace: str) -> DelayedResponseItem` - Add an item to the delayed queue.
- `get_pending(self, group_id: str) -> list[DelayedResponseItem]` - Get all pending items for a group.
- `tick(self, group_id: str, recent_messages: list[dict[str, Any]], rhythm: RhythmAnalysis | None) -> list[DelayedResponseItem]` - Process queue for a group based on recent conversation.


---

## core.emotional_engine

### Classes

#### `EmotionalGroupChatEngine`

Next-generation engine for emotional group chat (v0.28+).

**方法：**

- `is_proactive_enabled(self, group_id: str) -> bool` - Check if proactive triggers are enabled for a group.
- `load_state(self) -> None` - Restore runtime state from disk.
- `pop_developer_chats(self, group_id: str) -> list[str]` - Pop pending proactive developer chats for a group.
- `pop_reminders(self, group_id: str, adapter_type: str | None) -> list[str]` - Pop pending reminder messages for a group.
- `async proactive_check(self, group_id: str, _now: datetime | None) -> dict[str, Any] | None` - Check if proactive trigger should fire for a group.
- `async process_message(self, message: Message, participants: list[Participant], group_id: str) -> dict[str, Any]` - Process a single incoming message through the full pipeline.
- `save_state(self) -> None` - Persist all runtime state to disk.
- `set_proactive_enabled(self, group_id: str, enabled: bool) -> None` - Enable or disable proactive triggers for a specific group.
- `set_skill_runtime(self, skill_registry: Any | None, skill_executor: Any | None) -> None` - Attach SKILL registry and executor to the engine.
- `start_background_tasks(self) -> None` - Start periodic background tasks for delayed queue, proactive triggers,
- `stop_background_tasks(self) -> None` - Cancel all background tasks.
- `async tick_delayed_queue(self, group_id: str, on_partial_reply: Callable[[str], Any] | None) -> list[dict[str, Any]]` - Process delayed response queue for a group.

### Functions

#### `create_emotional_engine(work_path: Any, provider: Any | None, persona: Any | None, config: dict[str, Any] | None) -> 'EmotionalGroupChatEngine'`

Factory for EmotionalGroupChatEngine (v0.28+).

Args:
    work_path: Workspace path for persistence.
    provider: Optional LLM provider for async generation tasks.
    persona: Optional PersonaProfile or string archetype name.
    config: Optional engine configuration dict.

Returns:
    Configured EmotionalGroupChatEngine instance.


---

## core.events

### Classes

#### `SessionEvent`

A single event emitted by the engine during session processing.

Attributes:
    type: The category of the event.
    message: The ``Message`` object, present for message-related events.
    data: Arbitrary metadata (e.g. skill name, error details).
    timestamp: Unix timestamp when the event was created.

#### `SessionEventBus`

Per-session event bus supporting multiple concurrent subscribers.

Usage::

    bus = SessionEventBus()
    # Subscribe
    async for event in bus.subscribe():
        handle(event)

    # Publish (from engine internals)
    await bus.emit(SessionEvent(type=SessionEventType.PERCEPTION_COMPLETED, data={"message": msg}))

    # Close when the session ends
    await bus.close()

**方法：**

- `async close(self) -> None` - Signal all subscribers to stop and clear the subscriber list.
- `async emit(self, event: SessionEvent) -> None` - Publish an event to all current subscribers.
- `subscribe(self, max_queue_size: int) -> AsyncIterator[SessionEvent]` - Return an async iterator that yields events as they arrive.

#### `SessionEventType`

Categories of events emitted during session processing.


---

## core.identity_resolver

### Classes

#### `IdentityResolver`

Resolves IdentityContext into framework UserProfiles without
hard-coding any platform-specific logic.

**方法：**

- `resolve(self, ctx: IdentityContext, user_manager: UserManager, group_id: str) -> UserProfile` - Resolve or create a user profile from identity context.

#### `IdentityContext`

Platform-agnostic identity context provided by external callers (plugins).

Attributes:
    speaker_name: Human-readable display name.
    user_id: Framework-unified user ID (if already bound).
    platform_uid: Platform-native UID (e.g. QQ number, Discord ID).
    platform: Platform identifier (e.g. "qq", "discord", "wechat").
    is_developer: Whether this user has developer privileges.


---

## core.model_router

### Classes

#### `ModelRouter`

Routes cognitive tasks to appropriate LLM configurations.

Usage::

    router = ModelRouter()
    cfg = router.resolve("response_generate", urgency=85)
    # cfg.model_name == "gpt-4o" (escalated from default)

**方法：**

- `get_fallback(self, task_name: str) -> TaskConfig | None` - Get fallback config for a task.
- `list_tasks(self) -> list[str]` - Return all registered task names.
- `resolve(self, task_name: str, urgency: int, heat_level: str, user_communication_style: str) -> TaskConfig` - Resolve the best config for a task, considering urgency and context.

#### `TaskConfig`

Configuration for a specific cognitive task.


---

## core.proactive_trigger

### Classes

#### `ProactiveTrigger`

Decides when to proactively initiate conversation.

**方法：**

- `check(self, group_id: str, last_message_at: str | None, group_atmosphere: dict[str, Any] | None, important_dates: list[dict[str, str]] | None, _now: datetime | None) -> dict[str, Any] | None` - Check if proactive trigger should fire.


---

## core.response_assembler

### Classes

#### `ResponseAssembler`

Assembles LLM prompts with emotion, empathy, memory, and group context.

**方法：**

- `assemble(self, message: Message, intent: IntentAnalysisV3, emotion: EmotionState, empathy_strategy: EmpathyStrategy, memories: list[dict[str, Any]], group_profile: GroupSemanticProfile | None, user_profile: UserSemanticProfile | None, assistant_emotion: AssistantEmotionState, style_params: StyleParams | None, heat_level: str, pace: str, topic_stability: float, is_group_chat: bool, recent_participants: list[dict[str, Any]] | None, caller_is_developer: bool, glossary_section: str, cross_group_context: str) -> PromptBundle` - Build a structured prompt for response generation.
- `assemble_delayed(self, message_content: str, group_profile: GroupSemanticProfile | None, style_params: StyleParams | None, heat_level: str, pace: str, is_group_chat: bool, caller_is_developer: bool, glossary_section: str, adapter_type: str | None, is_first_interaction: bool, user_profiles: list[UserSemanticProfile] | None) -> PromptBundle` - Build prompt for a delayed response (topic-gap trigger).
- `assemble_proactive(self, trigger_reason: str, group_profile: GroupSemanticProfile | None, suggested_tone: str, is_group_chat: bool, glossary_section: str, topic_context: str, adapter_type: str | None) -> PromptBundle` - Build prompt for proactive initiation.
- `parse_dual_output(raw: str) -> tuple[str, str]` - Return the raw reply as spoken content.

#### `StyleAdapter`

Adapts response length and tone based on rhythm, heat, and user preferences.

**方法：**

- `adapt(self, heat_level: str, pace: str, user_communication_style: str, topic_stability: float, persona: PersonaProfile | None, is_group_chat: bool) -> StyleParams` - Compute style parameters for the current response context.

#### `StyleParams`

Adapted style parameters for a single response generation.


---

## core.response_strategy

### Classes

#### `ResponseStrategyEngine`

Decides response strategy based on intent, emotion, and context.

**方法：**

- `decide(self, intent: IntentAnalysisV3, is_mentioned: bool, is_developer: bool, heat_level: str, sender_type: str) -> StrategyDecision` - Decide response strategy from intent analysis.


---

## core.rhythm

### Classes

#### `RhythmAnalysis`

Conversation rhythm analysis result.

#### `RhythmAnalyzer`

Analyzes conversation rhythm beyond simple heat metrics.

**方法：**

- `analyze(self, group_id: str, messages: list[dict[str, Any]]) -> RhythmAnalysis` - Analyze rhythm from recent messages.


---

## core.threshold_engine

### Classes

#### `ThresholdEngine`

Computes dynamic engagement threshold based on multiple factors.

**方法：**

- `compute(self, sensitivity: float, heat_level: str, messages_per_minute: float, relationship_state: RelationshipState | None, hour_of_day: int | None, sender_type: str, is_developer: bool) -> float` - Compute dynamic threshold.


---

## exceptions

### Classes

#### `SiriusException`

Sirius Chat的基础异常类

所有自定义异常都继承自该类。包含以下属性：
- error_code: 错误代码（用于国际化和自动处理）
- context: 错误上下文信息（诊断用）
- is_retryable: 是否可重试

**方法：**

- `to_dict(self) -> dict[str, Any]` - 转换为字典，便于日志序列化

#### `ProviderError`

Provider相关错误的基类

包括网络连接、API响应、认证等问题。

**方法：**

- `to_dict(self) -> dict[str, Any]` - 转换为字典，便于日志序列化

#### `ProviderConnectionError`

Provider连接失败（网络超时、连接拒绝等）

**方法：**

- `to_dict(self) -> dict[str, Any]` - 转换为字典，便于日志序列化

#### `ProviderAuthError`

Provider认证失败（API Key无效、权限不足等）

**方法：**

- `to_dict(self) -> dict[str, Any]` - 转换为字典，便于日志序列化

#### `ProviderResponseError`

Provider返回异常响应（HTTP错误、格式错误等）

**方法：**

- `to_dict(self) -> dict[str, Any]` - 转换为字典，便于日志序列化

#### `TokenError`

Token相关错误的基类

**方法：**

- `to_dict(self) -> dict[str, Any]` - 转换为字典，便于日志序列化

#### `TokenBudgetExceededError`

Token预算已用完

**方法：**

- `to_dict(self) -> dict[str, Any]` - 转换为字典，便于日志序列化

#### `TokenEstimationError`

Token estimation error exception.

**方法：**

- `to_dict(self) -> dict[str, Any]` - 转换为字典，便于日志序列化

#### `ParseError`

Content parsing error exception.

**方法：**

- `to_dict(self) -> dict[str, Any]` - 转换为字典，便于日志序列化

#### `JSONParseError`

JSON parsing error exception.

**方法：**

- `to_dict(self) -> dict[str, Any]` - 转换为字典，便于日志序列化

#### `ContentValidationError`

内容验证失败（如字段缺失、类型错误）

**方法：**

- `to_dict(self) -> dict[str, Any]` - 转换为字典，便于日志序列化

#### `ConfigError`

Configuration error exception base class.

**方法：**

- `to_dict(self) -> dict[str, Any]` - 转换为字典，便于日志序列化

#### `InvalidConfigError`

Configuration parameter is invalid exception.

**方法：**

- `to_dict(self) -> dict[str, Any]` - 转换为字典，便于日志序列化

#### `MissingConfigError`

Required configuration is missing exception.

**方法：**

- `to_dict(self) -> dict[str, Any]` - 转换为字典，便于日志序列化

#### `MemoryError`

Memory management error exception.

**方法：**

- `to_dict(self) -> dict[str, Any]` - 转换为字典，便于日志序列化

#### `UserNotFoundError`

User record not found exception.

**方法：**

- `to_dict(self) -> dict[str, Any]` - 转换为字典，便于日志序列化

#### `ConflictingMemoryError`

记忆冲突（用于记忆管理器的冲突检测）

**方法：**

- `to_dict(self) -> dict[str, Any]` - 转换为字典，便于日志序列化


---

## logging_config

### Functions

#### `configure_logging(level: LogLevel, format_type: LogFormat, log_file: Path | str | None, enable_file_rotation: bool, model_calls_log_file: Path | str | None, third_party_level: LogLevel) -> None`

配置全局日志系统

Args:
    level: 日志级别，可选值：DEBUG/INFO/WARNING/ERROR/CRITICAL
    format_type: 输出格式，可选值：console/json
    log_file: 可选的日志文件路径（若指定则同时输出到文件）
    enable_file_rotation: 是否启用日志文件循环（每日轮换）
    model_calls_log_file: 可选的模型调用日志文件路径（独立的专用日志）
    third_party_level: 第三方库日志级别上调到该值，默认 WARNING

Example:
    ```python
    # 控制台输出（开发环境）
    configure_logging(level="DEBUG", format_type="console")

    # JSON输出到文件（生产环境）
    configure_logging(
        level="INFO",
        format_type="json",
        log_file="logs/app.log",
        enable_file_rotation=True,
        model_calls_log_file="logs/model_calls.log"
    )
    ```

#### `get_logger(name: str) -> logging.Logger`

获取指定名称的logger实例


---

## memory.user.models

### Classes

#### `UserProfile`

Initial user profile: provided by external system before session starts.

Should not be arbitrarily overwritten by AI during runtime.

**方法：**

- `to_dict(self) -> dict[str, Any]` - Serialise to a plain dict (recursively via ``dataclasses.asdict``).


---

## models.emotion

### Classes

#### `EmotionState`

2D emotion state with valence (-1~+1) and arousal (0~1).

**方法：**

- `to_dict(self) -> dict[str, Any]`

#### `AssistantEmotionState`

Assistant's own persistent emotion state with inertia & recovery.

**方法：**

- `tick_recovery(self) -> None` - Gradually drift back to baseline (call periodically).
- `update_from_interaction(self, user_emotion: EmotionState, user_id: str) -> None` - Update assistant emotion after an interaction, respecting inertia.

#### `EmpathyStrategy`

Empathy response strategy selected by EmotionAnalyzer.


---

## models.intent_v3

### Classes

#### `IntentAnalysisV3`

Extended intent analysis result compatible with v2 + v3 fields.

**方法：**

- `to_dict(self) -> dict[str, Any]`

#### `SocialIntent`

Purpose-driven intent taxonomy (paper §2.1).


---

## models.models

### Classes

#### `Message`

Message(role: 'str', content: 'str', speaker: 'str | None' = None, channel: 'str | None' = None, channel_user_id: 'str | None' = None, group_id: 'str | None' = None, multimodal_inputs: 'list[dict[str, str]]' = <factory>, reply_mode: 'str' = 'always', adapter_type: 'str | None' = None, sender_type: 'str' = 'human')

**方法：**

- `to_dict(self) -> dict[str, Any]` - Serialise to a plain dict (recursively via ``dataclasses.asdict``).

#### `Participant`

Multi-user participant representation with auto-generated unique user_id.

Attributes:
    name: Human-readable display name (not unique).
    user_id: Unique identifier, auto-generated as UUID if not provided.
             Used for memory binding and accurate identification.
    persona: Initial persona/background for the user.
    identities: Mapping from external systems (channel:external_uid) to track
               cross-platform user identity.
    aliases: Alternative names the user may go by.
    traits: Initial traits/characteristics.
    metadata: Additional custom metadata.

**方法：**

- `as_user_profile(self) -> UserProfile`
- `to_dict(self) -> dict[str, Any]` - Serialise to a plain dict (recursively via ``dataclasses.asdict``).

#### `Transcript`

Transcript(messages: 'list[Message]' = <factory>, user_memory: 'UserManager' = <factory>, reply_runtime: 'ReplyRuntimeState' = <factory>, session_summary: 'str' = '', orchestration_stats: 'dict[str, dict[str, int]]' = <factory>, token_usage_records: 'list[TokenUsageRecord]' = <factory>)

**方法：**

- `add(self, message: Message) -> None`
- `add_token_usage_record(self, record: TokenUsageRecord) -> None`
- `as_chat_history(self) -> list[dict[str, str]]`
- `compress_for_budget(self, max_messages: int, max_chars: int) -> None`
- `find_user_by_channel_uid(self, channel: str, uid: str, group_id: str) -> UserProfile | None`
- `remember_participant(self, participant: Participant, content: str, max_recent_messages: int, channel: str | None, channel_user_id: str | None, group_id: str) -> None`
- `to_dict(self) -> dict[str, Any]` - Serialize to dict. Complex fields use custom logic; all other simple

#### `User`

Multi-user participant representation with auto-generated unique user_id.

Attributes:
    name: Human-readable display name (not unique).
    user_id: Unique identifier, auto-generated as UUID if not provided.
             Used for memory binding and accurate identification.
    persona: Initial persona/background for the user.
    identities: Mapping from external systems (channel:external_uid) to track
               cross-platform user identity.
    aliases: Alternative names the user may go by.
    traits: Initial traits/characteristics.
    metadata: Additional custom metadata.

**方法：**

- `as_user_profile(self) -> UserProfile`
- `to_dict(self) -> dict[str, Any]` - Serialise to a plain dict (recursively via ``dataclasses.asdict``).


---

## models.response_strategy

### Classes

#### `ResponseStrategy`

Four-layer response strategy (paper §2.3 / §6).

#### `StrategyDecision`

Decision produced by ResponseStrategyEngine.


---

## providers.aliyun_bailian

### Classes

#### `AliyunBailianProvider`

Aliyun Bailian provider backed by DashScope's OpenAI-compatible endpoint.

The constructor accepts either:
- https://dashscope.aliyuncs.com/compatible-mode
- https://dashscope.aliyuncs.com/compatible-mode/v1
and normalizes both to the same request endpoint.

**方法：**

- `generate(self, request: GenerationRequest) -> str` - Generate one assistant message from the upstream provider.


---

## providers.base

### Classes

#### `AsyncLLMProvider`

Base class for LLM providers.

**方法：**

- `async generate_async(self, request: GenerationRequest) -> str` - Generate one assistant message asynchronously from the upstream provider.

#### `LLMProvider`

Base class for protocol classes.

Protocol classes are defined as::

    class Proto(Protocol):
        def meth(self) -> int:
            ...

Such classes are primarily used with static type checkers that recognize
structural subtyping (static duck-typing).

For example::

    class C:
        def meth(self) -> int:
            return 0

    def func(x: Proto) -> int:
        return x.meth()

    func(C())  # Passes static type check

See PEP 544 for details. Protocol classes decorated with
@typing.runtime_checkable act as simple-minded runtime protocols that check
only the presence of given attributes, ignoring their type signatures.
Protocol classes can be generic, they are defined as::

    class GenProto[T](Protocol):
        def meth(self) -> T:
            ...

**方法：**

- `generate(self, request: GenerationRequest) -> str` - Generate one assistant message from the upstream provider.


---

## providers.bigmodel

### Classes

#### `BigModelProvider`

BigModel provider backed by /api/paas/v4/chat/completions.

BigModel GLM models use an OpenAI-compatible message schema with a
BigModel-specific base path. The provider accepts either:
- https://open.bigmodel.cn
- https://open.bigmodel.cn/api/paas
- https://open.bigmodel.cn/api/paas/v4
and normalizes all of them to the same request endpoint.

**方法：**

- `generate(self, request: GenerationRequest) -> str` - Generate one assistant message from the upstream provider.


---

## providers.middleware.base

### Classes

#### `Middleware`

Base class for middleware components.

**方法：**

- `async process_request(self, context: MiddlewareContext) -> None` - 处理请求前的逻辑
- `async process_response(self, context: MiddlewareContext, response: str, error: Exception | None) -> str` - 处理响应的逻辑

#### `MiddlewareChain`

Chain of middleware components for request processing.

**方法：**

- `add(self, middleware: Middleware) -> MiddlewareChain` - 添加中间件
- `async execute_request(self, context: MiddlewareContext) -> None` - 执行所有中间件的请求处理逻辑
- `async execute_response(self, context: MiddlewareContext, response: str, error: Exception | None) -> str` - 执行所有中间件的响应处理逻辑（逆序）
- `async wrap_call(self, request: GenerationRequest, call_func: Callable[..., Coroutine[Any, Any, str]], kwargs) -> str` - 包装一个调用，自动处理中间件逻辑

#### `MiddlewareContext`

Context information for middleware execution.


---

## providers.middleware.cost_metrics

### Classes

#### `CostMetricsMiddleware`

成本计量中间件

追踪调用成本和使用指标

**方法：**

- `get_metrics(self) -> dict[str, Any]` - 获取全部指标
- `async process_request(self, context: MiddlewareContext) -> None` - 初始化成本追踪
- `async process_response(self, context: MiddlewareContext, response: str, error: Exception | None) -> str` - 计算和记录成本


---

## providers.middleware.rate_limiter

### Classes

#### `RateLimiterMiddleware`

速率限制中间件

限制在时间窗口内的最大请求数

**方法：**

- `async process_request(self, context: MiddlewareContext) -> None` - 检查并应用速率限制
- `async process_response(self, context: MiddlewareContext, response: str, error: Exception | None) -> str` - 不需要处理响应

#### `TokenBucketRateLimiter`

令牌桶算法的速率限制器

更精细的速率控制

**方法：**

- `async process_request(self, context: MiddlewareContext) -> None` - 获取令牌或等待
- `async process_response(self, context: MiddlewareContext, response: str, error: Exception | None) -> str` - 不需要处理响应


---

## providers.middleware.retry

### Classes

#### `RetryMiddleware`

统一的重试策略中间件

支持指数退避和最大重试次数

**方法：**

- `async process_request(self, context: MiddlewareContext) -> None` - 初始化重试计数器
- `async process_response(self, context: MiddlewareContext, response: str, error: Exception | None) -> str` - 如果有错误，在这里处理重试逻辑

#### `CircuitBreakerMiddleware`

断路器中间件

防止对故障 Provider 的持续调用

**方法：**

- `async process_request(self, context: MiddlewareContext) -> None` - 检查断路器状态
- `async process_response(self, context: MiddlewareContext, response: str, error: Exception | None) -> str` - 更新断路器状态


---

## providers.mock

### Classes

#### `MockProvider`

Deterministic provider for unit tests and local dry runs.

**方法：**

- `generate(self, request: GenerationRequest) -> str` - Generate one assistant message from the upstream provider.


---

## providers.openai_compatible

### Classes

#### `OpenAICompatibleProvider`

OpenAI-compatible provider backed by /v1/chat/completions.

**方法：**

- `generate(self, request: GenerationRequest) -> str` - Generate one assistant message from the upstream provider.


---

## providers.routing

### Classes

#### `AutoRoutingProvider`

Choose a configured provider automatically on each generation request.

**方法：**

- `generate(self, request: GenerationRequest) -> str` - Generate one assistant message from the upstream provider.
- `async generate_async(self, request: GenerationRequest) -> str`

#### `ProviderConfig`

ProviderConfig(provider_type: 'str', api_key: 'str', base_url: 'str', healthcheck_model: 'str' = '', enabled: 'bool' = True, models: 'list[str]' = <factory>)

#### `ProviderRegistry`

Store provider credentials and routing hints under work_path.

**方法：**

- `load(self) -> dict[str, ProviderConfig]`
- `remove(self, provider_type: str) -> bool`
- `save(self, providers: dict[str, ProviderConfig]) -> None`
- `upsert(self, provider_type: str, api_key: str, base_url: str, healthcheck_model: str, models: list[str] | None) -> None`

#### `WorkspaceProviderManager`

Workspace-scoped provider registry facade.

**方法：**

- `load(self) -> dict[str, ProviderConfig]`
- `merge_entries(self, providers_config: list[dict[str, object]]) -> dict[str, ProviderConfig]`
- `probe(self) -> None`
- `register(self, provider_type: str, api_key: str, base_url: str, healthcheck_model: str, models: list[str] | None) -> None`
- `remove(self, provider_type: str) -> bool`
- `save(self, providers: dict[str, ProviderConfig]) -> None`
- `save_from_entries(self, providers_config: list[dict[str, object]]) -> dict[str, ProviderConfig]`

### Functions

#### `ensure_provider_platform_supported(provider_type: str) -> str`

#### `get_supported_provider_platforms() -> dict[str, dict[str, str]]`

#### `merge_provider_sources(work_path: Path, providers_config: list[dict[str, object]]) -> dict[str, ProviderConfig]`

Merge providers from multiple sources with priority order.

Priority (high to low):
1. Session JSON: providers field
2. Persistent: <work_path>/provider_keys.json

#### `normalize_provider_type(provider_type: str) -> str`

#### `probe_provider_availability(provider: LLMProvider, model_name: str) -> None`

Run a minimal generation request to verify provider connectivity and credentials.

#### `register_provider_with_validation(work_path: Path, provider_type: str, api_key: str, healthcheck_model: str, base_url: str) -> str`

Register provider only after support and availability checks pass.

#### `run_provider_detection_flow(providers: dict[str, ProviderConfig]) -> None`

Framework-level provider checks.

1) Ensure provider platform/API config exists.
2) Ensure platform is supported by current framework.
3) Ensure provider is available using the registered healthcheck model.


---

## providers.siliconflow

### Classes

#### `SiliconFlowProvider`

SiliconFlow provider backed by OpenAI-compatible /v1/chat/completions.

**方法：**

- `generate(self, request: GenerationRequest) -> str` - Generate one assistant message from the upstream provider.


---

## providers.volcengine_ark

### Classes

#### `VolcengineArkProvider`

Volcengine Ark provider backed by /api/v3/chat/completions.

**方法：**

- `generate(self, request: GenerationRequest) -> str` - Generate one assistant message from the upstream provider.


---

## roleplay_prompting

### Classes

#### `DependencyFileSnapshot`

DependencyFileSnapshot(path: 'str', exists: 'bool', sha256: 'str' = '', content: 'str' = '', error: 'str' = '')

#### `PersonaGenerationTrace`

PersonaGenerationTrace(agent_key: 'str', generated_at: 'str', operation: 'str', model: 'str', temperature: 'float', max_tokens: 'int', system_prompt: 'str', user_prompt: 'str', raw_response: 'str', parsed_payload: 'dict[str, object]' = <factory>, prompt_enhancements: 'list[str]' = <factory>, dependency_snapshots: 'list[DependencyFileSnapshot]' = <factory>, persona_spec: 'PersonaSpec' = <factory>, output_preset: 'dict[str, object]' = <factory>)

#### `PersonaSpec`

Persisted generation input for a roleplay agent persona.

Supports three construction paths:
- Tag-based: provide ``trait_keywords`` only (fast, no Q&A required).
- Q&A-based: provide ``answers`` (traditional interview flow).
- Hybrid: combine both for richer generation.

Stored alongside generated output so individual dimensions can be
patched and regenerated without full rewrite.

**方法：**

- `merge(self, patch: object) -> 'PersonaSpec'` - Return a shallow-patched copy; *None* values are ignored.

#### `RolePlayAnswer`

RolePlayAnswer(question: 'str', answer: 'str', perspective: 'str' = 'subjective', details: 'str' = '')

#### `RolePlayQuestion`

RolePlayQuestion(question: 'str', perspective: 'str' = 'subjective', details: 'str' = '')

### Functions

#### `async abuild_roleplay_prompt_from_answers_and_apply(provider: LLMProvider | AsyncLLMProvider, config: SessionConfig, model: str, answers: list[RolePlayAnswer] | None, trait_keywords: list[str] | None, dependency_files: list[str] | None, persona_spec: PersonaSpec | None, persona_key: str, agent_name: str, agent_alias: str, background: str, output_language: str, persist_generated_agent: bool, select_after_save: bool, temperature: float, max_tokens: int, timeout_seconds: float) -> str`

Build roleplay prompt from user answers and apply it.

#### `async agenerate_agent_prompts_from_answers(provider: LLMProvider | AsyncLLMProvider, model: str, agent_name: str, agent_alias: str, answers: list[RolePlayAnswer], background: str, dependency_files: list[str] | None, dependency_root: Path | None, output_language: str, temperature: float, max_tokens: int, timeout_seconds: float, base_model: str, base_temperature: float, base_max_tokens: int) -> GeneratedSessionPreset`

Generate agent prompts from user answers.

#### `async agenerate_from_persona_spec(provider: LLMProvider | AsyncLLMProvider, spec: PersonaSpec, model: str, dependency_root: Path | None, temperature: float, max_tokens: int, timeout_seconds: float, base_model: str, base_temperature: float, base_max_tokens: int) -> GeneratedSessionPreset`

Generate a :class:`GeneratedSessionPreset` from a :class:`PersonaSpec`.

Supports three construction paths driven by the spec:

* **Tag-based**: set ``spec.trait_keywords`` only — fast path, no Q&A.
* **Q&A-based**: set ``spec.answers`` — traditional question-answer flow.
* **Hybrid**: set both for richer, anchored generation.

``Agent.persona`` in the returned preset contains compact keyword tags
(e.g. ``"热情/直接/逻辑清晰"``); the detailed role guide lives in
``global_system_prompt``. Structured persona generation now defaults to
``max_tokens=5120`` and ``timeout_seconds=120.0`` to reduce JSON truncation.

#### `async aregenerate_agent_prompt_from_dependencies(provider: LLMProvider | AsyncLLMProvider, work_path: Path, agent_key: str, model: str, dependency_files: list[str] | None, temperature: float, max_tokens: int, timeout_seconds: float, select_after_update: bool) -> GeneratedSessionPreset`

Regenerate an existing agent by re-reading its dependency files from disk.

#### `async aupdate_agent_prompt(provider: LLMProvider | AsyncLLMProvider, work_path: Path, agent_key: str, model: str, trait_keywords: list[str] | None, answers: list[RolePlayAnswer] | None, background: str | None, dependency_files: list[str] | None, agent_alias: str | None, output_language: str | None, temperature: float, max_tokens: int, timeout_seconds: float, select_after_update: bool) -> GeneratedSessionPreset`

Partially update the prompt for an existing agent without full rewrite.

Loads the persisted :class:`PersonaSpec` for *agent_key*, merges only
the provided patch fields, and regenerates.  Unspecified fields keep
their existing values.  Returns the newly generated preset and persists
it with the merged spec.

Raises :class:`ValueError` if no agent with *agent_key* exists or if
the agent has no persisted spec (run the initial generation first).

#### `create_session_config_from_selected_agent(work_path: Path, data_path: Path | None, agent_key: str, history_max_messages: int, history_max_chars: int, max_recent_participant_messages: int, enable_auto_compression: bool, orchestration: OrchestrationPolicy | None) -> SessionConfig`

Create session configuration from a selected agent preset.

#### `generate_humanized_roleplay_questions(template: str) -> list[RolePlayQuestion]`

Generate humanized roleplay questions for user interaction.

#### `list_roleplay_question_templates() -> list[str]`

List available roleplay questionnaire templates.

#### `load_generated_agent_library(work_path: Path) -> tuple[dict[str, GeneratedSessionPreset], str]`

Load the generated agent profile library.

#### `load_persona_generation_traces(work_path: Path, agent_key: str) -> list[PersonaGenerationTrace]`

Load all locally persisted generation traces for *agent_key*.

#### `load_persona_spec(work_path: Path, agent_key: str) -> PersonaSpec | None`

Load the persisted :class:`PersonaSpec` for a specific agent key.

Returns the latest staged spec when a generation attempt is pending;
otherwise returns the last successful spec. Returns ``None`` if the key
does not exist or no spec was saved.

#### `persist_generated_agent_profile(config: SessionConfig, agent_key: str, select_after_save: bool, persona_spec: PersonaSpec | None) -> str`

Persist a generated agent profile to storage.

#### `select_generated_agent_profile(work_path: Path, agent_key: str) -> GeneratedSessionPreset`

Select a generated agent profile from the library.


---

## session.store

### Classes

#### `JsonSessionStore`

JSON-based session store for persisting sessions.

**方法：**

- `clear(self) -> None`
- `exists(self) -> bool`
- `load(self) -> Transcript`
- `save(self, transcript: Transcript) -> None`

#### `SessionStoreFactory`

Create session stores for a workspace/session namespace.

**方法：**

- `create(self, layout: WorkspaceLayout, session_id: str) -> SessionStore`

#### `SqliteSessionStore`

SQLite-based session store for persisting sessions.

**方法：**

- `clear(self) -> None` - Remove the saved session state (deletes rows; keeps schema intact).
- `exists(self) -> bool`
- `load(self) -> Transcript`
- `save(self, transcript: Transcript) -> None`


---

## skills.data_store

### Classes

#### `SkillDataStore`

JSON-backed persistent key-value store for a single skill.

Thread-safety: protected by an internal re-entrant lock so concurrent
access from multiple async tasks or threads does not corrupt the file.

**方法：**

- `all(self) -> dict[str, Any]` - Return a shallow copy of all stored data.
- `delete(self, key: str) -> bool` - Delete a key. Returns True if key existed.
- `get(self, key: str, default: Any) -> Any` - Get a value by key.
- `keys(self) -> list[str]` - Return all stored keys.
- `save(self) -> None` - Persist current data to disk (only if modified).
- `set(self, key: str, value: Any) -> None` - Set a value by key. Call save() to persist.


---

## skills.executor

### Classes

#### `SkillExecutor`

Execute skills with parameter validation, retry, telemetry, and data store injection.

**方法：**

- `execute(self, skill: SkillDefinition, params: dict[str, Any], chain_context: SkillChainContext | None, invocation_context: SkillInvocationContext | None, max_retries: int) -> SkillResult` - Execute a skill synchronously with parameter validation and optional retry.
- `async execute_async(self, skill: SkillDefinition, params: dict[str, Any], timeout: float, chain_context: SkillChainContext | None, invocation_context: SkillInvocationContext | None, max_retries: int) -> SkillResult` - Execute a skill in a thread pool to avoid blocking the event loop.
- `get_bridge_for_skill(self, skill: SkillDefinition) -> Any | None` - Return the best-matching bridge for a skill.
- `get_data_store(self, skill_name: str) -> SkillDataStore` - Get or create the persistent data store for a skill.
- `save_all_stores(self) -> None` - Persist all dirty data stores.
- `set_bridge(self, adapter_type: str, bridge: Any) -> None` - Register a platform bridge for a given adapter type.


---

## skills.models

### Classes

#### `SkillDefinition`

Complete definition of a loadable skill.

**方法：**

- `get_parameter_schema(self) -> list[dict[str, Any]]` - Return parameter definitions as dicts for prompt rendering.

#### `SkillInvocationContext`

Per-call context injected into skills for authorization and auditing.

#### `SkillParameter`

Definition of a single skill parameter.

#### `SkillResult`

Result returned from skill execution.

**方法：**

- `from_raw_result(value: Any) -> 'SkillResult'` - Normalize a raw skill return value into SkillResult.
- `get_field(self, key: str, default: Any) -> Any` - Extract a field from dict/list data by key or index.
- `to_display_text(self) -> str` - Convert result to a human-readable text for AI consumption.
- `to_internal_payload(self) -> dict[str, Any]` - Build a structured internal payload for prompt injection.


---

## skills.registry

### Classes

#### `SkillRegistry`

Discovers and manages skill definitions from a directory.

**方法：**

- `all_skills(self) -> list[SkillDefinition]`
- `build_tool_descriptions(self, invocation_context: SkillInvocationContext | None, compact: bool, adapter_type: str | None) -> str` - Build a formatted text block describing all available skills.
- `builtin_skills_dir() -> Path` - Return the package directory containing built-in skills.
- `ensure_skills_directory(skills_dir: Path) -> None` - Ensure the skills directory and its README bootstrap file exist.
- `get(self, name: str) -> SkillDefinition | None`
- `load_from_directory(self, skills_dir: Path, auto_install_deps: bool, include_builtin: bool) -> int` - Load all *.py skill files from a directory.
- `register(self, skill: SkillDefinition) -> None` - Manually register a skill definition.
- `reload_from_directory(self, skills_dir: Path, auto_install_deps: bool, include_builtin: bool) -> int` - Reload all skill files from a directory, replacing removed entries too.
- `replace_all(self, skills: list[SkillDefinition]) -> None` - Replace the whole registry atomically.


---

## token.analytics

### Classes

#### `AnalyticsReport`

dict() -> new empty dictionary
dict(mapping) -> new dictionary initialized from a mapping object's
    (key, value) pairs
dict(iterable) -> new dictionary initialized as if via:
    d = {}
    for k, v in iterable:
        d[k] = v
dict(**kwargs) -> new dictionary initialized with the name=value pairs
    in the keyword argument list.  For example:  dict(one=1, two=2)

#### `BaselineDict`

dict() -> new empty dictionary
dict(mapping) -> new dictionary initialized from a mapping object's
    (key, value) pairs
dict(iterable) -> new dictionary initialized as if via:
    d = {}
    for k, v in iterable:
        d[k] = v
dict(**kwargs) -> new dictionary initialized with the name=value pairs
    in the keyword argument list.  For example:  dict(one=1, two=2)

#### `BucketDict`

dict() -> new empty dictionary
dict(mapping) -> new dictionary initialized from a mapping object's
    (key, value) pairs
dict(iterable) -> new dictionary initialized as if via:
    d = {}
    for k, v in iterable:
        d[k] = v
dict(**kwargs) -> new dictionary initialized with the name=value pairs
    in the keyword argument list.  For example:  dict(one=1, two=2)

#### `TimeSliceDict`

dict() -> new empty dictionary
dict(mapping) -> new dictionary initialized from a mapping object's
    (key, value) pairs
dict(iterable) -> new dictionary initialized as if via:
    d = {}
    for k, v in iterable:
        d[k] = v
dict(**kwargs) -> new dictionary initialized with the name=value pairs
    in the keyword argument list.  For example:  dict(one=1, two=2)

### Functions

#### `compute_baseline(store: TokenUsageStore, session_id: str | None, actor_id: str | None, task_name: str | None, model: str | None) -> BaselineDict`

Compute aggregate baseline statistics with optional filters.

#### `full_report(store: TokenUsageStore, session_id: str | None) -> AnalyticsReport`

Produce a comprehensive analytics report.

When *session_id* is given the report is scoped to that session;
otherwise it covers all sessions in the database.

#### `group_by_actor(store: TokenUsageStore, session_id: str | None, task_name: str | None, model: str | None) -> dict[str, BucketDict]`

Aggregate token usage grouped by actor.

#### `group_by_model(store: TokenUsageStore, session_id: str | None, actor_id: str | None, task_name: str | None) -> dict[str, BucketDict]`

Aggregate token usage grouped by model.

#### `group_by_session(store: TokenUsageStore, actor_id: str | None, task_name: str | None, model: str | None) -> dict[str, BucketDict]`

Aggregate token usage grouped by session.

#### `group_by_task(store: TokenUsageStore, session_id: str | None, actor_id: str | None, model: str | None) -> dict[str, BucketDict]`

Aggregate token usage grouped by task.

#### `time_series(store: TokenUsageStore, bucket_seconds: int, session_id: str | None, actor_id: str | None, task_name: str | None, model: str | None) -> list[TimeSliceDict]`

Aggregate token usage into fixed-width time buckets.

Parameters
----------
bucket_seconds:
    Width in seconds of each time bucket (default 3600 = 1 hour).


---

## token.store

### Classes

#### `TokenUsageStore`

Append-only SQLite store for :class:`TokenUsageRecord` instances.

Parameters
----------
db_path:
    Path to the SQLite database file.  Created automatically if absent.
session_id:
    Logical session identifier written alongside every record so that
    per-session queries are possible.

**方法：**

- `add(self, record: TokenUsageRecord, timestamp: float | None) -> None` - Persist a single :class:`TokenUsageRecord`.
- `add_many(self, records: list[TokenUsageRecord], timestamp: float | None) -> None` - Persist multiple records in a single transaction.
- `close(self) -> None`
- `count(self, session_id: str | None) -> int`
- `fetch_records(self, session_id: str | None, actor_id: str | None, task_name: str | None, model: str | None) -> list[dict[str, object]]` - Return raw rows matching the given filters.
- `get_breakdown_by(self, column: str) -> list[dict[str, Any]]` - Return token usage grouped by a column (e.g. 'task_name', 'model', 'group_id').
- `get_recent_records(self, limit: int) -> list[dict[str, Any]]` - Return the most recent token usage records.
- `get_summary(self) -> dict[str, Any]` - Return aggregated token usage summary.
- `list_sessions(self) -> list[str]`


---

## token.usage

### Classes

#### `TokenUsageBaseline`

TokenUsageBaseline(total_calls: 'int', total_prompt_tokens: 'int', total_completion_tokens: 'int', total_tokens: 'int', avg_tokens_per_call: 'float', avg_prompt_tokens_per_call: 'float', avg_completion_tokens_per_call: 'float', completion_to_prompt_ratio: 'float', retry_rate: 'float')

**方法：**

- `to_dict(self) -> TokenUsageBaselineDict`

### Functions

#### `build_token_usage_baseline(records: list[TokenUsageRecord]) -> TokenUsageBaseline`

Build a baseline for token usage metrics.

#### `summarize_token_usage(transcript: Transcript) -> TokenUsageSummary`

Summarize token usage statistics.


---

## workspace.layout

### Classes

#### `WorkspaceLayout`

Single authority for config-root and data-root persistence paths.

**方法：**

- `config_dir(self) -> Path`
- `config_watch_paths(self) -> list[Path]`
- `ensure_directories(self, session_id: str | None) -> None`
- `event_memory_dir(self) -> Path`
- `event_memory_path(self) -> Path`
- `generated_agent_trace_dir(self) -> Path`
- `generated_agents_path(self) -> Path`
- `legacy_event_memory_dir(self) -> Path`
- `legacy_event_memory_path(self) -> Path`
- `legacy_generated_agents_path(self) -> Path`
- `legacy_primary_user_path(self) -> Path`
- `legacy_provider_registry_path(self) -> Path`
- `legacy_self_memory_path(self) -> Path`
- `legacy_session_store_path(self, backend: str) -> Path`
- `legacy_token_usage_db_path(self) -> Path`
- `legacy_user_memory_dir(self) -> Path`
- `memory_dir(self) -> Path`
- `persisted_session_bundle_path(self) -> Path`
- `primary_user_path(self) -> Path`
- `provider_registry_path(self) -> Path`
- `providers_dir(self) -> Path`
- `roleplay_dir(self) -> Path`
- `self_memory_path(self) -> Path`
- `session_config_path(self) -> Path`
- `session_dir(self, session_id: str) -> Path`
- `session_id_from_slug(self, slug: str) -> str`
- `session_participants_path(self, session_id: str) -> Path`
- `session_slug(self, session_id: str) -> str`
- `session_store_path(self, session_id: str, backend: str) -> Path`
- `sessions_dir(self) -> Path`
- `skill_data_dir(self) -> Path`
- `skills_dir(self) -> Path`
- `token_dir(self) -> Path`
- `token_usage_db_path(self) -> Path`
- `user_memory_dir(self) -> Path`
- `workspace_manifest_path(self) -> Path`


---

## workspace.roleplay_manager

### Classes

#### `RoleplayWorkspaceManager`

Unify agent selection, session defaults and workspace persistence.

The host calls *bootstrap_active_agent* after a wizard flow completes;
this class takes care of selecting the agent in the library, updating
the workspace defaults, and persisting everything — so the host never
touches workspace files directly.

**方法：**

- `bootstrap_active_agent(self, agent_key: str, session_defaults: SessionDefaults | None, orchestration_defaults: dict[str, object] | None) -> WorkspaceConfig` - Select an agent and optionally update workspace defaults in one call.
- `bootstrap_from_legacy_session_config(self, source: Path, agent_key: str | None) -> WorkspaceConfig` - Bootstrap workspace config from a legacy ``session.json`` file.


---

## workspace.runtime

### Classes

#### `WorkspaceRuntime`

WorkspaceRuntime(work_path: 'Path', config_path: 'Path | None' = None, provider: 'LLMProvider | AsyncLLMProvider | None' = None, store_factory: 'SessionStoreFactory' = <factory>, session_config_factory: 'Callable[[str], SessionConfig] | None' = None, bootstrap: 'WorkspaceBootstrap | None' = None, persist_bootstrap: 'bool' = True)

**方法：**

- `async apply_workspace_updates(self, patch: dict[str, object]) -> WorkspaceConfig` - Apply a partial update to workspace defaults and persist.
- `async clear_session(self, session_id: str) -> None`
- `async close(self) -> None`
- `create_emotional_engine(self, config: dict[str, Any] | None, persona: Any | None) -> EmotionalGroupChatEngine` - Create an EmotionalGroupChatEngine bound to this workspace.
- `async delete_session(self, session_id: str) -> None`
- `export_workspace_defaults(self) -> dict[str, object]` - Return the current workspace defaults as a plain dict.
- `async get_primary_user(self, session_id: str) -> Participant | None`
- `get_session_store(self, session_id: str) -> SessionStore`
- `async get_transcript(self, session_id: str) -> Transcript | None`
- `async initialize(self) -> None`
- `async list_sessions(self) -> list[str]`
- `load_skills(self, auto_install_deps: bool) -> None` - Discover and load SKILL registry + executor from workspace.
- `async set_primary_user(self, session_id: str, participant: Participant) -> None`
- `set_provider(self, provider: LLMProvider | AsyncLLMProvider | None) -> None`
- `set_provider_entries(self, entries: list[dict[str, object]], persist: bool) -> None` - Inject provider config entries from the host.


---

