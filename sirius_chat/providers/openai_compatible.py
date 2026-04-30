from __future__ import annotations

import json
import logging
from typing import cast
from urllib import error, request as urllib_request

from sirius_chat.providers.base import (
    build_chat_completion_payload,
    build_generation_debug_context,
    GenerationRequest,
    LLMProvider,
    prepare_openai_compatible_messages,
    resolve_generation_timeout_seconds,
    set_last_generation_usage,
)
from sirius_chat.providers.response_utils import extract_assistant_text

logger = logging.getLogger(__name__)


class OpenAICompatibleProvider(LLMProvider):
    """OpenAI-compatible provider backed by /v1/chat/completions."""

    _provider_name = "openai-compatible"

    def __init__(self, *, base_url: str, api_key: str, timeout_seconds: int = 30) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds

    def generate(self, request: GenerationRequest) -> str:
        timeout_seconds = resolve_generation_timeout_seconds(request, self._timeout_seconds)
        url = f"{self._base_url}/v1/chat/completions"
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
        wire_messages, transport_stats = prepare_openai_compatible_messages(
            cast(list[dict[str, object]], payload["messages"])
        )
        wire_payload = dict(payload)
        wire_payload["messages"] = wire_messages

        body = json.dumps(wire_payload).encode("utf-8")
        logger.debug(
            f"[模型调用详情] {request.model} | 请求详情:\n"
            f"{json.dumps({**debug_context, **transport_stats, 'request_body_bytes': len(body), 'payload': payload}, ensure_ascii=False, indent=2)}"
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
            message = f"提供商 HTTP 错误 {exc.code}：{details}"
            if exc.code == 400 and "Failed to download multimodal content" in details:
                message = (
                    f"{message}。多模态文件下载失败：请确认 image_url 使用公网可访问的 http/https URL，"
                    "且响应头包含 Content-Type 与 Content-Length；若传入的是本地图片路径，"
                    "请直接传本地文件路径让框架自动转换为 data URL，或自行传入 data:*;base64,...。"
                )
            raise RuntimeError(message) from exc
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
        if not content:
            logger.error(
                f"[模型调用失败] {request.model} | Provider: {self._provider_name} | URL: {url} "
                f"| 响应为空 | message_keys={list(message.keys())}"
            )
            raise RuntimeError("提供商响应内容为空。")

        # Pass real token usage back to the engine tracker
        usage = data.get("usage")
        if usage and isinstance(usage, dict):
            set_last_generation_usage(dict(usage))
        else:
            set_last_generation_usage(None)

        logger.info(f"{self._provider_name} 的 {request.model} 回复我了，写了 {len(content)} 个字～")
        logger.debug(
            f"[模型输出] {request.model} | Provider: {self._provider_name} | URL: {url} | 响应内容:\n{content}"
        )
        return content
