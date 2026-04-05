"""记忆质量评估与智能遗忘的测试。"""

from __future__ import annotations

from datetime import datetime, timedelta
import pytest

from sirius_chat.memory.quality.models import (
    MemoryForgetEngine,
    MemoryQualityAssessor,
    MemoryQualityMetrics,
    MemoryQualityReport,
    UserBehaviorConsistency,
)
from sirius_chat.memory import MemoryFact, UserMemoryEntry, UserProfile, UserRuntimeState


def test_memory_quality_assessor_calculates_age() -> None:
    """测试年龄计算。"""
    now_iso = MemoryQualityAssessor._now_iso()
    age = MemoryQualityAssessor._calculate_age_days(now_iso)
    assert 0 <= age < 1  # 当前时间应该是极近的
    
    old_time = (datetime.now() - timedelta(days=30)).isoformat(timespec="seconds")
    age = MemoryQualityAssessor._calculate_age_days(old_time)
    assert 29 < age < 31  # 30天前


def test_memory_quality_assessor_recency_score() -> None:
    """测试活跃度评分。"""
    # 今天的记忆：高活跃度
    today_iso = MemoryQualityAssessor._now_iso()
    score = MemoryQualityAssessor._calculate_recency_score(0)
    assert score > 0.9
    
    # 7天后：活跃度下降
    score_7d = MemoryQualityAssessor._calculate_recency_score(7)
    assert 0.8 < score_7d < 0.95
    
    # 30天后：活跃度进一步下降
    score_30d = MemoryQualityAssessor._calculate_recency_score(30)
    assert 0.5 < score_30d < 0.8
    
    # 90天后：活跃度明显下降
    score_90d = MemoryQualityAssessor._calculate_recency_score(90)
    assert 0.1 < score_90d < 0.5
    
    # 180天后：接近遗忘
    score_180d = MemoryQualityAssessor._calculate_recency_score(180)
    assert 0 <= score_180d < 0.2


def test_memory_quality_assessor_assess_fact() -> None:
    """测试单条记忆的质量评估。"""
    recent_fact = MemoryFact(
        fact_type="preference",
        value="喜欢成本优化",
        source="memory_extract",
        confidence=0.85,
        observed_at=MemoryQualityAssessor._now_iso(),
        memory_category="preference",
        validated=True,
        conflict_with=[],
    )
    
    metrics = MemoryQualityAssessor.assess_fact(recent_fact)
    assert metrics.quality_score > 0.75  # 新的、高置信、已验证的记忆应该有高分
    assert not metrics.should_forget
    assert metrics.is_validated
    assert not metrics.has_conflict
    
    # 旧的、低置信度、有冲突的记忆
    old_fact = MemoryFact(
        fact_type="preference",
        value="喜欢快速迭代",
        source="heuristic",
        confidence=0.3,
        observed_at=(datetime.now() - timedelta(days=120)).isoformat(timespec="seconds"),
        memory_category="preference",
        validated=False,
        conflict_with=["pref_001"],
    )
    
    metrics = MemoryQualityAssessor.assess_fact(old_fact)
    assert metrics.quality_score < 0.4  # 应该有低分
    assert metrics.has_conflict


def test_memory_forgetengine_should_forget() -> None:
    """测试遗忘条件判断。"""
    # 陈旧且极低置信度的记忆应该被遗忘
    old_low_conf_fact = MemoryFact(
        fact_type="summary",
        value="很久以前的记忆",
        source="legacy",
        confidence=0.15,
        observed_at=(datetime.now() - timedelta(days=45)).isoformat(timespec="seconds"),
        memory_category="custom",
        validated=False,
        conflict_with=[],
    )
    assert MemoryForgetEngine.should_forget_fact(old_low_conf_fact)
    
    # 最近的记忆即使置信度低也不应该被遗忘
    recent_low_conf = MemoryFact(
        fact_type="summary",
        value="最近的低置信记忆",
        source="heuristic",
        confidence=0.3,
        observed_at=MemoryQualityAssessor._now_iso(),
        memory_category="custom",
        validated=False,
        conflict_with=[],
    )
    assert not MemoryForgetEngine.should_forget_fact(recent_low_conf)
    
    # 极旧、有冲突、低置信的记忆应该被遗忘
    conflicted_old = MemoryFact(
        fact_type="preference",
        value="陈旧的有冲突记忆",
        source="memory_extract",
        confidence=0.35,
        observed_at=(datetime.now() - timedelta(days=100)).isoformat(timespec="seconds"),
        memory_category="preference",
        validated=False,
        conflict_with=["pref_other"],
    )
    assert MemoryForgetEngine.should_forget_fact(conflicted_old)


