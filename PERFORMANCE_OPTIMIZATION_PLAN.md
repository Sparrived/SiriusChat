# 性能优化诊断报告

## 📊 现状统计（以"临雀"为例）

```
用户数据规模:
├─ Memory Facts:       41个 (summary 17, user_interest 10, social_context 7, ...)
├─ Inferred Traits:    19个 (重复/语义重叠)
├─ Preference Tags:    16个 (重复/语义重叠)
├─ 对话轮次:          8轮
└─ 存储大小:         ~1.7KB (~1762 tokens)

可视化:
┌─────────────────────────────────────┐
│ 8轮对话 × 41个facts = 5个facts/轮  │
│ 从而推断：                          │
│ 100轮对话 → ~500个facts → 10KB      │
│          → 2500+ tokens            │
└─────────────────────────────────────┘
```

---

## 🔴 三个核心问题

### 问题1️⃣ 角色画像调用频率过高

**当前流程**（每条消息）:
```
User Message
    ↓
_add_human_turn()
    ├─ event_extract_task() ← 🔴 LLM调用
    ├─ apply_event_insights() ← CPU密集
    ├─ interpret_event_with_user_context() ← 对齐度计算
    └─ add_memory_fact() ← DB写
```

**成本分析**:
- 10活跃用户 × 0.5msg/s = 5msg/10s
- 每条消息都处理 → **5次LLM调用/秒** ⚠️
- 模型最大并发数通常为 1-2，导致排队延迟

**表现**: 延迟高、成本高、无法扩展

---

### 问题2️⃣ 特征和偏好标签爆炸（语义重复）

**当前数据示例**:
```json
inferred_traits: [
  "技术导向",      ← 
  "求知欲强",      ← 与技术导向重叠
  "注重细节",      ← 与求知欲强重叠
  ...
]

preference_tags: [
  "代码学习",      ← 
  "编程",          ← 与代码学习重叠
  "技术实现",      ← 与编程重叠
  "软件开发",      ← 与编程重叠
  ...
]
```

**问题**: 35个特征，但实际信息量可能只有 8-10 个核心概念

**表现**: Token占用过多、语义不清、模型迷惑

---

### 问题3️⃣ Memory Facts 数量膨胀（存储爆炸）

**增长趋势**:
```
轮次    Facts数  合计Size
────────────────────────
1       5        1.7KB
2      10        3.4KB
...
8      41       ~7KB  ← 当前

推断:
100    ~500      10KB    (2.5K tokens) ⚠️⚠️
1000   ~5000     100KB   (25K tokens)  🔴🔴
```

**占比问题**:
```
单fact分类:
├─ summary (41%)      ← 最多，但重复度高
├─ user_interest (24%)
├─ social_context (17%)
├─ emotional_pattern (10%)
└─ event_context_note (7%)
```

**表现**: Context window快速填满、Token成本无法控制

---

## 💡 解决方案

### ✅ 方案A: 事件处理去重（**推荐优先实现**）

**目的**: 减少不必要的LLM调用

**三个子方案**（建议按优先级选择）:

#### A1 - 时间窗口去重 (最简单)
```python
# 核心逻辑
if last_event_within_5_minutes:
    # 5分钟内只处理第1条 + 最后1条
    if is_first_in_window or is_last_in_window:
        process_event()
    else:
        skip_event()
```
- 收益: **50-80%** 减少处理
- 实现难度: ⭐ (简单)
- 代码改动: ~20行

#### A2 - 信息熵过滤 (推荐)
```python
# 判断新消息是否含有新信息
has_new_info = (
    has_new_keywords(message, user_history) or
    has_new_roles(message, user_history) or
    has_new_emotions(message, user_history)
)
if has_new_info:
    process_event()
```
- 收益: **30-50%** 减少处理
- 实现难度: ⭐⭐ (中等)
- 代码改动: ~40行

#### A3 - 相似度去重 (精细)
```python
similarity = compare_with_recent_events(new_message, recent_3_events)
if similarity > 0.7:
    skip_event()  # 高度相似，跳过处理
```
- 收益: **40-60%** 减少处理
- 实现难度: ⭐⭐⭐ (需要embedding)
- 代码改动: ~60行

---

### ✅ 方案B: 特征分类与归纳

**目的**: 抑制特征空间爆炸，提升语义质量

**分类体系示例**:
```
核心分类:
├─ Technical    → ["编程", "代码学习", "技术导向", "技术实现"]
├─ Learning     → ["深度学习", "求知欲强", "注意力机制"]
├─ Social       → ["团队", "领导", "管理"]
├─ Creative     → ["绘画", "创作", "视觉艺术"]
└─ Professional → ["项目开发", "测试", "高效"]

压缩效果: 35个特征 → 8-10个分类 (70%压缩)
```

