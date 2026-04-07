"""Data models for the skill system."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass(slots=True)
class SkillParameter:
    """Definition of a single skill parameter."""

    name: str
    type: str  # "str", "int", "float", "bool", "list[str]", "dict"
    description: str
    required: bool = False
    default: Any = None


@dataclass(slots=True)
class SkillResult:
    """Result returned from skill execution."""

    success: bool
    data: Any = None
    error: str = ""

    def to_display_text(self) -> str:
        """Convert result to a human-readable text for AI consumption."""
        if not self.success:
            return f"[SKILL执行失败] {self.error}"
        if isinstance(self.data, dict):
            lines: list[str] = []
            for key, value in self.data.items():
                if isinstance(value, dict):
                    lines.append(f"{key}:")
                    for k, v in value.items():
                        lines.append(f"  {k}: {v}")
                elif isinstance(value, list):
                    lines.append(f"{key}: {', '.join(str(v) for v in value)}")
                else:
                    lines.append(f"{key}: {value}")
            return "\n".join(lines)
        return str(self.data) if self.data is not None else "执行完成（无返回数据）"


@dataclass(slots=True)
class SkillDefinition:
    """Complete definition of a loadable skill."""

    name: str
    description: str
    parameters: list[SkillParameter] = field(default_factory=list)
    version: str = "1.0.0"
    source_path: Path | None = None
    _run_func: Callable[..., Any] | None = field(default=None, repr=False)

    def get_parameter_schema(self) -> list[dict[str, Any]]:
        """Return parameter definitions as dicts for prompt rendering."""
        schema: list[dict[str, Any]] = []
        for param in self.parameters:
            entry: dict[str, Any] = {
                "name": param.name,
                "type": param.type,
                "description": param.description,
                "required": param.required,
            }
            if not param.required and param.default is not None:
                entry["default"] = param.default
            schema.append(entry)
        return schema
