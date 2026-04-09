# Sirius Chat 架构说明

## 目标

Sirius Chat 是一个面向“多人用户与单 AI 主助手”交互的核心框架，可用于：

- CLI 脚本调用
- Python 应用内嵌调用
- 需要 transcript 输出的外部编排器

项目愿景：打造具备真实情感表达与用户陪伴能力的核心引擎，在提供问题解决能力的同时提供情绪价值。

## 设计原则

- Provider 抽象优先：engine 逻辑不依赖单一 LLM 厂商。
- 编排可复现：人类参与者按轮次顺序发言，由同一个主 AI 统一回应。
- 契约显式化：通过 dataclass 定义输入配置与输出 transcript。
- 传输层可扩展：provider 实现可自由选择 HTTP 技术栈。
- 用户状态连续性：引擎需在运行时主动维护用户偏好、情绪线索与最近语境。

## 模块边界

- `sirius_chat/providers/openai_compatible.py`
  - 面向 `/v1/chat/completions` 风格 API 的具体实现。
- `sirius_chat/providers/siliconflow.py`
  - SiliconFlow 专用适配（仍走 OpenAI 兼容协议），默认基地址 `https://api.siliconflow.cn`。
- `sirius_chat/providers/deepseek.py`
  - DeepSeek 专用适配，默认基地址 `https://api.deepseek.com`，接口 `POST /chat/completions`（OpenAI 兼容消息格式）。
- `sirius_chat/providers/volcengine_ark.py`
  - 火山方舟专用适配，默认基地址 `https://ark.cn-beijing.volces.com/api/v3`。
- `sirius_chat/providers/routing.py`
  - provider key 注册表（`provider_keys.json`）、支持平台清单、自动路由 provider（优先按 `ProviderConfig.models` 显式模型列表匹配，其次按 `healthcheck_model` 精确匹配，最后使用启发式前缀规则）与框架级 Provider 检测流程。
- `sirius_chat/providers/mock.py`
  - 测试与本地演练使用的确定性 provider。
- `sirius_chat/providers/middleware/` ✨ **(P1-003)**
  - Provider 功能扩展层：在 provider 调用前后插入可组合的中间件。
  - `base.py`：Middleware ABC 与 MiddlewareChain 管理器，支持链式组合。
  - `rate_limiter.py`：RateLimiterMiddleware（固定窗口）与 TokenBucketRateLimiter（令牌桶）。
  - `retry.py`：RetryMiddleware（指数退避）与 CircuitBreakerMiddleware（断路器保护）。
  - `cost_metrics.py`：CostMetricsMiddleware（成本计量与使用统计）。
  - 透明地为任意 provider 添加流控、自动重试、故障转移、监控等功能。
- `sirius_chat/session/runner.py` ✨ **(包重构)**
  - 上层封装：自动维护主用户档案与会话持久化，降低调用方心智负担。
  - token 消耗分析：提供会话级 baseline 与按 actor/task/model 聚合函数。
- `sirius_chat/session/store.py` ✨ **(包重构)**
  - 会话持久化协议与实现（`SessionStore`、`JsonSessionStore`、`SqliteSessionStore`）。
- `sirius_chat/token/usage.py` ✨ **(包重构)**
  - Token 消耗统计与汇总函数（`TokenUsageBucket`、`TokenUsageBaseline`、`summarize_token_usage`）。
- `sirius_chat/token/store.py` ✨ **(v0.11.0 新增)**
  - SQLite 持久化存储后端（`TokenUsageStore`），每次模型调用自动写入 `{work_path}/token_usage.db`。
  - 支持跨会话查询与多维度筛选（session / actor / task / model）。
- `sirius_chat/token/analytics.py` ✨ **(v0.11.0 新增)**
  - 基于 SQLite 的多维度分析函数：`compute_baseline`、`group_by_session`、`group_by_actor`、`group_by_task`、`group_by_model`、`time_series`、`full_report`。
- `sirius_chat/token/utils.py` ✨ **(包重构)**
  - Token 估算工具模块（启发式估算、Tiktoken 精确计算、统计辅助函数）。
- `sirius_chat/models/models.py` ✨ **(包重构)**
  - 核心数据模型（`Message`、`Participant`、`User`、`Transcript`）。
