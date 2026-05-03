# 表情包 RAG 系统（Sticker RAG System）

> 版本：1.0.0  
> 作者：Sirius Chat  
> 最后更新：2026-05-04

---

## 1. 系统概述

表情包 RAG 系统是 Sirius Chat 框架的**内置 SKILL 扩展**，赋予 AI 人格**自动学习群聊表情包**、**根据语境智能检索**、**按人格偏好发送**的能力。

核心设计理念：
- **观察学习**：AI 像人类一样，通过观察群友在什么情境下发什么表情包来学习
- **人格化**：不同人格有不同的表情包品味，系统自动维护偏好档案
- **动态适应**：根据群友反馈不断调整，模拟"喜新厌旧"等人类习惯

---

## 2. 系统架构

```mermaid
flowchart TB
    subgraph Engine["EmotionalGroupChatEngine"]
        direction TB
        CA[Cognition Analyzer]
        IV3[IntentAnalysisV3]
        IC[image_caption]
        SL[StickerLearner<br/>自动学习器]
        SRS[StickerRAGSkill<br/>SKILL 调用]
        SFO[StickerFeedbackObserver<br/>反馈观察器]
        SP[StickerPreference<br/>人格偏好档案]
        SVS[StickerVectorStore<br/>ChromaDB 向量存储]
    end

    CA --> IV3
    IV3 --> IC
    IC -->|新表情包发现| SL
    SL -->|存入| SVS
    SRS -->|加载| SP
    SRS -->|检索| SVS
    SRS -->|发送后| SFO
    SFO -->|更新| SP
    SFO -->|更新使用次数| SVS

    style Engine fill:#f9f,stroke:#333,stroke-width:2px
    style CA fill:#bbf,stroke:#333
    style SL fill:#bfb,stroke:#333
    style SRS fill:#fbb,stroke:#333
    style SFO fill:#ffb,stroke:#333
```

---

## 3. 数据模型

### 3.1 StickerRecord（表情包记录）

```python
@dataclass
class StickerRecord:
    sticker_id: str                    # MD5 哈希，唯一标识
    file_path: str                     # 本地图片路径
    caption: str                       # cognition 生成的图片描述（辅助理解）
    usage_context: str                 # 核心：使用该表情包时的对话情境
    trigger_message: str               # 触发该表情包的消息内容
    trigger_emotion: str               # 触发时的情绪标签
    source_user: str                   # 发送者
    source_group: str                  # 群号
    discovered_at: str                 # 首次发现时间（ISO）
    last_used_at: str | None           # 上次使用时间
    usage_count: int                   # 被当前人格使用次数
    tags: list[str]                    # LLM 提取的标签（情绪/场景/风格）
    usage_context_embedding: list[float]  # 核心检索向量：usage_context 的语义向量（512 维）
    caption_embedding: list[float]     # 辅助向量：caption 的语义向量
    novelty_score: float               # 新鲜度（0-1，1=全新）
```

**存储位置**：`data/personas/{persona_name}/skill_data/stickers/records/{sticker_id}.json`

### 3.2 StickerPreference（人格偏好档案）

```python
@dataclass
class StickerPreference:
    preferred_tags: list[str]        # 喜欢的标签（如"傲娇","猫咪"）
    avoided_tags: list[str]          # 回避的标签（如"卖萌","撒娇"）
    style_weights: dict[str, float]  # 风格权重（cute:0.3, sarcastic:0.7）
    tag_success_rate: dict[str, float]   # 标签→成功率（运行时学习）
    novelty_preference: float        # 喜新程度（0=恋旧，1=追新）
    emotion_tag_map: dict[str, list[str]]  # 情绪→标签映射
    recent_usage_window: list[dict]  # 近期使用记录（模拟"一段时间内偏爱"）
    group_tag_feedback: dict[str, float]   # 群聊标签反馈
```

**存储位置**：`data/personas/{persona_name}/skill_data/stickers/sticker_preference.json`

---

## 4. 核心模块详解

### 4.1 StickerVectorStore（向量存储）

基于 **ChromaDB** 实现，每个人格拥有独立的 collection：

```python
# Collection 命名规则
collection_name = f"sticker_{persona_name}"  # 如 "sticker_傲娇少女"
```

**复用策略**：与日记系统的 `DiaryVectorStore` 共用同一套 ChromaDB 基础设施，但 collection 前缀不同（`sticker_` vs `diary_`），避免数据混淆。

**Embedding 模型**：`BAAI/bge-small-zh`（512 维），与日记系统共用同一模型实例，避免重复加载内存。模型从本地缓存加载，不连接 HuggingFace Hub。

