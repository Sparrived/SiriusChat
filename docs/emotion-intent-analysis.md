# 认知层：统一情绪与意图分析

> **认知层核心组件** — 让引擎"读懂"消息背后的情绪和目的。

## 一句话定位

情感分析回答"**对方现在什么心情**"，意图分析回答"**对方说这话想干什么**"。v0.28+ 中，两者由 `CognitionAnalyzer` **统一推断**——共享上下文、共享规则引擎、共享 LLM fallback，消除异步边界。

---

## 统一认知分析器（CognitionAnalyzer）

### 设计动机

旧架构中 `EmotionAnalyzer` 和 `IntentAnalyzerV3` 是独立的异步调用：

```
旧流程： emotion = await analyze_emotion(msg)
         intent = await analyze_intent(msg, emotion_state=emotion)
```

问题：
- 两次异步调用增加延迟
- 两次独立规则引擎重复扫描文本
- 两次独立 LLM fallback（2× token 成本）

新架构统一为单层分析：

```
新流程： emotion, intent, empathy = await analyze(msg)
```

### 三层架构

**第一层：联合规则引擎（零 LLM 成本，~90% 命中率）**

同时扫描消息文本：
- 情感词典匹配 → valence / arousal / intensity
- 意图模式匹配 → social_intent / subtype
- 紧急度关键词 → urgency_score
- 共享上下文 → 情感轨迹、群体氛围

**第二层：单次 LLM fallback（~10% 命中率）**

当规则引擎对情绪或意图的置信度不足时，**一次性**调用轻量模型请求联合 JSON：

```json
{
  "valence": -0.3,
  "arousal": 0.7,
  "intensity": 0.8,
  "basic_emotion": "anger",
  "social_intent": "emotional",
  "intent_subtype": "venting",
  "urgency_score": 65,
  "relevance_score": 0.7,
  "confidence": 0.85
}
```

**第三层：上下文融合**

- 情感轨迹（trajectory）：用户最近 5 条情绪的趋势外推
- 群体氛围（group sentiment）：EMA 平滑的群体愉悦度
- 助手情绪（assistant emotion）：从 persona baseline 初始化，受用户情绪影响

### 输出

`CognitionAnalyzer.analyze()` 返回三元组：

```python
(
    EmotionState,       # 2D valence-arousal + 基本情绪 + 置信度
    IntentAnalysisV3,   # 社交意图 + 紧急度 + 相关性 + 动态阈值
    EmpathyStrategy,    # 共情策略（confirm_action / cognitive / action / share_joy / presence）
)
```

---

## 情绪模型（EmotionState）

### 2D valence-arousal 坐标

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

从 valence-arousal 坐标自动映射到最接近的基本情绪：

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

---

## 意图分析（IntentAnalysisV3）

### 目的驱动分类

不问"这是什么"，而问"对方想要什么"。

| 意图 | 含义 | 典型句式 | urgency 基线 |
|------|------|---------|-------------|
| **help_seeking** | 求助 | "有人知道这个怎么弄吗" | 60 |
| **emotional** | 情感表达 | "今天好烦""太开心了" | 50 |
| **social** | 社交互动 | "哈哈哈""同意" | 20 |
| **silent** | 无明确目的 | "转发了一条新闻" | 10 |

### 量化评分

每条意图附带：
- `urgency_score`（0~100）：多快需要回应
- `relevance_score`（0~1）：与当前 AI 角色的相关度
- `confidence`（0~1）：分析置信度

### 决策影响

`urgency` 和 `relevance` 直接输入 `ThresholdEngine`：

```
threshold = base × activity_factor × relationship_factor × time_factor
```

- urgency ≥ 80 + relevance ≥ 0.7 → **IMMEDIATE**（立即回复）
- urgency ≥ 50 → **DELAYED**（延迟 15 秒）
- urgency ≥ 20 → **DELAYED**（延迟 45 秒）
- 否则 → **SILENT**（不回复）

---

## 共情策略（EmpathyStrategy）

基于情绪状态自动选择：

| 情绪状态 | 策略 | 行为 |
|---------|------|------|
| valence < -0.5, arousal > 0.7 | **confirm_action** | 先确认感受，再提供行动建议 |
| valence < -0.3 | **cognitive** | 帮助重新理解情境 |
| valence > 0.5 | **share_joy** | 积极回应，放大正面情绪 |
| 其他 | **presence** | 安静陪伴，不过度干预 |

---

## 群体情感

### 情感轨迹

引擎跟踪每个用户在一段时间内的情感变化。用于：
- 检测情感孤岛（某个用户长时间情绪低落）
- 为决策层提供上下文（"这个人最近一周都很丧"）

### 群体氛围快照（AtmosphereSnapshot）

每个群聊有整体氛围：
- `group_valence`：群整体愉悦度
- `group_arousal`：群整体活跃度
- `heat_level`：cold / warm / hot / overheated

快照在每条消息处理后更新，存入语义记忆的 `atmosphere_history`（保留最近 1000 条）。

---

## 使用方式

```python
from sirius_chat.core.cognition import CognitionAnalyzer

analyzer = CognitionAnalyzer()

# 直接调用（零成本规则引擎，复杂情况自动 LLM fallback）
emotion, intent, empathy = await analyzer.analyze(
    "今天工作好累，好想辞职", user_id="u1", group_id="g1"
)

print(emotion.valence)      # -0.6 (负面)
print(emotion.arousal)      # 0.7 (高唤醒)
print(intent.social_intent) # emotional
print(empathy.strategy_type) # confirm_action

# 手动更新群体氛围
analyzer.update_group_sentiment("g1", emotion)
```
