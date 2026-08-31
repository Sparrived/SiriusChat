"""WebUI Plugin 管理 API — 全局 Plugin 查阅、启停控制与配置管理。

Plugin 目录位于项目根 plugins/（与 data/ 同级），
配置持久化到 plugins/_config.json。

v1.2+: 支持插件自定义配置（如 chat_analyzer 的时间配置）
"""

from __future__ import annotations

import json
import logging
import math
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from aiohttp import web

from sirius_pulse.plugins.config import get_config_manager
from sirius_pulse.plugins.loader import PluginLoader
from sirius_pulse.plugins.models import PluginDefinition
from sirius_pulse.webui.server_utils import _json_response, handle_api_errors

LOG = logging.getLogger("sirius.webui")

# ── 模块级缓存，避免每次 API 请求都重新扫描磁盘和执行 importlib ──
_plugin_definitions_cache: dict[str, tuple[float, list[PluginDefinition]]] = {}
_CACHE_TTL = 60.0  # 秒
_SECRET_MASK = "********"
_MASKED_SECRET_VALUES = {"", _SECRET_MASK, "[已隐藏]", "••••••••"}
_MISSING = object()


class MaskedSecretUpdateError(ValueError):
    """Raised when a nested secret cannot be preserved without guessing."""


_SECRET_SUFFIXES = (
    "_token",
    "_tokens",
    "_key",
    "_keys",
    "_secret",
    "_secrets",
    "_password",
    "_passwords",
)


def _normalize_setting_key(key: Any) -> str:
    text = str(key)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    return re.sub(r"[^a-z0-9]+", "_", text.casefold()).strip("_")


def _is_secret_setting_key(
    key: Any,
    definition: PluginDefinition | None = None,
) -> bool:
    name = str(key)
    if definition is not None:
        for parameter in definition.parameters:
            if parameter.name == name and parameter.type.casefold() in {"password", "secret"}:
                return True
    normalized = _normalize_setting_key(name)
    return normalized in {
        "password",
        "passwords",
        "secret",
        "secrets",
        "token",
        "tokens",
        "key",
        "keys",
        "api_key",
        "api_keys",
        "access_token",
        "refresh_token",
        "authorization",
    } or normalized.endswith(_SECRET_SUFFIXES)


def _is_masked_secret_value(value: Any) -> bool:
    return isinstance(value, str) and value.strip() in _MASKED_SECRET_VALUES


def _url_contains_plaintext_secret(value: Any) -> bool:
    """Detect credentials embedded in an otherwise non-secret URL setting."""
    if not isinstance(value, str) or not any(marker in value for marker in ("://", "?", "#")):
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    if parsed.username is not None or parsed.password is not None:
        return True
    return any(
        _is_secret_setting_key(query_key) and bool(query_value)
        for component in (parsed.query, parsed.fragment)
        for query_key, query_value in parse_qsl(component, keep_blank_values=True)
    )


def _contains_plaintext_secret(
    value: Any,
    *,
    key: Any = "",
    definition: PluginDefinition | None = None,
    parameter: Any = None,
) -> bool:
    """Return whether a settings tree attempts to persist a secret value.

    Secret fields are environment-backed by design.  Masked placeholders are
    update-preserving values, while any other non-empty value must be rejected
    instead of being written to ``plugins/_config.json``.
    """
    parameter = parameter or _parameter_for(definition, key)
    if _is_secret_parameter(key, definition, parameter):
        return bool(value) and not _is_masked_secret_value(value)
    if _url_contains_plaintext_secret(value):
        return True
    if isinstance(value, dict):
        field_parameters = {
            str(field.get("name")): field
            for field in _parameter_fields(parameter)
            if field.get("name")
        }
        return any(
            _contains_plaintext_secret(
                child,
                key=child_key,
                definition=definition,
                parameter=field_parameters.get(str(child_key)),
            )
            for child_key, child in value.items()
        )
    if isinstance(value, list):
        return any(
            _contains_plaintext_secret(
                item,
                key=key,
                definition=definition,
                parameter=parameter,
            )
            for item in value
        )
    return False


def _parameter_for(
    definition: PluginDefinition | None,
    key: Any,
) -> Any:
    if definition is None:
        return None
    name = str(key)
    return next((parameter for parameter in definition.parameters if parameter.name == name), None)


def _parameter_value(parameter: Any, key: str, default: Any = None) -> Any:
    if parameter is None:
        return default
    if isinstance(parameter, dict):
        return parameter.get(key, default)
    return getattr(parameter, key, default)


def _parameter_fields(parameter: Any) -> list[dict[str, Any]]:
    fields = _parameter_value(parameter, "fields", [])
    return (
        [field for field in fields if isinstance(field, dict)] if isinstance(fields, list) else []
    )


def _secret_field_names(parameter: Any, definition: PluginDefinition | None) -> set[str]:
    """Return declared nested secret fields for an object-array parameter."""
    return {
        str(field.get("name"))
        for field in _parameter_fields(parameter)
        if field.get("name") and _is_secret_parameter(field.get("name"), definition, field)
    }


