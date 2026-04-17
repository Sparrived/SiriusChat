# v0.28 迁移指南：从 Legacy Engine 到 Emotional Group Chat Engine

> **版本**：v0.28.0  
> **日期**：2026-04-17  
> **迁移类型**：可选迁移。Legacy `AsyncRolePlayEngine` 继续保留在 `core/_legacy/` 中，默认行为不变。新引擎 `EmotionalGroupChatEngine` 需要显式启用。

---

## 目录

1. [迁移决策：是否切换到 Emotional Engine？](#1-迁移决策是否切换到-emotional-engine)
2. [数据迁移](#2-数据迁移)
3. [代码迁移](#3-代码迁移)
4. [新引擎完整使用指南](#4-新引擎完整使用指南)
5. [配置参考](#5-配置参考)
6. [API 对照表](#6-api-对照表)
7. [常见问题](#7-常见问题)

---

## 1. 迁移决策：是否切换到 Emotional Engine？

### 推荐切换到 Emotional Engine 的场景

- 你的应用场景是**群聊**（QQ群、微信群、Discord 服务器等），而非一对一对话。
- 你需要**情感感知**：识别用户情绪状态、生成共情回应、检测情感危机。
- 你需要**选择性参与**：AI 不应回复每条消息，而是根据 urgency、relevance、对话节奏决定是否参与。
- 你需要**长期关系建模**：用户画像、群体规范、互动频率、亲密度等。
- 你需要**主动发起**：群沉寂一段时间后，AI 能基于记忆或情感状态自然开启话题。

### 继续使用 Legacy Engine 的场景

- 一对一 RPG 角色扮演对话（legacy engine 的 `roleplay_prompting` 系统更成熟）。
- 需要 `session store`（SQLite/JSON）完整生命周期管理的场景（emotional engine 当前使用 `EngineStateStore` 替代）。
- 已有大量基于 `AsyncRolePlayEngine` 的自定义代码，迁移成本过高。

---

## 2. 数据迁移

### 2.1 自动迁移

v0.28 包含自动迁移脚本。当 `UserMemoryFileStore.load_all()` 首次检测到旧格式（`user_memory/*.json` 直接位于 `user_memory/` 目录下）时，会自动触发迁移：

1. 将旧文件移动到 `user_memory/groups/default/`
2. 生成默认的 `group_state.json`
3. 写入 `.migration_v0_28_done` 标记，防止重复迁移
4. 备份旧文件到 `user_memory/.backup_pre_v0_28/`

**你不需要手动执行任何操作。** 只需在升级后首次启动时观察日志即可。

### 2.2 手动迁移（如果需要）

```bash
python -m sirius_chat.memory.migration.v0_28_group_isolation --work-path /path/to/workspace
```

或从 Python 调用：

```python
from sirius_chat.memory.migration.v0_28_group_isolation import migrate_workspace
from pathlib import Path

migrate_workspace(Path("/path/to/workspace"))
```

### 2.3 迁移后验证

```python
from pathlib import Path
from sirius_chat.memory.migration.v0_28_group_isolation import detect_old_format

work_path = Path("/path/to/workspace")
assert not detect_old_format(work_path), "迁移未完成！"
print("迁移验证通过")
```

### 2.4 新增存储路径

v0.28  emotional engine 使用以下新路径：

| 路径 | 内容 | 说明 |
|------|------|------|
| `{work_path}/user_memory/groups/<group_id>/` | 群隔离用户记忆 | 自动从旧格式迁移 |
| `{work_path}/user_memory/groups/<group_id>/group_state.json` | 群级记忆 | 氛围历史、群体规范 |
| `{work_path}/event_memory/<group_id>/events.json` | 群隔离事件记忆 | 自动从旧格式迁移 |
| `{work_path}/episodic/<group_id>.jsonl` | 情景记忆 | 新引擎新增 |
| `{work_path}/semantic/users/<group_id>_<user_id>.json` | 用户语义画像 | 新引擎新增 |
| `{work_path}/semantic/groups/<group_id>.json` | 群体语义画像 | 新引擎新增 |
| `{work_path}/engine_state/` | 引擎运行态 | working memory、assistant emotion、token usage |

---

## 3. 代码迁移

### 3.1 CLI 切换（最简单）

```bash
# Legacy（默认）
python main.py --work-path ./data/my_workspace

# Emotional
python main.py --work-path ./data/my_workspace --engine emotional
```

### 3.2 通过 WorkspaceRuntime 创建

```python
import asyncio
from pathlib import Path
from sirius_chat.api import open_workspace_runtime, Message, Participant

async def main():
    runtime = open_workspace_runtime(Path("data/my_workspace"))

    # 创建 emotional engine（自动绑定 workspace provider 与 work_path）
    engine = runtime.create_emotional_engine()

    # 启动后台任务
    engine.start_background_tasks()

    # 处理消息
    result = await engine.process_message(
        Message(role="human", content="大家好！", speaker="alice"),
        [Participant(name="Alice", user_id="alice")],
        "my_group",
    )
    print(f"策略: {result['strategy']}, 回复: {result.get('reply')}")

    # 保存状态
    engine.save_state()

    # 停止后台任务
    engine.stop_background_tasks()
    await runtime.close()

asyncio.run(main())
```

### 3.3 独立创建（不依赖 WorkspaceRuntime）

```python
import asyncio
from pathlib import Path
from sirius_chat.api import EmotionalGroupChatEngine, Message, Participant
from sirius_chat.providers import OpenAICompatibleProvider

async def main():
    provider = OpenAICompatibleProvider(
        base_url="https://api.openai.com",
        api_key="YOUR_API_KEY",
    )

    engine = EmotionalGroupChatEngine(
        work_path=Path("data/emotional_standalone"),
        provider_async=provider,
        config={
            "enable_semantic_retrieval": False,  # 需要 sentence-transformers
            "proactive_silence_minutes": 30,
            "sensitivity": 0.5,
        },
    )

    engine.start_background_tasks()

    result = await engine.process_message(
        Message(role="human", content="这个项目报错了，怎么排查啊？", speaker="alice"),
        [Participant(name="Alice", user_id="alice")],
        "group_1",
    )
    print(f"策略: {result['strategy']}, urgency: {result['intent']['urgency_score']}")

    engine.stop_background_tasks()

asyncio.run(main())
```

### 3.4 事件订阅（监控 Pipeline 状态）

```python
from sirius_chat.api import SessionEventType

async def monitor(engine):
    async for event in engine.event_bus.subscribe():
        match event.type:
            case SessionEventType.PERCEPTION_COMPLETED:
                print(f"[感知完成] group={event.data['group_id']}")
            case SessionEventType.COGNITION_COMPLETED:
                print(f"[认知完成] intent={event.data['intent']['social_intent']}, "
                      f"emotion={event.data['emotion']['basic_emotion']}")
            case SessionEventType.DECISION_COMPLETED:
                print(f"[决策完成] strategy={event.data['strategy']}")
            case SessionEventType.EXECUTION_COMPLETED:
                print(f"[执行完成] has_reply={event.data['has_reply']}")
            case SessionEventType.PROACTIVE_RESPONSE_TRIGGERED:
                print(f"[主动触发] type={event.data['trigger_type']}")
            case SessionEventType.DELAYED_RESPONSE_TRIGGERED:
                print(f"[延迟触发] item={event.data['item_id']}")

# 启动监听
task = asyncio.create_task(monitor(engine))
```

---

## 4. 新引擎完整使用指南

### 4.1 单轮消息处理

```python
from sirius_chat.api import Message, Participant

result = await engine.process_message(
    message=Message(
        role="human",
        content="用户消息内容",
        speaker="user_id",      # 必填
        group_id="group_id",    # 可选，默认 "default"
    ),
    participants=[
        Participant(name="用户名", user_id="user_id"),
    ],
    group_id="group_id",
)
```

`result` 结构：

```python
{
    "strategy": "immediate" | "delayed" | "silent" | "proactive",
    "reply": "生成的回复文本" | None,
    "emotion": {
        "valence": 0.5,         # -1.0 ~ 1.0
        "arousal": 0.7,         # 0.0 ~ 1.0
        "basic_emotion": "joy",
        "intensity": 0.8,
    },
    "intent": {
        "social_intent": "help_seeking",
        "urgency_score": 85,
        "relevance_score": 0.9,
        "confidence": 0.92,
    },
}
```

### 4.2 后台任务管理

```python
# 启动（幂等：可多次调用，不会重复启动）
engine.start_background_tasks()

# 停止
engine.stop_background_tasks()
```

后台任务包括：

| 任务 | 间隔 | 职责 |
|------|------|------|
| delayed_queue ticker | 10 秒 | 检查所有活跃群的延迟响应队列 |
| proactive checker | 60 秒 | 检查长时间沉默群是否需要主动发起 |
| memory promoter | 5 分钟 | 将高重要性工作记忆晋升到情景记忆 |
| consolidator | 10 分钟 | 将情景事件聚合为语义用户画像 |

### 4.3 状态持久化

```python
# 保存当前运行态到 disk
engine.save_state()

# 下次启动时恢复
engine.load_state()
```

持久化内容：
- 所有群的 working memory 条目
- AssistantEmotionState（助手自身情感）
- group_last_message_at 时间戳
- token_usage_records

### 4.4 延迟响应队列手动触发

```python
# 手动触发某个群的延迟队列检查
results = await engine.tick_delayed_queue("group_id")
for r in results:
    print(f"[延迟回复] {r['reply']}")
```

### 4.5 主动触发检查

```python
# 手动触发某个群的主动检查
result = await engine.proactive_check("group_id")
if result:
    print(f"[主动发起] {result['reply']}")
```

### 4.6 SKILL 集成

```python
from sirius_chat.skills.registry import SkillRegistry
from sirius_chat.skills.executor import SkillExecutor

# 创建 skill runtime
registry = SkillRegistry()
registry.reload_from_directory(Path("skills/"), include_builtin=True)
executor = SkillExecutor(layout)

# 附加到 engine
engine.set_skill_runtime(
    skill_registry=registry,
    skill_executor=executor,
)

# 后续 process_message() 会自动解析并执行 [SKILL_CALL: ...] 标记
```

### 4.7 Token 追踪

```python
# engine.token_usage_records 是一个列表
for record in engine.token_usage_records:
    print(f"{record.task_name}: {record.total_tokens} tokens")

# 随 save_state() 持久化，随 load_state() 恢复
```

### 4.8 情感孤岛检测

```python
from sirius_chat.models.emotion import EmotionState

# 获取最近情感状态（需要你自己维护 recent_emotions 字典）
recent_emotions = {
    "alice": EmotionState(valence=0.2, arousal=0.3),
    "bob": EmotionState(valence=-0.9, arousal=0.8),  # 异常
}

islands = engine.emotion_analyzer.detect_emotion_islands("group_id", recent_emotions)
for island in islands:
    print(f"情感孤岛: {island['user_id']} 偏离度={island['deviation_score']}")
```

---

## 5. 配置参考

### 5.1 创建引擎时的配置项

```python
config = {
    # 功能开关
    "enable_semantic_retrieval": False,      # 语义相似度检索（需要 sentence-transformers）

    # 工作记忆
    "working_memory_max_size": 20,           # 滑动窗口容量
    "working_memory_promote_threshold": 0.3, # 自动晋升到情景记忆的阈值

    # 后台任务间隔
    "delayed_queue_tick_interval_seconds": 10,
    "proactive_check_interval_seconds": 60,
    "memory_promote_interval_seconds": 300,
    "consolidation_interval_seconds": 600,

    # 主动触发
    "proactive_silence_minutes": 30,

    # 阈值引擎
    "sensitivity": 0.5,                      # 全局敏感度 0.0~1.0

    # 模型路由覆盖
    "task_model_overrides": {
        "emotion_analyze": {"model": "gpt-4o-mini", "temperature": 0.2, "max_tokens": 256},
        "intent_analyze": {"model": "gpt-4o-mini", "temperature": 0.2, "max_tokens": 256},
        "response_generate": {"model": "gpt-4o", "temperature": 0.7, "max_tokens": 512},
    },
}
```

### 5.2 语义检索（可选依赖）

```bash
# 安装 sentence-transformers 以启用语义相似度检索
pip install sentence-transformers
```

然后在配置中启用：

```python
config = {"enable_semantic_retrieval": True}
```

---

## 6. API 对照表

### Legacy vs Emotional 核心 API

| 操作 | Legacy (`AsyncRolePlayEngine`) | Emotional (`EmotionalGroupChatEngine`) |
|------|-------------------------------|----------------------------------------|
| 创建引擎 | `AsyncRolePlayEngine(provider)` | `EmotionalGroupChatEngine(work_path, provider_async, config)` |
| 处理单轮消息 | `run_live_message(config, transcript, turn, ...)` | `process_message(message, participants, group_id)` |
| 启动后台任务 | `BackgroundTaskManager` 内部管理 | `start_background_tasks()` |
| 停止后台任务 | 随引擎销毁 | `stop_background_tasks()` |
| 保存状态 | `finalize_and_persist` | `save_state()` |
| 恢复状态 | 从 session store | `load_state()` |
| 事件订阅 | `engine.subscribe(transcript)` | `engine.event_bus.subscribe()` |
| SKILL 集成 | `engine.set_shared_skill_runtime(...)` | `engine.set_skill_runtime(...)` |
| 意图分析 | `intent_analysis` 任务 | `intent_analyzer.analyze()`（内部调用） |
| 情感分析 | 无 | `emotion_analyzer.analyze()`（内部调用） |
| 记忆检索 | `memory_manager` 任务 | `memory_retriever.retrieve()`（内部调用） |
| Token 追踪 | `Transcript.token_usage_records` | `engine.token_usage_records` |

### 数据模型对照

| Legacy | Emotional | 说明 |
|--------|-----------|------|
| `UserMemoryManager.entries[user_id]` | `UserMemoryManager.entries[group_id][user_id]` | 群隔离 |
| `EventMemoryFileStore` → `memory/events/events.json` | `EventMemoryFileStore` → `event_memory/<group_id>/events.json` | 群隔离 |
| `Transcript.messages` | `WorkingMemoryManager` 窗口 | 新引擎使用工作记忆 |
| `SessionStore` (SQLite/JSON) | `EngineStateStore` (`engine_state/`) | 新引擎状态持久化 |
| 无 | `EpisodicMemoryManager` | 新增情景记忆层 |
| 无 | `SemanticMemoryManager` | 新增语义记忆层 |

---

## 7. 常见问题

### Q1: 升级后旧数据会丢失吗？

**不会。** v0.28 包含自动迁移脚本。首次加载时若检测到旧格式，会自动迁移并备份。旧文件保留在 `user_memory/.backup_pre_v0_28/` 中。

### Q2: 可以同时运行 legacy 和 emotional 两个引擎吗？

**可以，但不推荐。** 两个引擎不共享内部状态。若需要并行运行，建议为 emotional engine 使用独立的 `work_path`，避免存储路径冲突。

### Q3: `process_message()` 返回的 `reply` 为 None 是什么意思？

表示当前策略为 `SILENT` 或 `DELAYED`：
- `SILENT`：AI 决定不回复，仅后台观察学习。
- `DELAYED`：消息已入延迟队列，等待合适时机再回复。可通过 `tick_delayed_queue()` 或后台 ticker 触发。

### Q4: 如何控制 AI 的回复频率？

通过 `sensitivity` 配置（0.0~1.0）：
- 高敏感度（如 0.8）：阈值降低，AI 更容易回复。
- 低敏感度（如 0.2）：阈值提高，AI 更克制。

也可通过 `ResponseStrategyEngine` 的特殊规则影响：
- 被 @ 提及 + HELP_SEEKING → 强制 IMMEDIATE
- EMOTIONAL + 高 negative arousal → urgency +20，优先 IMMEDIATE

### Q5: 语义检索提示 "sentence-transformers not installed" 怎么办？

```bash
pip install sentence-transformers
```

或在配置中关闭：
```python
config = {"enable_semantic_retrieval": False}
```

### Q6: 后台任务没有启动，延迟队列不工作？

确保调用了 `engine.start_background_tasks()`。该方法是幂等的，可多次调用不会重复启动。

### Q7: 如何查看引擎内部的决策过程？

订阅事件流：
```python
async for event in engine.event_bus.subscribe():
    print(event.type.value, event.data)
```

事件类型包括：`PERCEPTION_COMPLETED`、`COGNITION_COMPLETED`、`DECISION_COMPLETED`、`EXECUTION_COMPLETED`、`PROACTIVE_RESPONSE_TRIGGERED`、`DELAYED_RESPONSE_TRIGGERED`。

### Q8: 群隔离后，如何跨群查找同一用户？

```python
# 遍历所有群查找
for group_id, users in engine.user_memory.entries.items():
    if "user_id" in users:
        user = users["user_id"]
        print(f"在群 {group_id} 中找到用户")
```

或使用 `resolve_user_id()`：
```python
user = engine.user_memory.resolve_user_id("user_id")  # 返回第一个匹配
```

### Q9: 新引擎支持多模态输入吗？

当前 `EmotionalGroupChatEngine` 的 `process_message()` 主要处理文本消息。多模态支持（图片、视频）在 `ResponseAssembler` 层预留了扩展点，但完整多模态 pipeline 需要后续迭代实现。若需多模态，建议继续使用 legacy engine。

### Q10: 如何回滚到 v0.27？

1. 恢复代码到 v0.27.14 tag。
2. 将 `user_memory/groups/default/` 下的文件移回 `user_memory/`（逆向迁移）。
3. 删除 `user_memory/.migration_v0_28_done` 标记。

---

> **文档版本**：v0.28.0  
> **对应代码分支**：`feature/v0.28-emotional-group-chat`  
> **GitHub Release**：https://github.com/Sparrived/SiriusChat/releases/tag/v0.28.0
