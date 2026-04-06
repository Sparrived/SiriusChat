import asyncio
from pathlib import Path

from sirius_chat.api import AsyncRolePlayEngine, Message, create_session_config_from_selected_agent
from sirius_chat.providers import OpenAICompatibleProvider


async def _run() -> None:
    provider = OpenAICompatibleProvider(
        base_url="https://api.openai.com",
        api_key="YOUR_API_KEY",
    )
    engine = AsyncRolePlayEngine(provider=provider)

    config = create_session_config_from_selected_agent(
        work_path=Path("data/dynamic_group_chat_usage"),
        agent_key="main_agent",
    )

    human_turns = [
        Message(role="user", speaker="王PM", content="我是产品经理，倾向快速试点。"),
        Message(role="user", speaker="小李", content="我是财务，关注投入成本。"),
        Message(role="user", speaker="小王", content="建议先在一线城市灰度。"),
    ]

    transcript = await engine.run_live_session(config=config)
    for turn in human_turns:
        transcript = await engine.run_live_message(
            config=config,
            transcript=transcript,
            turn=turn,
            session_reply_mode=turn.reply_mode,
            finalize_and_persist=False,
        )
    for message in transcript.messages:
        if message.speaker:
            print(f"[{message.speaker}] {message.content}")


if __name__ == "__main__":
    asyncio.run(_run())


