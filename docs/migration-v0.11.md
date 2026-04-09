# 迁移指南：v0.10 → v0.11（供 AI 代理查阅）

本版本新增 Token 使用 SQLite 持久化与多维度分析系统，以及意图分析字段增强和消息尾部空白清理。**所有变更完全向后兼容**，无需修改现有调用代码。

---

## 变更总览

| 特性 | 涉及模块 | 默认行为 | 是否破坏性 |
|------|----------|----------|------------|
| Token SQLite 持久化 | `token/store.py` | 自动启用，写入 `{work_path}/token_usage.db` | 否 |
| 多维度 Token 分析 | `token/analytics.py` | 按需调用，无副作用 | 否 |
| `IntentAnalysis` 新增字段 | `core/intent.py` | `reason=""`、`evidence_span=""` 默认值，不影响现有逻辑 | 否 |
| 消息尾部空白清理 | `models/models.py` | 所有 `Message` 创建时自动执行 | 可能影响 snapshot 比对 |

---

## 1. Token SQLite 持久化（自动，无需修改代码）

### 变更内容

`AsyncRolePlayEngine` 在 `run_live_session()` 初始化时自动创建 `TokenUsageStore`，每次模型调用后将 `TokenUsageRecord` 同步写入 SQLite 数据库：

- 文件位置：`{work_path}/token_usage.db`
- 写入时机：`_call_provider_with_retry()` 成功返回后立即写入
- 双写模式：与现有 `Transcript.token_usage_records`（内存）**并行**，互不影响

### 新增持久化文件

```
{work_path}/
├── token_usage.db          ← 新增（SQLite，WAL模式）
├── session_state.json      （原有）
├── events/events.json      （原有）
└── primary_user.json       （原有）
```

### SQLite 表结构

```sql
CREATE TABLE token_usage (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id        TEXT    NOT NULL,   -- str(work_path)
    timestamp         REAL    NOT NULL,   -- Unix epoch float
    actor_id          TEXT    NOT NULL,   -- 发言者 user_id
    task_name         TEXT    NOT NULL,   -- 任务名（chat_main/memory_extract 等）
    model             TEXT    NOT NULL DEFAULT '',
    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens      INTEGER NOT NULL DEFAULT 0,
    input_chars       INTEGER NOT NULL DEFAULT 0,
    output_chars      INTEGER NOT NULL DEFAULT 0,
    estimation_method TEXT    NOT NULL DEFAULT 'char_div4',
    retries_used      INTEGER NOT NULL DEFAULT 0
);
```

**注意**：`session_id` 的值为 `str(config.work_path)`，即工作目录的绝对路径字符串。同一 `work_path` 下的所有历史会话共享同一个 `token_usage.db` 文件，`session_id` 相同。

### 无需迁移动作

现有代码无需任何修改。引擎自动处理：

```python
# v0.10（未改变，继续有效）
transcript = await engine.run_live_session(config=config)
# v0.11 会额外创建/写入 {work_path}/token_usage.db，对现有代码透明
```

---

## 2. 多维度 Token 分析 API（新增，可选使用）

### 导入方式

```python
# 从顶层包导入（推荐）
from sirius_chat import (
    TokenUsageStore,
    AnalyticsReport,
    BaselineDict,
    BucketDict,
    TimeSliceDict,
    compute_baseline,
    full_report,
    group_by_actor,
    group_by_model,
    group_by_session,
    group_by_task,
    time_series,
)

# 或从子模块导入
from sirius_chat.token import TokenUsageStore, compute_baseline, full_report
from sirius_chat.token.store import TokenUsageStore
from sirius_chat.token.analytics import compute_baseline, full_report
```

### 用法示例

