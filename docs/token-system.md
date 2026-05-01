# Token 统计与持久化

> **成本追踪层** — 记录每次 LLM 调用的 token 消耗，支持按任务/模型/人格维度分析。

## 一句话定位

Token 系统负责**精确记录每次 LLM 调用的输入输出 token 数**，并持久化到 SQLite，供成本分析和预算控制使用。

## 为什么需要它

运行 AI 角色扮演 bot 需要持续调用 LLM，成本是实际运营问题。Token 系统提供：
- **精确计量**：每次 `provider.generate()` 后记录 prompt + completion token 数
- **多维分析**：按任务类型（情绪分析/回复生成）、模型、人格、群聊分组统计
- **持久化**：SQLite 存储，进程重启不丢失
- **预算感知**：引擎可在生成前估算 prompt token，避免超出上下文窗口

## 架构总览

```
EmotionalGroupChatEngine._generate()
    │
    ├── 调用前：estimate_tokens_heuristic() 估算 prompt token
    ├── provider.generate_async() → 获取 response
    └── 调用后：创建 TokenUsageRecord → TokenUsageStore.add()
                                                │
                                                ▼
                                        SQLite (token_usage.db)
                                                │
                    ┌───────────────────────────┼───────────────────────────┐
                    ▼                           ▼                           ▼
            token/usage.py              token/analytics.py            token/store.py
            (内存聚合)                  (SQL 多维分析)                (SQLite 读写)
                    │                           │                           │
                    ▼                           ▼                           ▼
            单次会话统计                 时间序列/分组报告              WebUI / 仪表盘
```

---

## TokenUsageRecord（单次调用记录）

定义在 `config/models.py` 中的 dataclass：

```python
@dataclass(slots=True)
class TokenUsageRecord:
    timestamp: float           # 调用时间戳
    actor_id: str              # 人格名称
    task_name: str             # 任务类型：response_generate / cognition_analyze / memory_extract / proactive_generate / vision
    model: str                 # 模型名称
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    input_chars: int           # 输入字符数（辅助估算）
    output_chars: int          # 输出字符数
    retry_count: int           # 重试次数
    group_id: str | None       # 所属群聊
    provider_name: str | None  # Provider 名称
    persona_name: str | None   # 人格名称
```

---

## TokenUsageStore（SQLite 持久化）

**定位**：append-only 的 SQLite 存储层，带 schema 迁移支持。

### 核心能力

| 方法 | 说明 |
|------|------|
| `__init__(db_path, session_id="default")` | 确保目录和表结构存在；支持从 v1 schema 迁移到 v2（新增 persona_name、group_id、provider_name 列） |
| `for_workspace(layout, session_id)` | 工厂方法，按 `WorkspaceLayout` 自动解析 `token/token_usage.db` 路径 |
| `add(record, timestamp=None)` | 插入单条记录 |
| `add_many(records, timestamp=None)` | 批量插入（单事务） |
| `count(session_id=None)` | 统计记录数 |
| `get_summary()` | 汇总：总调用次数、总 prompt/completion/total token、总字符数 |
| `get_breakdown_by(column)` | 按维度分组统计（task_name / model / group_id / provider_name / persona_name），按总 token 降序 |
| `get_recent_records(limit=50)` | 最近 N 条记录 |
| `fetch_records(...)` | 按 session_id/actor_id/task_name/model 过滤查询 |
| `close()` | 关闭连接 |

### Schema

```sql
CREATE TABLE token_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    timestamp REAL,
    actor_id TEXT,
    task_name TEXT,
    model TEXT,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    total_tokens INTEGER,
    input_chars INTEGER,
    output_chars INTEGER,
    retry_count INTEGER,
    group_id TEXT,
    provider_name TEXT,
    persona_name TEXT
);

-- 8 个索引覆盖常见查询维度
CREATE INDEX idx_token_usage_session ON token_usage(session_id);
CREATE INDEX idx_token_usage_actor ON token_usage(actor_id);
CREATE INDEX idx_token_usage_task ON token_usage(task_name);
CREATE INDEX idx_token_usage_model ON token_usage(model);
CREATE INDEX idx_token_usage_timestamp ON token_usage(timestamp);
CREATE INDEX idx_token_usage_group ON token_usage(group_id);
CREATE INDEX idx_token_usage_provider ON token_usage(provider_name);
CREATE INDEX idx_token_usage_persona ON token_usage(persona_name);
```

