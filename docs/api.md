# Sirius Chat API 文档

自动生成的 Python API 参考文档。

## 模块索引

- [engine](#engine)
- [memory](#memory)
- [models](#models)
- [orchestration](#orchestration)
- [prompting](#prompting)
- [providers](#providers)
- [session](#session)
- [token_usage](#token_usage)

---

## engine

### Classes

#### `AsyncRolePlayEngine`

AsyncRolePlayEngine(provider: 'LLMProvider | AsyncLLMProvider')

**方法：**

- `get_model_for_task(self, config: SessionConfig, task_name: str) -> str` - 根据多模型协同配置获取任务模型。

Args:
    config: 会话配置
    task_name: 任务名称（如 'memory_extract'、'event_extract'）
    
Returns:
    该任务应使用的模型名称
    
Raises:
    ValueError: 如果无法确定任务模型
- `async run_live_message(self, config: SessionConfig, turn: Message, transcript: Transcript | None, session_reply_mode: str | None, finalize_and_persist: bool, environment_context: str, user_profile: UserProfile | None, on_reply: Callable[[Message], Awaitable[None]] | None, timeout: float) -> Transcript`
- `async run_live_session(self, config: SessionConfig, transcript: Transcript | None) -> Transcript` - Initialize a live session and prepare runtime context.

Breaking change: this method no longer processes user messages.
Use run_live_message(...) for per-message input/output handling.
- `async run_session(self, config: SessionConfig, transcript: Transcript | None) -> Transcript`
- `subscribe(self, transcript: Transcript, max_queue_size: int) -> AsyncIterator[SessionEvent]` - Subscribe to real-time session events for the given transcript.

Returns an async iterator that yields :class:`SessionEvent` objects
as they are produced by the engine (new messages, SKILL status,
processing lifecycle, etc.).

The iterator terminates when the session's event bus is closed.

Args:
    transcript: The transcript (session) to subscribe to.
    max_queue_size: Maximum buffered events per subscriber.

Yields:
    SessionEvent instances in chronological order.
- `validate_orchestration_config(self, config: SessionConfig) -> None` - 验证多模型协同配置的完整性。

多模型协同必需启用，支持两种配置方案：
1. unified_model: 所有任务使用同一个模型
2. task_models: 为每个任务独立配置模型

所有任务默认启用，可通过 task_enabled 字典禁用特定任务。

Args:
    config: 会话配置
    
Raises:
    OrchestrationConfigError: 如果配置不完整或冲突

#### `SessionEvent`

A single event emitted by the engine during session processing.

Attributes:
    type: The category of the event.
    message: The ``Message`` object, present for ``MESSAGE_ADDED`` events.
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
    await bus.emit(SessionEvent(type=SessionEventType.MESSAGE_ADDED, message=msg))

    # Close when the session ends
    await bus.close()

**方法：**

- `async close(self) -> None` - Signal all subscribers to stop and clear the subscriber list.
- `async emit(self, event: SessionEvent) -> None` - Publish an event to all current subscribers.
- `subscribe(self, max_queue_size: int) -> AsyncIterator[SessionEvent]` - Return an async iterator that yields events as they arrive.

The iterator terminates when :meth:`close` is called.

#### `SessionEventType`

Categories of events emitted during session processing.

### Functions

#### `create_async_engine(provider: LLMProvider | AsyncLLMProvider) -> AsyncRolePlayEngine`

Create an async roleplay engine for non-blocking integration.

#### `async ainit_live_session(engine: AsyncRolePlayEngine, config: SessionConfig, transcript: Transcript | None) -> Transcript`

Async facade for live session initialization.

#### `async arun_live_message(engine: AsyncRolePlayEngine, config: SessionConfig, turn: Message, transcript: Transcript | None, environment_context: str, user_profile: UserProfile | None, on_reply: Callable[[Message], Awaitable[None]] | None, timeout: float) -> Transcript`

Async facade for single-message live processing.

.. versionchanged:: 0.12.0
   Added *user_profile*, *on_reply* and *timeout* parameters.
   When *on_reply* is provided the engine subscribes to the event stream
   internally and calls back for each assistant message — no external
   ``asubscribe`` boilerplate needed.

.. versionchanged:: 0.9.0
   The ``on_message`` callback has been removed.  Use
   :func:`asubscribe` to receive real-time session events instead.

#### `async asubscribe(engine: AsyncRolePlayEngine, transcript: Transcript, max_queue_size: int) -> AsyncIterator[SessionEvent]`

Subscribe to real-time session events.

Returns an async iterator that yields :class:`SessionEvent` objects
(new messages, SKILL execution status, processing lifecycle, etc.)
as they are produced by the engine.

Example::

    async for event in asubscribe(engine, transcript):
        if event.type == SessionEventType.MESSAGE_ADDED:
            send_to_external(event.message)

Args:
    engine: The engine instance.
    transcript: The active transcript (session).
    max_queue_size: Maximum buffered events per subscriber.

Yields:
    SessionEvent instances in chronological order.

#### `find_user_by_channel_uid(transcript: Transcript, channel: str, uid: str) -> UserMemoryEntry | None`

Stable external lookup by channel + uid.

#### `extract_assistant_messages(transcript: Transcript, since_index: int) -> list[Message]`

Extract assistant messages only.

Useful for downstream delivery to avoid sending internal system notes.


---

## memory

### Classes

#### `UserMemoryEntry`

User memory entry combining profile and runtime state.

#### `UserMemoryManager`

Manages user memory entries, facts, and profiles.

**方法：**

- `add_memory_fact(self, user_id: str, fact_type: str, value: str, source: str, confidence: float, observed_at: str | None, max_facts: int | None, memory_category: str, context_channel: str, context_topic: str, source_event_id: str) -> None` - Add memory fact with trait normalization and intelligent upper limit management.

C1 approach: When exceeding max_facts, delete lowest-confidence facts rather than simple FIFO.
B approach: Auto-apply trait normalization for certain fact_types.
- `add_summary_note(self, user_id: str, note: str, max_notes: int) -> None` - Add a summary note for user.
- `apply_ai_runtime_update(self, user_id: str, inferred_persona: str | None, inferred_aliases: list[str] | None, inferred_traits: list[str] | None, preference_tags: list[str] | None, summary_note: str | None, source: str, confidence: float) -> None` - Apply AI inferred runtime updates to user memory.
- `apply_event_insights(self, user_id: str, event_features: dict[str, object], source: str, base_confidence: float, source_event_id: str) -> None` - Convert event features to user memory facts and feature signals.

Auto-converts event's emotion_tags, keywords, role_slots, time_hints etc.
to corresponding user memory facts and updates observed feature sets.
- `apply_scheduled_decay(self) -> dict[str, int]` - Apply scheduled decay to all user memories.

Returns: {user_id: number of decayed memories}
- `cleanup_expired_memories(self, min_quality: float) -> dict[str, int]` - Clean up expired/low-quality memories for all users.

Returns: {user_id: number of deleted memories}
- `cleanup_expired_transient_facts(self, user_id: str, max_age_minutes: int, transient_threshold: float) -> int` - Clean up expired TRANSIENT facts.

TRANSIENT facts (confidence <= threshold) are deleted after max_age_minutes
from their observed_at time. Returns number of deleted facts.
- `compress_memory_facts(self, user_id: str, similarity_threshold: float) -> int` - C3 approach: Dynamic memory facts compression.

Cluster and merge same-type facts to reduce redundant information.

Args:
    user_id: User ID to compress
    similarity_threshold: Similarity threshold (0.0-1.0)

Returns:
    Number of compressed/deleted facts
- `async consolidate_memory_facts(self, user_id: str, provider_async: Any, model_name: str, min_facts: int, temperature: float, max_tokens: int) -> int` - Consolidate memory facts for a user using LLM-based merging.

Group facts by type, merge similar ones, and produce refined facts.
Returns the number of facts removed (net reduction).
- `async consolidate_summary_notes(self, user_id: str, provider_async: Any, model_name: str, min_notes: int, temperature: float, max_tokens: int) -> int` - Consolidate summary notes for a user into fewer, more refined notes.

Uses LLM to merge and summarize multiple notes into concise summaries.
Returns the number of notes removed (net reduction).
- `ensure_user(self, speaker: str, persona: str) -> UserProfile` - Ensure user exists, creating if necessary.
- `get_facts_by_context(self, user_id: str, channel: str | None, topic: str | None) -> list[MemoryFact]` - Get facts filtered by communication channel and/or topic.

Args:
    user_id: The user ID to query
    channel: Optional channel filter (e.g., "qq", "wechat", "email")
    topic: Optional topic filter (e.g., "work", "hobby", "family")

Returns:
    List of MemoryFact objects matching the filters
- `get_resident_facts(self, user_id: str, threshold: float) -> list[MemoryFact]` - Get high-confidence RESIDENT facts (only for persistence to user.json).

RESIDENT: confidence > threshold, representing core, stable user traits and preferences.
These facts should be persisted to storage.
- `get_rich_user_summary(self, user_id: str, include_transient: bool, max_facts_per_type: int) -> dict[str, Any]` - Generate a model-friendly user summary with rich context.

This summary is suitable for injection into system prompts or as context
for the AI model to provide personalized responses.

Args:
    user_id: The user ID to generate summary for
    include_transient: Whether to include low-confidence transient facts
    max_facts_per_type: Maximum number of facts per type in the summary

Returns:
    Dict with keys: profile, summary, traits, interests, recent_facts, 
                   identities, confidence_distribution, channels
- `get_transient_facts(self, user_id: str, threshold: float) -> list[MemoryFact]` - Get low-confidence TRANSIENT facts (stored in session memory).

TRANSIENT: confidence <= threshold, representing recently observed uncertain information.
These facts should be stored in session memory and auto-cleaned after 30 minutes.
- `get_user_by_id(self, user_id: str) -> UserMemoryEntry | None` - Get user memory entry by exact user ID.

Args:
    user_id: The user ID to look up

Returns:
    UserMemoryEntry or None if not found
- `get_user_by_identity(self, channel: str, external_user_id: str) -> UserMemoryEntry | None` - Get user memory entry by channel identity.
- `merge_from(self, other: 'UserMemoryManager') -> None` - Merge another UserMemoryManager into this one.
- `register_user(self, profile: UserProfile) -> None` - Register a user profile.
- `remember_message(self, profile: UserProfile, content: str, max_recent_messages: int, channel: str | None, channel_user_id: str | None) -> None` - Remember a message from user.
- `resolve_user_id(self, speaker: str | None, channel: str | None, external_user_id: str | None) -> str | None` - Resolve user ID from speaker name, channel identity, or external user ID.
- `resolve_user_id_by_identity(self, channel: str, external_user_id: str) -> str | None` - Resolve user ID by channel identity.
- `search_users_by_fact(self, fact_type: str, value: str | None) -> dict[str, list[MemoryFact]]` - Search for users with specific fact types or values.

Args:
    fact_type: The type of fact to search for (e.g., "job", "location", "hobby")
    value: Optional specific value to match. If None, returns all facts of that type.

Returns:
    Dict mapping user_id to list of matching MemoryFact objects
- `to_dict(self) -> dict[str, Any]` - Serialize to dictionary.

#### `UserProfile`

Initial user profile: provided by external system before session starts.

Should not be arbitrarily overwritten by AI during runtime.

**方法：**

- `to_dict(self) -> dict[str, Any]` - Serialise to a plain dict (recursively via ``dataclasses.asdict``).

#### `EventMemoryManager`

Manages user-scoped observations with buffered batch extraction.

**方法：**

- `absorb_mention(self, content: str, known_entities: list[str], extracted_features: dict[str, object] | None, high_threshold: float, weak_threshold: float) -> dict[str, Any]` - v1 compatibility shim — buffers the message and returns a hit payload.
- `buffer_message(self, user_id: str, content: str) -> None` - Buffer a message for later batch extraction.

Very short / trivial messages are silently discarded.
- `check_relevance(self, user_id: str, content: str) -> dict[str, object]` - Lightweight relevance check against existing observations.

Returns a dict compatible with the legacy *hit_payload* shape
so ``_compute_event_relevance_score`` keeps working.
- `async consolidate_entries(self, user_id: str, provider_async: Any, model_name: str, min_entries: int, temperature: float, max_tokens: int) -> int` - Consolidate observations for a user into fewer, more refined entries.

Groups observations by category, uses LLM to merge and summarize them,
then replaces old entries with consolidated ones.

Returns the number of entries removed (net reduction).
- `async extract_observations(self, user_id: str, user_name: str, provider_async: Any, model_name: str, temperature: float, max_tokens: int) -> list[EventMemoryEntry]` - Consume the buffer for *user_id* and return new/merged observations.

``provider_async`` must expose an async ``generate_async(request)`` method
compatible with ``GenerationRequest``.
- `async finalize_pending_events(self, provider_async: Any, model_name: str, min_mentions: int) -> dict[str, Any]` - Flush all remaining buffers at session end.

Signature is kept compatible with v1 for engine integration.
Returns stats dict with verified_count / rejected_count / pending_count.
- `get_all_user_ids(self) -> set[str]` - Return all unique user IDs present in entries.
- `get_user_observations(self, user_id: str, limit: int) -> list[EventMemoryEntry]` - Get observations for a specific user, ordered by confidence.
- `pending_buffer_counts(self) -> dict[str, int]` - Return {user_id: buffered_message_count} for diagnostics.
- `should_extract(self, user_id: str, batch_size: int) -> bool` - Check whether buffered messages reached the extraction threshold.
- `to_dict(self) -> dict[str, Any]`
- `top_events(self, limit: int, include_pending: bool, user_id: str | None) -> list[EventMemoryEntry]` - Return top observations, optionally filtered by user.


---

## models

### Classes

#### `Agent`

AI agent definition with model and parameters.

#### `AgentPreset`

Pre-configured agent with system prompt.

#### `MemoryPolicy`

Centralized memory system configuration.

Controls memory fact limits, confidence thresholds, decay behaviour,
observed-set caps and prompt-injection budget.

#### `Message`

Message(role: 'str', content: 'str', speaker: 'str | None' = None, channel: 'str | None' = None, channel_user_id: 'str | None' = None, multimodal_inputs: 'list[dict[str, str]]' = <factory>, reply_mode: 'str' = 'always')

**方法：**

- `to_dict(self) -> dict[str, Any]` - Serialise to a plain dict (recursively via ``dataclasses.asdict``).

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

#### `SessionConfig`

Session configuration including agent, paths, and orchestration policy.

#### `TokenUsageRecord`

Record of token usage for a task execution.

**方法：**

- `to_dict(self) -> dict[str, Any]` - Serialise to a plain dict (recursively via ``dataclasses.asdict``).

#### `Transcript`

Transcript(messages: 'list[Message]' = <factory>, user_memory: 'UserMemoryManager' = <factory>, reply_runtime: 'ReplyRuntimeState' = <factory>, session_summary: 'str' = '', orchestration_stats: 'dict[str, dict[str, int]]' = <factory>, token_usage_records: 'list[TokenUsageRecord]' = <factory>)

**方法：**

- `add(self, message: Message) -> None`
- `add_token_usage_record(self, record: TokenUsageRecord) -> None`
- `as_chat_history(self) -> list[dict[str, str]]`
- `compress_for_budget(self, max_messages: int, max_chars: int) -> None`
- `find_user_by_channel_uid(self, channel: str, uid: str) -> UserMemoryEntry | None`
- `remember_participant(self, participant: Participant, content: str, max_recent_messages: int, channel: str | None, channel_user_id: str | None) -> None`
- `to_dict(self) -> dict[str, Any]` - Serialize to dict. Complex fields use custom logic; all other simple
fields on Transcript are auto-included via reflection so any future
addition is persisted without touching this method.

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

## orchestration

### Classes

#### `MultiModelConfig`

多模型协作配置对象。

**方法：**

- `to_orchestration_policy(self) -> OrchestrationPolicy` - 转换为 OrchestrationPolicy 对象。

### Functions

#### `setup_multimodel_config(session_config: SessionConfig, task_models: dict[str, str], task_budgets: dict[str, int] | None, task_temperatures: dict[str, float] | None, task_max_tokens: dict[str, int] | None, task_retries: dict[str, int] | None, max_multimodal_inputs_per_turn: int, max_multimodal_value_length: int) -> SessionConfig`

在现有会话配置中设置多模型编排。

Args:
    session_config: 现有的 SessionConfig 对象
    task_models: 任务模型映射，例如 {"memory_extract": "model-1", "event_extract": "model-2", "intent_analysis": "model-3"}
    task_budgets: 各任务的 token 预算，例如 {"memory_extract": 1200, "event_extract": 1000, "intent_analysis": 600}
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
    ...         "intent_analysis": "gpt-4o-mini",
    ...     },
    ...     task_budgets={
    ...         "memory_extract": 1200,
    ...         "event_extract": 1000,
    ...         "intent_analysis": 600,
    ...     },
    ...     task_temperatures={
    ...         "memory_extract": 0.1,
    ...         "event_extract": 0.1,
    ...     },
    ... )

#### `create_multimodel_config(task_models: dict[str, str], task_budgets: dict[str, int] | None, task_temperatures: dict[str, float] | None, task_max_tokens: dict[str, int] | None, task_retries: dict[str, int] | None, max_multimodal_inputs_per_turn: int, max_multimodal_value_length: int) -> MultiModelConfig`

创建多模型配置对象。

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
    ...     task_models={"memory_extract": "model-1", "intent_analysis": "model-2"},
    ...     task_budgets={"memory_extract": 1200, "intent_analysis": 600},
    ... )
    >>> orchestration = mm_config.to_orchestration_policy()


---

## prompting

### Classes

#### `DependencyFileSnapshot`

DependencyFileSnapshot(path: 'str', exists: 'bool', sha256: 'str' = '', content: 'str' = '', error: 'str' = '')

#### `GeneratedSessionPreset`

Pre-configured agent with system prompt.

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

#### `PersonaGenerationTrace`

PersonaGenerationTrace(agent_key: 'str', generated_at: 'str', operation: 'str', model: 'str', temperature: 'float', max_tokens: 'int', system_prompt: 'str', user_prompt: 'str', raw_response: 'str', parsed_payload: 'dict[str, object]' = <factory>, prompt_enhancements: 'list[str]' = <factory>, dependency_snapshots: 'list[DependencyFileSnapshot]' = <factory>, persona_spec: 'PersonaSpec' = <factory>, output_preset: 'dict[str, object]' = <factory>)

#### `RolePlayAnswer`

RolePlayAnswer(question: 'str', answer: 'str', perspective: 'str' = 'subjective', details: 'str' = '')

#### `RolePlayQuestion`

RolePlayQuestion(question: 'str', perspective: 'str' = 'subjective', details: 'str' = '')

### Functions

#### `async aregenerate_agent_prompt_from_dependencies(provider: LLMProvider | AsyncLLMProvider, work_path: Path, agent_key: str, model: str, dependency_files: list[str] | None, temperature: float, max_tokens: int, timeout_seconds: float, select_after_update: bool) -> GeneratedSessionPreset`

Regenerate an existing agent by re-reading its dependency files from disk.

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

#### `async aupdate_agent_prompt(provider: LLMProvider | AsyncLLMProvider, work_path: Path, agent_key: str, model: str, trait_keywords: list[str] | None, answers: list[RolePlayAnswer] | None, background: str | None, dependency_files: list[str] | None, agent_alias: str | None, output_language: str | None, temperature: float, max_tokens: int, timeout_seconds: float, select_after_update: bool) -> GeneratedSessionPreset`

Partially update the prompt for an existing agent without full rewrite.

Loads the persisted :class:`PersonaSpec` for *agent_key*, merges only
the provided patch fields, and regenerates.  Unspecified fields keep
their existing values.  Returns the newly generated preset and persists
it with the merged spec.

Raises :class:`ValueError` if no agent with *agent_key* exists or if
the agent has no persisted spec (run the initial generation first).

#### `create_session_config_from_selected_agent(work_path: Path, agent_key: str, history_max_messages: int, history_max_chars: int, max_recent_participant_messages: int, enable_auto_compression: bool, orchestration: OrchestrationPolicy | None) -> SessionConfig`

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

## providers

### Classes

#### `AliyunBailianProvider`

Aliyun Bailian provider backed by DashScope's OpenAI-compatible endpoint.

The constructor accepts either:
- https://dashscope.aliyuncs.com/compatible-mode
- https://dashscope.aliyuncs.com/compatible-mode/v1
and normalizes both to the same request endpoint.

**方法：**

- `generate(self, request: GenerationRequest) -> str` - Generate one assistant message from the upstream provider.

#### `AutoRoutingProvider`

Choose a configured provider automatically on each generation request.

**方法：**

- `generate(self, request: GenerationRequest) -> str` - Generate one assistant message from the upstream provider.

#### `DeepSeekProvider`

DeepSeek provider backed by /chat/completions.

DeepSeek is OpenAI-compatible. The constructor accepts either:
- https://api.deepseek.com
- https://api.deepseek.com/v1
and normalizes both to the same request endpoint.

**方法：**

- `generate(self, request: GenerationRequest) -> str` - Generate one assistant message from the upstream provider.

#### `MockProvider`

Deterministic provider for unit tests and local dry runs.

**方法：**

- `generate(self, request: GenerationRequest) -> str` - Generate one assistant message from the upstream provider.

#### `OpenAICompatibleProvider`

OpenAI-compatible provider backed by /v1/chat/completions.

**方法：**

- `generate(self, request: GenerationRequest) -> str` - Generate one assistant message from the upstream provider.

#### `ProviderConfig`

ProviderConfig(provider_type: 'str', api_key: 'str', base_url: 'str', healthcheck_model: 'str' = '', enabled: 'bool' = True, models: 'list[str]' = <factory>)

#### `ProviderRegistry`

Store provider credentials and routing hints under work_path.

**方法：**

- `load(self) -> dict[str, ProviderConfig]`
- `remove(self, provider_type: str) -> bool`
- `save(self, providers: dict[str, ProviderConfig]) -> None`
- `upsert(self, provider_type: str, api_key: str, base_url: str, healthcheck_model: str, models: list[str] | None) -> None`

#### `SiliconFlowProvider`

SiliconFlow provider backed by OpenAI-compatible /v1/chat/completions.

**方法：**

- `generate(self, request: GenerationRequest) -> str` - Generate one assistant message from the upstream provider.

#### `VolcengineArkProvider`

Volcengine Ark provider backed by /api/v3/chat/completions.

**方法：**

- `generate(self, request: GenerationRequest) -> str` - Generate one assistant message from the upstream provider.

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

### Functions

#### `normalize_provider_type(provider_type: str) -> str`

#### `ensure_provider_platform_supported(provider_type: str) -> str`

#### `get_supported_provider_platforms() -> dict[str, dict[str, str]]`

#### `merge_provider_sources(work_path: Path, providers_config: list[dict[str, object]]) -> dict[str, ProviderConfig]`

Merge providers from multiple sources with priority order.

Priority (high to low):
1. Session JSON: providers field
2. Persistent: <work_path>/provider_keys.json

#### `probe_provider_availability(provider: LLMProvider, model_name: str) -> None`

Run a minimal generation request to verify provider connectivity and credentials.

#### `run_provider_detection_flow(providers: dict[str, ProviderConfig]) -> None`

Framework-level provider checks.

1) Ensure provider platform/API config exists.
2) Ensure platform is supported by current framework.
3) Ensure provider is available using the registered healthcheck model.

#### `register_provider_with_validation(work_path: Path, provider_type: str, api_key: str, healthcheck_model: str, base_url: str) -> str`

Register provider only after support and availability checks pass.


---

## session

### Classes

#### `JsonPersistentSessionRunner`

High-level async runner with automatic JSON persistence.

Responsibilities:
- Manage primary user profile persistence.
- Manage transcript load/save around each turn.
- Expose simple send/reset APIs for application callers.

**方法：**

- `async initialize(self, primary_user: Participant | None, resume: bool) -> None`
- `async reset_primary_user(self, participant: Participant, clear_transcript: bool) -> None`
- `async send_user_message(self, content: str) -> Message`

#### `JsonSessionStore`

JSON-based session store for persisting sessions.

**方法：**

- `clear(self) -> None`
- `exists(self) -> bool`
- `load(self) -> Transcript`
- `save(self, transcript: Transcript) -> None`

#### `SqliteSessionStore`

SQLite-based session store for persisting sessions.

**方法：**

- `clear(self) -> None` - Remove the saved session state (deletes rows; keeps schema intact).
- `exists(self) -> bool`
- `load(self) -> Transcript`
- `save(self, transcript: Transcript) -> None`


---

## token_usage

### Classes

#### `TokenUsageBaseline`

TokenUsageBaseline(total_calls: 'int', total_prompt_tokens: 'int', total_completion_tokens: 'int', total_tokens: 'int', avg_tokens_per_call: 'float', avg_prompt_tokens_per_call: 'float', avg_completion_tokens_per_call: 'float', completion_to_prompt_ratio: 'float', retry_rate: 'float')

**方法：**

- `to_dict(self) -> TokenUsageBaselineDict`

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
- `list_sessions(self) -> list[str]`

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

#### `build_token_usage_baseline(records: list[TokenUsageRecord]) -> TokenUsageBaseline`

Build a baseline for token usage metrics.

#### `summarize_token_usage(transcript: Transcript) -> TokenUsageSummary`

Summarize token usage statistics.

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