_OBJECT_IDENTITY_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")


def _stable_secret_row_identity(item: Any) -> tuple[str, ...] | None:
    """Return a non-secret, stable identity suitable for retaining a secret.

    Generic repository-like rows use a normalized ``owner/repo`` pair rather
    than display text or list position.  A partial or malformed pair has no
    fallback identity: retaining a secret for it would be an unsafe guess.
    Other object-array schemas can use an id, UUID, slug, name, URL, or host.
    """
    if not isinstance(item, dict):
        return None

    if "owner" in item or "repo" in item:
        owner = item.get("owner")
        repo = item.get("repo")
        if not isinstance(owner, str) or not isinstance(repo, str):
            return None
        owner_text = owner.strip()
        repo_text = repo.strip()
        # Validate before normalizing: the GitHub identity grammar is ASCII;
        # Unicode case-folding must not turn an invalid source identifier into
        # a valid-looking secret lookup key.
        if not _OBJECT_IDENTITY_PART.fullmatch(owner_text) or not _OBJECT_IDENTITY_PART.fullmatch(
            repo_text
        ):
            return None
        return ("repository", owner_text.casefold(), repo_text.casefold())

    for fields in (("id",), ("uuid",), ("slug",), ("name",), ("url",), ("host",)):
        values = tuple(str(item.get(field, "")).strip() for field in fields)
        if all(values):
            return fields + values
    return None


def _row_has_explicit_masked_secret(item: Any, secret_fields: set[str]) -> bool:
    """Return whether a row explicitly asks to retain a hidden old secret."""
    explicit_masks = _MASKED_SECRET_VALUES - {""}
    return isinstance(item, dict) and any(
        field in item and isinstance(item[field], str) and item[field].strip() in explicit_masks
        for field in secret_fields
    )


def _is_secret_parameter(
    key: Any,
    definition: PluginDefinition | None,
    parameter: Any = None,
) -> bool:
    parameter_type = str(_parameter_value(parameter, "type", "")).casefold()
    return parameter_type in {"password", "secret"} or _is_secret_setting_key(key, definition)


def _mask_secret_value(
    value: Any,
    *,
    key: Any = "",
    definition: PluginDefinition | None = None,
    parameter: Any = None,
) -> Any:
    parameter = parameter or _parameter_for(definition, key)
    if _is_secret_parameter(key, definition, parameter):
        return _SECRET_MASK
    if isinstance(value, dict):
        field_parameters = {
            str(field.get("name")): field
            for field in _parameter_fields(parameter)
            if field.get("name")
        }
        return {
            str(child_key): _mask_secret_value(
                child_value,
                key=child_key,
                definition=definition,
                parameter=field_parameters.get(str(child_key)),
            )
            for child_key, child_value in value.items()
        }
    if isinstance(value, list):
        return [
            _mask_secret_value(item, key=key, definition=definition, parameter=parameter)
            for item in value
        ]
    return value


