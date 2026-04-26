# Sirius Chat — Agent 开发指南

> 本文档面向 AI Coding Agent。如果你对本项目一无所知，请从这里开始阅读。

---

## 项目概述

**Sirius Chat**（PyPI 包名 `sirius-chat`）是一个为**多人 RPG 对话场景**设计的 Python LLM 编排框架。它以异步优先（Async-First）架构为核心，支持多人用户与一个 AI 主助手之间的实时交互，具备结构化记忆系统、角色扮演资产生成、多 Provider 自动路由、可扩展 SKILL 任务编排、Token 消耗追踪与性能监控等能力。

- **版本**：`1.0.0`
- **Python 要求**：`>=3.12`
- **许可证**：MIT
- **仓库**：`https://github.com/Sparrived/SiriusChat`

项目源码、注释、docstring、CLI 输出与文档均以**中文**为主；英文仅出现在架构名词、API 标识与模块路径中。

---

## 核心编码宗旨

> 以下原则用于减少 LLM 编码时的常见失误。在执行任何代码变更前必读。

**权衡：** 这些准则偏向谨慎而非速度。对于琐碎任务，可自行判断。

### 1. 编码前思考（Think Before Coding）

**不要假设。不要隐藏困惑。暴露权衡。**

在动手实现前：
- 明确陈述你的假设。如果不确定，请发问。
- 如果存在多种理解方式，把它们列出来——不要沉默地自行选择。
- 如果存在更简单的方案，说出来。必要时提出反对意见。
- 如果有不清楚的地方，停下来。指出哪里令人困惑。请发问。

### 2. 简约优先（Simplicity First）

**最少代码解决问题。不做任何臆测。**

- 不添加超出需求的功能。
- 不为仅使用一次的代码做抽象。
- 不添加未被要求的"灵活性"或"可配置性"。
- 不处理不可能场景的错误。
- 如果你写了 200 行而其实可以 50 行搞定，重写它。

问自己："资深工程师会觉得这过度设计了吗？" 如果是，简化。

### 3. 精准变更（Surgical Changes）

**只碰必须碰的地方。只清理自己制造的混乱。**

编辑现有代码时：
- 不要"改进"相邻的代码、注释或格式。
- 不要重构没有坏掉的东西。
- 匹配现有风格，即使你本人做法不同。
- 如果你注意到无关的死代码，提及它即可——不要删除它。

当你的变更产生孤儿代码时：
- 删除**你的**变更导致不再使用的 import / 变量 / 函数。
- 不要删除预先存在的死代码，除非被要求。

检验标准：每一行变更都应能直接追溯到用户的请求。

### 4. 目标驱动执行（Goal-Driven Execution）

**定义成功标准。循环直到验证通过。**

将任务转化为可验证的目标：
- "添加验证" → "先为无效输入写测试，再让它们通过"
- "修复 bug" → "先写能复现它的测试，再让它通过"
- "重构 X" → "确保重构前后测试都能通过"

对于多步骤任务，给出一个简要计划：
```
1. [步骤] → 验证：[检查]
2. [步骤] → 验证：[检查]
3. [步骤] → 验证：[检查]
```

强有力的成功标准让你能独立迭代。弱标准（"让它跑起来"）需要不断澄清。

---

## 技术栈与运行时架构

### 核心依赖
- **Python 3.12+**（强制要求，CI 与 pre-commit 均绑定 3.12）
- **watchdog>=4.0.0**（文件监听，用于 workspace 配置热刷新）
- **asyncio**（全链路异步，核心引擎与 provider 调用均为 async）

### 可选依赖分组
| 分组 | 包含内容 |
|------|----------|
| `test` | `pytest>=8.0.0`, `pytest-asyncio>=0.21.0`, `pytest-cov>=4.0.0`, `psutil>=5.9.0` |
| `provider` | `httpx>=0.24.0`（异步 HTTP），`tenacity>=8.0.0`（重试） |
| `dev` | 以上全部 + `black`, `isort`, `flake8`, `pylint`, `mypy`, `bandit`, `pre-commit`, `build`, `twine` |
| `quality` | `tiktoken>=0.5.0`（精确 token 估算） |

