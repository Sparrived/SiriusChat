# 外部人格生成迁移指南：v0.19.x → v0.20.0

## 适用范围

这份迁移指南面向外部调用方：

- 直接调用 `sirius_chat.api` 生成/更新人格的 Python 服务
- 依赖 `<work_path>/generated_agents.json` 管理 agent 资产的应用
- 维护角色卡、设定稿、对白样本等本地素材文件，并希望在素材更新后自动重生人格的接入方

本次升级不改变会话主流程 API，但**升级了人格生成器的输入模型、持久化产物和再生能力**。

---

## 你需要关注的变化

### 1. 人格生成支持依赖文件

旧版本中，人格生成只依赖：

- `answers`
- `trait_keywords`
- `background`

从 `v0.20.0` 开始，`PersonaSpec` 与构建入口新增：

- `dependency_files`

适用内容包括：

- 角色设定稿
- 台词样本
- 世界观/背景设定
- 语气风格规范
- 陪伴型角色的长期关系说明

这些文件会在生成时被读取并注入 prompt，帮助模型稳定继承外部素材中的人格线索。

### 2. 生成器会自动加强“拟人/情感”相关 prompt

当输入中出现以下信号时：

- 拟人
- 情感 / 情绪
- 共情
- 陪伴
- 关系 / 信任

生成器会自动加入强化约束，显式要求模型：

- 提升真实人感
- 增加情绪细节与自然波动
- 保持关系连续性
- 避免机械助手腔、说明书腔和客服腔

这项能力默认开启，无需额外配置。

### 3. 生成过程会完整本地化

旧版本只持久化最终产物：

- `<work_path>/generated_agents.json`

新版本还会额外持久化完整生成轨迹：

- `<work_path>/generated_agent_traces/<agent_key>.json`

每条轨迹包含：

- 生成时间
- 操作类型（build / update / regenerate_from_dependencies）
- 最终发送给模型的 `system_prompt` / `user_prompt`
- 原始模型返回
- 解析后的 JSON payload
- 最终输出的 preset
- 依赖文件快照（完整内容、sha256、缺失状态）
- 触发的 prompt 强化项

这意味着外部系统现在可以审计人格生成来源，而不必只看最终结果。

### 4. 支持“依赖文件重生”

新增 API：

- `aregenerate_agent_prompt_from_dependencies(...)`

用途：

- 当角色卡改了
- 当语气样本更新了
- 当设定稿新增了背景信息

你不需要重新收集整套问答，也不需要手工重建 `PersonaSpec`，只需要让框架重新读取依赖文件并生成同一个 `agent_key`。

---

## 迁移步骤

### 场景 A：你原来只用问答生成，不关心文件依赖

原代码通常类似：

```python
from sirius_chat.api import abuild_roleplay_prompt_from_answers_and_apply

await abuild_roleplay_prompt_from_answers_and_apply(
    provider,
    config=config,
    model="deepseek-ai/DeepSeek-V3.2",
    agent_name=config.agent.name,
    answers=answers,
    persona_key="assistant_v1",
)
```

这段代码**仍然有效，无需修改**。

推荐你额外接入：

```python
from sirius_chat.api import load_persona_generation_traces

traces = load_persona_generation_traces(config.work_path, "assistant_v1")
latest = traces[-1]
print(latest.generated_at, latest.operation)
```

这样能在出问题时快速定位是 prompt、原始返回还是输入素材导致的人格变化。

### 场景 B：你希望把角色卡/设定稿接入生成器

把本地文件路径传给 `dependency_files`：

```python
await abuild_roleplay_prompt_from_answers_and_apply(
    provider,
    config=config,
    model="deepseek-ai/DeepSeek-V3.2",
    agent_name="北辰",
    answers=answers,
    dependency_files=[
        "persona/notes.md",
        "persona/style_examples.txt",
    ],
    persona_key="beichen_v2",
)
```

建议规则：

- 路径使用相对 `work_path` 的相对路径
- 文件内容尽量稳定、结构清晰
- 把“长期有效的人格线索”和“临时任务说明”分开存放，不要把运行时噪音塞进依赖文件

### 场景 C：你希望在素材更新后重生人格

当 `dependency_files` 指向的文件发生变化后，直接调用：

```python
from sirius_chat.api import aregenerate_agent_prompt_from_dependencies

updated = await aregenerate_agent_prompt_from_dependencies(
    provider,
    work_path=config.work_path,
    agent_key="beichen_v2",
    model="deepseek-ai/DeepSeek-V3.2",
)
```

效果：

- 重新读取当前磁盘上的依赖文件
- 使用已持久化的 `PersonaSpec` 继续生成
- 覆盖同一个 `agent_key` 的 preset
- 追加一条新的本地生成轨迹

---

## 新 API 一览

### `PersonaSpec.dependency_files`

```python
spec = PersonaSpec(
    agent_name="北辰",
    trait_keywords=["沉稳", "共情"],
    dependency_files=["persona/notes.md"],
)
```

### `load_persona_generation_traces(work_path, agent_key)`

读取完整本地生成历史：

```python
from sirius_chat.api import load_persona_generation_traces

traces = load_persona_generation_traces(work_path, "beichen_v2")
for trace in traces:
    print(trace.generated_at, trace.operation)
```

### `aregenerate_agent_prompt_from_dependencies(...)`

基于最新依赖文件直接重生：

```python
from sirius_chat.api import aregenerate_agent_prompt_from_dependencies

updated = await aregenerate_agent_prompt_from_dependencies(
    provider,
    work_path=work_path,
    agent_key="beichen_v2",
    model="deepseek-ai/DeepSeek-V3.2",
)
```

---

## 持久化产物变化

### 保留不变

- `<work_path>/generated_agents.json`

仍然保存：

- 生成后的 agent preset
- 持久化的 `PersonaSpec`

### 新增产物

- `<work_path>/generated_agent_traces/<agent_key>.json`

这是新增的本地轨迹目录，外部如果要做审计、回滚、A/B 对比，应该读取这里，而不是只看最终 preset。

---

## 兼容性说明

### 向后兼容

以下旧调用方式保持兼容：

- `agenerate_agent_prompts_from_answers(...)`
- `abuild_roleplay_prompt_from_answers_and_apply(..., answers=[...])`
- `aupdate_agent_prompt(...)`
- `load_persona_spec(...)`
- `generated_agents.json` 的既有读取流程

### 行为增强但不破坏

- 含“拟人/情感/陪伴/关系”语义的输入，现在生成结果通常会更有温度、更有人格一致性
- 生成后会自动追加本地轨迹文件

如果你的系统对“输出人格文本的具体措辞”做了严格快照断言，建议更新测试基线。

---

## 推荐升级策略

1. 先升级代码并保持旧调用不变，确认原问答流仍正常。
2. 接入 `load_persona_generation_traces(...)`，把生成过程纳入可观测范围。
3. 再逐步把角色卡、设定稿、语气样本迁入 `dependency_files`。
4. 最后把素材更新流程切换到 `aregenerate_agent_prompt_from_dependencies(...)`。

这条路径风险最低，也最适合已有外部系统平滑迁移。