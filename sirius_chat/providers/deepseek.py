from __future__ import annotations

import json
import logging
from urllib import error, request as urllib_request

from sirius_chat.providers.base import (
    build_chat_completion_payload,
    build_generation_debug_context,
    GenerationRequest,
    LLMProvider,
    resolve_generation_timeout_seconds,
)
from sirius_chat.providers.response_utils import extract_assistant_text

logger = logging.getLogger(__name__)

DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"


class DeepSeekProvider(LLMProvider):
    """DeepSeek provider backed by /chat/completions.

    DeepSeek is OpenAI-compatible. The constructor accepts either:
    - https://api.deepseek.com
    - https://api.deepseek.com/v1
    and normalizes both to the same request endpoint.
    """

    _provider_name = "deepseek"

    def __init__(self, *, api_key: str, timeout_seconds: int = 30) -> None:
        self._base_url = DEFAULT_DEEPSEEK_BASE_URL
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds

    def generate(self, request: GenerationRequest) -> str:
        timeout_seconds = resolve_generation_timeout_seconds(request, self._timeout_seconds)
        url = f"{self._base_url}/chat/completions"
        debug_context = build_generation_debug_context(
            request,
            provider_name=self._provider_name,
            url=url,
            base_url=self._base_url,
            timeout_seconds=timeout_seconds,
        )

        logger.info(
            f"正准备向 {self._provider_name} 的 {request.model} 请教问题，"
            f"手头有 {debug_context['input_message_count']} 条消息想说，"
            f"温度调到 {request.temperature}，Token 上限设了 {request.max_tokens}，"
            f"预计要花 {debug_context['estimated_input_tokens']} 个 Token，"
            f"超时 {timeout_seconds:.1f} 秒～"
        )
        payload = build_chat_completion_payload(request, provider_name=self._provider_name)

        body = json.dumps(payload).encode("utf-8")
        logger.debug(
            f"[模型调用详情] {request.model} | 请求详情:\n"
            f"{json.dumps({**debug_context, 'request_body_bytes': len(body), 'payload': payload}, ensure_ascii=False, indent=2)}"
        )
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
            with urllib_request.urlopen(req, timeout=timeout_seconds) as response:
                status_code = getattr(response, "status", None)
                content_type = str(getattr(response, "headers", {}).get("Content-Type", "")).strip()
                raw = response.read().decode("utf-8")
        except error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            logger.error(
                f"[模型调用失败] {request.model} | Provider: {self._provider_name} | URL: {url} "
                f"| HTTP {exc.code}: {details[:100]}"
            )
            raise RuntimeError(f"提供商 HTTP 错误 {exc.code}：{details}") from exc
        except error.URLError as exc:
            logger.error(
                f"[模型调用失败] {request.model} | Provider: {self._provider_name} | URL: {url} "
                f"| 网络错误: {exc.reason}"
            )
            raise RuntimeError(f"提供商网络错误：{exc.reason}") from exc

        logger.debug(
            f"[模型原始响应] {request.model} | Provider: {self._provider_name} | URL: {url} "
            f"| HTTP状态: {status_code} | Content-Type: {content_type or '(未知)'} | raw:\n{raw}"
        )

        data = json.loads(raw)
        choices = data.get("choices", [])
        if not choices:
            logger.error(
                f"[模型调用失败] {request.model} | Provider: {self._provider_name} | URL: {url} | 无 choices"
            )
            raise RuntimeError("提供商响应中没有 choices。")

        message = choices[0].get("message", {})
        if not isinstance(message, dict):
            logger.error(
                f"[模型调用失败] {request.model} | Provider: {self._provider_name} | URL: {url} | message 字段无效"
            )
            raise RuntimeError("提供商响应中 message 字段无效。")

        content = extract_assistant_text(message)
        if content:
            logger.info(f"{self._provider_name} 的 {request.model} 回复我了，写了 {len(content)} 个字～")
            logger.debug(
                f"[模型输出] {request.model} | Provider: {self._provider_name} | URL: {url} | 响应内容:\n{content}"
            )
            return content

        logger.error(
            f"[模型调用失败] {request.model} | Provider: {self._provider_name} | URL: {url} "
            f"| 响应为空 | message_keys={list(message.keys())}"
        )
        raise RuntimeError("提供商响应内容为空。")
