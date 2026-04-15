# v0.27.8 迁移说明

本次版本不引入新的配置项，主要修正 SKILL 事件流把内部技能结果暴露给外部订阅者的问题。

## 变更摘要

- `SKILL_COMPLETED` 事件不再携带 `result_preview`，只保留 `skill_name` 和 `success`。
- 技能执行结果仍会作为内部 system 上下文参与下一轮生成，但只有转成 assistant 回复后才会进入外部消息流。
- `on_reply` 与外部事件订阅继续支持接收 assistant 的普通中间文本和最终回复，但不再暴露内部 `SKILL执行结果` 文本。

## 对外影响

- 如果你的外部接入之前直接读取 `event.data["result_preview"]`，升级后需要改为只读取 `skill_name` / `success`，并从 assistant 的 `MESSAGE_ADDED` 或 `on_reply` 获取真正面向用户的回复内容。
- 无需修改现有配置文件。
- 对依赖 SKILL 状态做 UI 展示的外部系统，这个版本会让“技能完成”和“技能结果正文”两类信息的边界更清晰。

## 验证建议

升级后，建议至少覆盖以下场景：

1. 正常 SKILL 成功执行时，确认 `SKILL_COMPLETED` 事件只包含 `skill_name` 和 `success`。
2. 调用不存在的 SKILL 时，确认 `SKILL_COMPLETED` 仍不暴露错误预览或内部结果文本。
3. 外部若使用 `on_reply` 或订阅 assistant `MESSAGE_ADDED` 做投递，确认仍能收到普通 assistant 文本，但不会收到内部 `SKILL执行结果` 系统内容。