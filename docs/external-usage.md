# 外部程序使用 Sirius Chat

本文档说明如何从外部项目中调用 Sirius Chat 库。

## 安装

在你的项目环境中安装：

```bash
python -m pip install -e /path/to/sirius_chat
```

若通过打包产物安装，请替换为你的发布方式（如私有 index）。

## 方式一：直接在 Python 程序中调用

```python
import asyncio
from pathlib import Path

from sirius_chat.api import (
    Message,
    OrchestrationPolicy,
    User,
    AsyncRolePlayEngine,
    create_session_config_from_selected_agent,
    OpenAICompatibleProvider,
)

provider = OpenAICompatibleProvider(
    base_url="https://api.openai.com",
    api_key="YOUR_API_KEY",
)

engine = AsyncRolePlayEngine(provider=provider)

config = create_session_config_from_selected_agent(
    work_path=Path("data/external_usage"),
    agent_key="main_agent",
    orchestration=OrchestrationPolicy(
        enabled=True,
        task_models={"memory_extract": "doubao-seed-2-0-lite-260215"},
        task_budgets={"memory_extract": 1200},
        task_temperatures={"memory_extract": 0.1},
        task_max_tokens={"memory_extract": 128},
    ),
)

async def main() -> None:
    transcript = await engine.run_live_session(
        config=config,
        human_turns=[Message(role="user", speaker="校务主任", content="我关心预算和安全约束")],
    )
    for message in transcript.messages:
        if message.speaker:
            print(f"[{message.speaker}] {message.content}")

asyncio.run(main())
```

若使用 SiliconFlow，可直接替换 provider：

```python
from sirius_chat.api import SiliconFlowProvider

provider = SiliconFlowProvider(
    api_key="YOUR_API_KEY_FROM_CLOUD_SILICONFLOW_CN",
)
```

说明：

- `SiliconFlowProvider` 默认基地址为 `https://api.siliconflow.cn`。
- 若外部配置传入 `https://api.siliconflow.cn/v1` 也可兼容，内部会自动规范化。
- 接口路径遵循 OpenAI 兼容的 `/v1/chat/completions`。

若使用火山方舟，可使用：

```python
from sirius_chat.api import VolcengineArkProvider

provider = VolcengineArkProvider(
    api_key="YOUR_ARK_API_KEY",
)
```

说明：

- `VolcengineArkProvider` 默认基地址为 `https://ark.cn-beijing.volces.com/api/v3`。
- 兼容传入带 `/api/v3` 后缀的 base_url。
- 接口路径为 `/api/v3/chat/completions`。

### 多 provider 自动路由

若希望按模型自动路由 provider，可使用 `ProviderRegistry + AutoRoutingProvider`：

```python
from pathlib import Path

from sirius_chat.api import AutoRoutingProvider, ProviderRegistry

work_path = Path("data/session_runtime")
registry = ProviderRegistry(work_path)
registry.upsert(
    provider_type="siliconflow",
    api_key="YOUR_SF_KEY",
    healthcheck_model="Pro/zai-org/GLM-4.7B-Instruct",
    model_prefixes=["Pro/", "Qwen/"],
)
registry.upsert(
    provider_type="openai-compatible",
    api_key="YOUR_OPENAI_KEY",
    healthcheck_model="gpt-4o-mini",
    model_prefixes=["gpt-"],
)

provider = AutoRoutingProvider(registry.load())
```

也可通过 CLI 交互命令管理（`sirius-chat` 与 `python main.py` 均可）：

- `/provider platforms`
- `/provider list`
- `/provider add <type> <api_key> <healthcheck_model> [base_url] [model_prefixes_csv]`
- `/provider remove <type>`

框架内 Provider 检测流程（注册/启动阶段）：

1. 检查是否存在已配置平台信息（平台名 + API Key）。
2. 检查平台是否属于已适配清单（不允许自定义 provider 类型）。
3. 使用注册时提供的 `healthcheck_model` 发起最小请求验证可用性。

### 异步程序嵌入（推荐）

```python
from sirius_chat.api import Message, create_async_engine

engine = create_async_engine(provider)
transcript = await engine.run_live_session(
    config=config,
    human_turns=[Message(role="user", speaker="小王", content="请给我发布建议")],
)
```

多模态输入示例（阶段二）：

```python
from sirius_chat.api import Message

turn = Message(
    role="user",
    speaker="小王",
    content="请结合这张图给我发布建议",
    multimodal_inputs=[
        {"type": "image", "value": "https://example.com/demo.png"},
    ],
)
```

若配置了 `orchestration.task_models.multimodal_parse`，引擎会先提取多模态证据，再交给主模型生成回复。

token 消耗分析示例：

```python
from sirius_chat.api import build_token_usage_baseline, summarize_token_usage

summary = summarize_token_usage(transcript)
baseline = build_token_usage_baseline(transcript.token_usage_records)

print(summary["by_task"])
print(baseline.to_dict())
```

