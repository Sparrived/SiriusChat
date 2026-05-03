# Sirius Chat 架构说明

本文档描述当前代码库的稳定架构边界。历史迁移文档只用于解释版本演进，不作为当前实现的事实来源；当前架构以本文档、[full-architecture-flow.md](full-architecture-flow.md) 与实际代码为准。

## 目标

Sirius Chat 是一个面向"多人用户与单主 AI"交互场景的编排框架，目标包括：

- 为 CLI、脚本、服务端集成和外部编排器提供统一的会话运行模型
- 让调用方只关心输入消息与业务上下文，而不是底层文件布局与恢复细节
- 在多轮对话中保持用户画像、基础记忆、日记记忆、AI 自身名词解释与会话节奏的连续性
- 用 provider 抽象隔离上游模型差异，让编排逻辑稳定留在框架内部

## 核心原则

- Workspace 持久化由 runtime 统一管理，外部不直接拼接内部文件路径。
- `sirius_chat/models/models.py` 与 `sirius_chat/config/models.py` 是核心数据契约的事实来源。
- provider 细节只允许位于 `sirius_chat/providers/`，不混入编排核心。
- 当前推荐生产入口是 `PersonaManager`（多人格生命周期管理）；单个人格可直接创建 `EngineRuntime` 或 `EmotionalGroupChatEngine`。
- **v1.1.0**：`EmotionalGroupChatEngine` 是唯一默认引擎。
- 配置资产与运行态数据支持双根分离：config root 负责配置与角色资产，data root 负责 session、memory、token 与 skill_data。

## 推荐入口

| 入口 | 适用场景 | 负责内容 |
| --- | --- | --- |
| `PersonaManager` | **v1.0 推荐生产入口**：多人格生命周期管理 | 扫描人格目录、端口分配、启停调度、日志读取、监控子进程心跳 |
| `main.py` | 统一 CLI 入口 | 无参数启动 WebUI；`run` 启动所有人格 + NapCat + WebUI；`persona` 子命令管理单个人格 |
| `PersonaWorker` | 单个人格子进程入口 | 加载配置、创建 EngineRuntime、启动 NapCatBridge、心跳、日志归档 |
| `EngineRuntime` | 单个人格运行时封装 | 懒加载 EmotionalGroupChatEngine、Provider 绑定、SkillBridge 注入 |
| `EmotionalGroupChatEngine` | **v1.0 唯一引擎**：情感化群聊场景 | 四层认知架构编排、三层记忆底座、后台任务、事件流 |
| `create_emotional_engine(...)` | Python API 直接创建引擎（兼容旧版） | 绑定 workspace provider、加载 persona、配置参数 |

### 需要明确的语义

- `SessionConfig` 现在要求 `preset=AgentPreset(...)`，而不是直接在配置文件里手写 `agent` 和 `global_system_prompt`。
- `SessionConfig.work_path` 在当前架构中表示 config root；`SessionConfig.data_path` 表示 data root。
- `User` 是 `Participant` 的公开别名，不存在第二套独立的人类参与者模型。

## 模块边界

