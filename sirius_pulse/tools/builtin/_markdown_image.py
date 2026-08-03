"""Render model-authored Markdown-like text into a single shareable image."""

from __future__ import annotations

import base64
import html
import re
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from uuid import uuid4

_MAX_CONTENT_CHARS = 12_000
_MAX_TITLE_CHARS = 80
_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+)$")
_ORDERED_ITEM_RE = re.compile(r"^\d+[.)]\s+(.+)$")
_BULLET_ITEM_RE = re.compile(r"^[-*+]\s+(.+)$")
_FENCE_LINE_RE = re.compile(r"^\s{0,3}([`~]+)([^\r\n]*)$")
_MARKDOWN_LABEL_RE = re.compile(r"^\s*(?:markdown|md)\s*[:：]\s*(.*)$", re.IGNORECASE)
_MARKDOWN_LABEL_ONLY_RE = re.compile(r"^\s*(?:markdown|md)\s*$", re.IGNORECASE)
_STRONG_ONLY_RE = re.compile(r"^\s*(?:\*\*.+\*\*|__.+__)\s*$")
_INLINE_MARKDOWN_RE = re.compile(
    r"(?<!\\)(?:\*\*[^*\r\n]+\*\*|__[^_\r\n]+__|`[^`\r\n]+`|"
    r"(?<!\*)\*[^*\r\n]+\*(?!\*))"
)
_HORIZONTAL_RULE_RE = re.compile(r"^(?:-{3,}|\*{3,}|_{3,}|—-+|—{2,}|－{3,}|＿{3,})$")
_CUTE_FONT_PATH = Path(__file__).with_name("assets") / "ZCOOLKuaiLe-Regular.ttf"


def split_fenced_markdown(text: str) -> list[tuple[bool, str]]:
    """Split and repair fenced or clearly structured Markdown in display order."""
    source = _normalize_fence_chars(str(text or ""))
    parts: list[tuple[bool, str]] = []
    plain_lines: list[str] = []
    markdown_lines: list[str] = []
    fence_char = ""

    def flush_plain() -> None:
        content = "\n".join(plain_lines).strip()
        if content:
            parts.append((False, content))
        plain_lines.clear()

    def flush_markdown() -> None:
        content = "\n".join(markdown_lines).strip()
        if content:
            parts.append((True, content))
        markdown_lines.clear()

    for raw_line in source.splitlines():
        line = raw_line.rstrip()
        fence_match = _FENCE_LINE_RE.match(line)
        marker = fence_match.group(1) if fence_match else ""
        info = fence_match.group(2).strip() if fence_match else ""

        if not fence_char:
            if len(marker) >= 3:
                flush_plain()
                fence_char = marker[0]
            else:
                plain_lines.append(raw_line)
            continue

        # Accept a shorter matching closing marker so a malformed model fence
        # cannot swallow the rest of the reply.
        if marker and marker[0] == fence_char and not info:
            flush_markdown()
            fence_char = ""
        else:
            markdown_lines.append(raw_line)

    if fence_char:
        flush_markdown()
    flush_plain()

    if any(is_markdown for is_markdown, _ in parts):
        return parts

    return _split_unfenced_markdown(source)


def merge_markdown_blocks(blocks: Iterable[str]) -> str:
    """Join fenced blocks into one readable Markdown document."""
    clean_blocks = [str(block or "").strip() for block in blocks if str(block or "").strip()]
    return "\n\n---\n\n".join(clean_blocks)


def should_render_markdown_card(blocks: Iterable[str]) -> bool:
    """Require a substantive Markdown reply before creating an image card."""
    clean_blocks = [str(block or "").strip() for block in blocks if str(block or "").strip()]
    if not clean_blocks:
        return False
    content = "\n".join(clean_blocks)
    nonempty_lines = [line for line in content.splitlines() if line.strip()]
    return len(nonempty_lines) > 2 or len(content) > 80


def _normalize_fence_chars(text: str) -> str:
    return str(text or "").replace("｀", "`").replace("～", "~")