- `sirius_chat/async_engine/` ✨ **(P0-003 重构)**
  - 核心异步编排引擎包，支持多人交互、记忆管理、辅助任务编排。
  - `core.py` (500+ 行)：AsyncRolePlayEngine 类（主编排引擎，保持公开 API 不变）
    * 公开方法：`run_session()` 会话准备、`run_live_session()` 实时会话初始化、`run_live_message()` 单条消息处理、`subscribe()` 会话事件订阅
    * 私有协调方法：生命周期管理、token 追踪
  - `utils.py` (120+ 行)：工具函数模块，独立可测试
    * `build_event_hit_system_note()`：事件记忆命中渲染
    * `record_task_stat()`：任务统计记录
    * `estimate_tokens()`：Token 计数（启发式 + tiktoken 可选）
    * `extract_json_payload()`：JSON 有效载荷提取（容错处理）
    * `normalize_multimodal_inputs()`：多模态输入校验和规范化
  - `prompts.py`：系统提示构建
    * `build_system_prompt()`：整合 agent 身份、时间、用户记忆、环境上下文、编排指令的完整提示词
    * 支持 `environment_context` 外部注入（v0.8.0），渲染为 `<environment_context>` 段
    * 支持按类别分组记忆、置信度标记（`?`=低/`~`=中）、冲突提示
    * 输出约束与安全约束合并为 `<constraints>` 段
    * 支持提示词驱动的消息分割（`OrchestrationPolicy.enable_prompt_driven_splitting`）
  - `orchestration.py` (90+ 行)：任务编排配置
    * 任务常量（TASK_MEMORY_EXTRACT 等）与系统提示模板
    * TaskConfig dataclass：集中任务配置管理
    * `get_task_config()`：从 SessionConfig 提取任务配置
    * `get_system_prompt_for_task()`：获取任务系统提示
  - `__init__.py`：包导出（向后兼容 + 新 API）
- `sirius_chat/core/events.py` ✨ **(v0.9.0 会话事件流)**
  - Session 级别事件总线（`SessionEventBus`），支持多订阅者 pub/sub 模式。
  - `SessionEvent` dataclass：事件载体（类型、消息、元数据、时间戳）。
  - `SessionEventType` 枚举：`MESSAGE_ADDED`、`PROCESSING_STARTED`、`PROCESSING_COMPLETED`、`SKILL_STARTED`、`SKILL_COMPLETED`、`REPLY_SKIPPED`、`ERROR`。
  - 外部消费者通过 `engine.subscribe(transcript)` 或 `asubscribe()` facade 实时接收会话事件，替代已移除的 `on_message` 回调。
  - ✨ **(v0.12.0)** `arun_live_message` 新增 `on_reply` 回调参数：引擎内部管理事件流订阅与清理，外部只需提供回调函数即可接收 assistant 回复，无需手动 `asubscribe` 样板代码。同时新增 `user_profile`（自动注册用户）和 `timeout`（引擎管理超时与清理）参数。
  - 迁移指南：`docs/migration-event-stream.md`、`docs/migration-v0.12.md`。
- `sirius_chat/user_memory.py`
  - 用户识别与记忆管理（`UserProfile`、`UserRuntimeState`、`UserMemoryManager`、别名索引与跨环境 identity 索引）。
  - 事件记忆管理（`EventMemoryManager`）：
    * 两级事件验证：快速路径（关键词匹配、相似度算法）+ LLM 验证路径。
    * 新事件默认为 pending（`verified=False`）且积累 mention_count。
    * 当 mention_count >= 阈值（默认3）时，调用 `finalize_pending_events()` 用 LLM 判断是否值得记录、充实字段（summary、keywords、entities等）。
    * 支持 `top_events(include_pending=False)` 查询：默认仅返回已验证事件，避免无意义的寒暄内容被记录。
  - 统一对外接口层（外部程序调用入口与函数式 facade）。
- `sirius_chat/memory/self/` ✨ **(v0.13.0 新增)**
  - AI 自身记忆系统，与用户记忆独立。
  - `models.py`：`DiaryEntry`（日记条目）、`GlossaryTerm`（名词解释）、`SelfMemoryState`（聚合状态）。
  - `manager.py`：`SelfMemoryManager`，负责日记衰退、名词 CRUD、提示词段生成。
  - `store.py`：`SelfMemoryFileStore`，JSON 文件持久化（`self_memory.json`）。
- `sirius_chat/config_manager.py` ✨ **(P1-006)**
  - 多环境配置管理：支持加载 JSON 配置文件（base/dev/test/prod）。
  - 环境变量替换：支持 `${VAR_NAME}` 占位符语法进行环境变量注入。
  - 配置验证：提供配置有效性检查能力。
  - 使用示例：`ConfigManager.load_from_json('config/base.json')`。
