from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from sirius_pulse.config import TokenUsageRecord
from sirius_pulse.plugins.models import PluginDefinition, PluginParameterDef, PluginPermissionDef
from sirius_pulse.token.token_store import TokenUsageStore
from sirius_pulse.utils.json_io import atomic_write_json
from sirius_pulse.webui import persona_manager_api as persona_manager
from sirius_pulse.webui.app_keys import DATA_DIR_KEY
from sirius_pulse.webui.memory_api import api_persona_tokens_get
from sirius_pulse.webui.persona_api import (
    _resolve_persona_log_file,
    api_orchestration_get,
    api_persona_logs_get,
    api_system_logs_get,
)
from sirius_pulse.webui.routes import WEBUI_ROUTES
from sirius_pulse.webui.server import DELEGATED_HANDLERS, WebUIServer
from sirius_pulse.webui.server_plugin_api import (
    MaskedSecretUpdateError,
    _effective_permissions,
    _masked_parameter,
    _masked_settings,
    _request_plugin_reload,
    _settings_update_without_masked_secrets,
    api_plugin_config_get,
    api_plugin_config_post,
    api_plugin_detail_get,
    api_plugin_setting_delete,
    api_plugin_setting_post,
    api_plugin_settings_get,
    api_plugin_settings_post,
    api_plugin_toggle,
    api_plugins_get,
)


def _route_snapshot(app: web.Application) -> set[tuple[str, str]]:
    routes: set[tuple[str, str]] = set()
    for route in app.router.routes():
        resource = route.resource
        path = getattr(resource, "canonical", None)
        if path is None:
            continue
        routes.add((route.method, path))
    return routes


def _demo_plugin_definition() -> PluginDefinition:
    return PluginDefinition(
        name="demo",
        parameters=[
            PluginParameterDef(name="api_key", type="password"),
            PluginParameterDef(name="label", type="str"),
            PluginParameterDef(
                name="credentials",
                type="object",
                fields=[
                    {"name": "access_token", "type": "password"},
                    {"name": "label", "type": "str"},
                ],
            ),
            PluginParameterDef(
                name="repos",
                type="object_array",
                fields=[
                    {"name": "name", "type": "str"},
                    {"name": "repository_token", "type": "password"},
                ],
            ),
        ],
    )


class _FakeJsonRequest:
    """最小 aiohttp 请求替身：只提供 await request.json()。"""

    def __init__(
        self,
        payload: dict[str, object],
        match_info: dict[str, str] | None = None,
    ) -> None:
        self._payload = payload
        self.match_info = match_info or {}

    async def json(self) -> dict[str, object]:
        return self._payload


def test_webui_plugin_settings_mask_secrets_and_preserve_masked_updates():
    settings = {
        "password": "real-password",
        "api_key": "real-key",
        "nested": {"accessToken": "real-token", "label": "safe"},
        "repos": [{"name": "demo", "repository_token": "repo-token"}],
    }

    masked = _masked_settings(settings)
    assert masked == {
        "password": "********",
        "api_key": "********",
        "nested": {"accessToken": "********", "label": "safe"},
        "repos": [{"name": "demo", "repository_token": "********"}],
    }

    updated = _settings_update_without_masked_secrets(
        {
            "password": "********",
            "api_key": "",
            "nested": {"accessToken": "********", "label": "updated"},
            "repos": [{"name": "demo", "repository_token": "********"}],
        },
        existing=settings,
    )
    assert updated == {
        "password": "real-password",
        "api_key": "real-key",
        "nested": {"accessToken": "real-token", "label": "updated"},
        "repos": [{"name": "demo", "repository_token": "repo-token"}],
    }
    with pytest.raises(MaskedSecretUpdateError):
        _settings_update_without_masked_secrets(
            {"repos": [{"name": "renamed", "repository_token": "********"}]},
            existing=settings,
        )


def test_webui_plugin_parameter_masks_password_defaults_and_nested_fields():
    parameter = SimpleNamespace(
        name="credentials",
        type="object_array",
        description="",
        required=False,
        default=None,
        choices=None,
        fields=[
            {"name": "repository_token", "type": "password", "default": "secret"},
            {"name": "label", "type": "str", "default": "demo"},
        ],
        group="",
    )

    masked = _masked_parameter(parameter)
    assert masked["fields"][0]["default"] == "********"
    assert masked["fields"][1]["default"] == "demo"


@pytest.mark.asyncio
async def test_webui_plugin_settings_post_rejects_plaintext_secret_without_persisting(tmp_path):
    manager = SimpleNamespace(
        data_path=tmp_path / "data",
        plugin_definitions={"demo": _demo_plugin_definition()},
    )
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    config_path = plugins_dir / "_config.json"
    atomic_write_json(
        config_path,
        {
            "demo": {
                "enabled": True,
                "permissions": {},
                "settings": {"api_key": "stored-secret", "label": "old"},
            }
        },
    )
    before = config_path.read_text(encoding="utf-8")
    request = _FakeJsonRequest(
        {"settings": {"api_key": "new-secret", "label": "new"}},
        {"plugin_name": "demo"},
    )

    response = await api_plugin_settings_post(request, manager)

    assert response.status == 400
    assert "new-secret" not in response.text
    assert config_path.read_text(encoding="utf-8") == before
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["demo"]["settings"] == {"api_key": "stored-secret", "label": "old"}


