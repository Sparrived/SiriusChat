---
name: ncatbot-env
description: '使用 NcatBot SDK 构建实际 QQ 群聊环境，将真实群聊消息接入 sirius_chat 引擎。涵盖：NapCat 连接配置、非插件模式启动、群消息接收与转发、群成员信息获取、engine 输出回写 QQ 群。Use when: ncatbot、QQ 群聊、实际环境、群消息接入、NapCat、BotClient、群成员列表、发送群消息、桥接 sirius_chat、真实群聊测试、生产环境部署。'
---

# NcatBot 实际群聊环境构建指南

NcatBot 是基于 NapCat 的 Python SDK，用于构建真实连接 QQ 的 Bot。本 skill 聚焦：**将 ncatbot 作为 QQ 群聊适配器，接入 sirius_chat 的 EmotionalGroupChatEngine，构建真实群聊环境**。

---

## 核心判断

| 你要做什么 | 读哪一节 |
|-----------|---------|
| 首次搭建 NapCat + ncatbot 环境 | §环境搭建 |
| 启动一个真实 QQ Bot（非插件模式） | §最小启动代码 |
| 把 QQ 群消息接入 engine | §群消息桥接 |
| 获取群成员信息送入 engine | §群成员信息获取 |
| 把 engine 输出发回 QQ 群 | §消息回写 |
| 完整集成示例 | §完整集成示例 |
| 实际运行与监控 | §运行与监控 |

---

## 环境搭建

### 安装

```bash
pip install ncatbot5
```

### 项目目录结构

```
my_bot/
├── config.yaml          # ncatbot 配置（必填）
├── main.py              # 启动入口
├── work_path/           # sirius_chat 工作区
│   ├── roleplay/
│   ├── sessions/
│   └── ...
└── logs/
```

### config.yaml 配置

```yaml
bot_uin: "123456789"              # 机器人 QQ 号
root: "987654321"                 # 管理员 QQ 号
debug: true                       # 调试模式
napcat:
  ws_uri: ws://localhost:3001     # NapCat WebSocket 地址
  ws_token: napcat_ws             # WebSocket Token
  webui_uri: http://localhost:6099
  webui_token: napcat_webui
  enable_webui: true
plugin:
  plugins_dir: plugins
  load_plugin: false              # 非插件模式：关闭插件加载
```

> NapCat 由 NcatBot Setup 模式（首次 `ncatbot run`）自动安装。启动后通过 WebUI（默认 http://localhost:6099）扫码登录 QQ。

---

## 最小启动代码

### 非插件模式（推荐，与 sirius_chat engine 直接集成）

```python
from ncatbot.app import BotClient
from ncatbot.core import registrar
from ncatbot.event.qq import GroupMessageEvent
from ncatbot.utils import get_log

log = get_log("sirius_bridge")
bot = BotClient()

@registrar.on_group_message()
async def on_group_msg(event: GroupMessageEvent):
    """群消息入口：接收消息 → 送入 engine → 发送回复"""
    group_id = str(event.group_id)
    user_id = str(event.user_id)
    content = event.data.raw_message

    log.info("[%s] %s: %s", group_id, user_id, content)

    # TODO: 送入 sirius_chat engine 处理
    # reply = await engine.process_message(...)

    # 发送回复
    # await event.reply(reply_text)

if __name__ == "__main__":
    bot.run()
```

> 非插件模式无需 `plugins/` 目录和 `manifest.toml`，适合作为 engine 的外部适配器。

### 运行方式

```bash
# 开发模式（热重载 + 调试日志）
ncatbot dev

# 生产模式
ncatbot run

# 或直接用 Python
python main.py
```

---

## 群消息桥接

### 消息数据映射

ncatbot 的 `GroupMessageEvent` → sirius_chat 的 `Message`：

```python
from sirius_chat.models.models import Message, Participant
from ncatbot.event.qq import GroupMessageEvent

def map_qq_event_to_message(event: GroupMessageEvent) -> tuple[Message, str]:
    """将 QQ 群消息事件映射为 sirius_chat Message 和 group_id"""
    return (
        Message(
            role="human",
            content=event.data.raw_message,
            speaker=str(event.user_id),
        ),
        str(event.group_id),
    )

def map_qq_event_to_participant(event: GroupMessageEvent) -> Participant:
    """将 QQ 群消息发送者映射为 Participant"""
    sender = event.data.sender
    nickname = getattr(sender, "nickname", "")
    return Participant(
        name=nickname or str(event.user_id),
        user_id=str(event.user_id),
    )
```

### 桥接 handler

