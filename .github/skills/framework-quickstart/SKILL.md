---
name: framework-quickstart
description: "当你需要在不通读全部代码的情况下快速理解 Sirius Chat 架构时使用，包括模块边界、执行流与扩展点。关键词：架构总览、框架地图、修改位置、provider 集成。"
---

# 框架快速上手

## 目标

在开始修改前，快速建立对 Sirius Chat 的准确整体认知。

补充目标：本项目致力于构建具有真实情感表达、能提供帮助与情绪价值的核心引擎。

## 语言规范

- 本仓库所有 SKILL 文件必须使用中文编写。
- 后续新增或修改任意 SKILL 时，frontmatter 的 `description` 与正文均需使用中文。
- 若发现历史 SKILL 出现英文内容，需在当前任务中一并改为中文。

## 阅读顺序（先做这个）

1. `docs/architecture.md`
2. `docs/full-architecture-flow.md`
3. `README.md`
4. `docs/orchestration-policy.md`
5. `sirius_chat/models/models.py` ✨ (包重构)
6. `sirius_chat/async_engine/core.py` ✨ (P0-003 重构)
7. `sirius_chat/async_engine/prompts.py` ✨
8. `sirius_chat/async_engine/utils.py` ✨
9. `sirius_chat/async_engine/orchestration.py` ✨
10. `sirius_chat/config_manager.py` ✨ (P1-006 配置管理)
11. `sirius_chat/providers/base.py`
12. `sirius_chat/providers/middleware/base.py` ✨
13. `sirius_chat/providers/middleware/rate_limiter.py` ✨
14. `sirius_chat/providers/middleware/retry.py` ✨
15. `sirius_chat/providers/middleware/cost_metrics.py` ✨
16. `sirius_chat/cache/` ✨ (P2-001 缓存框架)
18. `sirius_chat/skills/` ✨ (SKILL系统)
19. `sirius_chat/providers/mock.py`
18. `sirius_chat/providers/openai_compatible.py`
19. `sirius_chat/providers/siliconflow.py`
20. `sirius_chat/providers/volcengine_ark.py`
21. `sirius_chat/providers/deepseek.py`
22. `sirius_chat/providers/routing.py`
23. `sirius_chat/user_memory.py`
23. `sirius_chat/performance/` ✨ (P2-002 性能监控)
24. `sirius_chat/cli.py`
25. `sirius_chat/api/`
26. `tests/test_engine.py`

## 心智模型

- `models/models.py` ✨ **（包重构）** 定义数据契约（多人用户 + 单 AI 主助手）。
- `OrchestrationPolicy` 用于任务路由与预算控制，**现已默认启用**（`enabled=True`），支持 `memory_extract`、`event_extract`、`multimodal_parse`、`memory_manager` 等任务的模型配置与预算限制。若需回退单模型模式，设置 `enabled=False`。同时支持提示词驱动的内容分割（`enable_prompt_driven_splitting=True`）。✨ `memory_manager` 是新增的可选 LLM 任务，用于汇聚、去重、标注、冲突检测记忆。
- ✨ **async_engine 包重构** (P0-003)：将 924 行单文件分解为多个职责明确的模块
  - `async_engine/core.py`：核心 AsyncRolePlayEngine 类，保持公开 API 不变
  - `async_engine/prompts.py`：系统提示词构建（整合 agent 身份、时间、用户记忆、编排指令）
  - `async_engine/utils.py`：工具函数（token 估算、JSON 提取、多模态输入规范化等）
  - `async_engine/orchestration.py`：任务编排配置和管理（TaskConfig、任务常量、系统提示模板）
  - 模块分解后每个文件 < 200 行，关注点明确，可独立测试和维护
- `async_engine.py` 的核心实现已迁移到 `async_engine/core.py`，旧导入位置仍然可用（向后兼容）。系统提示词生成时自动包含安全约束，防止 AI 主动泄露系统提示词。
- ✨ **动态模型路由**：支持根据输入内容自动在不同模型间切换，以平衡成本与能力
  - 在 `Agent.metadata["multimodal_model"]` 中配置多模态专用模型（如 `"gpt-4o"`）
  - 无多媒体数据时使用 `Agent.model`（廉价文本模型，如 `"gpt-4o-mini"`）
  - 检测到多媒体数据时自动升级至 `agent.metadata["multimodal_model"]`
  - 提供便捷配置：`create_agent_with_multimodal(...)` 一次性创建，或 `auto_configure_multimodal_agent(...)` 灵活配置
