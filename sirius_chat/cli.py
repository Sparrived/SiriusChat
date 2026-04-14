from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Callable

from sirius_chat.api import (
    Message,
    SessionConfig,
    create_session_config_from_selected_agent,
    generate_humanized_roleplay_questions,
    list_roleplay_question_templates,
)
from sirius_chat.config import ConfigManager
from sirius_chat.config.helpers import build_orchestration_policy_from_dict
from sirius_chat.logging_config import configure_logging, setup_log_archival, get_logger
from sirius_chat.workspace.runtime import WorkspaceRuntime

InputFunc = Callable[[str], str]
PrintFunc = Callable[[str], None]
DEFAULT_CLI_CHANNEL = "cli"


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sirius Chat CLI（库内薄封装）")
    parser.add_argument("--config", default="examples/session.json", help="会话 JSON/JSONC 配置文件路径")
    parser.add_argument("--config-root", default="", help="配置持久化目录（默认与 work-path 相同）")
    parser.add_argument("--work-path", default="", help="运行工作目录（默认当前目录下 data）")
    parser.add_argument("--output", default="", help="输出 transcript JSON 文件路径（默认 <work_path>/transcript.json）")
    parser.add_argument("--message", default="", help="用户输入消息；不提供则交互输入一条")
    parser.add_argument("--speaker", default="用户", help="用户说话人名称")
    parser.add_argument("--channel", default=DEFAULT_CLI_CHANNEL, help="消息渠道标识（默认 cli）")
    parser.add_argument("--channel-user-id", default="", help="渠道内用户ID（默认使用 speaker）")
    parser.add_argument(
        "--list-roleplay-question-templates",
        action="store_true",
        help="列出可用的人格问卷模板并退出",
    )
    parser.add_argument(
        "--print-roleplay-questions-template",
        default="",
        help="打印指定模板的问题清单 JSON 并退出，例如 companion",
    )
    return parser


def _serialize_roleplay_questions(template: str) -> dict[str, object]:
    questions = generate_humanized_roleplay_questions(template=template)
    return {
        "template": template,
        "questions": [
            {
                "question": item.question,
                "perspective": item.perspective,
                "details": item.details,
            }
            for item in questions
        ],
    }


def _load_session_config(
    config_path: Path,
    work_path: Path,
    *,
    config_root: Path | None = None,
) -> tuple[SessionConfig, list[dict[str, object]]]:
    resolved_config_root = config_root or work_path
    manager = ConfigManager(base_path=resolved_config_root)
    workspace_config, providers_config = manager.bootstrap_workspace_from_legacy_session_json(
        config_path,
        work_path=resolved_config_root,
        data_path=work_path,
    )
    if not workspace_config.active_agent_key:
        raise ValueError("必需提供 generated_agent_key")
    if not providers_config:
        raise ValueError("SessionConfig 必需包含 providers 字段（list format）")

    session = manager.build_session_config(
        work_path=resolved_config_root,
        data_path=work_path,
        session_id="cli",
        overrides={"agent_key": workspace_config.active_agent_key},
    )
    return session, providers_config


def _build_runtime(work_path: Path, config_root: Path | None = None) -> WorkspaceRuntime:
    return WorkspaceRuntime.open(work_path, config_path=config_root)


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

    if args.list_roleplay_question_templates:
        print_func(json.dumps(list_roleplay_question_templates(), ensure_ascii=False, indent=2))
        return 0

    if args.print_roleplay_questions_template:
        try:
            payload = _serialize_roleplay_questions(args.print_roleplay_questions_template)
        except ValueError as exc:
            print_func(str(exc))
            return 1
        print_func(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    # 配置日志系统 - 同时输出到控制台和日志文件
    config_path = Path(args.config)
    work_path = Path(args.work_path) if args.work_path else (Path.cwd() / "data")
    config_root = Path(args.config_root) if args.config_root else work_path
    work_path.mkdir(parents=True, exist_ok=True)
    config_root.mkdir(parents=True, exist_ok=True)
    
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
        session_config, providers_config = _load_session_config(
            config_path,
            work_path,
            config_root=config_root,
        )
    except ValueError as exc:
        print_func(f"加载 SessionConfig 失败：{exc}")
        print_func("请先通过提示词生成器生成并保存 agent 资产（generated_agents.json），再启动会话。")
        return 1
    _ = providers_config
    runtime = _build_runtime(work_path, config_root)

    user_text = args.message.strip() if args.message else input_func("你> ").strip()
    if not user_text:
        print_func("未输入消息，已退出。")
        return 0

    transcript = asyncio.run(
        runtime.run_live_message(
            session_id=session_config.session_id,
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

