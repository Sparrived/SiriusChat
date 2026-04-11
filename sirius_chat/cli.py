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
    SessionConfig,
    create_session_config_from_selected_agent,
    create_async_engine,
    merge_provider_sources,
)
from sirius_chat.config.helpers import build_orchestration_policy_from_dict
from sirius_chat.logging_config import configure_logging, setup_log_archival, get_logger

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


def _load_session_config(config_path: Path, work_path: Path) -> tuple[SessionConfig, list[dict[str, object]]]:
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


def _build_engine(work_path: Path, providers_config: list[dict[str, object]]) -> AsyncRolePlayEngine:
    merged = merge_provider_sources(
        work_path=work_path,
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

    # 配置日志系统 - 同时输出到控制台和日志文件
    config_path = Path(args.config)
    work_path = Path(args.work_path) if args.work_path else (Path.cwd() / "data")
    work_path.mkdir(parents=True, exist_ok=True)
    
    log_dir = work_path / "logs"
    
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

    try:
        session_config, providers_config = _load_session_config(config_path, work_path)
    except ValueError as exc:
        print_func(f"加载 SessionConfig 失败：{exc}")
        print_func("请先通过提示词生成器生成并保存 agent 资产（generated_agents.json），再启动会话。")
        return 1
    engine = _build_engine(work_path, providers_config)

    user_text = args.message.strip() if args.message else input_func("你> ").strip()
    if not user_text:
        print_func("未输入消息，已退出。")
        return 0

    transcript = asyncio.run(engine.run_live_session(config=session_config))
    transcript = asyncio.run(
        engine.run_live_message(
            config=session_config,
            transcript=transcript,
            turn=Message(
                role="user",
                speaker=args.speaker,
                content=user_text,
                channel=(args.channel or DEFAULT_CLI_CHANNEL).strip().lower(),
                channel_user_id=(args.channel_user_id.strip() or args.speaker),
            ),
        )
    )

    latest = transcript.messages[-1]
    if latest.speaker:
        print_func(f"[{latest.speaker}] {latest.content}")
    else:
        print_func(latest.content)

    output_path = Path(args.output) if args.output else Path("transcript.json")
    if not output_path.is_absolute():
        output_path = work_path / output_path
    _write_transcript_output(transcript.messages, output_path)
    return 0


def run() -> int:
    return main()


if __name__ == "__main__":
    raise SystemExit(main())

