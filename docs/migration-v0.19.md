# 迁移指南：v0.18.x → v0.19.0

## 概览

v0.19.0 包含三项重构性变更：

1. **Mixin 模块公开化** — `sirius_chat._mixin` 重命名为 `sirius_chat.mixins`（原路径保留兼容性垫片）
2. **Engine 层序列化统一** — `UserProfile`、`Participant` 加入 `JsonSerializable`；`UserMemoryFileStore` 使用 `profile.to_dict()` 代替手工字段列举
3. **会话持久化默认后端改为 SQLite** — `JsonPersistentSessionRunner` 默认使用 `SqliteSessionStore`（`session_state.db`）代替 `JsonSessionStore`（`session_state.json`）

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
| 文件格式 | 人类可读 JSON（indent=2） | SQLite 二进制（payload 仍是 JSON 字符串）|

### `store.clear()` 行为变化

`runner.reset_primary_user(clear_transcript=True)` 原先会 `unlink()` JSON 文件；现在改为调用 `store.clear()` —— 对 `SqliteSessionStore` 而言是清空数据行（文件本身保留以避免 Windows 文件锁），对 `JsonSessionStore` 而言等价于原先的 `unlink()`。

如果你的代码在 `reset_primary_user` 后检查 `store.path.exists()`，请改为检查 `store.exists()`（基于数据行是否存在）：

```python
# 旧写法（不再适用于 SQLite）
assert not (work_path / "session_state.json").exists()

# 新写法（兼容两种后端）
assert not runner.store.exists()
```

### 已有 `session_state.json` 的迁移

你有两种选择：

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

运行一次性迁移脚本将现有 `session_state.json` 导入 `session_state.db`：

```python
import json
from pathlib import Path
from sirius_chat import JsonSessionStore, SqliteSessionStore

def migrate_json_to_sqlite(work_path: Path) -> None:
    json_store = JsonSessionStore(work_path)
    if not json_store.exists():
        print(f"未找到 {json_store.path}，跳过")
        return
    transcript = json_store.load()
    sqlite_store = SqliteSessionStore(work_path)
    sqlite_store.save(transcript)
    print(f"已迁移：{json_store.path} → {sqlite_store.path}")
    # 可选：迁移完成后删除旧 JSON 文件
    # json_store.path.unlink()

migrate_json_to_sqlite(Path("./sirius_data"))
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
| `sirius_chat/session/store.py` | 两种 Store 均新增 `clear()` 方法 |
| `sirius_chat/session/runner.py` | 默认 Store 改为 `SqliteSessionStore`；reset 改用 `store.clear()` |

---

## 5. 常见问题

**Q: 我的程序在 `reset_primary_user` 后出现 `PermissionError` (Windows)**

A: 升级到 v0.19.0 即可解决。原先的 `unlink()` 在 SQLite 文件被系统持有时会失败，新 `clear()` 改为清空行，避免了该问题。

**Q: 能同时保留 JSON 和 SQLite 状态吗？**

A: 可以手动实例化并排他性操作，但不建议两个 Store 同时指向同一 `work_path` 进行写入，会造成数据分叉。迁移后请选用其中之一。

**Q: `SqliteSessionStore` 的并发安全性如何？**

A: SQLite 默认使用文件级锁，适合单进程多协程场景。多进程并发写入需要 WAL 模式，不在当前版本范围内。
