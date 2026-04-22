# Sirius Chat 记忆系统与用户系统工作流程报告

> 版本：v0.28+  
> 生成日期：2026-04-22  
> 基于代码库全量分析

---

## 1. 系统架构总览

Sirius Chat 采用**四层认知架构** + **三层记忆底座**的设计：

```
┌─────────────────────────────────────────────────────────────┐
│                     认知架构（四层）                           │
├─────────────┬─────────────┬─────────────┬───────────────────┤
│  感知层      │  认知层      │  决策层      │     执行层         │
│ Perception  │  Cognition  │  Decision   │    Execution      │
├─────────────┴─────────────┴─────────────┴───────────────────┤
│                     记忆底座（三层）                           │
├─────────────────┬─────────────────┬─────────────────────────┤
│   工作记忆       │   情景记忆       │      语义记忆            │
│ Working Memory  │ Episodic Memory │   Semantic Memory       │
│   (内存滑动窗口)  │  (按群文件存储)   │  (用户画像+群体画像)      │
├─────────────────┴─────────────────┴─────────────────────────┤
│                  辅助记忆子系统                                │
├─────────────┬─────────────┬─────────────┬───────────────────┤
│  用户记忆     │  事件记忆 V2  │ AI 自身记忆  │  激活度/质量/遗忘   │
│ User Memory │ Event Memory │ Self Memory │ Activation/QoS    │
└─────────────┴─────────────┴─────────────┴───────────────────┘
```

---

## 2. 用户系统详解

### 2.1 用户数据模型层级

用户系统由三个层级构成，从外到内逐渐细化：

| 层级 | 类名 | 职责 | 文件 |
|------|------|------|------|
| **外部接口层** | `Participant` / `User` | 外部系统传入的用户表示，含跨平台身份映射 | `models/models.py` |
| **持久化层** | `UserProfile` | 静态用户档案（不可被AI运行时随意覆盖） | `memory/user/models.py` |
| **运行时层** | `UserMemoryEntry` | 动态用户状态 = Profile + RuntimeState | `memory/user/models.py` |

#### Participant（外部接口）
```python
@dataclass
class Participant:
    name: str                # 显示名称
    user_id: str             # UUID，自动分配 "user_<uuid>"
    persona: str             # 初始人设
    identities: dict         # 跨平台身份映射 {channel: external_uid}
    aliases: list[str]       # 别名列表
    traits: list[str]        # 初始特质
    group_memberships: dict  # 群组成员关系
    metadata: dict           # 扩展元数据（含 is_developer 标记）
```

#### UserProfile（静态档案）
```python
@dataclass
class UserProfile:
    user_id: str
    name: str
    persona: str = ""        # 外部提供的初始人设，AI不可随意覆盖
    identities: dict         # 跨平台身份
    aliases: list[str]
    traits: list[str]
    metadata: dict
```

#### UserRuntimeState（动态运行时状态）
```python
@dataclass
class UserRuntimeState:
    inferred_persona: str           # AI推断的人设
    inferred_aliases: list[str]     # AI推断的别名
    inferred_traits: list[str]      # AI推断的特质
    preference_tags: list[str]      # 偏好标签
    recent_messages: list[str]      # 最近消息（最多8条）
    summary_notes: list[str]        # 摘要笔记（最多8条）
    memory_facts: list[MemoryFact]  # 记忆事实（最多50条）
    # 观察特征集合（用于行为一致性分析）
    observed_keywords: set[str]
    observed_roles: set[str]
    observed_emotions: set[str]
    observed_entities: set[str]
```

### 2.2 用户记忆管理器（UserMemoryManager）

**核心数据结构（v0.28 群隔离设计）：**

```python
class UserMemoryManager:
    # {group_id: {user_id: UserMemoryEntry}}
    entries: dict[str, dict[str, UserMemoryEntry]]
    speaker_index: dict[str, str]      # 名称/别名 → user_id 快速查找
    identity_index: dict[str, str]    # channel:external_uid → user_id
```

#### 群隔离机制
- 每个群（`group_id`）拥有独立的用户记忆命名空间
- 同一用户在不同群中的记忆完全隔离
- 持久化路径：`{work_path}/user_memory/groups/{group_id}/{user_id}.json`

#### 用户注册与解析
1. `register_user(profile, group_id)`：将用户注册到指定群
2. `resolve_user_id(speaker, channel, external_user_id)`：多渠道解析用户身份
3. `ensure_user(speaker, group_id)`：懒创建未知用户
4. `remember_message(profile, content, group_id)`：记录用户消息并更新运行时状态

