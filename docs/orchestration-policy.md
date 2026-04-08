# OrchestrationPolicy 配置说明

本文档描述 `SessionConfig.orchestration` 的实际字段与运行行为，以 `sirius_chat/config/models.py` 中 `OrchestrationPolicy` 为准。

## 总览

`OrchestrationPolicy` 负责控制：

- 辅助任务的模型路由与开关（`memory_extract`、`event_extract`、`multimodal_parse`）。
- 各任务预算、温度、最大输出、重试次数。
- 多模态输入限流。
- 提示词驱动消息分割（`split_marker`）。
- 记忆管理器任务（`memory_manager_model`）。
- `reply_mode="auto"` 下的回复意愿参数。

注意：没有 `orchestration.enabled` 字段。

## 模型路由模式

`OrchestrationPolicy` 支持两种互斥模式，必须二选一：

- 统一模型模式：设置 `unified_model`，所有辅助任务使用同一模型。
- 按任务模式：设置 `task_models`，每个任务单独指定模型。

校验规则：

- `unified_model` 和 `task_models` 不能同时为空。
- `unified_model` 和 `task_models` 不能同时有值。

## 任务与默认值

默认启用任务（通过 `task_enabled` 控制）：

- `memory_extract`: `true`
- `event_extract`: `true`
- `multimodal_parse`: `true`

其他关键默认值：

- `max_multimodal_inputs_per_turn`: `4`
- `max_multimodal_value_length`: `4096`
- `enable_prompt_driven_splitting`: `true`
- `split_marker`: `<MSG_SPLIT>`
- `memory_manager_model`: 空字符串（不启用）
- `memory_manager_temperature`: `0.3`
- `memory_manager_max_tokens`: `512`
- `memory_extract_batch_size`: `1`
- `memory_extract_min_content_length`: `0`
- `session_reply_mode`: `always`

## 完整示例

```json
{
  "orchestration": {
    "unified_model": "",
    "task_models": {
      "memory_extract": "doubao-seed-2-0-lite-260215",
      "event_extract": "doubao-seed-2-0-lite-260215",
      "multimodal_parse": "doubao-seed-2-0-lite-260215"
    },
    "task_enabled": {
      "memory_extract": true,
      "event_extract": true,
      "multimodal_parse": true
    },
    "task_budgets": {
      "memory_extract": 1200,
      "event_extract": 1000,
      "multimodal_parse": 1000,
      "memory_manager": 800
    },
    "task_temperatures": {
      "memory_extract": 0.1,
      "event_extract": 0.1,
      "multimodal_parse": 0.3
    },
    "task_max_tokens": {
      "memory_extract": 128,
      "event_extract": 192,
      "multimodal_parse": 256
    },
    "task_retries": {
      "memory_extract": 1,
      "event_extract": 1,
      "multimodal_parse": 1,
      "memory_manager": 1
    },
    "max_multimodal_inputs_per_turn": 4,
    "max_multimodal_value_length": 4096,
    "enable_prompt_driven_splitting": true,
    "split_marker": "<MSG_SPLIT>",
    "memory_manager_model": "gpt-4o-mini",
    "memory_manager_temperature": 0.3,
    "memory_manager_max_tokens": 512,
    "memory_extract_batch_size": 3,
    "memory_extract_min_content_length": 30,
    "session_reply_mode": "auto",
    "auto_reply_base_score": 0.22,
    "auto_reply_threshold": 0.58,
    "auto_reply_threshold_min": 0.4,
    "auto_reply_threshold_max": 0.72,
    "auto_reply_threshold_boost_start_count": 4,
    "auto_reply_probability_coefficient": 0.35,
    "auto_reply_probability_floor": 0.05,
    "auto_reply_user_cadence_seconds": 7.0,
    "auto_reply_group_window_seconds": 8.0,
    "auto_reply_group_penalty_start_count": 2,
    "auto_reply_assistant_cooldown_seconds": 12.0
  }
}
```

## 行为说明

任务调度：

- `task_enabled[task] = false` 时，任务直接跳过。
- 任务未配置模型时不会调用该任务模型。
- `task_budgets[task] <= 0` 或未设置，视为不限制预算。
- 预算判断采用近似 token 估算（字符数 / 4，向上取整）。

记忆提取频率控制：

- `memory_extract_batch_size > 1` 时，按消息批次触发记忆提取。
- `memory_extract_min_content_length > 0` 时，短内容会跳过记忆提取。

提示词驱动分割：

- `enable_prompt_driven_splitting = true` 时，系统提示会注入分割规则，告知模型当前为群聊场景。
- 分割规则要求：每条消息简短（通常 1-2 句，最多 3-4 句）；要表达多个独立内容时必须插入 `split_marker`；禁止用连续换行代替分割符。
- 模型输出中出现 `split_marker` 后，引擎按标记拆分为多条 assistant 消息。

记忆管理器：

- `memory_manager_model` 非空时启用记忆管理任务。
- 该任务可使用 `task_budgets["memory_manager"]` 与 `task_retries["memory_manager"]`。

## 回复意愿参数（reply_mode=auto）

会话级策略由 `session_reply_mode` 控制，可用值：

- `always`
- `never`
- `auto`
- `smart`（等价于 `auto`）
- `silent` / `none` / `no_reply`（等价于 `never`）

概率与阈值相关参数：

- `auto_reply_base_score`
- `auto_reply_threshold`
- `auto_reply_threshold_min`
- `auto_reply_threshold_max`
- `auto_reply_threshold_boost_start_count`
- `auto_reply_probability_coefficient`
- `auto_reply_probability_floor`
- `auto_reply_user_cadence_seconds`
- `auto_reply_group_window_seconds`
- `auto_reply_group_penalty_start_count`
- `auto_reply_assistant_cooldown_seconds`

## 校验约束

`OrchestrationPolicy.validate()` 会检查：

- 模型路由模式互斥与必填规则。
- `memory_extract_batch_size > 0`。
- `memory_extract_min_content_length >= 0`。
- 回复模式与阈值/概率/时间参数的取值范围合法。

若配置非法，会在 `SessionConfig` 初始化阶段抛出 `ValueError`。
