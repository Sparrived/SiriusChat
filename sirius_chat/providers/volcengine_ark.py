from __future__ import annotations

import json
import logging
from urllib import error, request as urllib_request

from sirius_chat.providers.base import GenerationRequest, LLMProvider

logger = logging.getLogger(__name__)

DEFAULT_VOLCENGINE_ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"


class VolcengineArkProvider(LLMProvider):
    """Volcengine Ark provider backed by /api/v3/chat/completions."""

    def __init__(self, *, api_key: str, base_url: str = DEFAULT_VOLCENGINE_ARK_BASE_URL, timeout_seconds: int = 30) -> None:
        normalized = base_url.rstrip("/")
        if normalized.endswith("/api/v3"):
            normalized = normalized[: -len("/api/v3")]
        self._base_url = normalized
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds

    def generate(self, request: GenerationRequest) -> str:
        # 记录调用详情
        msg_count = len(request.messages)
        system_preview = request.system_prompt[:200] if request.system_prompt else "(无系统提示)"
        user_msg_preview = ""
        if request.messages:
            user_content = request.messages[-1].get("content", "")[:150]
            user_msg_preview = f" | 用户消息: {user_content}"
        
        logger.info(
            f"[模型调用] {request.model} | 温度: {request.temperature}, Token上限: {request.max_tokens} "
            f"| 消息数: {msg_count}{user_msg_preview}\n"
            f"  系统提示: {system_preview}"
        )
        
        url = f"{self._base_url}/api/v3/chat/completions"
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
            raise RuntimeError(f"Provider HTTP error {exc.code}: {details}") from exc
        except error.URLError as exc:
            logger.error(f"[模型调用失败] {request.model} | 网络错误: {exc.reason}")
            raise RuntimeError(f"Provider network error: {exc.reason}") from exc

        data = json.loads(raw)
        choices = data.get("choices", [])
        if not choices:
            logger.error(f"[模型调用失败] {request.model} | 无 choices")
            raise RuntimeError("Provider response has no choices.")

        message = choices[0].get("message", {})
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            content_preview = content[:200]
            logger.info(f"[模型调用成功] {request.model} | 字数: {len(content)}\n  响应内容: {content_preview}")
            return content.strip()

        reasoning_content = message.get("reasoning_content")
        if isinstance(reasoning_content, str) and reasoning_content.strip():
            content_preview = reasoning_content[:200]
            logger.info(f"[模型调用成功] {request.model} | 字数: {len(reasoning_content)}\n  响应内容: {content_preview}")
            return reasoning_content.strip()

        raise RuntimeError("Provider response has empty content.")