#### 事实置信度分层（C1 方案）
```python
# 阈值（默认 0.85）
transient_confidence_threshold = 0.85

# RESIDENT（高置信度）：confidence > threshold
# - 代表核心、稳定的用户特质和偏好
# - 持久化到磁盘（user.json）

# TRANSIENT（低置信度）：confidence <= threshold
# - 代表近期观察到的不确定信息
# - 存储在会话内存中，30分钟后自动清理
```

#### 智能上限管理
- `MAX_MEMORY_FACTS = 50`：每个用户的记忆事实上限
- 超出时**不按 FIFO 删除**，而是删除置信度最低的 **10%**
- 确保高价值记忆不会被简单的时间顺序淘汰

#### 特质标准化（B 方案）
- 使用 `TRAIT_TAXONOMY` 对特质进行标准化分类
- 匹配策略：精确词匹配 → 子串匹配
- 无法分类时保留原始文本，避免过度泛化

---

## 3. 记忆系统详解

### 3.1 三层记忆底座

#### 3.1.1 工作记忆（Working Memory）

**管理器**：`WorkingMemoryManager`

**核心特性**：
- 按群维护独立的内存滑动窗口：`{group_id: [WorkingMemoryEntry]}`
- 默认最大容量：`DEFAULT_MAX_SIZE = 20` 条
- 每条 entry 含动态 `importance` 和 `protected` 标记
- 名称经过 sanitized 处理

**窗口截断策略**：
1. **Protected 条目优先保留**：含关键个人信息（喜好、承诺、截止日期、危机信号等）或 importance ≥ 0.7
2. 正常条目按时间倒序保留（最新优先）
3. 被移除的高重要性条目（importance ≥ 0.3）自动**晋升到情景记忆**

**数据结构**：
```python
@dataclass
class WorkingMemoryEntry:
    entry_id: str
    group_id: str
    user_id: str
    role: str              # "human" | "assistant" | "system"
    content: str
    timestamp: str
    importance: float = 0.5
    protected: bool = False
    emotion_state: dict
    mentioned_user_ids: list[str]
    channel: str
    channel_user_id: str
    multimodal_inputs: list[dict]
```

#### 3.1.2 情景记忆（Episodic Memory）

**管理器**：`EpisodicMemoryManager`

**核心特性**：
- 按群存储为 `{work_path}/episodic/{group_id}.json`
- 存储结构化事件，含情绪标签、重要性、激活度
- 支持关键词搜索与批量归档
- 激活度低于阈值时移入 `{group_id}_archive.json`

**激活度计算**：
```
activation = importance × exp(-λ × hours) × (1 + γ × access_count)
```

**分类衰减系数（λ）**：
| 记忆类别 | λ 值 | 衰减特性 |
|---------|------|---------|
| identity / preference | 0.001 | 几乎永久 |
| emotion / transient | 0.05 | 数周衰减 |
| event / timely | 0.1 | 事件后快速衰减 |

#### 3.1.3 语义记忆（Semantic Memory）

**管理器**：`SemanticMemoryManager`

**存储结构**：
```
{work_path}/semantic/
├── users/
│   └── {group_id}_{user_id}.json    # 用户语义画像
└── groups/
    └── {group_id}.json              # 群体语义画像
```

**用户语义画像（UserSemanticProfile）**：
```python
@dataclass
class UserSemanticProfile:
    user_id: str
    base_attributes: dict              # 基础属性
    interest_graph: list[InterestNode] # 兴趣图谱（话题-参与度-深度）
    relationship_state: RelationshipState  # 双边关系指标
    taboo_boundaries: list[str]        # 禁忌边界
    important_dates: list[dict]        # 重要日期
    communication_style: str           # concise / detailed / formal / casual
    confirmed: bool
```

**关系状态指标（RelationshipState）**：
```python
@dataclass
class RelationshipState:
    interaction_frequency_7d: float   # 7天消息频率
    emotional_intimacy: float         # 情感亲密度
    trust_score: float                # 信任度（基于自我暴露深度）
    dependency_score: float           # 依赖度（求助频率）
    familiarity: float                # 综合熟悉度（自动计算）
    milestones: list[dict]            # 关系里程碑
```

**群体语义画像（GroupSemanticProfile）**：
```python
@dataclass
class GroupSemanticProfile:
    group_id: str
    atmosphere_history: list[AtmosphereSnapshot]  # 氛围历史（最多1000条）
    group_norms: dict                  # 群体规范（统计推断）
    interest_topics: list[str]
    typical_interaction_style: str     # active | lurker | controversial | balanced
    taboo_topics: list[str]
```

### 3.2 事件记忆 V2（Event Memory）

**管理器**：`EventMemoryManager`

