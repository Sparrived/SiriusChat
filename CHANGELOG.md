# 变更日志

本文档记录 Sirius Chat 的所有版本变更。采用 [Keep a Changelog](https://keepachangelog.com/zh-CN/) 规范。

## [Unreleased]

## [0.14.7] - 2026-04-11

### Changed
- **`message_debounce_seconds` 默认值改为 5.0（生产环境并发消息合并）**
  - 高并发场景（如群聊）多条消息在 5 秒窗口内自动合并为一条，减少 AI 调用次数×提升用户体验
  - **测试环境**：所有测试文件需显式设置 `message_debounce_seconds=0.0`，保证测试速度 < 1 秒
  - 测试不需要等待完整 debounce 时长，只需验证功能逻辑可用性
  - 需要立即处理的场景可显式设为 `message_debounce_seconds=0.0`

## [0.14.6] - 2026-04-11

### Added
- **`write-tests` SKILL**（`.github/skills/write-tests/SKILL.md`）：测试编写完整规范
  - 速度红线：单测 < 1 秒，套件 < 30 秒；禁止 `asyncio.sleep`、debounce、后台任务
  - 标准 `OrchestrationPolicy` 配置模板（关闭所有辅助 LLM 任务）
  - `MockProvider` 与 `_run_live_turns` 标准模式
  - 断言规范、文件组织规范、命名规范
  - 常见陷阱速查表（debounce / enable_self_memory / work_path 污染等）

### Fixed
- **`message_debounce_seconds` 默认值 `8.0` → `0.0`**（性能）
  - 默认 8s 导致每次 `run_live_message` 都睡 8 秒，全套测试从 605s 降至 13s（× 46）
  - Debounce 是群聊 opt-in 功能，需显式设置方可启用



### Changed
- **SelfMemory 触发机制：消息计数 → 定时后台任务**
  - 旧机制（v0.13+）：每 N 条 AI 回复后（`self_memory_extract_batch_size`，默认 3）在主流程中 fire-and-forget；触发频率与对话强度强耦合，对话空闲时不提取，高频对话时每条回复都检查。
  - 新机制（v0.14.5）：以固定时间间隔（`self_memory_extract_interval_seconds`，默认 360 秒 / 6 分钟）在后台任务循环中提取，与对话速率完全解耦；由 `BackgroundTaskManager` 的 `_self_memory_loop` 统一管理。
  - `self_memory_extract_batch_size` 保留在 `OrchestrationPolicy` 中（向后兼容），但 engine 不再使用该字段触发提取。
  - `BackgroundTaskConfig` 新增 `self_memory_enabled` 和 `self_memory_interval_seconds` 字段。
  - `BackgroundTaskManager` 新增 `set_self_memory_callback()`、`trigger_self_memory_now()` 和 `_self_memory_loop()` 方法。
  - `LiveSessionContext.assistant_reply_count_since_self_extract` 字段已移除，替换为 `llm_semaphore: asyncio.Semaphore | None`。

- **LLM 并发限流：`max_concurrent_llm_calls`**
  - 高并发场景下多个用户的消息同时抵达时，原先会触发等量的并行 LLM 生成调用，造成模型侧压力堆积和响应延迟。
  - 新增 `OrchestrationPolicy.max_concurrent_llm_calls`（默认 `1`）：每个 session context 最多允许指定数量的 LLM 主回复生成同时执行，超出部分排队等待。
  - 纯算法路径（热度分析 `HeatAnalyzer`、关键词意图回退）**不受限流影响**，直接运行；只有 `_generate_assistant_message` 受信号量保护。
  - 设为 `0` 则禁用限制（无限并发，与旧版行为一致）。
  - 实现：`LiveSessionContext.llm_semaphore: asyncio.Semaphore | None`；`_noop_semaphore()` 用于限流关闭时的零开销兼容。

- **`_PendingTurn.timer_task` 死代码清理**：移除遗留的 `timer_task` 引用（该字段已在 v0.14.2 debounce 重构时删除）。

### Migration Guide (v0.14.4 → v0.14.5)

**SelfMemory：**
- 原 `self_memory_extract_batch_size=N` 配置不会报错，但已无实际效果（默认每 6 分钟后台提取一次）。
- 如需调整频率，设置 `self_memory_extract_interval_seconds`（单位：秒，推荐 300–600）。

**并发限流：**
- 默认 `max_concurrent_llm_calls=1` 会将主回复生成串行化。如需并发（旧行为），设 `max_concurrent_llm_calls=0`。
- 群聊+多用户场景建议保持 `1`，避免模型排队积压。



### Changed
- **SKILL 执行模式：模板链 → 迭代反馈循环（Breaking Change for v0.14.3 chain syntax）**
  - 旧模式（v0.14.3）：AI 在同一回复中放置多个 `[SKILL_CALL:]`，后续参数用 `${skill_name}` 引用前序结果；engine 一次性执行全部调用，结果全部注入后统一重生。
  - 新模式（v0.14.4）：每轮 AI 只放 **一个** `[SKILL_CALL:]`，engine 立即执行并将结果以 `[SKILL执行结果: skill_name]\n{result}` 注入到对话上下文，然后重新调用 LLM；模型看到真实结果后自主决定下一步（继续调用其他 SKILL、传入新参数，或直接给出最终回复）。
  - 优点：参数值可完全由模型基于实际结果动态生成，无需预先使用 `${template}` 占位符；适应非结构化/意外结果的能力更强。
  - 行为变化：N 次 SKILL 调用现需 N 次 LLM 生成轮次（原为 1 次）。`max_skill_rounds` 语义不变：仍为单 turn 内允许的最大 SKILL→重生轮数。
  - `SkillChainContext`、`SkillResult.get_field()`、`executor.chain_context` 参数仍保留（供高级程序化用途），但 engine 内部不再通过 `chain_context` 进行模板替换。
  - 系统提示词 `<available_skills>` 段已更新为迭代反馈模式说明，删除 `${template}` 语法示例。

### Migration Guide (v0.14.3 → v0.14.4)

**如果你有配置 SKILL 但未使用 `${template}` 语法：** 无需任何修改，行为兼容。

**如果你的 SKILL 提示词/system prompt 中显式教导模型使用 `${skill_name}` 语法：**
- 建议删除相关自定义引导，或替换为："每轮只调用一个SKILL，看到结果后决定下一步，参数直接写你想传的值"
- 模型不再需要预先声明完整调用链，遇到复杂任务可逐步决策

**程序化使用 `SkillExecutor.execute(chain_context=...)` 的代码：** 不受影响，`chain_context` 参数仍然有效（可在自定义流程中继续使用 `${template}` 解析）。


### Added
- **SKILL 链式调用（Chain Invocation）**：AI 现在可在同一回复中顺序调用多个 SKILL，后续 SKILL 的参数可直接引用前序结果。
  - `SkillChainContext`（`sirius_chat/skills/models.py`）：单轮 SKILL 执行的共享上下文，存储每个 SKILL 的 `SkillResult`。
  - 参数模板语法：
    - `${skill_name}` — 引用前序 SKILL 的完整文本输出
    - `${skill_name.field}` — 引用前序 SKILL 返回 dict 的某字段（或 list 的 0 索引）
    - 未能解析的占位符保持原样传入，不会导致执行失败
  - 单轮多调用：引擎在每个生成轮次内按顺序执行当前内容中的**所有** `[SKILL_CALL:]` 标记，而不再仅限于第一个；所有调用共享同一个 `SkillChainContext`。
  - 一轮所有调用完成后才统一重新生成 AI 最终回复（减少 LLM 调用次数）。
  - `SkillResult.get_field(key)` 新方法：支持从 dict/list 结果中取值，用于模板解析。
  - `SkillExecutor.execute()` / `execute_async()` 新增 `chain_context` 可选参数。
  - 系统提示词（`prompts.py`）更新 `<available_skills>` 章节，文档化链式语法和示例。
  - 已知 SKILL 中途遇到未知 SKILL 时，当轮链式调用中止，已执行部分的结果仍保留；未知 SKILL 前的文本不再作为 partial 消息提前发出（保持原有语义）。

### Fixed
- **记忆时间戳动态化**：修复 `participant_memory` 提示词中旧记忆因缺少时间上下文被 AI 误作当前对话的问题。
  - 新增 `_relative_time_zh()` 辅助函数，从 `observed_at` 字段实时计算中文相对时间（"3天前"、"2个月前"等）。
  - 每条 memory fact 附加动态相对时间标签，替代过期的静态 `observed_time_desc` 字符串。
  - `<participant>` 标签新增 `最后记录="X天前"` 属性，AI 可一眼判断该用户数据的新鲜度。
  - `recent_messages` 标签从"近期"改为"历史消息"，消除歧义。
  - 记忆块前缀说明明确标注"历史记忆积累，非当前对话状态"，并添加"不要主动回答记忆中的历史问题"指令。
- **`get_rich_user_summary()` 补全 `observed_at`**：`facts_by_type` 中每条 fact_info 现在包含 `observed_at` 字段，供 prompt 层动态计算时间。
- **新增 `last_fact_at`**：`get_rich_user_summary()` 返回值新增 `last_fact_at` 字段，为该用户所有 memory fact 中最新的 `observed_at` 时间戳。

## [0.14.1] - 2026-04-10

### Removed
- **彻底移除旧意愿分系统兼容代码**：
  - 删除 `OrchestrationPolicy` 中全部 11 个 `auto_reply_*` Legacy 参数（传入将引发 `TypeError`）。
  - 删除 `ReplyWillingnessDecision` dataclass。
  - 删除旧 `_run_intent_analysis()` 方法（由 `_run_engagement_intent_analysis()` 替代）。
  - 删除 `sirius_chat/core/intent.py`（由 `core/intent_v2.py` 替代）。
  - 简化 `_should_reply_for_turn()` 签名为 `(turn: Message) -> bool`。

### Added
- **迁移指南**：新增 `docs/migration-v0.14.md`，覆盖配置迁移对照表、代码迁移示例和检查清单。

## [0.14.0] - 2026-04-10

### Added
- **三级参与决策系统**：完全重写旧意愿分系统（~15 个 auto_reply_* 参数），替换为三个协作子系统：
  - **HeatAnalyzer** (`core/heat.py`)：零 LLM 开销的群聊热度分析，基于消息密度、活跃参与者数和 AI 参与比计算热度等级（cold/warm/hot/overheated）。
  - **IntentAnalyzer v2** (`core/intent_v2.py`)：重写意图分析，新增显式 `target` 字段（ai/others/everyone/unknown），解决群聊中 AI 无法正确识别对话对象的问题。LLM 路径增强上下文（参与者列表 + 8 条近期消息）；关键词回退路径支持参与者名称匹配。
  - **EngagementCoordinator** (`core/engagement.py`)：融合热度、意图和 `engagement_sensitivity` 输出最终回复决策（`EngagementDecision`），内置回复频率限制。
- **简化配置**：仅 `engagement_sensitivity`（0–1，默认 0.5）和 `heat_window_seconds`（默认 60）两个参数。

### Changed
- **OrchestrationPolicy**：旧 auto_reply_* 参数已移除（v0.14.1），新增 `engagement_sensitivity` 和 `heat_window_seconds`。
- **core/engine.py**：`_process_live_turn` 流程重写为 heat → intent v2 → engagement coordinator → frequency limit。
- **core/__init__.py / api/__init__.py**：导出更新为新模块。
- **测试**：重写 `test_async_engine.py`、`test_intent_and_consolidation.py`、`test_self_memory.py` 中所有涉及旧意愿系统的用例。

### Removed
- 引擎内部方法 `_evaluate_reply_willingness`、`_compute_intent_score`、`_compute_addressing_score`、`_compute_event_relevance_score`、`_compute_richness_score`、`_deterministic_probability_roll`。

## [0.13.0] - 2026-04-10

### Added
- **AI 自身记忆系统**（`sirius_chat/memory/self/`）：独立于用户记忆的 AI 自主记忆子系统。
  - **日记子系统 (Diary)**：AI 自主决定需要记忆的内容，每条日记携带重要性评分、关键词标签和分类（reflection/observation/decision/emotion/milestone）。基于时间的遗忘曲线自动衰退置信度（3天95%→180天5%），高重要性条目衰退减缓40%，被提及的条目获得保留加成。
  - **名词解释子系统 (Glossary)**：在对话中收集 AI 不理解的名词，逐步建立定义库。支持多来源（conversation/user_explained/inferred）和多领域（tech/daily/culture/game/custom），相同术语自动合并。
  - **提示词集成**：日记和名词解释分别以 `<self_diary>` 和 `<glossary>` XML 段注入系统提示词，紧凑格式减少 token 消耗。
  - **LLM 自动提取**：每 N 条回复后（`self_memory_extract_batch_size`，默认3）自动触发 LLM 提取日记和名词，fire-and-forget 不阻塞主流程。
  - **持久化**：`SelfMemoryFileStore` 将自身记忆序列化为 `{work_path}/self_memory.json`。
- **回复频率限制器**：基于滑动窗口的 AI 回复频率控制。
  - `reply_frequency_window_seconds`（默认60秒）窗口内超过 `reply_frequency_max_replies`（默认8次）时跳过回复。
  - 对主动提及 AI 名字或别名的消息免除限制（`reply_frequency_exempt_on_mention=True`）。
  - 回复时间戳存储在 `Transcript.reply_runtime.assistant_reply_timestamps` 中。
- **OrchestrationPolicy 新配置项**：`enable_self_memory`、`self_memory_extract_batch_size`、`self_memory_max_diary_prompt_entries`、`self_memory_max_glossary_prompt_terms`、`reply_frequency_window_seconds`、`reply_frequency_max_replies`、`reply_frequency_exempt_on_mention`。
- **测试**：新增 56 条测试覆盖日记/名词解释/衰退/持久化/提示词集成/频率限制器（`test_self_memory.py`）。

### Changed
- **提示词优化**：精简系统提示词文本，缩短 splitting_instruction、skill 规则和 constraints 段，减少 token 消耗。
- **会话后台任务**：归纳周期中同步执行日记衰退与自身记忆持久化。

## [0.12.6] - 2026-04-09

### Fixed
- **恢复 v0.9.4 语义**：当模型在同一轮同时输出 `SKILL_CALL` 与普通用户可见文本时，engine 会继续将清理后的普通文本通过事件总线发送给外部消费者，而不会泄露 `SKILL_CALL` 标记本身。
- **on_reply 同轮提示恢复**：修复此前过度抑制中间文本导致外部插件收不到“正在查询中”等正常提示文案的问题。

### Added
- **回归测试**：新增 `test_on_reply_emits_plain_text_alongside_skill_call`，覆盖 `SKILL_CALL + 普通文本` 同轮输出场景。

## [0.12.5] - 2026-04-09

### Fixed
- **SKILL 空回复兜底优化**：当 SKILL 已执行但模型未生成最终自然语言答复时，engine 不再直接输出固定报错，而是优先基于前置文案和最后一次 SKILL 结果生成可用摘要回复。
- **外部 on_reply 可用性提升**：避免外部消费者收到“已执行 skill 但没有任何有用内容”的低质量兜底消息。

### Added
- **回归测试**：新增 `test_skill_rounds_exhausted_fallback_uses_skill_result_summary`，覆盖“多次 SKILL_CALL 后仅能依赖 skill 结果摘要输出”的场景。

## [0.12.4] - 2026-04-09

### Fixed
- **SKILL 轮次耗尽导致空回复**：当模型在 SKILL 执行后持续返回 `SKILL_CALL` 或最终内容被清理为空时，engine 会强制触发一次“仅生成最终答复”的再生成，避免落地空 assistant 消息。
- **最终回复兜底**：若再生成后仍为空，返回明确兜底文本，确保外部回调始终可收到可用回复。

### Added
- **回归测试**：新增 `test_skill_rounds_exhausted_still_returns_final_answer`，覆盖 transcript 中出现的“多次 SKILL 结果后 assistant 为空”场景。

## [0.12.3] - 2026-04-09

### Fixed
- **SKILL 命中失败即时重载**：当 `SKILL_CALL` 已解析但 `skill_registry.get()` 未命中时，engine 会即时重载 `work_path/skills` 并二次查找，修复“skill 文件存在但上下文复用导致注册表未命中”的问题。
- **on_reply 中间态泄露修复**：SKILL 轮次中的中间 assistant 文本不再通过事件总线对外发送，避免外部插件再次收到调用前文案。
- **未知 SKILL 回退输出**：未知技能场景不再提前结束，改为注入系统提示后再生成最终回复，确保外部仍能收到技能后语义完整输出。

### Added
- **回归测试**：新增/增强 on_reply+SKILL 与注册表重载命中路径测试，覆盖插件侧真实调用场景。

## [0.12.2] - 2026-04-09

### Fixed
- **SKILL 运行时懒挂载**：修复 live context 复用场景下，`enable_skills` 后置开启或 `skills/` 目录后置就绪时 `skill_registry/skill_executor` 可能为空，导致 `SKILL_CALL` 被解析但未进入执行分支的问题。
- **on_reply + SKILL 回调可达性**：增强 `run_live_message(..., on_reply=...)` 路径，确保技能执行后 assistant 内容可稳定通过回调送达外部插件。
- **可观测性增强**：当检测到 `SKILL_CALL` 但技能运行时未就绪时输出明确 warning，便于外部插件快速定位配置/挂载问题。

### Added
- **回归测试**：新增 context 复用下技能懒挂载测试 `test_skill_runtime_lazy_attach_when_context_reused`，覆盖“先无技能再启用技能”的真实插件路径。

## [0.12.1] - 2026-04-09

### Fixed
- **on_reply 回调订阅竞态**：修复 `run_live_message(..., on_reply=...)` 在高并发时可能在订阅建立前开始处理消息，导致首批事件丢失的问题；该问题会在外部插件的 SKILL 场景中表现为回复未正确投递。
- **SKILL 回调链路稳定性**：确保 `on_reply` 模式下 SKILL 执行后的 assistant 消息稳定送达回调，避免出现“技能执行了但外部未收到回复”的现象。

### Added
- **回归测试**：新增 `test_on_reply_callback_with_skill_execution`，覆盖 `on_reply + SKILL` 组合路径，验证 SKILL_CALL 标记不外泄且最终回复可达。

## [0.12.0] - 2026-04-10

### Added
- **`arun_live_message` 新增三个可选参数**：
  - `on_reply: Callable[[Message], Awaitable[None]]`：engine 自动管理事件订阅与消费，每条 AI 回复触发回调，外部无需操作 `asubscribe`/事件总线。
  - `user_profile: UserProfile | None`：自动注册用户到记忆系统，免去外部手动 `register_user` 调用。
  - `timeout: float`：engine 级超时，超时后自动清理内部资源并抛出 `TimeoutError`。
- **内部方法 `_run_live_message_with_callback`**：封装事件订阅、回调消费、超时清理的完整流程。

### Fixed
- **debounce `CancelledError` 吞没外部超时**：修复 `_run_live_message_core` 中 debounce sleep 的 `except CancelledError: return transcript` 错误地拦截了外部 `asyncio.wait_for` 的超时取消信号。移除该捕获，使外部超时与关停取消能正确传播。

### Changed
- **外部插件样板代码精简**：`sirius_chat_group` 插件的 `_chat_once_locked` 和 `_chat_private_once_locked` 各减少约 45 行手动事件订阅/消费/清理代码，改为使用 `on_reply` + `timeout` 参数。
- **迁移指南**：`docs/migration-v0.12.md`。

## [0.11.0] - 2026-04-09

### Added
- **Token 使用 SQLite 持久化** (`sirius_chat/token/store.py`)：新增 `TokenUsageStore` 类，每次模型调用自动将 `TokenUsageRecord` 写入 `{work_path}/token_usage.db`。基于 Python 标准库 `sqlite3`，无新依赖。支持 WAL 模式、批量写入、跨会话查询与多条件筛选。
- **多维度 Token 分析模块** (`sirius_chat/token/analytics.py`)：基于 SQLite 的全量分析函数集：
  - `compute_baseline()`：全局/筛选级基线统计（总调用数、token 合计、均值、重试率、completion/prompt 比值）
  - `group_by_session()`：按会话聚合
  - `group_by_actor()`：按用户聚合
  - `group_by_task()`：按任务类型聚合
  - `group_by_model()`：按模型聚合
  - `time_series()`：按固定时间桶聚合（默认 1 小时）
  - `full_report()`：一次性输出包含 baseline + 所有维度的完整报告
- **引擎自动集成**：`AsyncRolePlayEngine` 在初始化 live session 时自动创建 `TokenUsageStore`，每次 `_call_provider_with_retry` 成功后同步写入 SQLite，与现有 `Transcript.token_usage_records` 内存归档并行，向后兼容。
- **公共 API 导出**：`TokenUsageStore`、`AnalyticsReport`、`BaselineDict`、`BucketDict`、`TimeSliceDict`、`compute_baseline`、`full_report`、`group_by_actor`、`group_by_model`、`group_by_session`、`group_by_task`、`time_series`。
- **意图分析增强**：`IntentAnalysis` 新增 `reason` 和 `evidence_span` 字段，LLM 路径和关键词回退路径均填充解释信息；JSON 解析失败时记录 `WARNING` 级日志。

### Fixed
- **消息尾部空白清理**：所有 `Message` 在创建时和通过 `Transcript.add()` 添加时，自动去除尾部 `\n` 和空格。

## [0.10.0] - 2026-04-09

### Added
- **意图分析系统** (`sirius_chat/core/intent.py`)：LLM-based 用户意图分析器，支持 question/request/chat/reaction/information_share/command 六种意图分类。LLM 路径默认启用（`enable_intent_analysis=True`）；可显式设为 `False` 退回关键词回退路径（零 LLM 开销）。
- **系统提示词段落跳过** (`skip_sections`)：意图分析可判定当前消息是否需要参与者记忆或会话摘要，跳过不需要的段落以减少 token 消耗。
- **事件归纳** (`EventMemoryManager.consolidate_entries`)：按 category 分组使用 LLM 归纳合并冗余观察记录。
- **摘要归纳** (`UserMemoryManager.consolidate_summary_notes`)：LLM 合并冗余摘要为精炼条目。
- **事实归纳** (`UserMemoryManager.consolidate_memory_facts`)：LLM 按 fact_type 合并冗余事实，保留最高 confidence 与累加 mention_count。
- **后台归纳循环** (`BackgroundTaskManager`)：新增记忆归纳定时循环，支持异步回调注入与 `trigger_consolidation_now()` 即时触发。
- **`OrchestrationPolicy` 新增配置字段**：`enable_intent_analysis`、`intent_analysis_model`、`consolidation_enabled`、`consolidation_interval_seconds`、`consolidation_min_entries`、`consolidation_min_notes`、`consolidation_min_facts`。
- **公共 API 导出**：`IntentAnalysis`、`IntentAnalyzer`、`BackgroundTaskConfig`、`BackgroundTaskManager`。

### Fixed
- **意图分析意愿修正隔离**：关键词回退路径（`enable_intent_analysis=False` 时）不再修改 willingness score，避免低阈值配置下误拒回复。

## [0.9.4] - 2026-04-08

### Fixed
- **SKILL 前置内容丢失**：当模型输出中 SKILL_CALL 标记与普通文字同时出现时（如 `[SKILL_CALL: ...]\n\n好的喵！`），SKILL_CALL 工标已被 `strip_skill_calls` 清理后的剩余文字现在会经过事件总线送出。逆转了 v0.9.3 中过度抹除该路径事件发送的错误修复。

## [0.9.3] - 2026-04-08

### Fixed
- **分割消息尾部空白**：所有经分割标记拆分和未拆分路径的消息内容均统一对尾部空白字符（`\n`、空格等）执行 `rstrip()` 清理。
- **SKILL 中间消息对外泄露**：移除 SKILL 执行过程中 `partial_msg`（SKILL 调用前的局部内容）的事件总线发送。外部订阅者现在只会收到 SKILL 执行完成后重新生成的最终消息，避免中间状态消息被外部平台识别为特殊卡片格式。

## [0.9.2] - 2026-04-08

### Fixed
- **`<MSG_SPLIT>` 明文输出**：当模型在同一回复中同时输出 `<MSG_SPLIT>` 和 `[SKILL_CALL: ...]` 时，SKILL 执行前提取的 `remaining_content` 未走分割逻辑，导致标记被原样输出。现在 `partial_msg` 路径也会对分割标记进行拆分处理。
- **`[SKILL_CALL: ...]` 残留输出**：当 SKILL 调用轮次达到 `max_skill_rounds` 上限后强制退出循环，此时 `content` 中可能仍残留 `[SKILL_CALL: ...]` 文本。现在循环退出后统一执行 `strip_skill_calls` 清理。

## [0.9.1] - 2026-04-08

### Changed
- **消息分割提示词强化**：`enable_prompt_driven_splitting=True` 时注入的分割指令更新：明确声明群聊场景、要求每条消息简短（1-2 句）、强制禁止用连续换行代替分割符，引导模型始终使用 `split_marker` 分割独立内容。

## [0.9.0] - 2026-04-08

### Added
- **Session 级事件流**：新增 `SessionEventBus`、`SessionEvent`、`SessionEventType`，提供实时 pub/sub 事件推送
- **`engine.subscribe(transcript)`**：返回 `AsyncIterator[SessionEvent]`，外部可持续接听会话事件
- **`asubscribe()` API 门面**：高层异步订阅接口
- 7 种事件类型：`MESSAGE_ADDED`、`PROCESSING_STARTED`、`PROCESSING_COMPLETED`、`SKILL_STARTED`、`SKILL_COMPLETED`、`REPLY_SKIPPED`、`ERROR`
- 迁移文档：`docs/migration-event-stream.md`

### Removed
- **`on_message` 回调参数**（破坏性变更）：从 `run_live_message()`、`arun_live_message()`、`run_session()` 移除
- **`OnMessage` 类型别名**：已由 `SessionEvent` 替代

### Changed
- 消息投递模型从回调式改为 pub/sub 事件流，外部消费者通过 `subscribe()` 获取实时事件

## [0.8.4] - 2026-04-08

### Added
- 引擎会在 `run_live_session` 初始化阶段始终创建 `{work_path}/skills/` 目录及 `README.md`，即使 `enable_skills=False` 也保留目录引导结构。

### Changed
- `OrchestrationPolicy.enable_skills` 默认值调整为 `True`，SKILL 系统改为默认开启。

## [0.8.3] - 2026-04-08

### Added
- 启用 SKILL 系统时，框架会自动在当前 `work_path` 下创建 `skills/` 目录，并生成 `README.md` 引导文档。

### Changed
- `SkillRegistry.load_from_directory()` 不再在目录缺失时直接返回，而是先完成 SKILL 目录初始化再继续扫描。

## [0.8.2] - 2026-04-08

### Added
- **`PersonaSpec` 持久化生成规格**：新增 `PersonaSpec` dataclass，封装角色生成的全部输入（keywords、answers、background 等），随生成结果一起写入 `generated_agents.json`，支持增量微调
- **Tag-based 构建路径**：`PersonaSpec(trait_keywords=[...])` 仅凭关键词列表即可生成完整角色，无需完整问卷访谈
- **Hybrid 构建路径**：同时提供 `trait_keywords` + `answers`，关键词锚定特质、问答丰富细节
- **`agenerate_from_persona_spec()`**：统一生成入口，支持 tag-only / Q&A / hybrid 三条路径
- **`aupdate_agent_prompt()`**：增量微调已生成的 agent，仅更新指定字段（背景/关键词/答案），无需全量重写
- **`load_persona_spec()`**：加载已持久化的 `PersonaSpec`
- 迁移文档：`docs/migration-roleplay-v082.md`
- 7 个新测试覆盖 PersonaSpec/tag-based/hybrid/update 路径

### Changed
- **`Agent.persona` 语义**：由 200-400 字描述性文本改为 3-5 个关键词标签（'/' 分隔，≤30 字）；完整角色指南移至 `global_system_prompt`
- **`abuild_roleplay_prompt_from_answers_and_apply`** 的 `answers` 参数由必填改为可选，新增 `trait_keywords` 和 `persona_spec` 参数
- **LLM 提示词精简**：生成提示词（system + user prompt）总长减少约 60%，结构更清晰



### Added
- **SKILL 依赖自动安装**：加载 SKILL 文件前自动检测并安装缺失的第三方依赖
  - 新增 `sirius_chat/skills/dependency_resolver.py`：AST 扫描 `SKILL_META["dependencies"]` 和 import 语句
  - 优先使用 `uv pip install`，回退到 `pip install`
  - `OrchestrationPolicy` 新增 `auto_install_skill_deps`（默认 True），可在受限环境关闭
  - `SKILL_META` 新增可选 `dependencies` 字段用于显式声明包名
- **迁移文档**：新增 `docs/migration-v0.8.md`，提供 v0.7→v0.8 全量变更指南（供 AI 查阅）

### Changed
- **测试套件瘦身与整合**：36 个测试文件整合为 27 个，删除 9 个冗余/微型文件
  - 4 个独立 provider 测试 + mock + middleware → 统一 `test_providers.py`（参数化基准测试）
  - `test_token_usage.py` → 并入 `test_token_utils.py`
  - `test_session_store.py` → 并入 `test_session_runner.py`
  - `test_main_resume.py` → 并入 `test_main_bootstrap.py`
  - Provider 测试改为参数化基准模式，新增 provider 只需扩展注册表

## [0.8.0] - 2026-04-10

### Added
- **System Prompt 瘦身**：大幅压缩系统提示词体积（约 22%），合并 `<output_constraints>` 与 `<security_constraints>` 为 `<constraints>`，压缩参与者记忆格式（`?`/`~` 替代冗长标签）
- **SKILL 执行超时**：`OrchestrationPolicy` 新增 `skill_execution_timeout`（默认 30 秒），超时后返回 `SkillResult(success=False)` 及友好提示
- **环境上下文注入**：`run_live_message` / `arun_live_message` 新增 `environment_context` 参数，允许外部注入群组信息、渠道上下文等附加信息，自动写入系统提示词的 `<environment_context>` 段
- **SKILL 编写指南**：新增 `docs/skill-authoring.md`，提供 AI 友好的 SKILL 开发模板与规范
- **外部调用同步指南**：新增 `docs/integration-sync-guide.md`，供 AI 编码助手在变更接口后快速同步外部调用
- 新增 13 个测试覆盖超时、环境上下文、提示词紧凑格式

## [0.7.0] - 2026-04-09

### Added
- **SKILL 系统**：AI 可在运行时调用外部 Python 代码的扩展机制
  - `sirius_chat/skills/models.py`：SkillDefinition、SkillParameter、SkillResult 数据模型
  - `sirius_chat/skills/registry.py`：从 `{work_path}/skills/` 自动发现并加载 SKILL 文件
  - `sirius_chat/skills/executor.py`：参数校验、类型转换和安全执行
  - `sirius_chat/skills/data_store.py`：每个 SKILL 独立的 JSON 持久化键值存储
  - `OrchestrationPolicy` 新增 `enable_skills`、`skill_call_marker`、`max_skill_rounds` 配置
  - 引擎通过 `[SKILL_CALL: name | {params}]` 提示词驱动机制检测和执行 SKILL 调用
  - 持久化数据通过 `data_store` 参数自动注入 SKILL 的 `run()` 函数
  - 新增示例 SKILL：`examples/skills/system_info.py`
  - 新增 50 个 SKILL 系统专项测试

## [0.6.0] - 2026-04-08

### Breaking Changes
- **MemoryFact 模型重构**
  - 删除 `is_transient` 字段，改为 `is_transient(threshold=0.85)` 动态方法
  - 删除 `created_at` 字段，统一使用 `observed_at`
  - 新增 `__post_init__` 自动钳位 confidence 到 [0.0, 1.0]
- **衰退曲线更新**：`MemoryForgetEngine.DEFAULT_DECAY_SCHEDULE` 更为激进（180天: 0.20→0.05）

### Added
- **MemoryPolicy 集中配置** (`OrchestrationPolicy.memory`)
  - `max_facts_per_user`：每用户最大记忆条目数（默认50）
  - `transient_confidence_threshold`：RESIDENT/TRANSIENT 分界线（默认0.85）
  - `event_dedup_window_minutes`：事件去重窗口（默认5分钟）
  - `max_observed_set_size`：observed_* 集合大小上限（默认100）
  - `max_summary_facts_per_type`：摘要每类型限制（默认5）
  - `decay_schedule`：可配置衰退时间表
- **MemoryFact 富上下文字段**
  - `mention_count`：去重提频计数
  - `source_event_id`：事件来源追踪
  - `context_channel` / `context_topic`：渠道与主题上下文
  - `observed_time_desc`：人类友好时间描述
- **UserMemoryManager 增强**
  - `add_memory_fact()` 自动去重提频（同 fact_type+value 递增 mention_count）
  - `get_resident_facts()` / `get_transient_facts()` 支持自定义 threshold
  - `get_rich_user_summary()` 支持 `max_facts_per_type` 限长
  - `apply_event_insights()` 支持 `source_event_id`，observed_* 集合自动 cap
- **序列化完整性**：UserMemoryFileStore 从 5 字段升级到 12 字段，向后兼容旧格式
- **apply_decay 自定义 schedule**：`MemoryForgetEngine.apply_decay()` 新增 `decay_schedule` 参数
- 新增迁移文档 `docs/migration-memory-v2.md`
- 新增 26 个记忆系统 V2 专项测试

### Changed
- `message_debounce_seconds` 默认值从 0.0 调整为 5.0

### Fixed
- 修复 `_cap_set()` 方法内残余的重复代码块导致 `NameError: event_features`
- 修复 `test_run_live_session_reply_runtime_persists_across_calls` 未显式设置 debounce 导致的测试失败

## [0.5.11] - 2026-04-07

### Changed
- 引擎层 provider 调用新增超时兜底，避免上游请求长时间阻塞导致消息处理卡住
- orchestration 配置日志增加去重，同一配置不再在每条消息重复打印“多模型协同（方案2）”

### Test
- 新增回归测试，验证 memory_extract 超时不会阻塞 live message 执行

## [0.5.10] - 2026-04-07

### Changed
- **用户画像提取器上下文增强**
  - `memory_extract` 不再只解析单句，改为携带最近聊天上下文
  - 输入中会包含最新用户消息、最近用户/助手对话片段，帮助模型更准确推断画像

### Removed
- **事件提取时间窗口去重**
  - 去掉 `event_extract` 的短时间去重跳过逻辑
  - 连续消息会按并行流程正常触发事件提取

### Test
- 新增回归测试，验证用户画像提取请求包含最近聊天上下文
- 新增回归测试，验证连续消息不会被 event_extract 去重跳过

## [0.5.9] - 2026-04-07

### Changed
- **chat_main system 消息注入策略调整**
  - 将 transcript 内的 `system` 消息统一合并到首个 `system_prompt`
  - `chat_main` 的 `messages` 不再携带中途 `role=system` 历史项
  - 保留内部系统信息语义，同时降低模型对中途 system 行的复述倾向

### Test
- 新增回归测试，验证第二轮 `chat_main` 请求中：
  - `messages` 无 `role=system`
  - `system_prompt` 包含“会话内部系统补充”与事件说明

## [0.5.8] - 2026-04-07

### Added
- **下游安全消息提取 API**
  - 新增 `extract_assistant_messages(transcript, since_index=0)`，用于只提取 assistant 消息下发
  - 更新示例代码，避免将 system 内部说明误发到聊天渠道
- **OpenAI provider 测试覆盖补齐**
  - 新增 `tests/test_openai_compatible_provider.py`

### Changed
- **Provider 响应解析兼容性增强**
  - 新增统一解析工具 `providers/response_utils.py`
  - `openai_compatible` / `siliconflow` / `deepseek` / `volcengine_ark` 统一支持结构化 `content`
  - 支持 `refusal` / `output_text` 等字段回退，减少误判“响应为空”
- **Provider DEBUG 日志增强**
  - 在 DEBUG 级别新增“模型原始响应 raw”日志，便于线上排障

## [0.5.7] - 2026-04-07

### Added
- **DeepSeek provider 适配**
  - 新增 `DeepSeekProvider`，默认基地址 `https://api.deepseek.com`
  - 兼容传入 `https://api.deepseek.com/v1` 的 base_url 规范化
  - 支持 `reasoning_content` 回退解析
- **DeepSeek 示例配置**
  - 新增 `examples/session.deepseek.json`，可直接用于 DeepSeek 快速接入

### Changed
- **Provider 路由增强**
  - 自动路由新增 `deepseek` 平台与模型前缀识别
  - 支持平台清单增加 `deepseek`

### Docs
- 更新 README、架构文档、外部接入文档与相关 SKILL，补充 DeepSeek 使用方式

## [0.5.6] - 2026-04-06

### Added
- **辅助任务并行执行**
  - 单条用户消息处理中，`memory_extract`、`multimodal_parse`、`event_extract` 改为并行调度
  - 保留 `memory_manager` 后置执行，确保汇聚阶段读取的是已更新记忆
  - 新增并行回归测试，验证单轮内辅助任务存在重叠执行

### Changed
- **点名回复概率增强**
  - 在 `session_reply_mode=auto` 下，明确点名主 AI 时提高概率兜底下限
  - 降低“被叫到但未回复”的体验问题

### Docs
- 更新 `OrchestrationPolicy` 文档与示例，使字段说明与实现保持一致
- 修正文档中对 `orchestration.enabled` 的过时描述

## [0.5.5] - 2026-04-06

### Added
- **auto 回复概率决策日志增强**
  - 新增 `[会话] 触发回复` 日志，输出 `trigger`（`threshold` 或 `probability_fallback`）
  - 输出 `score`、`threshold`、`probability`、`roll`，便于在线调参与回放分析

### Changed
- **意愿系统概率兜底补齐**
  - 在分数未过阈值时，按 `auto_reply_probability_coefficient` 与 `auto_reply_probability_floor` 计算兜底回复概率
  - 保持 `session_reply_mode=auto` 下的长期参与性，降低连续沉默概率

## [0.5.4] - 2026-04-06

### Added
- **日志基础信息增强**
  - 在模型调用 INFO 日志中新增 `调用目的`、`预计输入Token`、`预计总Token上限`
  - 引入统一估算函数 `estimate_generation_request_input_tokens()` 用于请求输入 token 粗估

### Changed
- **GenerationRequest 扩展**
  - 新增 `purpose` 字段（默认 `chat_main`），支持多场景调用意图标识
  - 已在核心调用链路补充 purpose：
    - `chat_main`
    - `memory_extract` / `multimodal_parse` / `event_extract` / `memory_manager`
    - `roleplay_prompt_generation`
    - `event_memory_verification`
    - `provider_healthcheck`

### Test Results
- 278 个测试通过 ✅
- 1 个测试被跳过
- 0 个测试失败 ✅

## [0.5.3] - 2026-04-06

### Fixed
- **前移到提示词层的元信息防泄漏策略**
  - `build_system_prompt()` 对参与者记忆注入增加“反结构化复述”约束
  - 明确记忆仅供语义理解，不应影响回复的字段结构、分隔符和顺序
  - 输出边界约束继续禁止复述内部记忆元信息

- **元信息清洗规则增强**
  - 对模型输出中的内部元信息行继续做清洗兜底
  - 兼容字段乱序、字段缺失、中文/英文标签变体

### Changed
- **模型调用日志分级优化**
  - INFO 仅保留模型名、温度、token 上限、消息数、响应字数
  - DEBUG 输出完整输入输出，便于排查问题

### Test Results
- 278 个测试通过 ✅
- 1 个测试被跳过
- 0 个测试失败 ✅

## [0.5.2] - 2026-04-06

### Fixed
- **防止内部记忆元信息外泄到用户回复**
  - 在系统提示词中新增输出边界约束，明确禁止输出内部记忆元字段（置信度/类型/来源/时间/内容）
  - 在引擎回复落地前增加清洗逻辑，过滤结构化元信息泄漏行
  - 对过滤后为空的极端情况提供安全回退回复

### Changed
- **模型调用日志分级优化**
  - INFO 仅保留基础信息（模型名、温度、token 上限、消息数、响应字数）
  - DEBUG 输出完整模型输入（system_prompt + messages）和完整模型输出（不截断）
  - 覆盖 provider：openai-compatible / siliconflow / volcengine-ark / mock

### Test Results
- 276 个测试通过 ✅
- 1 个测试被跳过
- 0 个测试失败 ✅

## [0.5.1] - 2026-04-06

### Added
- **动态模型路由配置 API**：新增灵活的多模态模型配置方式
  - `create_agent_with_multimodal(...)` 便捷构造函数
  - `auto_configure_multimodal_agent(agent, multimodal_model=...)` 灵活参数化配置
  - 手动配置：直接设置 `agent.metadata["multimodal_model"]`
  - 透明的自动路由：无多媒体内容使用廉价模型，有多媒体自动升级至指定的多模态模型

### Changed
- **提示词生成器大幅优化** (sirius_chat/roleplay_prompting.py)
  - 精简拟人问题从 17 个核心到 8 个高质量问题，提高信号强度
  - 每个问题添加详细的 hints 字段，为回答者提供更清晰的引导
  - Agent 基础信息（name、alias、model、temperature、max_tokens）现在被精确传送至 LLM
  - 补充信息（background、alias）权重强化，单独作为【补充信息】块呈现
  - 重写 LLM 指令和输出规范，明确 agent_persona 与 global_system_prompt 的职责差异
  - 系统提示词生成时自动包含安全约束，防止 AI 泄露系统提示词

- **文档和 SKILL 同步更新**
  - docs/external-usage.md：新增 Agent 多模态配置的详细说明和三种配置方法示例
  - docs/architecture.md：新增动态模型路由的设计原理和使用指导
  - .github/skills/external-integration/SKILL.md：补充多模态模型配置建议
  - .github/skills/framework-quickstart/SKILL.md：补充动态模型路由设计说明

### Test Results
- 274 个测试通过 ✅
- 1 个测试被跳过
- 0 个测试失败 ✅

---

## [Unreleased - Previous]

### Changed
- **API隔离迁移完成** (Stage 1-4)：将单体模块分解为逻辑清晰的独立子包
  - `sirius_chat/config/`：配置管理（models.py, manager.py, helpers.py, __init__.py）
  - `sirius_chat/core/`：核心编排引擎（engine.py, __init__.py）
  - `sirius_chat/memory/`：统一记忆系统（user/, event/, quality/ 子模块）
    * `memory/user/`：用户档案与记忆管理（models.py, manager.py, store.py）
    * `memory/event/`：事件记忆系统（models.py, manager.py, store.py）
    * `memory/quality/`：记忆质量评估与智能遗忘（models.py, tools.py）
  - `sirius_chat/models/`：数据模型与结构定义（models.py, __init__.py）
  - `sirius_chat/session/`：会话管理与持久化（runner.py, store.py, __init__.py）
  - `sirius_chat/token/`：Token管理与使用统计（usage.py, utils.py, __init__.py）
- **删除所有弃用的包装文件**：
  - `config_manager.py`（使用 `from sirius_chat.config import ConfigManager`）
  - `orchestration_config.py`（使用 `from sirius_chat.config import configure_*`）
  - `user_memory.py`（使用 `from sirius_chat.memory import UserMemoryManager`）
  - `event_memory.py`（使用 `from sirius_chat.memory import EventMemoryManager`）
  - `async_engine/core.py`（使用 `from sirius_chat import AsyncRolePlayEngine`）
  - `memory_quality.py` / `memory_quality_tools.py`（使用 `from sirius_chat.memory.quality import *`）
- **更新所有导入路径**：20+ 个源文件和文档已升级到新的导入路径
- **清理过时设计文档**：
  - 删除 C2C3_ARCHITECTURE_DESIGN.md、C2C3_IMPLEMENTATION_COMPLETE.md
  - 删除 PERFORMANCE_OPTIMIZATION_PLAN.md、PERFORMANCE_OPTIMIZATION_IMPLEMENTATION.md
  - 统一文档维护在 docs/architecture.md 而非独立设计文档

### Fixed
- **删除过时和冗余的测试**：
  - test_event_user_memory_integration.py：移除3个调用不存在方法的测试
  - test_api_integrity.py：移除测试已删除弃用导入的测试
  - sirius_chat/core/engine.py：移除调用不存在的 `interpret_event_with_user_context()` 的代码
- **修复test_orchestration_config.py**：更新导入从 `async_engine.orchestration_config` 到新的模块位置

### Test Results
- 256 个测试通过 ✅
- 1 个测试被跳过
- 0 个测试失败 ✅

---

## [Unreleased]

### Added
- **logging_config.py**: 生产级日志系统，支持JSON结构化输出、彩色控制台格式、日志文件循环
  - JSONFormatter：输出机器可读的JSON日志
  - ColoredFormatter：ANSI彩色的人类友好日志
  - configure_logging()：集中配置函数，支持DEBUG/INFO/WARNING/ERROR级别
- **exceptions.py**: 语义化的异常体系（18个自定义异常类）
  - 基础：SiriusException（error_code, context, is_retryable）
  - 分类：ProviderError, TokenError, ParseError, ConfigError, MemoryError
  - 特性：上下文信息、可重试标记、序列化支持
- **token_utils.py**: 多语言感知的Token估算工具
  - estimate_tokens_heuristic()：中英文感知估算（中文1字=1token, 英文4字=1token）
  - estimate_tokens_with_tiktoken()：可选的精确计数（若安装tiktoken）
  - estimate_tokens()：智能回退实现（优先tiktoken，降级启发式）
  - 支持多个模型配置（gpt-4, claude-3, doubao-seed等）
- **cli_diagnostics.py** (P0-005): CLI 诊断和环境检查工具
  - EnvironmentDiagnostics：Python版本、工作目录、配置文件、Provider配置检查
  - run_preflight_check()：启动前全面检查，给出详细建议
  - generate_default_config()：生成默认配置文件模板
- **Provider 中间件系统** (P1-003)：可组合的Provider功能框架
  - `sirius_chat/providers/middleware/base.py`：Middleware ABC、MiddlewareContext、MiddlewareChain
  - `sirius_chat/providers/middleware/rate_limiter.py`：RateLimiterMiddleware（固定窗口）、TokenBucketRateLimiter（令牌桶算法）
  - `sirius_chat/providers/middleware/retry.py`：RetryMiddleware（指数退避）、CircuitBreakerMiddleware（故障转移）
  - `sirius_chat/providers/middleware/cost_metrics.py`：CostMetricsMiddleware（成本计量与追踪）
  - 支持链式添加中间件，支持异步请求/响应处理
- **async_engine 包重构** (P0-003 Phase 1-2)：将单个 async_engine.py 模块分解为多模块包
  - `sirius_chat/async_engine/core.py`：核心 AsyncRolePlayEngine 类，保持公开 API 不变
  - `sirius_chat/async_engine/utils.py` (120+ 行)：工具函数模块
    * build_event_hit_system_note()：事件记忆命中提示生成
    * record_task_stat()：任务统计记录
    * estimate_tokens()：Token 计算 (cheap heuristic)
    * extract_json_payload()：JSON 有效载荷提取
    * normalize_multimodal_inputs()：多模态输入规范化和验证
  - `sirius_chat/async_engine/prompts.py` (90+ 行)：系统提示构建
    * build_system_prompt()：生成完整系统提示，整合agent身份、用户记忆、时间上下文
  - `sirius_chat/async_engine/orchestration.py` (90+ 行)：任务编排配置和管理
    * 任务常量定义 (TASK_MEMORY_EXTRACT, TASK_MULTIMODAL_PARSE 等)
    * TaskConfig dataclass：任务配置管理
    * get_task_config()：从 SessionConfig 提取任务配置
    * get_system_prompt_for_task()：获取任务系统提示
- **事件系统与用户记忆系统的双向适配** (方案C)：
  - `UserRuntimeState` 扩展：支持 observed_keywords/observed_roles/observed_emotions/observed_entities 集合
  - `ContextualEventInterpretation` 新数据类：事件与用户历史的对齐度评分与上下文理解
  - `UserMemoryManager.apply_event_insights()`：将事件特征自动转化为用户记忆事实
    * emotion_tags → emotional_pattern 事实 (信度 base - 0.05)
    * keywords → user_interest 事实 (信度 base - 0.10)
    * role_slots → social_context 事实 + 自动特征提升 (信度 base - 0.05)
    * entities → observed_entities 集合
  - `UserMemoryManager.interpret_event_with_user_context()`：基于用户历史调整事件理解
    * 计算四维对齐度：keyword_alignment / role_alignment / emotion_alignment / entity_alignment
    * 动态信度调整：`adjusted_confidence = 0.65 + avg_alignment × 0.3`，范围 [0.5, 1.0]
    * 推荐处理类别：high_confidence(avg>0.6) | normal | low_relevance(avg<0.2) | pending(新用户)
  - 在 async_engine._add_human_turn() 中集成新流程：事件提取 → apply_event_insights() → interpret_event_with_user_context() → event_context_note
  - 实现**双向观测管道**：事件特征 → 用户理解 + 用户历史 → 事件信度调整
  - 提升事件特征转化率：0% → 95%+，真正构建统一的用户心智模型
  - 新增9个集成测试，验证情感、关键词、角色、对齐度、序列化等能力

### Changed
  - `.github/workflows/ci.yml`：GitHub Actions 工作流，支持多版本 Python (3.10, 3.11, 3.12) 测试、代码质量检查、安全扫描、构建验证
  - `.pre-commit-config.yaml`：预提交钩子配置（black, isort, flake8, mypy, bandit, yamllint 等）
  - `scripts/ci_check.py`：本地/CI 代码质量检查脚本（格式、lint、类型、测试、覆盖率）
  - `scripts/setup_dev_env.py`：开发环境自动初始化脚本
  - `Makefile`：便捷的开发命令集（format, lint, typecheck, test, build 等）
- **PROJECT_ISSUES.md**: 项目问题与改进方向追踪文档
  - P0（5项）、P1（4项）、P2（4项）优先级划分
  - 3个月 roadmap 与进度矩阵
- **集成测试框架** (P1-001)：
  - `tests/integration/`：网络弹性、并发会话、故障转移测试
  - `tests/benchmarks/`：性能吞吐量、延迟、可扩展性基准测试
  - `conftest.py`：MockLLMProvider、临时目录、会话配置等通用fixtures
- commit-preparation SKILL：commit前检查清单，包括gitignore验证、改动总结、ChangeLog更新与标准格式commit
  - P0（5项）、P1（4项）、P2（3项）优先级划分
  - 3个月roadmap与进度矩阵
- **集成测试框架** (P1-001)：
  - `tests/integration/`：网络弹性、并发会话、故障转移测试
  - `tests/benchmarks/`：性能吞吐量、延迟、可扩展性基准测试
  - `conftest.py`：MockLLMProvider、临时目录、会话配置等通用fixtures
- commit-preparation SKILL：commit前检查清单，包括gitignore验证、改动总结、ChangeLog更新与标准格式commit

### Changed
- **pyproject.toml**: 显式声明可选依赖groups
  - provider：httpx>=0.24.0, tenacity>=8.0.0
  - dev：测试、linting、类型检查工具
  - quality：tiktoken用于精确token估算
- **__init__.py**: 扩展导出至57个项目，分类组织
  - 核心模型(10), 会话管理(7), Provider(3), API函数(13), 日志(2), 异常(18)
- **main.py** (P0-005): 改进CLI错误处理和诊断
  - 添加 --init-config 命令生成默认配置
  - 添加 --check-config 命令进行环境检查
  - 整合日志系统用于审计和调试
  - 改进异常捕获和错误消息详细度
  - 添加 KeyboardInterrupt 处理

### Changed

### Fixed

### Deprecated

---

## [0.1.0] - 2026-04-05

### Added

#### 核心框架
- 多人角色扮演编排引擎（`AsyncRolePlayEngine`）
- 支持"多人用户 + 单AI主助手"交互模式
- 结构化会话与记录系统（`SessionConfig`, `Transcript`）

#### LLM Provider支持
- OpenAI 兼容接口适配（`openai_compatible.py`）
- SiliconFlow 专用适配（`siliconflow.py`，默认基地址 `https://api.siliconflow.cn`）
- 火山方舟 Ark 专用适配（`volcengine_ark.py`，默认基地址 `https://ark.cn-beijing.volces.com/api/v3`）
- Provider 自动路由（按模型前缀匹配）

#### 用户记忆系统（Phase 1）
- 用户档案与运行时状态管理（`UserProfile`, `UserRuntimeState`）
- 结构化记忆事实存储（`MemoryFact`），支持分类、验证、冲突检测
- 事件记忆管理（`EventMemoryManager`），支持事件命中评分
- 用户识别与身份索引（支持跨渠道同人识别）

#### 记忆质量评估与智能遗忘（Phase 2）
- 记忆质量评估模块（`MemoryQualityAssessor`）：
  - 多维度评分：置信度(50%) + 活跃度(30%) + 验证状态(15%)
  - 非线性活跃度评分：按年龄划分(0-7天/7-30天/30-90天/>90天)五等级
  - 用户行为一致性分析：身份/偏好/情感/事件四维度评分
- 智能遗忘引擎（`MemoryForgetEngine`）：
  - 时间衰退表：{7: 0.95, 30: 0.85, 60: 0.70, 90: 0.50, 180: 0.20}
  - 自动清理规则：极低置信+陈旧 / 冲突+低置信+极旧 / 低质量+陈旧
  - 冲突记忆加速衰退（额外乘以0.7）
- CLI工具（`memory_quality_tools.py`）：
  - 子命令：analyze/cleanup/decay/all
  - JSON报告导出与控制台展示
  - 完整argparse集成

#### 编排策略与多模态处理
- 任务级编排系统（`memory_extract`, `event_extract`, `multimodal_parse`, `memory_manager`）
- Token 预算控制与限流裁剪
- 遵循 `OrchestrationPolicy` 配置

#### CLI与API接口
- 脚本式CLI（`sirius-chat` 命令）
- Python 库式接口（`.api` 模块化facade）
- 会话配置加载与持久化（JSON + `JsonSessionStore`）

#### 开发工具与文档
- Framework Quickstart SKILL：快速架构理解
- External Integration SKILL：外部接入指南
- Skill Sync Enforcer SKILL：代码与文档联动检查
- Release Checklist SKILL：发布前检查清单
- Commit Preparation SKILL：commit前检查清单
- 完整架构文档（`docs/architecture.md`）
- 外部使用指南（`docs/external-usage.md`）
- 编排策略详解（`docs/orchestration-policy.md`）

#### 测试覆盖
- 综合单元测试（79个测试用例）
- 记忆质量系统测试（8个新增测试）

### Changed

### Fixed

### Deprecated

---

## 版本优先级

### 发布约定
- 主版本号(Major)：重大架构变更或破坏性API改动
- 次版本号(Minor)：新增功能或向后兼容的改动
- 修订号(Patch)：问题修复与性能优化

### 标签命名
- 格式：`v{Major}.{Minor}.{Patch}`
- 示例：`v0.1.0`, `v0.2.0`, `v1.0.0`

---

## 如何贡献

提交前请：
1. 检查 `.gitignore` 覆盖范围（隐私文件、运行时缓存）
2. 总结改动内容
3. 更新此 CHANGELOG.md（在 `[Unreleased]` 部分记录）
4. 按 Conventional Commits 规范提交（中文信息，包含type和scope）

详见：`.github/skills/commit-preparation/SKILL.md`
