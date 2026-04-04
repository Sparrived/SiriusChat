import json
from unittest.mock import patch
from urllib import error

import pytest

from sirius_chat.providers.base import GenerationRequest
from sirius_chat.providers.volcengine_ark import VolcengineArkProvider


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
        model="doubao-seed-2-0-lite-260215",
        system_prompt="你是一个有用的助手",
        messages=[{"role": "user", "content": "你好"}],
    )


def test_volcengine_ark_provider_uses_default_endpoint_and_returns_content() -> None:
    provider = VolcengineArkProvider(api_key="test-key")

    with patch("sirius_chat.providers.volcengine_ark.urllib_request.urlopen") as mocked_urlopen:
        mocked_urlopen.return_value = _FakeResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": "  你好，我是火山方舟助手。  ",
                        }
                    }
                ]
            }
        )

        output = provider.generate(_request())

    assert output == "你好，我是火山方舟助手。"
    called_request = mocked_urlopen.call_args[0][0]
    assert called_request.full_url == "https://ark.cn-beijing.volces.com/api/v3/chat/completions"


def test_volcengine_ark_provider_accepts_base_url_with_api_v3_suffix() -> None:
    provider = VolcengineArkProvider(api_key="test-key", base_url="https://ark.cn-beijing.volces.com/api/v3")

    with patch("sirius_chat.providers.volcengine_ark.urllib_request.urlopen") as mocked_urlopen:
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
    assert called_request.full_url == "https://ark.cn-beijing.volces.com/api/v3/chat/completions"


def test_volcengine_ark_provider_falls_back_to_reasoning_content() -> None:
    provider = VolcengineArkProvider(api_key="test-key")

    with patch("sirius_chat.providers.volcengine_ark.urllib_request.urlopen") as mocked_urlopen:
        mocked_urlopen.return_value = _FakeResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "reasoning_content": "  先分析后回答。 ",
                        }
                    }
                ]
            }
        )
        output = provider.generate(_request())

    assert output == "先分析后回答。"


def test_volcengine_ark_provider_raises_runtime_error_on_network_failure() -> None:
    provider = VolcengineArkProvider(api_key="test-key")

    with patch(
        "sirius_chat.providers.volcengine_ark.urllib_request.urlopen",
        side_effect=error.URLError("timeout"),
    ):
        with pytest.raises(RuntimeError, match="Provider network error"):
            provider.generate(_request())


