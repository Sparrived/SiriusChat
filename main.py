"""SiriusChat 统一启动入口。

启动 NapCat Bot + WebUI 配置面板。

使用方法::

    python main.py                          # 使用默认配置启动
    python main.py --config config.json     # 指定配置文件
    python main.py --init-config config.json # 生成默认配置模板
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
LOG = logging.getLogger("sirius_chat")

REPO_ROOT = Path(__file__).resolve().parent


def _default_config() -> dict:
    """返回默认 Bot 配置。"""
    return {
        "ws_url": "ws://localhost:3001",
        "token": "napcat_ws",
        "work_path": str(REPO_ROOT / "data" / "bot"),
        "root": "",
        "allowed_group_ids": [],
        "allowed_private_user_ids": [],
        "enable_group_chat": True,
        "enable_private_chat": True,
        "auto_install_skill_deps": True,
        "providers": [],
        "webui_host": "0.0.0.0",
        "webui_port": 8080,
        "auto_manage_napcat": False,
        "napcat_install_dir": str(REPO_ROOT / "napcat"),
    }


def _init_config(path: Path) -> None:
    """生成默认配置文件并退出。"""
    config = _default_config()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    LOG.info("默认配置已写入: %s", path)


async def _run_bot(config: dict) -> None:
    """启动 NapCat Bot + WebUI。"""
    from sirius_chat.platforms import NapCatAdapter, NapCatBridge, NapCatManager, WebUIServer

    ws_url = str(config.get("ws_url", "ws://localhost:3001"))
    token = str(config.get("token", "napcat_ws"))
    work_path = str(
        config.get(
            "work_path",
            str(REPO_ROOT / "data" / "bot"),
        )
    )
    root = str(config.get("root", ""))
    auto_manage_napcat = bool(config.get("auto_manage_napcat", False))
    napcat_install_dir = str(
        config.get(
            "napcat_install_dir",
            str(REPO_ROOT / "napcat"),
        )
    )

    napcat_manager = None
    if auto_manage_napcat:
        napcat_manager = NapCatManager(napcat_install_dir)
        LOG.info("NapCat 自动管理已启用，安装目录: %s", napcat_install_dir)

        if not napcat_manager.is_installed:
            LOG.info("NapCat 未安装，尝试自动安装...")
            result = await napcat_manager.install()
            if result["success"]:
                LOG.info("NapCat 安装成功")
            else:
                LOG.warning("NapCat 自动安装失败: %s", result["message"])
                LOG.warning("请通过 WebUI 手动安装 NapCat")

        if napcat_manager.is_installed and not napcat_manager.is_running:
            # 尝试从已有配置推断 QQ 号
            config_dir = Path(napcat_install_dir) / "config"
            qq_number = None
            if config_dir.exists():
                for cfg in config_dir.glob("onebot11_*.json"):
                    qq_number = cfg.stem.replace("onebot11_", "")
                    break

            LOG.info("尝试自动启动 NapCat (QQ: %s)...", qq_number or "二维码登录")
            result = await napcat_manager.start(qq_number=qq_number)
            if result["success"]:
                LOG.info("NapCat 已启动，等待 WebSocket 就绪...")
                ready = await napcat_manager.wait_for_ws(timeout=120.0)
                if ready:
                    LOG.info("NapCat WebSocket 已就绪")
                else:
                    LOG.warning("NapCat WebSocket 未就绪，请检查 QQ 是否已扫码登录")
            else:
                LOG.warning("NapCat 启动失败: %s", result["message"])

    adapter = NapCatAdapter(ws_url=ws_url, token=token)
    bridge = NapCatBridge(
        adapter=adapter,
        work_path=work_path,
        config={"root": root, **config},
    )
    webui = WebUIServer(
        bridge=bridge,
        host=str(config.get("webui_host", "0.0.0.0")),
        port=int(config.get("webui_port", 8080)),
        napcat_install_dir=napcat_install_dir if auto_manage_napcat else None,
    )

    await adapter.connect()
    await bridge.start()
    await webui.start()
    LOG.info("Bot 已启动，WebUI: http://localhost:%s，按 Ctrl+C 停止", webui.port)

    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        pass
    finally:
        await webui.stop()
        await bridge.stop()
        await adapter.close()
        if napcat_manager and napcat_manager.is_running:
            LOG.info("正在停止 NapCat...")
            await napcat_manager.stop()
        LOG.info("Bot 已停止")


def main() -> int:
    parser = argparse.ArgumentParser(description="SiriusChat 启动入口")
    parser.add_argument("--config", default="config.json", help="配置文件路径 (默认: config.json)")
    parser.add_argument("--init-config", metavar="PATH", help="生成默认配置模板并退出")
    parser.add_argument("--work-path", help="覆盖配置中的工作路径")
    parser.add_argument("--ws-url", help="覆盖 NapCat WebSocket 地址")
    parser.add_argument("--root", help="覆盖管理员 QQ 号")
    parser.add_argument("--webui-port", type=int, help="覆盖 WebUI 端口")

    args = parser.parse_args()

    if args.init_config:
        _init_config(Path(args.init_config))
        return 0

    config_path = Path(args.config)
    config: dict = {}
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception as exc:
            LOG.warning("配置文件读取失败: %s", exc)
    else:
        LOG.warning("配置文件不存在: %s，使用默认配置", config_path)
        config = _default_config()

    # CLI 参数覆盖配置文件
    if args.work_path:
        config["work_path"] = args.work_path
    if args.ws_url:
        config["ws_url"] = args.ws_url
    if args.root:
        config["root"] = args.root
    if args.webui_port:
        config["webui_port"] = args.webui_port

    # 确保 work_path 存在
    work_path = Path(config.get("work_path", "."))
    work_path.mkdir(parents=True, exist_ok=True)

    try:
        asyncio.run(_run_bot(config))
    except KeyboardInterrupt:
        LOG.info("收到中断信号，正在停止...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
