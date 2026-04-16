# Sirius Chat 架构说明

本文档描述当前代码库的稳定架构边界。历史迁移文档只用于解释版本演进，不作为当前实现的事实来源；当前架构以本文档、[docs/full-architecture-flow.md](docs/full-architecture-flow.md) 与实际代码为准。

## 目标

Sirius Chat 是一个面向“多人用户与单主 AI”交互场景的编排框架，目标包括：

- 为 CLI、脚本、服务端集成和外部编排器提供统一的会话运行模型
- 让调用方只关心输入消息与业务上下文，而不是底层文件布局与恢复细节
- 在多轮对话中保持用户画像、事件记忆、AI 自身记忆与会话节奏的连续性
- 用 provider 抽象隔离上游模型差异，让编排逻辑稳定留在框架内部

## 核心原则

- Workspace 持久化由 runtime 统一管理，外部不直接拼接内部文件路径。
- `sirius_chat/models/models.py` 与 `sirius_chat/config/models.py` 是核心数据契约的事实来源。
- provider 细节只允许位于 `sirius_chat/providers/`，不混入编排核心。
- 当前推荐入口是 `WorkspaceRuntime`，低层 `AsyncRolePlayEngine` 只保留给高级自定义场景。
- 配置资产与运行态数据支持双根分离：config root 负责配置与角色资产，data root 负责 session、memory、token 与 skill_data。

## 推荐入口

| 入口 | 适用场景 | 负责内容 |
| --- | --- | --- |
| `open_workspace_runtime(...)` / `WorkspaceRuntime` | 默认外部接入、生产服务、插件宿主 | 初始化 workspace、热刷新、session 恢复、participants 元数据、session store 写回 |
| `AsyncRolePlayEngine` | 需要完全自管 transcript、provider 生命周期或自定义上层调度 | 单轮消息编排、辅助任务、提示词构造、事件流 |
| `main.py` | 仓库级交互入口、调试与人工演练 | provider 管理命令、持续会话、兼容 `primary_user.json` |
| `sirius-chat` | 库内薄 CLI、单轮调用或模板导出 | legacy session JSON bootstrap、单轮会话执行、模板输出 |

### 需要明确的语义

- `SessionConfig` 现在要求 `preset=AgentPreset(...)`，而不是直接在配置文件里手写 `agent` 和 `global_system_prompt`。
- `SessionConfig.work_path` 在当前架构中表示 config root；`SessionConfig.data_path` 表示 data root。
- `User` 是 `Participant` 的公开别名，不存在第二套独立的人类参与者模型。

## 模块边界

| 模块 | 主要职责 | 不应承担的职责 |
| --- | --- | --- |
| `sirius_chat/api/` | 对外统一导出稳定函数、类型与 facade | 不直接实现底层编排或路径布局 |
| `sirius_chat/workspace/` | layout、runtime、watcher、roleplay workspace bootstrap | 不写 provider 调用细节，不实现主对话生成 |
| `sirius_chat/config/` | `WorkspaceConfig` / `SessionConfig` / `OrchestrationPolicy` 契约、JSONC 读写、workspace 默认值构建 | 不直接保存 session transcript |
| `sirius_chat/core/` | 真正的编排实现：`AsyncRolePlayEngine`、意图分析、热度分析、参与协调、事件总线、聊天上下文构造 | 不负责 workspace 文件发现与目录组织 |
| `sirius_chat/async_engine/` | 兼容导出、提示词/任务配置/工具函数辅助层 | 不是持久化所有者，也不是 engine 真正实现位置 |
| `sirius_chat/memory/` | 用户记忆、事件记忆、自身记忆、质量评估 | 不直接决定 provider 路由 |
| `sirius_chat/session/` | session store 协议、JSON/SQLite 实现、兼容运行器 | 不负责 provider 注册表 |
| `sirius_chat/providers/` | provider 协议、具体上游实现、注册表、自动路由、中间件 | 不介入高层 session 生命周期 |
| `sirius_chat/roleplay_prompting.py` | persona 问卷、`PersonaSpec`、agent 资产生成与选择 | 不负责普通对话 session 落盘 |
| `sirius_chat/token/` | token 记录、SQLite 归档、多维分析 | 不参与对话决策 |
| `sirius_chat/skills/` | SKILL 注册、依赖解析、执行与 data store | 不负责 provider 注册表和 workspace 默认值 |
| `sirius_chat/cache/`、`sirius_chat/performance/` | 缓存与性能工具 | 不改变核心对话契约 |

