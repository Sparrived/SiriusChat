# run_live_session 破坏性变更迁移指南（给 AI/自动化代理）

## 变更摘要

从当前版本开始：

- `AsyncRolePlayEngine.run_live_session(...)` 仅用于 **会话初始化**。
- `run_live_session` 不再接收 `human_turns`，也不再处理消息输入输出。
- 新增/推荐使用 `AsyncRolePlayEngine.run_live_message(...)` 处理 **单条** `Message`。

## 旧写法（已废弃）

```python
transcript = await engine.run_live_session(
    config=config,
    human_turns=[Message(role="user", speaker="小王", content="hello")],
    transcript=transcript,
)
```

## 新写法（必须）

```python
# 1) 只初始化一次会话
transcript = await engine.run_live_session(
    config=config,
    transcript=transcript,
)

# 2) 每次仅处理一条外部输入
transcript = await engine.run_live_message(
    config=config,
    transcript=transcript,
    turn=Message(role="user", speaker="小王", content="hello"),
)
```

## 标准接入流程（流式/实时系统）

```python
transcript = await engine.run_live_session(config=config)

for incoming in incoming_stream:
    transcript = await engine.run_live_message(
        config=config,
        transcript=transcript,
        turn=Message(
            role="user",
            speaker=incoming.speaker,
            content=incoming.content,
            channel=incoming.channel,
            channel_user_id=incoming.channel_user_id,
        ),
    )
```

## 会话级回复策略

`run_live_message` 默认采用会话级配置：

- `config.orchestration.session_reply_mode = "always" | "never" | "auto"`

如果你在迁移旧逻辑时仍需要按消息覆盖，可传：

- `session_reply_mode=turn.reply_mode`

## AI 迁移规则（可直接用于代码改写）

1. 查找：`run_live_session(` 且包含 `human_turns=`。
2. 拆分为两步：
   - 先补一行 `transcript = await engine.run_live_session(config=..., transcript=...)`
   - 再将每条 `human_turns` 中的消息改为 `run_live_message(..., turn=...)`
3. 若 `human_turns` 是列表循环：将原单调用改为 `for turn in human_turns:` 循环调用 `run_live_message`。
4. 保留原有 `transcript` 变量并持续复用，确保上下文与 `reply_runtime` 连续。
5. 不要再向 `run_live_session` 传 `human_turns`。

## 常见问题

- Q: 为什么要拆分？
- A: 让引擎契约更清晰：`run_live_session` 管初始化，`run_live_message` 管单条 I/O；更适合外部实时系统逐条推送。

- Q: 会不会丢失 auto_reply 的节奏状态？
- A: 不会。节奏状态挂载在 `Transcript.reply_runtime`，复用同一 transcript 即可跨调用持续生效。
