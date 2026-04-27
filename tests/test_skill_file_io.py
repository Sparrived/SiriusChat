"""Tests for file_read and file_write built-in skills."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from sirius_chat.memory import UserProfile
from sirius_chat.skills.data_store import SkillDataStore
from sirius_chat.skills.models import SkillInvocationContext

from sirius_chat.skills.builtin import file_read, file_write, file_list


class TestFileReadSkill:
    """Tests for the file_read built-in skill."""

    @staticmethod
    def _make_store(tmp_path: Path) -> SkillDataStore:
        store_path = tmp_path / "skill_data" / "file_read.json"
        store_path.parent.mkdir(parents=True, exist_ok=True)
        return SkillDataStore(store_path)

    @staticmethod
    def _dev_ctx() -> SkillInvocationContext:
        dev = UserProfile(user_id="u1", name="Dev", metadata={"is_developer": True})
        return SkillInvocationContext(caller=dev, developer_profiles=[dev])

    @staticmethod
    def _non_dev_ctx() -> SkillInvocationContext:
        user = UserProfile(user_id="u2", name="User", metadata={"is_developer": False})
        return SkillInvocationContext(caller=user, developer_profiles=[])

    def test_read_text_file(self, tmp_path: Path):
        store = self._make_store(tmp_path)
        target = tmp_path / "docs" / "readme.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# Hello\nWorld", encoding="utf-8")

        result = file_read.run(
            path=str(target), data_store=store, invocation_context=self._dev_ctx()
        )
        assert result["success"] is True
        assert "# Hello" in result["text_blocks"][0]
        assert result["internal_metadata"]["line_count"] == 2

    def test_read_directory_lists_entries(self, tmp_path: Path):
        store = self._make_store(tmp_path)
        target = tmp_path / "src"
        target.mkdir()
        (target / "main.py").write_text("x", encoding="utf-8")
        (target / "utils").mkdir()

        result = file_read.run(
            path=str(target), data_store=store, invocation_context=self._dev_ctx()
        )
        assert result["success"] is True
        assert result["internal_metadata"]["is_directory"] is True
        listing = result["text_blocks"][0]
        assert "main.py" in listing
        assert "utils/" in listing

    def test_read_nonexistent_file(self, tmp_path: Path):
        store = self._make_store(tmp_path)
        result = file_read.run(
            path=str(tmp_path / "missing.txt"), data_store=store, invocation_context=self._dev_ctx()
        )
        assert result["success"] is False
        assert "不存在" in result["error"]

    def test_read_empty_path(self, tmp_path: Path):
        store = self._make_store(tmp_path)
        result = file_read.run(
            path="", data_store=store, invocation_context=self._dev_ctx()
        )
        assert result["success"] is False
        assert "不能为空" in result["error"]

    def test_read_binary_rejected(self, tmp_path: Path):
        store = self._make_store(tmp_path)
        target = tmp_path / "data.bin"
        target.write_bytes(b"\x00\x01\x02\x03")

        result = file_read.run(
            path=str(target), data_store=store, invocation_context=self._dev_ctx()
        )
        assert result["success"] is False
        assert result["error"]

    def test_read_outside_work_path_allowed(self, tmp_path: Path):
        store = self._make_store(tmp_path)
        sibling = tmp_path.parent / f"sibling_read_{tmp_path.name}"
        sibling.mkdir(exist_ok=True)
        (sibling / "outer.txt").write_text("hello", encoding="utf-8")

        result = file_read.run(
            path=str(sibling / "outer.txt"), data_store=store, invocation_context=self._dev_ctx()
        )
        assert result["success"] is True
        assert "hello" in result["text_blocks"][0]

    def test_read_absolute_path_allowed(self, tmp_path: Path):
        store = self._make_store(tmp_path)
        target = tmp_path / "abs.txt"
        target.write_text("ok", encoding="utf-8")

        result = file_read.run(
            path=str(target), data_store=store, invocation_context=self._dev_ctx()
        )
        assert result["success"] is True
        assert "ok" in result["text_blocks"][0]

    def test_read_deny_pattern_rejected(self, tmp_path: Path):
        store = self._make_store(tmp_path)
        bad = tmp_path / ".git" / "config"
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_text("[core]", encoding="utf-8")

        result = file_read.run(
            path=str(bad), data_store=store, invocation_context=self._dev_ctx()
        )
        assert result["success"] is False
        assert result["error"]

    def test_read_non_developer_allowed(self, tmp_path: Path):
        store = self._make_store(tmp_path)
        target = tmp_path / "public.txt"
        target.write_text("hi", encoding="utf-8")
        result = file_read.run(
            path=str(target), data_store=store, invocation_context=self._non_dev_ctx()
        )
        assert result["success"] is True
        assert "hi" in result["text_blocks"][0]

    def test_read_large_file_rejected(self, tmp_path: Path, monkeypatch: Any):
        store = self._make_store(tmp_path)
        target = tmp_path / "big.txt"
        # Write a small file but monkeypatch the limit to 10 bytes
        target.write_text("hello world", encoding="utf-8")
        monkeypatch.setattr(file_read, "_MAX_SIZE_BYTES", 5)

        result = file_read.run(
            path=str(target), data_store=store, invocation_context=self._dev_ctx()
        )
        assert result["success"] is False
        assert result["error"]


class TestFileWriteSkill:
    """Tests for the file_write built-in skill."""

    @staticmethod
    def _make_store(tmp_path: Path) -> SkillDataStore:
        store_path = tmp_path / "skill_data" / "file_write.json"
        store_path.parent.mkdir(parents=True, exist_ok=True)
        return SkillDataStore(store_path)

    @staticmethod
    def _dev_ctx() -> SkillInvocationContext:
        dev = UserProfile(user_id="u1", name="Dev", metadata={"is_developer": True})
        return SkillInvocationContext(caller=dev, developer_profiles=[dev])

    @staticmethod
    def _non_dev_ctx() -> SkillInvocationContext:
        user = UserProfile(user_id="u2", name="User", metadata={"is_developer": False})
        return SkillInvocationContext(caller=user, developer_profiles=[])

    def test_write_new_file(self, tmp_path: Path):
        store = self._make_store(tmp_path)
        target = tmp_path / "src" / "utils.py"
        result = file_write.run(
            path=str(target),
            content="def helper(): pass\n",
            mode="write",
            data_store=store,
            invocation_context=self._dev_ctx(),
        )
        assert result["success"] is True
        assert target.read_text(encoding="utf-8") == "def helper(): pass\n"

    def test_append_to_existing(self, tmp_path: Path):
        store = self._make_store(tmp_path)
        target = tmp_path / "log.txt"
        target.write_text("line1\n", encoding="utf-8")

        result = file_write.run(
            path=str(target),
            content="line2\n",
            mode="append",
            data_store=store,
            invocation_context=self._dev_ctx(),
        )
        assert result["success"] is True
        assert target.read_text(encoding="utf-8") == "line1\nline2\n"
        assert "追加" in result["summary"]

    def test_write_empty_path(self, tmp_path: Path):
        store = self._make_store(tmp_path)
        result = file_write.run(
            path="",
            content="x",
            data_store=store,
            invocation_context=self._dev_ctx(),
        )
        assert result["success"] is False
        assert "不能为空" in result["error"]

    def test_write_outside_work_path_allowed(self, tmp_path: Path):
        store = self._make_store(tmp_path)
        sibling = tmp_path.parent / f"sibling_write_{tmp_path.name}"
        sibling.mkdir(exist_ok=True)

        result = file_write.run(
            path=str(sibling / "test.py"),
            content="x = 1\n",
            data_store=store,
            invocation_context=self._dev_ctx(),
        )
        assert result["success"] is True
        assert (sibling / "test.py").read_text(encoding="utf-8") == "x = 1\n"

    def test_write_absolute_path_allowed(self, tmp_path: Path):
        store = self._make_store(tmp_path)
        target = tmp_path / "abs.txt"

        result = file_write.run(
            path=str(target),
            content="ok\n",
            data_store=store,
            invocation_context=self._dev_ctx(),
        )
        assert result["success"] is True
        assert target.read_text(encoding="utf-8") == "ok\n"

    def test_write_to_directory_rejected(self, tmp_path: Path):
        store = self._make_store(tmp_path)
        folder = tmp_path / "folder"
        folder.mkdir()
        result = file_write.run(
            path=str(folder),
            content="x",
            data_store=store,
            invocation_context=self._dev_ctx(),
        )
        assert result["success"] is False
        assert "目录" in result["error"]

    def test_write_overwrite_large_file_rejected(self, tmp_path: Path, monkeypatch: Any):
        store = self._make_store(tmp_path)
        target = tmp_path / "big.py"
        target.write_text("x" * 100, encoding="utf-8")
        monkeypatch.setattr(file_write, "_MAX_FILE_SIZE_BYTES", 50)

        result = file_write.run(
            path=str(target),
            content="new",
            mode="write",
            data_store=store,
            invocation_context=self._dev_ctx(),
        )
        assert result["success"] is False
        assert "过大" in result["error"]

    def test_write_to_binary_rejected(self, tmp_path: Path):
        store = self._make_store(tmp_path)
        target = tmp_path / "image.png"
        target.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\x00")

        result = file_write.run(
            path=str(target),
            content="new text",
            mode="write",
            data_store=store,
            invocation_context=self._dev_ctx(),
        )
        assert result["success"] is False
        assert "二进制" in result["error"]

    def test_write_large_content_rejected(self, tmp_path: Path, monkeypatch: Any):
        store = self._make_store(tmp_path)
        monkeypatch.setattr(file_write, "_MAX_WRITE_SIZE_BYTES", 5)

        result = file_write.run(
            path="small.py",
            content="hello world",
            data_store=store,
            invocation_context=self._dev_ctx(),
        )
        assert result["success"] is False
        assert "过大" in result["error"]

    def test_write_deny_pattern_rejected(self, tmp_path: Path):
        store = self._make_store(tmp_path)
        result = file_write.run(
            path=".git/hooks/pre-commit",
            content="#!/bin/bash\n",
            data_store=store,
            invocation_context=self._dev_ctx(),
        )
        assert result["success"] is False
        assert result["error"]

    def test_write_non_developer_denied(self, tmp_path: Path):
        store = self._make_store(tmp_path)
        with pytest.raises(PermissionError):
            file_write.run(
                path=str(tmp_path / "x.txt"),
                content="x",
                data_store=store,
                invocation_context=self._non_dev_ctx(),
            )


class TestFileListSkill:
    """Tests for the file_list built-in skill."""

    @staticmethod
    def _make_store(tmp_path: Path) -> SkillDataStore:
        store_path = tmp_path / "skill_data" / "file_list.json"
        store_path.parent.mkdir(parents=True, exist_ok=True)
        return SkillDataStore(store_path)

    @staticmethod
    def _dev_ctx() -> SkillInvocationContext:
        dev = UserProfile(user_id="u1", name="Dev", metadata={"is_developer": True})
        return SkillInvocationContext(caller=dev, developer_profiles=[dev])

    @staticmethod
    def _non_dev_ctx() -> SkillInvocationContext:
        user = UserProfile(user_id="u2", name="User", metadata={"is_developer": False})
        return SkillInvocationContext(caller=user, developer_profiles=[])

    def test_list_root_shows_files_and_dirs(self, tmp_path: Path):
        store = self._make_store(tmp_path)
        (tmp_path / "main.py").write_text("x", encoding="utf-8")
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "readme.md").write_text("x", encoding="utf-8")

        result = file_list.run(
            path=str(tmp_path), recursive=False, data_store=store, invocation_context=self._dev_ctx()
        )
        assert result["success"] is True
        assert result["internal_metadata"]["count"] >= 2
        text = result["text_blocks"][0]
        assert "main.py" in text
        assert "docs" in text

    def test_list_recursive(self, tmp_path: Path):
        store = self._make_store(tmp_path)
        (tmp_path / "src" / "utils").mkdir(parents=True)
        (tmp_path / "src" / "utils" / "helpers.py").write_text("x", encoding="utf-8")

        result = file_list.run(
            path=str(tmp_path / "src"), recursive=True, data_store=store, invocation_context=self._dev_ctx()
        )
        assert result["success"] is True
        assert result["internal_metadata"]["recursive"] is True
        text = result["text_blocks"][0]
        assert "helpers.py" in text

    def test_list_with_glob_pattern(self, tmp_path: Path):
        store = self._make_store(tmp_path)
        (tmp_path / "a.py").write_text("x", encoding="utf-8")
        (tmp_path / "b.txt").write_text("x", encoding="utf-8")
        (tmp_path / "c.py").write_text("x", encoding="utf-8")

        result = file_list.run(
            path=str(tmp_path), pattern="*.py", data_store=store, invocation_context=self._dev_ctx()
        )
        assert result["success"] is True
        assert result["internal_metadata"]["count"] == 2
        text = result["text_blocks"][0]
        assert "a.py" in text
        assert "c.py" in text
        assert "b.txt" not in text

    def test_list_single_file(self, tmp_path: Path):
        store = self._make_store(tmp_path)
        (tmp_path / "note.md").write_text("hello", encoding="utf-8")

        result = file_list.run(
            path=str(tmp_path / "note.md"), data_store=store, invocation_context=self._dev_ctx()
        )
        assert result["success"] is True
        assert result["internal_metadata"]["count"] == 1
        text = result["text_blocks"][0]
        assert "note.md" in text

    def test_list_nonexistent_path(self, tmp_path: Path):
        store = self._make_store(tmp_path)
        result = file_list.run(
            path="missing", data_store=store, invocation_context=self._dev_ctx()
        )
        assert result["success"] is False
        assert result["error"]

    def test_list_outside_work_path_allowed(self, tmp_path: Path):
        store = self._make_store(tmp_path)
        # Create a sibling directory outside the tmp_path project root
        sibling = tmp_path.parent / f"sibling_{tmp_path.name}"
        sibling.mkdir(exist_ok=True)
        (sibling / "outer.txt").write_text("x", encoding="utf-8")

        # Pass absolute path to list outside the project root
        result = file_list.run(
            path=str(sibling), data_store=store, invocation_context=self._dev_ctx()
        )
        assert result["success"] is True
        assert "outer.txt" in result["text_blocks"][0]

    def test_list_prunes_denied_dirs(self, tmp_path: Path):
        store = self._make_store(tmp_path)
        (tmp_path / ".git" / "hooks").mkdir(parents=True)
        (tmp_path / ".git" / "config").write_text("x", encoding="utf-8")
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "main.py").write_text("x", encoding="utf-8")

        result = file_list.run(
            path=str(tmp_path), recursive=True, data_store=store, invocation_context=self._dev_ctx()
        )
        assert result["success"] is True
        text = result["text_blocks"][0]
        assert ".git" not in text
        assert "main.py" in text

    def test_list_truncates_large_results(self, tmp_path: Path, monkeypatch: Any):
        store = self._make_store(tmp_path)
        for i in range(10):
            (tmp_path / f"f{i}.txt").write_text("x", encoding="utf-8")
        monkeypatch.setattr(file_list, "_MAX_RESULTS", 3)

        result = file_list.run(
            path=str(tmp_path), data_store=store, invocation_context=self._dev_ctx()
        )
        assert result["success"] is True
        assert result["internal_metadata"]["truncated"] is True
        assert result["internal_metadata"]["count"] == 3

    def test_list_non_developer_allowed(self, tmp_path: Path):
        store = self._make_store(tmp_path)
        (tmp_path / "public.txt").write_text("hello", encoding="utf-8")
        result = file_list.run(
            path=str(tmp_path), data_store=store, invocation_context=self._non_dev_ctx()
        )
        assert result["success"] is True
        assert "public.txt" in result["text_blocks"][0]
