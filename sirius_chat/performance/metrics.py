"""Performance metrics collection and tracking."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class ExecutionMetrics:
    """Metrics for a single execution."""
    
    name: str
    start_time: float = field(default_factory=time.time)
    end_time: float = 0.0
    duration: float = 0.0
    memory_start: int = 0
    memory_end: int = 0
    memory_delta: int = 0
    iterations: int = 1
    success: bool = True
    error: str | None = None
    
    def finish(self, memory_start: int = 0, memory_end: int = 0) -> None:
        """Mark execution as finished and calculate metrics."""
        self.end_time = time.time()
        self.duration = self.end_time - self.start_time
        # Only update memory if not already set
        if not self.memory_start:
            self.memory_start = memory_start
        if not self.memory_end:
            self.memory_end = memory_end
        self.memory_delta = self.memory_end - self.memory_start if self.memory_end and self.memory_start else 0
    
    def to_dict(self) -> dict[str, Any]:
        """Convert metrics to dictionary."""
        return {
            "name": self.name,
            "duration_ms": round(self.duration * 1000, 2),
            "duration_s": round(self.duration, 3),
            "memory_delta_kb": round(self.memory_delta / 1024, 2) if self.memory_delta else 0,
            "iterations": self.iterations,
            "success": self.success,
            "error": self.error,
        }


class MetricsCollector:
    """Collect and aggregate performance metrics."""
    
    def __init__(self) -> None:
        """Initialize metrics collector."""
        self.metrics: dict[str, list[ExecutionMetrics]] = {}
        self.start_time = datetime.now()
    
    def record_metric(self, metric: ExecutionMetrics) -> None:
        """Record a single execution metric.
        
        Args:
            metric: ExecutionMetrics instance
        """
        if metric.name not in self.metrics:
            self.metrics[metric.name] = []
        self.metrics[metric.name].append(metric)
    
    def get_stats(self, name: str) -> dict[str, Any]:
        """Get aggregated statistics for a metric name.
        
        Args:
            name: Metric name
            
        Returns:
            Dictionary with aggregated stats
        """
        if name not in self.metrics or not self.metrics[name]:
            return {}
        
        metrics = self.metrics[name]
        successful = [m for m in metrics if m.success]
        failed_count = len(metrics) - len(successful)
        
        # Return basic stats even without successful executions
        if not successful:
            return {
                "name": name,
                "count": 0,
                "failed": failed_count,
            }
        
        durations = [m.duration for m in successful]
        memory_deltas = [m.memory_delta for m in successful if m.memory_delta]
        
        return {
            "name": name,
            "count": len(successful),
            "failed": failed_count,
            "total_duration_s": round(sum(durations), 3),
            "avg_duration_ms": round(sum(durations) / len(successful) * 1000, 2),
            "min_duration_ms": round(min(durations) * 1000, 2),
            "max_duration_ms": round(max(durations) * 1000, 2),
            "avg_memory_delta_kb": round(sum(memory_deltas) / len(memory_deltas), 2) if memory_deltas else None,
            "total_iterations": sum(m.iterations for m in successful),
        }
    
    def get_all_stats(self) -> dict[str, dict[str, Any]]:
        """Get aggregated statistics for all metrics.
        
        Returns:
            Dictionary mapping metric names to their stats
        """
        return {name: self.get_stats(name) for name in self.metrics}
    
    def clear(self) -> None:
        """Clear all collected metrics."""
        self.metrics.clear()
    
    def summary(self) -> dict[str, Any]:
        """Get a summary of all metrics.
        
        Returns:
            Summary dictionary
        """
        total_metrics = sum(len(m) for m in self.metrics.values())
        total_success = sum(
            len([x for x in m if x.success]) for m in self.metrics.values()
        )
        
        return {
            "total_executions": total_metrics,
            "successful": total_success,
            "failed": total_metrics - total_success,
            "metrics_tracked": len(self.metrics),
            "elapsed_time_s": round((datetime.now() - self.start_time).total_seconds(), 2),
        }


# Global collector instance
_global_collector = MetricsCollector()


def get_global_collector() -> MetricsCollector:
    """Get the global metrics collector instance.
    
    Returns:
        Global MetricsCollector
    """
    return _global_collector


def reset_global_collector() -> None:
    """Reset the global metrics collector."""
    _global_collector.clear()
