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
from sirius_chat.config.models import (
    Agent,
    AgentPreset,
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

        with open(config_path, "r", encoding="utf-8") as f:
            raw_dict = json.load(f)

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
        if manifest_path.exists():
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                manifest_payload = payload

        session_snapshot = self._load_workspace_session_snapshot(layout)
        if manifest_payload:
            config = WorkspaceConfig.from_dict(manifest_payload)
        else:
            config = WorkspaceConfig(
                work_path=layout.config_root,
                data_path=layout.data_root,
                layout_version=layout.layout_version,
            )

        config.work_path = layout.config_root
        config.data_path = layout.data_root
        config.layout_version = layout.layout_version

        if session_snapshot:
            generated_agent_key = str(session_snapshot.get("generated_agent_key", "")).strip()
            if generated_agent_key:
                config.active_agent_key = generated_agent_key
            config.session_defaults = SessionDefaults(
                history_max_messages=int(
                    session_snapshot.get(
                        "history_max_messages",
                        config.session_defaults.history_max_messages,
                    )
                ),
                history_max_chars=int(
                    session_snapshot.get(
                        "history_max_chars",
                        config.session_defaults.history_max_chars,
                    )
                ),
                max_recent_participant_messages=int(
                    session_snapshot.get(
                        "max_recent_participant_messages",
                        config.session_defaults.max_recent_participant_messages,
                    )
                ),
                enable_auto_compression=bool(
                    session_snapshot.get(
                        "enable_auto_compression",
                        config.session_defaults.enable_auto_compression,
                    )
                ),
            )
            orchestration_payload = session_snapshot.get("orchestration", config.orchestration_defaults)
            if isinstance(orchestration_payload, dict):
                config.orchestration_defaults = dict(orchestration_payload)

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
        config.work_path = layout.config_root
        config.data_path = layout.data_root
        config.layout_version = layout.layout_version
        payload = config.to_dict()
        manifest_path = layout.workspace_manifest_path()
        manifest_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        session_snapshot = {
            "generated_agent_key": config.active_agent_key,
            "history_max_messages": config.session_defaults.history_max_messages,
            "history_max_chars": config.session_defaults.history_max_chars,
            "max_recent_participant_messages": config.session_defaults.max_recent_participant_messages,
            "enable_auto_compression": config.session_defaults.enable_auto_compression,
            "orchestration": dict(config.orchestration_defaults),
        }
        layout.session_config_path().write_text(
            json.dumps(session_snapshot, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

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

    def bootstrap_workspace_from_legacy_session_json(
        self,
        path: Path | str,
        *,
        work_path: Path | str,
        data_path: Path | str | None = None,
    ) -> tuple[WorkspaceConfig, list[dict[str, object]]]:
        """Import legacy session.json defaults into workspace config and provider registry."""
        from sirius_chat.providers.routing import WorkspaceProviderManager

        config_path = Path(path)
        raw_dict = json.loads(config_path.read_text(encoding="utf-8-sig"))
        resolved = self._resolve_values(raw_dict)
        config_root = Path(work_path)
        runtime_root = Path(data_path) if data_path is not None else config_root
        layout = WorkspaceLayout(runtime_root, config_path=config_root)
        workspace_config = self.load_workspace_config(layout.config_root, data_path=layout.data_root)

        generated_agent_key = str(resolved.get("generated_agent_key", "")).strip()
        if generated_agent_key:
            workspace_config.active_agent_key = generated_agent_key

        workspace_config.session_defaults = SessionDefaults(
            history_max_messages=int(resolved.get("history_max_messages", 24)),
            history_max_chars=int(resolved.get("history_max_chars", 6000)),
            max_recent_participant_messages=int(
                resolved.get("max_recent_participant_messages", 5)
            ),
            enable_auto_compression=bool(resolved.get("enable_auto_compression", True)),
        )
        workspace_config.orchestration_defaults = dict(resolved.get("orchestration", {}))
        self.save_workspace_config(layout.config_root, workspace_config, data_path=layout.data_root)

        providers_config = list(resolved.get("providers", []))
        if providers_config:
            WorkspaceProviderManager(layout).save_from_entries(providers_config)
        return workspace_config, providers_config

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
            payload = json.loads(session_config_path.read_text(encoding="utf-8"))
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
            payload = json.loads(path.read_text(encoding="utf-8"))
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
