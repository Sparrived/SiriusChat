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
- **AI 自身记忆**：日记系统 (Diary) 与名词解释系统 (Glossary)，支持遗忘曲线，并在长上下文或达到回复阈值时自动提取
- **跨环境身份识别**：通过 `identities` 映射不同平台的外部账号到同一用户
- **事件记忆管理**：自动提取关键事件，支持历史事件命中评分

### 🚀 **性能与扩展**
- **智能缓存框架**：内存 LRU + TTL 缓存，支持 LLM 响应缓存
- **性能监控**：完整的 Token 消耗追踪、基准测试工具、执行指标分析
- **SKILL 系统**：可扩展的任务编排，支持链式调用与迭代反馈
- **高并发支持**：会话积压静默批处理、LLM 并发限流、后台任务隔离

### 🔌 **多模型协同**
- **多 Provider 支持**：OpenAI / 智谱 BigModel（GLM-4.6V）/ 阿里云百炼 / DeepSeek / SiliconFlow / Volcengine Ark 等
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
>
> `WorkspaceBootstrap` 只会按 payload 签名持久化一次；同一份 bootstrap 在后续重启时不会再次覆盖你已经手工修改过的 workspace/config/provider 文件。若需要给已存在的 workspace 下发新默认值，请改用 `apply_workspace_updates()`、`set_provider_entries()`，或显式调整 bootstrap payload。

**BigModel GLM-4.6V 示例**

```python
from sirius_chat.api import BigModelProvider

provider = BigModelProvider(api_key="YOUR_BIGMODEL_API_KEY")
```

> `BigModelProvider` 默认请求 `https://open.bigmodel.cn/api/paas/v4/chat/completions`，兼容传入根域名 `https://open.bigmodel.cn` 或完整 `api/paas/v4` 前缀。多模态消息沿用 OpenAI 兼容的 `content` 列表格式，可直接用于 `glm-4.6v`。

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
        orchestration=OrchestrationPolicy(pending_message_threshold=0),
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
├── api/                          # 🔌 公开 API facade（engine/models/providers/session 等）
├── core/                         # 🧠 编排核心真实实现
│   ├── engine.py                 # AsyncRolePlayEngine
│   ├── chat_builder.py           # 主模型请求构造
│   ├── memory_runner.py          # 记忆相关辅助任务
│   ├── engagement_pipeline.py    # 热度/意图/参与协调流水线
│   ├── heat.py                   # 群聊热度分析
│   ├── intent_v2.py              # 意图分析
│   └── events.py                 # 会话事件流
├── async_engine/                 # 🧩 兼容导出 + prompts/orchestration/utils 辅助层
├── workspace/                    # 🗂️ layout/runtime/watcher/roleplay bootstrap
├── config/                       # ⚙️ WorkspaceConfig / SessionConfig / JSONC 管理
├── memory/                       # 📝 记忆子包
│   ├── user/
│   ├── event/
│   ├── self/
│   └── quality/
├── session/                      # 💾 SessionStore 与高层兼容 runner
├── providers/                    # 🔗 Provider 实现、路由与中间件
│   ├── routing.py
│   └── middleware/
├── token/                        # 📊 Token 统计、SQLite 持久化与分析
├── skills/                       # 🎯 SKILL 注册、执行与数据存储
├── roleplay_prompting.py         # 🎭 人格资产生成、持久化与选择
├── cache/                        # ⚡ 可扩展缓存框架
├── performance/                  # 📈 性能分析与基准测试
└── cli.py                        # 🖥️ 库内薄 CLI

tests/                            # ✅ 单元测试 (600+ 个)
├── test_engine.py                # 编排核心
├── test_workspace_runtime.py     # workspace 持久化与热刷新
├── test_provider_routing.py      # provider 注册表与自动路由
├── test_providers.py             # 各 provider 一致性
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

### 示例 1：推荐入口 WorkspaceRuntime

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

### 示例 2：低层入口 AsyncRolePlayEngine

```python
import asyncio
from pathlib import Path

from sirius_chat.api import Agent, AgentPreset, Message, OrchestrationPolicy, SessionConfig, create_async_engine
from sirius_chat.providers.mock import MockProvider


async def main() -> None:
  engine = create_async_engine(MockProvider(responses=["这很有意思"]))

  config = SessionConfig(
    work_path=Path("./config/chat_session"),
    data_path=Path("./data/chat_session"),
    preset=AgentPreset(
      agent=Agent(name="助手", persona="耐心和善", model="gpt-4o-mini"),
      global_system_prompt="你是一个友善、克制、连续性很强的助手。",
    ),
    orchestration=OrchestrationPolicy(
      unified_model="gpt-4o-mini",
      pending_message_threshold=0,
    ),
  )

  transcript = await engine.run_live_session(config=config)
  transcript = await engine.run_live_message(
    config=config,
    transcript=transcript,
    turn=Message(role="user", speaker="小王", content="我也想学"),
  )

  print(transcript.as_chat_history())


asyncio.run(main())
```

### 示例 3：多模态输入

```python
from sirius_chat.api import Message

msg = Message(
    role="user",
    speaker="用户",
    content="请结合图片分析这项内容",
    multimodal_inputs=[
        {"type": "image", "value": "https://example.com/demo.png"}
    ],
)
```

