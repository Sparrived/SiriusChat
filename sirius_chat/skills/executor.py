"""Skill executor — validates parameters and safely runs skills."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
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


class SkillExecutor:
    """Execute skills with parameter validation and data store injection."""

    def __init__(self, work_path: Path | WorkspaceLayout) -> None:
        self._layout = work_path if isinstance(work_path, WorkspaceLayout) else WorkspaceLayout(work_path)
        self._data_stores: dict[str, SkillDataStore] = {}

    def _get_data_store(self, skill_name: str) -> SkillDataStore:
        """Get or create the persistent data store for a skill."""
        if skill_name not in self._data_stores:
            store_path = self._layout.skill_data_dir() / f"{skill_name}.json"
            self._data_stores[skill_name] = SkillDataStore(store_path)
        return self._data_stores[skill_name]

    def execute(
        self,
        skill: SkillDefinition,
        params: dict[str, Any],
        chain_context: SkillChainContext | None = None,
        invocation_context: SkillInvocationContext | None = None,
    ) -> SkillResult:
        """Execute a skill synchronously with parameter validation.

        If *chain_context* is provided, any ``${skill_name}`` / ``${skill_name.field}``
        placeholders in parameter values are resolved against previously executed
        skills' results before the skill is called.  After execution the result is
        stored back into *chain_context* under ``skill.name`` for downstream use.

        The data_store is automatically injected as a keyword argument
        if the skill's run() function accepts it.
        """
        if skill._run_func is None:
            return SkillResult(success=False, error=f"SKILL '{skill.name}' 没有可执行的 run() 函数")

        # Resolve chain-context template placeholders before validation
        if chain_context is not None:
            params = chain_context.resolve_templates(params)

        # Validate required parameters
        for param_def in skill.parameters:
            if param_def.required and param_def.name not in params:
                return SkillResult(
                    success=False,
                    error=f"缺少必填参数: {param_def.name}",
                )

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
            if chain_context is not None:
                chain_context.store(skill.name, skill_result)
            return skill_result

        data_store = self._get_data_store(skill.name)
        injection_plan = _build_injection_plan(skill._run_func)
        if injection_plan.accepts("data_store"):
            call_params["data_store"] = data_store
        if invocation_context is not None and injection_plan.accepts("invocation_context"):
            call_params["invocation_context"] = invocation_context

        try:
            result = skill._run_func(**call_params)
            # Persist data store after execution
            data_store.save()
            skill_result = SkillResult.from_raw_result(result)
            skill_result.success = True if skill_result.error == "" else skill_result.success
        except Exception as exc:
            logger.error("SKILL '%s' 执行异常: %s", skill.name, exc)
            skill_result = SkillResult(success=False, error=str(exc))

        # Record into chain context so subsequent skills can reference this result
        if chain_context is not None:
            chain_context.store(skill.name, skill_result)

        return skill_result

    async def execute_async(
        self,
        skill: SkillDefinition,
        params: dict[str, Any],
        timeout: float = 0,
        chain_context: SkillChainContext | None = None,
        invocation_context: SkillInvocationContext | None = None,
    ) -> SkillResult:
        """Execute a skill in a thread pool to avoid blocking the event loop.

        Args:
            skill: The skill definition to execute.
            params: Parameters to pass to the skill.
            timeout: Max seconds to wait. 0 means no limit.
            chain_context: Optional chain context for template resolution and
                result accumulation across a multi-skill round.
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
