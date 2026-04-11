from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from sirius_chat.providers.base import GenerationRequest
from sirius_chat.providers.routing import (
    AutoRoutingProvider,
    ProviderConfig,
    ProviderRegistry,
    ensure_provider_platform_supported,
    get_supported_provider_platforms,
    merge_provider_sources,
    register_provider_with_validation,
    run_provider_detection_flow,
    probe_provider_availability,
)


def _request(model: str) -> GenerationRequest:
    return GenerationRequest(
        model=model,
        system_prompt="system",
        messages=[{"role": "user", "content": "hello"}],
    )


def test_provider_registry_supports_add_and_remove(tmp_path: Path) -> None:
    registry = ProviderRegistry(tmp_path)
    registry.upsert(provider_type="siliconflow", api_key="sf-key", healthcheck_model="Pro/zai-org/GLM-4.7")
    providers = registry.load()

    assert "siliconflow" in providers
    assert providers["siliconflow"].api_key == "sf-key"
    assert providers["siliconflow"].healthcheck_model == "Pro/zai-org/GLM-4.7"

    removed = registry.remove("siliconflow")
    assert removed is True
    assert registry.load() == {}


def test_auto_routing_provider_selects_provider_by_exact_model_name() -> None:
    routing = AutoRoutingProvider(
        {
            "siliconflow": ProviderConfig(
                provider_type="siliconflow",
                api_key="sf-key",
                base_url="",
                healthcheck_model="Pro/zai-org/GLM-4.7",
            ),
            "openai-compatible": ProviderConfig(
                provider_type="openai-compatible",
                api_key="openai-key",
                base_url="",
                healthcheck_model="gpt-4o-mini",
            ),
        }
    )

    with patch("sirius_chat.providers.routing.SiliconFlowProvider.generate", return_value="sf-ok") as sf_generate:
        output = routing.generate(_request("Pro/zai-org/GLM-4.7"))

    assert output == "sf-ok"
    assert sf_generate.call_count == 1


def test_auto_routing_provider_falls_back_to_first_enabled_provider() -> None:
    routing = AutoRoutingProvider(
        {
            "openai-compatible": ProviderConfig(
                provider_type="openai-compatible",
                api_key="openai-key",
                base_url="",
                healthcheck_model="",
            ),
        }
    )

    with patch("sirius_chat.providers.routing.OpenAICompatibleProvider.generate", return_value="openai-ok") as generate:
        output = routing.generate(_request("custom-model"))

    assert output == "openai-ok"
    assert generate.call_count == 1


def test_merge_provider_sources_uses_registry_and_config(tmp_path: Path) -> None:
    registry = ProviderRegistry(tmp_path)
    registry.upsert(provider_type="siliconflow", api_key="sf-registry-key", healthcheck_model="Pro/registry")

    merged = merge_provider_sources(
        work_path=tmp_path,
        providers_config=[
            {
                "type": "siliconflow",
                "api_key": "sf-config-key",
                "healthcheck_model": "Qwen/Qwen3",
            },
            {
                "type": "openai-compatible",
                "api_key": "openai-config-key",
                "base_url": "https://api.openai.com",
            }
        ],
    )

    assert merged["siliconflow"].api_key == "sf-config-key"
    assert merged["siliconflow"].healthcheck_model == "Qwen/Qwen3"
    assert merged["openai-compatible"].api_key == "openai-config-key"


def test_get_supported_provider_platforms_contains_core_platforms() -> None:
    platforms = get_supported_provider_platforms()

    assert "siliconflow" in platforms
    assert "deepseek" in platforms
    assert "openai-compatible" in platforms
    assert "newapi" in platforms
    assert "volcengine-ark" in platforms
    assert platforms["siliconflow"]["default_base_url"] == "https://api.siliconflow.cn"
    assert platforms["deepseek"]["default_base_url"] == "https://api.deepseek.com"
    assert platforms["newapi"]["default_base_url"] == "https://docs.newapi.pro"
    assert platforms["volcengine-ark"]["default_base_url"] == "https://ark.cn-beijing.volces.com/api/v3"