### 构建系统
- 使用 `setuptools>=61.0` 作为 PEP 517 build backend。
- 包发现规则：`include = ["sirius_chat*"]`。
- CLI 入口：`sirius-chat = "sirius_chat.cli:run"`。

### 运行时架构
项目采用**分层架构**：

1. **API 层**（`sirius_chat/api/`）：稳定的公开 facade，所有外部调用应通过这里。
2. **核心编排层**（`sirius_chat/core/`）：v1.0 唯一引擎 `EmotionalGroupChatEngine`，含四层认知架构（感知→认知→决策→执行）+ 简化两层记忆底座（基础记忆 + 日记记忆）+ 事件流。旧 `AsyncRolePlayEngine` 已完全移除。
3. **兼容/辅助层**（`sirius_chat/async_engine/`）：历史兼容导出与 prompts/orchestration/utils 辅助。
4. **Workspace 层**（`sirius_chat/workspace/`）：布局管理、运行时生命周期、文件监听与热刷新。
5. **配置层**（`sirius_chat/config/`）：WorkspaceConfig / SessionConfig / JSONC 读写管理。
6. **记忆层**（`sirius_chat/memory/`）：基础记忆（BasicMemory）、日记记忆（Diary）、用户画像（UserManager）、名词解释（Glossary）。
7. **会话层**（`sirius_chat/session/`）：`SessionStore` 协议及 JSON / SQLite 实现。
8. **Provider 层**（`sirius_chat/providers/`）：Provider 协议、具体实现（OpenAI / DeepSeek / 阿里云百炼 / 智谱 BigModel / SiliconFlow / 火山方舟等）、路由与中间件（重试、熔断、限流、成本监控）。
9. **SKILL 层**（`sirius_chat/skills/`）：内置 + 外部 SKILL 注册、依赖解析、执行与数据存储。
10. **Token 层**（`sirius_chat/token/`）：Token 消耗统计、SQLite 持久化与分析报表。
11. **缓存与性能层**（`sirius_chat/cache/`, `sirius_chat/performance/`）：LRU+TTL 缓存、性能分析与基准测试。

---

## 项目结构与主要模块

