# v0.16.0 迁移指南

本版本将 `reply_mode=auto` / `smart` 下的 LLM 意图分析正式纳入统一任务编排体系。

## 变化摘要

- 新增一等任务：`intent_analysis`
- 推荐使用统一任务配置：
  - `task_enabled["intent_analysis"]`
  - `task_models["intent_analysis"]`
  - `task_budgets["intent_analysis"]`
  - `task_temperatures["intent_analysis"]`
  - `task_max_tokens["intent_analysis"]`
  - `task_retries["intent_analysis"]`
- `enable_intent_analysis` 与 `intent_analysis_model` 仍保留兼容读取，但建议尽快迁移；自 v0.26.6 起，新的模板与持久化文件不再写回这两个旧字段
- `main.py`、库内 CLI 与 `ConfigManager` 现在会正确读取上述 orchestration JSON 设置

## 迁移前

```python
OrchestrationPolicy(
    unified_model="gpt-4o-mini",
    enable_intent_analysis=True,
    intent_analysis_model="gpt-4o-mini",
    session_reply_mode="auto",
)
```

```json
{
  "orchestration": {
    "unified_model": "gpt-4o-mini",
    "enable_intent_analysis": true,
    "intent_analysis_model": "gpt-4o-mini",
    "session_reply_mode": "auto"
  }
}
```

## 迁移后

```python
OrchestrationPolicy(
    unified_model="",
    task_enabled={
  "memory_extract": True,
  "event_extract": True,
  "intent_analysis": True,
    },
    task_models={
        "memory_extract": "doubao-seed-2-0-lite-260215",
        "event_extract": "doubao-seed-2-0-lite-260215",
        "intent_analysis": "gpt-4o-mini",
    },
    task_budgets={"intent_analysis": 600},
    task_temperatures={"intent_analysis": 0.1},
    task_max_tokens={"intent_analysis": 192},
    task_retries={"intent_analysis": 1},
    session_reply_mode="auto",
)
```

```json
{
  "orchestration": {
    "task_enabled": {
      "memory_extract": true,
      "event_extract": true,
      "intent_analysis": true
    },
    "task_models": {
      "memory_extract": "doubao-seed-2-0-lite-260215",
      "event_extract": "doubao-seed-2-0-lite-260215",
      "intent_analysis": "gpt-4o-mini"
    },
    "task_budgets": {
      "intent_analysis": 600
    },
    "task_temperatures": {
      "intent_analysis": 0.1
    },
    "task_max_tokens": {
      "intent_analysis": 192
    },
    "task_retries": {
      "intent_analysis": 1
    },
    "session_reply_mode": "auto"
  }
}
```

## 运行时行为

- 当 `intent_analysis` 任务启用时，引擎会在参与决策前调用 LLM 进行 target / intent 分类
- 若 `task_models["intent_analysis"]` 未设置，则回退顺序为：`unified_model` → `agent.model`
- 若任务被关闭、预算超限、调用失败或响应解析异常，则自动回退到关键词意图分析，不会阻塞主流程

## 建议检查项

- 若你使用 `reply_mode=auto` 或 `session_reply_mode="auto"`，建议显式为 `intent_analysis` 指定模型
- 若你原来只在 JSON 中写了 `task_enabled` 或 `message_debounce_seconds` 等 orchestration 字段，升级后这些设置会真正生效，请重新检查行为是否符合预期
- 若你希望完全禁用 LLM 意图分析，请设置 `task_enabled["intent_analysis"] = false`

## 兼容性说明

- 旧配置里的 `enable_intent_analysis` 和 `intent_analysis_model` 仍可继续读取，并会在加载时自动映射到 `task_enabled["intent_analysis"]` / `task_models["intent_analysis"]`
- 自 v0.26.6 起，`workspace.json`、`config/session_config.json`、CLI 默认模板与 `main.py` 兼容镜像不再写回这两个旧字段
- 后续版本将优先围绕 `intent_analysis` 任务维护文档、示例与能力扩展