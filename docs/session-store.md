# 会话存储（Session Store）

> **状态持久化层** — 将对话历史、用户档案、回复运行时和 token 记录持久化到磁盘，支持 JSON 和 SQLite 两种后端。

## 一句话定位

会话存储负责**把一次完整的聊天会话（消息、用户、token、运行时状态）保存到磁盘**，并在重启后完整恢复，使 AI 能记住之前的对话上下文。

## 为什么需要它

`EmotionalGroupChatEngine` 在内存中维护当前活跃的对话窗口，但进程重启后内存数据会丢失。会话存储提供：
- **持久化**：消息历史、用户档案、token 记录不因重启而丢失
- **Schema 演进**：新增字段时旧数据仍能加载（默认值回退）
- **并发安全**：SQLite WAL 模式支持多读者
- **后端选择**：JSON 适合便携和版本控制；SQLite 适合大容量和关系查询

## 架构总览

```
Transcript（内存中的会话对象）
    │
    ├── to_dict() / from_dict() ── JsonSessionStore
    │
    └── SQLite 表映射 ─────────── SqliteSessionStore
            ├── session_messages
            ├── session_user_profiles
            ├── session_reply_runtime
            ├── session_token_usage_records
            └── ...
```

---

## Transcript（会话数据模型）

定义在 `models/models.py`：

```python
@dataclass
class Transcript:
    messages: list[Message]
    user_memory: UserManager
    reply_runtime: ReplyRuntimeState
    session_summary: str
    orchestration_stats: dict
    token_usage_records: list[TokenUsageRecord]
```

| 字段 | 说明 |
|------|------|
| `messages` | 完整消息历史（包括已被压缩归档的消息） |
| `user_memory` | `UserManager` 实例，包含所有参与者的 `UserProfile` |
| `reply_runtime` | 回复运行时状态（上次回复时间、冷却计数器等） |
| `session_summary` | 长会话压缩后的摘要文本 |
| `orchestration_stats` | 编排统计信息 |
| `token_usage_records` | 本次会话的所有 LLM 调用记录 |

---

## SessionStore（存储协议）

```python
class SessionStore(Protocol):
    path: Path
    def exists(self) -> bool: ...
    def load(self) -> Transcript: ...
    def save(self, transcript: Transcript) -> None: ...
    def clear(self) -> None: ...
```

所有存储后端都遵循同一协议，调用方无需关心底层实现。

---

## JsonSessionStore（JSON 文件后端）

**定位**：简单文件存储，适合小容量和版本控制场景。

### 行为

- `save()`：调用 `transcript.to_dict()` → `json.dumps` → 原子写入文件
- `load()`：读取 JSON → `Transcript.from_dict()` → **schema write-back**（立即重新保存，使新默认值出现在磁盘上）
- `clear()`：删除文件

### 优缺点

| 优点 | 缺点 |
|------|------|
| 人类可读 | 大文件时加载慢 |
| 便于 git diff | 无索引，查询慢 |
| 无外部依赖 | 并发写入不安全 |

---

## SqliteSessionStore（SQLite 后端）

**定位**：关系型持久化，支持大容量、结构化查询和 WAL 并发。

### Schema（10 张表）

| 表 | 内容 |
|----|------|
| `_meta` | Schema 版本等元数据 |
| `session_meta` | 会话级元数据（summary、stats） |
| `session_messages` | 消息列表（role、content、speaker、timestamp） |
| `session_reply_runtime` | 回复运行时状态 |
| `session_user_profiles` | 用户档案（user_id、name、aliases、traits、metadata） |
| `session_user_runtime` | 用户级运行时状态 |
| `session_user_memory_facts` | 用户记忆事实 |
| `session_token_usage_records` | Token 使用记录 |

### 特性

- **WAL 模式**：`PRAGMA journal_mode=WAL`，读不阻塞写
- **外键约束**：`PRAGMA foreign_keys=ON`
- **Schema 自动创建**：`_ensure_schema()` 在首次使用时建表和索引
- **Bulk 操作**：`save()` 使用事务批量插入，避免逐条写入开销
- **Schema write-back**：`load()` 后同样会重新 `save()`，确保新字段同步到磁盘

---

## SessionStoreFactory（工厂）

```python
SessionStoreFactory.create(
    layout: WorkspaceLayout,
    session_id: str,
    backend: str = "sqlite",   # "json" 或 "sqlite"
) -> SessionStore
```

工厂按 `layout.session_store_path(session_id, backend)` 解析路径：
- JSON：`data_root/sessions/{session_id}/session_state.json`
- SQLite：`data_root/sessions/{session_id}/session_state.db`

测试时可通过 `SessionStoreFactory._store_class` 注入 MockStore。

---

## 路径布局

由 `WorkspaceLayout` 统一管理：

```
data_root/
└── sessions/
    └── {session_id}/
        ├── session_state.db      # SQLite 后端
        └── session_state.json    # JSON 后端（可选）
```

`session_id` 通常由调用方指定，人格架构下常使用群号作为 session 标识。

---

## 与其他系统的关系

| 交互对象 | 方式 |
|---------|------|
| **models.Transcript** | 序列化/反序列化的核心对象 |
| **utils.layout.WorkspaceLayout** | 解析 `sessions/{id}/` 路径 |
| **models.models.Message / Participant** | Transcript 的组成部分 |
| **memory.user.simple.UserManager** | Transcript 内嵌的用户管理系统 |
| **token.store.TokenUsageStore** | Token 记录独立存储，但也会出现在 Transcript 中 |
| **config.models.TokenUsageRecord** | Transcript.token_usage_records 的元素类型 |
