import json
from unittest.mock import patch
from urllib import error

import pytest

from sirius_chat.providers.base import GenerationRequest
from sirius_chat.providers.siliconflow import SiliconFlowProvider


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._raw

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def _request() -> GenerationRequest:
    return GenerationRequest(
        model="Pro/zai-org/GLM-4.7",
        system_prompt="你是一个有用的助手",
        messages=[{"role": "user", "content": "你好"}],
    )


def test_siliconflow_provider_uses_default_endpoint_and_returns_content() -> None:
    provider = SiliconFlowProvider(api_key="test-key")

    with patch("sirius_chat.providers.siliconflow.urllib_request.urlopen") as mocked_urlopen:
        mocked_urlopen.return_value = _FakeResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": "  你好，我是 SiliconFlow 上的助手。  ",
                        }
                    }
                ]
            }
        )

        output = provider.generate(_request())

    assert output == "你好，我是 SiliconFlow 上的助手。"
    called_request = mocked_urlopen.call_args[0][0]
    assert called_request.full_url == "https://api.siliconflow.cn/v1/chat/completions"


def test_siliconflow_provider_accepts_base_url_with_v1_suffix() -> None:
    provider = SiliconFlowProvider(api_key="test-key", base_url="https://api.siliconflow.cn/v1")

    with patch("sirius_chat.providers.siliconflow.urllib_request.urlopen") as mocked_urlopen:
        mocked_urlopen.return_value = _FakeResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": "ok",
                        }
                    }
                ]
            }
        )
        provider.generate(_request())

    called_request = mocked_urlopen.call_args[0][0]
    assert called_request.full_url == "https://api.siliconflow.cn/v1/chat/completions"


def test_siliconflow_provider_falls_back_to_reasoning_content() -> None:
    provider = SiliconFlowProvider(api_key="test-key")

    with patch("sirius_chat.providers.siliconflow.urllib_request.urlopen") as mocked_urlopen:
        mocked_urlopen.return_value = _FakeResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "reasoning_content": "  先分析目标，再给出方案。 ",
                        }
                    }
                ]
            }
        )
        output = provider.generate(_request())

    assert output == "先分析目标，再给出方案。"


def test_siliconflow_provider_accepts_content_as_structured_list() -> None:
    provider = SiliconFlowProvider(api_key="test-key")

    with patch("sirius_chat.providers.siliconflow.urllib_request.urlopen") as mocked_urlopen:
        mocked_urlopen.return_value = _FakeResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": [
                                {"type": "text", "text": "第一段"},
                                {"type": "text", "text": "第二段"},
                            ]
                        }
                    }
                ]
            }
        )
        output = provider.generate(_request())

    assert output == "第一段\n第二段"


def test_siliconflow_provider_falls_back_to_refusal_when_present() -> None:
    provider = SiliconFlowProvider(api_key="test-key")

    with patch("sirius_chat.providers.siliconflow.urllib_request.urlopen") as mocked_urlopen:
        mocked_urlopen.return_value = _FakeResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "refusal": "当前问题暂时无法回答",
                        }
                    }
                ]
            }
        )
        output = provider.generate(_request())

    assert output == "当前问题暂时无法回答"


def test_siliconflow_provider_raises_runtime_error_on_network_failure() -> None:
    provider = SiliconFlowProvider(api_key="test-key")

    with patch(
        "sirius_chat.providers.siliconflow.urllib_request.urlopen",
        side_effect=error.URLError("timeout"),
    ):
        with pytest.raises(RuntimeError, match="网络错误|network error"):
            provider.generate(_request())

