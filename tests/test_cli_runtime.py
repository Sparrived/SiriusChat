from __future__ import annotations

import json

import sirius_chat.cli as cli_module


def _write_generated_agents(work_path, key: str = "main_agent") -> None:
    work_path.mkdir(parents=True, exist_ok=True)
    (work_path / "generated_agents.json").write_text(
        json.dumps(
            {
                "selected_generated_agent": key,
                "generated_agents": {
                    key: {
                        "agent": {
                            "name": "主助手",
                            "persona": "测试人格",
                            "model": "mock-model",
                            "temperature": 0.7,
                            "max_tokens": 512,
                        },
                        "global_system_prompt": "测试系统提示词",
                    }
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


class _FakeEngine:
    def __init__(self, response: str = "主助手回复") -> None:
        self.response = response
        self.last_turn = None
        self._transcript = None

    async def run_live_session(self, *, config, transcript=None):  # noqa: ANN001
        _ = config
        if transcript is not None:
            self._transcript = transcript
            return transcript

        transcript_cls = type("Transcript", (), {})
        instance = transcript_cls()
        instance.messages = []
        self._transcript = instance
        return instance

    async def run_live_message(self, *, config, transcript, turn, **kwargs):  # noqa: ANN001
        _ = config
        _ = kwargs
        self.last_turn = turn
        msg = type("Msg", (), {})
        user_msg = msg()
        user_msg.role = "user"
        user_msg.speaker = turn.speaker
        user_msg.content = turn.content

        assistant = msg()
        assistant.role = "assistant"
        assistant.speaker = config.agent.name
        assistant.content = self.response

        transcript.messages = [*transcript.messages, user_msg, assistant]
        self._transcript = transcript
        return transcript


def test_cli_runs_single_turn_with_message_and_output(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "providers": [
                    {
                        "type": "openai-compatible",
                        "base_url": "https://api.openai.com",
                        "api_key": "test-key",
                    }
                ],
                "generated_agent_key": "main_agent",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    _write_generated_agents(tmp_path)

    monkeypatch.setattr(cli_module, "_build_engine", lambda *args, **kwargs: _FakeEngine("回复OK"))

    outputs: list[str] = []
    exit_code = cli_module.main(
        ["--config", str(config_path), "--work-path", str(tmp_path), "--message", "你好", "--speaker", "测试用户"],
        print_func=outputs.append,
    )

    assert exit_code == 0
    assert any("[主助手] 回复OK" in item for item in outputs)
    transcript_path = tmp_path / "transcript.json"
    assert transcript_path.exists()
    payload = json.loads(transcript_path.read_text(encoding="utf-8"))
    assert payload[-1]["content"] == "回复OK"


def test_cli_reads_one_message_from_input_when_missing_message_arg(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "providers": [
                    {
                        "type": "openai-compatible",
                        "base_url": "https://api.openai.com",
                        "api_key": "test-key",
                    }
                ],
                "generated_agent_key": "main_agent",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    _write_generated_agents(tmp_path)

    monkeypatch.setattr(cli_module, "_build_engine", lambda *args, **kwargs: _FakeEngine("输入模式OK"))

    outputs: list[str] = []
    exit_code = cli_module.main(
        ["--config", str(config_path), "--work-path", str(tmp_path)],
        input_func=lambda _prompt: "来自输入",
        print_func=outputs.append,
    )

    assert exit_code == 0
    assert any("输入模式OK" in item for item in outputs)


def test_cli_attaches_default_channel_identity(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "providers": [
                    {
                        "type": "openai-compatible",
                        "base_url": "https://api.openai.com",
                        "api_key": "test-key",
                    }
                ],
                "generated_agent_key": "main_agent",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    _write_generated_agents(tmp_path)

    fake_engine = _FakeEngine("身份OK")
    monkeypatch.setattr(cli_module, "_build_engine", lambda *args, **kwargs: fake_engine)

    exit_code = cli_module.main(
        ["--config", str(config_path), "--work-path", str(tmp_path), "--message", "你好", "--speaker", "测试用户"],
        print_func=lambda _msg: None,
    )

    assert exit_code == 0
    assert fake_engine.last_turn is not None
    assert fake_engine.last_turn.channel == "cli"
    assert fake_engine.last_turn.channel_user_id == "测试用户"


def test_cli_exits_when_message_is_empty(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "providers": [
                    {
                        "type": "openai-compatible",
                        "base_url": "https://api.openai.com",
                        "api_key": "test-key",
                    }
                ],
                "generated_agent_key": "main_agent",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    _write_generated_agents(tmp_path)

    outputs: list[str] = []
    exit_code = cli_module.main(
        ["--config", str(config_path), "--work-path", str(tmp_path)],
        input_func=lambda _prompt: "",
        print_func=outputs.append,
    )

    assert exit_code == 0
    assert any("未输入消息，已退出。" in item for item in outputs)
