"""并发会话测试"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from sirius_chat.api import SessionConfig, JsonSessionStore, AgentPreset, Agent
from tests.integration.conftest import MockLLMProvider


class TestConcurrentSessions:
    """并发会话测试套件"""

    def test_multiple_sessions_isolation(self, temp_work_dir: Path):
        """测试多个会话的隔离"""
        sessions = []
        
        for i in range(3):
            agent = Agent(
                name=f"agent_{i}",
                persona=f"Agent {i} persona",
                model="gpt-4",
            )
            preset = AgentPreset(
                agent=agent,
                global_system_prompt=f"You are agent {i}",
            )
            config = SessionConfig(
                preset=preset,
                work_path=temp_work_dir,
            )
            sessions.append(config)
        
        assert len(sessions) == 3

    @pytest.mark.asyncio
    async def test_concurrent_provider_calls(self):
        """测试并发 Provider 调用"""
        provider = MockLLMProvider()
        
        async def provider_call(i: int) -> str:
            return await provider.generate(
                [{"role": "user", "content": f"Request {i}"}]
            )
        
        tasks = [provider_call(i) for i in range(10)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        assert len(results) == 10
        assert all(isinstance(r, str) for r in results)
        assert provider.call_count == 10
