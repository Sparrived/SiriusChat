"""Performance monitoring and analysis tools.

Provides profiling, metrics collection, and benchmarking utilities for
identifying and optimizing performance bottlenecks.
"""

from __future__ import annotations

from sirius_chat.performance.benchmarks import (
    Benchmark,
    BenchmarkResult,
    BenchmarkSuite,
)
from sirius_chat.performance.metrics import (
    ExecutionMetrics,
    MetricsCollector,
    get_global_collector,
    reset_global_collector,
)
from sirius_chat.performance.profiler import (
    CPUProfiler,
    MemoryProfiler,
    PerformanceProfiler,
    profile_async,
    profile_sync,
)

__all__ = [
    # Metrics
    "ExecutionMetrics",
    "MetricsCollector",
    "get_global_collector",
    "reset_global_collector",
    # Profiler
    "PerformanceProfiler",
    "profile_sync",
    "profile_async",
    "CPUProfiler",
    "MemoryProfiler",
    # Benchmarks
    "Benchmark",
    "BenchmarkResult",
    "BenchmarkSuite",
]
