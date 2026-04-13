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
        unified_model="",  # 使用按任务配置模式
        task_enabled={"memory_extract": True, "intent_analysis": True},
        task_models={
            "memory_extract": "doubao-seed-2-0-lite-260215",
            "intent_analysis": "gpt-4o-mini",
        },
        task_budgets={"memory_extract": 1200, "intent_analysis": 600},
        task_temperatures={"memory_extract": 0.1, "intent_analysis": 0.1},
        task_max_tokens={"memory_extract": 128, "intent_analysis": 192},
        # 频率控制：每3条消息执行一次，且内容≥50字符
        memory_extract_batch_size=3,
        memory_extract_min_content_length=50,
        session_reply_mode="auto",
    ),
)

async def main() -> None:
    transcript = await engine.run_live_session(config=config)
    transcript = await engine.run_live_message(
        config=config,
        transcript=transcript,
        turn=Message(role="user", speaker="校务主任", content="我关心预算和安全约束"),
        environment_context="当前群名: 教务讨论群\n群成员数: 15",  # 可选：注入外部环境信息
    )
    for message in transcript.messages:
        if message.speaker:
            print(f"[{message.speaker}] {message.content}")

asyncio.run(main())
```

> 破坏性变更提示 (v0.9.0)：`on_message` 回调已移除，改用 `engine.subscribe()` 事件流。
> 迁移请参考 `docs/migration-event-stream.md`。

#### 实时事件订阅（推荐用于外部投递）

```python
import asyncio
from sirius_chat.api import (
    AsyncRolePlayEngine,
    Message,
    SessionEventType,
    OpenAICompatibleProvider,
    create_session_config_from_selected_agent,
)
from pathlib import Path

provider = OpenAICompatibleProvider(base_url="https://api.openai.com", api_key="KEY")
engine = AsyncRolePlayEngine(provider=provider)
config = create_session_config_from_selected_agent(work_path=Path("data/demo"), agent_key="main_agent")

async def main() -> None:
    transcript = await engine.run_live_session(config=config)

    # 启动事件监听（后台任务）
    async def listener():
        async for event in engine.subscribe(transcript):
            if event.type == SessionEventType.MESSAGE_ADDED:
                msg = event.message
                if msg and msg.role == "assistant":
                    print(f"[实时] [{msg.speaker}] {msg.content}")
            elif event.type == SessionEventType.SKILL_STARTED:
                print(f"[实时] SKILL 执行中: {event.data['skill_name']}")

    task = asyncio.create_task(listener())

    # 正常发送消息
    transcript = await engine.run_live_message(
        config=config,
        transcript=transcript,
        turn=Message(role="user", speaker="校务主任", content="查一下明天的天气"),
    )

    task.cancel()

asyncio.run(main())
```

#### 简化回调模式（v0.12.0 推荐）

对于最常见的"实时投递 assistant 回复"场景，可使用 `on_reply` 参数替代手动事件订阅：

```python
from sirius_chat.api import Message, UserProfile, arun_live_message

async def on_reply(msg: Message) -> None:
    """引擎为每条 assistant 回复调用此回调。"""
    print(f"[实时] {msg.content}")

transcript = await arun_live_message(
    engine, config,
    turn=Message(role="user", speaker="用户A", content="你好"),
    transcript=transcript,
    on_reply=on_reply,                    # 引擎内部管理事件订阅
    user_profile=UserProfile(             # 可选：自动注册用户
        user_id="u001", name="用户A",
        identities={"qq": "12345"},
    ),
    timeout=45.0,                         # 可选：超时自动清理
)
```

> `asubscribe` 原始事件流仍然可用，适合需要监听 `SKILL_STARTED` 等更多事件类型的场景。

#### Agent 配置：多模态模型（动态模型路由）

当需要在有图像时自动升级模型（例如使用廉价模型处理纯文本，但遇到图像时自动升级到多模态模型）时，可以为 Agent 配置 `multimodal_model`：

**方法一：使用便捷构造函数（推荐）**

```python
from sirius_chat.api import create_agent_with_multimodal