### 真实的 engine 位置

- `AsyncRolePlayEngine` 的实现位于 `sirius_chat/core/engine.py`。
- `sirius_chat/async_engine/__init__.py` 只是兼容导出入口，并补充 `prompts.py`、`orchestration.py`、`utils.py` 这类辅助模块。
- 涉及主流程的修改，应优先查看 `sirius_chat/core/engine.py`、`sirius_chat/core/chat_builder.py`、`sirius_chat/core/memory_runner.py`、`sirius_chat/core/engagement_pipeline.py`。

## Workspace 与持久化所有权

### 双根布局

当前 workspace 支持配置根与运行根分离：

- config root：`workspace.json`、`config/session_config.json`、`providers/provider_keys.json`、`roleplay/`、`skills/`
- data root：`sessions/`、`memory/`、`token/`、`skill_data/`、兼容 `primary_user.json`
- 若未显式提供 `config_path`，则退化为单根模式，即 config root 与 data root 指向同一路径

### 关键组件

- `WorkspaceLayout`：所有路径的单一事实来源。
- `WorkspaceRuntime`：初始化目录、配置热刷新、session 锁、engine 生命周期、participants 写回。
- `ConfigManager`：读取 `workspace.json` 与 `config/session_config.json`，构建 `WorkspaceConfig` 与 `SessionConfig`。
- `SessionStoreFactory`：按 `session_id` 创建 `JsonSessionStore` 或 `SqliteSessionStore`。

### 配置合并规则

- `workspace.json` 是 runtime 的机器可读 manifest，同时记录 `bootstrap_signature`，用于避免同一份 `WorkspaceBootstrap` 在重启时重复覆盖用户后续的手工修改。
- `config/session_config.json` 是面向人工维护的 JSONC 快照。
- 两者存在重叠字段时，`config/session_config.json` 对 `session_defaults` 与 `orchestration` 保持更高优先级；`generated_agent_key` 会在 manifest 缺失显式选择或 snapshot 更合适时回写为当前 active agent。

### Session store 语义

- 默认 store 为 `SqliteSessionStore`，路径是 `sessions/<session_id>/session_state.db`。
- `JsonSessionStore` 仍可选，但只作为显式指定的后端。
- SQLite store 使用结构化表存储消息、reply runtime、用户档案、事实与 token 记录，不再依赖单条 payload 快照。
- 打开 session 时会自动迁移 sibling `session_state.json` 与早期 payload 风格 SQLite。

## 运行生命周期

### 1. 构建 SessionConfig

典型顺序如下：

1. 调用方通过 `WorkspaceRuntime.open(...)` 提供 `work_path`、可选 `config_path`、可选 `bootstrap`。
2. `WorkspaceRuntime.initialize()` 使用 `WorkspaceLayout.ensure_directories()` 建立目录结构；若提供了 `bootstrap`，则只会在首次遇到该 bootstrap 签名时把默认值持久化到 workspace。
3. `ConfigManager.load_workspace_config()` 读取 `workspace.json` 与 `config/session_config.json`。
4. `WorkspaceProviderManager.load()` 读取 `providers/provider_keys.json`。
5. `roleplay_prompting.load_generated_agent_library()` 读取 `roleplay/generated_agents.json`，找到已选 agent。
6. `ConfigManager.build_session_config()` 把 workspace 默认值与已选 `GeneratedSessionPreset` 组合成可运行的 `SessionConfig`。

补充说明：`ConfigManager` 会在构建 `SessionConfig` 前把旧版 `enable_intent_analysis` / `intent_analysis_model` 自动规范化到 `task_enabled["intent_analysis"]` / `task_models["intent_analysis"]`，把旧版 `message_debounce_seconds` 按四舍五入映射到 `pending_message_threshold`，并把旧版 `memory_manager_model` / `memory_manager_temperature` / `memory_manager_max_tokens` 迁移为 `memory_manager` 任务参数；新的模板与持久化快照不再写回这些旧字段。

### 2. 执行单轮消息

`WorkspaceRuntime.run_live_message(...)` 的高层职责：

