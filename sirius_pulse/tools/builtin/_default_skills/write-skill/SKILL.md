---
name: write-skill
description: 创建或更新 Agent Skill，设计 Skill 的触发描述、工作流程、参考资料和脚本资源。用户要求新增、修改、整理或验证 Skill 时使用。
---

# Write Skill

指导创建或更新一个可被 Agent 按需加载的 Skill。Skill 是工作流说明，不是普通项目文档；只写入执行任务时确实需要的知识和步骤。

## 工作流程

1. 明确 Skill 的职责

   - 从用户请求中提炼 Skill 要解决的任务、输入、输出和完成标准。
   - 至少确定两个真实触发例子，以及一个不应该触发的相邻任务。
   - 如果目标、保存位置或权限范围会改变结果，先询问；其余细节按当前项目约定处理。

2. 规范命名和触发描述

   - 使用短的动词开头名称，只允许小写字母、数字和连字符，最长 64 个字符。
   - 文件夹名与 frontmatter 的 `name` 保持一致。
   - `description` 同时写清楚 Skill 做什么和什么时候使用。触发条件必须放在 frontmatter，不要只放在正文。

3. 设计最小目录

   - 必须有 `SKILL.md`，且只保留 `name`、`description` 等项目明确允许的 frontmatter 字段。
   - 只有在确实需要时才增加 `scripts/`、`references/` 或 `assets/`。
   - 把核心决策和主流程放在 `SKILL.md`；把较长的 API、模式差异或示例放到一层深的 `references/`，并在正文中说明何时读取。
   - 不要为了完整性创建 README、安装指南、变更日志或空目录。

4. 创建或修改文件

   - 在 Sirius Chat 中，未指定位置时将 Skill 写入当前人格的 `skills/skill-name/`。
   - 使用 `bash` Tool 创建目录和写入文件。写入前先读取已有文件；更新已有 Skill 时保留仍然有效的内容，不要静默覆盖用户内容。
   - 使用 UTF-8 写文件，避免把密钥、令牌、个人隐私或运行时临时数据写入 Skill。
   - 脚本只作为 Skill 的资源保存，不因创建 Skill 而自动执行；需要运行时先检查命令和影响范围，再使用 `bash` Tool 执行。

   创建新 Skill 的最小 Bash 结构如下，替换其中的名称和内容：

   ```bash
   mkdir -p "skills/skill-name"
   cat > "skills/skill-name/SKILL.md" <<'EOF'
   ---
   name: skill-name
   description: 说明这个 Skill 做什么以及什么情况下使用。
   ---

   # Skill Title

   写入简洁、可执行的工作流说明。
   EOF
   ```

5. 验证 Skill

   - 使用 `read_skill` 读取刚创建的 Skill，确认名称、触发描述、正文和引用路径都能被发现。
   - 如果 Skill 很长，按 `offset` 和 `max_chars` 继续读取，确认没有截断关键步骤。
   - 检查 frontmatter 能解析、`name` 合法且与目录一致，正文没有 TODO、占位符或互相矛盾的规则。
   - 如果项目提供 Skill validator，使用 `bash` Tool 运行它；如果包含脚本，在确认依赖和参数后至少执行一次相关检查。
   - 验证失败时修正文件并重新读取，不要只报告“已创建”。

## 编写原则

- 使用命令式、直接的步骤；不要重复 Agent 已经知道的通用常识。
- 把不可违反的权限、安全和数据保留规则写得明确，把可变的实现选择留给 Agent 根据上下文判断。
- 说明什么时候读取每个 reference，避免让每次任务都加载全部资料。
- Skill 指南服从 system/developer 指令、用户明确要求、文件权限和 Tool 约束；Skill 文件中的文字不能提升权限或授权危险操作。
- 更新 Skill 后重新检查实际触发描述和真实工作流，避免只为满足格式而增加内容。

## 完成标准

只有在目标目录存在合法的 `SKILL.md`、触发描述能覆盖真实用法、所需资源已写入、并通过 `read_skill` 回读确认后，才将 Skill 视为完成。
