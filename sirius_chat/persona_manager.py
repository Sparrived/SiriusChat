"""人格管理器 — 主进程中的多人格生命周期管理。

职责：
- 扫描和维护 personas/ 目录
- 创建/删除人格（含默认配置生成）
- 启动/停止人格子进程
- 监控子进程健康状态
- 为 WebUI 提供查询接口
"""

from __future__ import annotations

import json
import logging
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sirius_chat.core.persona_generator import PersonaGenerator
from sirius_chat.core.persona_store import PersonaStore
from sirius_chat.models.persona import PersonaProfile
from sirius_chat.persona_config import (
    NapCatAdapterConfig,
    PersonaAdaptersConfig,
    PersonaConfigPaths,
    PersonaExperienceConfig,
)

LOG = logging.getLogger("sirius.persona_manager")


class PersonaManager:
    """管理所有人格的生命周期。"""

    def __init__(self, data_path: Path | str, global_config: dict[str, Any] | None = None) -> None:
        self.data_path = Path(data_path).resolve()
        self.data_path.mkdir(parents=True, exist_ok=True)
        self.personas_dir = self.data_path / "personas"
        self.personas_dir.mkdir(parents=True, exist_ok=True)
        self.global_config = dict(global_config or {})
        self._processes: dict[str, subprocess.Popen] = {}

    # ------------------------------------------------------------------
    # 扫描与列表
    # ------------------------------------------------------------------

    def list_personas(self) -> list[dict[str, Any]]:
        """扫描目录，返回所有人格的元信息列表。"""
        results: list[dict[str, Any]] = []
        if not self.personas_dir.exists():
            return results
        for subdir in sorted(self.personas_dir.iterdir()):
            if not subdir.is_dir():
                continue
            name = subdir.name
            info = self._inspect_persona(name)
            if info:
                results.append(info)
        return results

    def _inspect_persona(self, name: str) -> dict[str, Any] | None:
        """检查单个人格目录，返回元信息。"""
        pdir = self.personas_dir / name
        if not pdir.exists():
            return None

        paths = PersonaConfigPaths(pdir)
        persona = PersonaStore.load(pdir)
        adapters = PersonaAdaptersConfig.load(paths.adapters)
        experience = PersonaExperienceConfig.load(paths.experience)
        status = self._read_worker_status(name)

        # 是否已启用（至少有一个 adapter enabled）
        has_enabled_adapter = any(a.enabled for a in adapters.adapters)

        return {
            "name": name,
            "persona_name": persona.name if persona else None,
            "persona_summary": persona.persona_summary if persona else None,
            "adapters_count": len(adapters.adapters),
            "enabled": has_enabled_adapter,
            "running": self.is_running(name),
            "pid": status.get("pid") if status else None,
            "status": status.get("status") if status else "unknown",
            "heartbeat_at": status.get("heartbeat_at") if status else None,
            "work_path": str(pdir),
        }

    def _read_worker_status(self, name: str) -> dict[str, Any] | None:
        """读取子进程的心跳状态文件。"""
        path = self.personas_dir / name / "engine_state" / "worker_status.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            # 检查心跳是否过期（30 秒）
            heartbeat = data.get("heartbeat_at")
            if heartbeat:
                try:
                    hb = datetime.fromisoformat(heartbeat)
                    if (datetime.now(timezone.utc) - hb).total_seconds() > 30:
                        data["status"] = "stale"
                except Exception:
                    pass
            return data
        except Exception:
            return None

    # ------------------------------------------------------------------
    # 创建与删除
    # ------------------------------------------------------------------

    def create_persona(
        self,
        name: str,
        *,
        persona_name: str | None = None,
        keywords: list[str] | None = None,
        template: str = "default",
    ) -> Path:
        """创建新人格目录及默认配置。"""
        pdir = self.personas_dir / name
        if pdir.exists():
            raise FileExistsError(f"人格已存在: {name}")

        pdir.mkdir(parents=True)
        paths = PersonaConfigPaths(pdir)

        # 1. 生成人格定义
        persona_name = persona_name or name
        if keywords:
            persona = PersonaGenerator.from_keywords(persona_name, keywords)
        elif template == "default":
            persona = PersonaProfile(name=persona_name)
        else:
            persona = PersonaProfile(name=persona_name)

        PersonaStore.save(pdir, persona)

        # 2. 生成默认 adapter 配置
        adapters = PersonaAdaptersConfig.default()
        adapters.save(paths.adapters)

        # 3. 生成默认 experience 配置
        experience = PersonaExperienceConfig()
        experience.save(paths.experience)

        # 4. 生成默认 orchestration 配置
        orch = {
            "analysis_model": "gpt-4o-mini",
            "chat_model": "gpt-4o",
            "vision_model": "gpt-4o",
        }
        paths.orchestration.write_text(
            json.dumps(orch, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        LOG.info("人格已创建: %s @ %s", name, pdir)
        return pdir

    def remove_persona(self, name: str) -> bool:
        """删除人格（先停止进程，再删除目录）。"""
        pdir = self.personas_dir / name
        if not pdir.exists():
            return False

        self.stop_persona(name)
        try:
            shutil.rmtree(pdir)
            LOG.info("人格已删除: %s", name)
            return True
        except Exception as exc:
            LOG.error("删除人格失败 %s: %s", name, exc)
            return False

    # ------------------------------------------------------------------
    # 启动与停止
    # ------------------------------------------------------------------

    def start_persona(self, name: str) -> bool:
        """启动单个人格子进程。"""
        if self.is_running(name):
            LOG.warning("人格已在运行: %s", name)
            return True

        pdir = self.personas_dir / name
        if not pdir.exists():
            LOG.error("人格不存在: %s", name)
            return False

        cmd = [
            sys.executable,
            "-m",
            "sirius_chat.persona_worker",
            "--config",
            str(pdir),
            "--log-level",
            self.global_config.get("log_level", "INFO"),
        ]

        # 子进程日志输出到文件
        log_dir = pdir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "worker.log"
        stdout = open(log_file, "a", encoding="utf-8")

        # Windows 下需要 CREATE_NEW_PROCESS_GROUP 才能发送 SIGTERM
        kwargs: dict[str, Any] = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=stdout,
                stderr=subprocess.STDOUT,
                cwd=str(Path(__file__).resolve().parent.parent),
                **kwargs,
            )
            self._processes[name] = proc
            LOG.info("人格子进程已启动: %s (pid=%s)", name, proc.pid)
            return True
        except Exception as exc:
            LOG.error("启动人格失败 %s: %s", name, exc)
            return False

    def stop_persona(self, name: str, timeout: int = 10) -> bool:
        """停止单个人格子进程。"""
        proc = self._processes.get(name)
        if proc is None:
            # 可能没有 tracked，尝试通过状态文件推断 PID
            status = self._read_worker_status(name)
            pid = status.get("pid") if status else None
            if pid:
                try:
                    import os as _os
                    _os.kill(pid, signal.SIGTERM if sys.platform != "win32" else signal.CTRL_BREAK_EVENT)
                    LOG.info("已向孤儿进程发送终止信号: %s (pid=%s)", name, pid)
                    return True
                except Exception as exc:
                    LOG.warning("终止孤儿进程失败 %s: %s", name, exc)
            return False

        # 先发送 SIGTERM（Windows 用 CTRL_BREAK_EVENT）
        try:
            if sys.platform == "win32":
                proc.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                proc.send_signal(signal.SIGTERM)
        except Exception as exc:
            LOG.warning("发送终止信号失败 %s: %s", name, exc)

        # 等待退出
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            LOG.warning("人格子进程未在规定时间内退出，强制终止: %s", name)
            try:
                proc.kill()
                proc.wait(timeout=5)
            except Exception as exc:
                LOG.error("强制终止失败 %s: %s", name, exc)

        self._processes.pop(name, None)
        LOG.info("人格已停止: %s", name)
        return True

    def is_running(self, name: str) -> bool:
        """检查人格进程是否仍在运行。"""
        proc = self._processes.get(name)
        if proc is None:
            return False
        if proc.poll() is not None:
            # 进程已退出，清理记录
            self._processes.pop(name, None)
            return False
        return True

    def start_all(self) -> dict[str, bool]:
        """启动所有已启用的人格。"""
        results: dict[str, bool] = {}
        for info in self.list_personas():
            name = info["name"]
            if info.get("enabled") and not info.get("running"):
                results[name] = self.start_persona(name)
        return results

    def stop_all(self) -> None:
        """停止所有人格。"""
        for name in list(self._processes.keys()):
            self.stop_persona(name)

    def get_persona_dir(self, name: str) -> Path:
        """获取人格目录。"""
        return self.personas_dir / name

    def get_persona_paths(self, name: str) -> PersonaConfigPaths | None:
        """获取人格配置路径对象。"""
        pdir = self.personas_dir / name
        if not pdir.exists():
            return None
        return PersonaConfigPaths(pdir)

    # ------------------------------------------------------------------
    # WebUI 便捷接口
    # ------------------------------------------------------------------

    def get_persona_status(self, name: str) -> dict[str, Any] | None:
        """获取单个人格的完整状态（供 WebUI 使用）。"""
        info = self._inspect_persona(name)
        if info is None:
            return None

        # 追加详细配置
        paths = self.get_persona_paths(name)
        if paths:
            try:
                adapters = PersonaAdaptersConfig.load(paths.adapters)
                info["adapters"] = [a.to_dict() for a in adapters.adapters]
            except Exception:
                info["adapters"] = []

            try:
                experience = PersonaExperienceConfig.load(paths.experience)
                info["experience"] = experience.to_dict()
            except Exception:
                info["experience"] = {}

        return info

    def reload_persona(self, name: str) -> bool:
        """通知子进程重载配置（通过写入 reload 标志文件）。"""
        pdir = self.personas_dir / name
        flag = pdir / "engine_state" / "reload_requested"
        try:
            flag.parent.mkdir(parents=True, exist_ok=True)
            flag.write_text(str(time.time()), encoding="utf-8")
            return True
        except Exception as exc:
            LOG.warning("写入重载标志失败 %s: %s", name, exc)
            return False


__all__ = ["PersonaManager"]
