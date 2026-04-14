from __future__ import annotations

from pathlib import Path
import pytest
import json

import main as main_module


def test_probe_provider_before_bootstrap_raises_when_unavailable(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        main_module,
        "_load_providers_config_from_config_file",
        lambda _config_path: [
            {
                "type": "openai-compatible",
                "api_key": "k",
                "base_url": "https://api.openai.com",
                "healthcheck_model": "mock-model",
            },
        ],
    )

    def _raise_detection(**_kwargs) -> None:  # noqa: ANN003
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(main_module, "run_provider_detection_flow", _raise_detection)

    with pytest.raises(RuntimeError, match="provider unavailable"):
        main_module._run_framework_provider_detection(
            config_path=config_path,
            work_path=tmp_path,
            print_func=lambda _msg: None,
        )


def test_bootstrap_first_generated_agent_does_not_probe_provider(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        main_module,
        "_load_providers_config_from_config_file",
        lambda _config_path: [
            {"type": "openai-compatible", "api_key": "k", "base_url": "https://api.openai.com"}
        ],
    )
    monkeypatch.setattr(main_module, "_build_provider", lambda *_a, **_k: object())

    def _unexpected_detection(**_kwargs) -> None:  # noqa: ANN003
        raise AssertionError("bootstrap should not run framework detection")

    monkeypatch.setattr(main_module, "run_provider_detection_flow", _unexpected_detection)

    def _unexpected_questions():
        return []

    monkeypatch.setattr(main_module, "generate_humanized_roleplay_questions", _unexpected_questions)

    async def _fake_build(*_args, **_kwargs) -> None:  # noqa: ANN003
        return None

    monkeypatch.setattr(main_module, "abuild_roleplay_prompt_from_answers_and_apply", _fake_build)
    monkeypatch.setattr(main_module, "_save_generated_agent_key_to_config", lambda *_a, **_k: None)

    answers = iter(["主助手", "", "mock-model", "main_agent"])
    input_func = lambda _prompt: next(answers)

    assert (
        main_module._bootstrap_first_generated_agent(
            config_path=config_path,
            work_path=tmp_path,
            provider_factory=None,
            input_func=input_func,
            print_func=lambda _msg: None,
        )
        is True
    )


