---
name: write-tests
description: "编写新测试文件或为现有模块补充测试时使用，覆盖命名约定、速度要求、Mock 模式与断言规范。关键词：测试编写、单元测试、pytest、MockProvider、测试速度、精准测试。"
---

# 测试编写指南

## 目标

为 Sirius Chat 编写正确、快速、可维护的 pytest 测试。所有测试必须满足三个核心要求：

1. **快（Fast）**：单个测试用例应在 **1 秒以内** 完成；整个测试套件目标 **30 秒以内**。
2. **准（Precise）**：一个测试函数只验证一个概念，断言直接指向被测行为。
3. **稳（Stable）**：无真实网络调用、无随机时间依赖、无副作用残留。

---

## 一、速度红线

### ❌ 禁止的写法

```python
# 禁止：在测试中手动睡眠
import asyncio
await asyncio.sleep(8)   # 等同于浪费 8 秒
time.sleep(3)

# 禁止：启用 debounce（除非测试的正是 debounce 本身）
orchestration=OrchestrationPolicy(
    message_debounce_seconds=8.0,   # 会导致每次 run_live_message 睡 8 秒
)

# 禁止：启用耗时后台任务（除非测试的正是该任务）
orchestration=OrchestrationPolicy(
    enable_self_memory=True,        # 会启动后台定时任务
    consolidation_enabled=True,
)
task_enabled={
    "memory_extract": True,   # 会触发额外 LLM 调用
    "event_extract": True,
}
```

### ✅ 标准配置模板（绝大多数测试应使用此配置）

```python
orchestration=OrchestrationPolicy(
    unified_model="mock-model",
    enable_self_memory=False,       # ← 关闭后台提取
    task_enabled={
        "memory_extract": False,    # ← 关闭所有辅助 LLM 任务
        "event_extract": False,
    },
    # message_debounce_seconds 默认为 0.0，无需显式设置
)
```

**若测试需要 LLM 并发控制**，注意 `max_concurrent_llm_calls=1`（默认），会将 LLM 串行化。若需要并行测试多路消息，需显式设为 `max_concurrent_llm_calls=0`。

---

## 二、标准 Provider 和引擎初始化

所有测试禁止真实网络调用，必须使用 `MockProvider`：

```python
from sirius_chat.providers.mock import MockProvider

provider = MockProvider(
    responses=[
        "第一条回复",
        "第二条回复",
    ]
)
engine = AsyncRolePlayEngine(provider=provider)
```

`MockProvider` 按序返回 responses，用完后循环最后一条。可通过 `provider.requests` 检查引擎向 LLM 发出的所有请求。

---

## 三、标准 SessionConfig 初始化

```python
from pathlib import Path
from sirius_chat.config import Agent, AgentPreset, SessionConfig, OrchestrationPolicy

config = SessionConfig(
    work_path=Path("data/tests/<test_feature_name>"),  # ← 每个测试用独立子目录
    preset=AgentPreset(
        agent=Agent(name="主助手", persona="测试用 AI", model="mock-model"),
        global_system_prompt="测试系统提示词",
    ),
    orchestration=OrchestrationPolicy(
        unified_model="mock-model",
        enable_self_memory=False,
        task_enabled={
            "memory_extract": False,
            "event_extract": False,
        },
    ),
)
```

**注意**：`work_path` 须使用每个测试特有的路径，避免测试间通过持久化文件产生状态污染。

---

## 四、标准 run_live_turns 辅助函数

每个需要多轮消息的测试文件应复制此辅助函数（不要跨文件共享 fixture，保持测试自包含）：

```python
async def _run_live_turns(
    *,
    engine: AsyncRolePlayEngine,
    config: SessionConfig,
    human_turns: list[Message],
    transcript=None,
):
    transcript = await engine.run_live_session(config=config, transcript=transcript)
    for index, turn in enumerate(human_turns):
        transcript = await engine.run_live_message(
            config=config,
            turn=turn,
            transcript=transcript,
            session_reply_mode=turn.reply_mode,
            finalize_and_persist=index == len(human_turns) - 1,
        )
    return transcript
```

---

## 五、异步测试写法

本项目使用 `asyncio.run()` 包裹异步测试，而非 `@pytest.mark.asyncio`（保持兼容性）：

```python
def test_something() -> None:
    async def _run() -> None:
        # ... 异步测试逻辑
        pass

    asyncio.run(_run())
```

---

## 六、断言规范

### 精准断言：只断言被测行为

