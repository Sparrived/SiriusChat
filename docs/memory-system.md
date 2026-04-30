# 记忆系统（Memory System）

> **v1.0 简化架构** — 基础记忆 → 日记记忆 → 用户管理 → 名词解释

## 一句话定位

记忆系统负责让引擎**记得住上下文、回忆得起往事、理解得了关系**。v1.0 采用极简四层模型：基础记忆保当下、日记记忆存长期、用户管理识身份、名词解释积知识。

## 架构总览

```
┌─────────────────────────────────────────────────────────────┐
│  基础记忆 (BasicMemory)      ──  短期注意力窗口 + 热度计算      │
│  日记记忆 (DiaryMemory)      ──  LLM 生成摘要 + 索引检索        │
│  用户管理 (UserManager)      ──  极简 UserProfile + 群隔离      │
│  名词解释 (GlossaryManager)  ──  AI 自身知识库                  │
└─────────────────────────────────────────────────────────────┘
         ↑
   ContextAssembler ── 将基础记忆 + 日记记忆组装为 OpenAI messages
```

| 模块 | 定位 | 核心能力 |
|------|------|---------|
| **BasicMemoryManager** | 短期上下文 | 按群滑动窗口（硬限制 30，上下文窗口 5），热度计算，归档 |
| **DiaryManager** | 长期摘要 | LLM 生成群聊日记，关键词/嵌入索引，token 预算检索 |
| **UserManager** | 身份管理 | 极简 `UserProfile`，群隔离存储，跨平台身份追踪 |
| **GlossaryManager** | 知识库 | 名词解释，由 `learn_term` SKILL 写入 |
| **ContextAssembler** | 上下文组装 | 将基础记忆 + 日记检索组装为标准 OpenAI messages |
| **IdentityResolver** | 身份解析 | 解耦平台特定身份（QQ/discord 等），多级解析 |

---

## 基础记忆（Basic Memory）

**定位**：短期注意力窗口，纯粹内存中的热数据。

每个群聊有自己独立的窗口：
- **硬限制**：30 条（`HARD_LIMIT = 30`）
- **上下文窗口**：5 条（`CONTEXT_WINDOW = 5`），直接用于 prompt
- **热度计算**：`RhythmAnalyzer` 基于消息速率、独特发言者、最近度计算群体热度（0~1）

**数据结构**：
```python
@dataclass
class BasicMemoryEntry:
    entry_id: str      # UUID
    group_id: str
    user_id: str
    role: str          # "user" / "assistant" / "system"
    content: str
    timestamp: float
    system_prompt: str = ""
```

**热度计算**：
```
heat = message_rate_factor × unique_speakers_factor × recency_factor
```
- `message_rate_factor`：最近 N 条消息的间隔倒数
- `unique_speakers_factor`：最近发言者数量 / 10
- `recency_factor`：最后一条消息的指数衰减

**冷群检测**：
- `heat < 0.25`（COLD_THRESHOLD）
- 且沉默 > 300 秒（SILENCE_THRESHOLD_SEC）

当群体变冷时，上下文窗口外的消息被归档为**日记素材**，后续由 `DiaryGenerator` 生成日记。

**持久化**：基础记忆归档存储在 `memory/basic/<group_id>.jsonl`。

---

## 日记记忆（Diary Memory）

**定位**：LLM 生成的群聊摘要，连接短期上下文与长期认知。

### 核心组件

| 组件 | 职责 |
|------|------|
| `DiaryGenerator` | 从基础记忆归档中批量生成日记条目（异步 LLM 调用） |
| `DiaryIndexer` | 关键词索引 + 可选 sentence-transformers 嵌入索引 |
| `DiaryRetriever` | token 预算 aware 检索（默认 800 tokens ≈ 1200 字符） |

### 日记条目结构

```python
@dataclass
class DiaryEntry:
    entry_id: str
    group_id: str
    content: str       # LLM 生成的摘要
    keywords: list[str]
    summary: str       # 一句话概括
    source_ids: list[str]  # 回链基础记忆的 entry_id
    timestamp: float
```

### 生成流程

```
基础记忆归档（冷群消息）
    │
    ▼
DiaryGenerator.generate(group_id, candidates, persona, provider)
    │  （构建 prompt：persona + 消息列表）
    ▼
LLM 返回 JSON：content / keywords / summary / source_ids
    │
    ▼
DiaryManager.add_entry(entry) → DiaryIndexer.add(entry)
    │
    ▼
persistent to memory/diary/<group_id>.jsonl
index to memory/diary/index/<group_id>.json
```

### 检索流程

```
query (当前消息内容)
    │
    ▼
DiaryIndexer.search(query, top_k=5)
    ├─ 关键词匹配（始终可用）
    └─ 嵌入余弦相似度（若 sentence-transformers 安装）
    │
    ▼
DiaryRetriever.retrieve(query, group_id, top_k=5, max_tokens_budget=800)
    │  （按相关性排序，然后按 token 预算截断）
    ▼
返回 DiaryEntry 列表 → 注入 system_prompt 作为「历史日记」
```

> **注意**：日记内容注入 `system_prompt`，不污染 `messages` 历史。这避免了长对话中消息数组无限增长。

---

## 用户管理（UserManager）

**定位**：极简身份系统，群隔离存储。

### UserProfile

