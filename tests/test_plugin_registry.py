"""插件注册中心在用户消息入口上的业务行为测试。"""

from __future__ import annotations

import pytest

from sirius_pulse.plugins import PluginLoader, PluginLoadError, PluginRegistry
from sirius_pulse.plugins.models import (
    PluginCommandDef,
    PluginDefinition,
    PluginPermissionDef,
    PluginRenderDef,
)


def _plugin(
    name: str,
    commands: list[PluginCommandDef],
    *,
    description: str = "test plugin",
    permissions: PluginPermissionDef | None = None,
) -> PluginDefinition:
    return PluginDefinition(
        name=name,
        display_name=name,
        description=description,
        version="1.0",
        commands=commands,
        events=[],
        parameters=[],
        permissions=permissions or PluginPermissionDef(),
        render=PluginRenderDef(),
        dependencies=[],
        source_path=None,
    )


def test_plugin_loader_when_plugin_directory_contains_tests_then_skips_test_directory(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_plugin.py").write_text("class NotAPlugin: pass", encoding="utf-8")
    (tmp_path / "real_plugin").mkdir()
    (tmp_path / "real_plugin" / "plugin.py").write_text(
        "from sirius_pulse.plugins import PluginBase\n"
        "class RealPlugin(PluginBase):\n"
        "    _plugin_name = 'real_plugin'\n",
        encoding="utf-8",
    )

    discovered = PluginLoader(tmp_path).discover()

    assert [path.name for path in discovered] == ["real_plugin"]


def test_plugin_loader_rejects_symlinked_plugin_sources(tmp_path):
    external = tmp_path / "external.py"
    external.write_text("raise AssertionError('must not execute')", encoding="utf-8")
    plugin_path = tmp_path / "linked_plugin"
    plugin_path.mkdir()
    try:
        (plugin_path / "__init__.py").symlink_to(external)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    discovered = PluginLoader(tmp_path).discover()

    assert plugin_path not in discovered


def test_plugin_loader_metadata_listing_does_not_import_or_install(tmp_path, monkeypatch):
    plugin_path = tmp_path / "safe_metadata"
    plugin_path.mkdir()
    marker = plugin_path / "imported.txt"
    (plugin_path / "__init__.py").write_text(
        "from pathlib import Path\n"
        "Path(__file__).with_name('imported.txt').write_text('executed')\n"
        "class SafeMetadataPlugin:\n"
        "    _plugin_name = 'safe_metadata'\n"
        "    _plugin_display_name = 'Safe metadata'\n"
        "    _plugin_min_framework_version = '1.1.0'\n"
        "    _plugin_dependencies = ['example-package>=1']\n"
        "    _plugin_parameters = [{'name': 'interval', 'type': 'int'}]\n",
        encoding="utf-8",
    )
    install_calls: list[list[str]] = []
    monkeypatch.setattr(
        PluginLoader,
        "install_dependencies",
        staticmethod(lambda deps: (install_calls.append(list(deps)) or (len(deps), 0))),
    )
    monkeypatch.setattr(
        "sirius_pulse.plugins.loader._installed_framework_version",
        lambda: "1.1.0",
    )

    definitions = PluginLoader(tmp_path).load_all_definitions(metadata_only=True)

    assert [definition.name for definition in definitions] == ["safe_metadata"]
    assert definitions[0].dependencies == ["example-package>=1"]
    assert definitions[0].parameters[0].name == "interval"
    assert marker.exists() is False
    assert install_calls == []


def test_plugin_loader_metadata_mode_rejects_dependency_install_request(tmp_path):
    with pytest.raises(ValueError, match="metadata-only"):
        PluginLoader(tmp_path).load_all_definitions(metadata_only=True, install_dependencies=True)


def test_plugin_registry_when_plugin_is_registered_then_user_command_can_find_it():
    registry = PluginRegistry()
    definition = _plugin(
        "dice_plugin",
        [
            PluginCommandDef(
                name="dice",
                patterns=["/dice"],
                pattern_type="prefix",
                description="掷骰子",
            )
        ],
    )

    registry.register(definition, instance=None)
    match = registry.match_message("/dice 100")

    assert registry.get("dice_plugin") is definition
    assert match is not None
    assert match.plugin_name == "dice_plugin"
    assert match.command_name == "dice"
    assert match.lexed is not None
    assert match.lexed.positional_args == ["100"]


def test_plugin_registry_when_user_uses_unrelated_command_then_no_plugin_is_selected():
    registry = PluginRegistry()
    registry.register(
        _plugin(
            "dice_plugin",
            [PluginCommandDef(name="dice", patterns=["/dice"], pattern_type="prefix")],
        ),
        instance=None,
    )

    assert registry.match_message("/weather Beijing") is None


def test_plugin_registry_when_keyword_plugin_exists_then_natural_language_can_match_it():
    registry = PluginRegistry()
    registry.register(
        _plugin(
            "weather_plugin",
            [PluginCommandDef(name="weather", patterns=["天气"], pattern_type="keyword")],
        ),
        instance=None,
    )

    match = registry.match_message("帮我看看北京天气怎么样")

    assert match is not None
    assert match.plugin_name == "weather_plugin"
    assert match.confidence == 0.9


def test_plugin_registry_when_plugin_is_hidden_from_intent_then_description_excludes_it():
    registry = PluginRegistry()
    registry.register(
        _plugin(
            "admin_plugin",
            [PluginCommandDef(name="admin", patterns=["/admin"], pattern_type="prefix")],
            description="管理员工具",
            permissions=PluginPermissionDef(hidden_from_intent=True),
        ),
        instance=None,
    )

    assert registry.get_plugin_descriptions(caller_is_developer=True) == ""
    assert registry.match_message("/admin reload") is not None


def test_plugin_registry_when_plugin_is_developer_only_then_normal_users_do_not_see_it():
    registry = PluginRegistry()
    registry.register(
        _plugin(
            "debug_plugin",
            [PluginCommandDef(name="debug", patterns=["/debug"], pattern_type="prefix")],
            description="调试工具",
            permissions=PluginPermissionDef(developer_only=True),
        ),
        instance=None,
    )

    assert registry.get_plugin_descriptions(caller_is_developer=False) == ""
    assert "debug_plugin" in registry.get_plugin_descriptions(caller_is_developer=True)


def test_plugin_registry_when_duplicate_plugin_name_is_loaded_then_registry_rejects_it():
    registry = PluginRegistry()
    registry.register(_plugin("same_name", []), instance=None)

    with pytest.raises(ValueError):
        registry.register(_plugin("same_name", []), instance=None)


def test_plugin_registry_when_plugin_is_uninstalled_then_its_commands_stop_matching():
    registry = PluginRegistry()
    registry.register(
        _plugin(
            "temporary_plugin",
            [PluginCommandDef(name="temp", patterns=["/temp"], pattern_type="prefix")],
        ),
        instance=None,
    )

    registry.unregister("temporary_plugin")

    assert registry.get("temporary_plugin") is None
    assert registry.match_message("/temp") is None
    assert registry.plugin_count == 0


def test_plugin_registry_when_workspace_reloads_then_clear_removes_all_runtime_plugins():
    registry = PluginRegistry()
    registry.register(_plugin("a", []), instance=None)
    registry.register(_plugin("b", []), instance=None)

    registry.clear()

    assert registry.plugin_names == []
    assert registry.plugin_count == 0


@pytest.mark.parametrize(
    ("required_version", "accepted"),
    [("1.1.0", True), ("1.2.0", False), ("not-a-version", False)],
)
def test_plugin_loader_when_framework_requirement_is_checked_then_incompatible_plugin_is_rejected(
    tmp_path, monkeypatch, required_version, accepted
):
    plugin_path = tmp_path / "versioned_plugin"
    plugin_path.mkdir()
    (plugin_path / "__init__.py").write_text(
        "from sirius_pulse.plugins import PluginBase\n"
        "class VersionedPlugin(PluginBase):\n"
        "    _plugin_name = 'versioned_plugin'\n"
        f"    _plugin_min_framework_version = {required_version!r}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "sirius_pulse.plugins.loader._installed_framework_version",
        lambda: "1.1.0",
    )

    loader = PluginLoader(tmp_path)
    if accepted:
        definition = loader.load_definition(plugin_path)
        assert definition is not None
        assert definition.min_framework_version == required_version
    else:
        with pytest.raises(PluginLoadError):
            loader.load_definition(plugin_path)