**核心设计（v0.28 重大重构）**：
- **按用户缓冲**：每条 human 消息经 `_perception` 进入 `buffer_message(user_id, content, group_id)`
- **批量 LLM 提取**：后台 `_bg_memory_promoter` 每 5 分钟检查缓冲
- **去重合并**：字符集 Jaccard 相似度 ≥ 0.55 的观察自动合并
- **群隔离**：`EventMemoryEntry.group_id` 确保观察归属特定群

**缓冲策略**：
- 过短消息（< 6 字符）被丢弃
- 单用户最大缓冲：20 条
- 提取阈值：默认 5 条

**结构化观察（EventMemoryEntry）**：
```python
@dataclass
class EventMemoryEntry:
    event_id: str
    user_id: str
    group_id: str
    category: str           # preference | trait | relationship | experience | emotion | goal | custom
    summary: str            # ≤ 50 字
    confidence: float       # 0.0-1.0
    evidence_samples: list[str]  # 原始消息片段（最多4条）
    mention_count: int      # 合并次数
    verified: bool          # LLM提取的视为已验证
```

### 3.3 AI 自身记忆（Self Memory）

**管理器**：`SelfMemoryManager`

**双轨制设计**：

#### 日记子系统（Diary）
- 最多 100 条日记条目
- 每条含重要性、关键词、类别、置信度
- 遗忘曲线：按天衰减，高重要性衰减更慢
- Prompt 预算：最多 8 条高相关日记进入系统提示

#### 名词解释子系统（Glossary）
- 最多 200 个术语
- 自动合并：保留高置信度定义，合并用例
- 按 `confidence × usage_count` 淘汰低价值术语
- Prompt 预算：最多 20 个相关术语

### 3.4 激活度引擎（Activation Engine）

**公式**：
```
activation = importance × exp(-λ × hours_since_creation) × (1 + γ × access_count)
```

**分类衰减参数**：
| 类别 | λ | 说明 |
|------|---|------|
| core_preference | 0.001 | 姓名、住址等核心信息 |
| transient_state | 0.05 | 情绪、临时状态 |
| timely_info | 0.1 | 截止日期、新闻等时效信息 |
| default | 0.01 | 一般记忆 |

**访问增强**：每次检索访问增加 `γ = 0.1` 的 boost

**归档阈值**：activation < 0.1 → 移入归档文件

### 3.5 检索引擎（MemoryRetriever）

**四层检索策略**：

```python
async def retrieve(query, group_id, user_id, top_k=5):
    # 1. 工作记忆（最高优先级，关键词匹配）
    results = search_working_memory(group_id, query, user_id)
    
    # 2. 情景记忆关键词搜索
    results += search_episodic_keywords(group_id, query, user_id)
    
    # 3. 语义相似度（可选，需 sentence-transformers）
    if enable_semantic:
        results += await search_semantic(group_id, query, user_id)
    
    # 4. 用户语义画像查找
    if user_id:
        results += search_user_profile(group_id, user_id, query)
    
    return deduplicate_and_score(results, top_k)
```

**评分公式**：
```
score = importance × 0.4 + recency_score × 0.3 + activation × 0.3
recency_score = exp(-0.1 × days_since_creation)
```

### 3.6 记忆质量评估与遗忘引擎

**质量评分**：
```
quality_score = (confidence × 0.5 + recency_score × 0.3 + validation_bonus × 0.15)
                × (1 - conflict_penalty × 0.3)
```

**遗忘条件（满足任一）**：
1. 极低置信度（< 0.2）+ 陈旧（> 30天）
2. 有冲突 + 低置信度（< 0.4）+ 极旧（> 90天）
3. 质量评分 < 0.2 + 陈旧（> 60天）

**时间衰退表**：
| 天数 | 置信度保留比例 |
|------|--------------|
| 7 | 95% |
| 30 | 80% |
| 60 | 55% |
| 90 | 30% |
| 180 | 5% |

---

## 4. 核心引擎工作流程

### 4.1 主流程：`process_message()`

当一条群消息进入系统时，依次经过以下五个阶段：