def test_memory_forgetengine_apply_decay() -> None:
    """测试时间衰退应用。"""
    fact = MemoryFact(
        fact_type="preference",
        value="成本优化",
        source="memory_extract",
        confidence=0.85,
        observed_at=(datetime.now() - timedelta(days=7)).isoformat(timespec="seconds"),
        memory_category="preference",
        validated=False,
        conflict_with=[],
    )
    
    # 7天后衰退应该保留95%置信度
    decayed = MemoryForgetEngine.apply_decay(fact, days_since_update=7)
    assert 0.79 < decayed.confidence < 0.81  # 0.85 * 0.95 ≈ 0.8075
    
    # 有冲突的记忆衰退更快
    conflicted_fact = MemoryFact(
        fact_type="preference",
        value="有冲突的偏好",
        source="memory_extract",
        confidence=0.85,
        observed_at=(datetime.now() - timedelta(days=60)).isoformat(timespec="seconds"),
        memory_category="preference",
        validated=False,
        conflict_with=["other_pref"],
    )
    decayed_with_conflict = MemoryForgetEngine.apply_decay(conflicted_fact, days_since_update=60)
    # 60天 -> 0.7 * 0.7(conflict penalty) = 0.49, 0.85 * 0.49 ≈ 0.42
    assert decayed_with_conflict.confidence < decayed.confidence


def test_memory_quality_assessor_assess_user() -> None:
    """测试用户记忆一致性评估。"""
    entry = UserMemoryEntry(
        profile=UserProfile(user_id="user_001", name="张三"),
        runtime=UserRuntimeState(
            memory_facts=[
                MemoryFact(
                    fact_type="summary",
                    value="软件工程师",
                    source="memory_extract",
                    confidence=0.9,
                    observed_at=MemoryQualityAssessor._now_iso(),
                    memory_category="identity",
                    validated=True,
                    conflict_with=[],
                ),
                MemoryFact(
                    fact_type="summary",
                    value="成本敏感",
                    source="memory_extract",
                    confidence=0.8,
                    observed_at=MemoryQualityAssessor._now_iso(),
                    memory_category="preference",
                    validated=True,
                    conflict_with=[],
                ),
                MemoryFact(
                    fact_type="summary",
                    value="最近焦虑",
                    source="memory_extract",
                    confidence=0.7,
                    observed_at=(datetime.now() - timedelta(days=5)).isoformat(timespec="seconds"),
                    memory_category="emotion",
                    validated=False,
                    conflict_with=[],
                ),
            ]
        ),
    )
    
    consistency = MemoryQualityAssessor.assess_user(entry)
    assert consistency.user_id == "user_001"
    assert consistency.total_facts == 3
    assert consistency.validated_facts == 2
    assert consistency.conflicting_facts == 0
    # overall_consistency = (0.9*0.25 + 0.85*0.25 + 0.629*0.25 + 0*0.25) ≈ 0.595
    assert consistency.overall_consistency > 0.5
    assert consistency.recommendation == "review"


def test_memory_quality_report_generation() -> None:
    """测试报告生成。"""
    entry = UserMemoryEntry(
        profile=UserProfile(user_id="user_001", name="李四", persona="产品经理"),
        runtime=UserRuntimeState(
            memory_facts=[
                MemoryFact(
                    fact_type="summary",
                    value="产品思维",
                    source="memory_extract",
                    confidence=0.92,
                    observed_at=MemoryQualityAssessor._now_iso(),
                    memory_category="identity",
                    validated=True,
                    conflict_with=[],
                ),
            ]
        ),
    )
    
    report = MemoryQualityReport.generate_user_report(entry)
    assert report["user_id"] == "user_001"
    assert report["user_name"] == "李四"
    assert report["summary"]["total_facts"] == 1
    assert len(report["facts"]) == 1
    assert report["recommendation"] in ["maintain", "review", "cleanup"]


def test_memory_forgetengine_cleanup_user_memories() -> None:
    """测试用户记忆清理。"""
    entry = UserMemoryEntry(
        profile=UserProfile(user_id="user_001", name="王五"),
        runtime=UserRuntimeState(
            memory_facts=[
                # 高质量记忆（应该保留）
                MemoryFact(
                    fact_type="summary",
                    value="优质记忆",
                    source="memory_extract",
                    confidence=0.9,
                    observed_at=MemoryQualityAssessor._now_iso(),
                    memory_category="identity",
                    validated=True,
                    conflict_with=[],
                ),
                # 低质量、陈旧的记忆（应该删除）
                MemoryFact(
                    fact_type="summary",
                    value="陈旧低质记忆",
                    source="heuristic",
                    confidence=0.2,
                    observed_at=(datetime.now() - timedelta(days=100)).isoformat(timespec="seconds"),
                    memory_category="custom",
                    validated=False,
                    conflict_with=["other"],
                ),
            ]
        ),
    )
    
    original_count = len(entry.runtime.memory_facts)
    deleted = MemoryForgetEngine.cleanup_user_memories(entry)
    
    assert deleted == 1
    assert len(entry.runtime.memory_facts) == 1
    assert entry.runtime.memory_facts[0].value == "优质记忆"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