@pytest.mark.asyncio
async def test_webui_plugin_setting_post_handles_nested_secrets_and_masks_response(tmp_path):
    manager = SimpleNamespace(
        data_path=tmp_path / "data",
        plugin_definitions={"demo": _demo_plugin_definition()},
    )
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    config_path = plugins_dir / "_config.json"
    atomic_write_json(
        config_path,
        {
            "demo": {
                "enabled": True,
                "permissions": {},
                "settings": {
                    "credentials": {"access_token": "object-secret", "label": "old"},
                    "repos": [{"name": "demo", "repository_token": "list-secret"}],
                },
            }
        },
    )

    rejected = await api_plugin_setting_post(
        _FakeJsonRequest(
            {"value": {"access_token": "new-object-secret", "label": "new"}},
            {"plugin_name": "demo", "key": "credentials"},
        ),
        manager,
    )
    assert rejected.status == 400
    assert "new-object-secret" not in rejected.text
    assert "list-secret" not in rejected.text

    rejected_list = await api_plugin_setting_post(
        _FakeJsonRequest(
            {"value": [{"name": "new", "repository_token": "new-list-secret"}]},
            {"plugin_name": "demo", "key": "repos"},
        ),
        manager,
    )
    assert rejected_list.status == 400
    assert "new-list-secret" not in rejected_list.text

    accepted_single = await api_plugin_setting_post(
        _FakeJsonRequest(
            {"value": {"access_token": "********", "label": "single"}},
            {"plugin_name": "demo", "key": "credentials"},
        ),
        manager,
    )
    single_payload = json.loads(accepted_single.text)
    single_saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert accepted_single.status == 200
    assert "object-secret" not in accepted_single.text
    assert single_payload["value"] == {"access_token": "********", "label": "single"}
    assert single_saved["demo"]["settings"]["credentials"] == {
        "access_token": "object-secret",
        "label": "single",
    }

    accepted = await api_plugin_settings_post(
        _FakeJsonRequest(
            {
                "settings": {
                    "credentials": {"access_token": "********", "label": "new"},
                    "repos": [{"name": "demo", "repository_token": "********"}],
                }
            },
            {"plugin_name": "demo"},
        ),
        manager,
    )
    payload = json.loads(accepted.text)
    saved = json.loads(config_path.read_text(encoding="utf-8"))

    assert accepted.status == 200
    assert "object-secret" not in accepted.text
    assert "list-secret" not in accepted.text
    assert payload["settings"] == {
        "credentials": {"access_token": "********", "label": "new"},
        "repos": [{"name": "demo", "repository_token": "********"}],
    }
    assert saved["demo"]["settings"] == {
        "credentials": {"access_token": "object-secret", "label": "new"},
        "repos": [{"name": "demo", "repository_token": "list-secret"}],
    }


def test_webui_plugin_parameter_masks_sensitive_names_even_if_mislabeled():
    parameter = SimpleNamespace(
        name="api_key",
        type="str",
        description="",
        required=False,
        default="should-not-leak",
        choices=None,
        fields=[
            {"name": "repository_token", "type": "str", "default": "also-secret"},
            {"name": "label", "type": "str", "default": "visible"},
        ],
        group="",
    )

    masked = _masked_parameter(parameter)

    assert masked["default"] == "********"
    assert masked["fields"][0]["default"] == "********"
    assert masked["fields"][1]["default"] == "visible"


def test_webui_masked_object_array_secrets_follow_stable_identity_not_position():
    definition = PluginDefinition(
        name="demo",
        parameters=[
            PluginParameterDef(
                name="repos",
                type="object_array",
                fields=[
                    {"name": "owner", "type": "str"},
                    {"name": "repo", "type": "str"},
                    {"name": "repository_token", "type": "password"},
                ],
            )
        ],
    )
    existing = {
        "repos": [
            {"owner": "alpha", "repo": "one", "repository_token": "token-alpha"},
            {"owner": "beta", "repo": "two", "repository_token": "token-beta"},
        ]
    }

    removed_first = _settings_update_without_masked_secrets(
        {"repos": [{"owner": "beta", "repo": "two", "repository_token": "********"}]},
        definition,
        existing,
    )
    reordered = _settings_update_without_masked_secrets(
        {
            "repos": [
                {"owner": "beta", "repo": "two", "repository_token": "********"},
                {"owner": "alpha", "repo": "one", "repository_token": "********"},
            ]
        },
        definition,
        existing,
    )

    assert removed_first["repos"] == [
        {"owner": "beta", "repo": "two", "repository_token": "token-beta"}
    ]
    assert reordered["repos"] == [
        {"owner": "beta", "repo": "two", "repository_token": "token-beta"},
        {"owner": "alpha", "repo": "one", "repository_token": "token-alpha"},
    ]
    with pytest.raises(MaskedSecretUpdateError):
        _settings_update_without_masked_secrets(
            {
                "repos": [
                    {"owner": "changed", "repo": "one", "repository_token": "********"},
                    {"owner": "beta", "repo": "two", "repository_token": "********"},
                ]
            },
            definition,
            existing,
        )


def test_webui_repository_masks_follow_casefolded_owner_repo_identity():
    definition = PluginDefinition(
        name="repository_monitor",
        parameters=[
            PluginParameterDef(
                name="repos",
                type="object_array",
                fields=[
                    {"name": "owner", "type": "str"},
                    {"name": "repo", "type": "str"},
                    {"name": "repository_token", "type": "password"},
                ],
            )
        ],
    )
    existing = {
        "repos": [
            {"owner": "Alpha", "repo": "One", "repository_token": "alpha-token"},
            {"owner": "Beta", "repo": "Two", "repository_token": "beta-token"},
        ]
    }

    updated = _settings_update_without_masked_secrets(
        {
            "repos": [
                {"owner": "beta", "repo": "TWO", "repository_token": "********"},
                {"owner": "ALPHA", "repo": "one", "repository_token": "********"},
                # A new repository does not receive any existing credential.
                {"owner": "Gamma", "repo": "Three"},
            ]
        },
        definition,
        existing,
    )

    assert updated["repos"] == [
        {"owner": "beta", "repo": "TWO", "repository_token": "beta-token"},
        {"owner": "ALPHA", "repo": "one", "repository_token": "alpha-token"},
        {"owner": "Gamma", "repo": "Three"},
    ]


def test_webui_repository_masks_reject_new_invalid_or_duplicate_identity():
    definition = PluginDefinition(
        name="repository_monitor",
        parameters=[
            PluginParameterDef(
                name="repos",
                type="object_array",
                fields=[
                    {"name": "owner", "type": "str"},
                    {"name": "repo", "type": "str"},
                    {"name": "repository_token", "type": "password"},
                ],
            )
        ],
    )
    existing = {
        "repos": [
            {"owner": "alpha", "repo": "one", "repository_token": "alpha-token"},
            {"owner": "beta", "repo": "two", "repository_token": "beta-token"},
        ]
    }

    for rows in (
        [{"owner": "gamma", "repo": "three", "repository_token": "********"}],
        [{"owner": "bad/name", "repo": "three", "repository_token": "********"}],
        [
            {"owner": "alpha", "repo": "one", "repository_token": "********"},
            {"owner": "ALPHA", "repo": "ONE", "repository_token": "********"},
        ],
    ):
        with pytest.raises(MaskedSecretUpdateError):
            _settings_update_without_masked_secrets({"repos": rows}, definition, existing)


