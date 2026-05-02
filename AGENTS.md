# Sirius Chat — Agent 开发指南

> 本文档面向 AI Coding Agent。如果你对本项目一无所知，请从这里开始阅读。

---

## 项目概述

**Sirius Chat**（PyPI 包名 `sirius-chat`）是一个**支持多人格启用的异步角色扮演程序**。它为 QQ 群聊等场景设计，以异步优先（Async-First）架构为核心，支持多个人格同时运行，每个人格独立进程、独立配置、独立记忆，具备真实情感表达与群聊互动能力。

- **版本**：`1.1.0`
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
| `provider` | `tenacity>=8.0.0`（重试）；`httpx>=0.24.0` 用于平台适配层（OneBot WS）和 WebUI，provider 层使用标准库 `urllib.request` |
| `dev` | 以上全部 + `black`, `isort`, `flake8`, `pylint`, `mypy`, `bandit`, `pre-commit`, `build`, `twine` |
| `quality` | `tiktoken>=0.5.0`（精确 token 估算） |

### 构建系统
- 使用 `setuptools>=61.0` 作为 PEP 517 build backend。
- 包发现规则：`include = ["sirius_chat*"]`。

### 运行时架构（v1.0 多人格）

```
主进程（CLI / WebUI）
    ├── PersonaManager          # 扫描人格目录、端口分配、启停调度
    ├── WebUIServer             # aiohttp REST API + 静态页面
    └── NapCatManager           # NapCat 全局安装/多实例管理
            │
            ▼
    子进程（独立控制台窗口）
    ├── PersonaWorker ── EngineRuntime ── EmotionalGroupChatEngine
    │       │
    │       ├── NapCatBridge ── NapCatAdapter ── NapCat OneBot v11 WS
    │       ├── BasicMemoryManager + DiaryManager + SemanticMemory
    │       ├── ModelRouter（任务感知模型选择）
    │       └── SkillRegistry + SkillExecutor
    │
    └── ...（多个人格并行）
```

---

## 项目结构与主要模块

```
sirius_chat/
├── __init__.py              # 顶层公开 API 统一重导出（严格 __all__）
├── config/                  # 配置模型、JSONC 管理、WorkspaceConfig / SessionConfig
├── configs/                 # 内置配置模板
├── core/                    # 编排核心（v1.0 唯一引擎）
│   ├── emotional_engine.py  # EmotionalGroupChatEngine（主引擎，v1.0）
│   ├── persona_generator.py # 人格生成器（关键词/问卷）
│   ├── persona_store.py     # 人格持久化（persona.json）
│   ├── response_assembler.py # 执行层：Prompt 组装 + 风格适配
│   ├── cognition.py         # 统一认知分析器（情感 + 意图）
│   ├── response_strategy.py # 四层响应策略（立即/延迟/沉默/主动）
│   ├── delayed_response_queue.py # 延迟响应队列（话题间隙检测）
│   ├── proactive_trigger.py # 主动触发器（时间/记忆/情感触发）
│   ├── rhythm.py            # 对话节奏分析（热度/速度/注意力窗口）
│   ├── threshold_engine.py  # 动态阈值引擎（Base × Activity × Relationship × Time）
│   ├── events.py            # 会话事件流
│   └── identity_resolver.py # 跨平台身份解析
├── memory/                  # 记忆子包（v1.0 简化架构）
│   ├── basic/               # 基础记忆（工作窗口 + 热度 + 归档）
│   ├── diary/               # 日记记忆（LLM 生成、索引、检索）
│   ├── glossary/            # 名词解释（AI 自身知识）
│   ├── user/                # 用户管理（简化 UserProfile + UserManager）
│   ├── context_assembler.py # 上下文组装器（basic + diary → OpenAI messages）
│   └── semantic/            # 语义记忆（群规范学习、氛围记录、关系状态、持久化）
├── models/                  # 核心数据模型（dataclass）
│   ├── emotion.py           # EmotionState / AssistantEmotionState / EmpathyStrategy
│   ├── intent_v3.py         # IntentAnalysisV3 / SocialIntent
│   ├── response_strategy.py # StrategyDecision / ResponseStrategy
│   └── models.py            # Message / Participant / Transcript / User 等
├── platforms/               # 平台适配层（v1.0 新增）
│   ├── napcat_manager.py    # NapCat 环境管理器（全局安装 + 多实例）
│   ├── napcat_adapter.py    # NapCat OneBot v11 WebSocket 适配
│   ├── napcat_bridge.py     # QQ 群聊/私聊桥接器
│   ├── runtime.py           # EngineRuntime 封装（人格子进程内）
│   ├── setup_wizard.py      # 首次启动配置向导（QQ 私聊交互式）
│   └── persona_utils.py     # 人格生成工具函数
├── persona_manager.py       # 多人格生命周期管理（主进程）
├── persona_worker.py        # 单个人格子进程入口
├── persona_config.py        # 人格级配置模型（adapters/experience/paths）
├── providers/               # Provider 实现、路由、中间件
│   ├── routing.py           # 自动路由与 ProviderRegistry（全局共享）
│   └── middleware/          # 重试、熔断、限流、成本监控
├── session/                 # 会话存储（JsonSessionStore / SqliteSessionStore）
├── skills/                  # SKILL 注册、执行与数据存储
│   └── builtin/             # 内置技能
├── token/                   # Token 统计、SQLite 持久化与分析
├── webui/                   # WebUI 管理面板
│   ├── server.py            # aiohttp REST API（多人格 API）
│   └── static/              # 前端页面（Dashboard + 配置面板）
├── utils/                   # WorkspaceLayout、JsonSerializable mixin、开发辅助
├── background_tasks.py      # 轻量级 asyncio 任务调度器
├── logging_config.py        # 日志配置（按日轮转、7 天备份）
└── roleplay_prompting.py    # 人格资产生成、持久化与选择（内部使用，不公开导出）

main.py                      # 统一 CLI 入口（子命令式：run/webui/persona）

tests/                       # 540+ 单元测试
docs/                        # 文档
examples/                    # 使用示例
scripts/                     # 开发脚本
```

