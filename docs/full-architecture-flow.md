# Sirius Chat 全量架构与流程图

本文档描述当前代码的真实执行路径与模块边界，重点覆盖：

- 入口层如何进入 workspace 与 session
- `WorkspaceRuntime`、`ConfigManager`、`WorkspaceLayout` 的协作关系
- **v1.0 默认引擎** `EmotionalGroupChatEngine` 的四层认知架构流水线
- provider 路由、roleplay 资产、session store 与 memory 的落盘位置

> **v1.0 重大变更**：`EmotionalGroupChatEngine` 是唯一引擎。`AsyncRolePlayEngine` 已完全移除。

历史迁移文档只用于说明版本演进，不作为当前架构的事实来源；当前实现以本文档、[docs/architecture.md](docs/architecture.md) 和实际代码为准。

---

## 1. 当前架构总览

```mermaid
flowchart TD
  Entry["外部入口\nmain.py / sirius_chat/cli.py / sirius_chat.api"] --> Runtime["WorkspaceRuntime\n高层入口与持久化所有者"]
  Runtime --> Layout["WorkspaceLayout\n统一计算 config root / data root 路径"]
  Runtime --> Config["ConfigManager\nworkspace.json + config/session_config.json"]
  Runtime --> ProviderMgr["WorkspaceProviderManager\nproviders/provider_keys.json"]
  Runtime --> Roleplay["roleplay_prompting\nroleplay/generated_agents.json"]
  Config --> SessionCfg["SessionConfig\n由 workspace 默认值 + 已选 agent 资产构建"]
  ProviderMgr --> Provider["AutoRoutingProvider 或显式 Provider"]
  Roleplay --> SessionCfg

  Provider --> EmotionalEngine["EmotionalGroupChatEngine\nsirius_chat/core/emotional_engine.py\n（v1.0.0 默认引擎）"]
  EmotionalEngine --> NewCognitive["core/cognition.py\ncore/response_strategy.py\ncore/threshold_engine.py\ncore/rhythm.py"]
  EmotionalEngine --> NewMemory["memory/basic/\nmemory/diary/\nmemory/user/\nmemory/glossary/\nmemory/context_assembler.py"]
  EmotionalEngine --> NewExecution["core/response_assembler.py\ncore/delayed_response_queue.py\ncore/proactive_trigger.py\ncore/model_router.py"]
  EmotionalEngine --> Skills
  EmotionalEngine --> Token
  EmotionalEngine --> EngineState["core/engine_persistence.py\nengine_state/"]

  SessionStore --> Outputs["会话输出\nsessions/<session_id>/session_state.db\nparticipants.json\nmemory/*\ntoken/token_usage.db"]
  EngineState --> Outputs
```

### 当前版本的几个关键事实

- 推荐外部入口是 `open_workspace_runtime(...)` / `WorkspaceRuntime`，而不是让调用方自己管理文件布局。
- `WorkspaceLayout` 是路径的单一事实来源，决定配置资产与运行态数据分别落在哪里。
- **v1.0.0 默认引擎** `EmotionalGroupChatEngine` 的实现位于 `sirius_chat/core/emotional_engine.py`，采用四层认知架构（感知→认知→决策→执行）与简化记忆模型（基础记忆 → 日记记忆）。
- **Legacy 引擎 `AsyncRolePlayEngine` 已完全移除**；`sirius_chat/async_engine/` 仅保留辅助导出与配置常量。
- `sirius_chat/async_engine/` 承担 legacy 兼容导出、提示词与工具函数。
- `UserManager` 采用简化 `UserProfile`（user_id, name, aliases, identities, metadata），群隔离存储 `{group_id: {user_id: UserProfile}}`。跨平台身份解析由 `IdentityResolver` 解耦。
- SKILL runtime 会先加载包内置技能（`system_info`、`desktop_screenshot`、`learn_term`、`url_content_reader`、`bing_search`），再加载 workspace `skills/`；同名 workspace 文件覆盖内置实现。
- 用户态记忆、事件记忆、自身记忆、session store、token store 都已经收敛到 workspace 语义下。
- v1.0.0 新增存储路径：`memory/basic/`（基础记忆归档）、`memory/diary/`（日记条目与索引）、`memory/glossary/`（名词解释）。

---

## 2. Workspace 启动与配置流

