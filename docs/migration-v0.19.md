# 迁移指南：v0.18.x → v0.19.0

## 概览

v0.19.0 包含三项重构性变更：

1. **Mixin 模块公开化** — `sirius_chat._mixin` 重命名为 `sirius_chat.mixins`（原路径保留兼容性垫片）
2. **Engine 层序列化统一** — `UserProfile`、`Participant` 加入 `JsonSerializable`；`UserMemoryFileStore` 使用 `profile.to_dict()` 代替手工字段列举
3. **会话持久化默认后端改为 SQLite** — `JsonPersistentSessionRunner` 默认使用 `SqliteSessionStore`（`session_state.db`）代替 `JsonSessionStore`（`session_state.json`）

补充说明：`SqliteSessionStore` 在后续版本中已继续演进为**结构化 SQLite 会话存储**，并内置 legacy JSON / legacy payload SQLite 自动迁移。本文以下关于会话持久化的说明按当前实现更新。

---

## 1. Mixin 模块重命名

### 变更内容

| 旧路径 | 新路径 | 状态 |
|--------|--------|------|
| `sirius_chat._mixin` | `sirius_chat.mixins` | `_mixin` 已变为薄型向后兼容垫片 |

`sirius_chat._mixin` 模块目前仍可正常导入（它是一个 re-export 垫片），**不会立即阻断已有代码**，但建议尽快迁移。

### 迁移操作

如果你在项目中直接使用了该模块：

```python
# v0.18.x（旧，仍可工作但已废弃）
from sirius_chat._mixin import JsonSerializable

# v0.19.0 起（推荐）
from sirius_chat.mixins import JsonSerializable
```

---

## 2. `UserProfile` 与 `Participant` 获得序列化支持

### 变更内容

`UserProfile`（`sirius_chat.memory.user.models`）和 `Participant`（`sirius_chat.models.models`）现在继承 `JsonSerializable`，自动拥有 `to_dict()` / `from_dict()` 方法。

`UserMemoryFileStore._entry_to_payload` 内部由手工字段枚举改为 `entry.profile.to_dict()`，**保存的用户文件现在额外包含 `identities` 和 `metadata` 字段**（之前被遗漏）。

### 影响评估

- **已有 `users/*.json` 文件**：完全向后兼容。加载逻辑不变，新字段在下次 `save_all()` 时按写回机制自动补齐。
- **外部代码**：如果你的代码直接构造了字典并比对 `UserMemoryFileStore._entry_to_payload` 的输出，注意现在保存的 profile 字典会额外包含 `identities: {}` 和 `metadata: {}` 两个字段。

---

## 3. 会话持久化默认后端：JSON → SQLite

### 变更内容

`JsonPersistentSessionRunner`（及 `JsonPersistentSessionRunner.__post_init__`）现在默认使用 `SqliteSessionStore`。

| 项目 | v0.18.x | v0.19.0 |
|------|---------|---------|
| 默认文件名 | `session_state.json` | `session_state.db` |
| 存储类 | `JsonSessionStore` | `SqliteSessionStore` |
| 文件格式 | 人类可读 JSON（indent=2） | SQLite 二进制（结构化表，按消息 / reply runtime / 用户记忆 / token records 分表保存） |

### 当前 SQLite 文件形态

当前版本的 `SqliteSessionStore` 不再把整份 transcript 按单条 payload 存入 SQLite，而是按会话组件拆成多张表：

- `session_meta`：`session_summary` 与 `orchestration_stats`
- `session_messages`：消息序列
- `session_reply_runtime` + `session_reply_runtime_*`：回复节奏状态
- `session_user_profiles`、`session_user_runtime`、`session_user_memory_facts`：结构化用户记忆
- `session_token_usage_records`：token 调用归档

这样保留了 SQLite 规避文件锁、单事务写入和后续扩展的优势，同时避免“只是把 JSON 塞进 SQLite 容器里”的伪结构化设计。

### `store.clear()` 行为变化

`runner.reset_primary_user(clear_transcript=True)` 原先会 `unlink()` JSON 文件；现在改为调用 `store.clear()` —— 对 `SqliteSessionStore` 而言是清空数据行（文件本身保留以避免 Windows 文件锁），对 `JsonSessionStore` 而言等价于原先的 `unlink()`。

