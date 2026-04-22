# EmotionalGroupChatEngine（情感化群聊引擎）

> **v1.0 默认引擎**，替代 legacy `AsyncRolePlayEngine`（已完全移除）。

## 一句话定位

EmotionalGroupChatEngine 是一个**让 AI 角色像真人一样在群聊里说话**的引擎——它会看气氛、挑话题、等时机、有情绪、记仇也记好。

## 架构总览：四层认知管线

引擎把"收到一条消息到决定是否回复"拆解成四个层次，每层只做一件事：

```
┌─────────────────────────────────────────────────────────────┐
│  感知层 Perception    ──  接收消息，更新上下文                │
│  认知层 Cognition     ──  分析情绪、意图、检索记忆            │
│  决策层 Decision      ──  判断要不要回、什么时候回            │
│  执行层 Execution     ──  生成回复，调用 SKILL                │
└─────────────────────────────────────────────────────────────┘
```

### 感知层（Perception）

**做什么**：最轻量的一步，同步完成。

1. `IdentityResolver.resolve()` 解析跨平台身份（speaker_name → user_id → platform_uid）
2. `UserManager.register()` 注册/更新用户（群隔离的极简 `UserProfile`）
3. `BasicMemoryManager.add_entry()` 写入按群滑动窗口（硬限制 30 条，上下文窗口 5 条）
4. `HeatCalculator.compute_heat()` 更新群体热度（0~1）
5. 更新群聊活跃度时间戳

**设计意图**：感知层不碰 LLM，保证高吞吐。即使群里消息刷得飞快，这一步也不会卡住。

### 认知层（Cognition）

**做什么**：统一分析情绪、意图、检索记忆。单层联合推断，情绪结果自然流入意图评分。

| 组件 | 输入 | 输出 | 默认模型 |
|------|------|------|---------|
| **CognitionAnalyzer** | 消息内容 + 上下文 | `EmotionState` + `IntentAnalysisV3` + `EmpathyStrategy` | gpt-4o-mini |
| **记忆检索** | `BasicMemoryManager.get_context()` + `DiaryManager.retrieve()` | 最近窗口 + 相关日记 | 无（本地检索） |

**统一分析器**（`CognitionAnalyzer`）：
- 联合规则引擎同时推断情绪和意图，共享上下文
- 热路径零 LLM 成本（~90% 命中率）
- 复杂情况单次 LLM fallback（~10% 命中率）
- 情绪结果直接用于意图紧急度评分，无需额外异步边界

**情绪状态**是一个二维坐标：
- `valence`（愉悦度）：-1（极不爽）~ +1（极开心）
- `arousal`（唤醒度）：0（平静）~ 1（激动）
- 基本情绪：从 19 种映射（joy, anger, sadness, fear, disgust, surprise, trust, anticipation...）

**社交意图** 按目的分类：
- `help_seeking` — 求助（"有人知道这个怎么弄吗"）
- `emotional` — 情感表达（"今天好烦"）
- `social` — 社交互动（"哈哈哈"）
- `silent` — 无明确意图（纯信息分享）

每条意图附带 `urgency_score`（0~100）和 `relevance_score`（0~1），这两个分数直接影响决策层的阈值计算。

### 决策层（Decision）

**做什么**：纯规则计算，零 LLM 成本，决定"回不回复"和"怎么回复"。

**步骤 1：节奏分析（RhythmAnalyzer）**

计算三个指标：
- `heat_level`：cold / warm / hot / overheated（消息频率越高越热）
- `pace`：accelerating / steady / decelerating / silent（消息增速趋势）
- `topic_stability`：0~1（话题是否稳定）

**步骤 2：动态阈值（ThresholdEngine）**

```
threshold = base × activity_factor × relationship_factor × time_factor
```

- `base`：基准阈值（默认 ~0.45）
- `activity_factor`：`heat_level` 越热阈值越高（群里刷消息时你更谨慎）
- `relationship_factor`：关系越近阈值越低（跟熟人更随意）
- `time_factor`：深夜阈值更高（不想打扰）

**人格偏移**：`reply_frequency` 会直接乘在阈值上：
- `high`（话痨）×0.8 — 更容易回复
- `low`（安静）×1.3 — 更谨慎
- `selective`（挑剔）×1.6 — 只回高相关性消息

**步骤 3：策略选择（ResponseStrategyEngine）**

综合 `intent.relevance`、`urgency`、`threshold`、`assistant_emotion` 四个因素，输出四种策略之一：

| 策略 | 行为 |
|------|------|
| **IMMEDIATE** | 立即生成回复 |
| **DELAYED** | 加入延迟队列，等话题间隙再回 |
| **SILENT** | 不回复 |
| **PROACTIVE** | 不回复这条，但标记为可能触发主动发言的候选 |

**助手情感状态（AssistantEmotionState）**：

引擎自己也有情绪。它从 `persona.emotional_baseline` 初始化，然后受用户情绪影响——如果用户很开心，助手也会轻微愉悦；如果用户愤怒，助手会紧张（arousal 上升）。情绪会随时间自然恢复（惯性 + 恢复机制）。

### 执行层（Execution）

**做什么**：按策略生成内容。

