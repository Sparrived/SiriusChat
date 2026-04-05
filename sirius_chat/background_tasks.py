"""
后台任务管理器 - 用于管理异步定时任务（内存压缩、数据清理等）

特点：
- 轻量级（不依赖APScheduler）
- 基于asyncio的异步实现
- 支持优雅关闭
- 可配置的触发间隔
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class BackgroundTaskConfig:
    """后台任务配置"""
    
    # 内存压缩配置
    compression_enabled: bool = True
    compression_interval_seconds: int = 3600  # 1小时
    compression_min_facts: int = 60  # 超过60个facts时触发
    compression_similarity_threshold: float = 0.8
    
    # 临时数据清理配置
    cleanup_enabled: bool = True
    cleanup_interval_seconds: int = 1800  # 30分钟
    cleanup_transient_max_age_minutes: int = 30
    
    # 是否启用日志
    verbose_logging: bool = False


class BackgroundTaskManager:
    """
    轻量级后台任务管理器
    
    用于运行异步定时任务，如内存压缩、数据清理等。
    基于asyncio.create_task，不引入额外依赖。
    """
    
    def __init__(
        self,
        config: BackgroundTaskConfig | None = None,
    ):
        self.config = config or BackgroundTaskConfig()
        self.tasks: dict[str, asyncio.Task] = {}
        self._running = False
        self._memory_compressor_callback: Optional[Callable[[str], None]] = None
        self._transient_cleanup_callback: Optional[Callable[[str], None]] = None
    
    def set_memory_compressor_callback(
        self, 
        callback: Callable[[str], None]
    ) -> None:
        """设置内存压缩回调函数。
        
        回调函数签名: callback(user_id: str) -> None
        """
        self._memory_compressor_callback = callback
    
    def set_transient_cleanup_callback(
        self,
        callback: Callable[[str], None]
    ) -> None:
        """设置临时数据清理回调函数。
        
        回调函数签名: callback(user_id: str) -> None
        """
        self._transient_cleanup_callback = callback
    
    async def start(self) -> None:
        """启动所有启用的后台任务"""
        if self._running:
            logger.warning("BackgroundTaskManager already running")
            return
        
        self._running = True
        logger.info("Starting background task manager")
        
        if self.config.compression_enabled:
            task = asyncio.create_task(
                self._memory_compression_loop(),
                name="memory_compression"
            )
            self.tasks["memory_compression"] = task
        
        if self.config.cleanup_enabled:
            task = asyncio.create_task(
                self._transient_cleanup_loop(),
                name="transient_cleanup"
            )
            self.tasks["transient_cleanup"] = task
    
    async def stop(self) -> None:
        """停止所有后台任务"""
        if not self._running:
            return
        
        self._running = False
        logger.info("Stopping background task manager")
        
        # 取消所有任务
        for task_name, task in self.tasks.items():
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    logger.debug(f"Task {task_name} cancelled")
        
        self.tasks.clear()
    
    async def _memory_compression_loop(self) -> None:
        """内存压缩定时任务循环"""
        interval = self.config.compression_interval_seconds
        
        try:
            while self._running:
                await asyncio.sleep(interval)
                
                if not self._running:
                    break
                
                if self.config.verbose_logging:
                    logger.debug("Memory compression task triggered")
                
                # 这个是占位符，实际压缩在UserMemoryManager中实现
                # 这里只负责定时调用和日志
                if self._memory_compressor_callback:
                    try:
                        self._memory_compressor_callback("all_users")
                    except Exception as e:
                        logger.error(f"Error in memory compression: {e}", exc_info=True)
        
        except asyncio.CancelledError:
            logger.debug("Memory compression loop cancelled")
            raise
    
    async def _transient_cleanup_loop(self) -> None:
        """临时数据清理定时任务循环"""
        interval = self.config.cleanup_interval_seconds
        
        try:
            while self._running:
                await asyncio.sleep(interval)
                
                if not self._running:
                    break
                
                if self.config.verbose_logging:
                    logger.debug("Transient cleanup task triggered")
                
                # 这个是占位符，实际清理在UserMemoryManager中实现
                if self._transient_cleanup_callback:
                    try:
                        self._transient_cleanup_callback("all_users")
                    except Exception as e:
                        logger.error(f"Error in transient cleanup: {e}", exc_info=True)
        
        except asyncio.CancelledError:
            logger.debug("Transient cleanup loop cancelled")
            raise
    
    async def trigger_compression_now(self, user_id: str = "all_users") -> None:
        """立即触发一次内存压缩"""
        if self._memory_compressor_callback:
            try:
                self._memory_compressor_callback(user_id)
            except Exception as e:
                logger.error(f"Error triggering compression: {e}", exc_info=True)
    
    async def trigger_cleanup_now(self, user_id: str = "all_users") -> None:
        """立即触发一次临时数据清理"""
        if self._transient_cleanup_callback:
            try:
                self._transient_cleanup_callback(user_id)
            except Exception as e:
                logger.error(f"Error triggering cleanup: {e}", exc_info=True)
    
    def is_running(self) -> bool:
        """检查后台任务是否在运行"""
        return self._running


__all__ = [
    "BackgroundTaskConfig",
    "BackgroundTaskManager",
]