def _settings_update_without_masked_secrets(
    settings: dict[str, Any],
    definition: PluginDefinition | None = None,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Drop masked secrets while preserving their existing nested values."""

    def merge(value: Any, old_value: Any, *, key: Any, parameter: Any = None) -> Any:
        parameter = parameter or _parameter_for(definition, key)
        if _is_secret_parameter(key, definition, parameter) and _is_masked_secret_value(value):
            return old_value if old_value is not _MISSING else _MISSING
        if isinstance(value, dict):
            old_mapping = old_value if isinstance(old_value, dict) else {}
            result: dict[str, Any] = {}
            fields_by_name = {
                str(field.get("name")): field
                for field in _parameter_fields(parameter)
                if field.get("name")
            }
            for child_key, child_value in value.items():
                merged = merge(
                    child_value,
                    old_mapping.get(child_key, _MISSING),
                    key=child_key,
                    parameter=fields_by_name.get(str(child_key)),
                )
                if merged is not _MISSING:
                    result[str(child_key)] = merged
            # Browser forms deliberately omit environment-managed secret fields.
            # Retain them only from the already matched row, never by position.
            for secret_field in _secret_field_names(parameter, definition):
                if secret_field not in result and secret_field in old_mapping:
                    result[secret_field] = old_mapping[secret_field]
            return result
        if isinstance(value, list):
            old_items = old_value if isinstance(old_value, list) else []
            secret_fields = _secret_field_names(parameter, definition)
            if not secret_fields:
                # Keep legacy settings safe even when no parameter metadata was
                # available at the time they were written.
                secret_fields = {
                    str(child_key)
                    for old_item in old_items
                    if isinstance(old_item, dict)
                    for child_key in old_item
                    if _is_secret_setting_key(child_key, definition)
                }
            old_by_identity: dict[tuple[str, ...], Any] = {}
            ambiguous_identities: set[tuple[str, ...]] = set()
            if secret_fields:
                for old_item in old_items:
                    identity = _stable_secret_row_identity(old_item)
                    if identity is None:
                        continue
                    if identity in old_by_identity:
                        ambiguous_identities.add(identity)
                    else:
                        old_by_identity[identity] = old_item

            result_items: list[Any] = []
            seen_new_identities: set[tuple[str, ...]] = set()
            for item in value:
                matched_old_item: Any = _MISSING
                identity = _stable_secret_row_identity(item)
                wants_secret_retention = _row_has_explicit_masked_secret(item, secret_fields)
                if secret_fields and identity is not None:
                    if identity in seen_new_identities:
                        raise MaskedSecretUpdateError("对象列表包含重复的稳定标识；请先消除重复仓库后再保留密钥")
                    seen_new_identities.add(identity)
                    if identity in ambiguous_identities:
                        raise MaskedSecretUpdateError("现有对象列表包含重复的稳定标识；请先移除重复项后再保留密钥")
                    matched_old_item = old_by_identity.get(identity, _MISSING)

                # Never use the item index as a secret lookup key.  An explicit
                # masked field means "retain this row's current secret" and is
                # only valid when one unambiguous stable identity matched.
                if wants_secret_retention and matched_old_item is _MISSING:
                    raise MaskedSecretUpdateError(
                        "无法为掩码密钥匹配稳定对象标识；新对象请省略该字段，并为现有对象保留有效 owner/repo、id、name 或 slug"
                    )
                merged = merge(item, matched_old_item, key=key, parameter=parameter)
                if merged is not _MISSING:
                    result_items.append(merged)
            return result_items
        return value

    result: dict[str, Any] = {}
    old_settings = existing if isinstance(existing, dict) else {}
    for key, value in settings.items():
        merged = merge(value, old_settings.get(key, _MISSING), key=key)
        if merged is not _MISSING:
            result[str(key)] = merged
    return result


def _masked_parameter(parameter: Any) -> dict[str, Any]:
    """Serialize a parameter without exposing secret defaults or child fields."""
    parameter_type = str(_parameter_value(parameter, "type", ""))
    result = {
        "name": _parameter_value(parameter, "name", ""),
        "type": parameter_type,
        "description": _parameter_value(parameter, "description", ""),
        "required": _parameter_value(parameter, "required", False),
        "default": _parameter_value(parameter, "default"),
        "choices": _parameter_value(parameter, "choices"),
        "fields": _parameter_fields(parameter) or None,
        "minimum": _parameter_value(parameter, "minimum"),
        "maximum": _parameter_value(parameter, "maximum"),
        "group": _parameter_value(parameter, "group", ""),
    }
    if _is_secret_parameter(result["name"], None, parameter):
        result["default"] = _SECRET_MASK
    if _parameter_fields(parameter):
        result["fields"] = [
            {
                **field,
                "default": (
                    _SECRET_MASK
                    if _is_secret_parameter(field.get("name", ""), None, field)
                    else field.get("default")
                ),
            }
            for field in _parameter_fields(parameter)
        ]
    return result


def _masked_settings(
    settings: Any,
    definition: PluginDefinition | None = None,
) -> dict[str, Any]:
    if not isinstance(settings, dict):
        return {}
    declared = (
        {parameter.name for parameter in definition.parameters} if definition is not None else None
    )
    return {
        str(key): _mask_secret_value(value, key=key, definition=definition)
        for key, value in settings.items()
        if declared is None or str(key) in declared
    }


def _definition_for(manager: Any, plugin_name: str) -> PluginDefinition | None:
    injected_definitions = getattr(manager, "plugin_definitions", None)
    if isinstance(injected_definitions, dict):
        candidate = injected_definitions.get(plugin_name)
        if isinstance(candidate, PluginDefinition):
            return candidate
    try:
        plugins_dir = _plugins_dir(manager)
        return next(
            (item for item in _load_definitions_cached(plugins_dir) if item.name == plugin_name),
            None,
        )
    except Exception:
        return None


def _effective_permissions(
    definition: PluginDefinition,
    configured: Any,
) -> dict[str, Any]:
    """Return safe effective permissions without weakening manifest restrictions."""
    overlay = configured if isinstance(configured, dict) else {}
    manifest = definition.permissions
    configured_blacklist = overlay.get("group_blacklist")
    group_blacklist = (
        [str(item).strip() for item in configured_blacklist if str(item).strip()]
        if isinstance(configured_blacklist, list)
        else list(manifest.group_blacklist)
    )
    configured_rate = overlay.get("rate_limit_calls_per_minute")
    rate_limit = (
        configured_rate
        if type(configured_rate) is int and 1 <= configured_rate <= 1000
        else manifest.rate_limit_calls_per_minute
    )
    return {
        "group_blacklist": group_blacklist,
        "developer_only": bool(manifest.developer_only or overlay.get("developer_only") is True),
        "hidden_from_intent": bool(
            manifest.hidden_from_intent or overlay.get("hidden_from_intent") is True
        ),
        "rate_limit_calls_per_minute": rate_limit,
        "developer_only_locked": bool(manifest.developer_only),
        "hidden_from_intent_locked": bool(manifest.hidden_from_intent),
    }


def _validate_parameter_value(value: Any, parameter: Any, *, path: str) -> str | None:
    """Validate a JSON setting against the declared Plugin parameter contract."""
    parameter_type = str(_parameter_value(parameter, "type", "str")).casefold()
    required = bool(_parameter_value(parameter, "required", False))
    if required and value in (None, "", [], {}):
        return f"{path} 不能为空"
    if value is None:
        return None

    string_types = {"str", "string", "model", "password", "secret"}
    if parameter_type in string_types:
        if not isinstance(value, str):
            return f"{path} 必须是字符串"
    elif parameter_type in {"bool", "boolean"}:
        if type(value) is not bool:
            return f"{path} 必须是布尔值"
    elif parameter_type == "int":
        if type(value) is not int:
            return f"{path} 必须是整数"
    elif parameter_type in {"float", "number"}:
        if type(value) not in {int, float}:
            return f"{path} 必须是有限数字"
    elif parameter_type in {"list", "array", "checkbox_group"}:
        if not isinstance(value, list):
            return f"{path} 必须是列表"
    elif parameter_type in {"object", "json"}:
        if not isinstance(value, dict):
            return f"{path} 必须是对象"
    elif parameter_type != "object_array":
        return f"{path} 使用了不支持的参数类型：{parameter_type}"

    if parameter_type in {"int", "float", "number"}:
        try:
            numeric_value = float(value)
        except (TypeError, ValueError, OverflowError):
            return f"{path} 必须是有限数字"
        if not math.isfinite(numeric_value):
            return f"{path} 必须是有限数字"
        minimum = _parameter_value(parameter, "minimum")
        maximum = _parameter_value(parameter, "maximum")
        try:
            if minimum is not None and numeric_value < float(minimum):
                return f"{path} 不能小于 {minimum}"
            if maximum is not None and numeric_value > float(maximum):
                return f"{path} 不能大于 {maximum}"
        except (TypeError, ValueError, OverflowError):
            return f"{path} 的数值边界声明无效"

    fields = {
        str(field.get("name")): field for field in _parameter_fields(parameter) if field.get("name")
    }
    if parameter_type in {"object", "json"} and fields:
        objects = [(path, value)]
    elif parameter_type == "object_array":
        if not isinstance(value, list):
            return f"{path} 必须是对象列表"
        objects = [(f"{path}[{index}]", item) for index, item in enumerate(value)]
    else:
        objects = []

    for item_path, item in objects:
        if not isinstance(item, dict):
            return f"{item_path} 必须是对象"
        unknown = set(item) - set(fields)
        if unknown:
            return f"{item_path} 包含未声明字段：{sorted(unknown)[0]}"
        for field_name, field in fields.items():
            if bool(_parameter_value(field, "required", False)) and field_name not in item:
                return f"{item_path}.{field_name} 不能为空"
        for field_name, field_value in item.items():
            error = _validate_parameter_value(
                field_value,
                fields[field_name],
                path=f"{item_path}.{field_name}",
            )
            if error:
                return error

    choices = _parameter_value(parameter, "choices")
    if isinstance(choices, list) and choices:
        if isinstance(value, list):
            if any(not any(item == choice for choice in choices) for item in value):
                return f"{path} 包含不支持的选项"
        elif not any(value == choice for choice in choices):
            return f"{path} 不是支持的选项"
    return None


def _validate_settings_schema(
    settings: dict[str, Any],
    definition: PluginDefinition,
) -> str | None:
    """Reject undeclared, incomplete, or wrongly typed settings before persistence."""
    parameters = {parameter.name: parameter for parameter in definition.parameters}
    for parameter in definition.parameters:
        if (
            parameter.required
            and parameter.name not in settings
            and not _is_secret_parameter(parameter.name, definition, parameter)
        ):
            return f"{parameter.name} 不能为空"
    for key, value in settings.items():
        declared_parameter = parameters.get(str(key))
        if declared_parameter is None:
            return f"未声明的插件设置：{key}"
        error = _validate_parameter_value(value, declared_parameter, path=str(key))
        if error:
            return error
    return None


def _data_dir(manager: Any) -> Path:
    """Resolve the data directory from the current Path-based WebUI contract."""
    return Path(getattr(manager, "data_path", manager))


def _plugins_dir(manager: Any) -> Path:
    """获取共享 plugins/ 目录，并兼容旧的 data/plugins 布局。"""
    data_dir = _data_dir(manager)
    candidates = [data_dir.parent / "plugins", data_dir / "plugins"]
    for plugins_dir in candidates:
        if plugins_dir.exists():
            return plugins_dir
    return candidates[0]


def _get_config_manager(manager: Any) -> Any:
    """获取配置管理器实例。"""
    return get_config_manager(_plugins_dir(manager))


def _iter_persona_dirs(manager: Any) -> list[Path]:
    """返回当前 workspace 中所有可重载的 Persona 目录。"""
    data_dir = _data_dir(manager)
    personas_dir = data_dir / "personas"
    if personas_dir.is_dir():
        return [
            path
            for path in sorted(personas_dir.iterdir())
            if path.is_dir() and (path / "persona.json").is_file()
        ]
    if (data_dir / "persona.json").is_file():
        return [data_dir]
    return []


def _request_plugin_reload(manager: Any) -> None:
    """通知所有 Persona Worker 重建 Plugin runtime。

    当前 Worker 已支持 ``all`` 重建标志，因此 Plugin 配置变化使用该
    兼容协议，确保旧 Worker 也能正确回收旧插件实例和任务。
    """
    for persona_dir in _iter_persona_dirs(manager):
        try:
            flag = persona_dir / "engine_state" / "reload_requested"
            flag.parent.mkdir(parents=True, exist_ok=True)
            reload_types: set[str] = set()
            if flag.exists():
                raw = flag.read_text(encoding="utf-8").strip()
                try:
                    payload = json.loads(raw)
                    if isinstance(payload, dict):
                        reload_types.update(str(item) for item in payload.get("types", []))
                    elif isinstance(payload, list):
                        reload_types.update(str(item) for item in payload)
                    elif raw:
                        reload_types.add(raw)
                except Exception:
                    if raw:
                        reload_types.add(raw)
            reload_types.add("all")
            payload = (
                next(iter(reload_types))
                if len(reload_types) == 1
                else json.dumps({"types": sorted(reload_types)}, ensure_ascii=False)
            )
            flag.write_text(payload, encoding="utf-8")
        except Exception as exc:
            LOG.warning("写入 Plugin 重载标志失败 (%s): %s", persona_dir, exc)


def _invalidate_plugin_cache(plugins_dir: Path) -> None:
    """清除指定插件目录的定义缓存。"""
    _plugin_definitions_cache.pop(str(plugins_dir), None)


def _load_definitions_cached(plugins_dir: Path) -> list[PluginDefinition]:
    """Load cached metadata without allowing request-driven dependency installs."""
    key = str(plugins_dir)
    now = time.monotonic()
    cached = _plugin_definitions_cache.get(key)
    if cached is not None:
        ts, definitions = cached
        if now - ts < _CACHE_TTL:
            return definitions
    loader = PluginLoader(plugins_dir)
    definitions = loader.load_all_definitions(metadata_only=True)
    _plugin_definitions_cache[key] = (now, definitions)
    return definitions


# ═══════════════════════════════════════════════════════════════════════
# API: 插件列表
# ═══════════════════════════════════════════════════════════════════════


def _plugin_admin_required(request: web.Request) -> web.Response | None:
    """Keep plugin scanning/importing off the read-only viewer request path."""
    if isinstance(request, web.Request) and request.get("auth_role") != "admin":
        return _json_response({"error": "权限不足，需要管理员权限"}, 403)
    return None


@handle_api_errors
async def api_plugins_get(request: web.Request, manager: Any) -> web.Response:
    """GET /api/plugins — 列出所有插件及其元数据。"""
    denied = _plugin_admin_required(request)
    if denied is not None:
        return denied
    plugins_dir = _plugins_dir(manager)
    if not plugins_dir.exists():
        return _json_response({"plugins": []})

    definitions = _load_definitions_cached(plugins_dir)
    config_manager = _get_config_manager(manager)

    plugins: list[dict[str, Any]] = []
    for d in definitions:
        plugin_config = config_manager.get_config(d.name)
        source_file = _find_source_file(d.source_path) if d.source_path else None

        plugins.append(
            {
                "name": d.name,
                "display_name": d.display_name or d.name,
                "description": d.description,
                "version": d.version,
                "author": d.author,
                "enabled": plugin_config["enabled"],
                "prompt_inject": d.prompt_inject or "",
                "permissions": {
                    "hidden_from_intent": d.permissions.hidden_from_intent,
                },
                "commands": [
                    {
                        "name": c.name,
                        "patterns": c.patterns,
                        "pattern_type": c.pattern_type,
                        "description": c.description,
                        "hidden_from_intent": c.hidden_from_intent,
                    }
                    for c in d.commands
                ],
                "events": [
                    {
                        "type": e.type,
                        "cron": e.cron,
                        "description": e.description,
                    }
                    for e in d.events
                ],
                "parameters": [_masked_parameter(p) for p in d.parameters],
                "nl_examples": d.natural_language.examples if d.natural_language else [],
                "source_file": source_file,
                "has_source": source_file is not None,
                "settings": _masked_settings(plugin_config["settings"], d),
            }
        )

    return _json_response({"plugins": plugins})


def _find_source_file(plugin_path: Path | None) -> str | None:
    """查找插件目录下的主 .py 文件，返回文件名。"""
    if plugin_path is None or not plugin_path.exists():
        return None
    py_files = sorted(plugin_path.glob("*.py"), key=lambda p: (p.name != "__init__.py", p.name))
    for pf in py_files:
        if not pf.name.startswith("_"):
            return pf.name
    return None


# ═══════════════════════════════════════════════════════════════════════
# API: 插件详情（含源码）
# ═══════════════════════════════════════════════════════════════════════


async def api_plugin_detail_get(request: web.Request, manager: Any) -> web.Response:
    """GET /api/plugins/{plugin_name} — 获取插件详情，含源码内容。"""
    denied = _plugin_admin_required(request)
    if denied is not None:
        return denied
    plugin_name = str(request.match_info.get("plugin_name", "")).strip()
    if not plugin_name:
        return _json_response({"error": "缺少 plugin_name"}, 400)

    plugins_dir = _plugins_dir(manager)
    config_manager = _get_config_manager(manager)
    config_manager.reload()  # 热重载，确保读取最新的磁盘配置

    definitions = _load_definitions_cached(plugins_dir)
    definition = next((d for d in definitions if d.name == plugin_name), None)
    if definition is None:
        return _json_response({"error": f"插件 {plugin_name} 不存在"}, 404)

    plugin_config = config_manager.get_config(plugin_name)
    effective_permissions = _effective_permissions(
        definition,
        plugin_config.get("permissions", {}),
    )
    source_file = _find_source_file(definition.source_path)
    auth_role = request.get("auth_role", "") if hasattr(request, "get") else ""
    source_content = ""

    # Plugin source can contain implementation details or accidentally embedded
    # secrets. Only the authenticated admin role may request it; viewers still
    # receive metadata and configuration needed by the read-only UI.
    if auth_role == "admin" and source_file and definition.source_path:
        try:
            plugins_root = plugins_dir.resolve()
            source_path = (definition.source_path / source_file).resolve(strict=True)
            source_path.relative_to(plugins_root)
            if source_path.is_file():
                source_content = source_path.read_text(encoding="utf-8")
        except ValueError:
            LOG.warning("拒绝读取 plugins 根目录外的源码: %s", definition.source_path)
        except Exception as exc:
            LOG.warning("读取源码失败 %s: %s", definition.source_path, exc)

    return _json_response(
        {
            "name": definition.name,
            "display_name": definition.display_name or definition.name,
            "description": definition.description,
            "version": definition.version,
            "author": definition.author,
            "prompt_inject": definition.prompt_inject or "",
            "hidden_from_intent": definition.permissions.hidden_from_intent,
            "enabled": plugin_config["enabled"],
            "commands": [
                {
                    "name": c.name,
                    "patterns": c.patterns,
                    "pattern_type": c.pattern_type,
                    "description": c.description,
                    "examples": c.examples,
                    "hidden_from_intent": c.hidden_from_intent,
                }
                for c in definition.commands
            ],
            "events": [
                {
                    "type": e.type,
                    "cron": e.cron,
                    "description": e.description,
                }
                for e in definition.events
            ],
            "parameters": [_masked_parameter(p) for p in definition.parameters],
            "nl_examples": (
                definition.natural_language.examples if definition.natural_language else []
            ),
            "nl_slots": definition.natural_language.slots if definition.natural_language else {},
            "source_file": source_file,
            "source_content": source_content,
            "settings": _masked_settings(plugin_config["settings"], definition),
            "permissions": effective_permissions,
        }
    )


# ═══════════════════════════════════════════════════════════════════════
# API: 启用/禁用
# ═══════════════════════════════════════════════════════════════════════


@handle_api_errors
async def api_plugin_toggle(request: web.Request, manager: Any) -> web.Response:
    """POST /api/plugins/{plugin_name}/toggle — 启用/禁用插件。"""
    plugin_name = str(request.match_info.get("plugin_name", "")).strip()
    if not plugin_name:
        return _json_response({"error": "缺少 plugin_name"}, 400)

    try:
        body = await request.json()
    except Exception:
        return _json_response({"error": "Invalid JSON"}, 400)
    if not isinstance(body, dict) or type(body.get("enabled")) is not bool:
        return _json_response({"error": "enabled 必须是布尔值"}, 400)
    definition = _definition_for(manager, plugin_name)
    if definition is None:
        return _json_response({"error": f"插件 {plugin_name} 不存在"}, 404)
    enabled = body["enabled"]

    config_manager = _get_config_manager(manager)
    config_manager.set_enabled(plugin_name, enabled)
    # Plugin 的启停会影响命令索引、后台任务和 Webhook，通知人格 Worker
    # 重建运行时，而不是只修改 WebUI 上的配置文件。
    _request_plugin_reload(manager)
    LOG.info("插件 %s enabled=%s，已请求运行时重载", plugin_name, enabled)
    return _json_response({"success": True, "plugin": plugin_name, "enabled": enabled})


# ═══════════════════════════════════════════════════════════════════════
# API: 插件权限配置
# ═══════════════════════════════════════════════════════════════════════


async def api_plugin_config_get(request: web.Request, manager: Any) -> web.Response:
    """GET /api/plugins/{plugin_name}/config — 获取插件权限配置。"""
    denied = _plugin_admin_required(request)
    if denied is not None:
        return denied
    plugin_name = str(request.match_info.get("plugin_name", "")).strip()
    if not plugin_name:
        return _json_response({"error": "缺少 plugin_name"}, 400)

    definition = _definition_for(manager, plugin_name)
    if definition is None:
        return _json_response({"error": f"插件 {plugin_name} 不存在"}, 404)
    config_manager = _get_config_manager(manager)
    permissions = _effective_permissions(
        definition,
        config_manager.get_permissions(plugin_name),
    )

    return _json_response({"plugin": plugin_name, **permissions})


async def api_plugin_config_post(request: web.Request, manager: Any) -> web.Response:
    """PUT /api/plugins/{plugin_name}/config — 保存插件权限配置。"""
    plugin_name = str(request.match_info.get("plugin_name", "")).strip()
    if not plugin_name:
        return _json_response({"error": "缺少 plugin_name"}, 400)

    try:
        body = await request.json()
    except Exception:
        return _json_response({"error": "Invalid JSON"}, 400)

    definition = _definition_for(manager, plugin_name)
    if definition is None:
        return _json_response({"error": f"插件 {plugin_name} 不存在"}, 404)
    if not isinstance(body, dict):
        return _json_response({"error": "配置必须是对象"}, 400)
    allowed_permission_keys = {
        "group_blacklist",
        "developer_only",
        "hidden_from_intent",
        "rate_limit_calls_per_minute",
    }
    unknown_keys = set(body) - allowed_permission_keys
    if unknown_keys:
        return _json_response({"error": f"未知权限配置：{sorted(unknown_keys)[0]}"}, 400)

    permissions: dict[str, Any] = {}
    if "group_blacklist" in body:
        blacklist = body["group_blacklist"]
        if not isinstance(blacklist, list) or not all(isinstance(item, str) for item in blacklist):
            return _json_response({"error": "group_blacklist 必须是字符串列表"}, 400)
        permissions["group_blacklist"] = list(
            dict.fromkeys(item.strip() for item in blacklist if item.strip())
        )

    for key in ("developer_only", "hidden_from_intent"):
        if key not in body:
            continue
        if type(body[key]) is not bool:
            return _json_response({"error": f"{key} 必须是布尔值"}, 400)
        if key == "developer_only" and definition.permissions.developer_only and not body[key]:
            return _json_response({"error": "该插件的 developer_only 由清单强制启用，不能关闭"}, 400)
        if (
            key == "hidden_from_intent"
            and definition.permissions.hidden_from_intent
            and not body[key]
        ):
            return _json_response(
                {"error": "该插件的 hidden_from_intent 由清单强制启用，不能关闭"},
                400,
            )
        permissions[key] = body[key]

    if "rate_limit_calls_per_minute" in body:
        rate_limit = body["rate_limit_calls_per_minute"]
        if type(rate_limit) is not int or not 1 <= rate_limit <= 1000:
            return _json_response(
                {"error": "rate_limit_calls_per_minute 必须是 1 到 1000 之间的整数"},
                400,
            )
        permissions["rate_limit_calls_per_minute"] = rate_limit

    config_manager = _get_config_manager(manager)
    config_manager.update_permissions(plugin_name, permissions)
    _request_plugin_reload(manager)

    LOG.info("插件权限配置已保存: %s", plugin_name)
    return _json_response({"success": True, "plugin": plugin_name})


# ═══════════════════════════════════════════════════════════════════════
# API: 插件自定义配置（如 chat_analyzer 的时间配置）
# ═══════════════════════════════════════════════════════════════════════


async def api_plugin_settings_get(request: web.Request, manager: Any) -> web.Response:
    """GET /api/plugins/{plugin_name}/settings — 获取插件自定义配置。"""
    denied = _plugin_admin_required(request)
    if denied is not None:
        return denied
    plugin_name = str(request.match_info.get("plugin_name", "")).strip()
    if not plugin_name:
        return _json_response({"error": "缺少 plugin_name"}, 400)

    config_manager = _get_config_manager(manager)
    config_manager.reload()
    definition = _definition_for(manager, plugin_name)
    if definition is None:
        return _json_response({"error": f"插件 {plugin_name} 不存在"}, 404)
    settings = config_manager.get_settings(plugin_name)

    return _json_response(
        {
            "plugin": plugin_name,
            "settings": _masked_settings(settings, definition),
        }
    )


async def api_plugin_settings_post(request: web.Request, manager: Any) -> web.Response:
    """POST /api/plugins/{plugin_name}/settings — 更新插件自定义配置（完整覆盖）。"""
    plugin_name = str(request.match_info.get("plugin_name", "")).strip()
    if not plugin_name:
        return _json_response({"error": "缺少 plugin_name"}, 400)

    try:
        body = await request.json()
    except Exception:
        return _json_response({"error": "Invalid JSON"}, 400)

    if not isinstance(body, dict):
        return _json_response({"error": "请求体必须是对象"}, 400)
    settings = body.get("settings", {})
    if not isinstance(settings, dict):
        return _json_response({"error": "settings 必须是对象"}, 400)

    config_manager = _get_config_manager(manager)
    config_manager.reload()
    definition = _definition_for(manager, plugin_name)
    if definition is None:
        return _json_response({"error": f"插件 {plugin_name} 不存在"}, 404)
    schema_error = _validate_settings_schema(settings, definition)
    if schema_error:
        return _json_response({"error": schema_error}, 400)
    if _contains_plaintext_secret(settings, definition=definition):
        return _json_response(
            {"error": "敏感配置必须通过环境变量或受支持的 Secret 管理器提供"},
            400,
        )
    existing_settings = config_manager.get_settings(plugin_name)
    try:
        safe_settings = _settings_update_without_masked_secrets(
            settings,
            definition,
            existing=existing_settings,
        )
    except MaskedSecretUpdateError as exc:
        return _json_response({"error": str(exc)}, 400)
    config_manager.replace_settings(plugin_name, safe_settings)
    _request_plugin_reload(manager)

    LOG.info("插件自定义配置已保存: %s", plugin_name)
    return _json_response(
        {
            "success": True,
            "plugin": plugin_name,
            "settings": _masked_settings(config_manager.get_settings(plugin_name), definition),
        }
    )


async def api_plugin_setting_post(request: web.Request, manager: Any) -> web.Response:
    """POST /api/plugins/{plugin_name}/settings/{key} — 设置单个配置项。"""
    plugin_name = str(request.match_info.get("plugin_name", "")).strip()
    key = str(request.match_info.get("key", "")).strip()

    if not plugin_name:
        return _json_response({"error": "缺少 plugin_name"}, 400)
    if not key:
        return _json_response({"error": "缺少 key"}, 400)

    try:
        body = await request.json()
    except Exception:
        return _json_response({"error": "Invalid JSON"}, 400)

    if not isinstance(body, dict) or "value" not in body:
        return _json_response({"error": "缺少 value"}, 400)
    value = body["value"]

    config_manager = _get_config_manager(manager)
    config_manager.reload()
    definition = _definition_for(manager, plugin_name)
    if definition is None:
        return _json_response({"error": f"插件 {plugin_name} 不存在"}, 404)
    parameter = _parameter_for(definition, key)
    if parameter is None:
        return _json_response({"error": f"未声明的插件设置：{key}"}, 400)
    schema_error = _validate_parameter_value(value, parameter, path=key)
    if schema_error:
        return _json_response({"error": schema_error}, 400)
    if _contains_plaintext_secret(value, key=key, definition=definition):
        return _json_response(
            {"error": "敏感配置必须通过环境变量或受支持的 Secret 管理器提供"},
            400,
        )

    existing_settings = config_manager.get_settings(plugin_name)
    try:
        safe_settings = _settings_update_without_masked_secrets(
            {key: value},
            definition,
            existing=existing_settings,
        )
    except MaskedSecretUpdateError as exc:
        return _json_response({"error": str(exc)}, 400)
    config_manager.update_settings(plugin_name, safe_settings)
    _request_plugin_reload(manager)

    LOG.info("插件配置项已保存: %s.%s", plugin_name, key)
    stored_value = config_manager.get_settings(plugin_name).get(key)
    return _json_response(
        {
            "success": True,
            "plugin": plugin_name,
            "key": key,
            "value": _mask_secret_value(stored_value, key=key, definition=definition),
        }
    )


async def api_plugin_setting_delete(request: web.Request, manager: Any) -> web.Response:
    """DELETE /api/plugins/{plugin_name}/settings/{key} — 删除单个配置项。"""
    plugin_name = str(request.match_info.get("plugin_name", "")).strip()
    key = str(request.match_info.get("key", "")).strip()

    if not plugin_name:
        return _json_response({"error": "缺少 plugin_name"}, 400)
    if not key:
        return _json_response({"error": "缺少 key"}, 400)

    definition = _definition_for(manager, plugin_name)
    if definition is None:
        return _json_response({"error": f"插件 {plugin_name} 不存在"}, 404)
    if _parameter_for(definition, key) is None:
        return _json_response({"error": f"未声明的插件设置：{key}"}, 400)
    config_manager = _get_config_manager(manager)
    config_manager.delete_setting(plugin_name, key)
    _request_plugin_reload(manager)

    LOG.info("插件配置项已删除: %s.%s", plugin_name, key)
    return _json_response({"success": True, "plugin": plugin_name, "key": key})


# ═══════════════════════════════════════════════════════════════════════
# API: 刷新插件（重新加载）
# ═══════════════════════════════════════════════════════════════════════


@handle_api_errors
async def api_plugins_reload(request: web.Request, manager: Any) -> web.Response:
    """POST /api/plugins/reload — 刷新插件列表和配置（热重载）。"""
    plugins_dir = _plugins_dir(manager)
    if not plugins_dir.exists():
        return _json_response({"plugins": []})

    # 清除缓存，强制重新加载
    _invalidate_plugin_cache(plugins_dir)

    loader = PluginLoader(plugins_dir)
    definitions = loader.load_all_definitions(metadata_only=True)

    # 更新缓存
    _plugin_definitions_cache[str(plugins_dir)] = (time.monotonic(), definitions)

    config_manager = _get_config_manager(manager)
    config_manager.reload()
    _request_plugin_reload(manager)

    count = len(definitions)
    enabled_count = sum(1 for d in definitions if config_manager.get_enabled(d.name))
    LOG.info("插件刷新完成: %d 个 (启用 %d)", count, enabled_count)

    return _json_response(
        {
            "success": True,
            "total": count,
            "enabled": enabled_count,
        }
    )
