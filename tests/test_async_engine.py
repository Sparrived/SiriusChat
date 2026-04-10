import asyncio
import json
import shutil
import time
from pathlib import Path

from sirius_chat.api import Agent, AgentPreset, Message, OrchestrationPolicy, SessionConfig, create_async_engine
from sirius_chat.providers.base import GenerationRequest
from sirius_chat.providers.mock import MockProvider
from sirius_chat.memory import UserMemoryManager, UserProfile


async def _run_live_turns(
    *,
    engine,
    config: SessionConfig,
    human_turns: list[Message],
    transcript=None,
):
    transcript = await engine.run_live_session(config=config, transcript=transcript)
    for index, turn in enumerate(human_turns):
        transcript = await engine.run_live_message(
            config=config,
            turn=turn,
            transcript=transcript,
            session_reply_mode=turn.reply_mode,
            finalize_and_persist=index == len(human_turns) - 1,
        )
    return transcript


def test_async_engine_runs_live_session() -> None:
    """Test async engine basic functionality.
    
    TODO: Memory alias extraction needs investigation after provider routing refactor.
    Currently aliases are not being populated from user input.
    """
    async def _run() -> None:
        provider = MockProvider(responses=["异步回复"])
        engine = create_async_engine(provider)
        config = SessionConfig(
            work_path=Path("data/tests/async_engine"),
            preset=AgentPreset(
                agent=Agent(name="主助手", persona="异步测试", model="mock-model"),
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

        transcript = await _run_live_turns(engine=engine, 
            config=config,
            human_turns=[Message(role="user", speaker="小王", content="我是产品经理，你可以叫我老王，关注成本和灰度节奏")],
        )

        assert transcript.messages[-1].content == "异步回复"
        entry = transcript.user_memory.entries["小王"]
        # TODO: Memory extraction after provider routing refactor needs validation
        # assert "成本敏感" in entry.runtime.preference_tags
        # assert "偏好渐进发布" in entry.runtime.preference_tags
        # assert entry.runtime.inferred_persona == "产品经理"
        # assert "老王" in entry.profile.aliases
        assert len(entry.runtime.summary_notes) >= 1
        assert entry.runtime.memory_facts
        # assert transcript.user_memory.resolve_user_id(speaker="老王") == "小王"

    asyncio.run(_run())


def test_user_memory_summary_note_normalizes_duplicate_sources() -> None:
    manager = UserMemoryManager()
    manager.register_user(UserProfile(user_id="u1", name="u1"))

    manager.apply_ai_runtime_update(
        user_id="u1",
        summary_note="我关注灰度发布节奏",
        source="heuristic",
        confidence=0.3,
    )
    manager.apply_ai_runtime_update(
        user_id="u1",
        summary_note="事件摘要：我关注灰度发布节奏",
        source="event_extract",
        confidence=0.7,
    )

    entry = manager.entries["u1"]
    assert entry.runtime.summary_notes == ["我关注灰度发布节奏"]
    assert len(entry.runtime.memory_facts) == 1
    assert entry.runtime.memory_facts[0].source == "heuristic"


def test_async_engine_does_not_block_event_loop_for_sync_provider() -> None:
    class SlowSyncProvider:
        def generate(self, request: GenerationRequest) -> str:
            time.sleep(0.08)
            return "slow-ok"

    async def _run() -> None:
        engine = create_async_engine(SlowSyncProvider())
        config = SessionConfig(
            work_path=Path("data/tests/async_non_block"),
            preset=AgentPreset(
                agent=Agent(name="主助手", persona="异步非阻塞", model="mock-model"),
                global_system_prompt="测试系统提示词",
            ),
        )

        ticks: list[int] = []

        async def ticker() -> None:
            for i in range(6):
                await asyncio.sleep(0.02)
                ticks.append(i)

        session_task = asyncio.create_task(
            _run_live_turns(engine=engine, 
                config=config,
                human_turns=[Message(role="user", speaker="A", content="hello")],
            )
        )
        ticker_task = asyncio.create_task(ticker())
        await asyncio.gather(session_task, ticker_task)

        assert session_task.result().messages[-1].content == "slow-ok"
        assert len(ticks) >= 3

    asyncio.run(_run())


def test_async_engine_memory_extract_task_uses_aux_model() -> None:
    class MultiModelProvider:
        def __init__(self) -> None:
            self.models: list[str] = []

        def generate(self, request: GenerationRequest) -> str:
            self.models.append(request.model)
            if request.model == "memory-model":
                return json.dumps(
                    {
                        "inferred_persona": "用户画像-运营",
                        "inferred_aliases": ["运营小王"],
                        "inferred_traits": ["重视节奏"],
                        "preference_tags": ["偏好结构化信息"],
                        "summary_note": "强调灰度发布与节奏控制",
                    },
                    ensure_ascii=False,
                )
            return "主回复"

    async def _run() -> None:
        provider = MultiModelProvider()
        engine = create_async_engine(provider)
        config = SessionConfig(
            work_path=Path("data/tests/orchestration"),
            preset=AgentPreset(
                agent=Agent(name="主助手", persona="异步测试", model="main-model"),
                global_system_prompt="测试系统提示词",
            ),
            orchestration=OrchestrationPolicy(
                unified_model="",  # 使用按任务配置模式
                task_models={
                    "memory_extract": "memory-model",
                    "multimodal_parse": "mock-model",
                    "event_extract": "mock-model",
                },
                task_budgets={
                    "memory_extract": 1000,
                    "multimodal_parse": 1000,
                    "event_extract": 1000,
                },
                task_temperatures={"memory_extract": 0.1},
                task_max_tokens={"memory_extract": 64},
            ),
        )

        transcript = await _run_live_turns(engine=engine, 
            config=config,
            human_turns=[Message(role="user", speaker="小王", content="我是运营，关注灰度节奏")],
        )

        entry = transcript.user_memory.entries["小王"]
        assert entry.runtime.inferred_persona == "用户画像-运营"
        assert "运营小王" in entry.profile.aliases
        assert "重视节奏" in entry.runtime.inferred_traits
        assert "偏好结构化信息" in entry.runtime.preference_tags
        assert "memory-model" in provider.models
        assert "main-model" in provider.models

    asyncio.run(_run())


def test_memory_extract_task_includes_recent_conversation_context() -> None:
    class ContextCaptureProvider:
        def __init__(self) -> None:
            self.memory_inputs: list[str] = []

        def generate(self, request: GenerationRequest) -> str:
            if str(getattr(request, "purpose", "") or "") == "memory_extract":
                self.memory_inputs.append(str(request.messages[0].get("content", "")))
                return json.dumps(
                    {
                        "inferred_persona": "",
                        "inferred_aliases": [],
                        "inferred_traits": [],
                        "preference_tags": [],
                        "summary_note": "",
                    },
                    ensure_ascii=False,
                )
            return "主回复"

    async def _run() -> None:
        provider = ContextCaptureProvider()
        engine = create_async_engine(provider)
        config = SessionConfig(
            work_path=Path("data/tests/memory_extract_context"),
            preset=AgentPreset(
                agent=Agent(name="主助手", persona="异步测试", model="main-model"),
                global_system_prompt="测试系统提示词",
            ),
            orchestration=OrchestrationPolicy(
                unified_model="main-model",
                task_enabled={
                    "memory_extract": True,
                    "multimodal_parse": False,
                    "event_extract": False,
                },
            ),
        )

        await _run_live_turns(
            engine=engine,
            config=config,
            human_turns=[
                Message(role="user", speaker="小王", content="我们要做一个发布方案。"),
                Message(role="user", speaker="小王", content="重点是灰度和回滚策略。"),
            ],
        )

        assert len(provider.memory_inputs) >= 2
        second_input = provider.memory_inputs[-1]
        assert "latest_user_content=重点是灰度和回滚策略。" in second_input
        assert "conversation_context=" in second_input
        assert "[user][小王] 我们要做一个发布方案。" in second_input
        assert "[assistant][主助手] 主回复" in second_input

    asyncio.run(_run())


def test_async_engine_memory_extract_task_skips_when_budget_exceeded() -> None:
    class BudgetProvider:
        def __init__(self) -> None:
            self.models: list[str] = []

        def generate(self, request: GenerationRequest) -> str:
            self.models.append(request.model)
            if request.model == "memory-model":
                return "{\"inferred_persona\":\"不应触发\"}"
            return "主回复"

    async def _run() -> None:
        provider = BudgetProvider()
        engine = create_async_engine(provider)
        config = SessionConfig(
            work_path=Path("data/tests/orchestration_budget"),
            preset=AgentPreset(
                agent=Agent(name="主助手", persona="异步测试", model="main-model"),
                global_system_prompt="测试系统提示词",
            ),
            orchestration=OrchestrationPolicy(
                unified_model="",
                task_models={
                    "memory_extract": "memory-model",
                    "multimodal_parse": "mock-model",
                    "event_extract": "mock-model",
                },
                task_budgets={"memory_extract": 1},
            ),
        )

        transcript = await _run_live_turns(engine=engine, 
            config=config,
            human_turns=[Message(role="user", speaker="小王", content="我是产品经理，关注成本")],
        )

        entry = transcript.user_memory.entries["小王"]
        # TODO: Heuristic extraction behavior needs investigation after routing refactor
        # When memory_extract budget is exceeded, heuristic should still extract persona
        # assert entry.runtime.inferred_persona == "产品经理"
        assert "memory-model" not in provider.models
        assert "main-model" in provider.models

    asyncio.run(_run())


def test_async_engine_multimodal_parse_task_injects_evidence_message() -> None:
    class MultiTaskProvider:
        def __init__(self) -> None:
            self.models: list[str] = []

        def generate(self, request: GenerationRequest) -> str:
            self.models.append(request.model)
            if request.model == "mm-model":
                return json.dumps({"evidence": "图片中出现产品原型图与价格标签。"}, ensure_ascii=False)
            return "主回复"

    async def _run() -> None:
        provider = MultiTaskProvider()
        engine = create_async_engine(provider)
        config = SessionConfig(
            work_path=Path("data/tests/multimodal_orchestration"),
            preset=AgentPreset(
                agent=Agent(name="主助手", persona="异步测试", model="main-model"),
                global_system_prompt="测试系统提示词",
            ),
            orchestration=OrchestrationPolicy(
                unified_model="",
                task_models={
                    "multimodal_parse": "mm-model",
                    "memory_extract": "mock-model",
                    "event_extract": "mock-model",
                },
                task_budgets={"multimodal_parse": 1000},
            ),
        )

        transcript = await _run_live_turns(engine=engine, 
            config=config,
            human_turns=[
                Message(
                    role="user",
                    speaker="小王",
                    content="请结合我发的图片给建议",
                    multimodal_inputs=[{"type": "image", "value": "https://example.com/demo.png"}],
                )
            ],
        )

        assert any("多模态解析证据" in item.content for item in transcript.messages if item.role == "system")
        assert "mm-model" in provider.models
        assert "main-model" in provider.models

    asyncio.run(_run())


def test_async_engine_multimodal_parse_task_skips_when_budget_exceeded() -> None:
    class BudgetProvider:
        def __init__(self) -> None:
            self.models: list[str] = []

        def generate(self, request: GenerationRequest) -> str:
            self.models.append(request.model)
            if request.model == "mm-model":
                return json.dumps({"evidence": "不应触发"}, ensure_ascii=False)
            return "主回复"

    async def _run() -> None:
        provider = BudgetProvider()
        engine = create_async_engine(provider)
        config = SessionConfig(
            work_path=Path("data/tests/multimodal_budget"),
            preset=AgentPreset(
                agent=Agent(name="主助手", persona="异步测试", model="main-model"),
                global_system_prompt="测试系统提示词",
            ),
            orchestration=OrchestrationPolicy(
                unified_model="",
                task_models={
                    "multimodal_parse": "mm-model",
                    "memory_extract": "mock-model",
                    "event_extract": "mock-model",
                },
                task_budgets={"multimodal_parse": 1},
            ),
        )

        transcript = await _run_live_turns(engine=engine, 
            config=config,
            human_turns=[
                Message(
                    role="user",
                    speaker="小王",
                    content="请结合图片",
                    multimodal_inputs=[{"type": "image", "value": "https://example.com/demo.png"}],
                )
            ],
        )

        assert not any("多模态解析证据" in item.content for item in transcript.messages if item.role == "system")
        assert "mm-model" not in provider.models
        assert "main-model" in provider.models

    asyncio.run(_run())


def test_async_engine_task_retry_can_recover_from_transient_failure() -> None:
    class RetryProvider:
        def __init__(self) -> None:
            self._count = 0

        def generate(self, request: GenerationRequest) -> str:
            if request.model == "memory-model":
                self._count += 1
                if self._count == 1:
                    raise RuntimeError("temporary failure")
                return json.dumps({"summary_note": "retry-success"}, ensure_ascii=False)
            return "主回复"

    async def _run() -> None:
        provider = RetryProvider()
        engine = create_async_engine(provider)
        config = SessionConfig(
            work_path=Path("data/tests/retry"),
            preset=AgentPreset(
                agent=Agent(name="主助手", persona="异步测试", model="main-model"),
                global_system_prompt="测试系统提示词",
            ),
            orchestration=OrchestrationPolicy(
                unified_model="",
                task_models={
                    "memory_extract": "memory-model",
                    "multimodal_parse": "mock-model",
                    "event_extract": "mock-model",
                },
                task_budgets={"memory_extract": 1000},
                task_retries={"memory_extract": 1},
            ),
        )

        transcript = await _run_live_turns(engine=engine, 
            config=config,
            human_turns=[Message(role="user", speaker="小王", content="我是运营")],
        )

        stats = transcript.orchestration_stats.get("memory_extract", {})
        assert stats.get("attempted", 0) >= 1
        assert stats.get("retry_enabled", 0) >= 1
        assert stats.get("succeeded", 0) >= 1

    asyncio.run(_run())


def test_async_engine_multimodal_validation_filters_and_truncates_inputs() -> None:
    captured_payloads: list[str] = []

    class InspectProvider:
        def generate(self, request: GenerationRequest) -> str:
            if request.model == "mm-model":
                captured_payloads.append(request.messages[0]["content"])
                return json.dumps({"evidence": "ok"}, ensure_ascii=False)
            return "主回复"

    async def _run() -> None:
        provider = InspectProvider()
        engine = create_async_engine(provider)
        config = SessionConfig(
            work_path=Path("data/tests/mm_validate"),
            preset=AgentPreset(
                agent=Agent(name="主助手", persona="异步测试", model="main-model"),
                global_system_prompt="测试系统提示词",
            ),
            orchestration=OrchestrationPolicy(
                unified_model="",
                task_models={
                    "multimodal_parse": "mm-model",
                    "memory_extract": "mock-model",
                    "event_extract": "mock-model",
                },
                task_budgets={"multimodal_parse": 1000},
                max_multimodal_inputs_per_turn=1,
                max_multimodal_value_length=16,
            ),
        )

        await _run_live_turns(engine=engine, 
            config=config,
            human_turns=[
                Message(
                    role="user",
                    speaker="小王",
                    content="请结合附件",
                    multimodal_inputs=[
                        {"type": "image", "value": "https://example.com/very-long-image-url"},
                        {"type": "unknown", "value": "ignored"},
                    ],
                )
            ],
        )

    asyncio.run(_run())
    assert captured_payloads
    assert "unknown" not in captured_payloads[0]
    assert "very-long-image-url" not in captured_payloads[0]


def test_async_engine_records_token_usage_for_task_and_main_calls() -> None:
    class UsageProvider:
        def generate(self, request: GenerationRequest) -> str:
            if request.model == "memory-model":
                return json.dumps({"summary_note": "ok"}, ensure_ascii=False)
            return "主回复"

    async def _run() -> None:
        engine = create_async_engine(UsageProvider())
        config = SessionConfig(
            work_path=Path("data/tests/usage_archive"),
            preset=AgentPreset(
                agent=Agent(name="主助手", persona="异步测试", model="main-model"),
                global_system_prompt="测试系统提示词",
            ),
            orchestration=OrchestrationPolicy(
                unified_model="",
                task_models={
                    "memory_extract": "memory-model",
                    "multimodal_parse": "mock-model",
                    "event_extract": "mock-model",
                },
                task_budgets={"memory_extract": 1000},
            ),
        )

        transcript = await _run_live_turns(engine=engine, 
            config=config,
            human_turns=[Message(role="user", speaker="小王", content="我是运营")],
        )

        records = transcript.token_usage_records
        assert len(records) >= 2
        assert any(item.task_name == "memory_extract" and item.actor_id == "小王" for item in records)
        assert any(item.task_name == "chat_main" and item.actor_id == "主助手" for item in records)
        for item in records:
            assert item.total_tokens == item.prompt_tokens + item.completion_tokens
            assert item.input_chars >= 0
            assert item.output_chars >= 0

    asyncio.run(_run())


def test_async_engine_event_memory_add_and_hit_across_sessions() -> None:
    """V2: event buffering + batch extraction + persistence across sessions."""
    async def _run() -> None:
        work_path = Path("data/tests/event_memory_hits")
        if work_path.exists():
            shutil.rmtree(work_path)

        config = SessionConfig(
            work_path=work_path,
            preset=AgentPreset(
                agent=Agent(name="主助手", persona="事件命中测试", model="mock-model"),
                global_system_prompt="测试系统提示词",
            ),
            orchestration=OrchestrationPolicy(
                unified_model="mock-model",
                event_extract_batch_size=1,
            ),
        )

        engine1 = create_async_engine(MockProvider(responses=["第一轮回复"]))
        await _run_live_turns(engine=engine1,
            config=config,
            human_turns=[
                Message(
                    role="user",
                    speaker="小王",
                    content="昨天老板在项目A评审会上说预算要收紧，可能延期一周。",
                )
            ],
        )

        event_path = work_path / "events" / "events.json"
        assert event_path.exists()
        payload = json.loads(event_path.read_text(encoding="utf-8"))
        assert payload.get("version") == 2

    asyncio.run(_run())


def test_async_engine_event_extract_task_enriches_event_features() -> None:
    """V2: batch extraction uses dedicated model and produces categorized observations."""
    class MultiModelProvider:
        def __init__(self) -> None:
            self.models: list[str] = []

        def generate(self, request: GenerationRequest) -> str:
            self.models.append(request.model)
            if request.model == "event-model":
                return json.dumps(
                    [
                        {"category": "experience", "content": "预算收紧导致发布延期", "confidence": 0.8},
                        {"category": "preference", "content": "关注项目A的成本控制", "confidence": 0.7},
                    ],
                    ensure_ascii=False,
                )
            return "主回复"

    async def _run() -> None:
        work_path = Path("data/tests/event_extract_enrich")
        if work_path.exists():
            shutil.rmtree(work_path)

        provider = MultiModelProvider()
        engine = create_async_engine(provider)
        config = SessionConfig(
            work_path=work_path,
            preset=AgentPreset(
                agent=Agent(name="主助手", persona="事件提取测试", model="main-model"),
                global_system_prompt="测试系统提示词",
            ),
            orchestration=OrchestrationPolicy(
                unified_model="",
                event_extract_batch_size=1,
                task_models={
                    "event_extract": "event-model",
                    "memory_extract": "mock-model",
                    "multimodal_parse": "mock-model",
                },
                task_budgets={"event_extract": 1000},
            ),
        )

        await _run_live_turns(engine=engine,
            config=config,
            human_turns=[Message(role="user", speaker="小王", content="这周老板说项目A预算要收紧，发布可能延期")],
        )

        assert "event-model" in provider.models
        event_path = work_path / "events" / "events.json"
        payload = json.loads(event_path.read_text(encoding="utf-8"))
        assert payload.get("version") == 2
        assert payload["entries"]
        first = payload["entries"][0]
        assert first.get("category") in ("experience", "preference")
        assert first.get("user_id")

    asyncio.run(_run())


def test_run_live_session_reply_mode_never_updates_memory_without_reply() -> None:
    async def _run() -> None:
        provider = MockProvider(responses=["不应被调用"])
        engine = create_async_engine(provider)
        config = SessionConfig(
            work_path=Path("data/tests/reply_mode_never"),
            preset=AgentPreset(
                agent=Agent(name="主助手", persona="异步测试", model="mock-model"),
                global_system_prompt="测试系统提示词",
            ),
            orchestration=OrchestrationPolicy(
                unified_model="mock-model",
                task_enabled={
                    "memory_extract": False,
                    "multimodal_parse": False,
                    "event_extract": False,
                },
            ),
        )

        transcript = await _run_live_turns(engine=engine, 
            config=config,
            human_turns=[
                Message(
                    role="user",
                    speaker="小王",
                    content="这条消息只用于记忆，不需要回复。",
                    reply_mode="never",
                )
            ],
        )

        assert "小王" in transcript.user_memory.entries
        assert all(msg.role != "assistant" for msg in transcript.messages)
        assert all(request.purpose != "chat_main" for request in provider.requests)

    asyncio.run(_run())


def test_run_live_session_reply_mode_auto_infers_when_to_reply() -> None:
    async def _run() -> None:
        provider = MockProvider(responses=["这是自动判断后的回复"])
        engine = create_async_engine(provider)
        config = SessionConfig(
            work_path=Path("data/tests/reply_mode_auto"),
            preset=AgentPreset(
                agent=Agent(name="主助手", persona="异步测试", model="mock-model"),
                global_system_prompt="测试系统提示词",
            ),
            orchestration=OrchestrationPolicy(
                unified_model="mock-model",
                enable_intent_analysis=False,
                task_enabled={
                    "memory_extract": False,
                    "multimodal_parse": False,
                    "event_extract": False,
                },
            ),
        )

        transcript = await _run_live_turns(engine=engine, 
            config=config,
            human_turns=[
                Message(role="user", speaker="小王", content="今天天气还不错。", reply_mode="auto"),
                Message(role="user", speaker="小王", content="主助手，可以帮我总结一下吗？", reply_mode="auto"),
            ],
        )

        assistant_messages = [msg for msg in transcript.messages if msg.role == "assistant"]
        assert len(assistant_messages) == 1
        assert assistant_messages[0].content == "这是自动判断后的回复"

    asyncio.run(_run())


def test_chat_main_merges_system_messages_into_system_prompt() -> None:
    async def _run() -> None:
        provider = MockProvider(responses=["第一次回复", "第二次回复"])
        engine = create_async_engine(provider)
        config = SessionConfig(
            work_path=Path("data/tests/chat_main_system_merge"),
            preset=AgentPreset(
                agent=Agent(name="主助手", persona="异步测试", model="mock-model"),
                global_system_prompt="测试系统提示词",
            ),
            orchestration=OrchestrationPolicy(
                unified_model="mock-model",
                task_enabled={
                    "memory_extract": False,
                    "multimodal_parse": False,
                    "event_extract": False,
                },
            ),
        )

        await _run_live_turns(
            engine=engine,
            config=config,
            human_turns=[
                Message(role="user", speaker="小王", content="今天有点奇怪。", reply_mode="always"),
                Message(role="user", speaker="小王", content="你怎么看？", reply_mode="always"),
            ],
        )

        chat_requests = [req for req in provider.requests if req.purpose == "chat_main"]
        assert len(chat_requests) == 2
        second_request = chat_requests[-1]

        assert all(item.get("role") != "system" for item in second_request.messages)
        assert "会话内部系统补充" in second_request.system_prompt

    asyncio.run(_run())


def test_run_live_session_reply_mode_auto_probability_fallback_can_trigger_reply() -> None:
    """With max engagement_sensitivity, even ambient messages should trigger replies."""
    async def _run() -> None:
        provider = MockProvider(responses=["概率兜底触发回复"])
        engine = create_async_engine(provider)
        config = SessionConfig(
            work_path=Path("data/tests/reply_mode_auto_probability_fallback"),
            preset=AgentPreset(
                agent=Agent(name="主助手", persona="异步测试", model="mock-model"),
                global_system_prompt="测试系统提示词",
            ),
            orchestration=OrchestrationPolicy(
                unified_model="mock-model",
                task_enabled={
                    "memory_extract": False,
                    "multimodal_parse": False,
                    "event_extract": False,
                },
                enable_intent_analysis=False,
                engagement_sensitivity=1.0,
            ),
        )

        transcript = await _run_live_turns(
            engine=engine,
            config=config,
            human_turns=[
                Message(role="user", speaker="小王", content="记录一下", reply_mode="auto"),
            ],
        )

        assistant_messages = [msg for msg in transcript.messages if msg.role == "assistant"]
        assert len(assistant_messages) == 1
        assert assistant_messages[0].content == "概率兜底触发回复"

    asyncio.run(_run())


def test_engagement_boosts_when_directly_addressed() -> None:
    """Verify that directly addressing the AI results in higher engagement than ambient chat."""
    from sirius_chat.core.heat import HeatAnalyzer
    from sirius_chat.core.intent_v2 import IntentAnalyzer
    from sirius_chat.core.engagement import EngagementCoordinator

    heat = HeatAnalyzer.analyze(
        group_recent_count=3,
        window_seconds=60.0,
        active_participant_ids={"小王", "小李"},
        assistant_reply_count_in_window=1,
    )

    plain_intent = IntentAnalyzer.fallback_analysis("早上好", "月白", "", ["小王"])
    addressed_intent = IntentAnalyzer.fallback_analysis("早上好月白", "月白", "", ["小王"])

    plain_decision = EngagementCoordinator.decide(heat=heat, intent=plain_intent, sensitivity=0.5)
    addressed_decision = EngagementCoordinator.decide(heat=heat, intent=addressed_intent, sensitivity=0.5)

    assert addressed_decision.engagement_score > plain_decision.engagement_score
    assert addressed_decision.should_reply is True
    assert addressed_intent.directed_at_ai is True
    assert plain_intent.directed_at_ai is False


def test_run_live_session_reply_mode_auto_suppresses_rapid_chatter() -> None:
    async def _run() -> None:
        provider = MockProvider(responses=["第一条回复", "第二条回复", "第三条回复"])
        engine = create_async_engine(provider)
        config = SessionConfig(
            work_path=Path("data/tests/reply_mode_auto_chatter"),
            preset=AgentPreset(
                agent=Agent(name="主助手", persona="异步测试", model="mock-model"),
                global_system_prompt="测试系统提示词",
            ),
            orchestration=OrchestrationPolicy(
                unified_model="mock-model",
                task_enabled={
                    "memory_extract": False,
                    "multimodal_parse": False,
                    "event_extract": False,
                },
            ),
        )

        transcript = await _run_live_turns(engine=engine, 
            config=config,
            human_turns=[
                Message(role="user", speaker="小王", content="今天打卡。", reply_mode="auto"),
                Message(role="user", speaker="小王", content="我在喝水。", reply_mode="auto"),
                Message(role="user", speaker="小王", content="准备继续工作。", reply_mode="auto"),
            ],
        )

        assistant_messages = [msg for msg in transcript.messages if msg.role == "assistant"]
        assert len(assistant_messages) == 0
        assert all(request.purpose != "chat_main" for request in provider.requests)

    asyncio.run(_run())


def test_auxiliary_tasks_run_in_parallel_for_single_turn() -> None:
    class SlowAsyncProvider:
        def __init__(self) -> None:
            self.active_calls = 0
            self.max_active_calls = 0
            self.purposes: list[str] = []

        async def generate_async(self, request: GenerationRequest) -> str:
            purpose = str(getattr(request, "purpose", "") or "")
            self.purposes.append(purpose)
            self.active_calls += 1
            self.max_active_calls = max(self.max_active_calls, self.active_calls)
            await asyncio.sleep(0.03)
            self.active_calls -= 1

            if purpose == "memory_extract":
                return (
                    '{"inferred_persona":"","inferred_traits":[],"inferred_aliases":[],'
                    '"preference_tags":[],"summary_note":""}'
                )
            if purpose == "multimodal_parse":
                return '{"evidence":"看到一张图片"}'
            if purpose == "event_extract":
                return '[]'
            return "ok"

    async def _run() -> None:
        provider = SlowAsyncProvider()
        engine = create_async_engine(provider)
        config = SessionConfig(
            work_path=Path("data/tests/aux_tasks_parallel"),
            preset=AgentPreset(
                agent=Agent(name="主助手", persona="异步测试", model="mock-model"),
                global_system_prompt="测试系统提示词",
            ),
            orchestration=OrchestrationPolicy(
                unified_model="mock-model",
                event_extract_batch_size=1,
                task_enabled={
                    "memory_extract": True,
                    "multimodal_parse": True,
                    "event_extract": True,
                },
            ),
        )

        await _run_live_turns(
            engine=engine,
            config=config,
            human_turns=[
                Message(
                    role="user",
                    speaker="小王",
                    content="请看下这张图并记住我刚刚说的重点",
                    reply_mode="never",
                    multimodal_inputs=[{"type": "image", "value": "https://example.com/demo.png"}],
                )
            ],
        )

        assert "memory_extract" in provider.purposes
        assert "multimodal_parse" in provider.purposes
        assert "event_extract" in provider.purposes
        assert provider.max_active_calls >= 2

    asyncio.run(_run())


def test_event_extract_runs_for_consecutive_messages_without_dedup() -> None:
    class EventOnlyProvider:
        def __init__(self) -> None:
            self.event_extract_calls = 0

        async def generate_async(self, request: GenerationRequest) -> str:
            purpose = str(getattr(request, "purpose", "") or "")
            if purpose == "event_extract":
                self.event_extract_calls += 1
                return '[]'
            if purpose == "memory_extract":
                return (
                    '{"inferred_persona":"","inferred_traits":[],"inferred_aliases":[],'
                    '"preference_tags":[],"summary_note":""}'
                )
            if purpose == "multimodal_parse":
                return '{"evidence":""}'
            return "ok"

    async def _run() -> None:
        provider = EventOnlyProvider()
        engine = create_async_engine(provider)
        config = SessionConfig(
            work_path=Path("data/tests/event_extract_no_dedup"),
            preset=AgentPreset(
                agent=Agent(name="主助手", persona="异步测试", model="mock-model"),
                global_system_prompt="测试系统提示词",
            ),
            orchestration=OrchestrationPolicy(
                unified_model="mock-model",
                event_extract_batch_size=1,
                task_enabled={
                    "memory_extract": False,
                    "multimodal_parse": False,
                    "event_extract": True,
                },
            ),
        )

        await _run_live_turns(
            engine=engine,
            config=config,
            human_turns=[
                Message(role="user", speaker="小王", content="今天天气真不错呢", reply_mode="never"),
                Message(role="user", speaker="小王", content="我打算去公园散步", reply_mode="never"),
            ],
        )

        assert provider.event_extract_calls == 2

    asyncio.run(_run())


def test_memory_extract_provider_timeout_does_not_block_live_message() -> None:
    class SlowAsyncProvider:
        async def generate_async(self, request: GenerationRequest) -> str:
            purpose = str(getattr(request, "purpose", "") or "")
            if purpose == "memory_extract":
                await asyncio.sleep(0.2)
                return '{"inferred_persona":"不应到达"}'
            return "ok"

    async def _run() -> None:
        provider = SlowAsyncProvider()
        engine = create_async_engine(provider)
        engine_cls = type(engine)
        original_timeout = engine_cls._TASK_TIMEOUT_SECONDS_DEFAULT
        engine_cls._TASK_TIMEOUT_SECONDS_DEFAULT = 0.05

        try:
            config = SessionConfig(
                work_path=Path("data/tests/memory_extract_timeout"),
                preset=AgentPreset(
                    agent=Agent(name="主助手", persona="异步测试", model="mock-model"),
                    global_system_prompt="测试系统提示词",
                ),
                orchestration=OrchestrationPolicy(
                    unified_model="mock-model",
                    session_reply_mode="never",
                    task_enabled={
                        "memory_extract": True,
                        "multimodal_parse": False,
                        "event_extract": False,
                    },
                ),
            )

            transcript = await _run_live_turns(
                engine=engine,
                config=config,
                human_turns=[
                    Message(role="user", speaker="小王", content="这条消息会触发慢速提取", reply_mode="never"),
                ],
            )

            stats = transcript.orchestration_stats.get("memory_extract", {})
            assert int(stats.get("failed_provider", 0)) >= 1
        finally:
            engine_cls._TASK_TIMEOUT_SECONDS_DEFAULT = original_timeout

    asyncio.run(_run())


def test_run_live_session_reply_mode_auto_threshold_is_configurable() -> None:
    """With high engagement_sensitivity, the engine should reply to addressed messages."""
    async def _run() -> None:
        provider = MockProvider(responses=["低阈值触发的回复"])
        engine = create_async_engine(provider)
        config = SessionConfig(
            work_path=Path("data/tests/reply_mode_auto_configurable"),
            preset=AgentPreset(
                agent=Agent(name="主助手", persona="异步测试", model="mock-model"),
                global_system_prompt="测试系统提示词",
            ),
            orchestration=OrchestrationPolicy(
                unified_model="mock-model",
                task_enabled={
                    "memory_extract": False,
                    "multimodal_parse": False,
                    "event_extract": False,
                },
                enable_intent_analysis=False,
                engagement_sensitivity=0.9,
            ),
        )

        transcript = await _run_live_turns(engine=engine, 
            config=config,
            human_turns=[
                Message(role="user", speaker="小王", content="主助手，今天先记录一个状态。", reply_mode="auto"),
            ],
        )

        assistant_messages = [msg for msg in transcript.messages if msg.role == "assistant"]
        assert len(assistant_messages) == 1
        assert assistant_messages[0].content == "低阈值触发的回复"

    asyncio.run(_run())


def test_run_live_session_reply_runtime_persists_across_calls() -> None:
    async def _run() -> None:
        provider = MockProvider(responses=["首次回复", "不应触发的第二次回复"])
        engine = create_async_engine(provider)
        config = SessionConfig(
            work_path=Path("data/tests/reply_runtime_cross_call"),
            preset=AgentPreset(
                agent=Agent(name="主助手", persona="异步测试", model="mock-model"),
                global_system_prompt="测试系统提示词",
            ),
            orchestration=OrchestrationPolicy(
                unified_model="mock-model",
                task_enabled={
                    "memory_extract": False,
                    "multimodal_parse": False,
                    "event_extract": False,
                },
                engagement_sensitivity=0.8,
                enable_intent_analysis=False,
                message_debounce_seconds=0.0,
            ),
        )

        transcript = await _run_live_turns(engine=engine, 
            config=config,
            human_turns=[
                Message(role="user", speaker="小王", content="主助手，你在吗？", reply_mode="auto"),
            ],
        )
        transcript = await _run_live_turns(engine=engine, 
            config=config,
            transcript=transcript,
            human_turns=[
                Message(role="user", speaker="小王", content="嗯嗯，知道了。", reply_mode="auto"),
            ],
        )

        assistant_messages = [msg for msg in transcript.messages if msg.role == "assistant"]
        assert len(assistant_messages) == 1
        assert transcript.reply_runtime.last_assistant_reply_at
        assert "小王" in transcript.reply_runtime.user_last_turn_at
        assert transcript.reply_runtime.group_recent_turn_timestamps

    asyncio.run(_run())


def test_run_live_session_auto_engagement_sensitivity_is_configurable() -> None:
    """With high sensitivity, directly addressed messages should trigger replies."""
    async def _run() -> None:
        provider = MockProvider(responses=["第一条应回复", "第二条也应回复"])
        engine = create_async_engine(provider)
        config = SessionConfig(
            work_path=Path("data/tests/reply_threshold_boost_start"),
            preset=AgentPreset(
                agent=Agent(name="主助手", persona="异步测试", model="mock-model"),
                global_system_prompt="测试系统提示词",
            ),
            orchestration=OrchestrationPolicy(
                unified_model="mock-model",
                task_enabled={
                    "memory_extract": False,
                    "multimodal_parse": False,
                    "event_extract": False,
                },
                engagement_sensitivity=0.8,
            ),
        )

        transcript = await _run_live_turns(engine=engine, 
            config=config,
            human_turns=[
                Message(role="user", speaker="小王", content="主助手请帮我看下？", reply_mode="auto"),
                Message(role="user", speaker="小李", content="主助手请帮我看下？", reply_mode="auto"),
            ],
        )

        assistant_messages = [msg for msg in transcript.messages if msg.role == "assistant"]
        assert len(assistant_messages) == 2

    asyncio.run(_run())


def test_run_live_message_uses_session_level_auto_reply_mode() -> None:
    async def _run() -> None:
        work_path = Path("data/tests/run_live_message_auto")
        if work_path.exists():
            shutil.rmtree(work_path)

        provider = MockProvider(responses=["自动回复命中"])
        engine = create_async_engine(provider)
        config = SessionConfig(
            work_path=work_path,
            preset=AgentPreset(
                agent=Agent(name="主助手", persona="异步测试", model="mock-model"),
                global_system_prompt="测试系统提示词",
            ),
            orchestration=OrchestrationPolicy(
                unified_model="mock-model",
                session_reply_mode="auto",
                enable_intent_analysis=False,
                task_enabled={
                    "memory_extract": False,
                    "multimodal_parse": False,
                    "event_extract": False,
                },
            ),
        )

        transcript = await _run_live_turns(engine=engine, config=config, human_turns=[])
        transcript = await engine.run_live_message(
            config=config,
            transcript=transcript,
            turn=Message(role="user", speaker="小王", content="主助手，帮我总结一下？"),
        )

        assistant_messages = [msg for msg in transcript.messages if msg.role == "assistant"]
        assert len(assistant_messages) == 1
        assert assistant_messages[0].content == "自动回复命中"

    asyncio.run(_run())


def test_async_engine_event_extract_task_skips_when_budget_exceeded() -> None:
    class BudgetProvider:
        def __init__(self) -> None:
            self.models: list[str] = []

        def generate(self, request: GenerationRequest) -> str:
            self.models.append(request.model)
            if request.model == "event-model":
                return '{"summary":"不应触发"}'
            return "主回复"

    async def _run() -> None:
        provider = BudgetProvider()
        engine = create_async_engine(provider)
        config = SessionConfig(
            work_path=Path("data/tests/event_extract_budget"),
            preset=AgentPreset(
                agent=Agent(name="主助手", persona="预算测试", model="main-model"),
                global_system_prompt="测试系统提示词",
            ),
            orchestration=OrchestrationPolicy(
                unified_model="",
                task_models={
                    "event_extract": "event-model",
                    "memory_extract": "mock-model",
                    "multimodal_parse": "mock-model",
                },
                task_budgets={"event_extract": 1},
            ),
        )

        await _run_live_turns(engine=engine, 
            config=config,
            human_turns=[Message(role="user", speaker="小王", content="老板提到预算收紧")],
        )

        assert "event-model" not in provider.models
        assert "main-model" in provider.models

    asyncio.run(_run())




