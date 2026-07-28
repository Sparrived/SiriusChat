"""Render model-authored Markdown-like text into a single shareable image."""

from __future__ import annotations

import base64
import html
import re
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

_MAX_CONTENT_CHARS = 12_000
_MAX_TITLE_CHARS = 80
_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+)$")
_ORDERED_ITEM_RE = re.compile(r"^\d+[.)]\s+(.+)$")
_BULLET_ITEM_RE = re.compile(r"^[-*+]\s+(.+)$")
_FENCED_MARKDOWN_RE = re.compile(r"```[^\r\n]*\r?\n(.*?)```", re.DOTALL)


def split_fenced_markdown(text: str) -> list[tuple[bool, str]]:
    """Split a reply into ordinary text and Markdown image blocks in display order."""
    source = str(text or "")
    parts: list[tuple[bool, str]] = []
    cursor = 0
    for match in _FENCED_MARKDOWN_RE.finditer(source):
        before = source[cursor : match.start()].strip()
        content = match.group(1).strip()
        if before:
            parts.append((False, before))
        if content:
            parts.append((True, content))
        cursor = match.end()
    after = source[cursor:].strip()
    if after:
        parts.append((False, after))
    return parts


def has_fenced_markdown(text: str) -> bool:
    return any(is_markdown for is_markdown, _ in split_fenced_markdown(text))


async def render_markdown_image(content: str, title: str, data_store: Any) -> Path:
    """Render a bounded, escaped Markdown-like response using bundled Chromium."""
    text = str(content or "").strip()
    if not text:
        raise ValueError("content 不能为空")
    if len(text) > _MAX_CONTENT_CHARS:
        raise ValueError(f"content 过长，最多 {_MAX_CONTENT_CHARS} 个字符")

    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError("富文本图片需要 Playwright；请重新部署包含 Chromium 的镜像") from exc

    output_dir = _artifact_dir(data_store)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"markdown_{uuid4().hex}.png"

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page(
                viewport={"width": 960, "height": 800}, device_scale_factor=1
            )
            await page.set_content(build_markdown_card_html(text, title), wait_until="load")
            await page.locator("#markdown-card").screenshot(path=str(output_path))
        finally:
            await browser.close()
    return output_path


async def render_and_send_markdown_image(
    content: str,
    *,
    adapter: Any,
    group_id: str,
    title: str = "",
) -> str:
    """Render a fenced reply block and deliver it to the current chat directly."""
    target = str(group_id or "").strip()
    client = getattr(adapter, "adapter", None) or adapter
    if not client or not target:
        raise RuntimeError("富文本图片发送缺少平台适配器或聊天目标")

    image_path = await render_markdown_image(content, title, data_store=None)
    try:
        image = [{"type": "image", "data": {"file": to_image_reference(str(image_path))}}]
        if target.startswith("private_"):
            user_id = target.removeprefix("private_").removeprefix("qq_")
            response = await client.send_private_msg(user_id, image)
        else:
            response = await client.send_group_msg(target, image)
        data = response.get("data", {}) if isinstance(response, dict) else {}
        return str(data.get("message_id") or "") if isinstance(data, dict) else ""
    finally:
        try:
            image_path.unlink(missing_ok=True)
        except OSError:
            pass


def to_image_reference(image_path: str) -> str:
    """Encode local image data so the platform need not access this process's filesystem."""
    if image_path.startswith(("http://", "https://", "data:", "base64://")):
        return image_path
    path = Path(image_path.removeprefix("file://")).expanduser()
    if not path.is_file():
        return image_path
    return f"base64://{base64.b64encode(path.read_bytes()).decode('ascii')}"