- `sirius_chat/cache/` ✨ **(P2-001)**
  - 可扩展缓存框架，提供多种后端实现。
  - `base.py`：`CacheBackend` 抽象基类，定义标准接口（get/set/delete/clear）。
  - `memory.py`：`MemoryCache` 内存实现，支持 LRU 策略和 TTL 过期机制。
  - `keygen.py`：确定性缓存 key 生成函数，支持温度感知的 key 变体。
  - 用途：缓存 LLM 响应、中间结果、用户档案等，提升性能与降低成本。
- `sirius_chat/performance/` ✨ **(P2-002)**
  - 性能监控与分析工具集。
  - `metrics.py`：`ExecutionMetrics` 与 `MetricsCollector`，用于收集和聚合执行指标。
  - `profiler.py`：`PerformanceProfiler` 上下文管理器与 `@profile_sync/@profile_async` 装饰器。
  - `benchmarks.py`：`Benchmark` 与 `BenchmarkSuite` 类，支持同步/异步/并发性能基准测试。
  - 用途：追踪代码执行时间和内存消耗，进行性能基准测试与优化。
- `sirius_chat/skills/` ✨ **(SKILL系统)**
  - AI 可调用的外部代码扩展系统，通过提示词驱动的 `[SKILL_CALL: name | {params}]` 机制在运行时调用外部 Python 函数。
  - `models.py`：`SkillDefinition`、`SkillParameter`、`SkillResult` 数据模型。
  - `registry.py`：`SkillRegistry`，从 `{work_path}/skills/` 目录自动发现并加载 Python SKILL 文件（需导出 `SKILL_META` 字典和 `run()` 函数）。目录会在运行时自动创建 `skills/` 与 `README.md` 引导文档；即使 `enable_skills=False`，引擎也会先完成目录初始化；加载前自动调用依赖解析器安装缺失包（受 `auto_install_skill_deps` 控制）。
  - `executor.py`：`SkillExecutor`，参数校验、类型转换和安全执行；`parse_skill_calls()` / `strip_skill_calls()` 解析与清理响应中的调用标记。支持 `timeout` 参数（由 `OrchestrationPolicy.skill_execution_timeout` 驱动，默认 30 秒），超时返回失败 `SkillResult`。
  - `dependency_resolver.py`：`resolve_skill_dependencies()`，在 SKILL 加载前通过 AST 扫描 `SKILL_META["dependencies"]` 和顶层 import 语句，检测缺失的第三方包并使用 `uv pip install`（回退 `pip`）自动安装。
  - `data_store.py`：`SkillDataStore`，每个 SKILL 独立的 JSON 持久化键值存储，路径为 `{work_path}/skill_data/{skill_name}.json`。
  - 默认行为：`OrchestrationPolicy.enable_skills=True`；如需关闭，显式设置 `enable_skills=False`。
  - 配置选项：`skill_execution_timeout`（秒）、`auto_install_skill_deps`（布尔，默认 True）。
  - 引擎在 `_generate_assistant_message()` 中检测 AI 响应里的 `[SKILL_CALL: ...]` 标记，执行对应 SKILL 后将结果注入上下文并重新生成回复，最多循环 `max_skill_rounds`（默认3）次。
  - 持久化数据通过 `data_store` 参数自动注入到 SKILL 的 `run()` 函数中。
- `main.py`
  - 仓库级测试/业务入口（用于验证与演练 sirius_chat 库能力）。
  - 承载主用户档案、provider 管理与持续会话流程。
- `sirius_chat/cli.py`
  - 库内薄封装 CLI，仅负责调用 `api` 执行单轮会话。
- `sirius_chat/core/intent.py` ✨ **(意图分析)**
  - `IntentAnalyzer`：LLM-based 或关键词回退的用户意图分析。
  - `IntentAnalysis`：分析结果数据类，包含 `intent_type`（question/request/chat/reaction/information_share/command）、`willingness_modifier`（[-0.2, +0.3] 意愿修正值）、`skip_sections`（可跳过的系统提示词段落列表）。
  - LLM 路径：向模型发送简洁 JSON schema 提示，解析意图类型、是否指向 AI、重要性、是否需要记忆/摘要上下文。
  - 关键词回退路径：基于问号、请求关键词、短反应语等模式匹配，零 LLM 开销。
  - 通过 `OrchestrationPolicy.enable_intent_analysis` 控制是否启用 LLM 路径（默认关闭）。
  - 仅当 LLM 路径启用时才使用 `willingness_modifier` 修改回复意愿分数；关键词回退路径仅用于 `skip_sections` 上下文优化。