| 模块 | 主要职责 | 不应承担的职责 |
| --- | --- | --- |
| `sirius_chat/__init__.py` | 顶层公开 API 统一重导出（严格 `__all__`） | 不直接实现底层编排或路径布局 |
| `sirius_chat/persona_manager.py` | **v1.0 推荐生产入口**：多人格生命周期管理 | 不实现底层对话生成 |
| `sirius_chat/persona_worker.py` | 单个人格子进程入口：加载配置、创建 EngineRuntime、心跳 | 不管理其他人格 |
| `sirius_chat/persona_config.py` | 人格级配置模型：adapters、experience、paths | 不处理全局配置 |
| `sirius_chat/platforms/` | NapCat 多实例管理、QQ 桥接器、EngineRuntime 封装、setup wizard | 不介入高层人格调度 |
| `sirius_chat/webui/` | WebUI REST API + 静态页面 | 不直接操作 NapCat 进程 |
| `sirius_chat/core/` | 编排核心：`EmotionalGroupChatEngine`、意图分析、情感分析、响应策略、阈值引擎、节奏分析、事件总线、身份解析 | 不负责人格目录组织 |
| `sirius_chat/memory/` | 基础记忆、日记记忆、用户管理、名词解释、语义记忆、上下文组装 | 不直接决定 provider 路由 |
| `sirius_chat/providers/` | provider 协议、具体上游实现、注册表、自动路由、中间件 | 不介入高层人格生命周期 |
| `sirius_chat/skills/` | SKILL 注册、依赖解析、执行、安全校验、遥测、数据存储 | 不负责 provider 注册表 |
| `sirius_chat/config/` | SessionConfig、WorkspaceConfig、ConfigManager、JSONC、helpers | 不改变核心对话契约 |
| `sirius_chat/models/` | 数据契约：Message、Participant、EmotionState、IntentAnalysisV3 等 | 不处理持久化 |
| `sirius_chat/session/` | SessionStore（Json/Sqlite）、持久化后端 | 不介入对话逻辑 |
| `sirius_chat/token/` | Token 记录、SQLite 持久化、成本分析 | 不介入对话逻辑 |
| `sirius_chat/utils/` | 工具函数、WorkspaceLayout 路径布局 | 不改变核心对话契约 |

### 真实的 engine 位置

- **默认引擎**：`EmotionalGroupChatEngine` 的实现位于 `sirius_chat/core/emotional_engine.py`。
- `sirius_chat/core/cognition.py`：统一情绪+意图分析器。
- `sirius_chat/core/response_assembler.py`：prompt 组装 + 风格适配。
- `sirius_chat/memory/glossary/`：名词解释（AI 自身知识库，由 `learn_term` SKILL 写入）。
- `sirius_chat/memory/basic/`：基础记忆（按群滑动窗口、热度跟踪、归档存储）。
- `sirius_chat/memory/diary/`：日记记忆（LLM 生成摘要、索引、token 预算检索）。
- `sirius_chat/memory/context_assembler.py`：上下文组装器（basic + diary → XML 嵌入 system prompt，只返回 `[system, user]` 2 条消息）。
- `sirius_chat/memory/semantic/`：语义记忆（群规范学习、氛围记录、关系状态、持久化）。
- `sirius_chat/core/identity_resolver.py`：跨平台身份解析器。

## Workspace 与持久化所有权

### 双根布局

当前人格目录支持配置与运行数据隔离：

- 全局配置：`data/global_config.json`、`data/providers/provider_keys.json`、`data/adapter_port_registry.json`
- 人格级配置（`data/personas/{name}/`）：`persona.json`、`orchestration.json`、`adapters.json`、`experience.json`
- 人格级运行数据（`data/personas/{name}/`）：`memory/`、`diary/`、`engine_state/`、`skill_data/`、`logs/`

### 关键组件

- `WorkspaceLayout`：所有路径的单一事实来源。
- `EngineRuntime`：单个人格运行时封装，懒加载 `EmotionalGroupChatEngine`，绑定 Provider 与 SkillBridge。
- `PersonaManager`：多人格生命周期管理，扫描目录、端口分配、启停调度、心跳监控。
- `ConfigManager`：读取人格级 `persona.json`、`orchestration.json`、`adapters.json`、`experience.json` 与全局 `data/global_config.json`。
- `SessionStoreFactory`：按 `session_id` 创建 `JsonSessionStore` 或 `SqliteSessionStore`。

### 配置合并规则

- `global_config.json` 是全局机器可读 manifest，记录 WebUI 参数、NapCat 管理、日志级别等。
- `config/session_config.json` 是面向人工维护的 JSONC 快照。
- 两者存在重叠字段时，`config/session_config.json` 对 `session_defaults` 与 `orchestration` 保持更高优先级；`generated_agent_key` 会在 manifest 缺失显式选择或 snapshot 更合适时回写为当前 active agent。