agent = create_agent_with_multimodal(
    name="Assistant",
    persona="A helpful AI assistant",
    model="gpt-4o-mini",           # 文本模式使用廉价模型
    multimodal_model="gpt-4o",     # 检测到图像时自动升级至多模态模型
    temperature=0.7,
    max_tokens=512,
)
```

**方法二：使用灵活配置函数**

```python
from sirius_chat.api import Agent, auto_configure_multimodal_agent

agent = Agent(
    name="Assistant",
    persona="A helpful AI assistant",
    model="gpt-4o-mini",
)
agent = auto_configure_multimodal_agent(agent, multimodal_model="gpt-4o")
```

**方法三：手动配置**

```python
from sirius_chat.api import Agent

agent = Agent(
    name="Assistant",
    persona="A helpful AI assistant",
    model="gpt-4o-mini",
)
agent.metadata["multimodal_model"] = "gpt-4o"
```

**工作原理**：
- 引擎在生成回复前检查用户输入中是否包含多媒体数据（图像、视频等）
- 如果没有多媒体数据，使用 `Agent.model`（廉价模型）
- 如果检测到多媒体数据，自动升级至 `agent.metadata["multimodal_model"]`（支持多模态的模型）
- 此过程对调用方完全透明，无需手动干预

#### OrchestrationPolicy 高级配置参考

当需要对不同任务使用不同模型、成本预算、温度等参数时，以下是完整的配置示例：

```python
orchestration = OrchestrationPolicy(
    # === 统一配置（可选） ===
    unified_model="gpt-4o",  # 若设置，则所有任务均使用该模型（覆盖task_models）
    
    # === 按任务细粒度配置 ===
    task_models={
        "memory_extract": "gpt-4o-mini",     # 用户记忆提取
        "event_extract": "gpt-4o-mini",      # 事件提取
        "intent_analysis": "gpt-4o-mini",    # 意图分析（reply_mode=auto/smart）
        "memory_manager": "gpt-4o-mini",     # 记忆管理（可选）
    },
    
    # === 预算与成本控制 ===
    task_budgets={
        "memory_extract": 1200,    # token预算
        "event_extract": 800,
        "intent_analysis": 600,
    },
    task_max_tokens={
        "memory_extract": 128,     # 最大输出token
        "event_extract": 256,
        "intent_analysis": 192,
    },
    task_temperatures={
        "memory_extract": 0.1,     # 温度（越低越稳定）
        "event_extract": 0.3,
        "intent_analysis": 0.1,
    },
    task_retries={
        "memory_extract": 2,       # 失败重试次数
        "event_extract": 1,
        "intent_analysis": 1,
    },
    
    # === 执行频率控制 ===
    memory_extract_batch_size=3,           # 每3条消息执行一次
    memory_extract_min_content_length=50,  # 消息内容需≥50字符才触发
    
    # === SKILL 系统 ===
    enable_skills=True,                    # SKILL 默认已启用；仅在需要关闭时显式设为 False
    skill_execution_timeout=30.0,          # SKILL 最大执行时长（秒），0=不限制
    max_skill_rounds=3,                    # AI 单轮最多调用 SKILL 次数
    
    # === 提示词驱动分割（可选） ===
    enable_prompt_driven_splitting=True,   # 启用AI主动分割消息
    split_marker="<MSG_SPLIT>",            # 分割标记
)
```

**配置说明**：
- 若 `unified_model` 设置，则覆盖所有 `task_models` 配置
- `reply_mode=auto` / `smart` 下，意图分析会优先使用 `task_models["intent_analysis"]`；未设置时回退 `unified_model` 或主模型
- 频率控制：当消息数达到批次大小且内容长度满足时，执行任务
- SKILL 目录：框架会始终先创建 `{work_path}/skills/` 与 `README.md`；关闭 SKILL 仅影响调用，不影响目录引导文件生成
- 提示词分割：当 `enable_prompt_driven_splitting=True` 时，系统提示会带分割指令，AI 会在适当位置输出 `<MSG_SPLIT>` 标记
- 兼容字段 `enable_intent_analysis` / `intent_analysis_model` 仍然可用，但建议迁移到任务配置，详见 `docs/migration-v0.16.md`

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

若使用 DeepSeek，可使用：

```python
from sirius_chat.api import DeepSeekProvider

