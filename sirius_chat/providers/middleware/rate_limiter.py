"""速率限制中间件"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from sirius_chat.providers.middleware.base import Middleware, MiddlewareContext


@dataclass(slots=True)
class RateLimiterMiddleware(Middleware):
    """
    速率限制中间件
    
    限制在时间窗口内的最大请求数
    """
    max_requests: int = 100  # 时间窗口内的最大请求数
    window_seconds: int = 60  # 时间窗口（秒）
    
    request_times: list[float] = field(default_factory=list, init=False)

    async def process_request(self, context: MiddlewareContext) -> None:
        """检查并应用速率限制"""
        now = time.time()
        
        # 清理过期的请求时间戳
        cutoff = now - self.window_seconds
        self.request_times = [t for t in self.request_times if t > cutoff]
        
        # 如果达到了限制，等待直到可以继续
        while len(self.request_times) >= self.max_requests:
            # 计算需要等待的时间
            oldest_request = self.request_times[0]
            wait_time = (oldest_request + self.window_seconds) - now
            
            if wait_time > 0:
                await asyncio.sleep(wait_time)
                now = time.time()
                self.request_times = [t for t in self.request_times if t > now - self.window_seconds]
            else:
                break
        
        # 记录当前请求
        self.request_times.append(now)
        context.metadata["rate_limited"] = False

    async def process_response(
        self,
        context: MiddlewareContext,
        response: str,
        error: Exception | None = None,
    ) -> str:
        """不需要处理响应"""
        return response


@dataclass(slots=True)
class TokenBucketRateLimiter(Middleware):
    """
    令牌桶算法的速率限制器
    
    更精细的速率控制
    """
    capacity: int = 10  # 桶容量
    refill_rate: float = 1.0  # 每秒补充的令牌数
    
    tokens: float = field(default=0, init=False)
    last_refill: float = field(default_factory=time.time, init=False)

    def _refill(self) -> None:
        """补充令牌"""
        now = time.time()
        elapsed = now - self.last_refill
        self.tokens = min(
            self.capacity,
            self.tokens + elapsed * self.refill_rate
        )
        self.last_refill = now

    async def process_request(self, context: MiddlewareContext) -> None:
        """获取令牌或等待"""
        while True:
            self._refill()
            
            if self.tokens >= 1:
                self.tokens -= 1
                break
            
            # 计算等待时间
            wait_time = (1 - self.tokens) / self.refill_rate
            await asyncio.sleep(wait_time)

    async def process_response(
        self,
        context: MiddlewareContext,
        response: str,
        error: Exception | None = None,
    ) -> str:
        """不需要处理响应"""
        return response
