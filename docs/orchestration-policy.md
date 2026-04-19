# OrchestrationPolicy 配置说明（Legacy 归档）

> **本文档仅适用于 Legacy `AsyncRolePlayEngine`。**
>
> v0.28+ 默认的 `EmotionalGroupChatEngine` 不使用 `OrchestrationPolicy`，
> 其配置方式见 [docs/configuration.md](configuration.md)。
>
> 新用户无需阅读本文档。

---

本文档描述 `SessionConfig.orchestration` 的实际字段与运行行为，以 `sirius_chat/config/models.py` 中 `OrchestrationPolicy` 为准。

## 总览

`OrchestrationPolicy` 负责控制 Legacy 引擎的辅助任务：

- 辅助任务的模型路由与开关（`memory_extract`、`event_extract`、`intent_analysis`、`memory_manager`）
- 各任务温度、最大输出、重试次数
- 多模态输入限流
- 提示词驱动消息分割
- `reply_mode="auto"` 下的参与决策参数
- AI 自身记忆系统
- 会话积压静默批处理

注意：没有 `orchestration.enabled` 字段。

## 模型路由模式

`OrchestrationPolicy` 支持两种互斥模式：

- 统一模型模式：设置 `unified_model`
- 按任务模式：设置 `task_models`

## 任务与默认值

默认启用任务：

- `memory_extract`: `true`
- `event_extract`: `true`
- `intent_analysis`: `true`
- `memory_manager`: `true`

## 完整示例

```json
{
  "orchestration": {
    "unified_model": "",
    "task_models": {
      "memory_extract": "gpt-4o-mini",
      "event_extract": "gpt-4o-mini",
      "intent_analysis": "gpt-4o-mini",
      "memory_manager": "gpt-4o-mini"
    },
    "task_enabled": {
      "memory_extract": true,
      "event_extract": true,
      "intent_analysis": true,
      "memory_manager": true
    }
  }
}
```

## Emotional Engine 的等效配置

在 Emotional Engine 中，模型路由通过 `task_model_overrides` 配置：

```json
{
  "emotional_engine": {
    "task_model_overrides": {
      "response_generate": { "model": "gpt-4o", "max_tokens": 512 },
      "cognition_analyze": { "model": "gpt-4o-mini", "max_tokens": 384 }
    }
  }
}
```
