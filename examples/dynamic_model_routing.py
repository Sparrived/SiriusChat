"""
Dynamic Model Routing Example

This example demonstrates automatic model switching based on input content:
- Plain text: Uses cheap model (e.g., gpt-4o-mini)
- With images: Automatically upgrades to multimodal model (e.g., gpt-4o)

This approach optimizes cost while maintaining quality for multimodal scenarios.

Three configuration methods are shown:
1. Manual: Set agent.metadata["multimodal_model"] directly
2. Helper: Use auto_configure_multimodal_agent() for flexible configuration
3. Constructor: Use create_agent_with_multimodal() for one-shot creation
"""

import asyncio
from pathlib import Path

from sirius_chat.api import (
    Agent,
    AgentPreset,
    AsyncRolePlayEngine,
    Message,
    OpenAICompatibleProvider,
    SessionConfig,
    auto_configure_multimodal_agent,
    create_agent_with_multimodal,
)


async def main() -> None:
    """Demonstrate dynamic model routing."""

    # Configure provider
    provider = OpenAICompatibleProvider(
        base_url="https://api.openai.com",
        api_key="YOUR_API_KEY",
    )

    # ============================================================
    # Method 1: Manual configuration (most explicit)
    # ============================================================
    print("Configuration Method 1: Manual metadata")
    print("-" * 60)
    agent = Agent(
        name="Assistant",
        persona="A helpful AI assistant that can analyze images and answer questions.",
        model="gpt-4o-mini",  # Default: cheap model for text
        temperature=0.7,
        max_tokens=512,
    )
    agent.metadata["multimodal_model"] = "gpt-4o"
    print(f"Agent configured: {agent.name}, model={agent.model}, multimodal_model={agent.metadata.get('multimodal_model')}\n")

    # ============================================================
    # Method 2: Using auto_configure_multimodal_agent helper (flexible)
    # ============================================================
    print("Configuration Method 2: auto_configure_multimodal_agent helper")
    print("-" * 60)
    agent = Agent(
        name="Assistant",
        persona="A helpful AI assistant that can analyze images and answer questions.",
        model="gpt-4o-mini",
        temperature=0.7,
        max_tokens=512,
    )
    agent = auto_configure_multimodal_agent(agent, multimodal_model="gpt-4o")
    print(f"Agent configured: {agent.name}, model={agent.model}, multimodal_model={agent.metadata.get('multimodal_model')}\n")

    # ============================================================
    # Method 3: Using create_agent_with_multimodal convenience constructor
    # ============================================================
    print("Configuration Method 3: create_agent_with_multimodal convenience constructor")
    print("-" * 60)
    agent = create_agent_with_multimodal(
        name="Assistant",
        persona="A helpful AI assistant that can analyze images and answer questions.",
        model="gpt-4o-mini",
        multimodal_model="gpt-4o",
        temperature=0.7,
        max_tokens=512,
    )
    print(f"Agent configured: {agent.name}, model={agent.model}, multimodal_model={agent.metadata.get('multimodal_model')}\n")

    # ============================================================
    # Demo: Run live session with dynamic model routing
    # ============================================================
    preset = AgentPreset(
        agent=agent,
        global_system_prompt="You are a helpful assistant. When presented with images, analyze them carefully and provide insightful responses.",
    )

    config_root = Path("data/dynamic_routing_config")
    data_root = Path("data/dynamic_routing_runtime")

    config = SessionConfig(
        work_path=config_root,
        data_path=data_root,
        preset=preset,
    )

    engine = AsyncRolePlayEngine(provider=provider)

    # ============================================================
    # Scenario 1: Plain text query (uses gpt-4o-mini)
    # ============================================================
    print("\n" + "=" * 60)
    print("Scenario 1: Plain text query (uses gpt-4o-mini)")
    print("=" * 60)
    transcript = await engine.run_live_session(config=config)
    transcript = await engine.run_live_message(
        config=config,
        transcript=transcript,
        turn=Message(
            role="user",
            speaker="User",
            content="What are the main differences between Python and JavaScript?",
        ),
    )
    for msg in transcript.messages:
        if msg.role == "assistant":
            print(f"[{msg.speaker}] {msg.content}\n")
    print("✓ Model used: gpt-4o-mini (text route - cost optimized)")
    print()

    # ============================================================
    # Scenario 2: Query with images (auto-upgrades to gpt-4o)
    # ============================================================
    print("=" * 60)
    print("Scenario 2: Query with image (auto-upgrades to gpt-4o)")
    print("=" * 60)
    transcript = await engine.run_live_session(config=config)
    transcript = await engine.run_live_message(
        config=config,
        transcript=transcript,
        turn=Message(
            role="user",
            speaker="User",
            content="Please analyze this architecture diagram and explain the components",
            multimodal_inputs=[
                {
                    "type": "image",
                    "value": "https://example.com/architecture.png",
                }
            ],
        ),
    )
    for msg in transcript.messages:
        if msg.role == "assistant":
            print(f"[{msg.speaker}] {msg.content}\n")
    print("✓ Model used: gpt-4o (multimodal route - automatically upgraded)")
    print()

    # ============================================================
    # Scenario 3: Multiple images query
    # ============================================================
    print("=" * 60)
    print("Scenario 3: Multiple images comparison")
    print("=" * 60)
    transcript = await engine.run_live_session(config=config)
    transcript = await engine.run_live_message(
        config=config,
        transcript=transcript,
        turn=Message(
            role="user",
            speaker="User",
            content="Compare these two interface designs. Which one is better and why?",
            multimodal_inputs=[
                {"type": "image", "value": "https://example.com/design-v1.png"},
                {"type": "image", "value": "https://example.com/design-v2.png"},
            ],
        ),
    )
    for msg in transcript.messages:
        if msg.role == "assistant":
            print(f"[{msg.speaker}] {msg.content}\n")
    print("✓ Model used: gpt-4o (multimodal route)")
    print()

    # ============================================================
    # Benefits of Dynamic Model Routing
    # ============================================================
    print("=" * 60)
    print("Benefits Summary")
    print("=" * 60)
    print("✓ Cost Optimization: Text queries use cheap model, image queries auto-upgrade")
    print("✓ Transparent: No manual model selection needed")
    print("✓ Consistent: Same system prompt across all scenarios")
    print("✓ User-Agnostic: Clients don't need to know about model routing logic")


if __name__ == "__main__":
    asyncio.run(main())