```python
@dataclass(slots=True)
class UserProfile:
    user_id: str
    name: str
    persona: str = ""
    aliases: list[str] = field(default_factory=list)
    identities: dict[str, str] = field(default_factory=dict)
    traits: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
```

**字段说明**：
- `user_id`：唯一标识符
- `name`：显示名
- `aliases`：别名列表，用于 speaker name 解析
- `identities`：跨平台身份映射 `{platform: external_uid}`
- `metadata`：扩展属性（如 `is_developer`、`inferred_persona`、`preference`）

### 存储结构

```python
# {group_id: {user_id: UserProfile}}
entries: dict[str, dict[str, UserProfile]]
```

### 身份解析（IdentityResolver）

解耦平台特定身份与框架内部：

```
speaker_name ──→ UserManager._speaker_index ──→ user_id
                    ↓
platform + external_uid ──→ UserManager._identity_index ──→ user_id
```

`IdentityContext` 提供统一的身份上下文：
```python
@dataclass
class IdentityContext:
    speaker_name: str
    user_id: str
    platform_uid: str | None = None
    platform: str | None = None
    is_developer: bool = False
```

**跨平台追踪**：
- 用户 A 在 QQ 叫 "Alice"，在 Discord 叫 "alice#1234"
- `identities = {"qq": "qq_456", "discord": "alice#1234"}`
- `IdentityResolver` 通过任意平台的 UID 解析到同一 `UserProfile`

---

## 名词解释（GlossaryManager）

**定位**：AI 自身知识库，替代旧 `AutobiographicalMemoryManager`。

### 条目结构

```python
@dataclass
class GlossaryTerm:
    term: str
    definition: str
    confidence: float       # 0.0~1.0
    source_group_id: str
    added_at: str
```

### 使用方式

1. **SKILL 写入**：内置 `learn_term` 技能被触发时，调用 `GlossaryManager.add_term()`
2. **Prompt 注入**：`ResponseAssembler` 检测到当前群存在 glossary 条目时，将其注入 system_prompt
3. **持久化**：`memory/glossary/terms.json`

---

## 上下文组装（ContextAssembler）

**定位**：将各记忆模块的输出组装为标准 OpenAI messages 数组。

### 组装流程

```python
def build_messages(
    self,
    group_id: str,
    current_query: str,
    system_prompt: str,
    recent_n: int = 5,
    diary_top_k: int = 5,
) -> list[dict]:
    # 1. 获取基础记忆最近 n 条
    recent = self.basic.get_context(group_id, recent_n)

    # 2. 检索相关日记
    diaries = self.diary.retrieve(current_query, group_id, diary_top_k)

    # 3. 丰富 system_prompt
    enriched_system = system_prompt
    if diaries:
        diary_text = "\n".join(d.content for d in diaries)
        enriched_system += f"\n\n[历史日记]\n{diary_text}"

    # 4. 组装 messages
    messages = [{"role": "system", "content": enriched_system}]
    messages += [{"role": e.role, "content": e.content} for e in recent]
    return messages
```

**设计原则**：
- 日记内容放在 `system_prompt` 中，不进入 `messages` 历史
- 基础记忆只保留最近窗口，避免消息数组无限增长
- 日记检索按 token 预算截断，控制 prompt 长度

---

## 数据流转全景

```
新消息进来
    │
    ▼
[IdentityResolver.resolve()] → 解析跨平台身份
    │
    ▼
[UserManager.register()] → 注册/更新用户（群隔离）
    │
    ▼
[BasicMemoryManager.add_entry()] → 加入窗口，计算热度
    │
    ▼
[ContextAssembler.build_messages()] → 基础记忆 + 日记检索 → OpenAI messages
    │
    ▼
[LLM 生成回复]
    │
    ▼
[BasicMemoryManager.add_entry()] → 记录 assistant 回复
    │
    ▼
后台：[_bg_diary_promoter] → 检查冷群 → DiaryGenerator 生成日记
```

---

## 与其他系统的关系

| 交互对象 | 方式 |
|---------|------|
| **EmotionalGroupChatEngine** | 持有 BasicMemoryManager、DiaryManager、UserManager、GlossaryManager 实例，调用 `process_message()` |
| **ContextAssembler** | 被引擎调用，组装 prompt；注入日记内容和基础记忆 |
| **IdentityResolver** | 在感知层解析身份，独立于平台特定逻辑 |
| **Background Tasks** | `_bg_diary_promoter` 检查冷群，触发日记生成 |
| **SKILL 系统** | `learn_term` 写入 GlossaryManager；其他 SKILL 通过 `data_store` 独立持久化 |

---

## 存储路径

| 数据 | 路径 | 生产者 |
|------|------|--------|
| 基础记忆归档 | `memory/basic/<group_id>.jsonl` | `BasicMemoryFileStore` |
| 日记条目 | `memory/diary/<group_id>.jsonl` | `DiaryManager` |
| 日记索引 | `memory/diary/index/<group_id>.json` | `DiaryIndexer` |
| 用户档案 | `user_memory/groups/<group_id>/<user_id>.json` | `UserManager` |
| 名词解释 | `memory/glossary/terms.json` | `GlossaryManager` |
| 引擎状态 | `memory/basic_state.json` / `memory/diary_state.json` | `save_state()` / `load_state()` |
