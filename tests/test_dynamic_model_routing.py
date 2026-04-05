"""Test dynamic model routing in core engine."""

import asyncio

import pytest

from sirius_chat.api import (
    Agent,
    AgentPreset,
    AsyncRolePlayEngine,
    Message,
    SessionConfig,
)
from sirius_chat.models.models import Transcript
from sirius_chat.providers.mock import MockProvider


def test_has_multimodal_inputs_no_messages():
    """Test _has_multimodal_inputs with empty transcript."""
    provider = MockProvider()
    engine = AsyncRolePlayEngine(provider)
    transcript = Transcript()
    
    assert engine._has_multimodal_inputs(transcript) is False


def test_has_multimodal_inputs_with_text_only():
    """Test _has_multimodal_inputs with text-only messages."""
    provider = MockProvider()
    engine = AsyncRolePlayEngine(provider)
    transcript = Transcript()
    
    transcript.add(
        Message(
            role="user",
            content="Hello, what is Python?",
            speaker="User",
        )
    )
    
    assert engine._has_multimodal_inputs(transcript) is False


def test_has_multimodal_inputs_with_images():
    """Test _has_multimodal_inputs with image inputs."""
    provider = MockProvider()
    engine = AsyncRolePlayEngine(provider)
    transcript = Transcript()
    
    transcript.add(
        Message(
            role="user",
            content="Analyze this image",
            speaker="User",
            multimodal_inputs=[
                {"type": "image", "value": "https://example.com/photo.jpg"}
            ],
        )
    )
    
    assert engine._has_multimodal_inputs(transcript) is True


def test_has_multimodal_inputs_finds_last_user_message():
    """Test _has_multimodal_inputs finds the last user message."""
    provider = MockProvider()
    engine = AsyncRolePlayEngine(provider)
    transcript = Transcript()
    
    # Add text-only message
    transcript.add(
        Message(
            role="user",
            content="First message",
            speaker="User",
        )
    )
    
    # Add assistant message (should be ignored)
    transcript.add(
        Message(
            role="assistant",
            content="Response",
            speaker="Assistant",
        )
    )
    
    # Add user message with images (this should be checked)
    transcript.add(
        Message(
            role="user",
            content="Image analysis",
            speaker="User",
            multimodal_inputs=[
                {"type": "image", "value": "https://example.com/image.png"}
            ],
        )
    )
    
    # Should return True because the last user message has multimodal inputs
    assert engine._has_multimodal_inputs(transcript) is True


def test_get_model_for_chat_text_only():
    """Test _get_model_for_chat returns default model for text-only input."""
    provider = MockProvider()
    engine = AsyncRolePlayEngine(provider)
    
    agent = Agent(
        name="Assistant",
        persona="helpful",
        model="gpt-4o-mini",
    )
    agent.metadata = {
        "multimodal_model": "gpt-4o",
    }
    
    preset = AgentPreset(
        agent=agent,
        global_system_prompt="You are helpful.",
    )
    config = SessionConfig(preset=preset, work_path="./data")
    
    transcript = Transcript()
    transcript.add(
        Message(
            role="user",
            content="What is AI?",
            speaker="User",
        )
    )
    
    model = engine._get_model_for_chat(config, transcript)
    assert model == "gpt-4o-mini"


def test_get_model_for_chat_with_image():
    """Test _get_model_for_chat returns multimodal model when images are present."""
    provider = MockProvider()
    engine = AsyncRolePlayEngine(provider)
    
    agent = Agent(
        name="Assistant",
        persona="helpful",
        model="gpt-4o-mini",
    )
    agent.metadata = {
        "multimodal_model": "gpt-4o",
    }
    
    preset = AgentPreset(
        agent=agent,
        global_system_prompt="You are helpful.",
    )
    config = SessionConfig(preset=preset, work_path="./data")
    
    transcript = Transcript()
    transcript.add(
        Message(
            role="user",
            content="Analyze this",
            speaker="User",
            multimodal_inputs=[
                {"type": "image", "value": "https://example.com/image.png"}
            ],
        )
    )
    
    model = engine._get_model_for_chat(config, transcript)
    assert model == "gpt-4o"


def test_get_model_for_chat_no_multimodal_model_configured():
    """Test _get_model_for_chat returns default when multimodal_model not configured."""
    provider = MockProvider()
    engine = AsyncRolePlayEngine(provider)
    
    agent = Agent(
        name="Assistant",
        persona="helpful",
        model="gpt-4o-mini",
    )
    # No multimodal_model in metadata
    
    preset = AgentPreset(
        agent=agent,
        global_system_prompt="You are helpful.",
    )
    config = SessionConfig(preset=preset, work_path="./data")
    
    transcript = Transcript()
    transcript.add(
        Message(
            role="user",
            content="Analyze this",
            speaker="User",
            multimodal_inputs=[
                {"type": "image", "value": "https://example.com/image.png"}
            ],
        )
    )
    
    model = engine._get_model_for_chat(config, transcript)
    # Should fall back to default model
    assert model == "gpt-4o-mini"


@pytest.mark.asyncio
async def test_generate_assistant_message_uses_dynamic_model():
    """Test that _generate_assistant_message applies dynamic model routing."""
    provider = MockProvider()
    engine = AsyncRolePlayEngine(provider)
    
    agent = Agent(
        name="Assistant",
        persona="helpful",
        model="gpt-4o-mini",
    )
    agent.metadata = {
        "multimodal_model": "gpt-4o",
    }
    
    preset = AgentPreset(
        agent=agent,
        global_system_prompt="You are helpful.",
    )
    config = SessionConfig(preset=preset, work_path="./data")
    
    transcript = Transcript()
    transcript.add(
        Message(
            role="system",
            content=config.global_system_prompt,
        )
    )
    transcript.add(
        Message(
            role="user",
            content="Analyze this image",
            speaker="User",
            multimodal_inputs=[
                {"type": "image", "value": "https://example.com/image.png"}
            ],
        )
    )
    
    # The method should use the multimodal model
    # (we're just verifying it doesn't raise an error)
    result = await engine._generate_assistant_message(config, transcript)
    
    assert result.role == "assistant"
    assert result.speaker == "Assistant"
