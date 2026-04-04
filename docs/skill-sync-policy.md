# Skill 同步策略

## 背景

本仓库将 AI 定制文件视为交付契约的一部分。
若代码结构已变化但 SKILL 仍停留在旧版本，后续 AI 修改会出现漂移。

## 强制规则

对于任何影响架构、命令、API 表面或目录布局的代码变更：

1. 必须更新 `.github/skills/framework-quickstart/SKILL.md`。
2. 若外部接入方式或配置变化，必须更新 `.github/skills/external-integration/SKILL.md`。
3. 当模块边界或执行流程变化时，必须更新 `docs/architecture.md`。
4. 若外部调用方式变化，必须更新 `docs/external-usage.md`。
5. 仅当全局工作流规则变化时，才更新 `.github/copilot-instructions.md`。
6. 内部实现可重构；当前未发布阶段若影响外部接口，可直接升级 `sirius_chat/api/`。
7. 内部新增可用能力，必须在 `sirius_chat/api/` 暴露对外接口。

补充：

- 若变更涉及动态参与者接入或识人记忆（`run_live_session` / `participant_memories`），必须同步更新外部接入 SKILL 与外部使用文档。
- 对外 Python 接口应统一从 `sirius_chat/api/` 提供。

## 合并请求检查清单

- [ ] 代码变更已完成。
- [ ] SKILL 快速上手文档已更新，或明确标注“无需变更”。
- [ ] 外部接入 SKILL 已更新，或明确标注“无需变更”。
- [ ] 架构文档已更新，或明确标注“无需变更”。
- [ ] 外部接入文档已更新，或明确标注“无需变更”。
- [ ] README 中命令示例仍然有效。