- `sirius_chat/background_tasks.py`
  - 轻量级 asyncio 后台任务管理器，支持记忆压缩、临时数据清理、记忆归纳三类定时循环。
  - `BackgroundTaskConfig`：配置数据类，控制各循环的启用状态、间隔、触发阈值。
  - `BackgroundTaskManager`：通过 `asyncio.create_task` 启动后台循环，支持回调注入与即时触发。
  - 记忆归纳循环：定时调用异步回调，执行事件归纳（`EventMemoryManager.consolidate_entries`）、摘要归纳（`UserMemoryManager.consolidate_summary_notes`）和事实归纳（`UserMemoryManager.consolidate_memory_facts`）。
- 当前未发布阶段，若内部变更影响外部调用，可直接升级 `api/` 并同步文档与示例。
- 任何新增可用能力，必须同步在 `api/` 暴露对外接口。
- 异步场景优先使用 `AsyncRolePlayEngine` 或 `api/` 的异步 facade。
## 执行流程

1. 从 JSON 或应用代码加载会话配置。

1. 执行 Provider 检测流程：配置检查（平台名/API）-> 平台适配检查 -> 可用性检查（`healthcheck_model`）。
1. 用 endpoint 与凭据初始化 provider。
1. 从 `SessionConfig.preset` 读取唯一主 AI 的完整预设（`agent + global_system_prompt`）。

1. 调用 `AsyncRolePlayEngine.run_live_session` 初始化会话。

可选前置步骤：

- 调用 `generate_humanized_roleplay_questions` 生成问题清单；
- 收集回答后调用 `agenerate_agent_prompts_from_answers`（Q&A 路径）或直接用 `agenerate_from_persona_spec`（支持 tag-only / 混合路径）构建 `GeneratedSessionPreset`；
- 或直接用 `abuild_roleplay_prompt_from_answers_and_apply`（支持 `answers`、`trait_keywords`、`persona_spec` 三种输入模式）一步写入 `SessionConfig`。
- 若需增量微调，可调用 `aupdate_agent_prompt(work_path, agent_key, ...)` 加载已持久化的 `PersonaSpec`、合并补丁后仅重新生成变化部分。
- 若采用 agent-first 流程，可通过 `select_generated_agent_profile(work_path, agent_key)` 选择已生成资产，再调用 `create_session_config_from_selected_agent(...)` 创建会话配置。

1. 对每条群聊 user 消息，调用 `AsyncRolePlayEngine.run_live_message`：追加 user 发言、调用同一主 AI、追加 assistant 回复。

**Agent 动态模型路由**：
- 引擎支持自动根据输入内容在不同模型间切换，以平衡成本与能力。
- 配置多模态模型：在 `Agent.metadata["multimodal_model"]` 中设置专用多模态模型（如 `"gpt-4o"`）
- 自动路由逻辑：
  * 检查用户输入中是否包含多媒体数据（图像、视频等）
  * 无多媒体数据：使用 `Agent.model`（廉价文本模型，如 `"gpt-4o-mini"`）
  * 有多媒体数据：自动升级至 `agent.metadata["multimodal_model"]`
- 便捷配置方法：
  * 使用 `create_agent_with_multimodal(...)`：直接创建带多模态模型的 Agent
  * 使用 `auto_configure_multimodal_agent(agent, multimodal_model="...")` 灵活配置既有 Agent
  * 或手动设置 `agent.metadata["multimodal_model"] = "gpt-4o"`
- 此过程对调用方完全透明，无需手动模型切换

当 `OrchestrationPolicy` 配置了统一模型（`unified_model`）或任务模型（`task_models`）时，引擎会按配置执行辅助 LLM 任务进行记忆汇聚与多模态处理，再调用主模型回复。

**辅助任务** (all optional, enable via config):
- `memory_extract`：LLM 提取用户身份、偏好、特征（confidence: 0.8）
  - 支持**频率控制**避免零碎提取：`memory_extract_batch_size`（每N条消息执行，默认1）+ `memory_extract_min_content_length`（内容最小长度，默认0）
- `event_extract`：LLM 提取事件结构化要素（confidence: 0.65）
- `multimodal_parse`：LLM 解析多模态输入为文本证据（confidence: 0.75）
- `memory_manager`：LLM 汇聚、去重、标注、冲突检测（confidence: 0.9+）✨ 新增

