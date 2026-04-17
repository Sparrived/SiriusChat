# 情感分析与意图分析

> **认知层核心组件** — 让引擎"读懂"消息背后的情绪和目的。

## 一句话定位

情感分析回答"**对方现在什么心情**"，意图分析回答"**对方说这话想干什么**"。两者合起来决定引擎应该用什么态度回应。

---

## 情感分析（EmotionAnalyzer）

### 模型

采用 **2D valence-arousal 情绪模型**：

```
        高唤醒
           │
    兴奋 ←─┼─→ 愤怒
    (0.7,0.8)  (-0.6,0.8)
           │
低愉悦 ────┼──── 高愉悦
           │
    悲伤 ←─┼─→ 满足
    (-0.5,-0.2) (0.6,-0.1)
           │
        低唤醒
```

- **valence（愉悦度）**：-1（极负面）~ +1（极正面）
- **arousal（唤醒度/紧张度）**：0（平静）~ 1（激动）
- **intensity（强度）**：0~1，表示情绪的明显程度

### 19 种基本情绪映射

从 valence-arousal 坐标映射到自然语言情绪标签：

| 情绪 | valence | arousal | 典型触发 |
|------|---------|---------|---------|
| joy | >0.5 | >0.3 | 好消息、被夸奖 |
| anger | <-0.3 | >0.5 | 被冒犯、不公平 |
| sadness | <-0.3 | <0.3 | 失落、告别 |
| fear | <-0.3 | >0.6 | 威胁、未知 |
| disgust | <-0.4 | 0.2~0.6 | 厌恶、反感 |
| surprise | ~0 | >0.5 | 意外信息 |
| trust | >0.4 | 0.2~0.5 | 被倾诉秘密 |
| anticipation | 0.2~0.6 | 0.3~0.6 | 期待、计划 |
| neutral | ~0 | ~0 | 信息陈述 |

### 分析方式：规则为主，LLM 兜底

**第一层：规则引擎**

基于中文情感词典和模式匹配快速判定：
- 关键词匹配："开心""爽"→ 高 valence；"烦""气"→ 低 valence
- 否定翻转："不开心" = 低 valence，不是中性
- 程度修饰："超级开心" valence 比 "有点开心" 更高
- 标点信号：连续 "!!!" → arousal 上升；"..." → arousal 下降

**第二层：LLM fallback**

当规则引擎判定 confidence < 0.6 时，调用轻量模型（gpt-4o-mini）做精细分析。LLM 被要求输出结构化 JSON：
```json
{"valence": 0.3, "arousal": 0.7, "basic_emotion": "anger", "intensity": 0.8}
```

### 情感轨迹

引擎会跟踪每个用户在一段时间内的情感变化，形成**情感轨迹**（trajectory）。这用于：
- 检测情感孤岛（某个用户长时间情绪低落，可能需要关心）
- 为 `ResponseStrategyEngine` 提供上下文（"这个人最近一周都很丧"）

### 群体情感聚合

每个群聊有一个**群体氛围快照**（`AtmosphereSnapshot`）：
- `group_valence`：群整体愉悦度
- `group_arousal`：群整体活跃度
- `heat_level`：cold / warm / hot / overheated

快照在每条消息处理后更新，存入语义记忆的 `atmosphere_history`（保留最近 1000 条）。

---

## 意图分析 v3（IntentAnalyzerV3）

### 从"分类"到"目的驱动"

传统意图分析是分类任务（"这句话属于哪一类"）。v3 改为**目的驱动**——不问"这是什么"，而问"对方想要什么"。

### 四大意图类别

| 意图 | 含义 | 典型句式 | urgency 基线 |
|------|------|---------|-------------|
| **help_seeking** | 求助 | "有人知道这个怎么弄吗" | 60 |
| **emotional** | 情感表达 | "今天好烦""太开心了" | 50 |
| **social** | 社交互动 | "哈哈哈""同意" | 20 |
| **silent** | 无明确目的 | "转发了一条新闻" | 10 |

### 量化评分

每条消息产出三个分数：

- **`urgency_score`**（0~100）：有多急。"救命"→ 90；"求助"→ 60；"闲聊"→ 10
- **`relevance_score`**（0~1）：和我（助手）有多相关。被 @ 时接近 1.0；提到我名字时 0.8；完全无关时 <0.2
- **`directed_at_current_ai`**（bool）：是否明确指向我

### 分析方式：规则为主，LLM 兜底

和情感分析一样，先走规则：
- 问号数量 → urgency 上升
- "有人""求助""怎么" → help_seeking
- 表情符号密度 → social
- 被 @ → directed_at_current_ai = True

规则 confidence < 0.6 时调用 LLM（gpt-4o-mini）输出结构化结果。

---

## 两者如何协作

```
消息进来
    │
    ├──→ EmotionAnalyzer → EmotionState
    │                        (valence, arousal, basic_emotion)
    │
    └──→ IntentAnalyzerV3 → IntentAnalysisV3
                             (intent_type, urgency, relevance, directed_at_me)
    │
    ▼
[决策层]
    ├── urgency > 80 + directed_at_me → IMMEDIATE（升级模型）
    ├── emotional + valence < -0.3 → 共情策略 = confirm_action
    ├── social + relevance < 0.2 → SILENT（群里闲聊，不插话）
    └── ...
```

**共情策略选择**：情感分析的结果直接决定 `EmpathyStrategy`：

| 用户情绪 | 共情策略 | 说明 |
|---------|---------|------|
| 负面 + 高 arousal（愤怒/焦虑） | `confirm_action` | 先确认感受，再提建议 |
| 负面 + 低 arousal（悲伤/失落） | `presence` | 安静陪伴，不过度干预 |
| 正面（开心/兴奋） | `share_joy` | 积极回应，放大正面情绪 |
| 困惑/不确定 | `cognitive` | 帮助重新理解情境 |
| 明确求助 | `action` | 提供具体可行的帮助 |

这些策略作为 `[共情策略]` 区块注入执行层的 prompt，指导回复的语气深度和内容方向。

---

## 为什么用"规则为主，LLM 兜底"

1. **成本**：90% 的消息用规则就能判定准确，不需要花钱调 LLM
2. **延迟**：规则分析是本地计算，微秒级；LLM 分析是网络调用，百毫秒级
3. **可控性**：规则的行为是可预测的，不会"创造性发挥"
4. **兜底**：规则覆盖不到的边缘情况（讽刺、双关、文化梗）交给 LLM

规则引擎和 LLM 的结果会融合：如果两者分歧，取置信度高的；如果 LLM 置信度显著高于规则，以 LLM 为准。
