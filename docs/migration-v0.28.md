# v0.28 迁移指南

> **版本**：v0.28.0+  
> **日期**：2026-04-18  
> **重要变更**：`EmotionalGroupChatEngine` 已成为默认引擎。Legacy `AsyncRolePlayEngine` 已归档到 `sirius_chat/core/_legacy/`，不再接收新功能。

---

## 目录

1. [发生了什么变化](#1-发生了什么变化)
2. [数据迁移](#2-数据迁移)
3. [API 迁移](#3-api-迁移)
4. [配置迁移](#4-配置迁移)
5. [常见问题](#5-常见问题)

---

## 1. 发生了什么变化

### 引擎变更

| 维度 | v0.28+（默认） | Legacy（已归档） |
|------|----------------|-----------------|
| 引擎类 | `EmotionalGroupChatEngine` | `AsyncRolePlayEngine` |
| 认知架构 | 四层（感知→认知→决策→执行） | 单轮消息编排 |
| 情绪分析 | 2D valence-arousal + 助手自身情绪 | 无 |
| 意图分析 | 目的驱动（help_seeking / emotional / social / silent） | 二元 should_reply |
| 记忆 | 三层（工作/情景/语义）+ 自传体记忆 | 单层用户事实 + 事件记忆 v2 |
| 延迟回复 | 话题间隙触发 | 无 |
| 主动发言 | 时间/记忆/情感触发 | 无 |
| 群聊隔离 | 原生支持 | 迁移后才支持 |
| 模型路由 | 按任务类型自动选择 | 固定模型 |
| 人格 | `PersonaProfile` 结构化 | `AgentPreset` 模板化 |
| 输出格式 | `<think>` + `<say>` 双输出 | 单输出 |

### CLI 变更

```bash
# v0.28+ 默认使用 emotional engine
python main.py --config session.jsonc

# 显式指定（推荐）
python main.py --config session.jsonc --engine emotional --persona warm_friend

# Legacy 引擎仍可运行（但不推荐）
python main.py --config session.jsonc --engine legacy
```

---

## 2. 数据迁移

### 2.1 自动迁移

v0.28 包含自动迁移脚本。当 `UserMemoryFileStore.load_all()` 首次检测到旧格式时，会自动触发迁移：

1. 将旧文件移动到 `user_memory/groups/default/`
2. 生成默认的 `group_state.json`
3. 写入 `.migration_v0_28_done` 标记
4. 备份旧文件到 `user_memory/.backup_pre_v0_28/`

### 2.2 手动迁移

```bash
python -m sirius_chat.memory.migration.v0_28_group_isolation --work-path /path/to/workspace
```

### 2.3 新增存储路径

v0.28+ 新增以下路径：

| 路径 | 说明 |
|------|------|
| `{work_path}/episodic/<group_id>.jsonl` | 情景记忆 |
| `{work_path}/semantic/users/<group_id>_<user_id>.json` | 用户语义画像 |
| `{work_path}/semantic/groups/<group_id>.json` | 群体语义画像 |
| `{work_path}/engine_state/` | 引擎运行态持久化 |
| `{work_path}/engine_state/persona.json` | 人格设定 |

---

## 3. API 迁移

### 旧 API（Legacy）

```python
from sirius_chat.async_engine import AsyncRolePlayEngine

engine = AsyncRolePlayEngine(provider)
transcript = await engine.run_live_session(config)
transcript = await engine.run_live_message(
    config=config, transcript=transcript, turn=message
)
```

### 新 API（Emotional）

```python
from sirius_chat.api import create_emotional_engine, Message, Participant

engine = create_emotional_engine(
    work_path="/path/to/workspace",
    provider=provider,
    persona="warm_friend",
    config={"sensitivity": 0.6},
)
engine.start_background_tasks()

result = await engine.process_message(
    message=Message(role="human", content="你好", speaker="u1"),
    participants=[Participant(name="user", user_id="u1")],
    group_id="default",
)

print(result["reply"])   # 说出口的话
print(result["thought"]) # 内心独白（来自 <think>）

engine.save_state()
```

### 关键差异

| 差异 | Legacy | Emotional |
|------|--------|-----------|
| 入口 | `run_live_message()` | `process_message()` |
| 参数 | `config`, `transcript`, `turn` | `message`, `participants`, `group_id` |
| 返回 | `Transcript` | `dict[str, Any]` |
| 回复 | `transcript.messages[-1]` | `result["reply"]` |
| 后台任务 | 自动启动 | 手动 `start_background_tasks()` |
| 持久化 | 自动 | 手动 `save_state()` |

---

## 4. 配置迁移

### 旧配置（Legacy）

```json
{
  "generated_agent_key": "main_agent",
  "providers": [...],
  "orchestration": {
    "task_models": {...},
    "task_enabled": {...}
  }
}
```

### 新配置（Emotional）

```json
{
  "providers": [...],
  "persona": "warm_friend",
  "emotional_engine": {
    "sensitivity": 0.5,
    "proactive_silence_minutes": 30,
    "task_model_overrides": {
      "response_generate": { "model": "gpt-4o", "max_tokens": 512 },
      "cognition_analyze": { "model": "gpt-4o-mini", "max_tokens": 384 }
    }
  }
}
```

### 配置字段对照

| Legacy 字段 | Emotional 等效字段 | 说明 |
|------------|-------------------|------|
| `generated_agent_key` | `persona` | 人格模板名或 `"generated"` |
| `orchestration.task_models` | `emotional_engine.task_model_overrides` | 按任务覆盖模型参数 |
| `orchestration.task_enabled` | — | Emotional Engine 无独立任务开关 |
| `history_max_messages` | — | Emotional Engine 使用 `working_memory_max_size` |
| `min_reply_interval_seconds` | — | Emotional Engine 使用延迟队列机制 |

---

## 5. 常见问题

### Q: Legacy 引擎还能用吗？

A: 可以，但不推荐。代码位于 `sirius_chat/core/_legacy/`，不再接收新功能。

```bash
python main.py --engine legacy
```

### Q: 已有的 roleplay 资产（generated_agents.json）还能用吗？

A: 可以。`PersonaGenerator.from_roleplay_preset()` 可以将 `AgentPreset` 桥接为 `PersonaProfile`。

```python
from sirius_chat.core.persona_generator import PersonaGenerator

preset = load_generated_agent_profile(work_path, "my_agent")
persona = PersonaGenerator.from_roleplay_preset(preset)
```

### Q: 情感分析一定要调用 LLM 吗？

A: 不需要。`CognitionAnalyzer` 的规则引擎覆盖 ~90% 的情况，零 LLM 成本。只有置信度不足时才触发一次 LLM fallback。

### Q: `<think>` 内容会暴露给用户吗？

A: 不会。`<think>` 是模型的内心独白，只存入 `AutobiographicalMemoryManager`，不会发送到用户。用户只收到 `<say>` 内容。
