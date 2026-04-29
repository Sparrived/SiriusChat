"""Built-in NapCat-specific skill for sending images to groups or private chats."""

from __future__ import annotations

from pathlib import Path
from typing import Any

SKILL_META = {
    "name": "send_image",
    "description": "发送图片到指定的 QQ 群聊或私聊。仅支持 NapCat 平台。",
    "version": "1.0.0",
    "tags": ["napcat", "image", "messaging"],
    "adapter_types": ["napcat"],
    "dependencies": [],
    "parameters": {
        "target_type": {
            "type": "str",
            "description": "目标类型：group（群聊）或 private（私聊）",
            "required": True,
        },
        "target_id": {
            "type": "str",
            "description": "目标群号或 QQ 号",
            "required": True,
        },
        "image_path": {
            "type": "str",
            "description": "本地图片绝对路径或网络图片 URL",
            "required": True,
        },
        "caption": {
            "type": "str",
            "description": "图片附带的文字说明（可选）",
            "required": False,
        },
    },
}


async def run(
    bridge: Any,
    target_type: str = "",
    target_id: str = "",
    image_path: str = "",
    caption: str = "",
    **kwargs: Any,
) -> dict[str, Any]:
    """Send an image to a group or private chat via NapCat.

    Args:
        bridge: The NapCatBridge instance injected by SkillExecutor.
        target_type: "group" or "private".
        target_id: Group ID or user QQ number.
        image_path: Absolute path to a local image or a remote URL.
        caption: Optional text caption to send before the image.
    """
    if not bridge:
        return {
            "success": False,
            "error": "bridge 未就绪，无法发送图片",
            "summary": "发送失败：平台桥接未初始化",
        }

    adapter = getattr(bridge, "adapter", None)
    if adapter is None:
        return {
            "success": False,
            "error": "adapter 未就绪",
            "summary": "发送失败：NapCat 适配器未连接",
        }

    target_type = (target_type or "").strip().lower()
    target_id = (target_id or "").strip()
    image_path = (image_path or "").strip()

    if target_type not in ("group", "private"):
        return {
            "success": False,
            "error": f"无效的目标类型: {target_type}，必须为 group 或 private",
            "summary": "发送失败：目标类型错误",
        }
    if not target_id:
        return {
            "success": False,
            "error": "target_id 不能为空",
            "summary": "发送失败：缺少目标 ID",
        }
    if not image_path:
        return {
            "success": False,
            "error": "image_path 不能为空",
            "summary": "发送失败：缺少图片路径",
        }

    # Normalize local paths to absolute on Windows
    if "://" not in image_path and not image_path.startswith("file://"):
        p = Path(image_path)
        if p.exists():
            image_path = str(p.resolve())

    msg: list[dict[str, Any]] = []
    caption = caption.strip()
    if caption:
        msg.append({"type": "text", "data": {"text": caption}})
    msg.append({"type": "image", "data": {"file": image_path}})

    try:
        if target_type == "group":
            result = await adapter.send_group_msg(target_id, msg)
        else:
            result = await adapter.send_private_msg(target_id, msg)

        data = result.get("data", {}) if isinstance(result, dict) else {}
        return {
            "success": True,
            "summary": f"图片已发送到 {target_type} {target_id}",
            "text_blocks": [f"图片发送成功: {image_path}"],
            "internal_metadata": {
                "target_type": target_type,
                "target_id": target_id,
                "message_id": data.get("message_id") if isinstance(data, dict) else None,
            },
        }
    except Exception as exc:
        return {
            "success": False,
            "error": str(exc),
            "summary": f"图片发送失败: {exc}",
        }
