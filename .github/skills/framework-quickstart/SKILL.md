---
name: framework-quickstart
description: "当你需要在不通读全部代码的情况下快速理解 Sirius Chat 架构时使用，包括模块边界、执行流与扩展点。关键词：架构总览、框架地图、修改位置、provider 集成。"
---

# 框架快速上手

## 目标

在开始修改前，快速建立对 Sirius Chat 当前代码结构的准确认知，优先搞清楚：

- 推荐入口是什么
- 真正的 engine 实现位于哪里
- workspace / config / session / provider / memory 的边界如何划分
- 哪些文件是当前架构的事实来源，哪些只是兼容层或历史迁移材料

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
3. `docs/external-usage.md`
4. `README.md`
5. `docs/orchestration-policy.md`
6. `sirius_chat/api/__init__.py`
7. `sirius_chat/workspace/layout.py`
8. `sirius_chat/workspace/runtime.py`
9. `sirius_chat/workspace/roleplay_manager.py`
10. `sirius_chat/config/models.py`
11. `sirius_chat/config/manager.py`
12. `sirius_chat/roleplay_prompting.py`
13. `sirius_chat/models/models.py`
14. `sirius_chat/core/engine.py`
15. `sirius_chat/core/chat_builder.py`
16. `sirius_chat/core/memory_runner.py`
17. `sirius_chat/core/engagement_pipeline.py`
18. `sirius_chat/core/heat.py`
19. `sirius_chat/core/intent_v2.py`
20. `sirius_chat/memory/__init__.py`
21. `sirius_chat/session/store.py`
22. `sirius_chat/providers/base.py`
23. `sirius_chat/providers/routing.py`
24. `sirius_chat/providers/middleware/base.py`
25. `sirius_chat/cli.py`
26. `tests/test_workspace_runtime.py`
27. `tests/test_engine.py`
- `models/models.py` ✨ **（包重构）** 定义数据契约（多人用户 + 单 AI 主助手）。
- `OrchestrationPolicy` 用于任务路由与任务级参数控制，支持 `memory_extract`、`event_extract`、`intent_analysis`、`memory_manager` 等任务的模型配置。`reply_mode=auto` 下的 LLM 意图分析已纳入 `intent_analysis` 任务；关闭该任务时才会走关键词回退，任务启用后若调用失败或解析失败，本轮不会再回退关键词意图推断。多 AI 群聊里，`intent_analysis` 还会区分消息是在叫当前模型自身还是其他 AI，并在后者场景下抑制当前模型自动回复；为降低误判，发给模型的上下文已改为最近交互链摘要，并会额外暴露最近 AI / 人类发言者、近期发言人的 aliases、`environment_context` 环境线索，以及当前消息命中的当前模型/其他 AI/人类名字线索。对未明确点名当前模型的群控/停用类命令，还会在 engagement 前做硬抑制。同时支持提示词驱动的内容分割（`enable_prompt_driven_splitting=True`）、基于 `pending_message_threshold` 的 runtime 积压静默批处理，以及基于 `min_reply_interval_seconds` 的最小回复间隔冷却。✨ `memory_manager` 是标准 LLM 任务，用于汇聚、去重、标注、冲突检测记忆，并为后台归纳与长上下文下的即时整理提供模型参数。
- 兼容层面，旧 `enable_intent_analysis` / `intent_analysis_model` 仅作为读取时的映射入口存在；当前模板、workspace 持久化与示例应统一使用 `task_enabled/task_models`。
- ✨ **(v0.13.0)** `OrchestrationPolicy` 新增 AI 自身记忆配置（`enable_self_memory`、`self_memory_extract_batch_size`、`self_memory_max_diary_prompt_entries`、`self_memory_max_glossary_prompt_terms`）。
## 心智模型