- `run_live_session` 负责会话初始化；动态参与者与逐条消息处理通过 `run_live_message` 完成。
- `run_live_message` 新增 `environment_context: str = ""` 参数（v0.8.0），允许外部注入环境信息（群名、在线人数等），自动写入系统提示词 `<environment_context>` 段。
- `Message.reply_mode` 可按消息控制回复策略：`always`（默认）/`never`（仅写入记忆与 transcript）/`auto`（自动推断是否回复）。
- 推荐在实时流式接入时使用 `run_live_message` 逐条处理消息；`run_live_session(...)` 用于一次性会话初始化。
- `run_live_message` 默认使用会话级 `session_reply_mode`，将回复策略从消息级提升为 session 级。
- `reply_mode=auto` 已升级为多维意愿分系统，参数由 `OrchestrationPolicy` 提供：`auto_reply_user_cadence_seconds`、`auto_reply_group_window_seconds`、`auto_reply_group_penalty_start_count`、`auto_reply_assistant_cooldown_seconds`、`auto_reply_threshold`、`auto_reply_threshold_boost_start_count` 等。
- `run_live_session` 的节奏临时状态已挂载到 `Transcript.reply_runtime`，跨调用复用 transcript 时可保持节奏连续性。
- `user_memory.py` 负责用户身份识别（user_id/aliases/identities）与结构化用户记忆。
- 用户记忆分为 `profile`（初始化字段）与 `runtime`（运行时可变字段）。
- `runtime.memory_facts` 是结构化分类记忆，每个记忆包含：
  - `fact_type`、`value`、`source`（来源：memory_extract/event_extract/multimodal_parse/memory_manager）、`confidence`
  - `memory_category`（分类：identity/preference/emotion/event/custom）、`validated`（验证标记）、`conflict_with`（冲突列表）
  - V2 新增：`mention_count`（去重提频）、`source_event_id`（事件来源）、`context_channel`/`context_topic`（渠道/主题）
  - V2 变更：`is_transient` 从字段改为方法 (`fact.is_transient(threshold)`)，`created_at` 已移除（使用 `observed_at`）
- ✨ V2 新增 `MemoryPolicy`（在 `OrchestrationPolicy.memory` 中配置）：集中管理记忆阈值、衰退曲线、集合上限、摘要限长。
- ✨ **新架构**：启发式正则提取已舍弃（高误率）；所有 AI 推断改由 LLM 任务（memory_extract）提供，质量 confidence 0.8。
- ✨ **新增**：`memory_manager` LLM 任务可选启用，自动汇聚/去重/标注/验证所有候选记忆，置信度 0.9+。
- 引擎在运行时会主动维护 `runtime` 记忆（通过 memory_extract 等任务），用于增强拟人化连续对话。
- 系统提示中的记忆呈现改为结构化格式，按类别分组，透明展示置信度。
- 引擎在每轮用户发言后执行事件命中分析（高置信命中/弱命中/新增），并把事件说明注入 system 消息。
- 事件记忆会持久化到 `work_path/events/events.json`，用于跨会话事件连续性。
- ✨ **事件系统与用户记忆系统的双向适配**（方案C）：
  - 每条事件的特征（emotion_tags、keywords、role_slots、entities）自动转化为结构化用户记忆事实
    * `emotion_tags` → `emotional_pattern` 事实（信度 base - 0.05）
    * `keywords` → `user_interest` 事实（信度 base - 0.10）
    * `role_slots` → `social_context` 事实 + 自动特征提升，如检测领导角色 → 推断 `leadership_tendency`
  - 基于用户历史调整事件理解（`interpret_event_with_user_context()`）
    * 计算四维对齐度：keyword_alignment, role_alignment, emotion_alignment, entity_alignment
    * 动态信度调整：`adjusted_confidence = 0.65 + avg_alignment × 0.3`，范围 [0.5, 1.0]
    * 推荐处理类别：high_confidence (avg>0.6) | normal | low_relevance (avg<0.2) | pending (新用户)
  - 实现真正的**双向观测**：事件不再被单向消费，而是成为用户理解的重要信号源