provider = DeepSeekProvider(
    api_key="YOUR_DEEPSEEK_API_KEY",
)
```

说明：

- `DeepSeekProvider` 默认基地址为 `https://api.deepseek.com`。
- 若外部配置传入 `https://api.deepseek.com/v1` 也可兼容，内部会自动规范化。
- 接口路径为 `POST /chat/completions`，请求体格式与 OpenAI 兼容。

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

#### Provider 统一配置体系

#### Provider 统一配置体系（v1.0）

Sirius Chat v1.0 统一采用单一 `providers` 列表格式：

**Session JSON 中的 `providers` 字段**（必需，数组格式）

```json
{
  "generated_agent_key": "sirius",
  "providers": [
    {
      "type": "openai-compatible",
      "base_url": "https://api.openai.com",
      "api_key": "YOUR_API_KEY",
      "healthcheck_model": "gpt-4o-mini"
    }
  ]
}
```

**多 Provider 自动路由**：在 `providers` 列表中指定多个 provider，框架通过 `healthcheck_model` 自动路由：

```json
{
  "providers": [
    {
      "type": "openai-compatible",
      "api_key": "YOUR_OPENAI_KEY",
      "healthcheck_model": "gpt-4o-mini"
    },
    {
      "type": "siliconflow",
      "api_key": "YOUR_SF_KEY",
      "healthcheck_model": "doubao-seed-2-0-lite-260215"
    }
  ],
  "orchestration": {
    "task_models": {
      "memory_extract": "doubao-seed-2-0-lite-260215"  # 自动路由至 SiliconFlow
    }
  }
}
```

**持久化密钥管理**：
- `merge_provider_sources(work_path, providers_config)` 自动从 `<work_path>/provider_keys.json` 加载持久化的 API 密钥
- 配置项中 `api_key` 字段支持值为环境变量名，自动从 `provider_keys.json` 中解析实际密钥
- 不存在向后兼容转换逻辑；v1.0 强制使用 `providers` 列表格式

### 异步程序嵌入（推荐）

```python
from sirius_chat.api import Message, create_async_engine

engine = create_async_engine(provider)
transcript = await engine.run_live_session(config=config)
transcript = await engine.run_live_message(
    config=config,
    transcript=transcript,
    turn=Message(role="user", speaker="小王", content="请给我发布建议"),
    environment_context="群聊:技术部 | 在线:12人",  # 可选
)
```

`Message` 支持 `reply_mode` 控制该条用户消息是否触发主 AI 回复：

- `"always"`：始终回复（默认值）。
- `"never"`：仅写入记忆与 transcript，不触发回复。
- `"auto"`：由引擎根据内容推断是否需要回复（如疑问句、@助手、请求语气时更倾向回复）。

推荐实时接入方式（每次仅传入一条上游消息）：

```python
transcript = await engine.run_live_session(config=config)  # 一次性初始化

for incoming in stream_of_messages:
    transcript = await engine.run_live_message(
        config=config,
        transcript=transcript,
        turn=Message(role="user", speaker=incoming.speaker, content=incoming.content),
        environment_context=incoming.env_context,  # 可选：传递外部环境信息
    )
```

`run_live_message` 默认使用会话级 `session_reply_mode`（配置于 `OrchestrationPolicy`），外部无需逐条传 `reply_mode`。

`reply_mode="auto"` 的参与决策系统可通过 `OrchestrationPolicy` 调参：

- `engagement_sensitivity`：参与敏感度（0.0=极度克制，1.0=积极参与，默认 0.5）。越高 AI 越主动回复。
- `heat_window_seconds`：热度分析滑动窗口（默认 60 秒）。
- `session_reply_mode`：会话级回复策略（`always`/`never`/`auto`），用于 `run_live_message`。

跨多次 `run_live_message` 调用时，若复用同一个 `transcript`，引擎会复用 `transcript.reply_runtime`
中的临时节奏状态（用户最近发言时间、群聊窗口时间序列、最近 AI 回复时间），从而保持
`reply_mode="auto"` 的连续拟人节奏。

示例：

