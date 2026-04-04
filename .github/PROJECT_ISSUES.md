---
title: 项目问题跟踪与改进清单
created: 2026-04-05
status: Active
---

# 项目问题跟踪与改进清单

## 优先级说明
- **P0** - 立即改进（影响生产可用性或代码质量）
- **P1** - 近期规划（功能完整性与长期维护）
- **P2** - 中期优化（性能与用户体验）

---

## 🔴 P0 - 立即行动

### P0-001: 生产级日志系统缺失
- **状态**: 待修复
- **描述**: 项目无logging配置，完全依赖异常传播，缺乏以下能力：
  - 任务执行追踪与调试
  - 重试过程可观测性
  - Token预算告警
  - 性能指标收集
- **影响**: 生产环境诊断困难，故障排查成本高
- **修复方案**:
  - [ ] 创建 `sirius_chat/logging_config.py` 配置模块
  - [ ] 支持JSON结构化日志、console日志两种格式
  - [ ] 在关键位置（Provider调用、任务执行、错误处理）添加日志
  - [ ] 提供日志级别可配置能力（DEBUG/INFO/WARNING/ERROR）
- **预计工作量**: 4-6小时

### P0-002: 错误处理不足，通用RuntimeError混用
- **状态**: 待修复
- **描述**: 所有错误都是通用RuntimeError，缺乏：
  - 异常分类与细化（Provider/Token/Parse/Config错误）
  - 错误上下文信息（重试次数、模型、预算等）
  - 区分错误的可恢复性
- **影响**: 调用方无法精准处理不同类型失败
- **修复方案**:
  - [ ] 创建 `sirius_chat/exceptions.py` 自定义异常模块
  - [ ] 定义异常树：SiriusException → {ProviderError, TokenError, ParseError, ConfigError}
  - [ ] 在async_engine/providers等模块替换RuntimeError为自定义异常
  - [ ] 添加异常文档与每个异常的示例处理
- **预计工作量**: 6-8小时

### P0-003: 代码复杂度过高，单文件过大
- **状态**: 待修复
- **描述**: 
  - `async_engine.py` (550行): 包含编排层主逻辑 + 4个辅助任务实现（memory_extract/event_extract/multimodal_parse/memory_manager）
  - `user_memory.py` (650行): 索引管理、序列化、业务逻辑混杂
- **影响**: 代码难以维护、修改风险高、测试粒度粗
- **修复方案**:
  - [ ] 从async_engine.py中拆分TaskRunner层：`sirius_chat/orchestration/task_runner.py`
  - [ ] 将4个任务拆分为单独模块：`sirius_chat/orchestration/tasks/{memory_extract,event_extract,multimodal_parse,memory_manager}.py`
  - [ ] 从user_memory.py中拆分FileStore：`sirius_chat/persistence/user_memory_store.py`
  - [ ] 重新组织导入结构，保持向后兼容性
- **预计工作量**: 12-16小时

### P0-004: HTTP依赖隐藏，pyproject.toml未显式声明
- **状态**: 待修复
- **描述**: 
  - openai_compatible.py / siliconflow.py / volcengine_ark.py都要求HTTP库（requests/httpx）
  - pyproject.toml中无任何HTTP库声明
  - 用户无法在安装时了解完整依赖
- **影响**: 运行时缺失依赖错误，Provider隐式依赖不可追踪
- **修复方案**:
  - [ ] 在pyproject.toml中添加optional-dependencies: provider
  - [ ] 在provider基类的__init__中添加版本检查与友好错误提示
  - [ ] 更新README与示例配置说明依赖安装方式
  - [ ] 提供requirements.txt模板（含所有可选依赖）
- **预计工作量**: 2-3小时

### P0-005: main.py启动失败错误信息不清晰
- **状态**: 待诊断与修复
- **描述**: `python main.py` 返回 Exit Code 1，但无有用的错误信息
- **可能原因**:
  - 缺失config文件但未告知
  - work_path初始化失败
  - Provider配置验证失败
- **修复方案**:
  - [ ] 诊断具体错误（运行并捕获完整stack trace）
  - [ ] 改进CLI参数验证逻辑，提供详细的失败原因
  - [ ] 添加--init-config命令生成默认配置模板
  - [ ] 添加preflight check命令验证环境就绪
- **预计工作量**: 3-4小时

---

## 🟡 P1 - 近期规划

### P1-001: 测试覆盖死角 - 缺乏集成测试
- **状态**: 待改进
- **描述**: 79个单元测试全通过，但缺乏：
  - 网络超时/断连与重试链的端到端测试
  - Token超额时的优雅降级流程
  - 并发会话下的内存竞争检测
  - Provider切换时的状态迁移
- **修复方案**:
  - [ ] 创建 `tests/integration/` 目录
  - [ ] 添加test_provider_resilience.py（超时/重试）
  - [ ] 添加test_concurrent_sessions.py（并发安全）
  - [ ] 添加test_memory_decay_integration.py（衰退机制）
  - [ ] 创建 `tests/benchmarks/` 目录，添加吞吐量/延迟基准测试
