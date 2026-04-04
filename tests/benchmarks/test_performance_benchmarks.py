"""性能基准测试"""

from __future__ import annotations

import asyncio
import time

import pytest

from tests.integration.conftest import MockLLMProvider


class TestPerformanceBenchmarks:
    """性能基准测试"""

    @pytest.mark.asyncio
    async def test_provider_call_throughput(self):
        """测试 Provider 调用吞吐量"""
        provider = MockLLMProvider()
        call_count = 0
        start = time.time()
        
        while time.time() - start < 0.5:  # 运行0.5秒
            await provider.generate(
                [{"role": "user", "content": "throughput test"}]
            )
            call_count += 1
        
        elapsed = time.time() - start
        throughput = call_count / elapsed
        
        assert throughput > 50  # 至少每秒50次
        assert provider.call_count == call_count

    @pytest.mark.asyncio
    async def test_concurrent_throughput(self):
        """测试并发吞吐量"""
        provider = MockLLMProvider()
        
        async def batch_calls(num: int):
            tasks = [
                provider.generate([{"role": "user", "content": f"batch {i}"}])
                for i in range(num)
            ]
            await asyncio.gather(*tasks)
        
        start = time.time()
        await batch_calls(50)
        elapsed = time.time() - start
        
        throughput = 50 / elapsed
        assert throughput > 20
        assert provider.call_count == 50

    @pytest.mark.asyncio
    async def test_provider_latency(self):
        """测试 Provider 调用延迟"""
        provider = MockLLMProvider()
        latencies = []
        
        for _ in range(5):
            start = time.time()
            await provider.generate(
                [{"role": "user", "content": "latency test"}]
            )
            latency = (time.time() - start) * 1000  # 毫秒
            latencies.append(latency)
        
        avg_latency = sum(latencies) / len(latencies)
        
        # Mock 应该很快
        assert avg_latency < 100
