# Sirius Chat

Sirius Chat 是一个用于“多人用户与单 AI 主助手”交互场景的 Python 核心库。

项目定位：构建具有真实情感表达能力、能为用户提供帮助与情绪价值的核心引擎。

## 支持方式

- 库模式：在你的程序中直接调用框架 API。
- CLI 模式：通过 JSON 配置文件运行会话。
- 外部 LLM Provider：当前内置 OpenAI 兼容接口。
- 外部 LLM Provider：内置 OpenAI 兼容接口，并提供 SiliconFlow 专用快捷适配。
- Provider 管理：支持多平台 API Key 持久化，按模型自动路由到已配置 provider。
- 动态群聊模式：参与者可运行时出现，主 AI 自动维护识人记忆。

## 功能特性

### 核心架构
- **异步编排引擎** (P0-003)：支持多人交互、实时记忆维护、多模态处理的异步编排。
- **Provider 中间件** (P1-003)：支持速率限制、自动重试、成本计量等透明中间件。
- **多环境配置管理** (P1-006)：JSON 配置文件、环境变量替换、配置验证。

### 性能与缓存
- **智能缓存框架** (P2-001)：内存缓存（LRU + TTL），支持缓存 LLM 响应。
- **性能监控** (P2-002)：执行指标收集、性能分析装饰器、基准测试工具。

### 记忆与用户识别
- **结构化用户记忆**：按分类（身份/偏好/情绪/事件）组织，支持置信度标记和冲突检测。
- **跨环境身份识别**：通过 `identities` 映射不同平台的外部账号到同一用户。
- **事件记忆管理**：自动提取关键事件，支持历史事件命中评分。

## 目标

- 构建可复用的多人角色扮演编排引擎。
- 保持“一次会话对应一个主 AI”与 provider 抽象，便于切换不同厂商。
- 输出结构化 transcript，便于下游系统消费。
- 在运行时主动维护用户信息与上下文线索，让对话更具连续性和拟人化体验。

## 快速开始

```bash
python -m pip install -e .
```

安装测试依赖：

```bash
python -m pip install -e .[test]
```

通过 CLI 脚本运行：

```bash
sirius-chat --config examples/session.json --work-path data/session_runtime --message "你好" --output transcript.json
```

若希望由库自动维护用户档案与会话持久化，可使用 `JsonPersistentSessionRunner`。

或通过 `main.py` 运行：

```bash
python main.py --config examples/session.json --work-path data/session_runtime --store json --output transcript.json
```

说明：`sirius_chat/cli.py` 是库内薄封装，只负责调用 `sirius_chat/api/`；
`main.py` 是仓库根目录的测试/业务入口，承载主用户档案、provider 管理、持续会话等流程。

`sirius-chat`（库内 CLI）为单轮薄调用，若不传 `--message` 则只交互输入一条消息。

`main.py`（测试入口）会优先使用根目录 `.last_config_path`，并维护持续会话所需的持久化状态。

在某个 `work_path` 首次启用时，CLI 会先通过交互方式创建主用户档案，并保存到 `<work_path>/primary_user.json`，后续启动自动复用。

在对话中可输入 `/reset-user` 重置主用户；主用户档案会以实时 JSON（原子写入）方式持续更新到 `<work_path>/primary_user.json`，便于后续调用直接复用。

在 CLI 交互模式中可用 provider 管理命令：

- `/provider platforms` 查看当前版本支持的平台
- `/provider list` 查看当前已配置 provider
- `/provider add <type> <api_key> <healthcheck_model> [base_url]` 添加或更新 provider（注册时即做可用性检测）
- `/provider remove <type>` 删除 provider

provider 配置会持久化到 `<work_path>/provider_keys.json`。
其中 `healthcheck_model` 为必填，框架会在注册与启动检测流程中使用该模型做可用性检查。

会话状态存储可选：

- `--store json`（默认）：轻量、可读、适合单机单会话。
- `--store sqlite`：适合需要更强一致性和后续复杂查询的场景。

每次通过 CLI 启动时，当前配置文件路径会写入仓库根目录 `.last_config_path`。

保存会话状态并在重启后恢复（默认自动恢复）：

```bash
sirius-chat --config examples/session.json --work-path data/session_runtime
```

如需禁用自动恢复，可在 `main.py` 入口添加 `--no-resume`。

## 配置示例

可使用如下 JSON：

```json
{
  "provider": {
    "type": "openai-compatible",
    "base_url": "https://api.openai.com",
    "api_key": "YOUR_API_KEY"
  },
  "generated_agent_key": "main_agent",
  "history_max_messages": 24,
  "history_max_chars": 6000,
  "max_recent_participant_messages": 5,
  "enable_auto_compression": true
}
```

