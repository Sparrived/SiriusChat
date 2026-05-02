"""SiriusChat WebUI — 基于 aiohttp 的多人格配置管理面板。

提供 REST API + 内嵌前端页面，用于：
- 多个人格的列表、状态、启停管理
- 每人格的 Provider / 人格 / 模型编排 / Adapter / Experience 配置
- 全局 NapCat 管理
- 每人格的 Skill 启停与配置管理
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
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
from sirius_chat.platforms.napcat_manager import NapCatManager
from sirius_chat.platforms.persona_utils import generate_persona_from_interview
from sirius_chat.webui.server_skill_api import (
    api_persona_skills_get,
    api_persona_skill_toggle,
    api_persona_skill_config_get,
    api_persona_skill_config_post,
)

LOG = logging.getLogger("sirius.webui")


def _json_response(data: dict[str, Any], status: int = 200) -> web.Response:
    return web.json_response(data, status=status, dumps=lambda o: json.dumps(o, ensure_ascii=False, indent=2))


def _get_name(request: web.Request) -> str:
    """从 URL 路径参数获取人格名称。"""
    return str(request.match_info.get("name", "")).strip()


@web.middleware
async def _no_cache_middleware(request: web.Request, handler: Any) -> web.StreamResponse:
    """为静态文件禁用浏览器缓存。"""
    response = await handler(request)
    if request.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


class WebUIServer:
    """aiohttp WebUI 服务器。"""

    def __init__(
        self,
        persona_manager: Any,
        host: str = "0.0.0.0",
        port: int = 8080,
        napcat_manager: NapCatManager | None = None,
    ) -> None:
        self.persona_manager = persona_manager
        self.host = host
        self.port = port
        self.napcat_manager = napcat_manager
        self.app = web.Application(middlewares=[_no_cache_middleware])
        self.runner: web.AppRunner | None = None
        self.site: web.TCPSite | None = None
        self._setup_routes()

    def _setup_routes(self) -> None:
        self.app.router.add_get("/", self.index)
        self.app.router.add_static("/static/", Path(__file__).parent / "static", show_index=False)

        # 全局 API
        self.app.router.add_get("/api/global-config", self.api_global_config_get)
        self.app.router.add_post("/api/global-config", self.api_global_config_post)
        self.app.router.add_get("/api/providers", self.api_providers_get)
        self.app.router.add_post("/api/providers", self.api_providers_post)
        self.app.router.add_get("/api/napcat/status", self.api_napcat_status)
        self.app.router.add_post("/api/napcat/install", self.api_napcat_install)
        self.app.router.add_post("/api/napcat/configure", self.api_napcat_configure)

        # 多人格 API: 列表 / 创建 / 删除 / 状态
        self.app.router.add_get("/api/personas", self.api_personas_get)
        self.app.router.add_post("/api/personas", self.api_personas_post)
        self.app.router.add_delete("/api/personas/{name}", self.api_personas_delete)
        self.app.router.add_get("/api/personas/{name}/status", self.api_persona_status_get)
        self.app.router.add_post("/api/personas/{name}/start", self.api_persona_start)
        self.app.router.add_post("/api/personas/{name}/stop", self.api_persona_stop)
        self.app.router.add_post("/api/personas/{name}/restart", self.api_persona_restart)

        # 多人格 API: 配置
        self.app.router.add_get("/api/personas/{name}/persona", self.api_persona_get)
        self.app.router.add_post("/api/personas/{name}/persona", self.api_persona_post)
        self.app.router.add_get("/api/personas/{name}/orchestration", self.api_orchestration_get)
        self.app.router.add_post("/api/personas/{name}/orchestration", self.api_orchestration_post)
        self.app.router.add_get("/api/personas/{name}/experience", self.api_experience_get)
        self.app.router.add_post("/api/personas/{name}/experience", self.api_experience_post)
        self.app.router.add_get("/api/personas/{name}/adapters", self.api_adapters_get)
        self.app.router.add_post("/api/personas/{name}/adapters", self.api_adapters_post)

        # 多人格 API: 引擎控制
        self.app.router.add_post("/api/personas/{name}/engine/reload", self.api_engine_reload)

        # Token usage (per persona)
        self.app.router.add_get("/api/personas/{name}/tokens", self.api_persona_tokens_get)

        # Cognition events (per persona)
        self.app.router.add_get("/api/personas/{name}/cognition", self.api_persona_cognition_get)

        # Diary entries (per persona)
        self.app.router.add_get("/api/personas/{name}/diary", self.api_persona_diary_get)
        self.app.router.add_get("/api/personas/{name}/vector-store-status", self.api_persona_vector_store_status_get)

        # User semantic profiles (per persona)
        self.app.router.add_get("/api/personas/{name}/users", self.api_persona_users_get)
        self.app.router.add_get("/api/personas/{name}/users/{user_id}", self.api_persona_user_get)

        # 桥接配置（写入 adapters.json）
        self.app.router.add_post("/api/personas/{name}/config", self.api_config_post)

        # Skill 管理（每人格独立）
        self.app.router.add_get("/api/personas/{name}/skills", self.api_persona_skills_get)
        self.app.router.add_post("/api/personas/{name}/skills/{skill_name}/toggle", self.api_persona_skill_toggle)
        self.app.router.add_get("/api/personas/{name}/skills/{skill_name}/config", self.api_persona_skill_config_get)
        self.app.router.add_post("/api/personas/{name}/skills/{skill_name}/config", self.api_persona_skill_config_post)

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

    # ─── 全局 API: 全局配置 ───────────────────────────────

    def _global_config_path(self) -> Path:
        return Path(self.persona_manager.data_path) / "global_config.json"

    async def api_global_config_get(self, request: web.Request) -> web.Response:
        path = self._global_config_path()
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                return _json_response(data)
            except Exception:
                pass
        return _json_response({
            "webui_host": self.host,
            "webui_port": self.port,
            "auto_manage_napcat": True,
            "log_level": "INFO",
        })

    async def api_global_config_post(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return _json_response({"error": "Invalid JSON"}, 400)

        path = self._global_config_path()
        data: dict[str, Any] = {}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass

        for key in ("webui_host", "webui_port", "auto_manage_napcat", "log_level"):
            if key in body:
                data[key] = body[key]

        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        return _json_response({"success": True})

    # ─── 全局 API: Provider 配置 ──────────────────────────

    def _provider_keys_path(self) -> Path:
        return Path(self.persona_manager.data_path) / "providers" / "provider_keys.json"

    async def api_providers_get(self, request: web.Request) -> web.Response:
        path = self._provider_keys_path()
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                # 脱敏：隐藏真实 key，只保留前 4 位
                masked: dict[str, Any] = {}
                for k, v in data.items():
                    if isinstance(v, dict) and "api_key" in v:
                        key = v["api_key"]
                        masked[k] = {
                            **v,
                            "api_key": key[:4] + "****" if len(key) > 4 else "****",
                        }
                    else:
                        masked[k] = v
                return _json_response(masked)
            except Exception:
                pass
        return _json_response({})

    async def api_providers_post(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return _json_response({"error": "Invalid JSON"}, 400)

        path = self._provider_keys_path()
        data: dict[str, Any] = {}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass

        for provider, cfg in body.items():
            if isinstance(cfg, dict):
                if provider not in data:
                    data[provider] = {}
                for k, v in cfg.items():
                    # 如果前端传的是脱敏值，不覆盖原值
                    if k == "api_key" and isinstance(v, str) and "****" in v:
                        continue
                    data[provider][k] = v

        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        return _json_response({"success": True})

    # ─── 多人格 API: 列表 / 创建 / 删除 ───────────────────

    async def api_personas_get(self, request: web.Request) -> web.Response:
        personas = self.persona_manager.list_personas()
        result = []
        for p in personas:
            paths = self.persona_manager.get_persona_paths(p["name"])
            status = {"running": False, "pid": None}
            if paths is not None:
                status_path = paths.engine_state / "worker_status.json"
                if status_path.exists():
                    try:
                        st = json.loads(status_path.read_text(encoding="utf-8"))
                        status = {
                            "running": st.get("running", False),
                            "pid": st.get("pid"),
                            "started_at": st.get("started_at"),
                        }
                    except Exception:
                        pass
            result.append({**p, "status": status})
        return _json_response({"personas": result})

    async def api_personas_post(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return _json_response({"error": "Invalid JSON"}, 400)

        name = str(body.get("name", "")).strip()
        if not name:
            return _json_response({"error": "缺少 name"}, 400)

        # 禁止特殊字符
        if not name.replace("_", "").replace("-", "").isalnum():
            return _json_response({"error": "name 只能包含字母、数字、下划线和连字符"}, 400)

        try:
            self.persona_manager.create_persona(name)
            return _json_response({"success": True, "name": name})
        except Exception as exc:
            LOG.warning("创建人格失败 %s: %s", name, exc)
            return _json_response({"error": str(exc)}, 500)

    async def api_personas_delete(self, request: web.Request) -> web.Response:
        name = _get_name(request)
        try:
            self.persona_manager.delete_persona(name)
            return _json_response({"success": True})
        except Exception as exc:
            LOG.warning("删除人格失败 %s: %s", name, exc)
            return _json_response({"error": str(exc)}, 500)

    # ─── 多人格 API: 状态 / 启停 ──────────────────────────

    async def api_persona_status_get(self, request: web.Request) -> web.Response:
        name = _get_name(request)
        paths = self.persona_manager.get_persona_paths(name)
        if paths is None:
            return _json_response({"error": "人格不存在"}, 404)

        status = {"running": False, "pid": None}
        status_path = paths.engine_state / "worker_status.json"
        if status_path.exists():
            try:
                st = json.loads(status_path.read_text(encoding="utf-8"))
                status = {
                    "running": st.get("running", False),
                    "pid": st.get("pid"),
                    "started_at": st.get("started_at"),
                    "last_heartbeat": st.get("last_heartbeat"),
                }
            except Exception:
                pass
        return _json_response({"name": name, "status": status})

    async def api_persona_start(self, request: web.Request) -> web.Response:
        name = _get_name(request)
        try:
            self.persona_manager.start_persona(name)
            return _json_response({"success": True, "message": f"{name} 已启动"})
        except Exception as exc:
            LOG.warning("启动人格失败 %s: %s", name, exc)
            return _json_response({"error": str(exc)}, 500)

    async def api_persona_stop(self, request: web.Request) -> web.Response:
        name = _get_name(request)
        try:
            self.persona_manager.stop_persona(name)
            return _json_response({"success": True, "message": f"{name} 已停止"})
        except Exception as exc:
            LOG.warning("停止人格失败 %s: %s", name, exc)
            return _json_response({"error": str(exc)}, 500)

    async def api_persona_restart(self, request: web.Request) -> web.Response:
        name = _get_name(request)
        try:
            self.persona_manager.stop_persona(name)
            await asyncio.sleep(1)
            self.persona_manager.start_persona(name)
            return _json_response({"success": True, "message": f"{name} 已重启"})
        except Exception as exc:
            LOG.warning("重启人格失败 %s: %s", name, exc)
            return _json_response({"error": str(exc)}, 500)

    # ─── 多人格 API: 人格配置 ─────────────────────────────

    async def api_persona_get(self, request: web.Request) -> web.Response:
        name = _get_name(request)
        paths = self.persona_manager.get_persona_paths(name)
        if paths is None:
            return _json_response({"error": "人格不存在"}, 404)

        profile = PersonaStore.load(paths.persona)
        if profile is None:
            profile = PersonaProfile(name=name)
        return _json_response({
            "name": profile.name,
            "display_name": profile.display_name,
            "description": profile.description,
            "system_prompt": profile.system_prompt,
            "traits": profile.traits,
            "speech_style": profile.speech_style,
            "scenario": profile.scenario,
            "relationship": profile.relationship,
            "knowledge": profile.knowledge,
            "emotions": profile.emotions,
            "rules": profile.rules,
            "avatar": profile.avatar,
            "version": profile.version,
        })

    async def api_persona_post(self, request: web.Request) -> web.Response:
        name = _get_name(request)
        try:
            body = await request.json()
        except Exception:
            return _json_response({"error": "Invalid JSON"}, 400)

        paths = self.persona_manager.get_persona_paths(name)
        if paths is None:
            return _json_response({"error": "人格不存在"}, 404)

        profile = PersonaStore.load(paths.persona)
        if profile is None:
            profile = PersonaProfile(name=name)

        for key in (
            "display_name", "description", "system_prompt", "traits",
            "speech_style", "scenario", "relationship", "knowledge",
            "emotions", "rules", "avatar",
        ):
            if key in body:
                setattr(profile, key, body[key])

        PersonaStore.save(paths.persona, profile)
        return _json_response({"success": True})

    # ─── 多人格 API: 模型编排 ─────────────────────────────

    async def api_orchestration_get(self, request: web.Request) -> web.Response:
        name = _get_name(request)
        paths = self.persona_manager.get_persona_paths(name)
        if paths is None:
            return _json_response({"error": "人格不存在"}, 404)

        data = OrchestrationStore.load(paths.orchestration)
        return _json_response(data)

    async def api_orchestration_post(self, request: web.Request) -> web.Response:
        name = _get_name(request)
        try:
            body = await request.json()
        except Exception:
            return _json_response({"error": "Invalid JSON"}, 400)

        paths = self.persona_manager.get_persona_paths(name)
        if paths is None:
            return _json_response({"error": "人格不存在"}, 404)

        cfg = OrchestrationStore.load(paths.orchestration)

        for key in ("analysis_model", "chat_model", "vision_model", "summary_model"):
            if key in body:
                cfg[key] = body[key]

        OrchestrationStore.save(paths.orchestration, cfg)
        return _json_response({"success": True})

    # ─── 多人格 API: 体验配置 ─────────────────────────────

    async def api_experience_get(self, request: web.Request) -> web.Response:
        name = _get_name(request)
        paths = self.persona_manager.get_persona_paths(name)
        if paths is None:
            return _json_response({"error": "人格不存在"}, 404)

        exp = PersonaExperienceConfig.load(paths.experience)
        return _json_response({
            "reply_mode": exp.reply_mode,
            "engagement_sensitivity": exp.engagement_sensitivity,
            "expressiveness": exp.expressiveness,
            "heat_window_seconds": exp.heat_window_seconds,
            "proactive_enabled": exp.proactive_enabled,
            "proactive_interval_seconds": exp.proactive_interval_seconds,
            "proactive_active_start_hour": exp.proactive_active_start_hour,
            "proactive_active_end_hour": exp.proactive_active_end_hour,
            "delay_reply_enabled": exp.delay_reply_enabled,
            "pending_message_threshold": exp.pending_message_threshold,
            "min_reply_interval_seconds": exp.min_reply_interval_seconds,
            "reply_frequency_window_seconds": exp.reply_frequency_window_seconds,
            "reply_frequency_max_replies": exp.reply_frequency_max_replies,
            "reply_frequency_exempt_on_mention": exp.reply_frequency_exempt_on_mention,
            "max_concurrent_llm_calls": exp.max_concurrent_llm_calls,
            "memory_depth": exp.memory_depth,
            "basic_memory_hard_limit": exp.basic_memory_hard_limit,
            "basic_memory_context_window": exp.basic_memory_context_window,
            "diary_top_k": exp.diary_top_k,
            "diary_token_budget": exp.diary_token_budget,
            "enable_skills": exp.enable_skills,
            "max_skill_rounds": exp.max_skill_rounds,
            "skill_execution_timeout": exp.skill_execution_timeout,
            "auto_install_skill_deps": exp.auto_install_skill_deps,
            "other_ai_names": exp.other_ai_names,
        })

    async def api_experience_post(self, request: web.Request) -> web.Response:
        name = _get_name(request)
        try:
            body = await request.json()
        except Exception:
            return _json_response({"error": "Invalid JSON"}, 400)

        paths = self.persona_manager.get_persona_paths(name)
        if paths is None:
            return _json_response({"error": "人格不存在"}, 404)

        exp = PersonaExperienceConfig.load(paths.experience)

        for key in (
            "reply_mode", "engagement_sensitivity", "expressiveness", "heat_window_seconds",
            "proactive_enabled", "proactive_interval_seconds", "proactive_active_start_hour",
            "proactive_active_end_hour", "delay_reply_enabled", "pending_message_threshold",
            "min_reply_interval_seconds", "reply_frequency_window_seconds",
            "reply_frequency_max_replies", "reply_frequency_exempt_on_mention",
            "max_concurrent_llm_calls", "memory_depth", "basic_memory_hard_limit",
            "basic_memory_context_window", "diary_top_k", "diary_token_budget",
            "enable_skills", "max_skill_rounds", "skill_execution_timeout",
            "auto_install_skill_deps", "other_ai_names",
        ):
            if key in body:
                setattr(exp, key, body[key])

        exp.save(paths.experience)
        return _json_response({"success": True})

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

        paths = self.persona_manager.get_persona_paths(name)
        if paths is None:
            return _json_response({"error": "人格不存在"}, 404)

        adapters = PersonaAdaptersConfig.load(paths.adapters)
        if "adapters" in body and isinstance(body["adapters"], list):
            adapters.adapters = [PersonaAdaptersConfig.Adapter(**a) for a in body["adapters"]]

        adapters.save(paths.adapters)
        return _json_response({"success": True})

    # ─── 多人格 API: 引擎重载 ─────────────────────────────

    async def api_engine_reload(self, request: web.Request) -> web.Response:
        name = _get_name(request)
        paths = self.persona_manager.get_persona_paths(name)
        if paths is None:
            return _json_response({"error": "人格不存在"}, 404)

        # 向 worker 发送重载信号（通过 engine_state/reload.flag）
        flag_path = paths.engine_state / "reload.flag"
        flag_path.parent.mkdir(parents=True, exist_ok=True)
        flag_path.write_text("1", encoding="utf-8")
        return _json_response({"success": True, "message": "重载信号已发送"})

    # ─── 多人格 API: Token 使用统计 ───────────────────────

    async def api_persona_tokens_get(self, request: web.Request) -> web.Response:
        name = _get_name(request)
        paths = self.persona_manager.get_persona_paths(name)
        if paths is None:
            return _json_response({"error": "人格不存在"}, 404)

        from sirius_chat.token.store import TokenUsageStore
        from sirius_chat.token import analytics as token_analytics

        db_path = paths.dir / "token_usage.db"
        if not db_path.exists():
            return _json_response({"total": 0, "daily": [], "models": []})

        try:
            store = TokenUsageStore(str(db_path))
            baseline = token_analytics.compute_baseline(store)
            by_model = token_analytics.group_by_model(store)
            time_series = token_analytics.time_series(store, bucket_seconds=86400)

            daily = [
                {
                    "date": ts["time_bucket"][:10],
                    "calls": ts["calls"],
                    "prompt_tokens": ts["prompt_tokens"],
                    "completion_tokens": ts["completion_tokens"],
                    "total_tokens": ts["total_tokens"],
                }
                for ts in time_series[-30:]
            ]

            models = [
                {"model": m, **v}
                for m, v in by_model.items()
            ]

            return _json_response({
                "total": baseline["total_tokens"],
                "calls": baseline["total_calls"],
                "daily": daily,
                "models": models,
            })
        except Exception as exc:
            LOG.warning("读取 Token 统计失败 %s: %s", name, exc)
            return _json_response({"error": str(exc)}, 500)

    # ─── 多人格 API: 认知事件 ─────────────────────────────

    async def api_persona_cognition_get(self, request: web.Request) -> web.Response:
        name = _get_name(request)
        paths = self.persona_manager.get_persona_paths(name)
        if paths is None:
            return _json_response({"error": "人格不存在"}, 404)

        events_path = paths.engine_state / "cognition_events.json"
        if not events_path.exists():
            return _json_response({"events": []})

        try:
            data = json.loads(events_path.read_text(encoding="utf-8"))
            events = data if isinstance(data, list) else []
            limit = int(request.query.get("limit", "50"))
            return _json_response({"events": events[-limit:]})
        except Exception as exc:
            LOG.warning("读取认知事件失败 %s: %s", name, exc)
            return _json_response({"error": str(exc)}, 500)

    # ─── 多人格 API: 日记 ─────────────────────────────────

    async def api_persona_diary_get(self, request: web.Request) -> web.Response:
        name = _get_name(request)
        paths = self.persona_manager.get_persona_paths(name)
        if paths is None:
            return _json_response({"error": "人格不存在"}, 404)

        diary_dir = paths.dir / "diary"
        if not diary_dir.exists():
            return _json_response({"entries": [], "stats": {}, "groups": []})

        try:
            limit = int(request.query.get("limit", "50"))
            group_id = request.query.get("group_id", "")

            entries: list[dict[str, Any]] = []
            groups: set[str] = set()
            keyword_counts: dict[str, int] = {}

            for path in diary_dir.glob("*.json"):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    g_id = data.get("group_id", "")
                    if g_id:
                        groups.add(g_id)
                    if group_id and g_id != group_id:
                        continue
                    for item in data.get("entries", []):
                        if isinstance(item, dict):
                            entries.append(item)
                            for kw in item.get("keywords", []):
                                keyword_counts[kw] = keyword_counts.get(kw, 0) + 1
                except (OSError, json.JSONDecodeError):
                    continue

            entries.sort(key=lambda e: e.get("created_at", ""), reverse=True)
            entries = entries[:limit]

            stats = {
                "total": len(entries),
                "groups": len(groups),
                "top_keywords": sorted(keyword_counts.items(), key=lambda x: x[1], reverse=True)[:20],
            }

            return _json_response({
                "entries": entries,
                "stats": stats,
                "groups": sorted(groups),
            })
        except Exception as exc:
            LOG.warning("读取日记失败 %s: %s", name, exc)
            return _json_response({"error": str(exc)}, 500)

    # ─── 多人格 API: 向量存储状态 ─────────────────────────

    async def api_persona_vector_store_status_get(self, request: web.Request) -> web.Response:
        name = _get_name(request)
        paths = self.persona_manager.get_persona_paths(name)
        if paths is None:
            return _json_response({"error": "人格不存在"}, 404)

        from sirius_chat.memory.diary.vector_store import DiaryVectorStore

        vector_db_dir = paths.dir / "diary" / "vector_db"
        try:
            vs = DiaryVectorStore(vector_db_dir)
            stats = vs.get_stats()
            return _json_response(stats)
        except Exception as exc:
            LOG.warning("读取向量存储状态失败 %s: %s", name, exc)
            return _json_response({
                "available": False,
                "total_entries": 0,
                "groups": [],
                "model": DiaryVectorStore.MODEL_NAME,
                "error": str(exc),
            })

    # ─── 多人格 API: 用户画像 ─────────────────────────────

    async def api_persona_users_get(self, request: web.Request) -> web.Response:
        """Return user semantic profiles for a single persona."""
        from sirius_chat.memory.semantic.store import SemanticProfileStore

        name = _get_name(request)
        paths = self.persona_manager.get_persona_paths(name)
        if paths is None:
            return _json_response({"error": "人格不存在"}, 404)

        # SemanticProfileStore expects persona_dir and appends memory/semantic itself
        semantic_base = paths.dir / "memory" / "semantic"
        if not semantic_base.exists():
            return _json_response({"users": [], "groups": []})

        try:
            group_id = request.query.get("group_id", "")
            store = SemanticProfileStore(paths.dir)

            users: list[dict[str, Any]] = []
            groups: set[str] = set()
            seen_user_ids: set[str] = set()

            # Collect available group IDs from directory structure
            users_dir = semantic_base / "users"
            if users_dir.exists():
                for g_dir in users_dir.iterdir():
                    if g_dir.is_dir():
                        groups.add(g_dir.name)

            if group_id:
                # Group-scoped query: only group-local users (no global fallback)
                user_dir = store._users_dir / store._safe_name(group_id)
                for profile in store.list_group_user_profiles(group_id):
                    if profile.user_id and profile.user_id not in seen_user_ids:
                        seen_user_ids.add(profile.user_id)
                        users.append(profile.to_dict())
            else:
                # Global query: group-local profiles first (they have real data),
                # then global profiles as fallback for users not seen in any group
                for g in groups:
                    for profile in store.list_group_user_profiles(g):
                        if profile.user_id and profile.user_id not in seen_user_ids:
                            seen_user_ids.add(profile.user_id)
                            users.append(profile.to_dict())
                for profile in store.list_global_user_profiles():
                    if profile.user_id and profile.user_id not in seen_user_ids:
                        seen_user_ids.add(profile.user_id)
                        users.append(profile.to_dict())

            return _json_response({"users": users, "groups": sorted(groups)})
        except Exception as exc:
            LOG.warning("读取用户画像失败 %s: %s", name, exc)
            return _json_response({"error": str(exc)}, 500)

    async def api_persona_user_get(self, request: web.Request) -> web.Response:
        """Return a single user semantic profile for a persona."""
        from sirius_chat.memory.semantic.store import SemanticProfileStore

        name = _get_name(request)
        user_id = str(request.match_info.get("user_id", "")).strip()
        if not user_id:
            return _json_response({"error": "缺少用户ID"}, 400)

        paths = self.persona_manager.get_persona_paths(name)
        if paths is None:
            return _json_response({"error": "人格不存在"}, 404)

        # SemanticProfileStore expects persona_dir and appends memory/semantic itself
        semantic_base = paths.dir / "memory" / "semantic"
        if not semantic_base.exists():
            return _json_response({"error": "用户不存在"}, 404)

        try:
            group_id = request.query.get("group_id", "")
            store = SemanticProfileStore(paths.dir)

            # Prefer global profile
            profile = store.load_global_user_profile(user_id)
            if profile is None and group_id:
                profile = store.load_user_profile(group_id, user_id)
            if profile is None:
                # Fallback: scan all groups
                users_dir = semantic_base / "users"
                if users_dir.exists():
                    for g_dir in users_dir.iterdir():
                        if g_dir.is_dir():
                            p = store.load_user_profile(g_dir.name, user_id)
                            if p is not None:
                                profile = p
                                break

            if profile is None:
                return _json_response({"error": "用户不存在"}, 404)

            return _json_response({"user": profile.to_dict()})
        except Exception as exc:
            LOG.warning("读取用户画像失败 %s/%s: %s", name, user_id, exc)
            return _json_response({"error": str(exc)}, 500)

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
        try:
            result = await self.napcat_manager.configure(
                qq_number=str(body.get("qq_number", "")),
                ws_port=int(body.get("ws_port", 3001)),
            )
            return _json_response(result)
        except Exception as exc:
            LOG.exception("NapCat 配置失败")
            return _json_response({"success": False, "message": str(exc)}, 500)

    # ─── Skill 管理 API 代理方法 ──────────────────────────
    # 这些方法将请求转发到 server_skill_api 模块，保持路由注册简洁

    async def api_persona_skills_get(self, request: web.Request) -> web.Response:
        return await api_persona_skills_get(request, self.persona_manager)

    async def api_persona_skill_toggle(self, request: web.Request) -> web.Response:
        return await api_persona_skill_toggle(request, self.persona_manager)

    async def api_persona_skill_config_get(self, request: web.Request) -> web.Response:
        return await api_persona_skill_config_get(request, self.persona_manager)

    async def api_persona_skill_config_post(self, request: web.Request) -> web.Response:
        return await api_persona_skill_config_post(request, self.persona_manager)