**记忆改造** (Phase 1 完成 → Phase 2 V2 重构):
- ✗ 删除：启发式正则提取（高误率，已舍弃）
- ✓ 新增：`MemoryFact.memory_category` 分类（identity/preference/emotion/event/custom）
- ✓ 新增：`MemoryFact.validated` 验证标记
- ✓ 新增：`MemoryFact.conflict_with` 冲突记忆列表
- ✓ 新增：结构化系统提示呈现（按类别分组，带置信度）
- ✓ **V2 破坏性变更**：`is_transient` 从存储字段改为动态方法 (`fact.is_transient(threshold=0.85)`)
- ✓ **V2 破坏性变更**：移除 `created_at` 字段，统一使用 `observed_at`
- ✓ V2 新增：`MemoryFact.mention_count` 去重提频计数
- ✓ V2 新增：`MemoryFact.source_event_id` 事件来源追踪
- ✓ V2 新增：`MemoryFact.context_channel` / `context_topic` 富上下文
- ✓ V2 新增：`MemoryPolicy` 集中配置（阈值、衰退曲线、集合上限、摘要限长）
- ✓ V2 新增：`observed_*` 集合自动 cap（默认 100）
- ✓ V2 新增：`confidence` 自动钳位 [0.0, 1.0]
- ✓ V2 新增：摘要按类型限制数量（`max_facts_per_type`）
- ✓ V2 新增：更陡峭的衰退曲线（180天仅保留5%，旧为20%）
- 迁移指南：`docs/migration-memory-v2.md`
每次模型调用后，写入 token 使用记录到 `Transcript.token_usage_records`（内存），并同步持久化至 `{work_path}/token_usage.db`（SQLite）。跨会话分析可通过 `TokenUsageStore` + `sirius_chat.token.analytics` 模块实现。

1. 返回 transcript 供展示或存储。

动态群聊模式（`run_live_session`）补充：

- 允许参与者在运行时首次出现（不要求预先出现在 `participants`）。
- 推荐调用方式：`run_live_session(...)` 用于一次性初始化；随后通过 `run_live_message(...)` 按条处理上游消息。
- 每条 `Message` 可通过 `reply_mode` 控制是否触发主 AI 回复：
  - `always`（默认）：始终回复；
  - `never`：仅摄取记忆与上下文，不生成 assistant 消息；
  - `auto`：根据文本特征自动判断（疑问、点名主 AI、请求语气更倾向回复）。
- `reply_mode=auto` 使用多维意愿分机制：请求意图、点名强度、事件相关度、内容丰富度与节奏惩罚（单用户频率、群聊密度、AI 冷却）共同决定是否回复。
- 相关参数可通过 `OrchestrationPolicy` 配置：`session_reply_mode`、`auto_reply_user_cadence_seconds`、`auto_reply_group_window_seconds`、`auto_reply_group_penalty_start_count`、`auto_reply_assistant_cooldown_seconds`、`auto_reply_threshold`、`auto_reply_threshold_boost_start_count` 等。
- `run_live_session` 的节奏临时状态已挂载到 `Transcript.reply_runtime`，在复用同一个 `transcript` 多次调用时保持连续。
- 外部可通过 `User`（`user_id/name/aliases/traits`）显式注册用户。
- 外部可通过 `User` 中的 `identities`（如 `qq/wechat` 外部 ID）实现跨环境同人识别。
- 对每位参与者维护结构化 `user_memory`：
  - `profile`：初始化档案字段（如 `name/persona/traits/identities`）。
  - `runtime.memory_facts`：结构化分类记忆，包含 fact_type、value、source、confidence、observed_at、observed_time_desc、memory_category、validated、conflict_with、context_channel、context_topic、mention_count、source_event_id。
  - `runtime.recent_messages`、`runtime.inferred_persona`、`runtime.inferred_traits`、`runtime.preference_tags`。
- `Transcript.find_user_by_channel_uid(channel, uid)` 提供按渠道+外部 UID 的直接定位能力。
- 每次调用主 AI 时自动注入“参与者记忆”上下文，增强识人与连续性。
- 引擎会在每次用户发言后主动更新 runtime 记忆（偏好标签、推断画像、摘要笔记），提升拟人化对话能力。
- 摘要写入采用统一去重入口：语义相同的普通摘要/事件摘要/多模态摘要不会重复污染 `summary_notes`，并会同步形成可追溯事实记录。
- 引擎会在每次用户发言后执行事件命中分析：
  - 先提取关键词、角色槽位、时间线索、实体、情绪标签；
  - 再按加权评分匹配历史事件；
  - 根据阈值输出高置信命中、弱命中或新增事件，并注入一条系统事件说明到上下文。
- 事件记忆持久化路径：`work_path/events/events.json`。

