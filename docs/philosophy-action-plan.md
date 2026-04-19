# 基于哲学审视的修改计划（修订版 v3）

> **项目宗旨**：生成一个具有独立人格的、能感知到环境下他人感受和自身思考的机器人（可能用户会要求他认为自己是真人）
>
> **前置分析**：
> - `philosophy-memory-analysis.md` — 记忆系统不符合哲学
> - `philosophy-project-analysis.md` — 整体架构部分符合，底层世界观需要调整
>
> **记录时间**：2026-04-18
> **修订记录**：
> - v1：初版，含错误的"串行多小模型"假设（5 个串行调用）
> - v2：矫枉过正，完全拒绝串行，改为"单模型多输出"
> - **v3**：折中。核心原则：**主流程极简（最多 1 次可选小模型），后台路径合理串行**

## 前两个版本的问题

### v1 的问题：无脑串行

```
情感分析(mini) → 意图分析(mini) → 内心独白(mini) → 自传体提取(mini) → 回复生成(4o)
     ↑ 5 次串行，每次 200~500ms，累加 2~3 秒，上下文割裂
```

**不可接受。**

### v2 的问题：矫枉过正

v2 为了回避串行延迟，采取了**极端保守**的策略：
- 所有内心活动必须由大模型在 `<think>` 中一并输出
- 沉默消息只用模板匹配，不用任何模型
- 后台任务只做本地统计

**问题**：
1. 大模型的 `<think>` 虽然共享上下文，但**长度受限**。一条消息的 think 只有一句话，无法承载复杂的内心活动
2. 沉默消息完全放弃模型处理，导致**角色对大部分消息没有 rich 的内心记录**（群里 70% 消息是沉默的）
3. 后台任务如果只用本地统计，自我反思的质量很低

**v2 做了一个虚假的取舍**——用"零成本"换"低质量"。

## v3 的核心原则

> **主流程（影响回复延迟的）最多串行 1 次轻量模型调用（百毫秒级）。**
> **后台路径（不阻塞回复的）可以合理串行多个模型。**

### 什么是"合理"的串行？

| 条件 | 合理？ | 理由 |
|------|--------|------|
| 轻量模型（qwen-flash / deepseek-chat / gpt-4o-mini） | ✅ | 单次 ~100ms，可接受 |
| 大模型（gpt-4o / claude-sonnet） | ❌ | 单次 300~800ms，不可接受 |
| 主流程串行 1 次 | ✅ | +100ms，用户体验可感知但可接受 |
| 主流程串行 2+ 次 | ❌ | +200ms+，体验明显变差 |
| 条件触发（命中率 < 30%） | ✅ | 平均成本 = 100ms × 30% = +30ms |
| 每条消息必调 | ❌ | 100 msg/min = 100 次调用，延迟和成本都累积 |
| 后台异步 | ✅ | 完全不阻塞，串行 10 次也无所谓 |

---

## 修正后的七层流程

```
群友发言
    │
    ▼
[感知层] ── 同步，零成本
    │      更新工作记忆、注册参与者、本地重要性评分
    │
    ▼
[认知层] ── 规则为主，可选一次轻量模型兜底（条件触发，~100ms）
    │      情感分析 + 意图分析合并为一次调用
    │      规则命中时跳过，规则不命中时调 qwen-flash
    │      输出：情绪坐标 + 意图分类 + 相关性评分
    │
    ▼
[联想层] ── 本地向量检索，零成本
    │      基于当前情绪坐标，从自传体记忆中检索情绪相似的记忆
    │      输出：top-k 相关记忆（只读，不生成文本）
    │
    ▼
[决策层] ── 同步，零成本
    │      阈值计算、策略选择
    │      策略：IMMEDIATE / DELAYED / SILENT / PROACTIVE
    │
    ▼ 策略 = IMMEDIATE ─────────────────────────────┐
    │                                               │
    ▼ 策略 = SILENT ── 一次轻量模型（可选，~100ms） │
    │                  生成 surface 内心独白        │
    │                  不阻塞回复（本来就沉默）      │
    │                                               │
    ▼                                               ▼
[执行层] ── 一次大模型调用（回复生成）
    │      prompt 包含：角色剧本 + 感受 + 联想记忆 + 内心冲动
    │      模型输出：<think> + <say>
    │      think 被解析，say 发给用户
    │
    ▼ 后台异步
[记忆层] ── 零 LLM token
    │      把 think + say + 情绪状态 追加到自传体记忆 JSONL
    │
    ▼ 后台异步（每 10~30 分钟）
[反思层] ── 可串行多个轻量模型
    │      自我画像更新、自传体润色、内心独白扩写
```