1. 初始化 workspace，并执行一次配置签名校验。
2. 将外部请求追加到 `session_id` 级待处理队列，并启动单会话 processor。
3. processor 在 session 锁内构建 `SessionConfig`；若 `min_reply_interval_seconds` 尚未满足，会继续保留队列并等待，再依据 `pending_message_threshold` 或冷却后的强制批处理逻辑决定逐条处理还是合并同一说话人的连续消息。
4. 读取 session store，恢复 `Transcript`，再调用 `AsyncRolePlayEngine.run_live_session(...)` 初始化上下文。
5. 对选中的单条或批量消息调用一次 `AsyncRolePlayEngine.run_live_message(...)`。
6. 在成功后写回 session store 与 `sessions/<session_id>/participants.json`，并把同一批次结果返回给所有等待中的调用方。

`AsyncRolePlayEngine.run_live_message(...)` 的核心阶段：

1. 校验输入 turn，必要时自动注册 `user_profile`。
2. 追加用户消息并更新 `Transcript.reply_runtime`。
3. 并行执行记忆相关任务与 `intent_analysis` 任务；若 `intent_analysis` 已启用，则该轮意图结论必须来自模型，预算不足、调用失败或解析失败时不会再回退到关键词意图推断。
4. 用 `HeatAnalyzer`、`IntentAnalyzer` 与 `EngagementCoordinator` 决定是否回复；在多 AI 群聊里，只有“明确指向当前模型自身”的消息才会走高优先级回复通道，指向其他 AI 时会主动抑制回复。为降低误判，`IntentAnalyzer` 发给模型的上下文已改为最近交互链摘要，并额外暴露最近 AI 发言者、最近用户侧发言者、近期别称、`environment_context` 环境线索，以及当前消息命中的“当前模型 / 其他 AI / 名称含 AI 线索对象 / possible-AI 候选对象”等线索。提示词不再预先把其它对象硬标为人类；只有名字或别称带有明显 AI 证据时才直接按 AI 对待，其余对象交给模型结合上下文判断。对未明确点名当前模型的群控/停用类命令，还会在 engagement 前做硬抑制。
5. 构建系统提示词，注入用户记忆、事件命中、自身记忆、`session_summary`、`environment_context` 与安全约束。
6. 选择模型并调用 provider；若需要，进入 SKILL 执行循环。
7. 记录 token 使用，压缩历史摘要，发出事件流。

补充说明：`min_reply_interval_seconds` 只会推迟“下一次回复判断”的进入时机，不会绕过 `reply_mode=auto` 的意图分析和 engagement 决策；因此冷却结束后仍可能选择不回复。

### 3. 后台循环

会话初始化后，`BackgroundTaskManager` 会按 `OrchestrationPolicy.consolidation_*` 配置静默启动归纳循环，整理事件、摘要与事实，控制记忆膨胀。若 live turn 在 finalize 时发现当前上下文已经逼近 `history_max_chars`，engine 还会直接补跑一轮归纳，不再只依赖后台定时器。该循环不再提供单独开关；若需停用相关 LLM 调用，应关闭 `task_enabled["memory_manager"]`。

## 记忆架构

### 用户记忆

- 代码位置：`sirius_chat/memory/user/`
- 运行时事实来源：`Transcript.user_memory`
- 主要职责：身份解析、别名与外部 identity 映射、近期消息、结构化 `memory_facts`、摘要笔记
- 外部稳定查询入口：`Transcript.find_user_by_channel_uid(channel, uid)`

### 事件记忆

- 代码位置：`sirius_chat/memory/event/`
- 持久化位置：`memory/events/events.json`
- 特点：快速路径 + LLM 验证的两级验证；事件特征会反向沉淀为用户事实

### AI 自身记忆

- 代码位置：`sirius_chat/memory/self/`
- 持久化位置：`memory/self_memory.json`
- 组成：日记系统 + 名词解释系统
- 触发方式：主流程内联触发；除 `self_memory_extract_batch_size` 和 `self_memory_min_chars` 外，当当前上下文已明显变长时也会自动提取
- 模型路由：优先使用 `task_models["self_memory_extract"]`；未显式配置时复用 `memory_manager` 模型，便于两者共用同一辅助模型

### 质量评估工具

- 代码位置：`sirius_chat/memory/quality/`
- 用途：离线质量评估、衰退与清理辅助
- 说明：它是辅助工具层，不是主运行时入口；当前架构文档不要求调用方依赖独立命令行工具

## Provider 系统

### 组成

- `providers/base.py`：`LLMProvider` / `AsyncLLMProvider` 协议
- `providers/routing.py`：`ProviderRegistry`、`WorkspaceProviderManager`、`AutoRoutingProvider`
- `providers/middleware/`：速率限制、重试、断路器、成本统计
- 具体 provider：OpenAI-compatible、Aliyun Bailian、BigModel、DeepSeek、SiliconFlow、Volcengine Ark、YTea、Mock

