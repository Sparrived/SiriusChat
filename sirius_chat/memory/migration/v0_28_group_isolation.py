"""Migration: migrate pre-v0.28 flat memory layout to group-isolated layout.

Old layout (pre-v0.28):
    {work_path}/user_memory/{user_id}.json
    {work_path}/event_memory/events.json

New layout (v0.28+):
    {work_path}/user_memory/global/         (reserved for cross-group profiles)
    {work_path}/user_memory/groups/default/{user_id}.json
    {work_path}/user_memory/groups/default/group_state.json
    {work_path}/event_memory/default/events.json
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MIGRATION_MARKER = ".migration_v0_28_done"
BACKUP_DIR = ".backup_pre_v0_28"


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def migrate_workspace_to_group_layout(work_path: Path) -> dict[str, Any]:
    """Migrate a workspace from flat to group-isolated memory layout.

    Returns migration report dict with counts and any errors.
    """
    report: dict[str, Any] = {
        "migrated": False,
        "user_memory_count": 0,
        "event_memory_migrated": False,
        "backup_path": None,
        "errors": [],
    }

    user_memory_dir = work_path / "user_memory"
    event_memory_dir = work_path / "event_memory"
    marker_file = user_memory_dir / MIGRATION_MARKER

    # Already migrated
    if marker_file.exists():
        report["migrated"] = True
        return report

    # No old data to migrate
    if not user_memory_dir.exists():
        user_memory_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(user_memory_dir / MIGRATION_MARKER, {"status": "no_old_data"})
        report["migrated"] = True
        return report

    # Detect old layout: *.json files directly in user_memory/
    old_user_files = list(user_memory_dir.glob("*.json"))
    if not old_user_files:
        # Could already be new layout, or empty
        _atomic_write_json(user_memory_dir / MIGRATION_MARKER, {"status": "already_layout_or_empty"})
        report["migrated"] = True
        return report

    logger.info("Migrating workspace %s to group-isolated layout...", work_path)

    # Create backup
    backup_dir = user_memory_dir / BACKUP_DIR
    try:
        if backup_dir.exists():
            shutil.rmtree(backup_dir)
        shutil.copytree(user_memory_dir, backup_dir)
        report["backup_path"] = str(backup_dir)
    except Exception as exc:
        report["errors"].append(f"backup_failed: {exc}")
        logger.error("Backup failed, aborting migration: %s", exc)
        return report

    # Migrate user memory files → groups/default/
    default_group_dir = user_memory_dir / "groups" / "default"
    default_group_dir.mkdir(parents=True, exist_ok=True)

    for old_file in old_user_files:
        try:
            data = json.loads(old_file.read_text(encoding="utf-8"))
            target = default_group_dir / old_file.name
            _atomic_write_json(target, data)
            old_file.unlink()
            report["user_memory_count"] += 1
        except Exception as exc:
            report["errors"].append(f"user_file_{old_file.name}: {exc}")
            logger.warning("Failed to migrate user file %s: %s", old_file.name, exc)

    # Create default group_state.json
    group_state = {
        "group_id": "default",
        "group_name": "Default Group",
        "created_at": _now_iso(),
        "atmosphere_history": [],
        "group_norms": {},
        "interest_topics": [],
        "typical_interaction_style": "balanced",
    }
    _atomic_write_json(default_group_dir / "group_state.json", group_state)

    # Migrate event memory
    if event_memory_dir.exists():
        old_event_file = event_memory_dir / "events.json"
        if old_event_file.exists():
            try:
                default_event_dir = event_memory_dir / "default"
                default_event_dir.mkdir(parents=True, exist_ok=True)
                data = json.loads(old_event_file.read_text(encoding="utf-8"))
                _atomic_write_json(default_event_dir / "events.json", data)
                old_event_file.unlink()
                report["event_memory_migrated"] = True
            except Exception as exc:
                report["errors"].append(f"event_memory: {exc}")
                logger.warning("Failed to migrate event memory: %s", exc)

    # Write migration marker
    _atomic_write_json(
        marker_file,
        {
            "status": "completed",
            "user_memory_count": report["user_memory_count"],
            "event_memory_migrated": report["event_memory_migrated"],
            "migrated_at": _now_iso(),
        },
    )

    report["migrated"] = True
    logger.info(
        "Migration completed: %d user files, event=%s",
        report["user_memory_count"],
        report["event_memory_migrated"],
    )
    return report


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
