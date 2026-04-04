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
3. `sirius_chat/api/`
4. `sirius_chat/models.py`
5. `sirius_chat/async_engine.py`
6. `sirius_chat/providers/base.py`
7. `sirius_chat/cli.py`

## 接入决策规则

- 外部系统是 Python 服务：优先使用异步库 API（`AsyncRolePlayEngine` + `SessionConfig`）。
- 若调用方不希望手动维护用户与持久化，优先使用 `JsonPersistentSessionRunner`。
- 会话持久化后端可选 `JsonSessionStore` 或 `SqliteSessionStore`（可按场景切换）。
- 外部系统接入时，优先从 `sirius_chat/api/` 导入接口。
- 系统提示词在生成时自动包含安全约束，明确告诉 AI 不要主动泄露系统提示词和初始指令；外部调用方无需手动添加，engine 会自动处理。
- 若需启用提示词驱动的内容分割，配置 `OrchestrationPolicy.enable_prompt_driven_splitting=True` 和 `OrchestrationPolicy.split_marker`（默认 `[MSG_BREAK]`）。
- 外部系统若为 asyncio 程序，优先使用 `AsyncRolePlayEngine` 或异步 facade。
- 外部系统是非 Python：优先通过 CLI 调用并读取输出文件。
- 每个 `AsyncRolePlayEngine` 会话只对应一个主 AI（由 `SessionConfig.preset` 描述）。
- `work_path` 是强制参数，调用方必须显式提供，所有持久化文件都写入该目录。
- 推荐显式构造 `User`（`user_id/name/aliases/traits/identities`），让系统稳定识别人。
- 通过 `identities` 可把不同环境（CLI/QQ/微信）的外部账号映射到同一 `user_id`。
- 群聊参与者若预先未知，使用 `run_live_session` 并传入动态 `human_turns`。
- 通过 `transcript.user_memory` 维护用户画像与近期发言，实现识人。
- 用户记忆分层：`profile`（初始化档案）+ `runtime`（运行时可变记忆）。
- 引擎运行时应主动更新 `runtime`（偏好标签、情绪线索、摘要），以提升拟人化体验。
- 需要按渠道身份直查时，使用 `transcript.find_user_by_channel_uid(channel, uid)`。
- 通过 `JsonSessionStore` 持久化 transcript，实现重启后恢复会话。
- 通过 `Transcript.token_usage_records` 获取全量 token 调用归档。
- 通过 `summarize_token_usage` / `build_token_usage_baseline` 输出成本与损耗基准分析。
- 通过 `generate_humanized_roleplay_questions` 自动生成拟人化问题清单。
- 通过 `agenerate_agent_prompts_from_answers`（输入 `agent_name`）或 `abuild_roleplay_prompt_from_answers_and_apply` 从问答中生成并应用完整 `GeneratedSessionPreset`。
- 推荐采用 agent-first：先生成并持久化 agent 资产（`generated_agents.json`），再用 `select_generated_agent_profile(work_path, agent_key)` 选择，最后通过 `create_session_config_from_selected_agent(...)` 创建会话。
- 通过 `history_max_messages/history_max_chars` 启用自动记忆压缩，控制 token 增长。
- 任何情况下，不应在 `async_engine.py` 中写入 provider 细节。
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
- SiliconFlow 上游：优先使用 `SiliconFlowProvider`（默认 `https://api.siliconflow.cn`，兼容传入 `/v1` 后缀）。
- 火山方舟上游：优先使用 `VolcengineArkProvider`（默认 `https://ark.cn-beijing.volces.com/api/v3`，接口 `/api/v3/chat/completions`）。
- 多平台自动选择：使用 `AutoRoutingProvider` + `ProviderRegistry`，通过模型前缀路由。
- 交互模式下可用 `/provider platforms|add|remove|list` 管理 API Key（持久化在 `work_path/provider_keys.json`）。
- `/provider add` 需提供 `healthcheck_model`，注册时会执行可用性检测：
	`/provider add <type> <api_key> <healthcheck_model> [base_url] [model_prefixes_csv]`
- 框架会执行统一 Provider 检测流程：配置检查（平台名/API）-> 平台适配检查（仅允许已适配平台）-> 可用性检查（healthcheck model）。
- **多模型协同（默认启用）**：`SessionConfig.orchestration.enabled` 现在默认为 `True`，引擎会按照 `task_models` 配置自动分发任务（记忆提取、事件提取、多模态解析）。如需全部由一个模型处理，设置 `enabled=False`。
- 生产环境建议配置 `task_retries` 与多模态限流参数，避免上游抖动与超长输入导致的失败。