def test_auto_routing_provider_prefers_ark_for_doubao_model() -> None:
    routing = AutoRoutingProvider(
        {
            "volcengine-ark": ProviderConfig(
                provider_type="volcengine-ark",
                api_key="ark-key",
                base_url="",
                healthcheck_model="",
            ),
            "openai-compatible": ProviderConfig(
                provider_type="openai-compatible",
                api_key="openai-key",
                base_url="",
                healthcheck_model="",
            ),
        }
    )

    with patch("sirius_chat.providers.routing.VolcengineArkProvider.generate", return_value="ark-ok") as ark_generate:
        output = routing.generate(_request("doubao-seed-2-0-lite-260215"))

    assert output == "ark-ok"
    assert ark_generate.call_count == 1


def test_auto_routing_provider_prefers_deepseek_for_deepseek_model() -> None:
    routing = AutoRoutingProvider(
        {
            "deepseek": ProviderConfig(
                provider_type="deepseek",
                api_key="deepseek-key",
                base_url="",
                healthcheck_model="",
            ),
            "openai-compatible": ProviderConfig(
                provider_type="openai-compatible",
                api_key="openai-key",
                base_url="",
                healthcheck_model="",
            ),
        }
    )

    with patch("sirius_chat.providers.routing.DeepSeekProvider.generate", return_value="deepseek-ok") as ds_generate:
        output = routing.generate(_request("deepseek-chat"))

    assert output == "deepseek-ok"
    assert ds_generate.call_count == 1


def test_probe_provider_availability_passes_on_non_empty_response() -> None:
    class _FakeProvider:
        def generate(self, request: GenerationRequest) -> str:  # noqa: ANN001
            assert request.model == "mock-model"
            return "ok"

    probe_provider_availability(provider=_FakeProvider(), model_name="mock-model")


def test_probe_provider_availability_raises_on_empty_response() -> None:
    class _FakeProvider:
        def generate(self, request: GenerationRequest) -> str:  # noqa: ANN001
            assert request.model == "mock-model"
            return "   "

    try:
        probe_provider_availability(provider=_FakeProvider(), model_name="mock-model")
    except RuntimeError as exc:
        assert "空内容" in str(exc) or "empty content" in str(exc)
    else:
        raise AssertionError("expected RuntimeError for empty provider healthcheck response")


def test_run_provider_detection_flow_rejects_unsupported_platform() -> None:
    providers = {
        "custom-provider": ProviderConfig(
            provider_type="custom-provider",
            api_key="k",
            base_url="",
            healthcheck_model="mock-model",
        )
    }

    try:
        run_provider_detection_flow(providers=providers)
    except RuntimeError as exc:
        assert "未适配" in str(exc)
    else:
        raise AssertionError("expected unsupported platform error")


def test_run_provider_detection_flow_requires_healthcheck_model() -> None:
    providers = {
        "openai-compatible": ProviderConfig(
            provider_type="openai-compatible",
            api_key="k",
            base_url="",
            healthcheck_model="",
        )
    }

    try:
        run_provider_detection_flow(providers=providers)
    except RuntimeError as exc:
        assert "healthcheck_model" in str(exc)
    else:
        raise AssertionError("expected missing healthcheck_model error")


def test_register_provider_with_validation_persists_healthcheck_model(tmp_path: Path) -> None:
    with patch("sirius_chat.providers.routing.OpenAICompatibleProvider.generate", return_value="ok"):
        provider_type = register_provider_with_validation(
            work_path=tmp_path,
            provider_type="openai-compatible",
            api_key="test-key",
            healthcheck_model="gpt-4o-mini",
            base_url="https://api.openai.com",
        )

    assert provider_type == "openai-compatible"
    providers = ProviderRegistry(tmp_path).load()
    assert providers["openai-compatible"].healthcheck_model == "gpt-4o-mini"


