from __future__ import annotations

import main as main_module


class _FakeStore:
    def __init__(self, exists: bool, payload):  # noqa: ANN001
        self._exists = exists
        self._payload = payload

    def exists(self) -> bool:
        return self._exists

    def load(self):  # noqa: ANN201
        return self._payload

    def save(self, _transcript) -> None:  # noqa: ANN001
        return None


def test_main_auto_resumes_by_default(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")

    loaded_transcript = object()
    captured = {"transcript": None}

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
        main_module,
        "_bootstrap_primary_user",
        lambda **_k: main_module.Participant(name="用户", user_id="u1"),
    )
    monkeypatch.setattr(main_module, "_build_provider", lambda *_a, **_k: object())
    monkeypatch.setattr(main_module, "_create_session_store", lambda **_k: _FakeStore(True, loaded_transcript))

    def _fake_run(*_args, **kwargs):  # noqa: ANN001
        captured["transcript"] = _args[7] if len(_args) > 7 else kwargs.get("transcript")
        return main_module.Transcript()

    monkeypatch.setattr(main_module, "run_interactive_session", _fake_run)
    monkeypatch.setattr(main_module, "_write_transcript_output", lambda *_a, **_k: None)

    exit_code = main_module.main([], input_func=lambda _p: "", print_func=lambda _m: None)

    assert exit_code == 0
    assert captured["transcript"] is loaded_transcript


def test_main_no_resume_disables_auto_resume(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")

    loaded_transcript = object()
    captured = {"transcript": "sentinel"}

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
        main_module,
        "_bootstrap_primary_user",
        lambda **_k: main_module.Participant(name="用户", user_id="u1"),
    )
    monkeypatch.setattr(main_module, "_build_provider", lambda *_a, **_k: object())
    monkeypatch.setattr(main_module, "_create_session_store", lambda **_k: _FakeStore(True, loaded_transcript))

    def _fake_run(*_args, **kwargs):  # noqa: ANN001
        captured["transcript"] = _args[7] if len(_args) > 7 else kwargs.get("transcript")
        return main_module.Transcript()

    monkeypatch.setattr(main_module, "run_interactive_session", _fake_run)
    monkeypatch.setattr(main_module, "_write_transcript_output", lambda *_a, **_k: None)

    exit_code = main_module.main(["--no-resume"], input_func=lambda _p: "", print_func=lambda _m: None)

    assert exit_code == 0
    assert captured["transcript"] is None
