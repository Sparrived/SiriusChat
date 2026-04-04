from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Callable

from sirius_chat.api import (
    Agent,
    AgentPreset,
    AsyncRolePlayEngine,
    AutoRoutingProvider,
    JsonSessionStore,
    LLMProvider,
    Message,
    OrchestrationPolicy,
    Participant,
    ProviderRegistry,
    SessionConfig,
    SqliteSessionStore,
    Transcript,
    RolePlayAnswer,
    abuild_roleplay_prompt_from_answers_and_apply,
    create_session_config_from_selected_agent,
    create_multimodel_config,
    setup_multimodel_config,
    ensure_provider_platform_supported,
    generate_humanized_roleplay_questions,
    get_supported_provider_platforms,
    merge_provider_sources,
    register_provider_with_validation,
    run_provider_detection_flow,
)

InputFunc = Callable[[str], str]
PrintFunc = Callable[[str], None]
ProviderFactory = Callable[[dict[str, str]], LLMProvider]
REPO_ROOT = Path(__file__).resolve().parent
LAST_CONFIG_PATH_FILE = REPO_ROOT / ".last_config_path"
DEFAULT_WORK_PATH = REPO_ROOT / "data"
DEFAULT_CONFIG_PATH = REPO_ROOT / "examples" / "session.json"
PRIMARY_USER_FILE_NAME = "primary_user.json"
PERSISTED_SESSION_CONFIG_FILE_NAME = "session_config.persisted.json"
RESET_USER_COMMAND = "/reset-user"
PROVIDER_COMMAND_PREFIX = "/provider"


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sirius Chat 测试入口（库外业务编排）")
    parser.add_argument("--config", default="", help="会话 JSON 配置文件路径")
    parser.add_argument("--work-path", default="", help="持久化工作路径（缺失时默认 data 目录）")
    parser.add_argument("--output", default="", help="可选：输出 transcript JSON 文件路径")
    parser.add_argument(
        "--store",
        default="json",
        choices=["json", "sqlite"],
        help="会话持久化后端：json 或 sqlite（默认 json）",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="兼容参数：会话默认自动恢复，通常无需显式指定",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="禁用自动恢复，始终从新会话开始",
    )
    return parser


def _load_session_config(config_path: Path, work_path: Path) -> tuple[SessionConfig, dict[str, str], list[dict[str, object]]]:
    raw = json.loads(config_path.read_text(encoding="utf-8-sig"))

    if "agent" in raw or "global_system_prompt" in raw:
        raise ValueError("manual agent/global_system_prompt is not allowed; use generated_agent_key")
    generated_agent_key = str(raw.get("generated_agent_key", "")).strip()
    if not generated_agent_key:
        raise ValueError("generated_agent_key is required")

    session = create_session_config_from_selected_agent(
        work_path=work_path,
        agent_key=generated_agent_key,
        history_max_messages=int(raw.get("history_max_messages", 24)),
        history_max_chars=int(raw.get("history_max_chars", 6000)),
        max_recent_participant_messages=int(raw.get("max_recent_participant_messages", 5)),
        enable_auto_compression=bool(raw.get("enable_auto_compression", True)),
        orchestration=OrchestrationPolicy(
            enabled=bool(raw.get("orchestration", {}).get("enabled", False)),
            task_models={
                str(k): str(v)
                for k, v in dict(raw.get("orchestration", {}).get("task_models", {})).items()
                if str(k).strip() and str(v).strip()
            },
            task_budgets={
                str(k): int(v)
                for k, v in dict(raw.get("orchestration", {}).get("task_budgets", {})).items()
                if str(k).strip()
            },
            task_temperatures={
                str(k): float(v)
                for k, v in dict(raw.get("orchestration", {}).get("task_temperatures", {})).items()
                if str(k).strip()
            },
            task_max_tokens={
                str(k): int(v)
                for k, v in dict(raw.get("orchestration", {}).get("task_max_tokens", {})).items()
                if str(k).strip()
            },
            task_retries={
                str(k): int(v)
                for k, v in dict(raw.get("orchestration", {}).get("task_retries", {})).items()
                if str(k).strip()
            },
            max_multimodal_inputs_per_turn=int(raw.get("orchestration", {}).get("max_multimodal_inputs_per_turn", 4)),
            max_multimodal_value_length=int(raw.get("orchestration", {}).get("max_multimodal_value_length", 4096)),
        ),
    )

    provider_config = dict(raw.get("provider", {}))
    providers_config = list(raw.get("providers", []))

    if not provider_config and providers_config:
        first = providers_config[0]
        if isinstance(first, dict):
            provider_config = dict(first)

    provider_type = str(provider_config.get("type", "openai-compatible")).strip().lower()
    if provider_type == "siliconflow":
        base_url = str(provider_config.get("base_url", "https://api.siliconflow.cn"))
    elif provider_type in {"volcengine-ark", "ark"}:
        base_url = str(provider_config.get("base_url", "https://ark.cn-beijing.volces.com/api/v3"))
    else:
        base_url = str(provider_config.get("base_url", "https://api.openai.com"))
    return session, {
        "type": provider_type,
        "base_url": base_url,
        "api_key": str(provider_config.get("api_key", "")).strip(),
    }, providers_config


