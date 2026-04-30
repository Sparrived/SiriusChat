# SemanticMemoryManager 充实实现方案

> 目标：将 `memory/semantic/` 从空壳 stub 升级为真正工作的群语义画像与用户关系状态系统。

---

## 一、现状分析

### 1.1 当前调用点（emotional_engine.py 中 10 处）

| 方法 | 调用语义内存的代码 | 用途 |
|------|-------------------|------|
| `__init__` | `self.semantic_memory = SemanticMemoryManager(work_path)` | 初始化 |
| `proactive_check` | `ensure_group_profile(group_id)` → `atmosphere_history[-1].group_valence` | 主动触发时读取群氛围 |
| `_decision` | `get_user_profile(group_id, user_id)` → `relationship_state` | 动态阈值计算（关系因子） |
| `_execution` | `get_group_profile(group_id)` + `get_user_profile(group_id, user_id)` | 响应组装（传入 response_assembler） |
| `tick_delayed_queue` | `get_group_profile(group_id)` | 延迟响应组装 |
| `_pick_proactive_topic` | `get_group_profile(group_id)` + `list_group_user_profiles(group_id)` | 主动话题选择 |
| `_build_proactive_prompt` | `get_group_profile(group_id)` | 主动响应组装 |

### 1.2 当前空壳实现的问题

- `save_user_profile()` → `pass`（不持久化）
- `list_group_user_profiles()` → `return []`（永远空列表）
- `RelationshipState.compute_familiarity()` → 永远返回 `0.5`
- `_pick_proactive_topic()` 中的循环遍历永远为空，主动话题选择完全失效

---

## 二、数据模型扩展（在现有 models.py 基础上）

现有模型字段已覆盖核心语义概念，只需补充计算字段和默认值：

```python
# RelationshipState 需要可计算的字段
trust_score: float = 0.5
dependency_score: float = 0.5
emotional_intimacy: float = 0.5
interaction_frequency_7d: float = 0.0
first_interaction_at: str = ""
last_interaction_at: str = ""

# GroupSemanticProfile 需要补充
atmosphere_history: list[AtmosphereSnapshot]  # 已存在，需要真正写入
dominant_topic: str = ""  # 新增：从日记或消息中提取的主导话题
```

---

## 三、持久化方案

采用与 `BasicMemoryFileStore`、`DiaryManager` 一致的 JSON 文件模式：

```
{work_path}/memory/semantic/
├── groups/
│   └── {group_id}.json          # GroupSemanticProfile
└── users/
    └── {group_id}/
        └── {user_id}.json       # UserSemanticProfile
```

**原子写入**：使用 `_atomic_write_json`（临时文件 + replace），避免脏写。

---

## 四、分阶段实现步骤

### Phase 1：基础持久化与读写（1-2 小时）

**目标**：让 `SemanticMemoryManager` 真正保存和读取数据。

1. **创建 `memory/semantic/store.py`**
   - `SemanticProfileStore` 类，管理 `groups/` 和 `users/` 目录的 JSON 读写
   - `load_group_profile(group_id) -> GroupSemanticProfile | None`
   - `save_group_profile(group_id, profile)`
   - `load_user_profile(group_id, user_id) -> UserSemanticProfile | None`
   - `save_user_profile(group_id, user_id, profile)`
   - `list_group_user_profiles(group_id) -> list[UserSemanticProfile]`

2. **修改 `memory/semantic/manager.py`**
   - 注入 `SemanticProfileStore(work_path)`
   - `get_user_profile()`：先查内存缓存，再读磁盘，不存在则新建并持久化
   - `save_user_profile()`：调用 store 写入磁盘
   - `ensure_group_profile()`：同上，带磁盘回写
   - `list_group_user_profiles()`：调用 store 读取目录下所有用户画像

3. **验证**
   - 启动 engine，处理几条消息
   - 检查 `{work_path}/memory/semantic/` 下是否生成 JSON 文件
   - 重启 engine，确认数据能加载回来

### Phase 2：群规范被动学习（2-3 小时）

**目标**：从消息流中自动推断群特征，填充 `group_norms` 和 `typical_interaction_style`。

**触发时机**：在 `emotional_engine.py` 的 `_perception()` 中，每条消息处理后调用一次（零 LLM 成本）。

**实现**：在 `SemanticMemoryManager` 中添加 `learn_from_message(group_id, message, intent)` 方法：

