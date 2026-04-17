# Sirius Chat — Agent 开发指南

> 本文档面向 AI Coding Agent。如果你对本项目一无所知，请从这里开始阅读。

---

## 项目概述

**Sirius Chat**（PyPI 包名 `sirius-chat`）是一个为**多人 RPG 对话场景**设计的 Python LLM 编排框架。它以异步优先（Async-First）架构为核心，支持多人用户与一个 AI 主助手之间的实时交互，具备结构化记忆系统、角色扮演资产生成、多 Provider 自动路由、可扩展 SKILL 任务编排、Token 消耗追踪与性能监控等能力。

- **版本**：`0.27.14`
- **Python 要求**：`>=3.12`
- **许可证**：MIT
- **仓库**：`https://github.com/Sparrived/SiriusChat`

项目源码、注释、docstring、CLI 输出与文档均以**中文**为主；英文仅出现在架构名词、API 标识与模块路径中。

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
2. **核心编排层**（`sirius_chat/core/`）：真实实现，包括 `AsyncRolePlayEngine`、`chat_builder`、`memory_runner`、`engagement_pipeline`。
3. **兼容/辅助层**（`sirius_chat/async_engine/`）：历史兼容导出与 prompts/orchestration/utils 辅助。
4. **Workspace 层**（`sirius_chat/workspace/`）：布局管理、运行时生命周期、文件监听与热刷新。
5. **配置层**（`sirius_chat/config/`）：WorkspaceConfig / SessionConfig / JSONC 读写管理。
6. **记忆层**（`sirius_chat/memory/`）：用户记忆、事件记忆、AI 自身记忆（日记 + 名词解释）、记忆质量与遗忘曲线。
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
├── core/                    # 编排核心真实实现
│   ├── engine.py            # AsyncRolePlayEngine（主引擎，~2000 行）
│   ├── chat_builder.py      # 主模型请求构造
│   ├── memory_runner.py     # 记忆相关辅助任务
│   ├── engagement_pipeline.py # 热度/意图/参与协调流水线
│   ├── heat.py              # 群聊热度分析
│   ├── intent_v2.py         # 意图分析
│   └── events.py            # 会话事件流
├── memory/                  # 记忆子包
│   ├── user/                # 用户记忆管理（UserMemoryManager / UserMemoryEntry / MemoryFact）
│   ├── event/               # 事件记忆 V2（observation-based、批量提取、去重）
│   ├── self/                # AI 自身记忆（Diary + Glossary）
│   └── quality/             # 记忆质量评估与遗忘引擎
├── models/                  # 核心数据模型（dataclass）
├── performance/             # 性能分析与基准测试
├── providers/               # Provider 实现、路由、中间件
│   ├── routing.py           # 自动路由与 ProviderRegistry
│   └── middleware/          # 重试、熔断、限流、成本监控中间件
├── session/                 # SessionStore 与高层兼容 runner
├── skills/                  # SKILL 注册、执行与数据存储
│   └── builtin/             # 内置技能（system_info、desktop_screenshot）
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
├── orchestration-policy.md  # 多模型编排策略
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
| `AsyncRolePlayEngine` | `sirius_chat/core/engine.py` | 底层引擎：单轮消息编排、辅助任务、prompt 构造、事件流、SKILL 循环 |

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
| `test_self_memory.py` / `test_memory_system_v2.py` / `test_event_user_memory_integration.py` | 记忆各子系统 |
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
- **用户记忆**：按 `memory_category`（identity / preference / emotion / event / custom）组织；区分可信身份锚点与弱别称线索。
- **事实置信度分层**：`transient_confidence_threshold`（默认 0.85）将事实分为 *RESIDENT*（持久化）与 *TRANSIENT*（会话级，30 分钟后自动清理）。
- **智能上限**：当 `memory_facts` 超过 `MAX_MEMORY_FACTS`（50）时，删除置信度最低的 10%，而非简单 FIFO。
- **事件记忆 V2**：基于 observation、按用户缓冲、批量提取；去重使用字符集 Jaccard 相似度（阈值 0.55）。
- **AI 自身记忆**：日记（Diary，最多 100 条）+ 名词解释（Glossary，最多 200 条），支持遗忘曲线与衰减。

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

### 文档同步义务
当架构、命令或 API 发生变更时，**必须**同步更新以下文件：
1. `docs/architecture.md`
2. `docs/full-architecture-flow.md`
3. `README.md`（若涉及用户可见行为）
4. `.github/skills/framework-quickstart/SKILL.md`
5. 相关的 `.github/skills/` 文件

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
| `sirius_chat/core/engine.py` | 核心编排引擎 |
| `sirius_chat/api/__init__.py` | 公开 API 导出清单 |
| `tests/conftest.py` | 测试最小 fixture |
| `scripts/ci_check.py` | 统一 CI 检查脚本 |
| `docs/architecture.md` | 架构边界与模块交互权威文档 |
| `docs/skill-authoring.md` | SKILL 编写规范 |
| `docs/best-practices.md` | 开发最佳实践 |
