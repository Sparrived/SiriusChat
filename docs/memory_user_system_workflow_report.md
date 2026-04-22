# Sirius Chat 记忆系统与用户系统工作流程报告

> 版本：v1.0
> 生成日期：2026-04-22
> 基于代码库全量分析

---

## 1. 系统架构总览

Sirius Chat v1.0 采用**简化两层记忆模型** + **AI 自我记忆**的设计：

```
┌─────────────────────────────────────────────────────────────┐
│                     认知架构（四层）                           │
├─────────────┬─────────────┬─────────────┬───────────────────┤
│  感知层      │  认知层      │  决策层      │     执行层         │
│ Perception  │  Cognition  │  Decision   │    Execution      │
├─────────────┴─────────────┴─────────────┴───────────────────┤
│                     记忆底座（两层）                           │
├─────────────────────────┬───────────────────────────────────┤
│   基础记忆 (Basic)       │         日记记忆 (Diary)           │
│   FIFO + 热度窗口        │   LLM 总结 + 关键词/向量检索        │
├─────────────────────────┴───────────────────────────────────┤
│                  辅助记忆子系统                                │
├─────────────────────────┬───────────────────────────────────┤
│  用户画像 (UserManager)  │   AI 自我记忆 (Glossary)           │
│  群隔离 + 跨平台身份      │   术语知识库                      │
└─────────────────────────┴───────────────────────────────────┘
```

---

## 2. 用户系统详解

### 2.1 用户数据模型

v1.0 使用简化的 `UserProfile` + `UserManager`：

| 层级 | 类名 | 职责 | 文件 |
|------|------|------|------|
| **外部接口层** | `Participant` / `User` | 外部系统传入的用户表示 | `models/models.py` |
| **运行时层** | `UserProfile` | 用户档案（身份、别名、特质、元数据） | `memory/user/simple.py` |

#### UserProfile（简化版）
```python
@dataclass(slots=True)
class UserProfile:
    user_id: str
    name: str
    persona: str = ""
    aliases: list[str] = field(default_factory=list)
    identities: dict[str, str] = field(default_factory=dict)  # platform: external_uid
    traits: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)    # e.g. is_developer
```

#### UserManager
```python
class UserManager:
    # {group_id: {user_id: UserProfile}}
    entries: dict[str, dict[str, UserProfile]]
```

**核心方法：**
- `register(profile, group_id="default")`：注册用户到指定群
- `get_user(user_id, group_id="default")`：按群隔离获取用户
- `resolve_user_id(speaker=None, platform=None, external_uid=None)`：多渠道身份解析

### 2.2 群隔离机制

- 每个群（`group_id`）拥有独立的用户命名空间
- 同一用户在不同群中的档案完全隔离
- 持久化路径：`{work_path}/memory/user/{group_id}/{user_id}.json`

### 2.3 跨平台身份映射

`IdentityResolver` 负责将平台特定 ID 映射到内部 `user_id`：

```
平台消息进入
    │
    ▼
IdentityResolver.resolve(platform, external_uid, speaker_name)
    │
    ├─ 已知 identities → 返回已有 user_id
    └─ 新用户 → 生成 user_id，注册到 UserManager
```

---

## 3. 记忆系统详解

### 3.1 基础记忆（BasicMemoryManager）

**核心数据结构：**

```python
@dataclass(slots=True)
class BasicMemoryEntry:
    entry_id: str
    group_id: str
    user_id: str
    role: str           # "user" | "assistant" | "system"
    content: str
    timestamp: float
    system_prompt: str = ""

class BasicMemoryManager:
    HARD_LIMIT = 30     # 窗口硬上限
    CONTEXT_WINDOW = 5  # 构建 LLM 上下文时保留条数
```

**工作原理：**

1. **FIFO 窗口**：每条消息加入群对应的 deque，超过 `HARD_LIMIT` 时旧消息进入归档
2. **热度跟踪**：`HeatCalculator` 基于消息频率、发言者数、时间衰减计算群热度（0~1）
3. **冷群检测**：`heat < 0.25` 且沉默 `> 300s` 时触发日记生成

**关键方法：**
- `add_entry(group_id, user_id, role, content)`：添加消息到窗口 + 归档
- `get_context(group_id, n=5)`：获取最近 n 条消息用于 prompt
- `get_archive_candidates(group_id)`：获取超出 context window 的归档候选
- `compute_heat(group_id)` / `is_cold(group_id)`：热度计算与检测

### 3.2 日记记忆（DiaryManager）

**核心数据结构：**

