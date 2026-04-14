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
    SessionStoreFactory,
)
from sirius_chat.cli_diagnostics import (
    EnvironmentDiagnostics,
    generate_default_config,
    run_preflight_check,
)
from sirius_chat.config.jsonc import load_json_document, write_session_config_jsonc
from sirius_chat.config.helpers import build_orchestration_policy_from_dict
from sirius_chat.logging_config import configure_logging, setup_log_archival, get_logger
from sirius_chat.workspace.layout import WorkspaceLayout
from sirius_chat.workspace.runtime import WorkspaceRuntime

InputFunc = Callable[[str], str]
PrintFunc = Callable[[str], None]
ProviderFactory = Callable[[dict[str, object]], LLMProvider]
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
    parser.add_argument("--config", default="", help="会话 JSON/JSONC 配置文件路径")
    parser.add_argument("--config-root", default="", help="配置持久化目录（默认与 work-path 相同）")
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
    parser.add_argument(
        "--init-config",
        type=str,
        default="",
        help="生成默认配置文件（指定输出路径）",
    )
    parser.add_argument(
        "--check-config",
        type=str,
        default="",
        help="检查配置文件（指定配置路径）",
    )
    return parser


def _load_session_config(
    config_path: Path,
    work_path: Path,
    *,
    config_root: Path | None = None,
) -> tuple[SessionConfig, list[dict[str, object]]]:
    resolved_config_root = config_root or work_path
    raw = load_json_document(config_path)
    if not isinstance(raw, dict):
        raise ValueError("配置文件顶层必须是对象")

    if "agent" in raw or "global_system_prompt" in raw:
        raise ValueError("不允许手动指定 agent/global_system_prompt；请使用 generated_agent_key")
    generated_agent_key = str(raw.get("generated_agent_key", "")).strip()
    if not generated_agent_key:
        raise ValueError("必需提供 generated_agent_key")

    session = create_session_config_from_selected_agent(
        work_path=resolved_config_root,
        data_path=work_path,
        agent_key=generated_agent_key,
        history_max_messages=int(raw.get("history_max_messages", 24)),
        history_max_chars=int(raw.get("history_max_chars", 6000)),
        max_recent_participant_messages=int(raw.get("max_recent_participant_messages", 5)),
        enable_auto_compression=bool(raw.get("enable_auto_compression", True)),
    )

    orchestration = build_orchestration_policy_from_dict(
        raw.get("orchestration", {}),
        agent_model=session.agent.model,
        return_none_if_empty=True,
    )
    if orchestration is not None:
        session.orchestration = orchestration
        session.orchestration.validate()

    # 加载 providers 列表（必需）
    providers_config = list(raw.get("providers", []))
    if not providers_config:
        raise ValueError("SessionConfig 必需包含 providers 字段（list format）")

    return session, providers_config


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


def _load_providers_config_from_config_file(config_path: Path) -> list[dict[str, object]]:
    """加载 Session JSON 中的 providers 配置。
    
    providers 字段为必需的 list format。
    """
    raw = load_json_document(config_path)
    if not isinstance(raw, dict):
        raise ValueError("配置文件顶层必须是对象")
    
    # 加载 providers 字段（必需）
    providers_config = list(raw.get("providers", []))
    if not providers_config:
        raise ValueError("SessionConfig 必需包含 providers 字段（list format）")
    
    return providers_config


def _save_generated_agent_key_to_config(config_path: Path, generated_agent_key: str) -> None:
    raw = load_json_document(config_path)
    if not isinstance(raw, dict):
        raise ValueError("配置文件顶层必须是对象")
    raw["generated_agent_key"] = generated_agent_key
    write_session_config_jsonc(config_path, raw)


def _load_generated_agent_key_from_config_file(config_path: Path) -> str:
    raw = load_json_document(config_path)
    if not isinstance(raw, dict):
        raise ValueError("配置文件顶层必须是对象")
    key = str(raw.get("generated_agent_key", "")).strip()
    if not key:
        raise ValueError("必需提供 generated_agent_key")
    return key


