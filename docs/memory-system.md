# 记忆系统（Memory System）

> **v0.28 三层记忆底座** — 工作记忆 → 情景记忆 → 语义记忆

## 一句话定位

记忆系统负责让引擎**记得住上下文、回忆得起往事、理解得了关系**。它不是简单的聊天记录存储，而是一套从"当下感知"到"长期认知"的分层筛选与沉淀机制。

## 为什么要分层

如果所有聊天记录都塞给 LLM，很快就会爆 token。如果只保留最近20条，重要的信息（比如用户说"我讨厌香菜"）会在几轮对话后被遗忘。三层记忆解决了这个矛盾：

| 层级 | 保留什么 | 遗忘策略 | 查询方式 |
|------|---------|---------|---------|
| **工作记忆** | 最近对话上下文 | 滑动窗口溢出时丢弃低重要性内容 | 直接读取 |
| **情景记忆** | 具体事件、偏好、关系变化 | 激活度衰减（艾宾浩斯曲线） | 关键词 + 语义相似度 |
| **语义记忆** | 用户画像、群体氛围、关系状态 | 长期保留，定期合并更新 | 画像字段直接读取 |

## 工作记忆（Working Memory）

**定位**：短期注意力窗口，纯粹内存中的热数据。

每个群聊有自己独立的窗口（默认最多20条）。窗口中的每条记录是一个 `WorkingMemoryEntry`，包含：
- 谁说的、说了什么、什么时候
- 情绪状态（valence/arousal）
- 重要性评分（0~1）
- 是否受保护（敏感信息不会被挤出窗口）

**窗口溢出时的行为**：
1. 按 `(受保护 > 重要性 > 时间)` 排序，保留前20条
2. 被挤出且 `importance ≥ 0.3` 的条目 → **晋升到情景记忆**
3. 被挤出且 `importance < 0.3` 的条目 → **直接丢弃**

**受保护规则**：包含"喜欢""讨厌""deadline""自杀"等关键词，或 `importance ≥ 0.7` 的条目不会被挤出。这是一个兜底的安全网——高风险的对话内容不会因为窗口溢出而丢失。

**持久化**：工作记忆在引擎重启后会从 `engine_state/working_memory.json` 恢复，但本质上是临时的，长期留存靠情景记忆。

## 情景记忆（Episodic Memory）

**定位**：中期事件库，按群聊分组存储的具体事件。

存储格式是**追加式 JSONL**（每行一个 JSON 对象），路径为 `{work_path}/episodic/{group_id}.jsonl`。

每条事件包含：
- `event_id`, `user_id`, `group_id`
- `category`: preference / trait / relationship / experience / emotion / goal / custom
- `summary`: 事件摘要（自然语言）
- `confidence`: 置信度（0~1）
- `evidence_samples`: 支撑证据片段
- `activation`: 激活度（0~1，动态衰减）
- `access_count`: 被检索次数

**激活度衰减公式**：
```
activation = importance × exp(-λ × 小时数) × (1 + 0.1 × 访问次数)
```

不同类别的衰减速度不同：
- `identity`/`preference`：λ=0.001（几乎永久）
- `emotion`/`transient`：λ=0.05（几周后淡化）
- `event`/`timely`：λ=0.1（事件结束后快速淡化）

当 `activation < 0.1` 时，事件被移入 `{group_id}_archive.jsonl`，不再参与日常检索，但并未删除。

**为什么用 JSONL 而不是数据库**：
- 追加写极快，不需要事务
- 人类可读，方便调试
- 按群聊分文件，天然隔离

## 语义记忆（Semantic Memory）

**定位**：长期认知层，从大量情景事件中提炼出的**结构化画像**。

### 用户画像（UserSemanticProfile）

每个 `(group_id, user_id)` 对应一个 JSON 文件 `{work_path}/semantic/users/{group_id}_{user_id}.json`：

```json
{
  "user_id": "u123",
  "base_attributes": {"职业": "程序员", "所在地": "北京"},
  "interest_graph": [
    {"topic": "原神", "participation": 0.8, "depth": 0.6}
  ],
  "relationship_state": {
    "interaction_frequency_7d": 12,
    "emotional_intimacy": 0.4,
    "trust_score": 0.3,
    "familiarity": 0.35
  },
  "communication_style": "casual",
  "taboo_boundaries": ["前女友"]
}
```

### 群体画像（GroupSemanticProfile）

每个 `group_id` 对应一个 JSON 文件 `{work_path}/semantic/groups/{group_id}.json`：

```json
{
  "group_id": "g456",
  "atmosphere_history": [
    {"timestamp": "...", "group_valence": 0.3, "heat_level": "warm"}
  ],
  "group_norms": {
    "avg_message_length": 15,
    "emoji_usage_rate": 0.3,
    "mention_rate": 0.1
  },
  "typical_interaction_style": "humorous"
}
```

**群体规范学习（被动学习）**：
- `avg_message_length`：滚动平均消息长度
- `emoji_usage_rate`：含 emoji 消息占比
- `mention_rate`：@人 消息占比
- `active_hours`：活跃时段分布
- `topic_switch_frequency`：话题切换频率
- `typical_interaction_style`：从氛围历史中推断（active / humorous / formal / balanced / lurker / controversial）

这些数据被 `StyleAdapter` 用来调整回复风格——如果群里人均 5 个字带三个 emoji，你回一大段正经文字就会显得很突兀。

