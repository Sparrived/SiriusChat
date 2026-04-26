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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sirius_chat.core.cognition import CognitionAnalyzer
from sirius_chat.core.proactive_trigger import ProactiveTrigger
from sirius_chat.core.response_strategy import ResponseStrategyEngine
from sirius_chat.core.delayed_response_queue import DelayedResponseQueue
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

        # 5. Background memory updates
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
        """Periodically tick delayed queue for all active groups.

        Note: This task only monitors pending items and emits events.
        Actual reply generation and delivery must be handled by the external
        caller (e.g. _background_delivery_loop in the QQ plugin) via
        tick_delayed_queue() to avoid consuming queue items without delivery.
        """
        interval = self.config.get("delayed_queue_tick_interval_seconds", 10)
        while self._bg_running:
            await asyncio.sleep(interval)
            for group_id in list(self._group_last_message_at.keys()):
                try:
                    pending = self.delayed_queue.get_pending(group_id)
                    if pending:
                        self._log_inner_thought("之前记下的延迟回复，现在该开口了～")
                        # Emit event so external delivery loop can call
                        # tick_delayed_queue() to generate and send the reply.
                        for item in pending:
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
        """Periodically consolidate diary entries (no-op for now)."""
        interval = self.config.get("consolidation_interval_seconds", 600)
        while self._bg_running:
            await asyncio.sleep(interval)
            try:
                # Placeholder: deduplication or merging can be added later
                pass
            except Exception as exc:
                logger.warning("Diary consolidation failed: %s", exc)

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

    async def tick_delayed_queue(self, group_id: str) -> list[dict[str, Any]]:
        """Process delayed response queue for a group.

        If multiple items trigger in the same tick, merge them into a single
        prompt so the model generates only one consolidated reply.
        Supports multi-round SKILL execution similar to immediate responses.
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

        for _round in range(max_skill_rounds + 1):
            raw_reply = await self._generate(
                system_prompt, messages, group_id
            )
            reply = raw_reply.strip()

            calls = parse_skill_calls(reply)
            if not calls or self._skill_registry is None or self._skill_executor is None:
                break

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
                    skill_results.append(f"[{err}]")
                    continue
                if skill.developer_only and not caller_is_developer:
                    err = f"SKILL '{skill_name}' 被拒绝：caller 不是 developer"
                    logger.warning(err)
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
                    else:
                        err = result.error or "未知错误"
                        logger.warning("SKILL '%s' 执行失败: %s", skill_name, err)
                        skill_results.append(f"[SKILL '{skill_name}' 失败] {err}")
                except Exception as exc:
                    logger.error("SKILL '%s' 执行异常: %s", skill_name, exc)
                    skill_results.append(f"[SKILL '{skill_name}' 异常] {exc}")

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

        return [{
            "strategy": "delayed",
            "item_id": triggered[0].item_id,
            "reply": reply,
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
            self._log_inner_thought("让我好好想想该怎么回应...")
            # Build recent participants list for identity context
            recent_participants: list[dict[str, Any]] = []
            if is_group_chat:
                group_entries = self.user_manager.entries.get(group_id, {})
                for uid, profile in list(group_entries.items())[:5]:
                    qq_id = profile.identities.get("qq_plugin_sirius_chat_v28", "")
                    recent_participants.append({
                        "user_id": uid,
                        "name": profile.name,
                        "aliases": profile.aliases,
                        "qq_id": qq_id or uid,
                    })
            glossary = self.glossary_manager.build_prompt_section(
                group_id, text=message.content, max_terms=5
            )
            bundle = self.response_assembler.assemble(
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
                is_group_chat=is_group_chat,
                recent_participants=recent_participants if recent_participants else None,
                caller_is_developer=caller_is_developer,
                glossary_section=glossary,
            )
            style = self.style_adapter.adapt(
                heat_level=rhythm.heat_level,
                pace=rhythm.pace,
                user_communication_style=getattr(user_profile, "communication_style", ""),
                topic_stability=rhythm.topic_stability,
                is_group_chat=is_group_chat,
            )

            # Build messages via new context assembler (basic memory + diary RAG)
            # Use bundle.user_content so the LLM receives sender identity metadata.
            messages = self.context_assembler.build_messages(
                group_id=group_id,
                current_query=bundle.user_content,
                search_query=intent.search_query,
                system_prompt=bundle.system_prompt,
                recent_n=self.config.get("basic_memory_context_window", 5),
                diary_top_k=self.config.get("diary_top_k", 5),
                diary_token_budget=self.config.get("diary_token_budget", 800),
            )
            # Avoid duplicating system prompt in _generate by passing enriched
            # system prompt separately and stripping it from messages.
            if messages and messages[0]["role"] == "system":
                system_prompt_for_generate = messages[0]["content"]
                messages = messages[1:]
            else:
                system_prompt_for_generate = bundle.system_prompt

            # Multi-round skill calling: generate → detect SKILL_CALL →
            # emit partial reply → execute skill → re-generate with result injected.
            from sirius_chat.skills.executor import parse_skill_calls, strip_skill_calls
            from sirius_chat.skills.models import SkillInvocationContext

            partial_replies: list[str] = []
            max_skill_rounds = max(1, self.config.get("max_skill_rounds", 3))
            say = ""

            for _round in range(max_skill_rounds + 1):
                raw_reply = await self._generate(
                    system_prompt_for_generate, messages, group_id, style
                )
                say = raw_reply.strip()

                # Check if the reply contains skill calls
                calls = parse_skill_calls(say)
                if not calls or self._skill_registry is None or self._skill_executor is None:
                    # No more skill calls — finalize
                    break

                # Extract non-skill text as a partial reply to send immediately.
                # For silent-only skills we still do a re-generation so the model
                # can synthesize the skill results into a natural reply.
                non_skill_text = strip_skill_calls(say).strip()
                if non_skill_text:
                    partial_replies.append(non_skill_text)
                    self._log_inner_thought(f"先跟用户回一声：{non_skill_text[:40]}...")

                # Execute skills and collect results
                skill_results: list[str] = []
                skill_multimodal: list[dict[str, Any]] = []
                from sirius_chat.memory.user.models import UserProfile
                skill_caller = UserProfile(
                    user_id=message.channel_user_id or "unknown",
                    name=message.speaker or "unknown",
                    metadata={"is_developer": caller_is_developer},
                )
                # Collect all developer profiles in the current group for security check
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
                        skill_results.append(f"[{err}]")
                        continue
                    if skill.developer_only and not caller_is_developer:
                        err = f"SKILL '{skill_name}' 被拒绝：caller 不是 developer"
                        logger.warning(err)
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
                        else:
                            err = result.error or "未知错误"
                            logger.warning("SKILL '%s' 执行失败: %s", skill_name, err)
                            skill_results.append(f"[SKILL '{skill_name}' 失败] {err}")
                    except Exception as exc:
                        logger.error("SKILL '%s' 执行异常: %s", skill_name, exc)
                        skill_results.append(f"[SKILL '{skill_name}' 异常] {exc}")

                # Inject skill results into the conversation for the next round
                messages.append({"role": "assistant", "content": strip_skill_calls(say)})
                messages.append({
                    "role": "user",
                    "content": self._build_skill_result_content(
                        skill_results,
                        skill_multimodal,
                        suffix="\n\n[继续] 请基于以上技能执行结果，继续完成你的回复。",
                    ),
                })

                # Persist intermediate skill turns into basic memory
                self.basic_memory.add_entry(
                    group_id=group_id,
                    user_id="assistant",
                    role="assistant",
                    content=strip_skill_calls(say),
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
            clean_reply = strip_skill_calls(say).strip()
            if clean_reply:
                self.basic_memory.add_entry(
                    group_id=group_id,
                    user_id="assistant",
                    speaker_name=self.persona.name if self.persona else "assistant",
                    role="assistant",
                    content=clean_reply,
                    system_prompt=bundle.system_prompt,
                )

            # Record reply timestamp for cooldown tracking
            self._last_reply_at[group_id] = datetime.now(timezone.utc).timestamp()
            self._persist_group_state(group_id)

            return {
                "strategy": "immediate",
                "reply": say,
                "emotion": emotion.to_dict(),
                "intent": intent.to_dict(),
                "thought": "",
                "partial_replies": partial_replies,
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
        """
        results_text = "\n".join(skill_results)
        text = f"[技能执行结果]\n{results_text}{suffix}"
        if not multimodal_blocks:
            return text
        content: list[dict[str, Any]] = [{"type": "text", "text": text}]
        content.extend(multimodal_blocks)
        return content

    def _build_delayed_prompt(self, items: Any, group_id: str, caller_is_developer: bool = False):
        """Build prompt bundle for delayed response (supports single item or merged list)."""
        from sirius_chat.core.response_assembler import PromptBundle
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
        return self.response_assembler.assemble_delayed(
            message_content=message_content,
            group_profile=self.semantic_memory.get_group_profile(group_id),
            is_group_chat=True,
            caller_is_developer=caller_is_developer,
            glossary_section=glossary,
        )

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


