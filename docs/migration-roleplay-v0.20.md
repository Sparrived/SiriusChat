# 外部人格生成迁移指南：v0.19.x → v0.22.1

## 适用范围

这份迁移指南面向外部调用方：

- 直接调用 `sirius_chat.api` 生成/更新人格的 Python 服务
- 依赖 `<work_path>/generated_agents.json` 管理 agent 资产的应用
- 维护角色卡、设定稿、对白样本等本地素材文件，并希望在素材更新后自动重生人格的接入方

本次升级不改变会话主流程 API，但**升级了人格生成器的输入模型、持久化产物和再生能力**。

在后续版本中，这条升级路径又继续增强：问卷现在支持模板化场景选择（`default` / `companion` / `romance` / `group_chat`），推荐外部系统优先收集高层人格 brief，再交给 LLM 具体化。

如果你是外部接入方，本文的目标不是让你“勉强兼容旧调用”，而是帮助你**真正切换到新的人格生成器工作流**：

1. 不再手写整段 `global_system_prompt`
2. 不再让前端或运营直接维护最终人格长文
3. 改为维护 `template + answers + dependency_files + traces`
4. 把人格生成纳入可观测、可重生、可审计的资产流

---

## 先看结论：外部现在应该怎么接

推荐把人格生成拆成四层：

1. **模板层**：用 `list_roleplay_question_templates()` 选择场景模板。
2. **输入层**：用 `generate_humanized_roleplay_questions(template=...)` 产出高层问题，只收集人物原型、关系策略、情绪原则、边界和小缺点。
3. **资产层**：把输入组织成 `PersonaSpec`，必要时挂接 `dependency_files`。
4. **运行层**：调用 `abuild_roleplay_prompt_from_answers_and_apply(...)` 或 `agenerate_from_persona_spec(...)`，并把 `generated_agents.json` + `generated_agent_traces/` 当成正式资产。

如果你现在的外部系统还在“人工手写 prompt 再直接塞给模型”，建议优先迁到这四层结构。

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

### 2.1 问卷现在支持按场景切模板

新增模板枚举 API：

- `list_roleplay_question_templates()`

并且：

- `generate_humanized_roleplay_questions(template="default")`
- `generate_humanized_roleplay_questions(template="companion")`
- `generate_humanized_roleplay_questions(template="romance")`
- `generate_humanized_roleplay_questions(template="group_chat")`

这意味着外部系统不需要自己维护多套问题表，只要存一份模板名，就能在不同场景下切换问题清单。

推荐映射关系：

- `default`：通用人格、普通角色设定
- `companion`：陪伴型、情绪支持型、长期在场型角色
- `romance`：恋爱向、亲密关系型角色
- `group_chat`：群聊型、多人互动型角色

如果只想通过命令行拿问题清单，也可以直接使用：

```bash
sirius-chat --list-roleplay-question-templates
sirius-chat --print-roleplay-questions-template romance
```

### 3. 生成过程会完整本地化

旧版本只持久化最终产物：

- `<work_path>/generated_agents.json`

新版本会先暂存输入，再额外持久化完整生成轨迹：

- `<work_path>/generated_agent_traces/<agent_key>.json`

并且在发起模型调用前，会先把最近一次 `PersonaSpec` 暂存到 `<work_path>/generated_agents.json`。如果生成失败，外部仍然可以通过 `load_persona_spec(work_path, agent_key)` 取回这次输入。

每条轨迹包含：

- 生成时间
- 操作类型（build / update / regenerate_from_dependencies）
- 最终发送给模型的 `system_prompt` / `user_prompt`
- 原始模型返回
- 解析后的 JSON payload
- 最终输出的 preset
- 依赖文件快照（完整内容、sha256、缺失状态）
- 触发的 prompt 强化项

也就是说，新的推荐链路不只是“有 trace 可审计”，而是“即便这次生成失败，前面收集的高层人格输入和依赖文件快照也已经先落盘”。

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

## 不要继续这样接

如果你的外部系统还存在下面任一种做法，建议迁移时一起清掉：

### 1. 不要让前端直接产出最终系统提示词

不推荐：

- 前端表单直接让用户填写一大段人格 prompt
- 运营在后台手工维护最终 `global_system_prompt`
- 外部服务把最终 prompt 当成唯一事实来源

推荐：

- 前端只维护 `template`
- 用户或运营只回答高层问题
- 由生成人格 API 负责把抽象输入展开为具体角色指南

### 2. 不要手工改写 `generated_agents.json`

不推荐把 `generated_agents.json` 当成手工编辑文件。这个文件应该被视为生成结果资产，而不是运营配置源。

推荐把以下内容作为外部系统的真实输入源：

- 模板名
- 高层回答
- 关键词标签
- 背景母题
- 依赖文件路径

### 3. 不要跳过轨迹文件

如果你的系统只保存最终人格文本，不保存 `generated_agent_traces/<agent_key>.json`，后面很难追踪：