```python
def learn_from_message(self, group_id, message, intent):
    profile = self.ensure_group_profile(group_id)
    norms = profile.group_norms
    content = message.content or ""

    # 1. 消息长度统计（滚动平均）
    length = len(content)
    old_avg = norms.get("avg_message_length", 0.0)
    old_count = norms.get("message_count", 0)
    new_count = old_count + 1
    norms["avg_message_length"] = (old_avg * old_count + length) / new_count
    norms["message_count"] = new_count

    # 2. 长度分布
    bucket = "short" if length < 20 else "medium" if length < 100 else "long"
    dist = norms.get("length_distribution", {})
    dist[bucket] = dist.get(bucket, 0) + 1
    norms["length_distribution"] = dist

    # 3. Emoji 使用率
    has_emoji = bool(emoji_pattern.search(content))
    emoji_total = norms.get("emoji_total", 0) + (1 if has_emoji else 0)
    norms["emoji_total"] = emoji_total
    norms["emoji_usage_rate"] = emoji_total / new_count

    # 4. @提及率
    has_mention = "@" in content
    mention_total = norms.get("mention_total", 0) + (1 if has_mention else 0)
    norms["mention_total"] = mention_total
    norms["mention_rate"] = mention_total / new_count

    # 5. 活跃时段直方图
    hour = datetime.now(timezone.utc).hour
    hours = norms.get("active_hours", {})
    hours[str(hour)] = hours.get(str(hour), 0) + 1
    norms["active_hours"] = hours

    # 6. 交互风格推断
    short_ratio = dist.get("short", 0) / new_count
    if short_ratio > 0.6:
        profile.typical_interaction_style = "active"
    elif norms.get("emoji_usage_rate", 0) > 0.3:
        profile.typical_interaction_style = "humorous"
    elif norms.get("mention_rate", 0) > 0.2:
        profile.typical_interaction_style = "formal"
    else:
        profile.typical_interaction_style = "balanced"

    # 7. 话题切换追踪
    if intent and hasattr(intent, "social_intent"):
        last = norms.get("last_intent", "")
        current = intent.social_intent.value if hasattr(intent.social_intent, "value") else str(intent.social_intent)
        if current != last:
            norms["topic_switches"] = norms.get("topic_switches", 0) + 1
        norms["last_intent"] = current
        norms["topic_switch_frequency"] = norms["topic_switches"] / new_count

    # 写入磁盘
    self.save_group_profile(group_id, profile)
```

> **注意**：原 `emotional_engine.py` 中的 `_learn_group_norms()` 方法已实现上述逻辑，但它是死方法（零调用）。应将其实现迁移到 `SemanticMemoryManager.learn_from_message()` 中，并在 `_perception()` 中调用。

### Phase 3：氛围历史记录（1 小时）

**目标**：在每次 `_decision()` 后记录群氛围快照。

**实现**：在 `emotional_engine.py` 的 `_decision()` 末尾添加：

```python
# 记录氛围快照
from sirius_chat.memory.semantic.models import AtmosphereSnapshot
atmosphere = AtmosphereSnapshot(
    timestamp=now_iso(),
    group_valence=emotion.valence,
    group_arousal=emotion.arousal,
    active_participants=len(set(m.get("user_id") for m in recent_msgs)),
)
self.semantic_memory.record_atmosphere(group_id, atmosphere)
```

`SemanticMemoryManager.record_atmosphere()` 实现：
- 读取群画像
- `profile.atmosphere_history.append(snapshot)`
- 限制历史长度（保留最近 100 条）
- 保存回磁盘

### Phase 4：用户关系状态更新（1-2 小时）

**目标**：基于消息交互更新 `RelationshipState`。

**触发时机**：`_perception()` 中，处理完消息 intent/emotion 后。

**实现**：`SemanticMemoryManager.update_relationship(group_id, user_id, message_emotion, intent)`

