from __future__ import annotations

import json
import logging
from urllib import error, request as urllib_request

from sirius_chat.providers.base import (
    GenerationRequest,
    LLMProvider,
    estimate_generation_request_input_tokens,
    resolve_generation_timeout_seconds,
)
from sirius_chat.providers.response_utils import extract_assistant_text

logger = logging.getLogger(__name__)


class OpenAICompatibleProvider(LLMProvider):
    """OpenAI-compatible provider backed by /v1/chat/completions."""

    def __init__(self, *, base_url: str, api_key: str, timeout_seconds: int = 30) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds

    def generate(self, request: GenerationRequest) -> str:
        timeout_seconds = resolve_generation_timeout_seconds(request, self._timeout_seconds)
        # 基础调用日志（INFO）
        msg_count = len(request.messages)
        estimated_input_tokens = estimate_generation_request_input_tokens(request)
        estimated_total_upper = estimated_input_tokens + max(0, int(request.max_tokens))

        logger.info(
            f"[模型调用] {request.model} | 温度: {request.temperature}, Token上限: {request.max_tokens} "
            f"| 消息数: {msg_count} | 调用目的: {request.purpose} | 超时: {timeout_seconds:.1f}s "
            f"| 预计输入Token: {estimated_input_tokens} | 预计总Token上限: {estimated_total_upper}"
        )
        debug_input = {
            "system_prompt": request.system_prompt,
            "messages": request.messages,
        }
        logger.debug(
            f"[模型调用详情] {request.model} | 完整输入:\n"
            f"{json.dumps(debug_input, ensure_ascii=False, indent=2)}"
        )
        
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
            with urllib_request.urlopen(req, timeout=timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            logger.error(f"[模型调用失败] {request.model} | HTTP {exc.code}: {details[:100]}")
            raise RuntimeError(f"提供商 HTTP 错误 {exc.code}：{details}") from exc
        except error.URLError as exc:
            logger.error(f"[模型调用失败] {request.model} | 网络错误: {exc.reason}")
            raise RuntimeError(f"提供商网络错误：{exc.reason}") from exc

        logger.debug(f"[模型原始响应] {request.model} | raw:\n{raw}")

        data = json.loads(raw)
        choices = data.get("choices", [])
        if not choices:
            logger.error(f"[模型调用失败] {request.model} | 无 choices")
            raise RuntimeError("提供商响应中没有 choices。")

        message = choices[0].get("message", {})
        if not isinstance(message, dict):
            logger.error(f"[模型调用失败] {request.model} | message 字段无效")
            raise RuntimeError("提供商响应中 message 字段无效。")

        content = extract_assistant_text(message)
        if not content:
            logger.error(
                f"[模型调用失败] {request.model} | 响应为空 | message_keys={list(message.keys())}"
            )
            raise RuntimeError("提供商响应内容为空。")
        
        logger.info(f"[模型调用成功] {request.model} | 字数: {len(content)}")
        logger.debug(f"[模型输出] {request.model} | 响应内容:\n{content}")
        return content