```python
# ✅ 好：直接断言目标行为
assert len(provider.requests) == 2
assert assistant_messages[-1].content == "预期回复"
assert transcript.messages[0].role == "user"

# ❌ 坏：断言无关的实现细节
assert len(transcript.messages) == 99   # 消息总数随功能迭代变化，脆弱
assert "测试系统提示词" in provider.requests[0]["messages"][0]["content"]  # 提示词结构内部细节
```

### 负向断言：同样需要精准

```python
# ✅ 好：断言空/无副作用
assert provider.requests == []    # 确认未调用 LLM
assistant_msgs = [m for m in transcript.messages if m.role == "assistant"]
assert len(assistant_msgs) == 0   # 确认无 AI 回复

# ❌ 坏：assertFalse / assert not ... 而不说明原因（加注释说明预期）
assert not some_field  # 为什么不应该有值？需注释
```

---

## 七、文件组织规范

| 场景 | 操作 |
|---|---|
| 测试本文件模块或功能（如 `core/engine.py`） | 加入 `tests/test_engine.py` |
| 测试引擎高阶流程（多轮对话、编排、回调） | 加入 `tests/test_async_engine.py` |
| 测试 Bug 修复（防回归） | 加入 `tests/test_bugfix_round2.py`（或下一个 roundN） |
| 测试某个新的独立特性（> 5 个测试函数） | 新建 `tests/test_<feature>.py` |
| 测试 < 5 个用例的小功能 | **不要新建文件**，合并到同域文件 |

**禁止**出现仅含 1-2 个测试函数的孤立文件。

---

## 八、测试命名规范

```
test_<被测对象>_<条件>_<期望结果>()

✅ test_run_live_message_with_no_participants_returns_empty_transcript
✅ test_orchestration_policy_negative_debounce_raises_value_error
✅ test_mock_provider_returns_responses_in_order

❌ test_basic                    # 太模糊
❌ test_engine_works             # 不知道测什么
❌ test_1                        # 完全无意义
```

---

## 九、常见陷阱速查

| 陷阱 | 表现 | 解决 |
|---|---|---|
| `message_debounce_seconds > 0` | 每条消息睡 N 秒，测试极慢 | 不要设置此字段（默认 0） |
| `enable_self_memory=True` | 后台任务持续运行，teardown 慢 | 测 SelfMemory 功能时才开启 |
| `task_enabled` 未全部关闭 | MockProvider 被追加调用推高 `requests` 计数 | 关闭所有不需要的任务 |
| `work_path` 多测试共享 | 上一个测试的持久化文件影响下一个 | 每测试用唯一子目录 |
| `max_concurrent_llm_calls=1`（默认）| 并发消息测试串行执行 | 需要并发时设为 `0` |
| 断言 `provider.requests` 数量时忘算辅助任务 | memory_extract 等会产生额外调用 | 先关闭无关任务再数 |

---

## 十、完整示例

```python
"""Tests for [feature name].

Covers:
- [scenario 1]
- [scenario 2]
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from sirius_chat.async_engine import AsyncRolePlayEngine
from sirius_chat.config import Agent, AgentPreset, SessionConfig, OrchestrationPolicy
from sirius_chat.models import Message
from sirius_chat.providers.mock import MockProvider


async def _run_live_turns(*, engine, config, human_turns, transcript=None):
    transcript = await engine.run_live_session(config=config, transcript=transcript)
    for i, turn in enumerate(human_turns):
        transcript = await engine.run_live_message(
            config=config, turn=turn, transcript=transcript,
            finalize_and_persist=i == len(human_turns) - 1,
        )
    return transcript


def test_<feature>_<condition>_<expected>() -> None:
    """一句话说明这个测试在验证什么。"""
    async def _run() -> None:
        provider = MockProvider(responses=["AI回复内容"])
        engine = AsyncRolePlayEngine(provider=provider)
        config = SessionConfig(
            work_path=Path("data/tests/<feature>"),
            preset=AgentPreset(
                agent=Agent(name="主助手", persona="测试", model="mock-model"),
                global_system_prompt="测试",
            ),
            orchestration=OrchestrationPolicy(
                unified_model="mock-model",
                enable_self_memory=False,
                task_enabled={
                    "memory_extract": False,
                    "event_extract": False,
                },
            ),
        )

        transcript = await _run_live_turns(
            engine=engine,
            config=config,
            human_turns=[Message(role="user", speaker="用户", content="输入内容")],
        )

        assert len(provider.requests) == 1
        assistant_msgs = [m for m in transcript.messages if m.role == "assistant"]
        assert assistant_msgs[-1].content == "AI回复内容"

    asyncio.run(_run())
```