```
消息进入
  │
  ▼
┌─────────────────────────────────────────────────────────┐
│ 1. 感知层 (Perception)                                   │
│    - 注册/更新参与者到 user_memory                        │
│    - 计算消息重要性，写入 working_memory                  │
│    - 缓冲原始消息到 event_memory                          │
│    - 更新 group_last_message_at                           │
└─────────────────────────────────────────────────────────┘
  │
  ▼
┌─────────────────────────────────────────────────────────┐
│ 2. 认知层 (Cognition)                                    │
│    - CognitionAnalyzer: 情感 + 意图 + 共情（单次LLM调用）  │
│    - MemoryRetriever: 三层记忆检索（working→episodic→semantic）│
└─────────────────────────────────────────────────────────┘
  │
  ▼
┌─────────────────────────────────────────────────────────┐
│ 3. 决策层 (Decision)                                     │
│    - RhythmAnalyzer: 热度/速度/话题稳定性                  │
│    - ThresholdEngine: 动态参与阈值（基础×活动×关系×时间）   │
│    - ResponseStrategyEngine: IMMEDIATE/DELAYED/SILENT     │
└─────────────────────────────────────────────────────────┘
  │
  ▼
┌─────────────────────────────────────────────────────────┐
│ 4. 执行层 (Execution)                                    │
│    - ResponseAssembler: 组装 PromptBundle                  │
│    - StyleAdapter: 动态适配 max_tokens/temperature/tone   │
│    - ModelRouter: 任务感知模型选择                        │
│    - LLM 生成回复                                        │
│    - 记录 assistant 回复到 working_memory                  │
└─────────────────────────────────────────────────────────┘
  │
  ▼
┌─────────────────────────────────────────────────────────┐
│ 5. 后台更新 (Background Update)                          │
│    - 更新群氛围 → semantic_memory.atmosphere_history      │
│    - 被动学习群规范 → semantic_memory.group_norms         │
│    - 更新用户情感轨迹                                     │
└─────────────────────────────────────────────────────────┘
```

### 4.2 感知层详解（`_perception`）

```python
def _perception(group_id, message, participants):
    # 1. 注册所有参与者到用户记忆（群隔离）
    for p in participants:
        user_memory.register_user(p.as_user_profile(), group_id)
    
    # 2. 计算消息动态重要性（基于内容长度、关键词、@提及等）
    importance = _compute_message_importance(message.content)
    
    # 3. 写入工作记忆（自动触发窗口管理）
    working_memory.add_entry(
        group_id=group_id,
        user_id=message.speaker,
        role="human",
        content=message.content,
        importance=importance,
        multimodal_inputs=message.multimodal_inputs,
    )
    
    # 4. 缓冲到事件记忆（用于后续批量观察提取）
    event_memory.buffer_message(
        user_id=message.speaker,
        content=message.content,
        group_id=group_id,
    )
    
    # 5. 更新群最后消息时间并持久化
    _group_last_message_at[group_id] = now_iso()
    _persist_group_state(group_id)
```

### 4.3 认知层详解（`_cognition`）

```python
async def _cognition(content, user_id, group_id):
    # 获取最近工作记忆作为上下文
    recent = _get_recent_messages(group_id, n=6)
    
    # 统一认知分析（emotion + intent + empathy，单次LLM调用）
    emotion, intent, empathy = await cognition_analyzer.analyze(
        content, user_id, group_id, recent
    )
    
    # 三层记忆检索
    memories = await memory_retriever.retrieve(
        query=content,
        group_id=group_id,
        user_id=user_id,
        top_k=5,
        enable_semantic=config.get("enable_semantic_retrieval", False),
    )
    
    return intent, emotion, memories, empathy
```

### 4.4 决策层详解（`_decision`）

```python
def _decision(intent, emotion, group_id, user_id):
    # 1. 节奏分析
    rhythm = rhythm_analyzer.analyze(group_id, recent_msgs)
    
    # 2. 获取用户语义画像中的关系状态
    user_profile = semantic_memory.get_user_profile(group_id, user_id)
    relationship_state = user_profile.relationship_state if user_profile else None
    
    # 3. 计算动态阈值
    threshold = threshold_engine.compute(
        sensitivity=config.get("sensitivity", 0.5),
        heat_level=rhythm.heat_level,
        messages_per_minute=_message_rate_per_minute(recent_msgs),
        relationship_state=relationship_state,
    )
    
    # 4. 人格回复频率偏置
    if persona.reply_frequency == "high":
        threshold *= 0.8
    elif persona.reply_frequency == "low":
        threshold *= 1.3
    elif persona.reply_frequency == "selective":
        if not intent.directed_at_current_ai and intent.urgency_score < 70:
            threshold *= 2.0
    
    # 5. 策略决策
    decision = strategy_engine.decide(
        intent,
        is_mentioned=intent.directed_at_current_ai,
        heat_level=rhythm.heat_level,
    )
    
    # 6. 冷却抑制（12秒内回复过则强制沉默）
    if seconds_since_reply < cooldown:
        decision = StrategyDecision(strategy=SILENT, ...)
    
    # 7. 更新助手情绪状态
    assistant_emotion.update_from_interaction(emotion, user_id)
    
    return decision
```

### 4.5 执行层详解（`_execution`）