**IMMEDIATE 流程**：
1. `ResponseAssembler.assemble()` 拼接 prompt：
   - `[角色剧本]`（persona.build_system_prompt()）
   - `[当下的感觉]`（用户情绪 + 群体氛围 + 助手自身情绪）
   - `[共情策略]`（confirm_action / cognitive / action / share_joy / presence）
   - `[相关记忆]`（基础记忆最近窗口 + 日记检索 top-k）
   - `[术语表]`（glossary_section，来自 `GlossaryManager`）
   - `[群体风格]`（群聊规范 + 长度/温度限制）
   - `[输出格式]`（纯文本回复，可包含内联 `[SKILL_CALL: ...]`）
   - `[消息] xxx`
2. `StyleAdapter.adapt()` 调整参数：
   - `max_tokens`：由 heat/pace 决定（cold 256 / warm 128 / hot 80 / overheated 50）
   - `temperature`：由 persona 偏好 + 用户风格决定
   - `length_instruction` / `tone_instruction`
3. `ModelRouter.resolve()` 选择模型：
   - 认知分析 → gpt-4o-mini（便宜、快）
   - 回复生成 → gpt-4o（质量好）
   - 日记生成 → gpt-4o-mini（冷群时批量生成日记）
   - urgency > 80 → 升级更强模型，降低 temperature
4. `_generate()` 调用 provider，估算 token 用量
5. `_process_skill_calls()` 解析并执行 `[SKILL_CALL: ...]` 标记（内置技能含 `learn_term`、`url_content_reader`、`bing_search`；`silent=True` 时结果不追加到回复文本）

> **注意**：v0.28+ 已完全移除 `<think>` / `<say>` 双输出格式。模型输出纯文本，`SKILL_CALL` 内联在回复中。`parse_dual_output()` 现在直接返回 `("", raw.strip())`。

**DELAYED 流程**：
- 把消息元数据加入 `DelayedResponseQueue`
- 后台 ticker（每 10 秒）检查话题间隙
- 当检测到"最近 N 秒无消息"或"话题切换"时，触发延迟回复生成

**PROACTIVE 流程**：
- 后台 checker（每 60 秒）检查沉默过久的群聊
- 当 `ProactiveTrigger` 判定条件满足（时间/记忆/情感三种触发类型），生成主动发言
- 主动话题从 `SemanticMemoryManager` stub 的 `interest_topics`、`group_norms.dominant_topic` 中选取（保留兼容）

## 后台任务

引擎启动后会创建 4 个 `asyncio.Task`：

| 任务 | 间隔 | 职责 |
|------|------|------|
| **延迟队列 ticker** | 10 秒 | 扫描所有群聊的延迟队列，检测话题间隙并触发回复 |
| **主动触发 checker** | 60 秒 | 检查沉默群聊，决定是否主动开口 |
| **日记生成 promoter** | 可配置 | 检查冷群（heat < 0.25 且沉默 > 300s）的基础记忆归档，经 `DiaryGenerator` 生成日记并写入 `DiaryManager` |

这些任务的生命周期由引擎自己管理（`start_background_tasks()` / `stop_background_tasks()`），WorkspaceRuntime 不负责启动它们。

## 事件总线（Event Bus）

引擎在处理每条消息时会发射 4 个认知事件：

```
PERCEPTION_COMPLETED → COGNITION_COMPLETED → DECISION_COMPLETED → EXECUTION_COMPLETED
```

外加 2 个后台事件：
```
DELAYED_RESPONSE_TRIGGERED → PROACTIVE_RESPONSE_TRIGGERED
```

外部可以通过 `SessionEventBus.subscribe()` 拿到 `AsyncIterator` 实时监听这些事件。事件总线是**有损广播**——如果消费者慢了，队列满后事件会被丢弃，不会阻塞引擎。

## 状态持久化

引擎在 `save_state()` 时持久化以下内容到 `{work_path}/memory/`：

- `basic_state.json` — 各群聊的基础记忆窗口
- `diary_state.json` — 日记索引状态
- `assistant_emotion.json` — 助手自身情感状态
- `group_timestamps.json` — 群聊活跃度时间戳
- `token_usage_records.json` — token 用量统计
- `proactive_state.json` — 主动发言启用/禁用状态

## 使用方式

```python
from sirius_chat.api import create_emotional_engine
from sirius_chat.core.persona_generator import PersonaGenerator

engine = create_emotional_engine(
    work_path="/path/to/workspace",
    provider=provider,
    persona=PersonaGenerator.from_template("sarcastic_techie"),
    config={
        "sensitivity": 0.6,
        "proactive_silence_minutes": 20,
        "basic_memory_hard_limit": 30,   # 基础记忆硬限制
        "basic_memory_context_window": 5,  # 上下文窗口大小
    },
)
engine.start_background_tasks()

# 处理消息
result = await engine.process_message(
    message=Message(role="human", content="今天工作好累"),
    group_id="g123",
    participants=[...],
)
# result["reply"] 为 None 表示决定不回复（SILENT / DELAYED / PROACTIVE）
# result["thought"] 保留字段，当前实现为空字符串（dual-output 已移除）
