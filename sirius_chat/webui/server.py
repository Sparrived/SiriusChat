"""SiriusChat WebUI — 基于 aiohttp 的多人格配置管理面板。

提供 REST API + 内嵌前端页面，用于：
- 多个人格的列表、状态、启停管理
- 每人格的 Provider / 人格 / 模型编排 / Adapter / Experience 配置
- 全局 NapCat 管理
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
from sirius_chat.models.persona import PersonaProfile
from sirius_chat.persona_config import PersonaAdaptersConfig, PersonaConfigPaths, PersonaExperienceConfig
from sirius_chat.providers.routing import WorkspaceProviderManager
from sirius_chat.platforms.persona_utils import generate_persona_from_interview

LOG = logging.getLogger("sirius.webui")


def _json_response(data: dict[str, Any], status: int = 200) -> web.Response:
    return web.json_response(data, status=status, dumps=lambda o: json.dumps(o, ensure_ascii=False, indent=2))


def _get_name(request: web.Request) -> str:
    """从 URL 路径参数获取人格名称。"""
    return str(request.match_info.get("name", "")).strip()


class WebUIServer:
    """轻量级 aiohttp WebUI 服务器（多人格版本）。"""

    def __init__(
        self,
        persona_manager: Any,
        host: str = "0.0.0.0",
        port: int = 8080,
        napcat_install_dir: str | Path | None = None,
    ) -> None:
        self.persona_manager = persona_manager
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
        static_dir = Path(__file__).parent / "static"
        self.app.router.add_static("/static/", static_dir)

        # ─── 全局 API ─────────────────────────────────────────
        self.app.router.add_get("/api/providers", self.api_providers_get)
        self.app.router.add_post("/api/providers", self.api_providers_post)
        self.app.router.add_get("/api/napcat/status", self.api_napcat_status)
        self.app.router.add_post("/api/napcat/install", self.api_napcat_install)
        self.app.router.add_post("/api/napcat/configure", self.api_napcat_configure)
        self.app.router.add_post("/api/napcat/start", self.api_napcat_start)
        self.app.router.add_post("/api/napcat/stop", self.api_napcat_stop)
        self.app.router.add_get("/api/napcat/logs", self.api_napcat_logs)

        # ─── 多人格 API ───────────────────────────────────────
        self.app.router.add_get("/api/personas", self.api_personas_list)
        self.app.router.add_get("/api/personas/{name}", self.api_persona_status)
        self.app.router.add_post("/api/personas/{name}/start", self.api_persona_start)
        self.app.router.add_post("/api/personas/{name}/stop", self.api_persona_stop)
        self.app.router.add_delete("/api/personas/{name}", self.api_persona_delete)

        # 人格配置
        self.app.router.add_get("/api/personas/{name}/persona", self.api_persona_get)
        self.app.router.add_post("/api/personas/{name}/persona/save", self.api_persona_save)
        self.app.router.add_post("/api/personas/{name}/persona/keywords", self.api_persona_keywords)
        self.app.router.add_post("/api/personas/{name}/persona/interview", self.api_persona_interview)

        # 模型编排
        self.app.router.add_get("/api/personas/{name}/orchestration", self.api_orchestration_get)
        self.app.router.add_post("/api/personas/{name}/orchestration", self.api_orchestration_post)

        # Adapter 配置
        self.app.router.add_get("/api/personas/{name}/adapters", self.api_adapters_get)
        self.app.router.add_post("/api/personas/{name}/adapters", self.api_adapters_post)

        # Experience 配置
        self.app.router.add_get("/api/personas/{name}/experience", self.api_experience_get)
        self.app.router.add_post("/api/personas/{name}/experience", self.api_experience_post)

        # 引擎操作
        self.app.router.add_post("/api/personas/{name}/engine/toggle", self.api_engine_toggle)
        self.app.router.add_post("/api/personas/{name}/engine/reload", self.api_engine_reload)

        # 桥接配置（写入 adapters.json）
        self.app.router.add_post("/api/personas/{name}/config", self.api_config_post)

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
        html_path = Path(__file__).parent / "static" / "index.html"
        if html_path.exists():
            return web.FileResponse(html_path)
        return web.Response(text="WebUI not found", status=404)

    # ─── 全局 API: Provider ───────────────────────────────

    async def api_providers_get(self, request: web.Request) -> web.Response:
        provider_mgr = WorkspaceProviderManager(self.persona_manager.data_path)
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
        provider_mgr = WorkspaceProviderManager(self.persona_manager.data_path)
        try:
            provider_mgr.save_from_entries(entries)
            LOG.info("Provider 配置已保存 %d 条", len(entries))
            return _json_response({"success": True, "message": "Provider 已保存"})
        except Exception as exc:
            LOG.exception("保存 Provider 失败")
            return _json_response({"error": str(exc)}, 500)

    # ─── 多人格 API: 列表与状态 ───────────────────────────

    async def api_personas_list(self, request: web.Request) -> web.Response:
        return _json_response({"personas": self.persona_manager.list_personas()})

    async def api_persona_status(self, request: web.Request) -> web.Response:
        name = _get_name(request)
        info = self.persona_manager.get_persona_status(name)
        if info is None:
            return _json_response({"error": f"人格不存在: {name}"}, 404)
        return _json_response(info)

    async def api_persona_start(self, request: web.Request) -> web.Response:
        name = _get_name(request)
        ok = self.persona_manager.start_persona(name)
        return _json_response({"success": ok, "name": name})

    async def api_persona_stop(self, request: web.Request) -> web.Response:
        name = _get_name(request)
        ok = self.persona_manager.stop_persona(name)
        return _json_response({"success": ok, "name": name})

    async def api_persona_delete(self, request: web.Request) -> web.Response:
        name = _get_name(request)
        ok = self.persona_manager.remove_persona(name)
        return _json_response({"success": ok, "name": name})

    # ─── 多人格 API: 人格配置 ─────────────────────────────

    async def api_persona_get(self, request: web.Request) -> web.Response:
        name = _get_name(request)
        pdir = self.persona_manager.get_persona_dir(name)
        persona = PersonaStore.load(pdir)
        if persona is None:
            return _json_response({"persona": None})
        return _json_response({"persona": asdict(persona)})

    async def api_persona_save(self, request: web.Request) -> web.Response:
        name = _get_name(request)
        try:
            body = await request.json()
        except Exception:
            return _json_response({"error": "Invalid JSON"}, 400)
        raw = body.get("persona")
        if not isinstance(raw, dict):
            return _json_response({"error": "persona must be an object"}, 400)
        try:
            persona = PersonaProfile(**raw)
            pdir = self.persona_manager.get_persona_dir(name)
            PersonaStore.save(pdir, persona)
            self.persona_manager.reload_persona(name)
            return _json_response({"success": True, "message": f"人格「{persona.name}」已保存"})
        except Exception as exc:
            LOG.exception("保存人格失败")
            return _json_response({"error": str(exc)}, 500)

    async def api_persona_keywords(self, request: web.Request) -> web.Response:
        name = _get_name(request)
        try:
            body = await request.json()
        except Exception:
            return _json_response({"error": "Invalid JSON"}, 400)
        p_name = str(body.get("name", "小星")).strip()
        keywords = [k.strip() for k in str(body.get("keywords", "")).split() if k.strip()]
        aliases = [a.strip() for a in body.get("aliases", []) if isinstance(a, str) and a.strip()]
        try:
            persona = PersonaGenerator.from_keywords(p_name, keywords)
            persona.aliases = aliases
            return _json_response({"success": True, "persona": asdict(persona)})
        except Exception as exc:
            LOG.exception("关键词人格生成失败")
            return _json_response({"error": str(exc)}, 500)

    async def api_persona_interview(self, request: web.Request) -> web.Response:
        name = _get_name(request)
        try:
            body = await request.json()
        except Exception:
            return _json_response({"error": "Invalid JSON"}, 400)
        p_name = str(body.get("name", "小星")).strip()
        answers = body.get("answers", {})
        aliases = [a.strip() for a in body.get("aliases", []) if isinstance(a, str) and a.strip()]
        pdir = self.persona_manager.get_persona_dir(name)
        # 使用全局 provider 配置
        provider_mgr = WorkspaceProviderManager(self.persona_manager.data_path)
        providers = provider_mgr.load()
        provider = None
        if providers:
            from sirius_chat.providers.routing import AutoRoutingProvider
            provider = AutoRoutingProvider(providers)
        try:
            persona = await generate_persona_from_interview(
                work_path=pdir,
                provider=provider,
                name=p_name,
                answers=answers,
                aliases=aliases,
            )
            return _json_response({"success": True, "persona": asdict(persona)})
        except Exception as exc:
            LOG.exception("问卷人格生成失败")
            return _json_response({"error": str(exc)}, 500)

    # ─── 多人格 API: 模型编排 ─────────────────────────────

    async def api_orchestration_get(self, request: web.Request) -> web.Response:
        name = _get_name(request)
        pdir = self.persona_manager.get_persona_dir(name)
        orch = OrchestrationStore.load(pdir)
        # 聚合全局 Provider 中所有可用模型
        available_models: list[str] = []
        try:
            provider_mgr = WorkspaceProviderManager(self.persona_manager.data_path)
            for cfg in provider_mgr.load().values():
                if cfg.enabled:
                    available_models.extend(cfg.models)
            available_models = sorted(set(available_models))
        except Exception:
            pass
        return _json_response({
            "analysis_model": orch.get("analysis_model", "gpt-4o-mini"),
            "chat_model": orch.get("chat_model", "gpt-4o"),
            "vision_model": orch.get("vision_model", "gpt-4o"),
            "available_models": available_models,
        })

    async def api_orchestration_post(self, request: web.Request) -> web.Response:
        name = _get_name(request)
        try:
            body = await request.json()
        except Exception:
            return _json_response({"error": "Invalid JSON"}, 400)
        pdir = self.persona_manager.get_persona_dir(name)
        orch = OrchestrationStore.load(pdir)
        for key in ("analysis_model", "chat_model", "vision_model"):
            if key in body:
                orch[key] = str(body[key]).strip()
        OrchestrationStore.save(pdir, orch)
        self.persona_manager.reload_persona(name)
        LOG.info("模型编排已更新 %s: %s", name, orch)
        return _json_response({"success": True, "message": "模型编排已保存，引擎将重载"})

    # ─── 多人格 API: Adapter 配置 ─────────────────────────

    async def api_adapters_get(self, request: web.Request) -> web.Response:
        name = _get_name(request)
        paths = self.persona_manager.get_persona_paths(name)
        if paths is None:
            return _json_response({"error": "人格不存在"}, 404)
        adapters = PersonaAdaptersConfig.load(paths.adapters)
        return _json_response({"adapters": [a.to_dict() for a in adapters.adapters]})

    async def api_adapters_post(self, request: web.Request) -> web.Response:
        name = _get_name(request)
        try:
            body = await request.json()
        except Exception:
            return _json_response({"error": "Invalid JSON"}, 400)
        raw = body.get("adapters")
        if not isinstance(raw, list):
            return _json_response({"error": "adapters must be a list"}, 400)
        paths = self.persona_manager.get_persona_paths(name)
        if paths is None:
            return _json_response({"error": "人格不存在"}, 404)
        adapters = PersonaAdaptersConfig.from_dict({"adapters": raw})
        adapters.save(paths.adapters)
        return _json_response({"success": True, "message": "Adapter 配置已保存"})

    # ─── 多人格 API: Experience 配置 ──────────────────────

    async def api_experience_get(self, request: web.Request) -> web.Response:
        name = _get_name(request)
        paths = self.persona_manager.get_persona_paths(name)
        if paths is None:
            return _json_response({"error": "人格不存在"}, 404)
        experience = PersonaExperienceConfig.load(paths.experience)
        return _json_response({"experience": experience.to_dict()})

    async def api_experience_post(self, request: web.Request) -> web.Response:
        name = _get_name(request)
        try:
            body = await request.json()
        except Exception:
            return _json_response({"error": "Invalid JSON"}, 400)
        raw = body.get("experience")
        if not isinstance(raw, dict):
            return _json_response({"error": "experience must be an object"}, 400)
        paths = self.persona_manager.get_persona_paths(name)
        if paths is None:
            return _json_response({"error": "人格不存在"}, 404)
        experience = PersonaExperienceConfig.from_dict(raw)
        experience.save(paths.experience)
        return _json_response({"success": True, "message": "体验参数已保存"})

    # ─── 多人格 API: 引擎操作 ─────────────────────────────

    async def api_engine_toggle(self, request: web.Request) -> web.Response:
        """通过写入 enabled 标志文件，通知子进程切换状态。"""
        name = _get_name(request)
        try:
            body = await request.json()
        except Exception:
            body = {}
        enabled = bool(body.get("enabled", True))
        pdir = self.persona_manager.get_persona_dir(name)
        flag = pdir / "engine_state" / "enabled"
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text("1" if enabled else "0", encoding="utf-8")
        return _json_response({"success": True, "enabled": enabled})

    async def api_engine_reload(self, request: web.Request) -> web.Response:
        """通过写入 reload 标志文件，通知子进程重载。"""
        name = _get_name(request)
        ok = self.persona_manager.reload_persona(name)
        return _json_response({"success": ok, "message": "重载请求已发送" if ok else "发送失败"})

    # ─── 多人格 API: 桥接配置 ─────────────────────────────

    async def api_config_post(self, request: web.Request) -> web.Response:
        """更新 adapter 配置（群白名单等），直接写入 adapters.json。"""
        name = _get_name(request)
        try:
            body = await request.json()
        except Exception:
            return _json_response({"error": "Invalid JSON"}, 400)
        paths = self.persona_manager.get_persona_paths(name)
        if paths is None:
            return _json_response({"error": "人格不存在"}, 404)

        adapters = PersonaAdaptersConfig.load(paths.adapters)
        if not adapters.adapters:
            return _json_response({"error": "无 adapter 可配置"}, 400)

        # 只更新第一个 napcat adapter
        for key in ("allowed_group_ids", "allowed_private_user_ids", "enable_group_chat", "enable_private_chat", "root"):
            if key in body and adapters.adapters:
                setattr(adapters.adapters[0], key, body[key])

        adapters.save(paths.adapters)
        LOG.info("配置已更新 %s: %s", name, {k: body.get(k) for k in body})
        return _json_response({"success": True, "message": "配置已保存"})

    # ─── NapCat 管理 ──────────────────────────────────────

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
