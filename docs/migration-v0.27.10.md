# v0.27.10 迁移说明

本次版本不引入新的配置项，主要调整两类内部行为：

- 用户记忆提示词改为“聚焦拼接 + 身份护栏”，减少昵称欺骗、上下文污染和 token 开销。
- `system_info` 改为框架内置 SKILL，engine 与 runtime 默认可直接调用；workspace 同名文件仍可覆盖内置实现。

## 变更摘要

- 主提示词里的 `<participant_memory>` 不再整体注入所有参与者，也不再直接塞入原始 `recent_messages`；现在只保留当前发言者、当前消息直接相关的参与者，以及尾部压缩后的 `session_summary`。
- `memory_extract` 任务输入新增 `strong_identity`、`trusted_labels`、`weak_labels` 与 `alias_guardrails`，要求模型只在“当前说话者明确自称且不与强绑定冲突”时才输出 `inferred_aliases`。
- AI 推断出的昵称不再写入 `profile.aliases`，而是写入 `runtime.inferred_aliases` 作为弱线索；这些弱线索不会进入稳定识人索引，也不会单独决定人物归属。
- `SkillRegistry` 现在会先加载包内置 SKILL（当前包含 `system_info`），再加载 workspace `skills/` 目录；若存在同名文件，workspace 版本覆盖内置实现。
- `examples/skills/system_info.py` 现在只复用内置实现，避免示例与实际运行时逻辑分叉。

## 对外影响

- 如果你之前依赖模型“自己学会”某个昵称后，后续直接用该昵称稳定识别人，升级后这种行为会收紧；真正需要稳定识别的昵称，请通过 `UserProfile.aliases` 或 `identities` 显式提供。
- 如果你只是在调用 `system_info`，升级后无需再先把示例文件复制到 workspace；默认即可调用。
- 如果你的 workspace 已有 `skills/system_info.py`，行为保持兼容：同名 workspace 文件仍会覆盖内置实现。
- 无需修改现有 `SessionConfig` 或 `OrchestrationPolicy` 配置。

## 推荐升级检查

升级后，建议至少覆盖以下场景：

1. 群聊里有人用玩笑、引用或临时昵称称呼他人，确认这些称呼不会被自动提升为稳定 alias。
2. 当前消息提到少量相关参与者时，确认主提示词只保留这些相关人的记忆，不再混入无关参与者的近期内容。
3. 未提供任何 workspace skill 文件时，确认 `system_info` 仍可被 engine 正常调用。
4. 若 workspace 中自定义了 `skills/system_info.py`，确认运行结果仍来自你的自定义实现，而不是内置版本。