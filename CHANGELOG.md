# 变更日志

本文档记录 Sirius Chat 的所有版本变更。采用 [Keep a Changelog](https://keepachangelog.com/zh-CN/) 规范。

## [Unreleased]

### Added
- commit-preparation SKILL：commit前检查清单，包括gitignore验证、改动总结、ChangeLog更新与标准格式commit

### Changed

### Fixed

### Deprecated

---

## [0.1.0] - 2026-04-05

### Added

#### 核心框架
- 多人角色扮演编排引擎（`AsyncRolePlayEngine`）
- 支持"多人用户 + 单AI主助手"交互模式
- 结构化会话与记录系统（`SessionConfig`, `Transcript`）

#### LLM Provider支持
- OpenAI 兼容接口适配（`openai_compatible.py`）
- SiliconFlow 专用适配（`siliconflow.py`，默认基地址 `https://api.siliconflow.cn`）
- 火山方舟 Ark 专用适配（`volcengine_ark.py`，默认基地址 `https://ark.cn-beijing.volces.com/api/v3`）
- Provider 自动路由（按模型前缀匹配）

#### 用户记忆系统（Phase 1）
- 用户档案与运行时状态管理（`UserProfile`, `UserRuntimeState`）
- 结构化记忆事实存储（`MemoryFact`），支持分类、验证、冲突检测
- 事件记忆管理（`EventMemoryManager`），支持事件命中评分
- 用户识别与身份索引（支持跨渠道同人识别）

#### 记忆质量评估与智能遗忘（Phase 2）
- 记忆质量评估模块（`MemoryQualityAssessor`）：
  - 多维度评分：置信度(50%) + 活跃度(30%) + 验证状态(15%)
  - 非线性活跃度评分：按年龄划分(0-7天/7-30天/30-90天/>90天)五等级
  - 用户行为一致性分析：身份/偏好/情感/事件四维度评分
- 智能遗忘引擎（`MemoryForgetEngine`）：
  - 时间衰退表：{7: 0.95, 30: 0.85, 60: 0.70, 90: 0.50, 180: 0.20}
  - 自动清理规则：极低置信+陈旧 / 冲突+低置信+极旧 / 低质量+陈旧
  - 冲突记忆加速衰退（额外乘以0.7）
- CLI工具（`memory_quality_tools.py`）：
  - 子命令：analyze/cleanup/decay/all
  - JSON报告导出与控制台展示
  - 完整argparse集成

#### 编排策略与多模态处理
- 任务级编排系统（`memory_extract`, `event_extract`, `multimodal_parse`, `memory_manager`）
- Token 预算控制与限流裁剪
- 遵循 `OrchestrationPolicy` 配置

#### CLI与API接口
- 脚本式CLI（`sirius-chat` 命令）
- Python 库式接口（`.api` 模块化facade）
- 会话配置加载与持久化（JSON + `JsonSessionStore`）

#### 开发工具与文档
- Framework Quickstart SKILL：快速架构理解
- External Integration SKILL：外部接入指南
- Skill Sync Enforcer SKILL：代码与文档联动检查
- Release Checklist SKILL：发布前检查清单
- Commit Preparation SKILL：commit前检查清单
- 完整架构文档（`docs/architecture.md`）
- 外部使用指南（`docs/external-usage.md`）
- 编排策略详解（`docs/orchestration-policy.md`）

#### 测试覆盖
- 综合单元测试（79个测试用例）
- 记忆质量系统测试（8个新增测试）

### Changed

### Fixed

### Deprecated

---

## 版本优先级

### 发布约定
- 主版本号(Major)：重大架构变更或破坏性API改动
- 次版本号(Minor)：新增功能或向后兼容的改动
- 修订号(Patch)：问题修复与性能优化

### 标签命名
- 格式：`v{Major}.{Minor}.{Patch}`
- 示例：`v0.1.0`, `v0.2.0`, `v1.0.0`

---

## 如何贡献

提交前请：
1. 检查 `.gitignore` 覆盖范围（隐私文件、运行时缓存）
2. 总结改动内容
3. 更新此 CHANGELOG.md（在 `[Unreleased]` 部分记录）
4. 按 Conventional Commits 规范提交（中文信息，包含type和scope）

详见：`.github/skills/commit-preparation/SKILL.md`
