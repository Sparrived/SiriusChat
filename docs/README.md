# Sirius Chat 文档导航

## 快速开始

| 文档 | 适合谁 | 内容 |
|------|--------|------|
| [快速开始](quickstart.md) | 第一次使用 | 安装、初始化 workspace、发送第一条消息 |
| [外部使用指南](external-usage.md) | 接入自己项目的开发者 | API 调用方式、WorkspaceRuntime 集成、常见模式 |

## 模块系统详解

每个文档都回答了三个问题：**这是什么？为什么要它？怎么用？**

| 文档 | 一句话定位 |
|------|-----------|
| [情感化群聊引擎](engine-emotional.md) | v1.0 唯一引擎，四层认知管线让 AI 像真人一样在群里说话 |
| [记忆系统](memory-system.md) | 三层记忆底座（工作→情景→语义）+ 自传体记忆，让引擎记得住、回忆得起、理解得了 |
| [认知层](emotion-intent-analysis.md) | 统一情绪+意图分析器（CognitionAnalyzer），零成本热路径 + 单次 LLM fallback |
| [人格系统](persona-system.md) | 可配置、可持久化的角色人格，影响引擎的整个认知管线 |
| [SKILL 系统](skill-system.md) | 插件机制，让 AI 能调用外部能力（查系统、截屏、调 API） |
| [工作空间运行时](workspace-runtime.md) | 入口层：管理目录、配置、会话、引擎生命周期、热刷新 |
| [模型提供者系统](provider-system.md) | LLM 调用层：统一接口、自动路由、健康检查 |

## 配置与策略

| 文档 | 内容 |
|------|------|
| [配置指南](configuration.md) | Emotional Engine 配置字段、provider 配置、环境变量 |
| [最佳实践](best-practices.md) | 生产环境部署、性能调优、常见问题 |
| [SKILL 编写指南](skill-authoring.md) | 如何写一个 SKILL：格式、参数、依赖、数据存储 |

## 架构与 API

| 文档 | 内容 |
|------|------|
| [架构概览](architecture.md) | 整体模块关系、数据流、技术栈 |
| [完整架构流程](full-architecture-flow.md) | 从消息进入到回复生成的全链路详细流程 |
| [API 文档](api.md) | 公开 API 接口清单、签名、返回值 |

## 迁移文档

| 文档 | 内容 |
|------|------|
| [v0.28 迁移指南](migration-v0.28.md) | 历史迁移文档（legacy 引擎已移除） |

## 哲学与设计

| 文档 | 内容 |
|------|------|
| [哲学-行动路线图](philosophy-action-plan.md) | 从"功能性 AI 工具"到"独立人格与内在生活"的架构演进 |
| [哲学-记忆分析](philosophy-memory-analysis.md) | 为什么当前记忆系统失败于哲学目标 |
| [哲学-项目分析](philosophy-project-analysis.md) | 全项目差距分析 |
| [哲学-格式选择](philosophy-json-vs-natural.md) | 为什么 XML 标签优于 JSON/破折号作为输出分隔符 |

## 已归档文档

以下文档仅用于维护旧实例，新用户无需关注：

| 文档 | 说明 |
|------|------|


> 更旧版本的迁移文档已删除。如需查看历史迁移步骤，请查阅对应版本的 Git 标签。
