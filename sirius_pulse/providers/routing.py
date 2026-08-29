from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import httpx

from sirius_pulse.providers.aliyun_bailian import AliyunBailianProvider
from sirius_pulse.providers.base import (
    AsyncLLMProvider,
    GenerationRequest,
    GenerationResult,
    LLMProvider,
)
from sirius_pulse.providers.bigmodel import BigModelProvider
from sirius_pulse.providers.deepseek import DeepSeekProvider
from sirius_pulse.providers.mimo import MimoProvider, MimoTokenPlanProvider
from sirius_pulse.providers.models_dev import auto_fill_models_from_dev
from sirius_pulse.providers.openai_compatible import OpenAICompatibleProvider
from sirius_pulse.providers.opencode import (
    DEFAULT_OPENCODE_BASE_URL,
    DEFAULT_OPENCODE_GO_BASE_URL,
    OpenCodeGoProvider,
    OpenCodeProvider,
)
from sirius_pulse.providers.proxy import (
    httpx_proxy_kwargs,
    load_proxy_settings,
)
from sirius_pulse.providers.siliconflow import SiliconFlowProvider
from sirius_pulse.providers.volcengine_ark import VolcengineArkProvider
from sirius_pulse.providers.ytea import YTeaProvider
from sirius_pulse.utils.json_io import atomic_write_json
from sirius_pulse.utils.layout import WorkspaceLayout

PROVIDER_KEYS_FILE = "provider_keys.json"

_OPENAI_PROVIDER_TYPES = {"openai", "openai-compatible"}
_ALIYUN_BAILIAN_PROVIDER_TYPES = {"aliyun-bailian", "bailian", "dashscope"}
_BIGMODEL_PROVIDER_TYPES = {"bigmodel", "zhipu", "zhipuai"}
_DEEPSEEK_PROVIDER_TYPES = {"deepseek"}
_SILICONFLOW_PROVIDER_TYPES = {"siliconflow"}
_VOLCENGINE_ARK_PROVIDER_TYPES = {"volcengine-ark", "ark"}
_YTEA_PROVIDER_TYPES = {"ytea"}
_MIMO_PROVIDER_TYPES = {"mimo", "xiaomi-mimo"}
_MIMO_TOKENPLAN_PROVIDER_TYPES = {"mimo-tokenplan", "xiaomi-mimo-tokenplan"}
_OPENCODE_PROVIDER_TYPES = {"opencode", "opencode-zen"}
_OPENCODE_GO_PROVIDER_TYPES = {"opencode-go", "opencode_go", "opencodego"}

_SUPPORTED_PROVIDER_PLATFORMS: dict[str, dict[str, str]] = {
    "openai-compatible": {
        "default_base_url": "https://api.openai.com",
        "notes": "OpenAI-compatible chat completions endpoint",
    },
    "aliyun-bailian": {
        "default_base_url": "https://dashscope.aliyuncs.com/compatible-mode",
        "notes": "Aliyun Bailian DashScope OpenAI-compatible endpoint",
    },
    "bigmodel": {
        "default_base_url": "https://open.bigmodel.cn/api/paas/v4",
        "notes": "BigModel GLM chat completions endpoint",
    },
    "deepseek": {
        "default_base_url": "https://api.deepseek.com",
        "notes": "DeepSeek chat completions endpoint (OpenAI-compatible format)",
    },
    "mimo": {
        "default_base_url": "https://api.xiaomimimo.com/v1",
        "notes": "小米MIMO平台按量付费API（OpenAI兼容协议），API Key格式：sk-xxxxx",
    },
    "mimo-tokenplan": {
        "default_base_url": "https://token-plan-cn.xiaomimimo.com/v1",
        "notes": "小米MIMO Token Plan订阅制API（OpenAI兼容协议），API Key格式：tp-xxxxx",
    },
    "siliconflow": {
        "default_base_url": "https://api.siliconflow.cn",
        "notes": "SiliconFlow OpenAI-compatible endpoint",
    },
    "volcengine-ark": {
        "default_base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "notes": "Volcengine Ark chat completions endpoint",
    },
    "ytea": {
        "default_base_url": "https://api.ytea.top",
        "notes": "YTea OpenAI-compatible endpoint",
    },
    "opencode": {
        "default_base_url": "https://opencode.ai/zen/v1",
        "notes": "OpenCode Zen 网关 OpenAI 兼容端点（按量计费），API Key 在 https://opencode.ai/auth 获取",
    },
    "opencode-go": {
        "default_base_url": "https://opencode.ai/zen/go/v1",
        "notes": "OpenCode GO 订阅 OpenAI 兼容端点（需在 Zen 控制台订阅 GO），与 Zen 共用 API Key",
    },
}