---

## 主流程的串行策略

### 认知层：合并为一次轻量调用

**当前**：情感分析和意图分析是**分开的**两个分析器，各自有自己的规则引擎和 LLM 兜底。

**修正**：合并为**统一的认知分析器**（`CognitionAnalyzer`）：

```python
class CognitionAnalyzer:
    def analyze(self, message, context, persona):
        # 1. 规则引擎快速判定（零成本，~90% 命中率）
        rule_result = self._rule_analyze(message, context)
        if rule_result.confidence >= 0.7:
            return rule_result
        
        # 2. 规则不命中，调一次轻量模型（~100ms，~10% 命中率）
        return self._llm_analyze(message, context, persona)
```

**为什么合并？**
- 情感和意图高度相关（"我分手了"→ 悲伤 + 情感表达），合并后模型可以联合推理
- 减少一次串行调用（从 2 次变成 1 次）
- 轻量模型（qwen-flash / deepseek-chat）完全有能力同时输出情感和意图

**Prompt 设计**（极简，控制 token）：
```
消息：{message.content}
上下文：{recent_3_messages}
角色：{persona.name}，{persona.personality_traits}

输出 JSON：
{"valence": float(-1~1), "arousal": float(0~1), "intent": str, "urgency": int(0~100), "relevance": float(0~1)}
```

输出只有 5 个数字，~50 tokens，模型响应极快。

### 沉默消息的处理

**v2 的做法**：沉默消息完全不用模型，只用模板匹配 → 质量低

**v3 的做法**：沉默消息**可以**调一次轻量模型生成 surface 内心独白，**但不阻塞回复**（因为本来就没回复）。

实现方式：
- 决策层判定 SILENT 后，把消息元数据放入**后台异步队列**
- 后台消费者用轻量模型生成一句内心独白（~100ms，不阻塞任何用户-facing 流程）
- 结果写入自传体记忆，标记为 `depth: surface`

**成本**：100 msg/hour × 70% 沉默率 = 70 次轻量调用/小时 ≈  negligible

### 执行层：大模型同时输出 think + say（不变）

这是 v2 的核心洞察，v3 保留：

```
[角色剧本]
[我现在的感受：valence={v}, arousal={a}]
[我想起的事：{retrieved_memory}]
[消息] {content}

请输出：
<think>你此刻的内心反应（一句话）</think>
<say>你要回复的内容</say>
```

**为什么 think 必须和大模型一起生成？**
- 上下文完整：大模型能看到角色剧本、感受、记忆、消息全文
- 一致性保证：think 和 say 由同一个模型在 same context 下生成，不会脱节
- 成本最低：零新增调用，只是多输出 ~50 tokens

---

## 后台路径的串行策略

后台路径**不阻塞回复**，所以可以合理串行多个模型。

### 自传体记忆润色（AutobiographyPolisher）

**触发**：每 5 分钟，扫描最近写入的 `depth: surface` 记录

**流程**：
```
读取 surface 记录（10~20 条）
    ↓
串行调用轻量模型（qwen-flash，~100ms/条）
    ↓
把 "有点烦" 扩写成 "看到 Bob 又在炫耀工资，我有点烦。他每次都是这样，完全不顾及别人的感受。"
    ↓
更新记录为 depth: rich
```