```python
transcript = await engine.run_live_session(config=config)
for turn in [
    Message(role="user", speaker="小王", content="今天开完周会，记录一下进展", reply_mode="never"),
    Message(role="user", speaker="小王", content="请你给我一个明天的优先级建议", reply_mode="auto"),
]:
    transcript = await engine.run_live_message(
        config=config,
        transcript=transcript,
        turn=turn,
        session_reply_mode=turn.reply_mode,
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

若消息包含图片，引擎会直接把图片以 vision 格式发送给主模型；如需升级模型，请配置 `Agent.metadata["multimodal_model"]`。

token 消耗分析示例：

```python
from sirius_chat.api import build_token_usage_baseline, summarize_token_usage

# 单会话内存分析（基于 Transcript）
summary = summarize_token_usage(transcript)
baseline = build_token_usage_baseline(transcript.token_usage_records)

print(summary["by_task"])
print(baseline.to_dict())
```

**跨会话 SQLite 持久化分析**（v0.11.0 新增）：

```python
from sirius_chat.api import (
    TokenUsageStore,
    compute_baseline,
    full_report,
    group_by_actor,
    group_by_task,
    group_by_model,
    group_by_session,
    time_series,
)

# 打开持久化存储（引擎运行时自动写入 {work_path}/token_usage.db）
store = TokenUsageStore("./data/token_usage.db", session_id="my_session")

# 全局基线
bl = compute_baseline(store)
print(bl["total_calls"], bl["total_tokens"])

# 按维度聚合
print(group_by_actor(store))          # 按用户
print(group_by_task(store))           # 按任务
print(group_by_model(store))          # 按模型
print(group_by_session(store))        # 按会话
print(time_series(store, bucket_seconds=3600))  # 按小时时间桶

# 完整报告（包含 baseline + 所有维度）
report = full_report(store)

# 过滤查询
bl_user = compute_baseline(store, actor_id="alice")
tasks_in_session = group_by_task(store, session_id="sess_1")
```

说明：

- 每次模型调用都会归档到 `transcript.token_usage_records`（内存）和 `{work_path}/token_usage.db`（SQLite）。
- 记录包含调用者（actor）、任务名、模型、token 估算与重试次数，可用于成本和损耗评估。
- SQLite 存储在引擎初始化会话时自动创建，无需额外配置。

#### Transcript 对象 API 参考

`run_session()`、`run_live_session()`（初始化）与 `run_live_message()` 返回的 `Transcript` 对象包含完整的会话记录。

**主要属性**：

```python
transcript = await engine.run_live_session(...)
transcript = await engine.run_live_message(...)

# 会话消息列表
messages: list[Message]  # 所有轮次的消息（user + agent）

# Token使用统计
token_usage_records: list[TokenUsageRecord]  # 每次模型调用的详细记录

# 参与者信息
participants: dict[str, Participant]  # 本次会话的全部参与者
users: dict[str, User]                # 本次会话的用户档案（含识人结果）

# 会话配置
config: SessionConfig  # 本次会话的配置

# 用户内存（运行时维护的动态信息）
user_memory: UserMemoryManager  # 用户识别与记忆
event_memory: EventMemoryManager  # 事件记忆
```

**主要方法**：

```python
# 按渠道+外部UID查找用户
user = transcript.find_user_by_channel_uid(
    channel="qq",
    uid="qq_12345"
)  # 返回 User 对象或None

# 导出为字典（便于序列化）
data = transcript.to_dict()

# 获取消息总数
msg_count = len(transcript.messages)

# 按角色过滤消息
agent_messages = [m for m in transcript.messages if m.role == "assistant"]
user_messages = [m for m in transcript.messages if m.role == "user"]
```

角色扮演提示词生成与注入示例：

```python
from sirius_chat.api import (
    RolePlayAnswer,
    aregenerate_agent_prompt_from_dependencies,
    agenerate_agent_prompts_from_answers,
    abuild_roleplay_prompt_from_answers_and_apply,
    create_session_config_from_selected_agent,
    generate_humanized_roleplay_questions,
    load_generated_agent_library,
    load_persona_generation_traces,
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
    dependency_files=["persona/notes.md", "persona/style_examples.txt"],
    persona_key="beichen_v2",
)
print(prompt)

# 查看完整本地生成轨迹
traces = load_persona_generation_traces(config.work_path, "beichen_v2")
print(traces[-1].generated_at, traces[-1].operation)