- 这次人格为什么变了
- 是模型返回变了还是输入变了
- 是哪份依赖文件影响了结果

外部系统至少应接入 `load_persona_generation_traces(...)`，把最近一次 build / update / regenerate 结果纳入观测。

---

## 迁移步骤

### 推荐的最小可用迁移范式

如果你只想知道“外部现在最推荐的接法是什么”，直接照下面这条链路实现：

```python
from sirius_chat.api import (
    PersonaSpec,
    RolePlayAnswer,
    abuild_roleplay_prompt_from_answers_and_apply,
    aregenerate_agent_prompt_from_dependencies,
    generate_humanized_roleplay_questions,
    list_roleplay_question_templates,
    load_persona_generation_traces,
)

# 1. 选择模板
templates = list_roleplay_question_templates()
questions = generate_humanized_roleplay_questions(template="companion")

# 2. 只回答高层人格问题
answers = [
    RolePlayAnswer(
        question=questions[0].question,
        answer="像一个安静但可靠的长期陪伴者，熟了以后很护短。",
        perspective=questions[0].perspective,
    ),
    RolePlayAnswer(
        question=questions[1].question,
        answer="对方低落时先接住情绪，再慢慢帮对方理清思路。",
        perspective=questions[1].perspective,
    ),
]

# 3. 组装 PersonaSpec
spec = PersonaSpec(
    agent_name="北辰",
    answers=answers,
    dependency_files=["persona/notes.md", "persona/style_examples.txt"],
)

# 4. 生成并写入当前 SessionConfig
await abuild_roleplay_prompt_from_answers_and_apply(
    provider,
    config=config,
    model="deepseek-ai/DeepSeek-V3.2",
    persona_spec=spec,
    persona_key="beichen_v2",
)

# 5. 读取轨迹，纳入可观测范围
traces = load_persona_generation_traces(config.work_path, "beichen_v2")

# 6. 后续依赖文件更新时直接重生
await aregenerate_agent_prompt_from_dependencies(
    provider,
    work_path=config.work_path,
    agent_key="beichen_v2",
    model="deepseek-ai/DeepSeek-V3.2",
)
```

只要你的外部系统能跑通上面这条链路，就已经真正切到新的人格生成器了。

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

但对外部系统来说，这里更推荐你把“旧问答流”继续往前推进一小步：

- 不只是继续传 `answers`
- 而是把 `answers` 改成从模板问卷收集而来
- 并逐步收敛到 `persona_spec=PersonaSpec(...)` 的统一入口

如果你希望把旧的“手写问卷”升级成模板问卷，推荐改成：

```python
from sirius_chat.api import (
    generate_humanized_roleplay_questions,
    list_roleplay_question_templates,
)

templates = list_roleplay_question_templates()
questions = generate_humanized_roleplay_questions(template="companion")
```

然后把回答重点放在：

- 人物原型
- 核心矛盾
- 关系策略
- 情绪原则
- 表达节奏
- 边界与小缺点

而不是直接手写整段系统提示词。

如果外部调用方还能改接口，推荐进一步统一到：

```python
spec = PersonaSpec(
    agent_name=config.agent.name,
    answers=answers,
)

await abuild_roleplay_prompt_from_answers_and_apply(
    provider,
    config=config,
    model="deepseek-ai/DeepSeek-V3.2",
    persona_spec=spec,
    persona_key="assistant_v2",
)
```

这样后续再接 `dependency_files` 或 `background` 时，不需要改调用形状。

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

### `list_roleplay_question_templates()`

查看模板名枚举：

```python
from sirius_chat.api import list_roleplay_question_templates

print(list_roleplay_question_templates())
```

### `generate_humanized_roleplay_questions(template=...)`

按场景导出高层人格问卷：

```python
from sirius_chat.api import generate_humanized_roleplay_questions

questions = generate_humanized_roleplay_questions(template="group_chat")
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
3. 再把手写问题表迁移为 `list_roleplay_question_templates()` + `generate_humanized_roleplay_questions(template=...)` 的模板流。
4. 逐步把角色卡、设定稿、语气样本迁入 `dependency_files`。
5. 最后把素材更新流程切换到 `aregenerate_agent_prompt_from_dependencies(...)`。

这条路径风险最低，也最适合已有外部系统平滑迁移。

---

## 迁移完成后的自检清单

如果你想确认外部系统是否已经“真的在用新生成器”，至少检查这几项：

- 你是否已经不再手写最终 `global_system_prompt`
- 你是否已经在外部输入里显式保存 `template`
- 你是否已经使用 `generate_humanized_roleplay_questions(template=...)` 来收集高层回答
- 你是否已经把输入组织为 `PersonaSpec` 或与之等价的结构
- 你是否已经保存并读取 `generated_agent_traces/<agent_key>.json`
- 你是否已经把素材更新流切到 `aregenerate_agent_prompt_from_dependencies(...)`

如果以上仍有 2 项以上未满足，你大概率还停留在“旧人格生成器兼容模式”，而不是“新人格生成器工作流”。