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
| [情感化群聊引擎](engine-emotional.md) | v0.28 核心引擎，四层认知管线让 AI 像真人一样在群里说话 |
| [Legacy 角色扮演引擎](engine-legacy.md) | v0.27 及之前的引擎，基于详细角色扮演 prompt，保留兼容 |
| [记忆系统](memory-system.md) | 三层记忆底座（工作→情景→语义），让引擎记得住、回忆得起、理解得了 |
| [情感与意图分析](emotion-intent-analysis.md) | 认知层核心：读懂"对方什么心情"和"对方想干什么" |
| [人格系统](persona-system.md) | 可配置、可持久化的角色人格，影响引擎的整个认知管线 |
| [SKILL 系统](skill-system.md) | 插件机制，让 AI 能调用外部能力（查系统、截屏、调 API） |
| [工作空间运行时](workspace-runtime.md) | 入口层：管理目录、配置、会话、引擎生命周期、热刷新 |
| [模型提供者系统](provider-system.md) | LLM 调用层：统一接口、自动路由、健康检查 |

## 配置与策略

| 文档 | 内容 |
|------|------|
| [配置指南](configuration.md) | workspace.json、编排策略、provider 配置 |
| [编排策略](orchestration-policy.md) | 消息分割、冷却时间、批处理、会话恢复策略 |
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
| [v0.28 迁移指南](migration-v0.28.md) | 从 v0.27 迁移到 v0.28：群聊隔离、引擎切换、新 API |
| [v0.27 迁移指南](migration-v0.27.md) | v0.27 的主要变更 |
| [事件流迁移](migration-event-stream.md) | 事件系统迁移（如有） |
| [实时消息迁移](migration-live-message.md) | 实时消息处理迁移 |
| [记忆 v2 迁移](migration-memory-v2.md) | 记忆系统 v2 升级 |

> 更旧版本的迁移文档已归档删除。如需查看历史迁移步骤，请查阅对应版本的 Git 标签。
