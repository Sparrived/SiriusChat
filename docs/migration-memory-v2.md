# 记忆系统 V2 破坏性变更迁移指南

## 变更摘要

本版本对 `sirius_chat.memory` 子系统进行了重大重构，涉及模型字段删除/新增、序列化格式变化和 API 签名调整。

### 影响范围

| 模块 | 变更等级 | 说明 |
|------|---------|------|
| `MemoryFact` | **破坏性** | 删除 `is_transient`/`created_at` 字段 |
| `MemoryPolicy` | 新增 | 记忆系统集中配置 |
| `UserMemoryManager` | 接口变更 | 多个方法签名调整 |
| `UserMemoryFileStore` | 格式变更 | 序列化字段从 5→12 |
| `MemoryForgetEngine` | 行为变更 | 衰退曲线更陡峭 |

---

## 1. MemoryFact 模型变更

### 1.1 删除的字段

```python
# ❌ 旧写法 — 不再有效
fact.is_transient  # AttributeError (字段已删除)
fact.created_at    # AttributeError (字段已删除)
```

### 1.2 `is_transient` 从字段变为方法

```python
# ❌ 旧写法
if fact.is_transient:
    ...

# ✅ 新写法 — 动态计算，支持自定义阈值
if fact.is_transient():           # 默认阈值 0.85
    ...
if fact.is_transient(threshold=0.7):  # 自定义阈值
    ...
```

**行为变化**：旧版 `is_transient` 在创建时根据 `confidence <= 0.85` 固定赋值；新版每次调用时动态计算，confidence 变化即反映最新状态。

### 1.3 `created_at` 替代方案

`created_at` 字段已被 `observed_at` 完全替代。`observed_at` 在 `add_memory_fact()` 时自动填充。

```python
# ❌ 旧写法
timestamp = fact.created_at

# ✅ 新写法
timestamp = fact.observed_at
```

### 1.4 新增字段

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `mention_count` | `int` | `0` | 该事实被重复提及的次数 |
| `source_event_id` | `str` | `""` | 关联的事件 ID |
| `context_channel` | `str` | `""` | 来源渠道 (qq/wechat/cli) |
| `context_topic` | `str` | `""` | 对话主题 |
| `observed_time_desc` | `str` | `""` | 人类友好的时间描述 |

### 1.5 confidence 自动钳位

`MemoryFact.__post_init__` 现在自动将 confidence 钳位到 `[0.0, 1.0]`：

```python
fact = MemoryFact(fact_type="t", value="v", confidence=1.5)
assert fact.confidence == 1.0  # 自动钳位
```

---

## 2. MemoryPolicy 新配置

新增 `MemoryPolicy` 数据类，集中管理记忆系统参数：

```python
from sirius_chat.config import MemoryPolicy, OrchestrationPolicy

# 使用默认值
orch = OrchestrationPolicy()
print(orch.memory.max_facts_per_user)  # 50

# 自定义配置
orch = OrchestrationPolicy(
    memory=MemoryPolicy(
        max_facts_per_user=100,
        transient_confidence_threshold=0.7,
        max_observed_set_size=200,
    )
)
```

### 配置项说明

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `max_facts_per_user` | 50 | 每用户最大记忆条目数 |
| `transient_confidence_threshold` | 0.85 | RESIDENT/TRANSIENT 分界线 |
| `event_dedup_window_minutes` | 5 | 事件去重时间窗口(分钟) |
| `max_observed_set_size` | 100 | observed_* 集合最大大小 |
| `max_summary_facts_per_type` | 5 | 摘要中每类型最多事实数 |
| `max_summary_total_chars` | 2000 | 摘要总字符上限 |
| `decay_schedule` | `{7:0.95, 30:0.80, 60:0.55, 90:0.30, 180:0.05}` | 衰退时间表 |

---

## 3. UserMemoryManager API 变更

### 3.1 `add_memory_fact()` 新参数

```python
# ❌ 旧写法（仍兼容，新参数有默认值）
manager.add_memory_fact(user_id="u1", fact_type="t", value="v", source="s", confidence=0.7)

# ✅ 新写法 — 传递额外上下文
manager.add_memory_fact(
    user_id="u1",
    fact_type="preference",
    value="likes coffee",
    source="conversation",
    confidence=0.7,
    memory_category="preference",     # 新增
    source_event_id="evt_001",        # 新增
    context_channel="qq",             # 新增
    context_topic="food",             # 新增
)
```

**去重逻辑**：同一 `(fact_type, value)` 不再创建重复条目，而是递增 `mention_count` 并更新 confidence（取较高值）。

### 3.2 `get_resident_facts()` / `get_transient_facts()` 新参数

