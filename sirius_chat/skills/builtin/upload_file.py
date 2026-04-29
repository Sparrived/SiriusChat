"""Built-in NapCat-specific skill for uploading files to groups or private chats."""

from __future__ import annotations

from pathlib import Path
from typing import Any

SKILL_META = {
    "name": "upload_file",
    "description": "上传本地文件到指定的 QQ 群聊或私聊。仅支持 NapCat 平台。",
    "version": "1.0.0",
    "tags": ["napcat", "file", "messaging"],
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
        "file_path": {
            "type": "str",
            "description": "本地文件绝对路径",
            "required": True,
        },
        "file_name": {
            "type": "str",
            "description": "在聊天中显示的文件名（不传则使用原文件名）",
            "required": False,
        },
    },
}


async def run(
    bridge: Any,
    target_type: str = "",
    target_id: str = "",
    file_path: str = "",
    file_name: str = "",
    **kwargs: Any,
) -> dict[str, Any]:
    """Upload a file to a group or private chat via NapCat.

    Args:
        bridge: The NapCatBridge instance injected by SkillExecutor.
        target_type: "group" or "private".
        target_id: Group ID or user QQ number.
        file_path: Absolute path to a local file.
        file_name: Optional display name for the uploaded file.
    """
    if not bridge:
        return {
            "success": False,
            "error": "bridge 未就绪，无法上传文件",
            "summary": "上传失败：平台桥接未初始化",
        }

    adapter = getattr(bridge, "adapter", None)
    if adapter is None:
        return {
            "success": False,
            "error": "adapter 未就绪",
            "summary": "上传失败：NapCat 适配器未连接",
        }

    target_type = (target_type or "").strip().lower()
    target_id = (target_id or "").strip()
    file_path = (file_path or "").strip()

    if target_type not in ("group", "private"):
        return {
            "success": False,
            "error": f"无效的目标类型: {target_type}，必须为 group 或 private",
            "summary": "上传失败：目标类型错误",
        }
    if not target_id:
        return {
            "success": False,
            "error": "target_id 不能为空",
            "summary": "上传失败：缺少目标 ID",
        }
    if not file_path:
        return {
            "success": False,
            "error": "file_path 不能为空",
            "summary": "上传失败：缺少文件路径",
        }

    p = Path(file_path)
    if not p.exists():
        return {
            "success": False,
            "error": f"文件不存在: {file_path}",
            "summary": "上传失败：文件不存在",
        }

    resolved_path = str(p.resolve())
    display_name = (file_name or "").strip() or p.name

    try:
        if target_type == "group":
            result = await adapter.upload_group_file(target_id, resolved_path, display_name)
        else:
            result = await adapter.upload_private_file(target_id, resolved_path, display_name)

        data = result.get("data", {}) if isinstance(result, dict) else {}
        return {
            "success": True,
            "summary": f"文件「{display_name}」已上传到 {target_type} {target_id}",
            "text_blocks": [f"文件上传成功: {resolved_path}"],
            "internal_metadata": {
                "target_type": target_type,
                "target_id": target_id,
                "file_name": display_name,
                "message_id": data.get("message_id") if isinstance(data, dict) else None,
            },
        }
    except Exception as exc:
        return {
            "success": False,
            "error": str(exc),
            "summary": f"文件上传失败: {exc}",
        }
