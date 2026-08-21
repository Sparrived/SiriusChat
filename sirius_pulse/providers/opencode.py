from __future__ import annotations

from sirius_pulse.providers.base import DEFAULT_TIMEOUT_SECONDS
from sirius_pulse.providers.openai_compatible import OpenAICompatibleProvider

# OpenCode Zen 官方 OpenAI 兼容端点
DEFAULT_OPENCODE_BASE_URL = "https://opencode.ai/zen/v1"

# OpenCode GO 官方 OpenAI 兼容端点（独立订阅）
DEFAULT_OPENCODE_GO_BASE_URL = "https://opencode.ai/zen/go/v1"


def _normalize_opencode_base_url(base_url: str, default: str) -> str:
    """标准化 OpenCode base URL，移除末尾斜杠并补全 /v1 路径段。

    OpenCode 官方端点形如 https://opencode.ai/zen/v1（GO 为
    https://opencode.ai/zen/go/v1），兼容用户省略 /v1 的写法。
    """
    normalized = (base_url or default).rstrip("/")
    if not normalized.endswith("/v1"):
        normalized = f"{normalized}/v1"
    return normalized


class OpenCodeProvider(OpenAICompatibleProvider):
    """OpenCode Zen 网关 provider，支持 OpenAI 兼容协议。

    官方端点：https://opencode.ai/zen/v1/chat/completions。
    API Key 在 https://opencode.ai/auth 创建（sk-xxx），按量计费（pay-as-you-go）。
    常用模型：deepseek-v4-flash / deepseek-v4-pro / glm-5.2 / kimi-k3 /
    minimax-m3 / big-pickle（模型以 opencode/<model> 形式引用）。
    """

    _provider_name = "opencode"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = DEFAULT_OPENCODE_BASE_URL,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        super().__init__(
            base_url=_normalize_opencode_base_url(base_url, DEFAULT_OPENCODE_BASE_URL),
            api_key=api_key,
            timeout_seconds=timeout_seconds,
        )

    def _build_url(self, request) -> str:
        """构建 OpenCode Chat Completions 请求 URL。"""
        return f"{self._base_url}/chat/completions"


class OpenCodeGoProvider(OpenAICompatibleProvider):
    """OpenCode GO 订阅 provider，支持 OpenAI 兼容协议。

    官方端点：https://opencode.ai/zen/go/v1/chat/completions。
    需先在 OpenCode Zen 控制台订阅 GO（首月 $5，之后 $10/月），
    与 Zen 共用同一套 API Key（sk-xxx，在 https://opencode.ai/auth 获取）。
    常用模型：glm-5.3 / glm-5.2 / kimi-k3 / deepseek-v4-pro / mimo-v2.5 /
    hy3（模型以 opencode-go/<model> 形式引用）。
    """

    _provider_name = "opencode-go"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = DEFAULT_OPENCODE_GO_BASE_URL,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        super().__init__(
            base_url=_normalize_opencode_base_url(base_url, DEFAULT_OPENCODE_GO_BASE_URL),
            api_key=api_key,
            timeout_seconds=timeout_seconds,
        )

    def _build_url(self, request) -> str:
        """构建 OpenCode GO Chat Completions 请求 URL。"""
        return f"{self._base_url}/chat/completions"
