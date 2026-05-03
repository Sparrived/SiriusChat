"""Glossary manager: term definitions learned from conversations.

Supports both per-persona (v2) and legacy per-group (v1) storage.
Persona-level terms are stored under <work_path>/glossary/<persona_name>/.
Legacy group-level terms remain under <work_path>/glossary/ for backward
compatibility and are migrated on first access.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from sirius_chat.memory.glossary.models import GlossaryTerm
from sirius_chat.utils.layout import WorkspaceLayout

logger = logging.getLogger(__name__)

MAX_GLOSSARY_TERMS = 200
MAX_CONTEXT_EXAMPLES = 5
GLOSSARY_PROMPT_MAX_TERMS = 20


class GlossaryManager:
    """Manages glossary terms with per-persona persistence.

    Terms are scoped to a persona so that different personas maintain
    independent vocabularies.  The *group_id* parameter in the public
    API is retained for backward compatibility but internally maps to
    the current persona.
    """

    def __init__(
        self,
        work_path: Path | WorkspaceLayout,
        persona_name: str = "default",
    ) -> None:
        layout = work_path if isinstance(work_path, WorkspaceLayout) else WorkspaceLayout(work_path)
        self._base_dir = layout.work_path / "glossary"
        self._legacy_dir = self._base_dir  # v1 flat storage
        self._persona_dir = self._base_dir / self._safe_name(persona_name)
        self._persona_dir.mkdir(parents=True, exist_ok=True)
        self._persona_name = persona_name
        self._terms: dict[str, dict[str, GlossaryTerm]] = {}
        self._migrated: bool = False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_migrated(self) -> None:
        """One-shot migration from legacy flat storage to persona-scoped."""
        if self._migrated:
            return
        self._migrated = True

        # If the persona directory already has data, assume migration done.
        if any(self._persona_dir.glob("*.json")):
            return

        # Migrate legacy group files into the persona directory.
        migrated_count = 0
        for legacy_path in self._legacy_dir.glob("*.json"):
            if legacy_path.parent == self._persona_dir:
                continue
            try:
                import json

                data = json.loads(legacy_path.read_text(encoding="utf-8"))
                group_id = legacy_path.stem
                terms = {
                    k: GlossaryTerm.from_dict(v)
                    for k, v in data.items()
                    if isinstance(v, dict)
                }
                if terms:
                    self._terms[group_id] = terms
                    self._save_group(group_id)
                    migrated_count += len(terms)
                    # Rename legacy file to .migrated backup
                    backup = legacy_path.with_suffix(".json.migrated")
                    legacy_path.rename(backup)
            except Exception as exc:
                logger.warning("Glossary migration failed for %s: %s", legacy_path, exc)

        if migrated_count:
            logger.info(
                "Glossary migrated %d terms to persona '%s'", migrated_count, self._persona_name
            )

    def _group_terms(self, group_id: str) -> dict[str, GlossaryTerm]:
        self._ensure_migrated()
        if group_id not in self._terms:
            self._terms[group_id] = self._load_group(group_id)
        return self._terms[group_id]

    def _path(self, group_id: str) -> Path:
        safe = self._safe_name(group_id)
        return self._persona_dir / f"{safe}.json"

    def _save_group(self, group_id: str) -> None:
        import json

        path = self._path(group_id)
        tmp = path.with_suffix(path.suffix + ".tmp")
        terms = self._group_terms(group_id)
        tmp.write_text(
            json.dumps({k: v.to_dict() for k, v in terms.items()}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(path)

    def _load_group(self, group_id: str) -> dict[str, GlossaryTerm]:
        import json

        path = self._path(group_id)
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return {
                k: GlossaryTerm.from_dict(v)
                for k, v in data.items()
                if isinstance(v, dict)
            }
        except (OSError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _safe_name(name: str) -> str:
        import re

        base = re.sub(r"[^a-zA-Z0-9_\-\u4e00-\u9fff]+", "_", name.strip())
        base = re.sub(r"_+", "_", base).strip("_")
        return base or "default"

    # ------------------------------------------------------------------
    # Public API (group_id retained for backward compat)
    # ------------------------------------------------------------------

    def add_or_update(self, group_id: str, term: GlossaryTerm) -> None:
        """Add or merge a glossary term in a group."""
        key = term.term.lower().strip()
        if not key:
            return

        terms = self._group_terms(group_id)
        existing = terms.get(key)
        if existing is not None:
            existing.usage_count += 1
            existing.last_updated_at = __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ).isoformat()
            if term.confidence > existing.confidence:
                existing.definition = term.definition
                existing.confidence = term.confidence
                existing.source = term.source
            seen = set(existing.context_examples)
            for ex in term.context_examples:
                if ex not in seen and len(existing.context_examples) < MAX_CONTEXT_EXAMPLES:
                    existing.context_examples.append(ex)
                    seen.add(ex)
            related_set = set(existing.related_terms)
            for rt in term.related_terms:
                if rt not in related_set:
                    existing.related_terms.append(rt)
                    related_set.add(rt)
            if term.domain != "custom":
                existing.domain = term.domain
        else:
            terms[key] = term

        if len(terms) > MAX_GLOSSARY_TERMS:
            self._evict_least_used(group_id)

        self._save_group(group_id)

    def get_term(self, group_id: str, term: str) -> GlossaryTerm | None:
        return self._group_terms(group_id).get(term.lower().strip())

    def search(
        self, group_id: str, text: str, max_terms: int = GLOSSARY_PROMPT_MAX_TERMS
    ) -> list[GlossaryTerm]:
        """Find glossary terms mentioned in or relevant to the given text."""
        text_lower = text.lower()
        matched: list[tuple[float, GlossaryTerm]] = []
        for term in self._group_terms(group_id).values():
            if term.term.lower() in text_lower:
                score = term.confidence * (1.0 + 0.1 * min(term.usage_count, 10))
                matched.append((score, term))
        matched.sort(key=lambda x: x[0], reverse=True)
        return [t for _, t in matched[:max_terms]]

    def build_prompt_section(
        self, group_id: str, text: str = "", max_terms: int = GLOSSARY_PROMPT_MAX_TERMS
    ) -> str:
        """Build a compact glossary section for the system prompt."""
        if text:
            terms = self.search(group_id, text, max_terms=max_terms)
        else:
            all_terms = sorted(
                self._group_terms(group_id).values(),
                key=lambda t: t.confidence * t.usage_count,
                reverse=True,
            )
            terms = all_terms[:max_terms]
        if not terms:
            return ""
        lines: list[str] = []
        for term in terms:
            conf_tag = "?" if term.confidence < 0.6 else ("~" if term.confidence < 0.8 else "")
            defn = term.definition[:100] if term.definition else "待明确"
            lines.append(f"{term.term}{conf_tag}: {defn}")
        return "\n".join(lines)

    def _evict_least_used(self, group_id: str) -> None:
        terms = self._group_terms(group_id)
        if len(terms) <= MAX_GLOSSARY_TERMS:
            return
        scored = sorted(
            terms.items(), key=lambda kv: kv[1].confidence * kv[1].usage_count, reverse=True
        )
        self._terms[group_id] = dict(scored[:MAX_GLOSSARY_TERMS])
