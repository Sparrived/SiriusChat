"""Test automatic multimodal model configuration."""

import pytest

from sirius_chat.api import (
    Agent,
    auto_configure_multimodal_agent,
    create_agent_with_multimodal,
)


class TestAutoConfigureMultimodalAgent:
    """Test the auto_configure_multimodal_agent function."""

    def test_auto_configure_with_parameter(self):
        """Test auto configuration with explicit multimodal_model parameter."""
        agent = Agent(
            name="Assistant",
            persona="helpful",
            model="gpt-4o-mini",
        )
        
        agent = auto_configure_multimodal_agent(agent, multimodal_model="gpt-4o")
        
        assert "multimodal_model" in agent.metadata
        assert agent.metadata["multimodal_model"] == "gpt-4o"

    def test_auto_configure_preserves_existing(self):
        """Test that existing multimodal_model is not overridden when no param given."""
        agent = Agent(
            name="Assistant",
            persona="helpful",
            model="gpt-4o-mini",
        )
        agent.metadata = {"multimodal_model": "custom-mm-model"}
        
        agent = auto_configure_multimodal_agent(agent)
        
        # Should keep the existing value
        assert agent.metadata["multimodal_model"] == "custom-mm-model"

    def test_auto_configure_param_overrides_existing(self):
        """Test that explicit parameter overrides existing multimodal_model."""
        agent = Agent(
            name="Assistant",
            persona="helpful",
            model="gpt-4o-mini",
        )
        agent.metadata = {"multimodal_model": "old-model"}
        
        agent = auto_configure_multimodal_agent(agent, multimodal_model="new-model")
        
        # Parameter should override existing value
        assert agent.metadata["multimodal_model"] == "new-model"

    def test_auto_configure_returns_agent(self):
        """Test that auto_configure returns the agent object."""
        agent = Agent(
            name="Assistant",
            persona="helpful",
            model="gpt-4o-mini",
        )
        
        result = auto_configure_multimodal_agent(agent, multimodal_model="gpt-4o")
        
        # Should return the same object (modified)
        assert result is agent

    def test_auto_configure_no_config_leaves_unset(self):
        """Test that no configuration leaves multimodal_model unset."""
        agent = Agent(
            name="Assistant",
            persona="helpful",
            model="custom-model",
        )
        
        agent = auto_configure_multimodal_agent(agent)
        
        # Should not set anything if no parameter and no existing config
        assert "multimodal_model" not in agent.metadata


class TestCreateAgentWithMultimodal:
    """Test the create_agent_with_multimodal helper function."""

    def test_create_agent_with_multimodal_basic(self):
        """Test basic agent creation with multimodal model."""
        agent = create_agent_with_multimodal(
            name="Assistant",
            persona="helpful",
            model="gpt-4o-mini",
            multimodal_model="gpt-4o",
        )
        
        assert agent.name == "Assistant"
        assert agent.persona == "helpful"
        assert agent.model == "gpt-4o-mini"
        assert agent.metadata["multimodal_model"] == "gpt-4o"

    def test_create_agent_with_multimodal_custom_params(self):
        """Test agent creation with custom temperature and max_tokens."""
        agent = create_agent_with_multimodal(
            name="Assistant",
            persona="helpful",
            model="gpt-4o-mini",
            multimodal_model="gpt-4o",
            temperature=0.5,
            max_tokens=256,
        )
        
        assert agent.temperature == 0.5
        assert agent.max_tokens == 256

    def test_create_agent_with_multimodal_extra_metadata(self):
        """Test agent creation with additional metadata."""
        agent = create_agent_with_multimodal(
            name="Assistant",
            persona="helpful",
            model="gpt-4o-mini",
            multimodal_model="gpt-4o",
            alias="GPT Assistant",
            custom_field="custom_value",
        )
        
        assert agent.metadata["multimodal_model"] == "gpt-4o"
        assert agent.metadata["alias"] == "GPT Assistant"
        assert agent.metadata["custom_field"] == "custom_value"

    def test_create_agent_with_multimodal_custom_models(self):
        """Test agent creation with custom model names."""
        agent = create_agent_with_multimodal(
            name="Assistant",
            persona="helpful",
            model="my-lite-model",
            multimodal_model="my-vision-model",
        )
        
        assert agent.model == "my-lite-model"
        assert agent.metadata["multimodal_model"] == "my-vision-model"


class TestIntegrationWithDynamicRouting:
    """Test integration with dynamic model routing."""

    def test_manually_configured_agent_works_with_routing(self):
        """Test that manually configured agent works with dynamic routing."""
        # AsyncRolePlayEngine is now a stub; skip legacy engine integration test
        pytest.skip("AsyncRolePlayEngine is a stub after v0.28 refactor")

    def test_create_agent_helper_with_dynamic_routing(self):
        """Test that create_agent_with_multimodal works with dynamic routing."""
        # AsyncRolePlayEngine is now a stub; skip legacy engine integration test
        pytest.skip("AsyncRolePlayEngine is a stub after v0.28 refactor")