```mermaid
flowchart TD
  A["调用方提供\nwork_path\n可选 config_path\n可选 bootstrap"] --> B["WorkspaceRuntime.open(...)"]
  B --> C["WorkspaceLayout\n解析 data_root / config_root"]
  C --> D["ensure_directories()\n创建 config/providers/roleplay/skills\n与 sessions/memory/token/skill_data"]
  D --> E["_apply_bootstrap()\n合并 active_agent_key\nsession_defaults\norchestration_defaults\nprovider_entries"]
  E --> F["ConfigManager.load_workspace_config()"]
  F --> G["读取 workspace.json"]
  F --> H["读取 config/session_config.json\nJSONC 注释快照"]
  G --> I["合并 workspace 默认值"]
  H --> I
  I --> J["重叠字段按较新的文件生效\n确保人工编辑不会在重启后被回滚"]
  J --> K["WorkspaceProviderManager.load()\n读取 providers/provider_keys.json"]
  J --> L["load_generated_agent_library()\n读取 roleplay/generated_agents.json"]
  K --> M["_build_session_config()"]
  L --> M
  M --> N["得到 SessionConfig\n包含 preset.agent / global_system_prompt / orchestration"]
  N --> O["启动 WorkspaceConfigWatcher\n监听 workspace.json\nsession_config.json\nprovider_keys.json\ngenerated_agents.json"]
```

### 这条链路的职责分工

- `WorkspaceRuntime`：拥有初始化、配置刷新、session 锁、store 生命周期和参与者元数据写回。**v1.0.0 新增**：`create_emotional_engine()` 工厂方法，为新引擎绑定 workspace provider 与 work_path。
- `WorkspaceLayout`：决定所有目录与文件名，不让外部调用方拼接路径。**v1.0.0 新增**：`episodic/`、`semantic/`、`engine_state/` 目录纳入布局。
- `ConfigManager`：负责 workspace 级默认值的读写，以及从 workspace + roleplay 资产构建可运行的 `SessionConfig`。
- `WorkspaceProviderManager`：只管理 provider 注册表，不参与对话编排。
- `roleplay_prompting`：只管理 agent 资产与提示词生成，不直接执行业务会话。

---

## 3. v0.28 Emotional 引擎单轮消息执行流

> Emotional 路径通过 `EmotionalGroupChatEngine.process_message(...)` 处理单轮消息。引擎内部采用四层认知架构，每层职责单一、可独立测试。

```mermaid
flowchart TD
  A["外部消息\nMessage + Participant[] + group_id"] --> B["EmotionalGroupChatEngine.process_message(...)"]

  subgraph Perception["① 感知层"]
    B --> C1["MessageNormalizer：标准化消息格式\n补全 group_id（默认 'default'）"]
    C1 --> C2["IdentityResolver.resolve() → UserManager.register()（群隔离）"]
    C2 --> C3["BasicMemoryManager.add_entry()（按群滑动窗口，硬限制 30，上下文窗口 5）"]
    C3 --> C3a["HeatCalculator.compute_heat()"]
    C3a --> C4["更新 group_last_message_at"]
    C4 --> E1["emit PERCEPTION_COMPLETED"]
  end

  subgraph Cognition["② 认知层（统一 CognitionAnalyzer）"]
    E1 --> D1["CognitionAnalyzer\n联合规则引擎：情绪 + 意图 同时推断\n热路径零成本（~90%），单 LLM fallback（~10%）"]
    E1 --> D2["记忆检索：BasicMemoryManager.get_context() + DiaryManager.retrieve()"]
    D1 --> E2["emit COGNITION_COMPLETED"]
    D2 --> E2
  end

  subgraph Decision["③ 决策层"]
    E2 --> F1["RhythmAnalyzer\nheat_level + pace + topic_stability"]
    F1 --> F2["ThresholdEngine\n动态阈值 = base × activity × relationship × time"]
    F2 --> F3["ResponseStrategyEngine\nIMMEDIATE / DELAYED / SILENT / PROACTIVE"]
    F3 --> F4["更新 AssistantEmotionState"]
    F4 --> E3["emit DECISION_COMPLETED"]
  end

  subgraph Execution["④ 执行层"]
    E3 --> G1{"策略？"}
    G1 -- IMMEDIATE --> G2["ResponseAssembler 返回 PromptBundle\nsystem_prompt（persona + 情绪 + 日记 + glossary + skill + 格式）\nuser_content（当前消息）\nContextAssembler.build_messages() 将 basic + diary 转为标准 messages"]
    G1 -- DELAYED --> G3["入 DelayedResponseQueue\n等待话题间隙或合并触发"]
    G1 -- SILENT --> G4["仅更新内部状态\n不生成回复"]
    G1 -- PROACTIVE --> G5["由 ProactiveTrigger 外部触发\n生成自然开场白"]
    G2 --> G6["StyleAdapter\nmax_tokens / temperature / tone 动态适配"]
    G6 --> G7["ModelRouter 选模型\n构建 GenerationRequest"]
    G7 --> G8["Provider.generate_async()\n或 sync via asyncio.to_thread"]
    G8 --> G8a["parse_dual_output()\n<think> → 内心独白\n<say> → 说出口的话"]
    G8a --> G8b["<think> 存入 DiaryManager\n形成群聊日记；glossary 术语同步更新"]
    G8a --> G9["SKILL 调用解析与执行（支持 silent 模式，如 learn_term）"]
    G9 --> G10["Token 追踪记录"]
    G10 --> E4["emit EXECUTION_COMPLETED"]
  end

  E4 --> H["_background_update()\n更新群体氛围 + 群规范学习 + 情感孤岛检测\n日记生成（冷群检测 → DiaryGenerator）"]
  H --> I["返回结果 dict\n{strategy, reply, emotion, intent, thought}"]
```