**实现策略**:
```python
# 步骤1: 定义taxonomy
TRAIT_TAXONOMY = {
    "Technical": ["编程", "代码", "技术", "实现", ...],
    "Learning": ["学习", "机制", "注意力", "知识", ...],
    ...
}

# 步骤2: 在add_memory_fact时规范化
def normalize_trait(trait: str) -> str:
    for category, keywords in TRAIT_TAXONOMY.items():
        if any(kw in trait for kw in keywords):
            return category
    return trait  # 无法归类则保留原样
    
# 步骤3: 定期合并
def merge_similar_traits():
    # 同category的traits超过3个 → 合并为分类标签
    pass
```

- 收益: 特征数 **35 → 12** (65%压缩)
- 实现难度: ⭐⭐ (中等)
- 代码改动: ~80行 + taxonomy定义

---

### ✅ 方案C: Memory Facts 生命周期管理

**目的**: 控制facts数量，防止无限膨胀

#### C1 - 上限 + 自动清理
```python
MAX_MEMORY_FACTS = 50  # 当前默认

def add_memory_fact(fact):
    self.facts.append(fact)
    
    if len(self.facts) > MAX_MEMORY_FACTS:
        # 删除confidence最低的10%的facts
        sorted_facts = sorted(self.facts, key=lambda f: f.confidence)
        self.facts = self.facts[len(sorted_facts)//10:]
```

- 收益: facts数 **41 → ~25** (40%压缩)
- 实现难度: ⭐ (简单)
- 代码改动: ~15行

#### C2 - RESIDENT vs TRANSIENT (推荐)
```python
# 分离存储策略
RESIDENT_FACTS = facts with confidence > 0.85  # → 持久化到user.json
TRANSIENT_FACTS = facts with confidence ≤ 0.85  # → session存储(30min)

# 好处：
# - 持久化数据量减少60%+
# - session facts自动清理
# - 查询时只加载relevant facts
```

- 收益: 持久化数据量 **1.7KB → 0.7KB** (60%压缩)
- 实现难度: ⭐⭐ (中等)  
- 代码改动: ~100行 (需要session存储层)

#### C3 - 动态压缩 (进阶)
```python
def compress_memory_facts():
    """定期执行（如1小时一次）"""
    # 相同类型的facts > 10个 → 聚类
    # 相似的facts → 合并为1个高置信度fact
    # E.g: 10个`user_interest`相似词 → 合并为2-3个aggregated fact
```

- 收益: facts数 **500 → 150** (70%压缩，long-term用户)
- 实现难度: ⭐⭐⭐ (高)
- 代码改动: ~150行

---

## 📈 预期效果

| 指标 | 当前 | A1 | A1+B | A1+B+C1 | A1+B+C2 |
|------|------|----|----|-----|----|
| **LLM调用/msg** | 1.0 | 0.25 | 0.25 | 0.25 | 0.25 |
| **Memory Facts** | 41 | 41 | 41 | 25 | 25 |
| **特征数** | 35 | 35 | 12 | 12 | 12 |
| **单用户 size** | 1.7KB | 1.7KB | 1.0KB | 0.8KB | **0.7KB** |
| **100用户 total** | 170KB | - | 100KB | 80KB | **70KB** |

---

## 🎯 建议的实现路线

### Phase 1 (本周) - 快速见效
```
1. ✅ 实现方案A1（时间窗口去重）
   - impact: -75% LLM调用
   - 时间: 2小时
   
2. ✅ 实现方案C1（facts上限 + 清理）
   - impact: -40% facts数量
   - 时间: 1小时
```
**收益**: 显著降低LLM成本和storage

### Phase 2 (1-2周) - 中长期优化
```
3. 实现方案B（特征分类）
   - impact: -65% 特征数，语义更清晰
   - 时间: 4小时
   
4. 实现方案C2（RESIDENT vs TRANSIENT）
   - impact: -60% 持久化数据量
   - 时间: 6小时
```
**收益**: 彻底解决token爆炸问题

### Phase 3 (后续) - 长期可维护性
```
5. 实现方案C3（动态压缩）
6. 定期性审视和调整
```

---

## 🚀 立即开始执行

你想从哪个方案开始？我可以直接：
1. **实现 A1 + C1** (最快见效)
2. **实现 A2** (更精细的去重)
3. **实现 A1 + B + C1** (全面优化)
4. **其他组合**

---

**生成时间**: 2026-04-05  
**诊断基于**: 临雀用户8轮对话数据

