# v0.15.0 迁移指南

v0.15.0 移除了上一阶段保留的两类兼容入口：

1. `OrchestrationPolicy.self_memory_extract_interval_seconds`
2. `multimodal_parse` 独立辅助任务及其相关配置

## Breaking Changes

### 1. 自我记忆不再支持时间触发

旧写法：

```python
OrchestrationPolicy(
    enable_self_memory=True,
    self_memory_extract_interval_seconds=300,
)
```

新写法：

```python
OrchestrationPolicy(
    enable_self_memory=True,
    self_memory_extract_batch_size=3,
    self_memory_min_chars=400,
)
```

迁移规则：

- 删除 `self_memory_extract_interval_seconds`
- 使用 `self_memory_extract_batch_size` 控制“每 N 条 AI 回复触发一次”
- 如需让长回复更快沉淀记忆，额外设置 `self_memory_min_chars`

### 2. 不再支持 `multimodal_parse` 任务配置

以下字段中的 `multimodal_parse` 键应全部删除：

- `orchestration.task_enabled`
- `orchestration.task_models`
- `orchestration.task_budgets`
- `orchestration.task_temperatures`
- `orchestration.task_max_tokens`
- `orchestration.task_retries`

旧写法：

```json
{
  "orchestration": {
    "task_models": {
      "memory_extract": "gpt-4o-mini",
      "event_extract": "gpt-4o-mini",
      "multimodal_parse": "gpt-4o"
    }
  }
}
```

新写法：

```json
{
  "agent": {
    "model": "gpt-4o-mini",
    "metadata": {
      "multimodal_model": "gpt-4o"
    }
  },
  "orchestration": {
    "task_models": {
      "memory_extract": "gpt-4o-mini",
      "event_extract": "gpt-4o-mini"
    }
  }
}
```

迁移规则：

- 图片不再经过单独证据提取模型
- 主模型必须本身支持 vision，或通过 `Agent.metadata["multimodal_model"]` 配置升级模型
- `multimodal_inputs` 仍然保留在 `Message` 上，调用方式不变

## 行为变化

- 图片将直接作为主模型请求的一部分发送
- transcript 中不会再注入 `多模态解析证据[...]` system 消息
- 自我记忆提取会在主回复完成后按计数/字数立即触发，而不是后台定时轮询

## 升级检查清单

- 删除所有 `self_memory_extract_interval_seconds`
- 删除所有 `multimodal_parse` 任务配置
- 为支持图片的 agent 配置 `metadata.multimodal_model`
- 检查依赖截图/图片理解的测试，改为断言主模型请求内容而非辅助任务证据