```python
# ❌ 旧写法（仍兼容）
resident = manager.get_resident_facts("u1")

# ✅ 新写法 — 可自定义阈值
resident = manager.get_resident_facts("u1", threshold=0.7)
transient = manager.get_transient_facts("u1", threshold=0.7)
```

### 3.3 `get_rich_user_summary()` 新参数

```python
# ✅ 新增 max_facts_per_type 控制摘要长度
summary = manager.get_rich_user_summary("u1", max_facts_per_type=3)
```

### 3.4 `apply_event_insights()` 新参数

```python
# ✅ 新增 source_event_id 追踪事件来源
manager.apply_event_insights("u1", event_features, source_event_id="evt_042")
```

---

## 4. 序列化格式变更

### 4.1 用户 JSON 文件

`work_path/users/<user_id>.json` 中 `memory_facts` 字段从 5 个增加到 12 个：

```json
{
  "memory_facts": [
    {
      "fact_type": "preference",
      "value": "likes coffee",
      "source": "conversation",
      "confidence": 0.8,
      "observed_at": "2025-01-01T12:00:00",
      "observed_time_desc": "今天中午",
      "memory_category": "preference",
      "validated": false,
      "conflict_with": [],
      "context_channel": "qq",
      "context_topic": "food",
      "mention_count": 3,
      "source_event_id": "evt_001"
    }
  ]
}
```

### 4.2 向后兼容

旧格式文件可正常加载，缺失字段使用默认值。旧格式中的 `is_transient` 和 `created_at` 字段会被忽略。

---

## 5. 衰退曲线变更

`MemoryForgetEngine.DEFAULT_DECAY_SCHEDULE` 更新为更激进的衰退曲线：

| 天数 | 旧倍率 | 新倍率 | 影响 |
|------|--------|--------|------|
| 7 | 0.95 | 0.95 | 无变化 |
| 30 | 0.85 | 0.80 | 略微加快 |
| 60 | 0.70 | 0.55 | 明显加快 |
| 90 | 0.50 | 0.30 | 大幅加快 |
| 180 | 0.20 | 0.05 | 接近完全遗忘 |

自定义衰退曲线：

```python
from sirius_chat.memory.quality.models import MemoryForgetEngine

# apply_decay 现在支持自定义 schedule
decayed = MemoryForgetEngine.apply_decay(
    fact,
    decay_schedule={7: 0.98, 30: 0.90, 60: 0.70, 90: 0.40, 180: 0.10},
)
```

---

## 6. observed_* 集合上限

`apply_event_insights()` 现在自动限制 `observed_keywords`、`observed_roles`、`observed_emotions`、`observed_entities` 集合大小为 `MAX_OBSERVED_SET_SIZE`（默认 100）。超出时随机移除多余元素。

---

## 7. 公共 API 新增导出

```python
from sirius_chat import MemoryPolicy              # 新增
from sirius_chat.config import MemoryPolicy        # 新增
from sirius_chat.memory import MAX_OBSERVED_SET_SIZE  # 新增
```

---

## 8. 事件记忆系统 V2 重构

### 8.1 设计动机

V1 事件系统存在以下问题：

1. **每条消息都触发 LLM 提取**：浪费 token，且大量消息不包含有价值信息
2. **Jaccard 启发式聚类不可靠**：6 维加权相似度矩阵（keywords/role_slots/entities/time_hints/emotion_tags/summary）复杂却不精确
3. **与用户系统割裂**：事件条目缺少 `user_id`，无法区分哪个参与者的观察
4. **验证流程多余**：`verified`/`pending` 状态 + `finalize_pending_events()` LLM 二次验证增加复杂度但收益有限

### 8.2 V2 架构概览

```
消息 → buffer_message() → 缓冲区累积
                              ↓ (达到 batch_size)
                    extract_observations() → LLM 批量提取
                              ↓
                    EventMemoryEntry (user_id, category, confidence)
                              ↓
                    engine 写入 user_memory.add_memory_fact()
```

**核心变化**：
- 从「每条消息 → LLM 提取 → 启发式聚类」变为「缓冲 N 条 → LLM 批量提取 → 直接归入用户记忆」
- 事件条目绑定 `user_id`，观察归属明确
- 去除所有停用词/关键词/角色/时间/情感的正则提取逻辑

### 8.3 `EventMemoryEntry` 模型变更

#### 删除的字段

```python
# ❌ 以下字段在 V2 中已删除
entry.keywords       # list[str]
entry.role_slots     # list[str]
entry.entities       # list[str]
entry.time_hints     # list[str]
entry.emotion_tags   # list[str]
entry.hit_count      # int
entry.verified       # bool
```

