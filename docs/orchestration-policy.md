# 模型编排与任务路由

> v1.0 中，`EmotionalGroupChatEngine` 通过 `task_model_overrides` 实现按任务模型路由，不再使用 `OrchestrationPolicy` dataclass。

## 总览

`EmotionalGroupChatEngine` 内置默认任务-模型映射：

| 任务类型 | 默认模型 | 用途 |
|---------|---------|------|
| `response_generate` | `chat_model` | 回复生成 |
| `proactive_generate` | `chat_model` | 主动发言生成 |
| `empathy_generate` | `chat_model` | 共情文本生成 |
| `cognition_analyze` | `analysis_model` | 统一情绪+意图分析 |
| `memory_extract` | `analysis_model` | 日记/记忆提取 |
| `emotion_analyze` | `analysis_model` | 情感分析（保留兼容） |
| `intent_analyze` | `analysis_model` | 意图分析（保留兼容） |
| `vision` | `vision_model` | 多模态视觉任务 |

默认模型从 `orchestration.json` 读取：

- `analysis_model` = `orchestration.json` 中的 `analysis_model`，默认 `gpt-4o-mini`
- `chat_model` = `orchestration.json` 中的 `chat_model`，默认 `gpt-4o`
- `vision_model` = `orchestration.json` 中的 `vision_model`，回退到 `chat_model`

## 通过配置覆盖

在会话配置的 `emotional_engine.task_model_overrides` 中覆盖：

```json
{
  "emotional_engine": {
    "task_model_overrides": {
      "response_generate": {
        "model": "gpt-4o",
        "max_tokens": 512,
        "temperature": 0.7
      },
      "cognition_analyze": {
        "model": "gpt-4o-mini",
        "max_tokens": 384,
        "temperature": 0.2
      },
      "vision": {
        "model": "gpt-4o"
      }
    }
  }
}
```

每个覆盖项可包含：

- `model`（必需）：模型名称
- `max_tokens`（可选）：该任务的最大输出 token
- `temperature`（可选）：该任务的采样温度

## 运行时动态选择

`ModelRouter` 在默认映射基础上，根据以下规则动态调整：

1. **紧急度升级**：`urgency >= 80` 时切换更强模型；`urgency >= 95` 时提升最大 token
2. **热度适配**：群聊 `hot` 时减少 30% token；`overheated` 时减半 token
3. **用户风格**：`concise` 用户限制 80 token；`detailed` 用户增加 20%

这些调整在 `task_model_overrides` 之后应用，因此最终参数 = 覆盖值 + 动态调整。

## 持久化

`orchestration.json` 位于 `{work_path}/engine_state/orchestration.json`，可通过 `OrchestrationStore` 读写：

```python
from sirius_chat.core.orchestration_store import OrchestrationStore

OrchestrationStore.save(work_path, {
    "analysis_model": "gpt-4o-mini",
    "chat_model": "gpt-4o",
    "vision_model": "gpt-4o",
})
```

引擎启动时自动加载此文件。若文件不存在，使用上述默认值。