SiliconFlow 配置示例：

```json
{
  "provider": {
    "type": "siliconflow",
    "api_key": "YOUR_API_KEY_FROM_CLOUD_SILICONFLOW_CN"
  },
  "generated_agent_key": "main_agent"
}
```

火山方舟（Volcengine Ark）配置示例：

```json
{
  "provider": {
    "type": "volcengine-ark",
    "api_key": "YOUR_ARK_API_KEY"
  },
  "generated_agent_key": "main_agent"
}
```

说明：

- `provider.type` 设为 `volcengine-ark`（或 `ark`）时，默认使用 `https://ark.cn-beijing.volces.com/api/v3`。
- 接口路径遵循方舟文档中的 `POST /api/v3/chat/completions`。

说明：

- `provider.type` 设为 `siliconflow` 时，默认使用 `https://api.siliconflow.cn`。
- 若你填入 `https://api.siliconflow.cn/v1` 也可正常工作，框架会自动规范化路径。
- 该适配走 OpenAI 兼容路径（`/v1/chat/completions`）。

多 provider 自动路由示例：

```json
{
  "providers": [
    {
      "type": "siliconflow",
      "api_key": "YOUR_SILICONFLOW_KEY",
      "healthcheck_model": "Pro/zai-org/GLM-4.7"
    },
    {
      "type": "openai-compatible",
      "base_url": "https://api.openai.com",
      "api_key": "YOUR_OPENAI_KEY",
      "healthcheck_model": "gpt-4o-mini"
    }
  ],
  "agent": {
    "name": "主助手",
    "persona": "自动路由示例",
    "model": "Pro/zai-org/GLM-4.7"
  }
}
```

路由规则：

- 先按 `healthcheck_model` 与请求模型名做精确匹配。
- 若无匹配，回退到第一个可用 provider。
- 若没有任何可用 provider，会抛出明确错误提示。

多模型任务编排（默认启用）：

```json
{
  "orchestration": {
    "task_enabled": {
      "memory_extract": true,
      "multimodal_parse": true,
      "event_extract": true
    },
    "task_models": {
      "memory_extract": "doubao-seed-2-0-lite-260215",
      "event_extract": "doubao-seed-2-0-lite-260215",
      "multimodal_parse": "doubao-seed-2-0-lite-260215"
    },
    "task_budgets": {
      "memory_extract": 1200,
      "event_extract": 1000,
      "multimodal_parse": 1000
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
      "event_extract": 1,
      "multimodal_parse": 1
    },
    "max_multimodal_inputs_per_turn": 4,
    "max_multimodal_value_length": 4096
    }
  }
}
```

说明：

- **多模型协同现已成为默认运作方式**。所有任务默认启用，可通过 `task_enabled` 字典按需禁用特定任务。
- `chat_main` 默认使用 `agent.model`。
- `memory_extract` 可配置单独模型协助用户记忆提取。
- `event_extract` 可配置单独模型提取事件结构化要素，增强跨会话事件命中。
- `multimodal_parse` 可配置单独模型将图片/视频输入转成文本证据。
- 若需全部由一个模型处理，可通过移除 `task_models` 并设置 `unified_model` 即可切换到统一模型模式。
- 可配置任务级重试（`task_retries`）提升临时错误恢复能力。
- 可配置多模态输入限流（`max_multimodal_inputs_per_turn`、`max_multimodal_value_length`）降低提示词膨胀风险。
- 预算超限或辅助任务失败会自动回退启发式记忆逻辑，不影响主回复。
- 详细策略见 `docs/orchestration-policy.md`。

Token 消耗全量归档与基准分析：

- 引擎会将每次模型调用写入 `Transcript.token_usage_records`，记录 `actor_id`、`task_name`、`model`、`prompt/completion/total tokens`、字符量与重试次数。
- 可通过 `sirius_chat.api` 的 `summarize_token_usage(transcript)` 获取按人/任务/模型聚合结果。
- 可通过 `build_token_usage_baseline(transcript.token_usage_records)` 获取会话级基准指标（平均 token、重试率、completion/prompt 比值）。

角色扮演前置内容生成与提示词生成器：

- 可通过 `generate_humanized_roleplay_questions()` 自动生成覆盖性格、日常行为、情绪触发、人际边界等维度的问题清单。
- 可通过 `agenerate_agent_prompts_from_answers(...)` 基于问答一次生成完整 `GeneratedSessionPreset`（`agent + global_system_prompt`）。
- 生成阶段会显式输入 `agent_name`，确保提示词与主 AI 名称对齐。
- 可通过 `abuild_roleplay_prompt_from_answers_and_apply(...)` 一步完成生成与回填（直接更新 `SessionConfig.preset`）。
- 可通过 `load_generated_agent_library(work_path)` / `select_generated_agent_profile(work_path, agent_key)` 管理与选择已生成 agent 资产。
- 可通过 `create_session_config_from_selected_agent(...)` 按“先生成 agent，再选择 agent 创建 session”的流程直接构建 `SessionConfig`。

