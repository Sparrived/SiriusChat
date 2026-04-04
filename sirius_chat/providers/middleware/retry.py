"""重试中间件"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from sirius_chat.exceptions import ProviderConnectionError, ProviderResponseError
from sirius_chat.providers.middleware.base import Middleware, MiddlewareContext


@dataclass(slots=True)
class RetryMiddleware(Middleware):
    """
    统一的重试策略中间件
    
    支持指数退避和最大重试次数
    """
    max_retries: int = 3  # 最大重试次数
    base_delay: float = 0.5  # 基础延迟时间（秒）
    exponential_base: float = 2.0  # 指数退避底数
    retry_on_exceptions: tuple[type[Exception], ...] = (
        ProviderConnectionError,
        ProviderResponseError,
        TimeoutError,
    )

    async def process_request(self, context: MiddlewareContext) -> None:
        """初始化重试计数器"""
        context.metadata["retry_count"] = 0
        context.metadata["last_error"] = None

    async def process_response(
        self,
        context: MiddlewareContext,
        response: str,
        error: Exception | None = None,
    ) -> str:
        """如果有错误，在这里处理重试逻辑"""
        if error is None:
            return response
        
        # 检查是否应该重试此错误
        if not isinstance(error, self.retry_on_exceptions):
            raise error
        
        retry_count = context.metadata.get("retry_count", 0)
        
        if retry_count >= self.max_retries:
            context.metadata["last_error"] = error
            raise error
        
        # 计算等待时间（指数退避）
        wait_time = self.base_delay * (self.exponential_base ** retry_count)
        
        # 更新重试计数
        context.metadata["retry_count"] = retry_count + 1
        
        # 返回一个特殊标记，表示应该重试
        context.metadata["should_retry"] = True
        context.metadata["retry_delay"] = wait_time
        
        return response


@dataclass(slots=True)
class CircuitBreakerMiddleware(Middleware):
    """
    断路器中间件
    
    防止对故障 Provider 的持续调用
    """
    failure_threshold: int = 5  # 失败次数阈值
    success_threshold: int = 2  # 恢复所需的成功次数
    timeout: float = 60.0  # 断路器打开时的超时（秒）
    
    failure_count: int = 0  # 失败计数
    success_count: int = 0  # 成功计数
    last_failure_time: float | None = None  # 最后一次失败的时间
    
    class CircuitOpen(Exception):
        """断路器打开异常"""
        pass

    async def process_request(self, context: MiddlewareContext) -> None:
        """检查断路器状态"""
        import time
        
        # 如果有最后的失败时间，检查是否应该尝试恢复
        if self.last_failure_time is not None:
            elapsed = time.time() - self.last_failure_time
            if elapsed < self.timeout:
                if self.failure_count >= self.failure_threshold:
                    raise self.CircuitOpen(
                        f"Circuit breaker open. Retrying in {self.timeout - elapsed:.1f}s"
                    )

    async def process_response(
        self,
        context: MiddlewareContext,
        response: str,
        error: Exception | None = None,
    ) -> str:
        """更新断路器状态"""
        import time
        
        if error is None:
            # 推进成功计数
            self.success_count += 1
            if self.success_count >= self.success_threshold:
                # 恢复正常状态
                self.failure_count = 0
                self.success_count = 0
                self.last_failure_time = None
        else:
            # 失败计数增加
            self.failure_count += 1
            self.success_count = 0
            self.last_failure_time = time.time()
        
        return response
