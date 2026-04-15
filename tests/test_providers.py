"""Provider 统一基准测试。

所有 OpenAI-compatible 协议 provider 共享相同的响应解析逻辑，
本文件通过参数化测试验证各 provider 的基准行为一致性。
"""

from __future__ import annotations

import json
import logging
from unittest.mock import patch
from urllib import error

import pytest

from sirius_chat.providers.aliyun_bailian import AliyunBailianProvider
from sirius_chat.providers.base import GenerationRequest
from sirius_chat.providers.bigmodel import BigModelProvider
from sirius_chat.providers.deepseek import DeepSeekProvider
from sirius_chat.providers.mock import MockProvider
from sirius_chat.providers.openai_compatible import OpenAICompatibleProvider
from sirius_chat.providers.siliconflow import SiliconFlowProvider
from sirius_chat.providers.volcengine_ark import VolcengineArkProvider
from sirius_chat.providers.ytea import YTeaProvider


# ---------------------------------------------------------------------------
# 共享 fixture
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._raw

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


# ---------------------------------------------------------------------------
# Provider 注册表：每个 provider 的差异化参数
# ---------------------------------------------------------------------------

_PROVIDER_SPECS: list[dict] = [
    {
        "id": "openai_compatible",
        "cls": OpenAICompatibleProvider,
        "init": {"base_url": "https://api.openai.com", "api_key": "test-key"},
        "model": "gpt-4o-mini",
        "patch_target": "sirius_chat.providers.openai_compatible.urllib_request.urlopen",
        "expected_url": "https://api.openai.com/v1/chat/completions",
        "has_reasoning": False,
    },
    {
        "id": "aliyun_bailian",
        "cls": AliyunBailianProvider,
        "init": {"api_key": "test-key"},
        "model": "qwen-plus",
        "patch_target": "sirius_chat.providers.openai_compatible.urllib_request.urlopen",
        "expected_url": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        "has_reasoning": True,
    },
    {
        "id": "deepseek",
        "cls": DeepSeekProvider,
        "init": {"api_key": "test-key"},
        "model": "deepseek-chat",
        "patch_target": "sirius_chat.providers.deepseek.urllib_request.urlopen",
        "expected_url": "https://api.deepseek.com/chat/completions",
        "has_reasoning": True,
    },
    {
        "id": "bigmodel",
        "cls": BigModelProvider,
        "init": {"api_key": "test-key"},
        "model": "glm-4.6v",
        "patch_target": "sirius_chat.providers.bigmodel.urllib_request.urlopen",
        "expected_url": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        "has_reasoning": True,
    },
    {
        "id": "siliconflow",
        "cls": SiliconFlowProvider,
        "init": {"api_key": "test-key"},
        "model": "Pro/zai-org/GLM-4.7",
        "patch_target": "sirius_chat.providers.siliconflow.urllib_request.urlopen",
        "expected_url": "https://api.siliconflow.cn/v1/chat/completions",
        "has_reasoning": True,
    },
    {
        "id": "volcengine_ark",
        "cls": VolcengineArkProvider,
        "init": {"api_key": "test-key"},
        "model": "doubao-seed-2-0-lite-260215",
        "patch_target": "sirius_chat.providers.volcengine_ark.urllib_request.urlopen",
        "expected_url": "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
        "has_reasoning": True,
    },
    {
        "id": "ytea",
        "cls": YTeaProvider,
        "init": {"api_key": "test-key"},
        "model": "gpt-4o-mini",
        "patch_target": "sirius_chat.providers.ytea.urllib_request.urlopen",
        "expected_url": "https://api.ytea.top/v1/chat/completions",
        "has_reasoning": False,
    },
]


def _make_request(model: str, *, timeout_seconds: float | None = None) -> GenerationRequest:
    return GenerationRequest(
        model=model,
        system_prompt="你是一个有用的助手",
        messages=[{"role": "user", "content": "你好"}],
        timeout_seconds=timeout_seconds,
    )


def _ids(specs: list[dict]) -> list[str]:
    return [s["id"] for s in specs]


# ---------------------------------------------------------------------------
# 基准 1：纯文本内容返回
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("spec", _PROVIDER_SPECS, ids=_ids(_PROVIDER_SPECS))
def test_provider_returns_plain_content(spec: dict) -> None:
    provider = spec["cls"](**spec["init"])
    with patch(spec["patch_target"]) as mocked:
        mocked.return_value = _FakeResponse(
            {"choices": [{"message": {"content": "  文本内容  "}}]}
        )
        output = provider.generate(_make_request(spec["model"]))
    assert output == "文本内容"