动态多模态输入可通过 `Message.multimodal_inputs` 传入：

```python
Message(
  role="user",
  speaker="小王",
  content="请结合图片分析",
  multimodal_inputs=[{"type": "image", "value": "https://example.com/demo.png"}],
)
```

## 库调用示例

推荐外部程序统一从 `sirius_chat/api/` 导入接口：

```python
import asyncio
from sirius_chat.api import AsyncRolePlayEngine, Message, OpenAICompatibleProvider, create_session_config_from_selected_agent
from pathlib import Path

provider = OpenAICompatibleProvider(
    base_url="https://api.openai.com",
    api_key="YOUR_API_KEY",
)

engine = AsyncRolePlayEngine(provider=provider)

config = create_session_config_from_selected_agent(
  work_path=Path("data/library_usage"),
  agent_key="main_agent",
)

async def main() -> None:
  transcript = await engine.run_live_session(
    config=config,
    human_turns=[Message(role="user", speaker="Professor Lin", content="我们先从需求分层开始讨论")],
  )
  for msg in transcript.messages:
    if msg.speaker:
      print(f"[{msg.speaker}] {msg.content}")

  from sirius_chat.api import summarize_token_usage

  usage = summarize_token_usage(transcript)
  print("token baseline:", usage["baseline"])

asyncio.run(main())
```

自动问题 + 回答提取 + 一键注入示例：

```python
from sirius_chat.api import (
  RolePlayAnswer,
  abuild_roleplay_prompt_from_answers_and_apply,
  generate_humanized_roleplay_questions,
)

questions = generate_humanized_roleplay_questions()
answers = [RolePlayAnswer(question=item.question, answer="请填入对应回答", perspective=item.perspective) for item in questions]

prompt = await abuild_roleplay_prompt_from_answers_and_apply(
  provider,
  config=config,
  model="deepseek-ai/DeepSeek-V3.2",
  answers=answers,
)
print(prompt)
```

如果你使用 SiliconFlow，可改为：

```python
from sirius_chat.api import SiliconFlowProvider

provider = SiliconFlowProvider(api_key="YOUR_API_KEY_FROM_CLOUD_SILICONFLOW_CN")
```

自动持久化封装示例：

```python
import asyncio
from pathlib import Path

from sirius_chat.api import JsonPersistentSessionRunner, Participant, create_session_config_from_selected_agent
from sirius_chat.providers.mock import MockProvider

async def main() -> None:
  config = create_session_config_from_selected_agent(
    work_path=Path("data/runner_demo"),
    agent_key="main_agent",
  )
  runner = JsonPersistentSessionRunner(config=config, provider=MockProvider(responses=["你好"] ))
  await runner.initialize(primary_user=Participant(name="小王", user_id="u_wang"))
  reply = await runner.send_user_message("你好")
  print(reply.content)

asyncio.run(main())
```

异步嵌入（不阻塞事件循环）示例：

```python
from sirius_chat.api import Message, create_async_engine

engine = create_async_engine(provider)
transcript = await engine.run_live_session(
  config=config,
  human_turns=[Message(role="user", speaker="用户", content="hello")],
)
```

## 外部系统接入方式

- Python 外部项目：直接调用库 API，参考 `examples/external_api_usage.py`。
- 动态群聊（参与者预先未知）：参考 `examples/dynamic_group_chat_usage.py`。
- 非 Python 外部项目：通过 CLI 调用并读取输出文件。

接口治理约定：

- 内部代码可按需重构。
- 当前未发布阶段，若影响到外部接口，可直接升级 `sirius_chat/api/` 并同步文档与示例。
- 内部新增可用能力，必须在 `sirius_chat/api/` 增加对外接口。

```bash
sirius-chat --config examples/session.json --output transcript.json
```

完整接入说明见 `docs/external-usage.md`。

## 项目结构

