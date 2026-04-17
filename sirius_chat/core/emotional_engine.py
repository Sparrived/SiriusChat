"""EmotionalGroupChatEngine: new core engine for v0.28 emotional group chat.

Integrates four-layer cognitive architecture from the paper:
    Perception → Cognition (parallel) → Decision → Execution
    ↓
    Memory Foundation (Working → Episodic → Semantic)

No backward compatibility with AsyncRolePlayEngine.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from sirius_chat.core.emotion import EmotionAnalyzer
from sirius_chat.core.intent_v3 import IntentAnalyzerV3
from sirius_chat.core.proactive_trigger import ProactiveTrigger
from sirius_chat.core.response_strategy import ResponseStrategyEngine
from sirius_chat.core.delayed_response_queue import DelayedResponseQueue
from sirius_chat.core.rhythm import RhythmAnalyzer
from sirius_chat.core.model_router import ModelRouter, TaskConfig
from sirius_chat.core.response_assembler import ResponseAssembler, StyleAdapter, StyleParams
from sirius_chat.core.threshold_engine import ThresholdEngine

from sirius_chat.memory.activation_engine import ActivationEngine
from sirius_chat.memory.episodic.manager import EpisodicMemoryManager
from sirius_chat.memory.retrieval_engine import MemoryRetriever
from sirius_chat.memory.semantic.manager import SemanticMemoryManager
from sirius_chat.memory.user.manager import UserMemoryManager
from sirius_chat.memory.working.manager import WorkingMemoryManager

from sirius_chat.core.events import SessionEvent, SessionEventBus, SessionEventType
from sirius_chat.models.emotion import AssistantEmotionState, EmotionState
from sirius_chat.models.intent_v3 import IntentAnalysisV3
from sirius_chat.models.models import Message, Participant, Transcript
from sirius_chat.models.response_strategy import ResponseStrategy, StrategyDecision

logger = logging.getLogger(__name__)


class EmotionalGroupChatEngine:
    """Next-generation engine for emotional group chat (v0.28+)."""

    def __init__(
        self,
        *,
        work_path: Any,
        provider_async: Any | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.config = dict(config or {})
        self.provider_async = provider_async

        # Memory foundation
        self.working_memory = WorkingMemoryManager(
            max_size=self.config.get("working_memory_max_size", 20)
        )
        self.episodic_memory = EpisodicMemoryManager(work_path)
        self.semantic_memory = SemanticMemoryManager(work_path)
        self.user_memory = UserMemoryManager()
        self.activation_engine = ActivationEngine()

        # Cognitive layer
        self.emotion_analyzer = EmotionAnalyzer()
        self.intent_analyzer = IntentAnalyzerV3()
        self.memory_retriever = MemoryRetriever(
            working_mgr=self.working_memory,
            episodic_mgr=self.episodic_memory,
            semantic_mgr=self.semantic_memory,
            activation_engine=self.activation_engine,
        )

        # Decision layer
        self.threshold_engine = ThresholdEngine()
        self.strategy_engine = ResponseStrategyEngine()
        self.delayed_queue = DelayedResponseQueue()
        self.proactive_trigger = ProactiveTrigger(
            silence_threshold_minutes=self.config.get("proactive_silence_minutes", 30),
        )
        self.rhythm_analyzer = RhythmAnalyzer()

        # Execution layer
        self.response_assembler = ResponseAssembler()
        self.style_adapter = StyleAdapter()
        self.model_router = ModelRouter(
            overrides=self.config.get("task_model_overrides"),
        )

        # Persistence
        from sirius_chat.core.engine_persistence import EngineStateStore
        self._state_store = EngineStateStore(work_path)

        # Assistant state
        self.assistant_emotion = AssistantEmotionState()

        # Group runtime state
        self._group_last_message_at: dict[str, str] = {}
        self._transcripts: dict[str, Transcript] = {}

        # Event bus
        self.event_bus = SessionEventBus()

    # ==================================================================
    # Public API
    # ==================================================================

    async def process_message(
        self,
        message: Message,
        participants: list[Participant],
        group_id: str,
    ) -> dict[str, Any]:
        """Process a single incoming message through the full pipeline.

        Returns a dict with at least:
            - strategy: str (immediate / delayed / silent / proactive)
            - reply: str | None
            - emotion: dict
            - intent: dict
        """
        user_id = message.speaker or "unknown"
        content = message.content

        # 1. Perception
        self._perception(group_id, message, participants)
        await self.event_bus.emit(SessionEvent(
            type=SessionEventType.PERCEPTION_COMPLETED,
            data={"group_id": group_id, "user_id": user_id},
        ))

        # 2. Cognition (parallel)
        intent, emotion, memories = await self._cognition(
            content, user_id, group_id
        )
        await self.event_bus.emit(SessionEvent(
            type=SessionEventType.COGNITION_COMPLETED,
            data={
                "group_id": group_id,
                "user_id": user_id,
                "intent": intent.to_dict(),
                "emotion": emotion.to_dict(),
            },
        ))

        # 3. Decision
        decision = self._decision(intent, emotion, group_id, user_id)
        await self.event_bus.emit(SessionEvent(
            type=SessionEventType.DECISION_COMPLETED,
            data={
                "group_id": group_id,
                "strategy": decision.strategy.value,
                "priority": getattr(decision, "priority", None),
            },
        ))

        # 4. Execution
        result = await self._execution(decision, message, intent, emotion, memories, group_id)
        await self.event_bus.emit(SessionEvent(
            type=SessionEventType.EXECUTION_COMPLETED,
            data={
                "group_id": group_id,
                "strategy": result.get("strategy"),
                "has_reply": result.get("reply") is not None,
            },
        ))

        # 5. Background memory updates
        self._background_update(group_id, message, emotion, intent)

        return result

    async def proactive_check(self, group_id: str) -> dict[str, Any] | None:
        """Check if proactive trigger should fire for a group."""
        last_at = self._group_last_message_at.get(group_id)
        group_profile = self.semantic_memory.ensure_group_profile(group_id)

        trigger = self.proactive_trigger.check(
            group_id,
            last_message_at=last_at,
            group_atmosphere={
                "valence": getattr(group_profile.atmosphere_history[-1], "group_valence", 0.0)
                if group_profile.atmosphere_history else 0.0,
            },
        )
        if not trigger:
            return None

        # Generate proactive message
        prompt = self._build_proactive_prompt(trigger, group_id)
        reply = await self._generate(prompt, group_id)

        await self.event_bus.emit(SessionEvent(
            type=SessionEventType.PROACTIVE_RESPONSE_TRIGGERED,
            data={
                "group_id": group_id,
                "trigger_type": trigger["trigger_type"],
            },
        ))

        return {
            "strategy": "proactive",
            "trigger_type": trigger["trigger_type"],
            "reply": reply,
        }

    async def tick_delayed_queue(self, group_id: str) -> list[dict[str, Any]]:
        """Process delayed response queue for a group."""
        recent = self._get_recent_messages(group_id, n=10)
        triggered = self.delayed_queue.tick(group_id, recent)
        results = []
        for item in triggered:
            prompt = self._build_delayed_prompt(item)
            reply = await self._generate(prompt, group_id)
            results.append({
                "strategy": "delayed",
                "item_id": item.item_id,
                "reply": reply,
            })
            await self.event_bus.emit(SessionEvent(
                type=SessionEventType.DELAYED_RESPONSE_TRIGGERED,
                data={
                    "group_id": group_id,
                    "item_id": item.item_id,
                },
            ))
        return results

    # ==================================================================
    # Persistence
    # ==================================================================

    def save_state(self) -> None:
        """Persist all runtime state to disk."""
        working_memories: dict[str, list[dict[str, Any]]] = {}
        for group_id in self.working_memory.list_groups():
            entries = self.working_memory.get_recent_entries(group_id, n=100)
            working_memories[group_id] = [
                {
                    "user_id": e.user_id,
                    "role": e.role,
                    "content": e.content,
                    "timestamp": e.timestamp,
                    "importance": e.importance,
                }
                for e in entries
            ]

        import dataclasses
        self._state_store.save_all(
            working_memories=working_memories,
            assistant_emotion=dataclasses.asdict(self.assistant_emotion),
            delayed_queue=[],
            group_timestamps=dict(self._group_last_message_at),
        )

    def load_state(self) -> None:
        """Restore runtime state from disk."""
        state = self._state_store.load_all()

        # Working memory
        for group_id, entries in state.get("working_memories", {}).items():
            for e in entries:
                self.working_memory.add_entry(
                    group_id=group_id,
                    user_id=e.get("user_id", "unknown"),
                    role=e.get("role", "human"),
                    content=e.get("content", ""),
                    importance=e.get("importance", 0.5),
                )

        # Assistant emotion
        ae = state.get("assistant_emotion")
        if ae:
            for key, value in ae.items():
                if hasattr(self.assistant_emotion, key):
                    setattr(self.assistant_emotion, key, value)

        # Group timestamps
        self._group_last_message_at = dict(state.get("group_timestamps", {}))

        logger.info("Engine state loaded | groups=%d", len(state.get("working_memories", {})))

    # ==================================================================
    # Pipeline stages
    # ==================================================================

    def _perception(
        self,
        group_id: str,
        message: Message,
        participants: list[Participant],
    ) -> None:
        """Perception layer: normalize, register participants, update transcript."""
        # Register participants in user memory
        for p in participants:
            self.user_memory.register_user(
                profile=p.as_user_profile(),
                group_id=group_id,
            )

        # Add to working memory
        self.working_memory.add_entry(
            group_id=group_id,
            user_id=message.speaker or "unknown",
            role="human",
            content=message.content,
            channel=message.channel or "",
            channel_user_id=message.channel_user_id or "",
        )

        # Update group last message time
        self._group_last_message_at[group_id] = _now_iso()

    async def _cognition(
        self,
        content: str,
        user_id: str,
        group_id: str,
    ) -> tuple[IntentAnalysisV3, EmotionState, list[dict[str, Any]]]:
        """Cognitive layer: parallel intent + emotion + memory retrieval."""
        # Emotion analysis
        emotion = self.emotion_analyzer.analyze(content, user_id, group_id)

        # Intent analysis
        intent = self.intent_analyzer.analyze(
            content, user_id, group_id, emotion_state=emotion
        )

        # Memory retrieval (async to allow future semantic search)
        memories = await self.memory_retriever.retrieve(
            query=content,
            group_id=group_id,
            user_id=user_id,
            top_k=5,
            enable_semantic=self.config.get("enable_semantic_retrieval", False),
        )

        return intent, emotion, memories

    def _decision(
        self,
        intent: IntentAnalysisV3,
        emotion: EmotionState,
        group_id: str,
        user_id: str,
    ) -> StrategyDecision:
        """Decision layer: strategy selection with threshold and rhythm."""
        # Rhythm context
        recent_msgs = self._get_recent_messages(group_id, n=10)
        rhythm = self.rhythm_analyzer.analyze(group_id, recent_msgs)

        # Update threshold with rhythm
        intent.activity_factor = self._activity_factor(rhythm.heat_level)
        intent.time_factor = self._time_factor()

        # Check if directly mentioned
        is_mentioned = intent.directed_at_current_ai

        decision = self.strategy_engine.decide(
            intent,
            is_mentioned=is_mentioned,
        )

        # Update assistant emotion
        self.assistant_emotion.update_from_interaction(emotion, user_id)

        return decision

    async def _execution(
        self,
        decision: StrategyDecision,
        message: Message,
        intent: IntentAnalysisV3,
        emotion: EmotionState,
        memories: list[dict[str, Any]],
        group_id: str,
    ) -> dict[str, Any]:
        """Execution layer: generate or queue reply."""
        # Rhythm context for style adaptation
        recent_msgs = self._get_recent_messages(group_id, n=10)
        rhythm = self.rhythm_analyzer.analyze(group_id, recent_msgs)

        # Empathy strategy
        empathy = self.emotion_analyzer.select_empathy_strategy(
            emotion, message.speaker or ""
        )

        # Profiles
        group_profile = self.semantic_memory.get_group_profile(group_id)
        user_profile = (
            self.semantic_memory.get_user_profile(group_id, message.speaker or "")
            if message.speaker else None
        )

        if decision.strategy == ResponseStrategy.IMMEDIATE:
            prompt = self.response_assembler.assemble(
                message=message,
                intent=intent,
                emotion=emotion,
                empathy_strategy=empathy,
                memories=memories,
                group_profile=group_profile,
                user_profile=user_profile,
                assistant_emotion=self.assistant_emotion,
                heat_level=rhythm.heat_level,
                pace=rhythm.pace,
                topic_stability=rhythm.topic_stability,
            )
            style = self.style_adapter.adapt(
                heat_level=rhythm.heat_level,
                pace=rhythm.pace,
                user_communication_style=getattr(user_profile, "communication_style", ""),
                topic_stability=rhythm.topic_stability,
            )
            reply = await self._generate(prompt, group_id, style)
            return {
                "strategy": "immediate",
                "reply": reply,
                "emotion": emotion.to_dict(),
                "intent": intent.to_dict(),
            }

        if decision.strategy == ResponseStrategy.DELAYED:
            self.delayed_queue.enqueue(
                group_id=group_id,
                user_id=message.speaker or "unknown",
                message_content=message.content,
                strategy_decision=decision,
                emotion_state=emotion.to_dict(),
                candidate_memories=[m.get("content", "") for m in memories],
            )
            return {
                "strategy": "delayed",
                "reply": None,
                "emotion": emotion.to_dict(),
                "intent": intent.to_dict(),
            }

        # SILENT or PROACTIVE
        return {
            "strategy": decision.strategy.value,
            "reply": None,
            "emotion": emotion.to_dict(),
            "intent": intent.to_dict(),
        }

    def _background_update(
        self,
        group_id: str,
        message: Message,
        emotion: EmotionState,
        intent: IntentAnalysisV3,
    ) -> None:
        """Background updates after main pipeline."""
        # Update group atmosphere
        group_profile = self.semantic_memory.ensure_group_profile(group_id)
        from sirius_chat.memory.semantic.models import AtmosphereSnapshot
        group_profile.atmosphere_history.append(AtmosphereSnapshot(
            timestamp=_now_iso(),
            group_valence=emotion.valence,
            group_arousal=emotion.arousal,
        ))
        if len(group_profile.atmosphere_history) > 1000:
            group_profile.atmosphere_history = group_profile.atmosphere_history[-1000:]
        self.semantic_memory.save_group_profile(group_profile)

    # ==================================================================
    # Prompt builders & generation
    # ==================================================================

    def _build_delayed_prompt(self, item: Any) -> str:
        """Build prompt for delayed response."""
        return self.response_assembler.assemble_delayed(
            message_content=item.message_content,
            group_profile=self.semantic_memory.get_group_profile(item.group_id),
        )

    def _build_proactive_prompt(self, trigger: dict[str, Any], group_id: str) -> str:
        """Build prompt for proactive initiation."""
        return self.response_assembler.assemble_proactive(
            trigger_reason=trigger.get("trigger_type", "silence"),
            group_profile=self.semantic_memory.get_group_profile(group_id),
            suggested_tone=trigger.get("suggested_tone", "casual"),
        )

    async def _generate(
        self,
        prompt: str,
        group_id: str,
        style_params: StyleParams | None = None,
        task_name: str = "response_generate",
        urgency: int = 0,
    ) -> str:
        """Call LLM provider to generate response.

        Args:
            prompt: The assembled prompt text.
            group_id: Target group identifier.
            style_params: Optional style parameters (max_tokens, temperature).
            task_name: Cognitive task type for model routing.
            urgency: Urgency score (0-100) for dynamic escalation.
        """
        if self.provider_async is None:
            return "[未配置 provider]"

        # Model routing
        recent = self._get_recent_messages(group_id, n=5)
        rhythm = self.rhythm_analyzer.analyze(group_id, recent)
        cfg = self.model_router.resolve(
            task_name,
            urgency=urgency,
            heat_level=rhythm.heat_level,
        )

        # Apply style params if provided (override router's max_tokens)
        if style_params:
            effective_max_tokens = min(cfg.max_tokens, style_params.max_tokens)
            effective_temperature = style_params.temperature
        else:
            effective_max_tokens = cfg.max_tokens
            effective_temperature = cfg.temperature

        # TODO: Wire to actual provider with effective_max_tokens / effective_temperature / cfg.model_name
        _ = effective_max_tokens, effective_temperature, cfg.model_name  # used when provider wired
        return "[ generated response placeholder ]"

    # ==================================================================
    # Helpers
    # ==================================================================

    def _get_recent_messages(self, group_id: str, n: int = 10) -> list[dict[str, Any]]:
        entries = self.working_memory.get_recent_entries(group_id, n=n)
        return [
            {
                "user_id": e.user_id,
                "content": e.content,
                "timestamp": e.timestamp,
            }
            for e in entries
        ]

    @staticmethod
    def _activity_factor(heat_level: str) -> float:
        return {"cold": 0.8, "warm": 1.0, "hot": 1.3, "overheated": 1.6}.get(heat_level, 1.0)

    @staticmethod
    def _time_factor() -> float:
        hour = datetime.now(timezone.utc).hour
        if 0 <= hour < 6:
            return 1.3
        if 9 <= hour < 18:
            return 1.1
        if 19 <= hour < 23:
            return 0.9
        return 1.0

    def _describe_atmosphere(self, group_id: str) -> str:
        recent = self._get_recent_messages(group_id, n=5)
        rhythm = self.rhythm_analyzer.analyze(group_id, recent)
        mood = "轻松" if rhythm.heat_level in ("warm", "hot") else "安静"
        return f"{mood} ({rhythm.heat_level})"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
