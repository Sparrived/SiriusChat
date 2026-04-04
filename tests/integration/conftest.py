"""集成测试共享配置和 fixtures"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Generator

import pytest

from sirius_chat.api import (
    JsonSessionStore,
    SessionConfig,
    LLMProvider,
    AgentPreset,
    Agent,
)


class MockLLMProvider(LLMProvider):
    """用于测试的 mock LLM provider"""

    def __init__(self, model: str = "mock-model", should_fail: bool = False):
        """初始化 mock provider
        
        Args:
            model: 模型名称
            should_fail: 是否模拟故障
        """
        self.model = model
        self.should_fail = should_fail
        self.call_count = 0
        self.last_messages = None

    @property
    def supported_models(self) -> list[str]:
        return [self.model]

    @property
    def is_chat_completion(self) -> bool:
        return True

    async def score_text(self, text: str) -> float:
        """模拟文本评分"""
        if self.should_fail:
            raise RuntimeError("Mock provider intentionally failed")
        return 0.8

    async def generate(self, messages: list[dict], **kwargs) -> str:
        """模拟文本生成"""
        self.call_count += 1
        self.last_messages = messages
        
        if self.should_fail:
            raise RuntimeError("Mock provider intentionally failed")
        
        return "Mock response from " + self.model


@pytest.fixture
def temp_work_dir() -> Generator[Path, None, None]:
    """临时工作目录 fixture"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def mock_provider() -> MockLLMProvider:
    """创建 mock LLM provider"""
    return MockLLMProvider()


@pytest.fixture
def failing_provider() -> MockLLMProvider:
    """创建会失败的 mock provider"""
    return MockLLMProvider(should_fail=True)


@pytest.fixture
def test_session_store(temp_work_dir: Path) -> JsonSessionStore:
    """创建测试用的会话存储"""
    return JsonSessionStore(work_path=temp_work_dir)


@pytest.fixture
def test_session_config(temp_work_dir: Path) -> SessionConfig:
    """创建测试用的会话配置"""
    agent = Agent(
        name="test_agent",
        persona="Test agent persona",
        model="gpt-4",
    )
    preset = AgentPreset(
        agent=agent,
        global_system_prompt="You are a test agent",
    )
    return SessionConfig(
        preset=preset,
        work_path=temp_work_dir,
    )