- **预计工作量**: 8-10小时

### P1-002: Token估算过粗，对中文文本准确度低
- **状态**: 待改进
- **描述**: 当前使用`len(text)/4`全局估算，但中文≈1token、英文≈0.25token
- **修复方案**:
  - [ ] 创建 `sirius_chat/token_utils.py` 精确估算模块
  - [ ] 实现启发式估算函数（区分中英文）
  - [ ] 提供可选的tiktoken集成（如已安装）
  - [ ] 添加单元测试验证各类文本的估算精度
- **预计工作量**: 4-5小时

### P1-003: 缺乏Provider中间件系统
- **状态**: 规划中
- **描述**: 当前Provider无统一的流控/重试/failover框架
- **修复方案**:
  - [ ] 创建 `sirius_chat/providers/middleware/` 模块
  - [ ] 实现RateLimiterMiddleware（速率限制）
  - [ ] 实现RetryMiddleware（统一重试策略）
  - [ ] 实现FallbackMiddleware（故障转移）
  - [ ] 实现CostMetricsMiddleware（成本计量）
- **预计工作量**: 10-12小时

### P1-004: 文档与代码同步缺乏自动化
- **状态**: 待改进
- **描述**: skill-sync-enforcer存在但无CI/CD自动化，文档生成手工化
- **修复方案**:
  - [ ] 创建.github/workflows/ci.yml（GitHub Actions）
  - [ ] 集成skill-sync-enforcer自动检查
  - [ ] 添加pre-commit钩子配置（类型检查+lint）
  - [ ] 考虑Sphinx/doctest自动化文档生成
- **预计工作量**: 6-8小时

---

## 🟢 P2 - 中期优化

### P2-001: 性能优化 - 缺乏缓存策略
- **状态**: 规划中
- **描述**: 用户档案查询每次遍历索引，无LRU缓存
- **方案**: 为frequently-accessed对象（档案/内存事实）加LRU缓存

### P2-002: 性能优化 - 连接池与批量操作
- **状态**: 规划中
- **描述**: 支持HTTP连接复用与batch API调用

### P2-003: README完善 - Troubleshooting章节
- **状态**: 规划中
- **描述**: 新增常见问题快速诊断指南

---

## 修复进度跟踪

| 问题ID | 优先级 | 状态 | 修复开始日期 | 完成日期 | 备注 |
|--------|--------|------|-----------|---------|------|
| P0-001 | P0 | 进行中 | 2026-04-05 | - | 日志系统 |
| P0-002 | P0 | 待开始 | - | - | 异常体系 |
| P0-003 | P0 | 待开始 | - | - | 代码拆分 |
| P0-004 | P0 | 待开始 | - | - | 依赖声明 |
| P0-005 | P0 | 待诊断 | - | - | main.py |
| P1-001 | P1 | 待开始 | - | - | 集成测试 |
| P1-002 | P1 | 待开始 | - | - | Token估算 |
| P1-003 | P1 | 规划中 | - | - | Middleware |
| P1-004 | P1 | 待开始 | - | - | CI/CD |

---

## 总体修复计划

```
Week 1 (2026-04-05 ~ 04-06)
├─ P0-001: 日志系统 (完成)
├─ P0-002: 异常体系 (开始)
├─ P0-005: main.py诊断 (并行)
└─ 验收: 通过所有79个单元测试 + 新增日志

Week 2 (2026-04-07 ~ 04-13)
├─ P0-002: 异常体系 (完成)
├─ P0-003: 代码拆分 (开始)
├─ P0-004: 依赖声明 (完成)
└─ P1-001: 集成测试框架 (开始)

Week 3-4 (2026-04-14 ~ 04-27)
├─ P0-003: 代码拆分 (完成)
├─ P1-001: 集成测试 (完成)
├─ P1-002: Token估算 (完成)
└─ 回归测试与文档更新

Month 2 (2026-05 onwards)
├─ P1-003: Provider Middleware
├─ P1-004: CI/CD自动化
└─ P2系列优化
```

---

## 修复后预期效果

```
当前状态 (80/100)
├─ 架构: ⭐⭐⭐⭐⭐
├─ 代码质量: ⭐⭐⭐⭐
├─ 生产就绪度: ⭐⭐⭐ ← 需改进
└─ 可维护性: ⭐⭐⭐⭐

修复后 (90/100)
├─ 架构: ⭐⭐⭐⭐⭐ (P0-003拆分后)
├─ 代码质量: ⭐⭐⭐⭐⭐
├─ 生产就绪度: ⭐⭐⭐⭐⭐ ← 日志+异常+错误处理
└─ 可维护性: ⭐⭐⭐⭐⭐ ← 模块化+文档同步
```

---

## 相关文档

- 完整分析: `/memories/session/project-analysis.md`
- 测试状态: `pytest -q` → 79/79 ✅
- Commit规范: `.github/skills/commit-preparation/SKILL.md`