# ---------------------------------------------------------------------------
# 基准 2：默认 endpoint 正确
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("spec", _PROVIDER_SPECS, ids=_ids(_PROVIDER_SPECS))
def test_provider_uses_correct_default_endpoint(spec: dict) -> None:
    provider = spec["cls"](**spec["init"])
    with patch(spec["patch_target"]) as mocked:
        mocked.return_value = _FakeResponse(
            {"choices": [{"message": {"content": "ok"}}]}
        )
        provider.generate(_make_request(spec["model"]))
    called_request = mocked.call_args[0][0]
    assert called_request.full_url == spec["expected_url"]


@pytest.mark.parametrize("spec", _PROVIDER_SPECS, ids=_ids(_PROVIDER_SPECS))
def test_provider_debug_log_includes_actual_url_and_metadata(caplog: pytest.LogCaptureFixture, spec: dict) -> None:
    provider = spec["cls"](**spec["init"])
    logger_name = str(spec["patch_target"]).removesuffix(".urllib_request.urlopen")
    with caplog.at_level(logging.DEBUG, logger=logger_name), patch(spec["patch_target"]) as mocked:
        mocked.return_value = _FakeResponse(
            {"choices": [{"message": {"content": "ok"}}]}
        )
        provider.generate(_make_request(spec["model"], timeout_seconds=12.5))

    assert spec["expected_url"] in caplog.text
    assert '"timeout_seconds": 12.5' in caplog.text
    assert '"payload"' in caplog.text
    assert '"provider":' in caplog.text


@pytest.mark.parametrize("spec", _PROVIDER_SPECS, ids=_ids(_PROVIDER_SPECS))
def test_provider_uses_request_timeout_override(spec: dict) -> None:
    provider = spec["cls"](**spec["init"])
    with patch(spec["patch_target"]) as mocked:
        mocked.return_value = _FakeResponse(
            {"choices": [{"message": {"content": "ok"}}]}
        )
        provider.generate(_make_request(spec["model"], timeout_seconds=95.0))
    assert mocked.call_args.kwargs["timeout"] == 95.0


@pytest.mark.parametrize("spec", _PROVIDER_SPECS, ids=_ids(_PROVIDER_SPECS))
def test_provider_falls_back_to_provider_timeout(spec: dict) -> None:
    init = dict(spec["init"])
    init["timeout_seconds"] = 41
    provider = spec["cls"](**init)
    with patch(spec["patch_target"]) as mocked:
        mocked.return_value = _FakeResponse(
            {"choices": [{"message": {"content": "ok"}}]}
        )
        provider.generate(_make_request(spec["model"]))
    assert mocked.call_args.kwargs["timeout"] == 41.0


def test_bigmodel_provider_normalizes_root_base_url() -> None:
    provider = BigModelProvider(api_key="test-key", base_url="https://open.bigmodel.cn")
    with patch("sirius_chat.providers.bigmodel.urllib_request.urlopen") as mocked:
        mocked.return_value = _FakeResponse({"choices": [{"message": {"content": "ok"}}]})
        provider.generate(_make_request("glm-4.6v"))
    called_request = mocked.call_args[0][0]
    assert called_request.full_url == "https://open.bigmodel.cn/api/paas/v4/chat/completions"


# ---------------------------------------------------------------------------
# 基准 3：结构化内容列表
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("spec", _PROVIDER_SPECS, ids=_ids(_PROVIDER_SPECS))
def test_provider_accepts_structured_content_list(spec: dict) -> None:
    provider = spec["cls"](**spec["init"])
    with patch(spec["patch_target"]) as mocked:
        mocked.return_value = _FakeResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": [
                                {"type": "text", "text": "段落A"},
                                {"type": "text", "text": "段落B"},
                            ]
                        }
                    }
                ]
            }
        )
        output = provider.generate(_make_request(spec["model"]))
    assert output == "段落A\n段落B"


# ---------------------------------------------------------------------------
# 基准 5：refusal 回退
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("spec", _PROVIDER_SPECS, ids=_ids(_PROVIDER_SPECS))
def test_provider_falls_back_to_refusal(spec: dict) -> None:
    provider = spec["cls"](**spec["init"])
    with patch(spec["patch_target"]) as mocked:
        mocked.return_value = _FakeResponse(
            {"choices": [{"message": {"content": "", "refusal": "拒绝回答"}}]}
        )
        output = provider.generate(_make_request(spec["model"]))
    assert output == "拒绝回答"


# ---------------------------------------------------------------------------
# 基准 6：reasoning_content 回退（仅支持的 provider）
# ---------------------------------------------------------------------------

_REASONING_SPECS = [s for s in _PROVIDER_SPECS if s["has_reasoning"]]


@pytest.mark.parametrize("spec", _REASONING_SPECS, ids=_ids(_REASONING_SPECS))
def test_provider_falls_back_to_reasoning_content(spec: dict) -> None:
    provider = spec["cls"](**spec["init"])
    with patch(spec["patch_target"]) as mocked:
        mocked.return_value = _FakeResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "reasoning_content": "  推理结果  ",
                        }
                    }
                ]
            }
        )
        output = provider.generate(_make_request(spec["model"]))
    assert output == "推理结果"


