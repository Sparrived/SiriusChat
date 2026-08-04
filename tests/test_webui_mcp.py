from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from sirius_pulse.config.file_io import atomic_json_save
from sirius_pulse.webui import server_mcp_api


class _JsonRequest(SimpleNamespace):
    def __init__(self, body):
        super().__init__(match_info={})
        self._body = body

    async def json(self):
        return self._body


@pytest.mark.asyncio
async def test_mcp_get_masks_headers_and_environment_values(tmp_path):
    atomic_json_save(
        tmp_path / "mcp.json",
        {
            "servers": {
                "remote": {
                    "enabled": True,
                    "transport": "streamable_http",
                    "url": "https://example.test/mcp",
                    "headers": {"Authorization": "Bearer secret"},
                    "env": {"MCP_TOKEN": "secret"},
                }
            }
        },
    )

    response = await server_mcp_api.api_persona_mcp_get(_JsonRequest({}), tmp_path)

    assert response.status == 200
    payload = json.loads(response.text)
    assert payload["servers"]["remote"]["headers"] == {"Authorization": "********"}
    assert payload["servers"]["remote"]["env"] == {"MCP_TOKEN": "********"}
    assert payload["servers"]["remote"]["url"] == "https://example.test/mcp"


@pytest.mark.asyncio
async def test_mcp_post_preserves_masked_secrets_and_requests_reload(tmp_path, monkeypatch):
    atomic_json_save(
        tmp_path / "mcp.json",
        {
            "servers": {
                "remote": {
                    "enabled": True,
                    "transport": "sse",
                    "url": "https://old.example/sse",
                    "headers": {"Authorization": "Bearer secret"},
                }
            }
        },
    )
    reload_types = []
    monkeypatch.setattr(
        server_mcp_api,
        "_request_config_reload",
        lambda reload_type, data_dir: reload_types.append((reload_type, data_dir)),
    )

    response = await server_mcp_api.api_persona_mcp_post(
        _JsonRequest(
            {
                "servers": {
                    "remote": {
                        "enabled": True,
                        "transport": "sse",
                        "url": "https://new.example/sse",
                        "headers": {"Authorization": "********"},
                    }
                }
            }
        ),
        tmp_path,
    )

    assert response.status == 200
    saved = json.loads((tmp_path / "mcp.json").read_text(encoding="utf-8"))
    assert saved["servers"]["remote"]["headers"]["Authorization"] == "Bearer secret"
    assert saved["servers"]["remote"]["url"] == "https://new.example/sse"
    assert reload_types == [("mcp", tmp_path)]


@pytest.mark.asyncio
async def test_mcp_post_rejects_enabled_stdio_without_command(tmp_path):
    response = await server_mcp_api.api_persona_mcp_post(
        _JsonRequest(
            {
                "servers": {
                    "local": {
                        "enabled": True,
                        "transport": "stdio",
                        "args": [],
                    }
                }
            }
        ),
        tmp_path,
    )

    assert response.status == 400
    assert "command" in json.loads(response.text)["error"]
