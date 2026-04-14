from pathlib import Path
import json

import pytest

from sirius_chat.cli import _load_session_config
from sirius_chat.cli_diagnostics import generate_default_config
from sirius_chat.config.jsonc import load_json_document


def _write_generated_agents(work_path: Path, key: str = "main_agent") -> None:
    work_path.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
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
    )
    (work_path / "generated_agents.json").write_text(payload, encoding="utf-8")
    roleplay_dir = work_path / "roleplay"
    roleplay_dir.mkdir(parents=True, exist_ok=True)
    (roleplay_dir / "generated_agents.json").write_text(payload, encoding="utf-8")


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
    assert session.data_path == work_path
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
    assert session.data_path == work_path
    assert len(providers) == 1
    assert providers[0]["type"] == "siliconflow"
    assert providers[0]["api_key"] == "sf-test-key"


def test_load_session_config_supports_aliyun_bailian_defaults(tmp_path) -> None:
    config_path = tmp_path / "aliyun_bailian.json"
    config_path.write_text(
        json.dumps(
            {
                "providers": [
                    {
                        "type": "aliyun-bailian",
                        "api_key": "dashscope-test-key",
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
    assert session.data_path == work_path
    assert len(providers) == 1
    assert providers[0]["type"] == "aliyun-bailian"
    assert providers[0]["api_key"] == "dashscope-test-key"


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


def test_load_session_config_supports_separate_config_root_and_work_path(tmp_path) -> None:
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

    config_root = tmp_path / "config"
    work_path = tmp_path / "runtime"
    _write_generated_agents(config_root)

    session, providers = _load_session_config(
        config_path,
        work_path,
        config_root=config_root,
    )

    assert session.work_path == config_root
    assert session.data_path == work_path
    assert session.agent.name == "主助手"
    assert providers[0]["type"] == "openai-compatible"


@pytest.mark.parametrize(
    "example_name",
    [
        "session.json",
        "session.deepseek.json",
        "session.siliconflow.json",
        "session.aliyun_bailian.json",
        "session_multimodel.json",
        "session_prompt_splitting.json",
    ],
)
def test_load_session_config_supports_repository_example_configs(tmp_path, example_name: str) -> None:
    example_path = Path(__file__).resolve().parents[1] / "examples" / example_name
    example_payload = load_json_document(example_path)

    config_root = tmp_path / "config"
    runtime_root = tmp_path / "runtime"
    _write_generated_agents(config_root, key=example_payload["generated_agent_key"])

    session, providers = _load_session_config(
        example_path,
        runtime_root,
        config_root=config_root,
    )

    assert session.work_path == config_root
    assert session.data_path == runtime_root
    assert session.agent.name == "主助手"
    assert providers
    assert providers[0]["type"] == example_payload["providers"][0]["type"]


def test_load_session_config_accepts_jsonc_comments(tmp_path) -> None:
    config_path = tmp_path / "session.json"
    config_path.write_text(
        '{\n  // 当前启用的 agent\n  "generated_agent_key": "main_agent",\n  "providers": [\n    {\n      "type": "openai-compatible",\n      "api_key": "test-key"\n    }\n  ]\n}\n',
        encoding="utf-8",
    )

    work_path = tmp_path / "work"
    _write_generated_agents(work_path)

    session, providers = _load_session_config(config_path, work_path)

    assert session.work_path == work_path
    assert session.data_path == work_path
    assert session.agent.name == "主助手"
    assert providers[0]["api_key"] == "test-key"


def test_load_session_config_accepts_generated_default_config_template(tmp_path) -> None:
    config_path = tmp_path / "session.jsonc"
    generate_default_config(config_path)

    raw_text = config_path.read_text(encoding="utf-8")
    raw_text = raw_text.replace('"generated_agent_key": ""', '"generated_agent_key": "main_agent"')
    raw_text = raw_text.replace('"api_key": "your-api-key-here"', '"api_key": "test-key"')
    config_path.write_text(raw_text, encoding="utf-8")

    config_root = tmp_path / "config"
    runtime_root = tmp_path / "runtime"
    _write_generated_agents(config_root)

    session, providers = _load_session_config(
        config_path,
        runtime_root,
        config_root=config_root,
    )

    assert session.work_path == config_root
    assert session.data_path == runtime_root
    assert session.agent.name == "主助手"
    assert providers[0]["type"] == "openai-compatible"
    assert providers[0]["api_key"] == "test-key"
