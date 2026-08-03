import base64
from pathlib import Path
from typing import Any

import pytest

from sirius_pulse.skills.builtin import _markdown_image
from sirius_pulse.skills.builtin._markdown_image import (
    build_markdown_card_html,
    has_fenced_markdown,
    merge_markdown_blocks,
    should_render_markdown_card,
    split_fenced_markdown,
)


def test_split_fenced_markdown_preserves_text_before_and_after_the_card():
    result = split_fenced_markdown(
        "先说整体思路。\n```markdown\n**顶层模块**：\n- core/\n```\n细节之后再聊。"
    )

    assert result == [
        (False, "先说整体思路。"),
        (True, "**顶层模块**：\n- core/"),
        (False, "细节之后再聊。"),
    ]


def test_merge_markdown_blocks_adds_a_visible_section_break():
    assert merge_markdown_blocks(["# first", "- second"]) == "# first\n\n---\n\n- second"


def test_split_fenced_markdown_repairs_tilde_indent_and_short_closing_fence():
    result = split_fenced_markdown("前言\n  ~~~markdown\n# 状态\n- healthy\n~\n后言")

    assert result == [
        (False, "前言"),
        (True, "# 状态\n- healthy"),
        (False, "后言"),
    ]


def test_split_fenced_markdown_repairs_unclosed_fullwidth_fence():
    result = split_fenced_markdown("｀｀｀md\n# 结果\n\n- 已完成")

    assert result == [(True, "# 结果\n\n- 已完成")]


def test_split_fenced_markdown_detects_structured_content_without_fence():
    result = split_fenced_markdown("markdown:\n# 部署结果\n\n- WebUI 正常\n- Embedding 正常")

    assert result == [
        (True, "# 部署结果"),
        (True, "- WebUI 正常\n- Embedding 正常"),
    ]


def test_split_fenced_markdown_keeps_natural_language_around_unfenced_markdown():
    result = split_fenced_markdown(
        "我看完了，核心问题是连接还没就绪。\n\n"
        "# 部署结果\n- WebUI 正常\n- Embedding 正常\n\n"
        "如果还在刷屏，可以重启。"
    )

    assert result == [
        (False, "我看完了，核心问题是连接还没就绪。"),
        (True, "# 部署结果\n- WebUI 正常\n- Embedding 正常"),
        (False, "如果还在刷屏，可以重启。"),
    ]


def test_split_unfenced_markdown_keeps_table_and_bold_markdown_in_the_card():
    result = split_fenced_markdown(
        "当然可以。\n\n"
        "---\n\n"
        "# 状态报告\n\n"
        "| 项目 | 状态 |\n"
        "|------|------|\n"
        "| WebUI | 正常 |\n\n"
        "**服务器状态速报**\n\n"
        "姐姐，一切安好。"
    )

    assert result == [
        (False, "当然可以。"),
        (True, "# 状态报告"),
        (True, "| 项目 | 状态 |\n|------|------|\n| WebUI | 正常 |"),
        (True, "**服务器状态速报**"),
        (False, "姐姐，一切安好。"),
    ]


def test_split_fenced_markdown_keeps_ordinary_text_unwrapped():
    text = "今天状态不错，准备晚点再看看服务器。"

    assert split_fenced_markdown(text) == [(False, text)]


def test_split_unfenced_markdown_detects_inline_formatting_from_group_history():
    text = "看起来是**只读模式**，可以执行 `/op Sparrived`。"

    assert split_fenced_markdown(text) == [(True, text)]
    assert split_fenced_markdown("执行 `docker ps` 查看状态。") == [
        (True, "执行 `docker ps` 查看状态。")
    ]


def test_markdown_card_requires_more_than_two_lines_or_eighty_chars():
    assert should_render_markdown_card(["**标题**\n- 一项"]) is False
    assert should_render_markdown_card(["**标题**\n- 一项\n- 二项"]) is True
    assert should_render_markdown_card(["执行 `docker ps` " + "检查服务器状态。" * 20]) is True
    assert has_fenced_markdown("执行 `docker ps` 查看状态。") is False


@pytest.mark.asyncio
async def test_render_and_send_markdown_image_sends_base64_and_removes_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    image_path = tmp_path / "card.png"
    sent: list[tuple[str, Any]] = []

    class Adapter:
        async def send_group_msg(self, group_id: str, message: Any) -> dict[str, Any]:
            sent.append((group_id, message))
            return {"data": {"message_id": 42}}

    async def fake_render(content: str, title: str, data_store: object) -> Path:
        assert content == "- core/"
        assert title == ""
        assert data_store is None
        image_path.write_bytes(b"card-bytes")
        return image_path

    monkeypatch.setattr(_markdown_image, "render_markdown_image", fake_render)
    message_id = await _markdown_image.render_and_send_markdown_image(
        "- core/", adapter=Adapter(), group_id="9001"
    )

    assert message_id == "42"
    assert sent == [
        (
            "9001",
            [
                {
                    "type": "image",
                    "data": {"file": f"base64://{base64.b64encode(b'card-bytes').decode('ascii')}"},
                }
            ],
        )
    ]
    assert image_path.exists() is False


def test_markdown_card_escapes_content_and_renders_common_structures():
    html = build_markdown_card_html(
        "# 概览\n\n- **服务**已恢复\n- 使用 `docker ps` 验证\n\n```\n<script>alert(1)</script>\n```",
        "处理结果",
    )

    assert "<h1>处理结果</h1>" in html
    assert "@font-face" in html
    assert 'font-family: "Sirius Cute"' in html
    assert "<h2>概览</h2>" in html
    assert "<ul>" in html
    assert "<strong>服务</strong>" in html
    assert "<code>docker ps</code>" in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>" not in html


def test_markdown_card_renders_pipe_tables_as_html_tables():
    rendered = build_markdown_card_html("| 项目 | 状态 |\n" "|:-----|:----:|\n" "| WebUI | 正常 |")

    assert "<table>" in rendered
    assert "<thead>" in rendered
    assert '<th align="left">项目</th>' in rendered
    assert '<th align="center">状态</th>' in rendered
    assert '<td align="left">WebUI</td>' in rendered
    assert '<p class="table-line">' not in rendered


def test_markdown_card_renders_supported_horizontal_rule_variants():
    rendered = build_markdown_card_html("前一段\n\n—-\n\n后一段")

    assert "<hr>" in rendered
    assert "—-" not in rendered
