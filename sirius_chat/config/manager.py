"""Configuration management framework for Sirius Chat.

Provides loading, validation, and merging of configurations from JSON files,
environment variables, and secret management.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from sirius_chat.config.helpers import build_orchestration_policy_from_dict
from sirius_chat.config.jsonc import (
    build_default_orchestration_payload,
    load_json_document,
    write_session_config_jsonc,
)
from sirius_chat.config.models import (
    Agent,
    AgentPreset,
    ProviderPolicy,
    SessionConfig,
    SessionDefaults,
    WorkspaceConfig,
)
from sirius_chat.workspace.layout import WorkspaceLayout


class ConfigManager:
    """Manages configuration loading, validation, and merging."""

    # Pattern for environment variable substitution: ${VAR_NAME}
    _ENV_VAR_PATTERN = re.compile(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}')

    def __init__(self, base_path: Path | None = None) -> None:
        """Initialize ConfigManager.
        
        Args:
            base_path: Base path for resolving relative config paths.
                      Defaults to package root.
        """
        if base_path is None:
            base_path = Path(__file__).parent
        self.base_path = base_path

    def load_from_json(self, path: Path | str) -> SessionConfig:
        """Load configuration from JSON file.
        
        Args:
            path: Path to JSON config file
            
        Returns:
            SessionConfig instance
            
        Raises:
            FileNotFoundError: If config file not found
            ValueError: If config is invalid
        """
        config_path = Path(path)
        if not config_path.is_absolute():
            config_path = self.base_path / config_path

        if not config_path.exists():
            raise FileNotFoundError(f"配置文件不存在：{config_path}")

        raw_dict = load_json_document(config_path)

        # Resolve environment variables and secrets
        resolved = self._resolve_values(raw_dict)

        # Validate required fields
        self._validate_config(resolved)

        # Build SessionConfig from dict
        return self._dict_to_session_config(resolved, config_path.parent)

    def load_from_env(self, env: str = "dev") -> SessionConfig:
        """Load configuration for a specific environment.
        
        Args:
            env: Environment name (dev, test, prod)
            
        Returns:
            SessionConfig instance
            
        Raises:
            ValueError: If environment not found
        """
        env_mapping = {
            "dev": "dev.json",
            "test": "test.json",
            "prod": "prod.json",
        }

        if env not in env_mapping:
            raise ValueError(f"未知环境：{env}。必须是：{list(env_mapping.keys())}")

        config_file = self.base_path / "presets" / env_mapping[env]
        if not config_file.exists():
            raise FileNotFoundError(f"环境 '{env}' 的配置文件不存在：{config_file}")

        return self.load_from_json(config_file)

    def merge_configs(
        self,
        base: dict[str, Any],
        override: dict[str, Any],
    ) -> dict[str, Any]:
        """Merge two configuration dictionaries.
        
        Override dict takes precedence over base dict.
        
        Args:
            base: Base configuration
            override: Override configuration
            
        Returns:
            Merged configuration
        """
        merged = dict(base)
        for key, value in override.items():
            if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
                merged[key] = self.merge_configs(merged[key], value)
            else:
                merged[key] = value
        return merged

    def _coerce_int(self, value: object, default: int) -> int:
        if value is None or value == "":
            return default
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float, str)):
            try:
                return int(value)
            except (TypeError, ValueError):
                return default
        return default

    def _coerce_bool(self, value: object, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "on"}:
                return True
            if normalized in {"false", "0", "no", "off"}:
                return False
        return bool(value)

    def _coerce_string(self, value: object, default: str = "") -> str:
        if value is None:
            return default
        text = str(value).strip()
        return text if text else default

    def _coerce_path(self, value: object, default: Path) -> Path:
        if value is None:
            return default
        text = str(value).strip()
        return Path(text) if text else default

    def _sanitize_nullable_list(self, value: object) -> list[Any]:
        if not isinstance(value, list):
            return []

        sanitized: list[Any] = []
        for item in value:
            if item is None:
                continue
            if isinstance(item, dict):
                sanitized.append(self._sanitize_nullable_mapping(item))
                continue
            if isinstance(item, list):
                sanitized.append(self._sanitize_nullable_list(item))
                continue
            sanitized.append(item)
        return sanitized

    def _sanitize_nullable_mapping(
        self,
        value: object,
        *,
        fallback: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        sanitized = dict(fallback or {})
        if not isinstance(value, dict):
            return sanitized

        for key, item in value.items():
            key_str = str(key)
            if item is None:
                continue
            existing = sanitized.get(key_str)
            if isinstance(item, dict):
                sanitized[key_str] = self._sanitize_nullable_mapping(
                    item,
                    fallback=existing if isinstance(existing, dict) else None,
                )
                continue
            if isinstance(item, list):
                sanitized[key_str] = self._sanitize_nullable_list(item)
                continue
            sanitized[key_str] = item
        return sanitized

    def _build_session_defaults(self, payload: object, fallback: SessionDefaults) -> SessionDefaults:
        if not isinstance(payload, dict):
            return SessionDefaults(
                history_max_messages=fallback.history_max_messages,
                history_max_chars=fallback.history_max_chars,
                max_recent_participant_messages=fallback.max_recent_participant_messages,
                enable_auto_compression=fallback.enable_auto_compression,
            )

        return SessionDefaults(
            history_max_messages=self._coerce_int(
                payload.get("history_max_messages"),
                fallback.history_max_messages,
            ),
            history_max_chars=self._coerce_int(
                payload.get("history_max_chars"),
                fallback.history_max_chars,
            ),
            max_recent_participant_messages=self._coerce_int(
                payload.get("max_recent_participant_messages"),
                fallback.max_recent_participant_messages,
            ),
            enable_auto_compression=self._coerce_bool(
                payload.get("enable_auto_compression"),
                fallback.enable_auto_compression,
            ),
        )

    def _build_workspace_config_from_payload(
        self,
        payload: dict[str, Any],
        *,
        layout: WorkspaceLayout,
        fallback: WorkspaceConfig,
    ) -> WorkspaceConfig:
        session_defaults = self._build_session_defaults(
            payload.get("session_defaults"),
            fallback.session_defaults,
        )
        provider_policy_payload = payload.get("provider_policy")
        provider_policy_default = fallback.provider_policy.prefer_workspace_registry

        return WorkspaceConfig(
            work_path=self._coerce_path(payload.get("work_path"), layout.config_root),
            data_path=self._coerce_path(payload.get("data_path"), layout.data_root),
            layout_version=self._coerce_int(payload.get("layout_version"), layout.layout_version),
            active_agent_key=self._coerce_string(
                payload.get("active_agent_key"),
                fallback.active_agent_key,
            ),
            session_defaults=session_defaults,
            orchestration_defaults=self._sanitize_nullable_mapping(
                payload.get("orchestration_defaults"),
                fallback=dict(fallback.orchestration_defaults),
            ),
            provider_policy=ProviderPolicy(
                prefer_workspace_registry=self._coerce_bool(
                    provider_policy_payload.get("prefer_workspace_registry")
                    if isinstance(provider_policy_payload, dict)
                    else None,
                    provider_policy_default,
                )
            ),
        )

    def _normalize_workspace_config(
        self,
        config: WorkspaceConfig,
        *,
        layout: WorkspaceLayout,
        fallback: WorkspaceConfig,
    ) -> WorkspaceConfig:
        session_defaults_payload = {
            "history_max_messages": getattr(config.session_defaults, "history_max_messages", None),
            "history_max_chars": getattr(config.session_defaults, "history_max_chars", None),
            "max_recent_participant_messages": getattr(
                config.session_defaults,
                "max_recent_participant_messages",
                None,
            ),
            "enable_auto_compression": getattr(
                config.session_defaults,
                "enable_auto_compression",
                None,
            ),
        }
        provider_policy_payload = {
            "prefer_workspace_registry": getattr(
                config.provider_policy,
                "prefer_workspace_registry",
                None,
            )
        }

        return WorkspaceConfig(
            work_path=layout.config_root,
            data_path=layout.data_root,
            layout_version=layout.layout_version,
            active_agent_key=self._coerce_string(
                getattr(config, "active_agent_key", None),
                fallback.active_agent_key,
            ),
            session_defaults=self._build_session_defaults(
                session_defaults_payload,
                fallback.session_defaults,
            ),
            orchestration_defaults=self._sanitize_nullable_mapping(
                getattr(config, "orchestration_defaults", None),
                fallback=dict(fallback.orchestration_defaults),
            ),
            provider_policy=ProviderPolicy(
                prefer_workspace_registry=self._coerce_bool(
                    provider_policy_payload.get("prefer_workspace_registry"),
                    fallback.provider_policy.prefer_workspace_registry,
                )
            ),
        )

    def load_workspace_config(
        self,
        work_path: Path | str,
        *,
        data_path: Path | str | None = None,
    ) -> WorkspaceConfig:
        """Load workspace-level config, creating defaults when missing."""
        config_root = Path(work_path)
        runtime_root = Path(data_path) if data_path is not None else config_root
        layout = WorkspaceLayout(runtime_root, config_path=config_root)
        layout.ensure_directories()
        manifest_path = layout.workspace_manifest_path()

        manifest_payload: dict[str, Any] = {}
        manifest_mtime_ns = -1
        if manifest_path.exists():
            payload = load_json_document(manifest_path)
            if isinstance(payload, dict):
                manifest_payload = payload
                manifest_mtime_ns = manifest_path.stat().st_mtime_ns

        session_snapshot = self._load_workspace_session_snapshot(layout)
        session_snapshot_mtime_ns = -1
        session_snapshot_path = layout.session_config_path()
        if session_snapshot and session_snapshot_path.exists():
            session_snapshot_mtime_ns = session_snapshot_path.stat().st_mtime_ns
        default_config = WorkspaceConfig(
            work_path=layout.config_root,
            data_path=layout.data_root,
            layout_version=layout.layout_version,
        )
        if manifest_payload:
            config = self._build_workspace_config_from_payload(
                manifest_payload,
                layout=layout,
                fallback=default_config,
            )
        else:
            config = default_config

        config.work_path = layout.config_root
        config.data_path = layout.data_root
        config.layout_version = layout.layout_version

        # workspace.json is the machine-readable workspace manifest, while
        # config/session_config.json is the human-editable session snapshot.
        # Keep the snapshot authoritative for session defaults and
        # orchestration so a newer manifest write cannot shadow task model
        # edits made through settings.
        if session_snapshot:
            generated_agent_key = self._coerce_string(
                session_snapshot.get("generated_agent_key"),
                config.active_agent_key,
            )
            if generated_agent_key and (
                not manifest_payload
                or session_snapshot_mtime_ns >= manifest_mtime_ns
                or not config.active_agent_key
            ):
                config.active_agent_key = generated_agent_key
            config.session_defaults = self._build_session_defaults(
                session_snapshot,
                config.session_defaults,
            )
            config.orchestration_defaults = self._sanitize_nullable_mapping(
                session_snapshot.get("orchestration"),
                fallback=dict(config.orchestration_defaults),
            )

        if not config.active_agent_key:
            config.active_agent_key = self._resolve_active_agent_key(layout)
        return config

    def save_workspace_config(
        self,
        work_path: Path | str,
        config: WorkspaceConfig,
        *,
        data_path: Path | str | None = None,
    ) -> None:
        """Persist workspace-level config and a human-readable session snapshot."""
        config_root = Path(work_path)
        runtime_root_source = data_path if data_path is not None else (config.data_path or config.work_path)
        runtime_root = Path(runtime_root_source)
        layout = WorkspaceLayout(runtime_root, config_path=config_root)
        layout.ensure_directories()
        existing_config = self.load_workspace_config(layout.config_root, data_path=layout.data_root)
        normalized_config = self._normalize_workspace_config(
            config,
            layout=layout,
            fallback=existing_config,
        )
        payload = normalized_config.to_dict()
        manifest_path = layout.workspace_manifest_path()
        manifest_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        session_snapshot = {
            "generated_agent_key": normalized_config.active_agent_key,
            "history_max_messages": normalized_config.session_defaults.history_max_messages,
            "history_max_chars": normalized_config.session_defaults.history_max_chars,
            "max_recent_participant_messages": normalized_config.session_defaults.max_recent_participant_messages,
            "enable_auto_compression": normalized_config.session_defaults.enable_auto_compression,
            "orchestration": self.merge_configs(
                build_default_orchestration_payload(),
                dict(normalized_config.orchestration_defaults),
            ),
        }
        write_session_config_jsonc(layout.session_config_path(), session_snapshot)

    def build_session_config(
        self,
        *,
        work_path: Path | str,
        data_path: Path | str | None = None,
        session_id: str,
        overrides: dict[str, Any] | None = None,
    ) -> SessionConfig:
        """Build a runtime SessionConfig from workspace config + roleplay assets."""
        from sirius_chat.roleplay_prompting import load_generated_agent_library

        config_root = Path(work_path)
        runtime_root = Path(data_path) if data_path is not None else config_root
        layout = WorkspaceLayout(runtime_root, config_path=config_root)
        workspace_config = self.load_workspace_config(layout.config_root, data_path=layout.data_root)
        agents, selected = load_generated_agent_library(layout.config_root)
        agent_key = str((overrides or {}).get("agent_key", "")).strip()
        resolved_agent_key = agent_key or workspace_config.active_agent_key or selected
        if not resolved_agent_key:
            raise ValueError("当前 workspace 尚未选择 generated agent。")
        if resolved_agent_key not in agents:
            raise ValueError(f"找不到生成的主教：{resolved_agent_key}")

        preset = agents[resolved_agent_key]
        session_defaults = workspace_config.session_defaults
        override_payload = dict(overrides or {})
        session_config = SessionConfig(
            work_path=layout.config_root,
            data_path=layout.data_root,
            preset=AgentPreset(
                agent=Agent(
                    name=preset.agent.name,
                    persona=preset.agent.persona,
                    model=preset.agent.model,
                    temperature=preset.agent.temperature,
                    max_tokens=preset.agent.max_tokens,
                    metadata=dict(preset.agent.metadata),
                ),
                global_system_prompt=preset.global_system_prompt,
            ),
            history_max_messages=int(
                override_payload.get(
                    "history_max_messages",
                    session_defaults.history_max_messages,
                )
            ),
            history_max_chars=int(
                override_payload.get("history_max_chars", session_defaults.history_max_chars)
            ),
            max_recent_participant_messages=int(
                override_payload.get(
                    "max_recent_participant_messages",
                    session_defaults.max_recent_participant_messages,
                )
            ),
            enable_auto_compression=bool(
                override_payload.get(
                    "enable_auto_compression",
                    session_defaults.enable_auto_compression,
                )
            ),
            orchestration=build_orchestration_policy_from_dict(
                dict(workspace_config.orchestration_defaults),
                agent_model=preset.agent.model,
            ),
            session_id=session_id,
        )
        return session_config


    def _resolve_values(self, obj: Any) -> Any:
        """Recursively resolve environment variables in configuration.
        
        Supports ${VAR_NAME} syntax for environment variable substitution.
        """
        if isinstance(obj, str):
            return self._resolve_env_vars(obj)
        elif isinstance(obj, dict):
            return {k: self._resolve_values(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._resolve_values(item) for item in obj]
        return obj

    def _resolve_env_vars(self, text: str) -> str:
        """Resolve environment variables in a string.
        
        Replaces ${VAR_NAME} with corresponding environment variable value.
        If variable not found, leaves placeholder unchanged.
        """
        def replacer(match: re.Match[str]) -> str:
            var_name = match.group(1)
            return os.environ.get(var_name, match.group(0))

        return self._ENV_VAR_PATTERN.sub(replacer, text)

    def _load_workspace_session_snapshot(self, layout: WorkspaceLayout) -> dict[str, Any]:
        session_config_path = layout.session_config_path()
        if session_config_path.exists():
            payload = load_json_document(session_config_path)
            if isinstance(payload, dict):
                return payload
        return {}

    def _resolve_active_agent_key(self, layout: WorkspaceLayout) -> str:
        candidate_paths = [
            layout.generated_agents_path(),
            layout.legacy_generated_agents_path(),
        ]
        for path in candidate_paths:
            if not path.exists():
                continue
            payload = load_json_document(path)
            if not isinstance(payload, dict):
                continue
            selected = str(payload.get("selected_generated_agent", "")).strip()
            if selected:
                return selected
        return ""

    def _validate_config(self, config: dict[str, Any]) -> None:
        """Validate configuration structure.
        
        Raises:
            ValueError: If required fields are missing or invalid
        """
        # Check required top-level keys
        required_keys = {"work_path", "agent", "orchestration"}
        missing = required_keys - set(config.keys())
        if missing:
            raise ValueError(f"缺少必要配置键：{missing}")

        # Validate agent section
        agent_config = config.get("agent", {})
        agent_required = {"name", "persona", "model"}
        agent_missing = agent_required - set(agent_config.keys())
        if agent_missing:
            raise ValueError(f"缺少必要的主角配置键：{agent_missing}")

        # Validate work_path can be converted to Path
        try:
            Path(config["work_path"])
        except (TypeError, ValueError) as e:
            raise ValueError(f"无效的 work_path：{e}")
        if "data_path" in config:
            try:
                Path(config["data_path"])
            except (TypeError, ValueError) as e:
                raise ValueError(f"无效的 data_path：{e}")

    def _dict_to_session_config(self, config_dict: dict[str, Any], base_dir: Path) -> SessionConfig:
        """Convert configuration dictionary to SessionConfig.
        
        Args:
            config_dict: Raw configuration dictionary
            base_dir: Base directory for resolving relative paths
            
        Returns:
            SessionConfig instance
        """
        work_path = Path(config_dict["work_path"])
        if not work_path.is_absolute():
            work_path = base_dir / work_path

        data_path_raw = config_dict.get("data_path", config_dict["work_path"])
        data_path = Path(data_path_raw)
        if not data_path.is_absolute():
            data_path = base_dir / data_path

        # Build Agent
        agent_dict = config_dict.get("agent", {})
        agent = Agent(
            name=agent_dict.get("name", ""),
            persona=agent_dict.get("persona", ""),
            model=agent_dict.get("model", ""),
            temperature=float(agent_dict.get("temperature", 0.7)),
            max_tokens=int(agent_dict.get("max_tokens", 512)),
            metadata=agent_dict.get("metadata", {}),
        )

        # Build AgentPreset
        preset = AgentPreset(
            agent=agent,
            global_system_prompt=config_dict.get("global_system_prompt", ""),
        )

        orchestration = build_orchestration_policy_from_dict(
            config_dict.get("orchestration", {}),
            agent_model=agent.model,
        )

        # Build SessionConfig
        return SessionConfig(
            work_path=work_path,
            data_path=data_path,
            preset=preset,
            history_max_messages=int(config_dict.get("history_max_messages", 24)),
            history_max_chars=int(config_dict.get("history_max_chars", 6000)),
            max_recent_participant_messages=int(
                config_dict.get("max_recent_participant_messages", 5)
            ),
            enable_auto_compression=config_dict.get("enable_auto_compression", True),
            orchestration=orchestration,
        )

    def bootstrap_workspace_from_legacy_session_json(
        self,
        config_path: Path | str,
        *,
        work_path: Path | str,
        data_path: Path | str | None = None,
    ) -> tuple[WorkspaceConfig, list[dict[str, Any]]]:
        """Bootstrap workspace config from a legacy session.json file.

        Reads a session.json (or JSONC) config, extracts workspace-level
        defaults and provider entries, persists them into the workspace
        layout, and returns the resolved WorkspaceConfig and provider list.
        """
        path = Path(config_path)
        if not path.exists():
            raise FileNotFoundError(f"配置文件不存在：{path}")
        raw = load_json_document(path)
        if not isinstance(raw, dict):
            raise ValueError("配置文件顶层必须是对象")

        config_root = Path(work_path)
        runtime_root = Path(data_path) if data_path is not None else config_root
        layout = WorkspaceLayout(runtime_root, config_path=config_root)
        layout.ensure_directories()

        generated_agent_key = str(raw.get("generated_agent_key", "")).strip()
        providers_config: list[dict[str, Any]] = list(raw.get("providers", []))

        workspace_config = self.load_workspace_config(config_root, data_path=runtime_root)
        if generated_agent_key:
            workspace_config.active_agent_key = generated_agent_key
        session_defaults_fields = {
            "history_max_messages", "history_max_chars",
            "max_recent_participant_messages", "enable_auto_compression",
        }
        for field_name in session_defaults_fields:
            if field_name in raw:
                setattr(workspace_config.session_defaults, field_name, type(
                    getattr(workspace_config.session_defaults, field_name)
                )(raw[field_name]))
        orchestration_raw = raw.get("orchestration")
        if isinstance(orchestration_raw, dict) and orchestration_raw:
            workspace_config.orchestration_defaults = dict(orchestration_raw)

        self.save_workspace_config(config_root, workspace_config, data_path=runtime_root)
        return workspace_config, providers_config