def build_markdown_card_html(content: str, title: str = "") -> str:
    """Build escaped HTML for the small Markdown subset used in chat replies."""
    clean_title = str(title or "").strip()[:_MAX_TITLE_CHARS]
    heading = f"<h1>{_inline_html(clean_title)}</h1>" if clean_title else ""
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><style>
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: #e8edef; color: #1d2529; font-family: "Microsoft YaHei", "Noto Sans CJK SC", "Segoe UI", sans-serif; }}
#markdown-card {{ width: 900px; background: #fff; border: 1px solid #cbd6d9; border-radius: 8px; overflow: hidden; }}
.accent {{ height: 10px; background: #117f85; }}
.content {{ padding: 34px 42px 38px; }}
h1 {{ color: #123a40; font-size: 30px; line-height: 1.3; margin: 0 0 24px; }}
h2 {{ color: #123a40; font-size: 24px; line-height: 1.35; margin: 28px 0 12px; }}
h3 {{ color: #176b70; font-size: 19px; line-height: 1.4; margin: 24px 0 10px; }}
h4 {{ color: #176b70; font-size: 16px; line-height: 1.4; margin: 20px 0 8px; }}
p, li, blockquote {{ font-size: 17px; line-height: 1.75; }}
p {{ margin: 0 0 15px; overflow-wrap: anywhere; }}
ul, ol {{ margin: 8px 0 18px; padding-left: 28px; }}
li {{ margin: 4px 0; padding-left: 4px; }}
blockquote {{ border-left: 4px solid #6db5b6; color: #466166; margin: 18px 0; padding: 4px 0 4px 16px; }}
pre {{ background: #f3f6f7; border: 1px solid #d9e2e4; border-radius: 5px; color: #24363a; font-family: "Cascadia Mono", Consolas, monospace; font-size: 14px; line-height: 1.65; margin: 18px 0; overflow-wrap: anywhere; padding: 16px; white-space: pre-wrap; }}
code {{ background: #edf3f3; border-radius: 3px; color: #8e3d2a; font-family: "Cascadia Mono", Consolas, monospace; font-size: .9em; padding: 2px 4px; }}
pre code {{ background: transparent; color: inherit; padding: 0; }}
strong {{ color: #102e34; }} em {{ color: #45676a; }}
.table-line {{ background: #f6f8f8; border-left: 3px solid #b6c7ca; font-family: "Cascadia Mono", Consolas, monospace; font-size: 14px; padding: 7px 10px; white-space: pre-wrap; }}
.footer {{ border-top: 1px solid #dfe7e9; color: #6b7d81; font-size: 11px; margin-top: 28px; padding-top: 14px; }}
</style></head><body><article id="markdown-card"><div class="accent"></div><div class="content">{heading}{_markdown_body_html(content)}<div class="footer">SIRIUS CHAT</div></div></article></body></html>"""


def _markdown_body_html(content: str) -> str:
    blocks: list[str] = []
    paragraph: list[str] = []
    list_items: list[str] = []
    list_tag = ""
    code_lines: list[str] = []
    in_code = False

    def flush_paragraph() -> None:
        if paragraph:
            blocks.append(f"<p>{'<br>'.join(_inline_html(line) for line in paragraph)}</p>")
            paragraph.clear()

    def flush_list() -> None:
        nonlocal list_tag
        if list_items:
            blocks.append(f"<{list_tag}>{''.join(list_items)}</{list_tag}>")
            list_items.clear()
        list_tag = ""

    def flush_code() -> None:
        if code_lines:
            blocks.append(f"<pre><code>{html.escape(chr(10).join(code_lines))}</code></pre>")
            code_lines.clear()

    for raw_line in str(content or "").strip().splitlines():
        line = raw_line.rstrip()
        if line.strip().startswith("```"):
            flush_paragraph()
            flush_list()
            if in_code:
                flush_code()
            in_code = not in_code
            continue
        if in_code:
            code_lines.append(line)
            continue
        if not line.strip():
            flush_paragraph()
            flush_list()
            continue

        heading_match = _HEADING_RE.match(line)
        if heading_match:
            flush_paragraph()
            flush_list()
            level = min(4, len(heading_match.group(1)) + 1)
            blocks.append(f"<h{level}>{_inline_html(heading_match.group(2))}</h{level}>")
            continue
        if line.startswith(">"):
            flush_paragraph()
            flush_list()
            blocks.append(f"<blockquote>{_inline_html(line[1:].lstrip())}</blockquote>")
            continue

        ordered_match = _ORDERED_ITEM_RE.match(line)
        bullet_match = _BULLET_ITEM_RE.match(line)
        if ordered_match or bullet_match:
            flush_paragraph()
            next_tag = "ol" if ordered_match else "ul"
            if list_tag and list_tag != next_tag:
                flush_list()
            list_tag = next_tag
            item = ordered_match.group(1) if ordered_match else bullet_match.group(1)
            list_items.append(f"<li>{_inline_html(item)}</li>")
            continue

        flush_list()
        if line.count("|") >= 2:
            flush_paragraph()
            blocks.append(f'<p class="table-line">{_inline_html(line)}</p>')
        else:
            paragraph.append(line)

    if in_code:
        flush_code()
    flush_paragraph()
    flush_list()
    return "".join(blocks)


def _inline_html(text: str) -> str:
    escaped = html.escape(str(text or ""))
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"(\*\*|__)(.+?)\1", r"<strong>\2</strong>", escaped)
    return re.sub(r"(?<!\*)\*([^*]+)\*", r"<em>\1</em>", escaped)


def _artifact_dir(data_store: Any) -> Path:
    artifact_dir = getattr(data_store, "artifact_dir", None)
    if isinstance(artifact_dir, Path):
        return artifact_dir
    if artifact_dir:
        return Path(str(artifact_dir))
    return Path(tempfile.gettempdir()) / "sirius_pulse" / "markdown_image"