### 特性

- **WAL 模式**：`PRAGMA journal_mode=WAL`，支持并发读写
- **NORMAL 同步**：`PRAGMA synchronous=NORMAL`，平衡性能与安全
- **Schema 版本**：`_meta` 表记录版本号，启动时自动迁移
- **纯 SQL**：无 ORM，直接用 `sqlite3` 标准库

---

## Token 分析（analytics.py / usage.py）

### 内存聚合（usage.py）

纯函数式、无数据库依赖的聚合器，适合运行时快速统计：

```python
# 从 Transcript 聚合
baseline = build_token_usage_baseline(transcript.token_usage_records)
summary = summarize_token_usage(transcript)
```

`TokenUsageBucket` 按 actor、task、model 三个维度分别累加，输出：
- 总调用次数
- 总 prompt / completion / total token
- 平均 prompt / completion token
- completion/prompt 比率
- 重试率

### SQL 多维分析（analytics.py）

基于 `TokenUsageStore` 的高级查询，所有聚合在 SQLite 内完成：

```python
# 基线统计（可带过滤条件）
baseline = compute_baseline(store, session_id="xxx", task_name="response_generate")

# 分组统计
by_task = group_by_task(store, session_id="xxx")
by_model = group_by_model(store, actor_id="月白")
by_actor = group_by_actor(store)

# 时间序列（默认 1 小时桶）
time_series = time_series(store, bucket_seconds=3600)

# 完整报告（一键获取所有维度）
report = full_report(store)
```

所有函数返回结构化 dict，便于直接序列化为 JSON 供前端消费。

---

## Token 估算（utils.py）

**定位**：在调用 LLM 前估算 prompt 的 token 数量，用于预算控制和上下文窗口管理。

### 三层估算策略

```
estimate_tokens(text, model="generic", use_tiktoken=True)
    │
    ├── 优先尝试 tiktoken（若安装且模型支持）→ 精确值
    └── 失败 → fallback 到 heuristic

estimate_tokens_heuristic(text, model="generic")
    ├── 中文/日文/韩文（CJK）字符：1 token/字符
    ├── 英文单词：~4 字符/token
    └── 其他符号：~4 字符/token
```

### API

| 函数 | 说明 |
|------|------|
| `estimate_tokens(text, model, use_tiktoken)` | 智能包装：优先 tiktoken，fallback heuristic |
| `estimate_tokens_heuristic(text, model)` | CJK-aware 启发式估算 |
| `estimate_tokens_with_tiktoken(text, model)` | tiktoken 精确估算（未安装返回 None） |
| `get_token_estimation_stats(text)` | 调试：同时返回 heuristic、tiktoken 和字符分解 |
| `legacy_estimate_tokens(text)` | 兼容旧代码：`len(text) // 4` |

### 模型比例表

```python
MODEL_TOKEN_RATIO = {
    "gpt-4": {"english": 4.0, "chinese": 1.0},
    "gpt-3.5-turbo": {"english": 4.0, "chinese": 1.5},
    "claude-3": {"english": 3.5, "chinese": 1.2},
    "llama-2": {"english": 3.8, "chinese": 1.8},
    "doubao-seed": {"english": 4.0, "chinese": 1.5},
    "generic": {"english": 4.0, "chinese": 1.0},
}
```

---

## 与其他系统的关系

| 交互对象 | 方式 |
|---------|------|
| **EmotionalGroupChatEngine** | 每次 `_generate()` 后创建 `TokenUsageRecord` 并写入 store |
| **TokenUsageStore** | 被 engine、WebUI、CLI 共同使用；每个 persona 有自己的 `token/token_usage.db` |
| **WorkspaceLayout** | `TokenUsageStore.for_workspace()` 按布局解析数据库路径 |
| **WebUIServer** | `/api/tokens` 和 `/api/personas/{name}/tokens` 打开各人格的 store 进行汇总 |
| **tiktoken** | 可选依赖；未安装时自动降级到 heuristic |