```
sirius_chat/
├── __init__.py              # 顶层公开 API 统一重导出（严格 __all__）
├── api/                     # 公开 API facade（engine/models/providers/session 等）
├── async_engine/            # 兼容导出 + prompts/orchestration/utils 辅助层
├── cache/                   # 可扩展缓存框架（LRU + TTL）
├── config/                  # 配置模型、JSONC 管理、WorkspaceConfig / SessionConfig
├── configs/                 # 内置配置模板
├── core/                    # 编排核心（v1.0 唯一引擎）
│   ├── emotional_engine.py  # EmotionalGroupChatEngine（主引擎，v1.0）
│   ├── response_assembler.py # 执行层：Prompt 组装 + 风格适配
│   ├── emotion.py           # 情感分析（二维模型 + 19 种基础情绪）
│   ├── intent_v3.py         # 意图分析 v3（目的驱动：求助/情感/社交/沉默）
│   ├── response_strategy.py # 四层响应策略（立即/延迟/沉默/主动）
│   ├── delayed_response_queue.py # 延迟响应队列（话题间隙检测）
│   ├── proactive_trigger.py # 主动触发器（时间/记忆/情感触发）
│   ├── rhythm.py            # 对话节奏分析（热度/速度/注意力窗口）
│   ├── threshold_engine.py  # 动态阈值引擎（Base × Activity × Relationship × Time）
│   ├── events.py            # 会话事件流
│   ├── chat_builder.py      # 主模型请求构造
│   └── identity_resolver.py # 跨平台身份解析
├── memory/                  # 记忆子包（v1.0 简化架构）
│   ├── basic/               # 基础记忆（工作窗口 + 热度 + 归档）
│   ├── diary/               # 日记记忆（LLM 生成、索引、检索）
│   ├── glossary/            # 名词解释（AI 自身知识）
│   ├── user/                # 用户管理（简化 UserProfile + UserManager）
│   ├── context_assembler.py # 上下文组装器（basic + diary → OpenAI messages）
│   └── semantic/            # 语义记忆 stub（向后兼容，保留 GroupSemanticProfile）
├── models/                  # 核心数据模型（dataclass）
│   ├── emotion.py           # EmotionState / AssistantEmotionState / EmpathyStrategy
│   ├── intent_v3.py         # IntentAnalysisV3 / SocialIntent
│   ├── response_strategy.py # StrategyDecision / ResponseStrategy
│   └── models.py            # Message / Participant / Transcript / User 等
├── performance/             # 性能分析与基准测试
├── providers/               # Provider 实现、路由、中间件
│   ├── routing.py           # 自动路由与 ProviderRegistry
│   └── middleware/          # 重试、熔断、限流、成本监控中间件
├── session/                 # SessionStore 与高层兼容 runner
├── skills/                  # SKILL 注册、执行与数据存储
│   └── builtin/             # 内置技能（system_info、desktop_screenshot、learn_term、url_content_reader、bing_search）
├── token/                   # Token 统计、SQLite 持久化与分析
├── workspace/               # 布局、运行时、文件监听、角色资产 bootstrap
├── roleplay_prompting.py    # 人格资产生成、持久化与选择
├── cli.py                   # 库内薄 CLI（sirius-chat 命令）
├── cli_diagnostics.py       # CLI 诊断与配置生成
└── logging_config.py        # 日志配置（按日轮转、7 天备份）

tests/                       # 35+ 测试文件，600+ 单元测试
├── conftest.py              # 最小 fixtures（仅添加项目根到 sys.path）
├── test_api_integrity.py    # 公开 API 完整性检查（__all__、无内部泄漏）
├── test_engine.py           # 编排核心
├── test_async_engine.py     # 异步引擎与兼容层
├── test_workspace_runtime.py # workspace 持久化与热刷新
├── test_skill_system.py     # SKILL 系统
├── test_roleplay_prompting.py # 人格生成与选择
├── test_self_memory.py      # AI 自身记忆
├── test_intent_and_consolidation.py # 意图分析与记忆整合
├── test_providers.py        # 各 provider 一致性
├── test_provider_routing.py # provider 注册表与自动路由
└── ...

docs/                        # 文档
├── architecture.md          # 架构总览与模块边界
├── configuration.md         # 配置字段说明与最佳实践
├── orchestration-policy.md  # 任务模型覆盖与动态路由
├── full-architecture-flow.md # 详细数据流
├── external-usage.md        # 库调用指南
├── skill-authoring.md       # SKILL 编写规范
├── best-practices.md        # 开发最佳实践
├── migration-*.md           # 各版本迁移指南
└── ...

examples/                    # 使用示例（JSON 配置 + Python 代码）
scripts/                     # 开发脚本
├── ci_check.py              # 统一 CI 检查脚本
├── setup_dev_env.py         # 开发环境设置
└── generate_api_docs.py     # API 文档生成（AST + 运行时内省）
```

### 主要入口点

| 入口 | 文件 | 用途 |
|------|------|------|
| `python main.py` | `main.py`（~1008 行） | 仓库级交互入口：持续会话、provider 管理、主用户档案、transcript 输出、首次引导向导 |
| `sirius-chat` | `sirius_chat/cli.py` | 库内薄 CLI：单轮消息、角色模板导出、legacy session JSON bootstrap |
| `open_workspace_runtime()` | `sirius_chat/api/engine.py` | **推荐生产入口**：自动恢复 workspace、热刷新、会话恢复、参与者元数据、store 回写 |
| `create_emotional_engine()` | `sirius_chat/api/engine.py` | **v0.28 推荐工厂**：创建 EmotionalGroupChatEngine 并注入 provider |
| `EmotionalGroupChatEngine` | `sirius_chat/core/emotional_engine.py` | **v1.0 唯一引擎**：群聊情感化编排、四层响应策略、简化记忆底座、事件流、ModelRouter |

