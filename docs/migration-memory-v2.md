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
