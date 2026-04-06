# Sirius Chat 全量架构与流程图

本文档给出项目的完整可读架构视图，覆盖：

- 端到端执行流程
- 关键模块边界
- 每个模块的输入/输出产物
- 代码更新后的文档同步要求

## 1. 全量端到端流程图

```mermaid
flowchart TD
    A["外部入口<br/>CLI / Python API / main.py"] --> B["配置加载<br/>SessionConfig<br/>providers 列表<br/>work_path"]
    B --> C["Provider 构建<br/>merge_provider_sources<br/>AutoRoutingProvider"]
    C --> D["会话执行<br/>AsyncRolePlayEngine<br/>.run_live_session"]

    D --> E["参与者解析<br/>channel identity<br/>-&gt; speaker"]
    E --> F["当前轮消息写入<br/>Transcript.messages"]
    F --> G["用户识别与记忆更新<br/>UserMemoryManager"]
    G --> G2["事件命中与落盘<br/>EventMemoryManager"]
    
    G2 --> G3["事件→用户洞察映射<br/>apply_event_insights<br/>interpret_event_with_user_context"]
    G3 --> G4["事件特征→用户事实<br/>emotional_pattern<br/>user_interest<br/>social_context"]

    G4 --> H{orchestration<br/>enabled?}
    H -- 支持多模型 --> I["memory_extract 任务<br/>按配置模型/预算/重试"]
    H -- 支持多模型 --> J["multimodal_parse 任务<br/>多模态证据提取"]
    H -- 仅单模型 --> K["跳过编排<br/>直接主模型回复"]
    I --> K
    J --> K

    K --> L["构建系统提示词<br/>主 AI + 参与者记忆<br/>+ 会话摘要<br/>分割指令（可选）"]
    L --> M["调用 Provider<br/>自动路由或指定<br/>生成 assistant 回复"]
    M --> N["token_usage_records<br/>按 actor/task/model<br/>聚合"]
    N --> O["自动压缩历史<br/>session_summary<br/>超过长度阈值时"]
    O --> P["输出更新后<br/>Transcript"]

    P --> Q["持久化选项<br/>JsonSessionStore<br/>SqliteSessionStore"]
    P --> Q2["事件落盘<br/>work_path/events<br/>events.json"]
    Q --> R["可选会话恢复<br/>resume flag"]
    R --> D
```

## 2. 模块分层图

```mermaid
flowchart LR
    subgraph Entry[入口层]
      CLI[cli.py]
      PublicAPI[api/]
      Main[main.py]
    end

    subgraph Domain[领域与编排层]
      Models[models.py]
      Engine[async_engine.py]
      Prompting[roleplay_prompting.py]
      UserMemory[user_memory.py]
      TokenUsage[token_usage.py]
      Memory[memory/]
    end

    subgraph Infra[基础设施层]
      SessionStore[session_store.py]
      SessionRunner[session_runner.py]
      Routing[providers/routing.py]
      ProviderBase[providers/base.py]
      Middleware["providers/middleware"]
      ProviderImpl[providers/implementations]
      ConfigMgr[config_manager.py]
      Cache[cache/]
      Perf[performance/]
    end

    Main --> PublicAPI
    CLI --> PublicAPI
    PublicAPI --> Engine
    PublicAPI --> Prompting
    Engine --> Models
    Engine --> Memory
    Engine --> UserMemory
    Engine --> TokenUsage
    Engine --> ProviderBase
    Routing --> ProviderImpl
    ProviderImpl --> ProviderBase
    Middleware --> ProviderBase
    CLI --> Routing
    SessionRunner --> Engine
    SessionRunner --> SessionStore
```

## 3. Provider 构建流程（v1.0 统一格式）

```mermaid
flowchart LR
    A["session.json<br/>providers: [{<br/>  'type': 'openai',<br/>  'label': 'gpt4',<br/>  'api_key': '<br/>  API_KEY_NAME'<br/>}]"] --> B["merge_provider_sources<br/>work_path<br/>providers_config"]
    B --> B2["ProviderRegistry<br/>从 work_path/<br/>provider_keys.json<br/>加载持久化密钥"]
    B2 --> C["AutoRoutingProvider<br/>或指定 Provider"]
    C --> D["Engine.provider<br/>生成文本"]

    style A fill:#e1f5ff
    style B fill:#fff3e0
    style B2 fill:#f3e5f5
    style C fill:#e8f5e9
    style D fill:#fce4ec
```

**关键点**：
- v1.0 统一采用 `providers` 列表格式（删除向后兼容的 `provider` 单字段）
- `merge_provider_sources()` 签名简化：仅接收 `(work_path, providers_config)`
- 持久化密钥通过 `<work_path>/provider_keys.json` 管理，由 ProviderRegistry 自动加载
- SessionConfig 内部转为单一 Provider 实例（AutoRoutingProvider 或指定 Provider）
- 支持多 Provider 路由：AutoRoutingProvider 在运行时自动选择合适提供商

## 4. 模块输入/输出产物清单

