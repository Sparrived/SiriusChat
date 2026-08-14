"""Tests for the persona-local read_skill tool."""

from __future__ import annotations

from pathlib import Path

from sirius_pulse.memory.user.unified_models import UnifiedUser
from sirius_pulse.tools import ToolExecutor, ToolInvocationContext, ToolRegistry
from sirius_pulse.tools.builtin import read_skill
from sirius_pulse.tools.data_store import ToolDataStore


def _store(persona_dir: Path) -> ToolDataStore:
    store_path = persona_dir / "tool_data" / "read_skill.json"
    return ToolDataStore(store_path)


def _write_skill(persona_dir: Path, name: str = "release-checklist") -> Path:
    skill_dir = persona_dir / "skills" / name
    skill_dir.mkdir(parents=True)
    long_body = ("先运行测试，再检查版本；确认依赖已经锁定，" "检查工作区没有未提交的敏感文件，验证构建产物和发布地址都能正常访问。\n") * 4
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: release-checklist\n"
        'description: "发布前执行检查"\n'
        "---\n\n"
        "# 发布检查\n\n" + long_body,
        encoding="utf-8",
    )
    (skill_dir / "references.md").write_text("参考内容\n", encoding="utf-8")
    return skill_dir


def test_read_skill_when_name_is_empty_then_lists_skill_metadata(tmp_path: Path):
    _write_skill(tmp_path)

    result = read_skill.run(data_store=_store(tmp_path))

    assert result["success"] is True
    assert "release-checklist" in result["text_blocks"][0]
    assert "发布前执行检查" in result["text_blocks"][0]


def test_read_skill_when_persona_has_no_defaults_then_reads_packaged_write_skill(
    tmp_path: Path,
):
    result = read_skill.run(skill_name="write_skill", data_store=_store(tmp_path))

    assert result["success"] is True
    assert result["data"]["skill_name"] == "write-skill"
    assert "创建或更新 Agent Skill" in result["data"]["content"]


def test_read_skill_when_persona_overrides_default_then_uses_persona_file(tmp_path: Path):
    skill_dir = tmp_path / "skills" / "write_skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: write_skill\ndescription: 自定义创建规范\n---\n\n自定义内容\n",
        encoding="utf-8",
    )

    result = read_skill.run(skill_name="write_skill", data_store=_store(tmp_path))

    assert result["success"] is True
    assert result["data"]["content"].endswith("自定义内容\n")
    assert str(skill_dir) in result["data"]["path"]


def test_read_skill_when_name_is_given_then_returns_chunk_and_next_offset(tmp_path: Path):
    _write_skill(tmp_path)

    result = read_skill.run(
        skill_name="release-checklist",
        max_chars=256,
        data_store=_store(tmp_path),
    )

    assert result["success"] is True
    assert result["data"]["relative_path"] == "SKILL.md"
    assert result["data"]["total_chars"] > 256
    assert result["data"]["has_more"] is True
    assert result["data"]["next_offset"] == 256
    assert "先运行测试" in result["data"]["content"]
    assert result["internal_metadata"]["model_content_kind"] == "skill"


def test_read_skill_when_relative_path_is_inside_skill_then_reads_reference(tmp_path: Path):
    _write_skill(tmp_path)

    result = read_skill.run(
        skill_name="release-checklist",
        relative_path="references.md",
        data_store=_store(tmp_path),
    )

    assert result["success"] is True
    assert result["data"]["content"] == "参考内容\n"


def test_read_skill_rejects_path_traversal_and_unknown_skill(tmp_path: Path):
    skill_dir = _write_skill(tmp_path)
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
    store = _store(tmp_path)

    traversal = read_skill.run(
        skill_name="release-checklist",
        relative_path="../secret.txt",
        data_store=store,
    )
    unknown = read_skill.run(skill_name="missing", data_store=store)

    assert traversal["success"] is False
    assert unknown["success"] is False
    assert skill_dir.exists()


def test_read_skill_when_called_through_tool_executor_uses_persona_work_path(tmp_path: Path):
    _write_skill(tmp_path)
    registry = ToolRegistry()
    registry.load_from_directory(
        tmp_path / "tools",
        auto_install_deps=False,
        include_builtin=True,
    )
    tool = registry.get("read_skill")
    assert tool is not None

    result = ToolExecutor(tmp_path).execute(
        tool,
        {"skill_name": "release-checklist"},
        invocation_context=ToolInvocationContext(caller=UnifiedUser(user_id="u1", name="u1")),
    )

    assert result.success is True
    assert "先运行测试" in result.to_display_text()
