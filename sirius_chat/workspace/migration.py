from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from sirius_chat.models import Participant
from sirius_chat.session.store import SqliteSessionStore
from sirius_chat.workspace.layout import WorkspaceLayout


@dataclass(slots=True)
class LegacyLayoutReport:
    work_path: Path
    detected_paths: list[str] = field(default_factory=list)

    @property
    def has_legacy_layout(self) -> bool:
        return bool(self.detected_paths)


@dataclass(slots=True)
class MigrationReport:
    work_path: Path
    dry_run: bool
    detected_paths: list[str] = field(default_factory=list)
    copied_paths: list[str] = field(default_factory=list)
    created_paths: list[str] = field(default_factory=list)
    skipped_paths: list[str] = field(default_factory=list)


class WorkspaceMigrationManager:
    """Non-destructive migration from legacy flat layout to workspace layout."""

    def __init__(self, layout: WorkspaceLayout | None = None) -> None:
        self._layout = layout

    def detect_legacy_layout(self, work_path: Path) -> LegacyLayoutReport:
        layout = self._layout or WorkspaceLayout(work_path)
        detected: list[str] = []
        legacy_paths = [
            layout.legacy_session_store_path(backend="json"),
            layout.legacy_session_store_path(backend="sqlite"),
            layout.legacy_primary_user_path(),
            layout.legacy_provider_registry_path(),
            layout.legacy_user_memory_dir(),
            layout.legacy_event_memory_dir(),
            layout.legacy_self_memory_path(),
            layout.legacy_token_usage_db_path(),
            layout.legacy_generated_agents_path(),
            layout.legacy_generated_agent_trace_dir(),
        ]
        for path in legacy_paths:
            if path.exists():
                detected.append(str(path.relative_to(layout.root)).replace("\\", "/"))
        return LegacyLayoutReport(work_path=layout.root, detected_paths=detected)

    def migrate(self, work_path: Path, *, dry_run: bool = False) -> MigrationReport:
        layout = self._layout or WorkspaceLayout(work_path)
        layout.ensure_directories(session_id="default")
        report = MigrationReport(
            work_path=layout.root,
            dry_run=dry_run,
            detected_paths=self.detect_legacy_layout(layout.root).detected_paths,
        )

        self._copy_file(
            source=layout.legacy_provider_registry_path(),
            target=layout.provider_registry_path(),
            report=report,
        )
        self._copy_dir(
            source=layout.legacy_user_memory_dir(),
            target=layout.user_memory_dir(),
            report=report,
        )
        self._copy_dir(
            source=layout.legacy_event_memory_dir(),
            target=layout.event_memory_dir(),
            report=report,
        )
        self._copy_file(
            source=layout.legacy_self_memory_path(),
            target=layout.self_memory_path(),
            report=report,
        )
        self._copy_file(
            source=layout.legacy_token_usage_db_path(),
            target=layout.token_usage_db_path(),
            report=report,
        )
        self._copy_file(
            source=layout.legacy_generated_agents_path(),
            target=layout.generated_agents_path(),
            report=report,
        )
        self._copy_dir(
            source=layout.legacy_generated_agent_trace_dir(),
            target=layout.generated_agent_trace_dir(),
            report=report,
        )

        self._migrate_session_store(layout=layout, report=report)
        self._migrate_primary_user(layout=layout, report=report)
        return report

    def _copy_file(
        self,
        *,
        source: Path,
        target: Path,
        report: MigrationReport,
    ) -> None:
        if not source.exists():
            return
        if target.exists():
            report.skipped_paths.append(str(target.relative_to(report.work_path)).replace("\\", "/"))
            return
        report.copied_paths.append(str(target.relative_to(report.work_path)).replace("\\", "/"))
        if report.dry_run:
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    def _copy_dir(
        self,
        *,
        source: Path,
        target: Path,
        report: MigrationReport,
    ) -> None:
        if not source.exists() or not source.is_dir():
            return
        target.mkdir(parents=True, exist_ok=True)
        for child in source.iterdir():
            child_target = target / child.name
            relative = str(child_target.relative_to(report.work_path)).replace("\\", "/")
            if child.is_dir():
                self._copy_dir(source=child, target=child_target, report=report)
                continue
            if child_target.exists():
                report.skipped_paths.append(relative)
                continue
            report.copied_paths.append(relative)
            if report.dry_run:
                continue
            child_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(child, child_target)

    def _migrate_session_store(self, *, layout: WorkspaceLayout, report: MigrationReport) -> None:
        session_dir = layout.session_dir("default")
        session_dir.mkdir(parents=True, exist_ok=True)

        legacy_db = layout.legacy_session_store_path(backend="sqlite")
        legacy_json = layout.legacy_session_store_path(backend="json")
        new_db = layout.session_store_path("default", backend="sqlite")
        new_json = layout.session_store_path("default", backend="json")

        if legacy_db.exists() and not new_db.exists():
            report.copied_paths.append(str(new_db.relative_to(layout.root)).replace("\\", "/"))
            if not report.dry_run:
                shutil.copy2(legacy_db, new_db)

        if legacy_json.exists() and not new_db.exists() and not new_json.exists():
            report.copied_paths.append(str(new_json.relative_to(layout.root)).replace("\\", "/"))
            if not report.dry_run:
                shutil.copy2(legacy_json, new_json)

        if report.dry_run:
            return

        if new_db.exists() or new_json.exists():
            SqliteSessionStore(path=new_db)
            if new_json.exists() and new_db.exists():
                new_json.unlink(missing_ok=True)

    def _migrate_primary_user(self, *, layout: WorkspaceLayout, report: MigrationReport) -> None:
        source = layout.legacy_primary_user_path()
        target = layout.session_participants_path("default")
        if not source.exists() or target.exists():
            return

        report.created_paths.append(str(target.relative_to(layout.root)).replace("\\", "/"))
        if report.dry_run:
            return

        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        participant = Participant(
            name=str(payload.get("name", "用户")).strip() or "用户",
            user_id=str(payload.get("user_id", payload.get("name", "用户"))).strip() or "user",
            persona=str(payload.get("persona", "")).strip(),
            aliases=list(payload.get("aliases", [])),
            traits=list(payload.get("traits", [])),
        )
        participants_payload = {
            "session_id": "default",
            "primary_user_id": participant.user_id,
            "participants": [participant.to_dict()],
            "legacy_source": str(source.relative_to(layout.root)).replace("\\", "/"),
        }
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(participants_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )