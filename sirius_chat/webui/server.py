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
        self.app.router.add_get("/api/models", self.api_available_models_get)
        self.app.router.add_get("/api/napcat/status", self.api_napcat_status)
        self.app.router.add_post("/api/napcat/install", self.api_napcat_install)
        self.app.router.add_post("/api/napcat/configure", self.api_napcat_configure)
        self.app.router.add_post("/api/napcat/start", self.api_napcat_start)
        self.app.router.add_post("/api/napcat/stop", self.api_napcat_stop)
        self.app.router.add_get("/api/napcat/logs", self.api_napcat_logs)
        self.app.router.add_get("/api/tokens", self.api_tokens_get)
        self.app.router.add_get("/api/telemetry", self.api_telemetry_get)

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
        self.app.router.add_post("/api/personas/{name}/persona/save", self.api_persona_post)
        self.app.router.add_get("/api/personas/{name}/persona/interview", self.api_persona_interview_get)
        self.app.router.add_post("/api/personas/{name}/persona/interview", self.api_persona_interview)
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

        profile = PersonaStore.load(paths.dir)
        if profile is None:
            profile = PersonaProfile(name=name)
        return _json_response({
            "name": profile.name,
            "aliases": profile.aliases,
            "persona_summary": profile.persona_summary,
            "full_system_prompt": profile.full_system_prompt,
            "personality_traits": profile.personality_traits,
            "backstory": profile.backstory,
            "core_values": profile.core_values,
            "flaws": profile.flaws,
            "motivations": profile.motivations,
            "communication_style": profile.communication_style,
            "speech_rhythm": profile.speech_rhythm,
            "catchphrases": profile.catchphrases,
            "emoji_preference": profile.emoji_preference,
            "humor_style": profile.humor_style,
            "typical_greetings": profile.typical_greetings,
            "typical_signoffs": profile.typical_signoffs,
            "emotional_baseline": profile.emotional_baseline,
            "emotional_range": profile.emotional_range,
            "empathy_style": profile.empathy_style,
            "stress_response": profile.stress_response,
            "boundaries": profile.boundaries,
            "taboo_topics": profile.taboo_topics,
            "preferred_topics": profile.preferred_topics,
            "social_role": profile.social_role,
            "max_tokens_preference": profile.max_tokens_preference,
            "temperature_preference": profile.temperature_preference,
            "reply_frequency": profile.reply_frequency,
            "version": profile.version,
            "created_at": profile.created_at,
            "source": profile.source,
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

        profile = PersonaStore.load(paths.dir)
        if profile is None:
            profile = PersonaProfile(name=name)

        for key in (
            "name", "aliases", "persona_summary", "full_system_prompt",
            "personality_traits", "backstory", "core_values", "flaws",
            "motivations", "communication_style", "speech_rhythm",
            "catchphrases", "emoji_preference", "humor_style",
            "typical_greetings", "typical_signoffs", "emotional_baseline",
            "emotional_range", "empathy_style", "stress_response",
            "boundaries", "taboo_topics", "preferred_topics", "social_role",
            "max_tokens_preference", "temperature_preference", "reply_frequency",
            "version", "created_at", "source",
        ):
            if key in body:
                setattr(profile, key, body[key])

        PersonaStore.save(paths.dir, profile)
        return _json_response({"success": True})

    async def api_persona_interview_get(self, request: web.Request) -> web.Response:
        """读取已保存的 interview 问卷答案。"""
        name = _get_name(request)
        paths = self.persona_manager.get_persona_paths(name)
        if paths is None:
            return _json_response({"error": "人格不存在"}, 404)
        record_path = paths.dir / "engine_state" / "persona_interview_record.json"
        pending_path = paths.dir / "engine_state" / "pending_persona_interview.json"
        try:
            if record_path.exists():
                data = json.loads(record_path.read_text(encoding="utf-8"))
                return _json_response({
                    "answers": data.get("answers", {}),
                    "name": data.get("name", ""),
                    "aliases": data.get("aliases", []),
                })
            if pending_path.exists():
                data = json.loads(pending_path.read_text(encoding="utf-8"))
                return _json_response({
                    "answers": data.get("answers", {}),
                    "name": data.get("name", ""),
                    "aliases": data.get("aliases", []),
                })
            return _json_response({"answers": {}, "name": "", "aliases": []})
        except Exception as exc:
            LOG.warning("读取 interview 记录失败: %s", exc)
            return _json_response({"answers": {}, "name": "", "aliases": []})

    async def api_persona_interview(self, request: web.Request) -> web.Response:
        """根据问卷答案生成人格。"""
        name = _get_name(request)
        try:
            body = await request.json()
        except Exception:
            return _json_response({"error": "Invalid JSON"}, 400)
        p_name = str(body.get("name", "小星")).strip()
        answers = body.get("answers", {})
        aliases = [a.strip() for a in body.get("aliases", []) if isinstance(a, str) and a.strip()]
        model = str(body.get("model", "gpt-4o-mini")).strip()
        paths = self.persona_manager.get_persona_paths(name)
        if paths is None:
            return _json_response({"error": "人格不存在"}, 404)

        from sirius_chat.providers.routing import AutoRoutingProvider
        provider_mgr = WorkspaceProviderManager(self.persona_manager.data_path)
        providers = provider_mgr.load()
        provider = None
        if providers:
            provider = AutoRoutingProvider(providers)
        try:
            persona = await generate_persona_from_interview(
                work_path=paths.dir,
                provider=provider,
                name=p_name,
                answers=answers,
                aliases=aliases,
                model=model,
            )
            PersonaStore.save(paths.dir, persona)
            self.persona_manager.reload_persona(name)
            return _json_response({"success": True, "persona": persona.to_dict()})
        except Exception as exc:
            LOG.exception("问卷人格生成失败")
            return _json_response({"error": str(exc)}, 500)

    def _build_model_choices(self) -> tuple[list[str], list[dict[str, str]]]:
        """返回 (available_models, model_choices)。"""
        available_models: list[str] = []
        model_choices: list[dict[str, str]] = []
        try:
            provider_mgr = WorkspaceProviderManager(self.persona_manager.data_path)
            for cfg in provider_mgr.load().values():
                if cfg.enabled:
                    for m in cfg.models:
                        available_models.append(m)
                        model_choices.append({
                            "label": f"{cfg.provider_type}/{m}",
                            "value": m,
                        })
            seen: set[str] = set()
            deduped_models: list[str] = []
            deduped_choices: list[dict[str, str]] = []
            for m, c in zip(available_models, model_choices):
                if m not in seen:
                    seen.add(m)
                    deduped_models.append(m)
                    deduped_choices.append(c)
            available_models = deduped_models
            model_choices = deduped_choices
        except Exception:
            pass
        return available_models, model_choices

    async def api_available_models_get(self, request: web.Request) -> web.Response:
        """返回全局可用模型列表（含 provider 前缀显示名）。"""
        available_models, model_choices = self._build_model_choices()
        return _json_response({
            "available_models": available_models,
            "model_choices": model_choices,
        })

    # ─── 多人格 API: 模型编排 ─────────────────────────────

    async def api_orchestration_get(self, request: web.Request) -> web.Response:
        name = _get_name(request)
        paths = self.persona_manager.get_persona_paths(name)
        if paths is None:
            return _json_response({"error": "人格不存在"}, 404)

        data = OrchestrationStore.load(paths.dir)
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

        cfg = OrchestrationStore.load(paths.dir)

        for key in ("analysis_model", "chat_model", "vision_model", "summary_model"):
            if key in body:
                cfg[key] = body[key]

        OrchestrationStore.save(paths.dir, cfg)
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

    # ─── 全局 API: Token 使用统计（聚合所有人格） ─────────

    async def api_tokens_get(self, request: web.Request) -> web.Response:
        """Return aggregated token usage across all personas."""
        from sirius_chat.token.store import TokenUsageStore
        from sirius_chat.token import analytics as token_analytics

        total_summary = {
            "total_calls": 0,
            "total_prompt_tokens": 0,
            "total_completion_tokens": 0,
            "total_tokens": 0,
        }
        persona_breakdown: list[dict[str, Any]] = []

        for persona_info in self.persona_manager.list_personas():
            name = persona_info.get("name")
            if not name:
                continue
            paths = self.persona_manager.get_persona_paths(name)
            if paths is None:
                continue
            db_path = paths.dir / "token_usage.db"
            if not db_path.exists():
                continue
            try:
                store = TokenUsageStore(str(db_path))
                baseline = token_analytics.compute_baseline(store)
                total_summary["total_calls"] += baseline.get("total_calls", 0)
                total_summary["total_prompt_tokens"] += baseline.get("total_prompt_tokens", 0)
                total_summary["total_completion_tokens"] += baseline.get("total_completion_tokens", 0)
                total_summary["total_tokens"] += baseline.get("total_tokens", 0)
                persona_breakdown.append({
                    "name": name,
                    "calls": baseline.get("total_calls", 0),
                    "prompt_tokens": baseline.get("total_prompt_tokens", 0),
                    "completion_tokens": baseline.get("total_completion_tokens", 0),
                    "total_tokens": baseline.get("total_tokens", 0),
                })
            except Exception as exc:
                LOG.warning("读取 Token 统计失败 %s: %s", name, exc)

        response_avg: dict[str, Any] = {"total_calls": 0, "avg_total_tokens": 0, "avg_prompt_tokens": 0, "avg_completion_tokens": 0}
        if total_summary["total_calls"]:
            response_avg = {
                "total_calls": total_summary["total_calls"],
                "avg_total_tokens": round(total_summary["total_tokens"] / total_summary["total_calls"], 1),
                "avg_prompt_tokens": round(total_summary["total_prompt_tokens"] / total_summary["total_calls"], 1),
                "avg_completion_tokens": round(total_summary["total_completion_tokens"] / total_summary["total_calls"], 1),
            }

        return _json_response({
            "summary": total_summary,
            "response_avg": response_avg,
            "personas": persona_breakdown,
        })

    async def api_telemetry_get(self, request: web.Request) -> web.Response:
        """Return global skill usage telemetry aggregated across all personas."""
        all_summaries: dict[str, dict[str, Any]] = {}
        total_calls = 0

        for persona_info in self.persona_manager.list_personas():
            name = persona_info.get("name")
            if not name:
                continue
            paths = self.persona_manager.get_persona_paths(name)
            if paths is None:
                continue
            telemetry_path = paths.dir / "skill_data" / ".telemetry.jsonl"
            if not telemetry_path.exists():
                continue
            try:
                with open(telemetry_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        record = json.loads(line)
                        skill_name = record.get("skill_name", "unknown")
                        if skill_name not in all_summaries:
                            all_summaries[skill_name] = {
                                "calls": 0,
                                "successes": 0,
                                "failures": 0,
                                "total_ms": 0.0,
                            }
                        agg = all_summaries[skill_name]
                        agg["calls"] += 1
                        total_calls += 1
                        if record.get("success"):
                            agg["successes"] += 1
                        else:
                            agg["failures"] += 1
                        agg["total_ms"] += record.get("duration_ms", 0)
            except Exception as exc:
                LOG.warning("读取 Telemetry 失败 %s: %s", name, exc)

        skills: dict[str, Any] = {}
        for skill_name, stats in all_summaries.items():
            calls = stats["calls"]
            skills[skill_name] = {
                "calls": calls,
                "success_rate": round(stats["successes"] / calls * 100, 1) if calls else 0,
                "avg_ms": round(stats["total_ms"] / calls, 1) if calls else 0,
            }

        return _json_response({
            "total_calls": total_calls,
            "skills": skills,
        })

    # ─── 多人格 API: Token 使用统计 ───────────────────────

    async def api_persona_tokens_get(self, request: web.Request) -> web.Response:
        name = _get_name(request)
        paths = self.persona_manager.get_persona_paths(name)
        if paths is None:
            return _json_response({"error": "人格不存在"}, 404)

        from sirius_chat.token.store import TokenUsageStore
        from sirius_chat.token import analytics as token_analytics

        db_path = paths.dir / "token" / "token_usage.db"
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

        db_path = paths.dir / "cognition_events.db"
        if not db_path.exists():
            return _json_response({"events": []})

        try:
            from sirius_chat.memory.cognition_store import CognitionEventStore
            store = CognitionEventStore(str(db_path))
            limit = int(request.query.get("limit", "50"))
            events = store.get_recent(limit=limit)
            store.close()
            return _json_response({"events": events})
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

    async def api_napcat_logs(self, request: web.Request) -> web.Response:
        if self.napcat_manager is None:
            return _json_response({"enabled": False, "logs": []})
        lines = int(request.query.get("lines", "100"))
        try:
            return _json_response({
                "enabled": True,
                "logs": self.napcat_manager.get_logs(lines=lines),
            })
        except Exception as exc:
            LOG.warning("读取 NapCat 日志失败: %s", exc)
            return _json_response({"enabled": True, "logs": [], "error": str(exc)})

    async def api_napcat_start(self, request: web.Request) -> web.Response:
        if self.napcat_manager is None:
            return _json_response({"success": False, "message": "NapCat 管理未启用"}, 400)
        try:
            body = await request.json()
        except Exception:
            body = {}
        qq_number = str(body.get("qq_number", "")).strip()
        if not qq_number:
            return _json_response({"success": False, "message": "QQ 号码不能为空"}, 400)
        try:
            result = await self.napcat_manager.start(qq_number)
            return _json_response(result)
        except Exception as exc:
            LOG.exception("NapCat 启动失败")
            return _json_response({"success": False, "message": str(exc)}, 500)

    async def api_napcat_stop(self, request: web.Request) -> web.Response:
        if self.napcat_manager is None:
            return _json_response({"success": False, "message": "NapCat 管理未启用"}, 400)
        try:
            body = await request.json()
        except Exception:
            body = {}
        qq_number = str(body.get("qq_number", "")).strip()
        if not qq_number:
            return _json_response({"success": False, "message": "QQ 号码不能为空"}, 400)
        try:
            result = await self.napcat_manager.stop(qq_number)
            return _json_response(result)
        except Exception as exc:
            LOG.exception("NapCat 停止失败")
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
