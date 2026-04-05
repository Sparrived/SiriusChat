"""
Dynamic Model Routing Example

This example demonstrates automatic model switching based on input content:
- Plain text: Uses cheap model (e.g., gpt-4o-mini)
- With images: Automatically upgrades to multimodal model (e.g., gpt-4o)

This approach optimizes cost while maintaining quality for multimodal scenarios.
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
)


async def main() -> None:
    """Demonstrate dynamic model routing."""

    # Configure provider
    provider = OpenAICompatibleProvider(
        base_url="https://api.openai.com",
        api_key="YOUR_API_KEY",
    )

    # Create agent with multimodal_model in metadata for auto-upgrade
    agent = Agent(
        name="Assistant",
        persona="A helpful AI assistant that can analyze images and answer questions.",
        model="gpt-4o-mini",  # Default: cheap model for text
        temperature=0.7,
        max_tokens=512,
    )
    # Add multimodal model for automatic use when images are detected
    agent.metadata = {
        "multimodal_model": "gpt-4o",  # Upgrade to this when images are present
    }

    preset = AgentPreset(
        agent=agent,
        global_system_prompt="You are a helpful assistant. When presented with images, analyze them carefully and provide insightful responses.",
    )

    config = SessionConfig(
        work_path=Path("data/dynamic_routing_demo"),
        preset=preset,
    )

    engine = AsyncRolePlayEngine(provider=provider)

    # ============================================================
    # Scenario 1: Plain text query (uses gpt-4o-mini)
    # ============================================================
    print("Scenario 1: Plain text query")
    print("-" * 60)
    transcript = await engine.run_live_session(
        config=config,
        human_turns=[
            Message(
                role="user",
                speaker="User",
                content="What are the main differences between Python and JavaScript?",
            )
        ],
    )
    for msg in transcript.messages:
        if msg.role == "assistant":
            print(f"[{msg.speaker}] {msg.content}\n")
    print(f"Model used: gpt-4o-mini (cheap route)")
    print()

    # ============================================================
    # Scenario 2: Query with images (auto-upgrades to gpt-4o)
    # ============================================================
    print("Scenario 2: Query with image (auto-upgrade)")
    print("-" * 60)
    transcript = await engine.run_live_session(
        config=config,
        human_turns=[
            Message(
                role="user",
                speaker="User",
                content="Please analyze this architecture diagram and explain the components",
                multimodal_inputs=[
                    {
                        "type": "image",
                        "value": "https://example.com/architecture.png",
                    }
                ],
            )
        ],
    )
    for msg in transcript.messages:
        if msg.role == "assistant":
            print(f"[{msg.speaker}] {msg.content}\n")
    print(f"Model used: gpt-4o (multimodal route - automatically upgraded)")
    print()

    # ============================================================
    # Scenario 3: Multiple images query
    # ============================================================
    print("Scenario 3: Multiple images comparison")
    print("-" * 60)
    transcript = await engine.run_live_session(
        config=config,
        human_turns=[
            Message(
                role="user",
                speaker="User",
                content="Compare these two interface designs. Which one is better and why?",
                multimodal_inputs=[
                    {"type": "image", "value": "https://example.com/design-v1.png"},
                    {"type": "image", "value": "https://example.com/design-v2.png"},
                ],
            )
        ],
    )
    for msg in transcript.messages:
        if msg.role == "assistant":
            print(f"[{msg.speaker}] {msg.content}\n")
    print(f"Model used: gpt-4o (multimodal route)")
    print()

    # ============================================================
    # Benefits of Dynamic Model Routing
    # ============================================================
    print("Benefits Summary")
    print("-" * 60)
    print("✓ Cost Optimization: Text queries use cheap model, image queries auto-upgrade")
    print("✓ Transparent: No manual model selection needed")
    print("✓ Consistent: Same system prompt across all scenarios")
    print("✓ User-Agnostic: Clients don't need to know about model routing logic")


if __name__ == "__main__":
    asyncio.run(main())
