"""Token management module - handles token counting, usage analysis, and persistent storage."""

from sirius_chat.token.analytics import (
    AnalyticsReport,
    BaselineDict,
    BucketDict,
    TimeSliceDict,
    compute_baseline,
    full_report,
    group_by_actor,
    group_by_model,
    group_by_session,
    group_by_task,
    time_series,
)
from sirius_chat.token.store import TokenUsageStore
from sirius_chat.token.usage import (
    TokenUsageBaseline,
    TokenUsageBucket,
    TokenUsageSummary,
    build_token_usage_baseline,
    summarize_token_usage,
)
from sirius_chat.token.utils import (
    ModelType,
    estimate_tokens,
    estimate_tokens_heuristic,
    estimate_tokens_with_tiktoken,
    get_token_estimation_stats,
    legacy_estimate_tokens,
)

__all__ = [
    "TokenUsageBucket",
    "TokenUsageBaseline",
    "TokenUsageSummary",
    "TokenUsageStore",
    "AnalyticsReport",
    "BaselineDict",
    "BucketDict",
    "TimeSliceDict",
    "build_token_usage_baseline",
    "summarize_token_usage",
    "compute_baseline",
    "full_report",
    "group_by_actor",
    "group_by_model",
    "group_by_session",
    "group_by_task",
    "time_series",
    "estimate_tokens",
    "estimate_tokens_heuristic",
    "estimate_tokens_with_tiktoken",
    "get_token_estimation_stats",
    "legacy_estimate_tokens",
    "ModelType",
]
