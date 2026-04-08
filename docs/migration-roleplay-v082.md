# Roleplay 提示词生成迁移指南：v0.8.1 → v0.8.2

## 概述

v0.8.2 对 `roleplay_prompting` 模块进行了三项核心改进：

1. **`Agent.persona` 语义变化**：从完整描述文本改为紧凑关键词标签。
2. **`PersonaSpec` 持久化**：生成输入现随输出一起写入 `generated_agents.json`，支持增量微调。
3. **多元构建路径**：新增 Tag-based 和 Hybrid 路径，无需完整问卷即可构建角色。

---

## 破坏性变化

### 1. `Agent.persona` 语义变化

**旧行为**：`agenerate_agent_prompts_from_answers` 要求 LLM 生成一段 200–400 字的 `agent_persona` 描述性文本，该文本写入 `Agent.persona`。

**新行为**：`Agent.persona` 现在是 3–5 个关键词标签，以 `/` 分隔（如 `"热情/直接/逻辑清晰"`），≤30 字。完整的角色扮演描述移至 `global_system_prompt`（400–700 字）。

**影响**：
- `<agent_identity>` 信息块中的 `设定:` 字段现在展示紧凑关键词，不再是长段描述。
- 若你的代码对 `config.agent.persona` 的*文本长度*有假设，需要更新。
- 若你对已保存的 `generated_agents.json` 文件有依赖，字段语义不变，但值的格式改变（紧凑关键词替代长描述）。已保存文件无需手动迁移，可用新版自动重新生成。

### 2. `abuild_roleplay_prompt_from_answers_and_apply` 签名变化

`answers` 参数从**必填**改为**可选**（`list[RolePlayAnswer] | None = None`）。

```python
# 旧签名（仍向后兼容）
await abuild_roleplay_prompt_from_answers_and_apply(
    provider, config=config, model="...", answers=[...]
)

# 新签名（answers 可省略，改用 trait_keywords 或 persona_spec）
await abuild_roleplay_prompt_from_answers_and_apply(
    provider, config=config, model="...",
    trait_keywords=["热情", "直接"],  # 或 answers=[...] 或两者同时提供
)
```

---

## 新增 API

### `PersonaSpec`

```python
from sirius_chat import PersonaSpec, RolePlayAnswer

spec = PersonaSpec(
    agent_name="北辰",
    agent_alias="小辰",
    trait_keywords=["热情", "直接", "逻辑清晰"],   # Tag-based
    answers=[RolePlayAnswer(question="...", answer="...")],  # Q&A
    background="曾在医疗行业工作十年",
    output_language="zh-CN",
)

# 增量合并（仅覆盖 background，其余字段保持不变）
updated_spec = spec.merge(background="经历变化后更加沉稳")
```

### `agenerate_from_persona_spec(provider, spec, *, model, ...)`

统一生成入口，支持三条路径。`agenerate_agent_prompts_from_answers` 现在在内部委托给此函数，行为不变。

```python
from sirius_chat import agenerate_from_persona_spec, PersonaSpec

preset = await agenerate_from_persona_spec(
    provider,
    PersonaSpec(agent_name="北辰", trait_keywords=["沉稳", "共情"]),
    model="your-model",
)
print(preset.agent.persona)        # "沉稳/共情" （关键词）
print(preset.global_system_prompt) # 完整角色指南
```

### `aupdate_agent_prompt(provider, *, work_path, agent_key, model, **patch)`

增量微调已生成的 agent，只传入需要变化的字段：

```python
from sirius_chat import aupdate_agent_prompt

# 只更新背景，answers 和 trait_keywords 沿用上次生成的 PersonaSpec
updated = await aupdate_agent_prompt(
    provider,
    work_path=Path("data/"),
    agent_key="my_agent",
    model="your-model",
    background="最近经历了重大变化，变得更加谨慎",
)
```

**注意**：目标 agent 必须已有持久化的 `PersonaSpec`（即通过 `abuild_roleplay_prompt_from_answers_and_apply` 生成过）。若无持久化 spec，会抛出 `ValueError`。

### `load_persona_spec(work_path, agent_key) -> PersonaSpec | None`

加载已持久化的 `PersonaSpec`（返回 `None` 表示未找到）：

```python
from sirius_chat import load_persona_spec

spec = load_persona_spec(Path("data/"), "my_agent")
if spec:
    print(spec.trait_keywords)   # ['热情', '直接']
    print(spec.background)       # 已保存的背景信息
```

---

## `generated_agents.json` 格式变化

新增 `persona_spec` 字段（已有文件自动向后兼容，缺少该字段不影响读取）：

```json
{
  "selected_generated_agent": "my_agent",
  "generated_agents": {
    "my_agent": {
      "agent": {
        "name": "北辰",
        "persona": "热情/直接/逻辑清晰",
        "model": "...",
        "temperature": 0.7,
        "max_tokens": 512
      },
      "global_system_prompt": "完整的角色扮演指南...",
      "persona_spec": {
        "agent_name": "北辰",
        "agent_alias": "",
        "trait_keywords": ["热情", "直接", "逻辑清晰"],
        "answers": [
          {"question": "...", "answer": "...", "perspective": "subjective", "details": ""}
        ],
        "background": "",
        "output_language": "zh-CN"
      }
    }
  }
}
```

---

## 完全不变的 API（无需迁移）

| API | 状态 |
|-----|------|
| `agenerate_agent_prompts_from_answers(provider, *, model, agent_name, answers, ...)` | ✅ 向后兼容，内部委托新实现 |
| `abuild_roleplay_prompt_from_answers_and_apply(..., answers=[...])` | ✅ 向后兼容 |
| `generate_humanized_roleplay_questions()` | ✅ 不变 |
| `persist_generated_agent_profile(config, *, agent_key)` | ✅ 不变（新增可选 `persona_spec` 参数）|
| `load_generated_agent_library(work_path)` | ✅ 不变 |
| `select_generated_agent_profile(work_path, agent_key)` | ✅ 不变 |
| `create_session_config_from_selected_agent(...)` | ✅ 不变 |