#### IMMEDIATE（立即回复）策略：

```python
# 1. 构建响应组装所需的所有上下文
bundle = response_assembler.assemble(
    message=message,
    intent=intent,
    emotion=emotion,
    empathy_strategy=empathy,
    memories=memories,                    # 检索到的记忆
    group_profile=group_profile,          # 群体语义画像
    user_profile=user_profile,            # 用户语义画像
    assistant_emotion=assistant_emotion,  # 助手当前情绪
    heat_level=rhythm.heat_level,
    pace=rhythm.pace,
    recent_participants=recent_participants,  # 群成员身份信息
    caller_is_developer=caller_is_developer,
    glossary_section=glossary,            # AI自身记忆-名词解释
)

# 2. 风格适配
style = style_adapter.adapt(
    heat_level=rhythm.heat_level,
    pace=rhythm.pace,
    user_communication_style=user_profile.communication_style,
)

# 3. 构建历史消息（从 working_memory 转换）
history = _build_history_messages(group_id, n=10)
messages = history + [{"role": "user", "content": bundle.user_content}]

# 4. 多轮生成（支持 SKILL 调用）
for round in range(max_skill_rounds + 1):
    raw_reply = await _generate(bundle.system_prompt, messages, group_id, style)
    calls = parse_skill_calls(raw_reply)
    if not calls:
        break
    # 执行 SKILL，将结果注入对话，重新生成
    ...

# 5. 记录 assistant 回复到 working_memory
working_memory.add_entry(
    group_id=group_id,
    user_id="assistant",
    role="assistant",
    content=clean_reply,
    importance=0.6,
)
```

#### DELAYED（延迟回复）策略：
- 将消息和决策加入 `DelayedResponseQueue`
- 由后台 `_bg_delayed_queue_ticker` 每 10 秒检查话题间隙
- 间隙检测通过后触发 `tick_delayed_queue()` 生成回复

#### SILENT（沉默）策略：
- 不生成回复
- 仅更新记忆和后台状态

### 4.6 后台更新层详解（`_background_update`）

```python
def _background_update(group_id, message, emotion, intent):
    # 1. 更新群氛围历史
    group_profile = semantic_memory.ensure_group_profile(group_id)
    group_profile.atmosphere_history.append(AtmosphereSnapshot(
        timestamp=now_iso(),
        group_valence=emotion.valence,
        group_arousal=emotion.arousal,
    ))
    
    # 2. 更新群体情感缓存（用于情绪孤岛检测）
    cognition_analyzer.update_group_sentiment(group_id, emotion)
    
    # 3. 被动学习群规范
    _learn_group_norms(group_profile, message, intent)
    
    # 统计项：
    # - avg_message_length（滚动平均）
    # - emoji_usage_rate（表情使用率）
    # - mention_rate（@提及率）
    # - active_hours（活跃时段直方图）
    # - topic_switch_frequency（话题切换频率）
    # - typical_interaction_style（推断：active/humorous/formal/balanced）
```

---

## 5. 记忆系统与用户系统的关联性

### 5.1 数据关联矩阵

| 功能场景 | 用户系统参与 | 记忆系统参与 | 关联方式 |
|---------|-----------|-----------|---------|
| **用户身份识别** | UserMemoryManager.resolve_user_id() | - | speaker_index + identity_index |
| **消息进入** | register_user() | working_memory.add_entry() | user_id 作为外键关联 |
| **认知分析** | UserMemoryEntry.profile | memory_retriever.retrieve() | 语义画像提供关系状态 |
| **策略决策** | semantic_memory.get_user_profile() | threshold_engine.compute() | relationship_state 影响阈值 |
| **回复生成** | user_profile, recent_participants | memories, glossary | 组装 Prompt 时融合多源信息 |
| **观察提取** | user_memory.get_user_by_id() | event_memory.extract_observations() | 提取后镜像到 episodic_memory |
| **语义整合** | semantic_memory.save_user_profile() | event_memory.entries | 观察按 category 更新用户画像 |
| **氛围更新** | - | semantic_memory.append_atmosphere() | 基于 emotion 更新群体画像 |
| **规范学习** | - | semantic_memory.group_norms | 基于 message + intent 统计推断 |
| **持久化** | UserMemoryFileStore | EngineStateStore | 统一在 save_state()/load_state() 中协调 |

### 5.2 关键关联路径详解

