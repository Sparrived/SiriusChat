import asyncio
from pathlib import Path

from sirius_chat.api import (
    GENERATED_AGENTS_FILE_NAME,
    Agent,
    AgentPreset,
    Message,
    PersonaSpec,
    RolePlayAnswer,
    agenerate_agent_prompts_from_answers,
    agenerate_from_persona_spec,
    aupdate_agent_prompt,
    create_session_config_from_selected_agent,
    SessionConfig,
    abuild_roleplay_prompt_from_answers_and_apply,
    create_async_engine,
    generate_humanized_roleplay_questions,
    load_generated_agent_library,
    load_persona_spec,
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
                    "event_extract": False,
                },
            message_debounce_seconds=0.0,
            ),
        )

        await abuild_roleplay_prompt_from_answers_and_apply(
            provider,
            config=config,
            model="prompt-model",
            answers=[RolePlayAnswer(question="沟通风格", answer="先共情，再给结构化步骤")],
            persona_key="assistant_v1",
        )
        transcript = await engine.run_live_session(config=config)
        await engine.run_live_message(
            config=config,
            transcript=transcript,
            turn=Message(role="user", speaker="小王", content="hello"),
            session_reply_mode="always",
            finalize_and_persist=False,
        )

        assert "先共情后给行动项" in provider.requests[1].system_prompt
        assert "设定: 行动导向" in provider.requests[1].system_prompt
        assert provider.requests[1].temperature == 0.35
        assert provider.requests[1].max_tokens == 256

    asyncio.run(_run())


def test_generate_humanized_roleplay_questions_covers_persona_dimensions() -> None:
    questions = generate_humanized_roleplay_questions()
    assert len(questions) >= 8  # 精简到8个核心问题
    joined = "\n".join(item.question for item in questions)
    assert "性格" in joined or "特质" in joined
    assert "聊天" in joined or "语言" in joined
    assert "情绪" in joined or "压力" in joined
    assert "冲突" in joined
    assert "价值" in joined or "看重" in joined


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
        assert "name=阿星" in payload  # 新格式中的 name 字段
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


# ────────────────────────────────────────────────────────────
# PersonaSpec / agenerate_from_persona_spec
# ────────────────────────────────────────────────────────────


def test_agenerate_from_persona_spec_tag_based() -> None:
    """Tag-only path: no Q&A needed, persona = compact keywords."""
    async def _run() -> None:
        provider = MockProvider(
            responses=[
                '{"agent_persona":"热情/直接/逻辑清晰","global_system_prompt":"你是北辰，热情直接。","temperature":0.7,"max_tokens":512}'
            ]
        )
        spec = PersonaSpec(
            agent_name="北辰",
            trait_keywords=["热情", "直接", "逻辑清晰"],
        )
        preset = await agenerate_from_persona_spec(
            provider, spec, model="test-model"
        )
        payload = provider.requests[0].messages[0]["content"]
        assert "keywords=热情/直接/逻辑清晰" in payload
        assert "name=北辰" in payload
        assert preset.agent.persona == "热情/直接/逻辑清晰"
        assert "北辰" in preset.global_system_prompt

    asyncio.run(_run())


def test_agenerate_from_persona_spec_hybrid_includes_both_inputs() -> None:
    """Hybrid path: keywords anchor traits, Q&A enriches the result."""
    async def _run() -> None:
        provider = MockProvider(
            responses=[
                '{"agent_persona":"沉稳/共情/决断","global_system_prompt":"混合路径生成","temperature":0.6,"max_tokens":600}'
            ]
        )
        spec = PersonaSpec(
            agent_name="南星",
            trait_keywords=["沉稳", "共情"],
            answers=[RolePlayAnswer(question="压力下表现", answer="先冷静，再行动")],
            background="曾在医疗行业工作",
        )
        preset = await agenerate_from_persona_spec(
            provider, spec, model="test-model"
        )
        payload = provider.requests[0].messages[0]["content"]
        assert "keywords=沉稳/共情" in payload
        assert "[Q&A]" in payload
        assert "background=曾在医疗行业工作" in payload
        assert preset.agent.persona == "沉稳/共情/决断"

    asyncio.run(_run())


def test_agenerate_from_persona_spec_raises_on_empty_spec() -> None:
    async def _run() -> None:
        provider = MockProvider(responses=["irrelevant"])
        spec = PersonaSpec(agent_name="X")  # no keywords or answers
        try:
            await agenerate_from_persona_spec(provider, spec, model="m")
            assert False, "should have raised ValueError"
        except ValueError:
            pass

    asyncio.run(_run())


