from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any


_SESSION_CONFIG_HEADER = [
    "Sirius Chat session config.",
    "This file accepts JSONC-style comments and can be edited directly.",
]

_SESSION_CONFIG_COMMENTS = {
    "generated_agent_key": "当前启用的 generated agent 标识。首次初始化后 main.py 会自动回写这个字段。",
    "history_max_messages": "参与上下文保留的最近消息数量。",
    "history_max_chars": "触发历史压缩前保留的最近字符预算。",
    "max_recent_participant_messages": "每个参与者额外保留的最近发言条数。",
    "enable_auto_compression": "超过上下文预算时是否自动压缩历史。",
    "provider": "旧版单 provider 兼容字段。新配置优先使用 providers 列表。",
    "providers": "Provider 列表。main.py 和 sirius-chat CLI 会优先读取这个字段。",
    "orchestration": "任务级编排配置，可为 memory_extract、event_extract 等任务单独设置模型与预算。",
}


def strip_json_comments(content: str) -> str:
    """Strip JSONC-style comments while preserving quoted strings."""

    result: list[str] = []
    in_string = False
    escaped = False
    line_comment = False
    block_comment = False
    index = 0

    while index < len(content):
        char = content[index]
        next_char = content[index + 1] if index + 1 < len(content) else ""

        if line_comment:
            if char in "\r\n":
                line_comment = False
                result.append(char)
            index += 1
            continue

        if block_comment:
            if char == "*" and next_char == "/":
                block_comment = False
                index += 2
                continue
            if char in "\r\n":
                result.append(char)
            index += 1
            continue

        if in_string:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue

        if char == '"':
            in_string = True
            result.append(char)
            index += 1
            continue

        if char == "/" and next_char == "/":
            line_comment = True
            index += 2
            continue

        if char == "/" and next_char == "*":
            block_comment = True
            index += 2
            continue

        result.append(char)
        index += 1

    return "".join(result)


def loads_json_document(content: str) -> Any:
    """Parse JSON or JSONC content."""

    return json.loads(strip_json_comments(content))


def load_json_document(path: Path | str) -> Any:
    """Load a JSON or JSONC document from disk."""

    document_path = Path(path)
    return loads_json_document(document_path.read_text(encoding="utf-8-sig"))


def render_session_config_jsonc(payload: Mapping[str, Any]) -> str:
    """Render a JSONC session config with inline guidance comments."""

    lines = [f"// {line}" for line in _SESSION_CONFIG_HEADER]
    lines.append("{")

    items = list(payload.items())
    for index, (key, value) in enumerate(items):
        comment = _SESSION_CONFIG_COMMENTS.get(str(key), "")
        if comment:
            lines.append(f"  // {comment}")

        rendered_value = json.dumps(value, ensure_ascii=False, indent=2)
        rendered_lines = rendered_value.splitlines()
        suffix = "," if index < len(items) - 1 else ""
        if len(rendered_lines) == 1:
            lines.append(f'  "{key}": {rendered_lines[0]}{suffix}')
            continue

        lines.append(f'  "{key}": {rendered_lines[0]}')
        lines.extend(f"  {line}" for line in rendered_lines[1:-1])
        lines.append(f"  {rendered_lines[-1]}{suffix}")

    lines.append("}")
    return "\n".join(lines) + "\n"


def write_session_config_jsonc(path: Path | str, payload: Mapping[str, Any]) -> None:
    """Write a commented session config document to disk."""

    document_path = Path(path)
    document_path.parent.mkdir(parents=True, exist_ok=True)
    document_path.write_text(render_session_config_jsonc(payload), encoding="utf-8")