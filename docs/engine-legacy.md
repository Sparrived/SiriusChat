# Legacy AsyncRolePlayEngine

> **v0.27 及之前的核心引擎**，v0.28+ 仍保留用于兼容。

## 一句话定位

AsyncRolePlayEngine 是 Sirius Chat 的**第一代引擎**，基于角色扮演（RolePlay）prompt 系统。它通过精心设计的系统提示词让 AI 扮演一个特定角色，支持多轮对话、用户记忆、SKILL 调用和会话恢复。

## 与 EmotionalGroupChatEngine 的区别

| 维度 | Legacy 引擎 | Emotional 引擎（v0.28+） |
|------|------------|------------------------|
| 核心机制 | 角色扮演提示词 + 规则后处理 | 四层认知管线（感知→认知→决策→执行） |
| 回复决策 | 静态概率 `should_reply` | 动态阈值 × 节奏分析 × 人格偏移 |
| 情绪理解 | 无 | 2D valence-arousal + 助手自身情绪 |
| 记忆 | 单层用户事实 + 事件记忆 v2 | 三层记忆底座（工作/情景/语义） |
| 延迟回复 | 无 | 话题间隙检测触发 |
| 主动发言 | 无 | 时间/记忆/情感触发 |
| 群聊隔离 | 否（v0.28 迁移后才有） | 是（设计之初） |
| 模型路由 | 固定模型 | 按任务类型自动选择 |
| 人格定义 | `AgentPreset` + `global_system_prompt` | `PersonaProfile` 结构化 |

## 什么时候还用 Legacy 引擎

EmotionalGroupChatEngine 是 v0.28+ 的推荐默认，但 Legacy 引擎在以下场景仍有价值：

1. **需要完整的角色扮演体验**：Legacy 引擎的 `global_system_prompt` 可以容纳 900~1600 字的详细角色设定，包含 `<role_profile>`、`<life_story>`、`<core_drives>` 等 9 个结构化段落。如果你的角色需要极其丰富的背景故事和世界观，Legacy 的 prompt 系统目前更成熟。
2. **已有成熟的 roleplay 资产**：如果你已经通过 `generated_agents.json` 和问卷系统生成了一套完整的角色资产，Legacy 引擎可以直接消费这些资产。
3. **稳定性优先**：Legacy 引擎经过更长时间的实战检验，行为更可预测。

## 核心工作方式

### 系统提示词构建

Legacy 引擎的 `build_system_prompt()` 是核心能力。它把以下信息拼接成一个完整的系统提示词：

```
<global_directive>     ← 全局行为指令（来自 roleplay prompt 生成器）
<agent_identity>       ← 角色身份（名字、别名、设定）
<environment_context>  ← 外部注入的上下文
<session_summary>      ← 长期会话摘要
<participant_memory>   ← 参与者记忆
<self_diary>           ← AI 自身日记
<glossary>             ← 术语表
<splitting_instruction> ← 消息分割规则
<available_skills>     ← 可用 SKILL 列表
<constraints>          ← 输出约束
```

### 消息处理流程

```
run_live_message(message)
    │
    ├── 1. 后处理消息（self_ai 判定、参与者识别）
    ├── 2. 加载/恢复会话状态
    ├── 3. 构建系统提示词（含记忆、上下文）
    ├── 4. 生成助手回复
    ├── 5. 更新用户记忆（提取偏好、关系）
    ├── 6. 更新会话摘要
    └── 7. 持久化会话状态
```

### Self-AI 判定

Legacy 引擎有一个重要的后处理步骤：判断用户消息是否在回应 AI 助手。判定依据包括：
- 消息中是否提到 AI 的名字或别名
- 上下文承接关系（用户是否在回应 AI 上一轮的发言）
- 消息中是否包含"你"等指代词

这个判定直接影响 `target_scope`（self_ai / other_ai / human / unknown），进而影响记忆提取和回复策略。

## 使用方式

```python
from sirius_chat.api import open_workspace_runtime

runtime = open_workspace_runtime(work_path)

# 使用 legacy 引擎处理消息
await runtime.run_live_message(
    session_id="session_1",
    message={"role": "human", "content": "你好"},
)
```

或者直接用引擎：

```python
from sirius_chat.async_engine import AsyncRolePlayEngine

engine = AsyncRolePlayEngine(provider)
engine.run_live_message(...)
```

## 迁移到 Emotional 引擎

如果你正在使用 Legacy 引擎，可以参考 `docs/migration-v0.28.md` 了解迁移步骤。核心变化：

1. 目录布局改变（群聊隔离）——运行迁移脚本自动处理
2. 引擎接口从 `run_live_message()` 变为 `process_message()`
3. 角色定义从 `AgentPreset` 变为 `PersonaProfile`（有桥接方法）
4. 需要手动启动后台任务（`start_background_tasks()`）

## 未来计划

Legacy 引擎在 v0.28 期间继续维护，但新功能（人格系统、情感分析、延迟回复、主动发言等）只会加入 EmotionalGroupChatEngine。长期目标是让 Emotional 引擎完全覆盖 Legacy 引擎的能力，届时 Legacy 引擎可能进入只读维护模式。