## 记忆检索（MemoryRetriever）

**定位**：统一查询接口，把三层记忆整合成一个有序的结果列表。

检索流程（`retrieve()`）：
1. **工作记忆层**：关键词子串匹配，激活度固定为 1.0（最近的内容永远最"热"）
2. **情景记忆层**：关键词搜索 + 可选的语义相似度（sentence-transformers）
3. **用户画像层**：如果提供了 `user_id`，搜索其 `base_attributes` 和 `interest_graph`

**综合评分**：
```
score = importance × 0.4 + recency × 0.3 + activation × 0.3
recency = exp(-0.1 × 天数)
```

去重后按 score 排序，默认返回 top 5。

**语义搜索是可选依赖**：如果 `sentence-transformers` 未安装，系统完全退化为纯关键词检索，不影响功能。

## 自传体记忆（Autobiographical Memory）v0.28+

**定位**：AI 的"第一人称日记"——不是关于用户的事实，而是关于"我自己经历了什么"的记录。

**来源**：Emotional Engine 的 `<think>` 输出（模型的内心独白）被解析后存入自传体记忆。

**组成**：
- `SelfSemanticProfile`：AI 的自我概念（"我是谁"、核心价值观、情绪时间线）
- `DiaryEntry`：第一人称体验记录，带价值加权重要性评分
- `emotion_timeline`：AI 自身的情绪变化轨迹
- `Glossary`：术语表，记录群聊中出现的俚语、黑话、专有名词及 AI 对其的理解

### 自传体记忆 glossary

**定位**：AI 对自身语言环境的"活字典"——不是通用百科，而是"这个群里的人把 X 叫做 Y"的局部知识。

**添加方式**：
1. **SKILL 调用**：内置 `learn_term` 技能被触发时，将用户提到的术语/俚语/黑话通过 `add_glossary_term()` 写入自传体记忆。该技能标记为 `silent=True`，执行结果不会出现在回复文本中。
2. **引擎自动提取**：`ResponseAssembler` 在组装 prompt 时，若检测到当前群存在已积累的 glossary 条目，可通过 `glossary_section` 参数将其注入系统提示词。

**条目结构**：
- `term`：术语原文
- `definition`：AI 对其的理解或解释
- `confidence`：置信度。用户明确提供的解释 → `0.9`；AI 自行推断的 → `0.6`
- `source_group_id`：来源群聊（群隔离）
- `added_at`：添加时间

**使用方式**：
- `build_glossary_prompt_section()` 将 glossary 格式化为 prompt 区块，供 `ResponseAssembler` 在生成**立即回复、延迟回复、主动回复**时注入。
- 低置信度条目可在后续对话中被用户纠正，从而提升置信度或更新定义。

**持久化**：glossary 作为自传体记忆的一部分，随 `self_memory` 持久化到磁盘，引擎重启后自动恢复。

**重要性评分**（零 LLM 成本）：
```
importance = 0.5 + value_resonance(0~0.3) + emotional_intensity(0~0.2)
```
- 与 persona 核心价值观共鸣的内容 → 更高重要性
- 情绪强度高的内容 → 更高重要性

**与其他记忆的区别**：
| 维度 | 自传体记忆 | 语义记忆 |
|------|-----------|---------|
| 主体 | AI 自己 | 用户 |
| 内容 | "我觉得..." "我担心..." | "用户喜欢..." "用户讨厌..." |
| 人称 | 第一人称 | 第三人称 |
| 用途 | 塑造"自我"连续性 | 个性化回复 |

## 数据流转全景

```
新消息进来
    │
    ▼
[工作记忆] 加入窗口，分配 importance
    │
    ▼ 窗口溢出
[情景记忆] 高 importance 条目晋升，追加 JSONL
    │
    ▼ 每 10 分钟（后台任务）
[激活度引擎] 重新计算 activation，低于 0.1 的归档
    │
    ▼ 每 10 分钟（后台任务）
[语义记忆] 聚合最近7天事件，更新用户画像 + 群体画像
    │
    ▼ 回复生成时
[记忆检索] 工作 + 情景 + 画像 + 自传体 → top_k 结果 → 注入 prompt
    │
    ▼ 回复输出后
[自传体记忆] <think> 内容 → DiaryEntry + 情绪时间线
```

## 群聊隔离

v0.28 最重要的设计决策之一：**所有记忆层级都按 `group_id` 隔离**。

- 工作记忆：`{group_id: [entries]}`
- 情景记忆：`{group_id}.jsonl`
- 语义记忆：`users/{group_id}_{user_id}.json`, `groups/{group_id}.json`
- 迁移脚本自动把旧格式（不分群）拆分到新布局

这意味着你在群 A 聊的秘密不会泄露到群 B 的回复中。

## 与其他系统的关系

| 交互对象 | 方式 |
|---------|------|
| **EmotionalGroupChatEngine** | 引擎持有所有记忆管理器实例，调用 `add_entry()` / `retrieve()` |
| **Persona** | 语义记忆中的 `communication_style` 影响 `StyleAdapter` 的参数选择 |
| **Background Tasks** | 4 个后台任务中的 2 个（memory promoter + consolidator）专门维护记忆系统 |
| **LLM Provider** | 检索结果作为 `[相关记忆]` 区块注入生成 prompt |
