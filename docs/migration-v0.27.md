# v0.27 迁移指南

本文档面向外部宿主、插件和直接调用 Sirius Chat 的项目，说明 v0.27 的破坏性变更与迁移步骤。

## 变更摘要

- 回复窗口策略从时间型 `message_debounce_seconds` 切换为积压计数型 `pending_message_threshold`。
- `WorkspaceRuntime.run_live_message(...)` 现在先按 session 入队，再由单会话 processor 逐条处理或静默批处理。
- `intent_analysis` 启用后，意图结论必须来自模型；预算不足、provider 调用失败或解析失败时，不再回退到关键词意图推断。
- 人格生成器会默认生成更偏短句、纯文本、少 markdown 的角色行为约束。

## 受影响场景

以下场景需要重点检查：

- 你在配置中显式使用了 `message_debounce_seconds`。
- 你依赖旧的时间窗口合并语义，例如“连续 5 秒内的同人消息自动合并”。
- 你依赖 `intent_analysis` 在预算不足或模型失败时自动降级为关键词推断。
- 你依赖旧人格生成器产出偏长、偏 markdown 的角色回复风格。

## 1. 配置键迁移

### 旧字段

```json
{
  "orchestration": {
    "message_debounce_seconds": 5.0
  }
}
```

### 新字段

```json
{
  "orchestration": {
    "pending_message_threshold": 4
  }
}
```

迁移说明：

- 新字段语义不再表示“等待多少秒”，而是“当前 session 待处理消息积压超过多少条时进入静默批处理”。
- 推荐值通常为 `3-5`。
- 设为 `0` 表示关闭该批处理，保持每条消息独立进入主流程。
- 旧字段在加载时仍会被兼容读取，并按四舍五入映射到 `pending_message_threshold`，但新的模板、持久化快照和导出结果都只会写新字段。

## 2. Runtime 行为变化

### 旧行为

- `AsyncRolePlayEngine` 内部基于时间窗口缓存同一用户的连续消息。
- 是否合并取决于 sleep 窗口是否到期。

### 新行为

- `WorkspaceRuntime.run_live_message(...)` 会先把消息追加到 session 队列。
- 单会话 processor 在锁内读取队列，并根据 `pending_message_threshold` 决定：
  - 逐条处理；或
  - 对同一说话人的连续消息做一次静默批处理。
- 一次批处理只会触发一次主模型调用，并把同一批次结果返回给该批次内所有等待中的调用方。

这意味着：

- 新策略面向“积压”而不是“时间窗口”。
- 若你的外部宿主会高并发地向同一 session 推消息，建议明确设置 `pending_message_threshold`，而不要继续以秒数思维调参。

## 3. intent_analysis 语义变化

### 旧行为

- `task_enabled["intent_analysis"] = true` 时优先走模型。
- 若预算不足、provider 失败或解析失败，仍可能回退到关键词意图推断，因此日志中可能出现“有意图分析结果但没有实际意图模型调用”。

### 新行为

- `task_enabled["intent_analysis"] = false`：仍使用关键词回退路径。
- `task_enabled["intent_analysis"] = true`：该轮意图结论必须来自模型。
- 若预算不足、provider 失败或解析失败：
  - 本轮不会生成关键词兜底意图；
  - 自动回复决策仅继续依赖热度与 engagement 信号；
  - 因此“模型没调用但仍有意图结论”的旧现象不会再发生。

如果你依赖旧的兜底行为，请在升级后评估：

- 是否要提高 `task_budgets["intent_analysis"]`
- 是否要为 `intent_analysis` 显式指定更稳定的模型
- 是否要把 `session_reply_mode` 调整为 `always` 或提高 `engagement_sensitivity`

## 4. Python API 迁移示例

### 旧写法

```python
orchestration = OrchestrationPolicy(
    unified_model="gpt-4o-mini",
    message_debounce_seconds=0.0,
)
```

### 新写法

```python
orchestration = OrchestrationPolicy(
    unified_model="gpt-4o-mini",
    pending_message_threshold=0,
)
```

若你使用 `WorkspaceRuntime`，建议显式检查：

```python
runtime = open_workspace_runtime(work_path, config_path=config_path)

# 每条消息独立处理
await runtime.apply_workspace_updates({
    "orchestration_defaults": {"pending_message_threshold": 0}
})
```

## 5. JSON / JSONC 配置迁移示例

### 旧写法

```json
{
  "orchestration": {
    "session_reply_mode": "auto",
    "message_debounce_seconds": 5.0
  }
}
```

### 新写法

```json
{
  "orchestration": {
    "session_reply_mode": "auto",
    "pending_message_threshold": 4
  }
}
```

## 6. 人格生成器变化

v0.27 起，角色生成器会更明确地要求生成的 `global_system_prompt`：

- 默认偏向短回复、轻量解释
- 优先纯文本交流
- 不主动使用 markdown 标题、列表、表格和代码块
- 除非用户明确要求或任务天然需要结构化输出

如果你依赖旧人格资产的长段落风格，不会自动重写已有资产；只有重新生成或更新人格时才会应用新倾向。

## 7. 推荐升级步骤

1. 把所有对外配置中的 `message_debounce_seconds` 替换为 `pending_message_threshold`。
2. 若外部宿主使用 `WorkspaceRuntime`，根据吞吐场景重新评估该阈值；多数群聊建议从 `4` 开始。
3. 检查所有 `reply_mode=auto` / `session_reply_mode="auto"` 场景，确认不再依赖意图失败时的关键词兜底。
4. 若需要更稳定的自动参与判断，为 `intent_analysis` 显式指定模型并补足预算。
5. 若会重新生成人格，确认外部产品接受“更短、更口语、少 markdown”的新默认风格。

## 8. 兼容性结论

- 旧配置文件通常仍可读取，但会被规范化为新字段。
- 新版本的持久化输出、模板和文档全部以 `pending_message_threshold` 为准。
- 旧的时间型 debounce 运行语义已移除，不应继续按秒级窗口理解系统行为。