---

## 构建与测试命令

### 安装
```bash
# 基础安装
python -m pip install -e .

# 含测试依赖
python -m pip install -e .[test]

# 含开发依赖（推荐）
python -m pip install -e .[dev]

# 含全部可选依赖
python -m pip install -e .[dev,provider,quality]
```

### 测试
```bash
# 运行全部测试（快速，<15 秒）
python -m pytest tests/ -q

# 详细输出运行特定模块
python -m pytest tests/test_engine.py -v

# 单测试快速验证
python -m pytest tests/test_engine.py::test_roleplay_engine_multi_human_single_ai_transcript -xvs

# 覆盖率分析
python -m pytest tests/ --cov=sirius_chat --cov-report=html --cov-report=term-missing

# 查看最慢 10 个测试
python -m pytest tests/ --durations=10
```

### 代码质量（Makefile）
```bash
make lint          # pylint + flake8
make format        # black + isort
make typecheck     # mypy
make test          # pytest -q
make test-cov      # pytest + 覆盖率报告
make build         # python -m build
make dist-check    # twine check dist/*
make api-docs      # 生成 docs/api.md + docs/api.json
make pre-commit-install
make pre-commit-run
make clean         # 清理构建产物、缓存
```

### 统一 CI 脚本
```bash
python scripts/ci_check.py
```
该脚本按顺序执行：
1. `black --check`（必需）
2. `isort --check-only`（必需）
3. `pylint`（可选）
4. `mypy`（可选）
5. `bandit -r -ll`（可选）
6. `pytest -q --tb=short`（必需）
7. `pytest --cov=sirius_chat --cov-report=term-missing`（可选）

---

## 代码风格指南

### 格式化与静态检查配置
| 工具 | 配置 |
|------|------|
| **black** | `--line-length=100` |
| **isort** | `--profile=black --line-length=100` |
| **flake8** | `--max-line-length=100 --extend-ignore=E203,W503`，附加 `flake8-docstrings` |
| **pylint** | `--fail-under=7.5 --disable=C0111,W0212`（CI 中放宽到 `--fail-under=8.0`） |
| **mypy** | `--ignore-missing-imports --skip-validation` |
| **bandit** | `-ll`（低置信度） |
| **pydocstyle** | `--convention=google`，排除 `tests/`、`setup.py` |

