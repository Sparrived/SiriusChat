---
name: external-integration
description: "当需要让外部项目正确接入 Sirius Chat 时使用，覆盖 Python API 调用、CLI 调用、配置组织和安全实践。关键词：外部接入、库调用、CLI 集成、provider 配置。"
---

# 外部接入指南

## 目标

帮助 AI 在不破坏框架边界的前提下，为外部系统提供正确、可维护的 Sirius Chat 集成方案。

项目方向：集成时应支持“问题帮助 + 情绪价值”双目标，保障用户上下文与情感线索连续。

## 语言规范（强制）

- 本 SKILL 及所有后续新增/修改的 SKILL 必须使用中文。
- `description` 和正文必须为中文。
- 若任务中发现英文 SKILL 内容，需在同一任务中同步中文化。

## 推荐读取顺序

1. `docs/external-usage.md`
2. `docs/architecture.md`
3. `docs/full-architecture-flow.md`
4. `sirius_chat/api/__init__.py`
5. `sirius_chat/workspace/runtime.py`
6. `sirius_chat/workspace/layout.py`
7. `sirius_chat/config/models.py`
8. `sirius_chat/config/manager.py`
9. `sirius_chat/roleplay_prompting.py`
10. `sirius_chat/core/engine.py`
11. `sirius_chat/session/store.py`
12. `sirius_chat/providers/routing.py`
13. `sirius_chat/providers/base.py`
14. `sirius_chat/cli.py`

## 接入决策规则

