"""Business-level security tests for authenticated WebUI WebSocket feeds."""

from __future__ import annotations

from pathlib import Path

import pytest
from aiohttp import WSServerHandshakeError, web
from aiohttp.test_utils import TestClient, TestServer

from sirius_pulse.webui.app_keys import AUTH_MANAGER_KEY
from sirius_pulse.webui.auth import AuthManager
from sirius_pulse.webui.middleware import auth_middleware
from sirius_pulse.webui.ws_server import WebSocketManager, setup_ws_routes


async def _websocket_client(
    tmp_path: Path,
    *,
    max_connections: int = 64,
    max_connections_per_user: int = 8,
) -> tuple[TestClient, WebSocketManager, AuthManager]:
    auth = AuthManager(tmp_path)
    app = web.Application(middlewares=[auth_middleware])
    app[AUTH_MANAGER_KEY] = auth
    manager = WebSocketManager(
        max_connections=max_connections,
        max_connections_per_user=max_connections_per_user,
    )
    setup_ws_routes(app, manager)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client, manager, auth


def _origin(client: TestClient) -> str:
    return str(client.make_url("/")).rstrip("/")


@pytest.mark.asyncio
async def test_websocket_requires_authenticated_upgrade_and_never_accepts_query_token(tmp_path):
    client, _manager, auth = await _websocket_client(tmp_path)
    token = auth.create_token("viewer", role="viewer")
    try:
        unauthenticated = await client.get("/ws/events")
        assert unauthenticated.status == 401

        # A query string must not become a credential transport for WebSocket:
        # it would be retained by URLs and intermediary logs.
        query_token = await client.get(f"/ws/events?token={token}")
        assert query_token.status == 401
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_websocket_allows_valid_viewer_from_same_origin_only(tmp_path):
    client, manager, auth = await _websocket_client(tmp_path)
    token = auth.create_token("viewer", role="viewer")
    try:
        ws = await client.ws_connect(
            "/ws/events",
            protocols=["sirius-auth", token],
            headers={"Origin": _origin(client)},
        )
        try:
            hello = await ws.receive_json(timeout=1)
            assert hello["type"] == "connected"
            assert ws.protocol == "sirius-auth"
            assert manager.connection_count == 1
        finally:
            await ws.close()

        with pytest.raises(WSServerHandshakeError) as rejected:
            await client.ws_connect(
                "/ws/events",
                protocols=["sirius-auth", token],
                headers={"Origin": "https://attacker.invalid"},
            )
        assert rejected.value.status == 403
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_websocket_enforces_global_and_per_user_connection_limits(tmp_path):
    client, manager, auth = await _websocket_client(
        tmp_path,
        max_connections=2,
        max_connections_per_user=1,
    )
    viewer_token = auth.create_token("viewer", role="viewer")
    other_token = auth.create_token("other", role="viewer")
    viewer_kwargs = {
        "protocols": ["sirius-auth", viewer_token],
        "headers": {"Origin": _origin(client)},
    }
    other_kwargs = {
        "protocols": ["sirius-auth", other_token],
        "headers": {"Origin": _origin(client)},
    }
    try:
        first = await client.ws_connect("/ws/events", **viewer_kwargs)
        try:
            await first.receive_json(timeout=1)
            with pytest.raises(WSServerHandshakeError) as per_user_rejected:
                await client.ws_connect("/ws/events", **viewer_kwargs)
            assert per_user_rejected.value.status == 429

            second = await client.ws_connect("/ws/events", **other_kwargs)
            try:
                await second.receive_json(timeout=1)
                with pytest.raises(WSServerHandshakeError) as global_rejected:
                    await client.ws_connect("/ws/events", **other_kwargs)
                assert global_rejected.value.status == 503
                assert manager.connection_count == 2
            finally:
                await second.close()
        finally:
            await first.close()
    finally:
        await client.close()
