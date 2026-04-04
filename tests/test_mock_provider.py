from sirius_chat.providers.base import GenerationRequest
from sirius_chat.providers.mock import MockProvider


def test_mock_provider_consumes_predefined_responses() -> None:
    provider = MockProvider(responses=["x", "y"])
    request = GenerationRequest(
        model="mock-model",
        system_prompt="system",
        messages=[{"role": "user", "content": "hello"}],
    )

    assert provider.generate(request) == "x"
    assert provider.generate(request) == "y"
    assert provider.generate(request).startswith("[mock]")
    assert len(provider.requests) == 3


