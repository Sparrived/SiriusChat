# 迁移指南：v0.8 → v0.9（会话事件流）

本版本将消息投递模型从 **`on_message` 回调** 迁移至 **Session 级事件流**（`subscribe` / `asubscribe`），解决 SKILL 执行期间外部无法实时收到中间消息的问题。

## 破坏性变更一览

| 变更 | 旧 API (v0.8) | 新 API (v0.9) |
|------|------|------|
| 消息回调 | `on_message: OnMessage \| None` 参数 | `engine.subscribe(transcript)` 事件流 |
| `run_live_message()` | 接受 `on_message` 参数 | **已移除** `on_message` 参数 |
| `arun_live_message()` | 接受 `on_message` 参数 | **已移除** `on_message` 参数 |
| `run_session()` | 接受 `on_message` 参数 | **已移除** `on_message` 参数 |
| `OnMessage` 类型 | 从 `sirius_chat.api` 导出 | **已移除** |
| 新增导出 | — | `SessionEvent`, `SessionEventBus`, `SessionEventType`, `asubscribe` |

## 迁移步骤

### 1. 移除 `on_message` 参数

```python
# ---- Before (v0.8) ----
def handle_message(msg):
    send_to_wx(msg.content)

transcript = await engine.run_live_message(
    config=config,
    turn=turn,
    on_message=handle_message,   # ← 移除此行
    transcript=transcript,
)

# ---- After (v0.9) ----
transcript = await engine.run_live_message(
    config=config,
    turn=turn,
    transcript=transcript,
)
```

### 2. 用 `subscribe()` 替代回调

在 `run_live_session()` 之后，启动一个后台任务订阅事件流：

```python
import asyncio
from sirius_chat.api import (
    AsyncRolePlayEngine,
    SessionEvent,
    SessionEventType,
    asubscribe,
)

engine = AsyncRolePlayEngine(provider=provider)
transcript = await engine.run_live_session(config=config)

# 方式 A：使用 engine.subscribe() 直接订阅
async def event_listener():
    async for event in engine.subscribe(transcript):
        if event.type == SessionEventType.MESSAGE_ADDED:
            if event.message and event.message.role == "assistant":
                send_to_external(event.message.content)
        elif event.type == SessionEventType.SKILL_STARTED:
            print(f"SKILL 执行中: {event.data['skill_name']}")

listener_task = asyncio.create_task(event_listener())

# 方式 B：使用 asubscribe() facade
async def event_listener():
    async for event in asubscribe(engine, transcript):
        handle(event)
```

### 3. 理解事件类型

| 事件类型 | 触发时机 | `event.message` | `event.data` |
|----------|----------|-----------------|--------------|
| `MESSAGE_ADDED` | 用户消息 / AI 回复 / 分割消息 / SKILL 后部分回复添加到 transcript 时 | `Message` 对象 | `participant_user_id`（用户消息时） |
| `PROCESSING_STARTED` | AI 开始生成回复 | `None` | `speaker` |
| `PROCESSING_COMPLETED` | AI 完成回复生成（含所有 SKILL 轮次） | 最终 `Message` | — |
| `SKILL_STARTED` | 检测到 SKILL 调用并开始执行 | `None` | `skill_name`, `params` |
| `SKILL_COMPLETED` | SKILL 执行完成 | `None` | `skill_name`, `success` |
| `REPLY_SKIPPED` | 回复意愿判定不回复 | `None` | `speaker` |
| `ERROR` | 处理过程中发生错误 | `None` | 错误详情 |

### 4. 外部投递示例（微信/Telegram）

```python
async def external_delivery_loop(engine, transcript):
    """持续监听会话事件，将 AI 消息投递到外部平台。"""
    async for event in engine.subscribe(transcript):
        if event.type != SessionEventType.MESSAGE_ADDED:
            continue
        msg = event.message
        if msg is None or msg.role != "assistant":
            continue
        # 实时投递，无需等待整个 run_live_message 完成
        await send_to_platform(msg.speaker, msg.content)
```

### 5. 清理导入

```python
# ---- Before ----
from sirius_chat.api import OnMessage, arun_live_message

# ---- After ----
from sirius_chat.api import (
    SessionEvent,
    SessionEventType,
    asubscribe,
    arun_live_message,
)
```

## FAQ

**Q: `subscribe()` 返回的迭代器什么时候停止？**
A: 当会话的事件总线关闭时（`event_bus.close()`）。正常使用中，你应在会话结束时取消监听任务。

**Q: 多个消费者可以同时订阅吗？**
A: 可以。每个 `subscribe()` 调用创建独立的队列，所有订阅者都会收到完整的事件流。

**Q: 事件会丢失吗？**
A: 每个订阅者有独立的缓冲队列（默认 256 条）。如果消费速度跟不上生产速度，超出队列容量的事件会被丢弃并记录警告日志。

**Q: `run_live_message()` 仍然返回 `Transcript` 吗？**
A: 是的。事件流是额外的实时通知通道，不影响返回值语义。

**Q: 为什么 `SKILL_COMPLETED` 里看不到技能结果正文？**
A: `SKILL_COMPLETED` 现在只表达执行状态，不再携带结果预览，避免把内部技能结果重复暴露给外部订阅者。真正面向外部的内容仍应从 assistant 的 `MESSAGE_ADDED` 事件或 `on_reply` 回调中消费。
