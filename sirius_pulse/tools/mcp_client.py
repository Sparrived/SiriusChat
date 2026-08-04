"""MCP client integration for the existing Sirius tool runtime."""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

from sirius_pulse.tools.models import (
    ToolContentBlock,
    ToolDefinition,
    ToolParameter,
    ToolResult,
)

logger = logging.getLogger(__name__)

_ENV_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_TOOL_NAME_RE = re.compile(r"[^A-Za-z0-9_-]+")
_MAX_TOOL_NAME_LENGTH = 64


def load_mcp_config(path: Path) -> dict[str, Any]:
    """Load the persona-local MCP configuration without exposing its contents in logs."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("MCP 配置加载失败 (%s): %s", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def _resolve_env(value: Any) -> str:
    text = str(value)
    if text.startswith("env:"):
        return os.getenv(text[4:].strip(), "")
    return _ENV_REF_RE.sub(lambda match: os.getenv(match.group(1), ""), text)


def _server_entries(raw: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    servers = raw.get("servers", raw)
    if isinstance(servers, dict):
        return [
            (str(name).strip(), config)
            for name, config in servers.items()
            if str(name).strip() and isinstance(config, dict)
        ]
    if isinstance(servers, list):
        entries: list[tuple[str, dict[str, Any]]] = []
        for config in servers:
            if not isinstance(config, dict):
                continue
            name = str(config.get("name", "")).strip()
            if name:
                entries.append((name, config))
        return entries
    return []


def _tool_name(server_name: str, remote_name: str) -> str:
    server = _TOOL_NAME_RE.sub("_", server_name).strip("_") or "server"
    remote = _TOOL_NAME_RE.sub("_", remote_name).strip("_") or "tool"
    name = f"mcp_{server}_{remote}"
    if len(name) <= _MAX_TOOL_NAME_LENGTH:
        return name
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
    return f"{name[:_MAX_TOOL_NAME_LENGTH - 9]}_{digest}"


def _schema_parameters(schema: dict[str, Any]) -> list[ToolParameter]:
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        return []
    required = schema.get("required", [])
    required_names = {str(name) for name in required} if isinstance(required, list) else set()
    result: list[ToolParameter] = []
    for name, raw in properties.items():
        if not isinstance(raw, dict):
            continue
        raw_type = raw.get("type", "string")
        if isinstance(raw_type, list):
            raw_type = next((item for item in raw_type if item != "null"), "string")
        choices = raw.get("enum")
        result.append(
            ToolParameter(
                name=str(name),
                type=str(raw_type),
                description=str(raw.get("description", "")),
                required=str(name) in required_names,
                default=raw.get("default"),
                choices=choices if isinstance(choices, list) else None,
            )
        )
    return result


def _content_blocks(result: Any) -> tuple[list[ToolContentBlock], list[ToolContentBlock]]:
    text_blocks: list[ToolContentBlock] = []
    multimodal_blocks: list[ToolContentBlock] = []
    for item in getattr(result, "content", []) or []:
        item_type = str(getattr(item, "type", "")).strip()
        if item_type == "text":
            text = str(getattr(item, "text", "")).strip()
            if text:
                text_blocks.append(ToolContentBlock(type="text", value=text))
        elif item_type in {"image", "audio"}:
            value = str(getattr(item, "data", "")).strip()
            if value:
                multimodal_blocks.append(
                    ToolContentBlock(
                        type=item_type,
                        value=value,
                        mime_type=str(getattr(item, "mime_type", "")),
                    )
                )
        elif item_type == "resource_link":
            uri = str(getattr(item, "uri", "")).strip()
            if uri:
                text_blocks.append(ToolContentBlock(type=item_type, value=uri))
        elif item_type == "resource":
            resource = getattr(item, "resource", None)
            text = str(getattr(resource, "text", "")).strip()
            if text:
                text_blocks.append(ToolContentBlock(type="text", value=text))
            blob = str(getattr(resource, "blob", "")).strip()
            if blob:
                multimodal_blocks.append(
                    ToolContentBlock(
                        type="resource",
                        value=blob,
                        mime_type=str(getattr(resource, "mime_type", "")),
                    )
                )
    return text_blocks, multimodal_blocks


def _result_to_tool_result(result: Any, *, server_name: str, remote_name: str) -> ToolResult:
    text_blocks, multimodal_blocks = _content_blocks(result)
    is_error = bool(getattr(result, "is_error", False))
    structured = getattr(result, "structured_content", None)
    metadata = {
        "mcp_server": server_name,
        "mcp_tool": remote_name,
        "mcp_is_error": is_error,
    }
    error = "\n".join(block.value for block in text_blocks) if is_error else ""
    if is_error and not error:
        error = "MCP tool 调用失败"
    return ToolResult(
        success=not is_error,
        data=deepcopy(structured) if structured is not None else None,
        error=error,
        text_blocks=text_blocks,
        multimodal_blocks=multimodal_blocks,
        internal_metadata=metadata,
    )


class MCPClientManager:
    """Keep MCP sessions alive and expose their tools as ToolDefinitions."""

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config
        self._exit_stack = contextlib.AsyncExitStack()
        self._sessions: dict[str, ClientSession] = {}
        self._started = False

    async def load_tools(self, *, reserved_names: set[str] | None = None) -> list[ToolDefinition]:
        """Connect configured servers and return their model-visible tools."""
        if self._started:
            return []
        self._started = True
        await self._exit_stack.__aenter__()
        reserved = set(reserved_names or ())
        definitions: list[ToolDefinition] = []
        for server_name, server_config in _server_entries(self._config):
            if not bool(server_config.get("enabled", True)):
                continue
            try:
                session = await self._connect(server_name, server_config)
                result = await session.list_tools()
                for remote_tool in getattr(result, "tools", []) or []:
                    remote_name = str(getattr(remote_tool, "name", "")).strip()
                    if not remote_name:
                        continue
                    local_name = _tool_name(server_name, remote_name)
                    if local_name in reserved:
                        logger.warning("MCP tool 名称冲突，跳过: %s", local_name)
                        continue
                    schema = getattr(remote_tool, "input_schema", None)
                    if not isinstance(schema, dict):
                        schema = {"type": "object", "properties": {}}
                    description = str(
                        getattr(remote_tool, "description", None)
                        or getattr(remote_tool, "title", None)
                        or remote_name
                    ).strip()
                    definitions.append(
                        ToolDefinition(
                            name=local_name,
                            description=f"[MCP:{server_name}] {description}",
                            parameters=_schema_parameters(schema),
                            version="mcp",
                            tags=["mcp", server_name],
                            _run_func=self._make_runner(server_name, remote_name),
                            raw_parameters_schema=deepcopy(schema),
                            inject_runtime_params=False,
                            allow_extra_parameters=True,
                        )
                    )
                    reserved.add(local_name)
                logger.info(
                    "MCP server 已连接: %s (%d 个工具)",
                    server_name,
                    len(getattr(result, "tools", []) or []),
                )
            except Exception as exc:
                logger.warning(
                    "MCP server 连接失败 (%s): %s",
                    server_name,
                    type(exc).__name__,
                )
        return definitions

    async def _connect(self, server_name: str, config: dict[str, Any]) -> ClientSession:
        transport = str(config.get("transport", "stdio")).strip().lower()
        if transport == "stdio":
            command = str(config.get("command", "")).strip()
            if not command:
                raise ValueError("stdio server 缺少 command")
            env = os.environ.copy()
            raw_env = config.get("env", {})
            if isinstance(raw_env, dict):
                env.update({str(key): _resolve_env(value) for key, value in raw_env.items()})
            raw_args = config.get("args", [])
            args = [_resolve_env(item) for item in raw_args] if isinstance(raw_args, list) else []
            streams = await self._exit_stack.enter_async_context(
                stdio_client(
                    StdioServerParameters(
                        command=command,
                        args=args,
                        env=env,
                        cwd=str(config["cwd"]) if config.get("cwd") else None,
                    )
                )
            )
        elif transport == "streamable_http":
            url = str(config.get("url", "")).strip()
            if not url:
                raise ValueError("streamable_http server 缺少 url")
            headers = config.get("headers", {})
            headers = (
                {str(key): _resolve_env(value) for key, value in headers.items()}
                if isinstance(headers, dict)
                else {}
            )
            http_client = create_mcp_http_client(headers=headers)
            await self._exit_stack.enter_async_context(http_client)
            streams = await self._exit_stack.enter_async_context(
                streamable_http_client(url, http_client=http_client)
            )
        elif transport == "sse":
            url = str(config.get("url", "")).strip()
            if not url:
                raise ValueError("sse server 缺少 url")
            headers = config.get("headers", {})
            headers = (
                {str(key): _resolve_env(value) for key, value in headers.items()}
                if isinstance(headers, dict)
                else {}
            )
            streams = await self._exit_stack.enter_async_context(sse_client(url, headers=headers))
        else:
            raise ValueError(f"不支持的 MCP transport: {transport}")

        session = await self._exit_stack.enter_async_context(ClientSession(*streams))
        await session.initialize()
        self._sessions[server_name] = session
        return session

    def _make_runner(self, server_name: str, remote_name: str):
        async def run(**arguments: Any) -> ToolResult:
            session = self._sessions.get(server_name)
            if session is None:
                return ToolResult(success=False, error=f"MCP server 未连接: {server_name}")
            result = await session.call_tool(remote_name, arguments=arguments)
            return _result_to_tool_result(
                result,
                server_name=server_name,
                remote_name=remote_name,
            )

        return run

    async def close(self) -> None:
        if not self._started:
            return
        self._sessions.clear()
        await self._exit_stack.aclose()
        self._started = False
