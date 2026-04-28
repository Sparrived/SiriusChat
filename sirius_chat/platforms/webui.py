"""SiriusChat WebUI — 基于 aiohttp 的配置管理面板。

提供 REST API + 内嵌前端页面，用于：
- Provider / 人格 / 模型编排 配置
- 群白名单管理
- 引擎状态监控与重启
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any

from aiohttp import web

from sirius_chat.core.orchestration_store import OrchestrationStore
from sirius_chat.core.persona_generator import PersonaGenerator
from sirius_chat.core.persona_store import PersonaStore
from sirius_chat.providers.routing import WorkspaceProviderManager

from .persona_utils import generate_persona_from_interview

LOG = logging.getLogger("sirius.platforms.webui")


def _json_response(data: dict[str, Any], status: int = 200) -> web.Response:
    return web.json_response(data, status=status, dumps=lambda o: json.dumps(o, ensure_ascii=False, indent=2))


class WebUIServer:
    """轻量级 aiohttp WebUI 服务器。"""

    def __init__(
        self,
        bridge: Any,
        host: str = "0.0.0.0",
        port: int = 8080,
        napcat_install_dir: str | Path | None = None,
    ) -> None:
        self.bridge = bridge
        self.host = host
        self.port = port
        self.napcat_manager = None
        if napcat_install_dir is not None:
            from .napcat_manager import NapCatManager
            self.napcat_manager = NapCatManager(napcat_install_dir)
        self.app = web.Application()
        self._setup_routes()
        self.runner: web.AppRunner | None = None
        self.site: web.TCPSite | None = None

    def _setup_routes(self) -> None:
        self.app.router.add_get("/", self.index)
        static_dir = Path(__file__).parent / "webui_static"
        self.app.router.add_static("/static/", static_dir)
        self.app.router.add_get("/api/status", self.api_status)
        self.app.router.add_get("/api/providers", self.api_providers_get)
        self.app.router.add_post("/api/providers", self.api_providers_post)
        self.app.router.add_get("/api/orchestration", self.api_orchestration_get)
        self.app.router.add_post("/api/orchestration", self.api_orchestration_post)
        self.app.router.add_get("/api/persona", self.api_persona_get)
        self.app.router.add_post("/api/persona/save", self.api_persona_save)
        self.app.router.add_post("/api/persona/keywords", self.api_persona_keywords)
        self.app.router.add_post("/api/persona/interview", self.api_persona_interview)
        self.app.router.add_post("/api/config", self.api_config_post)
        self.app.router.add_post("/api/engine/toggle", self.api_engine_toggle)
        self.app.router.add_post("/api/engine/reload", self.api_engine_reload)
        # NapCat 管理
        self.app.router.add_get("/api/napcat/status", self.api_napcat_status)
        self.app.router.add_post("/api/napcat/install", self.api_napcat_install)
        self.app.router.add_post("/api/napcat/configure", self.api_napcat_configure)
        self.app.router.add_post("/api/napcat/start", self.api_napcat_start)
        self.app.router.add_post("/api/napcat/stop", self.api_napcat_stop)
        self.app.router.add_get("/api/napcat/logs", self.api_napcat_logs)

    # ─── 生命周期 ─────────────────────────────────────────

    async def start(self) -> None:
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, self.host, self.port)
        await self.site.start()
        LOG.info("WebUI running on http://%s:%s", self.host, self.port)

    async def stop(self) -> None:
        if self.site:
            await self.site.stop()
        if self.runner:
            await self.runner.cleanup()
        LOG.info("WebUI stopped")

    # ─── 静态页面 ─────────────────────────────────────────

    async def index(self, request: web.Request) -> web.Response:
        html_path = Path(__file__).parent / "webui_static" / "index.html"
        if html_path.exists():
            return web.FileResponse(html_path)
        return web.Response(text="WebUI not found", status=404)

    # ─── API: 状态 ────────────────────────────────────────

    async def api_status(self, request: web.Request) -> web.Response:
        runtime = self.bridge.runtime
        persona = PersonaStore.load(runtime.work_path)
        orch = OrchestrationStore.load(runtime.work_path)
        provider_mgr = WorkspaceProviderManager(runtime.work_path)
        providers = provider_mgr.load()
        return _json_response({
            "ready": runtime.is_ready(),
            "enabled": self.bridge._enabled,
            "persona_name": persona.name if persona else None,
            "persona_source": persona.source if persona else None,
            "providers": [
                {"type": p.provider_type, "base_url": p.base_url, "healthcheck_model": p.healthcheck_model, "enabled": p.enabled}
                for p in providers.values()
            ],
            "orchestration": {
                "analysis_model": orch.get("analysis_model", "gpt-4o-mini"),
                "chat_model": orch.get("chat_model", "gpt-4o"),
                "vision_model": orch.get("vision_model", "gpt-4o"),
            },
            "allowed_group_ids": self.bridge._get_allowed_group_ids(),
            "allowed_private_user_ids": self.bridge._get_allowed_private_user_ids(),
            "enable_group_chat": self.bridge.get_config("enable_group_chat", True),
            "enable_private_chat": self.bridge.get_config("enable_private_chat", True),
        })

    # ─── API: Provider ────────────────────────────────────

    async def api_providers_get(self, request: web.Request) -> web.Response:
        runtime = self.bridge.runtime
        provider_mgr = WorkspaceProviderManager(runtime.work_path)
        providers = provider_mgr.load()
        return _json_response({
            "providers": [
                {
                    "type": p.provider_type,
                    "api_key": p.api_key,
                    "base_url": p.base_url,
                    "healthcheck_model": p.healthcheck_model,
                    "enabled": p.enabled,
                    "models": list(p.models),
                }
                for p in providers.values()
            ]
        })

    async def api_providers_post(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return _json_response({"error": "Invalid JSON"}, 400)
        entries = body.get("providers", [])
        if not isinstance(entries, list):
            return _json_response({"error": "providers must be a list"}, 400)
        runtime = self.bridge.runtime
        provider_mgr = WorkspaceProviderManager(runtime.work_path)
        try:
            provider_mgr.save_from_entries(entries)
            LOG.info("Provider 配置已保存 %d 条", len(entries))
            # 重建引擎使配置生效
            runtime.reload_engine()
            return _json_response({"success": True, "message": "Provider 已保存，引擎已重建"})
        except Exception as exc:
            LOG.exception("保存 Provider 失败")
            return _json_response({"error": str(exc)}, 500)

    # ─── API: Orchestration ───────────────────────────────

    async def api_orchestration_get(self, request: web.Request) -> web.Response:
        orch = OrchestrationStore.load(self.bridge.runtime.work_path)
        return _json_response({
            "analysis_model": orch.get("analysis_model", "gpt-4o-mini"),
            "chat_model": orch.get("chat_model", "gpt-4o"),
            "vision_model": orch.get("vision_model", "gpt-4o"),
        })

    async def api_orchestration_post(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return _json_response({"error": "Invalid JSON"}, 400)
        orch = OrchestrationStore.load(self.bridge.runtime.work_path)
        for key in ("analysis_model", "chat_model", "vision_model"):
            if key in body:
                orch[key] = str(body[key]).strip()
        OrchestrationStore.save(self.bridge.runtime.work_path, orch)
        self.bridge.runtime.reload_engine()
        LOG.info("模型编排已更新: %s", orch)
        return _json_response({"success": True, "message": "模型编排已保存，引擎已重建"})

    # ─── API: Persona ─────────────────────────────────────

    async def api_persona_get(self, request: web.Request) -> web.Response:
        persona = PersonaStore.load(self.bridge.runtime.work_path)
        if persona is None:
            return _json_response({"persona": None})
        return _json_response({"persona": asdict(persona)})

    async def api_persona_save(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return _json_response({"error": "Invalid JSON"}, 400)
        raw = body.get("persona")
        if not isinstance(raw, dict):
            return _json_response({"error": "persona must be an object"}, 400)
        try:
            persona = PersonaProfile(**raw)
            PersonaStore.save(self.bridge.runtime.work_path, persona)
            self.bridge.runtime.reload_engine()
            return _json_response({"success": True, "message": f"人格「{persona.name}」已保存"})
        except Exception as exc:
            LOG.exception("保存人格失败")
            return _json_response({"error": str(exc)}, 500)

    async def api_persona_keywords(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return _json_response({"error": "Invalid JSON"}, 400)
        name = str(body.get("name", "小星")).strip()
        keywords = [k.strip() for k in str(body.get("keywords", "")).split() if k.strip()]
        aliases = [a.strip() for a in body.get("aliases", []) if isinstance(a, str) and a.strip()]
        provider = self.bridge.runtime._build_provider()
        try:
            if provider is not None and hasattr(provider, "generate_async"):
                persona = PersonaGenerator.from_keywords(name, keywords, provider_async=provider)
            else:
                persona = PersonaGenerator.from_keywords(name, keywords)
            persona.aliases = aliases
            return _json_response({"success": True, "persona": asdict(persona)})
        except Exception as exc:
            LOG.exception("关键词人格生成失败")
            return _json_response({"error": str(exc)}, 500)

    async def api_persona_interview(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return _json_response({"error": "Invalid JSON"}, 400)
        name = str(body.get("name", "小星")).strip()
        answers = body.get("answers", {})
        aliases = [a.strip() for a in body.get("aliases", []) if isinstance(a, str) and a.strip()]
        provider = self.bridge.runtime._build_provider()
        try:
            persona = await generate_persona_from_interview(
                work_path=self.bridge.runtime.work_path,
                provider=provider,
                name=name,
                answers=answers,
                aliases=aliases,
            )
            return _json_response({"success": True, "persona": asdict(persona)})
        except Exception as exc:
            LOG.exception("问卷人格生成失败")
            return _json_response({"error": str(exc)}, 500)

    # ─── API: Config ──────────────────────────────────────

    async def api_config_post(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return _json_response({"error": "Invalid JSON"}, 400)
        for key in ("allowed_group_ids", "allowed_private_user_ids", "enable_group_chat", "enable_private_chat"):
            if key in body:
                self.bridge.set_config(key, body[key])
        LOG.info("配置已更新: %s", {k: body.get(k) for k in body})
        return _json_response({"success": True, "message": "配置已保存"})

    # ─── API: Engine ──────────────────────────────────────

    async def api_engine_toggle(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return _json_response({"error": "Invalid JSON"}, 400)
        enabled = bool(body.get("enabled", not self.bridge._enabled))
        self.bridge._enabled = enabled
        return _json_response({"success": True, "enabled": enabled})

    async def api_engine_reload(self, request: web.Request) -> web.Response:
        try:
            self.bridge.runtime.reload_engine()
            return _json_response({"success": True, "message": "引擎已重建"})
        except Exception as exc:
            LOG.exception("引擎重建失败")
            return _json_response({"error": str(exc)}, 500)

    # ─── API: NapCat ──────────────────────────────────────

    async def api_napcat_status(self, request: web.Request) -> web.Response:
        if self.napcat_manager is None:
            return _json_response({"enabled": False, "message": "NapCat 管理未启用"})
        return _json_response({"enabled": True, **self.napcat_manager.get_status()})

    async def api_napcat_install(self, request: web.Request) -> web.Response:
        if self.napcat_manager is None:
            return _json_response({"success": False, "message": "NapCat 管理未启用"}, 400)
        try:
            result = await self.napcat_manager.install()
            return _json_response(result)
        except Exception as exc:
            LOG.exception("NapCat 安装失败")
            return _json_response({"success": False, "message": str(exc)}, 500)

    async def api_napcat_configure(self, request: web.Request) -> web.Response:
        if self.napcat_manager is None:
            return _json_response({"success": False, "message": "NapCat 管理未启用"}, 400)
        try:
            body = await request.json()
        except Exception:
            return _json_response({"error": "Invalid JSON"}, 400)
        qq = str(body.get("qq", "")).strip()
        if not qq:
            return _json_response({"success": False, "message": "QQ 号不能为空"}, 400)
        result = self.napcat_manager.configure(
            qq_number=qq,
            ws_port=int(body.get("ws_port", 3001)),
            ws_token=str(body.get("ws_token", "napcat_ws")),
        )
        return _json_response(result)

    async def api_napcat_start(self, request: web.Request) -> web.Response:
        if self.napcat_manager is None:
            return _json_response({"success": False, "message": "NapCat 管理未启用"}, 400)
        try:
            body = await request.json()
        except Exception:
            body = {}
        qq = str(body.get("qq", "")).strip() or None
        result = await self.napcat_manager.start(qq_number=qq)
        return _json_response(result)

    async def api_napcat_stop(self, request: web.Request) -> web.Response:
        if self.napcat_manager is None:
            return _json_response({"success": False, "message": "NapCat 管理未启用"}, 400)
        result = await self.napcat_manager.stop()
        return _json_response(result)

    async def api_napcat_logs(self, request: web.Request) -> web.Response:
        if self.napcat_manager is None:
            return _json_response({"enabled": False, "logs": []})
        lines = int(request.query.get("lines", "100"))
        return _json_response({
            "enabled": True,
            "logs": self.napcat_manager.get_logs(lines=lines),
        })
