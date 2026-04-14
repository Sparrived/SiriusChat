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
- [最新变更](#-最新变更)
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
- **多 Provider 支持**：OpenAI / 阿里云百炼 / DeepSeek / SiliconFlow / Volcengine Ark 等
- **任务级模型选择**：记忆提取、事件分析、意图分析等任务可配置独立模型
- **自动路由**：按 `healthcheck_model` 智能选择最合适的 Provider

### 🎬 **高级功能**
- **多模态处理**：支持图片/视频输入与结构化解析
- **CLI 与 API 双模式**：库调用 + 命令行交互，灵活接入
- **Provider 管理**：多平台 API Key 持久化，自动可用性检测
- **WorkspaceRuntime 持久化接管**：外部至少提供运行态 `work_path`，必要时可额外提供独立 `config_root`；框架自动恢复会话、参与者元数据与持久化布局，并通过文件监听即时刷新外部修改的 workspace/config/provider/roleplay 配置

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
  --config-root data/session_config \
  --message "你好，请告诉我关于 LLM 的事" --output transcript.json
```

**项目入口（交互模式 + 持久化）：**

```bash
python main.py --config examples/session.json --work-path data/session_runtime \
  --config-root data/session_config --store json
```

- `--work-path`：运行态数据根目录，保存 `sessions/`、`memory/`、`token/`、`skill_data/`、`primary_user.json`
- `--config-root`：配置根目录，保存 `workspace.json`、`config/`、`providers/`、`roleplay/`、`skills/`；不传时默认回退到 `--work-path`
- `--config`：支持 JSON/JSONC；`--init-config <path>` 生成的模板会带内联注释，便于直接修改

**人格问卷模板辅助命令：**

```bash
# 列出可用模板
sirius-chat --list-roleplay-question-templates

# 导出指定模板的问题清单 JSON
sirius-chat --print-roleplay-questions-template companion
```

**禁用自动恢复:**

```bash
python main.py --config examples/session.json --work-path data/session_runtime \
  --config-root data/session_config --no-resume
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

**推荐入口：WorkspaceRuntime（自动恢复与落盘）**

```python
import asyncio
from pathlib import Path

from sirius_chat.api import Message, UserProfile, open_workspace_runtime
from sirius_chat.providers.mock import MockProvider


async def main() -> None:
  runtime = open_workspace_runtime(
    Path("./data/chat_session"),
    config_path=Path("./config/chat_session"),
    provider=MockProvider(responses=["我理解您的想法"]),
  )

  transcript = await runtime.run_live_message(
    session_id="group:demo",
    turn=Message(role="user", speaker="小王", content="Python 如何学习？"),
    user_profile=UserProfile(user_id="u_xiaowang", name="小王"),
  )

  print(transcript.as_chat_history())


asyncio.run(main())
```

> `work_path` 保存运行态数据；`config_path` 保存 workspace 配置、provider 与角色资产。不传 `config_path` 时，runtime 会回退到单根模式。外部修改 `config_path` 下的 workspace/config/provider/roleplay 文件后，runtime 会通过文件监听自动刷新；单轮调用前仍保留一次签名校验作为兜底。

**底层模式：AsyncRolePlayEngine + SessionConfig（高级控制）**

```python
import asyncio
from pathlib import Path
from sirius_chat.api import create_async_engine, SessionConfig, Agent, AgentPreset, Message, OrchestrationPolicy
from sirius_chat.providers.mock import MockProvider

async def main():
    provider = MockProvider(responses=["我理解您的想法", "这很有意思"])
    engine = create_async_engine(provider)
    
    config = SessionConfig(
      work_path=Path("./config/chat_session"),
      data_path=Path("./data/chat_session"),
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
├── workspace/                    # 🗂️ workspace 布局、迁移与运行时
│   ├── layout.py                 # WorkspaceLayout 路径解析
│   ├── migration.py              # 旧布局迁移
│   └── runtime.py                # WorkspaceRuntime 自动持久化入口
├── memory/                       # 📝 记忆系统
│   ├── user_memory.py            # 用户记忆管理
│   ├── self_memory.py            # AI 自身记忆（日记 + 名词表）
│   └── event_memory.py           # 事件记忆
├── models/                       # 📦 数据模型（契约）
│   └── models.py                 # Transcript / Message / UserProfile
├── providers/                    # 🔗 LLM Provider 实现
│   ├── openai_compatible.py      # OpenAI 兼容接口
│   ├── aliyun_bailian.py         # 阿里云百炼适配
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
  "generated_agent_key": "main_agent",
  "providers": [
    {
      "type": "openai-compatible",
      "base_url": "https://api.openai.com/v1",
      "api_key": "sk-..."
    }
  ],
  "history_max_messages": 24,
  "history_max_chars": 6000
}
```

### 🔹 DeepSeek 配置

```json
{
  "generated_agent_key": "main_agent",
  "providers": [
    {
      "type": "deepseek",
      "api_key": "sk-..."
    }
  ]
}
```

### 🔹 阿里云百炼（Aliyun Bailian）配置

```json
{
  "generated_agent_key": "main_agent",
  "providers": [
    {
      "type": "aliyun-bailian",
      "api_key": "sk-..."
    }
  ]
}
```

**说明：** 默认使用 `https://dashscope.aliyuncs.com/compatible-mode`，也兼容传入 `https://dashscope.aliyuncs.com/compatible-mode/v1`；如需美国站或国际站，可通过 `base_url` 显式覆盖。

### 🔹 SiliconFlow 配置

```json
{
  "generated_agent_key": "main_agent",
  "providers": [
    {
      "type": "siliconflow",
      "api_key": "sk-..."
    }
  ]
}
```

**说明：** 框架会自动规范化路径，支持 `https://api.siliconflow.cn` 或 `https://api.siliconflow.cn/v1`

### 🔹 火山方舟（Volcengine Ark）配置

```json
{
  "generated_agent_key": "main_agent",
  "providers": [
    {
      "type": "volcengine-ark",
      "api_key": "sk-..."
    }
  ]
}
```

**说明：** 默认使用 `https://ark.cn-beijing.volces.com/api/v3`

### 🔹 多 Provider 自动路由

```json
{
  "generated_agent_key": "main_agent",
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
  ]
}
```

**路由规则：**
1. 按 `healthcheck_model` 与请求模型名做精确匹配
2. 无匹配时回退到第一个可用 provider
3. 无任何可用 provider 时抛出错误

### 🔹 多模型任务编排

```json
{
  "generated_agent_key": "main_agent",
  "providers": [
    {
      "type": "openai-compatible",
      "base_url": "https://api.openai.com/v1",
      "api_key": "sk-open-...",
      "healthcheck_model": "gpt-4o-mini"
    }
  ],
  "orchestration": {
    "task_enabled": {
      "memory_extract": true,
      "event_extract": true,
      "intent_analysis": true
    },
    "task_models": {
      "memory_extract": "gpt-3.5-turbo",
      "event_extract": "gpt-3.5-turbo",
      "intent_analysis": "gpt-4o-mini"
    },
    "task_budgets": {
      "memory_extract": 1200,
      "event_extract": 1000,
      "intent_analysis": 600
    },
    "task_temperatures": {
      "memory_extract": 0.1,
      "event_extract": 0.1,
      "intent_analysis": 0.1
    },
    "task_max_tokens": {
      "memory_extract": 128,
      "event_extract": 192,
      "intent_analysis": 192
    },
    "task_retries": {
      "memory_extract": 1,
      "event_extract": 1,
      "intent_analysis": 1
    },
    "memory_extract_batch_size": 3,
    "memory_extract_min_content_length": 50,
    "max_multimodal_inputs_per_turn": 4,
    "max_multimodal_value_length": 4096
  }
}
```

说明：`main.py` 和 `sirius-chat` 读取的是轻量会话配置文件，要求提供 `generated_agent_key` 与 `providers`。完整的 `agent` / `global_system_prompt` 由 `roleplay/generated_agents.json` 中已保存的人格资产提供；如果你要手写完整 `SessionConfig`，请改用 Python API 的低层入口。

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

从 `v0.24.0` 起，推荐把 workspace 视为“配置根 + 运行根”的组合：配置资产可单独放在 config root，运行态数据放在 data root；未显式拆分时仍兼容单根模式。推荐布局如下：

| 文件 | 说明 |
|------|------|
| `workspace.json` | workspace 级配置清单与布局版本 |
| `config/session_config.json` | 可读的 session 默认配置快照（JSONC 注释模板，可直接人工编辑） |
| `providers/provider_keys.json` | Provider 注册表与路由元数据 |
| `sessions/<session_id>/session_state.db` | 默认会话状态（结构化 SQLite，可恢复；自动迁移旧 `session_state.json` / payload SQLite） |
| `sessions/<session_id>/participants.json` | 会话参与者与主用户元数据 |
| `memory/users/*.json` | 用户记忆持久化 |
| `memory/events/events.json` | 事件记忆持久化 |
| `memory/self_memory.json` | AI 自身记忆持久化 |
| `token/token_usage.db` | Token 消耗计量（SQLite） |
| `roleplay/generated_agents.json` | 已生成的人格资产库 |
| `roleplay/generated_agent_traces/<agent_key>.json` | 人格生成轨迹与失败快照 |
| `skills/` | SKILL 目录与 README 引导 |
| `skill_data/*.json` | SKILL 独立数据存储 |

旧的根目录 `session_state.json`、`session_state.db`、`provider_keys.json`、`generated_agents.json` 等文件会在首次打开 workspace 时自动迁移到新布局；`primary_user.json` 和 `session_config.persisted.json` 仅保留给兼容入口。

如果需要显式执行一次迁移并查看结果，可运行 `python examples/migrate_session_store.py --work-path <你的工作目录>`。

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
from sirius_chat.api import (
  RolePlayAnswer,
  aregenerate_agent_prompt_from_dependencies,
  abuild_roleplay_prompt_from_answers_and_apply,
  generate_humanized_roleplay_questions,
  list_roleplay_question_templates,
  load_persona_generation_traces,
  load_generated_agent_library,
  select_generated_agent_profile,
)

# 查看可用问卷模板，并选择更贴合场景的一套高层问题
print(list_roleplay_question_templates())
questions = generate_humanized_roleplay_questions(template="companion")

answers = [
    RolePlayAnswer(
        question=questions[0].question,
        answer="像一个晚熟但可靠的陪伴者，平时不抢话，但会长期在场，熟了以后很护短。",
        perspective=questions[0].perspective,
    ),
    RolePlayAnswer(
        question=questions[1].question,
        answer="用户低落时先接住情绪，再慢慢帮对方理清思路，不会一上来就讲道理。",
        perspective=questions[1].perspective,
    ),
    RolePlayAnswer(
        question=questions[6].question,
        answer="偶尔嘴硬、会记小事，也会在疲惫时变得更安静，但不会无限兜底。",
        perspective=questions[6].perspective,
    ),
]

# 直接生成并写入 SessionConfig，同时挂接本地素材文件
prompt = await abuild_roleplay_prompt_from_answers_and_apply(
    provider=provider,
    config=config,
    model="deepseek-ai/DeepSeek-V3.2",
    agent_name="我的助手",
    answers=answers,
    dependency_files=["persona/notes.md", "persona/style_examples.txt"],
    persona_key="assistant_v2",
  timeout_seconds=120.0,
)

# 查看完整生成轨迹
traces = load_persona_generation_traces(config.work_path, "assistant_v2")

# 当依赖文件变化后，直接基于文件重生同一个人格
updated = await aregenerate_agent_prompt_from_dependencies(
    provider,
    work_path=config.work_path,
    agent_key="assistant_v2",
    model="deepseek-ai/DeepSeek-V3.2",
)

# 管理生成的 Agent 资产
library, selected_key = load_generated_agent_library(config.work_path)
selected = select_generated_agent_profile(config.work_path, "assistant_v2")
```

说明：

- 推荐先用高层人格 brief 来描述人物原型、核心矛盾、关系策略、情绪原则、边界和小缺点，再让生成器落成具体人物小传与语言习惯。
- `generate_humanized_roleplay_questions(template=...)` 支持 `default`、`companion`、`romance`、`group_chat` 四类问卷模板，可配合 `list_roleplay_question_templates()` 做前端下拉或外部配置。
- 若外部系统只想先拿模板问题，不想立刻接入 Python API，可直接用 `sirius-chat --list-roleplay-question-templates` 和 `sirius-chat --print-roleplay-questions-template <template>`。
- 生成器会自动识别“拟人”“情感”“陪伴”“共情”等关键词并加强 prompt，让角色更自然、更有人味。
- 结构化人格生成默认使用 `max_tokens=5120` 和 `timeout_seconds=120.0`；如果上游模型更慢，仍可在这几个 API 上继续显式调高 `timeout_seconds`。
- 如果模型返回的是被 ```json 包裹但实际被截断的 JSON-like 响应，框架会显式报错并保留失败 trace，不再把原始文本污染到 `agent.persona` 或 `global_system_prompt`。
- 完整生成过程会本地化到 `<work_path>/roleplay/generated_agent_traces/<agent_key>.json`，便于审计和回滚。
- 外部调用方可直接按 `template + answers + dependency_files` 组织输入，示例输入规范见 [docs/external-usage.md](docs/external-usage.md)。
- 可直接参考 `examples/roleplay_template_selection.py` 导出 `PersonaSpec` 骨架，再交给外部表单或配置后台填充。
- 面向外部调用方的迁移说明见 [docs/migration-roleplay-v0.20.md](docs/migration-roleplay-v0.20.md)。

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
| [🗂️ migration-v0.23.md](docs/migration-v0.23.md) | workspace 持久化接管迁移档案 |
| [🗂️ migration-v0.24.md](docs/migration-v0.24.md) | JSONC 配置与 watcher 热刷新迁移档案 |
| [🔄 migration-roleplay-v0.20.md](docs/migration-roleplay-v0.20.md) | 外部人格生成能力迁移指南 |
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

## 🆕 最新变更

### ✨ **新增**
- **JSONC 配置模板**：`--init-config` 和 workspace 自动写出的 `config/session_config.json` 现在都使用带注释的 JSONC 模板，便于外部直接维护。
- **即时配置热刷新**：`WorkspaceRuntime` 改为通过 watcher 监听 `workspace.json`、`config/session_config.json`、`providers/provider_keys.json` 与 `roleplay/generated_agents.json`，配置变更会尽快生效。

### 🚀 **改进**
- **双根目录彻底打通**：`--config-root` / `config_path` 负责 workspace、provider、roleplay、skills；`--work-path` / `data_path` 负责 session、memory、token、skill_data 与主用户运行态数据。
- **示例和入口统一到新配置系统**：`main.py`、`sirius-chat`、仓库示例配置与相关文档全部收敛到 `generated_agent_key + providers + orchestration` 形态。

**迁移提示：**
> 若你已经在使用 v0.23 的 workspace 持久化，请继续阅读 `docs/migration-v0.24.md`，重点关注 JSONC 配置和 watcher 热刷新语义。

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