def test_abuild_accepts_trait_keywords_without_answers() -> None:
    """Builder must work with only trait_keywords (no answers)."""
    async def _run() -> None:
        provider = MockProvider(
            responses=[
                '{"agent_persona":"高效/简洁/务实","global_system_prompt":"你是效率助手","temperature":0.5,"max_tokens":400}'
            ]
        )
        config = SessionConfig(
            work_path=Path("data/tests/roleplay_tag_path"),
            preset=AgentPreset(
                agent=Agent(name="效率助手", persona="默认", model="mock-model"),
                global_system_prompt="测试",
            ),
        )
        prompt = await abuild_roleplay_prompt_from_answers_and_apply(
            provider,
            config=config,
            model="test-model",
            trait_keywords=["高效", "简洁", "务实"],
            persona_key="tag_only",
        )
        assert prompt == "你是效率助手"
        assert config.agent.persona == "高效/简洁/务实"
        assert (config.work_path / GENERATED_AGENTS_FILE_NAME).exists()

    asyncio.run(_run())


def test_persona_spec_persisted_along_with_output() -> None:
    """PersonaSpec must be saved alongside the generated preset."""
    async def _run() -> None:
        provider = MockProvider(
            responses=[
                '{"agent_persona":"温柔/体贴","global_system_prompt":"我是体贴助手","temperature":0.6,"max_tokens":512}'
            ]
        )
        config = SessionConfig(
            work_path=Path("data/tests/roleplay_spec_persist"),
            preset=AgentPreset(
                agent=Agent(name="体贴助手", persona="默认", model="mock-model"),
                global_system_prompt="初始提示词",
            ),
        )
        await abuild_roleplay_prompt_from_answers_and_apply(
            provider,
            config=config,
            model="test-model",
            answers=[RolePlayAnswer(question="风格", answer="温柔体贴")],
            persona_key="spec_persist_test",
        )
        saved_spec = load_persona_spec(config.work_path, "spec_persist_test")
        assert saved_spec is not None
        assert len(saved_spec.answers) == 1
        assert saved_spec.answers[0].question == "风格"
        assert saved_spec.agent_name == "体贴助手"

    asyncio.run(_run())


def test_aupdate_agent_prompt_partial_update() -> None:
    """aupdate_agent_prompt patches spec and regenerates without full rewrite."""
    async def _run() -> None:
        provider = MockProvider(
            responses=[
                # Initial generation
                '{"agent_persona":"温和/耐心","global_system_prompt":"原始描述","temperature":0.6,"max_tokens":512}',
                # Partial update generation
                '{"agent_persona":"温和/耐心/幽默","global_system_prompt":"更新后描述带幽默感","temperature":0.6,"max_tokens":512}',
            ]
        )
        work_path = Path("data/tests/roleplay_update_test")
        config = SessionConfig(
            work_path=work_path,
            preset=AgentPreset(
                agent=Agent(name="北辰", persona="默认", model="mock-model"),
                global_system_prompt="初始",
            ),
        )
        # Initial generation
        await abuild_roleplay_prompt_from_answers_and_apply(
            provider,
            config=config,
            model="test-model",
            answers=[RolePlayAnswer(question="性格", answer="温和耐心")],
            persona_key="update_test",
        )
        assert config.agent.persona == "温和/耐心"

        # Partial update: add background only, keep original answers
        updated = await aupdate_agent_prompt(
            provider,
            work_path=work_path,
            agent_key="update_test",
            model="test-model",
            background="最近变得更幽默了",
        )
        assert updated.agent.persona == "温和/耐心/幽默"
        assert "更新后描述带幽默感" in updated.global_system_prompt

        # Spec should reflect merged patch
        spec = load_persona_spec(work_path, "update_test")
        assert spec is not None
        assert spec.background == "最近变得更幽默了"
        # Original answers still preserved
        assert len(spec.answers) == 1

    asyncio.run(_run())


def test_persona_spec_merge_ignores_none_values() -> None:
    spec = PersonaSpec(
        agent_name="测试",
        trait_keywords=["热情"],
        background="原始背景",
    )
    merged = spec.merge(background="新背景", trait_keywords=None)
    assert merged.background == "新背景"
    assert merged.trait_keywords == ["热情"]  # None not applied
    assert merged.agent_name == "测试"  # unchanged