- `Transcript.find_user_by_channel_uid(channel, uid)` 支持按渠道+外部 UID 直接定位用户。
- `session/store.py` ✨ **（包重构）** 提供会话持久化与重启恢复（`SessionStore`、`JsonSessionStore`、`SqliteSessionStore`）。
- `session/runner.py` ✨ **（包重构）** 提供上层封装的会话运行器（`JsonPersistentSessionRunner`），自动维护用户档案与持久化。
- `Transcript.token_usage_records` 全量归档每次模型调用的 token 消耗信息（通过 `token/usage.py` 提供的 `summarize_token_usage` 与 `build_token_usage_baseline` 汇总）。
- `token/utils.py` ✨ **（包重构）** 提供 Token 估算工具（启发式估算、Tiktoken 精确计算、统计辅助函数）。
- 引擎支持自动记忆压缩（`session_summary` + 历史预算）。
- ✨ **配置管理** (P1-006)：`config_manager.py` 提供多环境配置管理能力
  - 支持 JSON 配置文件加载（base/dev/test/prod）
  - 支持环境变量替换（${VAR_NAME} 语法）
  - ConfigManager 类提供加载、合并、验证等核心功能
- ✨ **缓存框架** (P2-001)：`cache/` 模块提供可扩展的缓存后端
  - `base.py`：CacheBackend 抽象基类定义标准接口
  - `memory.py`：MemoryCache 内存缓存实现，支持 LRU 和 TTL 过期
  - `keygen.py`：有确定性的缓存 key 生成函数
- ✨ **性能监控** (P2-002)：`performance/` 模块提供性能分析和优化工具
  - `metrics.py`：ExecutionMetrics 和 MetricsCollector 用户执行指标收集
  - `profiler.py`：PerformanceProfiler 上下文管理器和 @profile_sync/@profile_async 装饰器
  - `benchmarks.py`：Benchmark 和 BenchmarkSuite 用于性能基准测试
- ✨ **SKILL系统**：`skills/` 模块允许 AI 通过 `[SKILL_CALL: name | {params}]` 调用外部代码
  - `models.py`：SkillDefinition、SkillParameter、SkillResult 数据模型
  - `registry.py`：从 `{work_path}/skills/` 自动发现和加载 SKILL 文件，并自动补齐目录与 `README.md` 引导文件
  - `executor.py`：参数校验、类型转换和安全执行，支持 `skill_execution_timeout`（默认 30 秒）
  - `dependency_resolver.py`：加载前自动检测并安装缺失的第三方依赖（`uv pip install`，回退 `pip`）
  - `data_store.py`：每个 SKILL 独立的 JSON 持久化存储（`{work_path}/skill_data/`）
  - 默认：`OrchestrationPolicy.enable_skills=True`；即使显式关闭，`run_live_session` 仍会先创建 `{work_path}/skills/`
  - SKILL 文件需导出 `SKILL_META` 字典和 `run(**kwargs)` 函数
  - `SKILL_META["dependencies"]`：可选显式声明第三方包名列表，框架自动安装
  - 持久化数据通过 `data_store` 参数自动注入到 `run()` 中
- ✨ **意图分析** (`core/intent.py`)：分析用户消息意图（question/request/chat/reaction/information_share/command），优化回复意愿评分与系统提示词段落。LLM 路径通过 `enable_intent_analysis=True` 启用；默认使用零开销关键词回退路径。
- ✨ **后台任务** (`background_tasks.py`)：轻量级 asyncio 定时循环管理器，支持记忆压缩、临时清理和记忆归纳三类后台任务。记忆归纳循环定时调用 LLM 合并冗余事件/摘要/事实。
- `providers/base.py` 定义 provider 协议。
- `providers/middleware/` 是 Provider 功能扩展层（✨ 新增 P1-003）：
  - `base.py`：Middleware ABC，支持链式组合
  - `rate_limiter.py`：固定窗口和令牌桶速率限制
  - `retry.py`：指数退避重试和断路器保护
  - `cost_metrics.py`：成本计量和追踪
  - 中间件通过 MiddlewareChain 串联，可透明地为任意 provider 添加流控、重试、监控等功能