- 当前推荐入口是 `WorkspaceRuntime`；它负责文件布局、session 恢复、participants 写回、watcher 热刷新和 provider 注册表联动。
- `WorkspaceRuntime.run_live_message(...)` 先按 session 入队，再由单会话 processor 决定逐条处理、按 `pending_message_threshold` 执行静默批处理，或在 `min_reply_interval_seconds` 冷却窗口结束后强制合并同一说话人的连续消息再进入下一次回复判断。
- `WorkspaceRuntime.initialize()` 会预先初始化共享 SKILL runtime，并在 `skills/` 目录变化时通过 watcher 触发全量 reload，不再在消息路径按次扫描目录。
- `WorkspaceRuntime` 会把 `WorkspaceBootstrap` 的签名记入 `workspace.json`；同一份 bootstrap 只在首次命中时持久化一次，后续重启会保留用户在 config root 下的手工修改。
- `WorkspaceLayout` 是路径语义的单一事实来源：config root 放配置与资产，data root 放运行态数据。
- `AsyncRolePlayEngine` 的真实实现位于 `sirius_chat/core/engine.py`；`sirius_chat/async_engine/` 只承担兼容导出与 prompts/orchestration/utils 辅助层。
- 一个 `SessionConfig` 只对应一个主 AI，主 AI 由 `preset=AgentPreset(...)` 描述，不再推荐在外部配置里手写完整 agent prompt。
- `User` 只是 `Participant` 的别名；运行时识人与记忆的事实来源是 `transcript.user_memory`，而不是旧版 `participants` 配置字段。
- provider 注册表由 `WorkspaceProviderManager` 管理，路由顺序是 `models` 列表优先、`healthcheck_model` 次之、最后回退到第一个启用 provider。
- roleplay 资产统一存放在 `roleplay/generated_agents.json` 与 `roleplay/generated_agent_traces/`，`active_agent_key` 决定 `SessionConfig` 使用哪份资产。
- session store、token store、memory store、SKILL data store 都已经收敛到 workspace 语义下，修改这些层时必须同时检查路径文档。

## 修改路由指南

