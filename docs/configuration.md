# Sirius Chat 配置指南

## 概述

从 v0.24.0 开始，Sirius Chat 建议把配置分成两层理解：

1. 轻量会话配置
   - 供 main.py --config、sirius-chat --config、ConfigManager.bootstrap_workspace_from_legacy_session_json(...) 使用
   - 支持 JSON 和 JSONC 注释
   - 只描述 generated_agent_key、providers、历史预算和 orchestration
2. 完整 SessionConfig
   - 供 Python API 高级接入使用
   - 包含 agent、global_system_prompt、work_path、data_path 等完整字段

推荐做法是：

- 用户可编辑配置文件使用轻量会话配置
- agent 和 global_system_prompt 由 roleplay/generated_agents.json 中已经保存的人格资产提供
- 若确实需要手写完整 SessionConfig，使用 Python API 直接加载或构造

## 1. 推荐的会话 JSON/JSONC 配置

适用入口：

- python main.py --config ...
- sirius-chat --config ...
- ConfigManager.bootstrap_workspace_from_legacy_session_json(...)

示例：

```json
{
  "generated_agent_key": "main_agent",
  "providers": [
    {
      "type": "openai-compatible",
      "base_url": "https://api.openai.com",
      "api_key": "${OPENAI_API_KEY}",
      "healthcheck_model": "gpt-4o-mini"
    }
  ],
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
    "enable_prompt_driven_splitting": true,
    "split_marker": "<MSG_SPLIT>",
    "session_reply_mode": "auto"
  }
}
```

说明：

- 文件可以直接写成 JSONC，允许 // 注释
- generated_agent_key 必须指向配置根 roleplay/generated_agents.json 中的已存在资产
- providers 是推荐字段；旧版单个 provider 仅保留兼容
- --init-config <path> 会生成带注释模板
- workspace 自动写出的 config/session_config.json 也会使用 JSONC 注释模板

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

## 3. 核心字段说明

### generated_agent_key

- 类型：字符串
- 含义：当前选择的人格资产 key
- 来源：roleplay/generated_agents.json

### providers

- 类型：列表
- 含义：provider 引导配置
- 推荐字段：type、api_key、base_url、healthcheck_model、models

示例：

```json
[
  {
    "type": "openai-compatible",
    "base_url": "https://api.openai.com",
    "api_key": "${OPENAI_API_KEY}",
    "healthcheck_model": "gpt-4o-mini"
  },
  {
    "type": "siliconflow",
    "api_key": "${SILICONFLOW_API_KEY}",
    "healthcheck_model": "Pro/glm-4.5",
    "models": ["Pro/glm-4.5"]
  }
]
```

### work_path / data_path

- 仅在完整 SessionConfig 中使用
- work_path：配置根，放 workspace.json、config/、providers/、roleplay/、skills/
- data_path：运行根，放 sessions/、memory/、token/、skill_data/、primary_user.json

### orchestration

orchestration 负责控制辅助任务、回复节奏、记忆频率和提示词驱动分割。

常用字段：

| 字段 | 含义 |
| --- | --- |
| task_enabled | 各辅助任务是否启用 |
| task_models | 任务模型映射 |
| task_budgets | 各任务 token 预算 |
| task_temperatures | 各任务温度 |
| task_max_tokens | 各任务最大输出 |
| task_retries | 各任务重试次数 |
| memory_extract_batch_size | 每 N 条消息做一次记忆提取 |
| memory_extract_min_content_length | 只处理达到最小长度的消息 |
| enable_prompt_driven_splitting | 是否启用提示词驱动分割 |
| split_marker | 分割标记 |
| session_reply_mode | always / never / auto |

注意：

- intent_analysis 已是正式一等任务，建议显式配置
- multimodal_parse 已在 v0.15.0 移除，不应再出现在配置中
- 图片能力应通过 agent 资产中的 metadata.multimodal_model 配置，而不是辅助任务

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

## 5. 多环境配置

如果你在 Python 服务内直接使用完整 SessionConfig 文件，可以继续用 ConfigManager.load_from_env(...) 读取 dev/test/prod 预设。

示例：

```python
from sirius_chat.config import ConfigManager

manager = ConfigManager()
dev_config = manager.load_from_env("dev")
test_config = manager.load_from_env("test")
prod_config = manager.load_from_env("prod")
```

适用建议：

- dev：偏向高可观察性，通常关闭压缩
- test：推荐配合 mock provider 和最小预算
- prod：优先使用环境变量注入密钥和路径

## 6. 常见加载方式

### 加载完整 SessionConfig 文件

```python
from sirius_chat.config import ConfigManager

manager = ConfigManager()
config = manager.load_from_json("full-session.jsonc")
```

### 把轻量配置 bootstrap 到 workspace

```python
from pathlib import Path
from sirius_chat.config import ConfigManager

manager = ConfigManager(base_path=Path("./config_root"))
workspace_config, providers = manager.bootstrap_workspace_from_legacy_session_json(
    "session.jsonc",
    work_path=Path("./config_root"),
    data_path=Path("./data_root"),
)
```

### 生成带注释的默认模板

```bash
python main.py --init-config session.jsonc
```

## 7. workspace 产物位置

双根模式下的默认产物：

| 路径 | 说明 |
| --- | --- |
| config_root/workspace.json | workspace 级清单 |
| config_root/config/session_config.json | JSONC 默认配置快照 |
| config_root/providers/provider_keys.json | provider 注册表 |
| config_root/roleplay/generated_agents.json | 已生成人格资产 |
| data_root/sessions/<session_id>/session_state.db | 会话状态 |
| data_root/sessions/<session_id>/participants.json | 参与者元数据 |
| data_root/memory/ | 用户/事件/自我记忆 |
| data_root/token/token_usage.db | token 计量 |
| data_root/skill_data/ | SKILL 数据存储 |

## 8. 最佳实践

1. 对外暴露给运营或脚本编辑的配置文件，统一用轻量 JSON/JSONC 结构。
2. 需要注释时直接使用 JSONC，不必更换扩展名。
3. 不要把 agent 和 global_system_prompt 写回用户入口配置；它们应由人格资产维护。
4. 不要再使用 multimodal_parse 任务配置。
5. 使用独立的 config_root 和 data_root 时，修改热刷新的文件必须落在 config_root。

## 9. 故障排查

### 轻量配置加载时报“必需提供 generated_agent_key”

说明当前配置文件不符合 workspace 推荐入口格式。请改为：

- 提供 generated_agent_key
- 去掉顶层 agent / global_system_prompt
- 确认 roleplay/generated_agents.json 中存在对应资产

### 修改配置文件后没有立即生效

请检查：

1. 修改的是 config_root 下的文件，而不是 data_root
2. 文件内容仍是合法 JSON/JSONC
3. 监听的路径是否属于以下之一：
   - workspace.json
   - config/session_config.json
   - providers/provider_keys.json
   - roleplay/generated_agents.json

### 示例配置里还有旧字段怎么办

请优先对照：

- examples/session.json
- examples/session_multimodel.json
- examples/session_prompt_splitting.json
- docs/migration-v0.15.md
- docs/migration-v0.24.md