若使用 OpenAI-compatible 或 Aliyun Bailian 等 HTTP provider，`multimodal_inputs` 中也可以直接传本地图片路径或 `file://` URI；框架会在发送前自动转换为 Data URL。若使用公网 URL，请确保上游可以直接访问该地址。

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
1. 优先按 `models` 显式模型列表匹配
2. 若未命中，再按 `healthcheck_model` 与请求模型名做精确匹配
3. 仍未命中时回退到第一个可用 provider；若没有可用 provider，则抛出错误

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
      "intent_analysis": true,
      "memory_manager": true
    },
    "task_models": {
      "memory_extract": "gpt-3.5-turbo",
      "event_extract": "gpt-3.5-turbo",
      "intent_analysis": "gpt-4o-mini",
      "memory_manager": "gpt-4o-mini"
    },
    "task_temperatures": {
      "memory_extract": 0.1,
      "event_extract": 0.1,
      "intent_analysis": 0.1,
      "memory_manager": 0.3
    },
    "task_max_tokens": {
      "memory_extract": 128,
      "event_extract": 192,
      "intent_analysis": 192,
      "memory_manager": 256
    },
    "task_retries": {
      "memory_extract": 1,
      "event_extract": 1,
      "intent_analysis": 1,
      "memory_manager": 1
    },
    "memory_extract_batch_size": 3,
    "memory_extract_min_content_length": 50,
    "min_reply_interval_seconds": 15,
    "max_multimodal_inputs_per_turn": 4,
    "max_multimodal_value_length": 4096
  }
}
```

说明：`main.py` 和 `sirius-chat` 读取的是轻量会话配置文件，要求提供 `generated_agent_key` 与 `providers`。完整的 `agent` / `global_system_prompt` 由 `roleplay/generated_agents.json` 中已保存的人格资产提供；如果你要手写完整 `SessionConfig`，请改用 Python API 的低层入口。

**说明：**

- **多模型协同已成为默认方式**，所有任务默认启用，可通过 `task_enabled` 按需禁用
- 图片不再经过 `multimodal_parse` 辅助任务；会直接随用户消息以 vision 格式发送给主模型
- `memory_manager` 已纳入标准任务路由；模型、温度、max_tokens、重试统一通过 `task_models/task_temperatures/task_max_tokens/task_retries` 配置
- `memory_extract` 频率控制：
  - `batch_size=3` 表示每 3 条消息提取一次
  - `min_content_length=50` 表示只提取 ≥50 字符的消息
  - 两个条件同时满足时才执行
- `min_reply_interval_seconds` 可配置（默认 0，关闭）：AI 刚回复后，runtime 会在最小间隔内继续排队；窗口结束后先合并同一说话人的连续消息，再进入正常的 reply_mode/intent 判断
- `intent_analysis` 启用后必须通过模型推断；若调用失败或解析失败，该轮不会再回退到关键词意图推断
- 多 AI 群聊里，`intent_analysis` 会优先区分“当前模型自身”与“其他 AI”；当用户明显是在调用其他 AI 时，当前模型会抑制自动回复
- 为减少多 AI 误判，`intent_analysis` 传给模型的上下文已收紧为极小摘要，并会显式标出当前消息命中的当前模型名字、其他 AI 名字和人类名字
- `max_concurrent_llm_calls` 可配置（默认 1）：LLM 并发数限流
- `pending_message_threshold` 可配置（默认 4）：当单会话待处理消息积压超过阈值时，runtime 会进入静默批处理并合并同一说话人的连续消息
- 提示词分割和 SKILL 调用标记现在为框架内置常量，外部配置不再暴露这些 marker

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

当前不需要单独执行迁移脚本：`WorkspaceRuntime` 与 `SqliteSessionStore` 会在首次打开 workspace / session 时自动完成兼容迁移。

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
| [🗂️ migration-v0.27.md](docs/migration-v0.27.md) | v0.27 破坏性变更迁移指南 |
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
- ⚡ **快速执行**：< 15 秒全套（通过关闭积压批处理并禁用无关辅助任务）
- 🔒 **完全隔离**：无真实网络调用，全量 Mock
- 📊 **92% 代码覆盖**：关键路径完整测试

---

## 🆕 最新变更

### ✨ **新增**
- **积压计数批处理**：`WorkspaceRuntime` 现在按待处理消息数而不是时间窗口决定是否进入静默批处理；当积压超过 `pending_message_threshold` 时，会合并同一说话人的连续消息并只发起一次模型调用。
- **外部迁移指南**：新增 `docs/migration-v0.27.md`，集中说明 v0.27 的配置键变更、意图分析语义变化与外部接入迁移步骤。

### 🚀 **改进**
- **intent_analysis 改为严格模型路径**：任务启用后，意图结论必须来自模型；预算不足、调用失败或解析失败时，不再回退关键词意图推断。
- **人格生成更克制**：新的角色生成提示词会默认产出更偏短句、纯文本、少 markdown 的角色行为约束，减少长段落和说明书式回复。

**迁移提示：**
> 若你此前依赖 `message_debounce_seconds` 或依赖 `intent_analysis` 在失败时自动回退关键词路径，请先阅读 `docs/migration-v0.27.md`。

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