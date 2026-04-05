from __future__ import annotations

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

    def _fake_load_session(path, work_path):  # noqa: ANN001
        assert path == persisted
        assert work_path == tmp_path
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
    assert providers[0]["type"] == "openai-compatible"