如果你的代码在 `reset_primary_user` 后检查 `store.path.exists()`，请改为检查 `store.exists()`（基于数据行是否存在）：

```python
# 旧写法（不再适用于 SQLite）
assert not (work_path / "session_state.json").exists()

# 新写法（兼容两种后端）
assert not runner.store.exists()
```

### 已有 `session_state.json` / 旧版 `session_state.db` 的迁移

当前版本通常**不需要手工迁移脚本**，默认 `SqliteSessionStore` 会在首次打开时自动处理 legacy 数据：

- 若当前结构化表为空，且同目录存在旧 `session_state.json`，会自动导入到 `session_state.db`。
- 若检测到早期 `session_state.db` 里仍存在 `session_state(payload)` 单表快照，会自动解码为 `Transcript` 并原地升级到结构化表，随后删除旧表。
- 导入 legacy JSON 后会记录迁移标记；之后即使调用 `store.clear()` 清空会话，也不会因为磁盘上残留旧 JSON 而再次“复活”旧会话。

你仍有两种使用选择：

#### 选项 A：保留 JSON 后端（无需改动现有数据）

在创建 `JsonPersistentSessionRunner` 时显式传入 `JsonSessionStore`：

```python
from sirius_chat import JsonSessionStore, JsonPersistentSessionRunner

runner = JsonPersistentSessionRunner(
    config=config,
    provider=provider,
    session_store=JsonSessionStore(work_path),  # 明确指定 JSON 后端
)
```

#### 选项 B：迁移到 SQLite（推荐）

通常只需实例化一次 `SqliteSessionStore`，迁移就会自动完成：

```python
from pathlib import Path
from sirius_chat import SqliteSessionStore

store = SqliteSessionStore(Path("./sirius_data"))
print(store.path)
print(store.exists())
```

如果 `store.exists()` 为 `True`，说明 legacy JSON 或 legacy payload SQLite 已成功导入到结构化 `session_state.db`。确认无误后，可以再决定是否手工删除旧的 `session_state.json`。

如果你希望显式运行一次迁移并打印核验信息，可以直接执行仓库内示例：

```bash
python examples/migrate_session_store.py --work-path ./sirius_data
```

---

## 4. 版本对应文件变化汇总

| 文件路径 | 变化说明 |
|----------|----------|
| `sirius_chat/mixins.py` | **新增**：`JsonSerializable` 正式公开模块 |
| `sirius_chat/_mixin.py` | 变为兼容垫片，重新导出 `JsonSerializable` |
| `sirius_chat/models/models.py` | `Participant` 继承 `JsonSerializable` |
| `sirius_chat/memory/user/models.py` | `UserProfile` 继承 `JsonSerializable` |
| `sirius_chat/memory/user/store.py` | `_entry_to_payload` 使用 `profile.to_dict()` |
| `sirius_chat/session/store.py` | 两种 Store 均新增 `clear()`；`SqliteSessionStore` 后续演进为结构化表并支持 legacy JSON / payload SQLite 自动迁移 |
| `sirius_chat/session/runner.py` | 默认 Store 改为 `SqliteSessionStore`；reset 改用 `store.clear()` |

---

## 5. 常见问题

**Q: 我的程序在 `reset_primary_user` 后出现 `PermissionError` (Windows)**

A: 升级到 v0.19.0 即可解决。原先的 `unlink()` 在 SQLite 文件被系统持有时会失败，新 `clear()` 改为清空行，避免了该问题。

**Q: 能同时保留 JSON 和 SQLite 状态吗？**

A: 可以手动实例化并排他性操作，但不建议两个 Store 同时指向同一 `work_path` 进行写入，会造成数据分叉。若你切到默认 `SqliteSessionStore`，它会优先把 legacy JSON 导入 SQLite；确认迁移完成后，应只保留一个活跃后端。

**Q: `SqliteSessionStore` 的并发安全性如何？**

A: 当前实现启用了 WAL，适合单进程多协程、低到中等写入频率的场景。它不是高并发多进程写入数据库的替代品，但足以覆盖本项目的本地会话状态落盘需求。
