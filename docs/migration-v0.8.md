# v0.7.0 → v0.8.0 迁移指南（供下游 AI/自动化代理查阅）

## 变更总览

v0.8.0 新增以下能力，均为向后兼容（不会破坏现有调用方式）：

| 特性 | 涉及模块 | 默认行为 |
|------|----------|----------|
| System Prompt 瘦身 | `async_engine/prompts.py` | 自动生效，无需变更 |
| SKILL 执行超时 | `config/models.py`, `skills/executor.py` | 默认 30 秒 |
| SKILL 依赖自动安装 | `skills/dependency_resolver.py`, `skills/registry.py` | 默认开启，使用 uv/pip |
| 环境上下文注入 | `core/engine.py`, `api/engine.py` | 默认空字符串，可选传入 |

---

## 1. System Prompt 瘦身

### 变更内容

- `<output_constraints>` + `<security_constraints>` 合并为 `<constraints>`。
- 参与者记忆改用紧凑格式：`?` 表示低置信（<0.6），`~` 表示中置信（<0.8）。
- Agent 身份段使用 `名:` / `设定:` 替代旧的 `本名：` / `角色设定：`。
- 别名仅在实际存在时才输出（不再显示"未设置"）。

### 迁移动作

**无需任何代码修改**。如果下游代码/测试检查了旧的 prompt 标签或文本：

```python
# 旧断言（需更新）
assert "<output_constraints>" in prompt   # ❌
assert "本名：Bot" in prompt              # ❌
assert "角色设定：helper" in prompt        # ❌

# 新断言
assert "<constraints>" in prompt           # ✅
assert "名: Bot" in prompt                 # ✅
assert "设定: helper" in prompt            # ✅
```

---

## 2. SKILL 执行超时

### 新增字段

```python
OrchestrationPolicy(
    enable_skills=True,
    skill_execution_timeout=30.0,  # 新增，默认 30 秒，0=不限制
)
```

### 行为

- `SkillExecutor.execute_async()` 在 `timeout > 0` 时使用 `asyncio.wait_for()` 限时。
- 超时返回 `SkillResult(success=False, error="SKILL执行超时（限制 30 秒）...")`。
- 引擎从 `config.orchestration.skill_execution_timeout` 读取值并传递。

### 迁移动作

**无需修改**。默认 30 秒适用于绝大多数场景。如需调整：

```python
# 增加至 60 秒
orchestration = OrchestrationPolicy(
    enable_skills=True,
    skill_execution_timeout=60.0,
)

# 完全关闭超时
orchestration = OrchestrationPolicy(
    enable_skills=True,
    skill_execution_timeout=0,
)
```

---

## 3. SKILL 依赖自动安装

### 新增模块

`sirius_chat/skills/dependency_resolver.py`

### 工作机制

当 `SkillRegistry.load_from_directory()` 加载 SKILL 文件时：

1. **AST 扫描** `SKILL_META["dependencies"]` 列表（显式声明，优先级最高）。
2. **AST 扫描**顶层 `import` 语句，提取第三方包名。
3. **过滤**标准库和已安装包。
4. **安装**缺失包：优先使用 `uv pip install`，回退到 `pip install`。

### SKILL 声明依赖的推荐写法

```python
SKILL_META = {
    "name": "weather",
    "description": "查询天气",
    "dependencies": ["requests", "beautifulsoup4"],  # 新增字段
    "parameters": { ... },
}
```

如果省略 `dependencies`，系统仍会通过 import 扫描自动推断，但显式声明更可靠（例如 `beautifulsoup4` 的 import 名是 `bs4`，自动推断无法反向映射）。

### 新增配置字段

```python
OrchestrationPolicy(
    enable_skills=True,
    auto_install_skill_deps=True,  # 新增，默认开启
)
```

### 迁移动作

**无需修改**。默认启用。如需在受限环境下关闭：

```python
orchestration = OrchestrationPolicy(
    enable_skills=True,
    auto_install_skill_deps=False,  # 禁止自动安装
)
```

---

## 4. 环境上下文注入

### 新增参数

`run_live_message` 和 `arun_live_message` 新增 `environment_context: str = ""` 参数：

```python
# 旧写法（仍然有效）
transcript = await engine.run_live_message(
    config=config,
    transcript=transcript,
    turn=turn,
)

# 新写法（可选传入环境信息）
transcript = await engine.run_live_message(
    config=config,
    transcript=transcript,
    turn=turn,
    environment_context="当前群名: 技术讨论群\n在线人数: 42",
)
```

### 行为

- 非空的 `environment_context` 会渲染为系统提示词中的 `<environment_context>` XML 段。
- 空字符串（默认）不产生任何额外提示内容。
- 参数从公共 API 逐层传递至 `build_system_prompt()`。

### 参数传递链路

```
arun_live_message()                    # api/engine.py
  └─ engine.run_live_message()         # core/engine.py
       └─ _process_live_turn()
            └─ _generate_assistant_message()
                 └─ _build_chat_main_request_context()
                      └─ _build_system_prompt()
                           └─ build_system_prompt()    # prompts.py
```

### 迁移动作

**无需修改**。默认值为空字符串，不改变任何现有行为。

---

## 5. 新增文件清单

| 文件 | 说明 |
|------|------|
| `sirius_chat/skills/dependency_resolver.py` | SKILL 依赖扫描与自动安装 |
| `docs/skill-authoring.md` | SKILL 编写指南（面向 AI） |
| `docs/integration-sync-guide.md` | 外部调用同步指南（面向 AI） |
| `docs/migration-v0.8.md` | 本文件 |

## 6. 变更文件清单

| 文件 | 变更类型 |
|------|----------|
| `sirius_chat/async_engine/prompts.py` | 重写：瘦身、合并段、新增 environment_context |
| `sirius_chat/config/models.py` | 新增字段：`skill_execution_timeout`, `auto_install_skill_deps` |
| `sirius_chat/skills/executor.py` | 新增 timeout 参数与超时处理 |
| `sirius_chat/skills/registry.py` | 新增 auto_install_deps 参数 |
| `sirius_chat/skills/__init__.py` | 导出 `resolve_skill_dependencies` |
| `sirius_chat/core/engine.py` | 传递 environment_context、timeout、auto_install_deps |
| `sirius_chat/api/engine.py` | `arun_live_message` 新增 environment_context |

## 7. 快速兼容性检查

```python
# 测试所有新参数均有默认值，旧调用不受影响
from sirius_chat.config.models import OrchestrationPolicy

p = OrchestrationPolicy(unified_model="gpt-4o")
assert p.skill_execution_timeout == 30.0
assert p.auto_install_skill_deps is True

from sirius_chat.api.engine import arun_live_message
import inspect
sig = inspect.signature(arun_live_message)
assert sig.parameters["environment_context"].default == ""
```
