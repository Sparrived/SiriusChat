# Sirius Chat v0.28 情感化群聊助手架构重构白皮书

> 基于《AI记忆系统与情感化助手：群聊场景可落地方案框架》论文的系统性架构重构。
> 
> 版本：v0.28.0-dev  
> 作者：AI Coding Agent  
> 日期：2026-04-17

---

## 目录

1. [背景与动机](#1-背景与动机)
2. [论文核心架构映射](#2-论文核心架构映射)
3. [现状诊断](#3-现状诊断)
4. [重构总体架构蓝图](#4-重构总体架构蓝图)
5. [分层详细设计](#5-分层详细设计)
   - 5.1 [感知层](#51-感知层)
   - 5.2 [认知层](#52-认知层)
   - 5.3 [决策层](#53-决策层)
   - 5.4 [执行层](#54-执行层)
   - 5.5 [记忆底座](#55-记忆底座)
6. [数据模型总览](#6-数据模型总览)
7. [文件与模块变更清单](#7-文件与模块变更清单)
8. [实施路线图](#8-实施路线图)
9. [向后兼容与迁移策略](#9-向后兼容与迁移策略)
10. [配置项扩展](#10-配置项扩展)
11. [测试策略](#11-测试策略)
12. [风险与应对](#12-风险与应对)
13. [附录：论文关键公式落盘](#13-附录论文关键公式落盘)

---

## 1. 背景与动机

Sirius Chat 当前版本（v0.27.14）是一个功能完备的多人 RPG 对话编排框架，具备意图分析、热度感知、记忆提取、事件记忆 V2、AI 自身记忆等能力。然而，与《AI记忆系统与情感化助手：群聊场景可落地方案框架》论文中提出的"伙伴型 AI"愿景相比，存在以下结构性差距：

- **无群聊隔离**：所有群组共享同一用户记忆空间，存在隐私泄露与文化串扰风险。
- **无情感分析**：无法识别用户情绪状态，无从实现共情回应与情感危机干预。
- **二元响应决策**：仅有 `should_reply: bool`，无法模拟人类"选择性参与"的自然社交行为。
- **扁平记忆结构**：缺少语义层抽象与知识图谱能力，无法支撑深度个性化与关系网络推理。

本次重构的目标是将 Sirius Chat 从一个"功能型群聊机器人"升级为"情感化数字伙伴"，在保持现有工程稳定性的前提下，系统性引入论文提出的四层认知架构与三层记忆底座。

---

## 2. 论文核心架构映射

论文提出的核心设计哲学可归纳为三个支柱：

| 支柱 | 论文章节 | 核心内容 | Sirius Chat 映射 |
|------|---------|---------|-----------------|
| **拟人化交互** | §1.1.1 | 情感感知、长期记忆个性化、可控响应 | 情感分析子系统 + 语义记忆层 + 四层响应策略 |
| **可控响应** | §1.1.2 | 意图分析器驱动四层响应（立即/延迟/沉默/主动） | ResponseStrategyEngine + DelayedResponseQueue + ProactiveTrigger |
| **渐进式记忆** | §1.1.3 | 工作记忆→情景记忆→语义记忆三层协同 | WorkingMemoryManager → EpisodicMemoryManager → SemanticMemoryManager |

---

## 3. 现状诊断

### 3.1 现有架构优势（应保留）

1. **异步编排引擎**：`AsyncRolePlayEngine` 的 `BackgroundTaskManager` 与事件流机制可平滑承载新任务。
2. **v2 意图分析器**：多 AI 区分、代词推断、指向目标判定（`self_ai`/`other_ai`/`human`/`ambient`）极为扎实，应作为 v3 的基础层。
3. **LLM 记忆提取流水线**：`memory_extract`、`event_extract`、`self_memory_extract` 的并行调度机制成熟。
4. **文件持久化原子写入**：`_atomic_write_json`（临时文件 + replace）机制可直接复用。
5. **模块化目录结构**：`memory/user/`、`memory/event/`、`memory/self/`、`memory/quality/` 的分离为新增 `working/`、`episodic/`、`semantic/` 提供了清晰范式。

### 3.2 核心差距矩阵

| # | 维度 | 论文要求 | 现状 | 严重度 |
|---|------|---------|------|--------|
| 1 | **群聊隔离** | "群-用户"二维索引，每群独立记忆空间 | 无任何 `group_id` 字段，所有群共享同一 `entries` 字典 | 🔴 P0 |
| 2 | **情感分析** | 二维情感模型(valence×arousal)，19种状态映射，群体情感聚合 | 完全缺失；仅有 `heat.py` 的消息密度统计 | 🔴 P0 |
| 3 | **响应策略** | 立即/延迟/沉默/主动四层策略 | 仅有 `should_reply: bool` 二元决策；无延迟队列 | 🔴 P0 |
| 4 | **记忆检索** | 向量语义检索 + 情境预加载 | 仅有字符串子串匹配和字符集 Jaccard 相似度(阈值0.55) | 🟠 P1 |
| 5 | **语义记忆** | 属性图模型（节点：用户/话题/实体；边：关系强度） | 平铺的 `MemoryFact` 列表，无结构化关系 | 🟠 P1 |
| 6 | **对话节奏** | 对话状态机（间隙/爆发/转折/注意力窗口） | `heat.py` 仅有密度/人数/AI占比三指标 | 🟠 P1 |
| 7 | **群级记忆** | 群体氛围、群体规范、文化参数 | 完全缺失；无 `group_state` 概念 | 🟡 P2 |
| 8 | **动态阈值** | `Threshold = Base × Activity × Relationship × Time` | 仅有全局 `sensitivity` 参数（0.0~1.0） | 🟡 P2 |
| 9 | **助手情感** | 助手自身情感状态 + 情感惯性 + 恢复机制 | 完全缺失 | 🟡 P2 |
| 10 | **共情生成** | 情感确认→认知共情→行动支持三层 | Prompt 中无情感状态注入，无共情策略指令 | 🟡 P2 |

---

## 4. 重构总体架构蓝图

### 4.1 运行时数据流

```
群消息进入（感知层）
    │
    ├─ MessageNormalizer ──→ 标准化 Message（含 group_id）
    ├─ FeatureExtractor ───→ @提及/关键词/长度/表情
    └─ ContextLinker ──────→ 绑定对话线程
    │
    ▼
认知层（并行 asyncio.gather）
    ├─ IntentAnalyzer v3 ──→ social_intent + urgency + relevance
    ├─ EmotionAnalyzer ────→ EmotionState(valence, arousal, basic_emotion)
    └─ MemoryRetriever ────→ 工作记忆 + 关键词 + 语义检索
    │
    ▼
决策层
    ├─ ThresholdEngine ────→ 多因子动态阈值
    ├─ ResponseStrategyEngine
    │        ├─ IMMEDIATE ─→ 直接调用 LLM 生成回复
    │        ├─ DELAYED ──→ 入 DelayedResponseQueue，择机发送
    │        ├─ SILENT ────→ 仅更新内部状态（被动学习）
    │        └─ PROACTIVE ─→ 由 ProactiveTrigger 定时/记忆/情感触发
    └─ TimingJudge ────────→ 对话间隙/注意力窗口/节奏适配
    │
    ▼
执行层
    ├─ ResponseAssembler ──→ 按策略组装 Prompt
    ├─ EmpathyGenerator ───→ 三层共情指令注入
    ├─ StyleAdapter ───────→ 长度/风格/表情包动态适配
    └─ ProviderDispatcher ─→ LLM 调用 + 多平台发送
    │
    ▼
记忆底座（异步后台更新）
    ├─ WorkingMemoryManager ──→ 按群滑动窗口
    ├─ EpisodicMemoryManager ──→ 结构化事件存储
    ├─ SemanticMemoryManager ──→ 用户画像 + 群体规范
    └─ ActivationEngine ───────→ 遗忘曲线 + 访问强化
```

### 4.2 模块层级图

```
sirius_chat/
├── core/
│   ├── engine.py                    # AsyncRolePlayEngine（集成新层）
│   ├── intent_v2.py                 # 保留（后向兼容）
│   ├── intent_v3.py                 # NEW: 目的驱动意图分析
│   ├── emotion.py                   # NEW: 二维情感分析器
│   ├── response_strategy.py         # NEW: 四层响应策略引擎
│   ├── delayed_response_queue.py    # NEW: 延迟响应队列
│   ├── proactive_trigger.py         # NEW: 主动发起触发器
│   ├── rhythm.py                    # NEW: 对话节奏感知（扩展 heat.py）
│   ├── threshold_engine.py          # NEW: 多因子动态阈值
│   ├── heat.py                      # 保留（逐步迁移到 rhythm.py）
│   ├── engagement.py                # 保留（逐步迁移到 response_strategy.py）
│   └── chat_builder.py              # 改造：注入情感/共情/群级风格
├── memory/
│   ├── user/                        # 保留（加 group_id 支持）
│   ├── event/                       # 保留（加 group_id 支持）
│   ├── self/                        # 保留（加助手情感状态）
│   ├── quality/                     # 保留
│   ├── working/                     # NEW: 工作记忆滑动窗口
│   ├── episodic/                    # NEW: 情景记忆管理器
│   ├── semantic/                    # NEW: 语义记忆管理器
│   ├── activation_engine.py         # NEW: 激活度与遗忘曲线
│   ├── retrieval_engine.py          # NEW: 三级记忆检索
│   └── migration/                   # NEW: 数据迁移脚本
├── models/
│   ├── models.py                    # 改造：Message/Participant 加 group_id
│   ├── emotion.py                   # NEW: EmotionState / EmpathyStrategy / AssistantEmotionState
│   ├── intent_v3.py                 # NEW: IntentAnalysisV3
│   └── response_strategy.py         # NEW: StrategyDecision / DelayedResponseItem
```

---

## 5. 分层详细设计

### 5.1 感知层

#### 5.1.1 MessageNormalizer

**职责**：将多平台（QQ/微信/Discord/钉钉等）原始消息转换为统一的 `Message` 表示。

**新增字段**：
```python
@dataclass
class Message:
    role: str
    content: str
    speaker: str | None = None
    channel: str | None = None
    channel_user_id: str | None = None
    group_id: str | None = None          # NEW: 群聊隔离标识
    reply_mode: str = "always"
```

**规则**：
- `group_id` 为空时回退到 `"default"`（向后兼容）。
- 群聊场景下 `group_id` 由外部 Adapter 提供（如 `qq_group_12345`）。

#### 5.1.2 ContextLinker

**职责**：建立消息与最近 N 条上下文的线程链接，检测引用关系（`@提及` / `回复` / `引用`）。

**实现**：复用 `Transcript.messages` 列表，新增按 `group_id` 分组的工作记忆缓存。

---

### 5.2 认知层

#### 5.2.1 IntentAnalyzer v3（`sirius_chat/core/intent_v3.py`）

**设计原则**：在保留 v2 全部精华（多 AI 区分、代词推断、后验校验）的基础上，增加**目的驱动分类**和**量化评分**。

**输出结构**：
```python
@dataclass
class IntentAnalysisV3:
    # v2 兼容
    intent_type: str                    # question | request | chat | ...
    target: str
    target_scope: str
    directed_at_current_ai: bool
    importance: float
    
    # v3 新增
    social_intent: SocialIntent         # help_seeking | emotional | social | silent
    intent_subtype: str                 # tech_help | venting | humor | ...
    urgency_score: float                # 0-100
    relevance_score: float              # 0-1
    confidence: float
    response_priority: int              # 1-10
    estimated_response_time: float      # 0=立即
```

**分类规则（规则引擎层，零 LLM 成本）**：

| 信号 | 权重 | 检测方式 |
|------|------|---------|
| 求助关键词（怎么/如何/求助/报错） | +3 | 正则匹配 `HELP_PATTERNS` |
| 情感关键词（难受/开心/累/烦/郁闷） | +2 | 情感词典匹配 |
| 社交关键词（大家觉得/一起/聊聊） | +1 | 关键词列表 |
| @提及当前助手 | 强制 `HELP_SEEKING` | 字符串匹配 |
| 高 negative arousal (>0.7) + 情感词 | 强制 `EMOTIONAL` | EmotionAnalyzer 输出 |

**LLM 层**：当规则层置信度 < 0.8 时，调用 LLM 进行高精度分类，Prompt 要求同时输出：
1. 行为类型（question/request/command/chat/reaction/information_share）
2. 目的类型（help_seeking/emotional/social/silent）
3. urgency_score（0-100）
4. relevance_score（0-1）

#### 5.2.2 EmotionAnalyzer（`sirius_chat/core/emotion.py`）

**设计原则**：规则引擎为主（零 LLM 成本），LLM 为辅（高精度模式）。

**二维情感模型**：
- **Valence**（愉悦度）：-1（极度负面） ~ +1（极度正面）
- **Arousal**（唤醒度）：0（平静） ~ 1（极度激动）

**19 种基本情感映射**：

| 情感 | valence | arousal | 典型触发词 |
|------|---------|---------|-----------|
| 喜悦 JOY | +0.8 | 0.7 | 开心、高兴、太棒了 |
| 愤怒 ANGER | -0.7 | 0.9 | 生气、愤怒、崩溃了 |
| 焦虑 ANXIETY | -0.6 | 0.8 | 焦虑、担心、睡不着 |
| 悲伤 SADNESS | -0.8 | 0.2 | 难过、伤心、想哭 |
| 孤独 LONELINESS | -0.7 | 0.3 | 孤独、没人理、寂寞 |
| ... | ... | ... | ... |

**分析流程**：
```python
async def analyze(message: str, user_id: str, group_id: str | None) -> EmotionState:
    text_emotion = _text_sentiment_analysis(message)      # 规则引擎
    context_emotion = _context_inference(user_id)          # 情感轨迹趋势外推
    group_emotion = _group_sentiment_perception(group_id)  # 群体氛围（缓存）
    return _fuse_emotions(text_emotion, context_emotion, group_emotion)
    # 权重：文本 0.5 + 语境 0.3 + 群体 0.2
```

**情感轨迹**：每个用户维护最近 100 条 `EmotionState`，用于：
- 情感突变检测（积极→消极的跳变）
- 情感持续监测（长期低情感强度）
- 趋势外推（动量预测）

**群体情感聚合**：
- 分析最近 20 条消息的情感状态。
- 加权聚合（近期消息指数加权 `exp(linspace(-1, 0, n))`，活跃成员加权）。
- 检测**情感孤岛**：个体情感与群体均值偏离 > 1.5σ 时标记。

**共情策略选择**：

| 象限 | valence | arousal | 策略 | 说明 |
|------|---------|---------|------|------|
| 高唤醒负面 | < -0.5 | > 0.7 | confirm_action | 先情感确认，再提供行动支持 |
| 中唤醒负面 | < -0.3 | — | cognitive | 认知共情，理解处境 |
| 高愉悦 | > 0.5 | — | share_joy | 分享喜悦，适度深化 |
| 其他 | — | — | presence | 保持陪伴存在 |

#### 5.2.3 MemoryRetriever（`sirius_chat/memory/retrieval_engine.py`）

**三级检索体系**（与论文 §4.3 一致）：

```
查询进入
  ├─ 1. 工作记忆检索（纯内存，关键词匹配，O(1)）
  ├─ 2. 关键词检索（情景记忆文件遍历，支持同义词扩展）
  ├─ 3. 语义相似度检索（可选，依赖 sentence-transformers）
  └─ 4. 用户画像检索（语义记忆层属性匹配）
      
  → 去重 → 综合评分 → 按 score 排序 → Top-K 返回
```

**综合评分公式**：
```python
score = importance × 0.4 + recency_score × 0.3 + activation × 0.3
recency_score = exp(-0.1 × days_since_creation)
```

**情境预加载**：当 `IntentAnalyzer v3` 初步判定 `social_intent != SILENT` 且 `urgency >= 20` 时，异步提前启动检索，结果缓存到 `WorkingMemoryManager.preload_cache`，供后续决策层直接使用。

---

### 5.3 决策层

#### 5.3.1 ThresholdEngine（`sirius_chat/core/threshold_engine.py`）

**核心公式**（论文 §2.2.3）：
```python
threshold = base_threshold × activity_factor × relationship_factor × time_factor

base_threshold = 0.60 - sensitivity × 0.30   # 0.30 ~ 0.60

activity_factor:
    cold       → 0.8   (降低阈值，填补冷场)
    warm       → 1.0
    hot        → 1.3   (提高阈值，避免刷屏)
    overheated → 1.6

relationship_factor (user_familiarity_score):
    0.0-0.3  陌生 → 1.2
    0.3-0.6  一般 → 1.0
    0.6-0.9  熟悉 → 0.8
    0.9+     亲密 → 0.6

time_factor (hour_of_day):
    00-06 深夜    → 1.3
    09-18 工作时段 → 1.1 (工作群)
    19-23 休闲时段 → 0.9
```

**用户亲密度计算**：
```python
familiarity = (
    interaction_frequency_score × 0.3 +
    emotional_intimacy_score × 0.3 +
    trust_score × 0.2 +
    dependency_score × 0.2
)
```
数据来源：`UserSemanticProfile.relationship_state`。

#### 5.3.2 ResponseStrategyEngine（`sirius_chat/core/response_strategy.py`）

**策略决策矩阵**：

| urgency | relevance | threshold | 策略 | 说明 |
|---------|-----------|-----------|------|------|
| ≥80 | ≥0.7 | 任意 | **IMMEDIATE** | 跳过时机判断，直接生成 |
| 50-79 | ≥0.5 | 任意 | **DELAYED** (priority 高) | 进入队列，15-30秒窗口 |
| 20-49 | ≥0.5 | 任意 | **DELAYED** (priority 低) | 进入队列，30-60秒窗口 |
| <20 | <0.5 | 任意 | **SILENT** | 不回复，后台观察 |
| — | — | — | **PROACTIVE** | 由外部触发器决定 |

**特殊规则**：
- `@当前助手` + `HELP_SEEKING` → 强制 **IMMEDIATE**，绕过频率限制。
- `EMOTIONAL` + 高 negative arousal → urgency +20，优先 **IMMEDIATE**。
- `SILENT` 意图 + 无 @提及 → 强制 **SILENT**。

#### 5.3.3 DelayedResponseQueue（`sirius_chat/core/delayed_response_queue.py`）

**数据结构**：
```python
@dataclass
class DelayedResponseItem:
    item_id: str
    group_id: str
    user_id: str
    message_content: str
    strategy_decision: StrategyDecision
    emotion_state: dict
    candidate_memories: list[str]
    enqueue_time: str
    window_seconds: float
    status: str  # pending | triggered | cancelled | sent
```

**等待窗口期间的监控逻辑**：
1. **问题已解决检测**：若后续对话中出现"解决了"、"好了"、"谢谢"，或另一成员提供了正确答案 → 取消响应，记录为"自助解决"学习样本。
2. **话题间隙检测**：群 10 秒无新消息 → 立即触发响应生成。
3. **话题漂移检测**：若当前话题转向助手高相关领域 → 提前触发。
4. **合并机制**：同一群内多条相关待响应消息可合并为一条综合回复。

#### 5.3.4 ProactiveTrigger（`sirius_chat/core/proactive_trigger.py`）

**触发器类型**：

| 类型 | 触发条件 | 冷却机制 |
|------|---------|---------|
| **时间触发** | 群沉寂 > 30 分钟，且历史活跃度中等以上 | 每群 1 小时最多 1 次 |
| **记忆触发** | 用户重要日期到来 / 话题新进展 / 长期沉默后回归 | 每用户 24 小时最多 2 次 |
| **情感触发** | 群体氛围持续低落（连续 10 条平均 valence < -0.3）/ 情感孤岛 | 每群 2 小时最多 1 次 |

**实现方式**：
- 在 `AsyncRolePlayEngine` 中注册后台 `asyncio` 定时任务（`proactive_check_interval=60` 秒）。
- 或暴露 `engine.proactive_check()` API 供外部 cron 调用。
- 触发后走 `_generate_assistant_message`，但 `ResponseStrategy=PROACTIVE` 会注入额外 Prompt："请以自然、不经意的方式开启对话，避免显得程序触发"。

---

### 5.4 执行层

#### 5.4.1 ResponseAssembler / EmpathyGenerator

**Prompt 新增注入内容**（改造 `chat_builder.py`）：

1. **情感状态摘要**（< 100 tokens）：
   ```
   [情感上下文]
   用户当前情绪：焦虑（强度0.8）
   群体氛围：中性偏消极
   助手情感状态：关切（愉悦度0.1，唤醒度0.4）
   ```

2. **共情策略指令**（由 `EmotionAnalyzer.select_empathy_strategy()` 输出）：
   ```
   [共情策略]
   类型：confirm_action
   深度：level 2
   要求：先情感确认（"听起来你很焦虑"），再提供具体帮助
   ```

3. **记忆关联引用**（从 `SemanticMemoryManager` 检索）：
   ```
   [相关记忆]
   - 用户之前提到过这周有项目 deadline
   - 用户偏好简洁直接的回复风格
   ```

4. **群级风格参数**：
   ```
   [群体风格]
   当前群活跃度：hot → 回复控制在 1-2 句话
   群体典型风格：轻松幽默
   ```

#### 5.4.2 StyleAdapter

**长度自适应**：
- `heat=hot` / `pace=accelerating` → `max_tokens` 限制为 80，Prompt 中要求"1-2句话"。
- `heat=cold` + `topic_stability>0.7` → 允许多段落详细回复。
- 用户画像 `communication_style=concise` → 全局压缩倾向。

**风格参数**：
- 幽默度：从 `GroupSemanticProfile.typical_interaction_style` 读取（`humorous` / `formal` / `balanced`）。
- 表情包/语气词：由群级配置开关控制，通过 Prompt 指令注入。

---

### 5.5 记忆底座

#### 5.5.1 群隔离存储布局

重构后 `user_memory` 目录结构：

```
{work_path}/
└── user_memory/
    ├── global/                          # 跨群共享语义画像（需用户授权）
    │   └── {user_id}.json
    └── groups/
        └── {group_id}/
            ├── {user_id}.json           # 该群内的用户记忆（recent_messages, memory_facts...）
            └── group_state.json         # 群级记忆（氛围历史、规范、兴趣）
```

`event_memory` 目录结构：
```
{work_path}/
└── event_memory/
    └── {group_id}/
        └── events.json
```

#### 5.5.2 WorkingMemoryManager（`sirius_chat/memory/working/manager.py`）

**职责**：按 `group_id` 维护内存中的对话上下文滑动窗口。

**滑动窗口策略**：
- 基础容量：最近 N 轮（可配置，`working_memory_max_size=20`）。
- 截断时按 `(importance, timestamp)` 降序排序保留。
- **关键信息保护**：包含用户偏好表达、重要约定、情感危机信号（`urgency>80` 或 `negative_arousal>0.7`）的消息标记 `protected=True`，优先保留。
- **自动晋升**：被移除的消息中，`importance >= 0.3` 的自动触发 `promote_to_episodic`。

#### 5.5.3 EpisodicMemoryManager（`sirius_chat/memory/episodic/manager.py`）

**数据模型**：
```python
@dataclass
class EpisodicMemoryEntry:
    event_id: str
    group_id: str
    user_ids: list[str]
    timestamp: str          # ISO 8601
    content: str            # 摘要或原文
    emotion_tags: dict[str, float]
    importance: float
    activation: float       # 动态激活度
    access_count: int
    last_accessed: str | None
    related_event_ids: list[str]
```

**职责**：
- 接管现有 `EventMemoryManager` 的观察提取能力。
- 提供按时间范围、用户、情感标签、重要性的复合查询。
- 支持按 `group_id` 隔离存储。

#### 5.5.4 SemanticMemoryManager（`sirius_chat/memory/semantic/manager.py`）

**用户语义画像**：
```python
@dataclass
class UserSemanticProfile:
    user_id: str
    base_attributes: dict[str, Any]       # 昵称、身份、沟通风格偏好
    interest_graph: list[InterestNode]    # 话题-参与度-深度三元组
    relationship_state: RelationshipState # 互动频率、亲密度、信任度、依赖度
    taboo_boundaries: list[str]           # 禁忌话题
    important_dates: list[dict[str, str]] # 用户提及的重要日期
    confirmed: bool
    updated_at: str
```

**群体语义画像**：
```python
@dataclass
class GroupSemanticProfile:
    group_id: str
    atmosphere_history: list[AtmosphereSnapshot]  # 时间、群体情感、活跃度、主导话题
    group_norms: dict[str, Any]                   # 介入频率期待、禁忌话题、偏好风格
    interest_topics: list[str]                    # 群体共同兴趣 Top N
    typical_interaction_style: str                # active / lurker / controversial
    ai_intervention_feedback: list[dict]          # AI 介入后群体反应记录
```

**周期性总结流水线**：
- `hourly_consolidation`：工作记忆 → 情景记忆（合并相似事件、去重）。
- `daily_consolidation`：情景记忆 → 语义记忆（提取用户画像更新、群体规范更新）。
- 使用 LLM 执行总结，输出结构化增量更新（JSON 格式）。

#### 5.5.5 ActivationEngine（`sirius_chat/memory/activation_engine.py`）

**核心公式**（论文 §4.2.4）：
```python
activation = importance_baseline × time_decay × access_boost

time_decay = exp(-decay_lambda × hours_since_creation)
access_boost = 1 + reinforcement_gamma × access_count
```

**差异化衰减参数**：

| 记忆类型 | decay_lambda | 说明 |
|---------|-------------|------|
| 核心偏好（姓名、居住地） | 0.001 | 几乎永久保留 |
| 临时状态（今天心情不好） | 0.05 | 数周后淡化 |
| 时事信息（下周发布会） | 0.1 | 事件后快速衰减 |

**遗忘决策**：
- 当 `activation < threshold`（默认 0.1）时，记忆进入**休眠状态**。
- 休眠记忆移至 `archive/` 目录，保留可恢复性，降低检索开销。
- 每次检索命中时实时更新：`access_count += 1`，`last_accessed = now()`，重新计算 `activation`。

---

## 6. 数据模型总览

### 6.1 新增模型

| 模型 | 文件 | 说明 |
|------|------|------|
| `BasicEmotion` | `models/emotion.py` | 19 种基本情感枚举 |
| `EmotionState` | `models/emotion.py` | valence/arousal/basic_emotion/intensity/confidence |
| `EmpathyStrategy` | `models/emotion.py` | 共情策略（confirm_action/cognitive/action/share_joy/presence） |
| `AssistantEmotionState` | `models/emotion.py` | 助手情感 + 惯性 + 恢复 |
| `SocialIntent` | `models/intent_v3.py` | 目的分类枚举 |
| `HelpSubtype` / `EmotionalSubtype` / `SocialSubtype` / `SilentSubtype` | `models/intent_v3.py` | 子类型枚举 |
| `IntentAnalysisV3` | `models/intent_v3.py` | 扩展意图分析结果 |
| `ResponseStrategy` | `models/response_strategy.py` | 四层策略枚举 |
| `StrategyDecision` | `models/response_strategy.py` | 策略决策结果 |
| `DelayedResponseItem` | `models/response_strategy.py` | 延迟队列项 |
| `EpisodicMemoryEntry` | `memory/episodic/models.py` | 结构化情景记忆 |
| `UserSemanticProfile` | `memory/semantic/models.py` | 用户语义画像 |
| `GroupSemanticProfile` | `memory/semantic/models.py` | 群体语义画像 |
| `InterestNode` | `memory/semantic/models.py` | 话题-参与度-深度三元组 |
| `RelationshipState` | `memory/semantic/models.py` | 双边关系状态 |
| `AtmosphereSnapshot` | `memory/semantic/models.py` | 群体氛围快照 |
| `WorkingMemoryWindow` | `memory/working/models.py` | 工作记忆窗口条目 |

### 6.2 修改现有模型

| 模型 | 变更 |
|------|------|
| `Message` | 新增 `group_id: str \| None = None` |
| `Participant` | 新增 `group_memberships: dict[str, Any] = field(default_factory=dict)` |
| `MemoryFact` | 新增 `group_id: str = ""`, `activation: float = 1.0`, `access_count: int = 0`, `last_accessed: str = ""` |
| `EventMemoryEntry` | 新增 `group_id: str = ""`, `activation: float = 1.0`, `user_ids: list[str] = field(default_factory=list)` |
| `UserMemoryManager` | `entries` 改为 `dict[str, dict[str, UserMemoryEntry]]`（group → user） |

---

## 7. 文件与模块变更清单

### 7.1 新建文件

```
sirius_chat/core/intent_v3.py
sirius_chat/core/emotion.py
sirius_chat/core/response_strategy.py
sirius_chat/core/delayed_response_queue.py
sirius_chat/core/proactive_trigger.py
sirius_chat/core/rhythm.py
sirius_chat/core/threshold_engine.py

sirius_chat/memory/working/__init__.py
sirius_chat/memory/working/manager.py
sirius_chat/memory/working/models.py
sirius_chat/memory/episodic/__init__.py
sirius_chat/memory/episodic/manager.py
sirius_chat/memory/episodic/models.py
sirius_chat/memory/semantic/__init__.py
sirius_chat/memory/semantic/manager.py
sirius_chat/memory/semantic/models.py
sirius_chat/memory/activation_engine.py
sirius_chat/memory/retrieval_engine.py
sirius_chat/memory/migration/__init__.py
sirius_chat/memory/migration/v0_28_group_isolation.py

sirius_chat/models/emotion.py
sirius_chat/models/intent_v3.py
sirius_chat/models/response_strategy.py
```

### 7.2 修改文件

```
sirius_chat/models/models.py              # Message.group_id, Participant.group_memberships
sirius_chat/models/__init__.py            # 导出新模型
sirius_chat/memory/user/models.py         # MemoryFact 增加激活度字段
sirius_chat/memory/user/manager.py        # entries 改为双层字典
sirius_chat/memory/user/store.py          # 群隔离存储布局
sirius_chat/memory/event/models.py        # EventMemoryEntry 增加 group_id, activation
sirius_chat/memory/event/store.py         # 群隔离存储布局
sirius_chat/memory/self/manager.py        # 增加助手情感状态维护
sirius_chat/memory/__init__.py            # 导出新模块
sirius_chat/core/engine.py                # 全链路传递 group_id，集成新层
sirius_chat/core/chat_builder.py          # 注入情感/共情/群级风格
sirius_chat/core/heat.py                  # 逐步迁移到 rhythm.py
sirius_chat/core/engagement.py            # 逐步迁移到 response_strategy.py

pyproject.toml                            # 可选依赖 sentence-transformers
```

---

## 8. 实施路线图

### Iteration 1: 记忆底座与群隔离（Week 1-2）

**目标**：搭建论文架构的"地基"，解决最严重的群聊隔离问题。

**核心任务**：
1. 修改 `Message`, `Participant`, `MemoryFact`, `EventMemoryEntry` 增加 `group_id`。
2. 重构 `UserMemoryFileStore` / `EventMemoryFileStore` 为群隔离目录结构。
3. 将 `UserMemoryManager.entries` 改为 `dict[str, dict[str, UserMemoryEntry]]`。
4. 在 `engine.py` 全链路传递 `group_id`。
5. 编写数据迁移脚本（旧格式 → `default` 群）。
6. 新建 `WorkingMemoryManager`，实现重要性加权滑动窗口。

**验收标准**：
- 两个群的用户 A 的记忆互不影响。
- 旧 workspace 数据无缝迁移。
- 工作记忆滑动窗口按重要性正确截断。

### Iteration 2: 语义记忆、激活度与检索（Week 3-4）

**目标**：实现三层记忆中的情景记忆和语义记忆层，升级检索能力。

**核心任务**：
1. 新建 `EpisodicMemoryManager`，接管并升级 `EventMemoryManager`。
2. 新建 `SemanticMemoryManager`，实现 `UserSemanticProfile` 和 `GroupSemanticProfile`。
3. 实现 `ActivationEngine`（遗忘曲线 + 访问强化）。
4. 实现 `MemoryRetriever`（关键词 + 语义相似度 + 预加载）。
5. 实现周期性总结流水线（hourly + daily consolidation）。

**验收标准**：
- 工作记忆中的重要信息自动晋升到情景记忆。
- 语义记忆能回答"用户 A 喜欢什么"。
- 低激活度记忆正确归档。

### Iteration 3: 情感分析、意图 v3、响应策略（Week 5-6）

**目标**：补齐情感智能和四层响应策略。

**核心任务**：
1. 新建 `EmotionAnalyzer`（二维情感模型 + 情感轨迹 + 群体情感聚合）。
2. 新建助手自身情感状态 `AssistantEmotionState`。
3. 新建 `IntentAnalyzer v3`（目的驱动分类 + urgency/relevance 评分）。
4. 新建 `ThresholdEngine` 和 `ResponseStrategyEngine`。
5. 新建 `DelayedResponseQueue`。
6. 在 `engine.py` 中集成认知层并行分析 → 决策层策略选择 → 执行层生成。

**验收标准**：
- 能识别"愤怒的求助"并提升 urgency。
- 延迟响应在话题间隙自然插入。
- 动态阈值随群活跃度和时间变化。

### Iteration 4: 主动发起、节奏感知、共情生成（Week 7-8）

**目标**：实现"伙伴型 AI"的高级能力。

**核心任务**：
1. 扩展 `heat.py` 为 `RhythmAnalyzer`（对话状态机 + 注意力窗口）。
2. 新建 `ProactiveTrigger`（定时器 + 记忆触发器 + 情感触发器）。
3. 在 `engine.py` 注册后台 `proactive_check` 任务。
4. 改造 `chat_builder.py`，集成情感状态、共情策略、群级风格到 Prompt。
5. 实现群级规范学习（被动学习）。
6. 端到端集成测试。

**验收标准**：
- 群沉寂 30 分钟后基于记忆主动发起自然对话。
- 群体氛围低落时识别并生成安慰内容。
- 高活跃度群中回复长度明显更短。

---

## 9. 向后兼容与迁移策略

### 9.1 数据迁移

**迁移脚本**：`sirius_chat/memory/migration/v0_28_group_isolation.py`

**迁移逻辑**：
1. 检测旧格式：`user_memory/*.json` 直接位于 `user_memory/` 目录下。
2. 自动迁移：将所有旧文件移动到 `user_memory/groups/default/`。
3. 生成 `group_state.json`（默认群状态）。
4. 迁移完成后写入 `.migration_v0_28_done` 标记文件，避免重复迁移。
5. 保留旧文件副本到 `user_memory/.backup_pre_v0_28/`。

### 9.2 API 兼容

- `AsyncRolePlayEngine.run_live_message(group_id=None)`：未提供时回退到 `"default"`。
- `UserMemoryManager.get_user_by_id(user_id, group_id=None)`：未提供时遍历所有群（返回第一个匹配）。
- `UserMemoryFileStore.load_all()`：先检查新布局，若不存在则检测旧布局并自动触发迁移。

### 9.3 配置兼容

- 新增配置项均有默认值，现有配置文件无需修改即可运行。
- `enable_emotion_analysis=False`（可在不引入新依赖的情况下先完成群隔离部署）。
- `enable_semantic_retrieval=False`（`sentence-transformers` 为可选依赖）。

---

## 10. 配置项扩展

在 `OrchestrationPolicy` / `SessionConfig` 中新增：

```python
# 功能开关
enable_emotion_analysis: bool = True
enable_semantic_retrieval: bool = False        # MVP 默认关闭
enable_proactive_trigger: bool = True
enable_delayed_response: bool = True

# 主动关怀参数
proactive_care_interval_minutes: int = 30
proactive_care_max_per_user_per_day: int = 2
proactive_care_max_per_group_per_hour: int = 1

# 延迟响应参数
delayed_response_window_seconds: tuple[int, int] = (15, 60)
delayed_response_gap_trigger_seconds: float = 10.0

# 情感参数
emotion_inertia_factor: float = 0.3
emotion_recovery_rate_per_10min: float = 0.1

# 记忆激活度参数
memory_activation_decay_lambda: float = 0.01
memory_activation_reinforcement_gamma: float = 0.1
memory_activation_threshold: float = 0.1

# 工作记忆参数
working_memory_max_size: int = 20
working_memory_promote_threshold: float = 0.3

# 阈值参数
threshold_base_low: float = 0.30
threshold_base_high: float = 0.60
```

---

## 11. 测试策略

### 11.1 单元测试（每个迭代必须）

| 模块 | 测试重点 |
|------|---------|
| `WorkingMemoryManager` | 滑动窗口截断、重要性排序、关键信息保护、自动晋升 |
| `ActivationEngine` | 遗忘曲线计算、访问强化、差异化衰减、休眠归档 |
| `MemoryRetriever` | 关键词检索、语义相似度、综合评分排序、预加载缓存 |
| `EmotionAnalyzer` | 规则引擎分类、情感轨迹更新、群体聚合、情感孤岛检测 |
| `IntentAnalyzer v3` | 目的分类边界、urgency/relevance 评分、与情感耦合 |
| `ThresholdEngine` | 多因子阈值计算、边界条件、时间因子变化 |
| `ResponseStrategyEngine` | 策略矩阵覆盖、特殊规则、边界条件 |
| `DelayedResponseQueue` | 入队/触发/取消/合并、窗口监控、状态流转 |
| `ProactiveTrigger` | 触发条件判定、冷却机制、自然性 Prompt |

### 11.2 集成测试

- **群隔离测试**：两个群同时运行，验证同一用户记忆不串扰。
- **迁移测试**：旧 workspace 启动后自动迁移，数据完整无丢失。
- **端到端场景测试**：
  - 场景 A：高活跃度群中的选择性沉默与适时介入。
  - 场景 B：情感危机消息的即时识别与关怀回应。
  - 场景 C：群沉寂后的主动关怀自然发起。
  - 场景 D：延迟响应在话题间隙的自然插入。

### 11.3 回归测试

每个迭代完成后必须运行：
```bash
python -m pytest tests/ -q
```
目标：无新增失败，覆盖率不下降。

---

## 12. 风险与应对

| 风险 | 可能性 | 影响 | 应对策略 |
|------|--------|------|---------|
| 群隔离引入破坏现有用户数据 | 中 | 高 | 自动迁移脚本 + 旧数据备份 + 回退逻辑 |
| 情感分析增加 LLM 调用成本 | 高 | 中 | 规则引擎为主，LLM 为辅；提供开关；缓存群体情感 |
| 语义检索引入新依赖导致部署复杂 | 中 | 中 | `sentence-transformers` 设为可选，默认关闭 |
| 延迟队列和主动发起导致测试困难 | 中 | 中 | 提供 `mock_clock` 和 `force_trigger` 接口 |
| Prompt 变长导致 Token 成本上升 | 高 | 中 | 情感摘要用结构化 JSON（<200 tokens）；严格控制 Top-K；提供 `empathy_prompt_level` 配置 |
| 重构周期过长影响主线发布 | 中 | 高 | 分 4 个迭代，每个迭代独立可交付；保留 v2 模块作为 fallback |

---

## 13. 附录：论文关键公式落盘

### 13.1 激活度（遗忘曲线）

```python
activation = importance_baseline × time_decay × access_boost

time_decay = exp(-decay_lambda × hours_since_creation)
access_boost = 1 + reinforcement_gamma × access_count
```

### 13.2 动态阈值

```python
threshold = base_threshold × activity_factor × relationship_factor × time_factor

base_threshold = 0.60 - sensitivity × 0.30
activity_factor    = f(heat_level)    # cold:0.8 warm:1.0 hot:1.3 overheated:1.6
relationship_factor = g(familiarity)  # 陌生:1.2 一般:1.0 熟悉:0.8 亲密:0.6
time_factor        = h(hour)         # 深夜:1.3 工作:1.1 休闲:0.9
```

### 13.3 综合评分（记忆检索）

```python
score = importance × 0.4 + recency_score × 0.3 + activation × 0.3
recency_score = exp(-0.1 × days_since_creation)
```

### 13.4 紧急度评分

```python
urgency = (
    language_markers × 0.25 +
    time_constraint × 0.20 +
    repeated_signal × 0.20 +
    emotional_intensity × 0.20 +
    group_response × 0.15
) × 100
```

### 13.5 相关度评分

```python
relevance = (
    topic_match × 0.4 +
    role_match × 0.3 +
    history_match × 0.3
)
```

### 13.6 群体情感聚合

```python
weights = exp(linspace(-1, 0, n))
weights /= weights.sum()
avg_valence = sum(e.valence × w for e, w in zip(emotions, weights))
avg_arousal = sum(e.arousal × w for e, w in zip(emotions, weights))
```

---

> **文档版本控制**
> 
> 本文档随代码迭代同步更新。每次架构变更时，相应章节必须更新，确保文档与实现一致。