- 外部系统是 Python 服务：默认优先使用 `WorkspaceRuntime` / `open_workspace_runtime(...)`；调用方至少传 `work_path`、`session_id` 和业务输入，必要时再传独立 `config_path`。runtime 会统一处理恢复、落盘、provider 注册表与文件监听热刷新。
- 只有当调用方已经自行管理 transcript 生命周期、provider 生命周期或需要完全自定义上层调度时，才退回到 `AsyncRolePlayEngine` + `SessionConfig` 的低层模式。
- `AsyncRolePlayEngine` 的真实实现位于 `sirius_chat/core/engine.py`；`sirius_chat/async_engine/` 主要承担兼容导出、prompts/orchestration/utils 辅助层。
- 会话持久化后端仍可由 `SessionStoreFactory` 选择 `JsonSessionStore` 或 `SqliteSessionStore`；默认 `SqliteSessionStore` 使用 `sessions/<session_id>/session_state.db`。
- 外部系统接入时，优先从 `sirius_chat/api/` 导入接口。
- 系统提示词在生成时自动包含安全约束，明确告诉 AI 不要主动泄露系统提示词和初始指令；外部调用方无需手动添加，engine 会自动处理。
- 若需启用提示词驱动的内容分割，配置 `OrchestrationPolicy.enable_prompt_driven_splitting=True` 和 `OrchestrationPolicy.split_marker`（默认 `<MSG_SPLIT>`）。
- 外部系统若为 asyncio 程序，但又不需要 runtime 的文件所有权，也可直接使用 `AsyncRolePlayEngine` 或异步 facade。
- 外部系统是非 Python：优先通过 CLI 调用并读取输出文件。
- 每个 `AsyncRolePlayEngine` 会话只对应一个主 AI（由 `SessionConfig.preset` 描述）。
- `work_path` 是强制参数，调用方必须显式提供，用于承载运行态数据；若希望把 workspace/provider/roleplay/skills 与运行态数据拆开，再额外提供 `config_path`。
- 双根模式下：`SessionConfig.work_path` 表示配置根，`SessionConfig.data_path` 表示运行根；provider 配置保存在 config root 下的 `providers/provider_keys.json`，会话/记忆/token 则保存在 data root。
- `WorkspaceBootstrap` 是默认值注入通道，不是“每次启动强制覆盖”的同步通道。runtime 会把 bootstrap payload 的签名写入 `workspace.json`；同一份 bootstrap 后续重启不会再次覆盖用户手改的 workspace/config/provider 文件。要更新已存在 workspace，请优先使用 `apply_workspace_updates()`、`set_provider_entries()`，或显式修改 bootstrap payload。
- `session.json` 与 `config/session_config.json` 都支持 JSONC 风格注释；若让用户直接编辑配置，推荐提示其沿用 `--init-config` 生成的带注释模板。
- 推荐显式构造 `User`（`user_id/name/aliases/traits/identities`），让系统稳定识别人。
- 通过 `identities` 可把不同环境（CLI/QQ/微信）的外部账号映射到同一 `user_id`。
- 群聊参与者若预先未知，优先直接调用 `WorkspaceRuntime.run_live_message(...)` 逐条传入动态消息；仅在需要手动掌控 transcript 生命周期时再使用 `run_live_session + run_live_message`。
- `Message.reply_mode` 支持按消息控制回复策略：`always`（默认）/`never`（仅记忆摄取）/`auto`（自动判定是否回复）。
- 推荐实时接入：`WorkspaceRuntime.run_live_message(...)` 每次传入一条外部消息，由 runtime 自动初始化、恢复和保存；低层模式才显式 `run_live_session(...)` 初始化一次。
- `run_live_message` 默认采用会话级 `session_reply_mode`（`always`/`never`/`auto`），外部系统可不再逐条设置 `reply_mode`。
- `run_live_message` / `arun_live_message` 新增 `environment_context: str = ""` 参数（v0.8.0），可注入群名、在线人数等上下文。
- **会话事件流** (v0.9.0)：`on_message` 回调已移除，改用 `engine.subscribe(transcript)` 实时事件流。外部系统应在 `run_live_session` 后启动后台任务订阅 `SessionEvent`，实时接收 AI 回复、SKILL 状态等事件。详见 `docs/migration-event-stream.md`。
- ✨ **(v0.12.0) 简化事件流接入**：`arun_live_message` 新增 `on_reply` 回调参数——引擎内部管理订阅与清理，外部只需传入回调即可接收 assistant 回复。同时新增 `user_profile`（消息处理前自动注册用户）和 `timeout`（引擎管理超时与清理）参数。原始 `asubscribe` API 仍可用于高级场景。详见 `docs/migration-v0.12.md`。
- `reply_mode=auto` 的参与决策参数可在 `OrchestrationPolicy` 调整：`engagement_sensitivity`（0–1，默认 0.5，越高越主动）和 `heat_window_seconds`（默认 60，热度统计时间窗口）。
- 若外部采用单条消息多次调用 `run_live_session`，建议复用同一个 `Transcript`，以保留 `reply_runtime` 节奏状态。
- 通过 `transcript.user_memory` 维护用户画像与近期发言，实现识人。
- 用户记忆分层：`profile`（初始化档案）+ `runtime`（运行时可变记忆）。
- ✨ V2：`MemoryPolicy`（`OrchestrationPolicy.memory`）集中配置记忆系统：阈值、衰退曲线、集合上限等。详见 `docs/migration-memory-v2.md`。
- ✨ **(v0.13.0)** AI 自身记忆系统（`enable_self_memory=True`）：日记（遗忘曲线）+ 名词解释（自动合并），以 `<self_diary>` / `<glossary>` 注入提示词。持久化至 `{work_path}/self_memory.json`。
- ✨ **(v0.15.0)** 自身记忆触发改回主流程内联：通过 `self_memory_extract_batch_size` 和 `self_memory_min_chars` 控制，不再支持 `self_memory_extract_interval_seconds`。
- ✨ **(v0.14.0)** 回复频率限制已集成到 `EngagementCoordinator.check_reply_frequency_limit()`，参数从 `OrchestrationPolicy` 继承（`reply_frequency_window_seconds`、`reply_frequency_max_replies`、`reply_frequency_exempt_on_mention`）。
- ✨ **参与决策系统** (v0.14.0)：三级架构替代旧意愿分系统：HeatAnalyzer（零 LLM 开销热度分析）→ IntentAnalyzer v2（意图分类 + target 识别）→ EngagementCoordinator（融合决策）。LLM 意图分析现由 `intent_analysis` 任务驱动，可通过 `task_enabled/task_models/task_budgets/task_temperatures/task_max_tokens/task_retries` 精细控制；任务关闭或失败时默认使用零开销关键词回退路径。
- 兼容提醒：旧配置里的 `enable_intent_analysis` / `intent_analysis_model` 仍可读取，但 `ConfigManager` 会在加载时自动映射到 `task_enabled["intent_analysis"]` / `task_models["intent_analysis"]`，新的模板与持久化输出不再写回旧字段。
- ✨ **后台记忆归纳** (v0.10.0)：引擎自动启动后台循环，定时使用 LLM 归纳合并冗余事件/摘要/事实。通过 `OrchestrationPolicy.consolidation_enabled`（默认 True）控制，`consolidation_interval_seconds` 设置间隔。
- 引擎运行时应主动更新 `runtime`（偏好标签、情绪线索、摘要），以提升拟人化体验。
- 需要按渠道身份直查时，使用 `transcript.find_user_by_channel_uid(channel, uid)`。
- workspace runtime 会自动持久化 transcript，实现重启后恢复会话；若工作目录里仍有旧 `session_state.json` 或早期 `session_state(payload)` 数据，`SqliteSessionStore` 会自动迁移到 `sessions/<session_id>/session_state.db`。
- 通过 `Transcript.token_usage_records` 获取全量 token 调用归档。
- 通过 `summarize_token_usage` 和 `build_token_usage_baseline`（来自 `token/usage.py`）输出成本与损耗基准分析。
- ✨ **(v0.11.0)** 引擎自动将 token 记录持久化至 `{work_path}/token_usage.db`（SQLite）。使用 `TokenUsageStore` + `sirius_chat.token.analytics` 进行跨会话分析（`compute_baseline`、`group_by_actor/task/model/session`、`time_series`、`full_report`）。
- 通过 `list_roleplay_question_templates()` 获取问卷模板名，再用 `generate_humanized_roleplay_questions(template=...)` 自动生成拟人化问题清单；当前支持 `default`、`companion`、`romance`、`group_chat` 四类模板。
- 若外部系统暂时只想通过命令行拿模板数据，可直接使用 `sirius-chat --list-roleplay-question-templates` 与 `sirius-chat --print-roleplay-questions-template <template>`。
- 通过 `agenerate_agent_prompts_from_answers`、`agenerate_from_persona_spec`（支持 `trait_keywords`、`answers`、`dependency_files`）或 `abuild_roleplay_prompt_from_answers_and_apply` 生成并应用完整 `GeneratedSessionPreset`。
- 外部调用方推荐传入高层人格 brief，而不是完整系统提示词：优先收集人物原型、核心矛盾、关系策略、情绪原则、表达节奏、边界和小缺点，再交给生成人格 API 落成具体人物小传与语言习惯。
- 对 `abuild_roleplay_prompt_from_answers_and_apply(...)`、`aupdate_agent_prompt(...)`、`aregenerate_agent_prompt_from_dependencies(...)` 这三条持久化链路，框架会先把最新 `PersonaSpec` 和待生成快照写入 `work_path`，再发起模型调用；若生成失败，可用 `load_persona_spec(work_path, agent_key)` 恢复最近一次输入。
- 结构化人格生成默认使用 `max_tokens=5120` 和 `timeout_seconds=120.0`，并把 `timeout_seconds` 透传到 `GenerationRequest`；各同步 provider 会优先使用请求级 timeout，而不是只使用 provider 构造器上的默认 30 秒。
- 若模型返回被 ```json 包裹但未完整闭合的 JSON-like 响应，框架会显式报错并把原始响应保留在 `roleplay/generated_agent_traces/<agent_key>.json`，避免脏数据覆盖现有人格配置。
- 当外部素材文件（角色卡、语气样本、设定稿）变化时，可使用 `aregenerate_agent_prompt_from_dependencies(...)` 重新读取 `dependency_files` 并重生人格，无需重新收集问答。
- 推荐采用 agent-first：先生成并持久化 agent 资产（`roleplay/generated_agents.json`），再用 `select_generated_agent_profile(work_path, agent_key)` 选择，最后通过 `WorkspaceRuntime` 或 `create_session_config_from_selected_agent(...)` 创建会话。
- 每次生成的完整过程都会本地化到 `{work_path}/roleplay/generated_agent_traces/<agent_key>.json`；外部若需审计/回放，可调用 `load_persona_generation_traces(...)`。
- ✨ **动态模型路由**：当需要在有图像时自动升级模型时，通过 `Agent.metadata["multimodal_model"]` 配置多模态专用模型
  - 推荐使用 `create_agent_with_multimodal(name, persona, model="gpt-4o-mini", multimodal_model="gpt-4o", ...)` 便捷构造函数
  - 或使用 `auto_configure_multimodal_agent(agent, multimodal_model="gpt-4o")` 灵活配置既有 Agent
  - 引擎自动检测输入中的多媒体数据，无多媒体时使用廉价模型，有多媒体时自动升级至指定的多模态模型
  - 完全透明，无需调用方手动干预
- 通过 `history_max_messages/history_max_chars` 启用自动记忆压缩，控制 token 增长。
- ✨ **配置管理** (P1-006)：使用 `ConfigManager` 处理多环境配置
  - 支持多环境配置文件（base.json/dev.json/test.json/prod.json）
  - 支持 ${VAR_NAME} 环境变量替换语法
  - 可选验证配置的有效性
  - 示例：`from sirius_chat.config import ConfigManager; cfg = ConfigManager.load_from_json('config/base.json')`
- ✨ **缓存层** (P2-001)：使用 `cache/` 模块实现高效的响应缓存
  - MemoryCache：本地内存缓存，支持 LRU 策略和 TTL 过期
  - 通过 `CacheBackend` 抽象实现自定义后端
  - 使用 `generate_cache_key()` 生成确定性的 key（支持温度感知）
  - 示例：`from sirius_chat.cache import MemoryCache; cache = MemoryCache(max_size=1000, ttl=3600)`
- ✨ **性能监控** (P2-002)：通过 `performance/` 模块追踪和优化应用性能
  - ExecutionMetrics：记录单次执行的时间和内存消耗
  - MetricsCollector：聚合执行指标，提供统计分析
  - PerformanceProfiler：上下文管理器用于代码块分析
  - @profile_sync/@profile_async：装饰器用于函数级性能追踪
  - Benchmark：支持同步/异步/并发性能基准测试
  - 示例：`from sirius_chat.performance import PerformanceProfiler; with PerformanceProfiler("task"): ...`
- ✨ **SKILL 系统**：通过 `skills/` 模块让 AI 在运行时调用外部 Python 代码
  - 默认：`enable_skills=True`；SKILL 文件放在 `{work_path}/skills/` 目录下，框架会自动创建该目录和 `README.md` 引导文档。若只想保留目录结构、不执行 SKILL，可显式设置 `enable_skills=False`
  - SKILL 文件需导出 `SKILL_META` 字典（含 name, description, parameters, 可选 dependencies）和 `run(**kwargs)` 函数
  - 依赖自动安装：加载 SKILL 前自动扫描 `SKILL_META["dependencies"]` 和 import 语句，用 `uv pip install`（回退 `pip`）安装缺失包。可通过 `auto_install_skill_deps=False` 关闭
  - 持久化：每个 SKILL 自动获得独立的 JSON 键值存储（`SkillDataStore`），通过 `data_store` 参数注入
  - 超时：`skill_execution_timeout`（默认 30 秒），超时返回 `SkillResult(success=False)`
  - 引擎自动检测 AI 回复中的 `[SKILL_CALL: name | {params}]` 标记并执行，结果注入上下文后重新生成
  - 导入：`from sirius_chat import SkillRegistry, SkillExecutor, SkillDataStore, resolve_skill_dependencies`
  - 示例 SKILL：`examples/skills/system_info.py`
- 任何情况下，不应在编排核心中写入 provider 细节（provider 抽象优先原则）。
  - 所有 provider 特定逻辑都应在 `sirius_chat/providers/` 目录下实现。
  - `sirius_chat/core/engine.py` 通过 `LLMProvider`/`AsyncLLMProvider` 抽象与 provider 交互，不依赖任何具体实现。
- 内部重构若影响外部接口（当前未发布阶段），可直接升级 `api/`，并同步外部文档与示例。
- 内部新增功能必须同步在 `api/` 暴露可调用接口。
- 异步引擎在同步 provider 场景下会自动线程化调用，避免阻塞事件循环。

## 最小可用接入模板

- Python 调用示例：`examples/external_api_usage.py`
- 动态群聊示例：`examples/dynamic_group_chat_usage.py`
- CLI 调用示例：`sirius-chat --config examples/session.json --work-path data/session_runtime --output transcript.json`
- 恢复会话示例（默认自动恢复）：`sirius-chat --config examples/session.json --work-path data/session_runtime`
- 如需禁用自动恢复，可在 `main.py` 入口使用 `--no-resume`。

## 变更同步要求（强制）

当以下内容发生变化时，必须同步更新本 SKILL：

1. 外部接入方式（API 或 CLI）
2. 配置结构或关键参数
3. provider 接入策略或边界约束

并同步更新：

- `README.md`（用户可见用法）
- `docs/external-usage.md`
- `docs/architecture.md`（若边界变化）

## Provider 选型补充

- OpenAI 兼容上游：使用 `OpenAICompatibleProvider`。
- 阿里云百炼上游：优先使用 `AliyunBailianProvider`（默认 `https://dashscope.aliyuncs.com/compatible-mode`，兼容传入 `/compatible-mode/v1` 后缀）。
- 智谱 BigModel 上游：优先使用 `BigModelProvider`（默认 `https://open.bigmodel.cn/api/paas/v4`，接口 `POST /chat/completions`，兼容传入根域名或完整 `api/paas/v4` 前缀）。
- DeepSeek 上游：优先使用 `DeepSeekProvider`（默认 `https://api.deepseek.com`，兼容传入 `/v1` 后缀，接口 `POST /chat/completions`）。
- SiliconFlow 上游：优先使用 `SiliconFlowProvider`（默认 `https://api.siliconflow.cn`，兼容传入 `/v1` 后缀）。
- 火山方舟上游：优先使用 `VolcengineArkProvider`（默认 `https://ark.cn-beijing.volces.com/api/v3`，接口 `/api/v3/chat/completions`）。
- 多平台自动选择：使用 `AutoRoutingProvider` + `ProviderRegistry`，通过模型前缀路由。
- 交互模式下可用 `/provider platforms|add|remove|list` 管理 API Key（持久化在 config root 下的 `providers/provider_keys.json`）。
- `/provider add` 需提供 `healthcheck_model`，注册时会执行可用性检测：
  `/provider add <type> <api_key> <healthcheck_model> [base_url]`
