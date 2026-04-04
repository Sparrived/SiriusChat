"""Provider 弹性与重试测试"""

from __future__ import annotations

import asyncio

import pytest

from tests.integration.conftest import MockLLMProvider


class TestProviderResilience:
    """Provider 弹性测试套件"""

    @pytest.mark.asyncio
    async def test_provider_failure_handling(self):
        """测试 Provider 故障处理"""
        provider = MockLLMProvider(should_fail=True)
        
        with pytest.raises(RuntimeError):
            await provider.generate([{"role": "user", "content": "test"}])

    @pytest.mark.asyncio
    async def test_provider_success(self):
        """测试 Provider 成功调用"""
        provider = MockLLMProvider(should_fail=False)
        
        result = await provider.generate(
            [{"role": "user", "content": "test"}]
        )
        
        assert isinstance(result, str)
        assert len(result) > 0
        assert provider.call_count == 1