✨ **事件系统与用户记忆系统的双向适配（方案C）**：
  - 每条事件的特征（emotion_tags、keywords、role_slots、entities）自动转化为用户记忆事实：
    * `emotion_tags` → `emotional_pattern` 事实（confidence: -0.05）
    * `keywords` → `user_interest` 事实（confidence: -0.10）
    * `role_slots` → `social_context` 事实 + 自动推断用户特征（如检测领导角色 → 推断 `leadership_tendency`，confidence: -0.05）
    * `entities` → `observed_entities` 集合（用于跨事件关联）
  - 事件与用户历史的**双向观测**：
    * 提取事件时：`apply_event_insights()` 转化事件特征为结构化用户事实
    * 理解事件时：`interpret_event_with_user_context()` 基于用户历史计算四维对齐度
      - keyword_alignment：事件关键词与用户历史的文本重叠度
      - role_alignment：事件角色与用户已知角色的重叠度
      - emotion_alignment：事件情感与用户历史情感的相似度
      - entity_alignment：事件实体与用户已知实体的重叠度
    * 对齐度计算：`avg_alignment = (keyword + role + emotion + entity) / 4`
    * 信度动态调整：`adjusted_confidence = base_confidence(0.65) + avg_alignment × 0.3`，范围 [0.5, 1.0]
    * 推荐处理类别：`high_confidence`(avg>0.6) | `normal` | `low_relevance`(avg<0.2) | `pending`(新用户)
  - 优势：事件不再被单向消费，而是成为用户理解的重要信号源，真正构建**统一的用户心智模型**

记忆压缩与预算控制补充：

- 引擎根据 `history_max_messages` 与 `history_max_chars` 执行自动压缩。
- 被压缩的历史会进入 `session_summary`，用于后续提示词补偿。
- 通过 `JsonSessionStore` 可在重启后恢复 transcript、participant_memories 与摘要。

## 记忆质量评估与智能遗忘（Phase 2）

### 记忆质量评估

系统提供离线评估工具，对所有用户的记忆进行质量分析：

**核心指标**：
- **年龄评分 (recency_score)**：根据记忆年龄划分活跃度等级
  - 0-7 天：0.9-1.0（高度活跃）
  - 7-30 天：0.6-0.9（中等活跃）
  - 30-90 天：0.2-0.6（低度活跃）
  - >90 天：0.0-0.2（接近遗忘）
- **综合质量评分 (quality_score)**：置信度(50%) + 活跃度(30%) + 验证状态(15%) 加权计算，冲突时额外减30%
- **行为一致性评分**：按记忆分类（identity/preference/emotion/event）分别计算，整体评分为四类加权平均

**评估命令示例**：
```bash
# 分析所有用户的记忆质量，输出报告
python -m sirius_chat.memory_quality_tools work_path --action analyze --output-report report.json --verbose
```

### 智能遗忘

**衰退机制**：基于时间表自动降低陈旧记忆的置信度
- 7 天：保留 95% 置信度
- 30 天：保留 85% 置信度
- 60 天：保留 70% 置信度
- 90 天：保留 50% 置信度
- 180 天：保留 20% 置信度
- 冲突记忆加速衰退：额外乘以 0.7

**自动清理**：满足以下条件之一的记忆会被清理
- 极低置信度(<0.2) 且陈旧(>30天)
- 存在冲突 且 低置信度(<0.4) 且 极旧(>90天)
- 质量评分(<0.2) 且陈旧(>60天)

**管理命令示例**：
```bash
# 完整流程：分析 + 应用衰退 + 清理低质量记忆
python -m sirius_chat.memory_quality_tools work_path --action all

# 仅清理质量评分<0.3 的记忆
python -m sirius_chat.memory_quality_tools work_path --action cleanup --min-quality 0.3

# 应用衰退表，更新所有记忆
python -m sirius_chat.memory_quality_tools work_path --action decay
```

**集成到主引擎**：
- 通过 `UserMemoryManager.apply_scheduled_decay()` 在会话周期内执行衰退。
- 通过 `UserMemoryManager.cleanup_expired_memories(min_quality)` 定期清理低质量记忆。

## AI 自身记忆系统 ✨ **(v0.13.0 新增)**

独立于用户记忆的 AI 自主记忆子系统，位于 `sirius_chat/memory/self/`。由两个子系统组成：

### 日记子系统 (Diary)

AI 自主决定需要记忆的内容（反思、观察、决策、情感、里程碑），每条日记携带重要性评分和关键词标签。

**数据模型** (`DiaryEntry`)：
- `content`：日记内容
- `importance`：重要性 [0, 1]，影响遗忘速度
- `keywords`：关键词标签，用于检索与相关性匹配
- `category`：分类（reflection | observation | decision | emotion | milestone）
- `confidence`：当前置信度（衰退后），初始 1.0
- `mention_count`：话题再次被提及的次数，可减缓遗忘

