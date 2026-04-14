import asyncio
import json
from pathlib import Path
import shutil

import pytest

from sirius_chat.api import (
    GENERATED_AGENTS_FILE_NAME,
    Agent,
    AgentPreset,
    aregenerate_agent_prompt_from_dependencies,
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
    list_roleplay_question_templates,
    load_generated_agent_library,
    load_persona_generation_traces,
    load_persona_spec,
    persist_generated_agent_profile,
    select_generated_agent_profile,
)
from sirius_chat.config import OrchestrationPolicy
from sirius_chat.providers.mock import MockProvider
from sirius_chat.workspace.layout import WorkspaceLayout


class RaisingMockProvider(MockProvider):
    def generate(self, request):  # type: ignore[override]
        self.requests.append(request)
        raise RuntimeError("mock generation failed")


def _generated_agents_path(work_path: Path) -> Path:
    return WorkspaceLayout(work_path).generated_agents_path()


def _trace_path(work_path: Path, agent_key: str) -> Path:
    return WorkspaceLayout(work_path).generated_agent_trace_dir() / f"{agent_key}.json"


def test_load_generated_agent_library_accepts_utf8_bom_files(tmp_path: Path) -> None:
    work_path = tmp_path / "roleplay_bom"
    generated_agents_path = _generated_agents_path(work_path)
    generated_agents_path.parent.mkdir(parents=True, exist_ok=True)
    generated_agents_path.write_text(
        json.dumps(
            {
                "selected_generated_agent": "bom_agent",
                "generated_agents": {
                    "bom_agent": {
                        "agent": {
                            "name": "BomAgent",
                            "persona": "bom persona",
                            "model": "mock-model",
                            "temperature": 0.7,
                            "max_tokens": 256,
                            "metadata": {},
                        },
                        "global_system_prompt": "bom prompt",
                    }
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8-sig",
    )

    agents, selected = load_generated_agent_library(work_path)

    assert selected == "bom_agent"
    assert agents["bom_agent"].agent.name == "BomAgent"


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
    assert len(questions) >= 9
    joined = "\n".join(item.question for item in questions)
    details = "\n".join(item.details for item in questions)
    assert "原型" in joined or "人生" in joined
    assert "矛盾" in joined or "张力" in joined
    assert "关系" in joined or "信任" in joined
    assert "情绪" in joined
    assert "价值" in joined or "看重" in joined
    assert "小缺点" in joined or "不完美" in details
    assert "不要直接写台词" in joined or "不要直接写完整回复" in details


def test_list_roleplay_question_templates_returns_supported_templates() -> None:
    assert list_roleplay_question_templates() == ["default", "companion", "romance", "group_chat"]


@pytest.mark.parametrize(
    ("template_name", "expected_terms"),
    [
        ("companion", ["陪伴", "安全感", "边界"]),
        ("romance", ["恋爱", "亲密", "边界"]),
        ("group_chat", ["群聊", "多人", "关系"]),
    ],
)
def test_generate_humanized_roleplay_questions_supports_scene_templates(
    template_name: str,
    expected_terms: list[str],
) -> None:
    questions = generate_humanized_roleplay_questions(template=template_name)
    joined = "\n".join(item.question + item.details for item in questions)

    assert len(questions) >= 8
    for term in expected_terms:
        assert term in joined


def test_generate_humanized_roleplay_questions_raises_on_unknown_template() -> None:
    with pytest.raises(ValueError, match="未知的人格问卷模板"):
        generate_humanized_roleplay_questions(template="mystery")


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
        assert _generated_agents_path(config.work_path).exists()

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
        payload = str(provider.requests[0].messages[0]["content"])
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

        assert _generated_agents_path(base_config.work_path).exists()
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
        payload = str(provider.requests[0].messages[0]["content"])
        assert "keywords=热情/直接/逻辑清晰" in payload
        assert "name=北辰" in payload
        assert provider.requests[0].max_tokens == 5120
        assert provider.requests[0].timeout_seconds == 120.0
        assert preset.agent.persona == "热情/直接/逻辑清晰"
        assert "北辰" in preset.global_system_prompt

    asyncio.run(_run())


def test_agenerate_from_persona_spec_allows_timeout_override() -> None:
    async def _run() -> None:
        provider = MockProvider(
            responses=[
                '{"agent_persona":"沉稳/克制","global_system_prompt":"你是临川，回复保持稳定和边界感。","temperature":0.4,"max_tokens":512}'
            ]
        )
        spec = PersonaSpec(
            agent_name="临川",
            trait_keywords=["沉稳", "克制"],
        )

        await agenerate_from_persona_spec(
            provider,
            spec,
            model="test-model",
            timeout_seconds=180.0,
        )

        assert provider.requests[0].timeout_seconds == 180.0

    asyncio.run(_run())


def test_agenerate_from_persona_spec_accepts_markdown_wrapped_json_response() -> None:
    async def _run() -> None:
        provider = MockProvider(
            responses=[
                '```json\n{"agent_persona":"AI猫娘/聪慧机灵","global_system_prompt":"你是月白，会像真人一样自然交流。","temperature":0.55,"max_tokens":640}\n```'
            ]
        )
        spec = PersonaSpec(
            agent_name="月白",
            trait_keywords=["猫娘", "自然交流"],
        )

        preset = await agenerate_from_persona_spec(
            provider,
            spec,
            model="test-model",
        )

        assert preset.agent.persona == "AI猫娘/聪慧机灵"
        assert preset.global_system_prompt == "你是月白，会像真人一样自然交流。"
        assert preset.agent.temperature == 0.55
        assert preset.agent.max_tokens == 640

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
        payload = str(provider.requests[0].messages[0]["content"])
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
        assert _generated_agents_path(config.work_path).exists()

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

        payload = json.loads(_generated_agents_path(config.work_path).read_text(encoding="utf-8"))
        assert "pending_persona_specs" not in payload

    asyncio.run(_run())


def test_abuild_persists_pending_persona_spec_before_generation_failure() -> None:
    async def _run() -> None:
        work_path = Path("data/tests/roleplay_pending_build_failure")
        if work_path.exists():
            shutil.rmtree(work_path)
        dependency_file = work_path / "persona.txt"
        dependency_file.parent.mkdir(parents=True, exist_ok=True)
        dependency_file.write_text("初始设定：更像一个慢热但可靠的朋友。", encoding="utf-8")

        provider = RaisingMockProvider()
        config = SessionConfig(
            work_path=work_path,
            preset=AgentPreset(
                agent=Agent(name="北辰", persona="默认", model="mock-model"),
                global_system_prompt="初始提示词",
            ),
        )

        with pytest.raises(RuntimeError, match="mock generation failed"):
            await abuild_roleplay_prompt_from_answers_and_apply(
                provider,
                config=config,
                model="test-model",
                answers=[RolePlayAnswer(question="关系", answer="像一个会慢慢建立信任的老朋友")],
                dependency_files=["persona.txt"],
                background="先把这些高层设定保存下来",
                persona_key="failed_build_case",
            )

        saved_spec = load_persona_spec(work_path, "failed_build_case")
        assert saved_spec is not None
        assert saved_spec.background == "先把这些高层设定保存下来"
        assert saved_spec.dependency_files == ["persona.txt"]
        assert saved_spec.answers[0].question == "关系"

        agents, selected = load_generated_agent_library(work_path)
        assert agents == {}
        assert selected == ""

        trace_payload = json.loads(_trace_path(work_path, "failed_build_case").read_text(encoding="utf-8"))
        assert trace_payload["history"] == []
        assert trace_payload["pending_trace"]["parsed_payload"]["stage"] == "generation_failed"
        assert "mock generation failed" in trace_payload["pending_trace"]["parsed_payload"]["error"]
        assert trace_payload["pending_trace"]["dependency_snapshots"][0]["content"].startswith("初始设定")

    asyncio.run(_run())


def test_abuild_rejects_truncated_json_like_response_instead_of_persisting_raw_text() -> None:
    async def _run() -> None:
        work_path = Path("data/tests/roleplay_truncated_json_response")
        if work_path.exists():
            shutil.rmtree(work_path)

        provider = MockProvider(
            responses=[
                '```json\n{"agent_persona":"AI猫娘/情感觉醒/聪慧机灵","global_system_prompt":"你是月白（Sirius），会像真人一样自然交流，擅长在陪伴中保留细腻情绪与边界感'
            ]
        )
        config = SessionConfig(
            work_path=work_path,
            preset=AgentPreset(
                agent=Agent(name="月白", persona="默认人格", model="mock-model"),
                global_system_prompt="初始系统提示词",
            ),
        )

        with pytest.raises(ValueError, match="人格生成响应疑似被截断或格式错误"):
            await abuild_roleplay_prompt_from_answers_and_apply(
                provider,
                config=config,
                model="test-model",
                answers=[RolePlayAnswer(question="关系", answer="像一个会慢慢建立信任的陪伴者")],
                persona_key="truncated_case",
            )

        assert config.agent.persona == "默认人格"
        assert config.global_system_prompt == "初始系统提示词"

        saved_spec = load_persona_spec(work_path, "truncated_case")
        assert saved_spec is not None
        assert saved_spec.answers[0].answer == "像一个会慢慢建立信任的陪伴者"

        trace_payload = json.loads(_trace_path(work_path, "truncated_case").read_text(encoding="utf-8"))
        assert trace_payload["pending_trace"]["raw_response"].startswith("```json")
        assert trace_payload["pending_trace"]["parsed_payload"]["truncated_fields"] == ["global_system_prompt"]

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


def test_generation_prompt_strengthens_anthropomorphic_and_emotional_keywords() -> None:
    async def _run() -> None:
        provider = MockProvider(
            responses=[
                '{"agent_persona":"温柔/拟人/陪伴","global_system_prompt":"像真实朋友一样陪伴","temperature":0.5,"max_tokens":512}'
            ]
        )
        spec = PersonaSpec(
            agent_name="星栖",
            trait_keywords=["拟人", "情感陪伴", "共情"],
            background="希望像真人朋友一样自然交流",
        )

        await agenerate_from_persona_spec(
            provider,
            spec,
            model="test-model",
        )

        request = provider.requests[0]
        assert "强化拟人感" in request.system_prompt
        assert "强化情绪表达" in request.system_prompt
        assert "上位人格 brief" in request.system_prompt
        assert "小缺点" in request.system_prompt
        assert "[Generation Goal]" in str(request.messages[0]["content"])
        assert "[Prompt Enhancements]" in str(request.messages[0]["content"])

    asyncio.run(_run())


def test_generation_prompt_requests_concrete_expansion_from_high_level_brief() -> None:
    async def _run() -> None:
        provider = MockProvider(
            responses=[
                '{"agent_persona":"慢热/可靠/克制","global_system_prompt":"生成结果","temperature":0.5,"max_tokens":512}'
            ]
        )
        spec = PersonaSpec(
            agent_name="临川",
            answers=[
                RolePlayAnswer(
                    question="这个角色最像哪类真人或人生原型？",
                    answer="像一个晚熟但可靠的老朋友，关系推进慢，但会长期在场。",
                )
            ],
        )

        await agenerate_from_persona_spec(
            provider,
            spec,
            model="test-model",
        )

        request = provider.requests[0]
        assert "先提炼人生原型" in request.system_prompt
        assert "再展开成具体可信的人物设定" in request.system_prompt
        assert "不要把原句直接拼贴成最终系统提示词" in str(request.messages[0]["content"])

    asyncio.run(_run())


def test_abuild_persists_persona_generation_trace_locally() -> None:
    async def _run() -> None:
        work_path = Path("data/tests/roleplay_trace_persist")
        if work_path.exists():
            shutil.rmtree(work_path)
        provider = MockProvider(
            responses=[
                '{"agent_persona":"沉稳/共情","global_system_prompt":"完整提示词","temperature":0.4,"max_tokens":640}'
            ]
        )
        config = SessionConfig(
            work_path=work_path,
            preset=AgentPreset(
                agent=Agent(name="北辰", persona="默认", model="mock-model"),
                global_system_prompt="初始提示词",
            ),
        )

        await abuild_roleplay_prompt_from_answers_and_apply(
            provider,
            config=config,
            model="test-model",
            answers=[RolePlayAnswer(question="风格", answer="沉稳但有温度")],
            persona_key="trace_case",
        )

        traces = load_persona_generation_traces(config.work_path, "trace_case")
        assert len(traces) == 1
        assert traces[0].operation == "build"
        assert "name=北辰" in traces[0].user_prompt
        assert "agent_persona" in traces[0].raw_response

        trace_payload = json.loads(_trace_path(config.work_path, "trace_case").read_text(encoding="utf-8"))
        assert "pending_trace" not in trace_payload

    asyncio.run(_run())


def test_aupdate_keeps_pending_spec_when_regeneration_fails() -> None:
    async def _run() -> None:
        work_path = Path("data/tests/roleplay_pending_update_failure")
        if work_path.exists():
            shutil.rmtree(work_path)
        provider = MockProvider(
            responses=[
                '{"agent_persona":"温和/耐心","global_system_prompt":"原始描述","temperature":0.6,"max_tokens":512}'
            ]
        )
        config = SessionConfig(
            work_path=work_path,
            preset=AgentPreset(
                agent=Agent(name="北辰", persona="默认", model="mock-model"),
                global_system_prompt="初始",
            ),
        )

        await abuild_roleplay_prompt_from_answers_and_apply(
            provider,
            config=config,
            model="test-model",
            answers=[RolePlayAnswer(question="性格", answer="温和耐心")],
            persona_key="update_failed_case",
        )

        with pytest.raises(RuntimeError, match="mock generation failed"):
            await aupdate_agent_prompt(
                RaisingMockProvider(),
                work_path=work_path,
                agent_key="update_failed_case",
                model="test-model",
                background="这次想补充一段新的成长经历",
            )

        saved_spec = load_persona_spec(work_path, "update_failed_case")
        assert saved_spec is not None
        assert saved_spec.background == "这次想补充一段新的成长经历"

        library, selected = load_generated_agent_library(work_path)
        assert selected == "update_failed_case"
        assert library["update_failed_case"].global_system_prompt == "原始描述"

        traces = load_persona_generation_traces(work_path, "update_failed_case")
        assert len(traces) == 1
        assert traces[0].operation == "build"

        trace_payload = json.loads(_trace_path(work_path, "update_failed_case").read_text(encoding="utf-8"))
        assert trace_payload["pending_trace"]["operation"] == "update"
        assert trace_payload["pending_trace"]["parsed_payload"]["stage"] == "generation_failed"

    asyncio.run(_run())


def test_agenerate_from_persona_spec_supports_dependency_files_only() -> None:
    async def _run() -> None:
        work_path = Path("data/tests/roleplay_dependency_only")
        if work_path.exists():
            shutil.rmtree(work_path)
        dependency_file = work_path / "persona" / "notes.txt"
        dependency_file.parent.mkdir(parents=True, exist_ok=True)
        dependency_file.write_text("角色长期像朋友一样陪伴用户，情绪表达要自然。", encoding="utf-8")

        provider = MockProvider(
            responses=[
                '{"agent_persona":"陪伴/温柔/拟人","global_system_prompt":"依赖文件驱动生成","temperature":0.45,"max_tokens":700}'
            ]
        )
        spec = PersonaSpec(
            agent_name="鹿鸣",
            dependency_files=["persona/notes.txt"],
        )

        preset = await agenerate_from_persona_spec(
            provider,
            spec,
            model="test-model",
            dependency_root=work_path,
        )

        assert preset.agent.persona == "陪伴/温柔/拟人"
        payload = str(provider.requests[0].messages[0]["content"])
        assert "[Dependency Files]" in payload
        assert "角色长期像朋友一样陪伴用户" in payload

    asyncio.run(_run())


def test_aregenerate_agent_prompt_from_dependencies_rereads_files() -> None:
    async def _run() -> None:
        work_path = Path("data/tests/roleplay_dependency_regenerate")
        if work_path.exists():
            shutil.rmtree(work_path)
        dependency_file = work_path / "persona.txt"
        dependency_file.parent.mkdir(parents=True, exist_ok=True)
        dependency_file.write_text("初版：更像克制的搭档。", encoding="utf-8")

        provider = MockProvider(
            responses=[
                '{"agent_persona":"克制/理性","global_system_prompt":"第一版人格","temperature":0.3,"max_tokens":512}',
                '{"agent_persona":"克制/理性/柔软","global_system_prompt":"第二版人格","temperature":0.35,"max_tokens":576}',
            ]
        )
        config = SessionConfig(
            work_path=work_path,
            preset=AgentPreset(
                agent=Agent(name="青岚", persona="默认", model="mock-model"),
                global_system_prompt="初始提示词",
            ),
        )

        await abuild_roleplay_prompt_from_answers_and_apply(
            provider,
            config=config,
            model="test-model",
            trait_keywords=["克制", "理性"],
            dependency_files=["persona.txt"],
            persona_key="regen_case",
        )

        dependency_file.write_text("二版：保留理性，但情绪更柔软，更像真实朋友。", encoding="utf-8")

        updated = await aregenerate_agent_prompt_from_dependencies(
            provider,
            work_path=work_path,
            agent_key="regen_case",
            model="test-model",
        )

        assert updated.agent.persona == "克制/理性/柔软"
        second_request = str(provider.requests[1].messages[0]["content"])
        assert "二版：保留理性，但情绪更柔软" in second_request

        traces = load_persona_generation_traces(work_path, "regen_case")
        assert len(traces) == 2
        assert traces[-1].operation == "regenerate_from_dependencies"
        assert traces[-1].dependency_snapshots[0].content.startswith("二版：")

    asyncio.run(_run())