@pytest.mark.asyncio
async def test_webui_repository_plaintext_token_is_rejected_without_persisting(tmp_path):
    definition = PluginDefinition(
        name="repository_monitor",
        parameters=[
            PluginParameterDef(
                name="repos",
                type="object_array",
                fields=[
                    {"name": "owner", "type": "str"},
                    {"name": "repo", "type": "str"},
                    {"name": "repository_token", "type": "password"},
                ],
            )
        ],
    )
    manager = SimpleNamespace(
        data_path=tmp_path / "data", plugin_definitions={"repository_monitor": definition}
    )
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    config_path = plugins_dir / "_config.json"
    atomic_write_json(
        config_path,
        {
            "repository_monitor": {
                "enabled": True,
                "permissions": {},
                "settings": {
                    "repos": [{"owner": "alpha", "repo": "one", "repository_token": "alpha-token"}]
                },
            }
        },
    )
    before = config_path.read_bytes()

    response = await api_plugin_setting_post(
        _FakeJsonRequest(
            {"value": [{"owner": "alpha", "repo": "one", "repository_token": "new-token"}]},
            {"plugin_name": "repository_monitor", "key": "repos"},
        ),
        manager,
    )

    assert response.status == 400
    assert "new-token" not in response.text
    assert config_path.read_bytes() == before


@pytest.mark.asyncio
async def test_webui_plugin_settings_reject_undeclared_keys_without_persisting(tmp_path):
    manager = SimpleNamespace(
        data_path=tmp_path / "data",
        plugin_definitions={"demo": _demo_plugin_definition()},
    )
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    config_path = plugins_dir / "_config.json"
    atomic_write_json(
        config_path,
        {"demo": {"enabled": True, "permissions": {}, "settings": {"label": "old"}}},
    )

    response = await api_plugin_settings_post(
        _FakeJsonRequest(
            {"settings": {"credential": "unrecognized-secret"}},
            {"plugin_name": "demo"},
        ),
        manager,
    )

    assert response.status == 400
    assert "unrecognized-secret" not in response.text
    assert json.loads(config_path.read_text(encoding="utf-8"))["demo"]["settings"] == {
        "label": "old"
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler", "path", "match_info"),
    [
        (api_plugins_get, "/api/plugins", {}),
        (api_plugin_detail_get, "/api/plugins/demo", {"plugin_name": "demo"}),
        (api_plugin_config_get, "/api/plugins/demo/config", {"plugin_name": "demo"}),
        (api_plugin_settings_get, "/api/plugins/demo/settings", {"plugin_name": "demo"}),
    ],
)
async def test_webui_plugin_reads_require_admin_before_scanning(
    tmp_path,
    monkeypatch,
    handler,
    path,
    match_info,
):
    def fail_if_scanned(*_args, **_kwargs):
        raise AssertionError("viewer requests must not scan or import plugins")

    monkeypatch.setattr(
        "sirius_pulse.webui.server_plugin_api._load_definitions_cached",
        fail_if_scanned,
    )
    request = make_mocked_request("GET", path, match_info=match_info)
    request["auth_role"] = "viewer"

    response = await handler(request, SimpleNamespace(data_path=tmp_path / "data"))

    assert response.status == 403


@pytest.mark.asyncio
async def test_webui_plugin_metadata_listing_does_not_execute_plugin_code(tmp_path):
    plugins_dir = tmp_path / "plugins"
    plugin_dir = plugins_dir / "metadata_demo"
    plugin_dir.mkdir(parents=True)
    marker = plugin_dir / "imported.txt"
    (plugin_dir / "__init__.py").write_text(
        "from pathlib import Path\n"
        "Path(__file__).with_name('imported.txt').write_text('executed')\n"
        "from sirius_pulse.plugins import PluginBase\n"
        "class MetadataDemo(PluginBase):\n"
        "    _plugin_name = 'metadata_demo'\n"
        "    _plugin_display_name = 'Metadata demo'\n"
        "    _plugin_parameters = [{'name': 'label', 'type': 'str'}]\n",
        encoding="utf-8",
    )
    request = make_mocked_request("GET", "/api/plugins")
    request["auth_role"] = "admin"

    response = await api_plugins_get(
        request,
        SimpleNamespace(data_path=tmp_path / "data"),
    )
    payload = json.loads(response.text)

    assert response.status == 200
    assert [item["name"] for item in payload["plugins"]] == ["metadata_demo"]
    assert marker.exists() is False


@pytest.mark.asyncio
async def test_webui_unknown_plugin_mutations_leave_config_untouched(tmp_path):
    manager = SimpleNamespace(
        data_path=tmp_path / "data",
        plugin_definitions={"demo": _demo_plugin_definition()},
    )
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    match_info = {"plugin_name": "missing"}

    responses = [
        await api_plugin_toggle(
            _FakeJsonRequest({"enabled": True}, match_info),
            manager,
        ),
        await api_plugin_config_post(
            _FakeJsonRequest({"developer_only": True}, match_info),
            manager,
        ),
        await api_plugin_settings_post(
            _FakeJsonRequest({"settings": {"label": "new"}}, match_info),
            manager,
        ),
        await api_plugin_setting_post(
            _FakeJsonRequest(
                {"value": "new"},
                {"plugin_name": "missing", "key": "label"},
            ),
            manager,
        ),
        await api_plugin_setting_delete(
            _FakeJsonRequest(
                {},
                {"plugin_name": "missing", "key": "label"},
            ),
            manager,
        ),
    ]

    assert all(response.status == 404 for response in responses)
    assert not (plugins_dir / "_config.json").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"developer_only": "false"},
        {"hidden_from_intent": 0},
        {"rate_limit_calls_per_minute": True},
        {"rate_limit_calls_per_minute": 0},
        {"rate_limit_calls_per_minute": -1},
        {"rate_limit_calls_per_minute": 1001},
    ],
)
async def test_webui_plugin_permissions_reject_non_strict_values(tmp_path, body):
    manager = SimpleNamespace(
        data_path=tmp_path / "data",
        plugin_definitions={"demo": _demo_plugin_definition()},
    )
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()

    response = await api_plugin_config_post(
        _FakeJsonRequest(body, {"plugin_name": "demo"}),
        manager,
    )

    assert response.status == 400
    assert not (plugins_dir / "_config.json").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", ["false", 0, 1, None])
