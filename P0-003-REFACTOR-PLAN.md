# P0-003 async_engine.py 大型代码重构规划

## 当前状态分析

- **文件大小**：924 行（单个 AsyncRolePlayEngine 类）
- **类数**：1 个主类
- **公共方法**：约 10-15 个关键方法
- **私有辅助方法**：约 15+ 个内部工具方法

## 重构目标

### 目标 1：按职责拆分模块
将单一的 924 行文件拆分为 5-6 个专职模块，每个模块职责清晰：
- `core.py`：核心编排逻辑（run_live_session）
- `orchestration.py`：多模型任务编排（memory_extract, event_extract 等）
- `memory_management.py`：用户记忆与事件记忆管理
- `system_prompt.py`：系统提示词构建
- `utils.py`：通用工具方法

### 目标 2：提升代码可维护性
- 模块间依赖明确
- 单个文件 < 200 行
- 涉及模块边界必须更新文档

### 目标 3：零破坏性重构
- 公共接口不变（AsyncRolePlayEngine 导出位置不变）
- 单元测试全部通过
- API 层 `sirius_chat/api/` 不受影响

## 拆分方案

### Phase 1：提取工具层（40-50 行）
```
async_engine/
├── __init__.py          # 重新导出 AsyncRolePlayEngine
├── core.py              # 核心编排类（400-500 行）
├── orchestration.py     # 任务编排模块（200-250 行）
├── memory.py            # 记忆管理模块（100-150 行）
├── prompts.py           # 提示词构建模块（100-150 行）
└── utils.py             # 工具函数集（100-150 行）
```

### Phase 2：方法迁移路线
1. **orchestration.py**：
   - `_run_memory_extract_task()`
   - `_run_event_extract_task()`
   - `_run_multimodal_parse_task()`
   - `_run_memory_manager_task()`

2. **memory.py**：
   - `_build_event_hit_system_note()`
   - 与 UserMemoryFileStore、EventMemoryManager 的交互

3. **prompts.py**：
   - `_build_system_prompt()`
   - 系统提示词格式化逻辑

4. **utils.py**：
   - `_extract_json_payload()`
   - `_estimate_tokens()`
   - `_normalize_multimodal_inputs()`
   - `_record_task_stat()`
   - `_prepare_transcript()`

## 实施步骤

### Step 1：建立包结构（0.5h）
- 创建 `sirius_chat/async_engine/` 目录
- 创建 `__init__.py`，暂时重新导出现有 AsyncRolePlayEngine

### Step 2：逐步拆分（4-6h）
1. 提取工具函数到 `utils.py` ✓
2. 提取提示词逻辑到 `prompts.py` ✓
3. 提取记忆管理到 `memory.py` ✓
4. 提取任务编排到 `orchestration.py` ✓
5. 精简 `core.py` ✓

### Step 3：测试验证（2-3h）
- 运行 139 个现有单元测试（0 失败）
- smoke test：`main.py --help` 与单次会话启动
- 验证导入路径正确

### Step 4：文档同步（1-2h）
- 更新 `docs/architecture.md`：模块不再是 async_engine.py，而是 async_engine/ 包
- 更新 `docs/full-architecture-flow.md`：去除单一 async_engine.py，改为模块视图
- 更新 SKILL 文件的阅读顺序
- 更新 README.md（如果有直接引用）

## 预期收益

- ✅ 单个文件 < 200 行，降低认知负荷
- ✅ 模块职责单一，便于维护和扩展
- ✅ 清晰的依赖关系，便于追踪影响
- ✅ 易于添加新的编排任务或记忆管理策略

## 风险与缓解

| 风险 | 缓解方法 |
|------|---------|
| 循环依赖 | 在 `__init__.py` 中小心管理导入顺序 |
| 导入路径变更 | 保持 public API 不变（sirius_chat/api/ 重新导出） |
| 测试失败 | 每个 Step 后立即运行 `pytest -q` 验证 |

## 时间估算

- 总耗时：8-10 小时
- Phase 1 架构：1-2h
- Phase 2 拆分：4-6h  
- Phase 3 测试：1-2h
- Phase 4 文档：1-2h
