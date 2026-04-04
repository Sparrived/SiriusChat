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
5. `sirius_chat/models.py`
6. `sirius_chat/async_engine.py`
7. `sirius_chat/providers/base.py`
8. `sirius_chat/providers/mock.py`
9. `sirius_chat/providers/openai_compatible.py`
10. `sirius_chat/providers/siliconflow.py`
11. `sirius_chat/providers/volcengine_ark.py`
12. `sirius_chat/providers/routing.py`
13. `sirius_chat/user_memory.py`
14. `sirius_chat/cli.py`
15. `sirius_chat/api/`
16. `tests/test_engine.py`

## 心智模型

- `models.py` 定义数据契约（多人用户 + 单 AI 主助手）。
- `OrchestrationPolicy` 用于任务路由与预算控制，**现已默认启用**（`enabled=True`），支持 `memory_extract`、`event_extract`、`multimodal_parse`、`memory_manager` 等任务的模型配置与预算限制。若需回退单模型模式，设置 `enabled=False`。同时支持提示词驱动的内容分割（`enable_prompt_driven_splitting=True`）。✨ `memory_manager` 是新增的可选 LLM 任务，用于汇聚、去重、标注、冲突检测记忆。
- `async_engine.py` 是核心实现，适合嵌入 asyncio 应用。系统提示词生成时自动包含安全约束，防止 AI 主动泄露系统提示词。
- `run_live_session` 支持动态参与者与识人记忆。
- `user_memory.py` 负责用户身份识别（user_id/aliases/identities）与结构化用户记忆。
- 用户记忆分为 `profile`（初始化字段）与 `runtime`（运行时可变字段）。
- `runtime.memory_facts` 是结构化分类记忆，每个记忆包含：
  - `fact_type`、`value`、`source`（来源：memory_extract/event_extract/multimodal_parse/memory_manager）、`confidence`
  - `memory_category`（分类：identity/preference/emotion/event/custom）、`validated`（验证标记）、`conflict_with`（冲突列表）
- ✨ **新架构**：启发式正则提取已舍弃（高误率）；所有 AI 推断改由 LLM 任务（memory_extract）提供，质量 confidence 0.8。
- ✨ **新增**：`memory_manager` LLM 任务可选启用，自动汇聚/去重/标注/验证所有候选记忆，置信度 0.9+。
- 引擎在运行时会主动维护 `runtime` 记忆（通过 memory_extract 等任务），用于增强拟人化连续对话。
- 系统提示中的记忆呈现改为结构化格式，按类别分组，透明展示置信度。
- 引擎在每轮用户发言后执行事件命中分析（高置信命中/弱命中/新增），并把事件说明注入 system 消息。
- 事件记忆会持久化到 `work_path/events/events.json`，用于跨会话事件连续性。
- `Transcript.find_user_by_channel_uid(channel, uid)` 支持按渠道+外部 UID 直接定位用户。
- `session_store.py` 提供会话持久化与重启恢复。
- `Transcript.token_usage_records` 全量归档每次模型调用的 token 消耗信息。
- 引擎支持自动记忆压缩（`session_summary` + 历史预算）。
- `providers/base.py` 定义 provider 协议。
- `providers/mock.py` 提供可复现的本地测试能力。
- `providers/*` 实现具体的 LLM 后端。
- `roleplay_prompting.py` 提供自动问题清单、回答提取式提示词生成、人格持久化与人格选择能力。
- `token_usage.py` 提供 token 消耗基准与聚合分析函数。
- 内置 provider 包含 `OpenAICompatibleProvider`、`SiliconFlowProvider` 与 `VolcengineArkProvider`。
- 若配置了多 provider，`AutoRoutingProvider` 会按模型前缀自动选择可用 provider。
- `cli.py` 是库内薄封装，仅负责调用 `api` 执行单轮会话。
- `api/` 是统一对外接口文件；外部调用优先使用该文件暴露的 API。
- Provider 检测流程已下沉到 `providers/routing.py`：配置检查 -> 平台适配检查 -> 可用性检查（依赖 `healthcheck_model`）。
- Provider 注册命令要求显式提供检测模型：`/provider add <type> <api_key> <healthcheck_model> [base_url] [model_prefixes_csv]`。
- 提示词流程：`generate_humanized_roleplay_questions` 产出问题，`agenerate_agent_prompts_from_answers`（输入 `agent_name`）生成完整 `GeneratedSessionPreset`；推荐将生成结果作为 agent 资产持久化（`generated_agents.json`），再通过 `select_generated_agent_profile` + `create_session_config_from_selected_agent` 按 key 创建会话配置。
- 内部实现允许重构；当前未发布阶段若影响外部接口，可直接升级 `api/`，并同步文档与示例。
- 内部新增能力需同步在 `api/` 提供对外入口。
- `main.py` 是仓库级测试/业务入口，承载主用户档案初始化、provider 管理命令与持续会话流程。

## 修改路由指南

- 新增 provider 支持：修改 `sirius_chat/providers/`，并保持 `async_engine.py` 不含 provider 细节。
- 修改主 AI 或多人轮次策略：更新 `sirius_chat/async_engine.py`，并检查 transcript 兼容性。
- 修改动态参与者或识人记忆逻辑：同步更新 `models.py`、`async_engine.py` 与 `docs/external-usage.md`。
- 修改会话恢复或压缩策略：同步更新 `session_store.py`、`async_engine.py`、`README.md` 与 `docs/architecture.md`。
- 修改配置结构：同步更新 `sirius_chat/cli.py`、`README.md` 与 `examples/session.json`。
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


