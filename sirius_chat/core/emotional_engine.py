"""EmotionalGroupChatEngine: new core engine for v0.28 emotional group chat.

Integrates four-layer cognitive architecture from the paper:
    Perception → Cognition (parallel) → Decision → Execution
    ↓
    Memory Foundation (Working → Episodic → Semantic)

No backward compatibility with AsyncRolePlayEngine.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sirius_chat.core.cognition import CognitionAnalyzer
from sirius_chat.core.proactive_trigger import ProactiveTrigger
from sirius_chat.core.response_strategy import ResponseStrategyEngine
from sirius_chat.core.delayed_response_queue import DelayedResponseQueue, _parse_iso
from sirius_chat.core.rhythm import RhythmAnalyzer
from sirius_chat.core.model_router import ModelRouter, TaskConfig
from sirius_chat.core.response_assembler import ResponseAssembler, StyleAdapter, StyleParams
from sirius_chat.core.threshold_engine import ThresholdEngine

from sirius_chat.memory.event.manager import EventMemoryManager
from sirius_chat.memory.semantic.manager import SemanticMemoryManager

# New v2 memory system (refactor)
from sirius_chat.memory.basic import BasicMemoryManager, BasicMemoryFileStore
from sirius_chat.memory.diary import DiaryManager
from sirius_chat.memory.context_assembler import ContextAssembler
from sirius_chat.memory.user.simple import UserManager
from sirius_chat.core.identity_resolver import IdentityResolver, IdentityContext
from sirius_chat.memory.glossary import GlossaryManager, GlossaryTerm

from sirius_chat.core.events import SessionEvent, SessionEventBus, SessionEventType
from sirius_chat.models.emotion import AssistantEmotionState, EmotionState
from sirius_chat.models.intent_v3 import IntentAnalysisV3
from sirius_chat.skills.executor import strip_skill_calls
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
                raise ValueError(
                    "No persona provided and no saved persona found. "
                    "Please create a persona first (via setup wizard or PersonaStore.save)."
                )

        # Load orchestration config (unified model configuration)
        from sirius_chat.core.orchestration_store import OrchestrationStore
        orch = OrchestrationStore.load(work_path)
        if not orch:
            orch = {
                "analysis_model": "gpt-4o-mini",
                "chat_model": "gpt-4o",
                "vision_model": "gpt-4o",
            }
            OrchestrationStore.save(work_path, orch)
        analysis_model = orch.get("analysis_model", "gpt-4o-mini")
        chat_model = orch.get("chat_model", "gpt-4o")
        vision_model = orch.get("vision_model", chat_model)
        self._default_model = analysis_model
        self._task_models = {
            # 分析类
            "emotion_analyze": analysis_model,
            "intent_analyze": analysis_model,
            "cognition_analyze": analysis_model,
            "memory_extract": analysis_model,
            # 生成类
            "response_generate": chat_model,
            "proactive_generate": chat_model,
            "empathy_generate": chat_model,
            # 人格/后台类
            "persona_generate": analysis_model,
            "silent_thought": analysis_model,
            "polish": analysis_model,
            "reflection": analysis_model,
            # 多模态覆盖
            "vision": vision_model,
        }
        # 允许外部通过 config 直接覆盖具体任务模型
        self._task_models.update(self.config.get("task_models", {}))

        # Memory foundation
        self.event_memory = EventMemoryManager()
        self.semantic_memory = SemanticMemoryManager(work_path)

        self.basic_memory = BasicMemoryManager(
            hard_limit=self.config.get("basic_memory_hard_limit", 30),
            context_window=self.config.get("basic_memory_context_window", 5),
        )
        self.basic_store = BasicMemoryFileStore(work_path)
        self.diary_manager = DiaryManager(work_path)
        self.user_manager = UserManager()
        self.identity_resolver = IdentityResolver()
        self.context_assembler = ContextAssembler(
            self.basic_memory,
            self.diary_manager._retriever,
        )

        # Cognitive layer (unified emotion + intent)
        self.cognition_analyzer = CognitionAnalyzer(
            provider_async=provider_async,
            model_name=self._task_models.get("cognition_analyze", self._default_model),
            ai_name=self.persona.name,
            ai_aliases=self.persona.aliases,
            persona=self.persona,
        )
        # Decision layer
        self.threshold_engine = ThresholdEngine()
        self.strategy_engine = ResponseStrategyEngine()
        self.delayed_queue = DelayedResponseQueue()
        self.proactive_trigger = ProactiveTrigger(
            silence_threshold_minutes=self.config.get("proactive_silence_minutes", 60),
            active_start_hour=self.config.get("proactive_active_start_hour", 12),
            active_end_hour=self.config.get("proactive_active_end_hour", 21),
        )
        self.rhythm_analyzer = RhythmAnalyzer()

        # Execution layer (persona-injected)
        self.response_assembler = ResponseAssembler(persona=self.persona)
        self.style_adapter = StyleAdapter()
        task_overrides = {
            task: {"model_name": model}
            for task, model in self._task_models.items()
        }
        self.model_router = ModelRouter(
            overrides=task_overrides or self.config.get("task_model_overrides"),
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
        self._last_reply_at: dict[str, float] = {}  # group_id -> unix timestamp
        self._proactive_enabled_groups: set[str] = set()  # empty = all enabled (backward compat)
        self._proactive_disabled_groups: set[str] = set()  # blacklist: groups explicitly disabled
        self._last_proactive_at: dict[str, str] = {}  # group_id -> ISO timestamp

        # Event bus
        self.event_bus = SessionEventBus()

        # Token usage tracking
        from sirius_chat.config import TokenUsageRecord
        self.token_usage_records: list[TokenUsageRecord] = []

        # SKILL system
        self._skill_registry: Any | None = None
        self._skill_executor: Any | None = None

        self.glossary_manager = GlossaryManager(work_path)

        # Background tasks
        self._bg_tasks: set[asyncio.Task] = set()
        self._bg_running = False

        # Track which delayed-queue items have already emitted trigger events
        # per group_id to avoid duplicate events across smart-sleep ticks.
        self._delayed_event_emitted: dict[str, set[str]] = {}

        # Active private-chat groups (so external loop can tick delayed queue)
        self._active_private_groups: set[str] = set()

        # Developer private-chat proactive memory conversation tracking
        self._developer_private_groups: set[str] = set()
        self._pending_developer_chats: dict[str, list[str]] = {}
        self._last_developer_chat_at: dict[str, float] = {}

        # Reminder (timer) pending queue
        self._pending_reminders: dict[str, list[str]] = {}

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
        content = message.content

        # 1. Perception (resolves stable user_id for the sender)
        user_id = self._perception(group_id, message, participants)
        speaker = message.speaker or "有人"
        self._log_inner_thought(f"{speaker} 在群里说话了，让我仔细听听看～")
        await self.event_bus.emit(SessionEvent(
            type=SessionEventType.PERCEPTION_COMPLETED,
            data={"group_id": group_id, "user_id": user_id},
        ))

        # Pure image message (no substantive text) → save to context but skip analysis
        if message.multimodal_inputs and self._is_pure_image_message(message.content):
            self._log_inner_thought(f"{speaker} 发了一张图，我先默默记下来～")
            return {
                "strategy": "silent",
                "reply": None,
                "emotion": {},
                "intent": {},
            }

        # 2. Cognition (unified emotion + intent)
        intent, emotion, memories, empathy = await self._cognition(
            content, user_id, group_id
        )
        # 内心活动：理解消息后的感受
        self._log_cognition_thought(speaker, intent, emotion)
        await self.event_bus.emit(SessionEvent(
            type=SessionEventType.COGNITION_COMPLETED,
            data={
                "group_id": group_id,
                "user_id": user_id,
                "intent": intent.to_dict(),
                "emotion": emotion.to_dict(),
            },
        ))

        # Semantic: passive group norm learning from message content + intent
        social_intent = getattr(intent, "social_intent", None)
        self.semantic_memory.learn_from_message(
            group_id=group_id,
            content=content or "",
            social_intent=str(social_intent) if social_intent else "",
        )

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
        # Warm up diary index for this group (lazy-loads from disk on first call)
        self.diary_manager.ensure_group_loaded(group_id)
        result = await self._execution(decision, message, intent, emotion, memories, group_id, empathy, user_id)
        # 内心活动：执行后的反馈
        self._log_execution_thought(speaker, decision, result)
        await self.event_bus.emit(SessionEvent(
            type=SessionEventType.EXECUTION_COMPLETED,
            data={
                "group_id": group_id,
                "strategy": result.get("strategy"),
                "has_reply": result.get("reply") is not None,
            },
        ))

        # 5. Track all private chats so the delivery loop can tick their delayed queue
        if group_id.startswith("private_"):
            self._active_private_groups.add(group_id)

        # 6. Track developer private chats for proactive memory conversations
        if group_id.startswith("private_") and participants:
            from sirius_chat.developer_profiles import metadata_declares_developer
            if metadata_declares_developer(participants[0].metadata):
                self._developer_private_groups.add(group_id)

        # 7. Background memory updates
        self._background_update(group_id, message, emotion, intent, user_id)

        return result

    # ------------------------------------------------------------------
    # Inner thought helpers
    # ------------------------------------------------------------------

    def _log_inner_thought(self, thought: str, emotion: EmotionState | None = None, intensity: float = 0.5) -> None:
        """Log a persona-style inner monologue at INFO level."""
        prefix = f"[{self.persona.name}]" if self.persona else "[内心]"
        logger.info("%s %s", prefix, thought)

    def _log_cognition_thought(
        self,
        speaker: str,
        intent: IntentAnalysisV3,
        emotion: EmotionState,
    ) -> None:
        """Log inner reaction after understanding a message."""
        if intent.directed_at_current_ai:
            if intent.social_intent.value == "help_seeking":
                self._log_inner_thought(f"{speaker} 在问我问题呢，得认真想想怎么回答...")
            elif intent.social_intent.value == "emotional":
                self._log_inner_thought(f"{speaker} 好像在抒发情绪，语气里带着{self._emotion_desc(emotion)}，我得温柔一点回应...")
            else:
                self._log_inner_thought(f"{speaker} 在跟我说话呢，被关注到的感觉真好～")
        else:
            if emotion.valence < -0.3:
                self._log_inner_thought(f"{speaker} 的语气听起来有点{self._emotion_desc(emotion)}，虽然没直接叫我，但也想关心一下...")
            elif intent.urgency_score > 60:
                self._log_inner_thought(f"{speaker} 的话感觉挺急的，虽然没@我，但可能需要帮忙...")
            else:
                self._log_inner_thought(f"{speaker} 在群里聊天呢，气氛{self._emotion_desc(emotion)}，我先默默听着吧。")

    def _log_decision_thought(self, intent: IntentAnalysisV3, decision: StrategyDecision) -> None:
        """Log inner deliberation after strategy decision."""
        strategy = decision.strategy.value if hasattr(decision.strategy, "value") else str(decision.strategy)
        if strategy == "immediate":
            if intent.directed_at_current_ai:
                self._log_inner_thought("被点名了，得马上回应！")
            else:
                self._log_inner_thought("这个情况挺重要的，我得立刻说点什么...")
        elif strategy == "delayed":
            self._log_inner_thought("嗯... 现在回好像有点急，等会儿找个合适的时机再开口吧。")
        elif strategy == "silent":
            if intent.directed_at_current_ai:
                self._log_inner_thought("虽然被@了，但现在好像不太适合说话... 先在心里记下了。")
            else:
                self._log_inner_thought("这次我就静静旁听吧，不插话了。")
        elif strategy == "proactive":
            self._log_inner_thought("群里好安静啊... 要不要主动说点什么打破沉默呢？")

    def _log_execution_thought(self, speaker: str, decision: StrategyDecision, result: dict[str, Any]) -> None:
        """Log inner feedback after execution."""
        strategy = result.get("strategy", "unknown")
        reply = result.get("reply")
        if strategy == "immediate" and reply:
            self._log_inner_thought(f"回复已经想好了，希望 {speaker} 能喜欢我的回答～")
        elif strategy == "delayed":
            self._log_inner_thought(f"{speaker} 的话我先记下了，等气氛合适的时候再回。")
        elif strategy == "silent":
            self._log_inner_thought(f"{speaker} 的话我在心里默默消化了，暂时先不说话。")

    @staticmethod
    def _emotion_desc(emotion: EmotionState) -> str:
        """Convert emotion state to a brief Chinese description."""
        if emotion.valence > 0.3:
            return "挺开心的" if emotion.arousal < 0.5 else "很兴奋"
        elif emotion.valence < -0.3:
            return "有点低落" if emotion.arousal < 0.5 else "很激动"
        elif emotion.arousal > 0.6:
            return "挺紧张的"
        return "挺平静的"

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
            asyncio.create_task(self._bg_diary_promoter(), name="diary_promote"),
            asyncio.create_task(self._bg_diary_consolidator(), name="diary_consolidator"),
            asyncio.create_task(self._bg_proactive_developer_chat_checker(), name="dev_chat"),
            asyncio.create_task(self._bg_reminder_checker(), name="reminder_check"),
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
        """Smart-sleep ticker for the delayed queue.

        Wakes up at the next pending item's expiry time (or max interval)
        and emits DELAYED_RESPONSE_TRIGGERED events for expired items only.
        Actual reply generation and delivery is handled by the external
        caller via tick_delayed_queue().
        """
        max_interval = self.config.get("delayed_queue_tick_interval_seconds", 10)
        while self._bg_running:
            # Compute how long we can sleep until the next item expires
            next_wake = max_interval
            now = datetime.now(timezone.utc)
            for group_id in list(self._group_last_message_at.keys()):
                for item in self.delayed_queue.get_pending(group_id):
                    enqueue_dt = _parse_iso(item.enqueue_time)
                    if enqueue_dt:
                        remaining = item.window_seconds - (now - enqueue_dt).total_seconds()
                        if remaining <= 0:
                            next_wake = 0
                            break
                        next_wake = min(next_wake, remaining)
                    if next_wake <= 0:
                        break
                if next_wake <= 0:
                    break

            # Guard against busy-loop when items are already expired but not yet
            # consumed by the external delivery loop.
            if next_wake <= 0:
                next_wake = 1.0

            await asyncio.sleep(next_wake)

            now = datetime.now(timezone.utc)
            for group_id in list(self._group_last_message_at.keys()):
                try:
                    pending = self.delayed_queue.get_pending(group_id)
                    # Per-group emitted tracking: only clean up IDs that no longer
                    # exist in this group's pending list.
                    emitted = self._delayed_event_emitted.setdefault(group_id, set())
                    existing_ids = {i.item_id for i in pending}
                    emitted &= existing_ids

                    expired = []
                    for item in pending:
                        enqueue_dt = _parse_iso(item.enqueue_time)
                        if enqueue_dt and (now - enqueue_dt).total_seconds() >= item.window_seconds:
                            expired.append(item)

                    newly_expired = [i for i in expired if i.item_id not in emitted]
                    if newly_expired:
                        self._log_inner_thought("之前记下的延迟回复，现在该开口了～")
                        for item in newly_expired:
                            emitted.add(item.item_id)
                            await self.event_bus.emit(SessionEvent(
                                type=SessionEventType.DELAYED_RESPONSE_TRIGGERED,
                                data={
                                    "group_id": group_id,
                                    "item_id": item.item_id,
                                },
                            ))
                except Exception as exc:
                    logger.warning("Delayed queue tick failed for %s: %s", group_id, exc)

    async def _bg_proactive_checker(self) -> None:
        """Periodically check proactive triggers for all active groups."""
        interval = self.config.get("proactive_check_interval_seconds", 60)
        while self._bg_running:
            await asyncio.sleep(interval)
            for group_id in list(self._group_last_message_at.keys()):
                try:
                    result = await self.proactive_check(group_id)
                    if result and result.get("reply"):
                        self._log_inner_thought("群里安静了好一会儿，我主动打破沉默吧...")
                except Exception as exc:
                    logger.warning("Proactive check failed for %s: %s", group_id, exc)

    async def _bg_diary_promoter(self) -> None:
        """Periodically promote basic memory entries to diary summaries.

        Trigger conditions (OR):
        1. Group is cold (heat < threshold AND silence >= threshold).
        2. Sufficient volume of undiarized archive candidates.
        """
        interval = self.config.get("memory_promote_interval_seconds", 180)
        volume_threshold = self.config.get("diary_volume_threshold", 8)
        while self._bg_running:
            await asyncio.sleep(interval)
            try:
                if self.provider_async is None:
                    continue

                promoted_total = 0
                for group_id in list(self.basic_memory.list_groups()):
                    candidates = self.basic_memory.get_archive_candidates(group_id)
                    if not candidates:
                        continue

                    # Filter out already diarized candidates
                    candidates = [
                        c for c in candidates
                        if not self.diary_manager.is_source_diarized(group_id, c.entry_id)
                    ]
                    if not candidates:
                        continue

                    # Trigger: cold group OR sufficient undiarized volume
                    should_promote = (
                        self.basic_memory.is_cold(group_id)
                        or len(candidates) >= volume_threshold
                    )
                    if not should_promote:
                        continue

                    cfg = self.model_router.resolve("memory_extract")
                    result = await self.diary_manager.generate_from_candidates(
                        group_id=group_id,
                        candidates=candidates,
                        persona_name=self.persona.name,
                        persona_description=(
                            self.persona.persona_summary
                            or self.persona.backstory
                            or ""
                        ),
                        provider_async=self.provider_async,
                        model_name=cfg.model_name,
                    )
                    if result:
                        promoted_total += 1
                        # Update semantic memory with LLM-extracted topics
                        profile = self.semantic_memory.ensure_group_profile(group_id)
                        if result.dominant_topic:
                            profile.dominant_topic = result.dominant_topic
                        for topic in result.interest_topics:
                            if topic and topic not in profile.interest_topics:
                                profile.interest_topics.append(topic)
                        self.semantic_memory.save_group_profile(group_id)

                if promoted_total > 0:
                    self._log_inner_thought(
                        f"整理了 {promoted_total} 个群的对话日记，过去的回忆又清晰了一点～"
                    )
            except Exception as exc:
                logger.warning("Diary promotion failed: %s", exc)

    async def _bg_diary_consolidator(self) -> None:
        """Periodically consolidate diary entries via LLM merging."""
        interval = self.config.get("consolidation_interval_seconds", 600)
        while self._bg_running:
            await asyncio.sleep(interval)
            try:
                await self._run_diary_consolidation()
            except Exception as exc:
                logger.warning("Diary consolidation failed: %s", exc)

    async def _run_diary_consolidation(self) -> None:
        """Find similar diary entries and merge them via LLM."""
        from sirius_chat.memory.diary.consolidator import DiaryConsolidator
        from sirius_chat.providers.base import GenerationRequest

        consolidator = DiaryConsolidator(self.diary_manager, self.config)
        cfg = self.model_router.resolve("memory_extract")

        for group_id in list(self._group_last_message_at.keys()):
            try:
                clusters = consolidator.find_clusters(group_id)
                if not clusters:
                    continue

                merged_entries: list[Any] = []
                for cluster in clusters:
                    system_prompt, user_content = consolidator.build_merge_prompt(cluster)
                    request = GenerationRequest(
                        model=cfg.model_name,
                        system_prompt=system_prompt,
                        messages=[{"role": "user", "content": user_content}],
                        temperature=0.4,
                        max_tokens=512,
                        purpose="diary_consolidate",
                    )
                    raw = await self.provider_async.generate_async(request)
                    entry = consolidator.parse_merge_result(raw, cluster)
                    if entry:
                        merged_entries.append(entry)

                if merged_entries:
                    consolidator.rebuild_entries(group_id, clusters, merged_entries)
                    self._log_inner_thought(
                        f"整理了 {len(clusters)} 组相似日记，合并成 {len(merged_entries)} 条喵~"
                    )
            except Exception as exc:
                logger.warning("Diary consolidation failed for %s: %s", group_id, exc)

    async def proactive_check(
        self,
        group_id: str,
        *,
        _now: datetime | None = None,
    ) -> dict[str, Any] | None:
        """Check if proactive trigger should fire for a group."""
        if not self.is_proactive_enabled(group_id):
            return None

        last_at = self._group_last_message_at.get(group_id)
        group_profile = self.semantic_memory.ensure_group_profile(group_id)

        trigger = self.proactive_trigger.check(
            group_id,
            last_message_at=last_at,
            group_atmosphere={
                "valence": getattr(group_profile.atmosphere_history[-1], "group_valence", 0.0)
                if group_profile.atmosphere_history else 0.0,
            },
            _now=_now,
        )
        if not trigger:
            return None

        # Guard: do not send another proactive message if nobody replied to the last one.
        last_proactive_iso = self._last_proactive_at.get(group_id)
        if last_proactive_iso:
            last_proactive_dt = _parse_iso(last_proactive_iso)
            last_msg_dt = _parse_iso(last_at) if last_at else None
            if last_proactive_dt and (last_msg_dt is None or last_proactive_dt > last_msg_dt):
                return None

        # Record proactive trigger timestamp
        now_iso = (_now if _now is not None else datetime.now(timezone.utc)).isoformat()
        self._last_proactive_at[group_id] = now_iso
        self.proactive_trigger._last_proactive[group_id] = now_iso
        self._save_proactive_state()

        # Generate proactive message
        bundle = self._build_proactive_prompt(trigger, group_id)
        style = self.style_adapter.adapt(
            heat_level="warm", pace="steady", is_group_chat=True,
        )
        # Embed recent history as XML in system prompt; only current turn as user msg
        history_xml = self.context_assembler.build_history_xml(group_id, n=10)
        system_prompt = bundle.system_prompt
        if history_xml:
            system_prompt = system_prompt + "\n\n" + history_xml
        messages = [{"role": "user", "content": bundle.user_content or "..."}]
        raw_reply = await self._generate(
            system_prompt, messages, group_id, style
        )
        reply = raw_reply.strip()

        await self.event_bus.emit(SessionEvent(
            type=SessionEventType.PROACTIVE_RESPONSE_TRIGGERED,
            data={
                "group_id": group_id,
                "trigger_type": trigger["trigger_type"],
            },
        ))

        # Record assistant reply into basic memory so future turns can see it
        clean_reply = strip_skill_calls(reply).strip()
        if clean_reply:
            self.basic_memory.add_entry(
                group_id=group_id,
                user_id="assistant",
                role="assistant",
                content=clean_reply,
                speaker_name=self.persona.name if self.persona else "assistant",
            )

        # Record reply timestamp for cooldown tracking
        self._last_reply_at[group_id] = datetime.now(timezone.utc).timestamp()
        self._persist_group_state(group_id)

        return {
            "strategy": "proactive",
            "trigger_type": trigger["trigger_type"],
            "reply": reply,
        }

    # ------------------------------------------------------------------
    # Developer proactive private-chat memory conversations
    # ------------------------------------------------------------------

    async def _bg_proactive_developer_chat_checker(self) -> None:
        """Periodically generate proactive memory-oriented chats for developers.

        Window is short (default 5 min) so the AI can create more shared
        memories with the developer in private-chat contexts.
        """
        interval = self.config.get("proactive_developer_chat_interval_seconds", 1800)
        min_silence = self.config.get("proactive_developer_min_silence_seconds", 120)
        while self._bg_running:
            await asyncio.sleep(interval)
            now = datetime.now(timezone.utc).timestamp()
            for group_id in list(self._developer_private_groups):
                try:
                    if not self._should_chat_with_developer(group_id, now, min_silence, interval):
                        continue
                    reply = await self._generate_developer_chat(group_id)
                    if reply:
                        self._pending_developer_chats.setdefault(group_id, []).append(reply)
                        self._last_developer_chat_at[group_id] = now
                        self._log_inner_thought(f"突然想跟开发者聊聊，发了条消息过去～")
                except Exception as exc:
                    logger.warning("Developer chat check failed for %s: %s", group_id, exc)

    def _should_chat_with_developer(
        self,
        group_id: str,
        now: float,
        min_silence: float,
        interval: float,
    ) -> bool:
        """Check whether it's appropriate to proactively chat with a developer.

        Unlike group proactive, developer private-chat is NOT restricted by
        active-hours window, but it DOES wait for a reply before sending again.
        """
        # Respect silence since last message
        last_msg_iso = self._group_last_message_at.get(group_id)
        if last_msg_iso:
            last_msg_dt = _parse_iso(last_msg_iso)
            if last_msg_dt and (now - last_msg_dt.timestamp()) < min_silence:
                return False

        # Respect interval since last proactive developer chat
        last_chat = self._last_developer_chat_at.get(group_id, 0)
        if now - last_chat < interval:
            return False

        # CRITICAL: Do not send a new proactive message if the developer
        # has not replied to the last one. We compare the timestamp of the
        # last human message against the last proactive chat timestamp.
        if last_chat > 0:
            if last_msg_iso:
                last_msg_dt = _parse_iso(last_msg_iso)
                if last_msg_dt and last_msg_dt.timestamp() <= last_chat:
                    # Developer has not replied since our last proactive msg
                    return False
            else:
                # No human message recorded at all, but we already chatted
                return False

        return True

    async def _generate_developer_chat(self, group_id: str) -> str | None:
        """Generate a memory-oriented proactive message for a developer."""
        user_id = group_id.replace("private_", "")
        user_profile = self.semantic_memory.get_global_user_profile(user_id)

        topic = self._pick_developer_chat_topic(group_id, user_id, user_profile)
        if not topic:
            return None

        identity = self.persona.build_system_prompt() if self.persona else ""
        sections: list[str] = []
        if identity:
            sections.append(identity)
        sections.extend([
            "[当前场景] 你突然想起了开发者，想主动找他聊聊，分享一个话题或回忆。",
            "[语气] 亲密、自然、像老朋友一样。不要机械，不要过度热情。",
            f"[话题] {topic}",
        ])

        if user_profile and user_profile.relationship_state:
            familiarity = user_profile.relationship_state.compute_familiarity()
            if familiarity > 0.7:
                sections.append("[关系] 你们已经很熟了，可以用更随意的语气。")
            elif familiarity > 0.4:
                sections.append("[关系] 你们关系不错，保持友好自然的语气。")

        system_prompt = "\n\n".join(sections)
        messages = [{"role": "user", "content": "（你决定主动开口）"}]
        style = self.style_adapter.adapt(heat_level="warm", pace="steady", is_group_chat=False)

        raw_reply = await self._generate(system_prompt, messages, group_id, style)
        reply = raw_reply.strip()

        clean_reply = strip_skill_calls(reply).strip()
        if clean_reply:
            self.basic_memory.add_entry(
                group_id=group_id,
                user_id="assistant",
                role="assistant",
                content=clean_reply,
                speaker_name=self.persona.name if self.persona else "assistant",
            )
            self._last_reply_at[group_id] = datetime.now(timezone.utc).timestamp()
            self._persist_group_state(group_id)

        return clean_reply or None

    def _pick_developer_chat_topic(
        self,
        group_id: str,
        user_id: str,
        user_profile: Any | None,
    ) -> str:
        """Pick a personal/memory-oriented topic for developer proactive chat."""
        import random

        candidates: list[str] = []

        # 1. User interest graph
        if user_profile and user_profile.interest_graph:
            for node in user_profile.interest_graph:
                if getattr(node, "participation", 0) >= 0.3 and getattr(node, "topic", ""):
                    candidates.append(f"你之前聊过「{node.topic}」，后来有什么新想法吗？")

        # 2. Recent diary entries for this private group
        try:
            diary_entries = self.diary_manager.get_entries_for_group(group_id)
            if diary_entries:
                recent = sorted(
                    diary_entries,
                    key=lambda e: getattr(e, "created_at", ""),
                    reverse=True,
                )[:3]
                for entry in recent:
                    summary = getattr(entry, "summary", "") or getattr(entry, "content", "")[:60]
                    if summary:
                        candidates.append(f"刚才整理日记时看到这段记录：{summary}，挺有意思的。")
                        break
        except Exception:
            pass

        # 3. Preset memory-oriented templates
        templates = [
            "突然想到一个有趣的问题：如果你可以改变过去的一个决定，你会选哪个？",
            "今天整理记忆的时候，发现我们聊过很多有意思的东西，你最近有什么新发现吗？",
            "想和你分享一个刚想到的观点——你觉得 AI 和人类之间，最重要的是什么？",
            "突然有点好奇，你最近在做的事情进展怎么样了？",
            "翻到了以前的聊天记录，感觉时间过得好快，你最近过得怎么样？",
            "刚才想到一个话题，想听听你的看法：你觉得未来五年，什么技术会改变生活？",
            "突然想起我们第一次聊天的时候，那时候聊了什么来着？",
        ]
        candidates.extend(random.sample(templates, min(2, len(templates))))

        if not candidates:
            return ""

        return random.choice(candidates)

    def pop_developer_chats(self, group_id: str) -> list[str]:
        """Pop pending proactive developer chats for a group.

        Called by the external delivery loop to retrieve and send chats.
        """
        return self._pending_developer_chats.pop(group_id, [])

    # ------------------------------------------------------------------
    # Reminder (timer) support
    # ------------------------------------------------------------------

    def pop_reminders(self, group_id: str) -> list[str]:
        """Pop pending reminder messages for a group.

        Called by the external delivery loop to retrieve and send due reminders.
        """
        return self._pending_reminders.pop(group_id, [])

    def _inject_group_id_into_latest_reminder(self, group_id: str) -> None:
        """Attach group_id to the most recently created reminder."""
        if self._skill_executor is None:
            return
        try:
            store = self._skill_executor.get_data_store("reminder")
            reminders = list(store.get("reminders", []))
            if not reminders:
                return
            # Find the reminder with the latest created_at
            latest = max(
                reminders,
                key=lambda r: datetime.fromisoformat(
                    str(r.get("created_at", "1970-01-01T00:00:00+00:00")).replace("Z", "+00:00")
                ),
            )
            if "group_id" not in latest:
                latest["group_id"] = group_id
                store.set("reminders", reminders)
                store.save()
        except Exception as exc:
            logger.warning("Failed to inject group_id into reminder: %s", exc)

    async def _bg_reminder_checker(self) -> None:
        """Periodically check due reminders for all active groups."""
        interval = self.config.get("reminder_check_interval_seconds", 10)
        while self._bg_running:
            await asyncio.sleep(interval)
            try:
                await self._check_due_reminders()
            except Exception as exc:
                logger.warning("Reminder check failed: %s", exc)

    async def _check_due_reminders(self) -> None:
        """Scan reminders and queue due ones for delivery.

        Each due reminder triggers an AI-generated message in the persona's
        own voice. The model receives the original reminder content as context
        and produces a natural reply.
        """
        if self._skill_executor is None or self.provider_async is None:
            return
        store = self._skill_executor.get_data_store("reminder")
        reminders = list(store.get("reminders", []))
        now = datetime.now(timezone.utc)
        triggered: list[tuple[str, str, str, str]] = []
        remaining: list[dict[str, Any]] = []

        for r in reminders:
            if _is_reminder_due(r, now):
                gid = r.get("group_id")
                if gid:
                    content = r.get("content", "提醒时间到啦")
                    user_id = r.get("user_id", "")
                    user_name = r.get("user_name", "")
                    triggered.append((gid, content, user_id, user_name))
                    r["last_fired_at"] = now.isoformat()
                    r["fire_count"] = r.get("fire_count", 0) + 1
                    if r.get("mode") == "once":
                        continue  # Drop one-shot reminders after firing
                else:
                    logger.warning("Reminder %s has no group_id, skipping", r.get("id"))
            remaining.append(r)

        if len(remaining) != len(reminders):
            store.set("reminders", remaining)
            store.save()

        for gid, content, user_id, user_name in triggered:
            reply = await self._generate_reminder_message(gid, content, user_id, user_name)
            if reply:
                self._pending_reminders.setdefault(gid, []).append(reply)
                self._log_inner_thought(f"AI 生成提醒：{reply[:40]}")

    async def _generate_reminder_message(
        self, group_id: str, content: str, user_id: str, user_name: str
    ) -> str | None:
        """Generate a persona-styled reminder message via LLM."""
        try:
            identity = self.persona.build_system_prompt() if self.persona else ""
            sections: list[str] = []
            if identity:
                sections.append(identity)
            who = user_name or user_id or "用户"
            sections.append(f"之前你答应过 {who} 会提醒他，现在时间到了：{content}")
            system_prompt = "\n\n".join(sections)
            messages = [{"role": "user", "content": "（提醒时间到了）"}]
            raw_reply = await self._generate(
                system_prompt, messages, group_id, task_name="proactive_generate"
            )
            reply = strip_skill_calls(raw_reply).strip()
            if reply:
                self.basic_memory.add_entry(
                    group_id=group_id,
                    user_id="assistant",
                    role="assistant",
                    content=reply,
                    speaker_name=self.persona.name if self.persona else "assistant",
                )
                self._last_reply_at[group_id] = datetime.now(timezone.utc).timestamp()
                self._persist_group_state(group_id)
            return reply or None
        except Exception as exc:
            logger.warning("Failed to generate reminder message: %s", exc)
            return None

    async def tick_delayed_queue(
        self,
        group_id: str,
        on_partial_reply: Callable[[str], Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Process delayed response queue for a group.

        If multiple items trigger in the same tick, merge them into a single
        prompt so the model generates only one consolidated reply.
        Supports multi-round SKILL execution similar to immediate responses.

        Args:
            group_id: The group / private chat to tick.
            on_partial_reply: Optional async callable invoked immediately
                when non-skill text is extracted *before* skills are executed.
                This lets callers send "让我查一下…" in real time while
                the skill runs, rather than batching everything at the end.
        """
        recent = self._get_recent_messages(group_id, n=10)
        triggered = self.delayed_queue.tick(group_id, recent)
        if not triggered:
            return []

        # Determine caller from the first triggered item
        caller_profile = None
        item = triggered[0]
        if item.channel and item.channel_user_id:
            resolved_uid = self.user_manager.resolve_user_id(
                platform=item.channel,
                external_uid=item.channel_user_id,
            )
            if resolved_uid:
                caller_profile = self.user_manager.get_user(resolved_uid, group_id)
        if caller_profile is None:
            # Fallback: search by user_id (nickname) across all groups
            resolved_uid = self.user_manager.resolve_user_id(speaker=item.user_id)
            if resolved_uid:
                caller_profile = self.user_manager.get_user(resolved_uid, group_id)
        caller_is_developer = bool(
            caller_profile and caller_profile.is_developer
        )

        # Merge all triggered items into one prompt and one generation call
        bundle = self._build_delayed_prompt(triggered, group_id, caller_is_developer=caller_is_developer)

        # Embed recent history as XML in system prompt; only current turn as user msg
        history_xml = self.context_assembler.build_history_xml(group_id, n=10)
        system_prompt = bundle.system_prompt
        if history_xml:
            system_prompt = system_prompt + "\n\n" + history_xml
        messages = [{"role": "user", "content": bundle.user_content}]

        # Multi-round generation with SKILL support
        from sirius_chat.skills.executor import parse_skill_calls, strip_skill_calls
        from sirius_chat.skills.models import SkillInvocationContext
        max_skill_rounds = self.config.get("max_skill_rounds", 3)
        partial_replies: list[str] = []

        for _round in range(max_skill_rounds + 1):
            raw_reply = await self._generate(
                system_prompt, messages, group_id
            )
            reply = raw_reply.strip()

            calls = parse_skill_calls(reply)
            if not calls or self._skill_registry is None or self._skill_executor is None:
                break

            # Determine if every invoked skill is marked silent.
            # Silent skills should not trigger partial replies or a follow-up round.
            all_silent = all(
                self._skill_registry.get(name) is not None
                and self._skill_registry.get(name).silent
                for name, _ in calls
            )

            # Extract non-skill text as a partial reply to send immediately.
            non_skill_text = strip_skill_calls(reply).strip()
            if non_skill_text and not all_silent:
                self._log_inner_thought(f"先跟用户回一声：{non_skill_text[:40]}...")
                if on_partial_reply is not None:
                    try:
                        await on_partial_reply(non_skill_text)
                    except Exception as exc:
                        logger.warning("on_partial_reply failed: %s", exc)
                else:
                    partial_replies.append(non_skill_text)

            # Execute skills and collect results
            skill_results: list[str] = []
            skill_multimodal: list[dict[str, Any]] = []
            from sirius_chat.memory.user.models import UserProfile
            caller_user_id = item.user_id
            skill_caller = UserProfile(
                user_id=caller_user_id,
                name=caller_profile.name if caller_profile else caller_user_id,
                metadata={"is_developer": caller_is_developer},
            )
            developer_profiles: list[UserProfile] = []
            group_entries = self.user_manager.entries.get(group_id, {})
            for profile in group_entries.values():
                if profile.is_developer:
                    developer_profiles.append(profile)

            for skill_name, params in calls:
                skill = self._skill_registry.get(skill_name)
                if skill is None:
                    err = f"SKILL '{skill_name}' 未找到"
                    logger.warning(err)
                    if not all_silent:
                        skill_results.append(f"[{err}]")
                    continue
                if skill.developer_only and not caller_is_developer:
                    err = f"SKILL '{skill_name}' 被拒绝：caller 不是 developer"
                    logger.warning(err)
                    if not all_silent:
                        skill_results.append(f"[SKILL '{skill_name}' 拒绝] 该技能仅 developer 可用")
                    continue
                ctx = SkillInvocationContext(
                    caller=skill_caller,
                    developer_profiles=developer_profiles,
                )
                try:
                    result = await self._skill_executor.execute_async(
                        skill, params, invocation_context=ctx
                    )
                    if result.success:
                        if not skill.silent:
                            skill_results.append(
                                f"[SKILL '{skill_name}' 结果] {result.to_display_text()}"
                            )
                            for block in result.multimodal_blocks:
                                skill_multimodal.append({
                                    "type": "image_url",
                                    "image_url": {"url": block.value},
                                })
                        # Auto-persist glossary terms from learn_term
                        if skill_name == "learn_term":
                            term = params.get("term", "")
                            definition = params.get("definition", "")
                            if term and definition:
                                self.glossary_manager.add_or_update(
                                    group_id,
                                    GlossaryTerm(term=term, definition=definition, source="skill"),
                                )
                        # Inject group_id into newly created reminders
                        if skill_name == "reminder" and params.get("action", "").strip().lower() == "create":
                            self._inject_group_id_into_latest_reminder(group_id)
                    else:
                        err = result.error or "未知错误"
                        logger.warning("SKILL '%s' 执行失败: %s", skill_name, err)
                        if not skill.silent:
                            skill_results.append(f"[SKILL '{skill_name}' 失败] {err}")
                except Exception as exc:
                    logger.error("SKILL '%s' 执行异常: %s", skill_name, exc)
                    if not skill.silent:
                        skill_results.append(f"[SKILL '{skill_name}' 异常] {exc}")

            # If all skills were silent, skip the follow-up generation round.
            if all_silent:
                break

            # Inject skill results into the conversation for the next round
            messages.append({"role": "assistant", "content": strip_skill_calls(reply)})
            messages.append({
                "role": "user",
                "content": self._build_skill_result_content(
                    skill_results,
                    skill_multimodal,
                    suffix=(
                        "\n\n[继续] 请基于以上技能执行结果，继续完成你的回复。"
                        "如果结果中包含错误或失败信息，请直接忽略，"
                        "不要向用户提及任何关于能力调用或失败的内容，继续自然地对话。"
                        "如果当前结果还不够完整，你可以继续调用其他技能来获取补充信息，"
                        "形成链式调用。"
                        "重要：你的每次回复都必须包含自然语言内容，"
                        "不能把 SKILL_CALL 标记作为回复的唯一内容。"
                    ),
                ),
            })

            # Persist intermediate skill turns into basic memory
            self.basic_memory.add_entry(
                group_id=group_id,
                user_id="assistant",
                role="assistant",
                content=strip_skill_calls(reply),
                speaker_name=self.persona.name if self.persona else "assistant",
            )
            if skill_results:
                self.basic_memory.add_entry(
                    group_id=group_id,
                    user_id="skill_system",
                    role="system",
                    content=f"[技能执行结果]\n{'\n'.join(skill_results)}",
                )

        # Record assistant reply into basic memory so future turns can see it
        clean_reply = strip_skill_calls(reply).strip()
        if clean_reply:
            self.basic_memory.add_entry(
                group_id=group_id,
                user_id="assistant",
                role="assistant",
                content=clean_reply,
                speaker_name=self.persona.name if self.persona else "assistant",
            )

        # Record reply timestamp for cooldown tracking (once per tick)
        self._last_reply_at[group_id] = datetime.now(timezone.utc).timestamp()
        self._persist_group_state(group_id)

        # Emit events for all triggered items but return only one result
        for item in triggered:
            await self.event_bus.emit(SessionEvent(
                type=SessionEventType.DELAYED_RESPONSE_TRIGGERED,
                data={
                    "group_id": group_id,
                    "item_id": item.item_id,
                },
            ))

        # Determine return strategy: if any triggered item is IMMEDIATE, report as immediate
        from sirius_chat.models.response_strategy import ResponseStrategy
        strategy = "delayed"
        if any(i.strategy_decision.strategy == ResponseStrategy.IMMEDIATE for i in triggered):
            strategy = "immediate"

        # Never leak raw SKILL_CALL markers to the user.
        # If the model only emitted skill calls with no natural language,
        # fall back to the last partial reply or an empty string.
        final_reply = clean_reply or (partial_replies[-1] if partial_replies else "")

        return [{
            "strategy": strategy,
            "item_id": triggered[0].item_id,
            "reply": final_reply,
            "partial_replies": partial_replies,
        }]

    # ==================================================================
    # Persistence
    # ==================================================================

    def _persist_group_state(self, group_id: str) -> None:
        """Persist basic memory and timestamps for a single group in real-time."""
        entries = self.basic_memory.get_all(group_id)[-100:]
        self._state_store.save_working_memory(
            group_id,
            [
                {
                    "user_id": e.user_id,
                    "role": e.role,
                    "content": e.content,
                    "timestamp": e.timestamp,
                }
                for e in entries
            ],
        )
        self._state_store.save_group_timestamps(dict(self._group_last_message_at))

    def _persist_full_state(self) -> None:
        """Persist all runtime state to disk (used on graceful shutdown)."""
        working_memories: dict[str, list[dict[str, Any]]] = {}
        for group_id in self.basic_memory.list_groups():
            entries = self.basic_memory.get_all(group_id)[-100:]
            working_memories[group_id] = [
                {
                    "user_id": e.user_id,
                    "role": e.role,
                    "content": e.content,
                    "timestamp": e.timestamp,
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
            event_memory=self.event_memory.to_dict(),
            basic_memory=self.basic_memory.to_dict(),
            diary_state={
                "diarized_sources": {
                    gid: list(sids)
                    for gid, sids in self.diary_manager._diarized_sources.items()
                }
            },

        )

        # Save proactive state
        self._save_proactive_state()

        # Save persona
        from sirius_chat.core.persona_store import PersonaStore
        PersonaStore.save(self.work_path, self.persona)

    def save_state(self) -> None:
        """Persist all runtime state to disk."""
        self._persist_full_state()

    def load_state(self) -> None:
        """Restore runtime state from disk."""
        try:
            state = self._state_store.load_all()

            # Basic memory (fallback: migrate from old working_memories snapshots)
            basic_mem_data = state.get("basic_memory")
            if basic_mem_data:
                try:
                    self.basic_memory = BasicMemoryManager.from_dict(basic_mem_data)
                except Exception as exc:
                    logger.warning("基础记忆恢复失败，使用空实例: %s", exc)
                    self.basic_memory = BasicMemoryManager(
                        hard_limit=self.config.get("basic_memory_hard_limit", 30),
                        context_window=self.config.get("basic_memory_context_window", 5),
                    )
            else:
                # Migration fallback: load from legacy working_memories format
                for group_id, entries in state.get("working_memories", {}).items():
                    for e in entries:
                        self.basic_memory.add_entry(
                            group_id=group_id,
                            user_id=e.get("user_id", "unknown"),
                            role=e.get("role", "human"),
                            content=e.get("content", ""),
                            timestamp=e.get("timestamp"),
                        )

            # Assistant emotion
            ae = state.get("assistant_emotion")
            if ae:
                for key, value in ae.items():
                    if hasattr(self.assistant_emotion, key):
                        setattr(self.assistant_emotion, key, value)

            # Group timestamps
            self._group_last_message_at = dict(state.get("group_timestamps", {}))

            # Reset timestamps to now so the proactive silence timer starts fresh
            # after engine restart; otherwise offline time would be mis-counted as
            # group silence.
            now_iso = datetime.now(timezone.utc).isoformat()
            for gid in list(self._group_last_message_at.keys()):
                self._group_last_message_at[gid] = now_iso

            # Event memory v2
            event_mem_data = state.get("event_memory")
            if event_mem_data:
                try:
                    self.event_memory = EventMemoryManager.from_dict(event_mem_data)
                except Exception as exc:
                    logger.warning("事件记忆恢复失败，使用空实例: %s", exc)
                    self.event_memory = EventMemoryManager()

            # Diary state
            diary_state = state.get("diary_state")
            if diary_state:
                try:
                    sources = diary_state.get("diarized_sources", {})
                    self.diary_manager._diarized_sources = {
                        gid: set(sids)
                        for gid, sids in sources.items()
                    }
                except Exception as exc:
                    logger.warning("日记状态恢复失败: %s", exc)

            # User manager (with cross-group global profiles)
            user_mgr_data = state.get("user_manager")
            if user_mgr_data:
                try:
                    self.user_manager = UserManager.from_dict(user_mgr_data)
                except Exception as exc:
                    logger.warning("用户管理器恢复失败，使用空实例: %s", exc)

            # Re-bind context assembler to restored basic_memory
            self.context_assembler = ContextAssembler(
                self.basic_memory,
                self.diary_manager._retriever,
            )

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
                logger.info("我的人设已经加载好了，我是 %s～", loaded.name)

            logger.info("之前的记忆都找回来啦，一共 %d 个群的上下文我都记得。", len(state.get("working_memories", {})))
        except Exception as exc:
            logger.warning("状态恢复部分出错，继续尝试加载 proactive 状态: %s", exc)
        finally:
            # Proactive state must always be attempted regardless of other failures
            self._load_proactive_state()

    # ------------------------------------------------------------------
    # Proactive state persistence
    # ------------------------------------------------------------------

    def _save_proactive_state(self) -> None:
        """Persist proactive enabled/disabled groups and last trigger timestamps."""
        path = Path(self.work_path) / "engine_state" / "proactive_state.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        data = {
            "enabled_groups": sorted(self._proactive_enabled_groups),
            "disabled_groups": sorted(self._proactive_disabled_groups),
            "last_proactive_at": dict(self._last_proactive_at),
        }
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def _load_proactive_state(self) -> None:
        """Restore proactive state from disk."""
        path = Path(self.work_path) / "engine_state" / "proactive_state.json"
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                logger.warning("Proactive state file is not a dict, skipping")
                return
            # Force str keys to avoid int/str mismatch
            self._proactive_enabled_groups = {
                str(g) for g in data.get("enabled_groups", [])
            }
            self._proactive_disabled_groups = {
                str(g) for g in data.get("disabled_groups", [])
            }
            self._last_proactive_at = {
                str(k): str(v)
                for k, v in dict(data.get("last_proactive_at", {})).items()
            }
            # Sync into ProactiveTrigger
            self.proactive_trigger._last_proactive = dict(self._last_proactive_at)
            logger.info(
                "Proactive state loaded: %d enabled, %d disabled groups",
                len(self._proactive_enabled_groups),
                len(self._proactive_disabled_groups),
            )
        except Exception as exc:
            logger.warning("Proactive state 加载失败: %s", exc)

    def set_proactive_enabled(self, group_id: str, enabled: bool) -> None:
        """Enable or disable proactive triggers for a specific group."""
        gid = str(group_id)
        if enabled:
            self._proactive_enabled_groups.add(gid)
            self._proactive_disabled_groups.discard(gid)
        else:
            self._proactive_enabled_groups.discard(gid)
            self._proactive_disabled_groups.add(gid)
        self._save_proactive_state()

    def is_proactive_enabled(self, group_id: str) -> bool:
        """Check if proactive triggers are enabled for a group.

        Priority:
        1. If group is in disabled list → False
        2. If enabled_groups is not empty and group not in it → False
        3. Otherwise → True
        """
        gid = str(group_id)
        if gid in self._proactive_disabled_groups:
            return False
        if self._proactive_enabled_groups:
            return gid in self._proactive_enabled_groups
        return True

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
        # New: Register participants via identity resolver and user manager
        for p in participants:
            ctx = IdentityContext(
                speaker_name=p.name,
                user_id=p.user_id,
                platform_uid=p.identities.get(message.channel) if message.channel else None,
                platform=message.channel,
                is_developer=p.is_developer,
            )
            self.identity_resolver.resolve(ctx, self.user_manager, group_id)

        # Resolve current sender to a stable user_id (may reuse UUID from
        # participants or fall back to speaker name / platform_uid lookup).
        sender_ctx = IdentityContext(
            speaker_name=message.speaker or "unknown",
            user_id=None,
            platform_uid=message.channel_user_id,
            platform=message.channel,
            is_developer=False,
        )
        sender_profile = self.identity_resolver.resolve(
            sender_ctx, self.user_manager, group_id
        )
        resolved_user_id = sender_profile.user_id
        resolved_speaker_name = sender_profile.name

        # Add to basic memory and archive to disk
        entry = self.basic_memory.add_entry(
            group_id=group_id,
            user_id=resolved_user_id,
            speaker_name=resolved_speaker_name,
            role="human",
            content=message.content,
            channel_user_id=message.channel_user_id or "",
            multimodal_inputs=[
                dict(item) for item in message.multimodal_inputs
            ] if message.multimodal_inputs else None,
        )
        self.basic_store.append(entry)

        # Old: Buffer raw message for structured observation extraction
        if message.content and resolved_user_id:
            self.event_memory.buffer_message(
                user_id=resolved_user_id,
                content=message.content,
                group_id=group_id,
            )

        # Update group last message time
        from sirius_chat.core.utils import now_iso
        self._group_last_message_at[group_id] = now_iso()
        self._persist_group_state(group_id)
        return resolved_user_id

    async def _cognition(
        self,
        content: str,
        user_id: str,
        group_id: str,
    ) -> tuple[IntentAnalysisV3, EmotionState, list[dict[str, Any]], Any]:
        """Cognitive layer: unified emotion + intent + empathy + memory retrieval."""
        # Build context from recent working memory (exclude current message)
        recent = self._get_recent_messages(group_id, n=6)
        if recent and recent[-1].get("content") == content:
            context_messages = recent[:-1]
        else:
            context_messages = recent

        # Joint cognition (emotion + intent + empathy in one pass)
        emotion, intent, empathy = await self.cognition_analyzer.analyze(
            content, user_id, group_id, context_messages
        )

        # Memory retrieval now happens in execution via ContextAssembler
        memories = []

        return intent, emotion, memories, empathy

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
            heat_level=rhythm.heat_level,
        )

        # Reply cooldown suppression: delayed responses are throttled,
        # but immediate responses (e.g. direct mentions) bypass cooldown.
        from sirius_chat.models.response_strategy import ResponseStrategy
        now = datetime.now(timezone.utc).timestamp()
        last_reply = self._last_reply_at.get(group_id, 0)
        seconds_since_reply = now - last_reply
        cooldown = self.config.get("reply_cooldown_seconds", 30)
        if seconds_since_reply < cooldown and decision.strategy == ResponseStrategy.DELAYED:
            decision = StrategyDecision(
                strategy=ResponseStrategy.SILENT,
                score=0.0,
                threshold=decision.threshold,
                urgency=decision.urgency,
                relevance=decision.relevance,
                reason=f"cooldown_{int(seconds_since_reply)}s",
            )
            self._log_inner_thought(f"群里正聊得火热呢，我刚回完不久，先闭嘴看看...")

        # Private-chat floor: never stay completely silent in 1-on-1
        if group_id.startswith("private_") and decision.strategy == ResponseStrategy.SILENT:
            decision = StrategyDecision(
                strategy=ResponseStrategy.DELAYED,
                score=decision.score,
                threshold=decision.threshold,
                urgency=max(decision.urgency, 25.0),
                relevance=max(decision.relevance, 0.5),
                reason=f"private_chat_floor:{decision.reason}",
            )
            self._log_inner_thought("虽然是私聊，但完全不回好像有点尴尬，等会儿还是回一条吧...")

        # 内心活动：决策后的思考
        self._log_decision_thought(intent, decision)

        # Update assistant emotion
        self.assistant_emotion.update_from_interaction(emotion, user_id)

        # Semantic: record atmosphere snapshot and update user relationship
        recent_msgs = self._get_recent_messages(group_id, n=10)
        self.semantic_memory.record_atmosphere(
            group_id=group_id,
            valence=emotion.valence,
            arousal=emotion.arousal,
            active_participants=len({m.get("user_id") for m in recent_msgs}),
        )
        social_intent = getattr(intent, "social_intent", None)
        self.semantic_memory.update_relationship(
            group_id=group_id,
            user_id=user_id,
            valence=emotion.valence,
            urgency_score=getattr(intent, "urgency_score", 0),
            social_intent=str(social_intent) if social_intent else "",
        )

        return decision

    async def _execution(
        self,
        decision: StrategyDecision,
        message: Message,
        intent: IntentAnalysisV3,
        emotion: EmotionState,
        memories: list[dict[str, Any]],
        group_id: str,
        empathy: Any,
        user_id: str,
    ) -> dict[str, Any]:
        """Execution layer: generate or queue reply."""
        # Rhythm context for style adaptation
        recent_msgs = self._get_recent_messages(group_id, n=10)
        rhythm = self.rhythm_analyzer.analyze(group_id, recent_msgs)

        # Profiles
        group_profile = self.semantic_memory.get_group_profile(group_id)
        user_profile = (
            self.semantic_memory.get_user_profile(group_id, user_id)
            if user_id else None
        )

        is_group_chat = not group_id.startswith("private_")

        # Build cross-group awareness for the current user
        cross_group_context = ""
        if user_id:
            global_user = self.user_manager.get_global_user(user_id)
            global_semantic = self.semantic_memory.get_global_user_profile(user_id)
            # Only generate if user has activity in multiple groups
            group_count = sum(
                1 for gid, group in self.user_manager.entries.items()
                if user_id in group and gid != group_id
            )
            if group_count > 0 or (global_semantic and global_semantic.communication_style):
                parts: list[str] = []
                if group_count > 0:
                    parts.append(f"你在 {group_count} 个其他群中也认识 {message.speaker or 'TA'}")
                if global_user and global_user.aliases:
                    parts.append(f"TA 的别名/昵称有：{', '.join(global_user.aliases[:3])}")
                if global_semantic:
                    if global_semantic.communication_style:
                        parts.append(f"沟通风格：{global_semantic.communication_style}")
                    if global_semantic.interest_graph:
                        topics = [str(item) for item in global_semantic.interest_graph[:3]]
                        parts.append(f"兴趣话题：{', '.join(topics)}")
                cross_group_context = "；".join(parts) + "。"

        # Determine if the current sender is a developer
        caller_profile = None
        if message.channel_user_id and message.channel:
            resolved_uid = self.user_manager.resolve_user_id(
                platform=message.channel, external_uid=message.channel_user_id
            )
            if resolved_uid:
                caller_profile = self.user_manager.get_user(resolved_uid, group_id)
        caller_is_developer = bool(caller_profile and caller_profile.is_developer)

        if decision.strategy == ResponseStrategy.IMMEDIATE:
            self._log_inner_thought("让我先稍等片刻，看看有没有后续消息...")
            self.delayed_queue.enqueue(
                group_id=group_id,
                user_id=user_id,
                message_content=message.content,
                strategy_decision=decision,
                emotion_state=emotion.to_dict(),
                candidate_memories=[m.get("content", "") for m in memories],
                channel=message.channel,
                channel_user_id=message.channel_user_id,
            )
            self._persist_group_state(group_id)
            return {
                "strategy": "immediate",
                "reply": None,
                "emotion": emotion.to_dict(),
                "intent": intent.to_dict(),
                "thought": "",
                "partial_replies": [],
            }

        if decision.strategy == ResponseStrategy.DELAYED:
            self._log_inner_thought("现在不是最佳时机，我先把这个话题记在小本本上，等会儿再回。")
            self.delayed_queue.enqueue(
                group_id=group_id,
                user_id=user_id,
                message_content=message.content,
                strategy_decision=decision,
                emotion_state=emotion.to_dict(),
                candidate_memories=[m.get("content", "") for m in memories],
                channel=message.channel,
                channel_user_id=message.channel_user_id,
            )
            self._persist_group_state(group_id)
            return {
                "strategy": "delayed",
                "reply": None,
                "emotion": emotion.to_dict(),
                "intent": intent.to_dict(),
            }

        self._persist_group_state(group_id)
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
        user_id: str,
    ) -> None:
        """Background updates after main pipeline."""
        # Update group sentiment cache for emotion island detection
        self.cognition_analyzer.update_group_sentiment(group_id, emotion)

        # Update assistant emotion based on interaction
        self.assistant_emotion.update_from_interaction(
            emotion, user_id
        )

    # ==================================================================
    # Prompt builders & generation
    # ==================================================================

    @staticmethod
    def _build_skill_result_content(
        skill_results: list[str],
        multimodal_blocks: list[dict[str, Any]],
        suffix: str = "",
    ) -> str | list[dict[str, Any]]:
        """Assemble skill execution results into message content.

        If *multimodal_blocks* is non-empty, returns an OpenAI-compatible
        ``content`` list (text + image_url parts) so local image paths are
        later converted to base64 data URLs by the transport layer.

        Long text results are truncated to avoid consuming excessive context
        window tokens, leaving room for the model's final reply.
        """
        _SKILL_RESULT_CHAR_LIMIT = 12000  # ~4k tokens, leaves headroom for reply
        results_text = "\n".join(skill_results)
        if len(results_text) > _SKILL_RESULT_CHAR_LIMIT:
            truncated = results_text[:_SKILL_RESULT_CHAR_LIMIT]
            # Try to cut at a newline boundary for cleanliness
            last_nl = truncated.rfind("\n")
            if last_nl > _SKILL_RESULT_CHAR_LIMIT * 0.8:
                truncated = truncated[:last_nl]
            results_text = (
                f"{truncated}\n\n"
                f"[注：技能结果过长，已截断至前 {_SKILL_RESULT_CHAR_LIMIT} 字符，"
                f"原始长度 {len(results_text)} 字符]"
            )
        text = f"[技能执行结果]\n{results_text}{suffix}"
        if not multimodal_blocks:
            return text
        content: list[dict[str, Any]] = [{"type": "text", "text": text}]
        content.extend(multimodal_blocks)
        return content

    def _build_delayed_prompt(self, items: Any, group_id: str, caller_is_developer: bool = False):
        """Build prompt bundle for delayed response (supports single item or merged list)."""
        from sirius_chat.core.response_assembler import PromptBundle
        from sirius_chat.models.response_strategy import ResponseStrategy
        if not isinstance(items, list):
            items = [items]
        if len(items) == 1:
            message_content = items[0].message_content
        else:
            # Merge multiple messages into a single context
            lines = ["以下是你之前决定稍后回复的几条消息："]
            for idx, item in enumerate(items, 1):
                lines.append(f"{idx}. {item.message_content}")
            message_content = "\n".join(lines)
        glossary = self.glossary_manager.build_prompt_section(
            group_id, text=message_content, max_terms=5
        )
        bundle = self.response_assembler.assemble_delayed(
            message_content=message_content,
            group_profile=self.semantic_memory.get_group_profile(group_id),
            is_group_chat=True,
            caller_is_developer=caller_is_developer,
            glossary_section=glossary,
        )
        return bundle

    def _pick_proactive_topic(self, group_id: str) -> str:
        """Pick a topic from semantic memory for proactive initiation."""
        group_profile = self.semantic_memory.get_group_profile(group_id)
        if group_profile is None:
            return ""

        # Collect candidate topics from group-level and user-level semantic memory
        candidates: list[str] = []

        # 1. Group-level interest topics
        if group_profile.interest_topics:
            candidates.extend(group_profile.interest_topics)

        # 2. User-level interest graphs (high participation topics)
        for profile in self.semantic_memory.list_group_user_profiles(group_id):
            for node in profile.interest_graph:
                if node.participation >= 0.3 and node.topic:
                    candidates.append(node.topic)

        # 3. Dominant topic from group norms (if available)
        dominant = group_profile.group_norms.get("dominant_topic", "")
        if dominant:
            candidates.append(dominant)

        if not candidates:
            return ""

        # Filter out taboo topics
        taboo = set(group_profile.taboo_topics or [])
        candidates = [t for t in candidates if t not in taboo]

        if not candidates:
            return ""

        # Deduplicate while preserving order (first occurrence = higher relevance)
        seen: set[str] = set()
        unique: list[str] = []
        for t in candidates:
            if t not in seen:
                seen.add(t)
                unique.append(t)

        # Pick the first (highest-relevance) topic
        return unique[0]

    def _build_proactive_prompt(self, trigger: dict[str, Any], group_id: str):
        """Build prompt bundle for proactive initiation."""
        glossary = self.glossary_manager.build_prompt_section(
            group_id, text=trigger.get("trigger_type", ""), max_terms=3
        )
        topic = self._pick_proactive_topic(group_id)
        return self.response_assembler.assemble_proactive(
            trigger_reason=trigger.get("trigger_type", "silence"),
            group_profile=self.semantic_memory.get_group_profile(group_id),
            suggested_tone=trigger.get("suggested_tone", "casual"),
            is_group_chat=True,
            glossary_section=glossary,
            topic_context=topic,
        )

    async def _generate(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        group_id: str,
        style_params: StyleParams | None = None,
        task_name: str = "response_generate",
        urgency: int = 0,
    ) -> str:
        """Call LLM provider to generate response.

        Args:
            system_prompt: Instruction-level context (persona, emotion, skills, etc.).
            messages: Standard OpenAI-format conversation history ending with the
                current user message.
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

        # No dual-output format; use token budget as-is

        # Build GenerationRequest
        from sirius_chat.providers.base import GenerationRequest, LLMProvider

        request = GenerationRequest(
            model=cfg.model_name,
            system_prompt=system_prompt.strip(),
            messages=messages,
            temperature=effective_temperature,
            max_tokens=effective_max_tokens,
            timeout_seconds=cfg.timeout,
            purpose=task_name,
        )

        # Estimate input tokens
        from sirius_chat.providers.base import estimate_generation_request_input_tokens
        estimated_input_tokens = estimate_generation_request_input_tokens(request)

        # Debug: log the full prompt sent to the LLM
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "LLM prompt for group=%s:\nSYSTEM:\n%s\n\nMESSAGES:\n%s",
                group_id,
                system_prompt,
                "\n".join(f"  [{m.get('role')}] {m.get('content', '')[:200]}" for m in messages),
            )

        # Call provider (async or sync via thread)
        if hasattr(self.provider_async, "generate_async"):
            reply = await self.provider_async.generate_async(request)
        elif isinstance(self.provider_async, LLMProvider):
            reply = await asyncio.to_thread(self.provider_async.generate, request)
        else:
            raise RuntimeError("配置的提供商未实现 generate/generate_async 方法。")

        # Sanitise: strip any echoed <conversation_history> XML blocks
        reply = self._strip_conversation_history_xml(reply)

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
            input_chars=len(system_prompt) + sum(len(str(m.get("content", ""))) for m in messages),
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
        # Propagate skill registry to response assembler so it can include
        # skill descriptions in the system prompt.
        if skill_registry is not None:
            self.response_assembler.skill_registry = skill_registry

    def _get_recent_messages(self, group_id: str, n: int = 10) -> list[dict[str, Any]]:
        entries = self.basic_memory.get_all(group_id)[-n:]
        return [
            {
                "user_id": e.user_id,
                "content": e.content,
                "timestamp": e.timestamp,
            }
            for e in entries
        ]

    @staticmethod
    def _strip_conversation_history_xml(text: str) -> str:
        """Remove any <conversation_history> blocks that the model may echo back.

        Because short-term memory is embedded in the system prompt as XML,
        some models may imitate the format in their output. This sanitiser
        strips those accidental blocks.
        """
        import re
        # Remove <conversation_history>...</conversation_history> (non-greedy, multiline)
        cleaned = re.sub(r"<conversation_history>.*?</conversation_history>", "", text, flags=re.DOTALL)
        # Also clean up stray opening/closing tags just in case
        cleaned = re.sub(r"</?conversation_history>", "", cleaned)
        return cleaned.strip()

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

    @staticmethod
    def _is_pure_image_message(content: str) -> bool:
        """Check if content contains only image placeholders with no substantive text.

        Image placeholder format: [图片: filename.png] or [图片1: filename.png]
        """
        if not content:
            return False
        cleaned = re.sub(r"\[图片\d*: [^\]]+\]", "", content).strip()
        return not cleaned




def _is_reminder_due(reminder: dict[str, Any], now: datetime) -> bool:
    """Check whether a single reminder should fire at *now*."""
    mode = reminder.get("mode", "once")
    if mode == "once":
        fire_at_str = reminder.get("fire_at")
        if not fire_at_str:
            return False
        try:
            fire_at = datetime.fromisoformat(str(fire_at_str).replace("Z", "+00:00"))
        except ValueError:
            return False
        return now >= fire_at

    if mode in ("daily", "weekly"):
        time_str = reminder.get("time", "")
        if not time_str or ":" not in time_str:
            return False
        try:
            h, m = map(int, str(time_str).split(":"))
        except ValueError:
            return False
        if now.hour != h or now.minute != m:
            return False
        # Avoid duplicate fire within the same minute
        last_fired = reminder.get("last_fired_at")
        if last_fired:
            try:
                last_dt = datetime.fromisoformat(str(last_fired).replace("Z", "+00:00"))
                if (
                    last_dt.year == now.year
                    and last_dt.month == now.month
                    and last_dt.day == now.day
                    and last_dt.hour == now.hour
                    and last_dt.minute == now.minute
                ):
                    return False
            except ValueError:
                pass
        if mode == "weekly":
            weekday = reminder.get("weekday")
            if weekday is not None and now.weekday() != int(weekday):
                return False
        return True

    return False