### 4.2 StickerIndexer（语义索引）

#### 4.2.1 检索流程

```mermaid
flowchart TD
    Start([输入: current_context + emotion_hint]) --> BuildCtx[构建情境<br/>最近消息 + 当前消息 + 情绪]
    BuildCtx --> Encode[语义编码<br/>BAAI/bge-small-zh]
    Encode --> SemanticSearch[向量检索<br/>ChromaDB top_k=40<br/>按 usage_context 匹配]
    BuildCtx --> KeywordSearch[关键词检索<br/>usage_context/tags/trigger_message]
    SemanticSearch --> Merge[候选合并<br/>语义 ∪ 关键词]
    KeywordSearch --> Merge
    Merge --> Score[多维度评分<br/>见 4.2.2]
    Score --> Filter[阈值过滤<br/>score >= 0.5]
    Filter --> WeightedRandom[加权随机选择<br/>按 score² 加权]
    WeightedRandom --> Output([输出: StickerRecord])

    style Start fill:#e1f5fe
    style Output fill:#e8f5e9
    style Score fill:#fff3e0
    style BuildCtx fill:#e3f2fd
```

#### 4.2.2 评分公式

```python
final_score = (
    base_score * 0.5 +                    # 语义相似度(60%) + 关键词(40%)
    tag_bonus - tag_penalty +             # 人格偏好匹配
    tag_success_bonus +                   # 标签成功率加权
    emotion_bonus +                       # 情绪匹配
    novelty_boost -                       # 新鲜度（喜新厌旧）
    overuse_penalty -                     # 使用频率惩罚
    recent_penalty +                      # 近期使用惩罚
    group_feedback_bonus                  # 群聊反馈加权
)
```

| 维度 | 说明 | 权重 |
|------|------|------|
| 语义相似度 | current_context 与 usage_context 的 cosine similarity | 0.6 |
| 关键词匹配 | current_context 在 usage_context/tags/trigger_message 中的匹配 | 0.4 |
| 偏好标签奖励 | preferred_tags 命中 +0.12/个 | - |
| 回避标签惩罚 | avoided_tags 命中 -0.2/个 | - |
| 标签成功率 | tag_success_rate 偏离 0.5 的部分 * 0.1 | - |
| 情绪匹配 | emotion_tag_map 命中 +0.15/个 | - |
| 新鲜度 | novelty_score * novelty_preference * 0.25 | - |
| 使用频率 | usage_count * 0.06，上限 0.35 | - |
| 近期使用 | 近期窗口内使用 >=3 次，额外惩罚 | - |
| 群聊反馈 | group_tag_feedback 偏离 0.5 的部分 * 0.08 | - |

### 4.3 StickerLearner（自动学习器）

#### 4.3.1 学习触发条件

在 `_background_update` 中检测：
```python
if intent.image_caption and getattr(message, "multimodal_inputs", None):
    self._learn_sticker_from_message(message, intent, group_id, user_id)
```

即：**消息包含动画表情（`sub_type=1`）且 cognition 成功生成了图片描述**。

> **注意**：只有动画表情（`sub_type=1`）会被学习，普通图片（`sub_type=0` 或不存在）不会被当作表情包处理。这是因为普通图片（如照片、截图）通常不具备表情包的表达性和复用性。

#### 4.3.2 情境构建

学习时，从群聊上下文中构建 `usage_context`：

```python
def _build_usage_context(self, group_id, trigger_message, source_user):
    context_parts = []
    # 获取最近 3 条消息
    if self._basic_memory is not None:
        recent = self._basic_memory.get_recent_messages(group_id, limit=3)
        for msg in recent:
            context_parts.append(f"{speaker}: {content}")
    # 追加触发消息
    context_parts.append(f"{source_user}: {trigger_message}")
    return "\n".join(context_parts)
```

**核心思想**：记录的不是"表情包画了什么"，而是"在什么对话情境下有人发了这个表情包"。例如：
- 群友说"今天又要加班"后，有人发了"猫咪瘫倒"的表情包
- 群友说"哈哈哈"后，有人发了"笑哭"的表情包

这样 AI 检索时，就能在类似情境下找到合适的表情包。

#### 4.3.3 标签生成

**有 LLM 时**（推荐）：
```python
prompt = f"""
表情包描述：{caption}
使用情境：{usage_context}
发送者：{source_user}

提取 3-5 个标签，涵盖情绪、场景、风格。
输出 JSON：{{"tags": ["标签1", ...]}}
"""
```

