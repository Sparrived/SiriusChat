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
        self._napcat_instances: dict[str, Any] = {}
        self._napcat_registry_path = Path("data/napcat_instance_registry.json")
        if napcat_install_dir is not None:
            self.napcat_manager = NapCatManager(napcat_install_dir)
            self._napcat_registry_path = Path(napcat_install_dir).parent / "napcat_instance_registry.json"
            self._load_napcat_registry()
        self.app = web.Application(middlewares=[_no_cache_middleware])
        self._setup_routes()
        self.runner: web.AppRunner | None = None
        self.site: web.TCPSite | None = None

    def _setup_routes(self) -> None:
        self.app.router.add_get("/", self.index)
        static_dir = Path(__file__).parent / "static"
        self.app.router.add_static("/static/", static_dir)

        # ─── 全局 API ─────────────────────────────────────────
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

        # ─── Telemetry API ────────────────────────────────────
        self.app.router.add_get("/api/telemetry", self.api_telemetry_get)

        # ─── Token usage API ──────────────────────────────────
        self.app.router.add_get("/api/tokens", self.api_tokens_get)

        # ─── 多人格 API ───────────────────────────────────────
        self.app.router.add_get("/api/personas", self.api_personas_list)
        self.app.router.add_post("/api/personas", self.api_personas_create)
        self.app.router.add_get("/api/personas/{name}", self.api_persona_status)
        self.app.router.add_post("/api/personas/{name}/start", self.api_persona_start)
        self.app.router.add_post("/api/personas/{name}/stop", self.api_persona_stop)
        self.app.router.add_delete("/api/personas/{name}", self.api_persona_delete)

        # 人格配置
        self.app.router.add_get("/api/personas/{name}/persona", self.api_persona_get)
        self.app.router.add_post("/api/personas/{name}/persona/save", self.api_persona_save)
        self.app.router.add_get("/api/personas/{name}/persona/interview", self.api_persona_interview_get)
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

        # Token usage (per persona)
        self.app.router.add_get("/api/personas/{name}/tokens", self.api_persona_tokens_get)

        # Cognition events (per persona)
        self.app.router.add_get("/api/personas/{name}/cognition", self.api_persona_cognition_get)

        # Diary entries (per persona)
        self.app.router.add_get("/api/personas/{name}/diary", self.api_persona_diary_get)

        # User semantic profiles (per persona)
        self.app.router.add_get("/api/personas/{name}/users", self.api_persona_users_get)
        self.app.router.add_get("/api/personas/{name}/users/{user_id}", self.api_persona_user_get)

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
            "webui_host": "0.0.0.0",
            "webui_port": 8080,
            "napcat_install_dir": "",
            "napcat_base_port": 3001,
            "log_level": "INFO",
        })

    async def api_global_config_post(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return _json_response({"error": "Invalid JSON"}, 400)
        path = self._global_config_path()
        try:
            existing = {}
            if path.exists():
                existing = json.loads(path.read_text(encoding="utf-8"))
            # 只更新允许的字段
            allowed = {
                "webui_host", "webui_port",
                "napcat_install_dir", "napcat_base_port", "log_level",
            }
            for key in allowed:
                if key in body:
                    existing[key] = body[key]
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(path)
            LOG.info("全局配置已更新: %s", existing)
            return _json_response({"success": True, "message": "全局配置已保存"})
        except Exception as exc:
            LOG.exception("保存全局配置失败")
            return _json_response({"error": str(exc)}, 500)

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

    async def api_personas_create(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return _json_response({"error": "Invalid JSON"}, 400)
        name = str(body.get("name", "")).strip()
        if not name:
            return _json_response({"error": "name is required"}, 400)
        persona_name = str(body.get("persona_name", "") or name).strip()
        try:
            pdir = self.persona_manager.create_persona(
                name,
                persona_name=persona_name,
            )
            return _json_response({
                "success": True,
                "name": name,
                "path": str(pdir),
            })
        except FileExistsError as exc:
            return _json_response({"error": str(exc)}, 409)
        except Exception as exc:
            LOG.exception("创建人格失败")
            return _json_response({"error": str(exc)}, 500)

    async def api_persona_status(self, request: web.Request) -> web.Response:
        name = _get_name(request)
        info = self.persona_manager.get_persona_status(name)
        if info is None:
            return _json_response({"error": f"人格不存在: {name}"}, 404)
        return _json_response(info)

    async def api_persona_start(self, request: web.Request) -> web.Response:
        name = _get_name(request)

        # 如果配置了 NapCat，先启动对应实例
        if self.napcat_manager is not None:
            paths = self.persona_manager.get_persona_paths(name)
            if paths is not None:
                adapters = PersonaAdaptersConfig.load(paths.adapters)
                for a in adapters.adapters:
                    if a.type != "napcat" or not a.enabled:
                        continue
                    qq = getattr(a, "qq_number", "")
                    port = int(a.ws_url.rsplit(":", 1)[-1]) if ":" in a.ws_url else 3001
                    if not qq:
                        return _json_response({"error": f"人格 {name} 的 NapCat 未配置 QQ 号"}, 400)

                    token = getattr(a, "token", "napcat_ws")

                    # 检查是否已有运行中的 NapCat 实例，避免重复启动
                    existing = self._napcat_instances.get(name)
                    if existing:
                        if existing.is_running:
                            LOG.info("NapCat 实例 %s 已在运行，跳过启动", name)
                            break
                        # 进程检测失败，但可能 WS 仍然可用（外部进程或跨进程启动）
                        try:
                            ws_check = await existing.wait_for_ws(
                                port=port, token=token, timeout=5.0
                            )
                            if ws_check.get("ready"):
                                LOG.info(
                                    "NapCat 实例 %s WS 已就绪（外部进程），跳过启动",
                                    name,
                                )
                                break
                        except Exception as exc:
                            LOG.debug(
                                "NapCat 实例 %s WS 快速检测失败: %s", name, exc
                            )
                        self._napcat_instances.pop(name, None)

                    instance_mgr = NapCatManager.for_persona(
                        global_install_dir=self.napcat_manager.install_dir,
                        persona_name=name,
                    )
                    LOG.info("配置 NapCat 实例 %s (QQ: %s, 端口: %s)...", name, qq, port)
                    instance_mgr.configure(qq_number=qq, ws_port=port, ws_token=token)
                    result = await instance_mgr.start(qq_number=qq)
                    if not result["success"]:
                        return _json_response({"error": f"启动 NapCat 失败: {result['message']}"}, 500)
                    LOG.info("NapCat 实例 %s 已启动，等待 WS 就绪...", name)
                    ready_info = await instance_mgr.wait_for_ws(port=port, token=token, timeout=120.0)
                    if not ready_info.get("ready"):
                        # 回滚：停止已启动的 NapCat，避免僵尸进程
                        error_msg = ready_info.get("error") or "NapCat WS 未就绪"
                        LOG.warning("NapCat 实例 %s WS 未就绪，执行回滚: %s", name, error_msg)
                        try:
                            await instance_mgr.stop()
                        except Exception as exc:
                            LOG.warning("回滚停止 NapCat 实例 %s 失败: %s", name, exc)
                        return _json_response(
                            {"error": f"{error_msg}，请检查 QQ 是否已扫码登录"}, 500
                        )
                    self_id = ready_info.get("self_id")
                    if self_id:
                        LOG.info("NapCat 实例 %s 账号验证通过 (QQ=%s)", name, self_id)
                    self._napcat_instances[name] = instance_mgr
                    self._save_napcat_registry()
                    break  # 只处理第一个启用的 napcat adapter

        ok = self.persona_manager.start_persona(name)
        return _json_response({"success": ok, "name": name})

    def _load_napcat_registry(self) -> None:
        """从磁盘恢复 NapCat 实例引用（WebUI 重启后使用）。"""
        if not self._napcat_registry_path.exists():
            return
        try:
            data = json.loads(self._napcat_registry_path.read_text(encoding="utf-8"))
            for name, info in data.items():
                instance_dir = info.get("instance_dir")
                if instance_dir and Path(instance_dir).exists():
                    self._napcat_instances[name] = NapCatManager(
                        install_dir=self.napcat_manager.install_dir if self.napcat_manager else "",
                        instance_dir=instance_dir,
                    )
        except Exception as exc:
            LOG.warning("加载 NapCat 实例注册表失败: %s", exc)

    def _save_napcat_registry(self) -> None:
        """将 NapCat 实例引用持久化到磁盘。"""
        try:
            data: dict[str, dict[str, Any]] = {}
            for name, mgr in self._napcat_instances.items():
                data[name] = {"instance_dir": str(mgr.instance_dir)}
            tmp = self._napcat_registry_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self._napcat_registry_path)
        except Exception as exc:
            LOG.warning("保存 NapCat 实例注册表失败: %s", exc)

    async def api_persona_stop(self, request: web.Request) -> web.Response:
        name = _get_name(request)
        ok = self.persona_manager.stop_persona(name)

        # 停止对应的 NapCat 实例
        instance_mgr = self._napcat_instances.pop(name, None)
        if instance_mgr is not None:
            try:
                await instance_mgr.stop()
            except Exception as exc:
                LOG.warning("停止 NapCat 实例 %s 失败: %s", name, exc)
        self._save_napcat_registry()

        return _json_response({"success": ok, "name": name})

    async def api_persona_delete(self, request: web.Request) -> web.Response:
        name = _get_name(request)

        # 先停止对应的 NapCat 实例
        instance_mgr = self._napcat_instances.pop(name, None)
        if instance_mgr is not None:
            try:
                await instance_mgr.stop()
            except Exception as exc:
                LOG.warning("停止 NapCat 实例 %s 失败: %s", name, exc)

        # 清理 NapCat 实例目录
        if self.napcat_manager is not None:
            instance_dir = Path(self.napcat_manager.install_dir) / "instances" / name
            if instance_dir.exists():
                try:
                    shutil.rmtree(instance_dir)
                    LOG.info("已清理 NapCat 实例目录: %s", instance_dir)
                except Exception as exc:
                    LOG.warning("清理 NapCat 实例目录 %s 失败: %s", instance_dir, exc)

        self._save_napcat_registry()
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

    async def api_persona_interview_get(self, request: web.Request) -> web.Response:
        """读取已保存的 interview 问卷答案。"""
        name = _get_name(request)
        pdir = self.persona_manager.get_persona_dir(name)
        record_path = pdir / "engine_state" / "persona_interview_record.json"
        pending_path = pdir / "engine_state" / "pending_persona_interview.json"
        target = record_path if record_path.exists() else pending_path
        if not target.exists():
            return _json_response({"answers": {}, "name": "", "aliases": []})
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
            return _json_response({
                "answers": data.get("answers", {}),
                "name": data.get("name", ""),
                "aliases": data.get("aliases", []),
            })
        except Exception as exc:
            LOG.warning("读取 interview 记录失败: %s", exc)
            return _json_response({"answers": {}, "name": "", "aliases": []})

    async def api_persona_interview(self, request: web.Request) -> web.Response:
        name = _get_name(request)
        try:
            body = await request.json()
        except Exception:
            return _json_response({"error": "Invalid JSON"}, 400)
        p_name = str(body.get("name", "小星")).strip()
        answers = body.get("answers", {})
        aliases = [a.strip() for a in body.get("aliases", []) if isinstance(a, str) and a.strip()]
        model = str(body.get("model", "gpt-4o-mini")).strip()
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
                model=model,
            )
            # 自动生成并保存到当前人格目录
            PersonaStore.save(pdir, persona)
            self.persona_manager.reload_persona(name)
            return _json_response({"success": True, "persona": asdict(persona)})
        except Exception as exc:
            LOG.exception("问卷人格生成失败")
            return _json_response({"error": str(exc)}, 500)

    # ─── 多人格 API: 模型列表 ─────────────────────────────

    def _build_model_choices(self) -> tuple[list[str], list[dict[str, str]]]:
        """返回 (available_models, model_choices)。
        available_models 为裸模型名列表；model_choices 为 {label, value} 列表，
        label 格式为 provider_name/model_name。
        """
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
            # 去重并保持稳定顺序
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
        pdir = self.persona_manager.get_persona_dir(name)
        orch = OrchestrationStore.load(pdir)
        available_models, model_choices = self._build_model_choices()
        return _json_response({
            "analysis_model": orch.get("analysis_model", "gpt-4o-mini"),
            "chat_model": orch.get("chat_model", "gpt-4o"),
            "vision_model": orch.get("vision_model", "gpt-4o"),
            "available_models": available_models,
            "model_choices": model_choices,
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
        """通知子进程重载配置（当前尚未实现，请使用重启功能）。"""
        name = _get_name(request)
        ok = self.persona_manager.reload_persona(name)
        return _json_response(
            {
                "success": ok,
                "message": (
                    "重载请求已发送" if ok else "热重载尚未实现，请使用重启功能使配置生效"
                ),
            }
        )

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

    async def api_telemetry_get(self, request: web.Request) -> web.Response:
        """Return global skill usage telemetry aggregated across all personas."""
        from sirius_chat.skills.telemetry import SkillTelemetry

        all_summaries: dict[str, dict[str, Any]] = {}
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
                telemetry = SkillTelemetry(telemetry_path)
                summary = telemetry.summary()
                for skill_name, stats in summary.items():
                    if skill_name not in all_summaries:
                        all_summaries[skill_name] = {
                            "calls": 0,
                            "successes": 0,
                            "failures": 0,
                            "total_ms": 0.0,
                            "errors": [],
                        }
                    agg = all_summaries[skill_name]
                    agg["calls"] += stats["calls"]
                    agg["successes"] += stats["successes"]
                    agg["failures"] += stats["failures"]
                    agg["total_ms"] += stats["total_ms"]
                    agg["errors"].extend(stats.get("errors", []))
                    agg["errors"] = agg["errors"][-5:]  # keep last 5 unique-ish errors
            except Exception:
                continue

        # Compute averages
        for stats in all_summaries.values():
            if stats["calls"]:
                stats["avg_ms"] = round(stats["total_ms"] / stats["calls"], 2)
                stats["success_rate"] = round(stats["successes"] / stats["calls"] * 100, 1)
            else:
                stats["avg_ms"] = 0.0
                stats["success_rate"] = 0.0

        return _json_response({
            "skills": all_summaries,
            "total_calls": sum(s["calls"] for s in all_summaries.values()),
        })

    async def api_tokens_get(self, request: web.Request) -> web.Response:
        """Return aggregated token usage across all personas."""
        from sirius_chat.token.store import TokenUsageStore

        total_summary = {
            "total_calls": 0,
            "total_prompt_tokens": 0,
            "total_completion_tokens": 0,
            "total_tokens": 0,
            "total_input_chars": 0,
            "total_output_chars": 0,
        }
        persona_breakdown: list[dict[str, Any]] = []
        global_section_breakdown: dict[str, int] = {}

        for persona_info in self.persona_manager.list_personas():
            name = persona_info.get("name")
            if not name:
                continue
            paths = self.persona_manager.get_persona_paths(name)
            if paths is None:
                continue
            db_path = paths.dir / "token" / "token_usage.db"
            LOG.debug("Token 统计检查 %s: db_path=%s exists=%s", name, db_path, db_path.exists())
            if not db_path.exists():
                continue
            try:
                store = TokenUsageStore(db_path, session_id="default")
                summary = store.get_summary()
                LOG.debug("Token 统计 %s: %s", name, summary)
                if summary.get("total_calls", 0):
                    persona_breakdown.append({
                        "persona_name": name,
                        **summary,
                    })
                    for key in total_summary:
                        total_summary[key] += summary.get(key, 0)
                # Aggregate section breakdown across all personas
                for section, tokens in store.get_section_breakdown().items():
                    global_section_breakdown[section] = global_section_breakdown.get(section, 0) + tokens
                store.close()
            except Exception as exc:
                LOG.warning("Token 统计读取失败 %s: %s", name, exc)
                continue

        # Compute average tokens per response_generate round across all personas
        response_total_calls = 0
        response_total_tokens = 0
        response_prompt_tokens = 0
        response_completion_tokens = 0
        for persona_info in self.persona_manager.list_personas():
            name = persona_info.get("name")
            if not name:
                continue
            paths = self.persona_manager.get_persona_paths(name)
            if paths is None:
                continue
            db_path = paths.dir / "token" / "token_usage.db"
            if not db_path.exists():
                continue
            try:
                store = TokenUsageStore(db_path, session_id="default")
                for row in store.get_breakdown_by("task_name"):
                    if row.get("name") == "response_generate":
                        response_total_calls += row.get("calls", 0)
                        response_total_tokens += row.get("total_tokens", 0)
                        response_prompt_tokens += row.get("prompt_tokens", 0)
                        response_completion_tokens += row.get("completion_tokens", 0)
                store.close()
            except Exception:
                continue

        response_avg = {
            "avg_total_tokens": round(response_total_tokens / response_total_calls) if response_total_calls else 0,
            "avg_prompt_tokens": round(response_prompt_tokens / response_total_calls) if response_total_calls else 0,
            "avg_completion_tokens": round(response_completion_tokens / response_total_calls) if response_total_calls else 0,
            "total_calls": response_total_calls,
        }

        # Aggregate retry, duration, efficiency, failure, depth across all personas
        global_retry = {"total_calls": 0, "total_retries": 0, "retry_rate_pct": 0.0}
        global_duration = {"calls": 0, "avg_ms": 0.0, "min_ms": 0.0, "max_ms": 0.0}
        global_efficiency = {"calls": 0, "chars_per_token": 0.0, "output_chars_per_token": 0.0}
        global_failure = {"total_calls": 0, "failure_calls": 0, "failure_rate_pct": 0.0}
        global_depth = {"calls": 0, "total_depth": 0, "max_depth": 0}
        all_hourly: dict[int, dict[str, Any]] = {}
        provider_agg: dict[str, dict[str, Any]] = {}
        for persona_info in self.persona_manager.list_personas():
            name = persona_info.get("name")
            if not name:
                continue
            paths = self.persona_manager.get_persona_paths(name)
            if paths is None:
                continue
            db_path = paths.dir / "token" / "token_usage.db"
            if not db_path.exists():
                continue
            try:
                store = TokenUsageStore(db_path, session_id="default")
                rs = store.get_retry_stats()
                global_retry["total_calls"] += rs.get("total_calls", 0)
                global_retry["total_retries"] += rs.get("total_retries", 0)
                ds = store.get_duration_stats()["overall"]
                global_duration["calls"] += ds.get("calls", 0)
                es = store.get_efficiency_stats()
                global_efficiency["calls"] += es.get("calls", 0)
                fs = store.get_failure_stats()
                global_failure["total_calls"] += fs.get("total_calls", 0)
                global_failure["failure_calls"] += fs.get("failure_calls", 0)
                ds = store.get_conversation_depth_stats()
                global_depth["calls"] += ds.get("calls", 0)
                global_depth["total_depth"] += ds.get("calls", 0) * (ds.get("avg_depth", 0) or 0)
                global_depth["max_depth"] = max(global_depth["max_depth"], ds.get("max_depth", 0))
                for hr in store.get_hourly_summary():
                    hts = int(hr["hour_ts"])
                    if hts not in all_hourly:
                        all_hourly[hts] = {"hour_ts": hts, "calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
                    for k in ("calls", "prompt_tokens", "completion_tokens", "total_tokens"):
                        all_hourly[hts][k] += hr.get(k, 0)
                for prow in store.get_breakdown_by("provider_name"):
                    pname = prow.get("name", "unknown")
                    if pname not in provider_agg:
                        provider_agg[pname] = {"name": pname, "calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
                    for k in ("calls", "prompt_tokens", "completion_tokens", "total_tokens"):
                        provider_agg[pname][k] += prow.get(k, 0)
                store.close()
            except Exception:
                continue
        if global_retry["total_calls"]:
            global_retry["retry_rate_pct"] = round(global_retry["total_retries"] * 100.0 / global_retry["total_calls"], 2)
        if global_failure["total_calls"]:
            global_failure["failure_rate_pct"] = round(global_failure["failure_calls"] * 100.0 / global_failure["total_calls"], 2)
        global_depth["avg_depth"] = round(global_depth["total_depth"] / global_depth["calls"], 2) if global_depth["calls"] else 0.0

        # Prompt / Completion ratio
        total_prompt = total_summary.get("total_prompt_tokens", 0)
        total_completion = total_summary.get("total_completion_tokens", 0)
        total_all = total_prompt + total_completion
        ratio = {
            "prompt_pct": round(total_prompt * 100.0 / total_all, 1) if total_all else 0,
            "completion_pct": round(total_completion * 100.0 / total_all, 1) if total_all else 0,
        }

        return _json_response({
            "summary": total_summary,
            "personas": persona_breakdown,
            "section_breakdown": global_section_breakdown,
            "response_avg": response_avg,
            "hourly": sorted(all_hourly.values(), key=lambda x: x["hour_ts"]),
            "retry_stats": global_retry,
            "duration_stats": global_duration,
            "efficiency_stats": global_efficiency,
            "failure_stats": global_failure,
            "depth_stats": global_depth,
            "ratio": ratio,
            "by_provider": sorted(provider_agg.values(), key=lambda x: x["total_tokens"], reverse=True),
        })

    async def api_persona_tokens_get(self, request: web.Request) -> web.Response:
        """Return detailed token usage for a single persona."""
        from sirius_chat.token.store import TokenUsageStore

        name = _get_name(request)
        paths = self.persona_manager.get_persona_paths(name)
        if paths is None:
            return _json_response({"error": "人格不存在"}, 404)

        db_path = paths.dir / "token" / "token_usage.db"
        if not db_path.exists():
            return _json_response({
                "summary": {},
                "by_task": [],
                "by_model": [],
                "by_group": [],
                "by_provider": [],
                "recent": [],
                "section_breakdown": {},
                "recent_with_breakdown": [],
                "response_avg": {"avg_total_tokens": 0, "avg_prompt_tokens": 0, "avg_completion_tokens": 0, "total_calls": 0},
                "hourly": [],
                "hourly_distribution": [],
                "retry_stats": {"total_calls": 0, "total_retries": 0, "retry_rate_pct": 0.0},
                "duration_stats": {"by_task": [], "overall": {"calls": 0, "avg_ms": 0.0, "min_ms": 0.0, "max_ms": 0.0}},
                "efficiency_stats": {"calls": 0, "chars_per_token": 0.0, "output_chars_per_token": 0.0, "output_ratio": 0.0},
                "empty_reply_stats": {"total_calls": 0, "empty_calls": 0, "empty_rate_pct": 0.0},
                "period_comparison": {"current": {}, "previous": {}, "change_calls": 0.0, "change_total_tokens": 0.0, "change_prompt_tokens": 0.0, "change_completion_tokens": 0.0},
                "failure_stats": {"total_calls": 0, "failure_calls": 0, "failure_rate_pct": 0.0, "by_type": []},
                "depth_stats": {"calls": 0, "avg_depth": 0.0, "max_depth": 0},
                "ratio": {"prompt_pct": 0, "completion_pct": 0},
            })

        # Parse optional time range filters
        try:
            start_ts = float(request.query.get("start", "0")) or None
            end_ts = float(request.query.get("end", "0")) or None
        except ValueError:
            start_ts = None
            end_ts = None

        try:
            store = TokenUsageStore(db_path, session_id="default")
            by_task = store.get_breakdown_by("task_name")
            response_row = next((r for r in by_task if r.get("name") == "response_generate"), None)
            response_calls = response_row.get("calls", 0) if response_row else 0
            response_avg = {
                "avg_total_tokens": round(response_row.get("total_tokens", 0) / response_calls) if response_calls else 0,
                "avg_prompt_tokens": round(response_row.get("prompt_tokens", 0) / response_calls) if response_calls else 0,
                "avg_completion_tokens": round(response_row.get("completion_tokens", 0) / response_calls) if response_calls else 0,
                "total_calls": response_calls,
            }
            summary = store.get_summary()
            total_prompt = summary.get("total_prompt_tokens", 0)
            total_completion = summary.get("total_completion_tokens", 0)
            total_all = total_prompt + total_completion
            ratio = {
                "prompt_pct": round(total_prompt * 100.0 / total_all, 1) if total_all else 0,
                "completion_pct": round(total_completion * 100.0 / total_all, 1) if total_all else 0,
            }
            result = {
                "summary": summary,
                "by_task": by_task,
                "by_model": store.get_breakdown_by("model"),
                "by_group": store.get_breakdown_by("group_id"),
                "by_provider": store.get_breakdown_by("provider_name"),
                "hourly_distribution": store.get_hourly_distribution(),
                "empty_reply_stats": store.get_empty_reply_stats(),
                "period_comparison": store.get_period_comparison(),
                "recent": store.get_recent_records(limit=30),
                "section_breakdown": store.get_section_breakdown(
                    start_ts=start_ts, end_ts=end_ts
                ),
                "section_breakdown_by_task": store.get_section_breakdown_by_task(
                    start_ts=start_ts, end_ts=end_ts
                ),
                "recent_with_breakdown": store.get_recent_records_with_breakdown(
                    limit=30
                ),
                "response_avg": response_avg,
                "hourly": store.get_hourly_summary(start_ts=start_ts, end_ts=end_ts),
                "retry_stats": store.get_retry_stats(),
                "duration_stats": store.get_duration_stats(),
                "efficiency_stats": store.get_efficiency_stats(),
                "empty_reply_stats": store.get_empty_reply_stats(),
                "period_comparison": store.get_period_comparison(),
                "failure_stats": store.get_failure_stats(),
                "depth_stats": store.get_conversation_depth_stats(),
                "ratio": ratio,
            }
            store.close()
            return _json_response(result)
        except Exception as exc:
            LOG.warning("读取 Token 统计失败 %s: %s", name, exc)
            return _json_response({"error": str(exc)}, 500)

    async def api_persona_cognition_get(self, request: web.Request) -> web.Response:
        """Return recent cognition events for a single persona."""
        from sirius_chat.memory.cognition_store import CognitionEventStore

        name = _get_name(request)
        paths = self.persona_manager.get_persona_paths(name)
        if paths is None:
            return _json_response({"error": "人格不存在"}, 404)

        db_path = paths.dir / "cognition_events.db"
        if not db_path.exists():
            return _json_response({"events": [], "emotion_distribution": {}})

        try:
            store = CognitionEventStore(db_path)
            limit = int(request.query.get("limit", "50"))
            group_id = request.query.get("group_id", "")
            if group_id:
                events = store.get_group_timeline(group_id, limit=limit)
            else:
                events = store.get_recent(limit=limit)
            dist = store.get_emotion_distribution(group_id if group_id else None)
            store.close()
            return _json_response({"events": events, "emotion_distribution": dist})
        except Exception as exc:
            LOG.warning("读取 Cognition 事件失败 %s: %s", name, exc)
            return _json_response({"error": str(exc)}, 500)

    async def api_persona_diary_get(self, request: web.Request) -> web.Response:
        """Return diary entries for a single persona."""
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