### Emotional 路径需要特别注意的语义

- **群隔离是 P0**：所有记忆操作必须携带 `group_id`。`UserManager.entries` 为 `{group_id: {user_id: UserProfile}}` 双层字典。
- **四层认知架构**：感知 → 认知 → 决策 → 执行，每层通过 `SessionEventBus` 发出事件，外部可订阅监控。
- **统一认知分析器**：`CognitionAnalyzer` 联合分析情绪+意图，规则引擎覆盖 ~90% 情况（零 LLM 成本），复杂情况单次 LLM fallback（~10% 命中）。情绪结果自然流入意图紧急度评分，无需额外异步边界。
- **简化记忆模型**：
  - `BasicMemoryManager`：按群滑动窗口（硬限制 30 条，上下文窗口 5 条），含热度计算（HeatCalculator）。当群体变冷（heat < 0.25）且沉默 > 300s 时，上下文窗口外消息归档为日记素材。
  - `DiaryManager`：LLM 生成的群聊摘要，含关键词和 source_ids 回链基础记忆。支持 sentence-transformers 嵌入索引（可选）和关键词回退搜索。检索按 token 预算（默认 800 tokens）截断。
  - `ContextAssembler`：将基础记忆最近 n 条 + 日记检索 top_k 条组装为标准 OpenAI messages 数组。日记内容注入 system_prompt 作为「历史日记」，不污染消息历史。
  - `GlossaryManager`：AI 自身名词解释，替代旧 AutobiographicalMemory。
- **四种响应策略**：
  - `IMMEDIATE`：直接生成回复（高 urgency 或被 @ 时）。
  - `DELAYED`：入延迟队列，等待话题间隙或合并后触发。
  - `SILENT`：不回复，仅后台观察与学习。
  - `PROACTIVE`：由外部触发器（时间/记忆/情感）决定何时发起。
- **后台任务**（`start_background_tasks()` / `stop_background_tasks()`）：
  - 延迟队列 ticker（每 10 秒）
  - 主动触发 checker（每 60 秒）
  - 日记生成 promoter：检查冷群的基础记忆归档，经 `DiaryGenerator` 生成日记并写入 `DiaryManager`
- **状态持久化**：`save_state()` / `load_state()` 持久化 basic memory、assistant emotion、group timestamps、diary index 到 `memory/` 目录。
- **Token 追踪**：`_generate()` 中估算 input/output tokens，记录到 `token_usage_records`，随状态持久化。

---

## 5. 分层视图与模块职责