def _setup_multimodel_orchestration(
    session: SessionConfig,
    task_models: dict[str, str] | None = None,
    task_budgets: dict[str, int] | None = None,
    task_temperatures: dict[str, float] | None = None,
    task_max_tokens: dict[str, int] | None = None,
    task_retries: dict[str, int] | None = None,
) -> SessionConfig:
    """配置多模型协作编排。

    这个辅助函数展示了如何使用 setup_multimodel_config 来配置多个任务的模型。

    Args:
        session: 会话配置对象
        task_models: 任务模型映射（例如 {"memory_extract": "doubao-seed-2-0-lite-260215"}）
        task_budgets: 任务 token 预算（例如 {"memory_extract": 1200}）
        task_temperatures: 采样温度（例如 {"memory_extract": 0.1}）
        task_max_tokens: 最大 token 数（例如 {"memory_extract": 128}）
        task_retries: 重试次数（例如 {"memory_extract": 1}）

    Returns:
        配置完成的会话对象

    Example:
        # 在 main.py 中使用
        session, provider_config, providers = _load_session_config(config_path, work_path)
        session = _setup_multimodel_orchestration(
            session,
            task_models={
                "memory_extract": "doubao-seed-2-0-lite-260215",
                "event_extract": "doubao-seed-2-0-lite-260215",
            },
            task_budgets={
                "memory_extract": 1200,
                "event_extract": 1000,
            },
            task_retries={
                "memory_extract": 1,
                "event_extract": 1,
            },
        )
        # 或者直接使用 API：
        # from sirius_chat.api import setup_multimodel_config
        # setup_multimodel_config(
        #     session_config=session,
        #     task_models={"memory_extract": "model-1"},
        #     task_budgets={"memory_extract": 1200},
        # )
    """
    if not task_models:
        return session
    return setup_multimodel_config(
        session_config=session,
        task_models=task_models,
        task_budgets=task_budgets or {},
        task_temperatures=task_temperatures or {},
        task_max_tokens=task_max_tokens or {},
        task_retries=task_retries or {},
    )


def _load_provider_config_from_config_file(config_path: Path) -> tuple[dict[str, str], list[dict[str, object]]]:
    raw = json.loads(config_path.read_text(encoding="utf-8-sig"))
    provider_config = dict(raw.get("provider", {}))
    providers_config = list(raw.get("providers", []))

    if not provider_config and providers_config:
        first = providers_config[0]
        if isinstance(first, dict):
            provider_config = dict(first)

    provider_type = str(provider_config.get("type", "openai-compatible")).strip().lower()
    if provider_type == "siliconflow":
        base_url = str(provider_config.get("base_url", "https://api.siliconflow.cn"))
    elif provider_type in {"volcengine-ark", "ark"}:
        base_url = str(provider_config.get("base_url", "https://ark.cn-beijing.volces.com/api/v3"))
    else:
        base_url = str(provider_config.get("base_url", "https://api.openai.com"))

    return {
        "type": provider_type,
        "base_url": base_url,
        "api_key": str(provider_config.get("api_key", "")).strip(),
    }, providers_config


def _save_generated_agent_key_to_config(config_path: Path, generated_agent_key: str) -> None:
    raw = json.loads(config_path.read_text(encoding="utf-8-sig"))
    raw["generated_agent_key"] = generated_agent_key
    config_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_generated_agent_key_from_config_file(config_path: Path) -> str:
    raw = json.loads(config_path.read_text(encoding="utf-8-sig"))
    key = str(raw.get("generated_agent_key", "")).strip()
    if not key:
        raise ValueError("generated_agent_key is required")
    return key


