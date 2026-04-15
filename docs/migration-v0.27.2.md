# v0.27.2 迁移指南

本文档说明 v0.27.2 新增的最小回复间隔配置，以及它与现有自动回复和静默批处理机制的配合关系。

## 变更摘要

- 新增 `min_reply_interval_seconds`。
- 该参数用于限制两次 AI 实际回复之间的最小时间间隔。
- 等待期间收到的新消息不会丢失，而是继续保留在 `WorkspaceRuntime` 的会话队列中。
- 冷却窗口结束后，runtime 会先合并同一说话人的连续消息，再进入下一次正常回复判断。
- 该参数不会强制 AI 一定回复；合并后的消息仍然受 `reply_mode=auto`、`intent_analysis` 与 engagement 决策影响。

## 1. 新配置项

### 新写法

```json
{
  "orchestration": {
    "session_reply_mode": "auto",
    "pending_message_threshold": 4,
    "min_reply_interval_seconds": 15.0
  }
}
```

迁移说明：

- 默认值为 `0.0`，表示关闭该能力。
- 设为大于 `0` 的值后，AI 回复过一次后，下一次进入回复判断前至少要等待这么多秒。

## 2. 与 pending_message_threshold 的区别

### `pending_message_threshold`

- 关注“队列里积压了多少条消息”。
- 只有当待处理消息数超过阈值时，才触发静默批处理合并。

### `min_reply_interval_seconds`

- 关注“距离上一次 AI 实际回复过了多久”。
- 在冷却窗口内，即使当前队列长度还没有超过 `pending_message_threshold`，runtime 也会继续等待并蓄积消息。
- 冷却窗口结束后，会先做一次同一说话人连续消息的合并，再进入下一次回复判断。

## 3. 与 reply_mode=auto 的关系

### 旧误解

- 容易把“最小回复间隔”理解成“窗口一到就一定回复”。

### 新语义

- `min_reply_interval_seconds` 只负责推迟“下一次是否回复”的判断时机。
- 冷却结束后，仍然会走：
  - `HeatAnalyzer`
  - `intent_analysis`（若启用）
  - `EngagementCoordinator`
- 因此即使消息已经合并，最终也可能不回复。

## 4. 推荐配置方式

### 适合高频聊天群

```json
{
  "orchestration": {
    "session_reply_mode": "auto",
    "pending_message_threshold": 4,
    "min_reply_interval_seconds": 10.0
  }
}
```

适用效果：

- 降低 AI 在高频短消息场景下的抢答感。
- 让模型在下一次判断时看到更完整的连续消息上下文。

### 适合严格逐条响应场景

```json
{
  "orchestration": {
    "session_reply_mode": "always",
    "pending_message_threshold": 0,
    "min_reply_interval_seconds": 0.0
  }
}
```

适用效果：

- 保持每条消息独立进入主流程。
- 不引入额外等待。

## 5. 兼容性结论

- 这是一个纯新增参数，旧配置无需修改即可继续运行。
- 只有在你显式设置 `min_reply_interval_seconds > 0` 时，runtime 才会启用“等待期间继续收消息、冷却结束后先合并再判断”的新行为。