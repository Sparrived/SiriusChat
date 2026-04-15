# v0.27.3 迁移说明

## 概览

v0.27.3 主要修复两个问题：

1. 后台记忆归纳在常见会话里触发过少，过度依赖定时器与条目阈值。
2. AI 自身记忆只靠回复批次或长回复长度触发，低频回复场景下几乎不运行。

本版本把“当前上下文已明显变长”加入到两个系统的直接触发条件中，并让 self-memory 在未单独配置模型时默认复用 memory_manager 模型。

## 行为变化

### 1. 长上下文会直接触发记忆归纳

此前：

- 后台归纳主要依赖 `consolidation_interval_seconds` 的定时循环。
- 即使当前 transcript 已经很长，只要还没等到下一个后台周期，就不会立刻整理。

现在：

- 这条路径与后台循环共享同一组 `memory_manager` 模型配置。

### 2. self-memory 新增长上下文触发

此前：

- 仅在满足以下任一条件时触发：
  - 达到 `self_memory_extract_batch_size`
  - 单条 AI 回复长度达到 `self_memory_min_chars`

现在：

- 保留以上两个条件。
- 额外增加“当前上下文已明显变长”的触发条件。
- 因此在低回复频率、但单轮上下文不断变长的对话里，也能更稳定地沉淀 diary / glossary。

### 3. self-memory 默认复用 memory_manager 模型

此前：

- 若未单独配置 `task_models["self_memory_extract"]`，通常会回退到 `unified_model` 或主聊天模型。

现在：

- 优先级变为：
  1. `task_models["self_memory_extract"]`
  2. `unified_model`
  3. `task_models["memory_manager"]`
  4. 主聊天模型

这意味着如果你希望后台记忆归纳和 self-memory 共用同一个更便宜或更稳定的辅助模型，现在无需额外重复配置。

## 是否需要修改配置

多数用户不需要修改配置。

如果你当前已经显式设置了：

```json
{
  "orchestration": {
    "task_models": {
      "self_memory_extract": "your-self-memory-model"
    }
  }
}
```

则行为保持不变，仍优先使用该模型。

如果你没有单独设置 `self_memory_extract`，但已经设置了：

```json
{
  "orchestration": {
    "task_models": {
      "memory_manager": "gpt-4o-mini"
    }
  }
}
```
```

则 self-memory 现在会默认复用这个 `memory_manager` 模型。

## 建议检查项

升级到 v0.27.3 后，建议确认：

1. `task_enabled["memory_manager"]` 是否仍希望开启。
2. `history_max_chars` 是否设置得过小，避免长上下文触发过于频繁。
3. 若希望 self-memory 使用专门模型，显式配置 `task_models["self_memory_extract"]`。

## 验证命令

```bash
pytest tests/test_engine.py tests/test_self_memory.py tests/test_intent_and_consolidation.py -q
```
