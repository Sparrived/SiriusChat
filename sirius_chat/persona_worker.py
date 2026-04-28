"""人格工作进程 — 单个人格的独立运行入口。

职责：
- 加载人格级配置（persona.json / orchestration.json / adapters.json / experience.json）
- 创建 EngineRuntime + NapCatBridge + NapCatAdapter
- 运行事件循环，定期写入心跳
- 响应 SIGTERM 优雅退出

启动方式（由 PersonaManager 调用）::

    python -m sirius_chat.persona_worker --config data/personas/akane
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Any

from sirius_chat.logging_config import configure_logging
from sirius_chat.persona_config import (
    NapCatAdapterConfig,
    PersonaAdaptersConfig,
    PersonaConfigPaths,
    PersonaExperienceConfig,
)
from sirius_chat.platforms.napcat_adapter import NapCatAdapter
from sirius_chat.platforms.napcat_bridge import NapCatBridge
from sirius_chat.platforms.runtime import EngineRuntime

LOG = logging.getLogger("sirius.persona_worker")


class PersonaWorker:
    """单个人格的运行时封装。"""

    def __init__(self, persona_dir: Path | str) -> None:
        self.persona_dir = Path(persona_dir).resolve()
        self.paths = PersonaConfigPaths(self.persona_dir)
        self._adapters: list[NapCatAdapter] = []
        self._bridges: list[NapCatBridge] = []
        self._runtime: EngineRuntime | None = None
        self._running = False
        self._shutdown_event = asyncio.Event()
        self._heartbeat_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------

    async def run(self) -> None:
        LOG.info("启动人格工作进程: %s", self.persona_dir.name)

        # 1. 加载配置
        adapters_cfg = PersonaAdaptersConfig.load(self.paths.adapters)
        experience = PersonaExperienceConfig.load(self.paths.experience)
        LOG.info("加载 %d 个 adapter，体验模式: %s", len(adapters_cfg.adapters), experience.memory_depth)

        # 2. 创建 EngineRuntime（experience 参数注入 plugin_config）
        plugin_config = self._build_plugin_config(experience)
        self._runtime = EngineRuntime(self.persona_dir, plugin_config=plugin_config)

        # 3. 启动引擎
        await self._runtime.start()

        # 4. 创建并启动各平台 Adapter
        for adapter_cfg in adapters_cfg.adapters:
            if not adapter_cfg.enabled:
                LOG.info("跳过 disabled adapter: %s", getattr(adapter_cfg, "type", "?"))
                continue
            await self._start_adapter(adapter_cfg, plugin_config)

        # 5. 启动心跳
        self._running = True
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        self._write_status({"status": "running", "pid": os.getpid(), "started_at": _now_iso()})

        LOG.info("人格「%s」已就绪，等待消息...", self.persona_dir.name)

        # 6. 阻塞等待关闭信号
        await self._shutdown_event.wait()

        # 7. 清理
        await self._cleanup()

    # ------------------------------------------------------------------
    # Adapter 启动
    # ------------------------------------------------------------------

    async def _start_adapter(
        self,
        adapter_cfg: Any,
        plugin_config: dict[str, Any],
    ) -> None:
        if isinstance(adapter_cfg, NapCatAdapterConfig):
            adapter = NapCatAdapter(
                ws_url=adapter_cfg.ws_url,
                token=adapter_cfg.token or None,
            )
            bridge_config: dict[str, Any] = {
                "root": adapter_cfg.root,
                "allowed_group_ids": adapter_cfg.allowed_group_ids,
                "allowed_private_user_ids": adapter_cfg.allowed_private_user_ids,
                "enable_group_chat": adapter_cfg.enable_group_chat,
                "enable_private_chat": adapter_cfg.enable_private_chat,
                "auto_install_skill_deps": plugin_config.get("auto_install_skill_deps", True),
            }
            bridge = NapCatBridge(
                adapter=adapter,
                work_path=self.persona_dir,
                config=bridge_config,
            )
            await adapter.connect()
            await bridge.start()
            self._adapters.append(adapter)
            self._bridges.append(bridge)
            LOG.info("NapCat adapter 已启动: %s", adapter_cfg.ws_url)
        else:
            LOG.warning("未知 adapter 类型，已跳过: %s", type(adapter_cfg).__name__)

    # ------------------------------------------------------------------
    # 配置转换
    # ------------------------------------------------------------------

    @staticmethod
    def _build_plugin_config(experience: PersonaExperienceConfig) -> dict[str, Any]:
        """将体验参数转换为 EngineRuntime 的 plugin_config。"""
        return {
            # 参与决策
            "sensitivity": experience.engagement_sensitivity,
            "reply_cooldown_seconds": int(experience.min_reply_interval_seconds),
            # 技能
            "max_skill_rounds": experience.max_skill_rounds,
            "auto_install_skill_deps": experience.auto_install_skill_deps,
            # 后台任务
            "delayed_queue_tick_interval_seconds": 3,
            "proactive_check_interval_seconds": int(experience.proactive_interval_seconds),
            "proactive_silence_minutes": max(1, int(experience.proactive_interval_seconds / 60)),
            # 其他体验参数直接透传（Bridge 可能用到）
            "reply_mode": experience.reply_mode,
            "proactive_enabled": experience.proactive_enabled,
            "delay_reply_enabled": experience.delay_reply_enabled,
            "pending_message_threshold": experience.pending_message_threshold,
            "reply_frequency_max_replies": experience.reply_frequency_max_replies,
            "reply_frequency_exempt_on_mention": experience.reply_frequency_exempt_on_mention,
            "max_concurrent_llm_calls": experience.max_concurrent_llm_calls,
            "enable_skills": experience.enable_skills,
            "skill_execution_timeout": experience.skill_execution_timeout,
            "memory_depth": experience.memory_depth,
        }

    # ------------------------------------------------------------------
    # 心跳与状态
    # ------------------------------------------------------------------

    async def _heartbeat_loop(self) -> None:
        while self._running:
            self._write_status({
                "status": "running",
                "pid": os.getpid(),
                "heartbeat_at": _now_iso(),
            })
            self._check_enabled_flag()
            await asyncio.sleep(10)

    def _check_enabled_flag(self) -> None:
        """读取 engine_state/enabled 标志，同步到各 Bridge。"""
        flag = self.paths.engine_state / "enabled"
        if not flag.exists():
            return
        try:
            text = flag.read_text(encoding="utf-8").strip()
            enabled = text == "1"
            for bridge in self._bridges:
                if hasattr(bridge, "_enabled") and bridge._enabled != enabled:
                    bridge._enabled = enabled
                    LOG.info("Bridge %s 已%s", bridge, "启用" if enabled else "禁用")
        except Exception:
            pass

    def _write_status(self, status: dict[str, Any]) -> None:
        try:
            path = self.paths.engine_state / "worker_status.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)
        except Exception as exc:
            LOG.debug("状态写入失败: %s", exc)

    # ------------------------------------------------------------------
    # 关闭与清理
    # ------------------------------------------------------------------

    def shutdown(self) -> None:
        """触发优雅关闭（可在信号处理器中调用）。"""
        LOG.info("收到关闭信号，正在停止人格工作进程...")
        self._running = False
        self._shutdown_event.set()

    async def _cleanup(self) -> None:
        LOG.info("开始清理资源...")

        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass

        for bridge in self._bridges:
            try:
                await bridge.stop()
            except Exception as exc:
                LOG.warning("Bridge 停止失败: %s", exc)

        for adapter in self._adapters:
            try:
                await adapter.close()
            except Exception as exc:
                LOG.warning("Adapter 关闭失败: %s", exc)

        if self._runtime is not None:
            try:
                await self._runtime.stop()
            except Exception as exc:
                LOG.warning("EngineRuntime 停止失败: %s", exc)

        self._write_status({
            "status": "stopped",
            "pid": os.getpid(),
            "stopped_at": _now_iso(),
        })
        LOG.info("人格工作进程已停止")


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


async def _main() -> None:
    parser = argparse.ArgumentParser(description="SiriusChat 人格工作进程")
    parser.add_argument("--config", required=True, help="人格配置目录路径")
    parser.add_argument("--log-level", default="INFO", help="日志级别")
    args = parser.parse_args()

    pdir = Path(args.config).resolve()
    log_file = pdir / "logs" / "worker.log"
    configure_logging(
        level=args.log_level.upper(),
        format_type="console",
        log_file=str(log_file),
    )

    worker = PersonaWorker(args.config)

    # 信号处理（Windows 不支持 loop.add_signal_handler）
    if sys.platform == "win32":
        import signal as _signal

        def _sig_handler(_signum, _frame):
            worker.shutdown()

        _signal.signal(_signal.SIGINT, _sig_handler)
        _signal.signal(_signal.SIGTERM, _sig_handler)
    else:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, worker.shutdown)

    try:
        await worker.run()
    except Exception:
        LOG.exception("人格工作进程异常退出")
        raise


if __name__ == "__main__":
    asyncio.run(_main())
