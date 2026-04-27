"""Built-in skill for creating timed reminders."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

SKILL_META = {
    "name": "reminder",
    "description": (
        "设置定时提醒，支持一次性、每日、每周重复提醒。"
        "到达指定时间后会通知对应的用户。"
        "例如：'三分钟后叫我吃饭'、'每天早上8点叫我起床'、'每周一提醒开会'。"
        "可以用 list 查看所有提醒，用 cancel 取消指定提醒。"
    ),
    "version": "1.0.0",
    "developer_only": False,
    "dependencies": [],
    "parameters": {
        "action": {
            "type": "str",
            "description": "操作类型: create(创建) / list(查看所有提醒) / cancel(取消指定提醒)",
            "required": True,
        },
        "content": {
            "type": "str",
            "description": "提醒内容，例如'该吃饭啦'、'起床啦'",
            "required": False,
        },
        "mode": {
            "type": "str",
            "description": "触发模式: once(一次性) / daily(每日重复) / weekly(每周重复)",
            "required": False,
            "default": "once",
        },
        "minutes_after": {
            "type": "int",
            "description": "几分钟后触发（仅 once 模式，与 trigger_at 二选一）",
            "required": False,
        },
        "trigger_at": {
            "type": "str",
            "description": "绝对触发时间 ISO 格式（仅 once 模式，与 minutes_after 二选一）",
            "required": False,
        },
        "time": {
            "type": "str",
            "description": "触发时间 HH:MM，例如 08:00、21:30（daily/weekly 模式必填）",
            "required": False,
        },
        "weekday": {
            "type": "int",
            "description": "星期几 0=周一, 1=周二, ..., 6=周日（仅 weekly 模式必填）",
            "required": False,
        },
        "reminder_id": {
            "type": "str",
            "description": "提醒任务ID（cancel 时使用，可通过 list 查看）",
            "required": False,
        },
    },
}


def run(
    action: str = "create",
    content: str = "",
    mode: str = "once",
    minutes_after: int = 0,
    trigger_at: str = "",
    time: str = "",
    weekday: int = -1,
    reminder_id: str = "",
    data_store: Any = None,
    invocation_context: Any = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Create, list, or cancel reminders."""
    action = action.strip().lower()

    caller = invocation_context.caller if invocation_context else None
    user_id = caller.user_id if caller else ""
    user_name = caller.name if caller else ""

    if action == "create":
        return _do_create(
            content=content,
            mode=mode,
            minutes_after=minutes_after,
            trigger_at=trigger_at,
            time=time,
            weekday=weekday,
            user_id=user_id,
            user_name=user_name,
            data_store=data_store,
        )
    if action == "list":
        return _do_list(data_store=data_store)
    if action == "cancel":
        return _do_cancel(
            reminder_id=reminder_id,
            data_store=data_store,
            requester_id=user_id,
        )

    return {
        "success": False,
        "error": f"未知操作: {action}，支持 create/list/cancel",
        "summary": "提醒操作失败：未知操作类型",
    }


def _do_create(
    content: str,
    mode: str,
    minutes_after: int,
    trigger_at: str,
    time: str,
    weekday: int,
    user_id: str,
    user_name: str,
    data_store: Any | None,
) -> dict[str, Any]:
    if not content or not content.strip():
        return {
            "success": False,
            "error": "提醒内容不能为空",
            "summary": "创建提醒失败：内容为空",
        }

    mode = mode.strip().lower()
    if mode not in {"once", "daily", "weekly"}:
        return {
            "success": False,
            "error": f"不支持的触发模式: {mode}，支持 once/daily/weekly",
            "summary": "创建提醒失败：模式不支持",
        }

    now = datetime.now(timezone.utc)
    reminder: dict[str, Any] = {
        "id": f"rem_{uuid.uuid4().hex[:12]}",
        "content": content.strip(),
        "mode": mode,
        "user_id": user_id,
        "user_name": user_name,
        "created_at": now.isoformat(),
        "last_fired_at": None,
        "fire_count": 0,
    }

    if mode == "once":
        if minutes_after and minutes_after > 0:
            fire_at = now + timedelta(minutes=minutes_after)
            reminder["fire_at"] = fire_at.isoformat()
            reminder["minutes_after"] = minutes_after
        elif trigger_at:
            try:
                dt = datetime.fromisoformat(trigger_at.replace("Z", "+00:00"))
                reminder["fire_at"] = dt.isoformat()
            except ValueError:
                return {
                    "success": False,
                    "error": f"触发时间格式错误: {trigger_at}",
                    "summary": "创建提醒失败：时间格式错误",
                }
        else:
            return {
                "success": False,
                "error": "一次性提醒需要指定 minutes_after 或 trigger_at",
                "summary": "创建提醒失败：未指定触发时间",
            }
    elif mode == "daily":
        if not time or not _is_valid_hhmm(time):
            return {
                "success": False,
                "error": "每日提醒需要指定有效的时间 HH:MM",
                "summary": "创建提醒失败：时间格式错误",
            }
        reminder["time"] = time
    elif mode == "weekly":
        if not time or not _is_valid_hhmm(time):
            return {
                "success": False,
                "error": "每周提醒需要指定有效的时间 HH:MM",
                "summary": "创建提醒失败：时间格式错误",
            }
        if weekday < 0 or weekday > 6:
            return {
                "success": False,
                "error": "每周提醒需要指定 weekday (0=周一 ~ 6=周日)",
                "summary": "创建提醒失败：星期参数错误",
            }
        reminder["time"] = time
        reminder["weekday"] = weekday

    _save_reminder(reminder, data_store)

    mode_desc = {"once": "一次性", "daily": "每日", "weekly": "每周"}.get(mode, mode)
    fire_desc = ""
    if mode == "once" and reminder.get("fire_at"):
        fire_desc = f"，将在 {reminder['fire_at']} 触发"
    elif mode in ("daily", "weekly"):
        fire_desc = f"，将在每天 {time} 触发" if mode == "daily" else f"，将在每周{_weekday_name(weekday)} {time} 触发"

    who = f"给 {user_name}" if user_name else ""
    return {
        "success": True,
        "summary": f"已创建{mode_desc}提醒{who}{fire_desc}",
        "text_blocks": [
            f"✅ 已设置提醒（ID: {reminder['id']}）\n"
            f"对象: {user_name or '未指定'}\n"
            f"内容: {reminder['content']}\n"
            f"模式: {mode_desc}{fire_desc}"
        ],
        "internal_metadata": {"reminder_id": reminder["id"]},
    }


