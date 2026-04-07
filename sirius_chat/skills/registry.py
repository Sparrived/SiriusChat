"""Skill registry — discovers, loads, and manages skill definitions.

Skills are Python files residing in {work_path}/skills/ that expose:
- SKILL_META: dict with name, description, parameters, version (optional)
- run(**kwargs) -> Any: the callable entry point
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any

from sirius_chat.skills.models import SkillDefinition, SkillParameter

logger = logging.getLogger(__name__)


class SkillRegistry:
    """Discovers and manages skill definitions from a directory."""

    def __init__(self) -> None:
        self._skills: dict[str, SkillDefinition] = {}

    @property
    def skill_names(self) -> list[str]:
        return list(self._skills.keys())

    def get(self, name: str) -> SkillDefinition | None:
        return self._skills.get(name)

    def all_skills(self) -> list[SkillDefinition]:
        return list(self._skills.values())

    def register(self, skill: SkillDefinition) -> None:
        """Manually register a skill definition."""
        self._skills[skill.name] = skill

    def load_from_directory(self, skills_dir: Path) -> int:
        """Load all *.py skill files from a directory.

        Returns the number of skills successfully loaded.
        """
        if not skills_dir.is_dir():
            logger.debug("SKILL目录不存在: %s", skills_dir)
            return 0

        loaded = 0
        for py_file in sorted(skills_dir.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            try:
                skill = self._load_skill_file(py_file)
                if skill is not None:
                    self._skills[skill.name] = skill
                    loaded += 1
                    logger.info("已加载SKILL: %s (v%s) from %s", skill.name, skill.version, py_file.name)
            except Exception as exc:
                logger.warning("加载SKILL文件失败 (%s): %s", py_file.name, exc)
        return loaded

    @staticmethod
    def _load_skill_file(file_path: Path) -> SkillDefinition | None:
        """Load a single skill from a Python file."""
        module_name = f"_sirius_skill_{file_path.stem}"

        spec = importlib.util.spec_from_file_location(module_name, file_path)
        if spec is None or spec.loader is None:
            logger.warning("无法创建模块规格: %s", file_path)
            return None

        module = importlib.util.module_from_spec(spec)
        # Temporarily add to sys.modules so relative imports work if needed
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            sys.modules.pop(module_name, None)
            raise RuntimeError(f"执行SKILL模块失败 ({file_path.name}): {exc}") from exc

        meta: dict[str, Any] | None = getattr(module, "SKILL_META", None)
        if not isinstance(meta, dict):
            sys.modules.pop(module_name, None)
            logger.warning("SKILL文件缺少 SKILL_META 字典: %s", file_path.name)
            return None

        run_func = getattr(module, "run", None)
        if not callable(run_func):
            sys.modules.pop(module_name, None)
            logger.warning("SKILL文件缺少 run() 函数: %s", file_path.name)
            return None

        name = str(meta.get("name", file_path.stem)).strip()
        description = str(meta.get("description", "")).strip()
        version = str(meta.get("version", "1.0.0")).strip()
        if not name:
            name = file_path.stem
        if not description:
            logger.warning("SKILL '%s' 缺少描述", name)

        # Parse parameters
        raw_params = meta.get("parameters", {})
        parameters: list[SkillParameter] = []
        if isinstance(raw_params, dict):
            for param_name, param_def in raw_params.items():
                if isinstance(param_def, dict):
                    parameters.append(
                        SkillParameter(
                            name=param_name,
                            type=str(param_def.get("type", "str")),
                            description=str(param_def.get("description", "")),
                            required=bool(param_def.get("required", False)),
                            default=param_def.get("default"),
                        )
                    )
        elif isinstance(raw_params, list):
            for item in raw_params:
                if isinstance(item, dict):
                    parameters.append(
                        SkillParameter(
                            name=str(item.get("name", "")),
                            type=str(item.get("type", "str")),
                            description=str(item.get("description", "")),
                            required=bool(item.get("required", False)),
                            default=item.get("default"),
                        )
                    )

        return SkillDefinition(
            name=name,
            description=description,
            parameters=parameters,
            version=version,
            source_path=file_path,
            _run_func=run_func,
        )

    def build_tool_descriptions(self) -> str:
        """Build a formatted text block describing all available skills.

        This is injected into the system prompt so the AI knows what tools
        are available and how to call them.
        """
        if not self._skills:
            return ""

        lines: list[str] = []
        for skill in self._skills.values():
            lines.append(f"- {skill.name}: {skill.description}")
            if skill.parameters:
                param_parts: list[str] = []
                for p in skill.parameters:
                    required_tag = "必填" if p.required else "可选"
                    default_tag = f", 默认={p.default}" if not p.required and p.default is not None else ""
                    param_parts.append(
                        f"    - {p.name} ({p.type}, {required_tag}{default_tag}): {p.description}"
                    )
                lines.extend(param_parts)
        return "\n".join(lines)
