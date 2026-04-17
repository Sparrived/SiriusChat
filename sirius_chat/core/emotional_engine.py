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
import re
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
        persona: Any | None = None,
    ) -> None:
        self.config = dict(config or {})
        self.provider_async = provider_async
        self.work_path = work_path

        # Persona loading
        from sirius_chat.core.persona_store import PersonaStore
        from sirius_chat.core.persona_generator import PersonaGenerator
        from sirius_chat.models.persona import PersonaProfile

        if persona is not None:
            self.persona = persona if isinstance(persona, PersonaProfile) else PersonaProfile.from_dict(dict(persona))
        else:
            # Try load from disk
            loaded = PersonaStore.load(work_path)
            if loaded:
                self.persona = loaded
            else:
                # Create default warm_friend persona
                self.persona = PersonaGenerator.from_template("warm_friend")
                PersonaStore.save(work_path, self.persona)
                logger.info("Created default persona: %s", self.persona.name)

        # Memory foundation
        self.working_memory = WorkingMemoryManager(
            max_size=self.config.get("working_memory_max_size", 20)
        )
        self.episodic_memory = EpisodicMemoryManager(work_path)
        self.semantic_memory = SemanticMemoryManager(work_path)
        self.user_memory = UserMemoryManager()
        self.activation_engine = ActivationEngine()

        # Cognitive layer
        self.emotion_analyzer = EmotionAnalyzer(provider_async=provider_async)
        self.intent_analyzer = IntentAnalyzerV3(provider_async=provider_async)
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

        # Execution layer (persona-injected)
        self.response_assembler = ResponseAssembler(persona=self.persona)
        self.style_adapter = StyleAdapter()
        self.model_router = ModelRouter(
            overrides=self.config.get("task_model_overrides"),
        )

        # Persistence
        from sirius_chat.core.engine_persistence import EngineStateStore
        self._state_store = EngineStateStore(work_path)

        # Assistant state (persona emotional baseline)
        baseline = self.persona.emotional_baseline
        self.assistant_emotion = AssistantEmotionState(
            valence=baseline.get("valence", 0.2),
            arousal=baseline.get("arousal", 0.3),
        )

        # Group runtime state
        self._group_last_message_at: dict[str, str] = {}
        self._transcripts: dict[str, Transcript] = {}

        # Event bus
        self.event_bus = SessionEventBus()

        # Token usage tracking
        from sirius_chat.config import TokenUsageRecord
        self.token_usage_records: list[TokenUsageRecord] = []

        # SKILL system
        self._skill_registry: Any | None = None
        self._skill_executor: Any | None = None

        # Background tasks
        self._bg_tasks: set[asyncio.Task] = set()
        self._bg_running = False

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

    # ==================================================================
    # Background tasks
    # ==================================================================

    def start_background_tasks(self) -> None:
        """Start periodic background tasks for delayed queue, proactive triggers,
        and memory promotion. Idempotent: safe to call multiple times.
        """
        if self._bg_running:
            return
        self._bg_running = True

        tasks = [
            asyncio.create_task(self._bg_delayed_queue_ticker(), name="delayed_queue"),
            asyncio.create_task(self._bg_proactive_checker(), name="proactive_check"),
            asyncio.create_task(self._bg_memory_promoter(), name="memory_promote"),
            asyncio.create_task(self._bg_consolidator(), name="consolidator"),
        ]
        for t in tasks:
            self._bg_tasks.add(t)
            t.add_done_callback(self._bg_tasks.discard)

    def stop_background_tasks(self) -> None:
        """Cancel all background tasks."""
        self._bg_running = False
        for t in list(self._bg_tasks):
            t.cancel()
        self._bg_tasks.clear()

    async def _bg_delayed_queue_ticker(self) -> None:
        """Periodically tick delayed queue for all active groups."""
        interval = self.config.get("delayed_queue_tick_interval_seconds", 10)
        while self._bg_running:
            await asyncio.sleep(interval)
            for group_id in list(self._group_last_message_at.keys()):
                try:
                    await self.tick_delayed_queue(group_id)
                except Exception as exc:
                    logger.warning("Delayed queue tick failed for %s: %s", group_id, exc)

    async def _bg_proactive_checker(self) -> None:
        """Periodically check proactive triggers for all active groups."""
        interval = self.config.get("proactive_check_interval_seconds", 60)
        while self._bg_running:
            await asyncio.sleep(interval)
            for group_id in list(self._group_last_message_at.keys()):
                try:
                    await self.proactive_check(group_id)
                except Exception as exc:
                    logger.warning("Proactive check failed for %s: %s", group_id, exc)

    async def _bg_memory_promoter(self) -> None:
        """Periodically promote high-importance working memory entries to episodic."""
        interval = self.config.get("memory_promote_interval_seconds", 300)
        threshold = self.config.get("working_memory_promote_threshold", 0.3)
        while self._bg_running:
            await asyncio.sleep(interval)
            try:
                for group_id in self.working_memory.list_groups():
                    entries = self.working_memory.get_recent_entries(group_id, n=100)
                    for entry in entries:
                        if entry.importance >= threshold:
                            self.episodic_memory.add_event(
                                group_id=group_id,
                                user_id=entry.user_id,
                                content=entry.content,
                                emotion_valence=0.0,
                                importance=entry.importance,
                            )
            except Exception as exc:
                logger.warning("Memory promotion failed: %s", exc)

    async def _bg_consolidator(self) -> None:
        """Periodically consolidate episodic memories into semantic profiles."""
        interval = self.config.get("consolidation_interval_seconds", 600)
        while self._bg_running:
            await asyncio.sleep(interval)
            try:
                for group_id in self.working_memory.list_groups():
                    await self._consolidate_group(group_id)
            except Exception as exc:
                logger.warning("Consolidation failed: %s", exc)

    async def _consolidate_group(self, group_id: str) -> None:
        """Consolidate episodic events into semantic user profiles for a group."""
        from datetime import datetime, timedelta, timezone
        from sirius_chat.memory.semantic.models import UserSemanticProfile

        # Read recent episodic events (last 7 days)
        entries = self.episodic_memory.get_entries(group_id, limit=500)
        if not entries:
            return

        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        recent = [
            e for e in entries
            if e.created_at and datetime.fromisoformat(e.created_at.replace("Z", "+00:00")) > cutoff
        ]
        if not recent:
            return

        # Aggregate per user
        user_stats: dict[str, dict[str, Any]] = {}
        for e in recent:
            uid = e.user_id or "unknown"
            if uid not in user_stats:
                user_stats[uid] = {
                    "count": 0,
                    "valence_sum": 0.0,
                    "help_count": 0,
                    "last_at": e.created_at,
                }
            user_stats[uid]["count"] += 1
            # Approximate valence from confidence and summary sentiment
            valence = 0.0
            if e.summary:
                s = e.summary.lower()
                pos = sum(1 for w in ["开心", "高兴", "喜欢", "棒", "好"] if w in s)
                neg = sum(1 for w in ["难受", "难过", "伤心", "烦", "累", "生气"] if w in s)
                valence = (pos - neg) * 0.2
            user_stats[uid]["valence_sum"] += valence
            if "help" in e.summary.lower() or "求助" in e.summary or "怎么办" in e.summary:
                user_stats[uid]["help_count"] += 1

        # Update semantic profiles
        for uid, stats in user_stats.items():
            profile = self.semantic_memory.get_user_profile(group_id, uid)
            if profile is None:
                profile = UserSemanticProfile(user_id=uid)

            # Update relationship state
            rs = profile.relationship_state
            rs.interaction_frequency_7d = stats["count"] / 7.0
            avg_valence = stats["valence_sum"] / stats["count"] if stats["count"] > 0 else 0.0
            # Smooth emotional intimacy update
            rs.emotional_intimacy = round(
                rs.emotional_intimacy * 0.7 + abs(avg_valence) * 0.3, 3
            )
            # Dependency score: how often they seek help
            rs.dependency_score = round(
                rs.dependency_score * 0.7 + min(1.0, stats["help_count"] / max(1, stats["count"])) * 0.3, 3
            )
            rs.compute_familiarity()
            rs.last_interaction_at = stats["last_at"]
            if not rs.first_interaction_at:
                rs.first_interaction_at = stats["last_at"]

            self.semantic_memory.save_user_profile(group_id, profile)

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
            token_usage_records=[r.to_dict() for r in self.token_usage_records],
        )

        # Save persona
        from sirius_chat.core.persona_store import PersonaStore
        PersonaStore.save(self.work_path, self.persona)

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

        # Token usage records
        from sirius_chat.config import TokenUsageRecord
        for rec_data in state.get("token_usage_records", []):
            try:
                self.token_usage_records.append(TokenUsageRecord.from_dict(rec_data))
            except Exception:
                pass

        # Load persona
        from sirius_chat.core.persona_store import PersonaStore
        loaded = PersonaStore.load(self.work_path)
        if loaded:
            self.persona = loaded
            self.response_assembler.persona = loaded
            logger.info("Persona loaded: %s", loaded.name)

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
        emotion = await self.emotion_analyzer.analyze(content, user_id, group_id)

        # Intent analysis
        intent = await self.intent_analyzer.analyze(
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

        # Compute dynamic threshold via ThresholdEngine
        user_profile = self.semantic_memory.get_user_profile(group_id, user_id)
        relationship_state = getattr(user_profile, "relationship_state", None) if user_profile else None

        # Message rate (per minute) from recent messages
        msg_rate = self._message_rate_per_minute(recent_msgs)

        threshold = self.threshold_engine.compute(
            sensitivity=self.config.get("sensitivity", 0.5),
            heat_level=rhythm.heat_level,
            messages_per_minute=msg_rate,
            relationship_state=relationship_state,
        )

        # Persona reply frequency bias
        freq = self.persona.reply_frequency
        if freq == "high":
            threshold *= 0.8
        elif freq == "low":
            threshold *= 1.3
        elif freq == "selective":
            # Only reply when mentioned or high urgency
            if not intent.directed_at_current_ai and intent.urgency_score < 70:
                threshold *= 2.0

        intent.threshold = threshold
        intent.activity_factor = self.threshold_engine._activity_factor(rhythm.heat_level, msg_rate)
        intent.time_factor = self.threshold_engine._time_factor(None)
        if relationship_state:
            intent.relationship_factor = self.threshold_engine._relationship_factor(relationship_state)

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
            reply = await self._process_skill_calls(reply, group_id)
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

        # Update group sentiment cache for emotion island detection
        self.emotion_analyzer.update_group_sentiment(group_id, emotion)

        # Passive group norm learning
        self._learn_group_norms(group_profile, message, intent)

        self.semantic_memory.save_group_profile(group_profile)

    def _learn_group_norms(
        self,
        group_profile: Any,
        message: Message,
        intent: IntentAnalysisV3,
    ) -> None:
        """Passive learning of group interaction norms from observed messages.

        Updates group_profile.group_norms with rolling statistics:
        - avg_message_length, message_length_distribution
        - emoji_usage_rate, mention_rate
        - most_active_hours
        - topic_switch_frequency
        """
        norms = group_profile.group_norms
        content = message.content or ""

        # 1. Message length rolling average
        length = len(content)
        old_avg = norms.get("avg_message_length", 0.0)
        old_count = norms.get("message_count", 0)
        new_count = old_count + 1
        norms["avg_message_length"] = (old_avg * old_count + length) / new_count
        norms["message_count"] = new_count

        # Length distribution buckets
        bucket = "short" if length < 20 else "medium" if length < 100 else "long"
        dist = norms.get("length_distribution", {})
        dist[bucket] = dist.get(bucket, 0) + 1
        norms["length_distribution"] = dist

        # 2. Emoji / emoticon usage
        emoji_pattern = re.compile(
            r"[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF"
            r"\U0001F1E0-\U0001F1FF\U00002702-\U000027B0\U000024C2-\U0001F251"
            r"\u4e00-\u9fff]{1,2}[\uD83C-\uDBFF\uDC00-\uDFFF]"
            r"|[\u2600-\u26FF\u2700-\u27BF]"
            r"|\[.+?\]|\(.+?\)"  # ASCII emoticons like [doge], (facepalm)
        )
        has_emoji = bool(emoji_pattern.search(content)) or any(
            e in content for e in ("😀", "😂", "👍", "❤️", "🎉", "😭", "😡", "🙏", "😊", "😅")
        )
        emoji_total = norms.get("emoji_total", 0)
        if has_emoji:
            emoji_total += 1
        norms["emoji_total"] = emoji_total
        norms["emoji_usage_rate"] = emoji_total / new_count if new_count else 0.0

        # 3. @mention rate
        has_mention = "@" in content
        mention_total = norms.get("mention_total", 0)
        if has_mention:
            mention_total += 1
        norms["mention_total"] = mention_total
        norms["mention_rate"] = mention_total / new_count if new_count else 0.0

        # 4. Active hours histogram
        hour = datetime.now(timezone.utc).hour
        hours = norms.get("active_hours", {})
        hours[str(hour)] = hours.get(str(hour), 0) + 1
        norms["active_hours"] = hours

        # 5. Topic switch tracking
        topic_switches = norms.get("topic_switches", 0)
        if intent.social_intent.value != norms.get("last_intent", ""):
            topic_switches += 1
        norms["topic_switches"] = topic_switches
        norms["last_intent"] = intent.social_intent.value
        norms["topic_switch_frequency"] = topic_switches / new_count if new_count else 0.0

        # 6. Interaction style inference
        short_ratio = dist.get("short", 0) / new_count if new_count else 0
        if short_ratio > 0.6:
            inferred_style = "active"
        elif norms.get("emoji_usage_rate", 0) > 0.3:
            inferred_style = "humorous"
        elif norms.get("mention_rate", 0) > 0.2:
            inferred_style = "formal"
        else:
            inferred_style = "balanced"
        group_profile.typical_interaction_style = inferred_style

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

        # Build GenerationRequest
        from sirius_chat.providers.base import GenerationRequest, LLMProvider

        # Split prompt: everything before the last [消息] section is system context
        if "[消息]" in prompt:
            system_prompt, _, user_content = prompt.rpartition("[消息] ")
        else:
            system_prompt = prompt
            user_content = ""

        request = GenerationRequest(
            model=cfg.model_name,
            system_prompt=system_prompt.strip(),
            messages=[{"role": "user", "content": user_content.strip()}],
            temperature=effective_temperature,
            max_tokens=effective_max_tokens,
            timeout_seconds=cfg.timeout,
            purpose=task_name,
        )

        # Estimate input tokens
        from sirius_chat.providers.base import estimate_generation_request_input_tokens
        estimated_input_tokens = estimate_generation_request_input_tokens(request)

        # Call provider (async or sync via thread)
        if hasattr(self.provider_async, "generate_async"):
            reply = await self.provider_async.generate_async(request)
        elif isinstance(self.provider_async, LLMProvider):
            reply = await asyncio.to_thread(self.provider_async.generate, request)
        else:
            raise RuntimeError("配置的提供商未实现 generate/generate_async 方法。")

        # Record token usage
        output_chars = len(reply)
        estimated_output_tokens = max(1, (output_chars + 3) // 4)
        from sirius_chat.config import TokenUsageRecord
        self.token_usage_records.append(TokenUsageRecord(
            actor_id="assistant",
            task_name=task_name,
            model=cfg.model_name,
            prompt_tokens=estimated_input_tokens,
            completion_tokens=estimated_output_tokens,
            total_tokens=estimated_input_tokens + estimated_output_tokens,
            input_chars=len(system_prompt) + len(user_content),
            output_chars=output_chars,
            estimation_method="char_div4",
            retries_used=0,
        ))

        return reply

    # ==================================================================
    # Helpers
    # ==================================================================

    # ------------------------------------------------------------------
    # SKILL integration
    # ------------------------------------------------------------------

    def set_skill_runtime(
        self,
        *,
        skill_registry: Any | None = None,
        skill_executor: Any | None = None,
    ) -> None:
        """Attach SKILL registry and executor to the engine."""
        self._skill_registry = skill_registry
        self._skill_executor = skill_executor

    async def _process_skill_calls(self, reply: str, group_id: str) -> str:
        """Parse and execute SKILL_CALL markers in the assistant reply.

        If no skill runtime is attached, strips markers and returns clean text.
        """
        if self._skill_registry is None or self._skill_executor is None:
            from sirius_chat.skills.executor import strip_skill_calls
            return strip_skill_calls(reply)

        from sirius_chat.skills.executor import parse_skill_calls
        from sirius_chat.skills.models import SkillInvocationContext

        calls = parse_skill_calls(reply)
        if not calls:
            return reply

        # Strip markers from the reply text first
        from sirius_chat.skills.executor import strip_skill_calls
        clean_reply = strip_skill_calls(reply)

        # Execute each skill and collect results
        skill_results: list[str] = []
        for skill_name, params in calls:
            skill = self._skill_registry.get_skill(skill_name)
            if skill is None:
                skill_results.append(f"[SKILL '{skill_name}' 未找到]")
                continue

            ctx = SkillInvocationContext(
                user_id="assistant",
                group_id=group_id,
                skill_registry=self._skill_registry,
            )
            try:
                result = await self._skill_executor.execute_async(
                    skill, params, invocation_context=ctx
                )
                if result.success:
                    skill_results.append(
                        f"[SKILL '{skill_name}' 结果] {result.summary or result.text or '完成'}"
                    )
                else:
                    skill_results.append(f"[SKILL '{skill_name}' 失败] {result.error or '未知错误'}")
            except Exception as exc:
                skill_results.append(f"[SKILL '{skill_name}' 异常] {exc}")

        # Append skill results to reply
        if skill_results:
            clean_reply += "\n\n" + "\n".join(skill_results)

        return clean_reply

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
    def _message_rate_per_minute(recent_msgs: list[dict[str, Any]]) -> float:
        """Estimate messages per minute from recent message timestamps."""
        if len(recent_msgs) < 2:
            return 0.0
        try:
            from datetime import datetime
            timestamps = []
            for m in recent_msgs:
                ts = m.get("timestamp")
                if isinstance(ts, str):
                    timestamps.append(datetime.fromisoformat(ts.replace("Z", "+00:00")))
                elif hasattr(ts, "isoformat"):
                    timestamps.append(ts)
            if len(timestamps) < 2:
                return 0.0
            span_minutes = (max(timestamps) - min(timestamps)).total_seconds() / 60.0
            if span_minutes <= 0:
                return 0.0
            return round((len(timestamps) - 1) / span_minutes, 2)
        except Exception:
            return 0.0

    def _describe_atmosphere(self, group_id: str) -> str:
        recent = self._get_recent_messages(group_id, n=5)
        rhythm = self.rhythm_analyzer.analyze(group_id, recent)
        mood = "轻松" if rhythm.heat_level in ("warm", "hot") else "安静"
        return f"{mood} ({rhythm.heat_level})"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
