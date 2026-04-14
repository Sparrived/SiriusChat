from __future__ import annotations

import argparse
from pathlib import Path

from sirius_chat.api import SqliteSessionStore


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="迁移 legacy 会话状态到结构化 SQLite session_state.db")
    parser.add_argument("--work-path", required=True, help="会话工作目录，内部会查找 session_state.db / session_state.json")
    parser.add_argument("--filename", default="session_state.db", help="目标 SQLite 文件名，默认 session_state.db")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    store = SqliteSessionStore(Path(args.work_path), filename=args.filename)

    print(f"store_path={store.path}")
    if not store.exists():
        print("status=no-session-data")
        print("detail=未检测到可迁移或可加载的会话状态")
        return 0

    transcript = store.load()
    print("status=ok")
    print(f"messages={len(transcript.messages)}")
    print(f"users={len(transcript.user_memory.entries)}")
    print(f"token_records={len(transcript.token_usage_records)}")
    print(f"summary_chars={len(transcript.session_summary)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())