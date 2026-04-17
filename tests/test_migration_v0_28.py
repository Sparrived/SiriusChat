"""Tests for v0.27 → v0.28 user memory migration script."""

from __future__ import annotations

import json

from sirius_chat.memory.migration.v0_28_group_isolation import (
    detect_old_format,
    migrate_workspace,
)


class TestDetectOldFormat:
    def test_detects_old_format(self, tmp_path):
        um_dir = tmp_path / "user_memory"
        um_dir.mkdir()
        (um_dir / "user1.json").write_text("{}", encoding="utf-8")
        assert detect_old_format(um_dir) is True

    def test_empty_dir_is_not_old_format(self, tmp_path):
        um_dir = tmp_path / "user_memory"
        um_dir.mkdir()
        assert detect_old_format(um_dir) is False

    def test_new_layout_is_not_old_format(self, tmp_path):
        um_dir = tmp_path / "user_memory"
        (um_dir / "groups" / "default").mkdir(parents=True)
        (um_dir / "groups" / "default" / "user1.json").write_text("{}", encoding="utf-8")
        assert detect_old_format(um_dir) is False


class TestMigrateWorkspace:
    def test_migrates_old_files(self, tmp_path):
        um_dir = tmp_path / "user_memory"
        um_dir.mkdir()
        (um_dir / "user1.json").write_text('{"name": "Alice"}', encoding="utf-8")
        (um_dir / "user2.json").write_text('{"name": "Bob"}', encoding="utf-8")

        result = migrate_workspace(tmp_path)

        assert result["migrated"] is True
        assert result["files_moved"] == 2
        assert (um_dir / "groups" / "default" / "user1.json").exists()
        assert (um_dir / "groups" / "default" / "user2.json").exists()
        assert not (um_dir / "user1.json").exists()
        assert (um_dir / ".backup_pre_v0_28" / "user1.json").exists()
        assert (um_dir / ".migration_v0_28_done").exists()

    def test_idempotent(self, tmp_path):
        um_dir = tmp_path / "user_memory"
        um_dir.mkdir()
        (um_dir / "user1.json").write_text('{"name": "Alice"}', encoding="utf-8")

        migrate_workspace(tmp_path)
        result2 = migrate_workspace(tmp_path)

        assert result2["migrated"] is False
        assert result2["reason"] == "already_migrated"

    def test_no_old_files_writes_marker(self, tmp_path):
        um_dir = tmp_path / "user_memory"
        um_dir.mkdir()
        (um_dir / "groups" / "default").mkdir(parents=True)

        result = migrate_workspace(tmp_path)

        assert result["migrated"] is False
        assert result["reason"] == "no_old_files"
        assert (um_dir / ".migration_v0_28_done").exists()
