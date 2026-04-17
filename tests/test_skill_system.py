"""Tests for the skill system.

Covers:
- SkillDefinition, SkillParameter, SkillResult models
- SkillDataStore persistence
- SkillRegistry loading and discovery
- SkillExecutor parameter validation and execution
- Skill call parsing (parse_skill_calls, strip_skill_calls)
- Engine integration (skill calls in _generate_assistant_message)
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from sirius_chat.memory import UserProfile
from sirius_chat.skills.models import SkillDefinition, SkillInvocationContext, SkillParameter, SkillResult
from sirius_chat.skills.data_store import SkillDataStore
from sirius_chat.skills.registry import SkillRegistry
from sirius_chat.skills.executor import (
    SkillExecutor,
    parse_skill_calls,
    strip_skill_calls,
    _coerce_type,
)
from sirius_chat.core.markers import SKILL_CALL_MARKER


# ─────────────────────── Model tests ───────────────────────

class TestSkillParameter:
    def test_basic_creation(self):
        p = SkillParameter(name="x", type="int", description="a number")
        assert p.name == "x"
        assert p.type == "int"
        assert p.required is False
        assert p.default is None

    def test_required_with_default(self):
        p = SkillParameter(name="q", type="str", description="query", required=True, default="hi")
        assert p.required is True
        assert p.default == "hi"


class TestSkillResult:
    def test_success_display(self):
        r = SkillResult(success=True, data={"cpu": "4 cores", "mem": "16GB"})
        text = r.to_display_text()
        assert "cpu" in text
        assert "4 cores" in text

    def test_failure_display(self):
        r = SkillResult(success=False, error="module not found")
        text = r.to_display_text()
        assert "SKILL执行失败" in text
        assert "module not found" in text

    def test_none_data_display(self):
        r = SkillResult(success=True, data=None)
        assert "执行完成" in r.to_display_text()

    def test_dict_with_nested(self):
        r = SkillResult(success=True, data={"section": {"a": 1, "b": 2}, "list_val": [1, 2, 3]})
        text = r.to_display_text()
        assert "section:" in text
        assert "list_val:" in text

    def test_from_raw_result_extracts_internal_text_and_multimodal_blocks(self):
        result = SkillResult.from_raw_result(
            {
                "summary": "会显示在展示文本里",
                "text_blocks": [
                    {"type": "text", "value": "检测到蓝天", "label": "summary"},
                ],
                "multimodal_blocks": [
                    {
                        "type": "image",
                        "value": "https://example.com/sky.png",
                        "mime_type": "image/png",
                        "label": "source",
                    }
                ],
                "internal_metadata": {"trace_id": "abc123"},
            }
        )

        assert result.success is True
        assert len(result.text_blocks) == 1
        assert result.text_blocks[0].value == "检测到蓝天"
        assert len(result.multimodal_blocks) == 1
        assert result.multimodal_blocks[0].mime_type == "image/png"
        assert result.internal_metadata == {"trace_id": "abc123"}

        payload = result.to_internal_payload()
        assert payload["text_blocks"][0]["value"] == "检测到蓝天"
        assert payload["multimodal_blocks"][0]["value"] == "https://example.com/sky.png"
        assert payload["internal_metadata"]["trace_id"] == "abc123"

    def test_display_text_hides_internal_metadata_fields(self):
        result = SkillResult.from_raw_result(
            {
                "status": "ok",
                "internal_metadata": {"trace_id": "abc123"},
                "metadata": {"debug": True},
                "attachments": [{"value": "https://example.com/a.png"}],
            }
        )

        text = result.to_display_text()
        assert "status: ok" in text
        assert "trace_id" not in text
        assert "attachments" not in text


class TestSkillDefinition:
    def test_parameter_schema(self):
        skill = SkillDefinition(
            name="test",
            description="A test skill",
            parameters=[
                SkillParameter(name="x", type="int", description="number", required=True),
                SkillParameter(name="y", type="str", description="name", default="default"),
            ],
        )
        schema = skill.get_parameter_schema()
        assert len(schema) == 2
        assert schema[0]["name"] == "x"
        assert schema[0]["required"] is True
        assert "default" not in schema[0]
        assert schema[1]["default"] == "default"


# ─────────────────────── DataStore tests ───────────────────────

class TestSkillDataStore:
    def test_get_set_delete(self, tmp_path: Path):
        store = SkillDataStore(tmp_path / "store.json")
        assert store.get("key") is None
        assert store.get("key", "fallback") == "fallback"

        store.set("key", 42)
        assert store.get("key") == 42
        assert store.is_dirty

        assert store.delete("key") is True
        assert store.get("key") is None
        assert store.delete("nonexistent") is False

    def test_keys_and_all(self, tmp_path: Path):
        store = SkillDataStore(tmp_path / "store.json")
        store.set("a", 1)
        store.set("b", 2)
        assert sorted(store.keys()) == ["a", "b"]
        assert store.all() == {"a": 1, "b": 2}

    def test_persistence(self, tmp_path: Path):
        path = tmp_path / "store.json"
        store1 = SkillDataStore(path)
        store1.set("key", {"nested": True})
        store1.save()

        store2 = SkillDataStore(path)
        assert store2.get("key") == {"nested": True}
        assert not store2.is_dirty

    def test_no_save_when_not_dirty(self, tmp_path: Path):
        path = tmp_path / "store.json"
        store = SkillDataStore(path)
        store.save()  # Should not create file
        assert not path.exists()

    def test_corrupted_file_handled(self, tmp_path: Path):
        path = tmp_path / "store.json"
        path.write_text("not json", encoding="utf-8")
        store = SkillDataStore(path)
        assert store.all() == {}


# ─────────────────────── Registry tests ───────────────────────

SAMPLE_SKILL_CODE = '''
SKILL_META = {
    "name": "greet",
    "description": "Say hello",
    "version": "1.0.0",
    "parameters": {
        "name": {
            "type": "str",
            "description": "Name to greet",
            "required": True,
        },
    },
}

def run(name: str, **kwargs):
    return {"greeting": f"Hello, {name}!"}
'''

SAMPLE_SKILL_LIST_PARAMS = '''
SKILL_META = {
    "name": "calc",
    "description": "Calculator",
    "parameters": [
        {"name": "a", "type": "int", "description": "first", "required": True},
        {"name": "b", "type": "int", "description": "second", "required": True},
    ],
}

def run(a: int, b: int, **kwargs):
    return {"sum": a + b}
'''


class TestSkillRegistry:
    def test_load_from_directory_bootstraps_skills_dir_and_readme(self, tmp_path: Path):
        skills_dir = tmp_path / "skills"

        registry = SkillRegistry()
        count = registry.load_from_directory(skills_dir)

        assert count == 0
        assert skills_dir.exists()
        readme_path = skills_dir / "README.md"
        assert readme_path.exists()
        readme_text = readme_path.read_text(encoding="utf-8")
        assert "SKILL_META" in readme_text
        assert "run()" in readme_text

    def test_load_from_directory(self, tmp_path: Path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "greet.py").write_text(SAMPLE_SKILL_CODE, encoding="utf-8")

        registry = SkillRegistry()
        count = registry.load_from_directory(skills_dir)
        assert count == 1
        assert "greet" in registry.skill_names

        skill = registry.get("greet")
        assert skill is not None
        assert skill.description == "Say hello"
        assert skill.version == "1.0.0"
        assert len(skill.parameters) == 1
        assert skill.parameters[0].name == "name"
        assert skill.parameters[0].required is True

    def test_load_with_list_params(self, tmp_path: Path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "calc.py").write_text(SAMPLE_SKILL_LIST_PARAMS, encoding="utf-8")

        registry = SkillRegistry()
        count = registry.load_from_directory(skills_dir)
        assert count == 1
        skill = registry.get("calc")
        assert skill is not None
        assert len(skill.parameters) == 2

    def test_load_nonexistent_directory(self, tmp_path: Path):
        registry = SkillRegistry()
        count = registry.load_from_directory(tmp_path / "nonexistent")
        assert count == 0
        assert (tmp_path / "nonexistent").exists()
        assert (tmp_path / "nonexistent" / "README.md").exists()

    def test_skip_underscore_files(self, tmp_path: Path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "__init__.py").write_text("# init", encoding="utf-8")
        (skills_dir / "_helper.py").write_text("# private", encoding="utf-8")

        registry = SkillRegistry()
        count = registry.load_from_directory(skills_dir)
        assert count == 0

    def test_skip_file_without_meta(self, tmp_path: Path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "bad.py").write_text("x = 1\n", encoding="utf-8")

        registry = SkillRegistry()
        count = registry.load_from_directory(skills_dir)
        assert count == 0

    def test_skip_file_without_run(self, tmp_path: Path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "no_run.py").write_text(
            'SKILL_META = {"name": "no_run", "description": "Missing run"}\n',
            encoding="utf-8",
        )

        registry = SkillRegistry()
        count = registry.load_from_directory(skills_dir)
        assert count == 0

    def test_register_manual(self):
        registry = SkillRegistry()
        skill = SkillDefinition(name="manual", description="Manual skill")
        registry.register(skill)
        assert registry.get("manual") is skill

    def test_build_tool_descriptions(self, tmp_path: Path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "greet.py").write_text(SAMPLE_SKILL_CODE, encoding="utf-8")

        registry = SkillRegistry()
        registry.load_from_directory(skills_dir)
        text = registry.build_tool_descriptions()
        assert "greet" in text
        assert "Say hello" in text
        assert "name" in text

    def test_build_tool_descriptions_hides_developer_only_skills_for_non_developer(self):
        registry = SkillRegistry()
        registry.register(SkillDefinition(name="public", description="公开工具"))
        registry.register(
            SkillDefinition(
                name="desktop_screenshot",
                description="截图",
                developer_only=True,
            )
        )

        developer = UserProfile(user_id="dev-1", name="开发者", metadata={"is_developer": True})
        user_ctx = SkillInvocationContext(
            caller=UserProfile(user_id="user-1", name="普通用户"),
            developer_profiles=[developer],
        )
        developer_ctx = SkillInvocationContext(caller=developer, developer_profiles=[developer])

        hidden_text = registry.build_tool_descriptions(invocation_context=user_ctx)
        visible_text = registry.build_tool_descriptions(invocation_context=developer_ctx)

        assert "public" in hidden_text
        assert "desktop_screenshot" not in hidden_text
        assert "desktop_screenshot" in visible_text
        assert "仅 developer 可调用" in visible_text

    def test_build_tool_descriptions_empty(self):
        registry = SkillRegistry()
        assert registry.build_tool_descriptions() == ""

    def test_all_skills(self, tmp_path: Path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "greet.py").write_text(SAMPLE_SKILL_CODE, encoding="utf-8")
        (skills_dir / "calc.py").write_text(SAMPLE_SKILL_LIST_PARAMS, encoding="utf-8")

        registry = SkillRegistry()
        registry.load_from_directory(skills_dir)
        assert len(registry.all_skills()) == 2

    def test_reload_from_directory_replaces_removed_skills(self, tmp_path: Path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "greet.py").write_text(SAMPLE_SKILL_CODE, encoding="utf-8")
        (skills_dir / "calc.py").write_text(SAMPLE_SKILL_LIST_PARAMS, encoding="utf-8")

        registry = SkillRegistry()
        assert registry.reload_from_directory(skills_dir, auto_install_deps=False) == 2
        assert sorted(registry.skill_names) == ["calc", "greet"]

        (skills_dir / "calc.py").unlink()
        assert registry.reload_from_directory(skills_dir, auto_install_deps=False) == 1
        assert registry.skill_names == ["greet"]

    def test_load_from_directory_can_include_builtin_skills(self, tmp_path: Path):
        registry = SkillRegistry()
        count = registry.load_from_directory(
            tmp_path / "skills",
            auto_install_deps=False,
            include_builtin=True,
        )

        assert count >= 1
        assert "system_info" in registry.skill_names

    def test_include_builtin_skills_resolves_dependencies(self, tmp_path: Path, monkeypatch):
        import sirius_chat.skills.registry as registry_module

        calls: list[str] = []

        def _fake_resolve(skill_file: Path, *, auto_install: bool = True) -> list[str]:
            calls.append(f"{skill_file.stem}:{auto_install}")
            return []

        monkeypatch.setattr(registry_module, "resolve_skill_dependencies", _fake_resolve)

        registry = SkillRegistry()
        registry.load_from_directory(
            tmp_path / "skills",
            auto_install_deps=True,
            include_builtin=True,
        )

        assert any(call.startswith("system_info:") for call in calls)
        assert any(call.startswith("desktop_screenshot:") for call in calls)

    def test_workspace_skill_can_override_builtin(self, tmp_path: Path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "system_info.py").write_text(
            '''
SKILL_META = {"name": "system_info", "description": "override", "parameters": {}}

def run(**kwargs):
    return {"source": "workspace"}
'''.strip(),
            encoding="utf-8",
        )

        registry = SkillRegistry()
        registry.reload_from_directory(
            skills_dir,
            auto_install_deps=False,
            include_builtin=True,
        )
        executor = SkillExecutor(tmp_path)

        skill = registry.get("system_info")
        assert skill is not None
        result = executor.execute(skill, {})
        assert result.success is True
        assert result.data["source"] == "workspace"


# ─────────────────────── Executor tests ───────────────────────

class TestSkillExecutor:
    def _make_skill(self, tmp_path: Path) -> tuple[SkillRegistry, SkillExecutor]:
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "greet.py").write_text(SAMPLE_SKILL_CODE, encoding="utf-8")

        registry = SkillRegistry()
        registry.load_from_directory(skills_dir)
        executor = SkillExecutor(tmp_path)
        return registry, executor

    def test_execute_success(self, tmp_path: Path):
        registry, executor = self._make_skill(tmp_path)
        skill = registry.get("greet")
        assert skill is not None

        result = executor.execute(skill, {"name": "World"})
        assert result.success
        assert result.data == {"greeting": "Hello, World!"}

    def test_execute_missing_required_param(self, tmp_path: Path):
        registry, executor = self._make_skill(tmp_path)
        skill = registry.get("greet")
        assert skill is not None

        result = executor.execute(skill, {})
        assert not result.success
        assert "缺少必填参数" in result.error

    def test_execute_with_data_store(self, tmp_path: Path):
        skill_code = '''
SKILL_META = {
    "name": "counter",
    "description": "A counter",
    "parameters": {},
}

def run(data_store=None, **kwargs):
    count = data_store.get("count", 0) + 1
    data_store.set("count", count)
    return {"count": count}
'''
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "counter.py").write_text(skill_code, encoding="utf-8")

        registry = SkillRegistry()
        registry.load_from_directory(skills_dir)
        executor = SkillExecutor(tmp_path)

        skill = registry.get("counter")
        assert skill is not None

        r1 = executor.execute(skill, {})
        assert r1.success and r1.data["count"] == 1

        r2 = executor.execute(skill, {})
        assert r2.success and r2.data["count"] == 2

        # Verify persistence
        store_path = tmp_path / "skill_data" / "counter.json"
        assert store_path.exists()
        data = json.loads(store_path.read_text(encoding="utf-8"))
        assert data["count"] == 2

    def test_execute_without_kwargs_still_succeeds(self, tmp_path: Path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "plain.py").write_text(
            '''
SKILL_META = {"name": "plain", "description": "No kwargs"}

def run():
    return {"ok": True}
'''.strip(),
            encoding="utf-8",
        )

        registry = SkillRegistry()
        registry.load_from_directory(skills_dir)
        executor = SkillExecutor(tmp_path)

        skill = registry.get("plain")
        assert skill is not None

        result = executor.execute(skill, {})
        assert result.success is True
        assert result.data == {"ok": True}

    def test_execute_rejects_developer_only_skill_for_non_developer(self, tmp_path: Path):
        skill = SkillDefinition(
            name="restricted",
            description="developer only",
            developer_only=True,
            _run_func=lambda **kwargs: {"ok": True},
        )
        executor = SkillExecutor(tmp_path)
        developer = UserProfile(user_id="dev-1", name="开发者", metadata={"is_developer": True})
        context = SkillInvocationContext(
            caller=UserProfile(user_id="user-1", name="普通用户"),
            developer_profiles=[developer],
        )

        result = executor.execute(skill, {}, invocation_context=context)
        assert result.success is False
        assert "仅允许 developer 调用" in result.error

    def test_execute_no_run_func(self, tmp_path: Path):
        skill = SkillDefinition(name="broken", description="No run func")
        executor = SkillExecutor(tmp_path)
        result = executor.execute(skill, {})
        assert not result.success
        assert "run()" in result.error

    def test_execute_runtime_error(self, tmp_path: Path):
        skill_code = '''
SKILL_META = {"name": "boom", "description": "Explodes"}

def run(**kwargs):
    raise ValueError("kaboom")
'''
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "boom.py").write_text(skill_code, encoding="utf-8")

        registry = SkillRegistry()
        registry.load_from_directory(skills_dir)
        executor = SkillExecutor(tmp_path)

        skill = registry.get("boom")
        assert skill is not None
        result = executor.execute(skill, {})
        assert not result.success
        assert "kaboom" in result.error

    @pytest.mark.asyncio
    async def test_execute_async(self, tmp_path: Path):
        registry, executor = self._make_skill(tmp_path)
        skill = registry.get("greet")
        assert skill is not None
        result = await executor.execute_async(skill, {"name": "Async"})
        assert result.success
        assert "Async" in str(result.data)

    def test_save_all_stores(self, tmp_path: Path):
        executor = SkillExecutor(tmp_path)
        store = executor._get_data_store("test_skill")
        store.set("key", "value")
        executor.save_all_stores()
        assert (tmp_path / "skill_data" / "test_skill.json").exists()


# ─────────────────────── Parsing tests ───────────────────────

class TestParseSkillCalls:
    def test_basic_call_with_params(self):
        text = 'Some text [SKILL_CALL: my_skill | {"x": 1}] more text'
        calls = parse_skill_calls(text)
        assert len(calls) == 1
        assert calls[0] == ("my_skill", {"x": 1})

    def test_call_without_params(self):
        text = "[SKILL_CALL: system_info]"
        calls = parse_skill_calls(text)
        assert len(calls) == 1
        assert calls[0] == ("system_info", {})

    def test_multiple_calls(self):
        text = "[SKILL_CALL: a | {}] text [SKILL_CALL: b | {\"x\": 2}]"
        calls = parse_skill_calls(text)
        assert len(calls) == 2

    def test_no_calls(self):
        text = "Just regular text with [brackets]"
        calls = parse_skill_calls(text)
        assert calls == []

    def test_invalid_json_params(self):
        text = '[SKILL_CALL: broken | {not valid json}]'
        calls = parse_skill_calls(text)
        assert len(calls) == 1
        assert calls[0] == ("broken", {})

    def test_strip_skill_calls(self):
        text = 'Before [SKILL_CALL: test | {"a": 1}] After'
        stripped = strip_skill_calls(text)
        assert "SKILL_CALL" not in stripped
        assert "Before" in stripped
        assert "After" in stripped

    def test_strip_preserves_no_call_text(self):
        text = "Hello world"
        assert strip_skill_calls(text) == text


# ─────────────────────── Type coercion tests ───────────────────────

class TestCoerceType:
    def test_int_coercion(self):
        assert _coerce_type("42", "int") == 42
        assert _coerce_type(3.14, "int") == 3

    def test_float_coercion(self):
        assert _coerce_type("3.14", "float") == 3.14

    def test_bool_coercion(self):
        assert _coerce_type("true", "bool") is True
        assert _coerce_type("false", "bool") is False
        assert _coerce_type(True, "bool") is True

    def test_list_coercion(self):
        assert _coerce_type('["a","b"]', "list[str]") == ["a", "b"]
        assert _coerce_type("a, b, c", "list[str]") == ["a", "b", "c"]
        assert _coerce_type(["x"], "list") == ["x"]

    def test_str_passthrough(self):
        assert _coerce_type("hello", "str") == "hello"

    def test_invalid_int(self):
        assert _coerce_type("not_a_number", "int") == "not_a_number"


# ─────────────────────── Engine integration tests ───────────────────────

class TestSkillEngineIntegration:
    """Test that OrchestrationPolicy.enable_skills and related fields exist."""

    def test_orchestration_policy_skill_fields(self):
        from sirius_chat.config.models import OrchestrationPolicy

        policy = OrchestrationPolicy(unified_model="test-model", enable_skills=True, pending_message_threshold=0.0)
        assert policy.enable_skills is True
        assert policy.max_skill_rounds == 3
        assert SKILL_CALL_MARKER == "[SKILL_CALL:"

    def test_orchestration_policy_skills_default_on(self):
        from sirius_chat.config.models import OrchestrationPolicy

        policy = OrchestrationPolicy(unified_model="test-model", pending_message_threshold=0.0)
        assert policy.enable_skills is True

    @pytest.mark.asyncio
    async def test_run_live_session_creates_skills_dir_even_when_disabled(self, tmp_path: Path):
        from sirius_chat.api import Agent, AgentPreset, AsyncRolePlayEngine, OrchestrationPolicy, SessionConfig
        from sirius_chat.providers.mock import MockProvider

        work_path = tmp_path / "runtime"
        config = SessionConfig(
            work_path=work_path,
            preset=AgentPreset(
                agent=Agent(name="Bot", persona="helper", model="mock-model"),
                global_system_prompt="Be helpful",
            ),
            orchestration=OrchestrationPolicy(
                unified_model="mock-model",
                enable_skills=False,
                task_enabled={
                    "memory_extract": False,
                    "event_extract": False,
                },
            pending_message_threshold=0.0,
            ),
        )

        engine = AsyncRolePlayEngine(MockProvider())
        await engine.run_live_session(config=config)

        skills_dir = work_path / "skills"
        assert skills_dir.exists()
        assert (skills_dir / "README.md").exists()

    def test_skill_system_prompt_section(self):
        """Verify the system prompt includes skill descriptions when enabled."""
        from sirius_chat.async_engine.prompts import build_system_prompt
        from sirius_chat.config.models import (
            Agent,
            AgentPreset,
            OrchestrationPolicy,
            SessionConfig,
        )
        from sirius_chat.models import Transcript

        config = SessionConfig(
            work_path=Path("/tmp/test"),
            preset=AgentPreset(
                agent=Agent(name="Bot", persona="helper", model="test"),
                global_system_prompt="Be helpful",
            ),
            orchestration=OrchestrationPolicy(
                unified_model="test",
                enable_skills=True,
            pending_message_threshold=0.0,
            ),
        )
        transcript = Transcript()
        prompt = build_system_prompt(
            config,
            transcript,
            skill_descriptions="- system_info: 获取系统信息",
        )
        assert "available_skills" in prompt
        assert "system_info" in prompt
        assert "SKILL_CALL" in prompt
        assert "internal_metadata" in prompt
        assert "mime_type" in prompt
        assert "label" in prompt
        assert "先获取证据，再总结结论" in prompt
        assert "不要把可以通过 SKILL 验证的信息留给猜测" in prompt
        assert "不要先向用户追问你本可自行获取的事实" in prompt

    def test_skill_system_prompt_not_included_when_disabled(self):
        """Verify skills are NOT in prompt when disabled."""
        from sirius_chat.async_engine.prompts import build_system_prompt
        from sirius_chat.config.models import (
            Agent,
            AgentPreset,
            OrchestrationPolicy,
            SessionConfig,
        )
        from sirius_chat.models import Transcript

        config = SessionConfig(
            work_path=Path("/tmp/test"),
            preset=AgentPreset(
                agent=Agent(name="Bot", persona="helper", model="test"),
                global_system_prompt="Be helpful",
            ),
            orchestration=OrchestrationPolicy(unified_model="test", enable_skills=False),
        )
        transcript = Transcript()
        prompt = build_system_prompt(
            config,
            transcript,
            skill_descriptions="- system_info: 获取系统信息",
        )
        assert "available_skills" not in prompt

    def test_public_api_exports(self):
        """Verify skill classes are importable from top-level."""
        from sirius_chat import (
            SkillDataStore,
            SkillDefinition,
            SkillExecutor,
            SkillInvocationContext,
            SkillParameter,
            SkillRegistry,
            SkillResult,
        )
        # Basic smoke test
        assert SkillRegistry is not None
        assert SkillExecutor is not None
        assert SkillInvocationContext is not None


class TestExampleSkillSystemInfo:
    """Test the example system_info skill loads and executes correctly."""

    def test_load_and_run(self):
        import importlib.util

        skill_path = Path(__file__).parent.parent / "examples" / "skills" / "system_info.py"
        if not skill_path.exists():
            pytest.skip("Example skill not found")

        spec = importlib.util.spec_from_file_location("_test_skill_system_info", skill_path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        assert hasattr(module, "SKILL_META")
        assert hasattr(module, "run")

        meta = module.SKILL_META
        assert meta["name"] == "system_info"
        assert "description" in meta

        # Run with os category only (doesn't need psutil)
        result = module.run(categories=["os"])
        assert "os" in result
        assert "system" in result["os"]
        assert "python_version" in result["os"]

    def test_via_registry(self):
        skill_dir = Path(__file__).parent.parent / "examples" / "skills"
        if not skill_dir.exists():
            pytest.skip("Example skills directory not found")

        registry = SkillRegistry()
        count = registry.load_from_directory(skill_dir)
        assert count >= 1

        skill = registry.get("system_info")
        assert skill is not None
        assert skill._run_func is not None

    def test_via_executor(self, tmp_path: Path):
        skill_dir = Path(__file__).parent.parent / "examples" / "skills"
        if not skill_dir.exists():
            pytest.skip("Example skills directory not found")

        registry = SkillRegistry()
        registry.load_from_directory(skill_dir)
        executor = SkillExecutor(tmp_path)

        skill = registry.get("system_info")
        assert skill is not None
        result = executor.execute(skill, {"categories": ["os"]})
        assert result.success
        assert "os" in result.data


class TestBuiltinDesktopScreenshot:
    def test_run_returns_image_block_for_developer(self, tmp_path: Path, monkeypatch):
        from sirius_chat.skills.builtin import desktop_screenshot

        class _FakeImage:
            def save(self, path, format="PNG") -> None:  # noqa: A002
                Path(path).write_bytes(b"fake-png")

        monkeypatch.setattr(
            desktop_screenshot,
            "_capture_desktop_image",
            lambda *, all_screens: _FakeImage(),
        )

        developer = UserProfile(user_id="dev-1", name="开发者", metadata={"is_developer": True})
        context = SkillInvocationContext(caller=developer, developer_profiles=[developer])
        store = SkillDataStore(tmp_path / "skill_data" / "desktop_screenshot.json")

        result = desktop_screenshot.run(
            focus="判断主机当前在做什么",
            data_store=store,
            invocation_context=context,
        )

        assert result["multimodal_blocks"][0]["mime_type"] == "image/png"
        assert "主机当前在做什么" in result["text_blocks"][1]["value"]
        image_path = Path(result["multimodal_blocks"][0]["value"])
        assert image_path.exists()
        assert image_path.parent == store.artifact_dir
        assert store.get("captures", [])[0]["caller_user_id"] == "dev-1"
        assert store.get("captures", [])[0]["analysis_focus"] == "判断主机当前在做什么"
        assert result["internal_metadata"]["analysis_focus"] == "判断主机当前在做什么"

    def test_run_rejects_missing_developer_context(self):
        from sirius_chat.skills.builtin import desktop_screenshot

        with pytest.raises(PermissionError, match="仅允许 developer 调用"):
            desktop_screenshot.run(invocation_context=None)


class TestSkillExecutionTimeout:
    """Test SKILL execution timeout mechanism."""

    def test_orchestration_policy_has_timeout_field(self):
        from sirius_chat.config.models import OrchestrationPolicy

        policy = OrchestrationPolicy(unified_model="test-model", enable_skills=True, pending_message_threshold=0.0)
        assert hasattr(policy, "skill_execution_timeout")
        assert policy.skill_execution_timeout == 30.0

    def test_custom_timeout_value(self):
        from sirius_chat.config.models import OrchestrationPolicy

        policy = OrchestrationPolicy(
            unified_model="test-model",
            enable_skills=True,
            skill_execution_timeout=10.0,
        pending_message_threshold=0.0,
        )
        assert policy.skill_execution_timeout == 10.0

    @pytest.mark.asyncio
    async def test_timeout_returns_failure_result(self, tmp_path: Path):
        """A SKILL that exceeds timeout should return a failure result."""
        import time

        skill = SkillDefinition(
            name="slow_skill",
            description="Takes too long",
            parameters=[],
            _run_func=lambda data_store=None, **kwargs: time.sleep(5) or {"ok": True},
        )
        executor = SkillExecutor(tmp_path)
        result = await executor.execute_async(skill, {}, timeout=0.2)
        assert result.success is False
        assert "超时" in result.error

    @pytest.mark.asyncio
    async def test_timeout_zero_means_no_limit(self, tmp_path: Path):
        """timeout=0 should not impose a time limit."""
        skill = SkillDefinition(
            name="fast_skill",
            description="Quick",
            parameters=[],
            _run_func=lambda data_store=None, **kwargs: {"ok": True},
        )
        executor = SkillExecutor(tmp_path)
        result = await executor.execute_async(skill, {}, timeout=0)
        assert result.success is True
        assert result.data == {"ok": True}

    @pytest.mark.asyncio
    async def test_successful_execution_within_timeout(self, tmp_path: Path):
        """A fast SKILL should succeed even with a timeout set."""
        skill = SkillDefinition(
            name="fast_skill",
            description="Quick",
            parameters=[],
            _run_func=lambda data_store=None, **kwargs: {"value": 42},
        )
        executor = SkillExecutor(tmp_path)
        result = await executor.execute_async(skill, {}, timeout=10.0)
        assert result.success is True
        assert result.data["value"] == 42

    def test_timeout_failure_has_user_friendly_message(self):
        """SkillResult error on timeout should include guidance for user."""
        result = SkillResult(
            success=False,
            error="SKILL执行超时（限制 30 秒），请稍后重试或联系管理员",
        )
        display = result.to_display_text()
        assert "超时" in display
        assert "SKILL执行失败" in display


class TestEnvironmentContext:
    """Test environment context injection in system prompt and engine API."""

    def test_prompt_includes_environment_context(self):
        from sirius_chat.async_engine.prompts import build_system_prompt
        from sirius_chat.config.models import (
            Agent,
            AgentPreset,
            OrchestrationPolicy,
            SessionConfig,
        )
        from sirius_chat.models import Transcript

        config = SessionConfig(
            work_path=Path("/tmp/test"),
            preset=AgentPreset(
                agent=Agent(name="Bot", persona="helper", model="test"),
                global_system_prompt="Be helpful",
            ),
            orchestration=OrchestrationPolicy(unified_model="test"),
        )
        transcript = Transcript()
        prompt = build_system_prompt(
            config,
            transcript,
            environment_context="当前群名: 技术讨论群\n群成员数: 42",
        )
        assert "environment_context" in prompt
        assert "技术讨论群" in prompt
        assert "42" in prompt

    def test_prompt_omits_empty_environment_context(self):
        from sirius_chat.async_engine.prompts import build_system_prompt
        from sirius_chat.config.models import (
            Agent,
            AgentPreset,
            OrchestrationPolicy,
            SessionConfig,
        )
        from sirius_chat.models import Transcript

        config = SessionConfig(
            work_path=Path("/tmp/test"),
            preset=AgentPreset(
                agent=Agent(name="Bot", persona="helper", model="test"),
                global_system_prompt="Be helpful",
            ),
            orchestration=OrchestrationPolicy(unified_model="test"),
        )
        transcript = Transcript()
        prompt = build_system_prompt(config, transcript, environment_context="")
        assert "environment_context" not in prompt

    def test_run_live_message_accepts_environment_context(self):
        """Verify run_live_message signature includes environment_context."""
        import inspect

        from sirius_chat.core._legacy.engine import AsyncRolePlayEngine

        sig = inspect.signature(AsyncRolePlayEngine.run_live_message)
        assert "environment_context" in sig.parameters
        param = sig.parameters["environment_context"]
        assert param.default == ""

    def test_arun_live_message_facade_accepts_environment_context(self):
        """Verify the API facade passes environment_context."""
        import inspect

        from sirius_chat.api.engine import arun_live_message

        sig = inspect.signature(arun_live_message)
        assert "environment_context" in sig.parameters
        param = sig.parameters["environment_context"]
        assert param.default == ""


class TestSystemPromptSlimming:
    """Verify the slimmed-down system prompt preserves essential info but is more compact."""

    def test_agent_identity_compact(self):
        from sirius_chat.async_engine.prompts import build_system_prompt
        from sirius_chat.config.models import (
            Agent,
            AgentPreset,
            OrchestrationPolicy,
            SessionConfig,
        )
        from sirius_chat.models import Transcript

        config = SessionConfig(
            work_path=Path("/tmp/test"),
            preset=AgentPreset(
                agent=Agent(name="TestBot", persona="a helpful bot", model="test"),
                global_system_prompt="",
            ),
            orchestration=OrchestrationPolicy(unified_model="test"),
        )
        transcript = Transcript()
        prompt = build_system_prompt(config, transcript)
        # Should contain compact format
        assert "名: TestBot" in prompt
        assert "设定: a helpful bot" in prompt
        # Should not include old verbose labels
        assert "本名：" not in prompt
        assert "别名：未设置" not in prompt

    def test_agent_alias_only_when_present(self):
        from sirius_chat.async_engine.prompts import build_system_prompt
        from sirius_chat.config.models import (
            Agent,
            AgentPreset,
            OrchestrationPolicy,
            SessionConfig,
        )
        from sirius_chat.models import Transcript

        # Without alias
        config = SessionConfig(
            work_path=Path("/tmp/test"),
            preset=AgentPreset(
                agent=Agent(name="Bot", persona="p", model="test"),
                global_system_prompt="",
            ),
            orchestration=OrchestrationPolicy(unified_model="test"),
        )
        prompt = build_system_prompt(config, Transcript())
        assert "别名:" not in prompt

        # With alias
        config2 = SessionConfig(
            work_path=Path("/tmp/test"),
            preset=AgentPreset(
                agent=Agent(name="Bot", persona="p", model="test", metadata={"alias": "小助手"}),
                global_system_prompt="",
            ),
            orchestration=OrchestrationPolicy(unified_model="test"),
        )
        prompt2 = build_system_prompt(config2, Transcript())
        assert "别名: 小助手" in prompt2

    def test_merged_constraints_section(self):
        from sirius_chat.async_engine.prompts import build_system_prompt
        from sirius_chat.config.models import (
            Agent,
            AgentPreset,
            OrchestrationPolicy,
            SessionConfig,
        )
        from sirius_chat.models import Transcript

        config = SessionConfig(
            work_path=Path("/tmp/test"),
            preset=AgentPreset(
                agent=Agent(name="Bot", persona="p", model="test"),
                global_system_prompt="",
            ),
            orchestration=OrchestrationPolicy(unified_model="test"),
        )
        prompt = build_system_prompt(config, Transcript())
        # Output and security constraints merged into one section
        assert "<constraints>" in prompt
        # Old separate sections should not exist
        assert "<output_constraints>" not in prompt
        assert "<security_constraints>" not in prompt


class TestDependencyResolver:
    """Test SKILL dependency resolution and auto-install logic."""

    def test_extract_declared_dependencies(self, tmp_path: Path):
        from sirius_chat.skills.dependency_resolver import _extract_declared_dependencies

        skill_file = tmp_path / "my_skill.py"
        skill_file.write_text(
            'SKILL_META = {\n'
            '    "name": "test",\n'
            '    "description": "demo",\n'
            '    "dependencies": ["requests", "beautifulsoup4"],\n'
            '}\n'
            'def run(**kw): pass\n',
            encoding="utf-8",
        )
        deps = _extract_declared_dependencies(skill_file)
        assert deps == {"requests", "beautifulsoup4"}

    def test_extract_declared_dependencies_missing_key(self, tmp_path: Path):
        from sirius_chat.skills.dependency_resolver import _extract_declared_dependencies

        skill_file = tmp_path / "my_skill.py"
        skill_file.write_text(
            'SKILL_META = {"name": "test", "description": "demo"}\n'
            'def run(**kw): pass\n',
            encoding="utf-8",
        )
        deps = _extract_declared_dependencies(skill_file)
        assert deps == set()

    def test_extract_imported_packages(self, tmp_path: Path):
        from sirius_chat.skills.dependency_resolver import _extract_imported_packages

        skill_file = tmp_path / "my_skill.py"
        skill_file.write_text(
            'import os\nimport json\nimport requests\n'
            'from bs4 import BeautifulSoup\n'
            'from pathlib import Path\n'
            'def run(**kw): pass\n',
            encoding="utf-8",
        )
        pkgs = _extract_imported_packages(skill_file)
        assert "os" in pkgs
        assert "requests" in pkgs
        assert "beautifulsoup4" in pkgs
        assert "pathlib" in pkgs

    def test_extract_imported_packages_normalizes_pillow(self, tmp_path: Path):
        from sirius_chat.skills.dependency_resolver import _extract_imported_packages

        skill_file = tmp_path / "shot.py"
        skill_file.write_text(
            'from PIL import ImageGrab\n'
            'def run(**kw): pass\n',
            encoding="utf-8",
        )

        pkgs = _extract_imported_packages(skill_file)
        assert "Pillow" in pkgs

    def test_find_missing_checks_package_probe_names(self, monkeypatch):
        import importlib.util

        from sirius_chat.skills.dependency_resolver import _find_missing

        def _fake_find_spec(name: str):
            if name == "PIL":
                return object()
            return None

        monkeypatch.setattr(importlib.util, "find_spec", _fake_find_spec)

        missing = _find_missing({"Pillow"})
        assert missing == set()

    def test_find_missing_filters_stdlib(self):
        from sirius_chat.skills.dependency_resolver import _find_missing

        candidates = {"os", "json", "sys", "pathlib", "collections"}
        missing = _find_missing(candidates)
        assert len(missing) == 0

    def test_find_missing_detects_nonexistent(self):
        from sirius_chat.skills.dependency_resolver import _find_missing

        candidates = {"os", "_nonexistent_pkg_abc123_"}
        missing = _find_missing(candidates)
        assert "_nonexistent_pkg_abc123_" in missing

    def test_pick_installer_returns_valid(self):
        from sirius_chat.skills.dependency_resolver import _pick_installer

        label, cmd = _pick_installer()
        assert label in ("uv", "pip")
        assert len(cmd) >= 3

    def test_resolve_no_deps_needed(self, tmp_path: Path):
        from sirius_chat.skills.dependency_resolver import resolve_skill_dependencies

        skill_file = tmp_path / "simple.py"
        skill_file.write_text(
            'import os\nimport json\n'
            'SKILL_META = {"name": "simple", "description": "test"}\n'
            'def run(**kw): return {}\n',
            encoding="utf-8",
        )
        installed = resolve_skill_dependencies(skill_file, auto_install=False)
        assert installed == []

    def test_resolve_with_auto_install_off(self, tmp_path: Path):
        from sirius_chat.skills.dependency_resolver import resolve_skill_dependencies

        skill_file = tmp_path / "needs_dep.py"
        skill_file.write_text(
            'import _nonexistent_pkg_xyz_\n'
            'SKILL_META = {"name": "needs", "description": "test"}\n'
            'def run(**kw): return {}\n',
            encoding="utf-8",
        )
        installed = resolve_skill_dependencies(skill_file, auto_install=False)
        assert installed == []

    def test_orchestration_policy_auto_install_field(self):
        from sirius_chat.config.models import OrchestrationPolicy

        policy = OrchestrationPolicy(unified_model="m", enable_skills=True, pending_message_threshold=0.0)
        assert policy.auto_install_skill_deps is True

        policy2 = OrchestrationPolicy(
            unified_model="m",
            enable_skills=True,
            auto_install_skill_deps=False,
        pending_message_threshold=0.0,
        )
        assert policy2.auto_install_skill_deps is False

    def test_registry_load_passes_auto_install(self, tmp_path: Path):
        """Verify load_from_directory accepts auto_install_deps kwarg."""
        from sirius_chat.skills.registry import SkillRegistry

        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "demo.py").write_text(
            'import os\n'
            'SKILL_META = {"name": "demo", "description": "test"}\n'
            'def run(**kw): return {"ok": True}\n',
            encoding="utf-8",
        )
        reg = SkillRegistry()
        count = reg.load_from_directory(skills_dir, auto_install_deps=False)
        assert count == 1
        assert "demo" in reg.skill_names