| 分层 | 关键模块 | 主要职责 |
| --- | --- | --- |
| 入口层 | `main.py`、`sirius_chat/cli.py`、`sirius_chat/api/*` | 接收外部输入、暴露稳定 API、拼接最少的运行参数。**v1.0.0 新增**：`main.py --engine {legacy,emotional}` 切换 |
| Workspace 层 | `workspace/layout.py`、`workspace/runtime.py`、`workspace/config_watcher.py`、`workspace/roleplay_manager.py` | 路径布局、配置热刷新、session 队列与锁、静默批处理、participants 元数据、roleplay 资产与 workspace 默认值联动。**v1.0.0 新增**：`create_emotional_engine()` 工厂方法 |
| 配置构建层 | `config/models.py`、`config/manager.py`、`config/jsonc.py`、`config/helpers.py` | `WorkspaceConfig` / `SessionConfig` / `OrchestrationPolicy` 契约、JSON/JSONC 读写、workspace 默认值与 orchestration 构造 |
| **认知编排层** | `core/emotional_engine.py`、`core/response_assembler.py`、`core/response_strategy.py`、`core/threshold_engine.py`、`core/rhythm.py`、`core/cognition.py` | 四层认知架构（感知→认知→决策→执行）、回复策略、阈值引擎、节奏分析 |
| **v0.28 新编排核心层** | `core/emotional_engine.py`、`core/cognition.py`、`core/response_strategy.py`、`core/threshold_engine.py`、`core/rhythm.py`、`core/response_assembler.py`、`core/delayed_response_queue.py`、`core/proactive_trigger.py`、`core/model_router.py`、`core/engine_persistence.py` | 四层认知架构、统一认知分析、响应策略、动态阈值、对话节奏、共情生成、延迟队列、主动触发、模型路由、状态持久化 |
| 兼容与辅助层 | `async_engine/prompts.py`、`async_engine/orchestration.py`、`async_engine/utils.py`、`async_engine/__init__.py` | 提示词生成、任务常量与配置、辅助工具、向后兼容导出 |
| 记忆层 | `memory/basic/`、`memory/diary/`、`memory/user/`、`memory/glossary/`、`memory/context_assembler.py` | 基础记忆（工作窗口+热度+归档）、日记记忆（LLM生成+检索）、用户管理（简化UserProfile）、名词解释、上下文组装器 |
| Provider 层 | `providers/base.py`、`providers/routing.py`、各 provider 文件、`providers/middleware/` | 统一请求协议、provider 注册表、自动路由、具体上游接入、中间件增强 |
| 会话与统计层 | `session/store.py`、`session/runner.py`、`token/store.py`、`token/usage.py`、`token/analytics.py` | Transcript 持久化、兼容运行器、token 归档、跨会话分析 |
| 扩展层 | `skills/`、`cache/`、`performance/` | SKILL 注册、依赖解析、执行与 data store；缓存框架；性能采样与基准 |

### 真实的 engine 位置

- **v1.0.0 默认引擎**：`EmotionalGroupChatEngine` 的实现位于 `sirius_chat/core/emotional_engine.py`。
- `sirius_chat/core/cognition.py`：统一情绪+意图分析器（`CognitionAnalyzer`）。
- `sirius_chat/core/response_assembler.py`：返回 `PromptBundle`（system_prompt + user_content），负责指令级上下文组装；`<think>` / `<say>` 双输出解析。历史消息由引擎通过 `_build_history_messages()` 独立管理为标准 OpenAI messages。
- `sirius_chat/memory/glossary/`：名词解释（AI 自身知识库）。
- `sirius_chat/memory/basic/`：基础记忆（按群滑动窗口、热度跟踪、归档存储）。
- `sirius_chat/memory/diary/`：日记记忆（LLM 生成摘要、索引、token 预算检索）。
- `sirius_chat/memory/context_assembler.py`：上下文组装器（basic + diary → OpenAI messages）。
- `sirius_chat/core/identity_resolver.py`：跨平台身份解析器。
- **v1.0 引擎**：`EmotionalGroupChatEngine` 位于 `sirius_chat/core/emotional_engine.py`，采用简化记忆模型（基础记忆 + 日记记忆 + 名词解释 + 用户画像）。

---

## 6. 文件所有权与路径语义

`WorkspaceLayout` 把路径分成两类：

- config root：配置资产、provider 注册表、roleplay 资产、skills 代码
- data root：会话状态、记忆数据、token 计量、skill_data

