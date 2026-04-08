"""Configuration data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class Agent:
    """AI agent definition with model and parameters."""
    
    name: str
    persona: str
    model: str
    temperature: float = 0.7
    max_tokens: int = 512
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgentPreset:
    """Pre-configured agent with system prompt."""
    
    agent: Agent
    global_system_prompt: str


@dataclass(slots=True)
class MemoryPolicy:
    """Centralized memory system configuration.
    
    Controls memory fact limits, confidence thresholds, decay behaviour,
    observed-set caps and prompt-injection budget.
    """
    max_facts_per_user: int = 50
    transient_confidence_threshold: float = 0.85
    event_dedup_window_minutes: int = 5
    max_observed_set_size: int = 100
    max_summary_facts_per_type: int = 5
    max_summary_total_chars: int = 2000
    decay_schedule: dict[int, float] = field(default_factory=lambda: {
        7: 0.95,
        30: 0.80,
        60: 0.55,
        90: 0.30,
        180: 0.05,
    })


@dataclass(slots=True)
class OrchestrationPolicy:
    """Multi-model orchestration strategy (required).
    
    Supports two configuration approaches:
    
    Approach 1 - Unified Model: all tasks use the same model
        - Set unified_model: model name
        - Simplifies configuration, suitable for small task volumes
        
    Approach 2 - Per-Task Configuration: specify model for each task
        - Set task_models: {"memory_extract": "model-a", "event_extract": "model-b", ...}
        - Supports fine-grained task-level control
    
    Task Enablement:
        - All tasks (memory_extract, event_extract, multimodal_parse) enabled by default
        - Use task_enabled dict to enable/disable specific tasks
        - Example: task_enabled={"memory_extract": False} disables memory extraction tasks
    """
    # Configuration approach selection (choose one, cannot both be empty)
    unified_model: str = ""  # Approach 1: all tasks use this model (higher priority)
    task_models: dict[str, str] = field(default_factory=dict)  # Approach 2: per-task configuration
    
    # Task enablement control (bool fields, all enabled by default)
    task_enabled: dict[str, bool] = field(default_factory=lambda: {
        "memory_extract": True,
        "multimodal_parse": True,
        "event_extract": True,
    })
    
    # Per-task parameter tuning
    task_budgets: dict[str, int] = field(default_factory=dict)  # token limits (optional)
    task_temperatures: dict[str, float] = field(default_factory=dict)
    task_max_tokens: dict[str, int] = field(default_factory=dict)
    task_retries: dict[str, int] = field(default_factory=dict)
    
    # Multimodal processing configuration
    max_multimodal_inputs_per_turn: int = 4
    max_multimodal_value_length: int = 4096
    
    # Prompt-driven content splitting (AI autonomously decides granularity)
    enable_prompt_driven_splitting: bool = True
    split_marker: str = "<MSG_SPLIT>"
    
    # Memory Manager configuration (enabled when memory_manager_model is set)
    memory_manager_model: str = ""
    memory_manager_temperature: float = 0.3
    memory_manager_max_tokens: int = 512
    
    # Memory Extract frequency control (避免调用过于频繁导致内容碎片化)
    memory_extract_batch_size: int = 1  # 每隔N条消息执行一次提取（1=每次，3=每3条）
    memory_extract_min_content_length: int = 0  # 最小内容长度阈值（字符数），0=无限制

    # Event Extract batch size (v2: 每N条消息批量提取一次用户观察)
    event_extract_batch_size: int = 5  # 每隔N条消息执行一次事件观察提取

    # Intent analysis (意图分析器)
    enable_intent_analysis: bool = True  # 是否启用 LLM 意图分析
    intent_analysis_model: str = ""  # 意图分析使用的模型（为空则使用 unified_model）

    # Background memory consolidation (后台记忆归纳)
    consolidation_enabled: bool = True  # 是否启用定时记忆归纳
    consolidation_interval_seconds: int = 7200  # 归纳间隔（秒）
    consolidation_min_entries: int = 6  # 事件最少条数
    consolidation_min_notes: int = 4   # 摘要最少条数
    consolidation_min_facts: int = 15  # 事实最少条数

    # Reply willingness configuration (auto reply mode)
    session_reply_mode: str = "always"  # auto|always|never
    auto_reply_base_score: float = 0.22
    auto_reply_threshold: float = 0.58
    auto_reply_threshold_min: float = 0.40
    auto_reply_threshold_max: float = 0.72
    auto_reply_threshold_boost_start_count: int = 4
    auto_reply_probability_coefficient: float = 0.35
    auto_reply_probability_floor: float = 0.05
    auto_reply_user_cadence_seconds: float = 7.0
    auto_reply_group_window_seconds: float = 8.0
    auto_reply_group_penalty_start_count: int = 2
    auto_reply_assistant_cooldown_seconds: float = 12.0
    
    # Message debounce: buffer same-user messages within this window (seconds).
    # 0 = disabled (immediate reply). Recommended: 3.0~5.0 for group chat.
    message_debounce_seconds: float = 5.0
    
    # Memory policy (centralized memory system configuration)
    memory: MemoryPolicy = field(default_factory=MemoryPolicy)

    # Skill system: allow AI to invoke external code via SKILL_CALL
    enable_skills: bool = True
    skill_call_marker: str = "[SKILL_CALL:"
    max_skill_rounds: int = 3  # max consecutive skill call rounds per turn
    skill_execution_timeout: float = 30.0  # max seconds per SKILL execution, 0 = no limit
    auto_install_skill_deps: bool = True  # auto-install missing SKILL dependencies via uv/pip
    
    def validate(self) -> None:
        """Validate configuration legitimacy."""
        if not self.unified_model and not self.task_models:
            raise ValueError(
                "Multi-model orchestration configuration error: must specify either "
                "unified_model (approach 1) or task_models (approach 2)."
            )
        
        if self.unified_model and self.task_models:
            raise ValueError(
                "Multi-model orchestration configuration error: unified_model (approach 1) "
                "and task_models (approach 2) cannot be specified simultaneously. "
                "Please choose one approach."
            )

        if self.memory_extract_batch_size <= 0:
            raise ValueError("memory_extract_batch_size 必须大于 0。")
        if self.memory_extract_min_content_length < 0:
            raise ValueError("memory_extract_min_content_length 不能小于 0。")

        if self.event_extract_batch_size <= 0:
            raise ValueError("event_extract_batch_size 必须大于 0。")

        normalized_reply_mode = self.session_reply_mode.strip().lower()
        if normalized_reply_mode not in {
            "auto",
            "smart",
            "always",
            "never",
            "silent",
            "none",
            "no_reply",
        }:
            raise ValueError(
                "session_reply_mode 仅支持 auto/smart/always/never/silent/none/no_reply。"
            )

        if not 0.0 <= self.auto_reply_base_score <= 1.0:
            raise ValueError("auto_reply_base_score 必须在 [0,1] 范围内。")
        if not 0.0 <= self.auto_reply_threshold <= 1.0:
            raise ValueError("auto_reply_threshold 必须在 [0,1] 范围内。")
        if not 0.0 <= self.auto_reply_threshold_min <= 1.0:
            raise ValueError("auto_reply_threshold_min 必须在 [0,1] 范围内。")
        if not 0.0 <= self.auto_reply_threshold_max <= 1.0:
            raise ValueError("auto_reply_threshold_max 必须在 [0,1] 范围内。")
        if self.auto_reply_threshold_min > self.auto_reply_threshold_max:
            raise ValueError("auto_reply_threshold_min 不能大于 auto_reply_threshold_max。")
        if self.auto_reply_threshold_boost_start_count < 0:
            raise ValueError("auto_reply_threshold_boost_start_count 不能小于 0。")
        if not 0.0 <= self.auto_reply_probability_coefficient <= 1.0:
            raise ValueError("auto_reply_probability_coefficient 必须在 [0,1] 范围内。")
        if not 0.0 <= self.auto_reply_probability_floor <= 1.0:
            raise ValueError("auto_reply_probability_floor 必须在 [0,1] 范围内。")
        if self.auto_reply_user_cadence_seconds <= 0:
            raise ValueError("auto_reply_user_cadence_seconds 必须大于 0。")
        if self.auto_reply_group_window_seconds <= 0:
            raise ValueError("auto_reply_group_window_seconds 必须大于 0。")
        if self.auto_reply_group_penalty_start_count < 0:
            raise ValueError("auto_reply_group_penalty_start_count 不能小于 0。")
        if self.auto_reply_assistant_cooldown_seconds <= 0:
            raise ValueError("auto_reply_assistant_cooldown_seconds 必须大于 0。")
        if self.message_debounce_seconds < 0:
            raise ValueError("message_debounce_seconds 不能小于 0。")


@dataclass(slots=True)
class TokenUsageRecord:
    """Record of token usage for a task execution."""
    
    actor_id: str
    task_name: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    input_chars: int = 0
    output_chars: int = 0
    estimation_method: str = "char_div4"
    retries_used: int = 0


@dataclass(slots=True, init=False)
class SessionConfig:
    """Session configuration including agent, paths, and orchestration policy."""
    
    preset: AgentPreset
    work_path: Path
    history_max_messages: int = 24
    history_max_chars: int = 6000
    max_recent_participant_messages: int = 5
    enable_auto_compression: bool = True
    orchestration: OrchestrationPolicy = field(default_factory=OrchestrationPolicy)

    def __init__(
        self,
        *,
        work_path: Path,
        preset: AgentPreset,
        history_max_messages: int = 24,
        history_max_chars: int = 6000,
        max_recent_participant_messages: int = 5,
        enable_auto_compression: bool = True,
        orchestration: OrchestrationPolicy | None = None,
    ) -> None:
        self.preset = preset
        self.work_path = Path(work_path)
        self.history_max_messages = history_max_messages
        self.history_max_chars = history_max_chars
        self.max_recent_participant_messages = max_recent_participant_messages
        self.enable_auto_compression = enable_auto_compression
        
        # If no orchestration provided, create default: use main AI model as unified model
        if orchestration is None:
            orchestration = OrchestrationPolicy(unified_model=preset.agent.model)
        
        self.orchestration = orchestration
        # Validate multi-model orchestration configuration
        self.orchestration.validate()

    @property
    def agent(self) -> Agent:
        return self.preset.agent

    @agent.setter
    def agent(self, value: Agent) -> None:
        self.preset = AgentPreset(agent=value, global_system_prompt=self.preset.global_system_prompt)

    @property
    def global_system_prompt(self) -> str:
        return self.preset.global_system_prompt

    @global_system_prompt.setter
    def global_system_prompt(self, value: str) -> None:
        self.preset = AgentPreset(agent=self.preset.agent, global_system_prompt=value)
