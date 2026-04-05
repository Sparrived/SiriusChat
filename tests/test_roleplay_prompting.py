import asyncio
from pathlib import Path

from sirius_chat.api import (
    GENERATED_AGENTS_FILE_NAME,
    Agent,
    AgentPreset,
    Message,
    RolePlayAnswer,
    agenerate_agent_prompts_from_answers,
    create_session_config_from_selected_agent,
    SessionConfig,
    abuild_roleplay_prompt_from_answers_and_apply,
    create_async_engine,
    generate_humanized_roleplay_questions,
    load_generated_agent_library,
    persist_generated_agent_profile,
    select_generated_agent_profile,
)
from sirius_chat.config import OrchestrationPolicy
from sirius_chat.providers.mock import MockProvider


def test_generated_prompt_is_used_by_engine() -> None:
    async def _run() -> None:
        provider = MockProvider(
            responses=[
                '{"agent_persona":"行动导向","global_system_prompt":"你是主助手，先共情后给行动项","temperature":0.35,"max_tokens":256}',
                "ok",
            ]
        )
        engine = create_async_engine(provider)
        config = SessionConfig(
            work_path=Path("data/tests/roleplay_prompt_apply"),
            preset=AgentPreset(
                agent=Agent(name="主助手", persona="测试", model="mock-model"),
                global_system_prompt="测试系统提示词",
            ),
            orchestration=OrchestrationPolicy(
                unified_model="mock-model",
                task_enabled={
                    "memory_extract": False,
                    "multimodal_parse": False,
                    "event_extract": False,
                }
            ),
        )

        await abuild_roleplay_prompt_from_answers_and_apply(
            provider,
            config=config,
            model="prompt-model",
            answers=[RolePlayAnswer(question="沟通风格", answer="先共情，再给结构化步骤")],
            persona_key="assistant_v1",
        )
        await engine.run_live_session(
            config=config,
            human_turns=[Message(role="user", speaker="小王", content="hello")],
        )

        assert "先共情后给行动项" in provider.requests[1].system_prompt
        assert "主 AI 角色设定：行动导向" in provider.requests[1].system_prompt
        assert provider.requests[1].temperature == 0.35
        assert provider.requests[1].max_tokens == 256

    asyncio.run(_run())


def test_generate_humanized_roleplay_questions_covers_persona_dimensions() -> None:
    questions = generate_humanized_roleplay_questions()
    assert len(questions) >= 10
    joined = "\n".join(item.question for item in questions)
    assert "性格" in joined
    assert "日常" in joined
    assert "情绪" in joined
    assert "冲突" in joined
    assert "聊天" in joined
    assert "接话" in joined


def test_abuild_roleplay_prompt_from_answers_and_apply_one_step() -> None:
    async def _run() -> None:
        provider = MockProvider(
            responses=[
                '{"agent_persona":"自动注入的角色提示词","global_system_prompt":"自动注入的角色提示词","temperature":0.6,"max_tokens":300}'
            ]
        )
        config = SessionConfig(
            work_path=Path("data/tests/roleplay_prompt_answer_one_step"),
            preset=AgentPreset(
                agent=Agent(name="主助手", persona="测试", model="mock-model"),
                global_system_prompt="测试系统提示词",
            ),
        )

        prompt = await abuild_roleplay_prompt_from_answers_and_apply(
            provider,
            config=config,
            model="prompt-model",
            answers=[RolePlayAnswer(question="说话风格", answer="短句、克制、少情绪词")],
            persona_key="persona_a",
        )

        assert prompt == "自动注入的角色提示词"
        assert config.agent.persona == "自动注入的角色提示词"
        assert config.global_system_prompt == "自动注入的角色提示词"
        assert config.agent.temperature == 0.6
        assert config.agent.max_tokens == 300
        assert (config.work_path / GENERATED_AGENTS_FILE_NAME).exists()

    asyncio.run(_run())