def _bootstrap_first_generated_agent(
    *,
    config_path: Path,
    work_path: Path,
    provider_factory: ProviderFactory | None,
    input_func: InputFunc,
    print_func: PrintFunc,
) -> bool:
    print_func("检测到当前配置缺少可用的 generated agent，进入首次初始化向导。")
    agent_name = _prompt_non_empty(prompt="请输入首个 Agent 名称：", input_func=input_func, print_func=print_func)
    agent_alias = input_func("请输入 Agent 别名（可留空）：").strip()
    prompt_model = _prompt_non_empty(
        prompt="请输入用于生成提示词的模型名：",
        input_func=input_func,
        print_func=print_func,
    )
    agent_key_raw = input_func("请输入 generated_agent_key（留空默认同 Agent 名称）：").strip() or agent_name

    provider_config, providers_config = _load_provider_config_from_config_file(config_path)
    provider = _build_provider(provider_config, providers_config, work_path, provider_factory)

    questions = generate_humanized_roleplay_questions()
    answers: list[RolePlayAnswer] = []
    print_func("请依次回答角色问题（可简短作答）。")
    for index, question in enumerate(questions, start=1):
        answer_text = _prompt_non_empty(
            prompt=f"[{index}/{len(questions)}] {question.question}\n> ",
            input_func=input_func,
            print_func=print_func,
        )
        answers.append(
            RolePlayAnswer(
                question=question.question,
                answer=answer_text,
                perspective=question.perspective,
                details=question.details,
            )
        )

    temp_config = SessionConfig(
        work_path=work_path,
        preset=AgentPreset(
            agent=Agent(name=agent_name, persona="待生成", model=prompt_model),
            global_system_prompt="待生成",
        ),
    )

    asyncio.run(
        abuild_roleplay_prompt_from_answers_and_apply(
            provider,
            config=temp_config,
            model=prompt_model,
            answers=answers,
            persona_key=agent_key_raw,
            agent_name=agent_name,
            agent_alias=agent_alias,
            persist_generated_agent=True,
            select_after_save=True,
        )
    )

    _save_generated_agent_key_to_config(config_path, agent_key_raw)
    print_func(f"首个 generated agent 初始化完成，已写入 generated_agent_key={agent_key_raw}。")
    return True


def _run_framework_provider_detection(
    *,
    config_path: Path,
    work_path: Path,
    print_func: PrintFunc,
) -> None:
    registry_providers = ProviderRegistry(work_path).load()
    if registry_providers:
        merged = registry_providers
        print_func("Provider 检测来源：provider_keys.json（已注册 provider）")
    else:
        provider_config, providers_config = _load_provider_config_from_config_file(config_path)
        merged = merge_provider_sources(
            work_path=work_path,
            provider_config=provider_config,
            providers_config=providers_config,
        )
        print_func("Provider 检测来源：配置文件（provider/providers）")
    print_func("开始执行 Provider 检测流程：配置检查 -> 平台适配检查 -> 可用性检查")
    run_provider_detection_flow(providers=merged)
    print_func("Provider 检测流程通过。")


def _register_provider_interactively_for_bootstrap(
    *,
    work_path: Path,
    input_func: InputFunc,
    print_func: PrintFunc,
) -> None:
    platforms = get_supported_provider_platforms()
    print_func("请注册可用 provider（仅支持已适配平台）：")
    for provider_type, meta in platforms.items():
        print_func(f"- {provider_type}: default_base_url={meta['default_base_url']}")

    provider_type = _prompt_non_empty(prompt="请输入 provider 类型：", input_func=input_func, print_func=print_func)
    normalized_provider_type = ensure_provider_platform_supported(provider_type)
    api_key = _prompt_non_empty(prompt="请输入 API Key：", input_func=input_func, print_func=print_func)
    default_base_url = platforms[normalized_provider_type]["default_base_url"]
    base_url = input_func(f"请输入 base_url（留空使用默认 {default_base_url}）：").strip() or default_base_url
    healthcheck_model = _prompt_non_empty(
        prompt="请输入用于检测可用性的模型名（必填）：",
        input_func=input_func,
        print_func=print_func,
    )
    prefixes_raw = input_func("请输入 model 前缀（逗号分隔，可留空）：").strip()
    model_prefixes = _split_csv(prefixes_raw)

    registered_type = register_provider_with_validation(
        work_path=work_path,
        provider_type=provider_type,
        api_key=api_key,
        healthcheck_model=healthcheck_model,
        base_url=base_url,
        model_prefixes=model_prefixes,
    )
    print_func(f"provider 注册并检测通过：{registered_type}")