def test_run_framework_provider_detection_prefers_registry_over_stale_config(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")

    main_module.ProviderRegistry(tmp_path).upsert(
        provider_type="siliconflow",
        api_key="sf-key",
        base_url="https://api.siliconflow.cn",
        healthcheck_model="Pro/test-model",
    )

    monkeypatch.setattr(
        main_module,
        "_load_providers_config_from_config_file",
        lambda _config_path: [
            {
                "type": "openai-compatible",
                "api_key": "openai-key",
                "base_url": "https://api.openai.com",
                "healthcheck_model": "",
            },
        ],
    )

    calls: list[dict[str, object]] = []

    def _fake_run_detection(*, providers):  # noqa: ANN001
        calls.append(providers)

    monkeypatch.setattr(main_module, "run_provider_detection_flow", _fake_run_detection)

    main_module._run_framework_provider_detection(
        config_path=config_path,
        work_path=tmp_path,
        print_func=lambda _msg: None,
    )

    assert len(calls) == 1
    assert "siliconflow" in calls[0]
    assert "openai-compatible" not in calls[0]


def test_load_or_persist_session_bundle_loads_generated_key_persisted(monkeypatch, tmp_path) -> None:
    persisted = tmp_path / "session_config.persisted.json"
    persisted.write_text(
        json.dumps(
            {
                "generated_agent_key": "main_agent",
                "providers": [
                    {
                        "type": "openai-compatible",
                        "base_url": "https://api.openai.com",
                        "api_key": "k",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        main_module,
        "create_session_config_from_selected_agent",
        lambda **kwargs: main_module.SessionConfig(
            work_path=kwargs["work_path"],
            preset=main_module.AgentPreset(
                agent=main_module.Agent(name="主助手", persona="测试人格", model="mock-model"),
                global_system_prompt="测试系统提示词",
            ),
        ),
    )

    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")

    session, providers = main_module._load_or_persist_session_bundle(
        config_path=config_path,
        work_path=tmp_path,
        print_func=lambda _msg: None,
    )

    assert session.agent.name == "主助手"
    assert session.global_system_prompt == "测试系统提示词"
    assert len(providers) == 1
    assert providers[0]["type"] == "openai-compatible"


def test_load_or_persist_session_bundle_uses_load_session_config_for_persisted(monkeypatch, tmp_path) -> None:
    persisted = tmp_path / "session_config.persisted.json"
    persisted.write_text("{}", encoding="utf-8")
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")

    def _fake_load_session(path, work_path, *, config_root=None):  # noqa: ANN001
        assert path == persisted
        assert work_path == tmp_path
        assert config_root == tmp_path
        return (
            main_module.SessionConfig(
                work_path=tmp_path,
                preset=main_module.AgentPreset(
                    agent=main_module.Agent(name="主助手", persona="测试", model="mock-model"),
                    global_system_prompt="测试系统提示词",
                ),
            ),
            [{"type": "openai-compatible", "base_url": "https://api.openai.com", "api_key": "k"}],
        )

    monkeypatch.setattr(main_module, "_load_session_config", _fake_load_session)

    session, providers = main_module._load_or_persist_session_bundle(
        config_path=config_path,
        work_path=tmp_path,
        print_func=lambda _msg: None,
    )

    assert session.agent.name == "主助手"
    assert len(providers) == 1


def test_load_or_persist_session_bundle_reads_persisted_copy_from_config_root(monkeypatch, tmp_path) -> None:
    config_root = tmp_path / "config"
    work_path = tmp_path / "runtime"
    config_root.mkdir()
    work_path.mkdir()

    persisted = config_root / "session_config.persisted.json"
    persisted.write_text("{}", encoding="utf-8")
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")

    def _fake_load_session(path, runtime_path, *, config_root=None):  # noqa: ANN001
        assert path == persisted
        assert runtime_path == work_path
        assert config_root == config_root_path
        return (
            main_module.SessionConfig(
                work_path=config_root_path,
                data_path=work_path,
                preset=main_module.AgentPreset(
                    agent=main_module.Agent(name="主助手", persona="测试", model="mock-model"),
                    global_system_prompt="测试系统提示词",
                ),
            ),
            [{"type": "openai-compatible", "base_url": "https://api.openai.com", "api_key": "k"}],
        )

    config_root_path = config_root
    monkeypatch.setattr(main_module, "_load_session_config", _fake_load_session)

    session, providers = main_module._load_or_persist_session_bundle(
        config_path=config_path,
        work_path=work_path,
        config_root=config_root_path,
        print_func=lambda _msg: None,
    )

    assert session.work_path == config_root_path
    assert session.data_path == work_path
    assert len(providers) == 1


def test_save_generated_agent_key_to_config_preserves_jsonc_comments(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{\n  // 首次初始化前为空\n  "generated_agent_key": "",\n  "providers": [\n    {\n      "type": "openai-compatible",\n      "api_key": "k"\n    }\n  ]\n}\n',
        encoding="utf-8",
    )

    main_module._save_generated_agent_key_to_config(config_path, "main_agent")

    content = config_path.read_text(encoding="utf-8")
    assert "//" in content
    assert '"generated_agent_key": "main_agent"' in content
    assert main_module._load_generated_agent_key_from_config_file(config_path) == "main_agent"


# ---------------------------------------------------------------------------
# 会话恢复基准（原 test_main_resume.py）
# ---------------------------------------------------------------------------


class _FakeRuntime:
    def __init__(self, transcript):  # noqa: ANN001
        self.transcript = transcript
        self.cleared_sessions: list[str] = []
        self.primary_users: list[tuple[str, object]] = []

    async def get_transcript(self, session_id: str):  # noqa: ANN201
        _ = session_id
        return self.transcript

    async def clear_session(self, session_id: str) -> None:
        self.cleared_sessions.append(session_id)

    async def set_primary_user(self, session_id: str, participant) -> None:  # noqa: ANN001
        self.primary_users.append((session_id, participant))


def _setup_resume_monkeypatch(monkeypatch, tmp_path, runtime):
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(main_module, "_resolve_runtime_paths", lambda *_a, **_k: (config_path, tmp_path))
    monkeypatch.setattr(main_module, "_persist_last_config_path", lambda *_a, **_k: None)
    monkeypatch.setattr(
        main_module,
        "_load_or_persist_session_bundle",
        lambda **_k: (
            main_module.SessionConfig(
                work_path=tmp_path,
                preset=main_module.AgentPreset(
                    agent=main_module.Agent(name="主助手", persona="测试", model="mock-model"),
                    global_system_prompt="测试系统提示词",
                ),
            ),
            [{"type": "openai-compatible", "base_url": "https://api.openai.com", "api_key": "k"}],
        ),
    )
    monkeypatch.setattr(
        main_module, "_bootstrap_primary_user",
        lambda **_k: main_module.Participant(name="用户", user_id="u1"),
    )
    monkeypatch.setattr(main_module, "_build_provider", lambda *_a, **_k: object())
    monkeypatch.setattr(main_module, "_build_runtime", lambda **_k: runtime)
    monkeypatch.setattr(main_module, "_write_transcript_output", lambda *_a, **_k: None)


def test_main_auto_resumes_by_default(monkeypatch, tmp_path) -> None:
    loaded_transcript = object()
    captured = {"transcript": None}
    runtime = _FakeRuntime(loaded_transcript)
    _setup_resume_monkeypatch(monkeypatch, tmp_path, runtime)

    def _fake_run(*_args, **kwargs):  # noqa: ANN001
        captured["transcript"] = _args[6] if len(_args) > 6 else kwargs.get("transcript")
        return main_module.Transcript()

    monkeypatch.setattr(main_module, "run_interactive_session", _fake_run)
    exit_code = main_module.main([], input_func=lambda _p: "", print_func=lambda _m: None)
    assert exit_code == 0
    assert captured["transcript"] is loaded_transcript
    assert runtime.cleared_sessions == []


def test_main_no_resume_disables_auto_resume(monkeypatch, tmp_path) -> None:
    captured: dict[str, object | None] = {"transcript": "sentinel"}
    runtime = _FakeRuntime(object())
    _setup_resume_monkeypatch(monkeypatch, tmp_path, runtime)

    def _fake_run(*_args, **kwargs):  # noqa: ANN001
        captured["transcript"] = _args[6] if len(_args) > 6 else kwargs.get("transcript")
        return main_module.Transcript()

    monkeypatch.setattr(main_module, "run_interactive_session", _fake_run)
    exit_code = main_module.main(["--no-resume"], input_func=lambda _p: "", print_func=lambda _m: None)
    assert exit_code == 0
    assert captured["transcript"] is None
    assert runtime.cleared_sessions == ["default"]


def test_main_writes_default_output_under_relative_work_path(monkeypatch, tmp_path) -> None:
    relative_work_path = Path("runtime")
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")
    captured: dict[str, Path | None] = {"output_path": None}

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(main_module, "_resolve_runtime_paths", lambda *_a, **_k: (config_path, relative_work_path))
    monkeypatch.setattr(main_module, "_persist_last_config_path", lambda *_a, **_k: None)
    monkeypatch.setattr(
        main_module,
        "_load_or_persist_session_bundle",
        lambda **_k: (
            main_module.SessionConfig(
                work_path=relative_work_path,
                preset=main_module.AgentPreset(
                    agent=main_module.Agent(name="主助手", persona="测试", model="mock-model"),
                    global_system_prompt="测试系统提示词",
                ),
            ),
            [{"type": "openai-compatible", "base_url": "https://api.openai.com", "api_key": "k"}],
        ),
    )
    monkeypatch.setattr(
        main_module,
        "_bootstrap_primary_user",
        lambda **_k: main_module.Participant(name="用户", user_id="u1"),
    )
    monkeypatch.setattr(main_module, "_build_provider", lambda *_a, **_k: object())
    monkeypatch.setattr(main_module, "_build_runtime", lambda **_k: _FakeRuntime(None))
    monkeypatch.setattr(main_module, "run_interactive_session", lambda *_a, **_k: main_module.Transcript())
    monkeypatch.setattr(
        main_module,
        "_write_transcript_output",
        lambda _transcript, output_path: captured.__setitem__("output_path", output_path),
    )

    exit_code = main_module.main([], input_func=lambda _p: "", print_func=lambda _m: None)

    assert exit_code == 0
    assert captured["output_path"] == relative_work_path / "transcript.json"


def test_handle_provider_command_registers_under_workspace_root(monkeypatch, tmp_path) -> None:
    registry = main_module.ProviderRegistry(tmp_path)
    captured: dict[str, object] = {}

    def _fake_register(**kwargs):  # noqa: ANN003
        captured.update(kwargs)
        return "openai-compatible"

    monkeypatch.setattr(main_module, "register_provider_with_validation", _fake_register)

    handled, changed = main_module._handle_provider_command(
        "/provider add openai-compatible test-key gpt-4o-mini https://api.openai.com",
        provider_registry=registry,
        print_func=lambda _msg: None,
    )

    assert handled is True
    assert changed is True
    assert captured["work_path"] == tmp_path