#### 路径 A：消息进入 → 用户识别 → 记忆写入
```
Message(speaker, channel_user_id)
    │
    ├─→ user_memory.resolve_user_id(speaker, channel, external_user_id)
    │       └─→ 返回 user_id（或创建新用户）
    │
    ├─→ user_memory.register_user(profile, group_id)
    │       └─→ 更新 entries[group_id][user_id]
    │
    ├─→ working_memory.add_entry(group_id, user_id, role="human", ...)
    │       └─→ 按群滑动窗口管理，高重要性晋升到 episodic
    │
    └─→ event_memory.buffer_message(user_id, content, group_id)
            └─→ 累积到批量提取缓冲
```

#### 路径 B：认知检索 → 用户画像 → 决策阈值
```
MemoryRetriever.retrieve(query, group_id, user_id)
    │
    ├─→ 工作记忆关键词匹配（user_id 过滤）
    ├─→ 情景记忆关键词搜索（user_id 过滤）
    ├─→ 语义相似度搜索（可选）
    └─→ 用户语义画像查找（_search_user_profile）
            └─→ base_attributes + interest_graph

ThresholdEngine.compute(relationship_state)
    └─→ relationship_state.familiarity 影响阈值
```

#### 路径 C：后台整合 → 观察聚合 → 画像更新
```
_bg_consolidator() 每10分钟
    │
    ├─→ 收集 event_memory.entries（近7天、已验证、按群过滤）
    │
    ├─→ 按 user_id 聚合观察统计
    │       ├── emotion_count → emotional_intimacy
    │       ├── relationship_count → trust_score
    │       └── preference/trait/goal → base_attributes
    │
    └─→ semantic_memory.save_user_profile(group_id, profile)
```

#### 路径 D：回复生成 → 多源信息融合
```
ResponseAssembler.assemble()
    │
    ├─→ 人格（persona）→ system_prompt 基础
    ├─→ 用户语义画像（user_profile）→ 个性化语境
    ├─→ 群体画像（group_profile）→ 群氛围/规范
    ├─→ 检索记忆（memories）→ 相关历史
    ├─→ 助手情绪（assistant_emotion）→ 情绪状态
    ├─→ 名词解释（glossary）→ AI自身知识
    └─→ 群成员身份（recent_participants）→ @提及解析
```

---

## 6. 群隔离机制

### 6.1 全链路群隔离

所有记忆子系统均按 `group_id` 实现物理/逻辑隔离：

| 子系统 | 隔离方式 | 存储路径/结构 |
|-------|---------|-------------|
| **用户记忆** | 逻辑隔离 | `entries: {group_id: {user_id: UserMemoryEntry}}` |
| **工作记忆** | 逻辑隔离 | `_windows: {group_id: [WorkingMemoryEntry]}` |
| **情景记忆** | 物理隔离 | `{work_path}/episodic/{group_id}.json` |
| **语义记忆-用户** | 物理隔离 | `{work_path}/semantic/users/{group_id}_{user_id}.json` |
| **语义记忆-群体** | 物理隔离 | `{work_path}/semantic/groups/{group_id}.json` |
| **事件记忆** | 字段隔离 | `EventMemoryEntry.group_id` |
| **持久化状态** | 逻辑隔离 | `working_memories: {group_id: [entry_dict]}` |

### 6.2 跨群边界保证

- `user_memory.entries` 不允许跨群读取（除非显式遍历所有群）
- `working_memory.get_window(group_id)` 仅返回指定群窗口
- `episodic_memory.get_entries(group_id)` 仅从指定群文件读取
- `semantic_memory.get_user_profile(group_id, user_id)` 按群前缀加载
- `event_memory.entries` 通过 `group_id` 字段过滤

---

## 7. 后台任务调度

### 7.1 任务列表

```python
start_background_tasks():
    ├─ _bg_delayed_queue_ticker()      # 每 10 秒
    ├─ _bg_proactive_checker()         # 每 60 秒
    ├─ _bg_memory_promoter()           # 每 300 秒（5分钟）
    └─ _bg_consolidator()              # 每 600 秒（10分钟）
```

### 7.2 延迟队列检查器（`_bg_delayed_queue_ticker`）

- 遍历所有活跃群（`_group_last_message_at` 中的群）
- 调用 `delayed_queue.get_pending(group_id)` 获取可触发项
- 触发 `DELAYED_RESPONSE_TRIGGERED` 事件
- **注意**：实际回复生成由外部调用者（如 QQ 插件）通过 `tick_delayed_queue()` 完成

### 7.3 主动触发检查器（`_bg_proactive_checker`）

- 遍历所有活跃群
- 调用 `proactive_check(group_id)` 检查沉默时间/记忆触发/情感触发
- 条件满足时生成主动消息并记录到 working_memory

### 7.4 记忆提升器（`_bg_memory_promoter`）