def _prompt_for_path(
    *,
    prompt_text: str,
    input_func: InputFunc,
    print_func: PrintFunc,
    must_exist: bool,
) -> Path:
    while True:
        raw = input_func(prompt_text).strip().strip('"')
        if not raw:
            print_func("请输入有效路径。")
            continue
        path = Path(raw)
        if must_exist and not path.exists():
            print_func(f"路径不存在：{path}")
            continue
        return path


def _resolve_runtime_paths(args: argparse.Namespace, input_func: InputFunc, print_func: PrintFunc) -> tuple[Path, Path]:
    if args.config:
        config_path = Path(args.config)
    elif LAST_CONFIG_PATH_FILE.exists():
        saved = LAST_CONFIG_PATH_FILE.read_text(encoding="utf-8").strip()
        saved_path = Path(saved)
        config_path = saved_path if saved and saved_path.exists() else DEFAULT_CONFIG_PATH
    elif DEFAULT_CONFIG_PATH.exists():
        config_path = DEFAULT_CONFIG_PATH
    else:
        config_path = _prompt_for_path(
            prompt_text="请输入会话配置文件路径：",
            input_func=input_func,
            print_func=print_func,
            must_exist=True,
        )

    if not config_path.exists():
        config_path = _prompt_for_path(
            prompt_text="请输入会话配置文件路径：",
            input_func=input_func,
            print_func=print_func,
            must_exist=True,
        )

    work_path = Path(args.work_path) if args.work_path else DEFAULT_WORK_PATH
    return config_path, work_path


def _parse_user_turn(raw_text: str) -> Message:
    return Message(role="user", speaker="用户", content=raw_text.strip())


def _build_provider(
    provider_config: dict[str, str],
    providers_config: list[dict[str, object]],
    work_path: Path,
    provider_factory: ProviderFactory | None,
) -> LLMProvider:
    if provider_factory is not None:
        return provider_factory(provider_config)
    merged_providers = merge_provider_sources(
        work_path=work_path,
        provider_config=provider_config,
        providers_config=providers_config,
    )
    return AutoRoutingProvider(merged_providers)


def _split_csv(raw_value: str) -> list[str]:
    return [item.strip() for item in raw_value.split(",") if item.strip()]


def _handle_provider_command(
    raw_text: str,
    *,
    provider_registry: ProviderRegistry,
    print_func: PrintFunc,
) -> tuple[bool, bool]:
    if not raw_text.startswith(PROVIDER_COMMAND_PREFIX):
        return False, False

    tokens = raw_text.split(maxsplit=6)
    if len(tokens) == 2 and tokens[1] == "platforms":
        platforms = get_supported_provider_platforms()
        print_func("当前支持的平台：")
        for provider_type, item in platforms.items():
            print_func(
                f"- {provider_type}: default_base_url={item['default_base_url']}, notes={item['notes']}"
            )
        return True, False

    if len(tokens) == 2 and tokens[1] == "list":
        providers = provider_registry.load()
        if not providers:
            print_func("当前没有已配置 provider。")
            return True, False
        for provider_type, config in providers.items():
            prefixes = ",".join(config.model_prefixes) if config.model_prefixes else "(未配置)"
            healthcheck_model = config.healthcheck_model or "(未配置)"
            print_func(
                f"- {provider_type}: base_url={config.base_url or '(默认)'}, "
                f"healthcheck_model={healthcheck_model}, model_prefixes={prefixes}"
            )
        return True, False

    if len(tokens) >= 5 and tokens[1] == "add":
        provider_type = tokens[2]
        api_key = tokens[3]
        healthcheck_model = tokens[4]
        base_url = tokens[5] if len(tokens) >= 6 else ""
        prefixes = _split_csv(tokens[6]) if len(tokens) >= 7 else []
        try:
            registered_type = register_provider_with_validation(
                work_path=provider_registry.path.parent,
                provider_type=provider_type,
                api_key=api_key,
                healthcheck_model=healthcheck_model,
                base_url=base_url,
                model_prefixes=prefixes,
            )
        except Exception as exc:
            print_func(f"provider 注册失败：{exc}")
            return True, False
        print_func(f"已添加并通过检测 provider: {registered_type}")
        return True, True

    if len(tokens) >= 3 and tokens[1] == "remove":
        provider_type = tokens[2]
        removed = provider_registry.remove(provider_type)
        if removed:
            print_func(f"已移除 provider: {provider_type}")
            return True, True
        print_func(f"未找到 provider: {provider_type}")
        return True, False

    print_func(
        "provider 命令格式：/provider platforms | /provider list | "
        "/provider add <type> <api_key> <healthcheck_model> [base_url] [model_prefixes_csv] | "
        "/provider remove <type>"
    )
    return True, False