_PROVIDER_HEALTHCHECK_USER_MESSAGE = "?"

logger = logging.getLogger(__name__)


def get_supported_provider_platforms() -> dict[str, dict[str, str]]:
    return dict(_SUPPORTED_PROVIDER_PLATFORMS)


@dataclass(slots=True)
class ProviderConfig:
    provider_type: str
    api_key: str
    base_url: str
    healthcheck_model: str = ""
    enabled: bool = True
    models: list[str] = field(default_factory=list)
    models_url: str = ""
    # 用户可见的唯一 Provider 标识；不能包含 '/'，因为模型路由使用 name/model。
    name: str = ""


def normalize_provider_type(provider_type: str) -> str:
    normalized = provider_type.strip().lower()
    if normalized == "openai":
        return "openai-compatible"
    if normalized == "ark":
        return "volcengine-ark"
    if normalized in {"bailian", "dashscope"}:
        return "aliyun-bailian"
    if normalized in {"zhipu", "zhipuai"}:
        return "bigmodel"
    if normalized == "xiaomi-mimo":
        return "mimo"
    if normalized == "xiaomi-mimo-tokenplan":
        return "mimo-tokenplan"
    if normalized == "opencode-zen":
        return "opencode"
    if normalized in {"opencode_go", "opencodego"}:
        return "opencode-go"
    return normalized


def normalize_provider_name(name: str) -> str:
    """Normalize and validate the user-visible Provider identifier.

    Provider names are also used as the scope in ``provider/model`` model
    references, so a slash would make the reference ambiguous.  Keep the
    validation in one place so the registry, WebUI and runtime enforce the
    same contract.
    """
    normalized = str(name or "").strip()
    if not normalized:
        raise ValueError("Provider 名称不能为空")
    if "/" in normalized or "\\" in normalized:
        raise ValueError("Provider 名称不能包含 '/' 或 '\\\\'")
    if any(ord(char) < 32 for char in normalized):
        raise ValueError("Provider 名称不能包含控制字符")
    if len(normalized) > 100:
        raise ValueError("Provider 名称不能超过 100 个字符")
    return normalized


def _provider_name_key(name: str) -> str:
    return name.casefold()


def _next_provider_name(base_name: str, used_names: set[str]) -> str:
    """Return a unique migration name without changing the first name."""
    try:
        base = normalize_provider_name(base_name)
    except ValueError:
        base = "provider"
    if _provider_name_key(base) not in used_names:
        return base
    index = 2
    while _provider_name_key(f"{base}-{index}") in used_names:
        index += 1
    return f"{base}-{index}"


def ensure_provider_platform_supported(provider_type: str) -> str:
    normalized = normalize_provider_type(provider_type)
    if normalized not in _SUPPORTED_PROVIDER_PLATFORMS:
        raise RuntimeError(f"provider 平台未适配：{provider_type}")
    return normalized