def _strip_markdown_label(text: str) -> str:
    lines = str(text or "").strip().splitlines()
    if not lines:
        return ""
    label_match = _MARKDOWN_LABEL_RE.match(lines[0])
    if label_match:
        replacement = label_match.group(1).strip()
        lines = ([replacement] if replacement else []) + lines[1:]
    elif _MARKDOWN_LABEL_ONLY_RE.match(lines[0]):
        lines = lines[1:]
    return "\n".join(lines).strip()


def _split_unfenced_markdown(text: str) -> list[tuple[bool, str]]:
    source = _strip_markdown_label(text)
    if not source:
        return []

    parts: list[tuple[bool, str]] = []
    for block in re.split(r"\n\s*\n", source):
        lines = block.splitlines()
        structural_indexes = [
            index for index, line in enumerate(lines) if _is_markdown_structure_line(line)
        ]
        if not structural_indexes or not _looks_like_unfenced_markdown(block):
            clean_block = block.strip()
            if clean_block:
                parts.append((False, clean_block))
            continue

        first = structural_indexes[0]
        last = structural_indexes[-1]
        before = "\n".join(lines[:first]).strip()
        markdown = "\n".join(lines[first : last + 1]).strip()
        after = "\n".join(lines[last + 1 :]).strip()
        if before:
            parts.append((False, before))
        if markdown:
            parts.append((True, markdown))
        if after:
            parts.append((False, after))
    return _remove_orphan_markdown_separators(_promote_markdown_sections(parts))


def _is_markdown_structure_line(line: str) -> bool:
    clean_line = str(line or "").strip()
    return bool(
        _HEADING_RE.match(clean_line)
        or _ORDERED_ITEM_RE.match(clean_line)
        or _BULLET_ITEM_RE.match(clean_line)
        or clean_line.startswith(">")
        or _is_table_row_line(clean_line)
        or _STRONG_ONLY_RE.match(clean_line)
        or _INLINE_MARKDOWN_RE.search(clean_line)
        or _HORIZONTAL_RULE_RE.match(clean_line)
    )


def _looks_like_unfenced_markdown(text: str) -> bool:
    source = str(text or "").strip()
    if not source:
        return False
    lines = [line.strip() for line in source.splitlines() if line.strip()]
    headings = sum(bool(_HEADING_RE.match(line)) for line in lines)
    bullets = sum(bool(_BULLET_ITEM_RE.match(line)) for line in lines)
    ordered = sum(bool(_ORDERED_ITEM_RE.match(line)) for line in lines)
    table_rows = sum(_is_table_row_line(line) for line in lines)
    quotes = sum(line.startswith(">") for line in lines)
    strong_lines = sum(bool(_STRONG_ONLY_RE.match(line)) for line in lines)
    inline_lines = sum(bool(_INLINE_MARKDOWN_RE.search(line)) for line in lines)
    horizontal_rules = sum(bool(_HORIZONTAL_RULE_RE.match(line)) for line in lines)
    score = 2 * headings
    score += 2 if bullets >= 2 else 0
    score += 2 if ordered >= 2 else 0
    score += 2 if table_rows >= 2 else 0
    score += 2 if strong_lines else 0
    score += min(quotes, 1)
    score += min(horizontal_rules, 1)
    score += 2 if inline_lines else 0
    score += 1 if re.search(r"[`*_]{2}", source) else 0
    score += 1 if len(source) >= 160 else 0
    return score >= 2 and bool(
        headings
        or bullets >= 2
        or ordered >= 2
        or table_rows >= 2
        or quotes
        or strong_lines
        or inline_lines
    )


def _is_table_row_line(line: str) -> bool:
    return str(line or "").count("|") >= 2


def _remove_orphan_markdown_separators(
    parts: list[tuple[bool, str]],
) -> list[tuple[bool, str]]:
    result: list[tuple[bool, str]] = []
    for index, (is_markdown, content) in enumerate(parts):
        if not is_markdown and _is_horizontal_rule_line(content):
            previous_is_markdown = bool(result and result[-1][0])
            next_is_markdown = index + 1 < len(parts) and parts[index + 1][0]
            if previous_is_markdown or next_is_markdown:
                continue
        result.append((is_markdown, content))
    return result