def _do_list(data_store: Any | None) -> dict[str, Any]:
    reminders = _load_reminders(data_store)
    if not reminders:
        return {
            "success": True,
            "summary": "当前没有设置任何提醒",
            "text_blocks": ["当前没有待触发的提醒任务。"],
        }

    lines = [f"共 {len(reminders)} 个提醒任务："]
    for r in reminders:
        mode_desc = {"once": "一次性", "daily": "每日", "weekly": "每周"}.get(r["mode"], r["mode"])
        who = f"[{r.get('user_name') or r.get('user_id', '?')}] "
        detail = f"[{r['id']}] {who}{mode_desc} | {r['content']}"
        if r.get("fire_at"):
            detail += f" | 触发: {r['fire_at']}"
        if r.get("time"):
            detail += f" | 时间: {r['time']}"
        if r.get("weekday") is not None:
            detail += f" ({_weekday_name(r['weekday'])}"
        lines.append(detail)

    return {
        "success": True,
        "summary": f"列出 {len(reminders)} 个提醒任务",
        "text_blocks": ["\n".join(lines)],
    }


def _do_cancel(
    reminder_id: str, data_store: Any | None, requester_id: str = ""
) -> dict[str, Any]:
    if not reminder_id:
        return {
            "success": False,
            "error": "取消提醒需要提供 reminder_id",
            "summary": "取消提醒失败：未提供ID",
        }

    reminders = _load_reminders(data_store)
    target = next((r for r in reminders if r.get("id") == reminder_id), None)
    if target is None:
        return {
            "success": False,
            "error": f"未找到提醒任务: {reminder_id}",
            "summary": "取消提醒失败：任务不存在",
        }

    owner_id = target.get("user_id", "")
    if owner_id and requester_id and owner_id != requester_id:
        owner_name = target.get("user_name") or owner_id
        return {
            "success": False,
            "error": f"该提醒由 {owner_name} 创建，只有创建者本人可以取消",
            "summary": "取消提醒失败：权限不足",
        }

    reminders = [r for r in reminders if r.get("id") != reminder_id]
    _store_reminders(reminders, data_store)
    return {
        "success": True,
        "summary": f"已取消提醒任务 {reminder_id}",
        "text_blocks": [f"✅ 已取消提醒任务 {reminder_id}"],
    }


def _is_valid_hhmm(value: str) -> bool:
    try:
        h, m = value.split(":")
        hi, mi = int(h), int(m)
        return 0 <= hi <= 23 and 0 <= mi <= 59
    except Exception:
        return False


def _weekday_name(d: int) -> str:
    names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    return names[d] if 0 <= d <= 6 else str(d)


def _load_reminders(data_store: Any | None) -> list[dict[str, Any]]:
    if data_store is None:
        return []
    return list(data_store.get("reminders", []))


def _store_reminders(reminders: list[dict[str, Any]], data_store: Any | None) -> None:
    if data_store is not None:
        data_store.set("reminders", reminders)


def _save_reminder(reminder: dict[str, Any], data_store: Any | None) -> None:
    reminders = _load_reminders(data_store)
    reminders.append(reminder)
    _store_reminders(reminders, data_store)
