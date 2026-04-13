# v0.17.0 迁移指南

本版本包含四项核心优化：消息合并策略升级、多模态智能降级、引擎级记忆共享、预处理并行流水线。

## 变化摘要

| 变更 | 影响范围 | 破坏性 |
|------|----------|--------|
| 消息合并从 `\n` 改为智能拼接 | debounce 合并输出 | ⚠️ 低 — 多数场景更自然 |
| 多模态请求仅在当前批次含图时发送 vision 格式 | 含历史图片的纯文本后续轮次 | ⚠️ 低 — 降低 token 成本 |
| 记忆系统提升至引擎级，跨 Session 共享 | 多 Session 场景 | ✅ 无 — 接口不变 |
| 预处理流水线并行化（intent + memory 并发） | 主模型调用前延迟 | ✅ 无 — 接口不变 |

---

## 1. 消息合并策略升级

### 变更前

debounce 窗口内同一用户的多条消息始终用 `\n` 拼接：

```
"你好\n我是临雀"
```

### 变更后

- **短消息**（所有片段 ≤ 30 字符且均为单行）：用中文逗号 `，` 拼接，读感更自然
- **长消息或多行消息**：保留 `\n` 拼接，保持段落结构

```python
# 短消息示例
"你好，我是临雀"

# 长消息示例（含超过 30 字符的片段）
"今天天气真不错，我们出去玩吧\n好啊，去哪里呢？我觉得公园比较合适。"
```

### 影响

- 若你的下游逻辑依赖 `\n` 作为消息边界分隔符进行解析，需更新为同时处理 `，` 连接的情况
- 对绝大多数 LLM 调用场景，此变更只影响可读性，不影响语义

---

## 2. 多模态智能降级

### 变更前

只要 transcript 中存在含图片的历史消息，所有后续请求都会以 vision 格式（`image_url` content parts）发送这些图片，即使当前用户轮次只包含纯文本。

### 变更后

引擎检测**当前批次**（最后一次 assistant 回复之后的用户消息）是否包含图片：

- **含图片**：全部历史图片以 `image_url` vision 格式发送（行为不变）
- **不含图片**：历史图片折叠为文本描述符 `[图片: url...]`，避免触发 provider 的 vision 模式

```python
# 当前批次无图时，历史图片变为：
{"role": "user", "content": "[小明] 看看这张图\n[图片: https://example.com/img.png...]"}

# 当前批次有图时，保持 vision 格式：
{"role": "user", "content": [
    {"type": "text", "text": "[小明] 看这张新图"},
    {"type": "image_url", "image_url": {"url": "https://example.com/new.png"}}
]}
```

### 影响

- **成本降低**：纯文本后续对话不再触发 vision 定价
- **兼容性**：若你依赖每次请求都包含完整 `image_url` 部分，需要调整预期
- 无需任何配置变更，此优化自动生效

---

## 3. 引擎级记忆共享

### 变更前

每个 `run_live_session` 调用独立从磁盘加载 `UserMemoryManager`、`SelfMemoryManager` 和 `EventObservationStore`。不同 Session 之间无法在内存层面共享记忆数据，必须等磁盘持久化后再由下一个 Session 重新加载。

### 变更后

`AsyncRolePlayEngine` 维护引擎级共享存储，按 `work_path` 键索引：

```
engine._shared_user_memory[work_key]   → UserMemoryManager
engine._shared_self_memory[work_key]   → SelfMemoryManager
engine._shared_event_stores[work_key]  → EventObservationStore
```

- **首次加载**：从磁盘读取后缓存到引擎级存储
- **后续 Session**：直接复用引擎级缓存，跳过磁盘 I/O
- **持久化时**：同步回写到引擎级存储，确保下一个 Session 能立即看到最新数据

### 影响

- **接口不变**：`SessionConfig`、`Transcript`、`run_live_session`、`run_live_message` 签名完全不变
- **行为增强**：同一 `work_path` 下的不同 Session 能即时共享记忆更新，无需等待磁盘 I/O 往返
- **内存占用**：引擎实例会在内存中持有所有活跃 `work_path` 的记忆数据；若 `work_path` 数量极多，需注意内存用量
- 无需任何配置变更

---

## 4. 预处理并行流水线

### 变更前

`_process_live_turn` 中的预处理步骤串行执行：

```
_add_human_turn (含 memory_extract + event_extract)  →  engagement intent_analysis  →  主模型调用
```

### 变更后

将 `_add_human_turn` 与 `intent_analysis` 并行执行：

```
┌─ _add_human_turn (memory_extract + event_extract) ─┐
│                                                      ├──→ 主模型调用
└─ intent_analysis (若 auto/smart 模式)              ─┘
```

使用 `asyncio.gather()` 实现并发，两个分支完成后再执行 engagement 决策和主模型调用。

### 影响

- **延迟降低**：intent_analysis 与 memory/event 提取并发执行，总预处理时间取决于最慢的分支
- **行为一致**：最终的 engagement 决策、主模型调用逻辑完全不变
- **并发控制**：仍受 `max_concurrent_llm_calls` 信号量限制，不会超过配置的并发上限
- 无需任何配置变更

---

## 升级步骤

1. **更新依赖**：
   ```bash
   pip install --upgrade sirius-chat
   ```

2. **检查消息合并逻辑**：若下游解析依赖 `\n` 分割 debounced 消息，需适配 `，` 拼接的情况

3. **检查多模态处理**：若依赖每次请求都包含 `image_url` parts，需调整为仅在含图轮次预期此格式

4. **无需修改配置**：所有优化自动生效，`SessionConfig` 和 `OrchestrationPolicy` 接口保持向后兼容

## 兼容性说明

- 本版本所有变更均为内部优化，公共 API 签名未变
- 消息合并策略变更可能影响依赖换行符分割的下游文本处理逻辑（破坏性低）
- 多模态降级可能改变对 provider 的请求格式（仅影响无图后续轮次）
