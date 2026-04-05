from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import re
from typing import Any

from sirius_chat.trait_taxonomy import TRAIT_TAXONOMY
from sirius_chat.event_memory import (
    ContextualEventInterpretation,
    EventMemoryEntry,
    EventMemoryManager,
    EventMemoryFileStore,
)

logger = logging.getLogger(__name__)

# ============================================================================
# 性能优化常数
# ============================================================================

# C1: Memory Facts 上限管理
MAX_MEMORY_FACTS = 50  # 单用户最多保留的memory facts数量

# A1: 时间窗口去重（分钟）
EVENT_DEDUP_WINDOW_MINUTES = 5


@dataclass(slots=True)
class UserProfile:
    """初始化档案：由外部在会话开始前提供，不应在运行中被 AI 随意覆盖。"""

    user_id: str
    name: str
    persona: str = ""
    identities: dict[str, str] = field(default_factory=dict)
    aliases: list[str] = field(default_factory=list)
    traits: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MemoryFact:
    """可追溯的记忆事实记录。支持多模型协作和冲突检测。
    
    C2优化: is_transient标记用于分离临时/永久事实
    - RESIDENT (confidence > 0.85): 持久化到user.json
    - TRANSIENT: session内存，30分钟自动清理
    """

    fact_type: str
    value: str
    source: str = "unknown"
    confidence: float = 0.5
    observed_at: str = ""
    memory_category: str = "custom"  # identity|preference|emotion|event|custom
    validated: bool = False  # 是否通过 memory_manager 验证
    conflict_with: list[str] = field(default_factory=list)  # 冲突记忆ID列表
    # C2: RESIDENT vs TRANSIENT 分离标记
    is_transient: bool = False  # 是否是临时事实（confidence ≤ 0.85）
    created_at: str = ""  # 创建时间（ISO格式），用于过期判断


@dataclass(slots=True)
class UserRuntimeState:
    """运行时状态：由系统/AI在会话中持续更新。"""

    inferred_persona: str = ""
    inferred_traits: list[str] = field(default_factory=list)
    preference_tags: list[str] = field(default_factory=list)
    recent_messages: list[str] = field(default_factory=list)
    summary_notes: list[str] = field(default_factory=list)
    memory_facts: list[MemoryFact] = field(default_factory=list)
    last_seen_channel: str = ""
    last_seen_uid: str = ""
    # 事件观测特征集合（用于与新事件做一致性比对）
    observed_keywords: set[str] = field(default_factory=set)
    observed_roles: set[str] = field(default_factory=set)
    observed_emotions: set[str] = field(default_factory=set)
    observed_entities: set[str] = field(default_factory=set)
    # A1: 时间窗口去重 - 记录上次处理事件的时间
    last_event_processed_at: datetime | None = None


@dataclass(slots=True)
class UserMemoryEntry:
    profile: UserProfile
    runtime: UserRuntimeState = field(default_factory=UserRuntimeState)

    @property
    def recent_messages(self) -> list[str]:
        """Backward-compatible alias for legacy callers."""
        return self.runtime.recent_messages

    @property
    def summary_notes(self) -> list[str]:
        """Backward-compatible alias for legacy callers."""
        return self.runtime.summary_notes


