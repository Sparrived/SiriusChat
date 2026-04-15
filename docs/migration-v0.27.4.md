# v0.27.4 迁移说明

## 概览

v0.27.4 主要修复多 AI 群聊下的意图分析偏差。

此前系统虽然能判断消息是否“指向 AI”，但默认把群聊里的所有 AI 视为同一个目标，导致当前模型可能会误把“在叫其他 AI”的消息当成“在叫自己”，从而触发不必要的自动回复。

本版本把“指向 AI”进一步细分为：

- 当前模型自身
- 其他 AI

并让 `reply_mode=auto` 只对“当前模型自身”进入高优先级回复路径；当消息明显是在调用其他 AI 时，当前模型会抑制回复。

## 行为变化

### 1. 意图分析新增更细的目标作用域

`IntentAnalyzer` 现在会在原有 `target` 之外，额外区分：

- `target_scope=self_ai`
- `target_scope=other_ai`
- `target_scope=human`
- `target_scope=everyone`
- `target_scope=unknown`

兼容性上，原有 `target` 字段仍然保留：

- `target=ai` 现在只表示“目标是某个 AI”
- 具体是不是当前模型自身，要看 `target_scope`

### 2. 自动回复会抑制对其他 AI 的抢答

此前：

- 只要消息被判定为 `target=ai`，当前模型就可能进入“被直接点名”的高优先级回复路径。

现在：

- 只有 `target_scope=self_ai` 才会进入该路径。
- 若 `target_scope=other_ai`，当前模型会主动抑制自动回复。

这对以下场景尤其重要：

- 群聊里同时存在多个 assistant
- 用户点名某个其他 AI
- 用户使用“你”追问最近发言的另一个 AI

### 3. 裸代词会参考近期 AI 上下文

此前：

- 对“你/您”的判断较保守，通常只能落到 unknown，或者在部分场景下误吸附到当前模型。

现在：

- 若近期发言者是当前模型，裸代词更可能落到 `self_ai`
- 若近期发言者是其他 AI，裸代词更可能落到 `other_ai`
- 若近期上下文更像是在和人类对话，则会落到 `human`

## 是否需要修改配置

多数用户不需要修改配置。

本次变化主要是运行时语义增强，不要求新增配置项。

如果你已经使用：

```json
{
  "orchestration": {
    "session_reply_mode": "auto",
    "task_enabled": {
      "intent_analysis": true
    }
  }
}
```

那么升级后会自动获得更精确的多 AI 判定与“对其他 AI 抑制回复”的行为。

## 对现有代码的影响

如果你的外部逻辑只读取：

- `IntentAnalysis.target`
- `IntentAnalysis.directed_at_ai`

那么现有行为仍保持兼容。

如果你希望精确区分“当前模型自身”与“其他 AI”，请改为读取：

- `IntentAnalysis.target_scope`
- `IntentAnalysis.directed_at_current_ai`

## 建议检查项

升级到 v0.27.4 后，建议确认：

1. 多 AI 群聊里是否仍然启用了 `intent_analysis`。
2. 若你有基于 `directed_at_ai` 的外部二次决策逻辑，是否需要改用 `directed_at_current_ai`。
3. 如果你原本依赖“只要有人提到任意 AI 当前模型都可能插话”的旧行为，需要重新评估业务预期。

## 验证命令

```bash
pytest tests/test_intent_and_consolidation.py tests/test_async_engine.py -q
pytest -q
```