# ---------------------------------------------------------------------------
# 基准 7：网络错误处理
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("spec", _PROVIDER_SPECS, ids=_ids(_PROVIDER_SPECS))
def test_provider_raises_on_network_failure(spec: dict) -> None:
    provider = spec["cls"](**spec["init"])
    with patch(spec["patch_target"], side_effect=error.URLError("timeout")):
        with pytest.raises(RuntimeError):
            provider.generate(_make_request(spec["model"]))


# ---------------------------------------------------------------------------
# MockProvider 基准
# ---------------------------------------------------------------------------


class TestMockProvider:
    def test_consumes_predefined_responses(self) -> None:
        provider = MockProvider(responses=["x", "y"])
        req = _make_request("mock-model")
        assert provider.generate(req) == "x"
        assert provider.generate(req) == "y"
        assert provider.generate(req).startswith("[mock]")
        assert len(provider.requests) == 3


# ---------------------------------------------------------------------------
# 中间件基准
# ---------------------------------------------------------------------------


class TestMiddleware:
    @pytest.mark.asyncio
    async def test_rate_limiter_tracks_requests(self) -> None:
        from sirius_chat.providers.middleware import RateLimiterMiddleware, MiddlewareContext
        limiter = RateLimiterMiddleware(max_requests=2, window_seconds=1)
        ctx = MiddlewareContext(request=_make_request("mock-model"), metadata={})
        await limiter.process_request(ctx)
        assert len(limiter.request_times) == 1
        await limiter.process_request(ctx)
        assert len(limiter.request_times) == 2

    @pytest.mark.asyncio
    async def test_token_bucket_consumes_tokens(self) -> None:
        from sirius_chat.providers.middleware import TokenBucketRateLimiter, MiddlewareContext
        bucket = TokenBucketRateLimiter(capacity=5, refill_rate=1.0)
        bucket.tokens = 5.0
        ctx = MiddlewareContext(request=_make_request("mock-model"), metadata={})
        await bucket.process_request(ctx)
        assert bucket.tokens == 4.0

    @pytest.mark.asyncio
    async def test_retry_initializes_counter(self) -> None:
        from sirius_chat.providers.middleware import RetryMiddleware, MiddlewareContext
        retry = RetryMiddleware(max_retries=3)
        ctx = MiddlewareContext(request=_make_request("mock-model"), metadata={})
        await retry.process_request(ctx)
        assert ctx.metadata["retry_count"] == 0

    @pytest.mark.asyncio
    async def test_circuit_breaker_opens_on_failures(self) -> None:
        from sirius_chat.providers.middleware import CircuitBreakerMiddleware, MiddlewareContext
        breaker = CircuitBreakerMiddleware(failure_threshold=2)
        ctx = MiddlewareContext(request=_make_request("mock-model"), metadata={})
        await breaker.process_response(ctx, "", RuntimeError("fail"))
        await breaker.process_response(ctx, "", RuntimeError("fail"))
        with pytest.raises(CircuitBreakerMiddleware.CircuitOpen):
            await breaker.process_request(ctx)

    @pytest.mark.asyncio
    async def test_circuit_breaker_recovers(self) -> None:
        from sirius_chat.providers.middleware import CircuitBreakerMiddleware, MiddlewareContext
        breaker = CircuitBreakerMiddleware(failure_threshold=1, success_threshold=2)
        ctx = MiddlewareContext(request=_make_request("mock-model"), metadata={})
        await breaker.process_response(ctx, "", RuntimeError("fail"))
        await breaker.process_response(ctx, "ok", None)
        await breaker.process_response(ctx, "ok", None)
        assert breaker.failure_count == 0

    @pytest.mark.asyncio
    async def test_cost_metrics_tracks_calls(self) -> None:
        from sirius_chat.providers.middleware import CostMetricsMiddleware, MiddlewareContext
        metrics = CostMetricsMiddleware()
        request = _make_request("gpt-3.5-turbo")
        ctx = MiddlewareContext(request=request, metadata={"request": request})
        await metrics.process_request(ctx)
        await metrics.process_response(ctx, "response" * 50, None)
        assert metrics.total_calls == 1
        report = metrics.get_metrics()
        assert report["total_calls"] == 1

    @pytest.mark.asyncio
    async def test_chain_execution(self) -> None:
        from sirius_chat.providers.middleware import (
            MiddlewareChain, RateLimiterMiddleware, RetryMiddleware, MiddlewareContext,
        )
        chain = MiddlewareChain()
        chain.add(RateLimiterMiddleware(max_requests=10))
        chain.add(RetryMiddleware(max_retries=3))
        ctx = MiddlewareContext(request=_make_request("mock-model"), metadata={})
        await chain.execute_request(ctx)
        assert ctx.metadata["retry_count"] == 0
