"""Migration script: v0.27 → v0.28 group-isolated user memory.

Detects old-format user_memory files and migrates them to the new
``user_memory/groups/default/`` layout. Idempotent: safe to run multiple times.

Usage:
    python -m sirius_chat.memory.migration.v0_28_group_isolation /path/to/workspace
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MIGRATION_MARKER = ".migration_v0_28_done"
_BACKUP_DIR = ".backup_pre_v0_28"


def detect_old_format(user_memory_dir: Path) -> bool:
    """Detect if user_memory_dir contains old-format files (direct .json files)."""
    if not user_memory_dir.exists():
        return False
    for path in user_memory_dir.iterdir():
        if path.is_file() and path.suffix == ".json":
            return True
    return False


def migrate_workspace(work_path: Path | str) -> dict[str, Any]:
    """Migrate a single workspace from old to new format.

    Returns:
        dict with ``migrated`` (bool), ``files_moved`` (int), ``backup_dir`` (str).
    """
    work_path = Path(work_path)
    user_memory_dir = work_path / "user_memory"
    user_memory_dir.mkdir(parents=True, exist_ok=True)
    marker_path = user_memory_dir / _MIGRATION_MARKER

    # Already migrated?
    if marker_path.exists():
        return {"migrated": False, "files_moved": 0, "reason": "already_migrated"}

    # Nothing to migrate?
    if not detect_old_format(user_memory_dir):
        # Write marker anyway so we don't keep checking
        marker_path.write_text("done", encoding="utf-8")
        return {"migrated": False, "files_moved": 0, "reason": "no_old_files"}

    # Backup old files
    backup_dir = user_memory_dir / _BACKUP_DIR
    backup_dir.mkdir(parents=True, exist_ok=True)

    # Create new layout
    default_group_dir = user_memory_dir / "groups" / "default"
    default_group_dir.mkdir(parents=True, exist_ok=True)

    files_moved = 0
    for path in list(user_memory_dir.iterdir()):
        if not path.is_file() or path.suffix != ".json":
            continue
        if path.name == _MIGRATION_MARKER:
            continue

        # Backup
        shutil.copy2(path, backup_dir / path.name)

        # Move to default group
        target = default_group_dir / path.name
        shutil.move(str(path), str(target))
        files_moved += 1
        logger.info("Migrated %s → %s", path.name, target)

    # Write marker
    marker_path.write_text("done", encoding="utf-8")
    logger.info(
        "v0.28 migration complete | work_path=%s | files_moved=%d | backup=%s",
        work_path,
        files_moved,
        backup_dir,
    )

    return {
        "migrated": True,
        "files_moved": files_moved,
        "backup_dir": str(backup_dir),
    }


def main() -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Migrate user memory to v0.28 group-isolated format")
    parser.add_argument("work_path", help="Workspace path to migrate")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    result = migrate_workspace(args.work_path)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("migrated") or result.get("reason") == "already_migrated" else 1


if __name__ == "__main__":
    raise SystemExit(main())