def _bootstrap_first_generated_agent(
    *,
    config_path: Path,
    work_path: Path,
    config_root: Path | None = None,
    provider_factory: ProviderFactory | None,
    input_func: InputFunc,
    print_func: PrintFunc,
) -> bool:
    resolved_config_root = config_root or work_path
    print_func("检测到当前配置缺少可用的 generated agent，进入首次初始化向导。")
    agent_name = _prompt_non_empty(prompt="请输入首个 Agent 名称：", input_func=input_func, print_func=print_func)
    agent_alias = input_func("请输入 Agent 别名（可留空）：").strip()
    prompt_model = _prompt_non_empty(
        prompt="请输入用于生成提示词的模型名：",
        input_func=input_func,
        print_func=print_func,
    )
    agent_key_raw = input_func("请输入 generated_agent_key（留空默认同 Agent 名称）：").strip() or agent_name

    providers_config = _load_providers_config_from_config_file(config_path)
    provider = _build_provider(providers_config, resolved_config_root, provider_factory)

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
        work_path=resolved_config_root,
        data_path=work_path,
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
        providers_config = _load_providers_config_from_config_file(config_path)
        merged = merge_provider_sources(
            work_path=work_path,
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

    registered_type = register_provider_with_validation(
        work_path=work_path,
        provider_type=provider_type,
        api_key=api_key,
        healthcheck_model=healthcheck_model,
        base_url=base_url,
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


def _resolve_runtime_paths(
    args: argparse.Namespace,
    input_func: InputFunc,
    print_func: PrintFunc,
) -> tuple[Path, Path, Path]:
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
    config_root = Path(args.config_root) if args.config_root else work_path
    return config_path, config_root, work_path


def _parse_user_turn(raw_text: str) -> Message:
    return Message(role="user", speaker="用户", content=raw_text.strip())


def _build_provider(
    providers_config: list[dict[str, object]],
    work_path: Path,
    provider_factory: ProviderFactory | None,
) -> LLMProvider:
    if provider_factory is not None:
        # For custom factory, use first provider if available
        if providers_config and isinstance(providers_config[0], dict):
            return provider_factory(providers_config[0])
        return provider_factory({})
    merged_providers = merge_provider_sources(
        work_path=work_path,
        providers_config=providers_config,
    )
    return AutoRoutingProvider(merged_providers)


def _build_runtime(
    *,
    work_path: Path,
    provider: LLMProvider | None,
    config_root: Path | None = None,
    store_kind: str,
) -> WorkspaceRuntime:
    return WorkspaceRuntime.open(
        work_path,
        config_path=config_root,
        provider=provider,
        store_factory=SessionStoreFactory(backend=store_kind),
    )


def _handle_provider_command(
    raw_text: str,
    *,
    provider_registry: ProviderRegistry,
    print_func: PrintFunc,
) -> tuple[bool, bool]:
    if not raw_text.startswith(PROVIDER_COMMAND_PREFIX):
        return False, False

    tokens = raw_text.split(maxsplit=5)
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
            healthcheck_model = config.healthcheck_model or "(未配置)"
            print_func(
                f"- {provider_type}: base_url={config.base_url or '(默认)'}, "
                f"healthcheck_model={healthcheck_model}"
            )
        return True, False

    if len(tokens) >= 5 and tokens[1] == "add":
        provider_type = tokens[2]
        api_key = tokens[3]
        healthcheck_model = tokens[4]
        base_url = tokens[5] if len(tokens) >= 6 else ""
        try:
            registered_type = register_provider_with_validation(
                work_path=provider_registry.work_path,
                provider_type=provider_type,
                api_key=api_key,
                healthcheck_model=healthcheck_model,
                base_url=base_url,
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
        "/provider add <type> <api_key> <healthcheck_model> [base_url] | "
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
    providers_config: list[dict[str, object]],
) -> dict[str, object]:
    payload: dict[str, object] = {
        "generated_agent_key": generated_agent_key,
        "providers": providers_config,
        "history_max_messages": session_config.history_max_messages,
        "history_max_chars": session_config.history_max_chars,
        "max_recent_participant_messages": session_config.max_recent_participant_messages,
        "enable_auto_compression": session_config.enable_auto_compression,
        "orchestration": {
            "unified_model": session_config.orchestration.unified_model,
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
    config_root: Path | None = None,
    print_func: PrintFunc,
) -> tuple[SessionConfig, list[dict[str, object]]]:
    resolved_config_root = config_root or work_path
    persisted_path = WorkspaceLayout(work_path, config_path=resolved_config_root).persisted_session_bundle_path()
    if persisted_path.exists():
        session_config, providers_config = _load_session_config(
            persisted_path,
            work_path,
            config_root=resolved_config_root,
        )
        print_func(f"已加载持久化 SessionConfig：{persisted_path}")
        return session_config, providers_config

    session_config, providers_config = _load_session_config(
        config_path,
        work_path,
        config_root=resolved_config_root,
    )
    generated_agent_key = _load_generated_agent_key_from_config_file(config_path)
    try:
        _atomic_write_json(
            persisted_path,
            _serialize_session_bundle(
                generated_agent_key=generated_agent_key,
                session_config=session_config,
                providers_config=providers_config,
            ),
        )
    except OSError as exc:
        print_func(f"写入持久化 SessionConfig 失败：{exc}")
    return session_config, providers_config


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
    runtime: WorkspaceRuntime,
    work_path: Path,
    provider_registry: ProviderRegistry,
    refresh_provider: Callable[[], LLMProvider | None],
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
            asyncio.run(runtime.clear_session(config.session_id))
            asyncio.run(runtime.set_primary_user(config.session_id, participant))
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
                runtime.set_provider(refresh_provider())
            continue

        human_turn = _parse_user_turn(raw_text)
        human_turn.speaker = primary_speaker
        try:
            active_transcript = asyncio.run(
                runtime.run_live_message(
                    session_id=config.session_id,
                    turn=human_turn,
                    user_profile=current_primary_user.as_user_profile(),
                )
            )
        except RuntimeError as exc:
            print_func(f"调用模型失败：{exc}")
            print_func("你可以检查网络或配置后继续输入重试。")
            continue
        latest_message = active_transcript.messages[-1]
        print_func(f"[{latest_message.speaker}] {latest_message.content}")
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

    # 配置日志系统 - 同时输出到控制台和日志文件
    log_dir = REPO_ROOT / "logs"
    
    # 先清理旧日志（在处理器创建前）
    setup_log_archival(log_dir / "sirius_chat.log")
    
    # 配置日志处理器（包括模型调用的专用日志）
    configure_logging(
        level="INFO",
        format_type="console",
        log_file=log_dir / "sirius_chat.log",
        enable_file_rotation=True,  # 每日轮换，保留7个备份
        model_calls_log_file=log_dir / "model_calls.log",  # 模型调用的专用日志
    )
    logger = get_logger(__name__)

    # 处理特殊命令：--init-config
    if args.init_config:
        try:
            output_path = Path(args.init_config)
            generate_default_config(output_path)
            print_func(f"默认配置文件已生成: {output_path}")
            return 0
        except Exception as e:
            print_func(f"生成配置文件失败: {e}")
            logger.error(f"配置生成失败：{e}", exc_info=True)
            return 1

    # 处理特殊命令：--check-config
    if args.check_config:
        try:
            config_path = Path(args.check_config)
            work_path = Path(args.work_path or REPO_ROOT / "data")
            ran = run_preflight_check(config_path, work_path, print_func=print_func)
            return 0 if ran else 1
        except Exception as e:
            print_func(f"配置检查失败: {e}")
            logger.error(f"配置检查失败：{e}", exc_info=True)
            return 1

    try:
        resolved_paths = tuple(_resolve_runtime_paths(args, input_func, print_func))
        if len(resolved_paths) == 3:
            config_path, config_root, work_path = resolved_paths
        else:
            config_path, work_path = resolved_paths
            config_root = work_path
    except Exception as e:
        logger.error(f"路径解析失败：{e}", exc_info=True)
        print_func(f"路径解析失败: {e}")
        return 1
    
    work_path.mkdir(parents=True, exist_ok=True)
    config_root.mkdir(parents=True, exist_ok=True)

    _persist_last_config_path(config_path, print_func)

    try:
        session_config, providers_config = _load_or_persist_session_bundle(
            config_path=config_path,
            work_path=work_path,
            config_root=config_root,
            print_func=print_func,
        )
    except ValueError as exc:
        logger.error(f"加载 SessionConfig 失败：{exc}", exc_info=True)
        print_func(f"加载 SessionConfig 失败：{exc}")
        should_bootstrap = input_func("是否现在初始化首个 generated agent？[Y/n] ").strip().lower()
        if should_bootstrap not in {"", "y", "yes"}:
            print_func("已取消初始化。")
            return 1
        try:
            _run_framework_provider_detection(
                config_path=config_path,
                work_path=config_root,
                print_func=print_func,
            )
        except Exception as provider_exc:
            logger.error(f"提供商检测失败：{provider_exc}", exc_info=True)
            print_func(f"Provider 检测流程失败：{provider_exc}")
            should_register = input_func("是否现在注册 provider 并重新检测？[Y/n] ").strip().lower()
            if should_register not in {"", "y", "yes"}:
                return 1
            try:
                _register_provider_interactively_for_bootstrap(
                    work_path=config_root,
                    input_func=input_func,
                    print_func=print_func,
                )
                _run_framework_provider_detection(
                    config_path=config_path,
                    work_path=config_root,
                    print_func=print_func,
                )
            except Exception as register_exc:
                logger.error(f"提供商注册失败：{register_exc}", exc_info=True)
                print_func(f"Provider 注册或检测失败：{register_exc}")
                return 1

        try:
            _bootstrap_first_generated_agent(
                config_path=config_path,
                work_path=work_path,
                config_root=config_root,
                provider_factory=provider_factory,
                input_func=input_func,
                print_func=print_func,
            )
            session_config, providers_config = _load_or_persist_session_bundle(
                config_path=config_path,
                work_path=work_path,
                config_root=config_root,
                print_func=print_func,
            )
        except Exception as bootstrap_exc:
            logger.error(f"启动失败：{bootstrap_exc}", exc_info=True)
            print_func(f"初始化首个 generated agent 失败：{bootstrap_exc}")
            return 1
    except Exception as e:
        logger.error(f"会话加载失败：{e}", exc_info=True)
        print_func(f"加载会话配置时发生未预期的错误：{e}")
        return 1
    
    try:
        primary_user = _bootstrap_primary_user(
            work_path=work_path,
            input_func=input_func,
            print_func=print_func,
        )
        provider_registry = ProviderRegistry(config_root)
        provider = _build_provider(providers_config, config_root, provider_factory) if provider_factory is not None else None
        runtime = _build_runtime(
            work_path=work_path,
            config_root=config_root,
            provider=provider,
            store_kind=args.store,
        )
        if args.no_resume:
            asyncio.run(runtime.clear_session(session_config.session_id))
            transcript = None
        else:
            transcript = asyncio.run(runtime.get_transcript(session_config.session_id))
        asyncio.run(runtime.set_primary_user(session_config.session_id, primary_user))

        transcript = run_interactive_session(
            session_config,
            primary_user,
            runtime,
            work_path,
            provider_registry,
            lambda: _build_provider(providers_config, config_root, provider_factory) if provider_factory is not None else None,
            transcript,
            input_func=input_func,
            print_func=print_func,
        )

        output_path = Path(args.output) if args.output else Path("transcript.json")
        if not output_path.is_absolute():
            output_path = work_path / output_path
        _write_transcript_output(transcript, output_path)

        return 0
    except KeyboardInterrupt:
        print_func("\n会话已中断。")
        return 0
    except Exception as e:
        logger.error(f"会话中遇到预查误误：{e}", exc_info=True)
        print_func(f"会话执行过程中发生错误：{e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

