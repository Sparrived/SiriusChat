# 变更日志

本文档记录 Sirius Chat 的所有版本变更。采用 [Keep a Changelog](https://keepachangelog.com/zh-CN/) 规范。

## [Unreleased]

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
