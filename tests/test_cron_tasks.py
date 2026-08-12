from __future__ import annotations

from datetime import datetime

import pytest

from sirius_pulse.tools import cron_tasks
from sirius_pulse.tools.builtin import bash
from sirius_pulse.tools.models import ToolInvocationContext
from sirius_pulse.memory.user.unified_models import UnifiedUser


def test_parse_echo_crontab_install_command():
    request = cron_tasks.parse_crontab_command(
        "echo '*/5 * * * * echo hello' | crontab -"
    )

    assert request == {
        "action": "install",
        "entries": [{"expression": "*/5 * * * *", "command": "echo hello"}],
    }


def test_parse_printf_crontab_install_command():
    request = cron_tasks.parse_crontab_command(
        "printf '%s\\n' '0 8 * * 1-5 echo weekday' | crontab -"
    )

    assert request["entries"][0]["expression"] == "0 8 * * 1-5"


def test_parse_crontab_list_and_remove():
    assert cron_tasks.parse_crontab_command("crontab -l") == {"action": "list"}
    assert cron_tasks.parse_crontab_command("crontab -r") == {"action": "remove"}


def test_cron_matches_common_fields_and_or_day_semantics():
    monday = datetime(2026, 8, 10, 8, 0, tzinfo=cron_tasks.CRON_TIMEZONE)
    assert cron_tasks.cron_matches("0 8 * * 1-5", monday)
    assert cron_tasks.cron_matches("0 8 10 * 0", monday)
    assert not cron_tasks.cron_matches("1 8 * * 1-5", monday)


def test_cron_matches_sunday_as_zero_or_seven():
    sunday = datetime(2026, 8, 9, 8, 0, tzinfo=cron_tasks.CRON_TIMEZONE)

    assert cron_tasks.cron_matches("0 8 * * 0", sunday)
    assert cron_tasks.cron_matches("0 8 * * 7", sunday)


def test_cron_day_or_rule_treats_step_wildcards_as_restricted():
    odd_day = datetime(2026, 8, 11, 8, 0, tzinfo=cron_tasks.CRON_TIMEZONE)
    even_day = datetime(2026, 8, 10, 8, 0, tzinfo=cron_tasks.CRON_TIMEZONE)

    assert cron_tasks.cron_matches("0 8 */2 * *", odd_day) is True
    assert cron_tasks.cron_matches("0 8 */2 * 0", even_day) is False


def test_cron_rejects_seconds_field():
    with pytest.raises(cron_tasks.CronParseError, match="五字段"):
        cron_tasks.validate_expression("0 0 8 * * *")


def test_crontab_text_does_not_intercept_an_unrelated_shell_command():
    assert cron_tasks.parse_crontab_command("echo crontab") is None


class _CronStore:
    def __init__(self):
        self.data = {}
        self.saved = 0

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value

    def save(self):
        self.saved += 1


@pytest.mark.asyncio
async def test_bash_crontab_registers_lists_and_removes_jobs(tmp_path):
    store = _CronStore()
    invocation = ToolInvocationContext(
        caller=UnifiedUser(user_id="u1", name="Alice", metadata={"is_developer": False})
    )
    context = {"group_id": "group-1", "user_id": "u1", "adapter_type": "napcat"}

    installed = await bash.run(
        "echo '*/5 * * * * echo hello' | crontab -",
        cwd=str(tmp_path),
        data_store=store,
        chat_context=context,
        invocation_context=invocation,
    )
    listed = await bash.run(
        "crontab -l",
        data_store=store,
        chat_context=context,
        invocation_context=invocation,
    )
    removed = await bash.run(
        "crontab -r",
        data_store=store,
        chat_context=context,
        invocation_context=invocation,
    )

    assert installed["success"] is True
    assert listed["success"] is True
    assert "*/5 * * * * echo hello" in listed["text_blocks"][0]
    assert removed["success"] is True
    assert store.data["cron_jobs"] == []


@pytest.mark.asyncio
async def test_bash_crontab_stdin_replaces_the_current_chat_crontab(tmp_path):
    store = _CronStore()
    context = {"group_id": "group-1", "adapter_type": "napcat"}
    invocation = ToolInvocationContext(
        caller=UnifiedUser(user_id="u1", name="Alice", metadata={})
    )

    await bash.run(
        "echo '0 8 * * * echo old' | crontab -",
        cwd=str(tmp_path),
        data_store=store,
        chat_context=context,
        invocation_context=invocation,
    )
    await bash.run(
        "echo '30 9 * * * echo new' | crontab -",
        cwd=str(tmp_path),
        data_store=store,
        chat_context=context,
        invocation_context=invocation,
    )

    assert len(store.data["cron_jobs"]) == 1
    assert store.data["cron_jobs"][0]["command"] == "echo new"


def test_cron_rejects_unsupported_expression():
    try:
        cron_tasks.validate_expression("@daily")
    except cron_tasks.CronParseError:
        pass
    else:
        raise AssertionError("@daily should be rejected by the five-field parser")
