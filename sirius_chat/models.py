from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sirius_chat.user_memory import UserMemoryEntry, UserMemoryManager, UserProfile


@dataclass(slots=True)
class Message:
    role: str
    content: str
    speaker: str | None = None
    channel: str | None = None
    channel_user_id: str | None = None
    multimodal_inputs: list[dict[str, str]] = field(default_factory=list)


@dataclass(slots=True)
class Participant:
    name: str
    user_id: str = ""
    persona: str = ""
    identities: dict[str, str] = field(default_factory=dict)
    aliases: list[str] = field(default_factory=list)
    traits: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.user_id:
            self.user_id = self.name

    def as_user_profile(self) -> UserProfile:
        return UserProfile(
            user_id=self.user_id,
            name=self.name,
            persona=self.persona,
            identities=self.identities,
            aliases=self.aliases,
            traits=self.traits,
            metadata=self.metadata,
        )


# External-facing alias: callers can construct User(...) explicitly.
User = Participant


@dataclass(slots=True)
class Agent:
    name: str
    persona: str
    model: str
    temperature: float = 0.7
    max_tokens: int = 512
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgentPreset:
    agent: Agent
    global_system_prompt: str


@dataclass(slots=True)
class OrchestrationPolicy:
    """多模型协同策略（必需）。支持两种配置方案：
    
    方案1 - 统一模型：所有任务使用同一个模型
        - 设置 unified_model: 模型名称
        - 简化配置，适合任务量小的场景
        
    方案2 - 按任务配置：为每个任务分别指定模型
        - 设置 task_models: {"memory_extract": "model-a", "event_extract": "model-b", ...}
        - 支持任务级细粒度控制
    
    任务启用：
        - 所有任务（memory_extract、event_extract、multimodal_parse）默认启用
        - 通过 task_enabled 字典来启用/禁用特定任务
        - 例如：task_enabled={"memory_extract": False} 禁用内存提取任务
    """
    # 配置方案选择（二选一，不能同时为空）
    unified_model: str = ""  # 方案1：所有任务使用此模型（优先级高）
    task_models: dict[str, str] = field(default_factory=dict)  # 方案2：按任务配置模型
    
    # 任务启用控制（bool 字段，默认全部启用）
    task_enabled: dict[str, bool] = field(default_factory=lambda: {
        "memory_extract": True,
        "multimodal_parse": True,
        "event_extract": True,
    })
    
    # 任务级参数调优
    task_budgets: dict[str, int] = field(default_factory=dict)  # token 限额（可选）
    task_temperatures: dict[str, float] = field(default_factory=dict)
    task_max_tokens: dict[str, int] = field(default_factory=dict)
    task_retries: dict[str, int] = field(default_factory=dict)
    
    # 多模态处理配置
    max_multimodal_inputs_per_turn: int = 4
    max_multimodal_value_length: int = 4096
    
    # 提示词驱动的内容分割（AI 自主决定分割粒度）
    enable_prompt_driven_splitting: bool = True
    split_marker: str = "[MSG_BREAK]"
    
    # Memory Manager 配置（memory_manager_model 非空时启用）
    memory_manager_model: str = ""
    memory_manager_temperature: float = 0.3
    memory_manager_max_tokens: int = 512
    
    def validate(self) -> None:
        """验证配置的合法性。"""
        if not self.unified_model and not self.task_models:
            raise ValueError(
                "多模型协同配置错误：必须指定 unified_model（方案1）或 task_models（方案2）之一。"
            )
        
        if self.unified_model and self.task_models:
            raise ValueError(
                "多模型协同配置错误：unified_model（方案1）和 task_models（方案2）不能同时指定，请选择其中一种。"
            )


@dataclass(slots=True)
class TokenUsageRecord:
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
        
        # 如果没有提供 orchestration，创建默认配置：使用主 AI 模型作为统一模型
        if orchestration is None:
            orchestration = OrchestrationPolicy(
                unified_model=preset.agent.model,
                # unified_model 模式下禁用辅助任务（不产生额外开销）
                task_enabled={
                    "memory_extract": False,
                    "multimodal_parse": False,
                    "event_extract": False,
                }
            )
        
        self.orchestration = orchestration
        # 验证多模型协同配置
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


