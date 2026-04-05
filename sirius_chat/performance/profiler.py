"""Performance profiler for tracking execution time and resource usage."""

from __future__ import annotations

import asyncio
import functools
import logging
import psutil
import time
from typing import Any, Callable, TypeVar

from sirius_chat.performance.metrics import ExecutionMetrics, get_global_collector

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


class PerformanceProfiler:
    """Context manager for profiling code blocks."""
    
    def __init__(self, name: str, track_memory: bool = True) -> None:
        """Initialize profiler.
        
        Args:
            name: Name of the code block being profiled
            track_memory: Whether to track memory usage
        """
        self.name = name
        self.track_memory = track_memory
        self.metric: ExecutionMetrics | None = None
        self.process = psutil.Process()
        self.memory_start = 0
        self.memory_end = 0
    
    def __enter__(self) -> PerformanceProfiler:
        """Enter context."""
        self.metric = ExecutionMetrics(name=self.name)
        
        if self.track_memory:
            try:
                mem_info = self.process.memory_info()
                self.memory_start = mem_info.rss
                self.metric.memory_start = self.memory_start
            except Exception as e:
                logger.warning(f"无法获取内存信息：{e}")
        
        return self
    
    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Exit context."""
        if self.metric is None:
            return
        
        if exc_type is not None:
            self.metric.success = False
            self.metric.error = str(exc_val)
        
        if self.track_memory:
            try:
                mem_info = self.process.memory_info()
                self.memory_end = mem_info.rss
                self.metric.memory_end = self.memory_end
            except Exception as e:
                logger.warning(f"无法获取内存信息：{e}")
        
        self.metric.finish()
        get_global_collector().record_metric(self.metric)
        
        # Log the metric
        logger.debug(
            f"[PROFILE] {self.name}: {self.metric.duration*1000:.2f}ms, "
            f"memory: {self.metric.memory_delta/1024:.1f}KB"
        )


def profile_sync(name: str | None = None, track_memory: bool = True) -> Callable[[F], F]:
    """Decorator for profiling synchronous functions.
    
    Args:
        name: Optional custom name for the profile
        track_memory: Whether to track memory usage
        
    Returns:
        Decorated function
    """
    def decorator(func: F) -> F:
        func_name = name or f"{func.__module__}.{func.__qualname__}"
        
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with PerformanceProfiler(func_name, track_memory=track_memory):
                return func(*args, **kwargs)
        
        return wrapper  # type: ignore
    
    return decorator


def profile_async(name: str | None = None, track_memory: bool = True) -> Callable[[F], F]:
    """Decorator for profiling asynchronous functions.
    
    Args:
        name: Optional custom name for the profile
        track_memory: Whether to track memory usage
        
    Returns:
        Decorated function
    """
    def decorator(func: F) -> F:
        func_name = name or f"{func.__module__}.{func.__qualname__}"
        
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            with PerformanceProfiler(func_name, track_memory=track_memory):
                return await func(*args, **kwargs)
        
        return wrapper  # type: ignore
    
    return decorator


class CPUProfiler:
    """Simple CPU time tracker using cProfile."""
    
    def __init__(self) -> None:
        """Initialize CPU profiler."""
        try:
            import cProfile
            self.profiler = cProfile.Profile()
        except ImportError:
            self.profiler = None
    
    def start(self) -> None:
        """Start CPU profiling."""
        if self.profiler is not None:
            self.profiler.enable()
    
    def stop(self) -> None:
        """Stop CPU profiling."""
        if self.profiler is not None:
            self.profiler.disable()
    
    def get_stats(self, top_n: int = 10) -> str:
        """Get profiling statistics.
        
        Args:
            top_n: Number of top functions to show
            
        Returns:
            Formatted stats string
        """
        if self.profiler is None:
            return "cProfile not available"
        
        try:
            import io
            import pstats
            
            stream = io.StringIO()
            stats = pstats.Stats(self.profiler, stream=stream)
            stats.sort_stats("cumulative")
            stats.print_stats(top_n)
            return stream.getvalue()
        except Exception as e:
            return f"Error getting stats: {e}"


class MemoryProfiler:
    """Simple memory usage tracker."""
    
    def __init__(self) -> None:
        """Initialize memory profiler."""
        self.process = psutil.Process()
        self.snapshots: list[dict[str, Any]] = []
    
    def take_snapshot(self, label: str = "") -> dict[str, Any]:
        """Take a memory snapshot.
        
        Args:
            label: Optional label for the snapshot
            
        Returns:
            Memory info dictionary
        """
        try:
            mem_info = self.process.memory_info()
            snapshot = {
                "timestamp": time.time(),
                "label": label,
                "rss_mb": round(mem_info.rss / 1024 / 1024, 2),
                "vms_mb": round(mem_info.vms / 1024 / 1024, 2),
            }
            self.snapshots.append(snapshot)
            return snapshot
        except Exception as e:
            logger.warning(f"无法获取内存快照：{e}")
            return {}
    
    def get_report(self) -> str:
        """Get memory usage report.
        
        Returns:
            Formatted memory report
        """
        if not self.snapshots:
            return "No snapshots taken"
        
        lines = ["Memory Usage Report:", "─" * 50]
        
        for snap in self.snapshots:
            label = f" [{snap['label']}]" if snap["label"] else ""
            lines.append(
                f"RSS: {snap['rss_mb']:>7} MB | VMS: {snap['vms_mb']:>7} MB{label}"
            )
        
        # Calculate deltas
        if len(self.snapshots) > 1:
            lines.append("─" * 50)
            first = self.snapshots[0]
            for snap in self.snapshots[1:]:
                delta_rss = snap["rss_mb"] - first["rss_mb"]
                delta_vms = snap["vms_mb"] - first["vms_mb"]
                label = f" [{snap['label']}]" if snap["label"] else ""
                delta_str = f"(Δ{delta_rss:+.1f} MB / {delta_vms:+.1f} MB)"
                lines.append(f"{delta_str}{label}")
        
        return "\n".join(lines)
    
    def clear(self) -> None:
        """Clear all snapshots."""
        self.snapshots.clear()
