#!/usr/bin/env python
"""
Example: LLM-assisted Event Verification System

Demonstrates the two-level event memory strategy:
1. Fast path: Keyword matching and similarity scoring (no LLM calls)
2. Verification path: LLM validates pending events when they accumulate

This prevents trivial conversations (greetings, chatter) from being recorded
while ensuring meaningful events are properly captured and enriched.
"""

import asyncio
from pathlib import Path

from sirius_chat.user_memory import UserProfile, EventMemoryManager, EventMemoryFileStore
from sirius_chat.providers.base import GenerationRequest


async def demo_event_verification():
    """
    Demo: How event verification works.
    
    Scenario:
    - User mentions some activities over 3+ interactions
    - Each mention creates a pending event (verified=False)
    - When mention_count >= 3, LLM verifies if it's worth recording
    """
    
    # Initialize event memory manager
    event_manager = EventMemoryManager()
    
    # Sample interactions that should accumulate into one event
    mentions = [
        "We discussed the Q4 roadmap yesterday",  # Actual event
        "Yeah, the Q4 roadmap looks good",  # Related mention
        "I think the Q4 roadmap needs some adjustments",  # Another mention
    ]
    
    # Register mentions (these are usually called by AsyncRolePlayEngine)
    print("📝 Registering mentions...")
    for mention in mentions:
        result = event_manager.absorb_mention(
            content=mention,
            known_entities=["Q4 roadmap"],
        )
        entry = result.get("entry")
        print(f"  - '{mention[:40]}...'")
        print(f"    Level: {result['level']}, Verified: {entry.verified}, Mentions: {entry.mention_count}")
    
    print(f"\n📊 Events before verification:")
    print(f"  Total: {len(event_manager.entries)}")
    for entry in event_manager.entries:
        print(f"    {entry.event_id}: '{entry.summary[:50]}...' (verified={entry.verified}, mentions={entry.mention_count})")
    
    # Simulate LLM verification
    print("\n🤖 Running LLM verification on pending events...")
    
    # In production, use actual provider:
    # from sirius_chat.providers import create_provider
    # provider = create_provider("openai", api_key=..., base_url=...)
    
    # For demo, use mock provider
    class DemoAsyncProvider:
        async def generate_async(self, request: GenerationRequest) -> str:
            # Simulated LLM response
            return """{
                "record": "yes",
                "reason": "Multiple mentions about Q4 roadmap planning",
                "summary": "Team discussed and reviewed Q4 product roadmap with proposed adjustments",
                "keywords": ["Q4", "roadmap", "planning", "adjustments"],
                "role_slots": ["manager", "teammate"],
                "time_hints": ["yesterday", "Q4"],
                "emotion_tags": []
            }"""
    
    result = await event_manager.finalize_pending_events(
        provider_async=DemoAsyncProvider(),
        model_name="gpt-4",
        min_mentions=3
    )
    
    print(f"  Verified: {result['verified_count']}")
    print(f"  Rejected: {result['rejected_count']}")
    print(f"  Still pending: {result['pending_count']}")
    
    print(f"\n📊 Events after verification:")
    for entry in event_manager.entries:
        print(f"  {entry.event_id}:")
        print(f"    Summary: {entry.summary}")
        print(f"    Verified: {entry.verified}")
        print(f"    Keywords: {', '.join(entry.keywords)}")
        print(f"    Roles: {', '.join(entry.role_slots)}")
        print(f"    Time hints: {', '.join(entry.time_hints)}")


async def demo_query_behavior():
    """
    Demo: Different query behaviors with verified vs pending events.
    """
    event_manager = EventMemoryManager()
    
    # Create mix of verified and pending events
    # (In real usage, these come from different stages of accumulation)
    
    print("\n📋 Query behavior example:")
    print(f"  top_events(include_pending=False): Only returns verified events")
    print(f"  top_events(include_pending=True):  Returns verified + pending (mention_count>=2)")
    print(f"  This allows filtering out trivial conversations in production")
    print(f"  while keeping them available for debugging.")


if __name__ == "__main__":
    print("=" * 60)
    print("LLM-Assisted Event Verification System Demo")
    print("=" * 60)
    
    asyncio.run(demo_event_verification())
    asyncio.run(demo_query_behavior())
    
    print("\n" + "=" * 60)
    print("Integration points:")
    print("- Call event_manager.finalize_pending_events() after:")
    print("  - Session ends (batch verification)")
    print("  - Every N messages (incremental verification)")
    print("  - Before exporting top_events for context")
    print("=" * 60)
