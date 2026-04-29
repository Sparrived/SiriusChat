"""Skill executor — validates parameters and safely runs skills."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from sirius_chat.skills.data_store import SkillDataStore
from sirius_chat.skills.models import (
    SkillChainContext,
    SkillDefinition,
    SkillInvocationContext,
    SkillResult,
)
from sirius_chat.skills.security import validate_skill_access
from sirius_chat.skills.telemetry import SkillExecutionRecord, SkillTelemetry
from sirius_chat.workspace.layout import WorkspaceLayout

logger = logging.getLogger(__name__)

# Pattern to detect a SKILL_CALL in AI output
# Format: [SKILL_CALL: skill_name | {"param": "value"}]
# or:     [SKILL_CALL: skill_name]  (no params)
SKILL_CALL_PATTERN = re.compile(
    r"\[SKILL_CALL:\s*(\w+)(?:\s*\|\s*(\{.*?\}))?\s*\]",
    re.DOTALL,
)


def parse_skill_calls(text: str) -> list[tuple[str, dict[str, Any]]]:
    """Extract all SKILL_CALL invocations from text.

    Returns list of (skill_name, parameters) tuples.
    """
    results: list[tuple[str, dict[str, Any]]] = []
    for match in SKILL_CALL_PATTERN.finditer(text):
        skill_name = match.group(1).strip()
        params_raw = match.group(2)
        params: dict[str, Any] = {}
        if params_raw:
            try:
                parsed = json.loads(params_raw)
                if isinstance(parsed, dict):
                    params = parsed
            except json.JSONDecodeError:
                logger.warning("SKILL_CALL参数解析失败: %s", params_raw)
        results.append((skill_name, params))
    return results


def strip_skill_calls(text: str) -> str:
    """Remove all SKILL_CALL markers from text, leaving surrounding content."""
    return SKILL_CALL_PATTERN.sub("", text).strip()


def _should_retry(exc: Exception) -> bool:
    """Heuristic: is this exception likely transient and worth retrying?"""
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    exc_name = type(exc).__name__.lower()
    return any(
        keyword in exc_name
        for keyword in ("timeout", "connection", "temporary", "network", "retry", "unreachable")
    )


class SkillExecutor:
    """Execute skills with parameter validation, retry, telemetry, and data store injection."""

    def __init__(self, work_path: Path | WorkspaceLayout) -> None:
        self._layout = work_path if isinstance(work_path, WorkspaceLayout) else WorkspaceLayout(work_path)
        self._data_stores: dict[str, SkillDataStore] = {}
        self._telemetry = SkillTelemetry(self._layout.skill_data_dir() / ".telemetry.jsonl")

    def get_data_store(self, skill_name: str) -> SkillDataStore:
        """Get or create the persistent data store for a skill."""
        if skill_name not in self._data_stores:
            store_path = self._layout.skill_data_dir() / f"{skill_name}.json"
            self._data_stores[skill_name] = SkillDataStore(store_path)
        return self._data_stores[skill_name]

    # Backward-compatible alias
    _get_data_store = get_data_store

    def execute(
        self,
        skill: SkillDefinition,
        params: dict[str, Any],
        chain_context: SkillChainContext | None = None,
        invocation_context: SkillInvocationContext | None = None,
        max_retries: int = 0,
    ) -> SkillResult:
        """Execute a skill synchronously with parameter validation and optional retry.

        If *chain_context* is provided, any ``${skill_name}`` / ``${skill_name.field}``
        placeholders in parameter values are resolved against previously executed
        skills' results before the skill is called.  After execution the result is
        stored back into *chain_context* under ``skill.name`` for downstream use.

        The data_store is automatically injected as a keyword argument
        if the skill's run() function accepts it.

        Args:
            max_retries: Number of extra attempts for transient failures
                (timeout, connection error, etc.).
        """
        start_time = time.perf_counter()
        skill_result: SkillResult | None = None

        try:
            if skill._run_func is None:
                skill_result = SkillResult(success=False, error=f"SKILL '{skill.name}' 没有可执行的 run() 函数")
                return skill_result

            # Resolve chain-context template placeholders before validation
            if chain_context is not None:
                params = chain_context.resolve_templates(params)

            # Validate required parameters
            for param_def in skill.parameters:
                if param_def.required and param_def.name not in params:
                    skill_result = SkillResult(
                        success=False,
                        error=f"缺少必填参数: {param_def.name}",
                    )
                    return skill_result

            # Apply defaults for optional parameters
            call_params: dict[str, Any] = {}
            for param_def in skill.parameters:
                if param_def.name in params:
                    call_params[param_def.name] = _coerce_type(
                        params[param_def.name], param_def.type
                    )
                elif param_def.default is not None:
                    call_params[param_def.name] = param_def.default

            access_error = validate_skill_access(skill=skill, invocation_context=invocation_context)
            if access_error:
                skill_result = SkillResult(success=False, error=access_error)
                return skill_result

            data_store = self._get_data_store(skill.name)
            injection_plan = _build_injection_plan(skill._run_func)
            if injection_plan.accepts("data_store"):
                call_params["data_store"] = data_store
            if invocation_context is not None and injection_plan.accepts("invocation_context"):
                call_params["invocation_context"] = invocation_context

            # Run with optional retry for transient failures
            last_error: Exception | None = None
            for attempt in range(max_retries + 1):
                try:
                    result = skill._run_func(**call_params)
                    # Persist data store after execution
                    data_store.save()
                    skill_result = SkillResult.from_raw_result(result)
                    skill_result.success = True if skill_result.error == "" else skill_result.success
                    logger.debug(
                        "SKILL '%s' 执行成功 | summary=%r | text_blocks=%d | "
                        "multimodal_blocks=%d | internal_metadata=%r",
                        skill.name,
                        skill_result.to_display_text()[:200],
                        len(skill_result.text_blocks),
                        len(skill_result.multimodal_blocks),
                        skill_result.internal_metadata,
                    )
                    break
                except Exception as exc:
                    last_error = exc
                    if attempt < max_retries and _should_retry(exc):
                        logger.warning(
                            "SKILL '%s' 第%d次执行失败（将重试）: %s",
                            skill.name, attempt + 1, exc,
                        )
                        continue
                    logger.error("SKILL '%s' 执行异常: %s", skill.name, exc)
                    skill_result = SkillResult(success=False, error=str(exc))
                    break
        finally:
            # Telemetry is best-effort and must not affect the result
            if skill_result is not None:
                duration_ms = (time.perf_counter() - start_time) * 1000
                try:
                    caller_id = ""
                    if invocation_context is not None:
                        caller_id = getattr(invocation_context, "caller_user_id", "") or ""
                    self._telemetry.record(
                        SkillExecutionRecord(
                            skill_name=skill.name,
                            timestamp=time.time(),
                            success=skill_result.success,
                            duration_ms=round(duration_ms, 2),
                            error=skill_result.error if not skill_result.success else "",
                            caller_user_id=caller_id,
                        )
                    )
                except Exception:
                    pass

        # Record into chain context so subsequent skills can reference this result
        if chain_context is not None and skill_result is not None:
            chain_context.store(skill.name, skill_result)

        return skill_result if skill_result is not None else SkillResult(success=False, error="未知错误")

    async def execute_async(
        self,
        skill: SkillDefinition,
        params: dict[str, Any],
        timeout: float = 0,
        chain_context: SkillChainContext | None = None,
        invocation_context: SkillInvocationContext | None = None,
        max_retries: int = 0,
    ) -> SkillResult:
        """Execute a skill in a thread pool to avoid blocking the event loop.

        Args:
            skill: The skill definition to execute.
            params: Parameters to pass to the skill.
            timeout: Max seconds to wait. 0 means no limit.
            chain_context: Optional chain context for template resolution and
                result accumulation across a multi-skill round.
            max_retries: Number of extra attempts for transient failures.
        """
        if timeout > 0:
            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(
                        self.execute,
                        skill,
                        params,
                        chain_context,
                        invocation_context,
                        max_retries,
                    ),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                logger.error("SKILL '%s' 执行超时 (限制 %.1f秒)", skill.name, timeout)
                return SkillResult(
                    success=False,
                    error=f"SKILL执行超时（限制 {timeout:.0f} 秒），请稍后重试或联系管理员",
                )
        return await asyncio.to_thread(
            self.execute,
            skill,
            params,
            chain_context,
            invocation_context,
            max_retries,
        )

    def save_all_stores(self) -> None:
        """Persist all dirty data stores."""
        for store in self._data_stores.values():
            store.save()


def _coerce_type(value: Any, type_hint: str) -> Any:
    """Best-effort type coercion based on the parameter type hint."""
    type_lower = type_hint.lower().strip()
    if type_lower == "int":
        try:
            return int(value)
        except (ValueError, TypeError):
            return value
    if type_lower == "float":
        try:
            return float(value)
        except (ValueError, TypeError):
            return value
    if type_lower == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in ("true", "1", "yes")
        return bool(value)
    if type_lower in ("list[str]", "list"):
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return parsed
            except json.JSONDecodeError:
                return [v.strip() for v in value.split(",") if v.strip()]
        return value
    return value


class _InjectionPlan:
    def __init__(self, *, accepts_kwargs: bool, keyword_params: set[str]) -> None:
        self._accepts_kwargs = accepts_kwargs
        self._keyword_params = keyword_params

    def accepts(self, param_name: str) -> bool:
        return self._accepts_kwargs or param_name in self._keyword_params


def _build_injection_plan(run_func: Any) -> _InjectionPlan:
    try:
        signature = inspect.signature(run_func)
    except (TypeError, ValueError):
        return _InjectionPlan(accepts_kwargs=True, keyword_params=set())

    accepts_kwargs = False
    keyword_params: set[str] = set()
    for name, param in signature.parameters.items():
        if param.kind == inspect.Parameter.VAR_KEYWORD:
            accepts_kwargs = True
            continue
        if param.kind in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ):
            keyword_params.add(name)
    return _InjectionPlan(accepts_kwargs=accepts_kwargs, keyword_params=keyword_params)
