"""Tests for performance monitoring module."""

from __future__ import annotations

import asyncio
import time

import pytest

from sirius_chat.performance import (
    Benchmark,
    BenchmarkResult,
    BenchmarkSuite,
    ExecutionMetrics,
    MetricsCollector,
    MemoryProfiler,
    PerformanceProfiler,
    get_global_collector,
    profile_async,
    profile_sync,
    reset_global_collector,
)


class TestExecutionMetrics:
    """Test ExecutionMetrics class."""
    
    def test_creation(self) -> None:
        """Test creating metrics."""
        metric = ExecutionMetrics(name="test_operation")
        assert metric.name == "test_operation"
        assert metric.success is True
        assert metric.duration == 0.0
    
    def test_finish(self) -> None:
        """Test finishing metrics."""
        metric = ExecutionMetrics(name="test")
        time.sleep(0.01)
        metric.finish(1000, 2000)
        
        assert metric.duration > 0.009  # Should be at least 10ms
        assert metric.memory_delta == 1000


class TestMetricsCollector:
    """Test MetricsCollector class."""
    
    def test_record_metric(self) -> None:
        """Test recording metrics."""
        collector = MetricsCollector()
        
        metric1 = ExecutionMetrics(name="op1")
        metric1.finish(0, 1000)
        
        metric2 = ExecutionMetrics(name="op1")
        metric2.finish(0, 500)
        
        collector.record_metric(metric1)
        collector.record_metric(metric2)
        
        assert len(collector.metrics["op1"]) == 2
    
    def test_get_stats(self) -> None:
        """Test getting aggregated stats."""
        collector = MetricsCollector()
        
        for i in range(3):
            metric = ExecutionMetrics(name="test_op")
            metric.start_time = time.time()
            time.sleep(0.01)
            metric.finish(0, 1000 * (i + 1))
            collector.record_metric(metric)
        
        stats = collector.get_stats("test_op")
        
        assert stats["count"] == 3
        assert stats["failed"] == 0
        assert "avg_duration_ms" in stats
        assert "min_duration_ms" in stats
        assert "max_duration_ms" in stats
    
    def test_summary(self) -> None:
        """Test getting summary."""
        collector = MetricsCollector()
        
        metric = ExecutionMetrics(name="op1")
        metric.finish()
        collector.record_metric(metric)
        
        summary = collector.summary()
        assert summary["total_executions"] == 1
        assert summary["successful"] == 1
        assert summary["failed"] == 0


class TestPerformanceProfiler:
    """Test PerformanceProfiler context manager."""
    
    def test_basic_profiling(self) -> None:
        """Test basic profiling."""
        reset_global_collector()
        
        with PerformanceProfiler("test_block"):
            time.sleep(0.01)
        
        collector = get_global_collector()
        stats = collector.get_stats("test_block")
        
        assert stats["count"] == 1
        # Should be at least 10ms
        assert stats["avg_duration_ms"] >= 9
    
    def test_profiling_with_error(self) -> None:
        """Test profiling with error."""
        reset_global_collector()
        
        try:
            with PerformanceProfiler("failing_block"):
                raise ValueError("Test error")
        except ValueError:
            pass
        
        collector = get_global_collector()
        stats = collector.get_stats("failing_block")
        
        assert stats["failed"] == 1
        assert stats["count"] == 0  # No successful runs
    
    def test_profiling_memory(self) -> None:
        """Test memory tracking."""
        collector = MetricsCollector()
        
        metric = ExecutionMetrics(name="mem_test")
        metric.memory_start = 1000000
        metric.memory_end = 2000000
        metric.finish()
        
        assert metric.memory_delta == 1000000


