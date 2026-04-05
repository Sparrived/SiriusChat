from __future__ import annotations

import json
import logging
from urllib import error, request as urllib_request

from sirius_chat.providers.base import GenerationRequest, LLMProvider

logger = logging.getLogger(__name__)

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
        logger.info(f"[模型调用] {request.model} | 温度: {request.temperature}, Token上限: {request.max_tokens}")
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
            logger.error(f"[模型调用失败] {request.model} | HTTP {exc.code}: {details[:100]}")
            raise RuntimeError(f"提供商 HTTP 错误 {exc.code}：{details}") from exc
        except error.URLError as exc:
            logger.error(f"[模型调用失败] {request.model} | 网络错误: {exc.reason}")
            raise RuntimeError(f"提供商网络错误：{exc.reason}") from exc

        data = json.loads(raw)
        choices = data.get("choices", [])
        if not choices:
            logger.error(f"[模型调用失败] {request.model} | 无 choices")
            raise RuntimeError("提供商响应中没有 choices。")

        message = choices[0].get("message", {})
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            logger.info(f"[模型调用成功] {request.model} | 回复字数: {len(content)}")
            return content.strip()

        reasoning_content = message.get("reasoning_content")
        if isinstance(reasoning_content, str) and reasoning_content.strip():
            logger.info(f"[模型调用成功] {request.model} | 回复字数: {len(reasoning_content)}")
            return reasoning_content.strip()

        logger.error(f"[模型调用失败] {request.model} | 响应为空")
        raise RuntimeError("提供商响应内容为空。")