说明：

- 每次模型调用都会归档到 `transcript.token_usage_records`。
- 记录包含调用者（actor）、任务名、模型、token 估算与重试次数，可用于成本和损耗评估。

角色扮演提示词生成与注入示例：

```python
from sirius_chat.api import (
    RolePlayAnswer,
    agenerate_agent_prompts_from_answers,
    abuild_roleplay_prompt_from_answers_and_apply,
    create_session_config_from_selected_agent,
    generate_humanized_roleplay_questions,
    load_generated_agent_library,
    select_generated_agent_profile,
)

questions = generate_humanized_roleplay_questions()
answers = [
    RolePlayAnswer(question=questions[0].question, answer="谨慎慢热，但责任感强", perspective=questions[0].perspective),
    RolePlayAnswer(question=questions[1].question, answer="说话短句、克制，偶尔重复确认", perspective=questions[1].perspective),
]

prompt = await abuild_roleplay_prompt_from_answers_and_apply(
    provider,
    config=config,
    model="deepseek-ai/DeepSeek-V3.2",
    agent_name=config.agent.name,
    answers=answers,
)
print(prompt)
```

说明：

- `generate_humanized_roleplay_questions()` 会生成覆盖拟人化关键维度的问题模板。
- `agenerate_agent_prompts_from_answers(...)` 会从回答中生成完整 `GeneratedSessionPreset`（`agent + global_system_prompt`）。
- 生成时会显式输入 `agent_name`，确保主 AI 命名与提示词一致。
- `abuild_roleplay_prompt_from_answers_and_apply(...)` 会把生成结果写入 `config.preset`。

agent-first 会话创建示例：

```python
# 读取已生成 agent 资产（key -> profile）
library, selected = load_generated_agent_library(config.work_path)
print(library.keys(), selected)

# 显式选择一个已生成 agent
profile = select_generated_agent_profile(config.work_path, agent_key="beichen_v1")
print(profile.name)

# 由已选择的 agent 直接创建 SessionConfig
session_config = create_session_config_from_selected_agent(
    work_path=config.work_path,
    agent_key="beichen_v1",
)
```

说明：

- 推荐流程为“先生成 agent 资产，再按 `agent_key` 选择后创建会话”，避免会话先创建后再反向覆盖主 AI 设定。
- 生成资产保存在 `<work_path>/generated_agents.json`，可跨次会话复用。

说明：

- 若 provider 原生支持异步（`generate_async`），引擎会直接 `await`。
- 若 provider 只有同步 `generate`，异步引擎会自动在线程中执行，避免阻塞事件循环。

## 方式二：通过子进程调用 CLI

外部系统若不直接嵌入 Python，可通过命令行调用并读取输出文件：

```bash
sirius-chat --config examples/session.json --work-path data/session_runtime --output transcript.json
```

带状态持久化与恢复运行：

```bash
sirius-chat --config examples/session.json --work-path data/session_runtime
```

默认会自动恢复历史会话；若要强制从新会话开始，可在 `main.py` 入口使用 `--no-resume`。

## 方式三：动态群聊（参与者预先未知）

当参与者是动态加入（例如群聊环境）时，使用 `run_live_session`：

```python
import asyncio
from pathlib import Path

from sirius_chat.api import AsyncRolePlayEngine, Message, User, create_session_config_from_selected_agent

engine = AsyncRolePlayEngine(provider=provider)

config = create_session_config_from_selected_agent(
    work_path=Path("data/dynamic_group_chat"),
    agent_key="main_agent",
)

human_turns = [
    Message(role="user", speaker="王PM", content="我是产品经理，偏好快速试点"),
    Message(role="user", speaker="小李", content="我是财务，关注成本"),
]

async def main() -> None:
    transcript = await engine.run_live_session(config=config, human_turns=human_turns)

asyncio.run(main())
```

说明：

- 引擎会自动登记未知参与者。
- 引擎会维护 `transcript.user_memory`：
- `profile`：初始化档案（`user_id/name/persona/traits/identities`）。
- `runtime`：运行时状态（近期发言、摘要、推断偏好标签、最近渠道身份）。
- 主 AI 每轮会收到“参与者记忆”上下文，从而实现识人与连续记忆。

## 识人架构（用户类驱动）

推荐由外部显式构造 `User`：

- `user_id`：稳定唯一标识（跨重启、跨昵称变化仍可识别）
- `name`：展示名
- `aliases`：可能出现的称呼（如群昵称）
- `traits/persona`：用户特征

主系统通过 `speaker -> user_id` 索引做解析，解析不到时再自动创建临时用户，因此已登记用户会优先被准确识别。

对于多环境（CLI/QQ/微信）推荐同时提供 `identities` 映射，例如：

