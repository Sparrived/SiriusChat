"""
Token估算工具模块

提供精确的Token数量估算，支持多个模型与语言。
相比简单的len(text)/4，本模块提供：
- 中英文混合文本的精确估算
- 不同模型的特性支持
- 可选的tiktoken精确计算
"""

from __future__ import annotations

import re
from typing import Literal

# 模型特性
ModelType = Literal[
    "gpt-4",
    "gpt-3.5-turbo",
    "claude-3",
    "llama-2",
    "doubao-seed",
    "generic",
]

# 不同模型的token化比率估算（基于OpenAI tokenizer）
MODEL_TOKEN_RATIO = {
    "gpt-4": {"english": 4, "chinese": 1.0},  # 英文约4字符/token, 中文约1字/token
    "gpt-3.5-turbo": {"english": 4, "chinese": 1.0},
    "claude-3": {"english": 4, "chinese": 1.0},
    "doubao-seed": {"english": 4, "chinese": 1.0},
    "generic": {"english": 4, "chinese": 1.0},
}


def estimate_tokens_heuristic(
    text: str,
    *,
    model: ModelType = "generic",
) -> int:
    """
    启发式估算文本的token数量

    基于语言分析（中英文分离）进行估算，无需外部库。
    相比简单的len(text)/4，此方法更准确。

    Args:
        text: 待估算的文本
        model: 模型类型，用于选择估算参数

    Returns:
        估算的token数量

    Example:
        ```python
        # 英文文本
        estimate_tokens_heuristic("Hello world")  # ≈ 2-3

        # 中文文本
        estimate_tokens_heuristic("你好世界")  # ≈ 4

        # 混合文本
        estimate_tokens_heuristic("Hello 世界 world")  # ≈ 6-7
        ```
    """
    if not text:
        return 0

    text = text.strip()
    if not text:
        return 0

    # 获取模型参数
    model_params = MODEL_TOKEN_RATIO.get(model, MODEL_TOKEN_RATIO["generic"])
    english_chars_per_token = model_params["english"]
    chinese_char_tokens = model_params["chinese"]

    # 提取中文字符（包含CJK统一表意文字）
    chinese_pattern = r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufa6f\u3040-\u309f\u30a0-\u30ff]"
    chinese_chars = re.findall(chinese_pattern, text)
    chinese_count = len(chinese_chars)

    # 提取英文单词（字母序列）
    english_pattern = r"[a-zA-Z]+"
    english_words = re.findall(english_pattern, text)
    english_char_count = sum(len(word) for word in english_words)

    # 计算其他字符（数字、空格、标点等）
    other_count = len(text) - chinese_count - english_char_count

    # 估算各部分token数
    chinese_tokens = int(chinese_count * chinese_char_tokens)
    english_tokens = max(1, (english_char_count + english_chars_per_token - 1) // english_chars_per_token)
    other_tokens = max(0, (other_count + 3) // 4)  # 其他字符粗估为每4个1token

    total = max(1, chinese_tokens + english_tokens + other_tokens)
    return total


def estimate_tokens_with_tiktoken(text: str, *, model: str = "gpt-4") -> int | None:
    """
    使用tiktoken进行精确Token估算

    如果已安装tiktoken库，使用官方tokenizer获得精确计算。
    否则返回None，调用方应fallback到启发式估算。

    Args:
        text: 待估算的文本
        model: tiktoken支持的模型名称

    Returns:
        精确的token数量，或None（若tiktoken不可用）

    Example:
        ```python
        # 如果安装了tiktoken
        tokens = estimate_tokens_with_tiktoken("Hello world", model="gpt-4")
        if tokens is not None:
            print(f"Estimated: {tokens} tokens")
        else:
            print("tiktoken not available")
        ```
    """
    try:
        import tiktoken

        try:
            encoding = tiktoken.encoding_for_model(model)
        except KeyError:
            # 模型不在预设列表，尝试使用基础编码
            encoding = tiktoken.get_encoding("cl100k_base")

        tokens = encoding.encode(text)
        return len(tokens)
    except ImportError:
        return None


def estimate_tokens(
    text: str,
    *,
    model: ModelType = "generic",
    use_tiktoken: bool = True,
) -> int:
    """
    Smart token估算函数

    优先使用tiktoken获得精确结果，fallback到启发式估算。

    Args:
        text: 待估算的文本
        model: 模型类型
        use_tiktoken: 是否尝试使用tiktoken

    Returns:
        估算的token数量

    Example:
        ```python
        # 自动选择最佳估算方法
        tokens = estimate_tokens("你好世界", model="gpt-4")
        print(f"Estimated tokens: {tokens}")
        ```
    """
    if not text:
        return 0

    # 尝试使用tiktoken
    if use_tiktoken:
        tiktoken_result = estimate_tokens_with_tiktoken(text, model=model)
        if tiktoken_result is not None:
            return tiktoken_result

    # Fallback到启发式估算
    return estimate_tokens_heuristic(text, model=model)


def get_token_estimation_stats(text: str) -> dict[str, int]:
    """
    获取文本的token估算统计信息

    用于调试和性能分析，返回各部分的详细估算。

    Args:
        text: 待分析的文本

    Returns:
        包含以下key的字典：
        - total: 总token数
        - heuristic: 启发式估算结果
        - characters: 总字符数
        - chinese_count: 中文字符数
        - english_count: 英文字符数
        - other_count: 其他字符数
    """
    text = text.strip()

    # 字符统计
    chinese_pattern = r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufa6f\u3040-\u309f\u30a0-\u30ff]"
    chinese_chars = re.findall(chinese_pattern, text)
    english_pattern = r"[a-zA-Z]+"
    english_words = re.findall(english_pattern, text)
    english_char_count = sum(len(word) for word in english_words)

    chinese_count = len(chinese_chars)
    other_count = len(text) - chinese_count - english_char_count

    heuristic_tokens = estimate_tokens_heuristic(text)
    tiktoken_tokens = estimate_tokens_with_tiktoken(text)

    return {
        "total": tiktoken_tokens if tiktoken_tokens is not None else heuristic_tokens,
        "heuristic": heuristic_tokens,
        "tiktoken": tiktoken_tokens,
        "characters": len(text),
        "chinese_count": chinese_count,
        "english_count": english_char_count,
        "other_count": other_count,
    }


# 向后兼容：提供之前的_estimate_tokens接口
def legacy_estimate_tokens(text: str) -> int:
    """
    旧的粗略估算方式（兼容性）

    仅用于兼容之前的代码，建议使用estimate_tokens()。
    """
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


__all__ = [
    "estimate_tokens",
    "estimate_tokens_heuristic",
    "estimate_tokens_with_tiktoken",
    "get_token_estimation_stats",
    "legacy_estimate_tokens",
    "MODEL_TOKEN_RATIO",
    "ModelType",
]
