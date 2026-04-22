# Workspace Runtime（工作空间运行时）

> **Sirius Chat 的入口层** — 管理目录、配置、会话、引擎生命周期。

## 一句话定位

WorkspaceRuntime 是 Sirius Chat 的**操作系统层**。它负责初始化目录结构、管理配置文件、维护会话状态、切换引擎、热刷新配置——让上层引擎可以专注于"怎么说话"，而不需要关心"文件存在哪"。

## 为什么需要它

如果没有 WorkspaceRuntime，每次启动都需要手动：
1. 创建目录结构（sessions/、memory/、config/...）
2. 加载 workspace.json 配置
3. 初始化 provider（读取 API key、测试连通性）
4. 加载 SKILL
5. 恢复之前的会话状态
6. 管理会话锁和消息队列

WorkspaceRuntime 把这全部自动化了。你只需调用 `open_workspace_runtime(work_path)`，剩下的事情它全包。

## 目录结构（WorkspaceLayout）

WorkspaceRuntime 不硬编码任何路径。所有路径由 `WorkspaceLayout` 统一管理：

```
{work_path}/                    ← 数据根目录（data_root）
├── sessions/                   ← 会话存储
│   └── {session_id}/
│       ├── session_state.db    ← 聊天记录（SQLite 或 JSON）
│       └── participants.json   ← 参与者元数据
├── memory/                     ← 记忆系统数据
│   ├── user_memory/            ← 用户事实记忆
│   ├── episodic/               ← 情景记忆（v0.28，JSON 数组格式）
│   ├── event_memory/           ← 事件记忆 V2（observation-based，按用户缓冲）
│   └── semantic/               ← 语义记忆（v0.28，用户画像 + 群体画像）
├── token/                      ← Token 用量统计
├── engine_state/               ← 引擎状态（v0.28）
├── skill_data/                 ← SKILL 数据存储
└── primary_user.json           ← 主用户配置

{config_path}/                  ← 配置根目录（默认等于 data_root）
├── workspace.json              ← 工作空间主配置
├── config/                     ← 额外配置
├── providers/                  ← 提供者配置
│   └── provider_keys.json
├── roleplay/                   ← 角色扮演资产
│   └── generated_agents.json
└── skills/                     ← 自定义 SKILL
    └── *.py
```

**双根设计**：v0.24+ 开始支持 `data_root` 和 `config_root` 分离。这允许把运行时数据（聊天记录、记忆）和配置（API key、角色资产）放在不同位置——比如配置在代码仓库里，数据在容器卷里。

## 核心职责

### 1. 初始化（initialize）

调用 `runtime.initialize()` 时：
1. 创建所有目录（如果不存在）
2. 加载 SKILL（从 `skills/` 目录 + 内置 SKILL，如 `learn_term`、`url_content_reader`、`bing_search` 等）
3. 应用 bootstrap 配置（如果提供了）
4. 加载 workspace.json，写入默认值（如果缺失）
5. 启动配置监听器（watchdog）

### 2. 配置管理

`WorkspaceConfig` 包含：
- `active_agent_key`：当前选中的角色（legacy 兼容）
- `providers`：模型提供者列表
- `orchestration`：编排策略（legacy 引擎使用，emotional engine 使用 `emotional_engine` 配置块）

配置热刷新有两种机制：

**轮询**：每次处理消息前计算配置文件的 SHA-256 签名（mtime + size），如果变了就重新加载。

**监听**：watchdog 监控 `workspace.json` 和 `config/` 目录，文件变化时 50ms 防抖后触发刷新。

如果配置变化涉及 provider，emotional engine 下次创建时会使用新 provider。

### 3. Provider 解析

WorkspaceRuntime 维护 provider 的生命周期：
1. 从 `provider_keys.json` 加载已保存的 provider 配置
2. 合并 session 级别的 provider 覆盖
3. 如果没有显式 provider，构建 `AutoRoutingProvider`（按 model name 自动路由到对应的 provider）
4. 创建引擎时注入解析好的 provider

### 4. 会话管理

emotional engine 直接处理消息，不经过 runtime 的队列系统：

```python
engine = runtime.create_emotional_engine()
result = await engine.process_message(message, participants, group_id)
```

legacy runtime 队列系统（`run_live_message`）仍然可用，但不推荐新用户使用。

### 5. 引擎管理

WorkspaceRuntime 支持两种引擎：

**EmotionalGroupChatEngine（v0.28+ 默认）**：
- 通过 `create_emotional_engine()` 显式创建
- **不缓存**：每次调用返回新实例，由调用方管理生命周期
- 自动加载 persona（从 `active_agent_key` 对应的 roleplay preset，或 config 中的 `persona`）
- 自动注入 SKILL 运行时

```python
# 推荐用法
runtime = open_workspace_runtime(work_path)
engine = runtime.create_emotional_engine()
engine.start_background_tasks()
result = await engine.process_message(message, participants, group_id)
```

**Legacy 引擎（AsyncRolePlayEngine，已归档）**：
- 懒加载：第一次 `_get_engine()` 时才创建
- 不再接收新功能，仅保留兼容

```python
# Legacy 用法（不推荐）
runtime = open_workspace_runtime(work_path)
await runtime.run_live_message(session_id, message)
```

### 6. Bootstrap 系统

Host 可以在不预先创建文件的情况下注入默认配置：

```python
from sirius_chat.config.models import WorkspaceBootstrap

runtime = open_workspace_runtime(
    work_path,
    bootstrap=WorkspaceBootstrap(
        workspace_config={"active_agent_key": "my_agent"},
        providers=[{"type": "deepseek", "api_key": "..."}],
    ),
    persist_bootstrap=True,  # 写入磁盘
)
```

Bootstrap 是幂等的——通过 SHA-256 签名判断是否已应用过相同配置，避免重复覆盖。

## 配置热刷新流程

```
文件变化（workspace.json 被编辑）
    │
    ▼
[WorkspaceConfigWatcher] 检测到事件
    │
    ▼ 50ms 防抖
[asyncio loop callback] 调度刷新
    │
    ▼
[_refresh_workspace_config] 计算文件签名
    │
    ▼ 签名改变
[重新加载 workspace.json]
    │
    ▼ 涉及 provider/agent
[_reset_engine_state] 停止后台任务，重置引擎状态
    │
    ▼
[下次消息处理时] 用新配置创建引擎
```

## 与其他系统的关系

| 系统 | 关系 |
|------|------|
| **EmotionalGroupChatEngine** | `create_emotional_engine()` 工厂方法创建并注入 provider + work_path + SKILL 运行时 |
| **AsyncRolePlayEngine** | `_get_engine()` 懒加载，缓存，配置变更时重置 |
| **SkillRegistry / SkillExecutor** | 初始化时从 `skills_dir()` 加载，注入到两种引擎 |
| **ProviderRegistry** | 管理 `provider_keys.json` 的读写和健康检查 |
| **SessionStore** | 每个 session 懒创建，持久化聊天记录 |
| **WorkspaceConfig** | 热刷新的核心对象，包含 orchestration、agent、provider 配置 |
