"""First-class NapCat tool for sending QQ likes."""

from __future__ import annotations

import time
from typing import Any

from sirius_pulse.config.config_builder import ConfigBuilder
from sirius_pulse.tools.builtin._internal._qq_ops import (
    bridge_error,
    failure_from_exception,
    get_adapter,
    success_result,
)
from sirius_pulse.tools.models import ToolInvocationContext

_MAX_TIMES = 10

_config = ConfigBuilder()
_config.group("QQ点赞").add(
    "user_id",
    type="int",
    description="目标 QQ 号；留空时默认使用当前发言人的 QQ 号。",
)
_config.group("QQ点赞").add(
    "times",
    type="int",
    description=f"点赞次数，范围 1-{_MAX_TIMES}。",
    default=1,
)
_config.group("QQ点赞").add(
    "reuse_last",
    type="bool",
    description="没有明确目标时复用当前会话上一次成功点赞的目标。",
    default=False,
)

TOOL_META = {
    "name": "qq_like",
    "description": (
        "通过当前已连接的 NapCat 适配器给 QQ 用户点赞。"
        "这是点赞的唯一执行入口；不要为点赞任务使用 bash、curl、WebSocket 脚本或搜索容器配置。"
        "用户明确说‘再来一次’或‘继续上次点赞’且没有新目标时，设置 reuse_last=true。"
    ),
    "version": "1.0.0",
    "retry_safe": False,
    "side_effect": "external_write",
    "tags": ["napcat", "qq", "like", "workflow"],
    "adapter_types": ["napcat"],
    "parameters": _config.build(),
}


async def run(
    user_id: int | None = None,
    times: int = 1,
    reuse_last: bool = False,
    bridge: Any = None,
    chat_context: dict[str, Any] | None = None,
    invocation_context: ToolInvocationContext | None = None,
    data_store: Any = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Send likes through the configured NapCat adapter."""
    adapter = get_adapter(bridge)
    if adapter is None:
        return bridge_error("给 QQ 用户点赞")

    try:
        count = int(times)
    except (TypeError, ValueError):
        return {"success": False, "error": "times 必须是整数"}
    if not 1 <= count <= _MAX_TIMES:
        return {"success": False, "error": f"times 必须在 1-{_MAX_TIMES} 之间"}

    scope = _scope_key(chat_context, invocation_context)
    reused = False
    if user_id is None and reuse_last and data_store is not None:
        previous = data_store.get(f"last_success:{scope}")
        if isinstance(previous, dict):
            user_id = previous.get("user_id")
            reused = user_id not in (None, "")

    if user_id is None:
        user_id = _current_user_id(chat_context, invocation_context)
    try:
        target_id = int(_normalize_user_id(user_id))
    except (TypeError, ValueError):
        return {"success": False, "error": "缺少有效的目标 QQ 号 user_id"}
    if target_id <= 0:
        return {"success": False, "error": "user_id 必须是正整数"}

    try:
        raw = await adapter.call_api(
            "send_like",
            {"user_id": target_id, "times": count},
        )
    except Exception as exc:
        return failure_from_exception("QQ点赞", exc)

    if data_store is not None:
        data_store.set(
            f"last_success:{scope}",
            {
                "user_id": target_id,
                "times": count,
                "action": "send_like",
                "scope": scope,
                "saved_at": time.time(),
            },
        )

    return success_result(
        f"已给 QQ {target_id} 点赞 {count} 次",
        text=f"已完成 QQ 点赞：{target_id}（{count} 次）",
        user_id=target_id,
        times=count,
        reused_last=reused,
        scope=scope,
        raw=raw,
    )


def _current_user_id(
    chat_context: dict[str, Any] | None,
    invocation_context: ToolInvocationContext | None,
) -> str:
    if invocation_context is not None and invocation_context.caller_user_id:
        return invocation_context.caller_user_id
    return str((chat_context or {}).get("user_id") or "").strip()


def _normalize_user_id(user_id: Any) -> str:
    value = str(user_id or "").strip()
    if value.lower().startswith("qq_"):
        value = value[3:]
    return value


def _scope_key(
    chat_context: dict[str, Any] | None,
    invocation_context: ToolInvocationContext | None,
) -> str:
    context = chat_context or {}
    chat_type = str(context.get("chat_type") or "unknown").strip()
    chat_id = str(context.get("chat_id") or context.get("group_id") or "").strip()
    if not chat_id and invocation_context is not None:
        chat_id = invocation_context.caller_user_id
    return f"{chat_type}:{chat_id or 'global'}"
