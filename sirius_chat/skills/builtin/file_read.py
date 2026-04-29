"""Built-in skill for reading files within the workspace."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from sirius_chat.skills.models import SkillInvocationContext
from sirius_chat.skills.security import ensure_developer_access

SKILL_META = {
    "name": "file_read",
    "description": (
        "读取任意路径下的文本文件内容，供模型分析代码、配置或日志。"
        "支持绝对路径和相对路径，仅支持 UTF-8 编码的文本文件，自动拒绝二进制文件。"
    ),
    "version": "1.0.0",
    "tags": ["file", "io"],
    "developer_only": False,
    "dependencies": [],
    "parameters": {
        "path": {
            "type": "str",
            "description": "文件路径，支持相对路径或绝对路径，例如 docs/README.md、D:/notes.txt、/etc/passwd。注意：路径中的空格是有意义的，请严格按照实际路径填写，不要擅自添加或删除空格",
            "required": True,
        },
    },
}

# 拒绝访问的路径模式（大小写不敏感）
_DENY_PATTERNS = (
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    ".env",
    ".ssh",
    ".aws",
)
_MAX_SIZE_BYTES = 1 * 1024 * 1024  # 1 MB


def run(
    path: str = "",
    data_store: Any = None,
    invocation_context: SkillInvocationContext | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    if not path or not path.strip():
        return {
            "success": False,
            "error": "path 不能为空",
            "summary": "文件读取失败：未提供路径",
        }

    raw_path = path.strip()
    target = _resolve_read_path(raw_path)
    if target is None:
        return {
            "success": False,
            "error": f"路径 '{path}' 包含非法遍历或命中黑名单目录",
            "summary": "文件读取失败：路径被拒绝",
        }

    # Fallback: if path doesn't exist, try common LLM mis-formatting variants
    # (e.g. model inserts space between CJK chars and digits)
    if not target.exists():
        alt = Path(raw_path.replace(" ", "")).resolve()
        if alt != target and alt.exists():
            import logging
            logging.getLogger(__name__).debug(
                "file_read 路径回退: 原始路径 '%s' 不存在，使用修正路径 '%s'",
                target,
                alt,
            )
            target = alt
        else:
            return {
                "success": False,
                "error": f"文件不存在: {target}",
                "summary": "文件读取失败：文件不存在",
            }

    if target.is_dir():
        # List directory contents instead of erroring
        try:
            entries = []
            for entry in sorted(target.iterdir()):
                entry_str = f"{entry.name}/" if entry.is_dir() else entry.name
                entries.append(entry_str)
            return {
                "success": True,
                "summary": f"'{path}' 是一个目录，共 {len(entries)} 项",
                "text_blocks": ["\n".join(entries)],
                "internal_metadata": {
                    "path": str(target),
                    "is_directory": True,
                    "entry_count": len(entries),
                },
            }
        except OSError as exc:
            return {
                "success": False,
                "error": f"无法列出目录: {exc}",
                "summary": "文件读取失败：目录访问错误",
            }

    # Size guard
    try:
        size = target.stat().st_size
    except OSError as exc:
        return {
            "success": False,
            "error": f"无法获取文件大小: {exc}",
            "summary": "文件读取失败：文件访问错误",
        }

    if size > _MAX_SIZE_BYTES:
        return {
            "success": False,
            "error": (
                f"文件过大 ({size / 1024 / 1024:.2f} MB)，"
                f"超过限制 {_MAX_SIZE_BYTES / 1024 / 1024:.0f} MB"
            ),
            "summary": "文件读取失败：文件过大",
        }

    # Detect image files for multimodal delivery
    mime_type = _guess_image_mime(target.name)
    if mime_type:
        return {
            "success": True,
            "summary": f"已读取图片 '{path}'（{size} 字节）",
            "text_blocks": [f"[图片] {path} — 已通过多模态通道发送给模型分析"],
            "multimodal_blocks": [
                {
                    "type": "image",
                    "label": "local_image",
                    "value": str(target),
                    "mime_type": mime_type,
                }
            ],
            "internal_metadata": {
                "path": str(target),
                "size_bytes": size,
                "mime_type": mime_type,
            },
        }

    # Binary guard: try to read as text
    try:
        raw = target.read_bytes()
        if b"\x00" in raw:
            return {
                "success": False,
                "error": "检测到二进制文件，拒绝读取",
                "summary": "文件读取失败：二进制文件",
            }
        content = raw.decode("utf-8", errors="replace")
    except OSError as exc:
        return {
            "success": False,
            "error": f"读取文件失败: {exc}",
            "summary": "文件读取失败：IO 错误",
        }

    return {
        "success": True,
        "summary": f"已读取 '{path}'（{size} 字节，约 {content.count(chr(10)) + 1} 行）",
        "text_blocks": [content],
        "internal_metadata": {
            "path": str(target),
            "size_bytes": size,
            "line_count": content.count("\n") + 1,
        },
    }


def _resolve_read_path(user_path: str) -> Path | None:
    """Resolve a path for file reading with minimal safety checks.

    Operates at the OS level: any path (absolute, relative, including '..')
    is allowed except for deny-listed sensitive directories.
    """
    raw = user_path.strip()
    if not raw:
        return None

    target = Path(raw).resolve()

    deny_set = {d.lower() for d in _DENY_PATTERNS}
    for part in target.parts:
        if part.lower() in deny_set:
            return None

    return target


def _guess_image_mime(filename: str) -> str | None:
    """Return MIME type for known image extensions, or None."""
    ext = Path(filename).suffix.lower()
    mapping = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".bmp": "image/bmp",
        ".webp": "image/webp",
        ".ico": "image/x-icon",
        ".tiff": "image/tiff",
        ".svg": "image/svg+xml",
    }
    return mapping.get(ext)