```python
@dataclass
class DiaryEntry:
    entry_id: str
    group_id: str
    content: str           # LLM 生成的日记正文
    summary: str           # 一句话摘要
    keywords: list[str]    # 关键词标签
    source_ids: list[str]  # 来源 BasicMemoryEntry ID
    timestamp: float
```

**组件分工：**

| 组件 | 职责 | 文件 |
|------|------|------|
| `DiaryGenerator` | 从归档候选消息生成日记（LLM） | `memory/diary/generator.py` |
| `DiaryIndexer` | 关键词 + sentence-transformers 向量索引 | `memory/diary/indexer.py` |
| `DiaryRetriever` | 按 token 预算检索相关日记 | `memory/diary/retriever.py` |

**检索流程：**

```
用户查询 → DiaryIndexer.search(query, top_k=5)
    │
    ├─ 有 sentence-transformers → 余弦相似度排序
    └─ 无 sentence-transformers → 纯关键词匹配
    │
    ▼
DiaryRetriever.retrieve(query, group_id, top_k, max_tokens_budget=800)
    │
    ▼
按 token 预算截断，返回适配 prompt 的日记列表
```

### 3.3 名词解释（GlossaryManager）

AI 自身的术语知识库，通过 `learn_term` SKILL 或自动提取维护：

```python
@dataclass
class GlossaryTerm:
    term: str
    definition: str
    source: str = "user"   # "user" | "auto"
    timestamp: float
```

- 持久化路径：`{work_path}/memory/glossary/glossary.json`
- 在系统提示词中注入为 "术语表" 段落
- 支持 `glossary_manager.build_prompt_section()` 生成格式化文本

---

## 4. 记忆系统工作流程

### 4.1 消息进入（感知层）

```
群消息进入
    │
    ├─ IdentityResolver → 解析 user_id
    ├─ UserManager.register() → 确保用户档案存在
    ├─ BasicMemoryManager.add_entry() → 写入窗口 + 归档
    └─ 更新群热度
    │
    ▼
```

### 4.2 冷群检测与日记生成（后台）

```
_bg_diary_promoter（每 300 秒）
    │
    ├─ 遍历所有活跃群
    ├─ 检查 is_cold(group_id)：heat < 0.25 且沉默 > 300s
    └─ 获取 archive_candidates（超出 context window 的消息）
    │
    ▼ 群聊变冷
DiaryGenerator.generate(candidates, persona, provider)
    │
    ├─ 构建 prompt：人格 + 候选消息列表
    ├─ LLM 生成 JSON：{content, summary, keywords, source_ids}
    └─ 创建 DiaryEntry，写入 DiaryManager
    │
    ▼
DiaryIndexer.add(entry) → 计算 embedding + 关键词索引
```

### 4.3 回复生成时的上下文组装（执行层）

```
ContextAssembler.build_messages(group_id, current_query, system_prompt)
    │
    ├─ BasicMemoryManager.get_context(group_id, n=5) → 最近 5 条消息
    ├─ DiaryRetriever.retrieve(current_query, group_id, top_k=5, budget=800)
    │
    ▼
组装 OpenAI 风格 messages：
    [
      {"role": "system", "content": enriched_system + 历史日记},
      {"role": "user", "content": "..."},   # 最近消息
      {"role": "assistant", "content": "..."},
      ...
    ]
```

---

## 5. 群聊处理流程

```
群消息到达
    │
    ├─ 解析身份 → user_id
    ├─ 注册/更新用户档案
    ├─ 写入基础记忆
    ├─ 更新群热度
    │
    ▼
认知层（并行）
    ├─ IntentAnalyzer v3 → social_intent + urgency + relevance
    ├─ EmotionAnalyzer → EmotionState(valence, arousal, basic_emotion)
    └─ 检索日记 → 相关历史上下文
    │
    ▼
决策层
    ├─ RhythmAnalyzer → heat_level + pace
    ├─ ThresholdEngine → dynamic engagement threshold
    └─ ResponseStrategyEngine → IMMEDIATE / DELAYED / SILENT / PROACTIVE
    │
    ▼
执行层
    ├─ ContextAssembler → 基础记忆 + 日记 → messages
    ├─ StyleAdapter → max_tokens / temperature / tone
    ├─ ModelRouter → 任务感知模型选择
    └─ LLM 生成回复
    │
    ▼
后台更新层
    ├─ 检查冷群 → 触发日记生成
    ├─ 更新用户情感轨迹
    └─ 触发事件 → event_bus
```

---

## 6. 跨平台身份处理

`IdentityResolver` 将平台特定标识符映射到内部 `user_id`：

```python
class IdentityResolver:
    def resolve(self, platform: str, external_uid: str, speaker_name: str = "") -> IdentityContext:
        """返回内部 user_id 和是否为新用户。"""
```