### Session store 语义

- 默认 store 为 `SqliteSessionStore`，路径是 `sessions/<session_id>/session_state.db`。
- `JsonSessionStore` 仍可选，但只作为显式指定的后端。
- SQLite store 使用结构化表存储消息、reply runtime、用户档案（UserProfile 扁平结构）与 token 记录，不再依赖单条 payload 快照。用户事实与 runtime 状态已收敛到 `UserProfile.metadata`。
- 打开 session 时会自动迁移 sibling `session_state.json` 与早期 payload 风格 SQLite。

## 运行生命周期

### 1. 构建 SessionConfig

典型顺序如下：

1. 调用方通过 `PersonaManager` 或 `EngineRuntime` 加载人格配置；`PersonaWorker` 在子进程中完成初始化。
2. `EngineRuntime` 使用人格目录（`data/personas/{name}/`）作为工作路径；`PersonaWorker` 在启动时加载 `adapters.json`、`experience.json`、`persona.json`。
3. `ConfigManager` 读取全局配置 `data/global_config.json` 与人格级配置。
4. `ProviderRegistry` 从全局 `data/providers/provider_keys.json` 加载 Provider 凭证（所有人格共用）。
5. `PersonaManager` 扫描 `data/personas/` 目录，为每个人格分配 NapCat 端口并维护注册表。
6. `PersonaWorker` 创建 `EngineRuntime`，后者懒加载 `EmotionalGroupChatEngine` 并启动后台任务。

补充说明：`ConfigManager` 会在构建 `SessionConfig` 前把旧版 `message_debounce_seconds` 按四舍五入映射到 `pending_message_threshold`；新的模板与持久化快照不再写回旧字段。

### 2. 执行单轮消息

#### Emotional 路径（默认）

`EmotionalGroupChatEngine.process_message(message, participants, group_id)` 的核心阶段：

1. **感知层**：`IdentityResolver.resolve()` 解析跨平台身份；`UserManager.register()` 注册参与者（群隔离）；`BasicMemoryManager.add_entry()` 写入按群滑动窗口（硬限制 30 条，上下文窗口 5 条）；`RhythmAnalyzer.analyze()` 更新群体热度与节奏；更新 `group_last_message_at`。
2. **认知层（统一）**：`CognitionAnalyzer` 联合规则引擎分析情绪+意图（零成本热路径，~90% 命中）；LLM fallback 处理复杂情况（~10% 命中）。
3. **决策层**：`RhythmAnalyzer` 分析对话节奏；`ThresholdEngine` 计算动态阈值（base × activity × relationship × time）；`ResponseStrategyEngine` 选择 IMMEDIATE / DELAYED / SILENT / PROACTIVE；更新 `AssistantEmotionState`。
4. **执行层**：`ResponseAssembler` 返回 `PromptBundle`（`system_prompt` 包含 persona、情绪、共情策略、日记引用、glossary、skill 描述与输出格式指令；`user_content` 为当前消息的格式化内容）。`ContextAssembler.build_messages()` 将基础记忆最近 n 条以 XML 格式嵌入 system prompt，日记检索 top_k 条同样注入 system prompt，最终只返回 `[{"role":"system","content":...}, {"role":"user","content":...}]` 2 条消息；`_generate()` 自动清洗模型仿写的 `<conversation_history>` 标签。`StyleAdapter` 动态调整 `max_tokens` / `temperature` / `tone`。`ModelRouter` 按任务感知选择模型，调用 provider 生成回复。
5. **后台层（异步）**：`_bg_diary_promoter` 检查群体变冷（heat < 0.25 且沉默 > 300s）的基础记忆归档，经 `DiaryGenerator` 生成日记并写入 `DiaryManager`；群氛围与规范学习随消息实时更新。
