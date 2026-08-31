"""Internal cron task parsing and due-time helpers for the Bash tool."""

from __future__ import annotations

import re
import shlex
from datetime import datetime, timedelta, timezone
from typing import Any

CRON_TIMEZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")
CRON_FIELD_COUNT = 5
_CRONTAB_RE = re.compile(r"(?:^|[|;&])\s*crontab(?:\s|$)")
_CRONTAB_LIST = r"crontab\s+-l(?:\s+2>/dev/null)?"
_CRONTAB_LIST_WITH_PROBE = _CRONTAB_LIST + r"(?:\s*;\s*echo\s+[\"']---exit:\s*\$\?\s*---[\"'])?"
_FIELD_NAMES = {
    3: {
        "JAN": 1,
        "FEB": 2,
        "MAR": 3,
        "APR": 4,
        "MAY": 5,
        "JUN": 6,
        "JUL": 7,
        "AUG": 8,
        "SEP": 9,
        "OCT": 10,
        "NOV": 11,
        "DEC": 12,
    },
    4: {"SUN": 0, "MON": 1, "TUE": 2, "WED": 3, "THU": 4, "FRI": 5, "SAT": 6},
}


class CronParseError(ValueError):
    """Raised when a project crontab command or expression is unsupported."""


def contains_crontab(command: str) -> bool:
    return bool(_CRONTAB_RE.search(command))


def parse_crontab_command(command: str) -> dict[str, Any] | None:
    """Parse the small, model-friendly crontab command surface.

    Supported forms intentionally mirror common Linux examples while keeping
    shell execution out of the registration path:
    ``crontab -l``, ``crontab -r``, and ``echo/printf ... | crontab -``.
    """
    text = command.strip()
    if not contains_crontab(text):
        return None
    if re.fullmatch(_CRONTAB_LIST_WITH_PROBE, text):
        return {"action": "list"}
    if re.fullmatch(r"crontab\s+-r", text):
        return {"action": "remove"}

    match = re.fullmatch(
        rf"(.+?)\s*\|\s*crontab\s+-(?:\s*(?:&&|;)\s*{_CRONTAB_LIST})?",
        text,
        flags=re.DOTALL,
    )
    if not match:
        raise CronParseError("仅支持 crontab -l、crontab -r，以及 echo/printf 'cron条目' | crontab -")

    try:
        producer = shlex.split(match.group(1), posix=True)
    except ValueError as exc:
        raise CronParseError(f"crontab 输入命令解析失败: {exc}") from exc
    if not producer or producer[0] not in {"echo", "printf"}:
        raise CronParseError("crontab - 只接受 echo 或 printf 生成的定时条目")

    if producer[0] == "echo":
        values = producer[1:]
        if not values:
            raise CronParseError("crontab 条目不能为空")
        payload = " ".join(values)
    else:
        if len(producer) >= 3 and "%s" in producer[1]:
            values = producer[2:]
            payload = "\n".join(values) if "\\n" in producer[1] else " ".join(values)
        elif len(producer) == 2 and producer[1]:
            payload = producer[1].replace("\\n", "\n")
        else:
            raise CronParseError("printf 形式应为 printf '%s\\n' 'cron条目' | crontab -")
        if not payload:
            raise CronParseError("crontab 条目不能为空")

    entries = []
    for line in payload.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        expression, scheduled_command = parse_crontab_entry(line)
        entries.append({"expression": expression, "command": scheduled_command})
    if not entries:
        raise CronParseError("crontab 条目不能为空")
    return {"action": "install", "entries": entries}


def parse_crontab_entry(line: str) -> tuple[str, str]:
    parts = line.split(None, 5)
    if len(parts) != 6:
        raise CronParseError("cron 条目必须是五个时间字段加一条命令")
    expression = " ".join(parts[:5])
    command = parts[5].strip()
    validate_expression(expression)
    if not command:
        raise CronParseError("cron 条目缺少要执行的命令")
    return expression, command


def validate_expression(expression: str) -> None:
    parts = expression.split()
    if len(parts) != CRON_FIELD_COUNT:
        raise CronParseError("目前只支持五字段 cron 表达式：分 时 日 月 周")
    limits = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
    for index, (field, (minimum, maximum)) in enumerate(zip(parts, limits)):
        _parse_field(field, minimum, maximum, _FIELD_NAMES.get(index))


def cron_matches(expression: str, now: datetime | None = None) -> bool:
    now = (now or datetime.now(CRON_TIMEZONE)).astimezone(CRON_TIMEZONE)
    parts = expression.split()
    if len(parts) != CRON_FIELD_COUNT:
        return False
    values = (now.minute, now.hour, now.day, now.month, (now.weekday() + 1) % 7)
    matches = [
        _field_matches(field, value, minimum, maximum, _FIELD_NAMES.get(index))
        for index, (field, value, (minimum, maximum)) in enumerate(
            zip(parts, values, ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7)))
        )
    ]
    if not (matches[0] and matches[1] and matches[3]):
        return False
    # The standard rule checks whether the field is literally '*'. A stepped
    # wildcard such as '*/2' is still a restricted field.
    day_of_month_wild = parts[2] == "*"
    day_of_week_wild = parts[4] == "*"
    if day_of_month_wild and day_of_week_wild:
        return True
    if day_of_month_wild:
        return matches[4]
    if day_of_week_wild:
        return matches[2]
    return matches[2] or matches[4]


def task_is_due(job: dict[str, Any], now: datetime | None = None) -> tuple[bool, str]:
    now = (now or datetime.now(CRON_TIMEZONE)).astimezone(CRON_TIMEZONE)
    expression = str(job.get("expression", ""))
    key = f"cron:{now:%Y%m%d%H%M}"
    return cron_matches(expression, now), key


def _field_matches(
    field: str,
    value: int,
    minimum: int,
    maximum: int,
    names: dict[str, int] | None,
) -> bool:
    try:
        values = _parse_field(field, minimum, maximum, names)
        if names is _FIELD_NAMES[4]:
            values = {0 if item == 7 else item for item in values}
        return value in values
    except CronParseError:
        return False


def _parse_field(
    field: str,
    minimum: int,
    maximum: int,
    names: dict[str, int] | None = None,
) -> set[int]:
    result: set[int] = set()
    for raw_part in field.upper().split(","):
        part = raw_part.strip()
        if not part:
            raise CronParseError(f"cron 字段无效: {field}")
        base, separator, raw_step = part.partition("/")
        if separator:
            if base != "*" and "-" not in base:
                raise CronParseError(f"cron 步长必须作用于范围或 *: {field}")
            try:
                step = int(raw_step)
            except ValueError as exc:
                raise CronParseError(f"cron 步长无效: {field}") from exc
            if step <= 0:
                raise CronParseError(f"cron 步长必须大于 0: {field}")
        else:
            step = 1

        if base == "*":
            start, end = minimum, maximum
        elif "-" in base:
            raw_start, raw_end = base.split("-", 1)
            start = _field_value(raw_start, names)
            end = _field_value(raw_end, names)
        else:
            start = _field_value(base, names)
            end = maximum if separator else start

        if start < minimum or end > maximum or start > end:
            raise CronParseError(f"cron 字段超出范围: {field}")
        result.update(range(start, end + 1, step))
    return result


def _field_value(value: str, names: dict[str, int] | None) -> int:
    if names and value in names:
        return names[value]
    try:
        return int(value)
    except ValueError as exc:
        raise CronParseError(f"cron 字段值无效: {value}") from exc
