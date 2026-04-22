"""Provider 中间件集成测试"""

from __future__ import annotations

import asyncio

import pytest

from sirius_chat import (
    Message,
    MockProvider,
    Participant,
    SessionConfig,
    User,
)
from sirius_chat.providers.middleware import (
    CircuitBreakerMiddleware,
    CostMetricsMiddleware,
    MiddlewareChain,
    MiddlewareContext,
    RateLimiterMiddleware,
    RetryMiddleware,
)


class TestMiddlewareIntegration:
    """中间件与 Provider 集成测试"""

    @pytest.mark.asyncio
    async def test_middleware_chain_with_provider(self):
        """测试中间件链与 Provider 集成"""
        # 创建中间件链
        chain = MiddlewareChain()
        chain.add(RateLimiterMiddleware(max_requests=5, window_seconds=1))
        chain.add(RetryMiddleware(max_retries=2))
        chain.add(CostMetricsMiddleware())
        
        # 模拟请求
        from sirius_chat.providers.base import GenerationRequest
        
        request = GenerationRequest(
            model="gpt-3.5-turbo",
            system_prompt="You are a helpful assistant",
            messages=[{"role": "user", "content": "Test prompt"}],
        )
        
        context = MiddlewareContext(request=request, metadata={})
        
        # 执行中间件处理
        await chain.execute_request(context)
        
        # 验证中间件链处理成功
        assert "retry_count" in context.metadata
        
    @pytest.mark.asyncio
    async def test_rate_limiting_enforcement(self):
        """测试速率限制强制"""
        limiter = RateLimiterMiddleware(max_requests=2, window_seconds=1)
        
        from sirius_chat.providers.base import GenerationRequest
        
        request = GenerationRequest(
            model="gpt-3.5-turbo",
            system_prompt="You are a helpful assistant",
            messages=[{"role": "user", "content": "Test"}],
        )
        
        context = MiddlewareContext(request=request, metadata={})
        
        # 第一次请求
        await limiter.process_request(context)
        assert len(limiter.request_times) == 1
        
        # 第二次请求
        await limiter.process_request(context)
        assert len(limiter.request_times) == 2
    
    @pytest.mark.asyncio
    async def test_cost_tracking_across_calls(self):
        """测试跨多个调用的成本跟踪"""
        metrics = CostMetricsMiddleware()
        
        from sirius_chat.providers.base import GenerationRequest
        
        requests_data = [
            ("gpt-3.5-turbo", "short prompt"),
            ("gpt-4", "a much longer prompt that contains more tokens" * 5),
        ]
        
        for model, prompt in requests_data:
            request = GenerationRequest(
                model=model,
                system_prompt="You are a helpful assistant",
                messages=[{"role": "user", "content": prompt}],
            )
            
            context = MiddlewareContext(request=request, metadata={"request": request})
            
            await metrics.process_request(context)
            response = "response text" * 10
            await metrics.process_response(context, response, None)
        
        # 验证跟踪
        assert metrics.total_calls == 2
        assert metrics.total_cost > 0
        report = metrics.get_metrics()
        assert report["total_calls"] == 2
    
    @pytest.mark.asyncio
    async def test_circuit_breaker_activation(self):
        """测试断路器激活"""
        breaker = CircuitBreakerMiddleware(failure_threshold=2, timeout=1.0)
        
        from sirius_chat.providers.base import GenerationRequest
        
        request = GenerationRequest(
            model="gpt-3.5-turbo",
            system_prompt="You are a helpful assistant",
            messages=[{"role": "user", "content": "test"}],
        )
        
        context = MiddlewareContext(request=request, metadata={})
        
        # 模拟两个失败
        error = RuntimeError("Provider error")
        await breaker.process_response(context, "", error)
        await breaker.process_response(context, "", error)
        
        # 现在断路器应该打开
        with pytest.raises(CircuitBreakerMiddleware.CircuitOpen):
            await breaker.process_request(context)


class TestMiddlewareComposition:
    """中间件组合测试"""
    
    @pytest.mark.asyncio
    async def test_multiple_middleware_in_chain(self):
        """测试多个中间件在链中的组合"""
        chain = MiddlewareChain()
        
        # 添加多个中间件
        chain.add(RateLimiterMiddleware(max_requests=10))
        chain.add(RetryMiddleware(max_retries=3))
        chain.add(CircuitBreakerMiddleware(failure_threshold=5))
        chain.add(CostMetricsMiddleware())
        
        assert len(chain.middlewares) == 4
        
        from sirius_chat.providers.base import GenerationRequest
        
        request = GenerationRequest(
            model="gpt-3.5-turbo",
            system_prompt="You are a helpful assistant",
            messages=[{"role": "user", "content": "test"}],
        )
        
        context = MiddlewareContext(request=request, metadata={"request": request})
        
        # 执行整个链
        await chain.execute_request(context)
        
        # 验证所有中间件都被执行
        assert context.metadata.get("retry_count") == 0
