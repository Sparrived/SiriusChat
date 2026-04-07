# Sirius Chat 配置指南

## 概述

Sirius Chat 提供灵活的配置管理系统，支持：
- 多环境配置（dev、test、prod）
- 环境变量替换
- 配置继承和合并
- 秘钥管理

## 配置文件格式

### 基础配置结构

```json
{
  "work_path": "./sirius_data",
  "global_system_prompt": "你是一个有帮助的 AI 助手。",
  "agent": {
    "name": "MyAI",
    "persona": "友好、专业的 AI 助手",
    "model": "gpt-4-turbo",
    "temperature": 0.7,
    "max_tokens": 512,
    "metadata": {}
  },
  "history_max_messages": 24,
  "history_max_chars": 6000,
  "max_recent_participant_messages": 5,
  "enable_auto_compression": true,
  "orchestration": {
    "task_enabled": {
      "memory_extract": true,
      "multimodal_parse": true,
      "event_extract": true
    },
    "task_models": {},
    "task_budgets": {},
    "task_temperatures": {},
    "task_max_tokens": {},
    "task_retries": {},
    "max_multimodal_inputs_per_turn": 4,
    "max_multimodal_value_length": 4096,
    "enable_prompt_driven_splitting": true,
    "split_marker": "[MSG_BREAK]",
    "memory_manager_model": "",
    "memory_manager_temperature": 0.3,
    "memory_manager_max_tokens": 512
  }
}
```

## 配置选项详解

### work_path
- **类型**: 字符串
- **含义**: 数据存储目录，用于保存用户记忆、事件记忆等
- **示例**: `"./sirius_data"` 或 `"/var/sirius/data"`

### global_system_prompt
- **类型**: 字符串
- **含义**: 全局系统提示词，作为所有对话的基础指令
- **示例**: `"你是一个专业的技术顾问..."`

### agent
Agent 配置定义 AI 助手的身份和行为。

#### agent.name
- **类型**: 字符串
- **含义**: AI 助手的名称
- **示例**: `"Claude"`, `"SiriusAI"`

#### agent.persona
- **类型**: 字符串
- **含义**: AI 助手的角色设定和背景
- **示例**: `"我是一个 10 年经验的软件架构师..."`

#### agent.model
- **类型**: 字符串
- **含义**: 使用的 LLM 模型
- **示例**: `"gpt-4-turbo"`, `"doubao-seed-2-0-pro"`

#### agent.temperature
- **类型**: 浮点数 (0.0 - 2.0)
- **含义**: 模型的创意度
- **推荐值**:
  - `0.0 - 0.3`: 确定性回答（用于内存提取等）
  - `0.5 - 0.7`: 均衡（通常对话）
  - `0.8 - 1.0`: 创意回答

#### agent.max_tokens
- **类型**: 整数
- **含义**: 单次回答的最大 token 数
- **推荐值**: `256 - 2048`

#### agent.metadata
- **类型**: 对象
- **含义**: 额外的助手元数据
- **示例**: `{"alias": "小助手", "version": "1.0"}`

### history_max_messages
- **类型**: 整数
- **含义**: 保留的最大历史消息数
- **推荐值**: `20 - 50`

### history_max_chars
- **类型**: 整数
- **含义**: 历史消息的最大字符数
- **推荐值**: `4000 - 8000`

### enable_auto_compression
- **类型**: 布尔值
- **含义**: 是否自动压缩超长会话
- **推荐值**: `true（生产）`, `false（开发）`

### orchestration
多任务编排配置，这是一个高级功能。

#### orchestration.task_enabled
- **类型**: 对象（布尔值键值对）
- **含义**: 各个任务的启用状态，所有任务默认启用
- **示例**:
```json
{
  "memory_extract": true,
  "event_extract": true,
  "multimodal_parse": true
}
```

#### orchestration.task_models
- **类型**: 对象
- **含义**: 各个任务使用的模型
- **示例**:
```json
{
  "memory_extract": "gpt-4-mini",
  "event_extract": "gpt-4-mini",
  "multimodal_parse": "gpt-4-vision"
}
```

#### orchestration.task_budgets
- **类型**: 对象
- **含义**: 各个任务的 token 预算（防止成本过高）
- **示例**:
```json
{
  "memory_extract": 2000,
  "event_extract": 2000,
  "multimodal_parse": 3000
}
```

#### orchestration.task_temperatures
- **类型**: 对象
- **含义**: 各个任务的温度设置
- **示例**:
```json
{
  "memory_extract": 0.1,
  "event_extract": 0.1,
  "multimodal_parse": 0.5
}
```

#### orchestration.memory_manager_model
- **类型**: 字符串
- **含义**: 用于内存管理的模型（空字符串表示禁用）
- **示例**: `"gpt-4-mini"`

#### orchestration.memory
- **类型**: `MemoryPolicy` 对象
- **含义**: 记忆系统集中配置（V2 新增）

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `max_facts_per_user` | int | 50 | 每用户最大记忆条目数 |
| `transient_confidence_threshold` | float | 0.85 | RESIDENT/TRANSIENT 分界阈值 |
| `event_dedup_window_minutes` | int | 5 | 事件去重时间窗口（分钟） |
| `max_observed_set_size` | int | 100 | observed_* 集合最大元素数 |
| `max_summary_facts_per_type` | int | 5 | 摘要中每类型最多事实数 |
| `max_summary_total_chars` | int | 2000 | 摘要总字符上限 |
| `decay_schedule` | dict | `{7:0.95, 30:0.80, ...}` | 衰退时间表 |

