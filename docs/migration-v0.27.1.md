# v0.27.1 迁移指南

本文档说明 v0.27.1 对 orchestration 配置面的进一步收缩，以及运行时行为的默认化调整。

## 变更摘要

- 删除 `task_budgets`，框架不再根据预算近似值跳过辅助任务。
- `split_marker` 改为内置 `<MSG_SPLIT>`，不再对外配置。
- `skill_call_marker` 改为内置 `[SKILL_CALL:`，不再对外配置。
- `memory_manager_model` / `memory_manager_temperature` / `memory_manager_max_tokens` 改为 `memory_manager` 任务配置。
- 后台记忆归纳静默常驻，不再提供 `consolidation_enabled` 开关。
- SKILL 改为启动时加载、文件变更时重载，不再在每条消息路径上扫描目录。

## 1. 删除 task_budgets

### 旧写法

```json
{
  "orchestration": {
    "task_models": {
      "memory_extract": "gpt-4o-mini"
    },
    "task_budgets": {
      "memory_extract": 1200
    }
  }
}
```

### 新写法

```json
{
  "orchestration": {
    "task_models": {
      "memory_extract": "gpt-4o-mini"
    },
    "task_max_tokens": {
      "memory_extract": 128
    },
    "task_retries": {
      "memory_extract": 1
    }
  }
}
```

迁移说明：

- 直接删除 `task_budgets` 即可。
- 若之前依赖预算限制成本，请改为：
  - 使用更便宜的模型；
  - 降低 `task_max_tokens`；
  - 关闭不需要的任务；
  - 调整 `memory_extract_batch_size` 等频率参数。

## 2. marker 改为内置

### 旧写法

```json
{
  "orchestration": {
    "enable_prompt_driven_splitting": true,
    "split_marker": "<MSG_SPLIT>",
    "skill_call_marker": "[SKILL_CALL:"
  }
}
```

### 新写法

```json
{
  "orchestration": {
    "enable_prompt_driven_splitting": true
  }
}
```

迁移说明：

- 删除 `split_marker` 与 `skill_call_marker`。
- 分割标记固定为 `<MSG_SPLIT>`。
- SKILL 调用标记固定为 `[SKILL_CALL:`。

## 3. memory_manager 改为标准任务

### 旧写法

```json
{
  "orchestration": {
    "memory_manager_model": "gpt-4o-mini",
    "memory_manager_temperature": 0.3,
    "memory_manager_max_tokens": 512
  }
}
```

### 新写法

```json
{
  "orchestration": {
    "task_enabled": {
      "memory_manager": true
    },
    "task_models": {
      "memory_manager": "gpt-4o-mini"
    },
    "task_temperatures": {
      "memory_manager": 0.3
    },
    "task_max_tokens": {
      "memory_manager": 512
    },
    "task_retries": {
      "memory_manager": 1
    }
  }
}
```

迁移说明：

- 新版本加载旧字段时仍会自动映射。
- 但新的模板、导出与持久化镜像不再写出旧字段。

## 4. 后台归纳默认静默常驻

### 旧写法

```json
{
  "orchestration": {
    "consolidation_enabled": false,
    "consolidation_interval_seconds": 3600
  }
}
```

### 新写法

```json
{
  "orchestration": {
    "task_enabled": {
      "memory_manager": false
    },
    "consolidation_interval_seconds": 3600
  }
}
```

迁移说明：

- 删除 `consolidation_enabled`。
- 若不希望后台归纳继续调用 LLM，关闭 `task_enabled["memory_manager"]`。
- 归纳循环本身仍会静默启动，但会在任务关闭时快速返回。

## 5. SKILL 加载时机变化

- 启动 runtime 时会预加载 `skills/`。
- `skills/*.py` 与 `skills/README.md` 变化时会触发全量 reload。
- 删除的 SKILL 也会从 registry 中移除。
- 外部调用方不再需要通过“发一条消息”来触发 SKILL 刷新。

## 6. 推荐升级步骤

1. 删除所有 `task_budgets`、`split_marker`、`skill_call_marker`、`consolidation_enabled`。
2. 将 `memory_manager_*` 迁移到 `memory_manager` 任务配置。
3. 检查是否仍希望后台继续使用 `memory_manager` 模型；如不需要，关闭 `task_enabled["memory_manager"]`。
4. 若外部系统依赖 skill 动态发现，改为在启动后或写文件后等待 watcher 刷新，而不是依赖消息路径惰性加载。

## 7. 兼容性结论

- 旧配置大多仍可读取，但会被规范化到新结构。
- 新版本的模板、文档与持久化输出全部以 `task_*` 配置和内置 marker 为准。