"""记忆质量评估与智能遗忘模块。

提供以下功能：
1. 记忆质量评估：计算置信度、活跃度、一致性等指标
2. 行为一致性分析：对比记忆与实际用户行为
3. 智能遗忘：自动衰退陈旧/冲突的低置信度记忆
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
from pathlib import Path
from typing import Any

from sirius_chat.models import Transcript
from sirius_chat.user_memory import MemoryFact, UserMemoryEntry, UserMemoryManager, UserMemoryFileStore


@dataclass(slots=True)
class MemoryQualityMetrics:
    """单条记忆的质量指标。"""
    
    memory_id: str  # fact_type + value的唯一标识
    fact_type: str
    value: str
    source: str
    memory_category: str
    
    # 基础指标
    confidence: float  # 置信度 (0-1)
    observed_at: str  # 观察时间
    
    # 质量指标
    age_days: float  # 距离现在的天数
    recency_score: float  # 活跃度 (0-1, 最近的记忆更高)
    has_conflict: bool  # 是否存在冲突
    is_validated: bool  # 是否通过memory_manager验证
    
    # 综合指标
    quality_score: float  # 综合质量分数 (0-1)
    should_forget: bool  # 是否应该遗忘
    decay_factor: float  # 衰退系数 (0-1，越低衰退越快)


@dataclass(slots=True)
class UserBehaviorConsistency:
    """用户行为一致性分析。"""
    
    user_id: str
    analysis_date: str  # ISO格式时间
    
    total_facts: int  # 总记忆数
    validated_facts: int  # 已验证的记忆数
    conflicting_facts: int  # 有冲突的记忆数
    outdated_facts: int  # 陈旧记忆数 (>30天)
    
    # 各类别一致性
    identity_consistency: float  # 身份一致性 (0-1)
    preference_consistency: float  # 偏好一致性 (0-1)
    emotion_consistency: float  # 情绪一致性 (0-1)
    event_consistency: float  # 事件一致性 (0-1)
    
    # 整体指标
    overall_consistency: float  # 整体一致性 (0-1)
    recommendation: str  # 建议 (maintain|review|cleanup)


class MemoryQualityAssessor:
    """记忆质量评估器。"""
    
    # 配置参数
    RECENCY_WINDOW_DAYS = 7  # 活跃期（7天内的记忆视为活跃）
    STALE_THRESHOLD_DAYS = 30  # 陈旧阈值（>30天的记忆为陈旧）
    OLD_THRESHOLD_DAYS = 90  # 过期阈值（>90天的记忆可考虑遗忘）
    
    MIN_QUALITY_THRESHOLD = 0.3  # 最低质量阈值
    
    @staticmethod
    def _now_iso() -> str:
        return datetime.now().isoformat(timespec="seconds")
    
    @staticmethod
    def _parse_iso_datetime(iso_str: str) -> datetime:
        """解析ISO格式时间戳。"""
        try:
            return datetime.fromisoformat(iso_str)
        except (ValueError, TypeError):
            return datetime.now()
    
    @staticmethod
    def _calculate_age_days(observed_at: str) -> float:
        """计算记忆的年龄（天数）。"""
        if not observed_at:
            return 999  # 未知时间视为极旧
        try:
            observed = MemoryQualityAssessor._parse_iso_datetime(observed_at)
            age = datetime.now() - observed
            return age.total_seconds() / 86400
        except Exception:
            return 999
    
    @staticmethod
    def _calculate_recency_score(age_days: float) -> float:
        """计算活跃度分数。
        - 0-7天：高活跃 (0.9-1.0)
        - 7-30天：中活跃 (0.6-0.9)
        - 30-90天：低活跃 (0.2-0.6)
        - >90天：极低活跃 (0.0-0.2)
        """
        if age_days <= MemoryQualityAssessor.RECENCY_WINDOW_DAYS:
            return 1.0 - (age_days / MemoryQualityAssessor.RECENCY_WINDOW_DAYS) * 0.1
        elif age_days <= MemoryQualityAssessor.STALE_THRESHOLD_DAYS:
            return 0.9 - ((age_days - MemoryQualityAssessor.RECENCY_WINDOW_DAYS) / 
                          (MemoryQualityAssessor.STALE_THRESHOLD_DAYS - MemoryQualityAssessor.RECENCY_WINDOW_DAYS)) * 0.3
        elif age_days <= MemoryQualityAssessor.OLD_THRESHOLD_DAYS:
            return 0.6 - ((age_days - MemoryQualityAssessor.STALE_THRESHOLD_DAYS) / 
                         (MemoryQualityAssessor.OLD_THRESHOLD_DAYS - MemoryQualityAssessor.STALE_THRESHOLD_DAYS)) * 0.4
        else:
            return max(0.0, 0.2 - (age_days - MemoryQualityAssessor.OLD_THRESHOLD_DAYS) / 180)
    
    @staticmethod
    def assess_fact(fact: MemoryFact) -> MemoryQualityMetrics:
        """评估单条记忆事实。"""
        age_days = MemoryQualityAssessor._calculate_age_days(fact.observed_at)
        recency_score = MemoryQualityAssessor._calculate_recency_score(age_days)
        
        # 综合质量分数：confidence * recency * validation * (1 - conflict_penalty)
        validation_bonus = 0.15 if fact.validated else 0
        conflict_penalty = 0.3 if fact.conflict_with else 0
        
        quality_score = (
            (fact.confidence * 0.5 +  # 置信度权重 50%
             recency_score * 0.3 +    # 活跃度权重 30%
             validation_bonus) *      # 验证奖励 15%
            (1 - conflict_penalty)    # 冲突惩罚
        )
        quality_score = max(0.0, min(1.0, quality_score))
        
        # 判断是否应该遗忘
        should_forget = (
            quality_score < MemoryQualityAssessor.MIN_QUALITY_THRESHOLD or
            (age_days > MemoryQualityAssessor.OLD_THRESHOLD_DAYS and 
             fact.confidence < 0.5 and fact.conflict_with)
        )
        
        # 衰退系数：用于后续的自动衰减
        decay_factor = recency_score * fact.confidence
        if fact.conflict_with:
            decay_factor *= 0.5
        
        memory_id = f"{fact.fact_type}:{fact.value[:20]}"
        
        return MemoryQualityMetrics(
            memory_id=memory_id,
            fact_type=fact.fact_type,
            value=fact.value,
            source=fact.source,
            memory_category=fact.memory_category,
            confidence=fact.confidence,
            observed_at=fact.observed_at,
            age_days=age_days,
            recency_score=recency_score,
            has_conflict=bool(fact.conflict_with),
            is_validated=fact.validated,
            quality_score=quality_score,
            should_forget=should_forget,
            decay_factor=decay_factor,
        )
    
    @staticmethod
    def assess_user(entry: UserMemoryEntry) -> UserBehaviorConsistency:
        """评估用户的记忆一致性。"""
        facts = entry.runtime.memory_facts
        if not facts:
            return UserBehaviorConsistency(
                user_id=entry.profile.user_id,
                analysis_date=MemoryQualityAssessor._now_iso(),
                total_facts=0,
                validated_facts=0,
                conflicting_facts=0,
                outdated_facts=0,
                identity_consistency=0,
                preference_consistency=0,
                emotion_consistency=0,
                event_consistency=0,
                overall_consistency=0,
                recommendation="maintain",
            )
        
        # 按类别统计
        category_metrics: dict[str, list[float]] = {
            "identity": [],
            "preference": [],
            "emotion": [],
            "event": [],
            "custom": [],
        }
        
        validated_count = 0
        conflict_count = 0
        outdated_count = 0
        
        for fact in facts:
            cat = fact.memory_category or "custom"
            metrics = MemoryQualityAssessor.assess_fact(fact)
            category_metrics[cat].append(metrics.quality_score)
            
            if fact.validated:
                validated_count += 1
            if fact.conflict_with:
                conflict_count += 1
            if metrics.age_days > MemoryQualityAssessor.STALE_THRESHOLD_DAYS:
                outdated_count += 1
        
        # 计算每个类别的一致性
        def avg_score(cat: str) -> float:
            scores = category_metrics.get(cat, [])
            return sum(scores) / len(scores) if scores else 0.0
        
        identity_consistency = avg_score("identity")
        preference_consistency = avg_score("preference")
        emotion_consistency = avg_score("emotion")
        event_consistency = avg_score("event")
        
        # 整体一致性（加权平均）
        category_scores = [
            identity_consistency * 0.25,
            preference_consistency * 0.25,
            emotion_consistency * 0.25,
            event_consistency * 0.25,
        ]
        overall_consistency = sum(category_scores)
        
        # 生成建议
        if overall_consistency > 0.75:
            recommendation = "maintain"
        elif overall_consistency > 0.5:
            recommendation = "review"
        else:
            recommendation = "cleanup"
        
        return UserBehaviorConsistency(
            user_id=entry.profile.user_id,
            analysis_date=MemoryQualityAssessor._now_iso(),
            total_facts=len(facts),
            validated_facts=validated_count,
            conflicting_facts=conflict_count,
            outdated_facts=outdated_count,
            identity_consistency=identity_consistency,
            preference_consistency=preference_consistency,
            emotion_consistency=emotion_consistency,
            event_consistency=event_consistency,
            overall_consistency=overall_consistency,
            recommendation=recommendation,
        )


class MemoryForgetEngine:
    """智能遗忘引擎。"""
    
    # 衰退策略配置
    DEFAULT_DECAY_SCHEDULE = {
        # 天数 -> 置信度衰减比例
        7: 0.95,    # 7天后保留95%置信度
        30: 0.85,   # 30天后保留85%
        60: 0.70,   # 60天后保留70%
        90: 0.50,   # 90天后保留50%
        180: 0.20,  # 180天后保留20%
    }
    
    @staticmethod
    def should_forget_fact(fact: MemoryFact) -> bool:
        """判断是否应该遗忘该记忆。
        
        遗忘条件 (满足任意一个)：
        1. 极低置信度(<0.2) + 陈旧（>30天）
        2. 有冲突 + 低置信度(<0.4) + 极旧（>90天）
        3. 质量评分 < 0.2 + 极旧（>60天）
        """
        age_days = MemoryQualityAssessor._calculate_age_days(fact.observed_at)
        
        # 条件1：极低置信度 + 陈旧
        if fact.confidence < 0.2 and age_days > 30:
            return True
        
        # 条件2：有冲突 + 低置信度 + 极旧
        if fact.conflict_with and fact.confidence < 0.4 and age_days > 90:
            return True
        
        # 条件3：质量极低 + 陈旧
        metrics = MemoryQualityAssessor.assess_fact(fact)
        if metrics.quality_score < 0.2 and age_days > 60:
            return True
        
        return False
    
    @staticmethod
    def apply_decay(fact: MemoryFact, days_since_update: float | None = None) -> MemoryFact:
        """对记忆应用时间衰退。
        
        基于记忆的年龄和原始置信度，计算衰退后的置信度。
        """
        if days_since_update is None:
            days_since_update = MemoryQualityAssessor._calculate_age_days(fact.observed_at)
        
        # 根据时间表计算衰减比例
        decay_ratio = 1.0
        sorted_days = sorted(MemoryForgetEngine.DEFAULT_DECAY_SCHEDULE.keys())
        
        for day_threshold in sorted_days:
            if days_since_update >= day_threshold:
                decay_ratio = MemoryForgetEngine.DEFAULT_DECAY_SCHEDULE[day_threshold]
            else:
                break
        
        # 特殊处理：有冲突的记忆加快衰退
        if fact.conflict_with:
            decay_ratio *= 0.7
        
        # 计算新的置信度
        new_confidence = fact.confidence * decay_ratio
        new_confidence = max(0.0, min(1.0, new_confidence))
        
        # 返回衰退后的记忆副本
        return MemoryFact(
            fact_type=fact.fact_type,
            value=fact.value,
            source=fact.source,
            confidence=new_confidence,
            observed_at=fact.observed_at,
            memory_category=fact.memory_category,
            validated=fact.validated,
            conflict_with=list(fact.conflict_with),  # 保持冲突列表
        )
    
    @staticmethod
    def cleanup_user_memories(entry: UserMemoryEntry, min_quality: float = 0.25) -> int:
        """清理用户的低质量记忆。
        
        返回：删除的记忆数。
        """
        original_count = len(entry.runtime.memory_facts)
        
        # 过滤：保留质量评分 >= min_quality 的记忆，或较新的未验证记忆
        filtered_facts = []
        for fact in entry.runtime.memory_facts:
            if MemoryForgetEngine.should_forget_fact(fact):
                continue  # 遗忘该记忆
            
            metrics = MemoryQualityAssessor.assess_fact(fact)
            if metrics.quality_score >= min_quality:
                filtered_facts.append(fact)
        
        entry.runtime.memory_facts = filtered_facts
        return original_count - len(filtered_facts)
    
    @staticmethod
    def apply_scheduled_decay(manager: UserMemoryManager) -> dict[str, int]:
        """对所有用户的记忆应用定期衰退。
        
        返回：{user_id: 衰退的记忆数}
        """
        decay_stats = {}
        
        for user_id, entry in manager.entries.items():
            decayed_count = 0
            
            for i, fact in enumerate(entry.runtime.memory_facts):
                old_confidence = fact.confidence
                decayed_fact = MemoryForgetEngine.apply_decay(fact)
                
                # 如果置信度下降超过10%，记录为衰退
                if decayed_fact.confidence < old_confidence - 0.1:
                    entry.runtime.memory_facts[i] = decayed_fact
                    decayed_count += 1
            
            if decayed_count > 0:
                decay_stats[user_id] = decayed_count
        
        return decay_stats


class MemoryQualityReport:
    """生成记忆质量报告。"""
    
    @staticmethod
    def generate_user_report(entry: UserMemoryEntry) -> dict[str, Any]:
        """生成用户的详细质量报告。"""
        consistency = MemoryQualityAssessor.assess_user(entry)
        
        # 每条记忆的质量指标
        fact_metrics = []
        for fact in entry.runtime.memory_facts:
            metrics = MemoryQualityAssessor.assess_fact(fact)
            fact_metrics.append({
                "memory_id": metrics.memory_id,
                "value": metrics.value,
                "category": metrics.memory_category,
                "confidence": round(metrics.confidence, 3),
                "quality_score": round(metrics.quality_score, 3),
                "age_days": round(metrics.age_days, 1),
                "recency_score": round(metrics.recency_score, 3),
                "has_conflict": metrics.has_conflict,
                "is_validated": metrics.is_validated,
                "should_forget": metrics.should_forget,
            })
        
        # 按质量评分排序
        fact_metrics.sort(key=lambda x: x["quality_score"], reverse=True)
        
        return {
            "user_id": entry.profile.user_id,
            "user_name": entry.profile.name,
            "report_date": consistency.analysis_date,
            "summary": {
                "total_facts": consistency.total_facts,
                "validated_facts": consistency.validated_facts,
                "conflicting_facts": consistency.conflicting_facts,
                "outdated_facts": consistency.outdated_facts,
            },
            "consistency": {
                "identity": round(consistency.identity_consistency, 3),
                "preference": round(consistency.preference_consistency, 3),
                "emotion": round(consistency.emotion_consistency, 3),
                "event": round(consistency.event_consistency, 3),
                "overall": round(consistency.overall_consistency, 3),
            },
            "recommendation": consistency.recommendation,
            "facts": fact_metrics,
        }
    
    @staticmethod
    def generate_system_report(manager: UserMemoryManager) -> dict[str, Any]:
        """生成整个系统的记忆质量报告。"""
        user_reports = []
        total_facts = 0
        total_quality = 0.0
        categories_distribution: dict[str, int] = {}
        source_distribution: dict[str, int] = {}
        
        for entry in manager.entries.values():
            report = MemoryQualityReport.generate_user_report(entry)
            user_reports.append(report)
            
            for fact in entry.runtime.memory_facts:
                total_facts += 1
                total_quality += fact.confidence
                
                cat = fact.memory_category or "custom"
                categories_distribution[cat] = categories_distribution.get(cat, 0) + 1
                
                source_distribution[fact.source] = source_distribution.get(fact.source, 0) + 1
        
        avg_quality = total_quality / total_facts if total_facts > 0 else 0
        
        return {
            "report_date": MemoryQualityAssessor._now_iso(),
            "summary": {
                "total_users": len(manager.entries),
                "total_facts": total_facts,
                "average_quality": round(avg_quality, 3),
            },
            "distribution": {
                "by_category": categories_distribution,
                "by_source": source_distribution,
            },
            "user_reports": user_reports,
        }
    
    @staticmethod
    def save_report(report: dict[str, Any], output_path: Path) -> None:
        """保存报告到文件。"""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
