import asyncio

from sirius_chat.api import (
    AsyncRolePlayEngine,
    Message,
    OpenAICompatibleProvider,
    create_session_config_from_selected_agent,
)
from pathlib import Path


async def main() -> None:
    provider = OpenAICompatibleProvider(
        base_url="https://api.openai.com",
        api_key="YOUR_API_KEY",
    )
    engine = AsyncRolePlayEngine(provider=provider)

    config = create_session_config_from_selected_agent(
        work_path=Path("data/external_api_usage"),
        agent_key="main_agent",
    )

    transcript = await engine.run_live_session(
        config=config,
        human_turns=[Message(role="user", speaker="教育顾问", content="我们先做小范围试点并评估效果")],
    )
    for message in transcript.messages:
        if message.speaker:
            print(f"[{message.speaker}] {message.content}")


if __name__ == "__main__":
    asyncio.run(main())