async def test_webui_plugin_toggle_requires_exact_boolean(tmp_path, enabled):
    manager = SimpleNamespace(
        data_path=tmp_path / "data",
        plugin_definitions={"demo": _demo_plugin_definition()},
    )
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()

    response = await api_plugin_toggle(
        _FakeJsonRequest({"enabled": enabled}, {"plugin_name": "demo"}),
        manager,
    )

    assert response.status == 400
    assert not (plugins_dir / "_config.json").exists()


@pytest.mark.asyncio
async def test_webui_plugin_settings_enforce_declared_numeric_bounds(tmp_path):
    definition = PluginDefinition(
        name="monitor",
        parameters=[
            PluginParameterDef(name="poll_seconds", type="int", minimum=30, maximum=86400),
            PluginParameterDef(name="timeout", type="float", minimum=1, maximum=300),
        ],
    )
    manager = SimpleNamespace(
        data_path=tmp_path / "data",
        plugin_definitions={"monitor": definition},
    )
    (tmp_path / "plugins").mkdir()

    too_fast = await api_plugin_settings_post(
        _FakeJsonRequest({"settings": {"poll_seconds": 29}}, {"plugin_name": "monitor"}),
        manager,
    )
    non_finite = await api_plugin_settings_post(
        _FakeJsonRequest({"settings": {"timeout": float("inf")}}, {"plugin_name": "monitor"}),
        manager,
    )

    assert too_fast.status == 400
    assert non_finite.status == 400
    assert not (tmp_path / "plugins" / "_config.json").exists()


@pytest.mark.asyncio
async def test_webui_manifest_developer_only_cannot_be_relaxed(tmp_path):
    definition = PluginDefinition(
        name="locked",
        permissions=PluginPermissionDef(developer_only=True),
    )
    manager = SimpleNamespace(
        data_path=tmp_path / "data",
        plugin_definitions={"locked": definition},
    )
    (tmp_path / "plugins").mkdir()

    rejected = await api_plugin_config_post(
        _FakeJsonRequest(
            {"developer_only": False, "rate_limit_calls_per_minute": 60},
            {"plugin_name": "locked"},
        ),
        manager,
    )
    accepted = await api_plugin_config_post(
        _FakeJsonRequest(
            {"developer_only": True, "rate_limit_calls_per_minute": 60},
            {"plugin_name": "locked"},
        ),
        manager,
    )

    assert rejected.status == 400
    assert accepted.status == 200
    assert _effective_permissions(definition, {"developer_only": False})["developer_only"] is True


def test_webui_plugin_reload_when_workspace_has_personas_then_marks_each_worker(tmp_path):
    personas_dir = tmp_path / "personas"
    for name in ("alpha", "beta"):
        persona_dir = personas_dir / name
        persona_dir.mkdir(parents=True)
        (persona_dir / "persona.json").write_text("{}", encoding="utf-8")

    _request_plugin_reload(tmp_path)

    for name in ("alpha", "beta"):
        flag = personas_dir / name / "engine_state" / "reload_requested"
        assert flag.read_text(encoding="utf-8") == "all"


def test_webui_plugin_reload_when_existing_types_then_preserves_them(tmp_path):
    persona_dir = tmp_path / "personas" / "alpha"
    persona_dir.mkdir(parents=True)
    (persona_dir / "persona.json").write_text("{}", encoding="utf-8")
    flag = persona_dir / "engine_state" / "reload_requested"
    flag.parent.mkdir()
    flag.write_text('{"types": ["provider"]}', encoding="utf-8")

    _request_plugin_reload(tmp_path)

    assert set(json.loads(flag.read_text(encoding="utf-8"))["types"]) == {"all", "provider"}


@pytest.mark.asyncio
async def test_webui_plugins_get_accepts_path_based_delegate(tmp_path):
    server = WebUIServer(data_dir=tmp_path)

    response = await server.api_plugins_get(SimpleNamespace())

    assert response.status == 200
    assert json.loads(response.text) == {"plugins": []}


def test_webui_routes_when_server_is_created_then_all_declared_routes_are_registered(tmp_path):
    server = WebUIServer(data_dir=tmp_path)

    registered = _route_snapshot(server.app)

    for spec in WEBUI_ROUTES:
        assert (spec.method, spec.path) in registered
    assert not any(spec.path.startswith("/api/persona/users") for spec in WEBUI_ROUTES)
    assert not any("/api/persona/persona/interview" == spec.path for spec in WEBUI_ROUTES)
    assert ("GET", "/") in registered
    assert ("GET", "/ws/events") in registered


def test_persona_logs_when_source_is_omitted_then_reads_persona_log(tmp_path):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    persona_log = logs_dir / "persona.log"
    persona_log.write_text("current runtime", encoding="utf-8")

    selected, source = _resolve_persona_log_file(tmp_path)

    assert selected == persona_log
    assert source == "persona"


@pytest.mark.asyncio
async def test_persona_logs_when_unknown_source_is_requested_then_reads_persona_log(tmp_path):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    persona_log = logs_dir / "persona.log"
    persona_log.write_text("persona line", encoding="utf-8")
    request = SimpleNamespace(query={"source": "worker", "lines": "10", "offset": "0"})

    response = await api_persona_logs_get(request, tmp_path)
    payload = json.loads(response.text)

    assert payload["source"] == "persona"
    assert payload["path"] == str(persona_log)
    assert payload["lines"] == ["persona line"]


@pytest.mark.asyncio
async def test_system_logs_when_called_then_reads_global_webui_log(tmp_path):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    webui_log = logs_dir / "webui.log"
    webui_log.write_text("webui line", encoding="utf-8")
    request = SimpleNamespace(query={"lines": "10", "offset": "0"})

    response = await api_system_logs_get(request, tmp_path)
    payload = json.loads(response.text)

    assert payload["target"] == "webui"
    assert payload["path"] == str(webui_log)
    assert payload["lines"] == ["webui line"]


