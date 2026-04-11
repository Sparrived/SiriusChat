from __future__ import annotations

from sirius_chat.providers.openai_compatible import OpenAICompatibleProvider

DEFAULT_NEWAPI_BASE_URL = "https://docs.newapi.pro"


class NewAPIProvider(OpenAICompatibleProvider):
    """NewAPI provider backed by OpenAI-compatible /v1/chat/completions.

    NewAPI 官方文档声明其 AI 模型接口兼容 OpenAI API 格式。
    该实现复用 OpenAICompatibleProvider，并对 base_url 做 /v1 归一化。
    """

    def __init__(
        self,
        *,
        api_key: str,
        timeout_seconds: int = 30,
    ) -> None:
        super().__init__(
            base_url=DEFAULT_NEWAPI_BASE_URL,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
        )
