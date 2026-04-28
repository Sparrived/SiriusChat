"""NapCat 环境管理器。

负责 NapCat 的自动下载、安装、配置和生命周期管理。

依赖:
    - httpx (下载 NapCat Release)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

LOG = logging.getLogger("napcat_manager")

GITHUB_API = "https://api.github.com/repos/NapNeko/NapCatQQ/releases/latest"
ASSET_NAME = "NapCat.Shell.zip"


class NapCatManager:
    """NapCat 环境管理器。

    提供安装检查、自动下载、配置生成、启动/停止、日志读取等功能。
    """

    def __init__(
        self,
        install_dir: str | Path,
        instance_dir: str | Path | None = None,
    ) -> None:
        self.install_dir = Path(install_dir).resolve()
        self.instance_dir = Path(instance_dir).resolve() if instance_dir else self.install_dir
        self.config_dir = self.instance_dir / "config"
        self.logs_dir = self.instance_dir / "logs"
        self._process: subprocess.Popen | None = None
        self._monitor_task: asyncio.Task | None = None

    @classmethod
    def for_persona(
        cls,
        global_install_dir: str | Path,
        persona_name: str,
        instances_root: str | Path | None = None,
    ) -> "NapCatManager":
        """为指定人格创建 NapCat 实例管理器。

        实例目录结构::

            {instances_root or global_install_dir}/instances/{persona_name}/
                ├── config/         # 独立配置
                ├── logs/           # 独立日志
                └── qqnt.json       # 从全局复制
        """
        global_dir = Path(global_install_dir).resolve()
        if instances_root is None:
            instances_root = global_dir / "instances"
        else:
            instances_root = Path(instances_root).resolve()

        instance_dir = instances_root / persona_name
        instance_dir.mkdir(parents=True, exist_ok=True)

        # 复制必要的全局文件到实例目录（如果不存在）
        for filename in ("qqnt.json",):
            src = global_dir / filename
            dst = instance_dir / filename
            if src.exists() and not dst.exists():
                shutil.copy2(str(src), str(dst))

        return cls(global_install_dir, instance_dir)

    # ── 状态检查 ─────────────────────────────────────────

    def is_installed(self) -> bool:
        """检查 NapCat 是否已安装（通过核心文件 napcat.mjs 判断）。"""
        return (self.install_dir / "napcat.mjs").exists()

    @staticmethod
    def is_qq_installed() -> bool:
        """检查 QQ 是否通过注册表安装（仅 Windows）。"""
        if sys.platform != "win32":
            return False
        try:
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\QQ",
            )
            winreg.QueryValueEx(key, "UninstallString")
            winreg.CloseKey(key)
            return True
        except Exception:
            return False

    @staticmethod
    def get_qq_path() -> str | None:
        """从注册表获取 QQ.exe 完整路径（仅 Windows）。"""
        if sys.platform != "win32":
            return None
        try:
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\QQ",
            )
            value, _ = winreg.QueryValueEx(key, "UninstallString")
            winreg.CloseKey(key)
            uninstall_path = Path(value)
            qq_path = uninstall_path.parent / "QQ.exe"
            return str(qq_path) if qq_path.exists() else None
        except Exception:
            return None

    @property
    def is_running(self) -> bool:
        """检查 NapCat 进程是否仍在运行。"""
        return self._process is not None and self._process.poll() is None

    # ── 安装 ─────────────────────────────────────────────

    async def install(self, version: str = "latest") -> dict:
        """从 GitHub Release 下载并安装 NapCat。

        Args:
            version: 目标版本标签，默认 latest。

        Returns:
            {"success": bool, "message": str}
        """
        if self.is_installed:
            return {"success": True, "message": "NapCat 已安装"}

        try:
            tag, download_url = await self._fetch_release_info(version)
        except Exception as exc:
            LOG.error("获取 NapCat Release 信息失败: %s", exc)
            return {
                "success": False,
                "message": f"获取 Release 信息失败: {exc}。请检查网络连接或手动下载 NapCat 到 {self.install_dir}",
            }

        LOG.info("正在下载 NapCat %s ...", tag)
        try:
            zip_path = await self._download_file(download_url)
        except Exception as exc:
            LOG.error("下载 NapCat 失败: %s", exc)
            return {"success": False, "message": f"下载失败: {exc}"}

        try:
            self._extract_zip(zip_path)
        except Exception as exc:
            LOG.error("解压 NapCat 失败: %s", exc)
            return {"success": False, "message": f"解压失败: {exc}"}
        finally:
            try:
                os.remove(zip_path)
            except Exception:
                pass

        LOG.info("NapCat %s 安装完成", tag)
        return {"success": True, "message": f"NapCat {tag} 安装完成"}

    async def _fetch_release_info(self, version: str) -> tuple[str, str]:
        """获取 GitHub Release 信息，返回 (tag, download_url)。"""
        try:
            import httpx
        except ImportError:
            raise RuntimeError("安装 NapCat 需要 httpx: pip install httpx")

        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            if version == "latest":
                resp = await client.get(GITHUB_API, headers={"Accept": "application/vnd.github.v3+json"})
                resp.raise_for_status()
                data = resp.json()
                tag = data["tag_name"]
                assets = data.get("assets", [])
            else:
                url = f"https://api.github.com/repos/NapNeko/NapCatQQ/releases/tags/{version}"
                resp = await client.get(url, headers={"Accept": "application/vnd.github.v3+json"})
                resp.raise_for_status()
                data = resp.json()
                tag = data["tag_name"]
                assets = data.get("assets", [])

            for asset in assets:
                if asset["name"] == ASSET_NAME:
                    return tag, asset["browser_download_url"]

            raise RuntimeError(f"Release {tag} 中未找到资源 {ASSET_NAME}")

    async def _download_file(self, url: str) -> str:
        """流式下载文件到临时目录，返回本地路径。"""
        import httpx

        fd, path = tempfile.mkstemp(suffix=".zip")
        os.close(fd)

        async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("content-length", 0))
                downloaded = 0
                chunk_size = 65536
                with open(path, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=chunk_size):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total > 0 and downloaded % (chunk_size * 16) == 0:
                            LOG.info("下载进度: %.1f%%", downloaded / total * 100)

        return path

    def _extract_zip(self, zip_path: str) -> None:
        """解压 ZIP 到 install_dir。"""
        self.install_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path, "r") as zf:
            # 如果 ZIP 根目录只有一个文件夹，先去掉那一层
            top_dirs = {name.split("/")[0] for name in zf.namelist() if "/" in name}
            if len(top_dirs) == 1:
                prefix = list(top_dirs)[0] + "/"
                for member in zf.namelist():
                    if member.startswith(prefix):
                        target = self.install_dir / member[len(prefix):]
                        if member.endswith("/"):
                            target.mkdir(parents=True, exist_ok=True)
                        else:
                            target.parent.mkdir(parents=True, exist_ok=True)
                            with zf.open(member) as src, open(target, "wb") as dst:
                                shutil.copyfileobj(src, dst)
            else:
                zf.extractall(self.install_dir)

    # ── 配置 ─────────────────────────────────────────────

    def configure(
        self,
        qq_number: str,
        ws_port: int = 3001,
        ws_token: str = "napcat_ws",
        report_self_message: bool = False,
    ) -> dict:
        """生成 NapCat 配置文件。

        会生成两个文件:
            - config/napcat_{qq}.json   NapCat 核心配置
            - config/onebot11_{qq}.json OneBot v11 协议配置

        Returns:
            {"success": bool, "message": str}
        """
        if not self.is_installed:
            return {"success": False, "message": "NapCat 未安装，请先安装"}

        self.config_dir.mkdir(parents=True, exist_ok=True)

        # NapCat 核心配置
        napcat_config = {
            "fileLog": False,
            "consoleLog": True,
            "fileLogLevel": "debug",
            "consoleLogLevel": "info",
            "packetBackend": "auto",
            "packetServer": "",
            "o3HookMode": 1,
            "bypass": {
                "hook": False,
                "window": False,
                "module": False,
                "process": False,
                "container": False,
                "js": False,
            },
            "autoTimeSync": True,
        }
        napcat_path = self.config_dir / f"napcat_{qq_number}.json"
        napcat_path.write_text(
            json.dumps(napcat_config, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        # OneBot v11 协议配置
        onebot_config = {
            "network": {
                "websocketServers": [
                    {
                        "enable": True,
                        "name": "WsServer",
                        "host": "localhost",
                        "port": ws_port,
                        "reportSelfMessage": report_self_message,
                        "enableForcePushEvent": True,
                        "messagePostFormat": "array",
                        "token": ws_token,
                        "debug": False,
                        "heartInterval": 30000,
                    }
                ],
                "httpServers": [],
                "httpSseServers": [],
                "httpClients": [],
                "websocketClients": [],
                "plugins": [],
            },
            "musicSignUrl": "",
            "enableLocalFile2Url": False,
            "parseMultMsg": False,
            "imageDownloadProxy": "",
            "timeout": {
                "baseTimeout": 10000,
                "uploadSpeedKBps": 256,
                "downloadSpeedKBps": 256,
                "maxTimeout": 1800000,
            },
        }
        onebot_path = self.config_dir / f"onebot11_{qq_number}.json"
        onebot_path.write_text(
            json.dumps(onebot_config, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        LOG.info("NapCat 配置已生成: %s", self.config_dir)
        return {"success": True, "message": f"配置已生成 (QQ: {qq_number}, WS: localhost:{ws_port})"}

    # ── 启动 / 停止 ──────────────────────────────────────

    async def start(self, qq_number: str | None = None) -> dict:
        """启动 NapCat。

        Windows 下通过 NapCatWinBootMain.exe 注入 QQ 并启动。
        实例模式下，二进制从 install_dir 加载，但运行时 cwd 在 instance_dir。

        Args:
            qq_number: QQ 号，提供则使用快速登录；省略则使用二维码登录。

        Returns:
            {"success": bool, "message": str}
        """
        if self.is_running:
            return {"success": True, "message": "NapCat 已在运行"}

        if not self.is_installed:
            return {"success": False, "message": "NapCat 未安装"}

        qq_path = self.get_qq_path()
        if not qq_path:
            return {
                "success": False,
                "message": "未检测到 QQ 安装。请先安装 QQ 客户端（支持 QQNT 9.9.x）。",
            }

        # 二进制从全局安装目录加载
        launcher = self.install_dir / "NapCatWinBootMain.exe"
        hook = self.install_dir / "NapCatWinBootHook.dll"
        main_script = self.install_dir / "napcat.mjs"
        # loadNapCat.js 在实例目录生成（避免冲突）
        load_script = self.instance_dir / "loadNapCat.js"

        if not launcher.exists():
            return {"success": False, "message": f"启动器不存在: {launcher}"}
        if not hook.exists():
            return {"success": False, "message": f"注入 DLL 不存在: {hook}"}

        # 生成 loadNapCat.js
        mjs_path = str(main_script).replace("\\", "/")
        load_script.write_text(
            f'(async () => {{await import("file:///{mjs_path}")}})()',
            encoding="utf-8",
        )

        # 准备环境变量
        env = os.environ.copy()
        # qqnt.json 优先使用实例目录的，fallback 到全局
        qqnt_path = self.instance_dir / "qqnt.json"
        if not qqnt_path.exists():
            qqnt_path = self.install_dir / "qqnt.json"
        env["NAPCAT_PATCH_PACKAGE"] = str(qqnt_path)
        env["NAPCAT_LOAD_PATH"] = str(load_script)
        env["NAPCAT_INJECT_PATH"] = str(hook)
        env["NAPCAT_LAUNCHER_PATH"] = str(launcher)
        env["NAPCAT_MAIN_PATH"] = str(main_script)

        cmd = [str(launcher), qq_path, str(hook)]
        if qq_number:
            cmd.extend(["-q", qq_number])

        LOG.info("正在启动 NapCat (实例: %s): %s", self.instance_dir.name, " ".join(cmd))
        try:
            # Windows 下使用 CREATE_NEW_CONSOLE 让 QQ 窗口独立显示
            creationflags = subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0
            self._process = subprocess.Popen(
                cmd,
                env=env,
                cwd=str(self.instance_dir),
                creationflags=creationflags,
            )
        except Exception as exc:
            LOG.error("启动 NapCat 失败: %s", exc)
            return {"success": False, "message": f"启动失败: {exc}"}

        LOG.info("NapCat 进程已启动 (pid=%s)", self._process.pid)
        return {
            "success": True,
            "message": f"NapCat 已启动 (pid={self._process.pid})。首次使用请在弹出的 QQ 窗口中扫码登录。",
        }

    async def stop(self) -> dict:
        """停止 NapCat 进程（同时会关闭 QQ）。"""
        if not self.is_running:
            return {"success": True, "message": "NapCat 未在运行"}

        try:
            self._process.terminate()  # type: ignore[union-attr]
            await asyncio.wait_for(
                asyncio.to_thread(self._process.wait),  # type: ignore[union-attr]
                timeout=10.0,
            )
        except asyncio.TimeoutError:
            LOG.warning("NapCat 进程未在 10 秒内退出，强制结束")
            self._process.kill()  # type: ignore[union-attr]
        except Exception as exc:
            LOG.error("停止 NapCat 失败: %s", exc)
            return {"success": False, "message": f"停止失败: {exc}"}

        self._process = None
        LOG.info("NapCat 已停止")
        return {"success": True, "message": "NapCat 已停止"}

    # ── 等待就绪 ─────────────────────────────────────────

    async def wait_for_ws(
        self,
        host: str = "localhost",
        port: int = 3001,
        timeout: float = 120.0,
    ) -> bool:
        """轮询等待 NapCat WebSocket 端口就绪。

        首次启动时 QQ 需要扫码，超时时间建议设长一些。
        """
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(host, port),
                    timeout=2.0,
                )
                writer.close()
                await writer.wait_closed()
                LOG.info("NapCat WebSocket 已就绪 (%s:%s)", host, port)
                return True
            except Exception:
                await asyncio.sleep(2.0)

        LOG.warning("等待 NapCat WebSocket 超时 (%s 秒)", timeout)
        return False

    # ── 日志 ─────────────────────────────────────────────

    def get_logs(self, lines: int = 100) -> list[str]:
        """读取 NapCat 日志文件（从 logs/ 目录读取最新的日志）。"""
        if not self.logs_dir.exists():
            return []

        log_files = sorted(
            [f for f in self.logs_dir.iterdir() if f.suffix == ".log"],
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        if not log_files:
            return []

        try:
            text = log_files[0].read_text(encoding="utf-8", errors="ignore")
            all_lines = text.splitlines()
            return all_lines[-lines:] if len(all_lines) > lines else all_lines
        except Exception as exc:
            LOG.warning("读取日志失败: %s", exc)
            return []

    def get_status(self) -> dict:
        """获取 NapCat 完整状态信息。"""
        return {
            "installed": self.is_installed,
            "running": self.is_running,
            "qq_installed": self.is_qq_installed(),
            "qq_path": self.get_qq_path(),
            "install_dir": str(self.install_dir),
        }