@pytest.mark.asyncio
async def test_persona_tokens_when_cache_usage_exists_then_returns_cache_stats(tmp_path):
    store = TokenUsageStore(tmp_path / "persona.db", batch_size=1)
    store.add(
        TokenUsageRecord(
            actor_id="assistant",
            task_name="response_generate",
            model="test-model",
            prompt_tokens=100,
            completion_tokens=10,
            total_tokens=110,
            cached_prompt_tokens=75,
            uncached_prompt_tokens=25,
            cache_info_available=True,
        ),
        timestamp=1.0,
    )
    store.close()

    response = await api_persona_tokens_get(SimpleNamespace(query={}), tmp_path)
    payload = json.loads(response.text)

    assert payload["cache_stats"]["cached_prompt_tokens"] == 75
    assert payload["cache_stats"]["uncached_prompt_tokens"] == 25
    assert payload["cache_stats"]["cache_hit_rate_pct"] == 75.0
    assert payload["recent_with_breakdown"][0]["cache_info_available"] == 1


def test_webui_routes_when_declared_then_handler_names_are_available(tmp_path):
    server = WebUIServer(data_dir=tmp_path)

    for spec in WEBUI_ROUTES:
        handler = getattr(server, spec.handler_name)
        assert callable(handler)


@pytest.mark.asyncio
async def test_webui_delegated_handler_when_called_then_injects_data_dir(tmp_path, monkeypatch):
    calls = []

    async def fake_handler(request, data_dir):
        calls.append((request, data_dir))
        return web.json_response({"ok": True})

    monkeypatch.setitem(DELEGATED_HANDLERS, "api_persona_get", fake_handler)
    server = WebUIServer(data_dir=tmp_path)
    request = SimpleNamespace()

    response = await server.api_persona_get(request)

    assert response.status == 200
    assert calls == [(request, server.data_dir)]


@pytest.mark.asyncio
async def test_webui_persona_stop_when_worker_is_injected_then_shutdown_is_requested(tmp_path):
    persona_dir = tmp_path / "personas" / "sirius"
    persona_dir.mkdir(parents=True)
    atomic_write_json(tmp_path / "global_config.json", {"active_persona": "sirius"})

    class Worker:
        def __init__(self) -> None:
            self.persona_dir = persona_dir
            self.shutdown_called = False

        def shutdown(self) -> None:
            self.shutdown_called = True

    worker = Worker()
    server = WebUIServer(data_dir=tmp_path, persona_manager=worker)

    response = await server.api_persona_stop(SimpleNamespace())
    saved = json.loads((tmp_path / "global_config.json").read_text(encoding="utf-8"))

    assert response.status == 200
    assert saved["active_persona"] == ""
    assert worker.shutdown_called is True


@pytest.mark.asyncio
async def test_webui_persona_stop_when_worker_targets_other_persona_then_shutdown_is_skipped(
    tmp_path,
):
    active_dir = tmp_path / "personas" / "sirius"
    other_dir = tmp_path / "personas" / "other"
    active_dir.mkdir(parents=True)
    other_dir.mkdir(parents=True)
    atomic_write_json(tmp_path / "global_config.json", {"active_persona": "sirius"})

    class Worker:
        def __init__(self) -> None:
            self.persona_dir = other_dir
            self.shutdown_called = False

        def shutdown(self) -> None:
            self.shutdown_called = True

    worker = Worker()
    server = WebUIServer(data_dir=tmp_path, persona_manager=worker)

    response = await server.api_persona_stop(SimpleNamespace())

    assert response.status == 200
    assert worker.shutdown_called is False


@pytest.mark.asyncio
async def test_webui_persona_activate_when_called_then_updates_server_active_persona(tmp_path):
    sirius_dir = tmp_path / "personas" / "sirius"
    other_dir = tmp_path / "personas" / "other"
    sirius_dir.mkdir(parents=True)
    other_dir.mkdir(parents=True)
    atomic_write_json(sirius_dir / "persona.json", {"name": "sirius"})
    atomic_write_json(other_dir / "persona.json", {"name": "other"})
    atomic_write_json(tmp_path / "global_config.json", {"active_persona": "other"})
    server = WebUIServer(data_dir=tmp_path)
    request = SimpleNamespace(match_info={"name": "sirius"})

    response = await server.api_persona_activate(request)

    assert response.status == 200
    assert server.persona_dir == sirius_dir


@pytest.mark.asyncio
async def test_persona_status_when_worker_pid_is_stale_then_not_running(tmp_path, monkeypatch):
    persona_dir = tmp_path / "personas" / "sirius"
    persona_dir.mkdir(parents=True)
    atomic_write_json(tmp_path / "global_config.json", {"active_persona": "sirius"})
    atomic_write_json(
        persona_dir / "engine_state" / "worker_status.json",
        {"status": "running", "pid": 12345},
    )
    monkeypatch.setattr(persona_manager, "_pid_exists", lambda pid: False)

    response = await persona_manager.api_persona_status(SimpleNamespace(), persona_dir)
    payload = json.loads(response.text)

    assert response.status == 200
    assert payload["running"] is False
    assert payload["pid"] == 12345


@pytest.mark.asyncio
async def test_persona_start_when_not_running_then_spawns_worker_process(tmp_path, monkeypatch):
    persona_dir = tmp_path / "personas" / "sirius"
    persona_dir.mkdir(parents=True)
    atomic_write_json(persona_dir / "persona.json", {"name": "sirius"})
    atomic_write_json(tmp_path / "global_config.json", {"active_persona": "", "log_level": "debug"})
    monkeypatch.setattr(persona_manager, "_pid_exists", lambda pid: False)
    calls = []

    class FakeProcess:
        pid = 24680

    def fake_popen(command, stdout, stderr, **kwargs):
        calls.append({"command": command, "stdout": stdout, "stderr": stderr, "kwargs": kwargs})
        return FakeProcess()

    monkeypatch.setattr(persona_manager.subprocess, "Popen", fake_popen)

    response = await persona_manager.api_persona_start(SimpleNamespace(), persona_dir)
    payload = json.loads(response.text)
    saved = json.loads((tmp_path / "global_config.json").read_text(encoding="utf-8"))

    assert response.status == 200
    assert payload["success"] is True
    assert payload["started"] is True
    assert payload["pid"] == 24680
    assert saved["active_persona"] == "sirius"
    assert calls[0]["command"][1:5] == [
        "-m",
        "sirius_pulse.persona_worker",
        "--config",
        str(persona_dir),
    ]
    assert calls[0]["command"][-2:] == ["--log-level", "DEBUG"]
    assert calls[0]["kwargs"]["cwd"] == str(tmp_path.parent)


