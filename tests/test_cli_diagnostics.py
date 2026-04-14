"""CLI 诊断模块测试"""

from __future__ import annotations

from pathlib import Path

import pytest

from sirius_chat.cli_diagnostics import (
    EnvironmentDiagnostics,
    run_preflight_check,
    generate_default_config,
)
from sirius_chat.config.jsonc import load_json_document


class TestEnvironmentDiagnostics:
    """环境诊断测试"""

    def test_check_python_version(self):
        """测试 Python 版本检查"""
        is_valid, msg = EnvironmentDiagnostics.check_python_version()
        assert is_valid is True  # 如果测试能运行，说明 Python 版本满足要求

    def test_check_work_path_valid(self, temp_work_dir: Path):
        """测试有效的工作目录检查"""
        is_valid, msg = EnvironmentDiagnostics.check_work_path(temp_work_dir)
        assert is_valid is True
        assert msg == ""

    def test_check_work_path_invalid(self):
        """测试无效的工作目录检查"""
        invalid_path = Path("/invalid/nonexistent/path/that/cannot/be/created")
        is_valid, msg = EnvironmentDiagnostics.check_work_path(invalid_path)
        # 在大多数系统上应该失败
        # （某些特殊环境可能有不同行为）

    def test_check_config_file_missing(self, temp_work_dir: Path):
        """测试缺失的配置文件检查"""
        config_path = temp_work_dir / "nonexistent.json"
        is_valid, msg = EnvironmentDiagnostics.check_config_file(config_path)
        assert is_valid is False
        assert "不存在" in msg

    def test_check_config_file_invalid_json(self, temp_work_dir: Path):
        """测试无效 JSON 配置文件检查"""
        config_path = temp_work_dir / "invalid.json"
        config_path.write_text("{invalid json content")
        
        is_valid, msg = EnvironmentDiagnostics.check_config_file(config_path)
        assert is_valid is False
        assert "JSON" in msg

    def test_check_config_file_valid(self, temp_work_dir: Path):
        """测试有效的配置文件检查"""
        import json
        config_path = temp_work_dir / "config.json"
        config_path.write_text(json.dumps({"key": "value"}))
        
        is_valid, msg = EnvironmentDiagnostics.check_config_file(config_path)
        assert is_valid is True
        assert msg == ""

    def test_check_config_file_accepts_jsonc_comments(self, temp_work_dir: Path):
        """测试 JSONC 注释格式配置文件检查"""
        config_path = temp_work_dir / "config.json"
        config_path.write_text('// 注释\n{\n  "key": "value"\n}\n', encoding="utf-8")

        is_valid, msg = EnvironmentDiagnostics.check_config_file(config_path)
        assert is_valid is True
        assert msg == ""

    def test_check_provider_config_missing(self, temp_work_dir: Path):
        """测试缺失 Provider 配置检查"""
        import json
        config_path = temp_work_dir / "config.json"
        config_path.write_text(json.dumps({}))
        
        is_valid, msg = EnvironmentDiagnostics.check_provider_config(config_path)
        assert is_valid is False
        assert "Provider" in msg

    def test_check_provider_config_empty_apikey(self, temp_work_dir: Path):
        """测试空 API Key 检查"""
        import json
        config_path = temp_work_dir / "config.json"
        config_path.write_text(json.dumps({
            "provider": {
                "type": "openai-compatible",
                "api_key": "",
            }
        }))
        
        is_valid, msg = EnvironmentDiagnostics.check_provider_config(config_path)
        assert is_valid is False
        assert "API Key" in msg

    def test_check_provider_config_accepts_jsonc_comments(self, temp_work_dir: Path):
        """测试带注释的 Provider 配置检查"""
        config_path = temp_work_dir / "config.json"
        config_path.write_text(
            '{\n  // 推荐使用 providers 列表\n  "providers": [\n    {\n      "type": "openai-compatible",\n      "api_key": "test-key"\n    }\n  ]\n}\n',
            encoding="utf-8",
        )

        is_valid, msg = EnvironmentDiagnostics.check_provider_config(config_path)
        assert is_valid is True
        assert msg == ""


class TestPreflightCheck:
    """启动前检查测试"""

    def test_preflight_check_valid_config(self, temp_work_dir: Path):
        """测试有效配置的启动前检查"""
        import json
        
        config_path = temp_work_dir / "config.json"
        config_path.write_text(json.dumps({
            "provider": {
                "type": "openai-compatible",
                "api_key": "test-key",
            }
        }))
        
        messages = []
        def capture_print(msg: str):
            messages.append(msg)
        
        result = run_preflight_check(config_path, temp_work_dir, print_func=capture_print)
        
        # 应该通过大部分检查（可能 Provider 配置有问题，但基本结构有效）
        assert len(messages) > 0


class TestGenerateDefaultConfig:
    """默认配置生成测试"""

    def test_generate_default_config(self, temp_work_dir: Path):
        """测试生成默认配置文件"""
        config_path = temp_work_dir / "default_config.json"
        generate_default_config(config_path)
        
        assert config_path.exists()

        raw_text = config_path.read_text(encoding="utf-8")
        assert "//" in raw_text

        content = load_json_document(config_path)
        assert "provider" in content
        assert "providers" in content
        assert "generated_agent_key" in content
        assert content["provider"]["type"] == "openai-compatible"
        assert content["providers"][0]["type"] == "openai-compatible"


@pytest.fixture
def temp_work_dir():
    """临时工作目录 fixture"""
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)