# 当依赖文件被更新后，直接重新生成人格
updated = await aregenerate_agent_prompt_from_dependencies(
    provider,
    work_path=config.work_path,
    agent_key="beichen_v2",
    model="deepseek-ai/DeepSeek-V3.2",
)
print(updated.agent.persona)
```

说明：

- `generate_humanized_roleplay_questions()` 会生成覆盖拟人化关键维度的问题模板。
- `agenerate_agent_prompts_from_answers(...)` 会从回答中生成完整 `GeneratedSessionPreset`（`agent + global_system_prompt`）。
- 生成时会显式输入 `agent_name`，确保主 AI 命名与提示词一致。
- `abuild_roleplay_prompt_from_answers_and_apply(...)` 会把生成结果写入 `config.preset`，并把完整生成过程本地化到 `<work_path>/generated_agent_traces/<agent_key>.json`。
- `dependency_files=[...]` 适合挂接角色卡、设定稿、语气样本、对白模板等本地素材；框架会读取文件内容参与生成，并记录快照与 sha256。
- 当输入中出现“拟人”“情感”“陪伴”“关系”“共情”等信号时，生成器会自动强化 prompt，使角色更有真实人感和情绪温度。
- `load_persona_generation_traces(...)` 可用于审计生成过程、追踪提示词来源、比对不同版本人格。
- `aregenerate_agent_prompt_from_dependencies(...)` 会重新读取最新依赖文件并重生同一个 agent key，适合外部系统在素材更新后做无问卷刷新。

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
- 完整生成轨迹保存在 `<work_path>/generated_agent_traces/<agent_key>.json`。
- 外部迁移建议见 `docs/migration-roleplay-v0.20.md`。

说明：

- 若 provider 原生支持异步（`generate_async`），引擎会直接 `await`。
- 若 provider 只有同步 `generate`，异步引擎会自动在线程中执行，避免阻塞事件循环。

## 方式二：通过子进程调用 CLI

外部系统若不直接嵌入 Python，可通过命令行调用并读取输出文件：

```bash
sirius-chat --config examples/session.json --work-path data/session_runtime --output transcript.json
```

### CLI 参数完整列表

| 参数 | 说明 | 示例 | 默认值 |
|------|------|------|--------|
| `--config` | 会话JSON配置文件路径 | `examples/session.json` | *必需* |
| `--work-path` | 运行工作目录 | `data/session_runtime` | 当前目录下data |
| `--output` | 输出transcript JSON路径 | `transcript.json` | `<work-path>/transcript.json` |
| `--message` | 用户消息文本（单条，可选） | `"你好"` | 否（启动交互输入） |
| `--speaker` | 消息发布者名称 | `"小王"` | `"用户"` |
| `--channel` | 消息渠道标识 | `"cli"` | `"cli"` |
| `--channel-user-id` | 渠道内用户ID | `"user123"` | 默认使用speaker |

**使用示例**：

```bash
# 方式1：交互式输入（多轮对话）
sirius-chat --config examples/session.json --work-path data/runtime

# 方式2：单条消息，非交互
sirius-chat --config examples/session.json --message "给我建议" --speaker "校务主任"

# 方式3：指定渠道身份（用于识人）
sirius-chat --config examples/session.json --channel "qq" --channel-user-id "qq_12345"

# 方式4：完整调用，指定输出路径
sirius-chat --config examples/session.json --work-path data/runtime \
    --message "推荐开源" --speaker "开源爱好者" --channel "wechat" \
    --output result.json
```

带状态持久化与恢复运行：

```bash
sirius-chat --config examples/session.json --work-path data/session_runtime
```

默认会自动恢复历史会话；若要强制从新会话开始，可在 `main.py` 入口使用 `--no-resume`。

## 方式三：动态群聊（参与者预先未知）

当参与者是动态加入（例如群聊环境）时，先初始化会话，再逐条调用 `run_live_message`：

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
    Message(role="user", speaker="小李", content="我是财务，关注成本", reply_mode="never"),
]

async def main() -> None:
    transcript = await engine.run_live_session(config=config)
    for turn in human_turns:
        transcript = await engine.run_live_message(
            config=config,
            transcript=transcript,
            turn=turn,
            session_reply_mode=turn.reply_mode,
            finalize_and_persist=False,
        )

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

#### User 与 Participant 的区别

Sirius Chat 中使用两个不同的模型来表示交互各方：

| 维度 | Participant | User |
|------|------------|------|
| **定义** | 会话中的一个角色 | 人类参与者的识别与档案 |
| **用途** | 会话配置中声明参与者 | 运行时识人与记忆管理 |
| **生命周期** | 固定（配置时声明） | 动态（可运行时新增） |
| **包含信息** | name, role, agent_id | user_id, name, traits, identities, profile, runtime |
| **示例** | agent="Beichen", human="小王" | user_id="qq_12345", aliases=["小王"] |

**使用场景**：

1. **静态群聊**（参与者已知）→ 在 SessionConfig 中声明 Participant

```python
from sirius_chat.api import SessionConfig, Participant, Agent

