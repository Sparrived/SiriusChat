"""迁移脚本：将名词解释从全局存储迁移到人格级存储。

用法:
    python scripts/migrate_glossary_to_persona.py <data_dir>

参数:
    data_dir: data/ 目录路径，例如 data/

说明:
    - 扫描 data/personas/* 下每个人格的 work_path
    - 将 <work_path>/glossary/*.json 迁移到 <work_path>/glossary/<persona_name>/
    - 原文件重命名为 *.json.migrated
    - 如果目标目录已有数据，跳过该人格
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from sirius_chat.memory.glossary.models import GlossaryTerm

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def _safe_name(name: str) -> str:
    import re

    base = re.sub(r"[^a-zA-Z0-9_\-\u4e00-\u9fff]+", "_", name.strip())
    base = re.sub(r"_+", "_", base).strip("_")
    return base or "default"


def migrate_persona_glossary(persona_work_path: Path, persona_name: str) -> int:
    """Migrate legacy glossary files for a single persona.

    Returns the number of terms migrated.
    """
    glossary_dir = persona_work_path / "glossary"
    if not glossary_dir.exists():
        return 0

    persona_dir = glossary_dir / _safe_name(persona_name)
    persona_dir.mkdir(parents=True, exist_ok=True)

    # Skip if persona directory already has data
    if any(persona_dir.glob("*.json")):
        logger.info("Skipping %s: persona glossary already exists", persona_name)
        return 0

    migrated_count = 0
    for legacy_path in glossary_dir.glob("*.json"):
        if legacy_path.parent != glossary_dir:
            continue  # Skip already-migrated files in subdirs
        try:
            data = json.loads(legacy_path.read_text(encoding="utf-8"))
            group_id = legacy_path.stem
            terms = {
                k: GlossaryTerm.from_dict(v)
                for k, v in data.items()
                if isinstance(v, dict)
            }
            if not terms:
                continue

            # Save to new persona-scoped location
            new_path = persona_dir / f"{legacy_path.stem}.json"
            tmp = new_path.with_suffix(new_path.suffix + ".tmp")
            tmp.write_text(
                json.dumps({k: v.to_dict() for k, v in terms.items()}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(new_path)

            migrated_count += len(terms)
            # Backup legacy file
            backup = legacy_path.with_suffix(".json.migrated")
            legacy_path.rename(backup)
            logger.info("Migrated %d terms from %s for persona '%s'", len(terms), legacy_path.name, persona_name)
        except Exception as exc:
            logger.warning("Migration failed for %s: %s", legacy_path, exc)

    return migrated_count


def main() -> int:
    if len(sys.argv) < 2:
        print(f"用法: python {sys.argv[0]} <data_dir>")
        print("示例: python scripts/migrate_glossary_to_persona.py data/")
        return 1

    data_dir = Path(sys.argv[1]).resolve()
    personas_dir = data_dir / "personas"

    if not personas_dir.exists():
        logger.error("Personas directory not found: %s", personas_dir)
        return 1

    total_migrated = 0
    for persona_path in sorted(personas_dir.iterdir()):
        if not persona_path.is_dir():
            continue
        persona_name = persona_path.name
        work_path = persona_path  # Each persona dir is its own work_path

        count = migrate_persona_glossary(work_path, persona_name)
        total_migrated += count

    logger.info("Migration complete. Total terms migrated: %d", total_migrated)
    return 0


if __name__ == "__main__":
    sys.exit(main())
