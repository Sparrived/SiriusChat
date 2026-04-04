---
name: release-checklist
description: "在为 Sirius Chat 做发布准备时使用，用于校验版本信息、命令可运行性、文档准确性与技能同步状态。关键词：发布准备、发布前检查、文档同步、命令校验。"
---

# 发布检查清单

## 目标

在打标签或发布前，执行一致的发布前检查流程。

## 语言规范

- 发布前需检查仓库内 SKILL 是否全部为中文。
- 任何新增或修改的 SKILL 必须使用中文（包含 frontmatter 的 `description`）。
- 若发现英文 SKILL 内容，发布前必须完成中文化修正。

## 步骤

1. 版本与元数据
   - 校验 `pyproject.toml` 中版本号与描述是否正确。
2. 安装并执行检查
   - `python -m pip install -e .[test]`
   - `pytest -q`
3. 命令校验
   - `sirius-chat --help`
   - `python main.py --help`
   - `python main.py --config examples/session.json --work-path data/release_smoke`（可通过输入 `exit` 立即退出，验证入口可用）
   - 检查 `data/release_smoke/transcript.json` 是否成功写出
4. 文档与 AI 资产同步
   - 确认 `README.md` 中命令/示例仍可使用。
   - 确认 `docs/architecture.md` 与当前模块边界一致。
   - 确认 `.github/skills/framework-quickstart/SKILL.md` 反映最新架构。
5. 变更摘要
   - 总结关键变更与已知限制。

## 失败条件

- 测试失败。
- 命令示例过期。
- 架构或 SKILL 文档与代码不同步。

## 交付输出

提供一份简要报告，包含：
- 已通过检查项
- 未通过检查项
- 后续处理动作