class ProviderRegistry:
    """Store provider credentials and routing hints under work_path."""

    def __init__(self, work_path: Path | WorkspaceLayout) -> None:
        self._layout = (
            work_path if isinstance(work_path, WorkspaceLayout) else WorkspaceLayout(work_path)
        )
        self.path = self._layout.provider_registry_path()
        # 同步磁盘上的全局代理配置到进程级存储，
        # 使本进程后续的 provider HTTP 调用（含子进程人格运行时）走同一代理。
        load_proxy_settings(self._layout.config_root)

    @property
    def work_path(self) -> Path:
        return self._layout.config_root

    def load(self) -> dict[str, ProviderConfig]:
        if not self.path.exists():
            return {}

        raw = json.loads(self.path.read_text(encoding="utf-8"))
        providers = raw.get("providers", {}) if isinstance(raw, dict) else {}
        if isinstance(providers, dict):
            entries = [(str(key), payload) for key, payload in providers.items()]
        elif isinstance(providers, list):
            entries = [
                (str(payload.get("name", "")), payload)
                for payload in providers
                if isinstance(payload, dict)
            ]
        else:
            entries = []

        results: dict[str, ProviderConfig] = {}
        used_names: set[str] = set()
        needs_migration = not isinstance(providers, dict)
        needs_model_migration = False
        for source_name, payload in entries:
            if not isinstance(payload, dict):
                continue
            provider_type = normalize_provider_type(
                str(payload.get("type") or payload.get("platform_type") or source_name)
            )
            api_key = str(payload.get("api_key", "")).strip()
            if not api_key:
                continue

            # Entries without a name are old registry data.  The old mapping
            # key is the best stable name; duplicate type keys are retained.
            requested_name = str(payload.get("name") or source_name or provider_type).strip()
            provider_name = _next_provider_name(requested_name, used_names)
            used_names.add(_provider_name_key(provider_name))
            if (
                not payload.get("name")
                or provider_name != requested_name
                or provider_name != source_name
            ):
                needs_migration = True
            if "models" not in payload:
                needs_model_migration = True

            base_url = str(payload.get("base_url", "")).strip()
            healthcheck_model = str(payload.get("healthcheck_model", "")).strip()
            enabled = bool(payload.get("enabled", True))
            models_url = str(payload.get("models_url", "")).strip()
            models_raw = payload.get("models", [])
            models = (
                [str(m).strip() for m in models_raw if str(m).strip()]
                if isinstance(models_raw, list)
                else []
            )
            results[provider_name] = ProviderConfig(
                provider_type=provider_type,
                api_key=api_key,
                base_url=base_url,
                healthcheck_model=healthcheck_model,
                enabled=enabled,
                models=models,
                models_url=models_url,
                name=provider_name,
            )

        if needs_model_migration and not isinstance(providers, list):
            # 保留字典格式旧配置的模型自动填充行为；旧列表格式只做
            # 名称迁移，避免打开 WebUI 时意外改变用户的模型配置。
            auto_fill_models_from_dev(self._layout.config_root, results)
        if needs_migration or needs_model_migration:
            # 将唯一 name 写回，保证下一次启动无需猜测身份。
            self.save(results)

        return results

    def save(self, providers: dict[str, ProviderConfig]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        providers_payload: dict[str, dict[str, object]] = {}
        used_names: set[str] = set()
        for source_name, config in providers.items():
            requested_name = config.name.strip() or str(source_name).strip() or config.provider_type
            provider_name = normalize_provider_name(requested_name)
            name_key = _provider_name_key(provider_name)
            if name_key in used_names:
                raise ValueError(f"Provider 名称重复：{provider_name}")
            used_names.add(name_key)
            config.name = provider_name
            config.provider_type = normalize_provider_type(config.provider_type)
            entry: dict[str, object] = {
                "name": provider_name,
                "type": config.provider_type,
                "api_key": config.api_key,
                "base_url": config.base_url,
                "healthcheck_model": config.healthcheck_model,
                "enabled": config.enabled,
                "models": config.models,
            }
            if config.models_url:
                entry["models_url"] = config.models_url
            providers_payload[provider_name] = entry
        payload: dict[str, object] = {"providers": providers_payload}
        atomic_write_json(self.path, payload)

    def upsert(
        self,
        *,
        provider_type: str,
        api_key: str,
        base_url: str = "",
        healthcheck_model: str = "",
        models: list[str] | None = None,
        name: str = "",
    ) -> str:
        provider_key = normalize_provider_type(provider_type)
        requested_name = normalize_provider_name(name or provider_key)
        providers = self.load()
        existing_key = next(
            (
                key
                for key, value in providers.items()
                if _provider_name_key(key) == _provider_name_key(requested_name)
            ),
            None,
        )
        if existing_key is not None and existing_key != requested_name:
            providers.pop(existing_key)
        elif not name:
            # Legacy callers used provider type as the update key.
            existing_key = next(
                (key for key, value in providers.items() if value.provider_type == provider_key),
                None,
            )
            if existing_key is not None:
                requested_name = existing_key
                providers.pop(existing_key)
        providers[requested_name] = ProviderConfig(
            provider_type=provider_key,
            api_key=api_key.strip(),
            base_url=base_url.strip(),
            healthcheck_model=healthcheck_model.strip(),
            enabled=True,
            models=models or [],
            name=requested_name,
        )
        self.save(providers)
        return requested_name

    def remove(self, provider_name: str) -> bool:
        providers = self.load()
        key = next(
            (
                key
                for key in providers
                if _provider_name_key(key) == _provider_name_key(provider_name.strip())
            ),
            None,
        )
        if key is None:
            # Backward compatibility for callers that still pass a platform type.
            provider_key = normalize_provider_type(provider_name)
            matching = [
                key for key, value in providers.items() if value.provider_type == provider_key
            ]
            if len(matching) != 1:
                return False
            key = matching[0]
        providers.pop(key)
        self.save(providers)
        return True


class WorkspaceProviderManager:
    """Workspace-scoped provider registry facade."""

    def __init__(self, work_path: Path | WorkspaceLayout) -> None:
        self._layout = (
            work_path if isinstance(work_path, WorkspaceLayout) else WorkspaceLayout(work_path)
        )
        self._registry = ProviderRegistry(self._layout)

    @property
    def path(self) -> Path:
        return self._registry.path

    def load(self) -> dict[str, ProviderConfig]:
        return self._registry.load()

    def save(self, providers: dict[str, ProviderConfig]) -> None:
        self._registry.save(providers)

    def merge_entries(self, providers_config: list[dict[str, object]]) -> dict[str, ProviderConfig]:
        providers = self.load()
        existing_names_by_type: dict[str, list[str]] = {}
        for existing_name, existing_config in providers.items():
            existing_names_by_type.setdefault(existing_config.provider_type, []).append(
                existing_name
            )
        unnamed_offsets: dict[str, int] = {}
        used_names = {_provider_name_key(name) for name in providers}

        for item in providers_config:
            provider_type = normalize_provider_type(
                str(item.get("type") or item.get("platform_type") or "")
            )
            requested_name = str(item.get("name", "")).strip()
            if requested_name:
                provider_name = normalize_provider_name(requested_name)
                existing_key = next(
                    (
                        key
                        for key in providers
                        if _provider_name_key(key) == _provider_name_key(provider_name)
                    ),
                    None,
                )
                existing = providers.get(existing_key) if existing_key is not None else None
            else:
                # Pair unnamed legacy entries with existing providers by order;
                # overflow entries receive deterministic ``type-N`` names.
                offset = unnamed_offsets.get(provider_type, 0)
                unnamed_offsets[provider_type] = offset + 1
                existing_names = existing_names_by_type.get(provider_type, [])
                existing_key = existing_names[offset] if offset < len(existing_names) else None
                if existing_key is not None:
                    provider_name = existing_key
                    existing = providers[existing_key]
                else:
                    provider_name = _next_provider_name(provider_type, used_names)
                    existing = None
            used_names.add(_provider_name_key(provider_name))
            api_key = str(item.get("api_key", "")).strip()
            if not api_key and existing is not None:
                api_key = existing.api_key
            if not provider_type or not api_key:
                continue

            if "base_url" in item:
                base_url = str(item.get("base_url", "")).strip()
            else:
                base_url = existing.base_url if existing is not None else ""

            if "healthcheck_model" in item:
                healthcheck_model = str(item.get("healthcheck_model", "")).strip()
            else:
                healthcheck_model = existing.healthcheck_model if existing is not None else ""

            if "enabled" in item:
                enabled = bool(item.get("enabled", True))
            else:
                enabled = existing.enabled if existing is not None else True

            if "models" in item:
                models_raw = item.get("models", [])
                models = (
                    [str(model).strip() for model in models_raw if str(model).strip()]
                    if isinstance(models_raw, list)
                    else []
                )
            else:
                models = list(existing.models) if existing is not None else []

            if existing is not None and existing.name != provider_name:
                providers.pop(existing.name, None)
            if "models_url" in item:
                models_url = str(item.get("models_url", "")).strip()
            else:
                models_url = existing.models_url if existing is not None else ""

            providers[provider_name] = ProviderConfig(
                provider_type=provider_type,
                api_key=api_key,
                base_url=base_url,
                healthcheck_model=healthcheck_model,
                enabled=enabled,
                models=models,
                models_url=models_url,
                name=provider_name,
            )
        return providers

    def save_from_entries(
        self, providers_config: list[dict[str, object]]
    ) -> dict[str, ProviderConfig]:
        providers = self.merge_entries(providers_config)
        self.save(providers)
        return providers

    def register(
        self,
        *,
        provider_type: str,
        api_key: str,
        base_url: str = "",
        healthcheck_model: str = "",
        models: list[str] | None = None,
        name: str = "",
    ) -> str:
        return self._registry.upsert(
            provider_type=provider_type,
            api_key=api_key,
            base_url=base_url,
            healthcheck_model=healthcheck_model,
            models=models,
            name=name,
        )

    def remove(self, provider_type: str) -> bool:
        return self._registry.remove(provider_type)

    async def probe(self) -> None:
        await run_provider_detection_flow(providers=self.load())

    def refresh_models_from_dev(self, *, tool_call_only: bool = True, force: bool = False) -> bool:
        """从 models.dev 刷新所有 provider 的模型列表。

        Args:
            tool_call_only: 是否只填充支持 tool_call 的模型
            force: True 时忽略已有列表强制刷新，False 时仅填充空列表

        Returns:
            是否有任何变更
        """
        from sirius_pulse.providers.models_dev import ModelsDevCache

        providers = self.load()
        cache = ModelsDevCache(self._layout.config_root)
        data = cache.get(force_refresh=force)
        if not data:
            logger.warning("无法获取 models.dev 数据，跳过刷新")
            return False

        from sirius_pulse.providers.models_dev import list_provider_model_ids

        changed = False
        for provider_name, config in providers.items():
            if config.models and not force:
                continue
            # models.dev 使用平台类型，而 registry key 是唯一的用户名称。
            dev_model_ids = list_provider_model_ids(
                data,
                config.provider_type,
                tool_call_only=tool_call_only,
            )
            if dev_model_ids:
                # 保留用户原有的模型，合并 models.dev 的模型
                original_models = set(config.models or [])
                dev_models = set(dev_model_ids)
                # 合并：保留用户原有的所有模型 + models.dev 的新模型
                merged_models = list(original_models | dev_models)

                if merged_models != config.models:
                    config.models = merged_models
                    changed = True
                    logger.info(
                        "刷新 %s 模型列表: %d 个模型（保留 %d 个原有模型）",
                        provider_name,
                        len(merged_models),
                        len(original_models),
                    )

        if changed:
            self.save(providers)
        return changed


def merge_provider_sources(
    *,
    work_path: Path,
    providers_config: list[dict[str, object]],
) -> dict[str, ProviderConfig]:
    """Merge providers from multiple sources with priority order.

    Priority (high to low):
    1. Session JSON: providers field
    2. Persistent: <work_path>/provider_keys.json
    """
    return WorkspaceProviderManager(work_path).merge_entries(providers_config)


def _create_provider_instance(config: ProviderConfig) -> Any:
    provider_type = config.provider_type
    if provider_type in _ALIYUN_BAILIAN_PROVIDER_TYPES:
        return AliyunBailianProvider(
            api_key=config.api_key,
            base_url=config.base_url or "https://dashscope.aliyuncs.com/compatible-mode",
        )
    if provider_type in _BIGMODEL_PROVIDER_TYPES:
        return BigModelProvider(
            api_key=config.api_key,
            base_url=config.base_url or "https://open.bigmodel.cn/api/paas/v4",
        )
    if provider_type in _SILICONFLOW_PROVIDER_TYPES:
        return SiliconFlowProvider(api_key=config.api_key)
    if provider_type in _DEEPSEEK_PROVIDER_TYPES:
        return DeepSeekProvider(api_key=config.api_key)
    if provider_type in _VOLCENGINE_ARK_PROVIDER_TYPES:
        return VolcengineArkProvider(api_key=config.api_key)
    if provider_type in _YTEA_PROVIDER_TYPES:
        return YTeaProvider(api_key=config.api_key)
    if provider_type in _MIMO_PROVIDER_TYPES:
        return MimoProvider(
            api_key=config.api_key, base_url=config.base_url or "https://api.xiaomimimo.com/v1"
        )
    if provider_type in _MIMO_TOKENPLAN_PROVIDER_TYPES:
        return MimoTokenPlanProvider(
            api_key=config.api_key,
            base_url=config.base_url or "https://token-plan-cn.xiaomimimo.com/v1",
        )
    if provider_type in _OPENCODE_PROVIDER_TYPES:
        return OpenCodeProvider(
            api_key=config.api_key,
            base_url=config.base_url or DEFAULT_OPENCODE_BASE_URL,
        )
    if provider_type in _OPENCODE_GO_PROVIDER_TYPES:
        return OpenCodeGoProvider(
            api_key=config.api_key,
            base_url=config.base_url or DEFAULT_OPENCODE_GO_BASE_URL,
        )
    if provider_type in _OPENAI_PROVIDER_TYPES:
        return OpenAICompatibleProvider(
            api_key=config.api_key, base_url=config.base_url or "https://api.openai.com"
        )
    raise RuntimeError(f"不支持的提供商类型：{provider_type}")


class AutoRoutingProvider(AsyncLLMProvider):
    """Choose a configured provider automatically on each generation request.

    The routing key is the user-controlled unique Provider name, not the
    platform type.  This allows two credentials for the same endpoint/type to
    expose different model scopes such as ``openai-team-a/gpt-4o`` and
    ``openai-team-b/gpt-4o``.
    """

    def __init__(self, providers: dict[str, ProviderConfig]) -> None:
        self._providers: dict[str, ProviderConfig] = {}
        for key, value in providers.items():
            if not value.enabled:
                continue
            provider_name = value.name.strip() or str(key).strip() or value.provider_type
            provider_name = normalize_provider_name(provider_name)
            if _provider_name_key(provider_name) in {
                _provider_name_key(existing) for existing in self._providers
            }:
                raise ValueError(f"Provider 名称重复：{provider_name}")
            value.name = provider_name
            self._providers[provider_name] = value
        self._last_provider_name = "unknown"

    @staticmethod
    def _split_scoped_model(model: str) -> tuple[str, str] | None:
        provider_name, sep, model_name = model.strip().partition("/")
        if not sep or not provider_name.strip() or not model_name.strip():
            return None
        return provider_name.strip(), model_name.strip()

    @staticmethod
    def _model_match_source(provider: ProviderConfig, model: str) -> str:
        model_stripped = model.strip()
        if provider.models and model_stripped in provider.models:
            return "models"
        expected = provider.healthcheck_model.strip()
        if expected and model_stripped == expected:
            return "healthcheck_model"
        return ""

    def _provider_matches_model(self, provider: ProviderConfig, model: str) -> bool:
        return bool(self._model_match_source(provider, model))

    def _create_provider(self, config: ProviderConfig) -> LLMProvider:
        return _create_provider_instance(config)

    def _pick_provider(self, model: str) -> tuple[ProviderConfig, str, str]:
        if not self._providers:
            raise RuntimeError("未配置任何提供商，请先添加至少一个提供商 API Key。")

        scoped = self._split_scoped_model(model)
        if scoped is not None:
            provider_name, model_name = scoped
            provider = next(
                (
                    value
                    for key, value in self._providers.items()
                    if key.casefold() == provider_name.casefold()
                ),
                None,
            )
            if provider is None:
                # Existing configurations may still reference the platform
                # type.  Keep that compatibility only if it identifies one
                # provider; never silently choose between duplicate types.
                candidates = [
                    value
                    for value in self._providers.values()
                    if value.provider_type == normalize_provider_type(provider_name)
                    and self._model_match_source(value, model_name)
                ]
                if len(candidates) == 1:
                    provider = candidates[0]
                else:
                    raise RuntimeError(
                        f"无法为模型 '{model}' 找到 provider '{provider_name}'。"
                        "请检查该 provider 名称是否正确并已启用。"
                    )
            matched_by = self._model_match_source(provider, model_name)
            if not matched_by:
                raise RuntimeError(
                    f"模型 '{model_name}' 未配置在 provider '{provider.name}' "
                    "的 models 或 healthcheck_model 中。"
                )
            return provider, matched_by, model_name

        matches: list[tuple[ProviderConfig, str]] = []
        model_stripped = model.strip()
        for provider in self._providers.values():
            matched_by = self._model_match_source(provider, model_stripped)
            if matched_by:
                matches.append((provider, matched_by))

        if len(matches) == 1:
            provider, matched_by = matches[0]
            return provider, matched_by, model_stripped

        if len(matches) > 1:
            providers = ", ".join(provider.name for provider, _ in matches)
            raise RuntimeError(
                f"模型 '{model}' 同时存在于多个 provider: {providers}。" "请在配置中使用 'provider/model' 形式明确指定。"
            )

        raise RuntimeError(
            f"无法为模型 '{model}' 找到合适的提供商。" "请确保在 provider_keys.json 或配置中的 'models' 列表中包含了该模型。"
        )

    async def generate_async(
        self, request: GenerationRequest, return_reasoning: bool = False
    ) -> GenerationResult | tuple[str, GenerationResult]:
        selected, matched_by, routed_model = self._pick_provider(request.model)
        logger.debug(
            "[Provider路由] model=%s | routed_model=%s | purpose=%s | provider_type=%s | matched_by=%s | base_url=%s | healthcheck_model=%s | models=%s",
            request.model,
            routed_model,
            request.purpose,
            selected.provider_type,
            matched_by,
            selected.base_url or "(默认)",
            selected.healthcheck_model or "(未设置)",
            selected.models,
        )
        provider = self._create_provider(selected)
        # 记录用户可见的唯一名称，便于 token 统计/日志区分同平台的不同凭据。
        self._last_provider_name = selected.name or getattr(
            provider, "_provider_name", selected.provider_type
        )
        routed_request = replace(request, model=routed_model)
        return await provider.generate_async(  # type: ignore[attr-defined]
            routed_request,
            return_reasoning=return_reasoning,
        )


async def probe_provider_availability(
    *,
    provider: AsyncLLMProvider,
    model_name: str,
) -> None:
    """Run a minimal generation request to verify provider connectivity and credentials."""

    result = await provider.generate_async(
        GenerationRequest(
            model=model_name,
            system_prompt="",
            messages=[{"role": "user", "content": _PROVIDER_HEALTHCHECK_USER_MESSAGE}],
            temperature=0.0,
            max_tokens=1,
            purpose="provider_healthcheck",
        )
    )
    content = result.content if hasattr(result, "content") else str(result)  # type: ignore[union-attr]
    if not content or not content.strip():
        raise RuntimeError("提供商健康检查返回空内容")


def _create_provider_from_config(config: ProviderConfig) -> LLMProvider:
    provider_type = ensure_provider_platform_supported(config.provider_type)
    config = ProviderConfig(
        provider_type=provider_type,
        api_key=config.api_key,
        base_url=config.base_url,
        healthcheck_model=config.healthcheck_model,
        enabled=config.enabled,
        models=config.models,
        models_url=config.models_url,
        name=config.name,
    )
    return _create_provider_instance(config)


async def run_provider_detection_flow(
    *,
    providers: dict[str, ProviderConfig],
) -> None:
    """Framework-level provider checks.

    1) Ensure provider platform/API config exists.
    2) Ensure platform is supported by current framework.
    3) Ensure provider is available using the registered healthcheck model.
    """

    if not providers:
        raise RuntimeError("未检测到已配置 provider（需包含平台与 API Key）")

    for provider_name, config in providers.items():
        ensure_provider_platform_supported(config.provider_type)
        if not config.api_key.strip():
            raise RuntimeError(f"provider 缺少 API Key：{provider_name}")
        if not config.healthcheck_model.strip():
            raise RuntimeError(f"provider 缺少 healthcheck_model：{provider_name}")

        provider = _create_provider_from_config(config)
        await probe_provider_availability(provider=provider, model_name=config.healthcheck_model)  # type: ignore[arg-type]


async def register_provider_with_validation(
    *,
    work_path: Path,
    provider_type: str,
    api_key: str,
    healthcheck_model: str,
    base_url: str = "",
    name: str = "",
) -> str:
    """Register provider only after support and availability checks pass."""

    normalized_provider_type = ensure_provider_platform_supported(provider_type)
    provider_name = normalize_provider_name(name or normalized_provider_type)
    model_name = healthcheck_model.strip()
    if not model_name:
        raise RuntimeError("注册 provider 需要提供 healthcheck_model")
    if not api_key.strip():
        raise RuntimeError("注册 provider 需要提供 API Key")

    config = ProviderConfig(
        provider_type=normalized_provider_type,
        api_key=api_key.strip(),
        base_url=base_url.strip(),
        healthcheck_model=model_name,
        enabled=True,
        name=provider_name,
    )
    provider = _create_provider_from_config(config)
    await probe_provider_availability(provider=provider, model_name=model_name)  # type: ignore[arg-type]

    ProviderRegistry(work_path).upsert(
        provider_type=normalized_provider_type,
        api_key=config.api_key,
        base_url=config.base_url,
        healthcheck_model=config.healthcheck_model,
        name=provider_name,
    )
    return provider_name


# ──────────────────────────────────────────────────────────────────
# models 接口探测（WebUI Provider 模型列表自动填充）
# ──────────────────────────────────────────────────────────────────


def _candidate_models_urls(*, base_url: str, models_url: str = "") -> list[str]:
    """生成待尝试的 models 接口候选地址（有序去重）。

    优先使用显式 ``models_url``；未指定时按常见 OpenAI 兼容布局
    尝试 ``<base>/models`` 与 ``<base>/v1/models``。
    """
    base = base_url.strip().rstrip("/")
    candidates: list[str] = []
    for url in (models_url.strip(), f"{base}/models", f"{base}/v1/models"):
        url = url.strip().rstrip("/")
        if url and url not in candidates:
            candidates.append(url)
    return candidates


def _extract_model_ids_from_payload(payload: Any) -> list[str]:
    """从常见 /models 响应中解析模型 ID 列表。

    支持 OpenAI 风格（``data[].id``）、Ollama 风格（``models[].name``）
    以及顶层数组等变体。
    """
    model_ids: list[str] = []

    def collect(items: Any) -> None:
        if isinstance(items, str):
            if items.strip():
                model_ids.append(items.strip())
            return
        if isinstance(items, dict):
            model_ids.append(
                str(
                    items.get("id")
                    or items.get("name")
                    or items.get("model")
                    or items.get("slug")
                    or ""
                ).strip()
            )
            return
        if isinstance(items, list):
            for item in items:
                collect(item)

    if isinstance(payload, dict):
        for key in ("data", "models", "items"):
            if isinstance(payload.get(key), list):
                collect(payload[key])
                break
        return [m for m in model_ids if m]
    collect(payload)
    return [m for m in model_ids if m]


async def probe_provider_models(
    *,
    provider_type: str,
    api_key: str,
    base_url: str,
    models_url: str = "",
    timeout_seconds: float = 15.0,
) -> tuple[list[str], str]:
    """通过 Provider 的 models 接口探测可用模型列表。

    Returns:
        ``(model_ids, used_url)``：解析出的模型 ID 列表与最终成功
        的接口地址；所有候选地址都失败时抛出 ``RuntimeError``。
    """
    normalized_type = ensure_provider_platform_supported(provider_type)
    platform = _SUPPORTED_PROVIDER_PLATFORMS.get(normalized_type, {})
    resolved_base = base_url.strip() or str(platform.get("default_base_url", "")).strip()
    if not resolved_base:
        raise RuntimeError(f"provider 平台未配置默认 Base URL：{provider_type}")

    candidates = _candidate_models_urls(base_url=resolved_base, models_url=models_url)
    if not candidates:
        raise RuntimeError("无法确定 models 接口地址")

    headers = {"Accept": "application/json"}
    bearer_key = api_key.strip()
    if bearer_key:
        headers["Authorization"] = f"Bearer {bearer_key}"

    failures: list[str] = []
    async with httpx.AsyncClient(timeout=float(timeout_seconds), **httpx_proxy_kwargs()) as client:
        for url in candidates:
            try:
                response = await client.get(url, headers=headers)
            except httpx.HTTPError as exc:
                failures.append(f"{url}: {type(exc).__name__}")
                continue
            if response.status_code >= 400:
                failures.append(f"{url}: HTTP {response.status_code}")
                continue
            try:
                payload = response.json()
            except Exception:
                failures.append(f"{url}: 非 JSON 响应")
                continue
            model_ids = _extract_model_ids_from_payload(payload)
            if not model_ids:
                failures.append(f"{url}: 响应中无模型数据")
                continue
            logger.info("models 接口探测成功: %s（%d 个模型）", url, len(model_ids))
            return model_ids, url

    raise RuntimeError(f"models 接口探测失败：{'；'.join(failures)}")