@dataclass(slots=True)
class UserMemoryManager:
    entries: dict[str, UserMemoryEntry] = field(default_factory=dict)
    speaker_index: dict[str, str] = field(default_factory=dict)
    identity_index: dict[str, str] = field(default_factory=dict)

    @staticmethod
    def _normalize_label(label: str) -> str:
        return label.strip().lower()

    @staticmethod
    def _identity_key(channel: str, external_user_id: str) -> str:
        return f"{channel.strip().lower()}:{external_user_id.strip().lower()}"

    @staticmethod
    def _now_iso() -> str:
        return datetime.now().isoformat(timespec="seconds")

    @staticmethod
    def _normalize_summary_note(note: str) -> str:
        value = note.strip().lower()
        prefixes = ("事件摘要：", "多模态证据：")
        for prefix in prefixes:
            if value.startswith(prefix):
                value = value[len(prefix) :].strip()
        return value

    def _append_summary_note(self, *, entry: UserMemoryEntry, note: str, max_notes: int = 8) -> bool:
        value = note.strip()
        if not value:
            return False
        normalized = self._normalize_summary_note(value)
        for existing in entry.runtime.summary_notes:
            if self._normalize_summary_note(existing) == normalized:
                return False
        entry.runtime.summary_notes.append(value)
        if len(entry.runtime.summary_notes) > max_notes:
            entry.runtime.summary_notes = entry.runtime.summary_notes[-max_notes:]
        return True

    def _normalize_trait(self, trait: str) -> str:
        """B方案：将特征规范化为分类标签或保留原样。
        
        如果特征属于已定义的分类（通过关键词匹配），则返回分类标签。
        否则返回原始特征，避免过度规范化。
        """
        if not trait or not isinstance(trait, str):
            return ""
        
        trait_stripped = trait.strip().lower()
        if not trait_stripped:
            return ""
        
        # 检查是否已经是一个分类标签
        if trait in TRAIT_TAXONOMY:
            return trait
        
        # 检查是否属于某个分类的关键词
        for category, info in TRAIT_TAXONOMY.items():
            keywords = info.get("keywords", [])
            # 精确匹配或包含匹配都可以
            if any(kw.lower() in trait_stripped or trait_stripped in kw.lower() for kw in keywords):
                return category
        
        return trait  # 无法分类，保留原样

    def add_memory_fact(
        self,
        *,
        user_id: str,
        fact_type: str,
        value: str,
        source: str,
        confidence: float,
        observed_at: str | None = None,
        max_facts: int | None = None,
    ) -> None:
        """添加内存事实，支持特征规范化和智能上限管理。
        
        C1方案：当超过max_facts时，删除confidence最低的facts而非简单的FIFO。
        B方案：对某些fact_type自动应用特征规范化。
        """
        entry = self.entries.get(user_id)
        if entry is None:
            return
        
        # 使用全局常数作为默认上限
        if max_facts is None:
            max_facts = MAX_MEMORY_FACTS
        
        text = value.strip()
        if not text:
            return
        
        # B方案: 规范化特征
        if fact_type in ("trait", "inferred_trait", "preference_tag"):
            normalized_trait = self._normalize_trait(text)
            if normalized_trait:
                text = normalized_trait
        
        timestamp = observed_at or self._now_iso()
        normalized = self._normalize_summary_note(text)
        
        # 检查是否已存在相同的fact，如果存在则更新confidence
        for item in entry.runtime.memory_facts:
            if item.fact_type != fact_type:
                continue
            if self._normalize_summary_note(item.value) != normalized:
                continue
            if confidence > item.confidence:
                item.confidence = confidence
                item.source = source
                item.observed_at = timestamp
            return
        
        # 添加新fact
        final_confidence = max(0.0, min(1.0, float(confidence)))
        # C2方案: 自动标记is_transient和created_at
        is_transient_fact = final_confidence <= 0.85
        created_at_time = timestamp if is_transient_fact else ""
        
        entry.runtime.memory_facts.append(
            MemoryFact(
                fact_type=fact_type,
                value=text,
                source=source,
                confidence=final_confidence,
                observed_at=timestamp,
                is_transient=is_transient_fact,
                created_at=created_at_time,
            )
        )
        
        # C1方案: 智能清理 - 当超过上限时，删除confidence最低的
        if len(entry.runtime.memory_facts) > max_facts:
            # 按confidence升序排序
            sorted_facts = sorted(
                entry.runtime.memory_facts,
                key=lambda f: f.confidence
            )
            # 计算要删除的数量（删除最低的10%，至少删除1个）
            num_to_delete = max(1, len(entry.runtime.memory_facts) // 10)
            # 保留top 90%的facts
            entry.runtime.memory_facts = sorted_facts[num_to_delete:]

    def register_user(self, profile: UserProfile) -> None:
        if not profile.user_id:
            profile.user_id = profile.name
        if profile.user_id not in self.entries:
            self.entries[profile.user_id] = UserMemoryEntry(profile=profile)
        else:
            existing = self.entries[profile.user_id].profile
            if profile.name and not existing.name:
                existing.name = profile.name
            if profile.persona and not existing.persona:
                existing.persona = profile.persona
            for channel, external_id in profile.identities.items():
                if channel and external_id:
                    existing.identities[channel] = external_id
            for alias in profile.aliases:
                if alias not in existing.aliases:
                    existing.aliases.append(alias)
            for trait in profile.traits:
                if trait not in existing.traits:
                    existing.traits.append(trait)
            existing.metadata.update(profile.metadata)

        labels = [profile.user_id, profile.name, *profile.aliases]
        for label in labels:
            if not label:
                continue
            self.speaker_index[self._normalize_label(label)] = profile.user_id

        for channel, external_id in profile.identities.items():
            if not channel or not external_id:
                continue
            self.identity_index[self._identity_key(channel, external_id)] = profile.user_id

    def resolve_user_id(
        self,
        *,
        speaker: str | None = None,
        channel: str | None = None,
        external_user_id: str | None = None,
    ) -> str | None:
        if channel and external_user_id:
            identity_user_id = self.identity_index.get(self._identity_key(channel, external_user_id))
            if identity_user_id:
                return identity_user_id
        if speaker:
            return self.speaker_index.get(self._normalize_label(speaker))
        return None

    def resolve_user_id_by_identity(self, *, channel: str, external_user_id: str) -> str | None:
        return self.identity_index.get(self._identity_key(channel, external_user_id))

    def get_user_by_identity(self, *, channel: str, external_user_id: str) -> UserMemoryEntry | None:
        user_id = self.resolve_user_id_by_identity(channel=channel, external_user_id=external_user_id)
        if not user_id:
            return None
        return self.entries.get(user_id)

    def ensure_user(self, *, speaker: str, persona: str = "") -> UserProfile:
        resolved_user_id = self.resolve_user_id(speaker=speaker)
        if resolved_user_id and resolved_user_id in self.entries:
            entry = self.entries[resolved_user_id]
            if persona and not entry.profile.persona:
                entry.profile.persona = persona
            return entry.profile

        profile = UserProfile(user_id=speaker, name=speaker, persona=persona)
        self.register_user(profile)
        return profile

    def remember_message(
        self,
        *,
        profile: UserProfile,
        content: str,
        max_recent_messages: int,
        channel: str | None = None,
        channel_user_id: str | None = None,
    ) -> None:
        self.register_user(profile)
        entry = self.entries[profile.user_id]
        entry.runtime.recent_messages.append(content)
        if len(entry.runtime.recent_messages) > max_recent_messages:
            entry.runtime.recent_messages = entry.runtime.recent_messages[-max_recent_messages:]
        if channel:
            entry.runtime.last_seen_channel = channel
        if channel_user_id:
            entry.runtime.last_seen_uid = channel_user_id

    def apply_ai_runtime_update(
        self,
        *,
        user_id: str,
        inferred_persona: str | None = None,
        inferred_aliases: list[str] | None = None,
        inferred_traits: list[str] | None = None,
        preference_tags: list[str] | None = None,
        summary_note: str | None = None,
        source: str = "unknown",
        confidence: float = 0.5,
    ) -> None:
        entry = self.entries.get(user_id)
        if entry is None:
            return
        if inferred_persona:
            entry.runtime.inferred_persona = inferred_persona
        if inferred_aliases:
            for alias in inferred_aliases:
                value = alias.strip()
                if not value:
                    continue
                if value not in entry.profile.aliases:
                    entry.profile.aliases.append(value)
                self.speaker_index[self._normalize_label(value)] = user_id
        if inferred_traits:
            for item in inferred_traits:
                if item not in entry.runtime.inferred_traits:
                    entry.runtime.inferred_traits.append(item)
        if preference_tags:
            for item in preference_tags:
                if item not in entry.runtime.preference_tags:
                    entry.runtime.preference_tags.append(item)
        if summary_note:
            appended = self._append_summary_note(entry=entry, note=summary_note, max_notes=8)
            if appended:
                self.add_memory_fact(
                    user_id=user_id,
                    fact_type="summary",
                    value=summary_note,
                    source=source,
                    confidence=confidence,
                )

    def add_summary_note(self, *, user_id: str, note: str, max_notes: int = 8) -> None:
        entry = self.entries.get(user_id)
        if entry is None:
            return
        appended = self._append_summary_note(entry=entry, note=note, max_notes=max_notes)
        if appended:
            self.add_memory_fact(
                user_id=user_id,
                fact_type="summary",
                value=note,
                source="manual",
                confidence=0.9,
            )

    def merge_from(self, other: "UserMemoryManager") -> None:
        for user_id, incoming in other.entries.items():
            incoming_profile = UserProfile(
                user_id=incoming.profile.user_id,
                name=incoming.profile.name,
                persona=incoming.profile.persona,
                identities=dict(incoming.profile.identities),
                aliases=list(incoming.profile.aliases),
                traits=list(incoming.profile.traits),
                metadata=dict(incoming.profile.metadata),
            )
            self.register_user(incoming_profile)
            current = self.entries[user_id]

            if incoming.runtime.inferred_persona and not current.runtime.inferred_persona:
                current.runtime.inferred_persona = incoming.runtime.inferred_persona

            for trait in incoming.runtime.inferred_traits:
                if trait not in current.runtime.inferred_traits:
                    current.runtime.inferred_traits.append(trait)

            for tag in incoming.runtime.preference_tags:
                if tag not in current.runtime.preference_tags:
                    current.runtime.preference_tags.append(tag)

            for msg in incoming.runtime.recent_messages:
                if msg not in current.runtime.recent_messages:
                    current.runtime.recent_messages.append(msg)
            if len(current.runtime.recent_messages) > 8:
                current.runtime.recent_messages = current.runtime.recent_messages[-8:]

            for note in incoming.runtime.summary_notes:
                self._append_summary_note(entry=current, note=note, max_notes=8)

            for fact in incoming.runtime.memory_facts:
                self.add_memory_fact(
                    user_id=user_id,
                    fact_type=fact.fact_type,
                    value=fact.value,
                    source=fact.source,
                    confidence=fact.confidence,
                    observed_at=fact.observed_at,
                )

            if incoming.runtime.last_seen_channel and not current.runtime.last_seen_channel:
                current.runtime.last_seen_channel = incoming.runtime.last_seen_channel
            if incoming.runtime.last_seen_uid and not current.runtime.last_seen_uid:
                current.runtime.last_seen_uid = incoming.runtime.last_seen_uid

    def apply_scheduled_decay(self) -> dict[str, int]:
        """对所有用户的记忆应用定期衰退。
        
        Returns: {user_id: 衰退的记忆数}
        """
        from sirius_chat.memory_quality import MemoryForgetEngine
        return MemoryForgetEngine.apply_scheduled_decay(self)
    
    def cleanup_expired_memories(self, min_quality: float = 0.25) -> dict[str, int]:
        """清理所有用户的过期/低质量记忆。
        
        Returns: {user_id: 删除的记忆数}
        """
        from sirius_chat.memory_quality import MemoryForgetEngine
        cleanup_stats = {}
        for user_id, entry in self.entries.items():
            deleted_count = MemoryForgetEngine.cleanup_user_memories(entry, min_quality=min_quality)
            if deleted_count > 0:
                cleanup_stats[user_id] = deleted_count
        return cleanup_stats

    def apply_event_insights(
        self,
        user_id: str,
        event_features: dict[str, object],
        source: str = "event_extract",
        base_confidence: float = 0.65,
    ) -> None:
        """将事件特征转化为用户记忆事实和特征信号。
        
        自动将事件的emotion_tags、keywords、role_slots、time_hints等
        转化为相应的用户记忆事实，并更新用户的观察到的特征集合。
        """
        entry = self.entries.get(user_id)
        if entry is None:
            return

        # 1. 情感识别 → 用户特征信号与记忆事实
        emotions = event_features.get("emotion_tags", [])
        if isinstance(emotions, list) and emotions:
            clean_emotions = [str(e).strip() for e in emotions if str(e).strip()]
            entry.runtime.observed_emotions.update(clean_emotions)
            
            emotion_str = ", ".join(clean_emotions[:3])
            self.add_memory_fact(
                user_id=user_id,
                fact_type="emotional_pattern",
                value=f"表现出的情感状态：{emotion_str}",
                source=source,
                confidence=base_confidence - 0.05,
            )

        # 2. 关键词积累 → 用户兴趣与记忆事实
        keywords = event_features.get("keywords", [])
        if isinstance(keywords, list) and keywords:
            clean_keywords = [str(k).strip() for k in keywords if str(k).strip()]
            entry.runtime.observed_keywords.update(clean_keywords)
            
            # 取前5个关键词，避免过长
            keywords_str = ", ".join(clean_keywords[:5])
            self.add_memory_fact(
                user_id=user_id,
                fact_type="user_interest",
                value=f"关注的话题：{keywords_str}",
                source=source,
                confidence=base_confidence - 0.1,
            )

        # 3. 角色识别 → 社交网络与特征提升
        roles = event_features.get("role_slots", [])
        if isinstance(roles, list) and roles:
            clean_roles = [str(r).strip() for r in roles if str(r).strip()]
            entry.runtime.observed_roles.update(clean_roles)
            
            roles_str = ", ".join(set(clean_roles))
            self.add_memory_fact(
                user_id=user_id,
                fact_type="social_context",
                value=f"与以下角色互动：{roles_str}",
                source=source,
                confidence=base_confidence - 0.05,
            )
            
            # 特征提升：检测领导相关角色
            # 注意：inferred_traits的添加不经过规范化，保持原始特征名
            leadership_roles = {"管理者", "leader", "manager", "经理", "主管", "团队",
                              "lead", "主导", "负责人", "项目经理", "项目主管"}
            if any(role in leadership_roles for role in clean_roles):
                if "leadership_tendency" not in entry.runtime.inferred_traits:
                    entry.runtime.inferred_traits.append("leadership_tendency")

        # 4. 实体识别 → 已知实体集合
        entities = event_features.get("entities", [])
        if isinstance(entities, list) and entities:
            clean_entities = [str(e).strip() for e in entities if str(e).strip()]
            entry.runtime.observed_entities.update(clean_entities)

    def interpret_event_with_user_context(
        self,
        user_id: str,
        event_id: str,
        event_summary: str,
        event_features: dict[str, object],
    ) -> ContextualEventInterpretation:
        """根据用户历史来调整事件理解，计算事件与用户背景的对齐度。
        
        返回ContextualEventInterpretation，包含多个对齐度评分和推荐的处理类别。
        """
        entry = self.entries.get(user_id)
        
        interpretation = ContextualEventInterpretation(
            event_id=event_id,
            event_summary=event_summary,
            base_confidence=0.65,
        )
        
        if entry is None:
            # 新用户，无历史对齐信息
            interpretation.recommended_category = "pending"
            interpretation.interpretation_notes.append("新用户，未有历史背景")
            return interpretation

        # 1. 关键词对齐度
        event_keywords = set(event_features.get("keywords", []) or [])
        if event_keywords and entry.runtime.observed_keywords:
            overlap = len(event_keywords & entry.runtime.observed_keywords)
            interpretation.keyword_alignment = overlap / max(len(event_keywords), 1)
            if interpretation.keyword_alignment > 0.5:
                interpretation.interpretation_notes.append(
                    f"关键词高度吻合（{interpretation.keyword_alignment:.1%}）"
                )

        # 2. 角色对齐度
        event_roles = set(event_features.get("role_slots", []) or [])
        if event_roles and entry.runtime.observed_roles:
            overlap = len(event_roles & entry.runtime.observed_roles)
            interpretation.role_alignment = overlap / max(len(event_roles), 1)
            if interpretation.role_alignment > 0.5:
                interpretation.interpretation_notes.append(
                    f"角色高度一致（{interpretation.role_alignment:.1%}）"
                )

        # 3. 情感对齐度
        event_emotions = set(event_features.get("emotion_tags", []) or [])
        if event_emotions and entry.runtime.observed_emotions:
            overlap = len(event_emotions & entry.runtime.observed_emotions)
            interpretation.emotion_alignment = overlap / max(len(event_emotions), 1)
            if interpretation.emotion_alignment > 0.5:
                interpretation.interpretation_notes.append(
                    f"情感模式重复（{interpretation.emotion_alignment:.1%}）"
                )

        # 4. 实体对齐度
        event_entities = set(event_features.get("entities", []) or [])
        if event_entities and entry.runtime.observed_entities:
            overlap = len(event_entities & entry.runtime.observed_entities)
            interpretation.entity_alignment = overlap / max(len(event_entities), 1)
            if interpretation.entity_alignment > 0.3:
                interpretation.interpretation_notes.append(
                    f"已知实体出现（{interpretation.entity_alignment:.1%}）"
                )

        # 5. 计算调整后的信度与推荐类别
        avg_alignment = (
            interpretation.keyword_alignment +
            interpretation.role_alignment +
            interpretation.emotion_alignment +
            interpretation.entity_alignment
        ) / 4.0
        
        # 根据平均对齐度调整信度
        interpretation.adjusted_confidence = (
            interpretation.base_confidence + (avg_alignment * 0.3)
        )
        interpretation.adjusted_confidence = min(1.0, max(0.5, interpretation.adjusted_confidence))

        # 推荐类别
        if avg_alignment > 0.6:
            interpretation.recommended_category = "high_confidence"
        elif avg_alignment < 0.2:
            interpretation.recommended_category = "low_relevance"
        else:
            interpretation.recommended_category = "normal"

        if not interpretation.interpretation_notes:
            interpretation.interpretation_notes.append("新事件，无明显与历史的关联")

        return interpretation

    # ============================================================================
    # C2方案: RESIDENT vs TRANSIENT 分离存储
    # ============================================================================

    def get_resident_facts(self, user_id: str) -> list[MemoryFact]:
        """获取高置信度的RESIDENT事实（仅用于持久化到user.json）。
        
        RESIDENT: confidence > 0.85，代表核心的、稳定的用户特征和偏好
        这些facts应该被持久化到持久化存储。
        """
        entry = self.entries.get(user_id)
        if entry is None:
            return []
        
        return [
            fact for fact in entry.runtime.memory_facts
            if fact.confidence > 0.85
        ]

    def get_transient_facts(self, user_id: str) -> list[MemoryFact]:
        """获取低置信度的TRANSIENT事实（存储在session内存中）。
        
        TRANSIENT: confidence ≤ 0.85，代表最近观察到的事但不确定的消息
        这些facts应该存储在session内存中，30分钟后自动清理。
        """
        entry = self.entries.get(user_id)
        if entry is None:
            return []
        
        return [
            fact for fact in entry.runtime.memory_facts
            if fact.confidence <= 0.85
        ]

    def cleanup_expired_transient_facts(
        self,
        user_id: str,
        max_age_minutes: int = 30,
    ) -> int:
        """清理过期的TRANSIENT事实。
        
        TRANSIENT事实在创建后max_age_minutes（默认30分钟）后会被删除。
        返回删除的facts数量。
        """
        entry = self.entries.get(user_id)
        if entry is None:
            return 0
        
        now = datetime.now(timezone.utc)
        deleted_count = 0
        facts_to_keep = []
        
        for fact in entry.runtime.memory_facts:
            # 只检查transient facts
            if not fact.is_transient:
                facts_to_keep.append(fact)
                continue
            
            # 检查是否过期
            if fact.created_at:
                try:
                    created_time = datetime.fromisoformat(fact.created_at)
                    age_minutes = (now - created_time).total_seconds() / 60
                    if age_minutes > max_age_minutes:
                        deleted_count += 1
                        continue  # 删除这个fact
                except (ValueError, TypeError):
                    # 时间解析失败，保留这个fact
                    pass
            
            facts_to_keep.append(fact)
        
        entry.runtime.memory_facts = facts_to_keep
        
        if deleted_count > 0:
            logger.debug(
                f"Cleaned up {deleted_count} expired transient facts for user {user_id}"
            )
        
        return deleted_count

    def compress_memory_facts(
        self,
        user_id: str,
        similarity_threshold: float = 0.8,
    ) -> int:
        """C3方案: 动态压缩memory facts。
        
        对同类型的facts进行聚类和合并，减少redundant信息。
        
        Args:
            user_id: 要压缩的用户ID
            similarity_threshold: 相似度阈值（0.0-1.0）
        
        Returns:
            被压缩/删除的facts数量
        """
        entry = self.entries.get(user_id)
        if entry is None:
            return 0
        
        facts = entry.runtime.memory_facts
        if len(facts) < 10:  # 事实太少，跳过压缩
            return 0
        
        # 按fact_type分组
        facts_by_type: dict[str, list[MemoryFact]] = {}
        for fact in facts:
            if fact.fact_type not in facts_by_type:
                facts_by_type[fact.fact_type] = []
            facts_by_type[fact.fact_type].append(fact)
        
        original_count = len(facts)
        compressed_facts = []
        
        # 对每个类型的facts进行压缩
        for fact_type, facts_of_type in facts_by_type.items():
            if len(facts_of_type) <= 3:
                # 事实太少，保留所有
                compressed_facts.extend(facts_of_type)
                continue
            
            # 方案：删除最低confidence的facts，保留top 70%
            sorted_facts = sorted(facts_of_type, key=lambda f: f.confidence, reverse=True)
            keep_count = max(2, int(len(sorted_facts) * 0.7))
            compressed_facts.extend(sorted_facts[:keep_count])
        
        # 按原有顺序重新排序（maintain observed_at顺序）
        compressed_facts.sort(
            key=lambda f: f.observed_at,
            reverse=True
        )
        
        entry.runtime.memory_facts = compressed_facts
        deleted_count = original_count - len(compressed_facts)
        
        if deleted_count > 0:
            logger.info(
                f"Compressed facts for user {user_id}: "
                f"{original_count} → {len(compressed_facts)} ({deleted_count} deleted)"
            )
        
        return deleted_count

    def to_dict(self) -> dict[str, Any]:
        return {
            "entries": {
                user_id: {
                    "profile": {
                        "user_id": entry.profile.user_id,
                        "name": entry.profile.name,
                        "persona": entry.profile.persona,
                        "identities": entry.profile.identities,
                        "aliases": entry.profile.aliases,
                        "traits": entry.profile.traits,
                        "metadata": entry.profile.metadata,
                    },
                    "runtime": {
                        "inferred_persona": entry.runtime.inferred_persona,
                        "inferred_traits": entry.runtime.inferred_traits,
                        "preference_tags": entry.runtime.preference_tags,
                        "recent_messages": entry.runtime.recent_messages,
                        "summary_notes": entry.runtime.summary_notes,
                        "memory_facts": [
                            {
                                "fact_type": item.fact_type,
                                "value": item.value,
                                "source": item.source,
                                "confidence": item.confidence,
                                "observed_at": item.observed_at,
                                "memory_category": item.memory_category,
                                "validated": item.validated,
                                "conflict_with": item.conflict_with,
                                # C2: RESIDENT/TRANSIENT标记
                                "is_transient": item.is_transient,
                                "created_at": item.created_at,
                            }
                            for item in entry.runtime.memory_facts
                        ],
                        "last_seen_channel": entry.runtime.last_seen_channel,
                        "last_seen_uid": entry.runtime.last_seen_uid,
                        "observed_keywords": list(entry.runtime.observed_keywords),
                        "observed_roles": list(entry.runtime.observed_roles),
                        "observed_emotions": list(entry.runtime.observed_emotions),
                        "observed_entities": list(entry.runtime.observed_entities),
                        # A1: 序列化时间戳
                        "last_event_processed_at": (
                            entry.runtime.last_event_processed_at.isoformat()
                            if entry.runtime.last_event_processed_at is not None
                            else None
                        ),
                    },
                }
                for user_id, entry in self.entries.items()
            },
            "speaker_index": self.speaker_index,
            "identity_index": self.identity_index,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "UserMemoryManager":
        manager = cls()
        raw_entries = payload.get("entries", {})
        for user_id, item in raw_entries.items():
            profile_data = item.get("profile", {})
            profile = UserProfile(
                user_id=profile_data.get("user_id", user_id),
                name=profile_data.get("name", user_id),
                persona=profile_data.get("persona", ""),
                identities=dict(profile_data.get("identities", {})),
                aliases=list(profile_data.get("aliases", [])),
                traits=list(profile_data.get("traits", [])),
                metadata=dict(profile_data.get("metadata", {})),
            )
            runtime_data = item.get("runtime", {})
            if not runtime_data:
                # Backward compatibility for old entry fields.
                runtime_data = {
                    "recent_messages": list(item.get("recent_messages", [])),
                    "summary_notes": list(item.get("summary_notes", [])),
                }
            manager.entries[user_id] = UserMemoryEntry(
                profile=profile,
                runtime=UserRuntimeState(
                    inferred_persona=str(runtime_data.get("inferred_persona", "")),
                    inferred_traits=list(runtime_data.get("inferred_traits", [])),
                    preference_tags=list(runtime_data.get("preference_tags", [])),
                    recent_messages=list(runtime_data.get("recent_messages", [])),
                    summary_notes=list(runtime_data.get("summary_notes", [])),
                    memory_facts=[
                        MemoryFact(
                            fact_type=str(item.get("fact_type", "")).strip() or "summary",
                            value=str(item.get("value", "")).strip(),
                            source=str(item.get("source", "unknown")).strip() or "unknown",
                            confidence=float(item.get("confidence", 0.5)),
                            observed_at=str(item.get("observed_at", "")).strip(),
                            memory_category=str(item.get("memory_category", "custom")).strip() or "custom",
                            validated=bool(item.get("validated", False)),
                            conflict_with=list(item.get("conflict_with", [])),
                            # C2: 反序列化RESIDENT/TRANSIENT标记
                            is_transient=bool(item.get("is_transient", False)),
                            created_at=str(item.get("created_at", "")).strip(),
                        )
                        for item in list(runtime_data.get("memory_facts", []))
                        if isinstance(item, dict) and str(item.get("value", "")).strip()
                    ],
                    last_seen_channel=str(runtime_data.get("last_seen_channel", "")),
                    last_seen_uid=str(runtime_data.get("last_seen_uid", "")),
                    observed_keywords=set(runtime_data.get("observed_keywords", [])),
                    observed_roles=set(runtime_data.get("observed_roles", [])),
                    observed_emotions=set(runtime_data.get("observed_emotions", [])),
                    observed_entities=set(runtime_data.get("observed_entities", [])),
                    # A1: 反序列化时间戳
                    last_event_processed_at=(
                        datetime.fromisoformat(runtime_data["last_event_processed_at"])
                        if runtime_data.get("last_event_processed_at")
                        else None
                    ),
                ),
            )
            runtime = manager.entries[user_id].runtime
            if not runtime.memory_facts:
                for note in runtime.summary_notes:
                    value = str(note).strip()
                    if not value:
                        continue
                    runtime.memory_facts.append(
                        MemoryFact(
                            fact_type="summary",
                            value=value,
                            source="legacy",
                            confidence=0.4,
                            observed_at="",
                        )
                    )

        manager.speaker_index = dict(payload.get("speaker_index", {}))
        manager.identity_index = dict(payload.get("identity_index", {}))
        if not manager.speaker_index:
            for user_id, entry in manager.entries.items():
                labels = [user_id, entry.profile.name, *entry.profile.aliases]
                for label in labels:
                    if label:
                        manager.speaker_index[manager._normalize_label(label)] = user_id
        if not manager.identity_index:
            for user_id, entry in manager.entries.items():
                for channel, external_id in entry.profile.identities.items():
                    if channel and external_id:
                        manager.identity_index[manager._identity_key(channel, external_id)] = user_id
        return manager


class UserMemoryFileStore:
    def __init__(self, work_path: Path) -> None:
        self._dir = Path(work_path) / "users"

    @property
    def directory(self) -> Path:
        return self._dir

    @staticmethod
    def _safe_filename(user_id: str) -> str:
        base = re.sub(r"[^a-zA-Z0-9_\-\u4e00-\u9fff]+", "_", user_id.strip())
        base = re.sub(r"_+", "_", base).strip("_")
        return base or "user"

    @staticmethod
    def _entry_to_payload(entry: UserMemoryEntry) -> dict[str, Any]:
        return {
            "profile": {
                "user_id": entry.profile.user_id,
                "name": entry.profile.name,
                "persona": entry.profile.persona,
                "identities": entry.profile.identities,
                "aliases": entry.profile.aliases,
                "traits": entry.profile.traits,
                "metadata": entry.profile.metadata,
            },
            "runtime": {
                "inferred_persona": entry.runtime.inferred_persona,
                "inferred_traits": entry.runtime.inferred_traits,
                "preference_tags": entry.runtime.preference_tags,
                "recent_messages": entry.runtime.recent_messages,
                "summary_notes": entry.runtime.summary_notes,
                "memory_facts": [
                    {
                        "fact_type": item.fact_type,
                        "value": item.value,
                        "source": item.source,
                        "confidence": item.confidence,
                        "observed_at": item.observed_at,
                    }
                    for item in entry.runtime.memory_facts
                ],
                "last_seen_channel": entry.runtime.last_seen_channel,
                "last_seen_uid": entry.runtime.last_seen_uid,
            },
        }

    def save_all(self, manager: UserMemoryManager) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        for user_id, entry in manager.entries.items():
            file_name = f"{self._safe_filename(user_id)}.json"
            target = self._dir / file_name
            tmp = target.with_suffix(target.suffix + ".tmp")
            tmp.write_text(json.dumps(self._entry_to_payload(entry), ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(target)

    def load_all(self) -> UserMemoryManager:
        manager = UserMemoryManager()
        if not self._dir.exists():
            return manager

        for file_path in self._dir.glob("*.json"):
            try:
                payload = json.loads(file_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue

            if not isinstance(payload, dict):
                continue

            profile_data = payload.get("profile", {})
            if not isinstance(profile_data, dict):
                continue
            user_id = str(profile_data.get("user_id", "")).strip()
            if not user_id:
                continue

            profile = UserProfile(
                user_id=user_id,
                name=str(profile_data.get("name", user_id)).strip() or user_id,
                persona=str(profile_data.get("persona", "")).strip(),
                identities=dict(profile_data.get("identities", {})),
                aliases=list(profile_data.get("aliases", [])),
                traits=list(profile_data.get("traits", [])),
                metadata=dict(profile_data.get("metadata", {})),
            )
            manager.register_user(profile)

            runtime_data = payload.get("runtime", {})
            if not isinstance(runtime_data, dict):
                continue
            entry = manager.entries[user_id]
            entry.runtime.inferred_persona = str(runtime_data.get("inferred_persona", "")).strip()
            entry.runtime.inferred_traits = list(runtime_data.get("inferred_traits", []))
            entry.runtime.preference_tags = list(runtime_data.get("preference_tags", []))
            entry.runtime.recent_messages = list(runtime_data.get("recent_messages", []))
            entry.runtime.summary_notes = list(runtime_data.get("summary_notes", []))
            entry.runtime.memory_facts = [
                MemoryFact(
                    fact_type=str(item.get("fact_type", "")).strip() or "summary",
                    value=str(item.get("value", "")).strip(),
                    source=str(item.get("source", "unknown")).strip() or "unknown",
                    confidence=float(item.get("confidence", 0.5)),
                    observed_at=str(item.get("observed_at", "")).strip(),
                )
                for item in list(runtime_data.get("memory_facts", []))
                if isinstance(item, dict) and str(item.get("value", "")).strip()
            ]
            if not entry.runtime.memory_facts:
                for note in entry.runtime.summary_notes:
                    value = str(note).strip()
                    if not value:
                        continue
                    entry.runtime.memory_facts.append(
                        MemoryFact(
                            fact_type="summary",
                            value=value,
                            source="legacy",
                            confidence=0.4,
                            observed_at="",
                        )
                    )
            entry.runtime.last_seen_channel = str(runtime_data.get("last_seen_channel", "")).strip()
            entry.runtime.last_seen_uid = str(runtime_data.get("last_seen_uid", "")).strip()

        return manager