- 检查 `event_memory.pending_buffer_counts()`
- 对达到 `batch_size`（默认5）的用户：
  1. 通过 `ModelRouter` 获取提取任务配置
  2. 调用 `event_memory.extract_observations(user_id, user_name, provider_async, ...)`
  3. 将提取结果镜像到 `episodic_memory.add_event(...)`

### 7.5 语义整合器（`_bg_consolidator`）

- 遍历所有有工作记忆的群
- 调用 `_consolidate_group(group_id)`：
  1. 优先使用 event_memory v2 的结构化观察（近7天）
  2. 无 v2 观察时回退到 episodic 原始事件统计
  3. 按 category 聚合更新 `UserSemanticProfile`
  4. 更新 `RelationshipState`（频率、亲密度、信任度、依赖度、熟悉度）

---

## 8. 持久化与恢复机制

### 8.1 实时持久化

```python
# 每次消息处理后触发
_persist_group_state(group_id):
    ├─→ _state_store.save_working_memory(group_id, recent_entries)
    └─→ _state_store.save_group_timestamps(_group_last_message_at)
```

### 8.2 全量持久化（`save_state()`）

```python
_persist_full_state():
    ├─ working_memories: {group_id: [entry_dict]}      # 所有群工作记忆
    ├─ assistant_emotion: dict                         # 助手情绪状态
    ├─ delayed_queue: []                               # 延迟队列（简化）
    ├─ group_timestamps: dict                          # 群最后消息时间
    ├─ token_usage_records: list                       # Token使用记录
    ├─ event_memory: dict                              # 事件记忆 v2（含缓冲）
    └─ proactive_state: dict                           # 主动触发状态
```

### 8.3 恢复流程（`load_state()`）

```python
load_state():
    ├─ 恢复 working_memory（按群重建）
    ├─ 恢复 assistant_emotion
    ├─ 恢复 group_timestamps（重置为当前时间，避免离线时间误判）
    ├─ 恢复 event_memory（含缓冲和已提取观察）
    └─ 恢复 proactive_state
    
# 注意：user_memory、episodic_memory、semantic_memory 的持久化
# 由各自的 FileStore 独立管理，不在 EngineStateStore 中
```

### 8.4 独立持久化组件

| 组件 | 存储类 | 路径 |
|-----|-------|------|
| 用户记忆 | `UserMemoryFileStore` | `{work_path}/user_memory/groups/{group_id}/{user_id}.json` |
| 情景记忆 | `EpisodicMemoryManager`（自管理） | `{work_path}/episodic/{group_id}.json` |
| 语义记忆 | `SemanticMemoryManager`（自管理） | `{work_path}/semantic/users/` 和 `groups/` |
| 引擎状态 | `EngineStateStore` | `{work_path}/engine_state/` |

---

## 9. 完整数据流示例

以用户 "Alice" 在群 "group_001" 发送消息 "我最近在看《三体》，感觉特别震撼" 为例：

### 阶段 1：感知层
```
1. user_memory.register_user(Alice_profile, "group_001")
   → entries["group_001"]["alice"] = UserMemoryEntry

2. working_memory.add_entry("group_001", "alice", "human", content, importance=0.6)
   → group_001 窗口追加条目，如超限触发截断

3. event_memory.buffer_message("alice", content, "group_001")
   → _buffer["alice"] = [("group_001", "我最近在看《三体》...")]

4. _group_last_message_at["group_001"] = "2026-04-22T17:20:10"
```

### 阶段 2：认知层
```
5. cognition_analyzer.analyze(content, "alice", "group_001", recent)
   → EmotionState(valence=0.4, arousal=0.5, basic_emotion="excited")
   → IntentAnalysisV3(social_intent="sharing", urgency=30, directed_at_ai=False)
   → EmpathyStrategy(type="enthusiasm", intensity=0.6)

6. memory_retriever.retrieve(query=content, group_id="group_001", user_id="alice")
   → working: ["上周 Alice 提过喜欢科幻"]
   → episodic: ["Alice 分享过《流浪地球》观后感"]
   → semantic: [兴趣节点: "科幻文学", 参与度=0.8]
```

### 阶段 3：决策层
```
7. rhythm_analyzer.analyze("group_001", recent)
   → heat_level="warm", pace="steady", topic_stability=0.7

8. threshold_engine.compute(sensitivity=0.5, heat_level="warm", ...)
   → threshold = 0.45

9. strategy_engine.decide(intent, is_mentioned=False, heat_level="warm")
   → StrategyDecision(strategy=SILENT, score=0.32, threshold=0.45)
   → 理由：未@AI，urgency < threshold，日常分享
```

### 阶段 4：执行层
```
10. 因策略为 SILENT，不生成回复
11. 但仍记录 assistant_emotion.update_from_interaction(emotion, "alice")
```