class TestProfileDecorators:
    """Test profile decorators."""
    
    def test_sync_profile(self) -> None:
        """Test synchronous profile decorator."""
        reset_global_collector()
        
        @profile_sync("my_function")
        def slow_function():
            time.sleep(0.01)
            return "result"
        
        result = slow_function()
        assert result == "result"
        
        collector = get_global_collector()
        stats = collector.get_stats("my_function")
        assert stats["count"] == 1
    
    @pytest.mark.asyncio
    async def test_async_profile(self) -> None:
        """Test asynchronous profile decorator."""
        reset_global_collector()
        
        @profile_async("async_function")
        async def slow_async_function():
            await asyncio.sleep(0.01)
            return "async_result"
        
        result = await slow_async_function()
        assert result == "async_result"
        
        collector = get_global_collector()
        stats = collector.get_stats("async_function")
        assert stats["count"] == 1


class TestMemoryProfiler:
    """Test MemoryProfiler."""
    
    def test_take_snapshot(self) -> None:
        """Test taking memory snapshot."""
        profiler = MemoryProfiler()
        
        snapshot = profiler.take_snapshot("initial")
        
        assert "rss_mb" in snapshot
        assert "vms_mb" in snapshot
        assert snapshot["label"] == "initial"
    
    def test_memory_report(self) -> None:
        """Test memory report generation."""
        profiler = MemoryProfiler()
        
        profiler.take_snapshot("start")
        time.sleep(0.01)
        profiler.take_snapshot("middle")
        
        report = profiler.get_report()
        
        assert "Memory Usage Report" in report
        assert "start" in report
        assert "middle" in report


class TestBenchmark:
    """Test Benchmark class."""
    
    def test_sync_benchmark(self) -> None:
        """Test synchronous benchmark."""
        def fibonacci(n: int) -> int:
            if n <= 1:
                return n
            return fibonacci(n - 1) + fibonacci(n - 2)
        
        result = Benchmark.run_sync(fibonacci, 10, 20)
        
        assert isinstance(result, BenchmarkResult)
        assert result.iterations == 10
        assert result.mean_time > 0
        assert result.min_time <= result.max_time
    
    @pytest.mark.asyncio
    async def test_async_benchmark(self) -> None:
        """Test asynchronous benchmark."""
        async def async_operation():
            await asyncio.sleep(0.001)
        
        result = await Benchmark.run_async(async_operation, iterations=10)
        
        assert result.iterations == 10
        assert result.mean_time > 0.0009  # Should be close to 1ms
    
    @pytest.mark.asyncio
    async def test_concurrent_benchmark(self) -> None:
        """Test concurrent benchmark."""
        async def async_task():
            await asyncio.sleep(0.001)
        
        result = await Benchmark.run_concurrent(
            async_task, concurrency=5, iterations=2
        )
        
        assert result.iterations == 10  # 5 concurrent * 2 iterations
    
    def test_benchmark_result_dict(self) -> None:
        """Test benchmark result to dict conversion."""
        result = BenchmarkResult(
            name="test",
            iterations=100,
            total_time=0.1,
            min_time=0.0009,
            max_time=0.0011,
            mean_time=0.001,
            median_time=0.001,
        )
        
        result_dict = result.to_dict()
        
        assert result_dict["name"] == "test"
        assert result_dict["iterations"] == 100
        assert "mean_ms" in result_dict


class TestBenchmarkSuite:
    """Test BenchmarkSuite."""
    
    def test_suite_creation(self) -> None:
        """Test creating benchmark suite."""
        suite = BenchmarkSuite("test_suite")
        assert suite.name == "test_suite"
        assert len(suite.results) == 0
    
    def test_add_sync_benchmark(self) -> None:
        """Test adding synchronous benchmark."""
        suite = BenchmarkSuite()
        
        def simple_func():
            return sum(range(100))
        
        result = suite.add_sync(simple_func, iterations=5)
        
        assert len(suite.results) == 1
        assert result.iterations == 5
    
    def test_suite_report(self) -> None:
        """Test generating suite report."""
        suite = BenchmarkSuite("test_suite")
        
        def func1():
            pass
        
        def func2():
            pass
        
        suite.add_sync(func1, iterations=5)
        suite.add_sync(func2, iterations=5)
        
        report = suite.report()
        
        assert "test_suite" in report
        assert "Summary" in report
        assert "Total benchmarks: 2" in report