### 强制约定
1. **每模块首行必须是 `from __future__ import annotations`**。所有生产代码与测试代码均遵循此约定。
2. **`__all__` 纪律**：`sirius_chat/__init__.py` 与 `sirius_chat/api/__init__.py` 均定义严格的 `__all__`；`test_api_integrity.py` 会自动检查公开 API 是否泄漏内部成员。
3. **禁止在顶层暴露内部包**：`core`、`memory`、`config`、`async_engine`、`session`、`token`、`cache`、`performance` 等内部模块**不得**通过 `sirius_chat` 顶层直接访问；外部调用必须走 `sirius_chat.api`。
4. **模块级 logger**：使用标准库 `logging`，格式为 `'%(asctime)s - %(name)s - %(levelname)s - %(message)s'`，logger 名与模块路径一致（如 `"sirius_chat.async_engine"`）。
5. **dataclass 优先**：核心数据契约使用 `@dataclass` 定义，位于 `sirius_chat/models/` 与 `sirius_chat/config/models.py`。
6. **原子文件写入**：配置持久化使用 `_atomic_write_json`（临时文件 + replace），避免脏写。
7. **中文为主**：源码注释、docstring、CLI 输出、用户可见异常信息、README 与 docs 均使用中文。架构名词保留英文（如 `AsyncRolePlayEngine`、`WorkspaceRuntime`）。
8. **Conventional Commits**：提交信息遵循 [Conventional Commits](https://www.conventionalcommits.org/) 格式。

---

## 测试策略

### 测试框架
- **pytest** + **pytest-asyncio** + **pytest-cov**
- `pyproject.toml` 中配置：`testpaths = ["tests"]`，`python_files = ["test_*.py"]`

### 测试哲学
- **完全隔离，无真实网络调用**：所有测试使用 `MockProvider`（定义在 `sirius_chat.providers.mock`）模拟 LLM 响应。
- **600+ 单元测试**，目标覆盖率约 92%，关键路径全覆盖。
- **快速执行**：全套测试关闭积压批处理并禁用无关辅助任务后，可在 <15 秒内完成。

### 测试文件对应关系
| 测试文件 | 覆盖范围 |
|----------|----------|
| `test_api_integrity.py` | 公开 API 完整性、向后兼容、无内部泄漏 |
| `test_engine.py` / `test_async_engine.py` | 核心编排引擎 |
| `test_workspace_runtime.py` | workspace 生命周期、持久化、热刷新 |
| `test_skill_system.py` | SKILL 注册、执行、链式调用、权限 |
| `test_roleplay_prompting.py` | 人格问卷、资产生成、选择与轨迹 |
| `test_basic_memory.py` / `test_diary_memory.py` / `test_context_assembler.py` | 新记忆子系统 |
| `test_identity_resolver.py` / `test_user_system.py` | 身份解析与用户管理 |
| `test_response_assembler.py` | 执行层测试（StyleAdapter + ResponseAssembler，12 项） |
| `test_model_router.py` | 模型路由层（任务感知选择、urgency 升级、heat 适配，18 项） |
| `test_engine_persistence.py` | 引擎状态持久化（group-isolated save/load，9 项） |
| `test_engine_event_stream.py` | 事件流集成（PERCEPTION/COGNITION/DECISION/EXECUTION 事件，4 项） |
| `test_engine_e2e.py` | 端到端集成测试（立即/延迟/沉默/主动/多群/氛围变化，7 项） |
| `test_intent_and_consolidation.py` / `test_orchestration_config.py` / `test_dynamic_model_routing.py` | 多模型编排与意图分析 |
| `test_providers.py` / `test_provider_routing.py` | Provider 实现与路由 |
| `test_config_manager.py` / `test_cli_*.py` | 配置与 CLI |
| `test_bugfix_round2.py` / `test_c2c3_optimization.py` / `test_auto_multimodal_config.py` | 回归与特性验证 |

---

## 安全考虑

### 敏感数据处理
- **API Key 持久化**：Provider 凭证存储在 `{config_root}/providers/provider_keys.json`，由 `WorkspaceProviderManager` / `ProviderRegistry` 管理。代码中不得将 API Key 打印到日志或 stdout。
- **环境变量替换**：配置系统支持 `${VAR_NAME}` 递归替换为 `os.environ` 值，用于避免在配置文件中硬写密钥。

### SKILL 安全模型
- **Developer-Only 限制**：某些 SKILL（如 `desktop_screenshot`）标记为 `developer_only=True`；调用者必须在 `UserProfile.metadata["is_developer"] = True` 中显式声明，否则执行会被拒绝。
- **非 developer 当前轮次隐藏**：developer-only SKILL 会在非 developer 用户的系统提示词中自动隐藏，执行时二次校验。
- **依赖自动安装**：SKILL 声明的缺失依赖由框架自动通过 `uv pip install`（fallback `pip`）安装，需确保运行环境可信。
- **SKILL 调用超时**：默认 30 秒，通过 `asyncio.to_thread()` 在独立线程中执行，防止阻塞事件循环。

### 静态安全扫描
- **bandit** 在 CI 中运行（`bandit -r sirius_chat -ll`），用于检测常见 Python 安全问题。
- `scripts/ci_check.py` 包含 bandit 扫描步骤。

### 会话与文件安全
- **文件路径校验**：workspace 布局通过 `WorkspaceLayout` 统一管理，禁止访问布局外的路径。
- **大文件限制**：pre-commit 的 `check-added-large-files` 限制为 500KB。

---

## 开发惯例与重要约定

### Workspace 双根模式（v0.24.0+）
- **`work_path`**（或 `data_path`）：运行态数据根，保存 `sessions/`、`memory/`、`token/`、`skill_data/`、`primary_user.json`。
- **`config_root`**（或 `config_path`）：配置根，保存 `workspace.json`、`config/`、`providers/`、`roleplay/`、`skills/`。
- 若未显式拆分，两者回退到单根模式。
- 外部修改 `config_root` 下的文件后，`WorkspaceConfigWatcher` 通过 `watchdog` 监听并在 50ms debounce 后自动刷新引擎状态与 SKILL 运行时。

### 配置层级
1. **轻量会话配置**（JSON/JSONC）：供 CLI 入口使用（`main.py --config`、`sirius-chat --config`），字段精简，必须包含 `generated_agent_key` 与 `providers`。
2. **完整 `SessionConfig`**：仅通过 Python API 构造，包含 `agent`、`global_system_prompt`、`work_path`、`data_path` 等完整字段。
3. **Workspace 配置**：`workspace.json` 持久化活跃 agent key、session defaults、orchestration defaults、provider policy。

### 记忆架构要点

**简化记忆架构（v1.0）**：
- **基础记忆（BasicMemory）**：按 `group_id` 维护内存中的对话滑动窗口（硬限制 30 条，上下文窗口 5 条）。含热度计算器（HeatCalculator），基于消息速率、独特发言者和最近度计算群体热度（0-1）。当群体变冷（heat < 0.25）且沉默超过 300 秒时，上下文窗口外的消息归档为日记素材。
- **日记记忆（DiaryMemory）**：LLM 生成的群聊摘要，含关键词和 source_ids 回链基础记忆。支持 sentence-transformers 嵌入索引（可选）和关键词回退搜索。检索时按 token 预算（默认 800 tokens）截断，通过 ContextAssembler 注入系统提示词。
- **用户管理（UserManager）**：极简 `UserProfile`（user_id, name, aliases, identities, metadata），群隔离存储 `{group_id: {user_id: UserProfile}}`。跨平台身份追踪通过 `IdentityResolver` 解耦，支持 speaker_name → user_id → platform_uid 的多级解析。
- **名词解释（GlossaryManager）**：AI 自身知识库，替代旧 AutobiographicalMemory。`learn_term` SKILL 路由至此。
- **上下文组装（ContextAssembler）**：将基础记忆的最近 n 条 + 日记检索的 top_k 条组装为标准 OpenAI messages 数组。日记内容注入 system_prompt 作为「历史日记」，不污染消息历史。

**模型路由层（v0.28 新增）**：
- `ModelRouter` 按任务类型（`emotion_analyze` / `intent_analyze` / `response_generate` / `memory_extract`）自动选择模型、温度、token 上限。
- Urgency 升级：`urgency ≥ 80` → 切换更强模型；`urgency ≥ 95` → 最大 token 上限。
- Heat 适配：`hot` 减少 30% token，`overheated` 减半 token。
- 用户风格覆盖：`concise` 限制 80 token，`detailed` 增加 20%。
- 通过 `task_model_overrides` 配置可自定义映射。

**后台任务（v0.28 新增）**：
- `start_background_tasks()` / `stop_background_tasks()` 幂等启停。
- 延迟队列 ticker：每 10 秒检查所有活跃群的延迟响应。
- 主动触发 checker：每 60 秒检查长时间沉默群。
- 日记生成 promoter：每 5 分钟检查 `BasicMemoryManager` 的冷群候选；触发 `DiaryGenerator` 将归档消息 LLM 总结为 `DiaryEntry`，写入 `DiaryManager` 并建立索引。
- 语义整合 consolidator：每 10 分钟（可配置）对日记条目进行聚合；当前为 no-op，保留接口供后续扩展。

**群级规范学习（v0.28 新增）**：
- 被动学习：每处理一条消息自动更新群体统计。
- 统计项：`avg_message_length`、`emoji_usage_rate`、`mention_rate`、`active_hours`、`topic_switch_frequency`。
- `typical_interaction_style` 推断：`active`（短消息多）/`humorous`（emoji 多）/`formal`（@提及多）/`balanced`。
- 数据写入 `GroupSemanticProfile.group_norms`，与 atmosphere_history 一起持久化。

### Provider 与路由
- 支持平台：`openai-compatible`、`deepseek`、`aliyun-bailian`、`bigmodel`（智谱）、`siliconflow`、`volcengine-ark`、`ytea`。
- `AutoRoutingProvider` 按 `models` 显式列表或 `healthcheck_model` 精确匹配选择后端；未命中时回退到第一个可用 provider。
- 中间件链支持：重试（指数退避）、熔断（5 次失败打开，2 次成功关闭，60s 超时）、滑动窗口限流、令牌桶限流、成本监控。
- 本地图片路径会在发送前自动转为 base64 Data URL；公网 URL 需确保上游可直接访问。

### SKILL 系统
- **发现规则**：加载 `{config_root}/skills/*.py` 与 `skills/builtin/*.py`；文件名以下划线或点开头的被忽略。
- **覆盖规则**：workspace skill 同名时覆盖内置 skill。
- **必须导出**：`SKILL_META`（dict）与 `run()` 函数。
- **结构化返回**：支持 `summary`、`text_blocks`、`multimodal_blocks`、`internal_metadata`；`internal_metadata` 不得泄露到用户可见输出。
- **AI 调用语法**：`[SKILL_CALL: skill_name | {"param": "value"}]`

### v0.28 认知架构

运行时数据流（四层 + 后台）：

```
群消息进入（感知层）
    │
    ├─ 注册参与者 → IdentityResolver.resolve() → UserManager.register()
    ├─ 写入基础记忆 → BasicMemoryManager.add_entry(group_id, user_id, role, content)
    ├─ 更新 group_last_message_at
    └─ 更新热度 → HeatCalculator.compute_heat(group_id)
    │
    ▼
认知层（并行 + 可选 LLM fallback）
    ├─ IntentAnalyzer v3 → social_intent + urgency + relevance
    │     └─ 规则 confidence < 0.8 → LLM 高精度分类（JSON 输出）
    ├─ EmotionAnalyzer → EmotionState(valence, arousal, basic_emotion)
    │     └─ 规则 confidence < 0.6 → LLM 高精度情感分析（JSON 输出）
    └─ 记忆检索 → DiaryManager.retrieve(query, group_id) + BasicMemoryManager.get_context(group_id)
    │
    ▼
决策层
    ├─ RhythmAnalyzer → heat_level + pace + topic_stability
    ├─ ThresholdEngine → dynamic engagement threshold
    └─ ResponseStrategyEngine → IMMEDIATE / DELAYED / SILENT / PROACTIVE
    │
    ▼
执行层
    ├─ ResponseAssembler → PromptBundle（system_prompt：persona + 情绪 + 共情 + 日记 + skill + 输出格式；user_content：当前消息）
    ├─ ContextAssembler.build_messages() → basic 最近 n 条 + diary 检索 top_k 条 → OpenAI messages
    ├─ StyleAdapter → max_tokens / temperature / tone 动态适配
    ├─ ModelRouter → 任务感知模型选择（urgency / heat / 用户风格）
    └─ LLM 生成回复（provider_async.generate_async / generate），传入标准 messages 数组
    │
    ├─ 记录 assistant 回复到 BasicMemoryManager
    │
    ▼
后台更新层
    ├─ 日记生成 → 当群体变冷（heat < 0.25）且沉默 > 300s 时，基础记忆归档消息经 DiaryGenerator 生成日记
    ├─ 更新群氛围 → semantic_memory stub（保留 GroupSemanticProfile.atmosphere_history）
    ├─ 被动学习群规范 → semantic_memory.group_norms
    ├─ 更新用户情感轨迹 → emotion_analyzer.trajectories
    └─ 触发事件 → event_bus (PERCEPTION/COGNITION/DECISION/EXECUTION_COMPLETED)
```

四层响应策略：
- **IMMEDIATE**：高 urgency / 被直接@ → 立即生成回复
- **DELAYED**：中等 relevance → 进入延迟队列，等待话题间隙后回复
- **SILENT**：低 relevance / 日常闲聊 → 不回复，仅更新记忆
- **PROACTIVE**：长时间沉默 / 记忆触发 / 情感触发 → AI 主动开启话题

**持久化与恢复**：
- `save_state()` → 原子写入 `engine_state/`（working_memory.json、assistant_emotion.json、group_timestamps.json、event_memory.json）
- `load_state()` → 从磁盘恢复所有群的运行态，含 `event_memory` 缓冲与已提取观察
- 语义记忆和情景记忆已自带文件持久化，无需额外处理

---

### 文档同步义务
当架构、命令或 API 发生变更时，**必须**同步更新以下文件：
1. `AGENTS.md`（本文档——agent 开发指南，最高优先级）
2. `docs/architecture.md`
3. `docs/full-architecture-flow.md`
4. `README.md`（若涉及用户可见行为）
5. `.github/skills/framework-quickstart/SKILL.md`
6. 相关的 `.github/skills/` 文件

**不要**在未明确要求的情况下，为新增功能自动生成额外的 markdown 文档（如独立指南、快速入门、参考手册），以保持文档集中化。

---

## CI / CD 与发布流程

### GitHub Actions 工作流
- **`.github/workflows/ci.yml`**：在 `push`/`pull_request` 到 `master`/`main`/`develop` 时触发，包含：
  - `test` job：Python 3.12，运行 pytest + coverage，上传 Codecov。
  - `lint` job：pylint、black check、isort check、mypy（均带 `|| true`，不阻塞）。
  - `security` job：bandit 扫描（`|| true`）。
  - `build` job：构建 wheel + sdist，用 twine check，上传 artifact。
- **`.github/workflows/publish.yml`**：在推送 `v*` 标签时自动构建并发布到 PyPI（使用 Trusted Publishing，无需手动创建 Release）。

### 发布检查清单
发布前需执行（详见 `.github/skills/release-checklist/SKILL.md`）：
1. 验证 `pyproject.toml` 版本号与描述。
2. `pip install -e .[test]` + `pytest -q`。
3. 命令冒烟测试：`sirius-chat --help`、`python main.py --help`、用 `examples/session.json` 在隔离 workspace 运行。
4. 确认 bootstrap 文件已写入：`workspace.json`、`roleplay/generated_agents.json`、`participants.json`。
5. 同步 `README.md`、`docs/architecture.md`、`.github/skills/framework-quickstart/SKILL.md`。
6. **语言检查**：所有 `.github/skills/` 文件必须为中文；发现英文内容需翻译修正。
7. 打 tag 并推送：`git tag v{VERSION}` + `git push origin v{VERSION}`，由 Actions 自动完成 PyPI 发布。

---

## 快速参考：常用文件路径

| 路径 | 说明 |
|------|------|
| `pyproject.toml` | 项目元数据、依赖、pytest 配置、setuptools 配置 |
| `Makefile` | 开发工作流快捷命令 |
| `main.py` | 仓库级交互入口 |
| `sirius_chat/core/emotional_engine.py` | v0.28 核心情感群聊引擎 |

| `sirius_chat/api/__init__.py` | 公开 API 导出清单 |
| `tests/conftest.py` | 测试最小 fixture |
| `scripts/ci_check.py` | 统一 CI 检查脚本 |
| `docs/architecture.md` | 架构边界与模块交互权威文档 |
| `docs/skill-authoring.md` | SKILL 编写规范 |
| `docs/best-practices.md` | 开发最佳实践 |
