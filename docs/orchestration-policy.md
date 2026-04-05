# 多模型协作编排策略（阶段二）

本文档定义 Sirius Chat 在“多模型、多能力、成本约束”场景下的最小可用策略，作为实现与回归测试的唯一依据。

## 运作模式（默认：多模型协同）

**Sirius Chat 现在默认采用多模型协同模式运作**。所有任务（记忆提取、事件提取、多模态解析）默认启用，引擎会按照任务路由策略自动调度不同的专用模型。

- **多模型协同模式**（默认）：引擎根据配置的 `task_models`，将不同任务分发给相应的专用模型，或使用 `unified_model` 统一处理。所有任务默认启用，可通过 `task_enabled` 字典按需禁用。
- **单模型模式**（可选）：如需全部由一个模型处理，可移除 `task_models`，改为设置 `unified_model`，所有任务将使用该统一模型。

## 目标

- 默认启用多模型协同，降低多模态/纯文本模型混用导致的配置复杂度。
- 支持主回复与用户信息维护的分任务路由。
- 提供可控的 token 预算，避免低价值任务抢占成本。

## 三层策略

1. 能力声明层（平台/模型能力）

- 由 provider 平台清单给出默认入口与说明。
- 模型能力先以“任务路由”方式显式声明，不做自动探测。

1. 任务路由层（Task Routing）

- 主回复任务：`chat_main`，默认使用 `SessionConfig.agent.model`。
- 记忆提取任务：`memory_extract`，可配置单独模型，默认启用。
- 事件提取任务：`event_extract`，用于抽取事件摘要/角色槽位/时间线索并增强事件命中，默认启用。
- 多模态解析任务：`multimodal_parse`，用于将图片/视频输入转换为文本证据，默认启用。
- 若未配置任务模型，则跳过该任务并保持回退逻辑。

1. 预算控制层（Budget Guardrail）

- 预算按任务维度配置（当前支持 `memory_extract`、`event_extract`、`multimodal_parse`）。
- 使用近似 token 估算（字符数 / 4，向上取整）。
- 当预计消耗超预算时，跳过该任务并回退启发式逻辑。

## 配置结构（SessionConfig）

新增 `orchestration` 字段（可选）：

```json
{
  "orchestration": {
    "task_enabled": {
      "memory_extract": true,
      "multimodal_parse": true,
      "event_extract": true
    },
    "task_models": {
      "memory_extract": "doubao-seed-2-0-lite-260215",
      "event_extract": "doubao-seed-2-0-lite-260215",
      "multimodal_parse": "doubao-seed-2-0-lite-260215"
    },
    "task_budgets": {
      "memory_extract": 1200,
      "event_extract": 1000,
      "multimodal_parse": 1000
    },
    "task_temperatures": {
      "memory_extract": 0.1,
      "event_extract": 0.1
    },
    "task_max_tokens": {
      "memory_extract": 128,
      "event_extract": 192
    },
    "task_retries": {
      "memory_extract": 1,
      "event_extract": 1,
      "multimodal_parse": 1
    },
    "max_multimodal_inputs_per_turn": 4,
    "max_multimodal_value_length": 4096
    }
  }
}
```

说明：

- `task_enabled.<task_name>` 为 `false` 时，不执行该任务。
- `task_models.<task_name>` 未设置时，不发起该任务调用。
- `task_budgets.<task_name>` 未设置或 <= 0 时，视为该任务不限制。
- `task_retries.<task_name>` 配置任务级重试次数（默认 0）。
- `max_multimodal_inputs_per_turn` 与 `max_multimodal_value_length` 用于输入限流与裁剪。

`Message` 可选字段：

```json
{
  "role": "user",
  "speaker": "小王",
  "content": "请结合图片分析",
  "multimodal_inputs": [
    {"type": "image", "value": "https://example.com/demo.png"}
  ]
}
```

## 执行顺序（单轮用户输入）

1. 写入用户消息到 transcript。
2. 先执行现有启发式更新（关键词/角色短语）。
3. 若编排启用且配置了 `memory_extract` 模型：

- 构造结构化提取请求。
- 检查预算是否允许。
- 调用辅助模型，解析 JSON 结果。
- 将提取结果合并进 `UserMemoryManager.apply_ai_runtime_update`。

1. 若编排启用且配置了 `event_extract` 模型：

- 构造结构化事件提取请求。
- 检查预算是否允许。
- 调用辅助模型，解析事件 JSON（summary/keywords/role_slots/entities/time_hints/emotion_tags）。
- 将模型提取结果与启发式特征融合后执行事件命中评分。

1. 若存在 `multimodal_inputs` 且配置了 `multimodal_parse` 模型：

- 构造多模态解析请求。
- 检查预算是否允许。
- 调用辅助模型，解析 `{"evidence": "..."}`。
- 将证据以 system 消息注入上下文。

1. 调用主模型生成最终回复。

## 可观测性（生产建议）

- 运行统计写入 `Transcript.orchestration_stats`，并随 session store 持久化。
- 常见指标：`attempted`、`succeeded`、`failed_provider`、`failed_parse`、`skipped_budget`、`skipped_invalid_input`。

## 失败与回退

- 辅助任务失败、超时、返回非 JSON、预算不足时：

- 不中断主流程。
- 保留启发式记忆结果。

## 持久化与优先级

- `main.py` 的 `session_config.persisted.json` 需完整保存 `orchestration`。
- 按现有策略：持久化配置优先于外部 `--config`。

## 非目标（当前阶段不做）

- 自动探测模型能力并动态重排任务。
- 复杂负载均衡与健康检查。
- 复杂多模态编解码与 OCR/ASR 管线（当前仅通过模型任务提取文本证据）。