### 主要入口点

| 入口 | 文件 | 用途 |
|------|------|------|
| `python main.py` | `main.py`（~480 行） | **默认启动 WebUI 管理模式**（无参数时） |
| `python main.py run` | `main.py` | 启动所有已启用人格 + NapCat 实例 + WebUI |
| `python main.py webui` | `main.py` | 仅启动 WebUI（不启动人格） |
| `python main.py persona create <name>` | `main.py` | 创建新人格 |
| `python main.py persona start <name>` | `main.py` | 前台启动单个人格（含 NapCat 自动管理） |
| `python main.py persona migrate` | `main.py` | 从旧版目录迁移人格 |
| `PersonaManager` | `sirius_chat/persona_manager.py` | **推荐生产入口**：多人格生命周期管理 |
| `PersonaWorker` | `sirius_chat/persona_worker.py` | 子进程入口（由 PersonaManager 调用） |
| `EngineRuntime` | `sirius_chat/platforms/runtime.py` | 单个人格运行时封装 |
| `EmotionalGroupChatEngine` | `sirius_chat/core/emotional_engine.py` | **v1.0 唯一引擎** |

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

### 强制约定
1. **每模块首行必须是 `from __future__ import annotations`**。
2. **`__all__` 纪律**：`sirius_chat/__init__.py` 定义严格的 `__all__`。
3. **禁止在顶层暴露内部包**：`core`、`memory`、`config`、`session`、`token` 等内部模块**不得**通过 `sirius_chat` 顶层直接访问；外部调用走 `sirius_chat` 顶层公开 API。
4. **模块级 logger**：使用标准库 `logging`，格式为 `'%(asctime)s - %(name)s - %(levelname)s - %(message)s'`。
5. **dataclass 优先**：核心数据契约使用 `@dataclass` 定义。
6. **原子文件写入**：配置持久化使用临时文件 + replace。
7. **中文为主**：源码注释、docstring、CLI 输出、用户可见异常信息均使用中文。
8. **Conventional Commits**：提交信息遵循 [Conventional Commits](https://www.conventionalcommits.org/) 格式。

---

## 多人格架构要点

### 数据隔离
```
data/
├── global_config.json              # 全局配置（webui_host/port、auto_manage_napcat、log_level）
├── providers/
│   └── provider_keys.json          # Provider 凭证（所有人格共用）
├── adapter_port_registry.json      # 端口分配表（PersonaManager 维护）
└── personas/
    └── {name}/                     # 人格隔离目录
        ├── persona.json            # 人格定义（PersonaProfile）
        ├── orchestration.json      # 模型编排（analysis/chat/vision model）
        ├── adapters.json           # 平台适配器（NapCatAdapterConfig 列表）
        ├── experience.json         # 体验参数（reply_mode、engagement_sensitivity 等）
        ├── engine_state/           # 运行状态（persona.json、orchestration.json、emotion、memory）
        ├── memory/                 # 语义记忆（semantic/global、semantic/groups、semantic/users）
        ├── diary/                  # 日记记忆（按 group_id 的 JSON 文件）
        ├── image_cache/            # 图片缓存
        ├── skill_data/             # 技能数据
        └── logs/                   # 文件日志（worker.log）
```

### 端口分配
`PersonaManager` 维护 `data/adapter_port_registry.json`，从 `global_config.napcat_base_port`（默认 3001）递增自动分配。每个 NapCat adapter 的 `ws_url` 格式为 `ws://localhost:{port}`。

### 进程模型
```
主进程（python main.py run）
    ├── 启动 NapCat 全局安装检查/自动安装
    ├── 为每个人格启动 NapCat 实例（独立 cwd、独立配置、共享全局二进制）
    ├── 启动所有人格子进程（subprocess.Popen + CREATE_NEW_CONSOLE）
    └── 启动 WebUI（aiohttp）

人格子进程（python -m sirius_chat.persona_worker --config {pdir}）
    ├── EngineRuntime（work_path = 人格目录）
    ├── NapCatBridge（读取 adapters.json 的 allowed_group_ids 等）
    └── 心跳：每 10 秒写入 engine_state/worker_status.json
```

### Provider 全局共享
所有人格共用 `data/providers/provider_keys.json`。`EngineRuntime._build_provider()` 优先从 `global_data_path`（即 `data/`）加载 ProviderRegistry，回退到人格目录（兼容旧版）。

`setup_wizard._save_providers_to_registry()` 保存到全局位置。

### NapCat 多实例
`NapCatManager.for_persona(global_install_dir, persona_name)` 创建 `napcat/instances/{name}/` 目录：
- 共享全局二进制（`NapCatWinBootMain.exe`、`NapCatWinBootHook.dll`、`napcat.mjs`）
- 独立配置（`config/napcat_{qq}.json`、`config/onebot11_{qq}.json`）
- 独立日志（`logs/`）
- 独立 `qqnt.json`（从全局复制）

`NapCatManager.start(qq_number)` 使用 `CREATE_NEW_CONSOLE` 在独立窗口启动。

### v1.0 认知架构（不变）
运行时数据流仍为四层 + 后台：
1. **感知层**：注册参与者 → 写入基础记忆 → 更新热度
2. **认知层**：IntentAnalyzer + EmotionAnalyzer + 记忆检索（并行）
3. **决策层**：RhythmAnalyzer + ThresholdEngine + ResponseStrategyEngine → IMMEDIATE/DELAYED/SILENT/PROACTIVE
4. **执行层**：ResponseAssembler → ContextAssembler → StyleAdapter → ModelRouter → LLM 生成
5. **后台更新层**：日记生成、群氛围更新、被动学习、关系更新、事件触发

---

## 测试策略

- **pytest** + **pytest-asyncio** + **pytest-cov**
- 完全隔离，无真实网络调用：所有测试使用 `MockProvider`。
- 540+ 单元测试，目标覆盖率约 92%。
- 快速执行：全套测试可在 <15 秒内完成。

---

## CI / CD 与发布流程

### GitHub Actions 工作流
- **`.github/workflows/ci.yml`**：push/pull_request 时触发，包含 test、lint、security、build。
- **`.github/workflows/publish.yml`**：推送 `v*` 标签时自动发布到 PyPI。

### 发布检查清单
1. 验证 `pyproject.toml` 版本号。
2. `pip install -e .[test]` + `pytest -q`。
3. 命令冒烟测试：`python main.py --help`。
4. 确认 bootstrap 文件已写入。
5. 同步 `README.md`、`docs/architecture.md`。
6. 打 tag 并推送。

---

## 快速参考：常用文件路径

| 路径 | 说明 |
|------|------|
| `pyproject.toml` | 项目元数据、依赖、pytest 配置 |
| `Makefile` | 开发工作流快捷命令 |
| `main.py` | 统一 CLI 入口 |
| `sirius_chat/core/emotional_engine.py` | v1.0 核心情感群聊引擎 |
| `sirius_chat/persona_manager.py` | 多人格生命周期管理 |
| `sirius_chat/persona_worker.py` | 子进程入口 |
| `sirius_chat/persona_config.py` | 人格级配置模型 |
| `sirius_chat/platforms/napcat_manager.py` | NapCat 多实例管理 |
| `sirius_chat/webui/server.py` | WebUI REST API |
| `sirius_chat/__init__.py` | 顶层公开 API 导出清单 |
| `tests/conftest.py` | 测试最小 fixture |
| `scripts/ci_check.py` | 统一 CI 检查脚本 |
| `docs/architecture.md` | 架构边界与模块交互权威文档 |

> **注意**：`ncatbot_env/` 是项目实际的 QQ Bot 运行测试环境，与 `sirius_chat` 主源码无直接耦合，清理代码时**不要删除或修改**此目录。
