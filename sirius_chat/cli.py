from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Callable

from sirius_chat.api import (
    AsyncRolePlayEngine,
    AutoRoutingProvider,
    Message,
    OrchestrationPolicy,
    SessionConfig,
    create_session_config_from_selected_agent,
    create_async_engine,
    merge_provider_sources,
)

InputFunc = Callable[[str], str]
PrintFunc = Callable[[str], None]
DEFAULT_CLI_CHANNEL = "cli"


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sirius Chat CLI（库内薄封装）")
    parser.add_argument("--config", default="examples/session.json", help="会话 JSON 配置文件路径")
    parser.add_argument("--work-path", default="", help="运行工作目录（默认当前目录下 data）")
    parser.add_argument("--output", default="", help="输出 transcript JSON 文件路径（默认 <work_path>/transcript.json）")
    parser.add_argument("--message", default="", help="用户输入消息；不提供则交互输入一条")
    parser.add_argument("--speaker", default="用户", help="用户说话人名称")
    parser.add_argument("--channel", default=DEFAULT_CLI_CHANNEL, help="消息渠道标识（默认 cli）")
    parser.add_argument("--channel-user-id", default="", help="渠道内用户ID（默认使用 speaker）")
    return parser


def _load_session_config(config_path: Path, work_path: Path) -> tuple[SessionConfig, dict[str, str], list[dict[str, object]]]:
    raw = json.loads(config_path.read_text(encoding="utf-8-sig"))

    if "agent" in raw or "global_system_prompt" in raw:
        raise ValueError("\u4e0d\u5141\u8bb8\u624b\u52a8\u6307\u5b9a agent/global_system_prompt\uff1b\u8bf7\u4f7f\u7528 generated_agent_key")
    generated_agent_key = str(raw.get("generated_agent_key", "")).strip()
    if not generated_agent_key:
        raise ValueError("\u5fc5\u9700\u63d0\u4f9b generated_agent_key")

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


def _build_engine(work_path: Path, provider_config: dict[str, str], providers_config: list[dict[str, object]]) -> AsyncRolePlayEngine:
    merged = merge_provider_sources(
        work_path=work_path,
        provider_config=provider_config,
        providers_config=providers_config,
    )
    provider = AutoRoutingProvider(merged)
    return create_async_engine(provider)


def _write_transcript_output(messages: list[Message], output_path: Path) -> None:
    payload = [
        {
            "role": message.role,
            "speaker": message.speaker,
            "content": message.content,
        }
        for message in messages
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main(
    argv: list[str] | None = None,
    *,
    input_func: InputFunc = input,
    print_func: PrintFunc = print,
) -> int:
    args = _build_arg_parser().parse_args(argv)

    config_path = Path(args.config)
    work_path = Path(args.work_path) if args.work_path else (Path.cwd() / "data")
    work_path.mkdir(parents=True, exist_ok=True)

    try:
        session_config, provider_config, providers_config = _load_session_config(config_path, work_path)
    except ValueError as exc:
        print_func(f"加载 SessionConfig 失败：{exc}")
        print_func("请先通过提示词生成器生成并保存 agent 资产（generated_agents.json），再启动会话。")
        return 1
    engine = _build_engine(work_path, provider_config, providers_config)

    user_text = args.message.strip() if args.message else input_func("你> ").strip()
    if not user_text:
        print_func("未输入消息，已退出。")
        return 0

    transcript = asyncio.run(
        engine.run_live_session(
            config=session_config,
            human_turns=[
                Message(
                    role="user",
                    speaker=args.speaker,
                    content=user_text,
                    channel=(args.channel or DEFAULT_CLI_CHANNEL).strip().lower(),
                    channel_user_id=(args.channel_user_id.strip() or args.speaker),
                )
            ],
        )
    )

    latest = transcript.messages[-1]
    if latest.speaker:
        print_func(f"[{latest.speaker}] {latest.content}")
    else:
        print_func(latest.content)

    output_path = Path(args.output) if args.output else (work_path / "transcript.json")
    if not output_path.is_absolute():
        output_path = work_path / output_path
    _write_transcript_output(transcript.messages, output_path)
    return 0


def run() -> int:
    return main()


if __name__ == "__main__":
    raise SystemExit(main())

