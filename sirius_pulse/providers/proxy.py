"""全局网络代理配置（进程级）。

代理配置持久化在 ``<config_root>/providers/proxy.json``，由 WebUI 的
Provider 页面维护。运行时组件（OpenAI 兼容请求、models 接口探测、
models.dev 拉取）在构造 httpx 客户端时通过 :func:`httpx_proxy_kwargs`
读取进程级当前值，实现配置即生效，不需要重启进程。
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path

from sirius_pulse.utils.json_io import atomic_write_json, read_json

logger = logging.getLogger(__name__)

PROXY_CONFIG_FILENAME = "proxy.json"

_lock = threading.Lock()
_current: ProxySettings | None = None


@dataclass(slots=True)
class ProxySettings:
    """WebUI 可配置的 HTTP(S) 代理。

    ``http`` / ``https`` 为代理服务地址（可含 user:password@），
    ``no_proxy`` 为逗号分隔的直连白名单（当前主要用于展示，
    具体生效节点按各自 httpx 客户端的 trust_env 规则处理）。
    """

    http: str = ""
    https: str = ""
    no_proxy: str = ""

    @property
    def enabled(self) -> bool:
        return bool(self.http.strip() or self.https.strip())

    def effective_url(self) -> str | None:
        """httpx ``proxy=`` 参数使用的地址：优先 https，回退 http。"""
        https = self.https.strip()
        if https:
            return https
        http = self.http.strip()
        return http or None

    def to_dict(self) -> dict[str, str]:
        return {
            "http": self.http,
            "https": self.https,
            "no_proxy": self.no_proxy,
        }

    @staticmethod
    def from_dict(payload: object) -> ProxySettings:
        if not isinstance(payload, dict):
            return ProxySettings()
        return ProxySettings(
            http=str(payload.get("http", "") or "").strip(),
            https=str(payload.get("https", "") or "").strip(),
            no_proxy=str(payload.get("no_proxy", "") or "").strip(),
        )


def get_current_proxy() -> ProxySettings:
    """返回进程级当前代理配置（未设置时为空配置）。"""
    with _lock:
        return _current if _current is not None else ProxySettings()


def set_current_proxy(settings: ProxySettings) -> None:
    """更新进程级当前代理配置。"""
    global _current
    with _lock:
        _current = settings


def proxy_config_path(config_root: Path | str) -> Path:
    """返回代理配置文件路径（providers/proxy.json）。"""
    return Path(config_root) / "providers" / PROXY_CONFIG_FILENAME


def load_proxy_settings(config_root: Path | str) -> ProxySettings:
    """从磁盘读取代理配置，并同步为进程级当前值。"""
    settings = ProxySettings.from_dict(read_json(proxy_config_path(config_root)))
    set_current_proxy(settings)
    return settings


def save_proxy_settings(config_root: Path | str, settings: ProxySettings) -> None:
    """持久化代理配置，并同步为进程级当前值。"""
    set_current_proxy(settings)
    atomic_write_json(proxy_config_path(config_root), settings.to_dict())


def httpx_proxy_kwargs() -> dict[str, object]:
    """返回可直接展开进 httpx 客户端构造参数的代理配置。

    未启用代理时返回空 dict，保证现有调用无需变更。
    """
    url = get_current_proxy().effective_url()
    if not url:
        return {}
    return {"proxy": url}