- 新增 provider：修改 `sirius_chat/providers/`、`sirius_chat/providers/routing.py`、`sirius_chat/api/providers.py`，并补测试与文档。
- 修改对话主流程：优先检查 `sirius_chat/core/engine.py`、`core/chat_builder.py`、`core/memory_runner.py`、`core/engagement_pipeline.py`。
- 修改 workspace / session 持久化：同步检查 `sirius_chat/workspace/`、`sirius_chat/config/manager.py`、`sirius_chat/session/store.py`。
- 修改识人或记忆逻辑：同步检查 `sirius_chat/memory/user/`、`sirius_chat/memory/event/`、`sirius_chat/memory/self/`、`sirius_chat/models/models.py` 与 `docs/external-usage.md`。
- 修改外部 API：同步更新 `sirius_chat/api/`、README、`docs/external-usage.md` 与示例代码。
- 修改 roleplay 资产流：同步更新 `sirius_chat/roleplay_prompting.py`、`workspace/roleplay_manager.py` 和架构文档。
- `providers/*` 实现具体的 LLM 后端。
- `roleplay_prompting.py` 提供自动问题清单、回答提取式提示词生成、关键词/依赖文件驱动的人格生成、人格持久化、完整本地生成轨迹与依赖文件重生能力；问卷支持 `default` / `companion` / `romance` / `group_chat` 四类模板，可通过 `list_roleplay_question_templates()` 获取模板名，再用 `generate_humanized_roleplay_questions(template=...)` 生成对应的高层人格问卷。人格资产现统一存放于 `roleplay/generated_agents.json` 与 `roleplay/generated_agent_traces/`；对会写入 `work_path` 的人格生成链路，会先暂存 `PersonaSpec` 与待生成快照，再调用模型；结构化人格生成默认使用 `max_tokens=5120`、`timeout_seconds=120.0`，并通过 `GenerationRequest.timeout_seconds` 透传请求级超时。
- 内置 provider 包含 `OpenAICompatibleProvider`、`AliyunBailianProvider`、`DeepSeekProvider`、`SiliconFlowProvider` 与 `VolcengineArkProvider`。
- 若配置了多 provider，`AutoRoutingProvider` 会优先按 `ProviderConfig.models`，其次按 `healthcheck_model` 精确选择可用 provider。
- `cli.py` 是库内薄封装，默认执行单轮会话；同时提供人格模板辅助命令 `--list-roleplay-question-templates` 与 `--print-roleplay-questions-template <template>`，方便外部快速导出问卷模板。
- `api/` 是统一对外接口文件；外部调用优先使用该文件暴露的 API。
- Provider 检测流程已下沉到 `providers/routing.py`：配置检查 -> 平台适配检查 -> 可用性检查（依赖 `healthcheck_model`）。
- Provider 注册命令要求显式提供检测模型：`/provider add <type> <api_key> <healthcheck_model> [base_url]`。
- 提示词流程：`list_roleplay_question_templates()` 暴露问卷模板枚举，`generate_humanized_roleplay_questions(template=...)` 产出高层人格问题；`agenerate_agent_prompts_from_answers` / `agenerate_from_persona_spec`（支持 `trait_keywords`、`answers`、`dependency_files`）生成完整 `GeneratedSessionPreset`。推荐先收集人物原型、核心矛盾、关系策略、情绪原则、表达节奏、边界和小缺点等上位约束，再让生成器展开为具体人物小传与语言习惯；推荐将生成结果作为 agent 资产持久化（`roleplay/generated_agents.json`），并利用 `roleplay/generated_agent_traces/<agent_key>.json` 保存完整生成轨迹。对于 `abuild_roleplay_prompt_from_answers_and_apply(...)`、`aupdate_agent_prompt(...)`、`aregenerate_agent_prompt_from_dependencies(...)` 三条持久化链路，框架会先落盘输入快照，再调用模型，失败时可通过 `load_persona_spec(...)` 恢复最近一次输入。依赖文件更新后可调用 `aregenerate_agent_prompt_from_dependencies(...)` 直接重生人格。
- 内部实现允许重构；当前未发布阶段若影响外部接口，可直接升级 `api/`，并同步文档与示例。
- 内部新增能力需同步在 `api/` 提供对外入口。
- ✨ **(v0.12.0)** `arun_live_message` 新增 `on_reply`（引擎管理事件订阅回调）、`user_profile`（消息处理前自动注册用户）、`timeout`（引擎管理超时与清理）参数，大幅减少外部集成样板代码。详见 `docs/migration-v0.12.md`。
- `main.py` 是仓库级测试/业务入口，承载主用户档案初始化、provider 管理命令与持续会话流程。
- ✨ **开发工具链** (P1-004)：
  - `.github/workflows/ci.yml`：GitHub Actions 多版本 Python 自动化测试与代码质量检查
  - `.pre-commit-config.yaml`：预提交钩子 (black, isort, flake8, mypy, bandit 等)
  - `scripts/ci_check.py`：本地/CI 检查脚本
  - `scripts/setup_dev_env.py`：开发环境自动化初始化
  - `Makefile`：便捷开发命令集

## 修改路由指南

- 新增 provider 支持：修改 `sirius_chat/providers/`，并保持 `sirius_chat/core/engine.py` 不含 provider 细节。
- 修改主 AI 或多人轮次策略：更新 `sirius_chat/core/engine.py`，并检查 transcript 兼容性。
- 修改动态参与者或识人记忆逻辑：同步更新 `models/models.py`、`sirius_chat/core/engine.py` 与 `docs/external-usage.md`。
- 修改会话恢复或压缩策略：同步更新 `workspace/`、`session/store.py`、`session/runner.py`、`docs/architecture.md`、相关迁移文档；若外部可见行为变化，再同步 `README.md`。
- 修改配置结构或环境变量处理：同步更新 `sirius_chat/config/manager.py`、`sirius_chat/cli.py`、`README.md` 与 `examples/session.json`。
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