**遗忘曲线**（时间衰退表）：
| 天数 | 基础保留率 |
|------|-----------|
| 3    | 95%       |
| 7    | 85%       |
| 14   | 70%       |
| 30   | 50%       |
| 60   | 30%       |
| 90   | 15%       |
| 180  | 5%        |

- 高重要性条目衰退减缓（importance=1.0 时衰退速度降低 40%）
- 每次被提及增加 5% 保留率（上限 25%）
- 置信度低于 0.05 的条目自动移除
- 容量上限：100 条日记，超出时淘汰最弱条目

### 名词解释子系统 (Glossary)

在对话中收集 AI 不理解的名词，逐步建立定义库。

**数据模型** (`GlossaryTerm`)：
- `term`：术语名称
- `definition`：当前最佳定义
- `source`：学习来源（conversation | user_explained | inferred）
- `confidence`：定义置信度
- `usage_count`：在对话中出现的次数
- `domain`：领域（tech | daily | culture | game | custom）
- `context_examples`：使用示例（上限 5 条）

更新规则：
- 相同术语再次出现时合并：保留更高置信度的定义，累加使用次数，合并示例
- 容量上限：200 条术语，超出时淘汰使用频率最低的条目

### 提示词集成

日记和名词解释在系统提示词中分别生成紧凑的 XML 段：

- `<self_diary>`：按相关性排序，格式 `[category]! content? #keywords`
- `<glossary>`：格式 `term?: definition`（`?` 表示低置信度，`~` 表示中等）

通过 `OrchestrationPolicy` 配置：
- `enable_self_memory`：是否启用（默认 `True`）
- `self_memory_extract_batch_size`：每 N 条回复后触发一次 LLM 提取（默认 3）
- `self_memory_max_diary_prompt_entries`：提示词中包含的日记条数上限（默认 6）
- `self_memory_max_glossary_prompt_terms`：提示词中包含的术语条数上限（默认 15）

### 持久化

`SelfMemoryFileStore` 将自身记忆序列化为 `{work_path}/self_memory.json`，在会话加载时读取、结束时保存。

## 回复频率限制 ✨ **(v0.13.0 新增)**

基于滑动窗口的回复频率控制，防止 AI 在短时间内过度回复。

**机制**：
- 在 `_process_live_turn()` 中，回复意愿判定通过后额外检查频率限制
- 滑动窗口内（默认 60 秒）AI 回复次数超过上限（默认 8 次）时跳过回复
- 对主动提及 AI 名字或别名的消息免除限制（可配置关闭）

**配置** (`OrchestrationPolicy`)：
- `reply_frequency_window_seconds`：滑动窗口长度（默认 60.0 秒）
- `reply_frequency_max_replies`：窗口内最大回复数（默认 8）
- `reply_frequency_exempt_on_mention`：提及 AI 时是否免除（默认 `True`）

回复时间戳存储在 `Transcript.reply_runtime.assistant_reply_timestamps` 中，支持跨调用复用 transcript 时保持频率控制连续性。

## 意图分析系统

分析用户消息意图，优化回复意愿评分与系统提示词构建。

### 工作原理

每次用户发言后，引擎在 `_process_live_turn()` 中对消息执行意图分析：

1. **LLM 路径**（`enable_intent_analysis=True` 时）：
   - 向意图分析模型发送结构化 JSON schema 提示
   - 解析返回的 `intent_type`、`directed_at_ai`、`importance`、`needs_memory`、`needs_summary`
   - 计算 `willingness_modifier`：question/request/command 正向加分，reaction 负向扣分，未指向 AI 额外减分
   - 生成 `skip_sections`：不需要 memory 时跳过 `participant_memory`，不需要 summary 时跳过 `session_summary`

2. **关键词回退路径**（默认，或 LLM 调用失败时）：
   - 基于问号、请求关键词（"请"、"帮我"、"please"）、短反应语（"好"、"嗯"、"ok"）等模式匹配
   - 零 LLM 开销
   - **不修改 willingness score**，仅提供 `skip_sections` 优化

### 配置

- `OrchestrationPolicy.enable_intent_analysis`：启用 LLM 意图分析（默认 `True`；设为 `False` 退回关键词回退路径）
- `OrchestrationPolicy.intent_analysis_model`：指定模型名（空字符串则依次回退 `unified_model` → `agent.model`）

## 记忆归纳系统

后台定时使用 LLM 对已有的事件、摘要和事实进行整理合并，控制记忆膨胀。

### 归纳方法

