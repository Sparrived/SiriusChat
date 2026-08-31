"""Project crontab storage and scheduling for the built-in Bash tool."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from sirius_pulse.memory.user.unified_models import UnifiedUser
from sirius_pulse.tools import cron_tasks
from sirius_pulse.tools.models import BackgroundTaskSpec, ToolInvocationContext

logger = logging.getLogger(__name__)
CommandRunner = Callable[..., Awaitable[dict[str, Any]]]


def create_background_tasks(ctx: Any, run_command: CommandRunner) -> list[BackgroundTaskSpec]:
    async def _check() -> None:
        await check_tasks(ctx, run_command)

    return [
        BackgroundTaskSpec(
            name="bash_cron_check",
            interval_seconds=max(
                1.0, float(ctx.get_config_value("cron_check_interval_seconds", 10))
            ),
            task_func=_check,
        )
    ]


def handle_request(
    request: dict[str, Any],
    *,
    cwd: Path,
    data_store: Any,
    chat_context: dict[str, Any] | None,
    engine_context: Any,
    invocation_context: ToolInvocationContext | None,
) -> dict[str, Any]:
    context = dict(chat_context or {})
    group_id = str(context.get("group_id") or context.get("chat_id") or "").strip()
    if not group_id:
        return {"success": False, "error": "crontab 任务必须绑定当前聊天会话"}

    jobs = [
        job
        for job in (data_store.get("cron_jobs", []) if data_store else [])
        if isinstance(job, dict)
    ]
    owner_id = (
        invocation_context.caller_user_id if invocation_context else str(context.get("user_id", ""))
    )
    owner_name = invocation_context.caller_name if invocation_context else ""
    adapter_type = str(context.get("adapter_type", ""))
    if not adapter_type and engine_context is not None:
        getter = getattr(engine_context, "get_current_adapter_type", None)
        if callable(getter):
            adapter_type = str(getter() or "")

    scoped = [job for job in jobs if job.get("group_id") == group_id]
    action = request.get("action")
    if action == "list":
        if not scoped:
            return {
                "success": True,
                "summary": "当前聊天没有定时任务",
                "text_blocks": ["当前没有定时任务。"],
            }
        lines = [f"{job.get('expression', '')} {job.get('command', '')}".strip() for job in scoped]
        return {
            "success": True,
            "summary": f"列出 {len(scoped)} 个定时任务（标准 crontab 格式）",
            "text_blocks": ["\n".join(lines)],
        }

    if action == "remove":
        kept = [job for job in jobs if job.get("group_id") != group_id]
        removed = len(jobs) - len(kept)
        data_store.set("cron_jobs", kept)
        data_store.save()
        return {
            "success": True,
            "summary": f"已删除 {removed} 个定时任务",
            "text_blocks": [f"✅ 已删除当前聊天的 {removed} 个定时任务。"],
        }

    created: list[dict[str, Any]] = []
    replacement = [job for job in jobs if job.get("group_id") != group_id]
    for entry in request.get("entries", []):
        job = {
            "id": f"cron_{uuid.uuid4().hex[:12]}",
            "schedule_type": "cron",
            "expression": entry["expression"],
            "command": entry["command"],
            "cwd": str(cwd),
            "group_id": group_id,
            "owner_user_id": owner_id,
            "owner_name": owner_name,
            "owner_is_developer": bool(
                invocation_context and invocation_context.caller_is_developer
            ),
            "adapter_type": adapter_type,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_run_key": "",
            "run_count": 0,
        }
        replacement.append(job)
        created.append(job)
    data_store.set("cron_jobs", replacement)
    data_store.save()
    lines = ["✅ 已注册内部定时任务："]
    lines.extend(f"[{job['id']}] {job['expression']} {job['command']}" for job in created)
    return {
        "success": True,
        "summary": f"已注册 {len(created)} 个定时任务",
        "text_blocks": ["\n".join(lines)],
    }


async def check_tasks(ctx: Any, run_command: CommandRunner) -> None:
    store = ctx.get_data_store("bash")
    jobs = [job for job in (store.get("cron_jobs", []) or []) if isinstance(job, dict)]
    if not jobs:
        return

    now = datetime.now(cron_tasks.CRON_TIMEZONE)
    changed = False
    remaining: list[dict[str, Any]] = []
    for job in jobs:
        if not job.get("group_id") or not job.get("command"):
            changed = True
            continue
        due, run_key = cron_tasks.task_is_due(job, now)
        if not due or job.get("last_run_key") == run_key:
            remaining.append(job)
            continue

        job["last_run_key"] = run_key
        job["run_count"] = int(job.get("run_count", 0) or 0) + 1
        changed = True
        try:
            await _run_job(ctx, job, run_command)
        except Exception:
            logger.exception("内部 cron 任务执行失败: %s", job.get("id", "unknown"))
        remaining.append(job)

    if changed:
        store.set("cron_jobs", remaining)
        store.save()


async def _run_job(ctx: Any, job: dict[str, Any], run_command: CommandRunner) -> None:
    group_id = str(job.get("group_id", ""))
    user_id = str(job.get("owner_user_id", ""))
    user_name = str(job.get("owner_name", ""))
    adapter_type = str(job.get("adapter_type", ""))
    if not adapter_type:
        getter = getattr(ctx, "get_current_adapter_type", None)
        if callable(getter):
            adapter_type = str(getter() or "")
    caller = UnifiedUser(
        user_id=user_id,
        name=user_name,
        metadata={"is_developer": bool(job.get("owner_is_developer", False))},
    )
    invocation_context = ToolInvocationContext(caller=caller)
    chat_context = {
        "group_id": group_id,
        "chat_id": group_id.replace("private_", "").replace("qq_", "")
        if group_id.startswith("private_")
        else group_id,
        "chat_type": "private" if group_id.startswith("private_") else "group",
        "user_id": user_id,
        "adapter_type": adapter_type,
    }
    result = await run_command(
        str(job["command"]),
        cwd=str(job.get("cwd") or "."),
        timeout_seconds=10.0,
        max_output_chars=12_000,
        data_store=ctx.get_data_store("bash"),
        chat_context=chat_context,
        engine_context=ctx,
        invocation_context=invocation_context,
        _skip_crontab=True,
    )
    output = (
        result.get("text_blocks", [""])[0] if result.get("success") else result.get("error", "")
    )
    if not output:
        output = "命令执行成功，但没有输出。"
    message = await ctx.generate_scheduled_message(
        job=job,
        command_output=str(output),
        group_id=group_id,
        user_id=user_id,
        user_name=user_name,
        adapter_type=adapter_type,
        caller_is_developer=bool(job.get("owner_is_developer", False)),
    )
    payload = message if isinstance(message, dict) else {"text": str(message)}
    text = str(payload.get("text", "")).strip()
    sticker_names = payload.get("sticker_names", [])
    poke_user_ids = payload.get("poke_user_ids", [])
    if text or sticker_names or poke_user_ids:
        await ctx.dispatch_proactive_message(
            group_id=group_id,
            text=text,
            adapter_type=adapter_type,
            event_id=str(job.get("id", "")),
            reply_references=payload.get("reply_references", []),
            sticker_names=sticker_names,
            poke_user_ids=poke_user_ids,
        )
