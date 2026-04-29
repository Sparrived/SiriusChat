"""Data models for the skill system."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from sirius_chat.memory import UserProfile


@dataclass(slots=True)
class SkillContentBlock:
    """Internal content block returned by a skill for model-side consumption."""

    type: str
    value: str
    mime_type: str = ""
    label: str = ""


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
    text_blocks: list[SkillContentBlock] = field(default_factory=list)
    multimodal_blocks: list[SkillContentBlock] = field(default_factory=list)
    internal_metadata: dict[str, Any] = field(default_factory=dict)

    def to_display_text(self) -> str:
        """Convert result to a human-readable text for AI consumption."""
        if not self.success:
            return f"[SKILL执行失败] {self.error}"
        if self.text_blocks:
            lines = [block.value.strip() for block in self.text_blocks if block.value.strip()]
            if lines:
                return "\n".join(lines)
        if isinstance(self.data, dict):
            lines: list[str] = []
            for key, value in self.data.items():
                if key in {"_meta", "metadata", "internal_metadata", "text_blocks", "multimodal_blocks", "multimodal", "attachments"}:
                    continue
                if isinstance(value, dict):
                    lines.append(f"{key}:")
                    for k, v in value.items():
                        lines.append(f"  {k}: {v}")
                elif isinstance(value, list):
                    lines.append(f"{key}: {', '.join(str(v) for v in value)}")
                else:
                    lines.append(f"{key}: {value}")
            if lines:
                return "\n".join(lines)
        return str(self.data) if self.data is not None else "执行完成（无返回数据）"

    def to_internal_payload(self) -> dict[str, Any]:
        """Build a structured internal payload for prompt injection."""
        return {
            "success": self.success,
            "text_blocks": [
                {
                    "type": block.type,
                    "value": block.value,
                    "mime_type": block.mime_type,
                    "label": block.label,
                }
                for block in self.text_blocks
            ],
            "multimodal_blocks": [
                {
                    "type": block.type,
                    "value": block.value,
                    "mime_type": block.mime_type,
                    "label": block.label,
                }
                for block in self.multimodal_blocks
            ],
            "internal_metadata": dict(self.internal_metadata),
        }

    @staticmethod
    def from_raw_result(value: Any) -> "SkillResult":
        """Normalize a raw skill return value into SkillResult."""
        if isinstance(value, SkillResult):
            return value
        if not isinstance(value, dict):
            return SkillResult(success=True, data=value)

        text_blocks = SkillResult._extract_content_blocks(
            value.get("text_blocks") or value.get("text") or value.get("texts"),
            default_type="text",
        )
        multimodal_blocks = SkillResult._extract_content_blocks(
            value.get("multimodal_blocks") or value.get("multimodal") or value.get("attachments"),
            default_type="image",
        )
        internal_metadata = value.get("internal_metadata")
        if not isinstance(internal_metadata, dict):
            internal_metadata = {}

        return SkillResult(
            success=bool(value.get("success", True)),
            data=value,
            error=str(value.get("error", "")).strip(),
            text_blocks=text_blocks,
            multimodal_blocks=multimodal_blocks,
            internal_metadata=dict(internal_metadata),
        )

    @staticmethod
    def _extract_content_blocks(raw: Any, *, default_type: str) -> list[SkillContentBlock]:
        blocks: list[SkillContentBlock] = []
        if isinstance(raw, str):
            value = raw.strip()
            if value:
                blocks.append(SkillContentBlock(type=default_type, value=value))
            return blocks
        if not isinstance(raw, list):
            return blocks
        for item in raw:
            if isinstance(item, str):
                value = item.strip()
                if value:
                    blocks.append(SkillContentBlock(type=default_type, value=value))
                continue
            if not isinstance(item, dict):
                continue
            value = str(item.get("value", "")).strip()
            if not value:
                continue
            blocks.append(
                SkillContentBlock(
                    type=str(item.get("type", default_type)).strip() or default_type,
                    value=value,
                    mime_type=str(item.get("mime_type", "")).strip(),
                    label=str(item.get("label", "")).strip(),
                )
            )
        return blocks

    def get_field(self, key: str, default: Any = None) -> Any:
        """Extract a field from dict/list data by key or index."""
        if isinstance(self.data, dict):
            return self.data.get(key, default)
        if isinstance(self.data, list):
            try:
                return self.data[int(key)]
            except (ValueError, IndexError):
                return default
        return default


@dataclass(slots=True)
class SkillDefinition:
    """Complete definition of a loadable skill."""

    name: str
    description: str
    parameters: list[SkillParameter] = field(default_factory=list)
    version: str = "1.0.0"
    developer_only: bool = False
    silent: bool = False
    tags: list[str] = field(default_factory=list)
    adapter_types: list[str] = field(default_factory=list)
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


@dataclass(slots=True)
class SkillInvocationContext:
    """Per-call context injected into skills for authorization and auditing."""

    caller: UserProfile | None = None
    developer_profiles: list[UserProfile] = field(default_factory=list)

    @property
    def caller_is_developer(self) -> bool:
        if self.caller is None:
            return False
        return bool(self.caller.metadata.get("is_developer"))

    @property
    def has_declared_developer(self) -> bool:
        return bool(self.developer_profiles)

    @property
    def caller_name(self) -> str:
        if self.caller is None:
            return ""
        return str(self.caller.name).strip()

    @property
    def caller_user_id(self) -> str:
        if self.caller is None:
            return ""
        return str(self.caller.user_id).strip()


class SkillChainContext:
    """Mutable context passed through a single-round skill chain.

    Stores the result of every skill executed in the current round so that
    subsequent skills can reference earlier results via ``${skill_name}`` or
    ``${skill_name.field}`` template placeholders in their parameters.
    """

    def __init__(self) -> None:
        self._results: dict[str, SkillResult] = {}

    def store(self, skill_name: str, result: SkillResult) -> None:
        """Record ``result`` under ``skill_name`` for later template lookup."""
        self._results[skill_name] = result

    def resolve_templates(self, params: dict[str, Any]) -> dict[str, Any]:
        """Return a copy of *params* with ``${...}`` placeholders substituted.

        Supported template formats (case-sensitive skill name):

        * ``${skill_name}`` — replaced with the skill's full display text.
        * ``${skill_name.field}`` — replaced with a single field of a dict
          or list result (list: ``field`` is a 0-based integer index).

        Placeholders that cannot be resolved are left unchanged.
        """
        import re as _re

        _PLACEHOLDER = _re.compile(r"\$\{([^}]+)\}")

        def _sub(value: str) -> str:
            def _replace(m: _re.Match[str]) -> str:
                expr = m.group(1)
                if "." in expr:
                    skill_name, field = expr.split(".", 1)
                else:
                    skill_name, field = expr, None
                result = self._results.get(skill_name)
                if result is None:
                    return m.group(0)  # unresolved — leave as-is
                if field is None:
                    return result.to_display_text()
                v = result.get_field(field)
                return str(v) if v is not None else m.group(0)

            return _PLACEHOLDER.sub(_replace, value)

        resolved: dict[str, Any] = {}
        for k, v in params.items():
            resolved[k] = _sub(v) if isinstance(v, str) else v
        return resolved

    @property
    def results(self) -> dict[str, SkillResult]:
        return dict(self._results)
