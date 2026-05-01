# Sirius Chat 文档导航

## 快速开始

| 文档 | 适合谁 | 内容 |
|------|--------|------|
| [README.md](../README.md) | 第一次使用 | 安装、基本用法、项目简介 |

## 模块系统详解

每个文档都回答了三个问题：**这是什么？为什么要它？怎么用？**

| 文档 | 一句话定位 |
|------|-----------|
| [情感化群聊引擎](engine-emotional.md) | v1.0 唯一引擎，四层认知管线让 AI 像真人一样在群里说话 |
| [记忆系统](memory-system.md) | 三层记忆底座（基础→日记→语义）+ 自传体记忆，让引擎记得住、回忆得起、理解得了 |
| [认知层](emotion-intent-analysis.md) | 统一情绪+意图分析器（CognitionAnalyzer），零成本热路径 + 单次 LLM fallback |
| [人格系统](persona-system.md) | 可配置、可持久化的角色人格，影响引擎的整个认知管线 |
| [SKILL 系统](skill-system.md) | 插件机制，让 AI 能调用外部能力（查系统、截屏、调 API） |
| [模型提供者系统](provider-system.md) | LLM 调用层：统一接口、自动路由、健康检查 |
| [多人格生命周期](persona-lifecycle.md) | 主进程调度多个人格子进程：创建、启停、监控、数据隔离 |
| [平台适配层](platforms.md) | NapCat 多实例管理、OneBot v11 适配、QQ 桥接、首次配置向导 |
| [WebUI 管理面板](webui.md) | aiohttp REST API + 静态页面，统一管理多人格和 NapCat |
| [Token 统计与持久化](token-system.md) | 精确记录 LLM 调用成本，SQLite 持久化，多维分析 |
| [配置系统](config-system.md) | 类型安全的配置契约：models、加载器、构建器、JSONC 支持 |
| [会话存储](session-store.md) | 对话历史、用户档案、token 记录的 JSON/SQLite 持久化 |
| [核心数据模型参考](models-reference.md) | Message、Participant、PersonaProfile 等跨模块共享的数据结构 |

## 配置与策略

| 文档 | 内容 |
|------|------|
| [配置指南](configuration.md) | Emotional Engine 配置字段、provider 配置、环境变量（用户视角） |
| [最佳实践](best-practices.md) | 生产环境部署、性能调优、常见问题 |
| [SKILL 编写指南](skill-authoring.md) | 如何写一个 SKILL：格式、参数、依赖、数据存储 |
| [模型编排](orchestration-policy.md) | 按任务类型路由模型、动态调整规则 |

## 架构与流程

| 文档 | 内容 |
|------|------|
| [架构概览](architecture.md) | 整体模块关系、数据流、技术栈 |
| [完整架构流程](full-architecture-flow.md) | 从消息进入到回复生成的全链路详细流程 |
