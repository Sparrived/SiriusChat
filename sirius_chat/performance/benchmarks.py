"""Benchmark utilities for performance testing."""

from __future__ import annotations

import asyncio
import statistics
import time
from dataclasses import dataclass
from typing import Any, Callable, Coroutine


@dataclass(slots=True)
class BenchmarkResult:
    """Result of a benchmark run."""
    
    name: str
    iterations: int
    total_time: float
    min_time: float
    max_time: float
    mean_time: float
    median_time: float
    stdev_time: float | None = None
    
    def __str__(self) -> str:
        """Format result as string."""
        lines = [
            f"\nBenchmark: {self.name}",
            f"  Iterations: {self.iterations}",
            f"  Total:      {self.total_time*1000:.2f} ms",
            f"  Mean:       {self.mean_time*1000:.2f} ms",
            f"  Median:     {self.median_time*1000:.2f} ms",
            f"  Min:        {self.min_time*1000:.2f} ms",
            f"  Max:        {self.max_time*1000:.2f} ms",
        ]
        if self.stdev_time is not None:
            lines.append(f"  StdDev:     {self.stdev_time*1000:.2f} ms")
        return "\n".join(lines)
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "name": self.name,
            "iterations": self.iterations,
            "total_ms": round(self.total_time * 1000, 2),
            "mean_ms": round(self.mean_time * 1000, 2),
            "median_ms": round(self.median_time * 1000, 2),
            "min_ms": round(self.min_time * 1000, 2),
            "max_ms": round(self.max_time * 1000, 2),
            "stdev_ms": round(self.stdev_time * 1000, 2) if self.stdev_time else None,
        }


class Benchmark:
    """Benchmark runner for performance testing."""
    
    @staticmethod
    def run_sync(
        func: Callable[..., Any],
        iterations: int = 100,
        *args: Any,
        **kwargs: Any,
    ) -> BenchmarkResult:
        """Run synchronous benchmark.
        
        Args:
            func: Function to benchmark
            iterations: Number of iterations
            *args: Positional arguments for func
            **kwargs: Keyword arguments for func
            
        Returns:
            BenchmarkResult
        """
        times: list[float] = []
        
        # Warmup
        for _ in range(max(1, iterations // 10)):
            func(*args, **kwargs)
        
        # Benchmark
        for _ in range(iterations):
            start = time.perf_counter()
            func(*args, **kwargs)
            end = time.perf_counter()
            times.append(end - start)
        
        return BenchmarkResult(
            name=f"{func.__module__}.{func.__name__}",
            iterations=iterations,
            total_time=sum(times),
            min_time=min(times),
            max_time=max(times),
            mean_time=statistics.mean(times),
            median_time=statistics.median(times),
            stdev_time=statistics.stdev(times) if len(times) > 1 else None,
        )
    
    @staticmethod
    async def run_async(
        func: Callable[..., Coroutine[Any, Any, Any]],
        iterations: int = 100,
        *args: Any,
        **kwargs: Any,
    ) -> BenchmarkResult:
        """Run asynchronous benchmark.
        
        Args:
            func: Async function to benchmark
            iterations: Number of iterations
            *args: Positional arguments for func
            **kwargs: Keyword arguments for func
            
        Returns:
            BenchmarkResult
        """
        times: list[float] = []
        
        # Warmup
        for _ in range(max(1, iterations // 10)):
            await func(*args, **kwargs)
        
        # Benchmark
        for _ in range(iterations):
            start = time.perf_counter()
            await func(*args, **kwargs)
            end = time.perf_counter()
            times.append(end - start)
        
        return BenchmarkResult(
            name=f"{func.__module__}.{func.__name__}",
            iterations=iterations,
            total_time=sum(times),
            min_time=min(times),
            max_time=max(times),
            mean_time=statistics.mean(times),
            median_time=statistics.median(times),
            stdev_time=statistics.stdev(times) if len(times) > 1 else None,
        )
    
    @staticmethod
    async def run_concurrent(
        func: Callable[..., Coroutine[Any, Any, Any]],
        concurrency: int = 10,
        iterations: int = 100,
        *args: Any,
        **kwargs: Any,
    ) -> BenchmarkResult:
        """Run concurrent async benchmark.
        
        Args:
            func: Async function to benchmark
            concurrency: Number of concurrent tasks
            iterations: Number of iterations per task
            *args: Positional arguments for func
            **kwargs: Keyword arguments for func
            
        Returns:
            BenchmarkResult with concurrent results
        """
        async def task() -> list[float]:
            times = []
            for _ in range(iterations):
                start = time.perf_counter()
                await func(*args, **kwargs)
                end = time.perf_counter()
                times.append(end - start)
            return times
        
        start = time.perf_counter()
        results = await asyncio.gather(*[task() for _ in range(concurrency)])
        end = time.perf_counter()
        
        all_times = [t for times in results for t in times]
        total_iterations = concurrency * iterations
        
        return BenchmarkResult(
            name=f"{func.__module__}.{func.__name__} (concurrent={concurrency})",
            iterations=total_iterations,
            total_time=end - start,
            min_time=min(all_times),
            max_time=max(all_times),
            mean_time=statistics.mean(all_times),
            median_time=statistics.median(all_times),
            stdev_time=statistics.stdev(all_times) if len(all_times) > 1 else None,
        )


class BenchmarkSuite:
    """Collection of benchmarks."""
    
    def __init__(self, name: str = "") -> None:
        """Initialize benchmark suite.
        
        Args:
            name: Suite name
        """
        self.name = name
        self.results: list[BenchmarkResult] = []
    
    def add_sync(
        self,
        func: Callable[..., Any],
        iterations: int = 100,
        *args: Any,
        **kwargs: Any,
    ) -> BenchmarkResult:
        """Add and run a synchronous benchmark.
        
        Args:
            func: Function to benchmark
            iterations: Number of iterations
            *args: Positional arguments
            **kwargs: Keyword arguments
            
        Returns:
            BenchmarkResult
        """
        result = Benchmark.run_sync(func, iterations, *args, **kwargs)
        self.results.append(result)
        return result
    
    async def add_async(
        self,
        func: Callable[..., Coroutine[Any, Any, Any]],
        iterations: int = 100,
        *args: Any,
        **kwargs: Any,
    ) -> BenchmarkResult:
        """Add and run an asynchronous benchmark.
        
        Args:
            func: Async function to benchmark
            iterations: Number of iterations
            *args: Positional arguments
            **kwargs: Keyword arguments
            
        Returns:
            BenchmarkResult
        """
        result = await Benchmark.run_async(func, iterations, *args, **kwargs)
        self.results.append(result)
        return result
    
    def report(self) -> str:
        """Generate benchmark report.
        
        Returns:
            Formatted report string
        """
        if not self.results:
            return "No benchmarks run"
        
        lines = []
        if self.name:
            lines.append(f"Benchmark Suite: {self.name}")
            lines.append("=" * 60)
        
        for result in self.results:
            lines.append(str(result))
        
        # Summary
        if len(self.results) > 1:
            lines.append("\n" + "=" * 60)
            lines.append("Summary")
            lines.append(f"  Total benchmarks: {len(self.results)}")
            
            total_iterations = sum(r.iterations for r in self.results)
            total_time = sum(r.total_time for r in self.results)
            
            lines.append(f"  Total iterations: {total_iterations}")
            lines.append(f"  Total time: {total_time*1000:.2f} ms")
        
        return "\n".join(lines)