**无 LLM 时**（降级）：基于关键词映射的简单提取：
- 情绪关键词：开心、难过、生气、无奈、疲惫、惊讶...
- 风格关键词：猫咪、狗狗、可爱、沙雕、正经...

### 4.4 StickerPreferenceManager（偏好管理）

#### 4.4.1 偏好自动生成

在 Engine 初始化时，如果偏好文件不存在，自动调用 LLM 生成：

```python
prompt = f"""
人格名称：{persona.name}
性格特点：{persona.personality_traits}
说话风格：{persona.communication_style}
社交角色：{persona.social_role}
幽默风格：{persona.humor_style}

推断该角色喜欢的表情包风格。
输出 JSON：
{{
  "preferred_tags": [...],
  "avoided_tags": [...],
  "style_weights": {{...}},
  "novelty_preference": 0.5,
  "emotion_tag_map": {{...}}
}}
"""
```

#### 4.4.2 运行时学习

| 学习类型 | 触发条件 | 更新内容 |
|----------|----------|----------|
| 使用记录 | 每次发送表情包 | recent_usage_window |
| 标签成功率 | 反馈观察后 | tag_success_rate |
| 群聊反馈 | 反馈观察后 | group_tag_feedback |

### 4.5 StickerFeedbackObserver（反馈观察器）

#### 4.5.1 观察流程

```mermaid
sequenceDiagram
    participant AI as AI人格
    participant Group as 群聊
    participant Observer as FeedbackObserver

    AI->>Group: 发送表情包
    AI->>Observer: 启动观察任务
    Note over Observer: 等待 15 秒
    Observer->>Group: 获取后续 10 条消息
    Group-->>Observer: 返回消息列表
    Note over Observer: 匹配正面/负面信号词
    Observer->>Observer: 更新 tag_success_rate
    Observer->>Observer: 更新 group_tag_feedback
    Observer->>Observer: 更新使用次数
```

#### 4.5.2 信号词

**正面**："哈哈", "笑死", "确实", "太真实了", "保存了", "好图", "绝了", "妙啊", "可以", "不错"

**负面**："?", "无语", "尴尬", "冷", "没意思", "不好笑", "算了"

#### 4.5.3 新鲜度衰减（每小时后台任务）

```python
base_decay = 0.95 ** days_since_discovery      # 随发现时间衰减
usage_decay = 0.9 ** usage_count                # 随使用次数衰减
time_decay = 0.98 ** days_since_used            # 随未使用时间衰减
novelty_score = max(0.1, base_decay * usage_decay * time_decay)
```

---

## 5. SKILL 接口

### 5.1 调用方式

模型在生成回复时，如果判断需要发送表情包，会输出：

```
[SKILL_CALL: send_sticker | {"emotion_hint": "sadness"}]
```

### 5.2 参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| emotion_hint | str | 否 | 情绪倾向（joy/anger/sadness/anxiety/neutral） |

> **注意**：`send_sticker` 不再需要 `query` 参数。系统会自动从当前聊天上下文中构建 `current_context`（最近消息 + 当前消息 + 情绪），然后检索与该情境最匹配的表情包。

### 5.3 返回值

```json
{
  "success": true,
  "sticker_id": "a1b2c3d4...",
  "file_path": "data/personas/xxx/image_cache/...",
  "caption": "一只橘猫躺在键盘上...",
  "tags": ["猫咪", "疲惫", "可爱"]
}
```

---

## 6. 集成点

### 6.1 Engine 初始化（engine_core.py）

```python
def _init_sticker_system(self) -> None:
    from sirius_chat.skills.builtin.send_sticker import init_sticker_system
    self._sticker_system = init_sticker_system(
        work_path=self.work_path,
        persona_name=self.persona.name,
        provider_async=self.provider_async,
        basic_memory=self.basic_memory,
    )
    # 自动生成偏好（如果不存在）
    if not pref_manager._file_path.exists():
        asyncio.create_task(
            pref_manager.generate_from_persona(self.persona, self.provider_async)
        )
```

### 6.2 消息处理（pipeline.py）

```python
def _background_update(self, group_id, message, emotion, intent, user_id):
    # ... 其他更新 ...
    
    # Sticker learning: only animated stickers (sub_type=1)
    if intent.image_caption and getattr(message, "multimodal_inputs", None):
        self._learn_sticker_from_message(message, intent, group_id, user_id)
```

### 6.3 后台任务（bg_tasks.py）

```python
async def _bg_sticker_novelty_updater(self) -> None:
    """每小时更新一次新鲜度分数"""
    interval = 3600  # 秒
    while self._bg_running:
        await asyncio.sleep(interval)
        await feedback_observer.update_novelty_scores()
```

