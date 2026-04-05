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

from sirius_chat.config.models import Agent, AgentPreset, OrchestrationPolicy, SessionConfig


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

        # Build OrchestrationPolicy
        orch_dict = config_dict.get("orchestration", {})
        # 如果既没有 unified_model 也没有 task_models，使用 agent 的模型作为 unified_model
        unified_model = orch_dict.get("unified_model", "")
        task_models = orch_dict.get("task_models", {})
        if not unified_model and not task_models:
            # 使用 agent 的模型作为默认的统一模型
            unified_model = agent.model
        
        orchestration = OrchestrationPolicy(
            unified_model=unified_model,
            task_models=task_models,
            # task_enabled 控制功能开关，默认所有任务启用
            # 若启用，则根据 unified_model/task_models 选择调用哪个模型
            task_enabled=orch_dict.get("task_enabled", {
                "memory_extract": True,
                "multimodal_parse": True,
                "event_extract": True,
            }),
            task_budgets=orch_dict.get("task_budgets", {}),
            task_temperatures=orch_dict.get("task_temperatures", {}),
            task_max_tokens=orch_dict.get("task_max_tokens", {}),
            task_retries=orch_dict.get("task_retries", {}),
            max_multimodal_inputs_per_turn=int(
                orch_dict.get("max_multimodal_inputs_per_turn", 4)
            ),
            max_multimodal_value_length=int(
                orch_dict.get("max_multimodal_value_length", 4096)
            ),
            enable_prompt_driven_splitting=orch_dict.get("enable_prompt_driven_splitting", True),
            split_marker=orch_dict.get("split_marker", "[MSG_BREAK]"),
            memory_manager_model=orch_dict.get("memory_manager_model", ""),
            memory_manager_temperature=float(orch_dict.get("memory_manager_temperature", 0.3)),
            memory_manager_max_tokens=int(orch_dict.get("memory_manager_max_tokens", 512)),
        )

        # Build SessionConfig
        return SessionConfig(
            work_path=work_path,
            preset=preset,
            history_max_messages=int(config_dict.get("history_max_messages", 24)),
            history_max_chars=int(config_dict.get("history_max_chars", 6000)),
            max_recent_participant_messages=int(
                config_dict.get("max_recent_participant_messages", 5)
            ),
            enable_auto_compression=config_dict.get("enable_auto_compression", True),
            orchestration=orchestration,
        )