def test_register_newapi_provider_with_validation_persists_healthcheck_model(tmp_path: Path) -> None:
    with patch("sirius_chat.providers.routing.NewAPIProvider.generate", return_value="ok"):
        provider_type = register_provider_with_validation(
            work_path=tmp_path,
            provider_type="newapi",
            api_key="test-key",
            healthcheck_model="gpt-4o-mini",
            base_url="https://docs.newapi.pro",
        )

    assert provider_type == "newapi"
    providers = ProviderRegistry(tmp_path).load()
    assert providers["newapi"].healthcheck_model == "gpt-4o-mini"
    assert providers["newapi"].base_url == ""


def test_ensure_provider_platform_supported_normalizes_alias() -> None:
    assert ensure_provider_platform_supported("ark") == "volcengine-ark"


def test_ensure_provider_platform_supported_normalizes_newapi_alias() -> None:
    assert ensure_provider_platform_supported("new-api") == "newapi"


def test_auto_routing_provider_matches_model_from_models_list() -> None:
    """When a model is in a provider's explicit models list, it should route there."""
    routing = AutoRoutingProvider(
        {
            "siliconflow": ProviderConfig(
                provider_type="siliconflow",
                api_key="sf-key",
                base_url="",
                healthcheck_model="deepseek-ai/DeepSeek-R1-Distill-Qwen-7B",
                models=["deepseek-ai/DeepSeek-R1-Distill-Qwen-7B", "doubao-seed-2-0-lite-260215"],
            ),
            "volcengine-ark": ProviderConfig(
                provider_type="volcengine-ark",
                api_key="ark-key",
                base_url="",
                healthcheck_model="",
            ),
        }
    )

    # "doubao-seed-2-0-lite-260215" is in SiliconFlow's models list,
    # so it should route to SiliconFlow, NOT volcengine-ark (which would be the heuristic).
    with patch("sirius_chat.providers.routing.SiliconFlowProvider.generate", return_value="sf-ok") as sf_generate:
        output = routing.generate(_request("doubao-seed-2-0-lite-260215"))

    assert output == "sf-ok"
    assert sf_generate.call_count == 1


def test_auto_routing_models_list_takes_priority_over_heuristic() -> None:
    """Explicit models list should override heuristic-based routing."""
    routing = AutoRoutingProvider(
        {
            "openai-compatible": ProviderConfig(
                provider_type="openai-compatible",
                api_key="openai-key",
                base_url="",
                healthcheck_model="gpt-4o-mini",
                models=["gpt-4o-mini", "deepseek-chat"],
            ),
            "deepseek": ProviderConfig(
                provider_type="deepseek",
                api_key="deepseek-key",
                base_url="",
                healthcheck_model="",
            ),
        }
    )

    # "deepseek-chat" is in openai-compatible's models list,
    # so it should NOT fall through to the deepseek heuristic.
    with patch("sirius_chat.providers.routing.OpenAICompatibleProvider.generate", return_value="openai-ok") as gen:
        output = routing.generate(_request("deepseek-chat"))

    assert output == "openai-ok"
    assert gen.call_count == 1


def test_provider_registry_persists_models_list(tmp_path: Path) -> None:
    """Models list should survive save/load round-trip in ProviderRegistry."""
    registry = ProviderRegistry(tmp_path)
    models = ["model-a", "model-b", "model-c"]
    providers = {
        "siliconflow": ProviderConfig(
            provider_type="siliconflow",
            api_key="sf-key",
            base_url="",
            healthcheck_model="model-a",
            models=models,
        )
    }
    registry.save(providers)

    loaded = registry.load()
    assert loaded["siliconflow"].models == models


def test_merge_provider_sources_carries_models_from_session_config(tmp_path: Path) -> None:
    """Models field from session JSON providers should propagate."""
    merged = merge_provider_sources(
        work_path=tmp_path,
        providers_config=[
            {
                "type": "siliconflow",
                "api_key": "sf-key",
                "healthcheck_model": "model-a",
                "models": ["model-a", "model-b"],
            }
        ],
    )

    assert merged["siliconflow"].models == ["model-a", "model-b"]


