"""成本计量中间件"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sirius_chat.providers.middleware.base import Middleware, MiddlewareContext


@dataclass(slots=True)
class CostMetricsMiddleware(Middleware):
    """
    成本计量中间件
    
    追踪调用成本和使用指标
    """
    # 模型定价（单位：美元/千token）
    model_costs: dict[str, dict[str, float]] = field(default_factory=lambda: {
        "gpt-4": {"input": 0.03, "output": 0.06},
        "gpt-3.5-turbo": {"input": 0.0005, "output": 0.0015},
        "claude-3-opus": {"input": 0.015, "output": 0.075},
        "claude-3-sonnet": {"input": 0.003, "output": 0.015},
    })
    
    total_calls: int = field(default=0, init=False)
    total_cost: float = field(default=0.0, init=False)
    total_tokens: int = field(default=0, init=False)
    call_costs: list[dict[str, Any]] = field(default_factory=list, init=False)

    async def process_request(self, context: MiddlewareContext) -> None:
        """初始化成本追踪"""
        context.metadata["cost_start_time"] = __import__("time").time()

    async def process_response(
        self,
        context: MiddlewareContext,
        response: str,
        error: Exception | None = None,
    ) -> str:
        """计算和记录成本"""
        if error is None:
            self.total_calls += 1
            
            # 从 request 获取模型信息
            request = context.metadata.get("request")
            model = getattr(request, "model", "unknown") if request else "unknown"
            
            # 从 response 中估算 token 数
            # 简单估算：英文平均 4 字符 = 1 token
            prompt_tokens = len(getattr(request, "prompt", "")) // 4  if request else 0
            completion_tokens = len(response) // 4
            total_tokens = prompt_tokens + completion_tokens
            
            # 计算成本
            cost = self._calculate_cost(model, prompt_tokens, completion_tokens)
            
            # 记录
            self.total_tokens += total_tokens
            self.total_cost += cost
            self.call_costs.append({
                "model": model,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "cost": cost,
            })
        
        return response

    def _calculate_cost(self, model: str, prompt_tokens: int, completion_tokens: int) -> float:
        """计算单次调用的成本"""
        pricing = self.model_costs.get(model)
        if not pricing:
            return 0.0
        
        input_cost = (prompt_tokens / 1000) * pricing["input"]
        output_cost = (completion_tokens / 1000) * pricing["output"]
        return input_cost + output_cost

    def get_metrics(self) -> dict[str, Any]:
        """获取全部指标"""
        avg_cost = self.total_cost / self.total_calls if self.total_calls > 0 else 0
        return {
            "total_calls": self.total_calls,
            "total_cost": f"${self.total_cost:.4f}",
            "total_tokens": self.total_tokens,
            "average_cost_per_call": f"${avg_cost:.6f}",
            "call_details": self.call_costs,
        }