### 阶段 5：后台更新
```
12. semantic_memory.ensure_group_profile("group_001")
    → atmosphere_history.append(AtmosphereSnapshot(valence=0.4, arousal=0.5))

13. _learn_group_norms(group_profile, message, intent)
    → avg_message_length 更新
    → topic_switch_frequency 更新
    → active_hours["17"] += 1

14. 5分钟后，_bg_memory_promoter 检查：
    → Alice 的缓冲达到 5 条
    → 调用 extract_observations("alice", "Alice", provider_async, ...)
    → LLM 返回：[{"category": "preference", "content": "喜欢科幻文学", "confidence": 0.85}]
    → event_memory.entries 追加 EventMemoryEntry
    → 镜像到 episodic_memory.add_event("group_001", "alice", "喜欢科幻文学", importance=0.85)

15. 10分钟后，_bg_consolidator 检查：
    → 读取 Alice 近7天的 event_memory 观察
    → 发现 preference 类别观察 "喜欢科幻文学"
    → semantic_memory.save_user_profile("group_001", updated_profile)
    → updated_profile.interest_graph.append(InterestNode("科幻文学", 0.8, 0.7))
```

---

## 10. 设计亮点与关键决策

### 10.1 群隔离（Group Isolation）
- **问题**：多群场景下用户记忆、对话历史、群体氛围互相干扰
- **方案**：所有记忆子系统按 `group_id` 实现物理/逻辑双重隔离
- **收益**：支持同一用户在不同群中拥有独立画像和关系状态

### 10.2 事实置信度分层（Confidence Tiers）
- **问题**：所有记忆同等对待导致噪声累积
- **方案**：`transient_confidence_threshold=0.85` 将事实分为 RESIDENT（持久化）和 TRANSIENT（会话级，30分钟清理）
- **收益**：高价值记忆长期保留，低置信度信息自动衰减

### 10.3 批量 LLM 观察提取（Batch Observation Extraction）
- **问题**：逐消息提取成本高、上下文碎片化
- **方案**：`event_memory.buffer_message()` 累积 + `extract_observations()` 批量 LLM 提取
- **收益**：降低 API 调用次数，提取质量更高（有上下文支撑）

### 10.4 智能上限管理（Smart Cap）
- **问题**：简单 FIFO 可能删除高价值旧记忆
- **方案**：`MAX_MEMORY_FACTS=50`，超出时删除置信度最低的 10%
- **收益**：保留高价值记忆，淘汰低质量信息

### 10.5 动态阈值引擎（Dynamic Threshold Engine）
- **问题**：静态回复阈值无法适应不同群氛围和用户关系
- **方案**：`threshold = Base × Activity × Relationship × Time`
- **收益**：热群降低响应频率避免刷屏，亲密关系提高响应灵敏度

### 10.6 多轮 SKILL 执行（Multi-round Skill Execution）
- **问题**：SKILL 调用阻塞对话流
- **方案**：生成 → 检测 SKILL_CALL → 执行 → 结果注入 → 重新生成（最多 3 轮）
- **收益**：SKILL 结果自然融入回复，支持链式调用

---

## 11. 模块文件索引

| 模块 | 文件路径 |
|-----|---------|
| 核心引擎 | `sirius_chat/core/emotional_engine.py` |
| 用户数据模型 | `sirius_chat/memory/user/models.py` |
| 用户记忆管理器 | `sirius_chat/memory/user/manager.py` |
| 用户记忆存储 | `sirius_chat/memory/user/store.py` |
| 事件记忆管理器 | `sirius_chat/memory/event/manager.py` |
| 事件记忆模型 | `sirius_chat/memory/event/models.py` |
| 事件记忆存储 | `sirius_chat/memory/event/store.py` |
| 工作记忆管理器 | `sirius_chat/memory/working/manager.py` |
| 工作记忆模型 | `sirius_chat/memory/working/models.py` |
| 情景记忆管理器 | `sirius_chat/memory/episodic/manager.py` |
| 语义记忆管理器 | `sirius_chat/memory/semantic/manager.py` |
| 语义记忆模型 | `sirius_chat/memory/semantic/models.py` |
| AI自身记忆管理器 | `sirius_chat/memory/self/manager.py` |
| AI自身记忆模型 | `sirius_chat/memory/self/models.py` |
| 激活度引擎 | `sirius_chat/memory/activation_engine.py` |
| 检索引擎 | `sirius_chat/memory/retrieval_engine.py` |
| 质量评估与遗忘 | `sirius_chat/memory/quality/models.py` |
| 参与者模型 | `sirius_chat/models/models.py` |

---

*报告结束*
