"""SetupWizard — SiriusChat v1.0 首次启动配置向导。

通过 QQ 私聊引导 root 用户完成 Provider + Persona 配置。
配置完成后才初始化 EmotionalGroupChatEngine，避免自动生成默认人格。
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import logging
from sirius_chat.core.persona_generator import PersonaGenerator
from sirius_chat.core.persona_store import PersonaStore
from sirius_chat.models.persona import PersonaProfile

from .persona_utils import (
    INTERVIEW_QUESTIONS as _INTERVIEW_QUESTIONS,
    PERSONA_JSON_SCHEMA as _PERSONA_JSON_SCHEMA,
    generate_persona_from_interview,
    extract_json as _extract_json,
)
from .runtime import _resolve_api_key

LOG = logging.getLogger("sirius.platforms.wizard")

_EXISTING_API_KEY_PLACEHOLDER = "(保留已保存的 API Key)"
_WIZARD_TIMEOUT_SECONDS = 300.0


class WizardCancelled(Exception):
    """用户主动取消向导。"""


class WizardBack(Exception):
    """用户要求回退到上一步。"""


class WizardSkipModule(Exception):
    """用户要求跳过当前模块（保留已有配置）。"""


class SetupWizard:
    """私聊交互式配置向导（v1.0 适配版）。"""

    def __init__(self, plugin) -> None:
        self.bridge = plugin
        self.runtime = plugin.runtime
        self._task: asyncio.Task | None = None

    # ─── 入口 ──────────────────────────────────────────

    def maybe_start(self, force: bool = False) -> None:
        """若尚未完成首次配置，则启动引导向导。"""
        if self.bridge.data.get("setup_wizard_running", False):
            if not force:
                return
            self.bridge.data["setup_wizard_running"] = False
            self.bridge.save_data()

        if not force and self.bridge.data.get("setup_completed", False):
            return

        self._task = asyncio.create_task(self._run(force=force))

    # ─── 通信辅助 ───────────────────────────────────────

    async def _send_private(self, uid: str, text: str) -> None:
        try:
            await self.bridge.adapter.send_private_msg(uid, text)
        except Exception:
            LOG.exception("向 %s 发送私聊消息失败", uid)

    async def _wait_root_text(self, root_uid: str, timeout: float = _WIZARD_TIMEOUT_SECONDS) -> str:
        
        def _is_root_private(e: dict[str, Any]) -> bool:
            if str(e.get("user_id")) != root_uid:
                return False
            if "raw_message" not in e:
                return False
            return str(e.get("message_type")).lower() == "private"

        event = await self.bridge.wait_event(
            predicate=_is_root_private,
            timeout=timeout,
        )
        return str(event.get("raw_message", "")).strip()

    @staticmethod
    def _normalize_action(text: str) -> str:
        return text.strip().lower().lstrip("/")

    @staticmethod
    def _is_skip_module_action(action: str) -> bool:
        return action in {
            "skip-module",
            "skip_module",
            "skipmodule",
            "module-skip",
            "module_skip",
            "moduleskip",
        }

    # ─── 核心流程 ───────────────────────────────────────

    async def _run(self, force: bool = False) -> None:
        self.bridge.data["setup_wizard_running"] = True
        self.bridge.save_data()
        root_uid = ""
        snapshot: dict[str, Any] | None = None
        try:
            root_uid = self._resolve_root_user_id()
            if not root_uid:
                LOG.warning("无法解析 root 用户 ID，向导未启动。请在 config.yaml 中设置 root。")
                return

            if not force and self.bridge.data.get("setup_completed", False):
                return

            # 先保存当前配置快照，以便 /cancel 时回退
            snapshot = self._snapshot_runtime_state()

            await self._send_private(
                root_uid,
                "[SiriusChat v1.0] 首次启动需要完成 Provider 与人格配置。\n"
                "回复 /start 开始向导，回复 /cancel 可随时取消并回退到当前配置。",
            )

            first_reply = await self._wait_root_text(root_uid)
            if self._normalize_action(first_reply) != "start":
                await self._send_private(root_uid, "未收到 /start，向导已取消。发送 /sc-setup 可重新触发。")
                return

            # ── Phase 1: Provider 配置 ──
            try:
                providers = await self._collect_provider_configs(root_uid)
            except WizardBack:
                await self._send_private(root_uid, "已回退到向导开始。请回复 /start 重新开始。")
                return
            except WizardSkipModule:
                providers = self._current_provider_configs()
                if providers:
                    await self._send_private(root_uid, "已跳过 Provider 配置，保留已有配置。")
                else:
                    await self._send_private(root_uid, "尚无 Provider 配置，无法跳过。请重新配置。")
                    raise WizardCancelled()
            except WizardCancelled:
                raise

            if not providers:
                await self._send_private(root_uid, "未配置任何 Provider，向导已取消。")
                raise WizardCancelled()

            # 持久化 provider 到 ProviderRegistry（引擎统一维护）
            self._save_providers_to_registry(providers)

            # ── Phase 1.5: Orchestration 配置（模型映射）──
            try:
                await self._collect_orchestration_config(root_uid)
            except WizardBack:
                await self._send_private(root_uid, "已回退到向导开始。请回复 /start 重新开始。")
                return
            except WizardSkipModule:
                await self._send_private(root_uid, "已跳过模型配置，使用默认值。")
            except WizardCancelled:
                raise

            # ── Phase 2: Persona 配置 ──
            try:
                persona = await self._collect_persona_config(root_uid)
            except WizardBack:
                await self._send_private(root_uid, "已回退到向导开始。请回复 /start 重新开始。")
                return
            except WizardSkipModule:
                persona = PersonaStore.load(self.runtime.work_path)
                if persona is not None:
                    await self._send_private(root_uid, f"已跳过人格配置，保留已有人格: {persona.name}。")
                else:
                    await self._send_private(root_uid, "尚未保存人格，无法跳过。请先完成人格配置。")
                    raise WizardCancelled()
            except WizardCancelled:
                raise

            if persona is not None:
                self._archive_existing_persona()
                PersonaStore.save(self.runtime.work_path, persona)
                LOG.info("人格已保存: %s (%s)", persona.name, persona.source)

            # 标记完成
            self.bridge.data["setup_completed"] = True
            self.bridge.data["setup_wizard_notified"] = True
            self.bridge.save_data()

            # 初始化引擎（预热）
            await self._try_init_engine()

            from sirius_chat.core.orchestration_store import OrchestrationStore
            orch = OrchestrationStore.load(self.runtime.work_path)
            chat_model = orch.get("chat_model", "未配置")
            analysis_model = orch.get("analysis_model", "未配置")
            persona_name = persona.name if persona else "未知"
            await self._send_private(
                root_uid,
                f"✅ 首次向导已完成！\n"
                f"Provider: {[p.get('type') for p in providers]}\n"
                f"分析模型: {analysis_model}\n"
                f"对话模型: {chat_model}\n"
                f"人格: {persona_name}\n"
                f"引擎状态: {'就绪' if self.runtime.is_ready() else '未就绪'}\n"
                f"如需修改，可私聊发送 /sc-setup 重新运行向导。",
            )

        except WizardCancelled:
            if snapshot is not None:
                self._restore_runtime_state(snapshot)
            if root_uid:
                await self._send_private(root_uid, "已取消向导，配置已回退。发送 /sc-setup 可重新开始。")
        except asyncio.TimeoutError:
            if snapshot is not None:
                self._restore_runtime_state(snapshot)
            if root_uid:
                await self._send_private(root_uid, "向导等待回复超时，已中止。发送 /sc-setup 可重新触发。")
        except Exception as exc:
            if snapshot is not None:
                self._restore_runtime_state(snapshot)
            if root_uid:
                await self._send_private(root_uid, f"向导执行失败: {exc}\n请修正后重新发送 /sc-setup。")
            LOG.exception("向导执行失败: %s", exc)
        finally:
            self.bridge.data["setup_wizard_running"] = False

    # ─── Provider 配置采集 ──────────────────────────────

    async def _collect_provider_configs(self, root_uid: str) -> list[dict]:
        try:
            from sirius_chat.providers import get_supported_provider_platforms
            supported = get_supported_provider_platforms()
        except Exception:
            supported = {}

        if supported:
            platform_lines = [
                f"- {name} | 默认URL: {meta.get('default_base_url', '')}"
                for name, meta in supported.items()
            ]
            await self._send_private(root_uid, "支持的平台:\n" + "\n".join(platform_lines))

        existing = self._current_provider_configs()
        default_count = len(existing) if existing else 1

        await self._send_private(
            root_uid,
            f"请输入要配置的 Provider 数量（建议 1-3，默认 {default_count}）\n"
            "回复 /skip 使用默认值，/skip-module 跳过本模块保留已有配置，/cancel 取消向导。",
        )

        raw_count = await self._wait_root_text(root_uid)
        action = self._normalize_action(raw_count)
        if action == "cancel":
            raise WizardCancelled()
        if self._is_skip_module_action(action):
            raise WizardSkipModule()
        if action == "skip":
            provider_count = default_count
        else:
            provider_count = max(1, min(5, int(raw_count) if raw_count.isdigit() else default_count))

        providers: list[dict] = []
        idx = 0
        while idx < provider_count:
            existing_item = existing[idx] if idx < len(existing) and isinstance(existing[idx], dict) else {}
            try:
                provider = await self._collect_single_provider(root_uid, idx, existing_item, supported)
            except WizardBack:
                if idx == 0:
                    await self._send_private(root_uid, "已返回 Provider 数量设置。")
                    # 重新询问数量
                    await self._send_private(root_uid, "请重新输入 Provider 数量:")
                    raw_count2 = await self._wait_root_text(root_uid)
                    if self._normalize_action(raw_count2) == "cancel":
                        raise WizardCancelled()
                    provider_count = max(1, min(5, int(raw_count2) if raw_count2.isdigit() else default_count))
                    providers = []
                    idx = 0
                    continue
                idx -= 1
                providers.pop()
                await self._send_private(root_uid, f"已回退到 Provider#{idx + 1}。")
                continue

            providers.append(provider)
            idx += 1

        return providers

    async def _collect_single_provider(
        self,
        root_uid: str,
        idx: int,
        existing_item: dict,
        supported: dict,
    ) -> dict:
        current = {
            "type": str(existing_item.get("type") or "openai-compatible"),
            "base_url": str(existing_item.get("base_url") or ""),
            "healthcheck_model": str(
                existing_item.get("healthcheck_model") or self.bridge.get_config("chat_model", "gpt-4o-mini")
            ),
            "api_key": str(
                existing_item.get("api_key") or os.getenv("SIRIUS_API_KEY", "")
            ),
        }

        # 步骤 0: 平台类型
        await self._send_private(
            root_uid,
            f"Provider#{idx + 1} 平台类型（默认: {current['type']}）\n"
            "直接回复新值，/skip 使用默认值，/skip-module 跳过本模块，/back 返回上一步，/cancel 取消。",
        )
        raw_type = await self._wait_root_text(root_uid)
        action = self._normalize_action(raw_type)
        if action == "cancel":
            raise WizardCancelled()
        if self._is_skip_module_action(action):
            raise WizardSkipModule()
        if action == "back" and idx == 0:
            raise WizardBack()
        if action not in ("skip", "back"):
            current["type"] = raw_type.strip() or current["type"]

        # 自动填充默认 base_url
        default_url = str(supported.get(current["type"], {}).get("default_base_url", ""))
        if default_url:
            current["base_url"] = default_url

        # 步骤 1: 模型
        await self._send_private(
            root_uid,
            f"Provider#{idx + 1} 健康检查模型（默认: {current['healthcheck_model']}）\n"
            "直接回复新值，/skip 使用默认值，/skip-module 跳过本模块，/back 返回上一步，/cancel 取消。",
        )
        raw_model = await self._wait_root_text(root_uid)
        action = self._normalize_action(raw_model)
        if action == "cancel":
            raise WizardCancelled()
        if self._is_skip_module_action(action):
            raise WizardSkipModule()
        if action == "back":
            raise WizardBack()
        if action != "skip":
            current["healthcheck_model"] = raw_model.strip() or current["healthcheck_model"]

        # 步骤 2: API Key
        await self._send_private(
            root_uid,
            f"Provider#{idx + 1} API Key\n"
            "支持直接粘贴 key，或 env:ENV_NAME 格式引用环境变量。\n"
            "/skip 使用默认值，/skip-module 跳过本模块，/back 返回上一步，/cancel 取消。",
        )
        raw_key = await self._wait_root_text(root_uid)
        action = self._normalize_action(raw_key)
        if action == "cancel":
            raise WizardCancelled()
        if self._is_skip_module_action(action):
            raise WizardSkipModule()
        if action == "back":
            raise WizardBack()
        if action != "skip":
            current["api_key"] = raw_key.strip() or current["api_key"]

        resolved_key = _resolve_api_key(current["api_key"])
        if not resolved_key:
            await self._send_private(root_uid, "API Key 无效，将保留当前值。")
            resolved_key = current["api_key"]

        return {
            "type": current["type"],
            "base_url": current["base_url"],
            "healthcheck_model": current["healthcheck_model"],
            "api_key": resolved_key,
            "enabled": True,
        }

    # ─── Orchestration 配置采集 ─────────────────────────

    async def _collect_orchestration_config(self, root_uid: str) -> None:
        """采集模型编排配置（analysis + chat + vision）。"""
        from sirius_chat.core.orchestration_store import OrchestrationStore

        existing = OrchestrationStore.load(self.runtime.work_path)
        analysis_model = existing.get("analysis_model", "gpt-4o-mini")
        chat_model = existing.get("chat_model", "gpt-4o")
        vision_model = existing.get("vision_model", chat_model)

        config = {
            "analysis_model": analysis_model,
            "chat_model": chat_model,
            "vision_model": vision_model,
        }

        # ── 主菜单 ──
        await self._send_private(
            root_uid,
            "=== 模型配置 ===\n"
            f"1) 分析模型（情感/意图/认知/记忆）: {analysis_model}\n"
            f"2) 对话生成模型: {chat_model}\n"
            f"3) 多模态模型（可选）: {vision_model}\n"
            "回复 1/2/3 修改对应项，/skip 保持默认，/cancel 取消向导。",
        )
        raw = await self._wait_root_text(root_uid)
        action = self._normalize_action(raw)
        if action == "cancel":
            raise WizardCancelled()
        if self._is_skip_module_action(action) or action == "skip":
            OrchestrationStore.save(self.runtime.work_path, config)
            await self._send_private(root_uid, "已使用默认模型配置。")
            return

        choice = raw.strip()

        # 如果用户直接输入了模型名（非数字），作为全局默认值
        if choice not in ("1", "2", "3"):
            config["analysis_model"] = choice
            config["chat_model"] = choice
            config["vision_model"] = choice
            OrchestrationStore.save(self.runtime.work_path, config)
            await self._send_private(root_uid, f"已设置全局默认模型: {choice}")
            return

        # 用户选择了 1/2/3，进入单项修改循环
        while choice in ("1", "2", "3"):
            if choice == "1":
                await self._send_private(
                    root_uid,
                    f"请输入新的分析模型（当前: {config['analysis_model']}）\n"
                    "直接回复模型名，/skip 保持不变。",
                )
                m = await self._wait_root_text(root_uid)
                if self._normalize_action(m) != "skip" and m.strip():
                    config["analysis_model"] = m.strip()
            elif choice == "2":
                await self._send_private(
                    root_uid,
                    f"请输入新的对话生成模型（当前: {config['chat_model']}）\n"
                    "直接回复模型名，/skip 保持不变。",
                )
                m = await self._wait_root_text(root_uid)
                if self._normalize_action(m) != "skip" and m.strip():
                    config["chat_model"] = m.strip()
            else:
                await self._send_private(
                    root_uid,
                    f"请输入新的多模态模型（当前: {config['vision_model']}）\n"
                    "直接回复模型名，/skip 保持不变。",
                )
                m = await self._wait_root_text(root_uid)
                if self._normalize_action(m) != "skip" and m.strip():
                    config["vision_model"] = m.strip()

            OrchestrationStore.save(self.runtime.work_path, config)
            await self._send_private(
                root_uid,
                "=== 模型配置已更新 ===\n"
                f"1) 分析模型: {config['analysis_model']}\n"
                f"2) 对话生成模型: {config['chat_model']}\n"
                f"3) 多模态模型: {config['vision_model']}\n"
                "回复 1/2/3 继续修改，/done 完成，/cancel 取消向导。",
            )
            raw = await self._wait_root_text(root_uid)
            action = self._normalize_action(raw)
            if action == "cancel":
                raise WizardCancelled()
            if action in ("done", "skip"):
                break
            choice = raw.strip()

    # ─── Persona 配置采集 ───────────────────────────────

    async def _collect_persona_config(self, root_uid: str) -> PersonaProfile | None:
        existing_persona = PersonaStore.load(self.runtime.work_path)
        skip_hint = ""
        if existing_persona is not None:
            skip_hint = f"当前已有人格: {existing_persona.name}。回复 /skip-module 保留现有人格。\n"

        # 检测持久化记录
        pending_path = Path(self.runtime.work_path) / "engine_state" / "pending_persona_interview.json"
        record_path = Path(self.runtime.work_path) / "engine_state" / "persona_interview_record.json"
        extra_hints: list[str] = []
        if pending_path.exists():
            extra_hints.append("回复 /resume 恢复中断的问卷配置")
        if record_path.exists():
            extra_hints.append("回复 /regenerate 基于已有问卷重新生成人格")
        extra_text = "\n".join(extra_hints)
        if extra_text:
            extra_text = "\n" + extra_text + "\n"

        await self._send_private(
            root_uid,
            "=== 人格配置 ===\n"
            f"{skip_hint}"
            "请选择人格创建方式:\n"
            "1) 基于关键词生成\n"
            "2) 基于问卷深度定制（推荐，需 LLM 生成）\n"
            f"{extra_text}"
            "回复 1/2，/cancel 取消。",
        )

        choice = await self._wait_root_text(root_uid)
        action = self._normalize_action(choice)
        if action == "cancel":
            raise WizardCancelled()
        if self._is_skip_module_action(action):
            raise WizardSkipModule()
        if action == "skip":
            await self._send_private(root_uid, "人格配置不能跳过，必须显式选择创建方式。")
            raise WizardCancelled()
        if action == "resume" and pending_path.exists():
            persona = await self._resume_pending_interview(root_uid)
            await self._collect_backstory(root_uid, persona)
            return persona
        if action == "regenerate" and record_path.exists():
            persona = await self._regenerate_from_record(root_uid)
            await self._collect_backstory(root_uid, persona)
            return persona

        if choice.strip() == "1":
            persona = await self._collect_persona_by_keywords(root_uid)
        elif choice.strip() == "2":
            persona = await self._collect_persona_by_interview(root_uid)
        else:
            await self._send_private(root_uid, "无效选项，请回复 1 或 2。")
            return await self._collect_persona_config(root_uid)

        # 统一采集背景故事（可选）
        await self._collect_backstory(root_uid, persona)
        return persona

    async def _collect_aliases(self, root_uid: str) -> list[str]:
        """采集角色别名/昵称，用空格分隔多个。"""
        await self._send_private(
            root_uid,
            "请输入角色的别名或昵称（用空格分隔多个，如: 小月 月白酱）\n"
            "/skip 不设置别名，/cancel 取消向导。",
        )
        raw = await self._wait_root_text(root_uid)
        action = self._normalize_action(raw)
        if action == "cancel":
            raise WizardCancelled()
        if action == "skip":
            return []
        aliases = [a.strip() for a in raw.split() if a.strip()]
        return aliases

    def _archive_existing_persona(self) -> None:
        """如果已有 persona.json，将其归档到 engine_state/archive/ 目录。"""
        persona_path = Path(self.runtime.work_path) / "engine_state" / "persona.json"
        if not persona_path.exists():
            return
        archive_dir = Path(self.runtime.work_path) / "engine_state" / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        archive_path = archive_dir / f"persona_{ts}.json"
        try:
            archive_path.write_text(persona_path.read_text(encoding="utf-8"), encoding="utf-8")
            LOG.info("旧人格已归档: %s", archive_path)
        except OSError as exc:
            LOG.warning("人格归档失败: %s", exc)

    async def _collect_backstory(self, root_uid: str, persona: PersonaProfile) -> None:
        """可选步骤：采集角色的背景故事（起源、经历、世界观），支持 LLM 自动生成。"""
        await self._send_private(
            root_uid,
            "=== 人格背景（可选）===\n"
            "你可以为角色设置一段背景故事，这将影响 TA 的说话方式和情感反应。\n"
            "回复 /generate 让 LLM 根据已有设定自动生成，\n"
            "直接输入文字自定义，/skip 跳过此步骤，/cancel 取消向导。",
        )
        raw = await self._wait_root_text(root_uid)
        action = self._normalize_action(raw)
        if action == "cancel":
            raise WizardCancelled()
        if action == "skip":
            return
        if action == "generate":
            backstory = await self._generate_backstory(root_uid, persona)
            if backstory:
                persona.backstory = backstory
            return
        backstory = raw.strip()
        if backstory:
            persona.backstory = backstory
            await self._send_private(
                root_uid,
                f"已设置背景故事（{len(backstory)} 字）。",
            )

    async def _generate_backstory(
        self, root_uid: str, persona: PersonaProfile
    ) -> str | None:
        """使用 LLM 根据已有 persona 信息生成背景故事。"""
        from sirius_chat.providers.base import GenerationRequest
        from sirius_chat.core.orchestration_store import OrchestrationStore

        orch = OrchestrationStore.load(self.runtime.work_path)
        model = orch.get("analysis_model", "gpt-4o-mini")

        prompt = (
            f"请为以下群聊角色写一段背景故事（200-400字），包含成长经历、"
            f"关键转折点、世界观形成过程等，使其与已有设定一致。\n\n"
            f"角色名称: {persona.name}\n"
            f"一句话描述: {persona.persona_summary or '未指定'}\n"
            f"性格标签: {', '.join(persona.personality_traits[:8])}\n"
            f"社交角色: {persona.social_role or '未指定'}\n"
            f"说话风格: {persona.communication_style or '未指定'}\n"
            f"共情方式: {persona.empathy_style or '未指定'}\n"
            f"幽默风格: {persona.humor_style or '未指定'}\n"
            f"边界: {', '.join(persona.boundaries[:5])}\n\n"
            f"只输出背景故事正文，不要其他内容。"
        )

        request = GenerationRequest(
            model=model,
            system_prompt="",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=1024,
            purpose="persona_generate",
        )

        provider = self.runtime._build_provider()
        if provider is None:
            await self._send_private(root_uid, "未配置 Provider，无法生成背景故事。")
            return None

        await self._send_private(root_uid, "正在通过 LLM 生成背景故事，请稍候...")
        try:
            if hasattr(provider, "generate_async"):
                raw = await provider.generate_async(request)
            else:
                import asyncio
                raw = await asyncio.to_thread(provider.generate, request)
        except Exception as exc:
            LOG.warning("背景故事 LLM 生成失败: %s", exc)
            await self._send_private(root_uid, f"生成失败: {exc}\n请手动输入背景故事，/skip 跳过。")
            return None

        backstory = raw.strip()
        if not backstory:
            await self._send_private(root_uid, "生成结果为空，请手动输入背景故事，/skip 跳过。")
            return None

        # 展示给用户确认
        await self._send_private(
            root_uid,
            f"=== 生成的背景故事 ===\n"
            f"{backstory}\n\n"
            f"回复 /ok 确认使用，/regenerate 重新生成，或直接输入修改后的内容。",
        )
        confirm = await self._wait_root_text(root_uid)
        c_action = self._normalize_action(confirm)
        if c_action == "ok":
            await self._send_private(root_uid, f"已设置背景故事（{len(backstory)} 字）。")
            return backstory
        if c_action == "regenerate":
            return await self._generate_backstory(root_uid, persona)
        if c_action == "skip":
            return None
        # 用户直接输入了修改内容
        modified = confirm.strip()
        if modified:
            await self._send_private(root_uid, f"已设置背景故事（{len(modified)} 字）。")
            return modified
        return None

    async def _collect_persona_by_interview(self, root_uid: str) -> PersonaProfile:
        """基于问卷深度定制人格（与旧版 roleplay-prompting 一致的交互流程）。"""
        await self._send_private(
            root_uid,
            "=== 人格问卷深度定制 ===\n"
            "我将逐个提问，请根据你心目中的角色形象回答。\n"
            "回复 /skip 使用默认答案，/done 提前结束剩余问题，/cancel 取消。",
        )

        # 询问角色名称
        await self._send_private(
            root_uid,
            "请输入角色名称（默认: 小星）\n/skip 使用默认名称，/cancel 取消。",
        )
        name_raw = await self._wait_root_text(root_uid)
        if self._normalize_action(name_raw) == "cancel":
            raise WizardCancelled()
        name = name_raw.strip() if name_raw.strip() and self._normalize_action(name_raw) != "skip" else "小星"

        # 询问别名
        aliases = await self._collect_aliases(root_uid)

        # 逐个问题采集
        answers: dict[str, str] = {}
        total = len(_INTERVIEW_QUESTIONS)
        for i, question in enumerate(_INTERVIEW_QUESTIONS, 1):
            await self._send_private(
                root_uid,
                f"第 {i}/{total} 题:\n{question}\n"
                f"默认答案: （无）\n"
                f"回复 /skip 跳过本题，/done 结束剩余题目，/cancel 取消。",
            )
            ans = await self._wait_root_text(root_uid)
            action = self._normalize_action(ans)
            if action == "cancel":
                raise WizardCancelled()
            if action == "done":
                break
            if action != "skip" and ans.strip():
                answers[str(i)] = ans.strip()

        if not answers:
            await self._send_private(root_uid, "未回答任何问题，人格配置已取消。")
            raise WizardCancelled()

        await self._send_private(root_uid, f"已收集 {len(answers)} 个回答，正在通过 LLM 生成人格设定，请稍候...")

        persona = await self._generate_persona_from_interview(name, answers, aliases)

        await self._send_private(
            root_uid,
            f"✅ 人格生成完成！\n"
            f"名称: {persona.name}\n"
            f"一句话描述: {persona.persona_summary or '未生成'}\n"
            f"性格标签: {', '.join(persona.personality_traits[:5])}\n"
            f"社交角色: {persona.social_role or '未指定'}\n"
            f"说话风格: {persona.communication_style or '未指定'}",
        )
        return persona

    async def _generate_persona_from_interview(
        self, name: str, answers: dict[str, str], aliases: list[str] | None = None
    ) -> PersonaProfile:
        """async 版本的 interview 人格生成（绕过 sync 的 from_interview）。"""
        provider = self.runtime._build_provider()
        return await generate_persona_from_interview(
            work_path=self.runtime.work_path,
            provider=provider,
            name=name,
            answers=answers,
            aliases=aliases,
        )

    async def _collect_persona_by_keywords(self, root_uid: str) -> PersonaProfile:
        await self._send_private(
            root_uid,
            "请输入描述角色性格的关键词（用空格分隔）\n"
            "例如: 温柔 猫奴 二次元\n"
            "/cancel 取消。",
        )
        raw = await self._wait_root_text(root_uid)
        if self._normalize_action(raw) == "cancel":
            raise WizardCancelled()

        keywords = [k.strip() for k in raw.split() if k.strip()]
        if not keywords:
            keywords = ["温暖", "包容"]

        await self._send_private(
            root_uid,
            "请输入角色名称（默认: 小星）\n/skip 使用默认名称，/cancel 取消。",
        )
        name_raw = await self._wait_root_text(root_uid)
        if self._normalize_action(name_raw) == "cancel":
            raise WizardCancelled()
        name = name_raw.strip() if name_raw.strip() and self._normalize_action(name_raw) != "skip" else "小星"

        # 询问别名
        aliases = await self._collect_aliases(root_uid)

        # 尝试用 provider 进行 LLM 精修
        provider = self.runtime._build_provider()
        if provider is not None and hasattr(provider, "generate_async"):
            try:
                persona = PersonaGenerator.from_keywords(name, keywords, provider_async=provider)
            except Exception as exc:
                LOG.warning("关键词人格 LLM 精修失败，回退到纯规则: %s", exc)
                persona = PersonaGenerator.from_keywords(name, keywords)
        else:
            persona = PersonaGenerator.from_keywords(name, keywords)

        persona.aliases = aliases

        await self._send_private(
            root_uid,
            f"已基于关键词生成人格: {persona.name}\n"
            f"别名: {', '.join(aliases) if aliases else '无'}\n"
            f"性格标签: {', '.join(persona.personality_traits[:5])}\n"
            f"社交角色: {persona.social_role or '未指定'}",
        )
        return persona

    async def _resume_pending_interview(self, root_uid: str) -> PersonaProfile:
        """恢复中断的问卷配置，从 pending 文件继续生成。"""
        pending_path = Path(self.runtime.work_path) / "engine_state" / "pending_persona_interview.json"
        if not pending_path.exists():
            await self._send_private(root_uid, "未找到中断的问卷记录，将开始新的问卷配置。")
            return await self._collect_persona_by_interview(root_uid)

        try:
            record = json.loads(pending_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            LOG.warning("读取 pending interview 失败: %s", exc)
            await self._send_private(root_uid, "读取中断记录失败，将开始新的问卷配置。")
            return await self._collect_persona_by_interview(root_uid)

        name = record.get("name", "小星")
        aliases = record.get("aliases", [])
        answers = record.get("answers", {})

        await self._send_private(
            root_uid,
            f"=== 恢复中断的问卷配置 ===\n"
            f"角色名称: {name}\n"
            f"别名: {', '.join(aliases) if aliases else '无'}\n"
            f"已回答问题: {len(answers)}/{len(_INTERVIEW_QUESTIONS)}\n"
            f"回复 /continue 继续生成（使用已有回答），\n"
            f"/restart 重新开始问卷，/cancel 取消。",
        )
        confirm = await self._wait_root_text(root_uid)
        action = self._normalize_action(confirm)
        if action == "cancel":
            raise WizardCancelled()
        if action == "restart":
            return await self._collect_persona_by_interview(root_uid)

        await self._send_private(root_uid, "正在通过 LLM 生成人格设定，请稍候...")
        persona = await self._generate_persona_from_interview(name, answers, aliases)
        await self._send_private(
            root_uid,
            f"✅ 人格生成完成！\n"
            f"名称: {persona.name}\n"
            f"别名: {', '.join(persona.aliases) if persona.aliases else '无'}\n"
            f"一句话描述: {persona.persona_summary or '未生成'}\n"
            f"性格标签: {', '.join(persona.personality_traits[:5])}\n"
            f"社交角色: {persona.social_role or '未指定'}\n"
            f"说话风格: {persona.communication_style or '未指定'}",
        )
        return persona

    async def _regenerate_from_record(self, root_uid: str) -> PersonaProfile:
        """基于已有的问卷记录重新生成人格（可更换模型后获得不同效果）。"""
        record_path = Path(self.runtime.work_path) / "engine_state" / "persona_interview_record.json"
        if not record_path.exists():
            await self._send_private(root_uid, "未找到问卷记录，无法重新生成。")
            raise WizardCancelled()

        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            LOG.warning("读取 interview record 失败: %s", exc)
            await self._send_private(root_uid, "读取问卷记录失败，无法重新生成。")
            raise WizardCancelled()

        name = record.get("name", "小星")
        aliases = record.get("aliases", [])
        answers = record.get("answers", {})

        await self._send_private(
            root_uid,
            f"=== 基于已有问卷重新生成 ===\n"
            f"角色名称: {name}\n"
            f"别名: {', '.join(aliases) if aliases else '无'}\n"
            f"历史回答数: {len(answers)}\n"
            f"回复 /ok 确认重新生成，/cancel 取消。",
        )
        confirm = await self._wait_root_text(root_uid)
        action = self._normalize_action(confirm)
        if action == "cancel":
            raise WizardCancelled()

        await self._send_private(root_uid, "正在通过 LLM 重新生成人格设定，请稍候...")
        persona = await self._generate_persona_from_interview(name, answers, aliases)
        await self._send_private(
            root_uid,
            f"✅ 人格重新生成完成！\n"
            f"名称: {persona.name}\n"
            f"别名: {', '.join(persona.aliases) if persona.aliases else '无'}\n"
            f"一句话描述: {persona.persona_summary or '未生成'}\n"
            f"性格标签: {', '.join(persona.personality_traits[:5])}\n"
            f"社交角色: {persona.social_role or '未指定'}\n"
            f"说话风格: {persona.communication_style or '未指定'}",
        )
        return persona

    # ─── 配置持久化辅助 ─────────────────────────────────

    def _current_provider_configs(self) -> list[dict]:
        """从全局 ProviderRegistry 读取已有 provider 配置作为默认值。"""
        try:
            from sirius_chat.providers.routing import ProviderRegistry
            global_path = self.runtime.work_path.parent.parent
            registry = ProviderRegistry(global_path)
            loaded = registry.load()
            if not loaded:
                # 回退到人格目录（兼容旧版）
                registry = ProviderRegistry(self.runtime.work_path)
                loaded = registry.load()
            return [
                {
                    "type": cfg.provider_type,
                    "base_url": cfg.base_url,
                    "healthcheck_model": cfg.healthcheck_model,
                    "api_key": cfg.api_key,
                    "enabled": cfg.enabled,
                    "models": list(cfg.models),
                }
                for cfg in loaded.values()
            ]
        except Exception:
            return []

    def _save_providers_to_registry(self, providers: list[dict]) -> None:
        """将 provider 列表持久化到全局 ProviderRegistry。"""
        try:
            from sirius_chat.providers.routing import ProviderRegistry, ProviderConfig
            global_path = self.runtime.work_path.parent.parent
            registry = ProviderRegistry(global_path)
            entries: dict[str, ProviderConfig] = {}
            for item in providers:
                ptype = str(item.get("type", "")).strip()
                if not ptype:
                    continue
                entries[ptype] = ProviderConfig(
                    provider_type=ptype,
                    api_key=str(item.get("api_key", "")).strip(),
                    base_url=str(item.get("base_url", "")).strip(),
                    healthcheck_model=str(item.get("healthcheck_model", "")).strip(),
                    enabled=bool(item.get("enabled", True)),
                    models=list(item.get("models", []) or []),
                )
            registry.save(entries)
            LOG.info("Provider 配置已持久化到全局 provider_keys.json")
        except Exception as exc:
            LOG.warning("Provider 持久化失败: %s", exc)

    def _snapshot_runtime_state(self) -> dict[str, Any]:
        return {
            "providers": self._current_provider_configs(),
            "data": {
                "setup_completed": self.bridge.data.get("setup_completed", False),
                "setup_wizard_notified": self.bridge.data.get("setup_wizard_notified", False),
            },
        }

    def _restore_runtime_state(self, snapshot: dict[str, Any]) -> None:
        providers = snapshot.get("providers", [])
        if providers:
            self._save_providers_to_registry(providers)
        elif not providers:
            # 如果之前没有配置，尝试删除全局 provider_keys.json
            try:
                global_path = self.runtime.work_path.parent.parent
                pk = global_path / "providers" / "provider_keys.json"
                if pk.exists():
                    pk.unlink()
            except OSError:
                pass

        data = snapshot.get("data", {})
        self.bridge.data["setup_completed"] = bool(data.get("setup_completed", False))
        self.bridge.data["setup_wizard_notified"] = bool(data.get("setup_wizard_notified", False))
        self.bridge.save_data()

    def _resolve_root_user_id(self) -> str:
        project_config = Path(__file__).resolve().parents[2] / "config.yaml"
        if project_config.exists():
            try:
                text = project_config.read_text(encoding="utf-8")
                for raw in text.splitlines():
                    line = raw.strip()
                    if line.startswith("root:"):
                        value = line.split(":", 1)[1].strip().strip("'\"")
                        if value:
                            return value
            except OSError:
                pass
        return ""

    async def _try_init_engine(self) -> None:
        """向导完成后尝试预热引擎。"""
        try:
            _ = self.runtime.engine
            LOG.info("引擎预热成功")
        except Exception as exc:
            LOG.warning("引擎预热失败: %s", exc)
