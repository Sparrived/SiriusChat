# Sirius Chat 配置指南

## 概述

Sirius Chat v1.0 使用 **EmotionalGroupChatEngine** 作为唯一引擎，支持**多人格架构**。配置分为两层：

1. **全局配置**（`data/global_config.json`）：WebUI 参数、NapCat 管理、日志级别
2. **人格级配置**（`data/personas/{name}/`）：人格定义、模型编排、平台适配器、体验参数

## 1. 全局配置

路径：`data/global_config.json`

```json
{
  "webui_host": "0.0.0.0",
  "webui_port": 8080,
  "auto_manage_napcat": true,
  "napcat_install_dir": "D:\\Code\\sirius_chat\\napcat",
  "napcat_base_port": 3001,
  "log_level": "INFO",
  "setup_completed": false,
  "setup_wizard_running": false
}
```

| 字段 | 类型 | 默认值 | 含义 |
|------|------|--------|------|
| `webui_host` | string | `"0.0.0.0"` | WebUI 监听地址 |
| `webui_port` | int | `8080` | WebUI 监听端口 |
| `auto_manage_napcat` | bool | `false` | 是否自动管理 NapCat 安装/启动 |
| `napcat_install_dir` | string | `"napcat"` | NapCat 全局安装目录 |
| `napcat_base_port` | int | `3001` | NapCat WebSocket 起始端口 |
| `log_level` | string | `"INFO"` | 日志级别 |
| `setup_completed` | bool | `false` | 首次配置向导是否完成 |
| `setup_wizard_running` | bool | `false` | 配置向导是否正在运行 |

## 2. 人格级配置

路径：`data/personas/{name}/`

### 2.1 人格定义（`persona.json`）

```json
{
  "name": "月白",
  "aliases": ["Sirius"],
  "persona_summary": "一位由AI猫娘构成的温暖群友...",
  "personality_traits": ["温暖治愈", "聪慧灵动"],
  "communication_style": "发言节奏适中...",
  "catchphrases": ["喵~", "大家要好好相处呀喵"],
  "emoji_preference": "heavy",
  "humor_style": "wholesome",
  "emotional_baseline": { "valence": 0.6, "arousal": 0.4 },
  "empathy_style": "warm",
  "boundaries": ["拒绝嘲讽亲友和家人"],
  "taboo_topics": ["辱骂家人", "恶意攻击"],
  "social_role": "caregiver"
}
```

### 2.2 模型编排（`orchestration.json`）

```json
{
  "analysis_model": "qwen3.5-flash",
  "chat_model": "qwen3.5-plus",
  "vision_model": "qwen3.5-plus"
}
```

模型只能从已配置的 Provider 的 `models` 列表中选择。

### 2.3 平台适配器（`adapters.json`）

```json
{
  "adapters": [
    {
      "type": "napcat",
      "enabled": true,
      "ws_url": "ws://localhost:3001",
      "token": "napcat_ws",
      "qq_number": "123456789",
      "allowed_group_ids": ["728196560"],
      "allowed_private_user_ids": [],
      "enable_group_chat": true,
      "enable_private_chat": true
    }
  ]
}
```

### 2.4 体验参数（`experience.json`）

```json
{
  "reply_mode": "auto",
  "engagement_sensitivity": 0.5,
  "heat_window_seconds": 60.0,
  "proactive_enabled": true,
  "proactive_interval_seconds": 300.0,
  "delay_reply_enabled": true,
  "pending_message_threshold": 4.0,
  "min_reply_interval_seconds": 0.0,
  "reply_frequency_window_seconds": 60.0,
  "reply_frequency_max_replies": 8,
  "reply_frequency_exempt_on_mention": true,
  "max_concurrent_llm_calls": 1,
  "enable_skills": true,
  "max_skill_rounds": 3,
  "skill_execution_timeout": 30.0,
  "auto_install_skill_deps": true,
  "memory_depth": "deep"
}
```

## 2. 配置字段说明

### providers

通用 provider 配置。支持 `type`、`api_key`、`base_url`、`healthcheck_model`、`models`。

### persona

- 类型：字符串
- 含义：人格模板名称或 `"generated"`
- 可选值：`warm_friend`、`sarcastic_techie`、`gentle_caregiver`、`chaotic_jester`、`stoic_observer`、`protective_elder`、`generated`
- CLI 可用 `--persona` 覆盖此字段

### emotional_engine

