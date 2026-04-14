# Workspace 持久化接管迁移指南：v0.22.4 → v0.23.0

## 背景

`v0.23.0` 把持久化、目录布局、session 恢复和 provider 注册的推荐入口统一收口到 workspace 层。若你继续升级到 `v0.24.0`，还需要进一步关注 JSONC 注释配置和基于文件监听的热刷新，详见 `docs/migration-v0.24.md`。

升级后的目标语义是：

- 外部只提供 `work_path`
- 外部只提供业务输入，例如 `session_id`、`turn`、`environment_context`、`user_profile`、`on_reply`、`timeout`
- 持久化文件名、目录布局、读写时机、迁移逻辑由内核统一维护

如果你之前在外部手工 `store.load()` / `store.save()`、手工拼 `session_state.db` 路径，或直接依赖根目录 `provider_keys.json` / `generated_agents.json`，需要关注本次变更。

## 新推荐入口

### 升级前

```python
transcript = store.load() if store.exists() else None
transcript = await engine.run_live_session(config=config, transcript=transcript)
transcript = await engine.run_live_message(
    config=config,
    transcript=transcript,
    turn=turn,
)
store.save(transcript)
```

### 升级后

```python
runtime = open_workspace_runtime(work_path)

transcript = await runtime.run_live_message(
    session_id="group:123456",
    turn=turn,
    environment_context=environment_context,
    user_profile=user_profile,
    on_reply=on_reply,
    timeout=45.0,
)
```

`WorkspaceRuntime` 会自动处理：

- workspace 初始化
- 旧布局检测与迁移
- provider registry 读取
- session transcript 恢复
- transcript 自动保存
- participants 元数据保存

## 新目录布局

升级后的推荐 layout：

```text
<work_path>/
  workspace.json
  config/
    session_config.json
  providers/
    provider_keys.json
  sessions/
    <session_id>/
      session_state.db
      participants.json
  memory/
    users/
      <user_id>.json
    events/
      events.json
    self_memory.json
  token/
    token_usage.db
  roleplay/
    generated_agents.json
    generated_agent_traces/
      <agent_key>.json
  skills/
    README.md
    *.py
  skill_data/
    <skill_name>.json
```

补充说明：

- `session_id` 会被 percent-encode 后再落到磁盘，所以像 `group:123456` 这种 ID 在 Windows 上也能安全建目录。
- `workspace.json` 是 workspace 级清单；`config/session_config.json` 是可读的默认配置快照，并从 `v0.24.0` 起改为支持 JSONC 注释的编辑模板。

## 自动迁移规则

首次打开 workspace 时，`WorkspaceMigrationManager` 会自动检测旧的根目录平铺布局，并执行非破坏性迁移。

### 会被检测的旧路径

- `session_state.json`
- `session_state.db`
- `primary_user.json`
- `provider_keys.json`
- `users/`
- `events/`
- `self_memory.json`
- `token_usage.db`
- `generated_agents.json`
- `generated_agent_traces/`

### 迁移后的对应关系

| 旧路径 | 新路径 | 说明 |
| --- | --- | --- |
| `session_state.json` / `session_state.db` | `sessions/default/session_state.db` | 自动复用结构化 SQLite 迁移逻辑；若只存在旧 JSON，会先复制后转为新 SQLite |
| `primary_user.json` | `sessions/default/participants.json` | 旧主用户会被写为 `primary_user_id` + 单参与者 payload |
| `provider_keys.json` | `providers/provider_keys.json` | provider registry 自动迁入新目录 |
| `users/` | `memory/users/` | 用户记忆逐文件复制 |
| `events/` | `memory/events/` | 事件记忆迁到统一 memory 目录 |
| `self_memory.json` | `memory/self_memory.json` | AI 自身记忆迁到统一 memory 目录 |
| `token_usage.db` | `token/token_usage.db` | token 计量迁到独立 token 目录 |
| `generated_agents.json` | `roleplay/generated_agents.json` | roleplay 资产统一收口 |
| `generated_agent_traces/` | `roleplay/generated_agent_traces/` | trace 统一收口 |

### 迁移特性

- 默认是非破坏性的 copy-style 迁移，不会先删旧文件
- roleplay trace、pending persona spec 会一起保留
- session store 迁移会复用已有 SQLite 结构化升级逻辑
- 完成后 runtime 会继续在新路径上读写

## 兼容性说明

以下能力仍然保留：

- `JsonPersistentSessionRunner` 仍可用，但内部会尽量复用 `WorkspaceRuntime`
- `JsonSessionStore` / `SqliteSessionStore` 仍可直接低层使用
- `create_session_config_from_selected_agent(...)` 仍可用，但推荐地位下降为兼容/高级工具
- `primary_user.json` 和 `session_config.persisted.json` 在兼容入口中仍会保留

以下推荐习惯需要迁移：

- 不再推荐外部手工调用 `merge_provider_sources(...)` 后自己创建 engine
- 不再推荐外部手工决定 transcript 文件路径
- 不再推荐外部显式 `store.load()` / `store.save()` 管理恢复与保存

## API 迁移建议

### 1. Python 服务接入

优先改成：

```python
runtime = open_workspace_runtime(work_path)
await runtime.run_live_message(session_id=session_id, turn=turn)
```

如果你确实需要：

- 自己维护 transcript 生命周期
- 自己控制何时 finalize
- 自己做特殊的 engine 组合

可以继续保留低层 `AsyncRolePlayEngine + SessionConfig` 写法。

### 2. CLI / main.py

`sirius-chat` CLI 和仓库根目录 `main.py` 已经切到 workspace runtime 模型：

- legacy `session.json` 会先 bootstrap 到 workspace
- 默认自动恢复 session
- `--no-resume` 会清空当前 session 后重新开始

### 3. Provider 管理

交互命令 `/provider platforms|add|remove|list` 现在统一写到：

```text
<work_path>/providers/provider_keys.json
```

## 验证升级是否成功

建议按以下顺序核验：

1. 首次打开旧 `work_path` 后，确认出现 `workspace.json`
2. 确认 `providers/provider_keys.json`、`sessions/default/session_state.db`、`memory/events/events.json`、`token/token_usage.db`、`roleplay/generated_agents.json` 已生成
3. 发送一条新消息，确认 `sessions/<session_id>/participants.json` 已更新
4. 重启进程后再次打开同一 `work_path`，确认 transcript 能恢复
5. 如有旧角色资产，确认 `roleplay/generated_agent_traces/` 中的 trace 仍可读取

## 常见问题

### 我还可以继续直接用 `JsonSessionStore` 吗？

可以。低层 store API 没有删除，只是推荐入口改为 workspace runtime。

### 自动迁移会删除旧文件吗？

不会。默认是非破坏性复制迁移，迁移后 runtime 只会继续使用新布局。

### 为什么我的 `group:123456` 会变成目录名 `group%3A123456`？

因为 session ID 会先做 percent-encode，以确保在 Windows 上也能安全落盘。这是预期行为。

## 相关接口

- `open_workspace_runtime(...)`
- `WorkspaceRuntime`
- `WorkspaceLayout`
- `WorkspaceMigrationManager`
- `ConfigManager.bootstrap_workspace_from_legacy_session_json(...)`
- `SessionStoreFactory`