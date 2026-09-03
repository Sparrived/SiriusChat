from __future__ import annotations

from types import SimpleNamespace

import pytest

from sirius_pulse.core.tool_engine_context import ToolEngineContextImpl
from sirius_pulse.providers.base import ToolCall
from sirius_pulse.tools.models import ToolResult


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler_result", "expected"),
    [
        (True, True),
        (False, False),
        ("accepted", False),
        (1, False),
        ({"accepted": True}, False),
        (None, False),
    ],
)
async def test_scheduled_dispatch_only_accepts_explicit_true(handler_result, expected):
    async def dispatch_proactive_message(**_kwargs):
        return handler_result

    context = ToolEngineContextImpl.__new__(ToolEngineContextImpl)
    context._engine = SimpleNamespace(
        dispatch_proactive_message=dispatch_proactive_message,
    )

    accepted = await context.dispatch_proactive_message(
        group_id="group-1",
        text="scheduled update",
    )

    assert accepted is expected


@pytest.mark.asyncio
async def test_scheduled_generation_runs_tool_calls_before_final_text():
    class Executor:
        def __init__(self):
            self.context = None
            self.calls = []

        def set_chat_context(self, **kwargs):
            self.context = kwargs

        async def execute_async(self, tool, params, **kwargs):
            self.calls.append((tool, params, kwargs))
            return ToolResult.from_raw_result({"success": True, "text": "tool output"})

    class Registry:
        def get(self, name):
            return SimpleNamespace(name=name, retry_safe=False)

    class Brain:
        def __init__(self):
            self.requests = []
            self.results = [
                SimpleNamespace(
                    raw_text="",
                    clean_text="",
                    tool_calls=[
                        ToolCall(
                            id="call-1",
                            function_name="web_lookup",
                            function_arguments='{"query":"weather"}',
                        )
                    ],
                ),
                SimpleNamespace(
                    raw_text="天气不错。",
                    clean_text="天气不错。",
                    tool_calls=[],
                    reply_references=[],
                    sticker_names=[],
                    poke_user_ids=[],
                ),
            ]

        async def chat(self, request):
            self.requests.append(request)
            return self.results.pop(0)

    executor = Executor()
    brain = Brain()
    engine = SimpleNamespace(
        persona=None,
        _tool_executor=executor,
        _tool_registry=Registry(),
        brain=brain,
        config={"max_tool_rounds": 2, "tool_execution_timeout": 5},
    )
    context = ToolEngineContextImpl.__new__(ToolEngineContextImpl)
    context._engine = engine
    context.get_tool_descriptions = lambda **_kwargs: "- web_lookup: 查询天气"

    result = await context.generate_scheduled_message(
        job={"expression": "*/5 * * * *", "command": "echo hello"},
        command_output="hello",
        group_id="group-1",
        user_id="u1",
        user_name="Alice",
        adapter_type="napcat",
    )

    assert result["text"] == "天气不错。"
    assert len(executor.calls) == 1
    assert executor.calls[0][1] == {"query": "weather"}
    assert executor.context == {
        "group_id": "group-1",
        "user_id": "u1",
        "adapter_type": "napcat",
    }
    assert len(brain.requests) == 2
    assert any(message["role"] == "tool" for message in brain.requests[1].messages)
