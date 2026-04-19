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
- **v1.0.0 新增**：`EmotionalGroupChatEngine` 作为新的引擎选项，与 `AsyncRolePlayEngine` 并存；两者不共享内部状态。
- 配置资产与运行态数据支持双根分离：config root 负责配置与角色资产，data root 负责 session、memory、token 与 skill_data。

## 推荐入口

| 入口 | 适用场景 | 负责内容 |
| --- | --- | --- |
| `open_workspace_runtime(...)` / `WorkspaceRuntime` | 默认外部接入、生产服务、插件宿主 | 初始化 workspace、热刷新、session 恢复、participants 元数据、engine 生命周期。`create_emotional_engine()` 工厂方法 |
| `EmotionalGroupChatEngine` | **v1.0.0 默认引擎**：情感化群聊场景，需要情感分析、延迟响应、主动发起、群隔离记忆 | 四层认知架构编排、三层记忆底座、后台任务、事件流 |
| `create_emotional_engine(...)` | Python API 直接创建引擎 | 绑定 workspace provider、加载 persona、配置参数 |
| `main.py` | 仓库级交互入口、调试与人工演练 | provider 管理命令、持续会话。**默认 `--engine emotional`** |
| `sirius-chat` | 库内薄 CLI、单轮调用或模板导出 | session JSON bootstrap、单轮会话执行、模板输出 |

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
| `sirius_chat/core/` | 真正的编排实现：`AsyncRolePlayEngine`、`EmotionalGroupChatEngine`、意图分析、热度分析、参与协调、事件总线、聊天上下文构造 | 不负责 workspace 文件发现与目录组织 |
| `sirius_chat/async_engine/` | 兼容导出、提示词/任务配置/工具函数辅助层 | 不是持久化所有者，也不是 engine 真正实现位置 |
| `sirius_chat/memory/` | 用户记忆、事件记忆、自身记忆、质量评估。**v1.0.0 新增**：工作记忆、情景记忆、语义记忆、激活度引擎、三级检索引擎 | 不直接决定 provider 路由 |
| `sirius_chat/session/` | session store 协议、JSON/SQLite 实现、兼容运行器 | 不负责 provider 注册表 |
| `sirius_chat/providers/` | provider 协议、具体上游实现、注册表、自动路由、中间件 | 不介入高层 session 生命周期 |
| `sirius_chat/roleplay_prompting.py` | persona 问卷、`PersonaSpec`、agent 资产生成与选择 | 不负责普通对话 session 落盘 |
| `sirius_chat/token/` | token 记录、SQLite 归档、多维分析 | 不参与对话决策 |
| `sirius_chat/skills/` | SKILL 注册、依赖解析、执行与 data store | 不负责 provider 注册表和 workspace 默认值 |
| `sirius_chat/cache/`、`sirius_chat/performance/` | 缓存与性能工具 | 不改变核心对话契约 |

### 真实的 engine 位置

- **v1.0.0 默认引擎**：`EmotionalGroupChatEngine` 的实现位于 `sirius_chat/core/emotional_engine.py`。
- `sirius_chat/core/cognition.py`：统一情绪+意图分析器。
- `sirius_chat/core/response_assembler.py`：prompt 组装 + `<think>` / `<say>` 双输出解析。
- `sirius_chat/memory/autobiographical/`：自传体记忆（第一人称体验记录 + glossary 术语表）。
- **Legacy 归档**：`AsyncRolePlayEngine` 的实现位于 `sirius_chat/core/_legacy/engine.py`，不再维护新功能。

## Workspace 与持久化所有权

### 双根布局

当前 workspace 支持配置根与运行根分离：

- config root：`workspace.json`、`config/session_config.json`、`providers/provider_keys.json`、`roleplay/`、`skills/`
- data root：`sessions/`、`memory/`、`token/`、`skill_data/`、`episodic/`、`semantic/`、`engine_state/`、兼容 `primary_user.json`
- 若未显式提供 `config_path`，则退化为单根模式，即 config root 与 data root 指向同一路径

### 关键组件

- `WorkspaceLayout`：所有路径的单一事实来源。
- `WorkspaceRuntime`：初始化目录、配置热刷新、session 锁、engine 生命周期、participants 写回。**v1.0.0 新增**：`create_emotional_engine()` 工厂方法，为新引擎绑定 workspace provider 与 work_path。
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

#### Emotional 路径（v0.28+ 默认）

`EmotionalGroupChatEngine.process_message(message, participants, group_id)` 的核心阶段：

1. **感知层**：注册参与者到群隔离的 `user_memory`，写入 `working_memory` 滑动窗口（含助手回复，按动态 importance 排序，消息名经过 sanitize），更新 `group_last_message_at`。
2. **认知层（统一）**：`CognitionAnalyzer` 联合规则引擎分析情绪+意图（零成本热路径，~90% 命中）；LLM fallback 处理复杂情况（~10% 命中）；`MemoryRetriever` 三级检索（working → episodic → semantic）获取相关记忆。
3. **决策层**：`RhythmAnalyzer` 分析对话节奏；`ThresholdEngine` 计算动态阈值（base × activity × relationship × time）；`ResponseStrategyEngine` 选择 IMMEDIATE / DELAYED / SILENT / PROACTIVE；更新 `AssistantEmotionState`。
4. **执行层**：`ResponseAssembler` 返回 `PromptBundle`（`system_prompt` 包含 persona、情绪、共情策略、记忆引用、glossary 术语表、skill 描述与输出格式指令；`user_content` 为当前消息的格式化内容）。`_build_history_messages()` 将 `working_memory` 中的历史条目转换为标准 OpenAI `messages` 格式（`user`/`assistant`/`system`）。`StyleAdapter` 动态调整 `max_tokens` / `temperature` / `tone`。`ModelRouter` 按任务感知选择模型，调用 provider 生成回复。skill 多轮循环时，assistant 回复与 skill 执行结果以标准消息形式追加到 `messages` 列表中，而非拼接回 prompt 字符串。