---

## 7. 数据存储

### 7.1 目录结构

```
data/personas/{persona_name}/
└── skill_data/
    └── stickers/                    # 表情包系统工作目录
        ├── records/                 # JSON 格式的 StickerRecord
        │   ├── a1b2c3d4.json
        │   └── e5f6g7h8.json
        ├── vector_store/            # ChromaDB 持久化
        │   ├── chroma.sqlite3
        │   └── ...
        └── sticker_preference.json  # 人格偏好档案
```

### 7.2 人格隔离

- **向量存储**：每个人格独立的 ChromaDB collection（`sticker_{persona_name}`）
- **记录文件**：每个人格独立的 records 目录
- **偏好档案**：每个人格独立的 sticker_preference.json

---

## 8. 使用示例

### 8.1 群友发送表情包（自动学习）

```mermaid
sequenceDiagram
    participant User as 群友A
    participant Engine as EmotionalGroupChatEngine
    participant CA as Cognition Analyzer
    participant SL as StickerLearner
    participant SVS as StickerVectorStore

    User->>Engine: [动画表情: 橘猫躺在键盘上]
    Note over Engine: sub_type=1 (动画表情)
    Engine->>CA: 分析消息（多模态）
    CA-->>Engine: image_caption = "一只橘猫...不想上班"
    Engine->>SL: 调用 learn_from_message()
    SL->>SL: sticker_id = md5(file_path)
    SL->>SL: usage_context = "群友A: 今天又要加班\n群友B: 是啊好累"
    SL->>SL: tags = ["猫咪", "疲惫", "可爱"] (LLM生成)
    SL->>SL: usage_context_embedding = encode(usage_context)
    SL->>SVS: add(record)
    SVS-->>SL: 存储完成
    SL-->>Engine: 学习完成
```

### 8.2 AI 发送表情包（检索）

```mermaid
sequenceDiagram
    participant User as 群友B
    participant Engine as EmotionalGroupChatEngine
    participant SRS as StickerRAGSkill
    participant SI as StickerIndexer
    participant SP as StickerPreference
    participant SVS as StickerVectorStore
    participant Bridge as NapCatBridge
    participant SFO as StickerFeedbackObserver

    User->>Engine: "今天又要加班，好累啊"
    Engine->>Engine: 判断需要安慰/共鸣
    Engine->>SRS: [SKILL_CALL: send_sticker]
    SRS->>SP: 加载人格偏好
    SP-->>SRS: preferred_tags, avoided_tags, emotion_tag_map
    SRS->>SI: search(current_context="群友B: 今天又要加班，好累啊", emotion="sadness")
    SI->>SVS: 向量检索 top_k=40
    SVS-->>SI: 候选列表
    SI->>SI: 关键词检索
    SI->>SI: 多维度评分
    Note over SI: 语义60% + 关键词40%<br/>+ 偏好匹配 + 情绪匹配<br/>+ 新鲜度 - 使用频率惩罚
    SI->>SI: 加权随机选择
    SI-->>SRS: 选中橘猫表情包
    SRS->>Bridge: 发送图片
    Bridge-->>SRS: 发送成功
    SRS-->>Engine: 返回结果
    SRS->>SFO: 启动反馈观察（15秒后）
    Note over SFO: 15秒后...
    SFO->>Engine: 获取后续消息
    Engine-->>SFO: 群友C: "哈哈太真实了"
    SFO->>SFO: 匹配正面信号
    SFO->>SP: tag_success_rate["猫咪"] += 0.05
```

---

## 9. 配置项

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| sticker_novelty_update_interval_seconds | 3600 | 新鲜度更新间隔（秒） |

---

## 10. 故障排除

| 问题 | 可能原因 | 解决方案 |
|------|----------|----------|
| 表情包检索为空 | 库中无记录 | 等待群友发送表情包自动学习，或手动导入 |
| 向量存储初始化失败 | chromadb 未安装 | `pip install chromadb` |
| 模型加载失败 | sentence-transformers 未安装 | `pip install sentence-transformers` |
| 标签生成失败 | LLM provider 不可用 | 降级为关键词映射提取 |
| 发送失败 | 图片文件不存在 | 检查 image_cache 目录 |

---

## 11. 未来扩展

1. **多模态 Embedding**：使用 CLIP 等视觉模型直接编码图片，而非依赖文本描述
2. **表情包推荐**：主动推荐新收集的表情包给群友
3. **跨人格共享**：允许人格间共享表情包库，但保留各自偏好
4. **表情包生成**：集成 AI 图像生成，根据语境实时生成表情包