def test_agenerate_agent_prompts_from_answers_includes_agent_name() -> None:
    async def _run() -> None:
        provider = MockProvider(
            responses=[
                '{"agent_persona":"冷静执行派","global_system_prompt":"你是主助手阿星，请先共情再给方案。","temperature":0.5,"max_tokens":640}'
            ]
        )
        prompts = await agenerate_agent_prompts_from_answers(
            provider,
            model="prompt-model",
            agent_name="阿星",
            answers=[RolePlayAnswer(question="风格", answer="先倾听，再行动")],
        )
        payload = provider.requests[0].messages[0]["content"]
        assert "agent_name=阿星" in payload
        assert prompts.agent.persona == "冷静执行派"
        assert "阿星" in prompts.global_system_prompt
        assert prompts.agent.temperature == 0.5
        assert prompts.agent.max_tokens == 640

    asyncio.run(_run())


def test_generated_agent_profile_can_be_selected_and_used_to_create_session() -> None:
    async def _run() -> None:
        provider = MockProvider(
            responses=[
                '{"agent_persona":"沉稳共情型","global_system_prompt":"你是主助手北辰，先理解用户感受再给可执行方案。","temperature":0.4,"max_tokens":768}'
            ]
        )
        base_config = SessionConfig(
            work_path=Path("data/tests/generated_agent_flow"),
            preset=AgentPreset(
                agent=Agent(name="北辰", persona="默认", model="mock-model"),
                global_system_prompt="测试系统提示词",
            ),
        )

        await abuild_roleplay_prompt_from_answers_and_apply(
            provider,
            config=base_config,
            model="prompt-model",
            agent_name="北辰",
            answers=[RolePlayAnswer(question="偏好", answer="先共情后行动")],
            persona_key="beichen_v1",
            persist_generated_agent=True,
        )

        assert (base_config.work_path / GENERATED_AGENTS_FILE_NAME).exists()
        key = persist_generated_agent_profile(base_config, agent_key="beichen_v1")
        assert key == "beichen_v1"

        agents, selected = load_generated_agent_library(base_config.work_path)
        assert "beichen_v1" in agents
        assert selected == "beichen_v1"

        selected_profile = select_generated_agent_profile(base_config.work_path, "beichen_v1")
        assert selected_profile.agent.name == "北辰"
        assert "共情" in selected_profile.agent.persona

        session_config = create_session_config_from_selected_agent(
            work_path=base_config.work_path,
            agent_key="beichen_v1",
        )
        assert session_config.agent.name == "北辰"
        assert session_config.agent.persona == selected_profile.agent.persona
        assert session_config.global_system_prompt == selected_profile.global_system_prompt
        assert session_config.agent.temperature == 0.4
        assert session_config.agent.max_tokens == 768

    asyncio.run(_run())


def test_prompt_generation_falls_back_to_current_agent_model_params() -> None:
    async def _run() -> None:
        provider = MockProvider(
            responses=['{"agent_persona":"简洁助手","global_system_prompt":"保持简洁"}']
        )
        config = SessionConfig(
            work_path=Path("data/tests/roleplay_prompt_defaults"),
            preset=AgentPreset(
                agent=Agent(name="主助手", persona="默认", model="mock-model", temperature=0.85, max_tokens=900),
                global_system_prompt="测试系统提示词",
            ),
        )

        await abuild_roleplay_prompt_from_answers_and_apply(
            provider,
            config=config,
            model="prompt-model",
            answers=[RolePlayAnswer(question="风格", answer="简洁明确")],
            persona_key="default_params_case",
        )

        assert config.agent.temperature == 0.85
        assert config.agent.max_tokens == 900

    asyncio.run(_run())


def test_prompt_generation_applies_agent_alias_to_metadata() -> None:
    async def _run() -> None:
        provider = MockProvider(
            responses=[
                '{"agent_persona":"自然陪伴型","agent_alias":"小星","global_system_prompt":"更像朋友一样聊天，避免频繁自我介绍。","temperature":0.4,"max_tokens":600}'
            ]
        )
        config = SessionConfig(
            work_path=Path("data/tests/roleplay_prompt_alias"),
            preset=AgentPreset(
                agent=Agent(name="北辰", persona="默认", model="mock-model"),
                global_system_prompt="测试系统提示词",
            ),
        )

        await abuild_roleplay_prompt_from_answers_and_apply(
            provider,
            config=config,
            model="prompt-model",
            agent_name="北辰",
            agent_alias="小星",
            answers=[RolePlayAnswer(question="聊天风格", answer="像朋友一样，少端着")],
            persona_key="alias_case",
        )

        assert config.agent.metadata.get("alias") == "小星"
        assert "避免频繁自我介绍" in config.global_system_prompt

    asyncio.run(_run())