| 模块 | 主要输入 | 主要输出/产物 |
| --- | --- | --- |
| `main.py` | 命令行参数、用户输入、`work_path`、`config.json` | `Transcript`、`transcript.json`、`session_config.persisted.json`、`primary_user.json`、`events/events.json` |
| `sirius_chat/cli.py` | `config.json`（含 `providers` 列表）、单轮用户输入 | 单轮 `Transcript`、`transcript.json` |
| `sirius_chat/api/` | 外部程序调用参数、`work_path` | 稳定对外函数与类型、`Transcript` |
| `sirius_chat/models/models.py` | 配置与消息数据 | 统一数据契约（`Message`、`Participant`、`Transcript` 等） |
| `sirius_chat/async_engine/core.py` | 初始化：`SessionConfig` + 可选已有 `Transcript`；逐条处理：`Message` + `Transcript` | 更新后的 `Transcript`、assistant 回复、编排统计与 token 记录 |
| `sirius_chat/user_memory.py` | speaker/channel identity、用户消息文本 | 用户档案与运行时记忆（profile/runtime）、事件记忆（命中/新增） |
| `sirius_chat/memory/` | 用户信息、对话历史、事件数据 | 记忆库、事件落盘、用户档案提取 |
| `sirius_chat/roleplay_prompting.py` | 角色问答、agent 名称、模型 | `GeneratedSessionPreset`、`generated_agents.json`、可直接创建的 `SessionConfig` |
| `sirius_chat/token/usage.py` | `Transcript.token_usage_records` | baseline 与按 actor/task/model 聚合报表 |
| `sirius_chat/session/store.py` | `Transcript` | JSON/SQLite 持久化状态文件 |
| `sirius_chat/session/runner.py` | `SessionConfig`、Provider、主用户输入、`work_path` | 自动持久化会话循环、主用户档案维护、恢复状态管理 |
| `sirius_chat/config_manager.py` | JSON 配置文件、环境变量 | 合并配置、环境变量覆盖、配置验证 |
| `sirius_chat/providers/base.py` | `GenerationRequest` | Provider 协议（同步/异步生成契约） |
| `sirius_chat/providers/middleware/` | `GenerationRequest`、中间件链配置 | 透明的 Provider 功能扩展（流控、重试、成本计量） |
| `sirius_chat/providers/routing.py` | `work_path`、`providers_config` 列表 | ProviderRegistry、`provider_keys.json`、最终路由选择 |
| `sirius_chat/providers/openai_compatible.py` | `GenerationRequest` | 模型文本回复、token 使用统计 |
| `sirius_chat/providers/siliconflow.py` | `GenerationRequest` | 模型文本回复、token 使用统计 |
| `sirius_chat/providers/volcengine_ark.py` | `GenerationRequest` | 模型文本回复、token 使用统计 |
| `sirius_chat/providers/mock.py` | `GenerationRequest` | 可预测测试回复 |
| `sirius_chat/cache/` | 缓存 key、模型响应值 | 缓存命中/未命中、LRU 淘汰、TTL 过期管理 |
| `sirius_chat/performance/` | 代码块/函数调用记录、基准参数 | 执行指标（时间、内存）、性能统计聚合、基准对比结果 |

## 4. 关键运行产物说明

- `Transcript.messages`: 会话全量消息（system/user/assistant）。
- `Transcript.user_memory`: 识人记忆状态（跨轮次延续）。
- `Transcript.session_summary`: 自动压缩后的历史摘要。
- `Transcript.orchestration_stats`: 任务级统计（attempted/succeeded/failed 等）。
- `Transcript.token_usage_records`: 每次模型调用的 token 归档。
- `generated_agents.json`: 由提示词生成器输出并持久化的 agent 资产库。
- `session_state.json` / `session_state.db`: 会话持久化与恢复状态。
- `events/events.json`: 事件记忆持久化文件（用于跨会话事件命中）。

## 5. 代码更新后的强制同步规则

当仓库发生代码更新时，本文件必须同步检查并更新以下内容：

1. 流程图是否仍与当前执行路径一致。
2. 模块输入/输出是否与代码契约一致。
3. 新增模块是否出现在分层图和产物清单中。
4. 删除/合并模块是否从图与表中移除。
5. **v1.0 生产标准**：所有示例与文档必须使用统一的 `providers` 列表格式，不支持向后兼容的单 `provider` 字段。

推荐同步顺序：

1. 更新代码。
2. 更新 `docs/full-architecture-flow.md`（特别是流程图、分层图、产物清单）。
3. 验证所有示例配置采用 `providers` 列表格式。
4. 再同步 `docs/architecture.md`、`docs/external-usage.md`、README 与 SKILL（如有必要）。
5. 运行 `pytest -q` 验证所有测试通过。

**v1.0 核心约束**：
- SessionConfig 必须通过 `providers` 列表字段（JSON 数组）指定所有提供商配置。
- `merge_provider_sources(work_path, providers_config)` 自动从 `<work_path>/provider_keys.json` 加载持久化密钥。
- 不存在中间提取层或向后兼容转换逻辑。
- 所有持久化文件使用统一的 `providers` 列表格式。


