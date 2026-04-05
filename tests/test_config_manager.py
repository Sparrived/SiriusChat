"""Tests for ConfigManager."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from sirius_chat.config_manager import ConfigManager
from sirius_chat.models import SessionConfig


class TestConfigManager:
    """Test ConfigManager functionality."""

    @pytest.fixture
    def config_manager(self) -> ConfigManager:
        """Create a ConfigManager instance."""
        return ConfigManager()

    @pytest.fixture
    def temp_config_file(self) -> Path:
        """Create a temporary config file."""
        config_dict = {
            "work_path": "/tmp/test_data",
            "global_system_prompt": "Test prompt",
            "agent": {
                "name": "TestAgent",
                "persona": "Test persona",
                "model": "test-model",
                "temperature": 0.7,
                "max_tokens": 256,
            },
            "orchestration": {
                "enabled": True,
                "task_models": {},
            },
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(config_dict, f)
            return Path(f.name)

    def test_load_from_json(self, config_manager: ConfigManager, temp_config_file: Path) -> None:
        """Test loading config from JSON file."""
        session_config = config_manager.load_from_json(temp_config_file)
        assert isinstance(session_config, SessionConfig)
        assert session_config.agent.name == "TestAgent"
        # Check that work_path ends with the expected path (platform-independent)
        assert session_config.work_path.parts[-2:] == ("tmp", "test_data")

    def test_load_from_json_file_not_found(self, config_manager: ConfigManager) -> None:
        """Test handling of missing config file."""
        with pytest.raises(FileNotFoundError):
            config_manager.load_from_json("/nonexistent/path.json")

    def test_resolve_env_vars(self, config_manager: ConfigManager) -> None:
        """Test environment variable substitution."""
        os.environ["TEST_VAR"] = "test_value"
        result = config_manager._resolve_env_vars("prefix_${TEST_VAR}_suffix")
        assert result == "prefix_test_value_suffix"

    def test_resolve_env_vars_with_default(self, config_manager: ConfigManager) -> None:
        """Test environment variable substitution with missing var."""
        result = config_manager._resolve_env_vars("${NONEXISTENT_VAR}")
        assert result == "${NONEXISTENT_VAR}"

    def test_merge_configs(self, config_manager: ConfigManager) -> None:
        """Test configuration merging."""
        base = {"a": 1, "b": {"c": 2}}
        override = {"b": {"d": 3}, "e": 4}
        result = config_manager.merge_configs(base, override)
        assert result == {"a": 1, "b": {"c": 2, "d": 3}, "e": 4}

    def test_validate_config_missing_keys(self, config_manager: ConfigManager) -> None:
        """Test validation of missing required keys."""
        config = {"agent": {"name": "test"}}
        with pytest.raises(ValueError, match="缺少必要配置键|Missing required config keys"):
            config_manager._validate_config(config)

    def test_validate_config_missing_agent_keys(self, config_manager: ConfigManager) -> None:
        """Test validation of missing agent keys."""
        config = {
            "work_path": "/tmp",
            "agent": {"name": "test"},
            "orchestration": {},
        }
        with pytest.raises(ValueError, match="缺少必要的主角配置键|Missing required agent keys"):
            config_manager._validate_config(config)

    def test_validate_config_invalid_work_path(self, config_manager: ConfigManager) -> None:
        """Test validation of invalid work_path."""
        config = {
            "work_path": {"invalid": "path"},
            "agent": {"name": "test", "persona": "p", "model": "m"},
            "orchestration": {},
        }
        with pytest.raises(ValueError, match="无效的 work_path|Invalid work_path"):
            config_manager._validate_config(config)

    def test_load_from_env_dev(self, config_manager: ConfigManager) -> None:
        """Test loading dev environment config."""
        try:
            config = config_manager.load_from_env("dev")
            assert isinstance(config, SessionConfig)
            assert config.agent.name == "SiriusAI-Dev"
        except FileNotFoundError:
            # Config files might not exist in test environment
            pytest.skip("Config files not available")

    def test_load_from_env_invalid_env(self, config_manager: ConfigManager) -> None:
        """Test error handling for invalid environment."""
        with pytest.raises(ValueError, match="未知环境|Unknown environment"):
            config_manager.load_from_env("invalid")

    def test_dict_to_session_config(self, config_manager: ConfigManager) -> None:
        """Test conversion from dict to SessionConfig."""
        config_dict = {
            "work_path": "/tmp/test",
            "global_system_prompt": "Test prompt",
            "agent": {
                "name": "TestAgent",
                "persona": "Test persona",
                "model": "test-model",
                "temperature": 0.8,
                "max_tokens": 256,
                "metadata": {"key": "value"},
            },
            "history_max_messages": 32,
            "history_max_chars": 8000,
            "enable_auto_compression": False,
            "orchestration": {
                "enabled": False,
                "task_models": {},
            },
        }
        session_config = config_manager._dict_to_session_config(config_dict, Path("/tmp"))
        assert session_config.agent.name == "TestAgent"
        assert session_config.history_max_messages == 32
        assert session_config.orchestration.enabled is False
        assert session_config.agent.metadata == {"key": "value"}

    def test_resolve_values_nested(self, config_manager: ConfigManager) -> None:
        """Test recursive environment variable resolution."""
        os.environ["TEST_MODEL"] = "test-model-value"
        obj = {
            "nested": {
                "model": "${TEST_MODEL}",
                "list": ["${TEST_MODEL}", "static"],
            }
        }
        result = config_manager._resolve_values(obj)
        assert result["nested"]["model"] == "test-model-value"
        assert result["nested"]["list"] == ["test-model-value", "static"]

    def test_relative_path_resolution(self, config_manager: ConfigManager, temp_config_file: Path) -> None:
        """Test loading config with relative path."""
        # Create a config in a known location relative to base_path
        temp_dir = temp_config_file.parent
        relative_config = temp_dir / "relative_config.json"
        
        config_dict = {
            "work_path": "./data",
            "global_system_prompt": "Test",
            "agent": {
                "name": "TestAgent",
                "persona": "Test",
                "model": "test-model",
            },
            "orchestration": {"enabled": True},
        }
        with open(relative_config, "w") as f:
            json.dump(config_dict, f)
        
        manager = ConfigManager(temp_dir)
        session_config = manager.load_from_json("relative_config.json")
        assert session_config.agent.name == "TestAgent"
        assert session_config.work_path == temp_dir / "data"


class TestEnvVarSubstitution:
    """Test environment variable substitution patterns."""

    @pytest.fixture
    def config_manager(self) -> ConfigManager:
        """Create a ConfigManager instance."""
        return ConfigManager()

    def test_multiple_env_vars(self, config_manager: ConfigManager) -> None:
        """Test string with multiple environment variables."""
        os.environ["HOST"] = "localhost"
        os.environ["PORT"] = "8080"
        result = config_manager._resolve_env_vars("${HOST}:${PORT}")
        assert result == "localhost:8080"

    def test_env_var_in_nested_structure(self, config_manager: ConfigManager) -> None:
        """Test environment variable resolution in nested structures."""
        os.environ["API_KEY"] = "secret123"
        config = {
            "credentials": {
                "api_key": "${API_KEY}",
                "endpoints": ["${API_KEY}_endpoint1", "${API_KEY}_endpoint2"],
            }
        }
        result = config_manager._resolve_values(config)
        assert result["credentials"]["api_key"] == "secret123"
        assert result["credentials"]["endpoints"][0] == "secret123_endpoint1"


class TestConfigIntegration:
    """Integration tests for ConfigManager."""

    def test_load_default_dev_config(self) -> None:
        """Test loading the default dev config file."""
        manager = ConfigManager()
        try:
            config = manager.load_from_json(
                Path(__file__).parent.parent / "sirius_chat" / "configs" / "dev.json"
            )
            assert config.agent.name == "SiriusAI-Dev"
            assert config.orchestration.enabled
        except FileNotFoundError:
            pytest.skip("Default config files not available")

    def test_load_default_test_config(self) -> None:
        """Test loading the default test config file."""
        manager = ConfigManager()
        try:
            config = manager.load_from_json(
                Path(__file__).parent.parent / "sirius_chat" / "configs" / "test.json"
            )
            assert config.agent.name == "SiriusAI-Test"
            assert config.agent.model == "mock-model"
        except FileNotFoundError:
            pytest.skip("Default config files not available")