config = SessionConfig(
    agent=Agent(name="主助手"),
    participants=[
        Participant(name="小王", role="human"),
        Participant(name="李四", role="human"),
    ],
)
```

2. **动态群聊**（参与者运行时出现）→ 使用 User + run_live_session 初始化 + run_live_message 逐条输入

```python
# 运行时动态添加参与者
transcript = await engine.run_live_session(config=config)
for turn in [
    Message(role="user", speaker="小王", content="..."),
    Message(role="user", speaker="新的人", content="..."),  # 自动创建
]:
    transcript = await engine.run_live_message(config=config, transcript=transcript, turn=turn)

# 访问识别结果
for user_id, user in transcript.users.items():
    print(f"识别用户：{user.name}，别名：{user.aliases}")
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
from sirius_chat.config import ConfigManager
from pathlib import Path

# 加载基础配置并应用环境变量替换
config_mgr = ConfigManager.load_from_json(Path("config/base.json"))
# 支持 ${VAR_NAME} 占位符，会自动替换为环境变量值

# 也可加载环境特定的配置
dev_config = ConfigManager.load_from_json(Path("config/dev.json"))
```

#### OrchestrationPolicy 配置辅助函数

为了简化 `OrchestrationPolicy` 的构造，提供了一套配置辅助函数：

```python
from sirius_chat.config import (
    configure_orchestration_models,      # 配置任务模型
    configure_orchestration_budgets,     # 配置任务预算
    configure_orchestration_temperatures, # 配置任务温度  
    configure_orchestration_retries,     # 配置重试策略
    configure_full_orchestration,        # 完整一体化配置
)
from sirius_chat.api import OrchestrationPolicy

# 方式1：分段配置（推荐用于逐步调整）
orchestration = OrchestrationPolicy()
configure_orchestration_models(orchestration, {
    "memory_extract": "gpt-4o-mini",
    "event_extract": "gpt-4o-mini",
})
configure_orchestration_budgets(orchestration, {
    "memory_extract": 1200,
    "event_extract": 800,
})
configure_orchestration_temperatures(orchestration, {
    "memory_extract": 0.1,
    "event_extract": 0.3,
})

# 方式2：一体化配置（推荐用于新项目）
orchestration = configure_full_orchestration(
    unified_model="gpt-4o",
    budgets={"memory_extract": 1200}
)
```

**函数说明**：

- `configure_orchestration_models(policy, models_dict)`：设置各任务的模型
- `configure_orchestration_budgets(policy, budgets_dict)`：设置各任务的token预算
- `configure_orchestration_temperatures(policy, temps_dict)`：设置各任务的采样温度
- `configure_orchestration_retries(policy, retries_dict)`：设置各任务的重试次数
- `configure_full_orchestration(...)`：一次性配置所有参数，返回OrchestrationPolicy实例

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

### 性能监控

监控会话执行性能：

```python
from sirius_chat.performance import PerformanceProfiler, Benchmark

# 上下文管理器方式
with PerformanceProfiler("session_execution"):
    # 执行会话逻辑
    transcript = await engine.run_live_session(config=config)
    for turn in human_turns:
        transcript = await engine.run_live_message(
            config=config,
            transcript=transcript,
            turn=turn,
            session_reply_mode=turn.reply_mode,
            finalize_and_persist=False,
        )

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
- **多模型协同现已默认启用**。通过 `SessionConfig.orchestration` 配置 `task_models`、`task_budgets` 等实现记忆提取、事件提取、多模态解析的分任务路由。若需全部由一个模型处理，改为仅设置 `unified_model`（并清空 `task_models`）。
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



