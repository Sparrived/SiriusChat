"""Runtime contracts shared by the Tool and Plugin extension systems."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class BackgroundTaskSpec:
    """Describe a framework-managed periodic extension task."""

    name: str
    interval_seconds: float
    task_func: Callable[..., Awaitable[None]]

    async def run_loop(self, running_check: Callable[[], bool]) -> None:
        """Run ``task_func`` periodically until ``running_check`` is false."""
        while running_check():
            await asyncio.sleep(self.interval_seconds)
            if not running_check():
                break
            try:
                await self.task_func()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Background task '%s' failed", self.name)
