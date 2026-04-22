# Sirius Chat 配置指南

## 概述

Sirius Chat v0.28+ 使用 **EmotionalGroupChatEngine** 作为默认引擎。配置体系已分为两套：

| 引擎 | 配置文件 | 适用入口 |
|------|---------|---------|
| **Emotional (v0.28+)** | `session_emotional.json` | `main.py --engine emotional --config ...` |
| **Legacy (已归档)** | `session.json` / `session_config.json` | `main.py --engine legacy --config ...`（不推荐） |

Legacy 配置面向 `AsyncRolePlayEngine`（`SessionConfig` + `OrchestrationPolicy`），已归档到 `docs/engine-legacy.md`，新用户无需关注。

本文档重点说明 **Emotional Engine 配置**；Legacy 配置保留在文末第 10 节供查阅。

## 1. Emotional Engine 配置（v0.28+ 推荐）

适用入口：

```bash
python main.py --engine emotional --config session_emotional.json
```

示例：

```json
{
  "providers": [
    {
      "type": "openai-compatible",
      "base_url": "https://api.openai.com",
      "api_key": "${OPENAI_API_KEY}",
      "healthcheck_model": "gpt-4o-mini"
    }
  ],

  "persona": "warm_friend",

  "emotional_engine": {
    "working_memory_max_size": 20,
    "enable_semantic_retrieval": false,
    "sensitivity": 0.5,

    "delayed_queue_tick_interval_seconds": 10,
    "proactive_silence_minutes": 30,
    "proactive_check_interval_seconds": 60,

    "memory_promote_interval_seconds": 300,
    "working_memory_promote_threshold": 0.3,

    "consolidation_interval_seconds": 600,

    "task_model_overrides": {
      "response_generate": { "model": "gpt-4o", "max_tokens": 512, "temperature": 0.7 },
      "cognition_analyze": { "model": "gpt-4o-mini", "max_tokens": 384, "temperature": 0.2 }
    }
  }
}
```

说明：

- 文件可直接写成 JSONC，允许 `//` 注释
- `persona` 支持模板名（`warm_friend`、`sarcastic_techie` 等）或 `"generated"`（从 roleplay 资产自动加载）
- `emotional_engine` 下的字段全部可选，缺失时使用默认值
- 完整示例见 `examples/session_emotional.json`

## 2. 完整 SessionConfig 文件

适用入口：

- ConfigManager.load_from_json(...)
- AsyncRolePlayEngine + SessionConfig
- 需要手写完整 agent / global_system_prompt 的高级场景

示例：

```json
{
  "work_path": "./config/chat_session",
  "data_path": "./data/chat_session",
  "global_system_prompt": "你是一个有帮助的 AI 助手。",
  "agent": {
    "name": "MyAI",
    "persona": "友好、专业的 AI 助手",
    "model": "gpt-4-turbo",
    "temperature": 0.7,
    "max_tokens": 512,
    "metadata": {
      "multimodal_model": "gpt-4o"
    }
  },
  "history_max_messages": 24,
  "history_max_chars": 6000,
  "max_recent_participant_messages": 5,
  "enable_auto_compression": true,
  "orchestration": {
    "task_enabled": {
      "memory_extract": true,
      "event_extract": true,
      "intent_analysis": true
    },
    "task_models": {
      "memory_extract": "gpt-4o-mini",
      "event_extract": "gpt-4o-mini",
      "intent_analysis": "gpt-4o-mini"
    }
  }
}
```

说明：

- 双根模式下，work_path 表示配置根，data_path 表示运行根
- 若不需要分离路径，可把两者写成同一路径
- 这类文件不建议直接给 main.py --config 使用；CLI/main 推荐轻量会话配置

## 2. Emotional Engine 配置字段说明

### providers

与 legacy 配置相同。支持 `type`、`api_key`、`base_url`、`healthcheck_model`、`models`。

### persona

- 类型：字符串
- 含义：人格模板名称或 `"generated"`
- 可选值：`warm_friend`、`sarcastic_techie`、`gentle_caregiver`、`chaotic_jester`、`stoic_observer`、`protective_elder`、`generated`
- CLI 可用 `--persona` 覆盖此字段

### emotional_engine

| 字段 | 类型 | 默认值 | 含义 |
|------|------|--------|------|
| `working_memory_max_size` | int | 20 | 工作记忆窗口容量（条数） |
| `enable_semantic_retrieval` | bool | false | 是否启用语义检索 |
| `sensitivity` | float | 0.5 | 回复敏感度（0.0~1.0），越高越容易回复 |
| `delayed_queue_tick_interval_seconds` | int | 10 | 延迟回复队列扫描间隔 |
| `proactive_silence_minutes` | int | 30 | 沉默多久后可能触发主动发言 |
| `proactive_check_interval_seconds` | int | 60 | 主动触发检查间隔 |
| `memory_promote_interval_seconds` | int | 300 | 事件记忆缓冲检查间隔（观察提取 promoter） |
| `working_memory_promote_threshold` | float | 0.3 | 保留兼容字段 |
| `event_memory_batch_size` | int | 5 | 单用户缓冲达到此数量触发 LLM 批量提取 |
| `consolidation_interval_seconds` | int | 600 | 语义整合间隔（event_memory.entries → 语义画像） |
| `task_model_overrides` | dict | {} | 按任务覆盖模型参数，见下表 |

