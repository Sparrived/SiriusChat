from __future__ import annotations

import json
from urllib import error, request as urllib_request

from sirius_chat.providers.base import GenerationRequest, LLMProvider


DEFAULT_SILICONFLOW_BASE_URL = "https://api.siliconflow.cn"


class SiliconFlowProvider(LLMProvider):
    """SiliconFlow provider backed by OpenAI-compatible /v1/chat/completions."""

    def __init__(self, *, api_key: str, base_url: str = DEFAULT_SILICONFLOW_BASE_URL, timeout_seconds: int = 30) -> None:
        normalized = base_url.rstrip("/")
        if normalized.endswith("/v1"):
            normalized = normalized[: -len("/v1")]
        self._base_url = normalized
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds

    def generate(self, request: GenerationRequest) -> str:
        url = f"{self._base_url}/v1/chat/completions"
        payload = {
            "model": request.model,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                *request.messages,
            ],
        }

        body = json.dumps(payload).encode("utf-8")
        req = urllib_request.Request(
            url=url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            method="POST",
        )

        try:
            with urllib_request.urlopen(req, timeout=self._timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"提供商 HTTP 错误 {exc.code}：{details}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"提供商网络错误：{exc.reason}") from exc

        data = json.loads(raw)
        choices = data.get("choices", [])
        if not choices:
            raise RuntimeError("提供商响应中没有 choices。")

        message = choices[0].get("message", {})
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()

        reasoning_content = message.get("reasoning_content")
        if isinstance(reasoning_content, str) and reasoning_content.strip():
            return reasoning_content.strip()

        raise RuntimeError("提供商响应内容为空。")