**映射规则：**
1. 精确匹配 `identities[platform] == external_uid` → 返回已知 user_id
2. 名称匹配 `speaker_name` 在 aliases 中 → 返回已知 user_id
3. 新用户 → 生成新 user_id，创建 UserProfile

**持久化：** `UserProfile.identities` 字典保存跨平台映射关系。

---

## 7. 持久化文件指南

### 7.1 基础记忆

| 文件 | 路径 | 说明 |
|------|------|------|
| 归档存储 | `{work_path}/memory/basic/{group_id}.jsonl` | 超出窗口的消息按群存储 |

### 7.2 日记记忆

| 文件 | 路径 | 说明 |
|------|------|------|
| 日记条目 | `{work_path}/memory/diary/entries.jsonl` | 所有 DiaryEntry |
| 关键词索引 | `{work_path}/memory/diary/keyword_index.json` | 倒排索引 |
| 向量索引 | `{work_path}/memory/diary/embedding_index.npz` | sentence-transformers 向量（可选） |

### 7.3 用户系统

| 文件 | 路径 | 说明 |
|------|------|------|
| 用户档案 | `{work_path}/memory/user/{group_id}/{user_id}.json` | 群隔离的用户 Profile |

### 7.4 名词解释

| 文件 | 路径 | 说明 |
|------|------|------|
| 术语库 | `{work_path}/memory/glossary/glossary.json` | GlossaryTerm 列表 |

### 7.5 引擎状态

| 文件 | 路径 | 说明 |
|------|------|------|
| 引擎状态 | `{work_path}/engine_state/engine_state.json` | 基础记忆窗口、assistant 情绪、群时间戳 |
| 编排配置 | `{work_path}/engine_state/orchestration.json` | 任务模型映射 |

---

## 8. 调试与监控提示

### 8.1 日记生成质量

- 检查 `memory/basic/{group_id}.jsonl` 中归档消息的质量
- 查看 `memory/diary/entries.jsonl` 中日记的 `source_ids` 是否覆盖了关键消息
- 确认 sentence-transformers 已安装（影响检索质量）

### 8.2 热度异常

- 如果群聊始终不冷：检查是否有机器人/自动消息持续更新热度
- 如果群聊过快变冷：调整 `basic_memory_hard_limit` 或检查 HeatCalculator 参数

### 8.3 Token 预算超限

- `diary_token_budget`（默认 800）≈ 1200 字符
- 如果日记内容过长，DiaryRetriever 会自动截断
- 可在日志中查看实际注入的日记长度

---

## 9. 常见陷阱与最佳实践

1. **不要混淆 `basic_memory_hard_limit` 和 `context_window`**
   - `hard_limit` = 内存中保留的总条数（含 context window）
   - `context_window` = 实际发给 LLM 的条数
   - 超出 hard_limit 的消息进入归档，可被日记生成器消费

2. **sentence-transformers 是可选依赖**
   - 安装后日记检索使用向量相似度
   - 未安装时回退到纯关键词匹配，召回率可能下降

3. **日记生成是异步后台任务**
   - 不会阻塞消息回复
   - 冷群检测有 300s 沉默阈值，短时冷群不会立即生成日记

4. **UserManager 是群隔离的**
   - 同一 `external_uid` 在不同群中会被视为不同用户
   - 跨群关联需通过外部系统显式维护

5. **Glossary 只影响 AI 自身知识**
   - 不用于用户查询理解
   - 仅在系统提示词中作为 "术语表" 注入

---

## 10. 模块文件索引

| 模块 | 文件 | 说明 |
|------|------|------|
| 基础记忆 | `memory/basic/manager.py` | BasicMemoryManager |
| 基础记忆存储 | `memory/basic/store.py` | BasicMemoryFileStore |
| 热度计算 | `memory/basic/heat.py` | HeatCalculator |
| 日记管理器 | `memory/diary/manager.py` | DiaryManager |
| 日记生成器 | `memory/diary/generator.py` | DiaryGenerator（LLM 调用） |
| 日记索引 | `memory/diary/indexer.py` | DiaryIndexer |
| 日记检索 | `memory/diary/retriever.py` | DiaryRetriever（token 预算） |
| 上下文组装 | `memory/context_assembler.py` | ContextAssembler |
| 用户管理（简化） | `memory/user/simple.py` | UserProfile, UserManager |
| 身份解析 | `core/identity_resolver.py` | IdentityResolver |
| 名词解释 | `memory/glossary/manager.py` | GlossaryManager |
| 核心引擎 | `core/emotional_engine.py` | EmotionalGroupChatEngine |