@dataclass(slots=True)
class Transcript:
    messages: list[Message] = field(default_factory=list)
    user_memory: UserMemoryManager = field(default_factory=UserMemoryManager)
    session_summary: str = ""
    orchestration_stats: dict[str, dict[str, int]] = field(default_factory=dict)
    token_usage_records: list[TokenUsageRecord] = field(default_factory=list)

    def add(self, message: Message) -> None:
        self.messages.append(message)

    def add_token_usage_record(self, record: TokenUsageRecord) -> None:
        self.token_usage_records.append(record)

    def remember_participant(
        self,
        *,
        participant: Participant,
        content: str,
        max_recent_messages: int = 5,
        channel: str | None = None,
        channel_user_id: str | None = None,
    ) -> None:
        self.user_memory.remember_message(
            profile=participant.as_user_profile(),
            content=content,
            max_recent_messages=max_recent_messages,
            channel=channel,
            channel_user_id=channel_user_id,
        )

    def find_user_by_channel_uid(self, *, channel: str, uid: str) -> UserMemoryEntry | None:
        return self.user_memory.get_user_by_identity(channel=channel, external_user_id=uid)

    def _generate_summary(self, archived_messages: list[Message], max_items: int = 8) -> str:
        items: list[str] = []
        for message in archived_messages:
            if not message.speaker:
                continue
            text = message.content.replace("\n", " ").strip()
            if not text:
                continue
            items.append(f"[{message.speaker}] {text[:60]}")
            if len(items) >= max_items:
                break
        return " | ".join(items)

    def compress_for_budget(self, *, max_messages: int, max_chars: int) -> None:
        if max_messages <= 0 or max_chars <= 0:
            return

        if len(self.messages) > max_messages:
            archived = self.messages[:-max_messages]
            summary_piece = self._generate_summary(archived)
            if summary_piece:
                if self.session_summary:
                    self.session_summary = f"{self.session_summary} || {summary_piece}"
                else:
                    self.session_summary = summary_piece
            self.messages = self.messages[-max_messages:]

        def _total_chars() -> int:
            return sum(len(item.content) for item in self.messages) + len(self.session_summary)

        while len(self.messages) > 2 and _total_chars() > max_chars:
            archived = [self.messages.pop(0)]
            summary_piece = self._generate_summary(archived, max_items=1)
            if summary_piece:
                if self.session_summary:
                    self.session_summary = f"{self.session_summary} || {summary_piece}"
                else:
                    self.session_summary = summary_piece

        if len(self.session_summary) > max_chars:
            self.session_summary = self.session_summary[-max_chars:]

    def to_dict(self) -> dict[str, Any]:
        return {
            "messages": [
                {
                    "role": item.role,
                    "content": item.content,
                    "speaker": item.speaker,
                    "channel": item.channel,
                    "channel_user_id": item.channel_user_id,
                    "multimodal_inputs": item.multimodal_inputs,
                }
                for item in self.messages
            ],
            "user_memory": self.user_memory.to_dict(),
            "session_summary": self.session_summary,
            "orchestration_stats": self.orchestration_stats,
            "token_usage_records": [
                {
                    "actor_id": item.actor_id,
                    "task_name": item.task_name,
                    "model": item.model,
                    "prompt_tokens": item.prompt_tokens,
                    "completion_tokens": item.completion_tokens,
                    "total_tokens": item.total_tokens,
                    "input_chars": item.input_chars,
                    "output_chars": item.output_chars,
                    "estimation_method": item.estimation_method,
                    "retries_used": item.retries_used,
                }
                for item in self.token_usage_records
            ],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Transcript":
        transcript = cls(
            messages=[
                Message(
                    role=item["role"],
                    content=item["content"],
                    speaker=item.get("speaker"),
                    channel=item.get("channel"),
                    channel_user_id=item.get("channel_user_id"),
                    multimodal_inputs=list(item.get("multimodal_inputs", [])),
                )
                for item in payload.get("messages", [])
            ],
            session_summary=str(payload.get("session_summary", "")),
            orchestration_stats=dict(payload.get("orchestration_stats", {})),
            token_usage_records=[
                TokenUsageRecord(
                    actor_id=str(item.get("actor_id", "unknown")),
                    task_name=str(item.get("task_name", "unknown")),
                    model=str(item.get("model", "")),
                    prompt_tokens=int(item.get("prompt_tokens", 0)),
                    completion_tokens=int(item.get("completion_tokens", 0)),
                    total_tokens=int(item.get("total_tokens", 0)),
                    input_chars=int(item.get("input_chars", 0)),
                    output_chars=int(item.get("output_chars", 0)),
                    estimation_method=str(item.get("estimation_method", "char_div4")),
                    retries_used=int(item.get("retries_used", 0)),
                )
                for item in payload.get("token_usage_records", [])
            ],
        )
        if "user_memory" in payload:
            transcript.user_memory = UserMemoryManager.from_dict(payload.get("user_memory", {}))
        else:
            # Backward compatibility for old state files.
            raw_memories = payload.get("participant_memories", {})
            for name, item in raw_memories.items():
                participant = Participant(
                    name=item.get("name", name),
                    user_id=name,
                    persona=item.get("persona", ""),
                )
                for text in list(item.get("recent_messages", [])):
                    transcript.remember_participant(
                        participant=participant,
                        content=text,
                        max_recent_messages=64,
                    )
        return transcript

    def as_chat_history(self) -> list[dict[str, str]]:
        history: list[dict[str, str]] = []
        for message in self.messages:
            if message.speaker:
                content = f"[{message.speaker}] {message.content}"
            else:
                content = message.content
            history.append({"role": message.role, "content": content})
        return history