def _promote_markdown_sections(parts: list[tuple[bool, str]]) -> list[tuple[bool, str]]:
    result: list[tuple[bool, str]] = []
    index = 0
    while index < len(parts):
        is_markdown, content = parts[index]
        if not is_markdown:
            result.append((is_markdown, content))
            index += 1
            continue

        section = [content]
        cursor = index + 1
        while cursor < len(parts) and not parts[cursor][0]:
            section.append(parts[cursor][1])
            if _is_horizontal_rule_line(parts[cursor][1]):
                cursor += 1
                break
            cursor += 1

        if cursor > index + 1 and _is_horizontal_rule_line(section[-1]):
            result.append((True, "\n\n".join(section)))
            index = cursor
        else:
            result.append((True, content))
            index += 1
    return result


def _is_horizontal_rule_line(line: str) -> bool:
    return bool(_HORIZONTAL_RULE_RE.match(str(line or "").strip()))


def _is_code_fence_line(line: str) -> bool:
    match = _FENCE_LINE_RE.match(str(line or "").rstrip())
    return bool(match and len(match.group(1)) >= 3)


def has_fenced_markdown(text: str) -> bool:
    parts = split_fenced_markdown(text)
    return should_render_markdown_card(content for is_markdown, content in parts if is_markdown)


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
    font_face = _cute_font_face_css()
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><style>
{font_face}
* {{ box-sizing: border-box; }}
html {{ background: #dfe4df; }}
body {{ margin: 0; padding: 24px; background: #dfe4df; color: #202a2b; font-family: "Sirius Cute", "Noto Sans CJK SC", "Microsoft YaHei", sans-serif; }}
#markdown-card {{ width: 900px; background: #f7f7f2; border: 1px solid #1c2b2e; border-radius: 8px; box-shadow: 8px 8px 0 #b8c4bc; overflow: hidden; }}
.masthead {{ display: flex; align-items: flex-end; justify-content: space-between; gap: 24px; min-height: 132px; padding: 25px 36px 24px; background: #18252a; color: #f3f0e7; }}
.identity {{ display: flex; align-items: center; gap: 16px; }}
.signal-mark {{ display: grid; grid-template-columns: repeat(3, 7px); gap: 4px; width: 38px; padding: 7px; border: 1px solid #7bd7c6; background: #26383b; }}
.signal-mark span {{ display: block; height: 7px; background: #7bd7c6; }}
.signal-mark span:nth-child(2), .signal-mark span:nth-child(5), .signal-mark span:nth-child(8) {{ background: #f0b85d; }}
.signal-mark span:nth-child(3), .signal-mark span:nth-child(7) {{ background: #e36b52; }}
.eyebrow, .telemetry-label, .footer {{ font-family: "Cascadia Mono", Consolas, monospace; letter-spacing: 1.4px; text-transform: uppercase; }}
.eyebrow {{ color: #7bd7c6; font-size: 11px; font-weight: 700; }}
.wordmark {{ margin-top: 5px; color: #f3f0e7; font-size: 27px; font-weight: 800; letter-spacing: 1px; }}
.telemetry {{ min-width: 160px; padding-left: 16px; border-left: 1px solid #536467; }}
.telemetry-label {{ color: #a8b8b5; font-size: 10px; }}
.telemetry-value {{ margin-top: 8px; color: #f0b85d; font-family: "Cascadia Mono", Consolas, monospace; font-size: 12px; font-weight: 700; }}
.accent {{ height: 8px; background: #e36b52; position: relative; }}
.accent::after {{ content: ""; position: absolute; top: 0; right: 0; width: 34%; height: 100%; background: #f0b85d; }}
.content {{ padding: 34px 42px 37px; }}
h1 {{ color: #18252a; font-size: 30px; line-height: 1.3; margin: 0 0 24px; overflow-wrap: anywhere; }}
h2 {{ color: #18252a; font-size: 24px; line-height: 1.35; margin: 28px 0 12px; padding-bottom: 8px; border-bottom: 2px solid #b8c4bc; }}
h3 {{ color: #b34e3d; font-size: 19px; line-height: 1.4; margin: 24px 0 10px; }}
h4 {{ color: #55716c; font-size: 16px; line-height: 1.4; margin: 20px 0 8px; }}
p, li, blockquote {{ font-size: 17px; line-height: 1.75; overflow-wrap: anywhere; }}
p {{ margin: 0 0 15px; overflow-wrap: anywhere; }}
ul, ol {{ margin: 8px 0 18px; padding-left: 28px; }}
li {{ margin: 4px 0; padding-left: 4px; }}
blockquote {{ border-left: 5px solid #e36b52; background: #e8eeea; color: #4a605e; margin: 18px 0; padding: 7px 15px; }}
hr {{ height: 1px; margin: 29px 0; border: 0; background: #b8c4bc; position: relative; }}
hr::after {{ content: ""; position: absolute; top: -2px; left: 0; width: 36px; height: 5px; background: #7bd7c6; }}
pre {{ background: #202d31; border: 0; border-left: 5px solid #7bd7c6; border-radius: 3px; color: #e8f0e9; font-family: "Cascadia Mono", Consolas, monospace; font-size: 14px; line-height: 1.65; margin: 18px 0; overflow-wrap: anywhere; padding: 16px; white-space: pre-wrap; }}
code {{ background: #e4ebe5; border-radius: 3px; color: #b34e3d; font-family: "Cascadia Mono", Consolas, monospace; font-size: .9em; padding: 2px 4px; }}
pre code {{ background: transparent; color: inherit; padding: 0; }}
strong {{ color: #18252a; }} em {{ color: #55716c; }}
table {{ width: 100%; border-collapse: collapse; margin: 18px 0 22px; table-layout: fixed; }}
th, td {{ border: 1px solid #c9d2cb; padding: 9px 11px; vertical-align: top; text-align: left; overflow-wrap: anywhere; }}
th {{ background: #e8eeea; color: #18252a; font-weight: 800; }}
tbody tr:nth-child(even) td {{ background: #f1f4ef; }}
.table-line {{ background: #edf1eb; border-left: 3px solid #f0b85d; font-family: "Cascadia Mono", Consolas, monospace; font-size: 14px; padding: 7px 10px; white-space: pre-wrap; }}
.footer {{ display: flex; justify-content: space-between; gap: 20px; border-top: 1px solid #c9d2cb; color: #70827d; font-size: 10px; margin-top: 30px; padding-top: 14px; }}
</style></head><body><article id="markdown-card"><header class="masthead"><div class="identity"><div class="signal-mark" aria-hidden="true"><span></span><span></span><span></span><span></span><span></span><span></span><span></span><span></span><span></span></div><div><div class="eyebrow">SIRIUS / RESPONSE ARCHIVE</div><div class="wordmark">FIELD NOTE</div></div></div><div class="telemetry"><div class="telemetry-label">Output mode</div><div class="telemetry-value">MERGED RESPONSE</div></div></header><div class="accent"></div><div class="content">{heading}{_markdown_body_html(content)}<div class="footer"><span>SIRIUS CHAT</span><span>ONE REPLY / MANY SIGNALS</span></div></div></article></body></html>"""


def _cute_font_face_css() -> str:
    try:
        encoded = base64.b64encode(_CUTE_FONT_PATH.read_bytes()).decode("ascii")
    except OSError:
        return ""
    return (
        "@font-face {"
        "font-family: 'Sirius Cute';"
        f"src: url(data:font/ttf;base64,{encoded}) format('truetype');"
        "font-style: normal; font-weight: 400; font-display: block;"
        "}"
    )


def _markdown_body_html(content: str) -> str:
    blocks: list[str] = []
    paragraph: list[str] = []
    list_items: list[str] = []
    list_tag = ""
    table_lines: list[str] = []
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

    def flush_table() -> None:
        if not table_lines:
            return
        if len(table_lines) < 2 or not _is_table_separator(table_lines[1]):
            blocks.extend(f'<p class="table-line">{_inline_html(line)}</p>' for line in table_lines)
            table_lines.clear()
            return

        header = _table_cells(table_lines[0])
        alignments = _table_alignments(table_lines[1])
        rows = [_table_cells(line) for line in table_lines[2:]]
        column_count = max([len(header), *(len(row) for row in rows)], default=0)
        header.extend([""] * (column_count - len(header)))
        alignments.extend([""] * (column_count - len(alignments)))

        def cell_html(tag: str, value: str, alignment: str) -> str:
            align = f' align="{alignment}"' if alignment else ""
            return f"<{tag}{align}>{_inline_html(value)}</{tag}>"

        head_html = "".join(
            cell_html("th", header[index], alignments[index]) for index in range(column_count)
        )
        body_html = "".join(
            "<tr>"
            + "".join(
                cell_html(
                    "td",
                    row[index] if index < len(row) else "",
                    alignments[index],
                )
                for index in range(column_count)
            )
            + "</tr>"
            for row in rows
        )
        blocks.append(
            f"<table><thead><tr>{head_html}</tr></thead><tbody>{body_html}</tbody></table>"
        )
        table_lines.clear()

    for raw_line in str(content or "").strip().splitlines():
        line = raw_line.rstrip()
        if _is_code_fence_line(line):
            flush_table()
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
            flush_table()
            flush_paragraph()
            flush_list()
            continue

        clean_line = line.strip()
        if _is_horizontal_rule_line(clean_line):
            flush_table()
            flush_paragraph()
            flush_list()
            blocks.append("<hr>")
            continue

        heading_match = _HEADING_RE.match(clean_line)
        if heading_match:
            flush_table()
            flush_paragraph()
            flush_list()
            level = min(4, len(heading_match.group(1)) + 1)
            blocks.append(f"<h{level}>{_inline_html(heading_match.group(2))}</h{level}>")
            continue
        if clean_line.startswith(">"):
            flush_table()
            flush_paragraph()
            flush_list()
            blocks.append(f"<blockquote>{_inline_html(clean_line[1:].lstrip())}</blockquote>")
            continue

        ordered_match = _ORDERED_ITEM_RE.match(clean_line)
        bullet_match = _BULLET_ITEM_RE.match(clean_line)
        if ordered_match or bullet_match:
            flush_table()
            flush_paragraph()
            next_tag = "ol" if ordered_match else "ul"
            if list_tag and list_tag != next_tag:
                flush_list()
            list_tag = next_tag
            item = ordered_match.group(1) if ordered_match else bullet_match.group(1)
            list_items.append(f"<li>{_inline_html(item)}</li>")
            continue

        flush_list()
        if _is_table_row_line(line):
            flush_paragraph()
            table_lines.append(line.strip())
        else:
            flush_table()
            paragraph.append(line)

    if in_code:
        flush_code()
    flush_table()
    flush_paragraph()
    flush_list()
    return "".join(blocks)


def _table_cells(line: str) -> list[str]:
    clean_line = str(line or "").strip()
    if clean_line.startswith("|"):
        clean_line = clean_line[1:]
    if clean_line.endswith("|"):
        clean_line = clean_line[:-1]
    return [cell.strip().replace("\\|", "|") for cell in re.split(r"(?<!\\)\|", clean_line)]


def _is_table_separator(line: str) -> bool:
    cells = _table_cells(line)
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)


def _table_alignments(line: str) -> list[str]:
    alignments: list[str] = []
    for cell in _table_cells(line):
        if cell.startswith(":") and cell.endswith(":"):
            alignments.append("center")
        elif cell.startswith(":"):
            alignments.append("left")
        elif cell.endswith(":"):
            alignments.append("right")
        else:
            alignments.append("")
    return alignments


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
