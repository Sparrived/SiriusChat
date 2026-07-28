import base64
from pathlib import Path
from typing import Any

import pytest

from sirius_pulse.skills.builtin import _markdown_image
from sirius_pulse.skills.builtin._markdown_image import (
    build_markdown_card_html,
    merge_markdown_blocks,
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
    assert "<h2>概览</h2>" in html
    assert "<ul>" in html
    assert "<strong>服务</strong>" in html
    assert "<code>docker ps</code>" in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>" not in html
