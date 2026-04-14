# 迁移指南：v0.24 → v0.25（Engine 全权接管文件管理）

## 概述

v0.25.0 实现了 Engine 全权接管文件管理，移除了旧版 `WorkspaceMigrationManager`，新增 `WorkspaceBootstrap`、`RoleplayWorkspaceManager` 以及 workspace 读写 API。

## 破坏性变更

### 1. `WorkspaceMigrationManager` 已移除

- `sirius_chat.workspace.migration` 模块不再存在。
- `WorkspaceRuntime.initialize()` 不再自动迁移根目录平铺布局文件。
- 若你的代码直接引用了 `WorkspaceMigrationManager` 或 `MigrationReport`，需移除相关导入。

**迁移方式**：如果工作区曾使用旧的平铺布局（`generated_agents.json`、`provider_keys.json` 等在根目录），需手动将文件移动到新的 workspace 布局路径，或使用 `RoleplayWorkspaceManager.bootstrap_from_legacy_session_config()` 一次性转换。

### 2. EventMemoryManager v1 格式不再自动迁移

- `EventMemoryManager.from_dict()` 遇到 `version < 2` 的数据时，返回空 manager。
- v1 事件记忆数据需要手动升级到 v2 格式。

### 3. SqliteSessionStore legacy JSON 导入后重命名源文件

- `SqliteSessionStore` 从 `session_state.json` 导入数据后，会将源文件重命名为 `session_state.json.migrated`。
- 这防止了 `clear()` 后再次打开 store 时重新导入旧数据。

## 新增功能

### WorkspaceBootstrap

通过 `open_workspace_runtime()` 的 `bootstrap` 参数，在首次打开 workspace 时注入配置：

```python
from sirius_chat.api import open_workspace_runtime, WorkspaceBootstrap
from sirius_chat.config.models import SessionDefaults

bootstrap = WorkspaceBootstrap(
    active_agent_key="main_agent",
    session_defaults=SessionDefaults(history_max_messages=100),
    provider_entries=[
        {"type": "openai-compatible", "api_key": "sk-...", "base_url": "https://api.openai.com"},
    ],
)
runtime = open_workspace_runtime(Path("data"), bootstrap=bootstrap)
```

设置 `persist_bootstrap=False` 可使注入仅在本次运行有效。

### Workspace 读写 API

无需理解文件布局即可管理 workspace 配置：

```python
defaults = runtime.export_workspace_defaults()  # 读取当前配置
await runtime.apply_workspace_updates({         # 部分更新并持久化
    "session_defaults": {"history_max_messages": 200},
})
```

### set_provider_entries

运行时注入 provider 配置：

```python
runtime.set_provider_entries([
    {"type": "openai-compatible", "api_key": "sk-...", "base_url": "https://api.openai.com"},
], persist=True)
```

### RoleplayWorkspaceManager

一站式 agent 选择 + workspace defaults 写入：

```python
from sirius_chat.workspace.roleplay_manager import RoleplayWorkspaceManager
from sirius_chat.workspace.layout import WorkspaceLayout

layout = WorkspaceLayout(Path("data"))
mgr = RoleplayWorkspaceManager(layout)
cfg = mgr.bootstrap_active_agent(agent_key="main_agent")
```

### Legacy generated_agents.json 回退读取

`load_generated_agent_library()` 现在会先查找 `roleplay/generated_agents.json`（新路径），找不到时回退到根目录的 `generated_agents.json`（旧路径），确保向后兼容。

## 新增导出

以下符号已加入公开 API（`from sirius_chat import ...`）：

- `WorkspaceBootstrap`
- `RoleplayWorkspaceManager`
