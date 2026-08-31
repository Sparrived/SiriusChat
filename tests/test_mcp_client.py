from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp.types import CallToolResult, TextContent

from sirius_pulse.tools.executor import ToolExecutor
from sirius_pulse.tools.mcp_client import MCPClientManager, _result_to_tool_result, _tool_name


class _FakeSession:
    def __init__(self, result: CallToolResult) -> None:
        self.result = result
        self.calls: list[tuple[str, dict]] = []

    async def list_tools(self):
        return SimpleNamespace(
            tools=[
                SimpleNamespace(
                    name="lookup",
                    description="查询资料",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "关键词"},
                            "options": {"type": "object"},
                        },
                        "required": ["query"],
                    },
                )
            ]
        )

    async def call_tool(self, name: str, *, arguments: dict):
        self.calls.append((name, arguments))
        return self.result


class _FakeManager(MCPClientManager):
    def __init__(self, session: _FakeSession) -> None:
        super().__init__({"servers": {"demo": {"enabled": True}}})
        self.session = session

    async def _connect(self, server_name: str, config: dict):
        self._sessions[server_name] = self.session
        return self.session


@pytest.mark.asyncio
async def test_mcp_tools_preserve_schema_and_execute_without_framework_injection(
    tmp_path: Path,
):
    session = _FakeSession(CallToolResult(content=[TextContent(type="text", text="结果")]))
    manager = _FakeManager(session)
    tools = await manager.load_tools()

    assert [tool.name for tool in tools] == ["mcp_demo_lookup"]
    assert tools[0].to_tool_schema()["function"]["parameters"]["properties"]["options"] == {
        "type": "object"
    }

    result = await ToolExecutor(tmp_path).execute_async(
        tools[0],
        {"query": "天气", "options": {"region": "华东"}},
    )

    assert result.success is True
    assert result.to_display_text() == "结果"
    assert session.calls == [("lookup", {"query": "天气", "options": {"region": "华东"}})]
    await manager.close()


@pytest.mark.asyncio
async def test_mcp_server_failure_does_not_block_other_servers():
    class Manager(MCPClientManager):
        async def _connect(self, server_name: str, config: dict):
            if server_name == "broken":
                raise RuntimeError("offline")
            session = _FakeSession(CallToolResult(content=[]))
            self._sessions[server_name] = session
            return session

    manager = Manager(
        {
            "servers": {
                "broken": {"enabled": True},
                "healthy": {"enabled": True},
            }
        }
    )

    tools = await manager.load_tools()

    assert [tool.name for tool in tools] == ["mcp_healthy_lookup"]
    await manager.close()


def test_mcp_result_and_name_are_bounded():
    result = _result_to_tool_result(
        CallToolResult(content=[TextContent(type="text", text="失败")], isError=True),
        server_name="demo",
        remote_name="lookup",
    )

    assert result.success is False
    assert result.error == "失败"
    assert len(_tool_name("s" * 100, "t" * 100)) <= 64
