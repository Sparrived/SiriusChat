"""Built-in skill for listing and querying files within the workspace."""

from __future__ import annotations

import fnmatch
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sirius_chat.skills.models import SkillInvocationContext
from sirius_chat.skills.security import ensure_developer_access

SKILL_META = {
    "name": "file_list",
    "description": (
        "列出或搜索任意路径下的文件和目录，支持按路径、递归深度和 glob 模式过滤。"
        "当不确定文件是否存在或需要浏览磁盘、项目结构时调用。"
    ),
    "version": "1.0.0",
    "developer_only": False,
    "dependencies": [],
    "parameters": {
        "path": {
            "type": "str",
            "description": "起始路径，支持相对路径或绝对路径，例如 src/、docs/、D:/、/etc。不传则列出当前目录。注意：路径中的空格是有意义的，请严格按照用户给出的路径填写，不要擅自添加或删除空格",
            "required": False,
            "default": ".",
        },
        "recursive": {
            "type": "bool",
            "description": "是否递归列出子目录内容",
            "required": False,
            "default": False,
        },
        "pattern": {
            "type": "str",
            "description": "glob 过滤模式，例如 *.py、*.md。不传则不过滤",
            "required": False,
            "default": "",
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
_MAX_RESULTS = 200

# 常见 AI 无法读取或意义不大的文件后缀（大小写不敏感）
_SKIP_EXTENSIONS = {
    # 二进制 / 可执行
    ".exe", ".dll", ".so", ".dylib", ".bin",
    ".obj", ".o", ".class", ".pyc", ".pyo",
    # 图片（保留，AI 可通过多模态读取）
    # 视频 / 音频
    ".mp4", ".avi", ".mov", ".mkv", ".flv",
    ".mp3", ".wav", ".ogg", ".flac", ".aac", ".wma",
    # 压缩包
    ".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz",
    # 字体
    ".ttf", ".woff", ".woff2", ".eot", ".otf",
    # 数据库 / 缓存 / 锁文件
    ".db", ".sqlite", ".sqlite3", ".lock",
    ".pkl", ".pickle", ".coverage",
    # 其他
    ".swp", ".swo", ".tmp", ".temp", ".DS_Store",
}


def run(
    path: str = ".",
    recursive: bool = False,
    pattern: str = "",
    data_store: Any = None,
    invocation_context: SkillInvocationContext | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    raw_path = path.strip() or "."
    target = _resolve_list_path(raw_path, data_store)
    if target is None:
        return {
            "success": False,
            "error": f"路径 '{path}' 包含非法遍历或命中黑名单目录",
            "summary": "文件查询失败：路径被拒绝",
        }

    if target.is_file():
        # Treat single file as a one-item list
        info = _describe_entry(target, target.parent)
        return {
            "success": True,
            "summary": f"'{path}' 是一个文件",
            "text_blocks": [_format_entries([info])],
            "internal_metadata": {
                "path": str(target),
                "count": 1,
                "truncated": False,
            },
        }

    # Fallback: if path doesn't exist, try common LLM mis-formatting variants
    # (e.g. model inserts space between CJK chars and digits)
    if not target.exists():
        alt = Path(raw_path.replace(" ", "")).resolve()
        if alt != target and alt.exists():
            import logging
            logging.getLogger(__name__).debug(
                "file_list 路径回退: 原始路径 '%s' 不存在，使用修正路径 '%s'",
                target,
                alt,
            )
            target = alt
        else:
            return {
                "success": False,
                "error": f"路径不存在: {target}",
                "summary": "文件查询失败：路径不存在",
            }

    # Collect entries
    entries: list[dict[str, Any]] = []
    truncated = False
    glob_pat = pattern.strip() if pattern else "*"

    try:
        if recursive:
            for root, dirs, files in os.walk(target):
                # Prune denied directories in-place
                dirs[:] = [
                    d for d in dirs
                    if not any(d.lower() == deny.lower() for deny in _DENY_PATTERNS)
                ]
                for name in files:
                    if glob_pat != "*" and not fnmatch.fnmatch(name, glob_pat):
                        continue
                    if any(name.lower().endswith(ext) for ext in _SKIP_EXTENSIONS):
                        continue
                    full = Path(root) / name
                    entries.append(_describe_entry(full, target))
                    if len(entries) >= _MAX_RESULTS:
                        truncated = True
                        break
                for name in dirs:
                    full = Path(root) / name
                    entries.append(_describe_entry(full, target))
                    if len(entries) >= _MAX_RESULTS:
                        truncated = True
                        break
                if truncated:
                    break
        else:
            for item in sorted(target.iterdir()):
                if any(item.name.lower() == deny.lower() for deny in _DENY_PATTERNS):
                    continue
                if glob_pat != "*" and not fnmatch.fnmatch(item.name, glob_pat):
                    continue
                if item.is_file() and any(
                    item.name.lower().endswith(ext) for ext in _SKIP_EXTENSIONS
                ):
                    continue
                entries.append(_describe_entry(item, target))
                if len(entries) >= _MAX_RESULTS:
                    truncated = True
                    break
    except OSError as exc:
        return {
            "success": False,
            "error": f"遍历目录失败: {exc}",
            "summary": "文件查询失败：目录遍历错误",
        }

    summary = f"在 '{path}' 下找到 {len(entries)} 项"
    if recursive:
        summary += "（递归）"
    if pattern:
        summary += f"，模式 '{pattern}'"
    if truncated:
        summary += f"，结果已截断至前 {_MAX_RESULTS} 项"

    return {
        "success": True,
        "summary": summary,
        "text_blocks": [_format_entries(entries)],
        "internal_metadata": {
            "path": str(target),
            "count": len(entries),
            "truncated": truncated,
            "recursive": recursive,
            "pattern": pattern,
        },
    }


def _describe_entry(entry: Path, base_path: Path) -> dict[str, Any]:
    """Build a metadata dict for a single file or directory."""
    try:
        rel = entry.relative_to(base_path).as_posix()
    except ValueError:
        rel = entry.as_posix()
    info: dict[str, Any] = {
        "path": rel,
        "type": "directory" if entry.is_dir() else "file",
    }
    try:
        st = entry.stat()
        info["size_bytes"] = st.st_size
        info["modified"] = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M"
        )
    except OSError:
        pass
    return info


def _format_entries(entries: list[dict[str, Any]]) -> str:
    """Format entry list as a plain-text table."""
    if not entries:
        return "（无结果）"
    lines: list[str] = []
    for e in entries:
        t = "[D]" if e.get("type") == "directory" else "[F]"
        size = e.get("size_bytes", "-")
        mtime = e.get("modified", "-")
        lines.append(f"{t} {e['path']:<50} {size:>12} {mtime:>16}")
    return "\n".join(lines)


def _resolve_list_path(user_path: str, data_store: Any) -> Path | None:
    """Resolve a path for file listing with minimal safety checks.

    Operates at the OS level: any path (absolute, relative, including '..')
    is allowed except for deny-listed sensitive directories.
    """
    raw = user_path.strip()
    if not raw:
        raw = "."

    target = Path(raw).resolve()

    deny_set = {d.lower() for d in _DENY_PATTERNS}
    for part in target.parts:
        if part.lower() in deny_set:
            return None

    return target
