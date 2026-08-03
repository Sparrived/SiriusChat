from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from sirius_pulse.github.events import fetch_compare_commit_count, fetch_compare_details
from sirius_pulse.tools.builtin import github_monitor


class _Client:
    def __init__(self, **_: Any) -> None:
        pass

    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None


class _Response:
    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload


@pytest.mark.asyncio
async def test_poll_recovers_from_persisted_pre_restart_timestamp(monkeypatch):
    data: dict[str, Any] = {
        "poll_seconds": 30,
        "repos": [
            {
                "owner": "Sparrived",
                "repo": "SiriusPulse",
                "mode": "poll",
                "events": ["pushes"],
                "groups": ["1057020972"],
            }
        ],
        "_last_poll_at": {"Sparrived/SiriusPulse": 10_210_977.687},
    }
    store = Mock()
    store.get.side_effect = lambda key, default=None: data.get(key, default)
    store.set.side_effect = lambda key, value: data.__setitem__(key, value)
    ctx = Mock()
    ctx.get_data_store.return_value = store
    fetch_events = AsyncMock(return_value=[])

    monkeypatch.setattr(github_monitor, "GitHubClient", _Client)
    monkeypatch.setattr(github_monitor, "fetch_repo_events", fetch_events)
    monkeypatch.setattr(github_monitor.time, "time", lambda: 1_754_000_000.0)

    await github_monitor._poll_github_events(ctx)

    fetch_events.assert_awaited_once()
    assert data["_last_poll_at"]["Sparrived/SiriusPulse"] == 1_754_000_000.0


@pytest.mark.asyncio
async def test_compare_api_returns_push_commit_count():
    client = Mock()
    client.get = AsyncMock(return_value=_Response(200, {"total_commits": 3}))

    count = await fetch_compare_commit_count(
        client,
        "Sparrived",
        "SiriusPulse",
        "a" * 40,
        "b" * 40,
        extra_headers={"Authorization": "Bearer test"},
    )

    assert count == 3
    client.get.assert_awaited_once_with(
        "/repos/Sparrived/SiriusPulse/compare/" + "a" * 40 + "..." + "b" * 40,
        headers={"Authorization": "Bearer test"},
    )


@pytest.mark.asyncio
async def test_compare_api_returns_push_details():
    client = Mock()
    payload = {
        "total_commits": 2,
        "commits": [{"sha": "abc123", "commit": {"message": "修复提交通知"}}],
        "files": [{"filename": "sirius_pulse/tools/builtin/github_monitor.py"}],
    }
    client.get = AsyncMock(return_value=_Response(200, payload))

    details = await fetch_compare_details(
        client,
        "Sparrived",
        "SiriusPulse",
        "a" * 40,
        "b" * 40,
    )

    assert details == payload


def test_merge_push_events_does_not_claim_zero_when_commit_count_is_unavailable():
    event = {
        "repo": {"name": "Sparrived/SiriusPulse"},
        "actor": {"login": "Sparrived"},
        "payload": {
            "ref": "refs/heads/master",
            "before": "a" * 40,
            "head": "b" * 40,
        },
    }

    info = github_monitor._merge_push_events([event])

    assert info is not None
    assert info["title"] == "提交数量未知 → master"


def test_merge_push_events_keeps_compare_commit_details_for_prompt():
    event = {
        "repo": {"name": "Sparrived/SiriusPulse"},
        "actor": {"login": "Sparrived"},
        "payload": {
            "ref": "refs/heads/master",
            "before": "a" * 40,
            "head": "b" * 40,
        },
    }

    info = github_monitor._merge_push_events(
        [event],
        commit_count=2,
        commit_details=[
            {
                "sha": "abc123456789",
                "commit": {
                    "message": "修复提交通知\n\n补充 Compare API 详情",
                    "author": {"name": "Sparrived"},
                },
            }
        ],
        changed_files=[
            {
                "filename": "sirius_pulse/tools/builtin/github_monitor.py",
                "status": "modified",
                "additions": 20,
                "deletions": 4,
            }
        ],
    )

    assert info is not None
    section = github_monitor._build_event_section(info)
    assert "提交数: 2" in section
    assert "修复提交通知" in section
    assert "github_monitor.py" in section


@pytest.mark.asyncio
async def test_notification_generation_does_not_inject_screenshot():
    ctx = Mock()
    ctx.get_persona.return_value = Mock(build_system_prompt=Mock(return_value="identity"))
    ctx.get_active_groups.return_value = ["1057020972"]
    ctx.generate_text = AsyncMock(return_value="通知")
    event_info = {
        "repo": "Sparrived/SiriusPulse",
        "type_desc": "推送",
        "actor": "Sparrived",
        "title": "3 个提交 → master",
        "body": "",
        "url": "https://github.com/Sparrived/SiriusPulse",
        "screenshot_url": "C:/artifacts/github_update.png",
    }

    result = await github_monitor._generate_notification_text(ctx, event_info)

    assert result == "通知"
    system_prompt, messages, *_ = ctx.generate_text.await_args.args
    assert "页面截图" not in system_prompt
    assert "image_url" not in str(messages)
    assert "github_update.png" not in str((system_prompt, messages))
    assert messages == [
        {
            "role": "user",
            "content": "（Sparrived/SiriusPulse 仓库有新动态，请播报一下）",
        }
    ]


@pytest.mark.asyncio
async def test_notification_generation_includes_event_commit_details():
    ctx = Mock()
    ctx.get_persona.return_value = Mock(build_system_prompt=Mock(return_value="identity"))
    ctx.get_active_groups.return_value = ["1057020972"]
    ctx.generate_text = AsyncMock(return_value="通知")
    event_info = {
        "repo": "Sparrived/SiriusPulse",
        "type": "PushEvent",
        "type_desc": "推送",
        "actor": "Sparrived",
        "action": "",
        "action_cn": "推送了",
        "branch": "master",
        "commit_count": 2,
        "commits": [
            {
                "sha": "abc123456789",
                "commit": {
                    "message": "修复提交通知",
                    "author": {"name": "Sparrived"},
                },
            }
        ],
        "changed_files": [
            {
                "filename": "sirius_pulse/tools/builtin/github_monitor.py",
                "status": "modified",
                "additions": 20,
                "deletions": 4,
            }
        ],
        "title": "2 个提交 → master",
        "url": "https://github.com/Sparrived/SiriusPulse/compare/a...b",
        "screenshot_url": "C:/artifacts/github_update.png",
    }

    await github_monitor._generate_notification_text(ctx, event_info)

    system_prompt = ctx.generate_text.await_args.args[0]
    assert "提交数: 2" in system_prompt
    assert "修复提交通知" in system_prompt
    assert "github_monitor.py" in system_prompt
    assert "github_update.png" not in system_prompt


@pytest.mark.asyncio
async def test_dispatch_notification_keeps_screenshot_for_group_delivery():
    ctx = Mock()
    ctx.emit_event = AsyncMock()
    screenshot_path = "C:/artifacts/github_update.png"

    await github_monitor._dispatch_notification(ctx, "1057020972", "通知", screenshot_path)

    ctx.queue_pending_message.assert_called_once_with("1057020972", "通知")
    ctx.emit_event.assert_awaited_once_with(
        "reminder_triggered",
        {
            "group_id": "1057020972",
            "reply": "通知",
            "image_path": screenshot_path,
            "adapter_type": "napcat",
        },
    )
