# v0.27.7 迁移说明

本次版本不引入新的配置项，主要修正多 AI 群聊中“近期发言人别称不足”和“群控命令误触发回复”的问题。

## 变更摘要

- intent prompt 现在会附带近期人类发言者的 aliases。
- intent prompt 现在会附带 `environment_context` 的环境摘要，例如群名或群描述。
- 对“关闭本群AI”“禁用机器人”“别让 bot 说话”这类群控/停用命令，如果没有明确点名当前模型自身，会在 engagement 前被硬抑制，不触发当前模型回复。

## 对外影响

- 无需修改现有配置文件。
- 如果你的外部接入本来就传入了 `environment_context`，这个版本会把其中的群名/环境摘要用于意图分析，提升多 AI 目标识别稳定性。
- 若用户在群里发的是通用 bot/AI 控制命令，而不是明确叫当前模型做事，当前模型在 `reply_mode=auto` 下会更克制。

## 验证建议

升级后，建议至少覆盖以下场景：

1. 近期发言人已有 aliases 时，确认 intent prompt 能看到 display name 和 aliases。
2. 外部调用传入群名/环境信息时，确认 intent prompt 能看到环境摘要。
3. provider 把“关闭本群AI”误判成 `self_ai` 时，确认当前模型仍不会触发主回复。