**为什么不在写入时直接生成 rich？**
- 写入时在主流程上（即使是 SILENT，也在决策后立即写入）
- 润色可以批量做（一次读 10 条，一次模型调用处理多条）
- 有些 surface 记录后来变得不重要（没被再次检索），不需要润色

### 自我反思（SelfReflection）

**触发**：每 30 分钟

**流程**：
```
读取最近 30 分钟的自传体记录
    ↓
调用轻量模型生成反思摘要（~200 tokens）
    ↓
更新 SelfSemanticProfile.growth_notes
```

**Prompt**：
```
以下是你最近的经历：
{recent_autobiographical_entries}

请用第一人称写一段内心独白（50~100字）：
- 你对最近发生的事有什么感受？
- 你觉得自己有什么变化？
- 有什么未解决的情绪？
```

### 主动发言内容生成（ProactiveGenerator）

**触发**：`ProactiveTrigger` 判定需要主动发言时

**流程**：
```
从自传体记忆中检索一条"想分享的"记忆
    ↓
调用轻量模型生成主动发言内容（~150 tokens）
    ↓
输出：自然的话题引入，不突兀
```

---

## 延迟预算分析

假设群聊 100 msg/hour，30% 回复率：

| 步骤 | 调用频率 | 模型 | 延迟 | 总延迟贡献 |
|------|---------|------|------|-----------|
| 规则引擎 | 100% | 本地 | ~1ms | ~1ms |
| 认知层 LLM 兜底 | ~10% | qwen-flash | ~100ms | ~10ms avg |
| 联想检索 | 30%（回复时） | 本地 | ~5ms | ~1.5ms avg |
| 回复生成 | 30% | gpt-4o | ~500ms | ~150ms avg |
| **单条消息平均延迟** | | | | **~163ms** |

**对比当前**：
- 当前：规则 (~1ms) + 回复 (~150ms) = **~151ms**
- v3：**~163ms**，增加 **+12ms**

**对比 v1（无脑串行）**：
- v1：规则 + 情感(mini) + 意图(mini) + 内心(mini) + 回复(4o) = **~1000ms+**

**v3 在 v1 和 v2 之间找到了平衡点**：比 v2 多了 12ms 的平均延迟（认知层兜底），但获得了 rich 的认知分析；比 v1 少了 800ms+ 的延迟。

---

## 成本预算分析（每小时）

| 项目 | 当前 | v3 | 变化 |
|------|------|-----|------|
| 认知层规则 | 100% × 0 | 90% × 0 + 10% × qwen-flash(~50 tokens) | +500 tokens |
| 回复生成 | 30% × 512 tokens | 30% × 640 tokens (+think) | +3.8K tokens |
| 沉默消息 surface | 0 | 70% × 0 (模板) | 0 |
| 沉默消息润色（后台） | 0 | ~20 条 × qwen-flash(~100 tokens) | +2K tokens |
| 自我反思（后台） | 0 | 2 次 × qwen-flash(~200 tokens) | +400 tokens |
| **总计** | ~15K | ~22K | **+47%** |

**每小时增加约 47% 的 token 成本**，但这是用 qwen-flash（极其便宜）替代了一部分 gpt-4o 的调用。

如果按价格折算（假设 qwen-flash 是 gpt-4o 的 1/10）：
- 新增 7K tokens 中，~6.5K 是 qwen-flash，~0.5K 是 gpt-4o
- 实际成本增加约 **~15%**（而不是 47%）

---

## 实施计划（v3）

### Phase 1：认知层合并（P0）

**目标**：把 EmotionAnalyzer + IntentAnalyzerV3 合并为统一的 CognitionAnalyzer

**修改**：
1. 新增 `CognitionAnalyzer`：
   - 内部包含规则引擎（合并现有情感规则和意图规则）
   - 规则不命中时，调一次轻量模型（模型名可配置，默认 qwen-flash）
   - 输出统一的数据结构：`CognitionResult(valence, arousal, intent, urgency, relevance)`