- 框架会执行统一 Provider 检测流程：配置检查（平台名/API）-> 平台适配检查（仅允许已适配平台）-> 可用性检查（healthcheck model）。
- **多模型协同**：`OrchestrationPolicy` 通过 `unified_model` 或 `task_models` 工作；若未显式传入 orchestration，`SessionConfig` 会默认用主 agent 模型构造 `unified_model`。图片输入不会触发独立解析任务，而是直接进入主模型；如需自动升级多模态模型，请配置 `Agent.metadata["multimodal_model"]`。
- 多模态接入补充：对 `OpenAICompatibleProvider` / `AliyunBailianProvider` 这类 HTTP provider，`multimodal_inputs` 中的本地图片路径或 `file://` URI 会在发送前自动转换为 Data URL；若传公网 URL，需确保上游能直接下载且响应头带 `Content-Type` / `Content-Length`。
- 生产环境建议配置 `task_retries` 与多模态限流参数，避免上游抖动与超长输入导致的失败。
- ✨ **Provider 中间件** (P1-003)：支持在 provider 调用前后插入可组合的中间件，功能包括：
  - 速率限制（固定窗口、令牌桶）
  - 自动重试（指数退避）与断路器保护
  - 成本计量和使用统计
  - 通过 `from sirius_chat import MiddlewareChain, RateLimiterMiddleware, RetryMiddleware, CircuitBreakerMiddleware, CostMetricsMiddleware` 导入
  - 支持自定义中间件扩展（继承 Middleware ABC）


