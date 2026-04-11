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
                "providers": [
                    {
                        "type": "openai-compatible",
                        "api_key": "test-key",
                    }
                ],
                "generated_agent_key": "main_agent",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    work_path = tmp_path / "work"
    _write_generated_agents(work_path)

    session, providers = _load_session_config(config_path, work_path)

    assert session.work_path == work_path
    assert session.agent.name == "主助手"
    assert len(providers) == 1
    assert providers[0]["type"] == "openai-compatible"
    assert providers[0]["api_key"] == "test-key"


def test_load_session_config_supports_siliconflow_defaults(tmp_path) -> None:
    config_path = tmp_path / "siliconflow.json"
    config_path.write_text(
        json.dumps(
            {
                "providers": [
                    {
                        "type": "siliconflow",
                        "api_key": "sf-test-key",
                    }
                ],
                "generated_agent_key": "main_agent",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    work_path = tmp_path / "work"
    _write_generated_agents(work_path)
    session, providers = _load_session_config(config_path, work_path)

    assert session.work_path == work_path
    assert len(providers) == 1
    assert providers[0]["type"] == "siliconflow"
    assert providers[0]["api_key"] == "sf-test-key"


def test_load_session_config_accepts_providers_list_without_single_provider(tmp_path) -> None:
    config_path = tmp_path / "providers.json"
    config_path.write_text(
        json.dumps(
            {
                "providers": [
                    {
                        "type": "siliconflow",
                        "api_key": "sf-test-key",
                        "healthcheck_model": "Pro/zai-org/GLM-4.7",
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
    session, providers = _load_session_config(config_path, work_path)

    assert len(providers) == 2
    assert providers[0]["type"] == "siliconflow"
    assert providers[0]["api_key"] == "sf-test-key"
    assert providers[1]["type"] == "openai-compatible"
    assert providers[1]["api_key"] == "openai-test-key"


def test_load_session_config_parses_orchestration_policy(tmp_path) -> None:
    config_path = tmp_path / "orchestration.json"
    config_path.write_text(
        json.dumps(
            {
                "providers": [
                    {
                        "type": "siliconflow",
                        "api_key": "sf-key",
                    }
                ],
                "generated_agent_key": "main_agent",
                "orchestration": {
                    "task_enabled": {
                        "memory_extract": True,
                        "event_extract": True,
                        "intent_analysis": False
                    },
                    "task_models": {
                        "memory_extract": "doubao-seed-2-0-lite-260215",
                        "intent_analysis": "gpt-4o-mini"
                    },
                    "task_budgets": {"memory_extract": 1200},
                    "task_temperatures": {"memory_extract": 0.1},
                    "task_max_tokens": {"memory_extract": 128},
                    "session_reply_mode": "auto",
                    "message_debounce_seconds": 0.0
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    work_path = tmp_path / "work"
    _write_generated_agents(work_path)
    session, _ = _load_session_config(config_path, work_path)

    # 验证多模型协同已配置
    assert session.orchestration.unified_model or session.orchestration.task_models
    assert session.orchestration.task_models.get("memory_extract") == "doubao-seed-2-0-lite-260215"
    assert session.orchestration.task_models.get("intent_analysis") == "gpt-4o-mini"
    assert session.orchestration.task_enabled["intent_analysis"] is False
    assert session.orchestration.task_budgets["memory_extract"] == 1200
    assert session.orchestration.session_reply_mode == "auto"
    assert session.orchestration.message_debounce_seconds == 0.0
