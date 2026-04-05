"""特征分类体系（B方案：Trait Taxonomy）。

用于自动规范化和分类用户特征。
"""

from typing import Any

# ============================================================================
# B: 特征分类体系（Trait Taxonomy）
# ============================================================================

TRAIT_TAXONOMY: dict[str, dict[str, Any]] = {
    "Technical": {
        "keywords": [
            "编程", "代码", "技术", "实现", "开发", "coding", "programming",
            "technical", "python", "javascript", "java", "c++", "软件", "算法"
        ],
        "priority": 1,
    },
    "Learning": {
        "keywords": [
            "学习", "注意力", "机制", "知识", "求知", "学", "learning",
            "neural", "深度", "理解", "探索", "研究"
        ],
        "priority": 2,
    },
    "Social": {
        "keywords": [
            "团队", "领导", "管理", "交流", "社交", "team", "leadership",
            "social", "collaboration", "合作", "讨论", "沟通"
        ],
        "priority": 3,
    },
    "Creative": {
        "keywords": [
            "绘画", "创作", "视觉", "艺术", "creative", "art", "drawing",
            "设计", "创意", "图像"
        ],
        "priority": 4,
    },
    "Professional": {
        "keywords": [
            "项目", "测试", "高效", "质量", "工作", "professional", "project",
            "开发", "交付", "维护", "部署", "工程"
        ],
        "priority": 5,
    },
}
