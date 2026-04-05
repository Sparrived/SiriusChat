from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sirius_chat.memory import UserMemoryEntry, UserMemoryManager, UserProfile
from sirius_chat.config import TokenUsageRecord, OrchestrationPolicy


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
    """Multi-user participant representation with auto-generated unique user_id.
    
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
    """
    
    name: str
    user_id: str = ""
    persona: str = ""
    identities: dict[str, str] = field(default_factory=dict)
    aliases: list[str] = field(default_factory=list)
    traits: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Auto-generate unique user_id if not provided."""
        if not self.user_id:
            # Generate UUID-based user_id for guaranteed uniqueness
            # Format: "user_<uuid>" for readability
            self.user_id = f"user_{uuid.uuid4().hex[:12]}"

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
