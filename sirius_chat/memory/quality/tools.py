"""离线记忆质量评估工具。

可用于分析历史会话数据中的记忆质量、生成报告、执行基于数据的清理策略。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sirius_chat.memory.quality.models import MemoryForgetEngine, MemoryQualityAssessor, MemoryQualityReport
from sirius_chat.memory import UserMemoryFileStore, UserMemoryManager


def analyze_workspace_memories(work_path: Path) -> dict[str, Any]:
    """分析工作目录中的所有用户记忆。
    
    Args:
        work_path: Sirius Chat 工作目录路径
    
    Returns:
        包含评估报告的字典
    """
    file_store = UserMemoryFileStore(work_path)
    manager = file_store.load_all()
    
    if not manager.entries:
        return {"status": "no_memories", "message": "未找到任何用户记忆"}
    
    # 生成系统级报告
    report = MemoryQualityReport.generate_system_report(manager)
    
    return report


def cleanup_workspace_memories(
    work_path: Path,
    min_quality: float = 0.25,
    force: bool = False,
) -> dict[str, Any]:
    """清理工作目录中的低质量记忆。
    
    Args:
        work_path: Sirius Chat 工作目录路径
        min_quality: 最低质量阈值（0-1）
        force: 是否跳过确认直接执行
    
    Returns:
        清理统计信息
    """
    file_store = UserMemoryFileStore(work_path)
    manager = file_store.load_all()
    
    if not manager.entries:
        return {"status": "no_memories", "deleted": {}}
    
    # 执行清理
    deleted_stats = manager.cleanup_expired_memories(min_quality=min_quality)
    
    # 保存清理后的数据
    file_store.save_all(manager)
    
    # 生成报告
    report = MemoryQualityReport.generate_system_report(manager)
    
    return {
        "status": "success",
        "deleted_per_user": deleted_stats,
        "total_deleted": sum(deleted_stats.values()),
        "report": report,
    }


def apply_decay_to_workspace(work_path: Path) -> dict[str, Any]:
    """对工作目录中的所有记忆应用时间衰退。
    
    Args:
        work_path: Sirius Chat 工作目录路径
    
    Returns:
        衰退统计信息
    """
    file_store = UserMemoryFileStore(work_path)
    manager = file_store.load_all()
    
    if not manager.entries:
        return {"status": "no_memories", "decayed": {}}
    
    # 应用衰退
    decayed_stats = manager.apply_scheduled_decay()
    
    # 保存衰退后的数据
    file_store.save_all(manager)
    
    # 生成报告
    report = MemoryQualityReport.generate_system_report(manager)
    
    return {
        "status": "success",
        "decayed_per_user": decayed_stats,
        "total_decayed": sum(decayed_stats.values()),
        "report": report,
    }


def save_quality_report(report: dict[str, Any], output_file: Path) -> None:
    """保存质量报告到文件。
    
    Args:
        report: 报告字典
        output_file: 输出文件路径
    """
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"✓ 报告已保存到: {output_file}")


def print_console_report(report: dict[str, Any], verbose: bool = False) -> None:
    """在控制台打印质量报告。
    
    Args:
        report: 报告字典
        verbose: 是否打印详细信息
    """
    print("\n" + "=" * 80)
    print("记忆质量评估报告")
    print("=" * 80)
    
    if "summary" in report:
        summary = report["summary"]
        print(f"\n【系统整体统计】")
        print(f"  • 总用户数: {summary['total_users']}")
        print(f"  • 总记忆数: {summary['total_facts']}")
        print(f"  • 平均质量: {summary['average_quality']:.1%}")
        
        if "distribution" in report:
            dist = report["distribution"]
            if "by_category" in dist:
                print(f"\n【记忆分类分布】")
                for cat, count in dist["by_category"].items():
                    print(f"  • {cat}: {count}")
            
            if "by_source" in dist:
                print(f"\n【记忆来源分布】")
                for source, count in dist["by_source"].items():
                    print(f"  • {source}: {count}")
    
    # 用户级报告
    if "user_reports" in report:
        print(f"\n【用户级报告】")
        for user_report in report["user_reports"]:
            print(f"\n  用户: {user_report['user_name']} (ID: {user_report['user_id']})")
            summary = user_report["summary"]
            consistency = user_report["consistency"]
            recommendation = user_report["recommendation"]
            
            print(f"    统计:")
            print(f"      - 总记忆数: {summary['total_facts']}")
            print(f"      - 已验证: {summary['validated_facts']}/{summary['total_facts']}")
            print(f"      - 有冲突: {summary['conflicting_facts']}")
            print(f"      - 陈旧记忆: {summary['outdated_facts']}")
            
            print(f"    一致性评分:")
            print(f"      - 身份: {consistency['identity']:.1%}")
            print(f"      - 偏好: {consistency['preference']:.1%}")
            print(f"      - 情绪: {consistency['emotion']:.1%}")
            print(f"      - 事件: {consistency['event']:.1%}")
            print(f"      - 整体: {consistency['overall']:.1%}")
            
            print(f"    建议: {recommendation}")
            
            if verbose and "facts" in user_report:
                print(f"    记忆详情 (前5条):")
                for fact in user_report["facts"][:5]:
                    forget_marker = " [待遗忘]" if fact["should_forget"] else ""
                    print(f"      • {fact['value'][:30]}")
                    print(f"        类别: {fact['category']}, 质量: {fact['quality_score']:.2f}{forget_marker}")
    
    print("\n" + "=" * 80)


if __name__ == "__main__":
    import argparse
    import sys
    
    parser = argparse.ArgumentParser(description="记忆质量评估与管理工具")
    parser.add_argument("work_path", type=Path, help="Sirius Chat 工作目录")
    parser.add_argument(
        "--action",
        choices=["analyze", "cleanup", "decay", "all"],
        default="analyze",
        help="执行的操作"
    )
    parser.add_argument(
        "--min-quality",
        type=float,
        default=0.25,
        help="清理时的最低质量阈值 (0-1)"
    )
    parser.add_argument(
        "--output-report",
        type=Path,
        help="保存报告的文件路径"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="打印详细信息"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="跳过确认直接执行清理"
    )
    
    args = parser.parse_args()
    
    if not args.work_path.exists():
        print(f"❌ 工作目录不存在: {args.work_path}")
        sys.exit(1)
    
    try:
        if args.action in ["analyze", "all"]:
            print(f"🔍 分析记忆质量...")
            report = analyze_workspace_memories(args.work_path)
            print_console_report(report, verbose=args.verbose)
            
            if args.output_report:
                save_quality_report(report, args.output_report)
        
        if args.action in ["cleanup", "all"]:
            print(f"\n🗑️  清理低质量记忆 (阈值: {args.min_quality})...")
            result = cleanup_workspace_memories(args.work_path, min_quality=args.min_quality, force=args.force)
            print(f"✓ 清理完成: 删除 {result['total_deleted']} 条记忆")
            print_console_report(result["report"], verbose=args.verbose)
            
            if args.output_report:
                save_quality_report(result["report"], args.output_report)
        
        if args.action in ["decay", "all"]:
            print(f"\n⏳ 应用时间衰退...")
            result = apply_decay_to_workspace(args.work_path)
            print(f"✓ 衰退完成: 更新 {result['total_decayed']} 条记忆")
            print_console_report(result["report"], verbose=args.verbose)
            
            if args.output_report:
                save_quality_report(result["report"], args.output_report)
        
        print("\n✅ 操作完成")
    
    except Exception as e:
        print(f"\n❌ 出错: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
