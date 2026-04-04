from pathlib import Path
import json

from sirius_chat.cli import _load_session_config


def _write_generated_agents(work_path: Path, key: str = "main_agent") -> None:
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


def test_load_session_config_parses_required_fields(tmp_path) -> None:
    config_path = tmp_path / "session.json"
    config_path.write_text(
        json.dumps(
            {
                "provider": {
                    "type": "openai-compatible",
                    "api_key": "test-key",
                },
                "generated_agent_key": "main_agent",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    work_path = tmp_path / "work"
    _write_generated_agents(work_path)

    session, provider, providers = _load_session_config(config_path, work_path)

    assert session.work_path == work_path
    assert session.agent.name == "主助手"
    assert provider["base_url"].startswith("https://")
    assert providers == []


def test_load_session_config_supports_siliconflow_defaults(tmp_path) -> None:
    config_path = tmp_path / "siliconflow.json"
    config_path.write_text(
        json.dumps(
            {
                "provider": {
                    "type": "siliconflow",
                    "api_key": "sf-test-key",
                },
                "generated_agent_key": "main_agent",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    work_path = tmp_path / "work"
    _write_generated_agents(work_path)
    session, provider, providers = _load_session_config(config_path, work_path)

    assert session.work_path == work_path
    assert provider["type"] == "siliconflow"
    assert provider["base_url"] == "https://api.siliconflow.cn"
    assert providers == []


def test_load_session_config_accepts_providers_list_without_single_provider(tmp_path) -> None:
    config_path = tmp_path / "providers.json"
    config_path.write_text(
        json.dumps(
            {
                "providers": [
                    {
                        "type": "siliconflow",
                        "api_key": "sf-test-key",
                        "model_prefixes": ["Pro/", "Qwen/"],
                    },
                    {
                        "type": "openai-compatible",
                        "api_key": "openai-test-key",
                    },
                ],
                "generated_agent_key": "main_agent",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    work_path = tmp_path / "work"
    _write_generated_agents(work_path)
    _, provider, providers = _load_session_config(config_path, work_path)

    assert provider["type"] == "siliconflow"
    assert provider["api_key"] == "sf-test-key"
    assert len(providers) == 2


def test_load_session_config_parses_orchestration_policy(tmp_path) -> None:
    config_path = tmp_path / "orchestration.json"
    config_path.write_text(
        json.dumps(
            {
                "provider": {
                    "type": "siliconflow",
                    "api_key": "sf-key",
                },
                "generated_agent_key": "main_agent",
                "orchestration": {
                    "enabled": True,
                    "task_models": {"memory_extract": "doubao-seed-2-0-lite-260215"},
                    "task_budgets": {"memory_extract": 1200},
                    "task_temperatures": {"memory_extract": 0.1},
                    "task_max_tokens": {"memory_extract": 128},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    work_path = tmp_path / "work"
    _write_generated_agents(work_path)
    session, _, _ = _load_session_config(config_path, work_path)

    assert session.orchestration.enabled is True
    assert session.orchestration.task_models["memory_extract"] == "doubao-seed-2-0-lite-260215"
    assert session.orchestration.task_budgets["memory_extract"] == 1200
