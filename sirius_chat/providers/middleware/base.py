"""Provider 中间件基础框架"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Coroutine

from sirius_chat.providers.base import GenerationRequest


@dataclass(slots=True)
class MiddlewareContext:
    """中间件执行上下文"""
    request: GenerationRequest
    metadata: dict[str, Any]  # 用于在中间件间传递数据


class Middleware(ABC):
    """中间件基类"""

    @abstractmethod
    async def process_request(self, context: MiddlewareContext) -> None:
        """处理请求前的逻辑
        
        Args:
            context: 中间件上下文
            
        Raises:
            Exception: 可以抛出异常来中断请求处理
        """
        pass

    @abstractmethod
    async def process_response(
        self, 
        context: MiddlewareContext, 
        response: str,
        error: Exception | None = None,
    ) -> str:
        """处理响应的逻辑
        
        Args:
            context: 中间件上下文
            response: 模型的响应
            error: 如果发生错误则不为 None
            
        Returns:
            处理后的响应
        """
        pass


class MiddlewareChain:
    """中间件链管理器"""

    def __init__(self):
        self.middlewares: list[Middleware] = []

    def add(self, middleware: Middleware) -> MiddlewareChain:
        """添加中间件
        
        Args:
            middleware: 要添加的中间件
            
        Returns:
            self（支持链式调用）
        """
        self.middlewares.append(middleware)
        return self

    async def execute_request(self, context: MiddlewareContext) -> None:
        """执行所有中间件的请求处理逻辑"""
        for middleware in self.middlewares:
            await middleware.process_request(context)

    async def execute_response(
        self,
        context: MiddlewareContext,
        response: str,
        error: Exception | None = None,
    ) -> str:
        """执行所有中间件的响应处理逻辑（逆序）"""
        result = response
        for middleware in reversed(self.middlewares):
            result = await middleware.process_response(context, result, error)
        return result

    async def wrap_call(
        self,
        request: GenerationRequest,
        call_func: Callable[..., Coroutine[Any, Any, str]],
        **kwargs,
    ) -> str:
        """包装一个调用，自动处理中间件逻辑
        
        Args:
            request: 生成请求
            call_func: 实际的调用函数
            **kwargs: 传递给 call_func 的其他参数
            
        Returns:
            最终响应
        """
        context = MiddlewareContext(request=request, metadata={})
        
        try:
            await self.execute_request(context)
            response = await call_func(**kwargs)
            return await self.execute_response(context, response, None)
        except Exception as e:
            final_response = await self.execute_response(context, "", e)
            if final_response:  # 如果中间件处理了错误，返回处理结果
                return final_response
            raise  # 否则重新抛出异常