@pytest.mark.asyncio
async def test_persona_start_when_previous_spawn_is_starting_then_does_not_spawn_again(
    tmp_path, monkeypatch
):
    persona_dir = tmp_path / "personas" / "sirius"
    persona_dir.mkdir(parents=True)
    atomic_write_json(persona_dir / "persona.json", {"name": "sirius"})
    atomic_write_json(tmp_path / "global_config.json", {"active_persona": "sirius"})
    atomic_write_json(
        persona_dir / "engine_state" / "worker_status.json",
        {"status": "starting", "pid": 24680, "started_at": "2026-07-07T00:00:00+00:00"},
    )
    monkeypatch.setattr(persona_manager, "_pid_exists", lambda pid: True)

    def fake_popen(*args, **kwargs):
        raise AssertionError("should not spawn another worker")

    monkeypatch.setattr(persona_manager.subprocess, "Popen", fake_popen)

    response = await persona_manager.api_persona_start(SimpleNamespace(), persona_dir)
    payload = json.loads(response.text)

    assert response.status == 200
    assert payload["success"] is True
    assert payload["started"] is False
    assert payload["already_running"] is True
    assert payload["pid"] == 24680


@pytest.mark.asyncio
async def test_persona_start_when_two_personas_are_requested_then_spawns_both_workers(
    tmp_path, monkeypatch
):
    first_dir = tmp_path / "personas" / "first"
    second_dir = tmp_path / "personas" / "second"
    for persona_dir in (first_dir, second_dir):
        persona_dir.mkdir(parents=True)
        atomic_write_json(persona_dir / "persona.json", {"name": persona_dir.name})
    atomic_write_json(tmp_path / "global_config.json", {"active_persona": ""})
    monkeypatch.setattr(persona_manager, "_pid_exists", lambda pid: False)
    calls = []

    class FakeProcess:
        def __init__(self, pid):
            self.pid = pid

    def fake_popen(command, stdout, stderr, **kwargs):
        calls.append(command)
        return FakeProcess(30000 + len(calls))

    monkeypatch.setattr(persona_manager.subprocess, "Popen", fake_popen)

    first_response = await persona_manager.api_persona_start(SimpleNamespace(), first_dir)
    second_response = await persona_manager.api_persona_start(SimpleNamespace(), second_dir)

    assert json.loads(first_response.text)["started"] is True
    assert json.loads(second_response.text)["started"] is True
    assert len(calls) == 2
    assert str(first_dir) in calls[0]
    assert str(second_dir) in calls[1]
    assert (
        json.loads((first_dir / "engine_state" / "worker_status.json").read_text())["pid"] == 30001
    )
    assert (
        json.loads((second_dir / "engine_state" / "worker_status.json").read_text())["pid"] == 30002
    )
    config = json.loads((tmp_path / "global_config.json").read_text())
    assert config["active_personas"] == ["first", "second"]
    assert config["active_persona"] == "first"


@pytest.mark.asyncio
async def test_webui_named_persona_start_targets_requested_persona(tmp_path, monkeypatch):
    first_dir = tmp_path / "personas" / "first"
    second_dir = tmp_path / "personas" / "second"
    for persona_dir in (first_dir, second_dir):
        persona_dir.mkdir(parents=True)
        atomic_write_json(persona_dir / "persona.json", {"name": persona_dir.name})
    atomic_write_json(tmp_path / "global_config.json", {"active_persona": "first"})
    monkeypatch.setattr(persona_manager, "_pid_exists", lambda pid: False)
    calls = []

    class FakeProcess:
        pid = 24681

    def fake_popen(command, stdout, stderr, **kwargs):
        calls.append(command)
        return FakeProcess()

    monkeypatch.setattr(persona_manager.subprocess, "Popen", fake_popen)
    server = WebUIServer(data_dir=tmp_path)
    request = SimpleNamespace(match_info={"name": "second"})

    response = await server.api_persona_named_start(request)
    payload = json.loads(response.text)

    assert response.status == 200
    assert payload["active"] == "second"
    assert str(second_dir) in calls[0]
    assert not (first_dir / "engine_state" / "worker_status.json").exists()


def test_webui_shutdown_persona_manager_targets_the_matching_worker(tmp_path):
    first_dir = tmp_path / "personas" / "first"
    second_dir = tmp_path / "personas" / "second"

    class Worker:
        def __init__(self, persona_dir):
            self.persona_dir = persona_dir
            self.shutdown_calls = 0

        def shutdown(self):
            self.shutdown_calls += 1

    first = Worker(first_dir)
    second = Worker(second_dir)
    server = WebUIServer(data_dir=tmp_path, persona_manager={"first": first, "second": second})

    assert server._shutdown_persona_manager(second_dir)
    assert second.shutdown_calls == 1
    assert first.shutdown_calls == 0


@pytest.mark.asyncio
async def test_webui_monitoring_overview_includes_all_personas(tmp_path):
    for name, pid in (("first", 31001), ("second", 31002)):
        persona_dir = tmp_path / "personas" / name
        persona_dir.mkdir(parents=True)
        atomic_write_json(persona_dir / "persona.json", {"name": name})
        atomic_write_json(
            persona_dir / "engine_state" / "worker_status.json",
            {"status": "running", "pid": pid},
        )

    server = WebUIServer(data_dir=tmp_path)
    response = await server.api_monitoring_overview(SimpleNamespace())
    payload = json.loads(response.text)

    assert payload["total_personas"] == 2
    assert payload["running_personas"] == 2
    assert {item["name"] for item in payload["personas"]} == {"first", "second"}