```python
import asyncio
from ncatbot.app import BotClient
from ncatbot.core import registrar
from ncatbot.event.qq import GroupMessageEvent

bot = BotClient()

# engine 实例（全局或单例）
engine: EmotionalGroupChatEngine | None = None

async def init_engine(work_path: str):
    """初始化 sirius_chat engine"""
    global engine
    from sirius_chat.core.emotional_engine import EmotionalGroupChatEngine
    engine = EmotionalGroupChatEngine(work_path=work_path)

@registrar.on_group_message()
async def on_group_msg(event: GroupMessageEvent):
    if engine is None:
        return

    group_id = str(event.group_id)
    message = Message(
        role="human",
        content=event.data.raw_message,
        speaker=str(event.user_id),
    )
    participant = Participant(
        name=getattr(event.data.sender, "nickname", str(event.user_id)),
        user_id=str(event.user_id),
    )

    # 送入 engine 处理
    result = await engine.process_message(
        message=message,
        participants=[participant],
        group_id=group_id,
    )

    # engine 决定回复 → 发回 QQ 群
    if result.get("reply"):
        await event.reply(result["reply"])
```

---

## 群成员信息获取

在 engine 处理前，获取群成员列表以构建 `participants`：

```python
async def get_group_members(group_id: str) -> list[Participant]:
    """通过 ncatbot API 获取群成员列表"""
    try:
        members = await bot.api.qq.query.get_group_member_list(group_id)
        return [
            Participant(
                name=m.get("card", "") or m.get("nickname", ""),
                user_id=str(m["user_id"]),
            )
            for m in members
        ]
    except Exception as e:
        log.warning("获取群 %s 成员失败: %s", group_id, e)
        return []
```

**优化**：首次获取后缓存成员信息，避免每次消息都调用 API。

```python
_group_members_cache: dict[str, list[Participant]] = {}

async def get_cached_group_members(group_id: str) -> list[Participant]:
    if group_id not in _group_members_cache:
        _group_members_cache[group_id] = await get_group_members(group_id)
    return _group_members_cache[group_id]
```

---

## 消息回写

engine 输出 → QQ 群消息的多种发送方式：

### 方式 1：event.reply()（最简单）

```python
# 在 handler 中直接回复
await event.reply(engine_reply_text)

# 带 @ 发送
await event.reply(text=engine_reply_text, at=event.user_id)

# 发送图片
await event.reply(text="看图", image="path/to/image.jpg")
```

### 方式 2：Sugar API（更灵活）

```python
# 发送纯文本
await bot.api.qq.send_group_text(group_id, engine_reply_text)

# 发送带 @ 的消息
await bot.api.qq.post_group_msg(group_id, text=engine_reply_text, at=user_id)

# 发送图片
await bot.api.qq.send_group_image(group_id, "https://example.com/pic.jpg")

# 发送富文本（MessageArray）
from ncatbot.types import MessageArray
msg = MessageArray().add_at(user_id).add_text(engine_reply_text)
await bot.api.qq.post_group_array_msg(group_id, msg)
```

### 方式 3：主动发送（非事件触发的回复）

```python
# engine 的 proactive_check 返回需要主动发送的消息
async def proactive_sender():
    while True:
        await asyncio.sleep(30)
        for group_id in monitored_groups:
            result = await engine.proactive_check(group_id)
            if result and result.get("reply"):
                await bot.api.qq.send_group_text(group_id, result["reply"])
```

---

## 完整集成示例

```python
"""
ncatbot + sirius_chat 完整集成示例
功能：
  - 接收 QQ 群消息，送入 EmotionalGroupChatEngine 处理
  - 把 engine 的回复发回 QQ 群
  - 支持 proactive 主动触发
  - 支持多群隔离
"""

import asyncio
from pathlib import Path

from ncatbot.app import BotClient
from ncatbot.core import registrar
from ncatbot.event.qq import GroupMessageEvent
from ncatbot.utils import get_log

from sirius_chat.core.emotional_engine import EmotionalGroupChatEngine
from sirius_chat.models.models import Message, Participant

log = get_log("sirius_bridge")

# ============ 配置 ============
WORK_PATH = Path("./work_path")

# ============ Engine 初始化 ============
engine = EmotionalGroupChatEngine(work_path=WORK_PATH)

# 群成员缓存
_group_members_cache: dict[str, list[Participant]] = {}

async def refresh_group_members(group_id: str) -> list[Participant]:
    """刷新并缓存群成员列表"""
    try:
        members = await bot.api.qq.query.get_group_member_list(group_id)
        participants = [
            Participant(
                name=m.get("card", "") or m.get("nickname", ""),
                user_id=str(m["user_id"]),
            )
            for m in members
        ]
        _group_members_cache[group_id] = participants
        return participants
    except Exception as e:
        log.warning("获取群 %s 成员失败: %s", group_id, e)
        return []

async def get_group_participants(group_id: str) -> list[Participant]:
    """获取群成员（带缓存）"""
    if group_id not in _group_members_cache:
        await refresh_group_members(group_id)
    return _group_members_cache.get(group_id, [])

# ============ 消息处理 ============

@registrar.on_group_message()
async def on_group_msg(event: GroupMessageEvent):
    """群消息入口"""
    group_id = str(event.group_id)
    user_id = str(event.user_id)
    content = event.data.raw_message

    log.info("[%s] %s: %s", group_id, user_id, content[:100])

    # 构建 Message
    message = Message(
        role="human",
        content=content,
        speaker=user_id,
    )

    # 获取群成员（用于 engine 的 participants 上下文）
    participants = await get_group_participants(group_id)

    # 送入 engine
    try:
        result = await engine.process_message(
            message=message,
            participants=participants,
            group_id=group_id,
        )
    except Exception as e:
        log.error("Engine 处理失败: %s", e)
        return

    strategy = result.get("strategy")
    reply = result.get("reply")

    log.info("[%s] strategy=%s reply=%s", group_id, strategy, reply[:50] if reply else None)

    # IMMEDIATE / DELAYED → 发送回复
    if reply and strategy in ("immediate", "delayed"):
        await event.reply(reply)

    # SILENT → engine 已内部记录，无需发送
    # PROACTIVE → 由后台任务主动发送

# ============ 后台任务：proactive 检查 ============

async def proactive_checker():
    """定期检查是否需要主动发言"""
    await asyncio.sleep(10)  # 等 engine 初始化完成
    while True:
        await asyncio.sleep(60)
        for group_id in list(_group_members_cache.keys()):
            try:
                result = await engine.proactive_check(group_id)
                if result and result.get("reply"):
                    await bot.api.qq.send_group_text(group_id, result["reply"])
                    log.info("[%s] proactive: %s", group_id, result["reply"][:50])
            except Exception as e:
                log.warning("Proactive check failed for %s: %s", group_id, e)

# ============ 启动 ============

bot = BotClient()

async def main():
    # 启动后台任务
    asyncio.create_task(proactive_checker())

    # 启动 ncatbot（阻塞）
    bot.run()

if __name__ == "__main__":
    asyncio.run(main())
```

