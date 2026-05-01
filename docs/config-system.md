# 配置系统

> **类型安全的配置契约层** — 从 JSON/JSONC 到 dataclass，统一管理所有配置模型、加载、合并和持久化。

## 一句话定位

配置系统定义了 Sirius Chat 中所有配置的**形状**（models）、**加载逻辑**（manager）、**便捷构建器**（helpers）和**人类可读格式**（jsonc），确保从 CLI 到引擎的每一层都使用同一套类型安全的配置对象。

## 为什么需要它

配置来源多样：JSON 文件、环境变量、代码覆盖、用户 WebUI 编辑。没有统一配置层会导致：
- 同一字段在不同模块中有不同的默认值
- 缺少字段时崩溃而不是优雅降级
- 旧版字段改名后无法兼容
- 用户手动编辑 JSON 时无法理解字段含义

配置系统解决以上全部问题。

## 架构总览

```
JSON/JSONC 文件（磁盘）
    │
    ├── config/jsonc.py ── 解析带注释的 JSONC / 生成带注释的 JSONC
    │
    └── config/manager.py ── 加载、验证、合并、迁移
            │
            ├── config/models.py ── 输出类型化 dataclass
            │
            └── config/helpers.py ── 便捷修改/构建函数

环境变量 ──► config/manager.py（${VAR} 替换）
```

---

## config/models.py（配置契约）

**定位**：所有配置对象的单一事实来源。使用 `@dataclass(slots=True)` 保证内存紧凑和类型安全。

### 核心类

| 类 | 用途 | 关键字段 |
|----|------|---------|
| `Agent` | AI agent 定义 | `name`, `persona`, `model`, `temperature`, `max_tokens`, `metadata` |
| `AgentPreset` | Agent + 全局系统提示词 | `agent`, `global_system_prompt` |
| `SessionDefaults` | 会话级默认 | `max_history`, `enable_compression` |
| `MemoryPolicy` | 记忆策略 | `fact_limit`, `confidence_threshold`, `decay_schedule` |
| `OrchestrationPolicy` | **最重的配置类**：多模型编排 | `unified_model`, `task_models`, `task_temperatures`, `task_max_tokens`, `task_retries`, `task_enabled`, `engagement_sensitivity`, `reply_frequency`, `enable_skills`, `max_skill_rounds`, ... |
| `TokenUsageRecord` | 单次 LLM 调用记录 | `prompt_tokens`, `completion_tokens`, `task_name`, `model`, `group_id`, ... |
| `WorkspaceConfig` | Workspace 级清单 | `layout_version`, `active_agent_key`, `session_defaults`, `orchestration_defaults` |
| `MultiModelConfig` | 便捷 DTO | 将 `task_models`/`task_temperatures`/`task_max_tokens`/`task_retries` 打包，可与 `OrchestrationPolicy` 互转 |
| `SessionConfig` | **运行时配置** | `work_path`, `data_path`, `preset`, `orchestration`, `agent`（property 代理到 preset） |

### OrchestrationPolicy 详解

这是引擎运行时最核心的配置对象：

| 字段 | 说明 |
|------|------|
| `unified_model` | 统一模型模式：所有任务用同一个模型 |
| `task_models` | 按任务映射模型：`{"response_generate": "gpt-4o", "cognition_analyze": "gpt-4o-mini", ...}` |
| `task_temperatures` | 按任务映射 temperature |
| `task_max_tokens` | 按任务映射 max_tokens |
| `task_retries` | 按任务映射重试次数 |
| `task_enabled` | 按任务开关：`{"intent_analysis": true, ...}` |
| `engagement_sensitivity` | 回复敏感度（0~1） |
| `reply_frequency` | 回复频率限制 |
| `enable_skills` / `max_skill_rounds` / `skill_execution_timeout` / `auto_install_skill_deps` | SKILL 系统控制 |
| `memory_manager_model` / `memory_manager_temperature` / `memory_manager_max_tokens` | 记忆管理任务参数 |

### 验证

`SessionConfig.__init__` 内部调用 `OrchestrationPolicy.validate()`，拒绝非法组合：
- 不能同时设置 `unified_model` 和 `task_models`
- 数值字段必须在合理范围内

---

## config/manager.py（配置加载器）

**定位**：将磁盘上的原始 JSON 转换为类型化的 `SessionConfig` / `WorkspaceConfig`。

### 核心能力

