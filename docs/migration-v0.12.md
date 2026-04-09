# 迁移指南：v0.11.x → v0.12.0

## 概述

v0.12.0 的核心变更是 **`arun_live_message` 参数增强**：新增 `on_reply`、`user_profile` 和 `timeout` 三个可选参数。这些参数将之前需要外部手动实现的常见模式（事件流订阅、用户注册、超时处理）内化到引擎中，大幅减少外部集成的样板代码。

此外，修复了 `timeout` 与消息 debounce 机制的交互问题——旧版本中 debounce 路径会吞掉 `CancelledError`，导致 `timeout` 无法生效。

**向后兼容**：所有新参数均为可选且有默认值，现有代码无需修改即可升级。

---

## 破坏性变更

无。所有变更均向后兼容。

---

## 新增参数

### `on_reply: Callable[[Message], Awaitable[None]] | None`

当提供 `on_reply` 回调时，引擎内部自动：
1. 订阅事件流（`subscribe`）
2. 为每条 assistant `MESSAGE_ADDED` 事件调用回调
3. 等待 `PROCESSING_COMPLETED` / `REPLY_SKIPPED` / `ERROR` 后退出
4. 清理 consume task（含超时/异常场景）

**迁移前**（~50 行样板代码）：

```python
async def _consume_events():
    async for evt in asubscribe(engine, transcript):
        if evt.type in (PROCESSING_COMPLETED, REPLY_SKIPPED, ERROR):
            break
        if evt.type == MESSAGE_ADDED and msg.role == "assistant":
            await send_to_external(msg.content)

consume_task = asyncio.create_task(_consume_events())
await asyncio.sleep(0)
try:
    transcript = await asyncio.wait_for(
        arun_live_message(engine, config, turn, transcript), timeout=45,
    )
except asyncio.TimeoutError:
    consume_task.cancel(); ...
try:
    await asyncio.wait_for(consume_task, timeout=120)
except asyncio.TimeoutError:
    consume_task.cancel(); ...
```

**迁移后**（~5 行）：

```python
async def on_reply(msg: Message) -> None:
    await send_to_external(msg.content)

transcript = await arun_live_message(
    engine, config, turn, transcript,
    on_reply=on_reply,
    timeout=45,
)
```

### `user_profile: UserProfile | None`

提供时，引擎在处理消息前自动调用 `transcript.user_memory.register_user(user_profile)`。

**迁移前**：

```python
profile = UserProfile(user_id="qq_123", name="用户A", identities={"qq": "123"})
transcript.user_memory.register_user(profile)
transcript = await arun_live_message(engine, config, turn, transcript)
```

**迁移后**：

```python
transcript = await arun_live_message(
    engine, config, turn, transcript,
    user_profile=UserProfile(user_id="qq_123", name="用户A", identities={"qq": "123"}),
)
```

> 注意：如果你在注册后还需要更新 alias 或 name（如 QQ 群名片与昵称不同），仍需手动操作 `transcript.user_memory.entries[user_id]`。

### `timeout: float`

提供正值时，引擎用 `asyncio.wait_for` 包裹整个处理流程。超时后抛出 `asyncio.TimeoutError`，引擎负责清理（包括 `on_reply` consume task 的取消）。

```python
try:
    transcript = await arun_live_message(
        engine, config, turn, transcript, timeout=45.0,
    )
except asyncio.TimeoutError:
    print("处理超时")
```

---

## Bug 修复：debounce 与 timeout 交互

v0.11.x 中，当 `message_debounce_seconds > 0`（默认 5.0）时，debounce 路径的 `except asyncio.CancelledError: return transcript` 会吞掉外部 `asyncio.wait_for` 的取消信号，导致 `timeout` 参数实际无效。

v0.12.0 已修复此问题：debounce sleep 不再捕获 `CancelledError`，外部超时可以正确传播。

---

## `asubscribe` 仍然可用

`on_reply` 是面向常见用例的便捷 API。如果你需要监听更多事件类型（如 `SKILL_STARTED`、`PROCESSING_STARTED`）或自定义消费逻辑，仍然可以使用 `asubscribe` 原始事件流。

---

## 版本要求

- `sirius-chat >= 0.12.0`
- Python >= 3.12