```python
from pathlib import Path
from sirius_chat import TokenUsageStore, full_report, compute_baseline, group_by_actor

work_path = Path("/path/to/work_dir")
store = TokenUsageStore(work_path / "token_usage.db")

# 全量报告（baseline + 所有分组维度）
report = full_report(store)
print(report["baseline"]["total_calls"])
print(report["by_model"])

# 全局基线（分可选筛选）
baseline = compute_baseline(store)
baseline_for_alice = compute_baseline(store, actor_id="alice")
baseline_for_session = compute_baseline(store, session_id=str(work_path))

# 按用户分组
by_user = group_by_actor(store)
for actor_id, bucket in by_user.items():
    print(f"{actor_id}: {bucket['total_tokens']} tokens")

# 按时间桶聚合（默认 1 小时）
series = time_series(store, bucket_seconds=3600)
for slot in series:
    print(slot["time_bucket"], slot["calls"])
```

### 函数签名汇总

| 函数 | 返回类型 | 说明 |
|------|----------|------|
| `compute_baseline(store, *, session_id, actor_id, task_name, model)` | `BaselineDict` | 带可选筛选的全局基线统计 |
| `group_by_session(store, *, actor_id, task_name, model)` | `dict[str, BucketDict]` | 按会话聚合 |
| `group_by_actor(store, *, session_id, task_name, model)` | `dict[str, BucketDict]` | 按用户聚合 |
| `group_by_task(store, *, session_id, actor_id, model)` | `dict[str, BucketDict]` | 按任务类型聚合 |
| `group_by_model(store, *, session_id, actor_id, task_name)` | `dict[str, BucketDict]` | 按模型聚合 |
| `time_series(store, *, bucket_seconds, session_id, actor_id, task_name, model)` | `list[TimeSliceDict]` | 时间维度聚合 |
| `full_report(store, *, session_id)` | `AnalyticsReport` | 一次性完整报告 |

所有筛选参数均为可选关键字参数，省略时不作筛选。

---

## 3. `IntentAnalysis` 新增字段

### 变更内容

`IntentAnalysis` dataclass 新增两个字段：

```python
# v0.10
@dataclass(slots=True)
class IntentAnalysis:
    intent_type: str
    confidence: float
    directed_at_ai: bool
    willingness_modifier: float
    skip_sections: list[str]

# v0.11（新增 reason 和 evidence_span）
@dataclass(slots=True)
class IntentAnalysis:
    intent_type: str
    confidence: float
    directed_at_ai: bool
    willingness_modifier: float
    skip_sections: list[str]
    reason: str = ""            # ← 新增：1句解释（LLM路径和回退路径均填充）
    evidence_span: str = ""     # ← 新增：从原话摘取的关键短语
```

两个字段均有默认值 `""`，对现有创建 `IntentAnalysis` 实例的代码完全透明。

### 迁移动作

- 无需修改代码。
- 若下游代码检查 `IntentAnalysis` 字段数量或 `__dataclass_fields__`，需更新为 7 个字段。
- JSON 解析失败时，引擎现在会记录 `WARNING` 级日志（`logger.warning("...")`），不再静默失败。

---

## 4. 消息尾部空白自动清理

### 变更内容

所有 `Message` 对象创建和添加时，`content` 末尾的 `\n` 和空格会被自动去除：

```python
# v0.11 新行为（__post_init__ + Transcript.add() 均生效）
msg = Message(role="user", content="你好\n\n  ")
assert msg.content == "你好"   # 尾部已自动清理

transcript.add(Message(role="assistant", content="OK\n"))
assert transcript.messages[-1].content == "OK"
```

### 可能影响场景

| 场景 | 影响 | 建议 |
|------|------|------|
| 对 `message.content` 做精确 `endswith("\n")` 断言 | **会失败** | 更新断言，不要期望尾部空白 |
| 比对 `Transcript.token_usage_records` 中的原始内容 | 无影响（token 记录不含消息内容） | - |
| 持久化后恢复的 `session_state.json` | 已持久化的旧内容不会被修改 | 见下方"持久化文件迁移" |

---

## 5. 持久化文件修改方式

### 5.1 新增文件：`token_usage.db`

**引擎自动创建**，无需手动操作。首次以 v0.11 运行时，`{work_path}/token_usage.db` 会被自动建立。

若需要**手动迁移历史 token 数据**（从 `Transcript.token_usage_records` 回填到 SQLite），使用以下脚本：