- `sirius_chat/models.py`：会话与 transcript 数据契约。
- `sirius_chat/providers/base.py`：provider 协议定义。
- `sirius_chat/providers/mock.py`：测试/本地演练用的确定性 provider。
- `sirius_chat/providers/openai_compatible.py`：OpenAI 兼容 provider 实现。
- `sirius_chat/session_store.py`：基于 work-path 的会话状态持久化与恢复。
- `sirius_chat/user_memory.py`：用户识别与用户记忆模块（User/UserMemoryManager，区分初始化档案与运行时状态）。
- `sirius_chat/cli.py`：库内薄封装 CLI，仅调用 `public_api` 执行单轮会话。
- `sirius_chat/api/`：统一对外接口入口（异步 facade 与便捷接口）。
- `sirius_chat/async_engine.py`：异步引擎核心实现（适配异步宿主程序）。
- `main.py`：仓库级测试/业务入口（承载主用户、provider 管理与持续会话流程）。

## 测试

运行全部测试：

```bash
pytest -q
```

运行单个测试文件：

```bash
pytest tests/test_engine.py -q
```

## 架构与技能文档

- 架构边界与扩展点：`docs/architecture.md`
- 完整架构流程图与模块产出：`docs/full-architecture-flow.md`
- 框架速读技能：`.github/skills/framework-quickstart/SKILL.md`
- 外部接入技能：`.github/skills/external-integration/SKILL.md`
- 代码变更同步约束技能：`.github/skills/skill-sync-enforcer/SKILL.md`

## 开发指南

### 环境初始化

推荐使用自动化脚本初始化开发环境：

```bash
python scripts/setup_dev_env.py
```

该脚本会：
1. 安装所有开发依赖
2. 安装 pre-commit 钩子
3. 运行初始测试验证环境

或手动安装：

```bash
# 安装开发依赖
pip install -e .[dev,test]

# 安装 pre-commit 钩子
pre-commit install
```

### 代码质量检查

使用 Makefile 简化常见任务：

```bash
make help              # 查看所有命令
make format            # 格式化代码（black + isort）
make lint              # 运行 linters（pylint + flake8）
make typecheck         # 类型检查（mypy）
make test              # 运行所有测试
make test-cov          # 生成覆盖率报告
make pre-commit-run    # 运行所有 pre-commit 钩子
```

或直接使用 CI 检查脚本：

```bash
python scripts/ci_check.py
```

### 代码标准

- **语言**：Python 3.12+
- **代码风格**：[Black](https://black.readthedocs.io/) (line-length=100)
- **Import 排序**：[isort](https://pycqa.github.io/isort/)
- **类型检查**：[mypy](http://mypy-lang.org/) (strict)
- **Linting**：[pylint](https://pylint.pycqa.org/) + [flake8](https://flake8.pycqa.org/)
- **安全检查**：[bandit](https://bandit.readthedocs.io/)

### Git 工作流

遵循 [Conventional Commits](https://www.conventionalcommits.org/) 规范：

```
feat(module):     新功能
fix(module):      问题修复
docs(file):       文档更新
refactor(module): 代码重构（不改变功能）
perf(module):     性能优化
test(file):       测试添加/修改
```

示例：
```bash
git commit -m "feat(middleware): add rate limiter middleware"
git commit -m "fix(engine): handle async timeout correctly"
git commit -m "docs(README): update development guide"
```

Pre-commit 钩子会自动：
- 格式化代码
- 检查导入顺序
- 运行基本的 linters
- 验证 YAML/JSON 文件

### CI/CD 流程

在 GitHub 上自动运行（`.github/workflows/ci.yml`）：

**On Push / Pull Request:**
1. ✓ 多版本测试 (Python 3.10, 3.11, 3.12)
2. ✓ 单元测试 + 覆盖率报告
3. ✓ 代码质量检查 (pylint, black, isort, mypy, flake8)
4. ✓ 安全扫描 (bandit)
5. ✓ 构建验证 (wheel + sdist)

## 贡献约定

1. Fork 项目
2. 创建特性分支：`git checkout -b feat/your-feature`
3. 编写代码并添加测试
4. 运行本地质量检查：`make lint test typecheck`
5. 提交 PR，自动 CI 会验证

关键约束：
- **零回归**：所有新代码不能破坏现有测试
- **测试覆盖**：新增代码需要对应的单元测试和集成测试
- **类型注解**：所有公开接口必须包含类型注解
- **文档同步**：如果修改接口或架构，需要同步以下文档：
  - `docs/architecture.md`
  - `CHANGELOG.md`
  - `.github/skills/` 中的相关 SKILL 文件

## 项目进度

详见 [PROJECT_ISSUES.md](PROJECT_ISSUES.md)：

| 优先级 | 总数 | 完成 | 进度 |
|------|-----|-----|-----|
| P0 (系统) | 5 | 4 | 80% ✅ |
| P1 (重要) | 4 | 4 | 100% ✅ |
| P2 (优化) | 4 | 0 | 0% ⏳ |

当前正在进行：P0-003 (async_engine 重构)