#### 新增/变更的字段

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `user_id` | `str` | `""` | 关联的参与者 ID |
| `category` | `str` | `"custom"` | 观察类别（见下表） |
| `confidence` | `float` | `0.5` | LLM 置信度 [0, 1] |
| `evidence` | `list[str]` | `[]` | 支撑该观察的原始消息片段 |

#### 观察类别 (`OBSERVATION_CATEGORIES`)

```python
from sirius_chat.memory.event import OBSERVATION_CATEGORIES

# frozenset: {"preference", "trait", "relationship", "experience", "emotion", "goal", "custom"}
```

| 类别 | 含义 | 对应 user_memory fact_type |
|------|------|--------------------------|
| `preference` | 偏好（喜欢/不喜欢） | `preference_tag` |
| `trait` | 性格特征 | `inferred_trait` |
| `relationship` | 社交关系 | `social_context` |
| `experience` | 经历/事件 | `summary` |
| `emotion` | 情感状态 | `summary` |
| `goal` | 目标/计划 | `summary` |
| `custom` | 其他 | `summary` |

### 8.4 `EventMemoryManager` API 变更

#### 新增方法

```python
# 缓冲消息（不触发 LLM）
manager.buffer_message(user_id="u1", content="我今天去了咖啡店")

# 检查是否应该触发批量提取
if manager.should_extract(user_id="u1", batch_size=5):
    ...

# 批量提取观察（触发 LLM）
new_entries = await manager.extract_observations(
    user_id="u1",
    user_name="小王",
    provider_async=provider,
    model_name="gpt-4o-mini",
)

# 按用户查询观察
observations = manager.get_user_observations("u1")

# 检查消息与已有观察的相关性
hit_payload = manager.check_relevance(user_id="u1", content="我又去了咖啡店")
# 返回: {"level": "high", "score": 0.8, ...} 或 None
```

#### 已移除的方法

```python
# ❌ 以下方法已移除
manager._extract_keywords()
manager._extract_role_slots()
manager._score()
manager._build_feature_payload()
```

#### 向后兼容保留

```python
# ✅ 仍可调用，但内部行为改变
manager.absorb_mention()   # 委托给 buffer_message()
manager.finalize_pending_events()  # 委托给 flush_buffer（需传 provider）
```

### 8.5 序列化格式变更

#### V2 JSON 格式

```json
{
  "version": 2,
  "entries": [
    {
      "event_id": "evt_001",
      "user_id": "u1",
      "summary": "喜欢喝美式咖啡",
      "category": "preference",
      "confidence": 0.8,
      "evidence": ["我今天去了咖啡店点了美式", "咖啡是我每天的必需品"],
      "first_seen": "2025-01-01T12:00:00",
      "last_seen": "2025-01-02T08:30:00"
    }
  ]
}
```

#### 自动迁移

加载 V1 数据时自动迁移：
- `keywords` + `entities` → `evidence`
- `verified=True` → `confidence=0.7`，`verified=False` → `confidence=0.3`
- `hit_count` → 用于推算 confidence
- `role_slots`/`time_hints`/`emotion_tags` → 丢弃

```python
# 自动处理，无需手动操作
manager = EventMemoryManager.from_dict(old_v1_data)  # 内部调用 _migrate_v1()
```

### 8.6 编排配置变更

`OrchestrationPolicy` 新增字段：

```python
from sirius_chat.config import OrchestrationPolicy

orch = OrchestrationPolicy(
    event_extract_batch_size=5,  # 每 N 条消息触发一次观察提取（默认 5）
)
```

### 8.7 Engine 行为变更

| 行为 | V1 | V2 |
|------|----|----|
| 提取触发 | 每条消息 | 每 N 条消息（`event_extract_batch_size`） |
| LLM 调用 | `_run_event_extract_task()` | `_run_batch_event_extract()` |
| 提取结果 | 写入 `EventMemoryEntry` 并标记 pending | 写入 `EventMemoryEntry` 并直接同步到 `user_memory` |
| 回复影响 | `build_event_hit_system_note()` 注入系统消息 | `check_relevance()` 影响回复意愿，不注入系统消息 |
| 旧方法 | `_run_event_extract_task()` | 保留但返回 `None`（空操作） |

### 8.8 迁移检查清单

- [ ] 确认不直接访问 `entry.keywords`/`entry.role_slots` 等已删除字段
- [ ] 将 `absorb_mention()` 调用替换为 `buffer_message()` + `should_extract()` + `extract_observations()`
- [ ] 如有自定义提取间隔需求，设置 `event_extract_batch_size`
- [ ] 确认序列化文件可被自动迁移（`from_dict` 会处理 V1 格式）
- [ ] 检查是否依赖 `build_event_hit_system_note()`（V2 中已不再被 engine 调用）
- [ ] 检查是否依赖 `finalize_pending_events()` 的旧行为（V2 需传入 provider 参数）