```python
"""将已有 transcript 的 token 记录一次性回填到 SQLite。"""
import json
from pathlib import Path
from sirius_chat import TokenUsageStore
from sirius_chat.config import TokenUsageRecord

work_path = Path("/path/to/work_dir")
transcript_file = work_path / "transcript.json"

store = TokenUsageStore(
    work_path / "token_usage.db",
    session_id=str(work_path),
)

# 加载旧 transcript（格式取决于你的持久化方式）
data = json.loads(transcript_file.read_text(encoding="utf-8"))
for rec in data.get("token_usage_records", []):
    store.add(TokenUsageRecord(
        actor_id=rec.get("actor_id", "unknown"),
        task_name=rec.get("task_name", "chat_main"),
        model=rec.get("model", ""),
        prompt_tokens=rec.get("prompt_tokens", 0),
        completion_tokens=rec.get("completion_tokens", 0),
        total_tokens=rec.get("total_tokens", 0),
        input_chars=rec.get("input_chars", 0),
        output_chars=rec.get("output_chars", 0),
        estimation_method=rec.get("estimation_method", "char_div4"),
        retries_used=rec.get("retries_used", 0),
    ))

store.close()
print(f"回填完成，共 {store.count()} 条记录")  # 需重新打开读取
```

### 5.2 旧 `session_state.json` / `session_state.db`

**无需修改**。`Message` 反序列化时，`__post_init__` 会自动清理尾部空白。已持久化的内容在加载后立即清理，行为一致。

若你直接操作 JSON 文件中的 `content` 字段做文本比对，需注意：

```python
# 旧 session_state.json 中可能存在
{"role": "user", "content": "你好\n"}

# 加载为 Message 后，content 变为
"你好"   # ← 已清理
```

若需要保持持久化格式统一，运行以下一次性清理脚本：

```python
"""原地清理 session_state.json 中所有 message.content 的尾部空白。"""
import json, re
from pathlib import Path

def trim_tail(s: str) -> str:
    return re.sub(r"[ \n]+$", "", s)

session_file = Path("/path/to/work_dir/session_state.json")
data = json.loads(session_file.read_text(encoding="utf-8"))

changed = 0
for msg in data.get("messages", []):
    original = msg.get("content", "")
    cleaned = trim_tail(original)
    if cleaned != original:
        msg["content"] = cleaned
        changed += 1

session_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"已清理 {changed} 条消息的尾部空白")
```

### 5.3 `events/events.json`

**无需修改**。事件记忆文件格式未变化。

### 5.4 `primary_user.json` / `users/*.json`

**无需修改**。用户档案格式未变化。

---

## 6. `.gitignore` 建议

`token_usage.db` 是运行时产物，应加入 `.gitignore`（与 `data/` 相同级别已覆盖）：

```gitignore
# 运行时数据（已有规则，确认包含）
data/
*.db
```

若 `work_path` 不在 `data/` 下，需额外添加：

```gitignore
token_usage.db
```

---

## 7. 测试影响

| 测试类型 | 是否受影响 | 说明 |
|---------|------------|------|
| 依赖 `message.content` 精确尾部匹配 | **受影响** | 去掉 `\n`/空格 断言 |
| `IntentAnalysis` 实例化 | 不受影响（默认值） | 可选访问 `.reason`、`.evidence_span` |
| `token_usage_records` 长度/内容 | 不受影响 | 内存路径不变 |
| `TokenUsageStore` 文件 IO | 需隔离临时目录 | 使用 `tmp_path` fixture 指定 `db_path` |

新增测试隔离示例：

```python
import pytest
from sirius_chat import TokenUsageStore
from sirius_chat.config import TokenUsageRecord

@pytest.fixture
def store(tmp_path):
    s = TokenUsageStore(tmp_path / "test.db", session_id="test_session")
    yield s
    s.close()

def test_token_count(store):
    record = TokenUsageRecord(
        actor_id="user1", task_name="chat_main", model="gpt-4",
        prompt_tokens=100, completion_tokens=50, total_tokens=150,
        input_chars=200, output_chars=100,
    )
    store.add(record)
    assert store.count() == 1
    assert store.count(session_id="test_session") == 1
```
