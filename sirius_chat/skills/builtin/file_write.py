"""Built-in skill for writing files within the workspace."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from sirius_chat.skills.models import SkillInvocationContext
from sirius_chat.skills.security import ensure_developer_access

SKILL_META = {
    "name": "file_write",
    "description": (
        "在任意路径下创建或修改文本文件。支持写入新文件或追加到现有文件末尾。"
        "支持绝对路径和相对路径，仅允许操作 UTF-8 文本文件，禁止覆盖二进制文件。"
    ),
    "version": "1.0.0",
    "developer_only": True,
    "dependencies": [],
    "parameters": {
        "path": {
            "type": "str",
            "description": "文件路径，支持相对路径或绝对路径，例如 notes.md、D:/config.json、/etc/hosts",
            "required": True,
        },
        "content": {
            "type": "str",
            "description": "要写入的文本内容",
            "required": True,
        },
        "mode": {
            "type": "str",
            "description": "写入模式：'write' 覆盖写入（默认），'append' 追加到末尾",
            "required": False,
            "default": "write",
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
_MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB — refuse to overwrite existing files larger than this
_MAX_WRITE_SIZE_BYTES = 1 * 1024 * 1024  # 1 MB — refuse to write content larger than this


def run(
    path: str = "",
    content: str = "",
    mode: str = "write",
    data_store: Any = None,
    invocation_context: SkillInvocationContext | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    ensure_developer_access(
        skill_name="file_write",
        invocation_context=invocation_context,
    )

    if not path or not path.strip():
        return {
            "success": False,
            "error": "path 不能为空",
            "summary": "文件写入失败：未提供路径",
        }

    target = _resolve_write_path(path.strip())
    if target is None:
        return {
            "success": False,
            "error": f"路径 '{path}' 包含非法遍历或命中黑名单目录",
            "summary": "文件写入失败：路径被拒绝",
        }

    # Reject writing to directories
    if target.exists() and target.is_dir():
        return {
            "success": False,
            "error": f"目标路径是一个目录，无法写入: {target}",
            "summary": "文件写入失败：目标是目录",
        }

    # Guard against overwriting large existing files
    if target.exists() and mode.lower() == "write":
        try:
            existing_size = target.stat().st_size
        except OSError as exc:
            return {
                "success": False,
                "error": f"无法检查现有文件: {exc}",
                "summary": "文件写入失败：无法访问目标文件",
            }
        if existing_size > _MAX_FILE_SIZE_BYTES:
            return {
                "success": False,
                "error": (
                    f"现有文件过大 ({existing_size / 1024 / 1024:.2f} MB)，"
                    f"超过安全覆盖限制 {_MAX_FILE_SIZE_BYTES / 1024 / 1024:.0f} MB，拒绝覆盖"
                ),
                "summary": "文件写入失败：现有文件过大",
            }

    # Guard write content size
    content_bytes = content.encode("utf-8")
    if len(content_bytes) > _MAX_WRITE_SIZE_BYTES:
        return {
            "success": False,
            "error": (
                f"写入内容过大 ({len(content_bytes) / 1024 / 1024:.2f} MB)，"
                f"超过单次限制 {_MAX_WRITE_SIZE_BYTES / 1024 / 1024:.0f} MB"
            ),
            "summary": "文件写入失败：内容过大",
        }

    # Binary guard for existing files
    if target.exists():
        try:
            header = target.read_bytes()[:8192]
            if b"\x00" in header:
                return {
                    "success": False,
                    "error": "目标文件是二进制文件，拒绝覆盖",
                    "summary": "文件写入失败：目标是二进制文件",
                }
        except OSError as exc:
            return {
                "success": False,
                "error": f"无法读取目标文件头: {exc}",
                "summary": "文件写入失败：文件访问错误",
            }

    # Ensure parent directory exists
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return {
            "success": False,
            "error": f"无法创建父目录: {exc}",
            "summary": "文件写入失败：目录创建错误",
        }

    # Write
    write_mode = "a" if mode.lower() == "append" else "w"
    try:
        with target.open(write_mode, encoding="utf-8") as f:
            f.write(content)
    except OSError as exc:
        return {
            "success": False,
            "error": f"写入文件失败: {exc}",
            "summary": "文件写入失败：IO 错误",
        }

    # Verify
    try:
        final_size = target.stat().st_size
    except OSError:
        final_size = -1

    action = "追加" if write_mode == "a" else "写入"
    return {
        "success": True,
        "summary": f"已{action} '{path}'（{len(content_bytes)} 字节）",
        "text_blocks": [f"{action}完成：{path}\n最终大小：{final_size} 字节"],
        "internal_metadata": {
            "path": str(target),
            "mode": write_mode,
            "bytes_written": len(content_bytes),
            "final_size_bytes": final_size,
        },
    }


def _resolve_write_path(user_path: str) -> Path | None:
    """Resolve a path for file writing with minimal safety checks.

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