```python
User(
    user_id="user_zhangsan",
    name="张三",
    aliases=["三哥"],
    identities={"qq": "10086", "wechat": "wx_zhangsan"},
)
```

在运行时，若 `Message` 带有 `channel` 和 `channel_user_id`，引擎会优先按该映射识别为同一用户，再回退到昵称/别名匹配。

若外部系统需要在运行时直接按环境身份查询用户，可调用：

```python
entry = transcript.find_user_by_channel_uid(channel="wechat", uid="wx_zhangsan")
if entry is not None:
    print(entry.profile.user_id)
```

## 记忆压缩与上下文预算

`SessionConfig` 提供以下压缩参数：

- `history_max_messages`：保留在上下文中的最大消息条数。
- `history_max_chars`：上下文近似字符预算。
- `max_recent_participant_messages`：每位参与者保留的近期发言数。
- `enable_auto_compression`：是否启用自动压缩。

当历史超过预算时，引擎会把旧对话压缩到 `session_summary`，并在后续请求中自动注入该摘要，避免 token 无限制增长。

## 优化与监控

### 配置管理

使用 `ConfigManager` 处理多环境配置：

```python
from sirius_chat.config_manager import ConfigManager
from pathlib import Path

# 加载基础配置并应用环境变量替换
config_mgr = ConfigManager.load_from_json(Path("config/base.json"))
# 支持 ${VAR_NAME} 占位符，会自动替换为环境变量值

# 也可加载环境特定的配置
dev_config = ConfigManager.load_from_json(Path("config/dev.json"))
```

### 缓存优化

使用 `cache/` 模块缓存 LLM 响应，降低成本和延迟：

```python
from sirius_chat.cache import MemoryCache, generate_cache_key

# 创建内存缓存（LRU + TTL）
cache = MemoryCache(max_size=1000, ttl=3600)

# 为 LLM 请求生成确定性 key
key = generate_cache_key(
    model="gpt-4",
    prompt="用户问题文本",
    temperature=0.7,
)

# 查询和存储
if await cache.get(key):
    response = await cache.get(key)
else:
    response = await provider.agenerate([...])
    await cache.set(key, response)
```

对于分布式场景，可使用 `RedisCache`（需要 Redis 依赖）：

```python
from sirius_chat.cache import RedisCache

redis_cache = RedisCache(
    redis_url="redis://localhost:6379/0",
    ttl=3600,
)
```

### 性能监控

监控会话执行性能：

```python
from sirius_chat.performance import PerformanceProfiler, Benchmark

# 上下文管理器方式
with PerformanceProfiler("session_execution"):
    # 执行会话逻辑
    transcript = await engine.run_live_session(config=config, human_turns=human_turns)

# 装饰器方式
from sirius_chat.performance import profile_async

@profile_async
async def my_handler():
    # 被装饰的函数会自动记录执行时间和内存消耗
    pass

# 基准测试
result = Benchmark.run_sync(
    my_sync_function,
    iterations=100,
)
print(f"平均执行时间: {result.mean}ms")
```

## 集成建议

- 对外 Python 调用统一从 `sirius_chat/api/` 导入接口。
- provider 可选 `OpenAICompatibleProvider` 或 `SiliconFlowProvider`（按上游厂商选择）。
- **多模型协同现已默认启用**。通过 `SessionConfig.orchestration` 配置 `task_models`、`task_budgets` 等实现记忆提取、事件提取、多模态解析的分任务路由。若需全部由一个模型处理，设置 `orchestration.enabled=False`。
- 若需更稳健的“提事不提人”识别，可为 `event_extract` 配置辅助模型，提取事件结构化字段后参与命中评分。
- 需要自动选择时，使用 `AutoRoutingProvider`，并在 `work_path/provider_keys.json` 维护可用 key。
- 当前未发布阶段，内部实现变更若影响外部行为，可直接升级 `api/` 并同步文档。
- 新增功能发布时，需同步在 `api/` 暴露入口供外部系统调用。
- 把 API Key 放在环境变量或密钥系统，不建议硬编码到配置文件。
- 一个 `AsyncRolePlayEngine` 会话只对应一个主 AI（由 `SessionConfig.preset` 描述）。
- `work_path` 必须由调用方显式提供，所有持久化文件都写入该目录。
- 动态群聊推荐使用 `run_live_session`，并通过 `transcript.user_memory` 进行识人记忆。
- 对长会话场景增加上下文裁剪或摘要策略。
- 对生产调用增加 provider 重试与超时治理。

## 相关文档

- 架构说明：`docs/architecture.md`
- 框架速读技能：`.github/skills/framework-quickstart/SKILL.md`
- 外部接入技能：`.github/skills/external-integration/SKILL.md`
- 技能同步规则：`.github/skills/skill-sync-enforcer/SKILL.md`