### 路由规则

- 优先按 `ProviderConfig.models` 显式模型列表匹配
- 其次按 `healthcheck_model` 精确匹配
- 若都未命中，则回退到第一个启用 provider
- `WorkspaceRuntime` 在未显式注入 provider、或注入的是 `AutoRoutingProvider` 时，默认优先使用 workspace provider 注册表

### 配置热刷新

- `WorkspaceConfigWatcher` 监听 `workspace.json`、`config/session_config.json`、`providers/provider_keys.json`、`roleplay/generated_agents.json`、`skills/*.py` 与 `skills/README.md`
- 检测到变化后，runtime 会重建 engine 状态，确保新 provider 配置或已选 agent 真正生效

### SKILL 生命周期

- `WorkspaceRuntime.initialize()` 会在框架启动时预先建立共享 `SkillRegistry` 与 `SkillExecutor`。
- `skills/` 目录变化后，runtime 会通过 watcher 触发全量重载，移除已删除的 SKILL，避免每条消息动态扫描目录。
- engine 主流程只消费 runtime 注入的 SKILL 运行时对象，不再在消息路径上做惰性 reload。
- `SKILL_COMPLETED` 事件只暴露执行状态，不直接携带技能结果正文；技能结果会先被规范化为内部文本/多模态通道参与下一轮生成，只有转成 assistant 回复后才会进入外部消息流。
- SKILL 返回结果支持结构化 `text_blocks`、`multimodal_blocks` 与 `internal_metadata`；engine 会把文本块和可识别图片隐藏注入模型上下文，同时通过系统提示词约束模型不要复述字段名、`mime_type`、`label`、路径、URL 或其他技能元信息。

## Roleplay 资产系统

### 资产流

1. 外部通过问卷模板、`trait_keywords`、`answers`、`dependency_files` 等构造 `PersonaSpec`。
2. `roleplay_prompting.py` 在调用生成模型前先保存 pending spec，防止失败丢失输入。
3. 生成成功后写入 `roleplay/generated_agents.json` 与 `roleplay/generated_agent_traces/<agent_key>.json`。
4. `select_generated_agent_profile()` 或 `RoleplayWorkspaceManager.bootstrap_active_agent()` 会同步更新 `workspace.active_agent_key`。
5. 后续 `ConfigManager.build_session_config()` 直接从已选资产构建 `SessionConfig`。

### 边界约束

- roleplay 资产生成不负责普通 session transcript 的读写。
- 普通对话运行也不会直接修改 persona 资产，除非显式调用 roleplay API。

## Public API 与兼容性

- `sirius_chat/api/` 按主题拆分为 `engine.py`、`models.py`、`providers.py`、`session.py`、`memory.py`、`token_usage.py`、`prompting.py` 等模块。
- 根包 `sirius_chat/__init__.py` 再统一重导出外部常用符号。
- `AsyncRolePlayEngine` 从 `sirius_chat.core` 导出，再由 `sirius_chat.async_engine` 与 `sirius_chat.api` 提供兼容访问路径。
- `User` 是 `Participant` 的别名；外部如果只想在运行时注册用户，通常传 `UserProfile` 更合适。

## 扩展与修改规则

1. 新增 provider：修改 `sirius_chat/providers/`、`providers/routing.py`、`api/providers.py`，并同步 README、外部接入文档与测试。
2. 修改 session / workspace 契约：同步 `config/models.py`、`config/manager.py`、`workspace/`、README、`docs/full-architecture-flow.md` 与相关 SKILL。
3. 修改 engine 主流程：优先检查 `core/engine.py` 及其 helper，不要把 provider 细节塞进 core。
4. 修改外部可见 API：必须同步 `sirius_chat/api/` 与示例代码。

## 已知限制

- 自动路由当前仍是轻量规则，不包含更复杂的健康检查、负载均衡或熔断编排策略。
- 当前主流程仍以完整上下文 + 摘要压缩为主；极长会话需依赖合理的 `history_*` 预算与摘要策略。
- API Key 目前主要来自配置与 provider 注册表；生产环境仍建议配合环境变量或外部 secret 管理。

## 相关技能

- 框架速读：`.github/skills/framework-quickstart/SKILL.md`
- 外部接入：`.github/skills/external-integration/SKILL.md`
- 结构同步：`.github/skills/project-structure-sync/SKILL.md`