- **事件归纳** (`EventMemoryManager.consolidate_entries`)：按 category 分组，合并含义相似的观察记录，保留最高 confidence，累加 mention_count。
- **摘要归纳** (`UserMemoryManager.consolidate_summary_notes`)：LLM 合并冗余摘要为更精炼的条目。
- **事实归纳** (`UserMemoryManager.consolidate_memory_facts`)：LLM 按 fact_type 合并事实，保留最高 confidence，累加 mention_count。

### 后台循环

`BackgroundTaskManager` 在会话初始化时启动归纳循环（`_consolidation_loop`），按 `consolidation_interval_seconds`（默认 900 秒）定时触发。每次触发时遍历所有用户执行归纳并持久化。

### 配置

- `OrchestrationPolicy.consolidation_enabled`：启用后台归纳（默认 `True`）
- `OrchestrationPolicy.consolidation_interval_seconds`：归纳间隔秒数（默认 `7200`，引擎层传递给 BackgroundTaskConfig）
- `OrchestrationPolicy.consolidation_min_entries/notes/facts`：触发阈值

## 扩展点

- 在 `sirius_chat/providers/` 下新增实现 `LLMProvider` 的 provider。
- 在 provider 调用前增加安全层（审核、token 预算、重试）。
- 引入除 round-robin 外的人类发言调度策略（优先级、权重等）。
- 增加 transcript 的持久化存储能力。

## Roleplay 提示词生成系统

### PersonaSpec（持久化生成输入）

`PersonaSpec` 是角色提示词生成的统一输入规格，支持三条构建路径：

| 路径 | 输入 | 适用场景 |
|------|------|---------|
| **Tag-based** | `trait_keywords=["热情","直接"]` | 快速构建，无需问卷访谈 |
| **Q&A-based** | `answers=[RolePlayAnswer(...)]` | 传统问答流程（向后兼容） |
| **Hybrid** | 同时提供 keywords + answers | 关键词锚定特质，问答丰富细节 |

`PersonaSpec` 随生成结果一起持久化到 `generated_agents.json`，支持增量微调：调用 `aupdate_agent_prompt()` 时仅需传入变化字段，其余字段保留原始值。

### Persona 语义变化（v0.8.2+）

- `Agent.persona` = **关键词标签**（3-5 个，`/` 分隔，≤30 字，如 `"热情/直接/逻辑清晰"`）
  - 展示在 `<agent_identity>` 信息块中，供模型快速理解角色核心
- `global_system_prompt` = **完整角色扮演指南**（400-700 字）
  - 包含性格、沟通风格、价值观、行为边界及安全提醒
  - 展示在 `<global_directive>` 信息块中

### 核心 API

```python
# Tag-based（快速路径）
spec = PersonaSpec(agent_name="北辰", trait_keywords=["热情", "直接"])
preset = await agenerate_from_persona_spec(provider, spec, model="...")

# Q&A（传统路径，向后兼容）
preset = await agenerate_agent_prompts_from_answers(provider, model="...", agent_name="...", answers=[...])

# 一步构建并写入 config
await abuild_roleplay_prompt_from_answers_and_apply(
    provider, config=config, model="...",
    trait_keywords=[...],           # 或 answers=[...] 或同时提供
    persona_key="my_agent",
)

# 增量微调（只重写背景，其余不变）
updated = await aupdate_agent_prompt(
    provider, work_path=..., agent_key="my_agent", model="...",
    background="最近经历了变化",
)

# 加载已持久化的 spec
spec = load_persona_spec(work_path, "my_agent")
```

迁移指南：`docs/migration-roleplay-v082.md`

## 已知限制

- 当前 provider 实现默认假设 OpenAI 兼容 JSON 响应结构。
- SiliconFlow 适配使用 OpenAI 兼容接口，默认请求路径为 `/v1/chat/completions`。
- 自动路由基于模型名前缀与可用 provider；复杂策略（负载均衡/健康检查）暂未内置。
- 任务级编排当前包含 `memory_extract`、`multimodal_parse`、`event_extract` 与 `memory_manager`，并使用 token 预算控制。
- 任务级编排支持任务重试与多模态输入限流裁剪（按 `OrchestrationPolicy` 配置）。
- API Key 目前直接来自配置；生产环境建议改为环境注入。
- 当前每轮传入完整上下文；长会话需考虑裁剪或摘要压缩。

编排策略详情见：`docs/orchestration-policy.md`。

## 相关技能

- 框架速读：`.github/skills/framework-quickstart/SKILL.md`
- 外部接入：`.github/skills/external-integration/SKILL.md`
- 技能同步约束：`.github/skills/skill-sync-enforcer/SKILL.md`