| 路径 | 所属 root | 生产者 | 用途 |
| --- | --- | --- | --- |
| `workspace.json` | config root | `ConfigManager.save_workspace_config()` | 机器可读的 workspace 清单与默认值 |
| `config/session_config.json` | config root | `ConfigManager.save_workspace_config()`、CLI 默认模板 | 人类可编辑的 JSONC 快照 |
| `providers/provider_keys.json` | config root | `WorkspaceProviderManager` | provider 注册表、healthcheck 与模型映射 |
| `roleplay/generated_agents.json` | config root | `roleplay_prompting.py` | 已生成 agent 资产库与选中 agent |
| `roleplay/generated_agent_traces/<agent_key>.json` | config root | `roleplay_prompting.py` | 提示词生成完整轨迹 |
| `skills/` | config root | `SkillRegistry`、runtime 初始化 | SKILL 源文件与 README 引导；同名 workspace 文件可覆盖内置 SKILL |
| `sessions/<session_id>/session_state.db` | data root | `SqliteSessionStore` | 默认结构化会话存储（legacy 路径） |
| `sessions/<session_id>/session_state.json` | data root | `JsonSessionStore` | 可选 JSON store（legacy 路径） |
| `sessions/<session_id>/participants.json` | data root | `WorkspaceRuntime` | 会话参与者与主用户元数据 |
| `memory/basic/<group_id>.jsonl` | data root | `BasicMemoryFileStore` | **v1.0**：基础记忆条目 |
| `memory/diary/<group_id>.jsonl` | data root | `DiaryManager` | **v1.0**：日记条目 |
| `memory/diary/index/<group_id>.json` | data root | `DiaryIndexer` | **v1.0**：日记索引（关键词+可选嵌入） |
| `memory/glossary/terms.json` | data root | `GlossaryManager` | **v1.0**：名词解释 |
| `user_memory/groups/<group_id>/<user_id>.json` | data root | `UserManager` | 群隔离用户档案（简化 UserProfile） |
| `token/token_usage.db` | data root | `TokenUsageStore` | 跨会话 token 使用记录 |
| `skill_data/*.json` | data root | `SkillDataStore` | 每个 SKILL 的独立数据存储 |
| `primary_user.json` | data root | `main.py` | 主用户档案保留文件 |

### `workspace.json` 与 `config/session_config.json` 的关系

- `workspace.json`：偏机器侧、结构稳定、便于 runtime 直接读取。
- `config/session_config.json`：偏人工编辑，带注释，用于暴露完整可配置项。
- 两者存在重叠字段时，当前实现按文件修改时间选择较新的版本作为事实来源。

---

## 7. Provider 路由流

```mermaid
flowchart TD
  A["来源 A\nSession JSON / bootstrap provider_entries"] --> D
  B["来源 B\nproviders/provider_keys.json"] --> D["WorkspaceProviderManager / ProviderRegistry"]
  D --> E["ProviderConfig 列表"]
  E --> F["AutoRoutingProvider"]
  F --> G{"请求模型如何匹配"}
  G -- "命中 ProviderConfig.models" --> H["使用该 provider"]
  G -- "否则命中 healthcheck_model" --> H
  G -- "都未命中" --> I["回退到第一个 enabled provider"]
  H --> J["具体 provider\nOpenAI / BigModel / Bailian / DeepSeek / SiliconFlow / Ark / YTea"]
  I --> J
```

### 当前路由规则

- 优先看 `ProviderConfig.models` 的显式模型列表。
- 其次看 `healthcheck_model` 的精确匹配。
- 都未命中时，回退到第一个启用的 provider。
- 如果 runtime 没有显式注入 provider，且 workspace 注册表中有 provider，`WorkspaceRuntime` 会优先创建 `AutoRoutingProvider`。
- 当 `provider_keys.json` 被 watcher 检测到变化时，runtime 会重建 engine，确保新 provider 配置真正生效。
- **v1.0.0 新增**：`EmotionalGroupChatEngine` 内部通过 `ModelRouter` 按任务类型（`emotion_analyze` / `intent_analyze` / `response_generate` / `memory_extract`）选择模型、温度和 token 上限；urgency ≥ 80 时切换更强模型，urgency ≥ 95 时最大化 token 上限。

---

## 8. Roleplay 资产与 SessionConfig 构建流