2. 删除独立的 `EmotionAnalyzer` 和 `IntentAnalyzerV3`（或保留为内部辅助类）
3. 修改 `emotional_engine.py` 的 `_cognition()` 方法，使用新的 `CognitionAnalyzer`

**成本**：零新增 LLM 调用次数（只是把 2 次可能的分开调用合并为 1 次）

**文件**：`sirius_chat/core/cognition_analyzer.py`

---

### Phase 2：执行层改造（P0）

**目标**：大模型同时输出 `<think>` + `<say>`

**修改**：
1. 修改 `ResponseAssembler.assemble()`，在 prompt 末尾追加 `<think>` / `<say>` 指令
2. 修改 `_generate()`，解析两个标签
3. `inner_thought = think_tag_content` 传入后台队列

**文件**：`sirius_chat/core/response_assembler.py`, `sirius_chat/core/emotional_engine.py`

---

### Phase 3：自传体记忆层（P0）

**目标**：存储角色的第一人称经历

**修改**：
1. 新增 `AutobiographicalMemoryManager`
   - 路径：`{work_path}/autobiographical/{persona_name}.jsonl`
   - 格式：
     ```json
     {
       "timestamp": "...",
       "trigger_message": "Alice: 我分手了",
       "inner_thought": "Alice 平时很少说私事的，这次肯定真的很累",
       "reply": "摸摸头，需要我给你点外卖吗",
       "my_emotion": {"valence": -0.3, "arousal": 0.2},
       "depth": "rich"  // rich（大模型 think）或 surface（轻模型/模板）
     }
     ```
2. 新增 `EmotionalTimeline`（本地，零成本）
3. 修改 importance 计算，加入人格化权重

**文件**：`sirius_chat/memory/autobiographical.py`

---

### Phase 4：联想层（P1）

**目标**：基于情绪共鸣检索记忆

**修改**：
1. 修改 `MemoryRetriever.retrieve()`，增加情绪共鸣模式
2. 检索结果注入 prompt

**文件**：`sirius_chat/memory/memory_retriever.py`

---

### Phase 5：沉默消息的 surface 处理（P1）

**目标**：沉默消息也有 rich 的内心记录

**修改**：
1. 决策层判定 SILENT 后，把消息元数据放入**异步队列**
2. 后台消费者用轻量模型生成 surface 内心独白
3. 写入自传体记忆，标记 `depth: surface`

**文件**：`sirius_chat/core/emotional_engine.py`（异步队列集成）

---

### Phase 6：后台润色与反思（P2）

**目标**：定期润色 surface 记忆，生成自我反思

**修改**：
1. 新增 `AutobiographyPolisher`（后台任务）
   - 每 5 分钟扫描 surface 记录
   - 批量调轻量模型润色
   - 更新为 depth: rich
2. 新增 `SelfReflectionTicker`（后台任务）
   - 每 30 分钟读取近期自传体记录
   - 调轻量模型生成反思摘要
   - 更新 SelfSemanticProfile

**文件**：`sirius_chat/core/background_tasks.py`

---

### Phase 7：自我画像（P2）

**目标**：角色能认识自己

**修改**：
1. 新增 `SelfSemanticProfile`
2. 大部分字段本地统计更新
3. `growth_notes` 由后台反思任务更新

**文件**：`sirius_chat/memory/self_profile.py`

---

## 总结

v3 的核心理念：**主流程极简，后台路径丰富。**

- **主流程**：感知 → [可选：一次轻量模型兜底] → 联想检索 → 决策 → 大模型回复（含 think）
- **后台路径**：沉默消息润色、自传体扩写、自我反思、主动发言生成

**延迟**：单条消息平均 +12ms（认知层兜底 10% 命中率 × 100ms）
**成本**：每小时 +15%（折算后，因为大量调用的是便宜的轻量模型）
**收益**：
- 所有回复都有与最终回复一致的内心独白
- 沉默消息也有 rich 的内心记录（后台异步生成）
- 完整的自传体记忆时间线
- 情绪共鸣检索
- 自我画像与成长记录