@pytest.mark.asyncio
async def test_persona_stop_when_external_worker_is_running_then_sends_sigterm(
    tmp_path, monkeypatch
):
    persona_dir = tmp_path / "personas" / "sirius"
    persona_dir.mkdir(parents=True)
    atomic_write_json(tmp_path / "global_config.json", {"active_persona": "sirius"})
    atomic_write_json(
        persona_dir / "engine_state" / "worker_status.json",
        {"status": "running", "pid": 24680},
    )
    kills = []

    monkeypatch.setattr(persona_manager, "_pid_exists", lambda pid: len(kills) == 0)
    monkeypatch.setattr(persona_manager.os, "kill", lambda pid, sig: kills.append((pid, sig)))

    response = await persona_manager.api_persona_stop(SimpleNamespace(), persona_dir)
    payload = json.loads(response.text)
    saved = json.loads((tmp_path / "global_config.json").read_text(encoding="utf-8"))

    assert response.status == 200
    assert payload["success"] is True
    assert payload["stopped"] is True
    assert saved["active_persona"] == ""
    assert kills == [(24680, persona_manager.signal.SIGTERM)]


@pytest.mark.asyncio
async def test_webui_providers_get_when_registry_exists_then_returns_masked_providers(tmp_path):
    atomic_write_json(
        tmp_path / "providers" / "provider_keys.json",
        {
            "providers": {
                "aliyun-bailian": {
                    "type": "aliyun-bailian",
                    "api_key": "sk-secret",
                    "base_url": "https://dashscope.example",
                    "enabled": True,
                    "models": ["qwen-plus"],
                    "healthcheck_model": "qwen-plus",
                }
            }
        },
    )
    server = WebUIServer(data_dir=tmp_path)

    response = await server.api_providers_get(SimpleNamespace())
    payload = json.loads(response.text)

    assert payload["providers"] == [
        {
            "name": "aliyun-bailian",
            "type": "aliyun-bailian",
            "platform_type": "aliyun-bailian",
            "api_key": "sk-s****",
            "base_url": "https://dashscope.example",
            "enabled": True,
            "models": ["qwen-plus"],
            "healthcheck_model": "qwen-plus",
        }
    ]


@pytest.mark.asyncio
async def test_webui_providers_get_when_legacy_list_has_duplicate_names_then_migrates_unique_names(
    tmp_path,
):
    path = tmp_path / "providers" / "provider_keys.json"
    atomic_write_json(
        path,
        {
            "providers": [
                {"name": "openai", "type": "openai-compatible", "api_key": "sk-one"},
                {"name": "openai", "type": "openai-compatible", "api_key": "sk-two"},
            ]
        },
    )
    server = WebUIServer(data_dir=tmp_path)

    response = await server.api_providers_get(SimpleNamespace())
    payload = json.loads(response.text)
    saved = json.loads(path.read_text(encoding="utf-8"))

    assert response.status == 200
    assert [item["name"] for item in payload["providers"]] == ["openai", "openai-2"]
    assert [item["api_key"] for item in payload["providers"]] == ["sk-o****", "sk-t****"]
    assert list(saved["providers"]) == ["openai", "openai-2"]


@pytest.mark.asyncio
async def test_webui_providers_get_when_registry_uses_legacy_list_then_returns_providers(tmp_path):
    atomic_write_json(
        tmp_path / "providers" / "provider_keys.json",
        {
            "providers": [
                {
                    "name": "deepseek-main",
                    "platform_type": "deepseek",
                    "api_key": "sk-deepseek",
                    "enabled": True,
                }
            ]
        },
    )
    server = WebUIServer(data_dir=tmp_path)

    response = await server.api_providers_get(SimpleNamespace())
    payload = json.loads(response.text)

    assert payload["providers"] == [
        {
            "name": "deepseek-main",
            "type": "deepseek",
            "platform_type": "deepseek",
            "api_key": "sk-d****",
            "enabled": True,
        }
    ]


@pytest.mark.asyncio
async def test_orchestration_get_when_persona_scoped_then_uses_global_provider_models(tmp_path):
    persona_dir = tmp_path / "personas" / "sirius"
    persona_dir.mkdir(parents=True)
    atomic_write_json(
        tmp_path / "providers" / "provider_keys.json",
        {
            "providers": {
                "deepseek": {
                    "type": "deepseek",
                    "api_key": "sk-deepseek",
                    "enabled": True,
                    "models": ["deepseek-chat"],
                }
            }
        },
    )

    response = await api_orchestration_get(
        SimpleNamespace(app={DATA_DIR_KEY: tmp_path}),
        persona_dir,
    )
    payload = json.loads(response.text)

    assert [choice["value"] for choice in payload["model_choices"]] == ["deepseek/deepseek-chat"]


@pytest.mark.asyncio
async def test_webui_providers_post_when_key_is_masked_then_preserves_secret_and_reloads(
    tmp_path,
):
    atomic_write_json(
        tmp_path / "providers" / "provider_keys.json",
        {
            "providers": {
                "aliyun-bailian": {
                    "type": "aliyun-bailian",
                    "api_key": "sk-original",
                    "base_url": "https://old.example",
                    "models_url": "https://models.example",
                    "enabled": True,
                    "models": ["old-model"],
                    "healthcheck_model": "old-model",
                },
                "deepseek": {
                    "type": "deepseek",
                    "api_key": "sk-deleted",
                },
            }
        },
    )
    server = WebUIServer(data_dir=tmp_path)

    async def json_body():
        return {
            "providers": [
                {
                    "name": "aliyun-bailian",
                    "platform_type": "aliyun-bailian",
                    "api_key": "sk-o****",
                    "base_url": "https://new.example",
                    "enabled": False,
                    "models": ["qwen-plus"],
                    "healthcheck_model": "qwen-plus",
                }
            ]
        }

    response = await server.api_providers_post(SimpleNamespace(json=json_body))
    saved = json.loads((tmp_path / "providers" / "provider_keys.json").read_text(encoding="utf-8"))

    assert response.status == 200
    assert saved == {
        "providers": {
            "aliyun-bailian": {
                "type": "aliyun-bailian",
                "api_key": "sk-original",
                "base_url": "https://new.example",
                "models_url": "https://models.example",
                "enabled": False,
                "models": ["qwen-plus"],
                "healthcheck_model": "qwen-plus",
            }
        }
    }
    assert (tmp_path / "engine_state" / "reload_requested").read_text(
        encoding="utf-8"
    ) == "provider"


