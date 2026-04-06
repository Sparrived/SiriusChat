import json
from unittest.mock import patch
from urllib import error

import pytest

from sirius_chat.providers.base import GenerationRequest
from sirius_chat.providers.openai_compatible import OpenAICompatibleProvider


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
        model="gpt-4o-mini",
        system_prompt="你是一个有用的助手",
        messages=[{"role": "user", "content": "你好"}],
    )


def test_openai_compatible_provider_returns_plain_content() -> None:
    provider = OpenAICompatibleProvider(base_url="https://api.openai.com", api_key="test-key")

    with patch("sirius_chat.providers.openai_compatible.urllib_request.urlopen") as mocked_urlopen:
        mocked_urlopen.return_value = _FakeResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": "  你好，我是助手。  ",
                        }
                    }
                ]
            }
        )
        output = provider.generate(_request())

    assert output == "你好，我是助手。"


def test_openai_compatible_provider_accepts_structured_content_list() -> None:
    provider = OpenAICompatibleProvider(base_url="https://api.openai.com", api_key="test-key")

    with patch("sirius_chat.providers.openai_compatible.urllib_request.urlopen") as mocked_urlopen:
        mocked_urlopen.return_value = _FakeResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": [
                                {"type": "text", "text": "第一行"},
                                {"type": "text", "text": "第二行"},
                            ]
                        }
                    }
                ]
            }
        )
        output = provider.generate(_request())

    assert output == "第一行\n第二行"


def test_openai_compatible_provider_falls_back_to_refusal() -> None:
    provider = OpenAICompatibleProvider(base_url="https://api.openai.com", api_key="test-key")

    with patch("sirius_chat.providers.openai_compatible.urllib_request.urlopen") as mocked_urlopen:
        mocked_urlopen.return_value = _FakeResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "refusal": "当前请求被拒绝",
                        }
                    }
                ]
            }
        )
        output = provider.generate(_request())

    assert output == "当前请求被拒绝"


def test_openai_compatible_provider_raises_runtime_error_on_network_failure() -> None:
    provider = OpenAICompatibleProvider(base_url="https://api.openai.com", api_key="test-key")

    with patch(
        "sirius_chat.providers.openai_compatible.urllib_request.urlopen",
        side_effect=error.URLError("timeout"),
    ):
        with pytest.raises(RuntimeError, match="网络错误|network error"):
            provider.generate(_request())