#### 记忆系统

| 字段 | 类型 | 默认值 | 含义 |
|------|------|--------|------|
| `basic_memory_hard_limit` | int | 30 | 基础记忆窗口硬上限（条数），超过后旧消息进入归档 |
| `basic_memory_context_window` | int | 5 | 构建 LLM 上下文时保留的最近消息条数 |
| `diary_top_k` | int | 5 | 回复生成时检索的日记条目数量 |
| `diary_token_budget` | int | 800 | 注入系统提示词的日记内容 token 预算（约 1200 字符） |

#### 行为控制

| 字段 | 类型 | 默认值 | 含义 |
|------|------|--------|------|
| `sensitivity` | float | 0.5 | 回复敏感度（0.0~1.0），越高越容易回复 |
| `reply_cooldown_seconds` | int | 12 | 同群连续回复的最小冷却间隔 |
| `max_skill_rounds` | int | 3 | 单次消息处理中最大 SKILL 调用轮数 |

#### 后台任务

| 字段 | 类型 | 默认值 | 含义 |
|------|------|--------|------|
| `delayed_queue_tick_interval_seconds` | int | 10 | 延迟回复队列扫描间隔 |
| `proactive_check_interval_seconds` | int | 60 | 主动发言触发器检查间隔 |
| `proactive_silence_minutes` | int | 60 | 群聊沉默多久后可能触发主动发言 |
| `proactive_active_start_hour` | int | 12 | 主动发言允许的开始小时（24 小时制） |
| `proactive_active_end_hour` | int | 21 | 主动发言允许的结束小时（24 小时制） |
| `memory_promote_interval_seconds` | int | 300 | 日记生成器检查间隔：冷群基础记忆归档 → 日记 |

#### 模型覆盖

| 字段 | 类型 | 默认值 | 含义 |
|------|------|--------|------|
| `task_model_overrides` | dict | {} | 按任务覆盖模型参数，见下表 |

#### task_model_overrides

按认知任务指定 model、max_tokens、temperature：

| 任务名 | 默认模型 | 说明 |
|--------|---------|------|
| `response_generate` | gpt-4o | 回复生成 |
| `cognition_analyze` | gpt-4o-mini | 统一情绪+意图分析 |
| `memory_extract` | gpt-4o-mini | 记忆提取（日记生成时） |
| `emotion_analyze` | gpt-4o-mini | 情感分析 |
| `intent_analyze` | gpt-4o-mini | 意图分析 |
| `proactive_generate` | gpt-4o | 主动发言生成 |
| `vision` | gpt-4o | 多模态视觉任务 |

示例：

```json
"task_model_overrides": {
  "response_generate": { "model": "gpt-4o", "max_tokens": 512, "temperature": 0.7 },
  "cognition_analyze": { "model": "gpt-4o-mini", "max_tokens": 384, "temperature": 0.2 }
}
```

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
- 轻量会话配置在 bootstrap 时同样支持这套替换逻辑

## 4. 常见加载方式

### Emotional Engine

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
| config_root/providers/provider_keys.json | provider 注册表 |
| config_root/roleplay/generated_agents.json | 已生成人格资产 |
| data_root/engine_state/ | 引擎运行态持久化 |
| data_root/memory/basic/ | 基础记忆归档存储 |
| data_root/memory/diary/ | 日记条目与索引 |
| data_root/memory/glossary/ | AI 名词解释库 |
| data_root/token/token_usage.db | token 计量 |
| data_root/skill_data/ | SKILL 数据存储 |

## 6. 最佳实践

1. 直接使用 Emotional Engine 配置，无需关注 `orchestration` 等旧字段。
2. 需要注释时直接使用 JSONC，不必更换扩展名。
3. `persona` 字段优先使用模板名；复杂人格通过 roleplay 资产 + `"generated"` 加载。
4. 使用独立的 config_root 和 data_root 时，修改热刷新的文件必须落在 config_root。
5. 日记检索质量取决于 sentence-transformers（`pip install sentence-transformers` 可选安装）。未安装时自动回退到纯关键词匹配。

## 7. 故障排查

### 修改配置文件后没有立即生效

请检查：

1. 修改的是 config_root 下的文件，而不是 data_root
2. 文件内容仍是合法 JSON/JSONC
3. 监听的路径是否属于以下之一：
   - workspace.json
   - providers/provider_keys.json
   - roleplay/generated_agents.json