#### task_model_overrides

按认知任务指定模型、max_tokens、temperature：

| 任务名 | 默认模型 | 说明 |
|--------|---------|------|
| `response_generate` | gpt-4o | 回复生成 |
| `cognition_analyze` | gpt-4o-mini | 统一情绪+意图分析 |
| `event_extract` | gpt-4o-mini | 事件记忆 V2 观察提取 |
| `emotion_analyze` | gpt-4o-mini | 情感分析（保留兼容） |
| `intent_analyze` | gpt-4o-mini | 意图分析（保留兼容） |

示例：

```json
"task_model_overrides": {
  "response_generate": { "model": "gpt-4o", "max_tokens": 512, "temperature": 0.7 },
  "cognition_analyze": { "model": "gpt-4o-mini", "max_tokens": 384, "temperature": 0.2 }
}
```

## 4. 环境变量替换

ConfigManager.load_from_json(...) 支持 ${VAR_NAME} 形式的环境变量替换。

示例：

```json
{
  "work_path": "${SIRIUS_CONFIG_ROOT}",
  "data_path": "${SIRIUS_DATA_ROOT}",
  "agent": {
    "name": "SiriusAI",
    "persona": "专业、友善、稳定",
    "model": "${SIRIUS_MODEL}",
    "metadata": {
      "api_key": "${OPENAI_API_KEY}"
    }
  },
  "orchestration": {
    "task_models": {
      "memory_extract": "${MEMORY_EXTRACT_MODEL}",
      "event_extract": "${EVENT_EXTRACT_MODEL}"
    }
  }
}
```

说明：

- 未定义的环境变量会保留原占位符
- 轻量会话配置在 bootstrap 时同样支持这套替换逻辑

## 3. 环境变量替换

ConfigManager 支持 `${VAR_NAME}` 形式的环境变量替换。

示例：

```json
{
  "providers": [
    {
      "type": "openai-compatible",
      "base_url": "https://api.openai.com",
      "api_key": "${OPENAI_API_KEY}"
    }
  ]
}
```

说明：

- 未定义的环境变量会保留原占位符
- Emotional 和 Legacy 配置都支持这套替换逻辑

## 4. 常见加载方式

### Emotional Engine（推荐）

```python
from sirius_chat.api import create_emotional_engine

engine = create_emotional_engine(
    work_path="/path/to/workspace",
    provider=provider,
    persona="warm_friend",
    config={
        "sensitivity": 0.6,
        "proactive_silence_minutes": 20,
    },
)
engine.start_background_tasks()
```

### 生成带注释的默认模板

```bash
python main.py --init-config session.jsonc
```

## 5. workspace 产物位置

双根模式下的默认产物：

| 路径 | 说明 |
| --- | --- |
| config_root/workspace.json | workspace 级清单 |
| config_root/config/session_config.json | JSONC 默认配置快照（Legacy） |
| config_root/providers/provider_keys.json | provider 注册表 |
| config_root/roleplay/generated_agents.json | 已生成人格资产 |
| data_root/engine_state/ | 引擎运行态持久化（v0.28+） |
| data_root/sessions/<session_id>/session_state.db | 会话状态（Legacy） |
| data_root/memory/ | 用户/事件/自我记忆 |
| data_root/token/token_usage.db | token 计量 |
| data_root/skill_data/ | SKILL 数据存储 |

## 6. 最佳实践

1. v0.28+ 新用户直接使用 Emotional Engine 配置，不再关注 `generated_agent_key`、`orchestration` 等 legacy 字段。
2. 需要注释时直接使用 JSONC，不必更换扩展名。
3. `persona` 字段优先使用模板名；复杂人格通过 roleplay 资产 + `"generated"` 加载。
4. 使用独立的 config_root 和 data_root 时，修改热刷新的文件必须落在 config_root。

## 7. 故障排查

### 修改配置文件后没有立即生效

请检查：

1. 修改的是 config_root 下的文件，而不是 data_root
2. 文件内容仍是合法 JSON/JSONC
3. 监听的路径是否属于以下之一：
   - workspace.json
   - providers/provider_keys.json
   - roleplay/generated_agents.json

---

## 10. Legacy 配置（已归档）

> **Legacy 引擎 `AsyncRolePlayEngine` 已在 v0.28 归档。** 以下内容仅供维护旧实例时参考。

Legacy 配置使用 `SessionConfig` + `OrchestrationPolicy` 体系，核心字段包括 `generated_agent_key`、`orchestration.task_enabled`、`orchestration.task_models` 等。

完整说明请查阅 `docs/engine-legacy.md` 和历史迁移文档：

- `examples/session.json`
- `examples/session_multimodel.json`
- `docs/migration-v0.15.md`
- `docs/migration-v0.24.md`
