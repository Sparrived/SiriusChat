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

### Functions

#### `create_async_engine(provider: LLMProvider | AsyncLLMProvider) -> AsyncRolePlayEngine`

Create an async roleplay engine for non-blocking integration.

#### `async ainit_live_session(engine: AsyncRolePlayEngine, config: SessionConfig, transcript: Transcript | None) -> Transcript`

Async facade for live session initialization.

#### `async arun_live_message(engine: AsyncRolePlayEngine, config: SessionConfig, turn: Message, transcript: Transcript | None) -> Transcript`

Async facade for single-message live processing.

#### `async asubscribe(engine: AsyncRolePlayEngine, transcript: Transcript, *, max_queue_size: int = 256) -> AsyncIterator[SessionEvent]`

Subscribe to real-time session events. Returns an async iterator of `SessionEvent` objects.

#### `find_user_by_channel_uid(transcript: Transcript, channel: str, uid: str) -> UserMemoryEntry | None`

Stable external lookup by channel + uid.


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
    ...     task_models={"memory_extract": "model-1"},
    ...     task_budgets={"memory_extract": 1200},
    ... )
    >>> orchestration = mm_config.to_orchestration_policy()


---

