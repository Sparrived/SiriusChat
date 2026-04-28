"""SiriusChat 多进程人格管理 CLI。

启动与管理多个人格实例，每个人格在独立子进程中运行。

使用方法::

    python main.py run                           # 启动所有已启用人格 + WebUI
    python main.py webui                         # 仅启动 WebUI（管理模式）
    python main.py persona list                  # 列出所有人格
    python main.py persona create <name>         # 创建人格
    python main.py persona remove <name>         # 删除人格
    python main.py persona start <name>          # 前台启动单个人格
    python main.py persona stop <name>           # 停止单个人格
    python main.py persona status <name>         # 查看人格状态
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from sirius_chat.logging_config import configure_logging

REPO_ROOT = Path(__file__).resolve().parent
DATA_DIR = REPO_ROOT / "data"
GLOBAL_CONFIG_PATH = DATA_DIR / "global_config.json"


def _default_global_config() -> dict:
    """返回默认全局配置。"""
    return {
        "webui_host": "0.0.0.0",
        "webui_port": 8080,
        "auto_manage_napcat": False,
        "napcat_install_dir": str(REPO_ROOT / "napcat"),
        "log_level": "INFO",
    }


def _load_global_config() -> dict:
    """加载全局配置，若不存在则创建默认。"""
    if GLOBAL_CONFIG_PATH.exists():
        try:
            return json.loads(GLOBAL_CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception as exc:
            logging.getLogger("sirius.main").warning("全局配置读取失败: %s，使用默认", exc)
    config = _default_global_config()
    GLOBAL_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    GLOBAL_CONFIG_PATH.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    return config


# ---------------------------------------------------------------------------
# 子命令实现
# ---------------------------------------------------------------------------

async def _cmd_run(args: argparse.Namespace) -> None:
    """启动所有已启用的人格 + WebUI（可选自动管理 NapCat）。"""
    config = _load_global_config()
    configure_logging(level=config.get("log_level", "INFO"), format_type="console")
    LOG = logging.getLogger("sirius.main")

    from sirius_chat.persona_manager import PersonaManager
    from sirius_chat.webui import WebUIServer

    persona_manager = PersonaManager(DATA_DIR, global_config=config)

    # ── NapCat 自动管理（多实例）───────────────────────────
    napcat_managers: dict[str, Any] = {}
    if config.get("auto_manage_napcat"):
        from sirius_chat.platforms.napcat_manager import NapCatManager
        from sirius_chat.persona_config import PersonaAdaptersConfig

        napcat_install_dir = str(config.get("napcat_install_dir", str(REPO_ROOT / "napcat")))
        global_mgr = NapCatManager(napcat_install_dir)

        # 1. 全局安装（只需要一次）
        if not global_mgr.is_installed:
            LOG.info("NapCat 未安装，尝试自动安装...")
            result = await global_mgr.install()
            if result["success"]:
                LOG.info("NapCat 安装成功")
            else:
                LOG.warning("NapCat 自动安装失败: %s", result["message"])
                LOG.warning("请通过 WebUI 手动安装 NapCat")

        # 2. 为每个人格启动独立的 NapCat 实例
        if global_mgr.is_installed:
            for info in persona_manager.list_personas():
                name = info["name"]
                paths = persona_manager.get_persona_paths(name)
                if paths is None:
                    continue
                adapters = PersonaAdaptersConfig.load(paths.adapters)
                for a in adapters.adapters:
                    if a.type != "napcat" or not a.enabled:
                        continue
                    qq = getattr(a, "qq_number", "")
                    port = int(a.ws_url.rsplit(":", 1)[-1]) if ":" in a.ws_url else 3001
                    if not qq:
                        LOG.warning("人格 %s 的 NapCat 未配置 QQ 号，跳过", name)
                        continue

                    instance_mgr = NapCatManager.for_persona(
                        global_install_dir=napcat_install_dir,
                        persona_name=name,
                    )
                    LOG.info("配置 NapCat 实例 %s (QQ: %s, 端口: %s)...", name, qq, port)
                    instance_mgr.configure(qq_number=qq, ws_port=port)
                    result = await instance_mgr.start(qq_number=qq)
                    if result["success"]:
                        LOG.info("NapCat 实例 %s 已启动，等待 WS 就绪...", name)
                        ready = await instance_mgr.wait_for_ws(port=port, timeout=120.0)
                        if ready:
                            LOG.info("NapCat 实例 %s WS 已就绪", name)
                        else:
                            LOG.warning("NapCat 实例 %s WS 未就绪，请检查 QQ 是否已扫码登录", name)
                        napcat_managers[name] = instance_mgr
                    else:
                        LOG.warning("NapCat 实例 %s 启动失败: %s", name, result["message"])

    # ── 启动所有已启用人格 ────────────────────────────────
    LOG.info("正在启动已启用人格...")
    results = persona_manager.start_all()
    for name, ok in results.items():
        LOG.info("  %s %s", "✓" if ok else "✗", name)

    # ── 启动 WebUI ────────────────────────────────────────
    napcat_dir = config.get("napcat_install_dir") if config.get("auto_manage_napcat") else None
    webui = WebUIServer(
        persona_manager=persona_manager,
        host=str(config.get("webui_host", "0.0.0.0")),
        port=int(config.get("webui_port", 8080)),
        napcat_install_dir=napcat_dir,
    )
    await webui.start()
    LOG.info("WebUI: http://localhost:%s", webui.port)
    LOG.info("按 Ctrl+C 停止所有服务")

    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        pass
    finally:
        LOG.info("正在停止所有人格...")
        persona_manager.stop_all()
        await webui.stop()
        for name, mgr in napcat_managers.items():
            if mgr.is_running:
                LOG.info("正在停止 NapCat 实例 %s...", name)
                await mgr.stop()
        LOG.info("所有服务已停止")


async def _cmd_webui(args: argparse.Namespace) -> None:
    """仅启动 WebUI（不启动任何人格）。"""
    config = _load_global_config()
    configure_logging(level=config.get("log_level", "INFO"), format_type="console")
    LOG = logging.getLogger("sirius.main")

    from sirius_chat.persona_manager import PersonaManager
    from sirius_chat.webui import WebUIServer

    persona_manager = PersonaManager(DATA_DIR, global_config=config)
    napcat_dir = config.get("napcat_install_dir") if config.get("auto_manage_napcat") else None
    webui = WebUIServer(
        persona_manager=persona_manager,
        host=str(config.get("webui_host", "0.0.0.0")),
        port=int(config.get("webui_port", 8080)),
        napcat_install_dir=napcat_dir,
    )
    await webui.start()
    LOG.info("WebUI: http://localhost:%s（仅管理模式，无人格运行）", webui.port)

    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        pass
    finally:
        await webui.stop()


def _cmd_persona_list(args: argparse.Namespace) -> None:
    """列出所有人格。"""
    configure_logging(level="WARNING", format_type="console")
    from sirius_chat.persona_manager import PersonaManager

    config = _load_global_config()
    manager = PersonaManager(DATA_DIR, global_config=config)
    personas = manager.list_personas()
    if not personas:
        print("暂无任何人格。使用 `python main.py persona create <name>` 创建。")
        return

    print(f"{'人格名':<12} {'角色名':<12} {'状态':<8} {'PID':<8} {'Adapter'}")
    print("-" * 60)
    for p in personas:
        status = "运行中" if p.get("running") else "已停止"
        pid = str(p.get("pid") or "-")
        adapters = p.get("adapters_count", 0)
        print(f"{p['name']:<12} {p.get('persona_name') or '-':<12} {status:<8} {pid:<8} {adapters}")


def _cmd_persona_create(args: argparse.Namespace) -> None:
    """创建新人格。"""
    configure_logging(level="WARNING", format_type="console")
    from sirius_chat.persona_manager import PersonaManager

    config = _load_global_config()
    manager = PersonaManager(DATA_DIR, global_config=config)
    try:
        pdir = manager.create_persona(
            args.name,
            persona_name=args.name,
            keywords=args.keywords or [],
        )
        print(f"人格已创建: {args.name}")
        print(f"  目录: {pdir}")
        print(f"  请编辑 {pdir / 'adapters.json'} 配置连接，然后运行:")
        print(f"    python main.py run")
    except FileExistsError:
        print(f"人格已存在: {args.name}")
        sys.exit(1)


def _cmd_persona_remove(args: argparse.Namespace) -> None:
    """删除人格。"""
    configure_logging(level="WARNING", format_type="console")
    from sirius_chat.persona_manager import PersonaManager

    config = _load_global_config()
    manager = PersonaManager(DATA_DIR, global_config=config)
    ok = manager.remove_persona(args.name)
    if ok:
        print(f"人格已删除: {args.name}")
    else:
        print(f"人格不存在: {args.name}")
        sys.exit(1)


def _cmd_persona_migrate(args: argparse.Namespace) -> None:
    """从旧目录迁移人格。"""
    configure_logging(level="INFO", format_type="console")
    from sirius_chat.persona_manager import PersonaManager

    config = _load_global_config()
    manager = PersonaManager(DATA_DIR, global_config=config)
    source = Path(args.source).resolve()
    if not source.exists():
        print(f"源目录不存在: {source}")
        sys.exit(1)

    try:
        pdir = manager.migrate_persona(source, args.name)
        print(f"人格已迁移: {args.name}")
        print(f"  目录: {pdir}")
        port = manager.get_port(args.name)
        if port:
            print(f"  分配端口: {port}")
            print(f"  请为该人格配置 NapCat (QQ) 并监听端口 {port}")
    except FileExistsError as exc:
        print(f"迁移失败: {exc}")
        sys.exit(1)
    except FileNotFoundError as exc:
        print(f"迁移失败: {exc}")
        sys.exit(1)


async def _cmd_persona_start(args: argparse.Namespace) -> None:
    """前台启动单个人格（调试用）。"""
    from sirius_chat.persona_worker import PersonaWorker

    pdir = DATA_DIR / "personas" / args.name
    if not pdir.exists():
        print(f"人格不存在: {args.name}")
        sys.exit(1)

    configure_logging(level="INFO", format_type="console")
    worker = PersonaWorker(pdir)

    # 信号处理
    loop = asyncio.get_running_loop()
    for sig in (__import__("signal").SIGTERM, __import__("signal").SIGINT):
        loop.add_signal_handler(sig, worker.shutdown)

    try:
        await worker.run()
    except Exception:
        logging.getLogger("sirius.main").exception("人格工作进程异常退出")
        raise


def _cmd_persona_stop(args: argparse.Namespace) -> None:
    """停止单个人格。"""
    configure_logging(level="WARNING", format_type="console")
    from sirius_chat.persona_manager import PersonaManager

    config = _load_global_config()
    manager = PersonaManager(DATA_DIR, global_config=config)
    ok = manager.stop_persona(args.name)
    if ok:
        print(f"人格已停止: {args.name}")
    else:
        print(f"人格未在运行或不存在: {args.name}")


def _cmd_persona_status(args: argparse.Namespace) -> None:
    """查看人格状态。"""
    configure_logging(level="WARNING", format_type="console")
    from sirius_chat.persona_manager import PersonaManager

    config = _load_global_config()
    manager = PersonaManager(DATA_DIR, global_config=config)
    info = manager.get_persona_status(args.name)
    if info is None:
        print(f"人格不存在: {args.name}")
        sys.exit(1)

    print(json.dumps(info, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="SiriusChat 多进程人格管理 CLI")
    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # run
    subparsers.add_parser("run", help="启动所有已启用人格 + WebUI")

    # webui
    subparsers.add_parser("webui", help="仅启动 WebUI（管理模式）")

    # persona
    persona_parser = subparsers.add_parser("persona", help="人格管理")
    persona_sub = persona_parser.add_subparsers(dest="persona_cmd", help="人格子命令")

    persona_sub.add_parser("list", help="列出所有人格")

    create_parser = persona_sub.add_parser("create", help="创建人格")
    create_parser.add_argument("name", help="人格标识名（目录名）")
    create_parser.add_argument("--keywords", nargs="*", default=[], help="关键词（空格分隔）")

    remove_parser = persona_sub.add_parser("remove", help="删除人格")
    remove_parser.add_argument("name", help="人格标识名")

    migrate_parser = persona_sub.add_parser("migrate", help="从旧目录迁移人格")
    migrate_parser.add_argument("--source", required=True, help="源目录路径（如 data/bot）")
    migrate_parser.add_argument("--name", required=True, help="目标人格标识名")

    start_parser = persona_sub.add_parser("start", help="前台启动单个人格")
    start_parser.add_argument("name", help="人格标识名")

    stop_parser = persona_sub.add_parser("stop", help="停止单个人格")
    stop_parser.add_argument("name", help="人格标识名")

    status_parser = persona_sub.add_parser("status", help="查看人格状态")
    status_parser.add_argument("name", help="人格标识名")

    args = parser.parse_args()

    if args.command == "run":
        asyncio.run(_cmd_run(args))
    elif args.command == "webui":
        asyncio.run(_cmd_webui(args))
    elif args.command == "persona":
        if args.persona_cmd == "list":
            _cmd_persona_list(args)
        elif args.persona_cmd == "create":
            _cmd_persona_create(args)
        elif args.persona_cmd == "remove":
            _cmd_persona_remove(args)
        elif args.persona_cmd == "migrate":
            _cmd_persona_migrate(args)
        elif args.persona_cmd == "start":
            asyncio.run(_cmd_persona_start(args))
        elif args.persona_cmd == "stop":
            _cmd_persona_stop(args)
        elif args.persona_cmd == "status":
            _cmd_persona_status(args)
        else:
            persona_parser.print_help()
            return 1
    else:
        parser.print_help()
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