```python
def update_relationship(self, group_id, user_id, emotion, intent):
    user_profile = self.get_user_profile(group_id, user_id)
    rs = user_profile.relationship_state

    # 更新最近交互时间
    from sirius_chat.core.utils import now_iso
    rs.last_interaction_at = now_iso()
    if not rs.first_interaction_at:
        rs.first_interaction_at = rs.last_interaction_at

    # 交互频率（简化：每次交互 +1/7 衰减）
    rs.interaction_frequency_7d = min(1.0, rs.interaction_frequency_7d + 0.05)

    # 情感亲密度（基于 emotion valence）
    if emotion and hasattr(emotion, "valence"):
        valence = abs(emotion.valence)
        rs.emotional_intimacy = round(rs.emotional_intimacy * 0.9 + valence * 0.1, 3)

    # 信任分（高 urgency 求助增加信任）
    if intent and getattr(intent, "urgency_score", 0) > 70:
        rs.trust_score = round(min(1.0, rs.trust_score + 0.02), 3)

    # 依赖度（求助频率）
    if intent and getattr(intent, "social_intent", None):
        intent_val = str(intent.social_intent)
        if "help" in intent_val.lower() or "求助" in intent_val:
            rs.dependency_score = round(min(1.0, rs.dependency_score + 0.03), 3)

    # 熟悉度计算（基于交互次数和时间跨度）
    rs.compute_familiarity = lambda: min(1.0, 0.3 + rs.interaction_frequency_7d * 0.5 + rs.emotional_intimacy * 0.2)

    self.save_user_profile(group_id, user_id, user_profile)
```

### Phase 5：兴趣话题提取（2-3 小时，可选 LLM）

**目标**：填充 `group_profile.interest_topics` 和 `user_profile.interest_graph`。

**方案 A（零 LLM 成本，关键词统计）**：
- 使用 jieba/简单分词统计名词短语频率
- 频率 > 阈值且出现次数 > 3 的词加入 `interest_topics`

**方案 B（LLM 提取，精度更高）**：
- 在 `DiaryGenerator` 生成日记时，让 LLM 同时提取 "群聊主导话题" 和 "各用户感兴趣的话题"
- 写入 `SemanticMemoryManager`

**建议**：先做方案 A（简单关键词统计），后续需要时再升级到方案 B。

### Phase 6：主动话题选择修复（30 分钟）

**目标**：让 `_pick_proactive_topic()` 真正工作。

当前 `_pick_proactive_topic()` 的逻辑本身是正确的（去重、过滤禁忌话题、选第一个）。唯一的问题是数据源为空。

完成 Phase 1-5 后：
- `group_profile.interest_topics` 将有数据（Phase 5）
- `group_profile.group_norms["dominant_topic"]` 将有数据（Phase 5）
- `list_group_user_profiles()` 将返回真实用户列表（Phase 1）

`_pick_proactive_topic()` 无需修改即可自动生效。

---

## 五、Engine 侧调用点改造清单

| 引擎方法 | 当前行为 | 改造后行为 |
|---------|---------|-----------|
| `_perception()` | 不调用 semantic_memory | 结尾调用 `semantic_memory.learn_from_message()` + `update_relationship()` |
| `_decision()` | 读取 user_profile 的 relationship_state | 结尾调用 `semantic_memory.record_atmosphere()` |
| `_execution()` / `tick_delayed_queue()` / `_build_proactive_prompt()` | 读取 group_profile 传入 assembler | 无需改造（Phase 1 后数据自然有） |
| `proactive_check()` | 读取 atmosphere_history | 无需改造（Phase 3 后数据自然有） |
| `_pick_proactive_topic()` | 遍历空列表 | 无需改造（Phase 5 后数据自然有） |

---

## 六、风险与回退方案

| 风险 | 缓解措施 |
|------|---------|
| 性能：每条消息都写磁盘 | 采用内存缓存 + 批量回写（每 10 条消息或 60 秒刷盘一次） |
| 并发：多群同时写入 | 使用 `asyncio.Lock` 或按 `group_id` 分片锁 |
| 数据膨胀：atmosphere_history 无限增长 | 限制历史长度（100 条），超限时截断 |
| 格式不兼容：旧 JSON 缺少新字段 | `dataclass` 使用 `field(default_factory=...)`，缺失字段自动取默认值 |

---

## 七、验收标准

1. 启动 engine → 处理 10 条消息 → 检查 `{work_path}/memory/semantic/groups/` 和 `users/` 目录下生成 JSON
2. 重启 engine → `semantic_memory.get_group_profile()` 返回的 `group_norms` 中 `message_count >= 10`
3. `_pick_proactive_topic()` 返回非空字符串（interest_topics 或 dominant_topic 有数据）
4. `_decision()` 中的 `relationship_state` 不再是 `None`，且 `compute_familiarity()` 返回值随交互增长
5. 全部现有测试仍通过（475+）

---

*方案制定时间：2026-04-26*
*预计总工时：Phase 1-4 约 6-8 小时（不含 Phase 5 LLM 方案）*