@pytest.mark.asyncio
async def test_webui_providers_post_when_name_changes_then_rekeys_and_migrates_models(tmp_path):
    provider_path = tmp_path / "providers" / "provider_keys.json"
    orchestration_path = tmp_path / "personas" / "sirius" / "engine_state" / "orchestration.json"
    atomic_write_json(
        provider_path,
        {
            "providers": {
                "team-a": {
                    "type": "openai-compatible",
                    "api_key": "sk-secret",
                    "models": ["shared-model"],
                }
            }
        },
    )
    atomic_write_json(
        orchestration_path,
        {
            "chat_model": "team-a/shared-model",
            "task_models": {"memory_extract": "team-a/shared-model"},
        },
    )
    server = WebUIServer(data_dir=tmp_path)

    async def json_body():
        return {
            "providers": [
                {
                    "name": "team-b",
                    "original_name": "team-a",
                    "type": "openai-compatible",
                    "api_key": "sk-s****",
                    "models": ["shared-model"],
                    "enabled": True,
                }
            ]
        }

    response = await server.api_providers_post(SimpleNamespace(json=json_body))
    providers = json.loads(provider_path.read_text(encoding="utf-8"))["providers"]
    orchestration = json.loads(orchestration_path.read_text(encoding="utf-8"))

    assert response.status == 200
    assert list(providers) == ["team-b"]
    assert providers["team-b"]["api_key"] == "sk-secret"
    assert orchestration["chat_model"] == "team-b/shared-model"
    assert orchestration["task_models"]["memory_extract"] == "team-b/shared-model"


@pytest.mark.asyncio
async def test_webui_providers_post_when_names_duplicate_then_rejects(tmp_path):
    path = tmp_path / "providers" / "provider_keys.json"
    atomic_write_json(
        path,
        {"providers": {"existing": {"type": "deepseek", "api_key": "sk-existing"}}},
    )
    before = path.read_text(encoding="utf-8")
    server = WebUIServer(data_dir=tmp_path)

    async def json_body():
        return {
            "providers": [
                {"name": "team-a", "type": "deepseek", "api_key": "sk-a"},
                {"name": "TEAM-A", "type": "deepseek", "api_key": "sk-b"},
            ]
        }

    response = await server.api_providers_post(SimpleNamespace(json=json_body))

    assert response.status == 400
    assert "名称重复" in json.loads(response.text)["error"]
    assert path.read_text(encoding="utf-8") == before


@pytest.mark.asyncio
async def test_engine_reload_writes_worker_reload_requested_flag(tmp_path):
    server = WebUIServer(data_dir=tmp_path)

    response = await server.api_engine_reload(SimpleNamespace())

    assert response.status == 200
    assert (tmp_path / "engine_state" / "reload_requested").read_text(encoding="utf-8") == "all"
    assert not (tmp_path / "engine_state" / "reload.flag").exists()


@pytest.mark.asyncio
async def test_webui_proxy_when_empty_then_returns_blank_and_posts_persist(tmp_path):
    server = WebUIServer(data_dir=tmp_path)

    response = await server.api_providers_proxy_get(SimpleNamespace())
    payload = json.loads(response.text)
    assert payload == {"proxy": {"http": "", "https": "", "no_proxy": ""}}

    post_response = await server.api_providers_proxy_post(
        _FakeJsonRequest({"http": "http://127.0.0.1:7890", "https": "", "no_proxy": "localhost"})
    )
    payload = json.loads(post_response.text)
    assert payload["success"] is True
    assert payload["proxy"]["http"] == "http://127.0.0.1:7890"

    saved = json.loads((tmp_path / "providers" / "proxy.json").read_text(encoding="utf-8"))
    assert saved == {"http": "http://127.0.0.1:7890", "https": "", "no_proxy": "localhost"}

    get_response = await server.api_providers_proxy_get(SimpleNamespace())
    assert json.loads(get_response.text)["proxy"]["http"] == "http://127.0.0.1:7890"


@pytest.mark.asyncio
async def test_webui_models_probe_when_draft_config_then_passes_inline_credentials(
    tmp_path, monkeypatch
):
    captured: dict[str, object] = {}

    async def _fake_probe(**kwargs):
        captured.update(kwargs)
        return ["kimi-k3", "glm-5.2"], "https://opencode.ai/zen/v1/models"

    monkeypatch.setattr("sirius_pulse.webui.server_core.probe_provider_models", _fake_probe)
    server = WebUIServer(data_dir=tmp_path)

    response = await server.api_providers_models_probe(
        _FakeJsonRequest(
            {
                "type": "opencode-go",
                "base_url": "https://opencode.ai/zen/go/v1",
                "api_key": "sk-draft",
            }
        )
    )
    payload = json.loads(response.text)

    assert payload["success"] is True
    assert payload["models"] == ["kimi-k3", "glm-5.2"]
    assert captured["provider_type"] == "opencode-go"
    assert captured["api_key"] == "sk-draft"
    assert captured["base_url"] == "https://opencode.ai/zen/go/v1"


@pytest.mark.asyncio
async def test_webui_models_probe_when_named_provider_then_uses_stored_key(tmp_path, monkeypatch):
    atomic_write_json(
        tmp_path / "providers" / "provider_keys.json",
        {
            "providers": {
                "deepseek": {
                    "type": "deepseek",
                    "api_key": "sk-stored-secret",
                    "base_url": "https://api.deepseek.com",
                    "models": ["deepseek-chat"],
                }
            }
        },
    )
    captured: dict[str, object] = {}

    async def _fake_probe(**kwargs):
        captured.update(kwargs)
        return ["deepseek-chat", "deepseek-reasoner"], "https://api.deepseek.com/models"

    monkeypatch.setattr("sirius_pulse.webui.server_core.probe_provider_models", _fake_probe)
    server = WebUIServer(data_dir=tmp_path)

    # 前端对已保存 Provider 只回传脱敏 Key（含 ****），服务端应使用磁盘真实 Key
    response = await server.api_providers_models_probe(
        _FakeJsonRequest({"name": "deepseek", "api_key": "sk-****cret"})
    )
    payload = json.loads(response.text)

    assert payload["success"] is True
    assert captured["api_key"] == "sk-stored-secret"
    assert captured["base_url"] == "https://api.deepseek.com"