**JSON 示例**:
```json
{
  "orchestration": {
    "memory": {
      "max_facts_per_user": 100,
      "transient_confidence_threshold": 0.7,
      "max_observed_set_size": 200
    }
  }
}
```

## 环境变量替换

支持在配置文件中使用环境变量，使用 `${VAR_NAME}` 语法：

```json
{
  "work_path": "${SIRIUS_DATA_PATH:/home/user/sirius}",
  "agent": {
    "model": "${SIRIUS_MODEL:gpt-4-turbo}",
    "metadata": {
      "api_key": "${OPENAI_API_KEY}"
    }
  }
}
```

加载时会自动替换：
- `${SIRIUS_DATA_PATH}` → 系统环境变量值
- `${SIRIUS_DATA_PATH:/default}` → 环境变量或默认值

## 多环境配置

### 开发环境 (dev.json)

```json
{
  "work_path": "./sirius_data_dev",
  "global_system_prompt": "你是一个有帮助的 AI 助手。（开发环境）",
  "agent": {
    "name": "SiriusAI-Dev",
    "model": "gpt-4-turbo",
    "temperature": 0.9,
    "max_tokens": 1024
  },
  "enable_auto_compression": false,
  "orchestration": {
    "task_enabled": {
      "memory_extract": true,
      "multimodal_parse": true,
      "event_extract": true
    },
    "task_models": {
      "memory_extract": "gpt-4-mini",
      "event_extract": "gpt-4-mini"
    }
  }
}
```

### 测试环境 (test.json)

```json
{
  "work_path": "./sirius_data_test",
  "agent": {
    "name": "SiriusAI-Test",
    "model": "mock-model",
    "temperature": 0.5,
    "max_tokens": 256
  },
  "orchestration": {
    "task_enabled": {
      "memory_extract": false,
      "multimodal_parse": false,
      "event_extract": false
    }
  }
}
```

### 生产环境 (prod.json)

```json
{
  "work_path": "${SIRIUS_DATA_PATH:/opt/sirius_data}",
  "agent": {
    "name": "SiriusAI",
    "model": "${SIRIUS_MODEL:gpt-4-turbo}",
    "metadata": {
      "api_key": "${SIRIUS_API_KEY}"
    }
  },
  "enable_auto_compression": true,
  "orchestration": {
    "task_enabled": {
      "memory_extract": true,
      "multimodal_parse": true,
      "event_extract": true
    },
    "memory_manager_model": "${MEMORY_MANAGER_MODEL:gpt-4-mini}"
  }
}
```

## 加载配置

### 通过环境名加载

```python
from sirius_chat.config import ConfigManager

manager = ConfigManager()

# 加载开发环境配置
config = manager.load_from_env("dev")

# 加载生产环境配置
config = manager.load_from_env("prod")
```

### 通过文件路径加载

```python
from pathlib import Path
from sirius_chat.config import ConfigManager

manager = ConfigManager()

# 加载自定义配置文件
config = manager.load_from_json("path/to/config.json")

# 相对路径也支持
config = manager.load_from_json("./configs/custom.json")
```

## 配置验证

配置加载时会自动验证：
- 必需字段的存在性
- 数据类型正确性
- 路径的可访问性

验证失败会抛出 `ValueError`：

```python
try:
    config = manager.load_from_json("incomplete_config.json")
except ValueError as e:
    print(f"配置验证失败: {e}")
```

## 最佳实践

### 1. 使用环境变量存储敏感信息

```json
{
  "agent": {
    "metadata": {
      "api_key": "${OPENAI_API_KEY}"
    }
  }
}
```

### 2. 使用多环境配置

```bash
# 开发
ENVIRONMENT=dev python main.py

# 测试
ENVIRONMENT=test python -m pytest

# 生产
ENVIRONMENT=prod python app.py
```

### 3. 使用配置合并

```python
# 加载基础配置
base_config = manager.load_from_json("base.json")

# 加载环境特定配置
env_config = manager.load_from_json(f"{env}.json")

# 合并配置
merged = manager.merge_configs(
    base_config.__dict__,
    env_config.__dict__
)
```

### 4. 验证关键配置

```python
config = manager.load_from_json("config.json")

# 验证必需的任务模型
assert config.orchestration.task_models.get("memory_extract"), \
    "must configure memory_extract model"

# 验证路径
from pathlib import Path
Path(config.work_path).mkdir(parents=True, exist_ok=True)
```

## 故障排查

### 环境变量未被替换

**症状**: 配置中出现 `${VAR_NAME}` 字符串

**解决**:
1. 确保环境变量已设置: `echo $VAR_NAME`
2. 重新加载配置文件
3. 检查变量名拼写

### 文件路径错误

**症状**: FileNotFoundError

**解决**:
1. 使用绝对路径或相对于脚本的路径
2. 确保目录存在: `mkdir -p sirius_data`
3. 检查文件权限

### 配置验证失败

**症状**: ValueError: "Missing required config keys"

**解决**:
1. 确保配置文件包含所有必需字段
2. 查看错误消息中缺失的字段
3. 参考基础配置模板
