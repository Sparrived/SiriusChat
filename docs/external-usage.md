# 外部程序使用 Sirius Chat

本文档说明如何从外部项目中调用 Sirius Chat 库（v1.0 多人格架构）。

## 安装

在你的项目环境中安装：

```bash
python -m pip install -e /path/to/sirius_chat
```

若通过打包产物安装，请替换为你的发布方式（如私有 index）。

## 方式一：多人格管理（推荐生产入口）

```python
from sirius_chat.persona_manager import PersonaManager

# 创建管理器
manager = PersonaManager("data", global_config={"auto_manage_napcat": True})

# 创建人格
manager.create_persona("yuebai", keywords=["温暖", "猫娘"])

# 启动所有已启用人格
results = manager.start_all()

# 停止单个人格
manager.stop_persona("yuebai")

# 获取人格状态
status = manager.get_persona_status("yuebai")
print(status)
```

## 方式二：直接使用 Emotional Engine

### 最小示例

```python
import asyncio
from pathlib import Path

from sirius_chat import create_emotional_engine, Message, Participant
from sirius_chat.providers.openai_compatible import OpenAICompatibleProvider


async def main() -> None:
    provider = OpenAICompatibleProvider(
        base_url="https://api.openai.com",
        api_key="YOUR_API_KEY",
    )

    engine = create_emotional_engine(
        work_path=Path("./data/runtime"),
        provider=provider,
        persona="warm_friend",
        config={
            "sensitivity": 0.6,
            "proactive_silence_minutes": 20,
        },
    )
    engine.start_background_tasks()

    # 处理消息
    result = await engine.process_message(
        message=Message(role="human", content="今天工作好累", speaker="u1"),
        participants=[Participant(name="小王", user_id="u1")],
        group_id="group:demo",
    )

    print(f"策略: {result['strategy']}")
    print(f"回复: {result.get('reply')}")
    # result.get('thought') 保留兼容，当前为空字符串（dual-output 已移除）

    # 保存状态
    engine.save_state()


asyncio.run(main())
```

### 多群聊场景

```python
async def handle_message(engine, group_id: str, user_id: str, content: str):
    """处理来自任意群聊的消息。"""
    result = await engine.process_message(
        message=Message(role="human", content=content, speaker=user_id),
        participants=[Participant(name=user_id, user_id=user_id)],
        group_id=group_id,
    )
    return result.get("reply")


async def main():
    engine = create_emotional_engine(
        work_path=Path("./data"),
        provider=provider,
        persona="sarcastic_techie",
    )
    engine.start_background_tasks()

    # 模拟多个群聊
    groups = ["group_a", "group_b", "group_c"]
    for group_id in groups:
        reply = await handle_message(engine, group_id, "user_1", "大家好")
        print(f"[{group_id}] {reply}")

    engine.save_state()
```

### 事件订阅

```python
from sirius_chat.core.events import SessionEventType

async def monitor_events(engine):
    """订阅引擎事件，实时监控认知过程。"""
    async for event in engine.event_bus.subscribe():
        if event.type == SessionEventType.COGNITION_COMPLETED:
            print(f"认知完成: {event.data}")
        elif event.type == SessionEventType.DECISION_COMPLETED:
            print(f"决策: {event.data['strategy']}")
        elif event.type == SessionEventType.EXECUTION_COMPLETED:
            print(f"执行完成: has_reply={event.data['has_reply']}")
```

## 方式二：通过 WorkspaceRuntime

```python
import asyncio
from pathlib import Path

from sirius_chat import open_workspace_runtime, Message, Participant


async def main() -> None:
    runtime = open_workspace_runtime(
        Path("./data/runtime"),
        config_path=Path("./data/config"),
    )

    # 创建 emotional engine（自动绑定 workspace provider）
    engine = runtime.create_emotional_engine(
        persona="warm_friend",
        config={"sensitivity": 0.6},
    )
    engine.start_background_tasks()

    result = await engine.process_message(
        message=Message(role="human", content="你好", speaker="u1"),
        participants=[Participant(name="小王", user_id="u1")],
        group_id="default",
    )

    print(result.get("reply"))
    await runtime.close()


asyncio.run(main())
```

## 人格加载

### 从模板加载

```python
from sirius_chat.core.persona_generator import PersonaGenerator

persona = PersonaGenerator.from_template("warm_friend")
# 可选: sarcastic_techie, gentle_caregiver, chaotic_jester, stoic_observer, protective_elder
```

### 从关键词生成

```python
persona = PersonaGenerator.from_keywords(
    name="小星",
    keywords=["温柔", "细心", "幽默"],
)
```

### 从 roleplay 资产桥接

```python
from sirius_chat.core.persona_generator import PersonaGenerator
from sirius_chat.roleplay_prompting import load_generated_agent_library

agents, _ = load_generated_agent_library(work_path)
preset = agents["my_agent"]
persona = PersonaGenerator.from_roleplay_preset(preset)
```

## 配置参数

`create_emotional_engine` 的 `config` 参数：

```python
config = {
    "working_memory_max_size": 20,
    "enable_semantic_retrieval": False,
    "sensitivity": 0.5,
    "delayed_queue_tick_interval_seconds": 10,
    "proactive_silence_minutes": 30,
    "proactive_check_interval_seconds": 60,
    "memory_promote_interval_seconds": 300,
    "event_memory_batch_size": 5,
    "consolidation_interval_seconds": 600,
    "task_model_overrides": {
        "response_generate": {"model": "gpt-4o", "max_tokens": 512},
        "cognition_analyze": {"model": "gpt-4o-mini", "max_tokens": 384},
    },
}
```

## 常见模式

### 静默模式（不主动回复）

```python
config = {"sensitivity": 0.0}  # 最低敏感度，几乎不回复
```

### 高活跃模式（积极回复）

```python
config = {"sensitivity": 0.9}  # 高敏感度，容易回复
```

### 关闭主动发言

```python
config = {"proactive_silence_minutes": 99999}  # 实际上永不主动发言
```

## 生命周期管理

```python
# 启动时
engine = create_emotional_engine(...)
engine.start_background_tasks()

# 运行时（处理消息）
result = await engine.process_message(...)

# 关闭时
engine.stop_background_tasks()
engine.save_state()
```

## 错误处理

```python
from sirius_chat.exceptions import SiriusChatException, ProviderError

async def safe_process(engine, message, participants, group_id):
    try:
        return await engine.process_message(message, participants, group_id)
    except ProviderError as e:
        print(f"Provider 错误: {e}")
        return {"strategy": "silent", "reply": None}
    except SiriusChatException as e:
        print(f"引擎错误: {e}")
        return {"strategy": "silent", "reply": None}
```