---

## 运行与监控

### 启动前检查

```bash
# 检查配置
ncatbot config check

# 检查 NapCat 连接
ncatbot napcat diagnose

# 查看完整配置
ncatbot config show
```

### 日志监控

```powershell
# 启用完整调试日志
$env:LOG_LEVEL = "DEBUG"
ncatbot run --debug

# 实时查看日志
Get-Content logs\bot.log -Tail 100 -Wait
```

### 常见启动问题

| 症状 | 原因 | 修复 |
|------|------|------|
| WebSocket 连接失败 | NapCat 未启动 / ws_uri 错误 | 确认 NapCat 进程运行，检查 `ws://` 非 `http://` |
| QQ 未登录 | 未扫码 | 访问 WebUI（默认 6099 端口）扫码 |
| 收不到群消息 | Bot 不在群内 / 被禁言 | 确认 Bot QQ 已加入目标群 |
| engine 回复未发送 | `strategy` 为 SILENT | 检查 engine 决策逻辑 |

---

## 快速参考

### 核心导入

```python
from ncatbot.app import BotClient              # 应用入口
from ncatbot.core import registrar              # 全局事件注册器
from ncatbot.event.qq import GroupMessageEvent  # QQ 群消息事件
from ncatbot.utils import get_log               # 日志
from ncatbot.types import MessageArray, At      # 消息构造
```

### 事件装饰器速查

```python
@registrar.on_group_message()                   # 群消息
@registrar.on_private_message()                 # 私聊消息
@registrar.on_group_command("cmd")              # 群命令
@registrar.qq.on_group_increase()               # 群成员增加
@registrar.qq.on_group_decrease()               # 群成员减少
```

### API 速查

```python
# 消息发送
await event.reply("文本")
await bot.api.qq.send_group_text(group_id, "文本")
await bot.api.qq.post_group_msg(group_id, text="文本", at=user_id)

# 群管理
members = await bot.api.qq.query.get_group_member_list(group_id)
info = await bot.api.qq.query.get_group_info(group_id)

# 查询
login_info = await bot.api.qq.query.get_login_info()
```

### engine 集成要点

| ncatbot 侧 | sirius_chat 侧 | 说明 |
|-----------|---------------|------|
| `event.group_id` | `group_id` | 群隔离键 |
| `event.user_id` | `Message.speaker` | 用户标识 |
| `event.data.raw_message` | `Message.content` | 消息内容 |
| `event.data.sender.nickname` | `Participant.name` | 用户昵称 |
| `event.reply()` | `result["reply"]` | 回复发送 |
| `bot.api.qq.query.*` | `participants` 构建 | 群成员信息 |

---

## 完整参考索引

| 需要了解 | 查阅位置 |
|---------|---------|
| CLI 命令、config 模板、NapCat 安装 | `ncatbot_env/skills/framework-usage/references/getting-started.md` |
| 事件系统完整装饰器列表 | `ncatbot_env/skills/framework-usage/references/events.md` |
| 消息构造与发送（MessageArray、Sugar API） | `ncatbot_env/skills/framework-usage/references/messaging.md` |
| Bot API 完整签名（群管理、查询、文件） | `ncatbot_env/skills/framework-usage/references/bot-api.md` |
| 调试排错、日志系统、启动序列 | `ncatbot_env/skills/framework-usage/references/troubleshooting.md` |
| sirius_chat engine 架构与 API | `.github/skills/framework-quickstart/SKILL.md` |
