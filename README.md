# 🌟 Sirius Chat

<div align="center">

[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-blue?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)](LICENSE)
[![PyPI](https://img.shields.io/badge/PyPI-sirius--chat-blueviolet?style=flat-square)](https://pypi.org/project/sirius-chat/)
[![Tests](https://img.shields.io/badge/Tests-600%2B%20passing-brightgreen?style=flat-square)](#-测试)
[![Async First](https://img.shields.io/badge/Async-First-orange?style=flat-square)](sirius_chat/async_engine/)

*一个为多人交互场景设计的 Python LLM 编排框架。构建具有真实情感表达能力、能提供帮助与情绪价值的核心引擎。*

[📚 文档](#-文档) • [🚀 快速开始](#-快速开始) • [💡 示例](#使用示例) • [🛠️ 配置](#-配置示例) • [🤝 贡献](#-贡献)

</div>

---

## 📋 目录

- [核心特性](#-核心特性)
- [快速开始](#-快速开始)
- [项目结构](#-项目结构)
- [使用示例](#使用示例)
- [配置指南](#-配置示例)
- [文档](#-文档)
- [测试](#-测试)
- [最新变更](#-最新变更-v0147)
- [贡献](#-贡献)

---

## 🎯 核心特性

### ✨ **多人交互架构**
- **异步编排引擎**：支持实时多人交互、动态参与者加入、自动身份识别
- **单 AI 主助手**：多人用户与一个主 AI 的标准模式，便于一致性管理
- **结构化 Transcript**：完整记录交互过程，便于下游系统消费

### 🧠 **智能记忆系统**
- **结构化用户记忆**：按分类（身份/偏好/情绪/事件）组织，支持置信度标记和冲突检测
- **AI 自身记忆**：日记系统 (Diary) 与名词解释系统 (Glossary)，支持遗忘曲线和定时提取
- **跨环境身份识别**：通过 `identities` 映射不同平台的外部账号到同一用户
- **事件记忆管理**：自动提取关键事件，支持历史事件命中评分

### 🚀 **性能与扩展**
- **智能缓存框架**：内存 LRU + TTL 缓存，支持 LLM 响应缓存
- **性能监控**：完整的 Token 消耗追踪、基准测试工具、执行指标分析
- **SKILL 系统**：可扩展的任务编排，支持链式调用与迭代反馈
- **高并发支持**：自动消息合并（debounce）、LLM 并发限流、后台任务隔离

### 🔌 **多模型协同**
- **多 Provider 支持**：OpenAI / NewAPI / DeepSeek / SiliconFlow / Volcengine Ark 等
- **任务级模型选择**：记忆提取、事件分析、多模态处理可配置独立模型
- **自动路由**：按 `healthcheck_model` 智能选择最合适的 Provider

### 🎬 **高级功能**
- **多模态处理**：支持图片/视频输入与结构化解析
- **CLI 与 API 双模式**：库调用 + 命令行交互，灵活接入
- **Provider 管理**：多平台 API Key 持久化，自动可用性检测
- **会话持久化**：JSON 与 SQLite 后端支持，无缝恢复

---

## 🚀 快速开始

### 1️⃣ **安装**

```bash
# 基础安装
python -m pip install -e .

# 含测试依赖
python -m pip install -e .[test]
```

### 2️⃣ **CLI 运行**

**库内 CLI（单轮调用）：**

```bash
sirius-chat --config examples/session.json --work-path data/session_runtime \
  --message "你好，请告诉我关于 LLM 的事" --output transcript.json
```

**项目入口（交互模式 + 持久化）：**

```bash
python main.py --config examples/session.json --work-path data/session_runtime --store json
```

**禁用自动恢复:**

```bash
python main.py --config examples/session.json --work-path data/session_runtime --no-resume
```

### 3️⃣ **CLI 命令说明**

| 命令 | 说明 |
|------|------|
| `sirius-chat` | 库内 CLI，单轮薄调用（不传 `--message` 时可交互输入） |
| `python main.py` | 仓库入口，维护持续会话、主用户档案、provider 配置 |
| `/reset-user` | 重置主用户档案（会话中输入） |
| `/provider platforms` | 查看支持的平台 |
| `/provider list` | 查看已配置 Provider |
| `/provider add <type> <api_key> <healthcheck_model> [base_url]` | 添加或更新 provider |
| `/provider remove <type>` | 删除 provider |

**会话存储选项：**

```bash
# 轻量、可读、适合单机单会话
python main.py --store json --config examples/session.json

# 强一致性、支持复杂查询
python main.py --store sqlite --config examples/session.json
```

### 4️⃣ **Python API 调用**

```python
import asyncio
from pathlib import Path
from sirius_chat.api import create_async_engine, SessionConfig, Agent, AgentPreset, Message, OrchestrationPolicy
from sirius_chat.providers.mock import MockProvider

async def main():
    provider = MockProvider(responses=["我理解您的想法", "这很有意思"])
    engine = create_async_engine(provider)
    
    config = SessionConfig(
        work_path=Path("./data/chat_session"),
        preset=AgentPreset(
            agent=Agent(name="助手", persona="耐心和善", model="gpt-4o-mini"),
            global_system_prompt="你是一个友善的助手。",
        ),
        orchestration=OrchestrationPolicy(message_debounce_seconds=0.0),
    )
    
    # 启动会话
    transcript = await engine.run_live_session(config=config)
    
    # 用户发言
    msg = Message(role="user", speaker="小王", content="Python 如何学习？")
    transcript = await engine.run_live_message(config=config, turn=msg, transcript=transcript)
    
    print(transcript.as_chat_history())

asyncio.run(main())
```

---

## 📁 项目结构

```
sirius_chat/
├── __init__.py
├── api/                          # 🔌 公开 API 入口
│   └── __init__.py
├── async_engine/                 # 🧠 异步编排引擎（核心）
│   ├── core.py                   # AsyncRolePlayEngine 主类
│   ├── prompts.py                # 系统提示词构建
│   ├── utils.py                  # 工具函数
│   └── orchestration.py          # 任务编排配置
├── config/                       # ⚙️ 配置模型 (dataclass)
│   └── models.py                 # SessionConfig / OrchestrationPolicy
├── core/                         # 🔧 引擎核心逻辑
│   └── engine.py                 # 消息处理、参与决策
├── memory/                       # 📝 记忆系统
│   ├── user_memory.py            # 用户记忆管理
│   ├── self_memory.py            # AI 自身记忆（日记 + 名词表）
│   └── event_memory.py           # 事件记忆
├── models/                       # 📦 数据模型（契约）
│   └── models.py                 # Transcript / Message / UserProfile
├── providers/                    # 🔗 LLM Provider 实现
│   ├── openai_compatible.py      # OpenAI 兼容接口
│   ├── newapi.py                 # NewAPI 兼容接口
│   ├── deepseek.py               # DeepSeek 适配
│   ├── siliconflow.py            # SiliconFlow 适配
│   └── volcengine_ark.py         # 火山方舟适配
├── session/                      # 💾 会话持久化
│   ├── store.py                  # JSON / SQLite 后端
│   └── runner.py                 # JsonPersistentSessionRunner
└── skills/                       # 🎯 SKILL 系统
    ├── executor.py               # 任务执行器
    └── models.py                 # SKILL 数据模型

tests/                            # ✅ 单元测试 (600+ 个)
├── test_engine.py                # 引擎层
├── test_async_engine.py          # 异步编排
├── test_memory_system_v2.py      # 记忆系统
├── test_skill_system.py          # SKILL 系统
└── ...

docs/                             # 📚 文档
├── architecture.md               # 架构总览
├── orchestration-policy.md       # 编排策略
├── configuration.md              # 配置指南
├── full-architecture-flow.md     # 详细数据流
└── external-usage.md             # 库调用指南

examples/                         # 💡 使用示例
├── session.json                  # 基础会话配置
└── *.py                          # Python 代码示例

scripts/                          # 🔨 开发脚本
├── setup_dev_env.py             # 开发环境设置
└── generate_api_docs.py         # API 文档生成
```

---

## 使用示例

### 示例 1：基础多人对话

```python
import asyncio
from pathlib import Path
from sirius_chat.api import create_async_engine, SessionConfig, Agent, Message, OrchestrationPolicy

async def multi_user_chat():
    from sirius_chat.providers.mock import MockProvider
    provider = MockProvider(responses=["我理解您的想法", "这很有意思"])
    engine = create_async_engine(provider)
    
    config = SessionConfig(
        work_path=Path("./data/chat_session"),
        agent=Agent(name="助手", persona="耐心和善", model="gpt-4o-mini"),
        orchestration=OrchestrationPolicy(message_debounce_seconds=0.0),
    )
    
    # 启动会话
    transcript = await engine.run_live_session(config=config)
    
    # 用户 1 发言
    msg1 = Message(role="user", speaker="小王", content="Python 如何学习？")
    transcript = await engine.run_live_message(config=config, turn=msg1, transcript=transcript)
    
    # 用户 2 发言
    msg2 = Message(role="user", speaker="小李", content="我也想学")
    transcript = await engine.run_live_message(config=config, turn=msg2, transcript=transcript)
    
    print(transcript.as_chat_history())

asyncio.run(multi_user_chat())
```

### 示例 2：启用高级功能

```python
config = SessionConfig(
    work_path=Path("./data/advanced_session"),
    agent=Agent(name="高级助手", persona="详细专业", model="gpt-4"),
    orchestration=OrchestrationPolicy(
        unified_model="gpt-4",
        # ✨ 记忆系统
        enable_self_memory=True,                           # 启用 AI 自身记忆
        self_memory_extract_batch_size=3,                 # 每 3 条 AI 回复提取一次
        self_memory_min_chars=400,                        # 或单条回复超过 400 字时提取
        # 🎯 任务编排
        task_enabled={
            "memory_extract": True,
            "event_extract": True,
        },
        task_models={
            "memory_extract": "gpt-3.5-turbo",  # 用廉价模型提取
            "event_extract": "gpt-3.5-turbo",
        },
        # 💬 参与决策
        engagement_sensitivity=0.7,                        # 更主动回复
        heat_window_seconds=60,
        # ⏱️ 消息合并
        message_debounce_seconds=5.0,                     # 5s 内的消息合并
    ),
)
```

### 示例 3：多模态输入

```python
from sirius_chat.models import Message

msg = Message(
    role="user",
    speaker="用户",
    content="请结合图片分析这项内容",
    multimodal_inputs=[
        {"type": "image", "value": "https://example.com/demo.png"}
    ],
)
```

更多示例见 [`examples/`](examples/) 目录。

---

## ⚙️ 配置示例

### 🔹 OpenAI 配置

```json
{
  "providers": [
    {
      "type": "openai-compatible",
      "base_url": "https://api.openai.com/v1",
      "api_key": "sk-..."
    }
  ],
  "agent": {
    "name": "Claude",
    "persona": "helpful",
    "model": "gpt-4o"
  },
  "history_max_messages": 24,
  "history_max_chars": 6000
}
```

### 🔹 DeepSeek 配置

```json
{
  "providers": [
    {
      "type": "deepseek",
      "api_key": "sk-..."
    }
  ],
  "agent": {
    "name": "DeepSeek",
    "model": "deepseek-chat"
  }
}
```

### 🔹 SiliconFlow 配置

```json
{
  "providers": [
    {
      "type": "siliconflow",
      "api_key": "sk-..."
    }
  ],
  "agent": {
    "name": "GLM",
    "model": "Pro/glm-4.5"
  }
}
```

**说明：** 框架会自动规范化路径，支持 `https://api.siliconflow.cn` 或 `https://api.siliconflow.cn/v1`

### 🔹 NewAPI 配置

```json
{
  "providers": [
    {
      "type": "newapi",
      "api_key": "sk-...",
      "base_url": "https://docs.newapi.pro"
    }
  ],
  "agent": {
    "name": "NewAPI",
    "model": "gpt-4o-mini"
  }
}
```

**说明：** NewAPI 文档声明 AI 模型接口兼容 OpenAI 格式，使用 `/v1/chat/completions`。

### 🔹 火山方舟（Volcengine Ark）配置

```json
{
  "providers": [
    {
      "type": "volcengine-ark",
      "api_key": "sk-..."
    }
  ],
  "agent": {
    "name": "豆包",
    "model": "doubao-seed-2-0-lite-260215"
  }
}
```

**说明：** 默认使用 `https://ark.cn-beijing.volces.com/api/v3`

### 🔹 多 Provider 自动路由

```json
{
  "providers": [
    {
      "type": "siliconflow",
      "api_key": "sk-sf-...",
      "healthcheck_model": "Pro/glm-4.5"
    },
    {
      "type": "openai-compatible",
      "base_url": "https://api.openai.com/v1",
      "api_key": "sk-open-...",
      "healthcheck_model": "gpt-4o-mini"
    }
  ],
  "agent": {
    "name": "主助手",
    "model": "Pro/glm-4.5"
  }
}
```

**路由规则：**
1. 按 `healthcheck_model` 与请求模型名做精确匹配
2. 无匹配时回退到第一个可用 provider
3. 无任何可用 provider 时抛出错误

### 🔹 多模型任务编排

```json
{
  "orchestration": {
    "task_enabled": {
      "memory_extract": true,
      "event_extract": true
    },
    "task_models": {
      "memory_extract": "gpt-3.5-turbo",
      "event_extract": "gpt-3.5-turbo"
    },
    "task_budgets": {
      "memory_extract": 1200,
      "event_extract": 1000
    },
    "task_temperatures": {
      "memory_extract": 0.1,
      "event_extract": 0.1
    },
    "task_max_tokens": {
      "memory_extract": 128,
      "event_extract": 192
    },
    "task_retries": {
      "memory_extract": 1,
      "event_extract": 1
    },
    "memory_extract_batch_size": 3,
    "memory_extract_min_content_length": 50,
    "max_multimodal_inputs_per_turn": 4,
    "max_multimodal_value_length": 4096
  }
}
```

**说明：**

- **多模型协同已成为默认方式**，所有任务默认启用，可通过 `task_enabled` 按需禁用
- 图片不再经过 `multimodal_parse` 辅助任务；会直接随用户消息以 vision 格式发送给主模型
- `memory_extract` 频率控制：
  - `batch_size=3` 表示每 3 条消息提取一次
  - `min_content_length=50` 表示只提取 ≥50 字符的消息
  - 两个条件同时满足时才执行
- **预算超限或任务失败** 自动回退到启发式逻辑
- `max_concurrent_llm_calls` 可配置（默认 1）：LLM 并发数限流
- `message_debounce_seconds` 可配置（默认 5.0）：高并发场景自动合并

---

## 💾 会话管理

### 状态持久化路径

在每个 `work_path` 下自动生成以下文件：

| 文件 | 说明 |
|------|------|
| `primary_user.json` | 主用户档案（首次启动交互生成） |
| `provider_keys.json` | Provider 配置（通过 CLI `/provider` 命令管理） |
| `session_config.persisted.json` | 当前会话配置 |
| `session_state.json` | 会话状态（支持恢复） |
| `token_usage.db` | Token 消耗计量（SQLite） |

### 主用户档案管理

在 CLI 交互中可运行时更新主用户档案，会实时持久化到 `<work_path>/primary_user.json`。

每个配置文件启动时，路径会记录到仓库根目录 `.last_config_path`。

### Token 消耗分析

```python
from sirius_chat.api import summarize_token_usage, build_token_usage_baseline

# 单会话统计
summary = summarize_token_usage(transcript)

# 基准指标
baseline = build_token_usage_baseline(transcript.token_usage_records)
```

跨会话分析可通过 `TokenUsageStore` 实现全维度分组。

---

## 🎬 高级功能

### 角色扮演前置内容生成

```python
from sirius_chat.roleplay_prompting import (
    generate_humanized_roleplay_questions,
    agenerate_agent_prompts_from_answers,
    load_generated_agent_library,
    select_generated_agent_profile,
)

# 生成问题清单
questions = generate_humanized_roleplay_questions()

# 基于答案生成 Agent 配置
preset = await agenerate_agent_prompts_from_answers(
    answers={...},
    agent_name="我的助手",
    provider=provider,
)

# 管理生成的 Agent 资产
agents = load_generated_agent_library("./data/session")
selected = select_generated_agent_profile("./data/session", "agent_key_1")
```

### SKILL 系统

SKILL 系统支持可扩展任务编排：

- 在 `work_path` 下自动初始化 `skills/` 目录
- 支持外部 Python 技能文件
- 链式调用与迭代反馈

详见 [`docs/skill-authoring.md`](docs/skill-authoring.md)。

---

## 📚 文档

| 文件 | 描述 |
|------|------|
| [📖 architecture.md](docs/architecture.md) | 完整架构设计、消息流、模块交互 |
| [⚙️ orchestration-policy.md](docs/orchestration-policy.md) | 多模型编排、任务路由、预算控制 |
| [🔧 configuration.md](docs/configuration.md) | 所有配置字段说明和最佳实践 |
| [📋 full-architecture-flow.md](docs/full-architecture-flow.md) | 详细数据流图解 |
| [🎬 external-usage.md](docs/external-usage.md) | 库调用指南与集成文档 |
| [📘 skill-authoring.md](docs/skill-authoring.md) | SKILL 系统编写规范 |
| [🛠️ best-practices.md](docs/best-practices.md) | 最佳实践与模式 |

---

## 🧪 测试

```bash
# 运行所有测试
python -m pytest tests/ -q

# 运行特定模块
python -m pytest tests/test_engine.py -v

# 显示最慢的 10 个测试
python -m pytest tests/ --durations=10

# 覆盖率分析
python -m pytest tests/ --cov=sirius_chat

# 快速验证单个测试
python -m pytest tests/test_engine.py::test_roleplay_engine_multi_human_single_ai_transcript -xvs
```

**测试特性：**

- ✅ **600+ 单元测试**：涵盖引擎、记忆、编排、技能系统
- ⚡ **快速执行**：< 15 秒全套（通过禁用 debounce）
- 🔒 **完全隔离**：无真实网络调用，全量 Mock
- 📊 **92% 代码覆盖**：关键路径完整测试

---

## 🆕 最新变更 (v0.14.7)

### ✨ **新增**
- **`write-tests` SKILL**：测试编写完整规范与最佳实践指南
- **SelfMemory 计数触发**：按 `self_memory_extract_batch_size` / `self_memory_min_chars` 在主流程中触发

### 🚀 **改进**
- **消息合并优化**：高并发场景自动合并（`message_debounce_seconds=5.0` 生产默认，测试须设 `0.0`）
- **LLM 并发限流**：可配置并发数（`max_concurrent_llm_calls` 默认 1，使用 `asyncio.Semaphore`）
- **脚本优化**：自动化修复工具 (`fix_debounce_*.py`) 简化批量更新
- **性能提升**：测试执行时间 605s → 13s（通过消除不必要的 debounce 等待）

**测试须知：**
> 生产环境采用 `message_debounce_seconds=5.0` 进行消息合并，测试环境必须显式设为 `0.0` 跳过等待，保证测试速度 < 1s/test

更多信息见 [CHANGELOG.md](CHANGELOG.md)。

---

## 🤝 贡献

欢迎贡献！请遵循以下流程：

1. **Fork** 项目并创建分支：`git checkout -b feature/my-feature`
2. **编辑代码** 并编写测试（参考 [.github/skills/write-tests/SKILL.md](.github/skills/write-tests/SKILL.md)）
3. **验证**：`python -m pytest tests/ -q`
4. **提交**：遵循 [conventional commits](https://www.conventionalcommits.org/) 格式
5. **推送** 并发起 Pull Request

### 开发环境

```bash
# 安装开发依赖
python -m pip install -e .[dev]

# 运行代码检查
python -m pytest tests/ --cov=sirius_chat
```

---

## 📄 许可证

MIT License © 2025 Sparrived. 详见 [LICENSE](LICENSE)。

---

## 🔗 相关链接

- 📦 [PyPI 项目页](https://pypi.org/project/sirius-chat/)
- 📚 [完整文档](docs/)
- 🐛 [报告问题](https://github.com/Sparrived/SiriusChat/issues/new)
- 💬 [讨论区](https://github.com/Sparrived/SiriusChat/discussions)

---

<div align="center">

**Made with ❤️ by the Sirius Chat team**

⭐ 如果觉得有帮助，欢迎给个 Star！

</div>