def _write_transcript_output(transcript: Transcript, output_path: Path) -> None:
    payload = [
        {
            "role": message.role,
            "speaker": message.speaker,
            "content": message.content,
        }
        for message in transcript.messages
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _create_session_store(*, work_path: Path, store_kind: str):
    if store_kind == "sqlite":
        return SqliteSessionStore(work_path)
    return JsonSessionStore(work_path)


def _persist_last_config_path(config_path: Path, print_func: PrintFunc) -> None:
    try:
        LAST_CONFIG_PATH_FILE.write_text(str(config_path.resolve()), encoding="utf-8")
    except OSError as exc:
        print_func(f"写入最近配置路径失败：{exc}")


def _serialize_session_bundle(
    *,
    generated_agent_key: str,
    session_config: SessionConfig,
    provider_config: dict[str, str],
    providers_config: list[dict[str, object]],
) -> dict[str, object]:
    payload: dict[str, object] = {
        "generated_agent_key": generated_agent_key,
        "provider": {
            "type": provider_config.get("type", "openai-compatible"),
            "base_url": provider_config.get("base_url", ""),
            "api_key": provider_config.get("api_key", ""),
        },
        "providers": providers_config,
        "history_max_messages": session_config.history_max_messages,
        "history_max_chars": session_config.history_max_chars,
        "max_recent_participant_messages": session_config.max_recent_participant_messages,
        "enable_auto_compression": session_config.enable_auto_compression,
        "orchestration": {
            "enabled": session_config.orchestration.enabled,
            "task_models": session_config.orchestration.task_models,
            "task_budgets": session_config.orchestration.task_budgets,
            "task_temperatures": session_config.orchestration.task_temperatures,
            "task_max_tokens": session_config.orchestration.task_max_tokens,
            "task_retries": session_config.orchestration.task_retries,
            "max_multimodal_inputs_per_turn": session_config.orchestration.max_multimodal_inputs_per_turn,
            "max_multimodal_value_length": session_config.orchestration.max_multimodal_value_length,
        },
    }
    return payload


def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _load_or_persist_session_bundle(
    *,
    config_path: Path,
    work_path: Path,
    print_func: PrintFunc,
) -> tuple[SessionConfig, dict[str, str], list[dict[str, object]]]:
    persisted_path = work_path / PERSISTED_SESSION_CONFIG_FILE_NAME
    if persisted_path.exists():
        try:
            session_config, provider_config, providers_config = _load_session_config(persisted_path, work_path)
            print_func(f"已优先加载持久化 SessionConfig：{persisted_path}")
            return session_config, provider_config, providers_config
        except ValueError as exc:
            message = str(exc)
            if "manual agent/global_system_prompt is not allowed" not in message:
                raise
            print_func("检测到旧版持久化 SessionConfig 格式，正在自动重建为 generated_agent_key 结构。")

    session_config, provider_config, providers_config = _load_session_config(config_path, work_path)
    generated_agent_key = _load_generated_agent_key_from_config_file(config_path)
    try:
        _atomic_write_json(
            persisted_path,
            _serialize_session_bundle(
                generated_agent_key=generated_agent_key,
                session_config=session_config,
                provider_config=provider_config,
                providers_config=providers_config,
            ),
        )
    except OSError as exc:
        print_func(f"写入持久化 SessionConfig 失败：{exc}")
    return session_config, provider_config, providers_config


def _prompt_non_empty(*, prompt: str, input_func: InputFunc, print_func: PrintFunc) -> str:
    while True:
        value = input_func(prompt).strip()
        if value:
            return value
        print_func("该项不能为空，请重新输入。")


def _prompt_comma_values(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _persist_primary_user(
    *,
    work_path: Path,
    participant: Participant,
    transcript: Transcript | None,
    print_func: PrintFunc,
) -> None:
    profile_path = work_path / PRIMARY_USER_FILE_NAME
    payload: dict[str, object] = {
        "name": participant.name,
        "user_id": participant.user_id,
        "persona": participant.persona,
        "aliases": participant.aliases,
        "traits": participant.traits,
    }
    if transcript is not None and participant.user_id in transcript.user_memory.entries:
        runtime = transcript.user_memory.entries[participant.user_id].runtime
        payload["runtime"] = {
            "inferred_persona": runtime.inferred_persona,
            "inferred_traits": runtime.inferred_traits,
            "preference_tags": runtime.preference_tags,
            "recent_messages": runtime.recent_messages,
            "summary_notes": runtime.summary_notes,
            "last_seen_channel": runtime.last_seen_channel,
            "last_seen_uid": runtime.last_seen_uid,
        }
    try:
        _atomic_write_json(profile_path, payload)
    except OSError as exc:
        print_func(f"写入主用户档案失败：{exc}")


def _collect_primary_user_from_input(*, input_func: InputFunc, print_func: PrintFunc) -> Participant:
    name = _prompt_non_empty(prompt="请输入你的称呼：", input_func=input_func, print_func=print_func)
    user_id = input_func("请输入用户ID（留空则与称呼一致）：").strip() or name
    persona = input_func("请输入你的角色/偏好描述（可留空）：").strip()
    aliases_raw = input_func("请输入别名（逗号分隔，可留空）：").strip()
    return Participant(
        name=name,
        user_id=user_id,
        persona=persona,
        aliases=_prompt_comma_values(aliases_raw),
    )


def _bootstrap_primary_user(
    *,
    work_path: Path,
    input_func: InputFunc,
    print_func: PrintFunc,
) -> Participant:
    profile_path = work_path / PRIMARY_USER_FILE_NAME
    if profile_path.exists():
        payload = json.loads(profile_path.read_text(encoding="utf-8"))
        participant = Participant(
            name=str(payload.get("name", "用户")),
            user_id=str(payload.get("user_id", payload.get("name", "用户"))),
            persona=str(payload.get("persona", "")),
            aliases=list(payload.get("aliases", [])),
            traits=list(payload.get("traits", [])),
        )
        return participant

    print_func("首次启用：请先创建一个主用户档案。")
    participant = _collect_primary_user_from_input(
        input_func=input_func,
        print_func=print_func,
    )
    _persist_primary_user(work_path=work_path, participant=participant, transcript=None, print_func=print_func)
    print_func(f"已创建主用户档案：{participant.name}(id={participant.user_id})")
    return participant


def run_interactive_session(
    config: SessionConfig,
    primary_user: Participant,
    engine: AsyncRolePlayEngine,
    state_store,
    work_path: Path,
    provider_registry: ProviderRegistry,
    refresh_provider: Callable[[], LLMProvider],
    transcript: Transcript | None,
    *,
    input_func: InputFunc = input,
    print_func: PrintFunc = print,
) -> Transcript:
    active_transcript = transcript
    current_primary_user = primary_user
    primary_speaker = current_primary_user.name
    print_func(
        f"当前模式：与 {config.agent.name} 一对一对话（你是 {primary_speaker}）。"
        f"输入 {RESET_USER_COMMAND} 可重置用户，输入 {PROVIDER_COMMAND_PREFIX} 管理 provider，输入 exit/quit 结束。"
    )
    while True:
        raw_text = input_func("你> ").strip()
        if raw_text.lower() in {"exit", "quit", "q"}:
            break
        if raw_text == RESET_USER_COMMAND:
            print_func("开始重置主用户档案。")
            participant = _collect_primary_user_from_input(input_func=input_func, print_func=print_func)
            current_primary_user = participant
            primary_speaker = participant.name
            _persist_primary_user(work_path=work_path, participant=participant, transcript=None, print_func=print_func)
            if state_store.exists():
                state_store.path.unlink(missing_ok=True)
            active_transcript = None
            print_func(f"已重置主用户：{participant.name}(id={participant.user_id})，会话上下文已清空。")
            continue
        if not raw_text:
            continue

        provider_handled, provider_changed = _handle_provider_command(
            raw_text,
            provider_registry=provider_registry,
            print_func=print_func,
        )
        if provider_handled:
            if provider_changed:
                engine.provider = refresh_provider()
            continue

        human_turn = _parse_user_turn(raw_text)
        human_turn.speaker = primary_speaker
        try:
            active_transcript = asyncio.run(
                engine.run_live_session(
                    config=config,
                    human_turns=[human_turn],
                    transcript=active_transcript,
                )
            )
        except RuntimeError as exc:
            print_func(f"调用模型失败：{exc}")
            print_func("你可以检查网络或配置后继续输入重试。")
            continue
        latest_message = active_transcript.messages[-1]
        print_func(f"[{latest_message.speaker}] {latest_message.content}")
        state_store.save(active_transcript)
        _persist_primary_user(
            work_path=work_path,
            participant=current_primary_user,
            transcript=active_transcript,
            print_func=print_func,
        )

    return active_transcript or Transcript()


def main(
    argv: list[str] | None = None,
    *,
    input_func: InputFunc = input,
    print_func: PrintFunc = print,
    provider_factory: ProviderFactory | None = None,
) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    config_path, work_path = _resolve_runtime_paths(args, input_func, print_func)
    work_path.mkdir(parents=True, exist_ok=True)

    _persist_last_config_path(config_path, print_func)

    try:
        session_config, provider_config, providers_config = _load_or_persist_session_bundle(
            config_path=config_path,
            work_path=work_path,
            print_func=print_func,
        )
    except ValueError as exc:
        print_func(f"加载 SessionConfig 失败：{exc}")
        should_bootstrap = input_func("是否现在初始化首个 generated agent？[Y/n] ").strip().lower()
        if should_bootstrap not in {"", "y", "yes"}:
            print_func("已取消初始化。")
            return 1
        try:
            _run_framework_provider_detection(
                config_path=config_path,
                work_path=work_path,
                print_func=print_func,
            )
        except Exception as provider_exc:
            print_func(f"Provider 检测流程失败：{provider_exc}")
            should_register = input_func("是否现在注册 provider 并重新检测？[Y/n] ").strip().lower()
            if should_register not in {"", "y", "yes"}:
                return 1
            try:
                _register_provider_interactively_for_bootstrap(
                    work_path=work_path,
                    input_func=input_func,
                    print_func=print_func,
                )
                _run_framework_provider_detection(
                    config_path=config_path,
                    work_path=work_path,
                    print_func=print_func,
                )
            except Exception as register_exc:
                print_func(f"Provider 注册或检测失败：{register_exc}")
                return 1

        try:
            _bootstrap_first_generated_agent(
                config_path=config_path,
                work_path=work_path,
                provider_factory=provider_factory,
                input_func=input_func,
                print_func=print_func,
            )
            session_config, provider_config, providers_config = _load_or_persist_session_bundle(
                config_path=config_path,
                work_path=work_path,
                print_func=print_func,
            )
        except Exception as bootstrap_exc:
            print_func(f"初始化首个 generated agent 失败：{bootstrap_exc}")
            return 1
    primary_user = _bootstrap_primary_user(
        work_path=work_path,
        input_func=input_func,
        print_func=print_func,
    )
    provider_registry = ProviderRegistry(work_path)
    provider = _build_provider(provider_config, providers_config, work_path, provider_factory)
    engine = AsyncRolePlayEngine(provider=provider)
    state_store = _create_session_store(work_path=work_path, store_kind=args.store)
    should_resume = (not args.no_resume) and state_store.exists()
    transcript = state_store.load() if should_resume else None

    transcript = run_interactive_session(
        session_config,
        primary_user,
        engine,
        state_store,
        work_path,
        provider_registry,
        lambda: _build_provider(provider_config, providers_config, work_path, provider_factory),
        transcript,
        input_func=input_func,
        print_func=print_func,
    )

    output_path = Path(args.output) if args.output else work_path / "transcript.json"
    if not output_path.is_absolute():
        output_path = work_path / output_path
    _write_transcript_output(transcript, output_path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