| 方法 | 说明 |
|------|------|
| `load_from_json(path)` | 读取 JSON/JSONC，解析 `${VAR}` 环境变量，构建 `SessionConfig` |
| `load_from_env(env)` | 从环境加载预设（dev.json / test.json / prod.json） |
| `merge_configs(base, override)` | 深度合并两个字典（override 优先） |
| `load_workspace_config(work_path, data_path)` | 加载 workspace 级配置：同时读取 `workspace.json`（机器清单）和 `config/session_config.json`（人工快照），后者对 `session_defaults` 和 `orchestration` 有更高优先级 |
| `save_workspace_config(work_path, config)` | 将配置持久化回两个文件 |
| `build_session_config(...)` | **运行时 builder**：加载 workspace → 解析 active agent → 从 roleplay 库加载 preset → 应用覆盖 → 输出完整 `SessionConfig` |
| `bootstrap_workspace_from_legacy_session_json(...)` | 将旧版 `session.json` 一次性迁移到 workspace 布局 |

### 加载优先级

```
1. workspace.json（机器可读 manifest）
2. config/session_config.json（人工维护快照）→ 对 session_defaults 和 orchestration 更高优先级
3. 代码传入的 overrides
4. 环境变量 ${VAR} 替换（在 parse 阶段完成）
```

### 兼容迁移

`build_session_config` 内部自动处理旧字段到新字段的映射：

| 旧字段 | 新字段 |
|--------|--------|
| `enable_intent_analysis` | `task_enabled["intent_analysis"]` |
| `intent_analysis_model` | `task_models["intent_analysis"]` |
| `message_debounce_seconds` | `pending_message_threshold`（四舍五入） |
| `memory_manager_model` | `task_models["memory_manager"]` |

---

## config/helpers.py（便捷构建器）

**定位**：避免调用方手动组装庞大的 `OrchestrationPolicy`。

### 核心函数

| 函数 | 说明 |
|------|------|
| `build_orchestration_policy_from_dict(raw, agent_model)` | 从原始 dict 构建 `OrchestrationPolicy`；自动迁移旧字段；若未指定模型则使用 `agent_model` 作为 `unified_model` |
| `auto_configure_multimodal_agent(agent, ...)` | 为 Agent 设置 `metadata["multimodal_model"]` |
| `create_agent_with_multimodal(...)` | 构造已配置好多模态的 Agent |
| `configure_orchestration_models(config, **task_models)` | 将 `SessionConfig` 从统一模型切换到按任务模型模式 |
| `setup_multimodel_config(...)` | `MultiModelConfig` ↔ `OrchestrationPolicy` 转换 |
| `configure_orchestration_temperatures / retries / full_orchestration` | 使用 `dataclasses.replace` 不可变地修改特定字段 |

### 不可变更新

所有 helper 函数遵循**不可变**原则：返回新的 `SessionConfig`/`OrchestrationPolicy` 实例，不修改原对象（`auto_configure_multimodal_agent` 除外，它会修改 `agent.metadata`）。

---

## config/jsonc.py（JSON-with-Comments）

**定位**：让人类能写带注释的配置文件，同时让标准 `json` 模块能解析。

### 核心函数

| 函数 | 说明 |
|------|------|
| `strip_json_comments(content)` | 状态机解析器：移除 `//` 行注释和 `/* */` 块注释，同时保留字符串内的注释符号 |
| `loads_json_document(content)` | 解析 JSONC 字符串为 Python 对象 |
| `load_json_document(path)` | 从文件加载 JSONC |
| `render_session_config_jsonc(payload)` | 将 dict 渲染为带中文注释的 JSONC 字符串 |
| `write_session_config_jsonc(path, payload)` | 原子写入带注释的配置 |
| `build_default_orchestration_payload()` | 生成包含所有默认值的 orchestration dict |
| `build_default_session_config_payload()` | 生成完整默认 session config dict |

### 注释渲染

`render_session_config_jsonc` 会根据字段路径自动插入中文注释：

```jsonc
{
  // 模型编排策略
  "orchestration": {
    // 统一模型（若设置则所有任务使用同一模型）
    "unified_model": "",
    // 按任务分配模型
    "task_models": {
      // 回复生成模型
      "response_generate": "gpt-4o"
    }
  }
}
```

注释映射表 `_SESSION_CONFIG_COMMENTS` 按 `"orchestration.task_models"` 等路径索引。

---

## 与其他系统的关系

| 交互对象 | 方式 |
|---------|------|
| **config/models.py** | 被 manager、helpers、jsonc 共同消费，也是 engine 的输入契约 |
| **config/manager.py** | 使用 jsonc 解析文件，使用 helpers 规范化字段，输出 SessionConfig/WorkspaceConfig |
| **config/helpers.py** | 纯消费者 of models；被 manager 和外部调用方（examples、CLI）使用 |
| **config/jsonc.py** | 被 manager 读写 session_config.json；独立提供带注释的模板生成 |
| **utils/layout.WorkspaceLayout** | manager 使用 layout 解析 work_path 和 config_path |
| **roleplay_prompting** | manager 调用 `load_generated_agent_library()` 解析 generated_agents.json |
| **EngineRuntime** | 消费 SessionConfig 的 orchestration 和 agent 字段 |
