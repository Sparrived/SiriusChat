"""框架级 GitHub 交互基础设施。

提供：
- GitHubWebhookServer: Webhook 签名验证 + HTTP 生命周期 + 事件分发
- GitHubClient: 渐近式请求封装，使用标准 API headers
- fetch_repo_events / fetch_compare_commit_count: GitHub 事件与提交范围查询
- event_bridge: github_monitor → plugin 事件通知桥接
"""

from sirius_pulse.github.client import GitHubClient, github_headers
from sirius_pulse.github.event_bridge import (
    get_coding_bot_login,
    get_issue_repos,
    notify_issue_comment,
    notify_issue_opened,
    notify_pr_event,
    register_comment_handler,
    register_issue_handler,
    register_pr_handler,
    set_coding_bot_login,
    set_issue_repos,
)
from sirius_pulse.github.events import fetch_compare_commit_count, fetch_repo_events
from sirius_pulse.github.webhook import (
    GitHubWebhookServer,
    RepoFilter,
    WebhookHandler,
    verify_signature,
)

__all__ = [
    "GitHubClient",
    "GitHubWebhookServer",
    "RepoFilter",
    "WebhookHandler",
    "fetch_repo_events",
    "fetch_compare_commit_count",
    "github_headers",
    "get_coding_bot_login",
    "get_issue_repos",
    "notify_issue_comment",
    "notify_issue_opened",
    "notify_pr_event",
    "register_comment_handler",
    "register_issue_handler",
    "register_pr_handler",
    "set_coding_bot_login",
    "set_issue_repos",
    "verify_signature",
]