```mermaid
flowchart TD
  A["问题模板\ntrait_keywords\nanswers\ndependency_files"] --> B["PersonaSpec"]
  B --> C["先落盘 pending PersonaSpec\n避免生成失败丢失输入"]
  C --> D["调用生成模型\nroleplay_prompting.py"]
  D --> E["解析为 GeneratedSessionPreset"]
  E --> F["写 roleplay/generated_agents.json"]
  E --> G["写 generated_agent_traces/<agent_key>.json"]
  F --> H["select_generated_agent_profile()\n或 RoleplayWorkspaceManager.bootstrap_active_agent()"]
  H --> I["workspace.active_agent_key 更新"]
  I --> J["ConfigManager.build_session_config()\n把 agent 资产 + workspace 默认值组装成 SessionConfig"]
```

### 这一层的边界

- `roleplay_prompting.py` 只负责生成、持久化和选择 agent 资产。
- `WorkspaceRuntime` 不生成人格，只消费已经选中的资产。
- `RoleplayWorkspaceManager` 是“选中 agent + 更新 workspace 默认值”的组合封装。
- **v1.0 说明**：`EmotionalGroupChatEngine` 不直接消费 roleplay 资产；`ResponseAssembler` 负责构建 `PromptBundle`（system_prompt 注入 persona、情绪、共情、日记记忆、glossary、skill 与输出格式；user_content 为当前消息格式化内容）。历史对话通过 `ContextAssembler.build_messages()` 从 `basic_memory` 提取，转为标准 `user`/`assistant` messages 数组传入 LLM。

---

## 9. 关键运行产物

| 产物 | 来源 | 被谁消费 |
| --- | --- | --- |
| `Transcript.messages` | `EmotionalGroupChatEngine` | 对话展示、session store |
| `Transcript.user_memory` | `UserManager` | 提示词注入、识人、participants 写回；`profile.aliases` 为强绑定，`profile.metadata` 存放扩展属性 |
| `Transcript.reply_runtime` | 引擎运行时 | `reply_mode=auto` 节奏控制（legacy 路径） |
| `Transcript.session_summary` | 自动压缩逻辑 | 后续主模型上下文（legacy 路径） |
| `Transcript.token_usage_records` | provider 调用后 | 内存统计与 `TokenUsageStore` 持久化（legacy 路径） |
| `SessionEventBus` 事件流 | `EmotionalGroupChatEngine` | `on_reply` 外部回调 |
| `BasicMemoryManager` 窗口 | `EmotionalGroupChatEngine` | 当前群对话上下文、RhythmAnalyzer 输入、热度计算 |
| `DiaryManager` 条目 | `EmotionalGroupChatEngine._bg_diary_promoter()` | 群聊摘要、长期记忆检索素材 |
| `SemanticMemoryManager` 画像 | `EmotionalGroupChatEngine`（stub） | ThresholdEngine 关系因子、ResponseAssembler 群风格（保留兼容） |
| `EngineStateStore` 快照 | `EmotionalGroupChatEngine.save_state()` / `load_state()` | 引擎重启后状态恢复 |
| `engine.token_usage_records` | `EmotionalGroupChatEngine._generate()` | Token 使用追踪与持久化；`_generate()` 接收 `system_prompt` + 标准 `messages` 列表，不再做字符串分割 |

---

## 10. 文档同步规则

当以下任一条件发生变化时，必须同步检查本文档：

1. 入口层改变：`main.py`、`cli.py`、`api/engine.py` 的推荐调用方式变化。
2. workspace 布局改变：`WorkspaceLayout` 新增、删除或迁移路径。
3. provider 行为改变：路由规则、注册表格式、支持平台变化。
4. engine 主流程改变：辅助任务、参与决策、SKILL 循环、消息压缩逻辑变化。
5. roleplay 资产流改变：`generated_agents.json`、trace、选中 agent 语义变化。
6. **v1.0.0 新增**：`emotional_engine.py` 与 `cognition.py` 统一认知架构的行为或数据流变化。
7. **v1.0.0 新增**：记忆存储布局变化（群隔离、新增 `episodic/` / `semantic/` / `engine_state/` 路径）。

推荐同步顺序：

1. 先更新 `docs/full-architecture-flow.md`。
2. 再同步 [docs/architecture.md](docs/architecture.md)。
3. 若外部用法变化，再同步 [docs/external-usage.md](docs/external-usage.md) 和 [README.md](README.md)。
4. 最后同步 `.github/skills/` 下的相关 SKILL。

---

> **文档版本**：v1.0.0  
> **最后更新**：2026-04-22  
> **对应代码分支**：`master`
