"""人格可调用的 Bash 工具。"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sirius_pulse.config.config_builder import ConfigBuilder
from sirius_pulse.tools.builtin import _container_status_card, _docker_cli
from sirius_pulse.tools import cron_tasks
from sirius_pulse.tools.models import ToolInvocationContext

logger = logging.getLogger(__name__)

_SENSITIVE_ENV = re.compile(
    r"(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH|COOKIE|SESSION)", re.IGNORECASE
)
_DEFAULT_MAX_TIMEOUT = 15.0
_DEFAULT_MAX_OUTPUT = 12_000
_MAX_COMMAND_LENGTH = 4_000
_MIN_OUTPUT = 256
_RUNTIME_DIR_NAME = "runtime"
_RUNTIME_BIN_DIR = "bin"
_RUNTIME_PYTHON_DIR = "python"
_RUNTIME_NODE_DIR = "node"
_RUNTIME_CACHE_DIR = "cache"
_DOCKER_FUNCTION_TEMPLATE = """docker() {{
    {python_executable} -m sirius_pulse.tools.builtin._docker_cli \"$@\"
}}
docker-compose() {{
    {python_executable} -m sirius_pulse.tools.builtin._docker_cli compose \"$@\"
}}
"""

_config = ConfigBuilder()
_config.group("Bash 执行").add(
    "command",
    type="str",
    description=(
        "要执行的 Bash 命令，支持管道、重定向、here-document、变量和命令替换。"
        "也支持项目级 crontab：crontab -l 查看、crontab -r 删除，"
        "以及 printf '%s\\n' '分 时 日 月 周 命令' | crontab - 注册内部定时任务。"
        "cron 只接受 Linux crontab 的五个字段，不支持秒字段。"
    ),
    required=True,
)
_config.group("Bash 执行").add(
    "cwd",
    type="str",
    description="容器内工作目录；可使用绝对路径，默认为当前进程目录。",
    default=".",
)
_config.group("Bash 执行").add(
    "timeout_seconds",
    type="float",
    description="本次命令的超时时间；实际值不会超过人格配置的上限。",
    default=10.0,
)
_config.group("Bash 执行").add(
    "max_output_chars",
    type="int",
    description="最多返回多少字符；实际值不会超过人格配置的上限。",
    default=8_000,
)

TOOL_META = {
    "name": "bash",
    "description": (
        "在当前容器中启动 Bash，用于文件处理、系统状态查询和自动化。优先使用该工具操作系统。"
        "支持标准 Bash 语法与容器内任意工作目录。访问其他容器时，docker/docker-compose 仍经宿主机 Unix Socket 代理，支持常规 Docker 命令和完整 docker exec；"
        "Bash 不在宿主机，不能使用 systemctl 或宿主机 /var/log；"
        "docker inspect 会在当前 QQ 会话发送容器状态卡片。"
        "Bash 还提供项目级 crontab 兼容调度：crontab -l 查看当前聊天任务，"
        "crontab -r 删除当前聊天任务，使用 echo/printf '五字段 cron + 命令' | crontab - 注册任务。"
        "表达式严格按 Linux crontab 五字段解释，不支持秒字段；"
        "它不会修改操作系统 crontab；任务由 Sirius 内部调度器持久化并执行，"
        "命令输出会作为上下文在原聊天中生成主动消息。"
    ),
    "version": "1.5.0",
    "side_effect": "unknown",
    "tags": ["bash", "shell", "file", "system", "container", "cron", "schedule"],
    "parameters": _config.build(),
    "config": {
        "max_timeout_seconds": {
            "type": "number",
            "description": "单次 Bash 的最大执行时间，范围 1 到 60 秒。",
            "default": _DEFAULT_MAX_TIMEOUT,
            "group": "限制",
        },
        "max_output_chars": {
            "type": "int",
            "description": "单次 Bash 返回的最大字符数，范围 256 到 50000。",
            "default": _DEFAULT_MAX_OUTPUT,
            "group": "限制",
        },
    },
}


def create_background_tasks(ctx: Any) -> list[Any]:
    """Register the internal crontab-compatible scheduler under Bash."""
    from sirius_pulse.tools.models import BackgroundTaskSpec

    async def _check() -> None:
        await _check_cron_tasks(ctx)

    return [
        BackgroundTaskSpec(
            name="bash_cron_check",
            interval_seconds=max(1.0, float(ctx.get_config_value("cron_check_interval_seconds", 10))),
            task_func=_check,
        )
    ]


async def run(
    command: str,
    cwd: str = ".",
    timeout_seconds: float = 10.0,
    max_output_chars: int = 8_000,
    data_store: Any = None,
    chat_context: dict[str, Any] | None = None,
    engine_context: Any = None,
    invocation_context: ToolInvocationContext | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Execute one Bash command with a Docker proxy function for other containers."""
    if data_store is not None and data_store.get("_enabled", True) is False:
        return {"success": False, "error": "bash Tool 已被当前人格禁用"}

    policy = _load_policy(data_store)
    try:
        command_text = _validate_command(command)
        cwd_path = _resolve_cwd(cwd)
        timeout = _bounded_number(
            timeout_seconds, default=10.0, minimum=0.1, maximum=policy["max_timeout_seconds"]
        )
        output_limit = int(
            _bounded_number(
                max_output_chars,
                default=8_000,
                minimum=_MIN_OUTPUT,
                maximum=policy["max_output_chars"],
            )
        )
    except ValueError as exc:
        return {"success": False, "error": str(exc)}

    if not kwargs.get("_skip_crontab", False):
        try:
            cron_request = cron_tasks.parse_crontab_command(command_text)
        except cron_tasks.CronParseError as exc:
            return {"success": False, "error": str(exc)}
        if cron_request is not None:
            if data_store is None:
                return {"success": False, "error": "crontab 需要可持久化的 Bash 数据存储"}
            return _handle_crontab_request(
                cron_request,
                cwd=cwd_path,
                data_store=data_store,
                chat_context=chat_context,
                engine_context=engine_context,
                invocation_context=invocation_context,
            )

    bash = _find_bash()
    if not bash:
        return {
            "success": False,
            "error": "系统未找到 Bash；请安装 Bash 或设置 SIRIUS_BASH_PATH。",
        }

    try:
        environment = _runtime_environment(data_store)
    except OSError as exc:
        return {"success": False, "error": f"准备人格运行时目录失败: {exc}"}

    try:
        completed = await asyncio.to_thread(
            subprocess.run,
            [bash, "-o", "pipefail", "-lc", f"{_docker_function()}\n{command_text}"],
            cwd=str(cwd_path),
            env=environment,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        partial = _decode_output((exc.stdout or b"") + (exc.stderr or b""), output_limit)
        detail = f"命令执行超时（上限 {timeout:g} 秒）"
        if partial:
            detail += f"\n部分输出:\n{partial}"
        return {"success": False, "error": detail}
    except OSError as exc:
        return {"success": False, "error": f"启动 Bash 失败: {exc}"}

    output, inspect_statuses, truncated = _decode_output_with_inspect_status(
        completed.stdout + completed.stderr, output_limit
    )
    metadata = {
        "cwd": str(cwd_path),
        "returncode": completed.returncode,
        "command_length": len(command_text),
        "docker_bridge_enabled": True,
        "truncated": truncated,
    }
    if completed.returncode != 0:
        detail = f"命令退出码 {completed.returncode}"
        if output:
            detail += f"\n{output}"
        detail += _container_recovery_hint(command_text)
        return {"success": False, "error": detail, "internal_metadata": metadata}

    cards = await _send_inspect_status_cards(
        inspect_statuses,
        data_store=data_store,
        chat_context=chat_context,
        engine_context=engine_context,
        invocation_context=invocation_context,
    )
    metadata["inspect_statuses"] = [item["status"] for item in cards if item.get("status")]
    metadata["status_cards"] = cards
    sent_count = sum(1 for item in cards if item["sent"])
    card_errors = [str(item["error"]) for item in cards if item["error"]]
    text_parts = [output or "命令执行成功，但没有输出。"]
    text_parts.extend(
        _container_status_card.status_summary(item["status"])
        for item in cards
        if item.get("status")
    )
    if card_errors:
        text_parts.append("状态卡片未发送：" + "；".join(card_errors))
    summary = f"Bash 执行完成（退出码 0，工作目录 {cwd_path}）"
    if sent_count:
        summary += f"；已发送 {sent_count} 张容器状态卡片"

    return {
        "success": True,
        "summary": summary,
        "text_blocks": ["\n".join(text_parts)],
        "internal_metadata": metadata,
    }


def _handle_crontab_request(
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
        invocation_context.caller_user_id
        if invocation_context
        else str(context.get("user_id", ""))
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
        lines = [
            f"{job.get('expression', '')} {job.get('command', '')}".strip()
            for job in scoped
        ]
        return {
            "success": True,
            "summary": f"列出 {len(scoped)} 个定时任务（标准 crontab 格式）",
            "text_blocks": ["\n".join(lines)],
        }

    if action == "remove":
        kept = [job for job in jobs if job.get("group_id") != group_id]
        removed = len(jobs) - len(kept)
        if data_store is not None:
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
    if data_store is not None:
        data_store.set("cron_jobs", replacement)
        data_store.save()
    lines = ["✅ 已注册内部定时任务："]
    lines.extend(f"[{job['id']}] {job['expression']} {job['command']}" for job in created)
    return {
        "success": True,
        "summary": f"已注册 {len(created)} 个定时任务",
        "text_blocks": ["\n".join(lines)],
    }


async def _check_cron_tasks(ctx: Any) -> None:
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
            await _run_cron_job(ctx, job)
        except Exception:
            logger.exception("内部 cron 任务执行失败: %s", job.get("id", "unknown"))
        remaining.append(job)

    if changed:
        store.set("cron_jobs", remaining)
        store.save()


async def _run_cron_job(ctx: Any, job: dict[str, Any]) -> None:
    from sirius_pulse.memory.user.unified_models import UnifiedUser

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
    result = await run(
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
    output = result.get("text_blocks", [""])[0] if result.get("success") else result.get("error", "")
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


def _load_policy(data_store: Any) -> dict[str, Any]:
    reload_store = getattr(data_store, "reload", None)
    if callable(reload_store):
        reload_store()

    max_timeout = _bounded_number(
        (
            data_store.get("max_timeout_seconds", _DEFAULT_MAX_TIMEOUT)
            if data_store
            else _DEFAULT_MAX_TIMEOUT
        ),
        default=_DEFAULT_MAX_TIMEOUT,
        minimum=1.0,
        maximum=60.0,
    )
    max_output = int(
        _bounded_number(
            (
                data_store.get("max_output_chars", _DEFAULT_MAX_OUTPUT)
                if data_store
                else _DEFAULT_MAX_OUTPUT
            ),
            default=_DEFAULT_MAX_OUTPUT,
            minimum=_MIN_OUTPUT,
            maximum=50_000,
        )
    )
    return {
        "max_timeout_seconds": max_timeout,
        "max_output_chars": max_output,
    }


def _validate_command(command: str) -> str:
    text = str(command or "").strip()
    if not text:
        raise ValueError("command 不能为空")
    if len(text) > _MAX_COMMAND_LENGTH:
        raise ValueError(f"command 过长，最多 {_MAX_COMMAND_LENGTH} 个字符")
    if "\0" in text:
        raise ValueError("command 不能包含空字节")
    return text


def _resolve_cwd(cwd: str) -> Path:
    requested = str(cwd or ".").strip() or "."
    resolved = Path(requested).expanduser().resolve()
    if not resolved.is_dir():
        raise ValueError(f"cwd 不是目录: {cwd}")
    return resolved


def _find_bash() -> str | None:
    configured = os.environ.get("SIRIUS_BASH_PATH", "").strip()
    return configured or shutil.which("bash")


def _docker_function() -> str:
    """Build the Docker shell function with the active Sirius interpreter."""
    return _DOCKER_FUNCTION_TEMPLATE.format(python_executable=shlex.quote(sys.executable))


async def _send_inspect_status_cards(
    statuses: list[dict[str, Any]],
    *,
    data_store: Any,
    chat_context: dict[str, Any] | None,
    engine_context: Any,
    invocation_context: ToolInvocationContext | None,
) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for raw_status in statuses:
        status = _container_status_card.normalize_status(raw_status)
        record: dict[str, Any] = {"status": status, "sent": False, "error": "", "message_id": None}
        cards.append(record)
        if status is None:
            record["error"] = "Docker 代理未返回有效的容器状态"
            continue
        if not _chat_target(chat_context)[1]:
            record["error"] = "当前调用没有 QQ 会话上下文"
            continue
        registry = getattr(engine_context, "tool_registry", None)
        executor = getattr(engine_context, "tool_executor", None)
        if registry is None or executor is None:
            record["error"] = "Tool 运行上下文未就绪，无法发送状态卡片"
            continue
        group_file_exec = registry.get("group_file_exec")
        if group_file_exec is None:
            record["error"] = "未找到 group_file_exec Tool，无法发送状态卡片"
            continue
        try:
            image_path = await _container_status_card.render_status_card(status, data_store)
            record["card_path"] = str(image_path)
            sent = await executor.execute_async(
                group_file_exec,
                {"action": "image", "image_path": str(image_path)},
                invocation_context=invocation_context,
            )
            if sent.success:
                record["sent"] = True
                record["message_id"] = sent.internal_metadata.get("message_id")
            else:
                record["error"] = sent.error or "状态卡片发送失败"
        except Exception as exc:
            record["error"] = str(exc)
    return cards


def _chat_target(chat_context: dict[str, Any] | None) -> tuple[str, str]:
    context = chat_context or {}
    chat_type = str(context.get("chat_type") or "").strip()
    if chat_type not in {"group", "private"}:
        return "", ""
    return chat_type, str(context.get("chat_id") or context.get("group_id") or "").strip()


def _container_recovery_hint(command: str) -> str:
    text = command.lower()
    if not any(token in text for token in ("docker", "minecraft", "systemctl")):
        return ""
    return (
        "\n容器排障请继续使用受支持命令，不要在 Sirius 容器中使用 systemctl 或宿主机 /var/log："
        "\n1. docker ps -a"
        "\n2. docker inspect <容器名称>"
        "\n3. docker logs --tail 200 <容器名称>"
        "\n4. docker exec <容器名称> tail -n 200 /data/logs/latest.log"
        "\n5. docker exec <容器名称> ls -lt /data/crash-reports"
    )


def _safe_environment() -> dict[str, str]:
    keep = {
        "HOME",
        "LANG",
        "LC_ALL",
        "PATH",
        "SIRIUS_CONTAINER_ADMIN_SOCKET",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "USER",
        "USERPROFILE",
    }
    return {
        key: value
        for key, value in os.environ.items()
        if key.upper() in keep and not _SENSITIVE_ENV.search(key)
    }


def _runtime_environment(data_store: Any) -> dict[str, str]:
    environment = _safe_environment()
    runtime_root = _runtime_root(data_store)
    if runtime_root is None:
        return environment

    runtime_bin = runtime_root / _RUNTIME_BIN_DIR
    runtime_python = runtime_root / _RUNTIME_PYTHON_DIR
    runtime_node = runtime_root / _RUNTIME_NODE_DIR
    runtime_cache = runtime_root / _RUNTIME_CACHE_DIR
    for directory in (
        runtime_bin,
        runtime_python,
        runtime_node,
        runtime_node / "bin",
        runtime_cache,
        runtime_cache / "pip",
        runtime_cache / "npm",
        runtime_cache / "go",
    ):
        directory.mkdir(parents=True, exist_ok=True)

    environment["SIRIUS_RUNTIME_ROOT"] = str(runtime_root)
    environment["SIRIUS_RUNTIME_BIN"] = str(runtime_bin)
    environment["PIP_TARGET"] = str(runtime_python)
    environment["PIP_CACHE_DIR"] = str(runtime_cache / "pip")
    environment["NPM_CONFIG_PREFIX"] = str(runtime_node)
    environment["NPM_CONFIG_CACHE"] = str(runtime_cache / "npm")
    environment["GOBIN"] = str(runtime_bin)
    environment["GOMODCACHE"] = str(runtime_cache / "go")
    environment["PATH"] = os.pathsep.join(
        [str(runtime_bin), str(runtime_node / "bin"), environment.get("PATH", "")]
    )
    environment["PYTHONPATH"] = _prepend_environment_path(
        str(runtime_python), environment.get("PYTHONPATH", "")
    )
    return environment


def _runtime_root(data_store: Any) -> Path | None:
    store_path = getattr(data_store, "store_path", None)
    if not store_path:
        return None
    path = Path(store_path).expanduser().resolve()
    if path.parent.name != "tool_data":
        return None
    return path.parent.parent / _RUNTIME_DIR_NAME


def _prepend_environment_path(value: str, existing: str) -> str:
    return os.pathsep.join(item for item in (value, existing) if item)


def _bounded_number(value: Any, *, default: float, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def _decode_output(raw: bytes, limit: int) -> str:
    return _truncate_text(raw.decode("utf-8", errors="replace"), limit)[0]


def _decode_output_with_inspect_status(
    raw: bytes, limit: int
) -> tuple[str, list[dict[str, Any]], bool]:
    statuses: list[dict[str, Any]] = []
    kept_lines: list[str] = []
    for line in raw.decode("utf-8", errors="replace").splitlines(keepends=True):
        if not line.startswith(_docker_cli.INSPECT_STATUS_MARKER):
            kept_lines.append(line)
            continue
        encoded = line[len(_docker_cli.INSPECT_STATUS_MARKER) :].strip()
        try:
            value = json.loads(base64.b64decode(encoded, validate=True).decode("utf-8"))
        except (binascii.Error, ValueError, UnicodeDecodeError, json.JSONDecodeError):
            kept_lines.append(line)
            continue
        if isinstance(value, dict):
            statuses.append(value)
    output, truncated = _truncate_text("".join(kept_lines), limit)
    return output, statuses, truncated


def _truncate_text(text: str, limit: int) -> tuple[str, bool]:
    if len(text) > limit:
        return f"{text[:limit]}\n[输出已截断]", True
    return text, False