- `providers/mock.py` 提供可复现的本地测试能力。
- `providers/*` 实现具体的 LLM 后端。
- `roleplay_prompting.py` 提供自动问题清单、回答提取式提示词生成、人格持久化与人格选择能力。
- 内置 provider 包含 `OpenAICompatibleProvider`、`DeepSeekProvider`、`SiliconFlowProvider` 与 `VolcengineArkProvider`。
- 若配置了多 provider，`AutoRoutingProvider` 会按模型前缀自动选择可用 provider。
- `cli.py` 是库内薄封装，仅负责调用 `api` 执行单轮会话。
- `api/` 是统一对外接口文件；外部调用优先使用该文件暴露的 API。
- Provider 检测流程已下沉到 `providers/routing.py`：配置检查 -> 平台适配检查 -> 可用性检查（依赖 `healthcheck_model`）。
- Provider 注册命令要求显式提供检测模型：`/provider add <type> <api_key> <healthcheck_model> [base_url]`。
- 提示词流程：`generate_humanized_roleplay_questions` 产出问题，`agenerate_agent_prompts_from_answers`（输入 `agent_name`）生成完整 `GeneratedSessionPreset`；推荐将生成结果作为 agent 资产持久化（`generated_agents.json`），再通过 `select_generated_agent_profile` + `create_session_config_from_selected_agent` 按 key 创建会话配置。
- 内部实现允许重构；当前未发布阶段若影响外部接口，可直接升级 `api/`，并同步文档与示例。
- 内部新增能力需同步在 `api/` 提供对外入口。
- `main.py` 是仓库级测试/业务入口，承载主用户档案初始化、provider 管理命令与持续会话流程。
- ✨ **开发工具链** (P1-004)：
  - `.github/workflows/ci.yml`：GitHub Actions 多版本 Python 自动化测试与代码质量检查
  - `.pre-commit-config.yaml`：预提交钩子 (black, isort, flake8, mypy, bandit 等)
  - `scripts/ci_check.py`：本地/CI 检查脚本
  - `scripts/setup_dev_env.py`：开发环境自动化初始化
  - `Makefile`：便捷开发命令集

## 修改路由指南

- 新增 provider 支持：修改 `sirius_chat/providers/`，并保持 `async_engine.py` 不含 provider 细节。
- 修改主 AI 或多人轮次策略：更新 `sirius_chat/async_engine.py`，并检查 transcript 兼容性。
- 修改动态参与者或识人记忆逻辑：同步更新 `models/models.py`、`async_engine.py` 与 `docs/external-usage.md`。
- 修改会话恢复或压缩策略：同步更新 `session/store.py`、`async_engine.py`、`README.md` 与 `docs/architecture.md`。
- 修改配置结构或环境变量处理：同步更新 `sirius_chat/config_manager.py`、`sirius_chat/cli.py`、`README.md` 与 `examples/session.json`。
- 修改缓存策略或后端：在 `sirius_chat/cache/` 实现新后端或修改现有接口，并更新 `docs/best-practices.md`。
- 修改性能监控或基准：更新 `sirius_chat/performance/` 中的指标收集或分析逻辑，添加相应测试。
- 修改 engine/provider 行为：在 `tests/` 下新增或更新测试。
- 新增可对外使用功能：在 `sirius_chat/api/` 暴露接口并补充外部调用示例。

## 代码变更后的必做同步

当架构、命令或 API 形态变化后，必须同步更新：

1. `docs/architecture.md`
2. `docs/full-architecture-flow.md`
3. `README.md`（若用户可见用法发生变化）
4. 本文件（`.github/skills/framework-quickstart/SKILL.md`）
5. 相关 SKILL 文件（`.github/skills/external-integration/SKILL.md` 等）

**重点提醒**：实现新功能后，**不应自动生成额外的 markdown 文档**来说明新功能的用法（如指南、快速启动、参考手册），除非用户明确提及。应将功能文档集中在现有位置或等待用户要求。


